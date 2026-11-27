import json,re,secrets,hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

def codes(row):
    value=row.get('CT_JOB_CODES') or []
    if isinstance(value,str):
        try:value=json.loads(value)
        except ValueError:return []
    return value if isinstance(value,list) else []

def completed_codes(text,catalog):
    names=set(re.findall(r'^\s*\[공모전\]\s*:\s*(.+?)\s*$',text or '',re.M))
    return sorted({code for row in catalog if row['CT_NAME'].strip() in names for code in codes(row)})

def recommend(reader,token,road,day,force=False):
    catalog=reader._rpc('get_contest_catalog',{},token)
    choices=[row for row in catalog if road['job_code'] in codes(row) and row.get('CT_APPLY_START') and row.get('CT_APPLY_END') and row['CT_APPLY_START']<=day<=row['CT_APPLY_END']]
    cached=reader._rpc('get_contest_recommendations',{'p_road_id':road['road_id']},token) or {}
    prior=next(iter(cached.get('recommendations',[])),{}).get('contest_id') if isinstance(cached,dict) else None
    selected=next((x for x in choices if x['CT_ID']==prior),None)
    if force or selected is None:
        pool=[x for x in choices if x['CT_ID']!=prior] if force and len(choices)>1 else choices
        selected=secrets.choice(pool) if pool else None
    result={'basis_hash':hashlib.sha256((str(road['road_id'])+day+road['job_code']).encode()).hexdigest(),'recommendations':([{'contest_id':selected['CT_ID'],'reason':'목표 직무와 연결된 접수 중인 공모전입니다.'}] if selected else [])}
    if result!=cached:reader._rpc('save_contest_recommendations',{'p_road_id':road['road_id'],'p_result':result},token)
    return {'road_id':road['road_id'],'contest':selected,'today':day}
