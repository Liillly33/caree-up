"""Run the login HTML and Python recommendation API together."""
from pathlib import Path
import os

def main():
    os.chdir(Path(__file__).resolve().parent)
    try:import uvicorn
    except ImportError:
        print('먼저 python -m pip install -r requirements.txt 를 실행해주세요.')
        return
    print('시작할 때마다 Supabase 최신 공고를 내려받고 추천 인덱스를 구성합니다. 기존 SBERT 문장은 캐시를 재사용하고 새 문장만 계산합니다.')
    print('공고 동기화에 실패하면 서버를 시작하지 않습니다. 인터넷 연결과 서버 메시지를 확인해주세요.')
    print('Application startup complete가 보이면 http://localhost:8000/index.html 을 여세요.')
    uvicorn.run('api:app',host='127.0.0.1',port=8000,log_level='info')
if __name__=='__main__':main()
