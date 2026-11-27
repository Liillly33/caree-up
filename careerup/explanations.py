"""Evidence-bound summaries. This module never changes matching scores.
No course completion, experience duration, proficiency or soft skills are inferred.
"""
import json
import re
from pathlib import Path
from text_processing import norm

RULES=json.loads((Path(__file__).parent/'data/explanation_rules.json').read_text(encoding='utf-8'))
GROUPS=RULES['groups']
# Explicit technology groups take precedence over broad dictionary categories.
TOKEN_GROUP={norm(t):g for g in GROUPS for t in g.get('tokens',[])}
PAST_ACTION=re.compile(r'(?:구현|개발|구축|제작|배포|운영|완성)\s*(?:했|함|완료|경험|$)|프로젝트.{0,15}(?:완료|구축|개발)|\b(?:built|implemented|deployed|operated|developed)\b',re.I)


def group_for(token,tokenizer):
    if tokenizer.categories.get(token)=='certificate':return next(g for g in GROUPS if g['id']=='certificate')
    if token in TOKEN_GROUP:return TOKEN_GROUP[token]
    return next((g for g in GROUPS if tokenizer.categories.get(token) in g.get('categories',[])),GROUPS[-1])


def display_tokens(tokens,tokenizer):
    display={norm(e['canonical']):e['canonical'] for e in tokenizer.config['entries']}
    return ' / '.join(display.get(t,t) for t in tokens[:3])+(' 등' if len(tokens)>3 else '')


def metadata(records,tokens,profile,matched,group):
    first=records[0];evidence=list(dict.fromkeys(r['text'] for r in records))
    return {'technology':next((t for t in tokens if t in first['tokens']),tokens[0]),'technologies':tokens,'domain':group['id'],
            'section':first['section'],'item_id':first['item_id'],'item_ids':list(dict.fromkeys(r['item_id'] for r in records)),
            'source_column':first.get('source_column','JOB_REQUIRE' if first['section']=='requirements' else 'JOB_PREFERRD_QUALIFICATION'),
            'job_evidence':evidence[0],'job_evidences':evidence,'technical_evidence':first.get('match_text',first['text']),
            'profile_evidence':list(dict.fromkeys(r['text'] for r in profile if set(r['technologies']) & set(tokens))) if matched else [],
            'label_origin':first.get('label_origin'),'evidence_type':'technology' if matched else 'job_requirement'}


def build_explanation(sections,profile_records,query_tokens,tokenizer,idf,job_code=None):
    query=set(query_tokens);records=[]
    for section in ('requirements','preferences'):
        records.extend({**r,'section':section} for r in sections[section]['included'])
    def priority(record):
        return (record['section']!='requirements',-sum(idf.get(t,0) for t in set(record['tokens'])),record['item_id'])
    records.sort(key=priority)
    groups={}
    for record in records:
        for token in set(record['tokens']) & query:
            group=group_for(token,tokenizer);bucket=groups.setdefault(group['id'],{'group':group,'tokens':set(),'records':{}})
            bucket['tokens'].add(token);bucket['records'].setdefault(record['item_id'],record)
    ranked=sorted(groups.values(),key=lambda g:(min(priority(r)[0] for r in g['records'].values()),-sum(idf.get(t,0) for t in g['tokens']),g['group']['id']))
    strengths=[]
    for bucket in ranked[:3]:
        group=bucket['group'];source=sorted(bucket['records'].values(),key=priority)
        tokens=sorted(bucket['tokens'],key=lambda t:(-idf.get(t,0),t));names=display_tokens(tokens,tokenizer)
        item=metadata(source,tokens,profile_records,True,group)
        # An experience title is allowed only when EVERY displayed technology has positive user action evidence.
        experience={t:[r['text'] for r in profile_records if t in r['technologies'] and PAST_ACTION.search(r['text'])] for t in tokens}
        has_experience=all(experience.values())
        if group['id']=='certificate':message=f'{names} 자격 보유'
        elif has_experience:
            deploy=all(any('배포' in text or re.search(r'\bdeployed\b',text,re.I) for text in texts) for texts in experience.values())
            message=f'{names} 기반 서비스 배포 경험' if deploy else f'{names} 활용 개발·구현 경험'
        else:
            title=RULES['language_roles'].get(job_code,group['title']) if group['id']=='language' else group['title']
            message=f'{names} 기반 {title}'
        item.update(message=message,evidence_level='reported_experience' if has_experience else 'reported_technology',explanation_version=RULES['version'])
        strengths.append(item)
    improvements=[];used=set()
    for record in records:
        missing=set(record['tokens'])-query
        if not missing or not missing-used:continue
        # One original condition produces at most one point, including partial matches.
        tokens=sorted(missing-used,key=lambda t:(-idf.get(t,0),t));names=display_tokens(tokens,tokenizer)
        group=group_for(tokens[0],tokenizer)
        item=metadata([record],tokens,profile_records,False,group)
        text=record['text'];experience=bool(re.search(r'경험|실무|프로젝트|포트폴리오|experience',text,re.I))
        if all(tokenizer.categories.get(t)=='certificate' for t in tokens):message=f'{names} 자격 취득 준비'
        elif '포트폴리오' in text:message=f'{names} 활용 포트폴리오 구성'
        elif '배포' in text:message=f'{names} 기반 서비스 배포 '+('경험 보완' if experience else '연습')
        elif '운영' in text:message=f'{names} 기반 운영 '+('경험 보완' if experience else '연습')
        elif '설계' in text:message=f'{names} 기반 설계 '+('경험 보완' if experience else '연습')
        elif '프로젝트' in text:message=f'{names} 활용 프로젝트 구현 경험 보완'
        elif experience:message=f'{names} 활용 경험 보완'
        else:message=f'{names} 관련 기술 보완'
        item.update(message=message,raw_message=text,all_missing_technologies=sorted(missing),recommendation=True,explanation_version=RULES['version'])
        improvements.append(item);used.update(missing)
        if len(improvements)==3:break
    return {'strengths':strengths,'improvements':improvements,'explanation_version':RULES['version'],
            'note':'공고와 사용자 입력에 근거한 기술 요약입니다. 과목 이수·경력연수·숙련도·합격 가능성을 추정하지 않습니다.',
            'limited_evidence':len(strengths)<3 or len(improvements)<3}
