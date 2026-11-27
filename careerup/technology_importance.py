"""User-reviewed per-posting decisions; drafts and stale reviews never act as labels."""
import hashlib,json
from pathlib import Path
from text_processing import norm

def load_reviews(path,jobs):
    path=Path(path)
    if not path.exists():return {},{'reviewed':0,'pending':0,'stale':0},None
    payload=json.loads(path.read_text(encoding='utf-8'));lookup={j['JOB_ANN_ID']:j for j in jobs}
    result={};seen=set();summary={'reviewed':0,'pending':0,'stale':0}
    for r in payload['records']:
        label=r.get('label')
        if not label:summary['pending']+=1;continue
        if r.get('label_source')!='USER' or label not in ('핵심','보조','판단 불가','제외'):raise ValueError('검토 라벨 형식이 올바르지 않습니다.')
        summary['reviewed']+=1;jid=r['job_id'];key=(jid,norm(r['technology']))
        if key in seen:raise ValueError('검토 라벨이 중복되었습니다.')
        seen.add(key);job=lookup.get(jid)
        if job is None:summary['stale']+=1;continue
        fingerprint=hashlib.sha256(json.dumps(job,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        if fingerprint!=r['job_sha256']:summary['stale']+=1;continue
        result.setdefault(jid,{})[key[1]]=label
    return result,summary,hashlib.sha256(path.read_bytes()).hexdigest()
