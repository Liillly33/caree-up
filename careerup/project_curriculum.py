"""Choose a main technology from gaps, then draw uniformly within its project group."""
from __future__ import annotations
import hashlib,json,secrets,threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from license_curriculum import contains
from text_processing import TechTokenizer

VERSION='project-curriculum-v4-typescript-drizzle'
MAIN_TECH={'SB':'Spring Boot','NEXT':'Next.js','FASTAPI':'FastAPI','TW':'Tailwind CSS','REDIS':'Redis','DRIZZLE':'Drizzle ORM','K8S':'Kubernetes','GHA':'GitHub Actions','TORCH':'PyTorch','VDB':'Vector Database','VERCEL':'Vercel','DOCKER':'Docker'}
ALIASES={'Spring Boot':['Spring','SpringBoot','스프링','스프링부트','스프링 부트'],'Next.js':['Nextjs','Next js'],'FastAPI':['Fast API'],'Tailwind CSS':['Tailwind','TailwindCSS','테일윈드'],'Redis':['레디스'],'Drizzle ORM':['Drizzle'],'Kubernetes':['쿠버네티스','K8s'],'GitHub Actions':['GithubActions','깃허브 액션'],'PyTorch':['파이토치'],'Vector Database':['Vector DB','벡터 데이터베이스','벡터 DB'],'Vercel':['버셀'],'Docker':['도커']}
FALLBACK={'Spring Boot':['Java'],'Next.js':['React','React.js'],'FastAPI':['Python','파이썬'],'Tailwind CSS':['CSS']}

def main_technology(row):return MAIN_TECH.get(str(row.get('PJT_CODE','')).split('_')[0])
def project_title(row):
    main=main_technology(row);name=row['PJT_NAME']
    return name if not main or name.startswith(main+' · ') else main+' · '+name

def present_project(row):
    return {'project_id':row['PJT_ID'],'project_code':row['PJT_CODE'],'project_name':project_title(row),
            'main_technology':main_technology(row),'tech_tags':row['PJT_TECH_TAGS'],'final_goal':row['PJT_FINAL_GOAL'],
            'total_days':row['PJT_TOTAL_DAYS'],'mid_goals':row['PJT_MID_GOALS'],'review_days':row['PJT_REVIEW_DAYS'],'final_criteria':row['PJT_FINAL_CRITERIA']}

class ProjectCurriculum:
    def __init__(self,transport=None,chooser=None):
        # transport is retained only for constructor compatibility; no LLM call is made.
        self.choose=chooser or secrets.choice;self._guard=threading.Lock();self._locks={}
        self.tokenizer=TechTokenizer(Path(__file__).parent/'data/tech_dictionary.json')
    def lock_for(self,key):
        with self._guard:return self._locks.setdefault(key,threading.Lock())
    def matched(self,text,tag):
        canonical=next((m for m in MAIN_TECH.values() if m.casefold()==tag.casefold()),tag)
        return any(contains(text,a) for a in [canonical,*ALIASES.get(canonical,[])])
    def candidates_for(self,road,profile,catalog):
        groups={}
        for row in sorted(catalog,key=lambda r:r['PJT_ID']):
            main=main_technology(row)
            if main:groups.setdefault(main,[]).append(row)
        sources=[('gaps',[g.strip() for g in (road.get('weak') or '').split(',') if g.strip()][:3]),
                 ('strengths',[g.strip() for g in (road.get('strong') or '').split(',') if g.strip()][:3]),
                 ('profile',[str(g) for g in profile.get('tech_skills',[])])]
        for source,texts in sources:
            if source=='gaps' and 'Drizzle ORM' in groups:
                index=next((i for i,text in enumerate(texts) if any(contains(text,alias) for alias in ('TypeScript','Type Script','타입스크립트','타입 스크립트'))),None)
                if index is not None:
                    return texts,[{**present_project(row),'basis_type':'gaps','learning_target':'TypeScript',
                        'evidence':[{'gap_index':index,'tags':['TypeScript','Drizzle ORM']}]} for row in groups['Drizzle ORM']]
            matches=[]
            for index,text in enumerate(texts):
                for main in groups:
                    if self.matched(text,main):matches.append((index,main))
            if not matches:
                for index,text in enumerate(texts):
                    for main,aliases in FALLBACK.items():
                        if main in groups and any(contains(text,a) for a in aliases):matches.append((index,main))
            if matches:
                main=min(dict.fromkeys(m for _,m in matches),key=lambda m:(-len({i for i,x in matches if x==m}),next(i for i,x in matches if x==m)))
                index=next(i for i,m in matches if m==main)
                return texts,[{**present_project(row),'basis_type':source,'evidence':[{'gap_index':index,'tags':[main]}]} for row in groups[main]]
        if not groups:return [],[]
        main={'FE':'Next.js','BE':'Spring Boot','APP':'Next.js','OPS':'Docker','SEC':'Docker','DATA':'Vector Database','AIML':'PyTorch','QA':'GitHub Actions','NET':'Kubernetes'}.get(road.get('job_code'),'Spring Boot')
        if main not in groups:main=next(iter(groups))
        return ['목표 직무에 맞춘 프로젝트 학습'],[{**present_project(row),'basis_type':'job','evidence':[{'gap_index':0,'tags':[main]}]} for row in groups[main]]
    def materialize(self,snapshot,catalog):
        by_id={p['PJT_ID']:p for p in catalog}
        return {**snapshot,'recommendations':[{**present_project(by_id[x['project_id']]),**x} for x in snapshot.get('recommendations',[]) if x.get('project_id') in by_id]}
    def recommend(self,road,profile,catalog,cached=None,generate=False,force=False):
        gaps,candidates=self.candidates_for(road,profile,catalog)
        evidence={'version':VERSION,'job_id':road['job_id'],'gaps':gaps,'candidates':candidates}
        basis=hashlib.sha256(json.dumps(evidence,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        valid=isinstance(cached,dict) and cached.get('basis_hash')==basis
        if not force and valid:return self.materialize(cached,catalog),False
        if not generate:return None,False
        previous=cached.get('recommendations',[]) if isinstance(cached,dict) else []
        previous_id=previous[0].get('project_id') if previous else None
        eligible=[p for p in candidates if not force or p['project_id']!=previous_id]
        if force and not eligible and previous and valid:return {**self.materialize(cached,catalog),'no_alternative':True},False
        result={'version':VERSION,'basis_hash':basis,'generated_at':datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),'selection_method':'main_technology_random','recommendations':[]}
        if eligible:
            project=self.choose(eligible);main=project['main_technology'];idx=project['evidence'][0]['gap_index']
            target=project.get('learning_target')
            result['recommendations']=[{'project_id':project['project_id'],'gap_index':idx,'gap':gaps[idx],'matching_tags':[main],
                 'main_technology':main,'basis_type':project['basis_type'],'reason':(f'TypeScript를 보완할 수 있도록 Drizzle ORM 기반 프로젝트를 추천합니다.' if target=='TypeScript' else f'보완이 필요한 {main} 기술을 연습할 수 있는 프로젝트입니다.' if project['basis_type']=='gaps' else f'강점을 바탕으로 {main} 프로젝트 경험을 넓힐 수 있습니다.' if project['basis_type']=='strengths' else f'{main} 기술과 프로젝트 경험을 쌓을 수 있는 학습 과제입니다.')}]
        return self.materialize(result,catalog),True
