"""Contextual importance proposals. User labels always override these rules."""
import re
from text_processing import clauses,sha

ROLE_CORE={
 'AIML':{'python','pytorch','tensorflow','r','scikit-learn','onnx','cuda'},
 'BE':{'python','java','c','c++','c#','go','rust','kotlin','php','ruby','spring','fastapi','django','flask','node.js','.net','express','nestjs'},
 'FE':{'javascript','typescript','react','vue.js','angular','angularjs','next.js','html','css','tailwind css'},
 'APP':{'swift','kotlin','java','objective-c','flutter','dart','react native'},
 'OPS':{'aws','azure','gcp','linux','docker','kubernetes','terraform','ansible','ci/cd','github actions','jenkins','argo cd'},
 'DATA':{'python','r','sql','spark','hadoop','airflow','kafka','mysql','postgresql','oracle','sql server','mongodb','snowflake'},
 'ERP':{'java','c#','.net','spring','sql','oracle','sql server','sap','abap'},
 'IOT':{'c','c++','rust','linux','rtos','freertos','fpga','cuda'},
 'NET':{'tcp/ip','ethernet','linux','cisco','wireshark','ccna','ccnp'},
 'SEC':{'linux','tcp/ip','http','ssl','wireshark','python','c','c++'},
 'QA':{'python','java','javascript','selenium','appium','jmeter','pytest','postman'},
 'GAME':{'c++','c#','unity','unreal engine','lua'},
}
ACTION=re.compile(r'개발|구현|설계|운영|구축|배포|분석|활용|작성|관리|최적화|실무|경험|능숙|이해|숙련|가능|다룰|기반',re.I)
OPTIONAL=re.compile(r'우대|있으면|선호|좋(?:아요|습니다|겠)|플러스|preferred|nice.to.have',re.I)
MANDATORY=re.compile(r'필수|반드시|꼭.{0,8}(?:필요|있어야)|must|required',re.I)
STACK=re.compile(r'기술\s*스택|사용\s*언어|개발환경|tech\s*stack',re.I)
RULE_VERSION=sha({'role_core':{k:sorted(v) for k,v in ROLE_CORE.items()},'action':ACTION.pattern,
                  'optional':OPTIONAL.pattern,'mandatory':MANDATORY.pattern,'stack':STACK.pattern,'precedence_version':1})

def assign_importance(job,terms,human,tokenizer):
    mentions={t:[] for t in terms}
    for col in ('JOB_REQUIRE','JOB_RESPONSIBILITIES','JOB_PREFERRD_QUALIFICATION'):
        in_stack=False
        for text in clauses(job.get(col)):
            if STACK.search(text):in_stack=True
            elif re.match(r'^\s*[\[【○]',text):in_stack=False
            for t in set(tokenizer.tokens(text)) & terms:
                mentions[t].append({'source_column':col,'text':text,'stack_list':in_stack})
    role=ROLE_CORE.get(job.get('JOB_CODE'),set());out={}
    for token in sorted(terms):
        rows=mentions[token]
        if token in human:
            out[token]={'label':human[token],'source':'USER','reason':'사용자 검토 판정 우선','evidence':rows[:3]};continue
        req=[r for r in rows if r['source_column']=='JOB_REQUIRE' and not OPTIONAL.search(r['text'])]
        duty=[r for r in rows if r['source_column']=='JOB_RESPONSIBILITIES' and not OPTIONAL.search(r['text'])]
        pref=[r for r in rows if r['source_column']=='JOB_PREFERRD_QUALIFICATION' or OPTIONAL.search(r['text'])]
        explicit=[r for r in req if MANDATORY.search(r['text']) and not r['stack_list']]
        direct=[r for r in duty if token in role and ACTION.search(r['text']) and not r['stack_list']]
        required=[r for r in req if ACTION.search(r['text']) and not r['stack_list']]
        if explicit:label='핵심';reason='자격요건의 명시적인 필수 요구';evidence=explicit
        elif direct:label='핵심';reason='목표 직무의 대표 기술이며 주요업무 수행에 직접 등장';evidence=direct
        elif required:label='핵심';reason='자격요건에서 해당 기술의 경험·활용 역량을 요구';evidence=required
        elif req and token in role:label='핵심';reason='자격요건에 등장하며 직무별 대표 기술과 일치하는 추정';evidence=req
        elif pref:label='보조';reason='우대사항 또는 우대 표현에 등장';evidence=pref
        else:label='판단 불가';reason='직무·요구 문맥만으로 중요도를 확정하기 어려움';evidence=rows
        out[token]={'label':label,'source':'AUTO_RULE','reason':reason,'evidence':evidence[:3]}
    return out
