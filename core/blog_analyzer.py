"""경쟁 포스팅 분석 모듈 — 네이버 블로그 크롤링 + 형태소 분석.

검색 방식: python_rank_check 와 동일 (검증된 방식)
- URL: m.search.naver.com/search.naver?where=m_blog&query={q}
- "인기글" 섹션의 articleSourceJSX_title 앵커만 추출
- 광고(ader.naver.com) 제외
- 인기글 더보기 AJAX 처리
- 결과: 사용자가 검색결과 화면에서 보는 정확한 인기글 순위
"""

import json
import re
import urllib.parse
import urllib.request
import requests
from bs4 import BeautifulSoup
from collections import Counter


# ── 네이버 검색 → 블로그 URL 추출 ──

_USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
]

_MOBILE_UAS = [
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (Linux; Android 14; SM-S928N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36',
]

_BLOG_RE = re.compile(r"https?://(?:m\.)?blog\.naver\.com/([a-zA-Z0-9_.\-]+)(?:/(\d+))?")
_SKIP_IDS = {"PostView", "PostList", "prologue", "BlogHome"}


def _rc_extract_inner_html(j: dict) -> str:
    """rank_check 의 _extract_inner_html_from_response 와 동일.
    더보기 API 응답 JSON에서 카드 HTML 추출."""
    coll = j.get("collection")
    if isinstance(coll, list) and coll and isinstance(coll[0], dict):
        h = coll[0].get("html") or ""
        if h:
            return h
    dom = j.get("dom")
    if isinstance(dom, dict):
        dcoll = dom.get("collection")
        if isinstance(dcoll, list) and dcoll and isinstance(dcoll[0], dict):
            h = dcoll[0].get("html") or ""
            if h:
                return h
    return ""


def _rc_fetch_more_anchors(more_url: str, start: int, keyword: str, referer: str, ua: str) -> list:
    """인기글 더보기 AJAX 호출하여 추가 카드 앵커 리스트 반환."""
    try:
        parts = urllib.parse.urlsplit(more_url)
        qs = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        qs["start"] = str(start)
        new_url = urllib.parse.urlunsplit((
            parts.scheme, parts.netloc, parts.path,
            urllib.parse.urlencode(qs), parts.fragment,
        ))
        req = urllib.request.Request(new_url, headers={
            "User-Agent": ua,
            "Accept": "application/json",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Referer": referer,
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        j = json.loads(body)
        inner_html = _rc_extract_inner_html(j)
        if not inner_html:
            return []
        inner_soup = BeautifulSoup(inner_html, "html.parser")
        return inner_soup.find_all("a", attrs={"data-heatmap-target": "articleSourceJSX_title"})
    except Exception:
        return []


def _rc_collect_items(anchors, seen_keys: set, items: list, max_count: int):
    """articleSourceJSX_title 앵커 리스트에서 카드 정보 추출.
    광고 카드 (ader.naver.com) 제외. blog_id 기준 중복 제거.
    """
    for a in anchors:
        if len(items) >= max_count:
            return
        name = a.get_text(strip=True)
        if not name or len(name) < 2:
            continue
        href = a.get("href", "") or ""
        # 광고 카드 제외
        if "ader.naver.com" in href:
            continue
        m = _BLOG_RE.search(href)
        if not m:
            continue
        blog_id = m.group(1)
        log_no = m.group(2)
        if blog_id in _SKIP_IDS:
            continue
        if not log_no:
            # 카드 안에서 다시 log_no 포함된 링크 찾기
            card = a
            for _ in range(10):
                card = card.parent
                if card is None:
                    break
                if card.get("data-template-id") == "ugcItem":
                    break
            else:
                card = None
            if card:
                inner = card.find("a", href=re.compile(r"blog\.naver\.com/[^/]+/\d+"))
                if inner:
                    m2 = _BLOG_RE.search(inner.get("href", ""))
                    if m2 and m2.group(2):
                        log_no = m2.group(2)
        if not log_no:
            continue
        key = blog_id
        if key in seen_keys:
            continue
        seen_keys.add(key)
        items.append(f"https://blog.naver.com/{blog_id}/{log_no}")


def _rc_find_more_urls(soup) -> list:
    """첫 페이지에서 '인기글더보기' AJAX trigger URL 모두 반환."""
    urls = []
    for a in soup.find_all("a", attrs={"data-lb-trigger": True}):
        txt = a.get_text(strip=True)
        if "인기글" in txt and "더보기" in txt:
            urls.append(a["data-lb-trigger"])
    return urls


def _extract_blog_urls(html: str, count: int, keyword: str = '', ua: str = '') -> list:
    """검색 HTML 에서 인기글 섹션의 블로그 URL 만 추출 (rank_check 방식).

    1) BS4로 '<h1~h4>인기글</h1~h4>' 헤더 확인
    2) data-heatmap-target="articleSourceJSX_title" 앵커 수집
    3) 광고 카드(ader.naver.com) 제외
    4) blog_id 중복 제거
    5) 부족하면 인기글더보기 AJAX 호출 (start=1, 11, 21...)
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []

    # 인기글 헤더 있는지 확인
    headers = [
        h for h in soup.find_all(["h1", "h2", "h3", "h4"])
        if "인기글" in h.get_text()
    ]
    if not headers:
        # 인기글 섹션 없음 — 폴백으로 정규식 사용 (구형 동작)
        return _extract_blog_urls_regex_fallback(html, count)

    items = []
    seen_keys = set()
    anchors = soup.find_all("a", attrs={"data-heatmap-target": "articleSourceJSX_title"})
    _rc_collect_items(anchors, seen_keys, items, count)

    # 부족하면 더보기 AJAX
    if len(items) < count and keyword and ua:
        referer = (
            "https://m.search.naver.com/search.naver?where=m_blog&query="
            + urllib.parse.quote(keyword)
        )
        for more_url in _rc_find_more_urls(soup):
            if len(items) >= count:
                break
            start = 1
            for _ in range(5):
                if len(items) >= count:
                    break
                more_anchors = _rc_fetch_more_anchors(more_url, start, keyword, referer, ua)
                if not more_anchors:
                    break
                before = len(items)
                _rc_collect_items(more_anchors, seen_keys, items, count)
                if len(items) == before:
                    break
                start += 10
    return items[:count]


def _extract_blog_urls_regex_fallback(html: str, count: int) -> list:
    """폴백 — 인기글 섹션 못 찾았을 때 정규식으로 페이지 전체 href 스캔.
    구형 동작 호환용. 정확도 떨어짐."""
    blog_urls = []
    seen_blog_ids = set()
    pattern = r'href="(https?://(?:m\.)?blog\.naver\.com/([a-zA-Z0-9_\-]+)/(\d+))"'
    for m in re.finditer(pattern, html):
        blog_id = m.group(2)
        log_no = m.group(3)
        if blog_id in _SKIP_IDS:
            continue
        if blog_id in seen_blog_ids:
            continue
        seen_blog_ids.add(blog_id)
        blog_urls.append(f'https://blog.naver.com/{blog_id}/{log_no}')
        if len(blog_urls) >= count:
            break
    return blog_urls


def _try_search(url: str, ua: str, referer: str, timeout: int = 15) -> str:
    """단일 검색 요청 — 성공 시 HTML, 실패/차단 시 빈 문자열.

    rank_check 의 검증된 방식 — urllib.request + 최소 헤더 (Sec-Fetch-* 같은
    봇 탐지 유발 헤더 안 보냄, referer 방문도 안 함).
    """
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': ua,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ko-KR,ko;q=0.9',
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        return raw.decode('utf-8', errors='ignore')
    except Exception:
        return ''


def search_naver_blogs(keyword: str, count: int = 5, callback=None) -> list:
    """네이버 블로그 검색 — '인기글' 섹션 상위 N개 URL 추출 (rank_check 검증 방식).

    1순위: m.search.naver.com/search.naver?where=m_blog (rank_check 와 동일)
    2~3순위: 폴백 엔드포인트 (인기글 섹션 못 찾으면 regex fallback)
    """
    import time, random

    if callback:
        callback(f"[I] 네이버 블로그 검색 중: '{keyword}' (인기글 섹션 추출 모드)")

    q = requests.utils.quote(keyword)
    # 시도 순서: 모바일 블로그탭(m_blog) — rank_check 검증된 방식 → 폴백들
    attempts = [
        ('m_blog',  f'https://m.search.naver.com/search.naver?where=m_blog&query={q}',
                    'https://m.naver.com/'),
        ('mobile',  f'https://m.search.naver.com/search.naver?ssc=tab.m_blog.all&sm=mtb_jum&query={q}',
                    'https://m.naver.com/'),
        ('pc',      f'https://search.naver.com/search.naver?ssc=tab.blog.all&sm=tab_jum&query={q}',
                    'https://www.naver.com/'),
        ('pc2',     f'https://search.naver.com/search.naver?where=blog&sm=tab_jum&query={q}',
                    'https://www.naver.com/'),
    ]

    last_err = ''
    for kind, url, referer in attempts:
        # m_blog / mobile 은 모바일 UA, 그 외는 PC UA
        ua_pool = _MOBILE_UAS if kind in ('m_blog', 'mobile') else _USER_AGENTS
        for try_num in range(2):  # 각 엔드포인트마다 2회 시도 (UA 바꿔가며)
            ua = random.choice(ua_pool)
            try:
                html = _try_search(url, ua, referer)
                if not html:
                    last_err = f'{kind} 차단'
                    time.sleep(random.uniform(3.0, 5.0))
                    continue
                # 인기글 섹션 파싱 (rank_check 방식) — 키워드·UA 전달해 더보기 AJAX도 호출 가능
                blog_urls = _extract_blog_urls(html, count, keyword=keyword, ua=ua)
                if blog_urls:
                    if callback:
                        callback(f"[I] {len(blog_urls)}개 블로그 URL 추출 완료 ({kind}, 인기글)")
                        for i, u in enumerate(blog_urls):
                            callback(f"[I]   {i+1}. {u}")
                    return blog_urls
                last_err = f'{kind} 0건'
            except Exception as e:
                last_err = f'{kind}: {e}'
            time.sleep(random.uniform(3.0, 5.0))

    # 최후 폴백: 실제 Chrome(Playwright)으로 검색
    if callback:
        callback(f"[I] requests 차단됨 ({last_err}) - Chrome으로 재시도...")
    try:
        blog_urls = _search_via_browser(keyword, count, callback)
        if blog_urls:
            return blog_urls
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        last_err = f'browser: {e}'
        if callback:
            # 마지막 라인 3개만 보여주기 (디버깅용)
            short_tb = '\n'.join(tb.strip().split('\n')[-5:])
            callback(f"[!] 브라우저 폴백 상세: {short_tb}")

    if callback:
        callback(f"[!] 네이버 검색 실패 (마지막 오류: {last_err})")
    return []


def _search_via_browser(keyword: str, count: int, callback=None) -> list:
    """requests가 차단됐을 때 — 별도 파이썬 서브프로세스에서 Playwright 시크릿 Chrome 실행.
    부모 프로세스 상태(PySide6/Qt, 기존 Playwright 인스턴스 등)와 완전 격리 → WinError 50 회피."""
    import subprocess
    import sys
    import json
    import os

    # 서브프로세스에서 실행할 스크립트 (stdout에 JSON으로 URL 리스트 출력)
    worker_script = os.path.join(os.path.dirname(__file__), '_search_worker.py')
    if not os.path.exists(worker_script):
        if callback:
            callback(f"[!] 검색 워커 스크립트 없음: {worker_script}")
        return []

    try:
        # 서브프로세스로 검색 실행 (30초 타임아웃)
        startupinfo = None
        if os.name == 'nt':
            # Windows: 콘솔 창 숨김
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE

        result = subprocess.run(
            [sys.executable, worker_script, keyword, str(count)],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=60,
            startupinfo=startupinfo,
        )
        stdout = (result.stdout or '').strip()
        stderr = (result.stderr or '').strip()

        # stdout 마지막 줄이 JSON 결과
        blog_urls = []
        for line in stdout.split('\n'):
            line = line.strip()
            if line.startswith('[') and line.endswith(']'):
                try:
                    blog_urls = json.loads(line)
                    break
                except Exception:
                    pass

        if blog_urls:
            if callback:
                callback(f"[I] {len(blog_urls)}개 블로그 URL 추출 완료 (chrome 시크릿/서브프로세스)")
                for i, u in enumerate(blog_urls):
                    callback(f"[I]   {i+1}. {u}")
            return blog_urls
        else:
            if callback:
                msg = stderr[-300:] if stderr else stdout[-300:]
                callback(f"[!] 서브프로세스 검색 결과 없음: {msg}")
            return []
    except subprocess.TimeoutExpired:
        if callback:
            callback("[!] 브라우저 폴백 타임아웃 (60초)")
        return []
    except Exception as e:
        if callback:
            callback(f"[!] 서브프로세스 실행 오류: {e}")
        return []


# 참고: 서브프로세스 워커 스크립트는 core/_search_worker.py 에 별도 파일로 존재


# ── 블로그 크롤링 ──

def _get_blog_content(url: str, callback=None) -> dict:
    """네이버 블로그 URL에서 제목+본문 추출"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    # 모바일 버전으로 변환 (iframe 우회)
    blog_id = ''
    log_no = ''

    if 'blog.naver.com' in url:
        # URL 파싱
        m = re.search(r'blog\.naver\.com/([^/?\s]+)/(\d+)', url)
        if m:
            blog_id = m.group(1)
            log_no = m.group(2)
        else:
            m = re.search(r'blogId=([^&]+).*logNo=(\d+)', url)
            if m:
                blog_id = m.group(1)
                log_no = m.group(2)

    if not blog_id or not log_no:
        if callback:
            callback(f"[!] URL 파싱 실패: {url}")
        return {'title': '', 'body': '', 'url': url}

    # 모바일 버전 URL (iframe 없이 본문 직접 접근)
    mobile_url = f'https://m.blog.naver.com/{blog_id}/{log_no}'

    try:
        res = requests.get(mobile_url, headers=headers, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')

        # 제목
        title_el = soup.select_one('.se-title-text, .post_title, .__se_title_text, .pcol1')
        title = title_el.get_text(strip=True) if title_el else ''

        # 본문
        body_el = soup.select_one('.se-main-container, .post_ct, #postViewArea, .se_component_wrap')
        if body_el:
            # 불필요한 태그 제거
            for tag in body_el.select('script, style, .og_box, .se-module-oglink'):
                tag.decompose()
            body = body_el.get_text('\n', strip=True)
        else:
            body = ''

        # 본문이 비어있으면 PostView로 재시도
        if not body:
            post_url = f'https://blog.naver.com/PostView.naver?blogId={blog_id}&logNo={log_no}'
            res2 = requests.get(post_url, headers=headers, timeout=15)
            soup2 = BeautifulSoup(res2.text, 'html.parser')
            body_el2 = soup2.select_one('.se-main-container, #postViewArea')
            if body_el2:
                for tag in body_el2.select('script, style'):
                    tag.decompose()
                body = body_el2.get_text('\n', strip=True)

        if callback:
            callback(f"[I] 크롤링 완료: {title[:30]}... ({len(body)}자)")

        return {'title': title, 'body': body, 'url': url}

    except Exception as e:
        if callback:
            callback(f"[!] 크롤링 오류 ({url}): {e}")
        return {'title': '', 'body': '', 'url': url}


def crawl_blogs(urls: list, callback=None) -> list:
    """여러 블로그 URL 크롤링"""
    results = []
    for i, url in enumerate(urls):
        url = url.strip()
        if not url:
            continue
        if callback:
            callback(f"[I] [{i+1}/{len(urls)}] 크롤링 중: {url[:50]}...")
        result = _get_blog_content(url, callback)
        if result['body']:
            results.append(result)
    return results


# ── 형태소 분석 (블라이 방식) ──

# 불용어 — 기능어 + UI 잡음 + 불완전 형태
STOPWORDS = {
    # 접속/기능어
    '그리고', '그래서', '하지만', '그런데', '그러나', '또한', '그리하여',
    '있습니다', '했습니다', '됩니다', '합니다', '입니다',
    # 불필요 UI/메타
    '복사', '본문', '신고', '폰트', '크기', '조정', '추가', '이웃', '공유',
    '기타', '댓글', '좋아요', '구독', '팔로우', '블로그', '카페',
    # 불완전 어미/활용형 (kiwi 분리 잔해)
    '가어', '남어', '느끼어', '되어', '하어', '보이어', '쌓이어', '생기어',
    '걸리었', '다르었', '느끼었', '보이었', '가었', '챙기어', '맞추어',
    '들었', '된', '될', '하는', '하기', '있는', '않는',
    '스럽', '딩', '몰',
    # 단독 의미 없는 1글자
    '거', '것', '수', '때', '데', '등', '더', '다', '이', '그', '저',
    '분', '중', '편', '면', '쪽', '번', '줄', '새',
}

# 이상한 형태 패턴 필터
def _is_valid_word(word):
    """정상적인 한국어 단어인지 검증"""
    # 1글자는 명사만 허용 (눈, 손, 물, 집, 점 등)
    if len(word) == 1:
        return word in {'눈', '손', '물', '집', '점', '겉', '발', '문', '위', '뒤',
                        '곳', '틈', '말', '앞', '밑', '속', '옆', '힘', '빛', '길'}
    # 2글자 이상: 한글만
    if not re.match(r'^[가-힣]+$', word):
        return False
    # 이상한 활용형 제거 (어/었/이어 로 끝나는 동사 잔해)
    if re.search(r'(어|었|이어|이었|았)$', word) and len(word) <= 3:
        return False
    return True

# 제외할 품사 — 조사(J*), 어미(E*), 접속사(MA 제외), 기호(S*), 숫자(SN)
EXCLUDE_TAGS = {'JKS', 'JKC', 'JKG', 'JKO', 'JKB', 'JKV', 'JKQ', 'JX', 'JC',
                'EP', 'EF', 'EC', 'ETN', 'ETM',
                'SF', 'SP', 'SS', 'SE', 'SO', 'SW', 'SH',
                'SN', 'XPN', 'XSN', 'XSV', 'XSA'}


def analyze_morphemes(posts: list, callback=None) -> dict:
    """여러 포스팅의 형태소를 분석하여 Common words 생성 (블라이 방식)

    블라이 방식:
    - 형태소를 원형 그대로 유지 (동사/형용사 활용형 포함)
    - 명사, 동사, 형용사, 부사, 관형사 등 내용어 추출
    - 조사/어미만 제거
    - 1글자 명사도 포함 (겉, 눈, 물, 집 등)
    - 복합어 유지 (천안입주청소, 사용감, 무게감 등)
    """
    from kiwipiepy import Kiwi
    kiwi = Kiwi()

    if callback:
        callback(f"[I] {len(posts)}개 포스팅 형태소 분석 시작...")

    total = len(posts)
    post_word_sets = []

    for i, post in enumerate(posts):
        text = post.get('body', '')
        if not text:
            continue

        if callback:
            callback(f"[I] [{i+1}/{total}] 형태소 분석 중...")

        tokens = kiwi.tokenize(text)
        words = set()

        for j, token in enumerate(tokens):
            form = token.form
            tag = token.tag

            # 제외 품사 스킵
            if tag in EXCLUDE_TAGS:
                continue

            # 한글이 포함된 토큰만
            if not re.search(r'[가-힣]', form):
                continue

            # 불용어 + 유효성 스킵
            if form in STOPWORDS:
                continue
            if not _is_valid_word(form):
                continue

            # 명사(NNG, NNP, NNB) — 1글자 포함
            if tag.startswith('NN'):
                words.add(form)
                # 명사+접미사(XSN) 합성어 처리: 사용+감→사용감, 안정+적→안정적
                if j + 1 < len(tokens) and tokens[j + 1].tag == 'XSN':
                    compound = form + tokens[j + 1].form
                    words.add(compound)
                continue

            # 접미사(XSN) 단독 — 이미 위에서 합성됨, 스킵
            if tag == 'XSN':
                continue

            # 동사(VV), 형용사(VA) — 활용형 그대로 (뒤 어미 붙인 형태)
            if tag.startswith(('VV', 'VA', 'XSV', 'XSA')):
                # 뒤에 어미가 붙어있으면 결합형 추출
                # ex) "보이는" = 보이(VV) + 는(ETM) → "보이는"
                combined = form
                for k in range(j + 1, min(j + 3, len(tokens))):
                    next_tok = tokens[k]
                    if next_tok.tag.startswith(('ETM', 'EC', 'EF', 'EP')):
                        combined += next_tok.form
                        break
                    elif next_tok.tag == 'XSV':
                        combined += next_tok.form
                    else:
                        break
                if len(combined) >= 1:
                    words.add(combined)
                if len(form) >= 2 and form != combined:
                    words.add(form)
                continue

            # 부사(MAG), 관형사(MM), 감탄사(IC)
            if tag.startswith(('MAG', 'MM', 'IC')):
                words.add(form)
                continue

        # 본문에서 직접 단어 추출 (형태소 분석 보완)
        for eojeol in re.findall(r'[가-힣]+', text):
            # 복합 키워드 (청소, 느낌으로 끝나는)
            if len(eojeol) >= 4 and any(eojeol.endswith(s) for s in ['청소', '느낌']):
                words.add(eojeol)

            # 관형형/활용형 — 어절 끝에서 패턴 추출 (블라이 방식)
            # "걷어낸"→"낸", "정돈된"→"된", "안정적인"→"안정적"
            for suffix in ['있는', '않는', '하는', '보이는', '맡기는', '넘어가는',
                           '미세한', '새로운', '쉬운', '쌓인',
                           '된', '한', '큰', '낸', '하기']:
                if eojeol.endswith(suffix) or eojeol == suffix:
                    words.add(suffix)
                    break

            # -적, -감 복합어 추출: "안정적인"→"안정적", "사용감의"→"사용감"
            m_suffix = re.match(r'^(.+?(?:적|감))', eojeol)
            if m_suffix and len(m_suffix.group(1)) >= 2:
                words.add(m_suffix.group(1))

        # 외래어 직접 추출 (kiwi가 분리하는 문제 보완)
        for loanword in re.findall(r'(?:몰딩|타일|필터|린스|왁스|코팅|실리콘|스팀|시스템)', text):
            words.add(loanword)

        # 최종 필터링: 불용어 + 유효성 검증
        cleaned = {w for w in words if w not in STOPWORDS and _is_valid_word(w)}
        post_word_sets.append(cleaned)

    # 단어별 등장 포스팅 수 카운트
    word_post_count = Counter()
    for word_set in post_word_sets:
        for word in word_set:
            word_post_count[word] += 1

    # 빈도별 분류
    common_words = {}
    for level in range(total, 0, -1):
        key = f'{level}/{total}'
        words = sorted([w for w, c in word_post_count.items() if c == level])
        if words:
            common_words[key] = words

    # 포맷 문자열 생성
    formatted_lines = ["■ Common words :\n"]
    for level in range(total, 0, -1):
        key = f'{level}/{total}'
        if key in common_words:
            words_str = ', '.join(common_words[key])
            formatted_lines.append(f"[{key}] : {words_str}\n")

    formatted = '\n'.join(formatted_lines)

    if callback:
        total_words = sum(len(ws) for ws in common_words.values())
        callback(f"[I] 분석 완료! 총 {total_words}개 형태소 추출")

    return {
        'total_posts': total,
        'common_words': common_words,
        'word_freq': dict(word_post_count),
        'formatted': formatted,
    }
