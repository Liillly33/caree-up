"""BM25 over JOB documents + example-based TECH filter + dictionary-first tokens."""
from __future__ import annotations
from collections import Counter, OrderedDict
import json
import math
import os
import threading
from statistics import median
from pathlib import Path
import numpy as np
from text_processing import TechTokenizer, clauses, item_id, load_labels, norm, sha
from explanations import build_explanation

from profile_context import UNCERTAIN, positive_profile_fragments
from technology_importance import load_reviews
from importance_scoring import importance_coverage
from importance_rules import assign_importance,RULE_VERSION
from project_semantic import ProjectSemanticIndex,VERSION as PROJECT_SEMANTIC_VERSION

MODEL_NAME='jhgan/ko-sroberta-multitask'
MODEL_REVISION='8fca7c9c98c26599be0e14b9916b11a756a26f19'
RANKING_VERSION='project-evidence-responsive-v9'

class SentenceEncoder:
    mode='sbert'
    def __init__(self,model_name=MODEL_NAME,revision=None,cache_dir=None):
        import importlib.metadata
        import logging,re
        from embedding_cache import EmbeddingCache
        revision=revision or os.environ.get('SBERT_REVISION') or (MODEL_REVISION if model_name==MODEL_NAME else None)
        self.name=model_name;self.revision=revision;self._model=None
        self.cache=OrderedDict();self._lock=threading.RLock();self.persist_public=True
        self.stats={'disk_hits':0,'memory_hits':0,'encoded':0}
        self.disk=None
        if revision and re.fullmatch(r'[0-9a-f]{40}',revision):
            versions={}
            for name in ['numpy','torch','transformers','sentence-transformers']:
                try:versions[name]=importlib.metadata.version(name)
                except importlib.metadata.PackageNotFoundError:versions[name]='missing'
            self.disk=EmbeddingCache(cache_dir or Path(__file__).parent/'runtime-cache'/'embeddings',{'format':1,'model':model_name,'revision':revision,'packages':versions,'normalized':True,'precision':'float32-default'})
    @property
    def model(self):
        if self._model is None:
            try:from sentence_transformers import SentenceTransformer
            except ImportError as exc:raise RuntimeError('sentence-transformers를 설치해주세요. SBERT를 다른 모델로 대체하지 않습니다.') from exc
            import logging,time
            started=time.perf_counter();logging.getLogger('uvicorn.error').info('SBERT 모델 로딩: 새 문장 계산이 필요합니다.')
            self._model=SentenceTransformer(self.name,revision=self.revision)
            logging.getLogger('uvicorn.error').info('SBERT 모델 준비: %.1f초 · 장치 %s',time.perf_counter()-started,self._model.device)
        return self._model
    @model.setter
    def model(self,value):self._model=value
    def encode(self,texts):
        with self._lock:return self._encode(texts)
    def _encode(self,texts):
        import logging,time
        missing=[]
        for text in dict.fromkeys(texts):
            if text in self.cache:self.stats['memory_hits']+=1;continue
            vector=self.disk.get(text) if self.disk else None
            if vector is None:missing.append(text)
            else:self.cache[text]=vector;self.stats['disk_hits']+=1
        if missing:
            started=time.perf_counter()
            logging.getLogger('uvicorn.error').info('SBERT 새 문장 %s개 계산 중…',len(missing))
            vectors=self.model.encode(missing,normalize_embeddings=True,batch_size=32,show_progress_bar=True)
            pairs=[(text,np.asarray(vector,dtype=float)) for text,vector in zip(missing,vectors)]
            for text,vector in pairs:self.cache[text]=vector
            if self.persist_public and self.disk:self.disk.put(pairs)
            self.stats['encoded']+=len(missing)
            logging.getLogger('uvicorn.error').info('SBERT 문장 계산 완료: %.1f초',time.perf_counter()-started)
        result=np.stack([self.cache[t] for t in texts]) if texts else np.empty((0,0))
        if not self.persist_public:
            while len(self.cache)>2048:self.cache.popitem(last=False)
        return result
    def finish_index_cache(self):
        self.persist_public=False
        while len(self.cache)>2048:self.cache.popitem(last=False)
    def close(self):
        if self.disk:self.disk.close()

class LexicalOnlyEncoder:
    mode='lexical_only';name='none';revision=None
    def encode(self,texts):return np.zeros((len(texts),1))

class ExampleFilter:
    def __init__(self,labels,encoder,k=5,min_similarity=.45,min_margin=.10):
        self.labels=labels;self.encoder=encoder;self.k=k;self.min_similarity=min_similarity;self.min_margin=min_margin
        self.references=[r for r in labels.values() if r.get('label_source','USER') in ('USER','USER_PREVIOUS') and r['label'] in ('TECH','NONTECH')]
        if {r['label'] for r in self.references}!={'TECH','NONTECH'}:raise ValueError('사람이 작성한 TECH/NONTECH 예시가 모두 필요합니다.')
        self.reference_vectors=encoder.encode([r['text'] for r in self.references])

    def classify_many(self,texts,allow_exact=True):
        results={};unknown=[]
        for text in dict.fromkeys(texts):
            known=self.labels.get(item_id(text)) if allow_exact else None
            if known:
                results[text]={'label':known['label'],'source':'csv_exact','label_origin':known.get('label_source','USER'),
                               'match_text':known.get('match_text','').strip() or text,'neighbors':[]}
            else:unknown.append(text)
        if unknown and self.encoder.mode=='lexical_only':
            for text in unknown:results[text]={'label':'REVIEW','source':'unseen_in_lexical_mode','match_text':'','neighbors':[]}
        elif unknown:
            vectors=self.encoder.encode(unknown)
            similarities=np.clip(vectors @ self.reference_vectors.T,-1,1)
            for text,scores in zip(unknown,similarities):
                ids=np.argsort(-scores,kind='stable')[:min(self.k,len(scores))]
                votes={label:sum(max(0,float(scores[i])) for i in ids if self.references[i]['label']==label) for label in ['TECH','NONTECH']}
                total=sum(votes.values());winner=max(votes,key=votes.get)
                margin=abs(votes['TECH']-votes['NONTECH'])/total if total else 0
                label=winner if float(scores[ids[0]])>=self.min_similarity and margin>=self.min_margin else 'REVIEW'
                results[text]={'label':label,'source':'sbert_examples','match_text':text if label=='TECH' else '',
                               'vote_margin':round(margin,4),'nearest_similarity':round(float(scores[ids[0]]),4),
                               'neighbors':[{'text':self.references[i]['text'],'label':self.references[i]['label'],'cosine':round(float(scores[i]),4)} for i in ids]}
        return results

    def evaluate_human_holdout(self):
        # No exact-label lookup; test human labels must NEVER be reference examples.
        train=[r for r in self.references if r['split']=='train']
        test=[r for r in self.references if r['split']=='test']
        if not test or {r['label'] for r in train}!={'TECH','NONTECH'}:return {'status':'INSUFFICIENT_HUMAN_LABELS'}
        if self.encoder.mode!='sbert':return {'status':'SBERT_REQUIRED','test_count':len(test)}
        saved_refs,saved_vec=self.references,self.reference_vectors
        try:
            self.references=train;self.reference_vectors=self.encoder.encode([r['text'] for r in train])
            predictions=self.classify_many([r['text'] for r in test],allow_exact=False)
        finally:self.references,self.reference_vectors=saved_refs,saved_vec
        matrix=Counter((r['label'],predictions[r['text']]['label']) for r in test)
        tp=matrix['TECH','TECH'];fp=matrix['NONTECH','TECH'];fn=sum(matrix['TECH',p] for p in ['NONTECH','REVIEW'])
        precision=tp/(tp+fp) if tp+fp else None;recall=tp/(tp+fn) if tp+fn else None
        return {'status':'EVALUATED','train_count':len(train),'test_count':len(test),
                'tech_precision':precision,'tech_recall':recall,
                'confusion_counts':{a+'->'+b:n for (a,b),n in matrix.items()},
                'note':'동일 문장 누출만 차단한 소규모 사람 라벨 평가. 회사/유사문장 누출 및 표본 편향 가능.'}

class JobBM25:
    def __init__(self,documents,k1=1.5,b=.75):
        self.documents=[Counter(doc) for doc in documents];self.n=len(documents);self.k1=k1;self.b=b
        self.lengths=[sum(doc.values()) for doc in self.documents]
        self.avgdl=sum(self.lengths)/self.n if self.n else 0
        df=Counter(t for document in self.documents for t in document)
        self.df=dict(df);self.idf={t:math.log(1+(self.n-v+.5)/(v+.5)) for t,v in df.items()}
        self.weights=[]
        for doc,length in zip(self.documents,self.lengths):
            self.weights.append({t:self.idf[t]*tf*(k1+1)/(tf+k1*(1-b+b*length/(self.avgdl or 1))) for t,tf in doc.items()})

    def item_score(self,job_index,item_tokens,query_tokens):
        weights=self.weights[job_index];terms=set(item_tokens);query=set(query_tokens)
        upper=sum(weights[t] for t in terms if t in weights)
        raw=sum(weights[t] for t in terms & query if t in weights)
        return {'raw':raw,'self_upper_bound':upper,'normalized':raw/upper if upper else 0.0}

    def score_against_query(self,job_index,item_tokens,query_tokens,unseen_idf):
        terms=set(item_tokens);query=set(query_tokens)
        raw=sum(self.weights[job_index].get(t,0) for t in terms & query)
        # A fixed reference per query preserves BM25 document-length effects.
        # It is the hypothetical score of a document containing each query term
        # once at length |Q|, using this corpus's avgdl and IDF.
        length_factor=(1-self.b+self.b*len(query)/(self.avgdl or 1))
        reference=sum(self.idf.get(t,unseen_idf) for t in query)*(self.k1+1)/(1+self.k1*length_factor)
        return {'raw':raw,'query_reference':reference,'normalized':min(1,raw/reference) if reference else 0.0,
                'clipped':bool(reference and raw>reference),'document_length':self.lengths[job_index],'average_document_length':self.avgdl}

    def score_against_reference(self,job_index,item_tokens,profile_tokens,reference_mass,reference_count):
        raw=sum(self.weights[job_index].get(t,0) for t in set(item_tokens) & set(profile_tokens))
        length_factor=1-self.b+self.b*reference_count/(self.avgdl or 1)
        reference=reference_mass*(self.k1+1)/(1+self.k1*length_factor)
        return {'raw':raw,'reference_score':reference,'normalized':min(1,raw/reference) if reference else 0.0,
                'clipped':bool(reference and raw>reference),'reference_basis':'fixed_corpus_role',
                'document_length':self.lengths[job_index],'average_document_length':self.avgdl}

class Recommender:
    def __init__(self,jobs,labels_path,dictionary_path,encoder=None,policy_path=None):
        self.policy=json.loads(Path(policy_path or Path(__file__).resolve().parent/'data/scoring_policy.json').read_text(encoding='utf-8'))
        self._validate_policy()
        self.encoder=encoder if encoder is not None else SentenceEncoder()
        self.tokenizer=TechTokenizer(dictionary_path);self.labels=load_labels(labels_path)
        self.filter=ExampleFilter(self.labels,self.encoder)
        if any(not isinstance(j.get('JOB_ANN_ID'),int) for j in jobs):raise ValueError('공고 ID는 정수여야 합니다.')
        if len({j['JOB_ANN_ID'] for j in jobs})!=len(jobs):raise ValueError('중복 공고 ID가 있습니다.')
        self.jobs=sorted(jobs,key=lambda j:j['JOB_ANN_ID'])
        self.importance_reviews,self.importance_summary,self.importance_version=load_reviews(Path(dictionary_path).parent/'technology_importance_reviews.json',self.jobs)
        all_texts=[text for j in self.jobs for col in ['JOB_REQUIRE','JOB_PREFERRD_QUALIFICATION'] for text in clauses(j.get(col))]
        decisions=self.filter.classify_many(all_texts)
        self.index=[];documents=[]
        for job in self.jobs:
            reviewed=self.importance_reviews.get(job['JOB_ANN_ID'],{})
            excluded_technologies={t for t,label in reviewed.items() if label=='제외'}
            sections={};document=[]
            for section,column in [('requirements','JOB_REQUIRE'),('preferences','JOB_PREFERRD_QUALIFICATION')]:
                records=[]
                for text in clauses(job.get(column)):
                    decision=dict(decisions[text])
                    verified={t for t in self.tokenizer.tokens(text) if reviewed.get(t) in ('핵심','보조')}
                    if verified and decision['label']!='TECH':
                        decision.update(label='TECH',source='human_importance_review',match_text=text,label_origin='USER')
                    record={'text':text,**decision,'item_id':item_id(text),'source_column':column}
                    if decision['label']=='TECH':
                        analysis=self.tokenizer.analyze(decision['match_text'])
                        removed=set(analysis['tokens']) & excluded_technologies
                        analysis['tokens']=[t for t in analysis['tokens'] if t not in excluded_technologies]
                        analysis['explicit']=sorted(set(analysis['tokens']))
                        analysis['projection']=' '.join(analysis['tokens'])
                        record['human_excluded_technologies']=sorted(removed)
                        record['technology_importance']={t:reviewed[t] for t in analysis['tokens'] if t in reviewed}
                        if analysis['tokens']:
                            record.update(analysis);document.extend(analysis['tokens'])
                        elif removed:record['label']='NONTECH';record['source']='human_excluded_technologies'
                        else:record['label']='REVIEW';record['source']='no_scoring_tokens'
                    records.append(record)
                sections[section]=records
            # Keep explicitly reviewed duty-only requirements in the index too.
            existing={t for records in sections.values() for r in records if r['label']=='TECH' for t in r['tokens']}
            for text in clauses(job.get('JOB_RESPONSIBILITIES')):
                tokens=[t for t in self.tokenizer.tokens(text) if reviewed.get(t) in ('핵심','보조') and t not in existing]
                if not tokens:continue
                sections['requirements'].append({'text':text,'match_text':text,'item_id':item_id(text),'label':'TECH',
                    'source':'human_importance_review','label_origin':'USER','source_column':'JOB_RESPONSIBILITIES',
                    'tokens':tokens,'explicit':sorted(tokens),'projection':' '.join(tokens),'excluded_terms':[],
                    'unregistered_terms':[],'technology_importance':{t:reviewed[t] for t in tokens}})
                document.extend(tokens);existing.update(tokens)
            self.index.append(sections);documents.append(document)
        self.importance_assignments=[]
        for job,sections,document in zip(self.jobs,self.index,documents):
            assignments=assign_importance(job,set(document),self.importance_reviews.get(job['JOB_ANN_ID'],{}),self.tokenizer)
            self.importance_assignments.append(assignments)
            for records in sections.values():
                for record in records:
                    if record['label']=='TECH':record['technology_importance']={t:assignments[t] for t in record['tokens']}
        self.bm25=JobBM25(documents)
        self.unseen_idf=math.log(1+(len(documents)+.5)/.5)
        self.job_technologies=[set(document) for document in documents]
        self._build_role_references()
        self.project_semantic=ProjectSemanticIndex(self.jobs,self.encoder,self.filter,self.tokenizer,
            self.importance_reviews,self.policy['project_semantic'])
        if hasattr(self.encoder,'finish_index_cache'):self.encoder.finish_index_cache()
        self.version=sha({'jobs':self.jobs,'labels':list(self.labels.values()),'dictionary':self.tokenizer.config,'importance_reviews':self.importance_version,
                          'ranking_version':RANKING_VERSION,'project_semantic_version':PROJECT_SEMANTIC_VERSION,'importance_rule_version':RULE_VERSION,'policy':self.policy,'non_performed_pattern':UNCERTAIN.pattern,
                          'model':self.encoder.name,'revision':getattr(self.encoder,'revision',None),'mode':self.encoder.mode})

    def manifest(self):
        counts=Counter(r['label'] for sections in self.index for records in sections.values() for r in records)
        origins=Counter(r.get('label_source','USER') for r in self.labels.values())
        return {'index_version':self.version,'job_documents':self.bm25.n,'average_document_length':self.bm25.avgdl,
                'document_frequency':self.bm25.df,'idf':self.bm25.idf,'indexed_clause_counts':dict(counts),
                'label_provenance':dict(origins),'human_reference_count':len(self.filter.references),
                'dictionary_version':self.tokenizer.version,'encoder_mode':self.encoder.mode,'model':self.encoder.name,
                'importance_review_summary':self.importance_summary,'importance_review_version':self.importance_version,
                'importance_rule_version':RULE_VERSION,
                'project_semantic_version':PROJECT_SEMANTIC_VERSION,
                'project_semantic_job_sentence_count':sum(len(r['records']) for r in self.project_semantic.rows),
                'importance_classification_counts':dict(Counter((r['source']+':'+r['label']) for a in self.importance_assignments for r in a.values())),
                'excluded_dictionary_terms':sorted(t for t,enabled in self.tokenizer.scoring.items() if not enabled),
                'scoring_policy':self.policy,'role_references':self.role_references,'global_reference':self.global_reference,'unregistered_english_terms':sorted({t for j in self.jobs for col in ['JOB_REQUIRE','JOB_PREFERRD_QUALIFICATION'] for t in self.tokenizer.analyze(str(j.get(col) or ''))['unregistered_terms']})}

    def _build_role_references(self):
        # Fixed corpus statistics; no user-profile value enters these baselines.
        groups={};all_values=[]
        for job,terms in zip(self.jobs,self.job_technologies):
            if not terms:continue
            value=(sum(self.bm25.idf[t] for t in terms),len(terms))
            groups.setdefault(job.get('JOB_CODE'),[]).append(value);all_values.append(value)
        def reference(values,basis):
            return {'idf_mass':median(v[0] for v in values) if values else 0.0,
                    'technology_count':median(v[1] for v in values) if values else 0,
                    'sample_count':len(values),'basis':basis}
        self.global_reference=reference(all_values,'global_corpus_median')
        self.role_references={code:reference(values,'job_code_median') for code,values in groups.items()}

    def _validate_policy(self):
        p=self.policy
        def number(value):return isinstance(value,(float,int)) and not isinstance(value,bool) and math.isfinite(value)
        if not number(p['technical_points']) or p['technical_points']<=0:raise ValueError('기술점수 배점을 확인해주세요.')
        if len(p['preference_bonuses'])!=3 or any(not number(v) or v<0 for v in p['preference_bonuses']):raise ValueError('선호직무 가점은 3개의 음이 아닌 수여야 합니다.')
        company=list(p['company_scale_bonuses'].values())+[p['unknown_company_bonus']]
        if any(not number(v) or v<0 for v in company):raise ValueError('기업규모 가점을 확인해주세요.')
        if p['technical_points']+max(p['preference_bonuses'])+max(company)>100:raise ValueError('총 배점이 100점을 초과합니다.')
        for key in ['bm25_weight','semantic_weight']:
            if not number(p[key]) or not 0<=p[key]<=1:raise ValueError('BM25/의미 가중치는 0~1이어야 합니다.')
        if p['bm25_weight']+p['semantic_weight']>1:raise ValueError('BM25와 의미 가중치 합계는 1 이하여야 합니다.')
        iw=p.get('importance_weights',{})
        if set(iw)!={'core','auxiliary','neutral'} or any(not number(v) or v<=0 for v in iw.values()):raise ValueError('핵심·보조·중립 가중치를 확인해주세요.')
        if abs(iw['core']+iw['auxiliary']+p['bm25_weight']+p['semantic_weight']-1)>1e-9:raise ValueError('기본 점수 가중치 합은 1이어야 합니다.')
        power=p.get('evidence_sufficiency_power',1.0)
        if not number(power) or not .1<=power<=2:raise ValueError('기술 근거 보정 지수는 0.1~2여야 합니다.')
        sp=p['project_semantic']
        eb=sp.get('evidence_budget')
        if eb is not None:
            if not isinstance(eb.get('slots'),int) or isinstance(eb['slots'],bool) or eb['slots']<1:raise ValueError('프로젝트 근거 슬롯을 확인해주세요.')
            if any(not number(eb.get(k)) for k in ('semantic_weight','explicit_weight','similarity_floor','similarity_full_credit')):raise ValueError('프로젝트 근거 배점을 확인해주세요.')
            if min(eb['semantic_weight'],eb['explicit_weight'])<0 or abs(eb['semantic_weight']+eb['explicit_weight']-1)>1e-9 or not 0<=eb['similarity_floor']<eb['similarity_full_credit']<=1:raise ValueError('프로젝트 근거 배점을 확인해주세요.')
        if not number(sp.get('explicit_evidence_weight',.25)) or not 0<=sp.get('explicit_evidence_weight',.25)<=1:raise ValueError('프로젝트 기술 근거 가중치를 확인해주세요.')
        if any(not number(sp[k]) for k in ('similarity_floor','similarity_ceiling','duplicate_cosine')) or not 0<=sp['similarity_floor']<sp['similarity_ceiling']<=1 or not .9<=sp['duplicate_cosine']<=1:raise ValueError('프로젝트 유사도 설정을 확인해주세요.')
        if set(sp['section_weights'])!={'responsibilities','requirements','preferences'} or any(not number(v) or v<=0 for v in sp['section_weights'].values()):raise ValueError('프로젝트 비교 구역별 가중치를 확인해주세요.')
        if set(p['section_weights'])!={'requirements','preferences'} or any(not number(v) or v<=0 for v in p['section_weights'].values()):raise ValueError('구분별 가중치는 양수여야 합니다.')

    def _company_bonus(self,scale):
        normalized=norm(str(scale or ''))
        aliases={norm(k):v for k,v in self.policy['company_scale_bonuses'].items()}
        return aliases.get(normalized,self.policy['unknown_company_bonus'])

    def _explain(self,sections,profile_records,query_tokens,job_code=None):
        return build_explanation(sections,profile_records,query_tokens,self.tokenizer,self.bm25.idf,job_code)

    def explain_job(self,profile,job_id):
        """Reuse recommendation evidence for one saved posting, including empty current profiles."""
        index=next((i for i,j in enumerate(self.jobs) if j['JOB_ANN_ID']==job_id),None)
        if index is None:raise KeyError(job_id)
        values=[]
        for key in ('tech_skills','licenses'):
            rows=profile.get(key,[])
            if not isinstance(rows,list) or any(not isinstance(t,str) for t in rows):raise ValueError('스펙은 문자열 배열이어야 합니다.')
            values.extend(t.strip() for t in rows if t.strip())
        fragments=positive_profile_fragments(values,self.tokenizer)
        query={t for r in fragments['records'] for t in r['technologies']}
        sections={name:{'included':[r for r in records if r['label']=='TECH']} for name,records in self.index[index].items()}
        return self._explain(sections,fragments['records'],query,self.jobs[index].get('JOB_CODE'))

    def recommend(self,profile,job_codes=None,top_k=5,method='hybrid',deduplicate=True,include_unmatched=False,target_job_id=None,excluded_job_ids=None):
        if not isinstance(top_k,int) or isinstance(top_k,bool) or not 1<=top_k<=1000:raise ValueError('top_k는 1~1000 정수여야 합니다.')
        if method not in ('exact','bm25','hybrid'):raise ValueError('지원하지 않는 점수 방식입니다.')
        if job_codes is not None and (not isinstance(job_codes,list) or len(job_codes)>3 or
                any(not isinstance(c,str) for c in job_codes) or len(set(job_codes))!=len(job_codes)):
            raise ValueError('선호직무는 중복 없이 최대 3개여야 합니다.')
        preference_bonus=dict(zip(job_codes or [],self.policy['preference_bonuses']))
        values=[]
        for key in ['tech_skills','licenses']:
            value=profile.get(key,[])
            if not isinstance(value,list) or any(not isinstance(t,str) for t in value):raise ValueError('스펙은 문자열 배열이어야 합니다.')
            values.extend(t.strip() for t in value if t.strip())
        semantic_requested=method=='hybrid' and self.encoder.mode=='sbert' and self.policy['semantic_weight']>0
        project_text='\n'.join(line for line in (profile.get('project_experience') or '').splitlines() if not line.lstrip().startswith('[공모전]'))
        project_prepared=self.project_semantic.prepare(project_text,enabled=semantic_requested)
        if not values and not profile.get('contest_job_codes') and project_prepared['status']!='AVAILABLE':raise ValueError('마이페이지에 보유 기술 스택·자격증 또는 수행한 프로젝트 역할을 입력해주세요.')
        profile_evidence=positive_profile_fragments(values,self.tokenizer)
        profile_records=profile_evidence['records']
        query={t for r in profile_records for t in r['technologies']}
        if not query and not profile.get('contest_job_codes') and project_prepared['status']!='AVAILABLE':raise ValueError('구체적인 기술명이나 실제 수행한 프로젝트 역할을 입력해주세요.')
        sbert_enabled=semantic_requested and project_prepared['status']=='AVAILABLE'
        def effective_weights(available):
            if method=='exact':return 1.0,0.0,0.0
            base=self.policy['importance_weights']['core']+self.policy['importance_weights']['auxiliary']
            bm=self.policy['bm25_weight'];total=base+bm
            # Keep the established skill score independent of project input.
            # Project relevance adds an independent, bounded contribution.
            return base/total,bm/total,self.policy['semantic_weight'] if available else 0.0
        coverage_weight,bm25_weight,semantic_weight=effective_weights(sbert_enabled)
        results=[]
        for i,(job,sections) in enumerate(zip(self.jobs,self.index)):
            # Keep the full BM25 corpus/role reference; only limit which target is rescored.
            if target_job_id is not None and job['JOB_ANN_ID']!=target_job_id:continue
            context=self.role_references.get(job.get('JOB_CODE'),self.global_reference)
            scoped_query=query & self.job_technologies[i]
            project_match=self.project_semantic.score(i,project_prepared)
            row_coverage_weight,row_bm25_weight,row_semantic_weight=effective_weights(sbert_enabled and project_match['score'] is not None)
            project_score=project_match['score'] or 0.0
            importance_points,importance_details=importance_coverage(self.job_technologies[i],query,
                {t:r['label'] for t,r in self.importance_assignments[i].items()},self.bm25.idf,context['technology_count'],
                row_coverage_weight,self.policy['importance_weights'],self.policy.get('evidence_sufficiency_power',1.0))
            importance_details['assignments']=self.importance_assignments[i]
            out={};weight_sum=0.0;review_count=0;known_matches=set()
            bm25_sum=0.0
            for section,weight in self.policy['section_weights'].items():
                scored=[];excluded=[]
                for record in sections[section]:
                    if record['label']!='TECH':
                        excluded.append(record);review_count+=record['label']=='REVIEW';continue
                    matched=set(record['tokens']) & query
                    known_matches.update(matched)
                    scored.append({**record,'matched_tokens':sorted(matched)})
                terms={t for r in scored for t in r['tokens']};matched=terms & query
                matched_idf=sum(self.bm25.idf[t] for t in matched)
                term_weight=sum(self.bm25.idf[t] for t in terms)
                precision=matched_idf/term_weight if term_weight else 0
                recall=min(1,matched_idf/context['idf_mass']) if context['idf_mass'] else 0
                lexical=recall*math.sqrt(precision)
                if method=='exact':
                    precision=len(matched)/len(terms) if terms else 0
                    recall=min(1,len(matched)/context['technology_count']) if context['technology_count'] else 0
                    lexical=recall*math.sqrt(precision)
                bm25=self.bm25.score_against_reference(i,terms,query,context['idf_mass'],context['technology_count'])
                skill_contributions={
                    **importance_points,
                    'bm25':100*row_bm25_weight*bm25['normalized'],
                }
                contributions={**skill_contributions,
                    'sbert':min(max(0,100-sum(skill_contributions.values())),100*row_semantic_weight*project_score)}
                section_score=sum(contributions.values()) if scored else None
                out[section]={'score_100':round(section_score,4) if section_score is not None else None,
                    'included':scored,'excluded':excluded,'coverage':{
                        'requirement_precision':round(precision,6),'profile_recall':round(recall,6),
                        'lexical_base':round(lexical,6),'legacy_lexical_base_used_for_scoring':False,'matched_technologies':sorted(matched),
                        'matched_technology_count':len(matched),'required_technology_count':len(terms),'profile_technology_count':len(scoped_query),'total_profile_technology_count':len(query),
                        'reference_idf_mass':round(context['idf_mass'],8),'reference_technology_count':context['technology_count'],
                        'reference_basis':context['basis'],'reference_sample_count':context['sample_count']},
                    'bm25':{k:round(v,8) if isinstance(v,float) else v for k,v in bm25.items()},
                    'sbert':{'active':row_semantic_weight>0,'model':self.encoder.name if row_semantic_weight else None,
                        'comparison':'project_tasks_to_job_sentences','cosine_raw':None,
                        'project_similarity':project_match['score'],'status':project_match['status']},
                    'component_points_100':{k:round(v,6) for k,v in contributions.items()}}
                if section_score is not None:
                    weight_sum+=weight
                    bm25_sum+=weight*bm25['normalized']
            eligible=bool(known_matches) or (row_semantic_weight>0 and project_score>0)
            technical_available=bool(self.job_technologies[i]) or project_match['score'] is not None
            normalized_components={**importance_points,
                'bm25':100*row_bm25_weight*(bm25_sum/weight_sum if weight_sum else 0)}
            skill_score=sum(normalized_components.values())
            normalized_components['sbert']=min(max(0,100-skill_score),100*row_semantic_weight*project_score)
            technical=sum(normalized_components.values()) if technical_available else None
            bonus=preference_bonus.get(job['JOB_CODE'],0) if eligible else 0
            company_bonus=self._company_bonus(job.get('JOB_CORP_SCALE')) if eligible else 0
            eligible=eligible or job['JOB_CODE'] in profile.get('contest_job_codes',[])
            points=technical*self.policy['technical_points']/100 if technical is not None else None
            final=min(100,points+bonus+company_bonus+(1.5 if job['JOB_CODE'] in profile.get('contest_job_codes',[]) else 0)) if points is not None else (1.5 if job['JOB_CODE'] in profile.get('contest_job_codes',[]) else None)
            components={k:round(v*self.policy['technical_points']/100,6) if technical is not None else None for k,v in normalized_components.items()}
            results.append({'job_id':job['JOB_ANN_ID'],'job_code':job['JOB_CODE'],'company':job.get('JOB_CORP_NAME'),
                'title':job.get('JOB_ANN_TITLE'),'responsibilities':job.get('JOB_RESPONSIBILITIES'),'url':job.get('JOB_URL'),
                'score_100':round(final,2) if final is not None else None,
                'technical_score_100':round(technical,2) if technical is not None else None,
                'technical_points':round(points,2) if points is not None else None,'technical_max_points':self.policy['technical_points'],
                'score_components':components,'role_reference':context,'scoped_profile_tokens':sorted(scoped_query),'company_scale':job.get('JOB_CORP_SCALE'),'company_bonus':company_bonus,
                'importance':importance_details,'project_semantic':project_match,
                'effective_weights':{'coverage':row_coverage_weight,'bm25':row_bm25_weight,'sbert':row_semantic_weight},
                'project_scoring_policy':'independent_project_bonus_capped_at_technical_maximum',
                'preference_bonus':bonus,'preference_rank':(job_codes.index(job['JOB_CODE'])+1) if job_codes and job['JOB_CODE'] in job_codes else None,
                'eligible':eligible,'status':('SCORED' if known_matches else 'PROJECT_RELATED_ONLY') if eligible else ('NO_TECH_OVERLAP' if technical_available else 'INSUFFICIENT_TECH'),
                'provisional':bool(review_count or importance_details['groups']['neutral']['technologies']),'sections':out,'explanation':self._explain(out,profile_records,query,job.get('JOB_CODE'))})
        if excluded_job_ids:
            excluded=set(excluded_job_ids)
            results=[row for row in results if row['job_id'] not in excluded]
        results.sort(key=lambda r:(r['score_100'] is None,-(r['score_100'] or 0),r['job_id']))
        eligible_count=sum(r['eligible'] for r in results)
        if not include_unmatched:results=[r for r in results if r['eligible']]
        raw_count=len(results)
        if deduplicate:
            seen=set();unique=[]
            for row in results:
                key=self.source_key(row)
                if key in seen:continue
                seen.add(key);unique.append(row)
            results=unique
        return {'index_version':self.version,'ranking_version':RANKING_VERSION,'method':method,
            'encoder_mode':self.encoder.mode,'encoder_model':self.encoder.name,'sbert_enabled':sbert_enabled,
            'active_weights':{'coverage':coverage_weight,'bm25':bm25_weight,'sbert':semantic_weight},
            'project_experience_status':project_prepared['status'],'project_semantic_version':PROJECT_SEMANTIC_VERSION,
            'project_task_count':len(project_prepared['records']),
            'activity_scoring':False,'corpus_job_count':self.bm25.n,'raw_candidate_count':raw_count,
            'eligible_job_count':eligible_count,'candidate_count':len(results),'profile_tokens':sorted(query),
            'profile_excluded_terms':self.tokenizer.analyze('\n'.join(values))['excluded_terms'],
            'profile_unregistered_terms':self.tokenizer.analyze('\n'.join(values))['unregistered_terms'],
            'ignored_profile_statements':profile_evidence['ignored'],
            'score_notice':f'기술 적합도 {self.policy["technical_points"]}점 + 선호직무 가점 {self.policy["preference_bonuses"]} + 기업규모 가점(최대 {max(self.policy["company_scale_bonuses"].values())}점)입니다. 프로젝트 수행 내용의 관련성을 비교하며 경험의 진위·숙련도·합격 가능성은 판정하지 않습니다.',
            'results':results[:top_k]}

    @staticmethod
    def source_key(row):
        # Listing/search URLs can be shared by many distinct postings.
        # Physical content duplicates are handled in the database; identity is the posting PK.
        jid=row.get('job_id',row.get('JOB_ANN_ID'))
        return 'id:'+str(jid) if jid is not None else 'content:'+sha(row)

    @classmethod
    def from_folder(cls,folder,encoder=None):
        root=Path(folder)
        jobs=json.loads((root/'data/jobs.json').read_text(encoding='utf-8'))
        excluded=set(json.loads((root/'data/excluded_jobs.json').read_text(encoding='utf-8'))['excluded_ids'])
        return cls([job for job in jobs if job['JOB_ANN_ID'] not in excluded],
                   root/'data/labels.csv',root/'data/tech_dictionary.json',encoder,root/'data/scoring_policy.json')
