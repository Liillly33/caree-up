"""Content-addressed public-corpus vectors; no source text is stored."""
import hashlib,json,logging,sqlite3,time
from pathlib import Path
import numpy as np
LOG=logging.getLogger('uvicorn.error')
class EmbeddingCache:
    def __init__(self,folder,identity):
        self.db=None
        try:
            folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
            key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
            self.path=folder/(key+'.sqlite3')
            for attempt in range(2):
                try:
                    self.db=sqlite3.connect(self.path,timeout=5,check_same_thread=False)
                    self.db.execute('PRAGMA journal_mode=WAL')
                    self.db.execute('CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, dim INTEGER NOT NULL, vector BLOB NOT NULL, checksum TEXT NOT NULL)')
                    self.db.commit();break
                except sqlite3.DatabaseError:
                    if self.db:self.db.close()
                    self.db=None
                    if attempt:raise
                    self.path.rename(self.path.with_suffix('.corrupt-'+str(time.time_ns())))
        except (OSError,sqlite3.Error) as exc:
            LOG.warning('문장 캐시를 사용할 수 없어 직접 계산합니다: %s',type(exc).__name__)
    @staticmethod
    def key(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()
    def get(self,text):
        if self.db is None:return None
        try:
            row=self.db.execute('SELECT dim,vector,checksum FROM vectors WHERE key=?',(self.key(text),)).fetchone()
            if not row:return None
            dim,blob,checksum=row
            if dim<1 or dim>65536 or len(blob)!=dim*8 or hashlib.sha256(blob).hexdigest()!=checksum:return None
            v=np.frombuffer(blob,dtype='<f8').copy()
            return v if np.isfinite(v).all() and abs(np.linalg.norm(v)-1)<.001 else None
        except (ValueError,sqlite3.Error):return None
    def put(self,pairs):
        if self.db is None:return
        try:
            rows=[]
            for text,vector in pairs:
                v=np.asarray(vector,dtype='<f8');blob=v.tobytes()
                rows.append((self.key(text),len(v),blob,hashlib.sha256(blob).hexdigest()))
            with self.db:self.db.executemany('INSERT OR REPLACE INTO vectors VALUES (?,?,?,?)',rows)
        except sqlite3.Error:
            LOG.warning('문장 캐시 저장에 실패했습니다. 계산 결과는 정상 사용합니다.')
    def close(self):
        if self.db is not None:self.db.close();self.db=None
