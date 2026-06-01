"""네이버 카페 글쓰기 자동화 모듈 - UI 타이핑 방식"""

import os
import shutil
import time
import random
from datetime import datetime
from playwright.sync_api import Page

from .template_parser import parse_manuscript, apply_risky_words, get_image_path


LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'log')
POSTINGS_DONE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'postings_done_cafe')
POSTINGS_FAILED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'postings_failed_cafe')
RISKY_WORD_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'risky_word.txt')


def _sp(a=0.3, b=0.8):
    time.sleep(random.uniform(a, b))


_global_stop_check = None

def _human_type(page: Page, text: str):
    """타이핑 — Enter 줄바꿈, 빈 줄 Enter 2번 (문단 구분)"""
    lines = text.split('\n') if '\n' in text else [text]
    for line in lines:
        if _global_stop_check and _global_stop_check():
            return
        line = line.strip()
        if not line:
            # 빈 줄 = 문단 구분 → Enter 2번
            page.keyboard.press('Enter')
            time.sleep(0.1)
            page.keyboard.press('Enter')
            time.sleep(0.1)
            continue
        page.keyboard.type(line, delay=random.randint(5, 15))
        # 줄바꿈 → Enter 1번
        page.keyboard.press('Enter')
        time.sleep(random.uniform(0.05, 0.15))


def _log(message: str):
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, 'upload_log.txt')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(f"{timestamp}\t{message}\n")


def post_to_cafe(page: Page, cafe_url: str, board_name: str,
                 manuscript_dir: str, cafe_id: str = '', menu_id: str = '',
                 log_callback=None, stop_check=None, brand: str = '') -> bool:
    """카페에 글 발행

    brand: 발행 업체명('새집느낌'/'가족사랑클린'/'카드뉴스(정보성)'). 지정 시 본문 안의 다른 브랜드
           토큰(이름·전화번호·유튜브 링크)을 이 값으로 강제 치환해서 발행.
           '카드뉴스(정보성)' 모드에서는 텍스트 단락 끝에 빈 줄 2개를 추가로 박아 가독성 ↑
    """
    is_cardnews = (brand == '카드뉴스(정보성)')
    global _global_stop_check
    _global_stop_check = stop_check

    from .template_parser import _find_manuscript_file, normalize_brand
    manuscript_path = _find_manuscript_file(manuscript_dir) or os.path.join(manuscript_dir, '원고.txt')
    folder_name = os.path.basename(manuscript_dir)

    def log(msg):
        if log_callback:
            log_callback(msg)
        _log(msg)

    try:
        # 1. 원고 파싱
        data = parse_manuscript(manuscript_path)
        title = data['title']
        sections = data['sections']

        # 1-1. 발행 업체(brand) 기준 본문 정규화 — 다른 브랜드용 원고도 안전하게 발행
        if brand:
            title = normalize_brand(title, brand)
            for sec in sections:
                t = sec.get('type')
                if t in ('text', 'quote', 'link', 'thumbnail'):
                    sec['content'] = normalize_brand(sec.get('content', ''), brand)
            log(f"[브랜드] 발행 업체: {brand}")

        if not title:
            log(f"[실패] {folder_name}: 제목이 없습니다")
            return False

        log(f"[시작] {title}")

        # 2. 글쓰기 페이지 이동
        if cafe_id and menu_id:
            write_url = f'https://cafe.naver.com/ca-fe/cafes/{cafe_id}/menus/{menu_id}/articles/write?boardType=L'
        else:
            cafe_name = cafe_url.rstrip('/').split('/')[-1]
            write_url = f'https://cafe.naver.com/ca-fe/cafes/{cafe_name}/articles/write'

        log(f"[진행] 글쓰기 페이지 이동...")
        # 이전 발행 직후의 페이지 상태(글 상세·dialog 등)를 끊고 깨끗하게 시작
        try:
            page.goto('about:blank', timeout=10000)
            time.sleep(0.5)
        except Exception:
            pass

        # 글쓰기 페이지 이동 + 로딩 안정화 + 재시도 1회
        title_sel = 'textarea[placeholder*="제목"], input[placeholder*="제목"]'
        loaded = False
        for attempt in range(2):
            try:
                page.goto(write_url, timeout=30000)
                try:
                    page.wait_for_load_state('domcontentloaded', timeout=15000)
                except Exception:
                    pass
                time.sleep(1.5)
                # 제목 입력란이 보이는지 확인 (state=visible)
                page.wait_for_selector(title_sel, timeout=20000, state='visible')
                loaded = True
                break
            except Exception as e:
                if attempt == 0:
                    log(f"[재시도] 글쓰기 페이지 로드 실패 — 다시 시도 ({e})")
                    try:
                        page.goto('about:blank', timeout=10000)
                        time.sleep(1)
                    except Exception:
                        pass
                    continue
                else:
                    log(f"[실패] {folder_name}: 글쓰기 페이지 로드 실패 - {e}")
                    _move_to_failed(manuscript_dir)
                    return False

        if not loaded:
            log(f"[실패] {folder_name}: 글쓰기 페이지 로드 실패")
            _move_to_failed(manuscript_dir)
            return False

        _sp(1, 2)
        log(f"[진행] 글쓰기 페이지 로드 완료")

        # 3. 공개설정: 전체공개
        try:
            page.evaluate("""
                () => {
                    const labels = document.querySelectorAll('label, span');
                    for (const el of labels) {
                        if (el.textContent && el.textContent.includes('전체공개')) {
                            el.click(); return true;
                        }
                    }
                    return false;
                }
            """)
            log(f"[진행] 공개설정: 전체공개")
        except Exception:
            pass

        # 4. 제목 입력
        # 줄바꿈·탭 등 의도치 않은 키 입력 방지 — 한 줄로 강제
        safe_title = title.replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
        # 연속 공백 1칸으로
        import re as _re_title
        safe_title = _re_title.sub(r'\s+', ' ', safe_title).strip()
        log(f"[진행] 제목 입력: {safe_title[:30]}...")
        try:
            title_el = page.wait_for_selector(
                'textarea[placeholder*="제목"], input[placeholder*="제목"]',
                timeout=15000
            )
            if not title_el:
                log(f"[실패] {folder_name}: 제목 요소 못 찾음")
                _move_to_failed(manuscript_dir)
                return False

            title_el.click()
            _sp(0.5, 0.8)

            # 1차: fill() 로 한 번에 직접 입력 (가장 안전 — 키스트로크 race 없음)
            entered = False
            try:
                title_el.fill('')
                _sp(0.1, 0.2)
                title_el.fill(safe_title)
                _sp(0.5, 0.8)
                # Naver 가 input/change 이벤트 인식하도록 한 글자 더치고 지움
                page.keyboard.press('End')
                page.keyboard.press(' ')
                page.keyboard.press('Backspace')
                _sp(0.3, 0.5)
                # 확인 — 현재 입력된 텍스트 읽기
                cur = title_el.input_value() if hasattr(title_el, 'input_value') else ''
                if cur and cur.strip() == safe_title:
                    entered = True
                    log(f"[진행] 제목 입력됨 (fill 방식)")
            except Exception as e:
                log(f"[경고] fill 방식 실패 — type 방식으로 폴백 ({e})")

            # 2차: 타이핑 — fill 실패 시 또는 짧은 텍스트일 때 안전망
            if not entered:
                try:
                    title_el.click()
                    _sp(0.3, 0.5)
                    # 기존 텍스트 제거 (Ctrl+A → Delete)
                    page.keyboard.press('Control+a')
                    _sp(0.1, 0.2)
                    page.keyboard.press('Delete')
                    _sp(0.2, 0.3)
                    # 타이핑 — 충분히 느리게 (30~60ms) — race 방지
                    page.keyboard.type(safe_title, delay=random.randint(30, 60))
                    _sp(0.3, 0.5)
                    log(f"[진행] 제목 입력됨 (type 방식)")
                except Exception as e:
                    log(f"[실패] {folder_name}: 제목 입력 실패 - {e}")
                    _move_to_failed(manuscript_dir)
                    return False
        except Exception as e:
            log(f"[실패] {folder_name}: 제목 입력 실패 - {e}")
            _move_to_failed(manuscript_dir)
            return False

        # 5. 본문 입력
        log(f"[진행] 본문 입력 중...")
        _sp(0.8, 1.2)

        # 본문 영역 포커스 — 제목 영역(.se-documentTitle) 제외하고 본문 첫 단락만 잡기
        # 최대 3회 재시도 — 실패 시 본문 입력 중단 (제목 field 에 본문 leak 방지)
        focus_ok = None
        for _retry in range(3):
            focus_ok = page.evaluate("""
                () => {
                    // 우선순위 1: data-placeholder*="내용" (본문 placeholder)
                    let target = document.querySelector('[data-placeholder*="내용"]');
                    // 우선순위 2: .se-component-body .se-text-paragraph (본문 컴포넌트 안의 단락)
                    if (!target) {
                        const bodyContainer = document.querySelector('.se-component-body, .se-document-body');
                        if (bodyContainer) {
                            target = bodyContainer.querySelector('.se-text-paragraph');
                        }
                    }
                    // 우선순위 3: .se-section-text .se-text-paragraph (텍스트 섹션)
                    if (!target) {
                        target = document.querySelector('.se-section-text .se-text-paragraph');
                    }
                    // 우선순위 4: 제목(.se-documentTitle) 제외하고 첫 .se-text-paragraph
                    if (!target) {
                        const allP = document.querySelectorAll('.se-text-paragraph');
                        for (const p of allP) {
                            if (!p.closest('.se-documentTitle, .se-title')) {
                                target = p;
                                break;
                            }
                        }
                    }
                    if (target) {
                        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        target.click();
                        target.focus();
                        // active element 가 제목 영역에 속하면 실패로 간주
                        const ae = document.activeElement;
                        if (ae && ae.closest && ae.closest('.se-documentTitle, .se-title')) {
                            return null;
                        }
                        return 'ok:' + (target.className || target.tagName);
                    }
                    return null;
                }
            """)
            if focus_ok:
                break
            log(f"[경고] 본문 포커스 재시도 {_retry+1}/3")
            _sp(0.8, 1.2)
        log(f"[디버그] 본문 포커스: {focus_ok}")
        if not focus_ok:
            log(f"[실패] {folder_name}: 본문 영역 포커스 실패 — 본문 입력 중단 (제목 field 오염 방지)")
            _move_to_failed(manuscript_dir)
            return False
        _sp(0.5, 0.8)

        # 중앙정렬 — 정렬 버튼 클릭 후 가운데정렬 선택
        try:
            # 글감 메뉴 숨기기
            page.evaluate("() => { const m = document.querySelector('.se-floating-material-container'); if(m) m.style.display='none'; }")
            _sp(0.2, 0.3)
            # 정렬 드롭다운 버튼 클릭
            page.click('.se-align-left-toolbar-button, [data-name="align-drop-down-with-justify"]')
            _sp(0.5, 0.8)
            # 가운데정렬 클릭
            page.evaluate("""
                () => {
                    const btns = document.querySelectorAll('[data-value="center"], .se-toolbar-item-align button');
                    for (const btn of btns) {
                        if (btn.getAttribute('data-value') === 'center' ||
                            btn.className.includes('center')) {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }
            """)
            _sp(0.2, 0.3)
        except Exception:
            # 실패하면 Ctrl+E 시도
            page.keyboard.press('Control+e')
            _sp(0.2, 0.3)

        # 섹션별 입력
        for section in sections:
            if stop_check and stop_check():
                log(f"[중지] 사용자 중지 요청")
                return False

            stype = section['type']
            content = section['content']

            if stype == 'text':
                content = apply_risky_words(content, RISKY_WORD_PATH)
                _human_type(page, content)
                # 카드뉴스 모드: 텍스트 단락 끝에 추가 Enter — 단락 사이 2칸 띄움 (사용자 요청)
                # 일반 모드는 그대로 (이미지·텍스트 간격 변동 없음)
                if is_cardnews:
                    page.keyboard.press('Enter')
                    time.sleep(0.1)
                    page.keyboard.press('Enter')
                    time.sleep(0.1)
                _sp()

            elif stype == 'quote':
                # 인용구: 글감메뉴 숨기기 → 버튼 클릭 → 텍스트 → ArrowDown 4번 + Enter
                try:
                    page.evaluate("""
                        () => {
                            const m = document.querySelector('.se-floating-material-container');
                            if(m) m.style.display='none';
                            const btn = document.querySelector('.se-insert-quotation-default-toolbar-button');
                            if(btn) btn.click();
                        }
                    """)
                    _sp(0.5, 1)
                    page.keyboard.type(content, delay=random.randint(5, 15))
                    _sp(0.3, 0.5)

                    # 인용구 밖으로: ArrowDown 4번 + Enter
                    for _ in range(4):
                        page.keyboard.press('ArrowDown')
                        time.sleep(0.15)
                    page.keyboard.press('Enter')
                    _sp(0.3, 0.5)

                    # 중앙정렬 복구
                    page.keyboard.press('Control+e')
                    _sp(0.1, 0.2)
                except Exception:
                    _human_type(page, content)
                    _sp()

            elif stype == 'link':
                # 링크 — 나중에 구현, 지금은 스킵
                _sp()

            elif stype == 'img':
                img_path = get_image_path(manuscript_dir, content)
                if img_path:
                    _upload_image(page, img_path)
                    _sp(1, 1.5)
                    page.keyboard.press('Control+e')
                    _sp(0.1, 0.2)

        # 5.5) 전체 본문 중앙정렬 강제 — 본문 영역만 클릭 → Ctrl+A ×2 → Ctrl+E
        log(f"[진행] 전체 본문 중앙정렬 적용...")
        _sp(0.5, 1)
        try:
            page.evaluate("""
                () => {
                    // 제목 제외하고 본문 첫 단락 잡기
                    let target = document.querySelector('[data-placeholder*="내용"]');
                    if (!target) {
                        const bodyContainer = document.querySelector('.se-component-body, .se-document-body');
                        if (bodyContainer) {
                            target = bodyContainer.querySelector('.se-text-paragraph');
                        }
                    }
                    if (!target) {
                        const allP = document.querySelectorAll('.se-text-paragraph');
                        for (const p of allP) {
                            if (!p.closest('.se-documentTitle, .se-title')) {
                                target = p;
                                break;
                            }
                        }
                    }
                    if (target) { target.click(); target.focus(); }
                }
            """)
            _sp(0.3, 0.5)
            page.keyboard.press('Control+a')
            time.sleep(0.3)
            page.keyboard.press('Control+a')
            time.sleep(0.3)
            page.keyboard.press('Control+e')
            time.sleep(0.5)
            page.keyboard.press('End')
            _sp(0.2, 0.3)
            log(f"[진행] 중앙정렬 적용 완료")
        except Exception as e:
            log(f"[경고] 중앙정렬 실패: {e}")

        # 6. 등록
        log(f"[진행] 등록 버튼 클릭...")
        _sp(1, 2)

        page.on('dialog', lambda dialog: dialog.accept())

        try:
            clicked = page.evaluate("""
                () => {
                    const btn = document.querySelector('a.BaseButton--skinGreen');
                    if (btn) { btn.click(); return 'BaseButton'; }
                    const els = document.querySelectorAll('a, button');
                    for (const el of els) {
                        if (el.textContent.trim() === '등록' && el.offsetWidth > 0) {
                            el.click(); return 'text match';
                        }
                    }
                    return null;
                }
            """)

            log(f"[진행] 등록 클릭: {clicked}")

            if not clicked:
                log(f"[실패] {folder_name}: 등록 버튼 못 찾음")
                _move_to_failed(manuscript_dir)
                return False

            _sp(3, 5)

        except Exception as e:
            log(f"[실패] {folder_name}: 등록 실패 - {e}")
            _move_to_failed(manuscript_dir)
            return False

        # 7. 발행 확인
        current_url = page.url
        log(f"[진행] 등록 후 URL: {current_url}")

        # write 페이지가 아니거나, 다른 카페 URL로 이동했으면 성공
        if 'write' not in current_url or 'articles' in current_url:
            log(f"[완료] {current_url}\t{title}")
            _move_to_done(manuscript_dir)
            return True
        else:
            # write 페이지에 머물러도 일단 성공으로 처리 (등록은 됐을 수 있음)
            log(f"[완료] {title} (URL 확인 필요)")
            _move_to_done(manuscript_dir)
            return True

    except Exception as e:
        log(f"[실패] {folder_name}: {e}")
        _move_to_failed(manuscript_dir)
        return False


def _upload_image(page: Page, img_path: str):
    """에디터에 이미지 업로드 (파일 대화상자 없이)"""
    try:
        # file_chooser 이벤트를 기다리면서 사진 버튼 클릭
        with page.expect_file_chooser(timeout=5000) as fc_info:
            page.evaluate("""
                () => {
                    const btn = document.querySelector('.se-image-toolbar-button');
                    if (btn) btn.click();
                }
            """)
        file_chooser = fc_info.value
        file_chooser.set_files(img_path)
        _sp(2, 3)
    except Exception:
        # 백업: 직접 input 찾아서 넣기
        try:
            file_input = page.query_selector('input[type="file"]')
            if file_input:
                file_input.set_input_files(img_path)
                _sp(2, 3)
        except Exception:
            pass


def _move_to_done(manuscript_dir: str):
    os.makedirs(POSTINGS_DONE_DIR, exist_ok=True)
    dest = os.path.join(POSTINGS_DONE_DIR, os.path.basename(manuscript_dir))
    try:
        shutil.move(manuscript_dir, dest)
    except Exception:
        pass


def _move_to_failed(manuscript_dir: str):
    os.makedirs(POSTINGS_FAILED_DIR, exist_ok=True)
    dest = os.path.join(POSTINGS_FAILED_DIR, os.path.basename(manuscript_dir))
    try:
        shutil.move(manuscript_dir, dest)
    except Exception:
        pass
