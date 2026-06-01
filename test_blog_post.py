"""블로그 발행 테스트 — 동영상 첨부 플로우 검증용.
쿠키는 최신 PID cookie를 복사해서 사용.
임시저장 모드로만 실행 (실제 발행 X).
"""
import os
import sys
import shutil
import glob
import time as _time

BASE = os.path.dirname(os.path.abspath(__file__))
COOKIE_DIR = os.path.join(BASE, 'cookies')

# 최신 쿠키 파일 찾아서 현재 PID용으로 복사 (browser.py가 PID별 경로를 쓰므로)
pid = os.getpid()
cookie_files = glob.glob(os.path.join(COOKIE_DIR, 'naver_cookies_*.json'))
if cookie_files:
    latest = max(cookie_files, key=os.path.getmtime)
    target = os.path.join(COOKIE_DIR, f'naver_cookies_{pid}.json')
    shutil.copy2(latest, target)
    print(f'[PREP] 쿠키 복사: {os.path.basename(latest)} → naver_cookies_{pid}.json')
else:
    print('[PREP] 쿠키 없음 — 로그인 필요')

sys.path.insert(0, BASE)
from core.browser import NaverBrowser
from core.blog_poster import post_to_blog

MANUSCRIPT_DIR = os.path.join(BASE, 'postings_blog', '이천입주청소')
BLOG_ID = 'newhousefeeling'

def log(msg):
    print(f'[LOG] {msg}')

print('=' * 60)
print(f'MANUSCRIPT: {MANUSCRIPT_DIR}')
print(f'BLOG_ID: {BLOG_ID}')
print(f'MODE: draft (임시저장)')
print('=' * 60)

browser = NaverBrowser()

if not browser.has_cookies():
    print('[!] 쿠키 없음. 수동 로그인 필요.')
    browser.login_manual(
        username=os.environ.get('NAVER_ID', ''),
        password=os.environ.get('NAVER_PW', ''),
        callback=log,
    )

page = browser.start_headless(visible=True)

try:
    ok = post_to_blog(
        page, BLOG_ID, MANUSCRIPT_DIR,
        log_callback=log,
        pkg_dir='',
        draft=True,
    )
    print()
    print(f'=== 결과: {"성공" if ok else "실패"} ===')
except Exception as e:
    import traceback
    print(f'[EXCEPTION] {e}')
    traceback.print_exc()

print('30초 대기, 브라우저에서 결과 확인...')
for i in range(30, 0, -1):
    print(f'  남은 시간: {i}초  ', end='\r')
    _time.sleep(1)
print()

browser.close()
print('완료.')
