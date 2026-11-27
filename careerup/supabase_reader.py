"""Read current authenticated user's own profile through Supabase Auth + RLS.
No service-role key, arbitrary USER_ID, or JWT-only decoding. Writes use caller RLS.
"""
import json
import os
import re
import logging
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

DEFAULT_URL='https://rrfydzmsjwfgxfynfqys.supabase.co'
DEFAULT_KEY='sb_publishable_jSOvZcQtUoXJvgQ6CRCZ8w_5rBSamsS'

logger=logging.getLogger('uvicorn.error')
class ReaderError(Exception):
    def __init__(self,message,status=400,code='SUPABASE_REQUEST_FAILED'):
        super().__init__(message);self.status=status;self.code=code

class SupabaseReader:
    def __init__(self,transport=None):
        self.url=os.environ.get('SUPABASE_URL',DEFAULT_URL).rstrip('/')
        self.key=os.environ.get('SUPABASE_PUBLISHABLE_KEY',DEFAULT_KEY)
        self.transport=transport or self._get
    def _get(self,path,token):
        request=Request(self.url+path,headers={'apikey':self.key,'Authorization':'Bearer '+token})
        try:
            with urlopen(request,timeout=15) as response:return json.load(response)
        except HTTPError as exc:
            logger.warning('Supabase 조회 HTTP %s endpoint=%s',exc.code,path.split('?')[0])
            status=401 if exc.code in (401,403) else 502
            raise ReaderError('로그인 세션 또는 스펙 조회 권한을 확인해주세요.' if status==401 else '회원정보 조회 서버에 오류가 있습니다.',status) from exc
        except (URLError,TimeoutError,ValueError) as exc:raise ReaderError('회원정보 서버에 연결할 수 없습니다.',502) from exc
    def read_own_profile(self,token):
        if not token:raise ReaderError('로그인 후 이용해주세요.',401)
        account=self.transport('/auth/v1/user',token)  # Supabase verifies token and user.
        auth_id=account.get('id')
        if not auth_id:raise ReaderError('유효하지 않은 로그인 세션입니다.',401)
        query=urlencode({'select':'USER_ID','AUTH_USER_ID':'eq.'+auth_id})
        users=self.transport('/rest/v1/SC_USERS?'+query,token)
        if not isinstance(users,list) or len(users)!=1:raise ReaderError('회원정보를 확인할 수 없습니다.',403)
        user_id=users[0]['USER_ID']
        query=urlencode({'select':'SPEC_TECH_SKILL,SPEC_LICENSES,SPEC_PROJECT_EXPERIENCE','USER_ID':'eq.'+str(user_id)})
        specs=self.transport('/rest/v1/SC_SPEC_INFO?'+query,token)
        if not isinstance(specs,list) or len(specs)!=1:raise ReaderError('마이페이지에 스펙정보를 입력해주세요.',400)
        query=urlencode({'select':'JOB_CODE,PRFD_RANK','USER_ID':'eq.'+str(user_id),'order':'PRFD_RANK.asc'})
        preferences=self.transport('/rest/v1/SC_USER_PRFD_JOB?'+query,token)
        if not isinstance(preferences,list):raise ReaderError('선호직무를 조회할 수 없습니다.',502)
        profile={}
        for column,key in [('SPEC_TECH_SKILL','tech_skills'),('SPEC_LICENSES','licenses')]:
            value=specs[0].get(column) or []
            if not isinstance(value,list) or any(not isinstance(t,str) for t in value):raise ReaderError('기술 스택과 자격증은 문자열 JSON 배열이어야 합니다.')
            profile[key]=value
        experience=specs[0].get('SPEC_PROJECT_EXPERIENCE')
        if experience is not None and not isinstance(experience,str):raise ReaderError('프로젝트 경험은 텍스트여야 합니다.')
        profile['project_experience']=experience or ''
        if '[공모전]' in (experience or ''):
            from contest import completed_codes
            profile['contest_job_codes']=completed_codes(experience,self._rpc('get_contest_catalog',{},token))
        return {'user_id':user_id,'profile':profile,'job_codes':[r['JOB_CODE'] for r in preferences]}

    def _rpc(self,name,payload,token):
        request=Request(self.url+'/rest/v1/rpc/'+name,data=json.dumps(payload,ensure_ascii=False).encode(),
                        headers={'apikey':self.key,'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
        try:
            with urlopen(request,timeout=20) as response:return json.load(response)
        except HTTPError as exc:
            try:
                failure=json.load(exc);code=str(failure.get('code','UNKNOWN'))
            except (ValueError,TypeError,AttributeError):code='UNKNOWN'
            if not re.fullmatch(r'[A-Z0-9_]{1,40}',code):code='UNKNOWN'
            logger.warning('Supabase RPC %s HTTP %s code=%s',name,exc.code,code)
            if code=='P0002':raise ReaderError('선택한 추천 결과를 찾을 수 없습니다.',404,'MATCH_NOT_FOUND') from None
            if code in ('22023','22007','22008'):
                message='입력 내용을 확인해주세요.'
                if name=='certificate_adventure' and isinstance(failure,dict):
                    candidate=failure.get('message','')
                    if isinstance(candidate,str) and len(candidate)<160 and not any(x in candidate for x in ('SELECT','INSERT','UPDATE','CONTEXT','password=')):message=candidate
                for old,new in [('중간보스','중기목표'),('최종 보스','최종목표'),('보스','목표'),('토벌','완료'),('모험','커리큘럼')]:message=message.replace(old,new)
                raise ReaderError(message,400,'ADVENTURE_INVALID_INPUT') from None
            if code=='23503':
                raise ReaderError('DB에 없는 공고가 추천에 포함됐습니다. 공고 데이터를 동기화한 뒤 서버를 재시작해주세요.',409,'MATCH_JOB_NOT_FOUND') from None
            if code in ('PGRST202','PGRST205','42883','42P01'):
                raise ReaderError('추천 기록 저장 함수 또는 테이블을 찾을 수 없습니다. DB 설정을 확인해주세요.',503,'MATCH_DATABASE_SETUP') from None
            if exc.code in (401,403):
                raise ReaderError('로그인 세션 또는 추천 기록 접근 권한을 확인해주세요. 다시 로그인해주세요.',401,'MATCH_ACCESS_DENIED') from None
            raise ReaderError('추천 기록 저장·조회에 실패했습니다. 서버 창에 표시된 오류 코드를 확인해주세요.',502,'MATCH_DATABASE_'+code) from None
        except (URLError,TimeoutError,ValueError):raise ReaderError('추천 기록 서버에 연결할 수 없습니다.',503,'MATCH_DATABASE_CONNECTION') from None
    def save_results(self,token,results):
        return self._rpc('save_matching_results',{'p_results':[
            {'job_id':r['job_id'],'score_100':r['score_100'],'reason':r['reason']} for r in results]},token)
    def history(self,token,offset=0):
        return self._rpc('get_matching_history',{'p_offset':offset},token)

    def match_result(self,token,match_id,rank):
        query=urlencode({'select':'JOB_ANN_ID,MATCH_ID,MATCH_RANK','MATCH_ID':'eq.'+match_id,'MATCH_RANK':'eq.'+str(rank)})
        rows=self.transport('/rest/v1/SC_MATCHING_RESULT?'+query,token)
        if not isinstance(rows,list) or len(rows)!=1:raise ReaderError('선택한 추천 결과를 찾을 수 없습니다.',404,'MATCH_NOT_FOUND')
        return rows[0]
    def current_roadmap(self,token):
        return self._rpc('get_current_career_roadmap',{},token)
    def adventure_roadmap(self,token,road_id):
        """Resolve the curriculum's own target through the caller's RLS, even after a newer roadmap exists."""
        rows=self.transport('/rest/v1/SC_CAREER_ROADMAP?'+urlencode({'select':'ROAD_ID,JOB_ANN_ID,MATCH_ID,MATCH_RANK','ROAD_ID':'eq.'+str(road_id)}),token)
        if not isinstance(rows,list) or len(rows)!=1:raise ReaderError('선택한 커리큘럼의 목표 공고를 확인할 수 없습니다.',404)
        road=rows[0]
        scores=self.transport('/rest/v1/SC_MATCHING_RESULT?'+urlencode({'select':'MATCH_SCORE','MATCH_ID':'eq.'+str(road['MATCH_ID']),'MATCH_RANK':'eq.'+str(road['MATCH_RANK']),'JOB_ANN_ID':'eq.'+str(road['JOB_ANN_ID'])}),token)
        if not isinstance(scores,list) or len(scores)!=1:raise ReaderError('원래 추천 점수를 확인할 수 없습니다.',404)
        return {'road_id':road['ROAD_ID'],'job_id':road['JOB_ANN_ID'],'score_100':scores[0]['MATCH_SCORE']}

    def create_roadmap(self,token,match_id,rank,strong,weak):
        return self._rpc('create_career_roadmap',{'p_match_id':match_id,'p_match_rank':rank,'p_strong':strong,'p_weak':weak},token)

    def adventure(self,token,action='get',args=None):
        def check(result):
            if result.get('adventure_revision')!=7:
                raise ReaderError('커리큘럼 DB 업데이트가 필요합니다. sql/007_project_adventure.sql을 확인해주세요.',503)
            return result
        if action!='get':check(self._rpc('certificate_adventure',{'p_action':'get','p_args':{}},token))
        return check(self._rpc('certificate_adventure',{'p_action':action,'p_args':args or {}},token))

    def _auth_post(self,path,data,token=None):
        headers={'apikey':self.key,'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        request=Request(self.url+path,data=json.dumps(data).encode(),headers=headers,method='POST')
        try:
            with urlopen(request,timeout=15) as response:
                raw=response.read();return json.loads(raw) if raw else {}
        except HTTPError as exc:
            if exc.code==429:raise ReaderError('비밀번호 확인 요청이 많습니다. 잠시 후 다시 시도해주세요.',429,'RESTART_RATE_LIMIT') from None
            if exc.code in (400,401,403,422):raise ReaderError('비밀번호가 올바르지 않습니다.',403,'RESTART_PASSWORD_INVALID') from None
            raise ReaderError('비밀번호 확인 서버에 연결할 수 없습니다. 다시 시도해주세요.',503,'RESTART_AUTH_UNAVAILABLE') from None
        except (URLError,TimeoutError,ValueError):raise ReaderError('비밀번호 확인 서버에 연결할 수 없습니다. 다시 시도해주세요.',503,'RESTART_AUTH_UNAVAILABLE') from None

    def verify_restart_password(self,token,password):
        account=self.transport('/auth/v1/user',token)
        if not isinstance(account,dict) or not account.get('id') or not account.get('email'):raise ReaderError('로그인 상태를 확인해주세요.',401)
        signed=self._auth_post('/auth/v1/token?grant_type=password',{'email':account['email'],'password':password})
        verified=signed.get('access_token')
        if not verified or signed.get('user',{}).get('id')!=account['id']:
            if verified:self.close_restart_session(verified)
            raise ReaderError('비밀번호를 다시 확인해주세요.',403,'RESTART_PASSWORD_INVALID')
        return verified

    def close_restart_session(self,token):
        try:self._auth_post('/auth/v1/logout?scope=local',{},token)
        except ReaderError:pass

    def license_catalog(self,token):
        fields='LC_ID,LC_NAME,WRITTEN_DATE,PRACTICAL_DATE,RESULT_DATE,LC_CERT_CODE,LC_CERT_NAME,LC_YEAR,LC_ROUND,LC_PHASE,LC_TECH_TAGS,LC_STUDY_SCOPE'
        rows=self.transport('/rest/v1/SC_LC?'+urlencode({'select':fields,'order':'LC_ID.asc'}),token)
        if not isinstance(rows,list):raise ReaderError('자격증 목록을 확인할 수 없습니다.',502)
        tagged=[x for x in rows if x.get('LC_CERT_CODE') and isinstance(x.get('LC_TECH_TAGS'),list) and x['LC_TECH_TAGS']]
        if rows and not tagged:raise ReaderError('자격증 메타데이터 업데이트가 필요합니다. sql/005_ai_curriculum.sql을 실행해주세요.',503)
        return tagged
    def license_recommendations(self,token,road_id):
        return self._rpc('get_license_recommendations',{'p_road_id':road_id},token)
    def save_license_recommendations(self,token,road_id,result):
        return self._rpc('save_license_recommendations',{'p_road_id':road_id,'p_result':result},token)

    def project_catalog(self,token):
        fields='PJT_ID,PJT_CODE,PJT_NAME,PJT_TECH_TAGS,PJT_FINAL_GOAL,PJT_TOTAL_DAYS,PJT_MID_GOALS,PJT_REVIEW_DAYS,PJT_FINAL_CRITERIA'
        rows=self.transport('/rest/v1/SC_PJT?'+urlencode({'select':fields,'order':'PJT_ID.asc'}),token)
        if not isinstance(rows,list):raise ReaderError('프로젝트 목록을 확인할 수 없습니다.',502)
        return rows
    def project_recommendations(self,token,road_id):
        return self._rpc('get_project_recommendations',{'p_road_id':road_id},token) or {}
    def save_project_recommendations(self,token,road_id,result):
        return self._rpc('save_project_recommendations',{'p_road_id':road_id,'p_result':result},token)
    def select_project(self,token,road_id,project_id):
        return self._rpc('select_roadmap_project',{'p_road_id':road_id,'p_project_id':project_id},token)
