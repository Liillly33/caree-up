from contextlib import asynccontextmanager
import logging
from datetime import date
from typing import Literal
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict, SecretStr
from uuid import UUID
from recommender import Recommender, SentenceEncoder, RANKING_VERSION
from supabase_reader import SupabaseReader, ReaderError
from license_curriculum import LicenseCurriculum
from project_curriculum import ProjectCurriculum, present_project
from gemini_reason import GeminiReasonGenerator, ReasonError
from sync_jobs import refresh_snapshot

logger=logging.getLogger('uvicorn.error')
ROOT=Path(__file__).resolve().parent
class RecommendationRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    top_k:int=Field(default=5,ge=5,le=5)
    use_preferred_jobs:bool=True

class RoadmapRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    match_id:str=Field(min_length=1,max_length=20,pattern=r'^[0-9]+$')
    match_rank:int=Field(ge=1,le=5)

class CurriculumRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    force:bool=False

class ProjectRequest(CurriculumRequest):
    road_id:int=Field(ge=1,le=2147483647)

class ProjectSelection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    road_id:int=Field(ge=1,le=2147483647)
    project_id:int=Field(ge=1,le=2147483647,strict=True)

class AdventureStart(BaseModel):
    model_config=ConfigDict(extra='forbid')
    road_id:int=Field(ge=1,le=2147483647)
    kind:Literal['project','certificate','contest']
    project_id:int|None=Field(default=None,ge=1,le=2147483647,strict=True)
    lc_id:int|None=Field(default=None,ge=1,le=2147483647,strict=True)
    contest_id:str|None=Field(default=None,min_length=1,max_length=50)
    expected_run_id:UUID|None=None
    password:SecretStr|None=Field(default=None,max_length=256)

class AdventureRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['start','add','edit','delete','toggle','boss','dates','test_on','test_off','test_reset']
    args:dict=Field(default_factory=dict)

def validate_adventure(body):
    allowed={'start':set(),'add':{'text'},'edit':{'id','text'},'delete':{'id'},'toggle':{'id'},'boss':{'stage','password'},'dates':{'written','practical','result'},'test_on':{'date'},'test_reset':{'date'},'test_off':set()}[body.action]
    if body.action=='start' and 'lc_id' in body.args:allowed=allowed|{'lc_id'}
    if body.action=='dates' and 'third' in body.args:allowed=allowed|{'third'}
    if body.action in ('test_on','test_reset') and 'time' in body.args:allowed=allowed|{'time'}
    if set(body.args)!=allowed or any(not isinstance(v,str) for v in body.args.values()):raise HTTPException(422,'입력 항목을 확인해주세요.')
    for key,value in body.args.items():
        if len(value)>200:raise HTTPException(422,'입력 내용이 너무 깁니다.')
        if key=='time':
            import re
            if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',value):raise HTTPException(422,'시간을 HH:MM 형식으로 입력해주세요.')
        if key=='lc_id' and (not value.isdecimal() or not 1<=int(value)<=2147483647):raise HTTPException(422,'시험을 선택해주세요.')
        if key in ('practical','third') and value=='':continue
        if key in ('written','practical','third','result','date'):
            try:
                if date.fromisoformat(value).isoformat()!=value:raise ValueError()
            except ValueError:raise HTTPException(422,'날짜를 YYYY-MM-DD 형식으로 입력해주세요.') from None

def roadmap_evidence(items):
    # Internal commas become middle dots so the requested comma-separated TEXT roundtrips.
    return ', '.join(' '.join(item['message'].split()).replace(',',' ·') for item in items[:3])

def create_app(recommender=None,reader=None,reason_generator=None,curriculum_generator=None,project_generator=None):
    @asynccontextmanager
    async def lifespan(app):
        if recommender is None:
            import time
            started=time.perf_counter()
            logger.info('Supabase 최신 공고 동기화 중…')
            app.state.snapshot=refresh_snapshot(ROOT)
            logger.info('공고 동기화 완료: %.1f초',time.perf_counter()-started)
            started=time.perf_counter()
            logger.info('Supabase 최신 공고 %s개를 동기화했습니다. 추천 인덱스를 구성합니다.',app.state.snapshot['count'])
            app.state.recommender=Recommender.from_folder(ROOT)
            logger.info('추천 인덱스 준비: %.1f초 · 문장 캐시 %s',time.perf_counter()-started,getattr(getattr(app.state.recommender,'encoder',None),'stats',{}))
        else:
            app.state.snapshot=None
            app.state.recommender=recommender
        app.state.reader=reader if reader is not None else SupabaseReader()
        app.state.reason_generator=reason_generator if reason_generator is not None else GeminiReasonGenerator()
        app.state.curriculum=curriculum_generator if curriculum_generator is not None else LicenseCurriculum()
        app.state.project_curriculum=project_generator if project_generator is not None else ProjectCurriculum()
        logger.info('추천 API v8 / Gemini %s',getattr(app.state.reason_generator,'model','test'))
        try:yield
        finally:
            if recommender is None and hasattr(getattr(app.state.recommender,'encoder',None),'close'):app.state.recommender.encoder.close()
    app=FastAPI(title='CAREE-UP 채용공고 추천',lifespan=lifespan)
    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(request,exc):
        # Never echo submitted passwords or request bodies in validation responses.
        return JSONResponse(status_code=422,content={'detail':'입력 항목을 확인해주세요.'})
    @app.post('/api/recommendations')
    def recommendations(body:RecommendationRequest,authorization:str|None=Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'로그인 후 이용해주세요.')
        try:
            own=app.state.reader.read_own_profile(authorization[7:])
            road=app.state.reader.current_roadmap(authorization[7:])
            excluded={'excluded_job_ids':[road['job_id']]} if road else {}
            result=app.state.recommender.recommend(own['profile'],own['job_codes'] if body.use_preferred_jobs else None,body.top_k,**excluded)
            # Only five highest-scoring candidates reach Gemini; one batch API call.
            rows=result['results'][:5]
            reasons=app.state.reason_generator.generate(rows)
            if len(reasons)!=len(rows):raise ReasonError('추천 이유 개수가 일치하지 않습니다.')
            for row,reason in zip(rows,reasons):row['reason']=reason
            saved=app.state.reader.save_results(authorization[7:],rows) if rows else None
            # Return only presentation fields; detailed diagnostics stay in Python/CLI.
            fields=('job_id','company','title','url','score_100','reason')
            return {'results':[{k:r.get(k) for k in fields} for r in rows],
                    'candidate_count':result['candidate_count'],'saved':saved}
        except ReasonError as exc:
            logger.warning('추천 실패 [%s]: %s',exc.code,str(exc))
            raise HTTPException(exc.status,str(exc),headers={'X-Careerup-Error':exc.code}) from None
        except ReaderError as exc:
            logger.warning('추천 기록/스펙 요청 실패 [%s]: %s',exc.code,str(exc))
            raise HTTPException(exc.status,str(exc),headers={'X-Careerup-Error':exc.code}) from None
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    @app.get('/api/history')
    def history(offset:int=0,authorization:str|None=Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'로그인 후 이용해주세요.')
        if offset<0:raise HTTPException(400,'잘못된 페이지입니다.')
        try:
            body=app.state.reader.history(authorization[7:],offset)
            sessions=body.get('sessions',[])
            return {'sessions':sessions[:5],'has_more':len(sessions)>5 or bool(body.get('has_more')),'next_offset':offset+5,'page':offset//5+1}
        except ReaderError as exc:
            logger.warning('추천 기록/스펙 요청 실패 [%s]: %s',exc.code,str(exc))
            raise HTTPException(exc.status,str(exc),headers={'X-Careerup-Error':exc.code}) from None
    def present_saved_roadmap(road,token):
        # Old rows contain the former raw token message. Upgrade the view without rewriting historical DB rows.
        if not road:return road
        strong=road.get('strong') or '';weak=road.get('weak') or ''
        legacy='공고의 기술명과 입력한 스펙이 일치합니다' in strong or (not strong and bool(weak) and not weak.endswith(('보완','준비','연습','구성')))
        if not legacy:return road
        try:
            own=app.state.reader.read_own_profile(token)
            explanation=app.state.recommender.explain_job(own['profile'],road['job_id'])
            return {**road,'strong':roadmap_evidence(explanation.get('strengths',[])),'weak':roadmap_evidence(explanation.get('improvements',[])),'explanation_version':'evidence-summary-v2','evidence_basis':'current_profile'}
        except (ReaderError,KeyError,ValueError):return road
    @app.get('/api/roadmaps/current')
    def current_roadmap(authorization:str|None=Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'로그인 후 이용해주세요.')
        try:return {'roadmap':present_saved_roadmap(app.state.reader.current_roadmap(authorization[7:]),authorization[7:])}
        except ReaderError as exc:raise HTTPException(exc.status,str(exc)) from None
    @app.post('/api/roadmaps')
    def create_roadmap(body:RoadmapRequest,authorization:str|None=Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'로그인 후 이용해주세요.')
        token=authorization[7:]
        try:
            own=app.state.reader.read_own_profile(token)
            selected=app.state.reader.match_result(token,body.match_id,body.match_rank)
            try:explanation=app.state.recommender.explain_job(own['profile'],selected['JOB_ANN_ID'])
            except KeyError:raise HTTPException(409,'공고 데이터를 갱신한 뒤 다시 시도해주세요.') from None
            roadmap=app.state.reader.create_roadmap(token,body.match_id,body.match_rank,
                roadmap_evidence(explanation.get('strengths',[])),roadmap_evidence(explanation.get('improvements',[])))
            response={'roadmap':roadmap}
            try:
                if hasattr(app.state.reader,'license_catalog'):
                    automatic=curriculum_response(token,True,False,roadmap['road_id'])
                    response['curriculum']=automatic['curriculum']
            except (ReaderError,ReasonError,HTTPException) as exc:
                response['curriculum_error']=str(exc.detail if isinstance(exc,HTTPException) else exc)
            try:
                if hasattr(app.state.reader,'project_catalog'):
                    response['project_curriculum']=project_response(token,True,False,roadmap['road_id'])
            except (ReaderError,ReasonError,HTTPException) as exc:
                response['project_curriculum_error']=str(exc.detail if isinstance(exc,HTTPException) else exc)
            return response
        except ReaderError as exc:raise HTTPException(exc.status,str(exc)) from None
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
    def curriculum_response(token,generate=False,force=False,expected_road_id=None):
        reader=app.state.reader
        own=reader.read_own_profile(token)
        road=present_saved_roadmap(reader.current_roadmap(token),token)
        if not road:raise HTTPException(404,'먼저 커리어 로드맵을 생성해주세요.')
        if expected_road_id is not None and road['road_id']!=expected_road_id:raise HTTPException(409,'현재 로드맵이 변경되었습니다.')
        try:
            explanation=app.state.recommender.explain_job(own['profile'],road['job_id'])
            road={**road,'technical_requirements':list(dict.fromkeys(x.get('technical_evidence','') for x in explanation.get('improvements',[])))[:6]}
            if hasattr(reader,'adventure'):
                adventure=reader.adventure(token).get('adventure')
                if adventure and adventure.get('road_id')==road['road_id'] and adventure.get('state',{}).get('final_at'):
                    road={**road,'weak':roadmap_evidence(explanation.get('improvements',[])),'strong':roadmap_evidence(explanation.get('strengths',[]))}
        except (KeyError,ValueError):road={**road,'technical_requirements':[]}
        with app.state.curriculum.lock_for((own['user_id'],road['road_id'])):
            catalog=reader.license_catalog(token);cached=reader.license_recommendations(token,road['road_id'])
            result,changed=app.state.curriculum.recommend(road,own['profile'],catalog,cached,generate,force)
            if changed:reader.save_license_recommendations(token,road['road_id'],result)
        return {'road_id':road['road_id'],'curriculum':result,'cached':not changed and result is not None}
    @app.get('/api/curriculum/recommendations')
    def get_curriculum(authorization:str|None=Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'로그인 후 이용해주세요.')
        try:return curriculum_response(authorization[7:])
        except ReaderError as exc:raise HTTPException(exc.status,str(exc)) from None
    @app.post('/api/curriculum/recommendations')
    def generate_curriculum(body:CurriculumRequest,authorization:str|None=Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'로그인 후 이용해주세요.')
        try:return curriculum_response(authorization[7:],True,body.force)
        except (ReaderError,ReasonError) as exc:raise HTTPException(exc.status,str(exc)) from None
    def project_response(token,generate=False,force=False,expected_road_id=None):
        reader=app.state.reader
        own=reader.read_own_profile(token)
        road=present_saved_roadmap(reader.current_roadmap(token),token)
        if not road:raise HTTPException(404,'먼저 커리어 로드맵을 생성해주세요.')
        if expected_road_id is not None and road['road_id']!=expected_road_id:
            raise HTTPException(409,'현재 로드맵이 변경되었습니다. 다시 불러와주세요.')
        try:
            explanation=app.state.recommender.explain_job(own['profile'],road['job_id'])
            road={**road,'technical_requirements':list(dict.fromkeys(x.get('technical_evidence','') for x in explanation.get('improvements',[])))[:6]}
            if hasattr(reader,'adventure'):
                adventure=reader.adventure(token).get('adventure')
                if adventure and adventure.get('road_id')==road['road_id'] and adventure.get('state',{}).get('final_at'):
                    road={**road,'weak':roadmap_evidence(explanation.get('improvements',[])),'strong':roadmap_evidence(explanation.get('strengths',[]))}
        except (KeyError,ValueError):road={**road,'technical_requirements':[]}
        with app.state.project_curriculum.lock_for((own['user_id'],road['road_id'])):
            catalog=reader.project_catalog(token)
            state=reader.project_recommendations(token,road['road_id'])
            result,changed=app.state.project_curriculum.recommend(road,own['profile'],catalog,state.get('curriculum'),generate,force)
            if changed:reader.save_project_recommendations(token,road['road_id'],result)
            selected=next((present_project(p) for p in catalog if p['PJT_ID']==state.get('selected_project_id')),None)
        return {'road_id':road['road_id'],'curriculum':result,'selected_project':selected,'cached':not changed and result is not None}

    @app.get('/api/projects/recommendations')
    def get_projects(road_id:int,authorization:str|None=Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'로그인 후 이용해주세요.')
        try:return project_response(authorization[7:],expected_road_id=road_id)
        except (ReaderError,ReasonError) as exc:raise HTTPException(exc.status,str(exc)) from None

    @app.post('/api/projects/recommendations')
    def generate_project(body:ProjectRequest,authorization:str|None=Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'로그인 후 이용해주세요.')
        try:return project_response(authorization[7:],True,body.force,body.road_id)
        except (ReaderError,ReasonError) as exc:raise HTTPException(exc.status,str(exc)) from None

    @app.post('/api/projects/select')
    def select_project(body:ProjectSelection,authorization:str|None=Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'로그인 후 이용해주세요.')
        token=authorization[7:]
        try:
            road=app.state.reader.current_roadmap(token)
            if not road or road['road_id']!=body.road_id:raise HTTPException(409,'현재 로드맵이 변경되었습니다.')
            return start_adventure_response(token,AdventureStart(road_id=body.road_id,kind='project',project_id=body.project_id))
        except (ReaderError,ReasonError) as exc:raise HTTPException(exc.status,str(exc)) from None
    def start_adventure_response(token,body):
        reader=app.state.reader
        road=reader.current_roadmap(token)
        if not road or road['road_id']!=body.road_id:raise HTTPException(409,'현재 로드맵이 변경되었습니다. 다시 불러와주세요.')
        if body.kind=='contest' and (not body.contest_id or body.project_id is not None or body.lc_id is not None):raise HTTPException(422,'공모전을 선택해주세요.')
        if (body.kind=='project' and (body.project_id is None or body.lc_id is not None)) or (body.kind=='certificate' and (body.lc_id is None or body.project_id is not None)):
            raise HTTPException(422,'시작할 커리큘럼을 하나 선택해주세요.')
        current=reader.adventure(token).get('adventure')
        run=current.get('run_id') if current else None
        if str(body.expected_run_id or '')!=str(run or ''):
            raise HTTPException(409,'커리큘럼 상태가 변경되었습니다. 다시 선택해주세요.')
        requires_password=bool(current and not current.get('state',{}).get('final_at'))
        if requires_password and (body.password is None or not body.password.get_secret_value()):
            raise HTTPException(409,'이미 진행중인 셀프 커리큘럼가 있어요! 비밀번호를 확인해주세요.')
        args={'road_id':str(body.road_id),'expected_run_id':str(body.expected_run_id or ''),
              ('contest_id' if body.kind=='contest' else 'project_id' if body.kind=='project' else 'lc_id'):str(body.contest_id if body.kind=='contest' else body.project_id if body.kind=='project' else body.lc_id)}
        verified=None
        try:
            if requires_password:verified=reader.verify_restart_password(token,body.password.get_secret_value())
            result=reader.adventure(verified or token,'start',args)
        finally:
            if verified:reader.close_restart_session(verified)
        return adventure_response(token,result)

    @app.get('/api/contests/recommendations')
    def contests(force:bool=False,authorization:str|None=Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'로그인 후 이용해주세요.')
        token=authorization[7:]
        try:
            road=app.state.reader.current_roadmap(token)
            if not road:raise HTTPException(404,'로드맵을 먼저 생성해주세요.')
            adventure=app.state.reader.adventure(token).get('adventure')
            from contest import recommend
            from datetime import datetime
            from zoneinfo import ZoneInfo
            day=adventure['today'] if adventure and adventure.get('test_mode') else datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
            return recommend(app.state.reader,token,road,day,force)
        except ReaderError as exc:raise HTTPException(exc.status,str(exc)) from None

    @app.post('/api/adventure/start')
    def start_adventure(body:AdventureStart,authorization:str|None=Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'로그인 후 이용해주세요.')
        try:return start_adventure_response(authorization[7:],body)
        except ReaderError as exc:raise HTTPException(exc.status,str(exc)) from None
    def adventure_response(token,result):
        adventure=result.get('adventure')
        if adventure and adventure.get('state',{}).get('final_at'):
            try:
                own=app.state.reader.read_own_profile(token)
                road=app.state.reader.current_roadmap(token)
                if adventure.get('road_id') is not None and (not road or road['road_id']!=adventure['road_id']):
                    road=app.state.reader.adventure_roadmap(token,adventure['road_id'])
                if not road:raise ReaderError('목표 공고를 확인할 수 없습니다.',404)
                # Read the committed profile AFTER the completion RPC has reflected the result.
                scored=app.state.recommender.recommend(own['profile'],own['job_codes'],top_k=1,include_unmatched=True,deduplicate=False,target_job_id=road['job_id'])
                job=next((j for j in scored['results'] if j['job_id']==road['job_id']),None)
                if job is None:raise ReaderError('목표 공고가 현재 공고 스냅샷에 없습니다.',404)
                result['match_report']={'road_id':road['road_id'],'run_id':adventure.get('run_id'),'score_100':job['score_100'],'explanation':job['explanation'],'previous_score':road['score_100']}
            except Exception:
                # Completion and spec reflection are already committed; a scoring failure is retryable.
                logger.exception('Completed curriculum score refresh failed')
                result['report_pending']=True
        return result
    @app.get('/api/adventure')
    def get_adventure(authorization:str|None=Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'로그인 후 이용해주세요.')
        token=authorization[7:]
        try:return adventure_response(token,app.state.reader.adventure(token))
        except ReaderError as exc:raise HTTPException(exc.status,str(exc)) from None
    @app.post('/api/adventure')
    def change_adventure(body:AdventureRequest,authorization:str|None=Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'로그인 후 이용해주세요.')
        validate_adventure(body);token=authorization[7:]
        try:
            if body.action=='start':
                road=app.state.reader.current_roadmap(token)
                if not road:raise HTTPException(404,'먼저 로드맵을 생성해주세요.')
                if not body.args.get('lc_id'):raise HTTPException(422,'시험 회차를 선택해주세요.')
                return start_adventure_response(token,AdventureStart(road_id=road['road_id'],kind='certificate',lc_id=int(body.args['lc_id'])))
            return adventure_response(token,app.state.reader.adventure(token,body.action,body.args))
        except ReaderError as exc:raise HTTPException(exc.status,str(exc)) from None
    @app.get('/api/health')
    def health():
        engine=app.state.recommender
        return {'ready':True,'api_version':'8','gemini_model':getattr(app.state.reason_generator,'model',None),'model':engine.encoder.name,'index_version':engine.version,'job_count':len(engine.jobs),'snapshot':app.state.snapshot,'ranking_version':RANKING_VERSION,
                'encoder_mode':engine.encoder.mode,'real_sbert_loaded':isinstance(engine.encoder,SentenceEncoder),
                'sbert_scoring_enabled':engine.encoder.mode=='sbert' and engine.policy['semantic_weight']>0,
                'activity_scoring':False,'configured_bm25_weight':engine.policy['bm25_weight'],
                'configured_sbert_weight':engine.policy['semantic_weight']}
    @app.get('/')
    @app.get('/index.html')
    def index():return FileResponse(ROOT/'web/index.html',headers={'Cache-Control':'no-store, max-age=0','Pragma':'no-cache','X-Careerup-History-UI':'compact-2'})
    @app.get('/reset-password.html')
    def reset_password():return FileResponse(ROOT/'web/reset-password.html')
    @app.get('/assets/recommendation.js')
    def recommendation_script():return FileResponse(ROOT/'web/recommendation.js',media_type='text/javascript',headers={'Cache-Control':'no-store, max-age=0'})
    app.mount('/assets',StaticFiles(directory=ROOT/'web'),name='assets')
    return app
app=create_app()
