"""Project-task / technical-job-sentence similarity, isolated from skill ownership."""
import re
import numpy as np
from text_processing import clauses,norm,sha
from profile_context import UNCERTAIN

VERSION='project-task-evidence-budget-v3'
SOURCES={'responsibilities':'JOB_RESPONSIBILITIES','requirements':'JOB_REQUIRE','preferences':'JOB_PREFERRD_QUALIFICATION'}
ACTION=re.compile(r'구현|개발|구축|설계|배포|운영|분석|수집|처리|테스트|최적화|자동화|추론|서빙|학습|예측|분류|검증|만들|작성|연동|관리|사용|활용|implement|develop|deploy|build|built|operat|analy|train|test',re.I)
CONTEXT=re.compile(r'API|컨테이너|서버|서비스|모델|데이터|파이프라인|트래픽|쿼리|캐시|분산|추론|서빙|스트리밍|배치|이벤트|인증|보안|웹|앱|코드|프론트|백엔드|알고리즘|인프라|시스템|품질|배포|container|server|model|pipeline|database',re.I)
SOFT=re.compile(r'원만|의사소통|커뮤니케이션|성실|책임감|긍정적|팀워크|인성|학력|학사|졸업|전공|communication|teamwork',re.I)

def task_units(value):
    if value is None:return []
    if not isinstance(value,str):raise ValueError('프로젝트 경험은 텍스트여야 합니다.')
    out=[];seen=set()
    for clause in clauses(value):
        for part in re.split(r'[.!?](?=\s|$)|[。！？]|(?<=했고)\s+|(?<=했으며)\s+|(?<=하고)\s+',clause):
            part=part.strip(' \t-·•')
            if not part:continue
            # Disjoint chunks prevent long text from becoming one truncated embedding.
            pieces=[];pending=''
            for word in part.split():
                if pending and len(pending)+len(word)+1>320:pieces.append(pending);pending=''
                pending=(pending+' '+word).strip()
            if pending:pieces.append(pending)
            for text in pieces:
                key=re.sub(r'[^\w+#]+','',norm(text))
                if key and key not in seen:out.append(text);seen.add(key)
    return out

def normalized_vectors(encoder,texts):
    vectors=np.asarray(encoder.encode(texts),dtype=float)
    if not len(texts):return vectors
    lengths=np.linalg.norm(vectors,axis=1,keepdims=True)
    return np.divide(vectors,lengths,out=np.zeros_like(vectors),where=lengths>0)

def job_task_units(value):
    """Split compound duties, preserving technology lists inside parentheses."""
    out=[]
    for text in task_units(value):
        start=0;depth=0
        for i,char in enumerate(text):
            if char in '([{（':depth+=1
            elif char in ')]}）':depth=max(0,depth-1)
            elif char in ',，' and depth==0:
                if text[start:i].strip():out.append(text[start:i].strip())
                start=i+1
        if text[start:].strip():out.append(text[start:].strip())
    return list(dict.fromkeys(out))

def keep_unique_semantics(records,vectors,threshold):
    chosen=[]
    for i in range(len(records)):
        comparable=[j for j in chosen if records[i].get('technologies')==records[j].get('technologies')]
        if comparable and np.any(vectors[i] @ vectors[comparable].T>=threshold):continue
        chosen.append(i)
    return [records[i] for i in chosen],vectors[chosen]

class ProjectSemanticIndex:
    def __init__(self,jobs,encoder,classifier,tokenizer,reviews,policy):
        self.encoder=encoder;self.classifier=classifier;self.tokenizer=tokenizer;self.policy=policy;self.rows=[]
        raw=[]
        for job in jobs:
            records=[];seen=set()
            # Higher-weight sections retain duplicated sentences first.
            for section in sorted(SOURCES,key=lambda s:-policy['section_weights'][s]):
                for text in job_task_units(job.get(SOURCES[section])):
                    key=re.sub(r'[^\w+#]+','',norm(text))
                    if key in seen:continue
                    seen.add(key);records.append({'text':text,'section':section,'source_column':SOURCES[section],
                                                 'technologies':sorted(set(tokenizer.tokens(text)))})
            raw.append(records)
        if encoder.mode!='sbert':self.rows=[{'records':[],'vectors':None} for _ in jobs];return
        decisions=classifier.classify_many([r['text'] for rs in raw for r in rs])
        selected=[]
        for job,records in zip(jobs,raw):
            excluded={t for t,l in reviews.get(job['JOB_ANN_ID'],{}).items() if l=='제외'}
            kept=[]
            for record in records:
                text=record['text'];analysis=tokenizer.analyze(text);tokens=set(analysis['tokens'])
                if tokens and not tokens-excluded:continue
                if analysis['excluded_terms'] and not tokens and not CONTEXT.search(text):continue
                record={**record,'technologies':sorted(tokens-excluded)}
                if self.is_technical(text,decisions[text],project=False):kept.append(record)
            selected.append(kept)
        texts=list(dict.fromkeys(r['text'] for rs in selected for r in rs))
        vectors=normalized_vectors(encoder,texts);by_text=dict(zip(texts,vectors))
        for records in selected:
            if records:
                matrix=np.stack([by_text[r['text']] for r in records])
                records,matrix=keep_unique_semantics(records,matrix,policy['duplicate_cosine'])
            else:matrix=None
            self.rows.append({'records':records,'vectors':matrix})

    def is_technical(self,text,decision,project):
        tokens=self.tokenizer.tokens(text)
        if SOFT.search(text) and not tokens and not CONTEXT.search(text):return False
        if project and (UNCERTAIN.search(norm(text)) or not ACTION.search(text)):return False
        if decision['label']=='NONTECH':return False
        if decision['label']=='TECH':return True
        if not project and tokens:return True
        # Conservative fallback for technical tasks without literal dictionary skills.
        return bool(ACTION.search(text) and (tokens or CONTEXT.search(text)))

    def prepare(self,text,enabled=True):
        units=task_units(text)
        if not units:return {'status':'NOT_PROVIDED','records':[],'vectors':None}
        if not enabled or self.encoder.mode!='sbert':return {'status':'SEMANTIC_DISABLED','records':[],'vectors':None}
        decisions=self.classifier.classify_many(units)
        records=[{'text':t,'technologies':sorted(set(self.tokenizer.tokens(t)))} for t in units if self.is_technical(t,decisions[t],project=True)]
        if not records:return {'status':'NO_SCORABLE_PROJECT_TASKS','records':[],'vectors':None}
        vectors=normalized_vectors(self.encoder,[r['text'] for r in records])
        records,vectors=keep_unique_semantics(records,vectors,self.policy['duplicate_cosine'])
        return {'status':'AVAILABLE','records':records,'vectors':vectors}

    def score(self,index,prepared):
        if prepared['status']!='AVAILABLE':return {'status':prepared['status'],'score':None,'evidence':[],'sections':{}}
        job=self.rows[index]
        if not job['records']:return {'status':'NO_TECH_JOB_SENTENCES','score':None,'evidence':[],'sections':{}}
        similarities=np.clip(job['vectors'] @ prepared['vectors'].T,-1,1)
        best=np.argmax(similarities,axis=1);evidence=[];groups={}
        low=self.policy['similarity_floor'];high=self.policy['similarity_ceiling']
        for i,(record,p) in enumerate(zip(job['records'],best)):
            cosine=float(similarities[i,p]);semantic=max(0.0,min(1.0,(cosine-low)/(high-low)))
            terms=set(record['technologies'])
            explicit=terms & {t for task in prepared['records'] for t in task['technologies']}
            coverage=len(explicit)/len(terms) if terms else None
            # Explicit performed project evidence belongs to this component only.
            # It never enters the owned-skill query, BM25, or core/auxiliary coverage.
            grounding=self.policy.get('explicit_evidence_weight',.25) if terms else 0.0
            score=(1-grounding)*semantic+grounding*(coverage or 0.0)
            item={**record,'project_text':prepared['records'][int(p)]['text'],'cosine':cosine,
                  'semantic_similarity':semantic,'explicit_technology_coverage':coverage,
                  'matched_project_technologies':sorted(explicit),'normalized_similarity':score}
            evidence.append(item);groups.setdefault(record['section'],[]).append(item)
        sections={name:{'score':sum(r['normalized_similarity'] for r in rs)/len(rs),'sentence_count':len(rs),
                        'weight':self.policy['section_weights'][name]} for name,rs in groups.items()}
        total=sum(r['weight'] for r in sections.values())
        semantic_score=sum(sum(x['semantic_similarity'] for x in groups[name])/len(groups[name])*r['weight'] for name,r in sections.items())/total
        # Six distinct, explicitly demonstrated technologies share a fixed budget.
        # A new required technology must not disappear in a long posting's average.
        # This is computed for manually written experience too, never completion count.
        budget=self.policy.get('evidence_budget',{'slots':6,'semantic_weight':.2,'explicit_weight':.8,'similarity_floor':.4,'similarity_full_credit':.5})
        technology_evidence={}
        for i,record in enumerate(job['records']):
            priority=self.policy['section_weights'][record['section']]/max(self.policy['section_weights'].values())
            for term in record['technologies']:
                candidates=[k for k,t in enumerate(prepared['records']) if term in t['technologies']]
                if not candidates:continue
                k=max(candidates,key=lambda k:similarities[i,k]);cosine=float(similarities[i,k])
                confidence=max(0.0,min(1.0,(cosine-budget['similarity_floor'])/(budget['similarity_full_credit']-budget['similarity_floor'])))*priority
                if confidence>technology_evidence.get(term,{}).get('credit',0):
                    technology_evidence[term]={'credit':confidence,'cosine':cosine,'job_text':record['text'],'project_text':prepared['records'][k]['text']}
        selected=sorted(technology_evidence.items(),key=lambda x:(-x[1]['credit'],x[0]))[:budget['slots']]
        grounded_score=sum(item['credit'] for _,item in selected)/budget['slots']
        score=budget['semantic_weight']*semantic_score+budget['explicit_weight']*grounded_score
        return {'status':'SCORED','score':score,'sections':sections,'evidence':evidence,
                'semantic_score':semantic_score,'technology_evidence':technology_evidence,
                'grounded_score':grounded_score,'evidence_budget_slots':budget['slots'],
                'combination':'semantic_20_explicit_task_evidence_80',
                'project_task_count':len(prepared['records']),'job_sentence_count':len(job['records']),
                'normalization':{'floor':low,'ceiling':high,'calibration':'initial_policy_not_fit_to_human_relevance'}}
