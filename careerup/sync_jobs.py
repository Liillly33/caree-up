"""Fetch one consistent public read-only job catalog snapshot; replace the local file atomically."""
import json
import os
import tempfile
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from supabase_reader import DEFAULT_URL,DEFAULT_KEY

ROOT=Path(__file__).resolve().parent
FIELDS=('JOB_ANN_ID','JOB_ANN_TITLE','JOB_CORP_NAME','JOB_CORP_SCALE','JOB_SALARY','JOB_LOCATION','JOB_TYPE','JOB_EXPERIENCE','JOB_RESPONSIBILITIES','JOB_REQUIRE','JOB_PREFERRD_QUALIFICATION','JOB_URL','JOB_CODE')
CODES={'FE','BE','APP','ERP','OPS','SEC','DATA','AIML','QA','GAME','IOT','NET','ETC'}
class SnapshotError(RuntimeError):pass

def load_sync_env(root):
    path=Path(root)/'.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line=line.strip()
            if not line or line.startswith('#') or '=' not in line:continue
            key,value=line.split('=',1)
            if key.strip() in ('SUPABASE_URL','SUPABASE_PUBLISHABLE_KEY'):
                os.environ.setdefault(key.strip(),value.strip().strip('\"\''))

def validate_catalog(payload):
    if not isinstance(payload,dict) or payload.get('schema_version')!=1:raise SnapshotError('공고 동기화 응답 형식이 올바르지 않습니다. DB 업데이트를 확인해주세요.')
    rows=payload.get('jobs');count=payload.get('count')
    if not isinstance(rows,list) or not rows or type(count) is not int or count!=len(rows):raise SnapshotError('빈 공고 목록 또는 공고 개수 불일치입니다. 기존 스냅샷을 유지합니다.')
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or any(k not in row for k in FIELDS):raise SnapshotError('공고에 필요한 열이 누락되었습니다.')
        jid=row['JOB_ANN_ID']
        if type(jid) is not int or jid<1 or jid in seen:raise SnapshotError('공고 ID가 잘못되었거나 중복되었습니다.')
        if row['JOB_CODE'] not in CODES or not isinstance(row['JOB_ANN_TITLE'],str) or not row['JOB_ANN_TITLE'].strip():raise SnapshotError('공고 제목 또는 직무분류가 잘못되었습니다.')
        if any(row[k] is not None and not isinstance(row[k],str) for k in FIELDS if k!='JOB_ANN_ID'):raise SnapshotError('공고의 텍스트 형식을 확인해주세요.')
        seen.add(jid)
    return sorted(rows,key=lambda r:r['JOB_ANN_ID'])

def refresh_snapshot(root=ROOT,opener=None):
    root=Path(root);load_sync_env(root)
    url=os.environ.get('SUPABASE_URL',DEFAULT_URL).rstrip('/')
    public_key=os.environ.get('SUPABASE_PUBLISHABLE_KEY',DEFAULT_KEY)
    open_request=opener or urlopen
    headers={'apikey':public_key,'Content-Type':'application/json'}
    if public_key.count('.')==2:headers['Authorization']='Bearer '+public_key
    request=Request(url+'/rest/v1/rpc/get_job_catalog_snapshot',data=b'{}',headers=headers,method='POST')
    try:
        with open_request(request,timeout=30) as response:payload=json.load(response)
    except HTTPError as exc:
        if exc.code in (400,404):raise SnapshotError('공고 동기화 함수를 찾을 수 없습니다. DB 업데이트를 확인해주세요.') from None
        if exc.code in (401,403):raise SnapshotError('공고 조회 권한을 확인해주세요. 기존 스냅샷을 유지합니다.') from None
        raise SnapshotError('공고 서버 응답 오류입니다. 잠시 후 다시 실행해주세요.') from None
    except (URLError,TimeoutError,ValueError):raise SnapshotError('공고 서버에 연결할 수 없거나 응답을 읽지 못했습니다. 인터넷 연결 후 다시 실행해주세요.') from None
    rows=validate_catalog(payload)
    target=root/'data/jobs.json';target.parent.mkdir(parents=True,exist_ok=True);pending=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=target.parent,prefix='jobs-',suffix='.pending',delete=False) as f:
            pending=Path(f.name);json.dump(rows,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(pending,target)
    finally:
        if pending:pending.unlink(missing_ok=True)
    return {'count':len(rows),'fetched_at':payload.get('fetched_at')}

def main():
    try:result=refresh_snapshot()
    except SnapshotError as exc:raise SystemExit(str(exc)) from None
    print(f"Supabase 공고 {result['count']}개 스냅샷 갱신 완료.")
if __name__=='__main__':main()
