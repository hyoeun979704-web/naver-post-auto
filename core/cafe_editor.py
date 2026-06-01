"""카페 '내가 쓴 게시글' 수정 자동화 모듈.

플로우:
1. (로그인 후) 카페의 '내가 쓴 게시글' 목록 진입
2. 제목에 키워드(예: '세종') 포함 글만 수집 → GUI 체크 선택
3. 선택 글의 수정 페이지 진입 → 제목+본문+이미지를 새 원고로 전체 교체 → 등록

이미지: 지정 폴더의 파일을 정렬 순서대로, 본문의 미디어 마커([이미지: …] 등) 자리에 순차 삽입.

⚠ 네이버 카페는 SPA라 DOM/셀렉터가 수시로 바뀐다. 셀렉터는 다중 폴백으로 방어하되,
   실제 동작은 사용자 PC에서 한 번 검증하며 미세조정이 필요할 수 있다.
"""

import os
import re
import time
import random

from playwright.sync_api import Page

# 본문 입력·이미지 업로드는 기존 발행 모듈 로직을 재사용
from . import poster
from .poster import _human_type, _upload_image, _sp, RISKY_WORD_PATH
from .template_parser import apply_risky_words


# 한 줄짜리 미디어 마커: [이미지: …] [영상: …] [인포그래픽: …] [배너: …] [비포/애프터: …]
_MEDIA_MARKER_RE = re.compile(r'^\[\s*(이미지|영상|인포그래픽|배너|비포|애프터|비포/애프터)[^\]]*\]\s*$')

_IMG_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif')


def list_folder_images(folder: str) -> list:
    """폴더 내 이미지 파일을 이름 정렬 순으로 반환 (절대경로)."""
    if not folder or not os.path.isdir(folder):
        return []
    files = [f for f in os.listdir(folder)
             if f.lower().endswith(_IMG_EXTS) and os.path.isfile(os.path.join(folder, f))]
    files.sort()
    return [os.path.join(folder, f) for f in files]


def build_ops(body: str, image_paths: list):
    """본문 텍스트를 (op, payload) 리스트로 변환.

    - 미디어 마커 줄 → ('image', 경로)  (남은 이미지가 있을 때만; 없으면 마커 줄 제거)
    - 그 외 → ('text', 줄)
    연속 텍스트 줄은 묶어서 하나의 text op 로 합쳐 _human_type 에 그대로 전달.
    image_paths 는 마커 수만큼만 앞에서부터 소비한다(마커보다 이미지가 많으면 남는 건 무시).
    """
    imgs = list(image_paths)
    ops = []
    text_buf = []

    def flush():
        if text_buf:
            ops.append(('text', '\n'.join(text_buf)))
            text_buf.clear()

    for raw in (body or '').split('\n'):
        if _MEDIA_MARKER_RE.match(raw.strip()):
            flush()
            if imgs:
                ops.append(('image', imgs.pop(0)))
            # 이미지가 없으면 마커 줄은 그냥 버린다 (안내문 노출 방지)
            continue
        text_buf.append(raw)
    flush()
    return ops


def fetch_my_articles(page: Page, cafe_id: str, keyword: str = '',
                      log_callback=None, max_count: int = 50) -> list:
    """카페 '내가 쓴 게시글' 목록에서 글을 수집.

    반환: [{'article_id', 'menu_id', 'title'}], article_id 정수 큰 순(최신).
    keyword 가 있으면 제목에 포함된 글만.
    """
    def log(m):
        if log_callback:
            log_callback(m)

    # SPA 버전에 따라 경로가 다를 수 있어 후보 URL 다중 시도
    candidates = [
        f'https://cafe.naver.com/ca-fe/cafes/{cafe_id}/members/me/articles',
        f'https://cafe.naver.com/ca-fe/cafes/{cafe_id}/members/me',
        f'https://cafe.naver.com/ca-fe/cafes/{cafe_id}/my-activity/articles',
    ]
    rows = []
    for url in candidates:
        try:
            page.goto(url, timeout=30000)
            time.sleep(2)
        except Exception as e:
            log(f"  [에러] 목록 이동 실패: {e}")
            continue

        frames = [page] + list(page.frames)
        for frm in frames:
            try:
                data = frm.evaluate("""
                    () => {
                        const out = [];
                        const seen = new Set();
                        const links = document.querySelectorAll('a[href*="/articles/"]');
                        for (const a of links) {
                            const href = a.getAttribute('href') || '';
                            const m = href.match(/(?:menus\\/(\\d+)\\/)?articles\\/(\\d+)/);
                            if (!m) continue;
                            const aid = m[2];
                            if (seen.has(aid)) continue;
                            seen.add(aid);
                            const title = (a.textContent || '').trim().substring(0, 100);
                            if (!title) continue;
                            out.push({ aid, menu: m[1] || '', title });
                        }
                        return out;
                    }
                """)
                if data:
                    rows.extend(data)
            except Exception:
                continue
        if rows:
            break  # 글을 찾은 URL 에서 멈춤

    # 중복 제거 + 키워드 필터 + 최신순
    seen = set()
    uniq = []
    kw = (keyword or '').strip()
    for r in rows:
        aid = r.get('aid')
        if not aid or aid in seen:
            continue
        seen.add(aid)
        title = r.get('title', '')
        if kw and kw not in title:
            continue
        uniq.append({'article_id': aid, 'menu_id': r.get('menu', ''), 'title': title})
    uniq.sort(key=lambda x: int(x['article_id']) if x['article_id'].isdigit() else 0, reverse=True)
    result = uniq[:max_count]
    log(f"  [수집] '{kw}' 포함 내 글 {len(result)}건" if kw else f"  [수집] 내 글 {len(result)}건")
    return result


def _enter_edit_page(page: Page, cafe_id: str, menu_id: str, article_id: str, log) -> bool:
    """수정 에디터 진입. 1) 글 상세에서 '수정' 클릭 2) 실패 시 edit URL 직접 이동."""
    title_sel = 'textarea[placeholder*="제목"], input[placeholder*="제목"]'

    # 1) 글 상세 → '수정' 버튼
    if menu_id:
        detail = f'https://cafe.naver.com/ca-fe/cafes/{cafe_id}/menus/{menu_id}/articles/{article_id}'
    else:
        detail = f'https://cafe.naver.com/ca-fe/cafes/{cafe_id}/articles/{article_id}'
    try:
        page.goto(detail, timeout=30000)
        time.sleep(2)
        clicked = page.evaluate("""
            () => {
                const els = document.querySelectorAll('a, button');
                for (const el of els) {
                    const t = (el.textContent || '').trim();
                    if ((t === '수정' || t === '글수정' || t === '게시글 수정') && el.offsetParent !== null) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }
        """)
        if clicked:
            try:
                page.wait_for_selector(title_sel, timeout=15000, state='visible')
                return True
            except Exception:
                pass
    except Exception as e:
        log(f"  [경고] 상세→수정 진입 실패: {e}")

    # 2) edit URL 직접 (boardType=L 의 write 에디터가 article id 로 수정 모드)
    #    menu_id 가 비어도(목록 링크에 메뉴가 없던 경우) 메뉴 없는 형태로 폴백 시도
    edit_urls = []
    if menu_id:
        edit_urls += [
            f'https://cafe.naver.com/ca-fe/cafes/{cafe_id}/menus/{menu_id}/articles/{article_id}/edit?boardType=L',
            f'https://cafe.naver.com/ca-fe/cafes/{cafe_id}/menus/{menu_id}/articles/write?articleId={article_id}&boardType=L',
        ]
    edit_urls += [
        f'https://cafe.naver.com/ca-fe/cafes/{cafe_id}/articles/{article_id}/edit?boardType=L',
        f'https://cafe.naver.com/ca-fe/cafes/{cafe_id}/articles/write?articleId={article_id}&boardType=L',
    ]
    for edit_url in edit_urls:
        try:
            page.goto(edit_url, timeout=30000)
            page.wait_for_selector(title_sel, timeout=15000, state='visible')
            return True
        except Exception:
            continue
    return False


def _focus_body(page: Page) -> bool:
    """본문 영역 포커스 (poster.py 와 동일한 우선순위 셀렉터)."""
    for _ in range(3):
        ok = page.evaluate("""
            () => {
                let target = document.querySelector('[data-placeholder*="내용"]');
                if (!target) {
                    const bc = document.querySelector('.se-component-body, .se-document-body');
                    if (bc) target = bc.querySelector('.se-text-paragraph');
                }
                if (!target) target = document.querySelector('.se-section-text .se-text-paragraph');
                if (!target) {
                    for (const p of document.querySelectorAll('.se-text-paragraph')) {
                        if (!p.closest('.se-documentTitle, .se-title')) { target = p; break; }
                    }
                }
                if (target) {
                    target.scrollIntoView({block:'center'});
                    target.click(); target.focus();
                    const ae = document.activeElement;
                    if (ae && ae.closest && ae.closest('.se-documentTitle, .se-title')) return null;
                    return 'ok';
                }
                return null;
            }
        """)
        if ok:
            return True
        _sp(0.6, 1.0)
    return False


def edit_article_replace(page: Page, cafe_id: str, menu_id: str, article_id: str,
                         new_title: str, new_body: str, image_paths=None,
                         log_callback=None, stop_check=None):
    """글 수정 페이지 진입 → 제목/본문/이미지 전체 교체 → 등록.

    new_body: 미디어 마커가 포함된 본문. 마커 자리에 image_paths 의 이미지를 순서대로 삽입.
    image_paths: 이 글에 쓸 이미지 경로 리스트(마커 수만큼 앞에서부터 소비).
    반환: (성공여부, 사용한 이미지 수) — 호출부가 배치 큐에서 소비량을 빼는 데 사용.
    """
    image_paths = image_paths or []

    def log(m):
        if log_callback:
            log_callback(m)

    # 본문 타이핑은 poster._human_type 를 재사용하는데, 이 함수는 poster 모듈 전역
    # _global_stop_check 를 본다. 이번 작업의 중지 플래그를 연결해 타이핑 중에도 중지가 먹게 한다.
    poster._global_stop_check = stop_check

    if stop_check and stop_check():
        return (False, 0)

    log(f"[시작] 수정: {article_id} / {new_title[:30]}")
    if not _enter_edit_page(page, cafe_id, menu_id, article_id, log):
        log(f"[실패] {article_id}: 수정 에디터 진입 실패")
        return (False, 0)
    _sp(1, 2)

    title_sel = 'textarea[placeholder*="제목"], input[placeholder*="제목"]'
    safe_title = re.sub(r'\s+', ' ', (new_title or '').replace('\n', ' ')).strip()

    # 1) 제목 교체 (기존 내용 비우고 새로 입력)
    try:
        el = page.wait_for_selector(title_sel, timeout=15000)
        el.click()
        _sp(0.3, 0.5)
        page.keyboard.press('Control+a')
        _sp(0.1, 0.2)
        page.keyboard.press('Delete')
        _sp(0.2, 0.3)
        if hasattr(el, 'fill'):
            try:
                el.fill(safe_title)
            except Exception:
                page.keyboard.type(safe_title, delay=random.randint(30, 60))
        else:
            page.keyboard.type(safe_title, delay=random.randint(30, 60))
        log("[진행] 제목 교체 완료")
    except Exception as e:
        log(f"[실패] {article_id}: 제목 입력 실패 - {e}")
        return (False, 0)

    # 2) 본문 포커스 + 기존 본문 전체 삭제
    if not _focus_body(page):
        log(f"[실패] {article_id}: 본문 포커스 실패")
        return (False, 0)
    try:
        page.keyboard.press('Control+a')
        _sp(0.2, 0.3)
        page.keyboard.press('Delete')
        _sp(0.3, 0.5)
        log("[진행] 기존 본문 비움")
    except Exception:
        pass

    # 3) 새 본문 입력 (텍스트 + 이미지 순차)
    ops = build_ops(new_body, image_paths)
    used = sum(1 for kind, _ in ops if kind == 'image')
    if image_paths:
        log(f"[진행] 본문 입력 — 이미지 {used}장 삽입")

    for kind, payload in ops:
        if stop_check and stop_check():
            log("[중지] 사용자 중지")
            return (False, used)
        if kind == 'text':
            content = apply_risky_words(payload, RISKY_WORD_PATH)
            _human_type(page, content)
            _sp()
        elif kind == 'image':
            _upload_image(page, payload)
            _sp(1, 1.5)
            try:
                page.keyboard.press('Control+e')  # 가운데 정렬 유지
            except Exception:
                pass
            _sp(0.1, 0.2)

    # 4) 등록(수정 완료)
    _sp(1, 2)
    # dialog 핸들러는 page 당 한 번만 등록 (글마다 호출되어도 누적되지 않도록)
    if not getattr(page, '_cedit_dialog_bound', False):
        page.on('dialog', lambda d: d.accept())
        try:
            page._cedit_dialog_bound = True
        except Exception:
            pass
    try:
        clicked = page.evaluate("""
            () => {
                const btn = document.querySelector('a.BaseButton--skinGreen');
                if (btn) { btn.click(); return 'BaseButton'; }
                for (const el of document.querySelectorAll('a, button')) {
                    const t = (el.textContent || '').trim();
                    if ((t === '등록' || t === '수정' || t === '수정완료' || t === '확인') && el.offsetWidth > 0) {
                        el.click(); return 'text:' + t;
                    }
                }
                return null;
            }
        """)
        log(f"[진행] 등록 클릭: {clicked}")
        if not clicked:
            log(f"[실패] {article_id}: 등록 버튼 못 찾음")
            return (False, used)
        _sp(3, 5)
    except Exception as e:
        log(f"[실패] {article_id}: 등록 실패 - {e}")
        return (False, used)

    cur = page.url
    if 'write' not in cur and 'edit' not in cur:
        log(f"[완료] {article_id} 수정 발행: {cur}")
    else:
        log(f"[완료] {article_id} (URL 확인 필요: {cur})")
    return (True, used)
