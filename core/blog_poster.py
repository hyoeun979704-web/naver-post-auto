"""네이버 블로그 글쓰기 자동화 모듈 — 카페 발행과 동일한 방식"""

import os
import shutil
import time
import random
from datetime import datetime
from playwright.sync_api import Page

from .template_parser import parse_manuscript, apply_risky_words, get_image_path, _find_manuscript_file


LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'log')
BLOG_DONE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'postings_done_blog')
BLOG_FAILED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'postings_failed_blog')
RISKY_WORD_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'risky_word.txt')


def _sp(a=0.3, b=0.8):
    time.sleep(random.uniform(a, b))


_global_stop_check = None

def _human_type(page: Page, text: str):
    """타이핑 — Enter 줄바꿈"""
    lines = text.split('\n') if '\n' in text else [text]
    for line in lines:
        if _global_stop_check and _global_stop_check():
            return
        line = line.strip()
        if not line:
            page.keyboard.press('Enter')
            time.sleep(0.1)
            page.keyboard.press('Enter')
            time.sleep(0.1)
            continue
        page.keyboard.type(line, delay=random.randint(5, 15))
        page.keyboard.press('Enter')
        time.sleep(random.uniform(0.05, 0.15))


def _log(message: str):
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, 'blog_upload_log.txt')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(f"{timestamp}\t{message}\n")


def _get_editor_frame(page: Page, log=None):
    """네이버 블로그 에디터가 들어있는 iframe 찾기.
    blog.naver.com/{id}/postwrite 는 mainFrame 안에 실제 에디터가 있음.
    """
    # 이름 기반
    for name in ['mainFrame', 'se-main-frame']:
        f = page.frame(name=name)
        if f:
            if log:
                log(f"[frame] name='{name}' 진입")
            return f
    # URL 기반
    for f in page.frames:
        if 'PostWriteForm' in (f.url or '') or 'postwrite' in (f.url or '').lower():
            if log:
                log(f"[frame] url 매칭: {f.url[:80]}")
            return f
    # 폴백: 최상위 page
    if log:
        log(f"[frame] iframe 못 찾음 - page 전체 사용")
    return page


def post_to_blog(page: Page, blog_id: str,
                 manuscript_dir: str,
                 log_callback=None, stop_check=None,
                 pkg_dir: str = '', draft: bool = False) -> bool:
    """네이버 블로그에 글 발행

    Args:
        page: Playwright 페이지
        blog_id: 네이버 블로그 아이디 (예: surfingtaiji)
        manuscript_dir: 원고 폴더 경로 (원고.txt + image/)
        log_callback: 로그 콜백
        stop_check: 중지 체크 함수
        pkg_dir: (선택) 패키징 결과 폴더 — '동영상 첨부' 마커가 있을 때
                 이 폴더의 {키워드}.mp4 를 업로드. 없으면 image/ 폴더에서 탐색.
        draft: True 면 발행 대신 임시저장(저장 버튼) 처리 — 테스트용
    """
    global _global_stop_check
    _global_stop_check = stop_check

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

        if not title:
            log(f"[실패] {folder_name}: 제목이 없습니다")
            return False

        log(f"[시작] {title}")

        # 2. 블로그 글쓰기 페이지 이동
        write_url = f'https://blog.naver.com/{blog_id}/postwrite'
        log(f"[진행] 글쓰기 페이지 이동...")
        page.goto(write_url, timeout=30000)
        _sp(3, 4)

        # iframe 로드 대기 + 진입
        try:
            page.wait_for_selector('#mainFrame', timeout=15000)
        except Exception:
            pass
        _sp(1, 2)
        frame = _get_editor_frame(page, log=log)

        # 에디터 준비 대기 — iframe 내부에서
        try:
            frame.wait_for_selector('.se-component, .se-text-paragraph, .se-documentTitle',
                                    timeout=15000)
        except Exception:
            log("[경고] 에디터 로드 확인 실패 - 진행 시도")
        _sp(1, 2)

        # '작성 중인 글이 있습니다' 팝업 처리 (새로 작성 선택)
        try:
            popup = frame.evaluate("""
                () => {
                    const btns = document.querySelectorAll('button, a');
                    for (const b of btns) {
                        const t = (b.textContent || '').trim();
                        if ((t === '새로 작성' || t === '취소') && b.offsetWidth > 0) {
                            b.click();
                            return t;
                        }
                    }
                    return null;
                }
            """)
            if popup:
                log(f"[진행] 팝업 닫기: {popup}")
                _sp(1, 1.5)
        except Exception:
            pass

        log(f"[진행] 글쓰기 페이지 로드 완료")

        # DOM 가벼운 확인 (정상 여부만)
        try:
            found_count = frame.evaluate("""
                () => {
                    return {
                        title: !!document.querySelector('.se-documentTitle'),
                        body: !!document.querySelector('.se-component.se-text .se-text-paragraph'),
                        link_btn: !!document.querySelector('.se-oglink-toolbar-button'),
                        video_btn: !!document.querySelector('.se-video-toolbar-button'),
                    };
                }
            """)
            log(f"[디버그] 에디터 요소: {found_count}")
        except Exception as e:
            log(f"[디버그] 체크 실패: {e}")

        # 3. 제목 입력 — .se-documentTitle .se-text-paragraph
        # 줄바꿈·탭 등 의도치 않은 키 입력 방지 — 한 줄로 강제
        import re as _re_title_b
        safe_title = title.replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
        safe_title = _re_title_b.sub(r'\s+', ' ', safe_title).strip()
        log(f"[진행] 제목 입력: {safe_title[:30]}...")
        try:
            title_el = frame.wait_for_selector(
                '.se-documentTitle .se-text-paragraph, '
                '.se-title-text .se-text-paragraph, '
                '.se-documentTitle, '
                'textarea[placeholder*="제목"]',
                timeout=15000
            )
            if title_el:
                title_el.click()
                _sp(0.5, 0.8)
                # 충분히 느리게 타이핑 (30~60ms) — race condition 방지 / 제목 잘림 방지
                page.keyboard.type(safe_title, delay=random.randint(30, 60))
                _sp(0.3, 0.5)
                log(f"[진행] 제목 입력됨")
            else:
                log(f"[실패] {folder_name}: 제목 요소 못 찾음")
                _move_to_failed_blog(manuscript_dir)
                return False
        except Exception as e:
            log(f"[실패] {folder_name}: 제목 입력 실패 - {e}")
            _move_to_failed_blog(manuscript_dir)
            return False

        # 4. 본문으로 이동 — ArrowDown 한 번 (엔터 X) + 중앙정렬
        log(f"[진행] 본문 영역으로 이동...")
        _sp(0.3, 0.6)
        page.keyboard.press('ArrowDown')
        _sp(0.3, 0.6)
        try:
            page.keyboard.press('Control+Alt+c')
            _sp(0.2, 0.3)
        except Exception:
            pass

        # 중앙정렬
        try:
            page.keyboard.press('Control+Alt+c')
            _sp(0.2, 0.3)
        except Exception:
            pass

        # 5. 섹션별 입력
        for section in sections:
            if stop_check and stop_check():
                log(f"[중지] 사용자 중지 요청")
                return False

            stype = section['type']
            content = section['content']

            if stype == 'text':
                content = apply_risky_words(content, RISKY_WORD_PATH)
                _human_type(page, content)
                _sp()

            elif stype == 'quote':
                # 인용구
                try:
                    frame.evaluate("""
                        () => {
                            const btn = document.querySelector('.se-insert-quotation-default-toolbar-button, '
                                + 'button[data-name="quotation"]');
                            if(btn) btn.click();
                        }
                    """)
                    _sp(0.5, 1)
                    page.keyboard.type(content, delay=random.randint(5, 15))
                    _sp(0.3, 0.5)

                    for _ in range(4):
                        page.keyboard.press('ArrowDown')
                        time.sleep(0.15)
                    page.keyboard.press('Enter')
                    _sp(0.3, 0.5)

                    page.keyboard.press('Control+Alt+c')
                    _sp(0.1, 0.2)
                except Exception:
                    _human_type(page, content)
                    _sp()

            elif stype == 'link':
                # 링크 — 툴바 링크 버튼 클릭 → 팝업에 URL paste → 확인 버튼 활성화 대기 → 클릭
                # 확인된 셀렉터: .se-popup-button-confirm, .se-popup-container
                log(f"[진행] 링크 삽입: {content[:50]}")
                try:
                    # 1) 링크 버튼 클릭
                    btn_clicked = frame.evaluate("""
                        () => {
                            const b = document.querySelector('.se-oglink-toolbar-button')
                                     || document.querySelector('button[data-name="oglink"]');
                            if (b && b.offsetWidth > 0) { b.click(); return true; }
                            return false;
                        }
                    """)
                    log(f"[진행] 링크 버튼 클릭: {btn_clicked}")
                    if not btn_clicked:
                        log("[경고] 링크 버튼 못 찾음 - 스킵")
                        continue

                    # 2) 팝업이 mainFrame 안에 뜰 때까지 대기
                    popup_ready = False
                    for _ in range(20):  # 최대 2초
                        time.sleep(0.1)
                        try:
                            r = frame.evaluate("""
                                () => {
                                    const p = document.querySelector('.se-popup-container');
                                    if (p && p.offsetWidth > 0) return true;
                                    return false;
                                }
                            """)
                            if r:
                                popup_ready = True
                                break
                        except Exception:
                            pass
                    log(f"[진행] 팝업 감지: {popup_ready}")

                    # 3) URL 입력창 포커스 + paste (insert_text)
                    input_focused = frame.evaluate("""
                        () => {
                            const sels = [
                                '.se-popup-container input[type="text"]',
                                '.se-popup-container input',
                                'input[placeholder*="URL"]',
                            ];
                            for (const s of sels) {
                                const inp = document.querySelector(s);
                                if (inp && inp.offsetWidth > 0) {
                                    inp.focus();
                                    inp.click();
                                    return true;
                                }
                            }
                            return false;
                        }
                    """)
                    log(f"[진행] URL input 포커스: {input_focused}")
                    _sp(0.3, 0.5)
                    # 기존 입력 초기화 후 paste
                    page.keyboard.press('Control+a')
                    time.sleep(0.2)
                    page.keyboard.insert_text(content)
                    log(f"[진행] URL paste 완료")
                    time.sleep(0.3)
                    # Enter 한 번 → 네이버 URL 정보 fetch 트리거 (검색 실행)
                    page.keyboard.press('Enter')
                    log(f"[진행] Enter로 URL 검색 트리거")

                    # 4) 확인 버튼 활성화까지 폴링 (최대 30초) — disabled + aria-disabled + opacity 체크
                    poll_js = """
                        () => {
                            const b = document.querySelector('.se-popup-button-confirm');
                            if (!b || b.offsetWidth <= 0) return {found: false};
                            const style = window.getComputedStyle(b);
                            const opacity = parseFloat(style.opacity || '1');
                            const pe = style.pointerEvents;
                            const aria = b.getAttribute('aria-disabled');
                            return {
                                found: true,
                                disabled: !!b.disabled,
                                aria_disabled: aria === 'true',
                                opacity: opacity,
                                pointer_events: pe,
                                cls: (b.className || '').substring(0, 100),
                            };
                        }
                    """
                    max_poll = 30
                    waited = 0
                    confirm_enabled = False
                    last_state = None
                    while waited < max_poll:
                        time.sleep(1)
                        waited += 1
                        state = frame.evaluate(poll_js)
                        last_state = state
                        if state and state.get('found'):
                            # 활성화 조건: 모든 disabled 지표가 아님 + opacity > 0.5
                            if (not state.get('disabled')
                                    and not state.get('aria_disabled')
                                    and state.get('opacity', 0) > 0.5
                                    and state.get('pointer_events') != 'none'):
                                confirm_enabled = True
                                log(f"[진행] 확인 버튼 활성화 ({waited}초): opacity={state.get('opacity')}")
                                break
                    if not confirm_enabled:
                        log(f"[디버그] {max_poll}초 후 버튼 상태: {last_state}")

                    # 5) 확인 버튼 클릭 + Enter (사용자 지시: 확인 후 Enter 한 번 쳐야 OG 카드 확정)
                    if confirm_enabled:
                        clicked = frame.evaluate("""
                            () => {
                                const b = document.querySelector('.se-popup-button-confirm');
                                if (b && !b.disabled && b.offsetWidth > 0) {
                                    b.click();
                                    return true;
                                }
                                return false;
                            }
                        """)
                        log(f"[진행] 확인 클릭: {clicked}")
                        _sp(1, 2)
                        # Enter 한 번 — OG 카드 확정
                        page.keyboard.press('Enter')
                        log(f"[진행] Enter 입력")
                        _sp(2, 3)
                        # OG 카드 삽입 후 중앙정렬
                        try:
                            page.keyboard.press('Control+Alt+c')
                            _sp(0.2, 0.4)
                        except Exception:
                            pass
                    else:
                        log(f"[경고] {max_poll}초 후에도 확인 비활성 - Escape로 팝업 닫음")
                        for _ in range(3):
                            page.keyboard.press('Escape')
                            time.sleep(0.3)
                except Exception as e:
                    log(f"[경고] 링크 처리 실패: {e}")
                _sp(0.5, 0.8)

            elif stype == 'video':
                # 동영상 — pkg_dir 우선, 없으면 image/ 폴더에서 탐색
                video_path = _find_video_path(manuscript_dir, pkg_dir, content)
                if video_path:
                    log(f"[진행] 동영상 업로드: {os.path.basename(video_path)}")
                    _upload_video_blog(frame, page, video_path, log=log)
                    _sp(1, 2)
                    try:
                        page.keyboard.press('Control+Alt+c')
                        _sp(0.1, 0.2)
                    except Exception:
                        pass
                else:
                    log(f"[실패] 동영상 파일 못 찾음 — 발행 중단")
                    raise RuntimeError("동영상 파일 누락 — 발행 중단")

            elif stype == 'thumbnail':
                # 썸네일 마커 — {키워드}<TEL:...> 에서 추출한 키워드로 이미지 탐색
                thumb_path = _find_thumbnail_path(manuscript_dir, pkg_dir, content)
                if thumb_path:
                    log(f"[진행] 썸네일 삽입: {os.path.basename(thumb_path)}")
                    _upload_image_blog(frame, page, thumb_path)
                    _sp(1.5, 2)
                    page.keyboard.press('Control+Alt+c')
                    _sp(0.1, 0.2)
                else:
                    log(f"[실패] 썸네일 파일 못 찾음 ('{content}') — 발행 중단")
                    raise RuntimeError(f"썸네일 파일 누락 ({content}) — 발행 중단")

            elif stype == 'img':
                img_path = get_image_path(manuscript_dir, content)
                if img_path:
                    _upload_image_blog(frame, page, img_path)
                    _sp(1, 1.5)
                    page.keyboard.press('Control+Alt+c')
                    _sp(0.1, 0.2)

        # 5.5) 전체 본문 중앙정렬 — 본문 클릭 → Ctrl+A ×2 (Naver 전체 선택) → Ctrl+E
        log(f"[진행] 전체 본문 중앙정렬 적용...")
        _sp(0.5, 1)
        try:
            # 첫 번째 본문 paragraph 클릭해서 커서 위치
            frame.evaluate("""
                () => {
                    const p = document.querySelector('.se-component.se-text .se-text-paragraph');
                    if (p) { p.click(); p.focus(); }
                }
            """)
            _sp(0.3, 0.5)
            # Naver 스마트에디터는 Ctrl+A가 블록 → 전체 단계로 동작
            page.keyboard.press('Control+a')
            time.sleep(0.3)
            page.keyboard.press('Control+a')  # 전체 문서
            time.sleep(0.3)
            page.keyboard.press('Control+Alt+c')
            time.sleep(0.5)
            # 선택 해제
            page.keyboard.press('End')
            _sp(0.2, 0.3)
            log(f"[진행] 중앙정렬 적용 완료")
        except Exception as e:
            log(f"[경고] 중앙정렬 실패: {e}")

        # 6. 발행 또는 임시저장
        _sp(1, 2)

        if draft:
            # 임시저장 — 네이버 블로그는 '저장' 버튼이 top bar 오른쪽에 있음
            log(f"[진행] 임시저장 버튼 탐색...")
            try:
                clicked = page.evaluate("""
                    () => {
                        // 1차: 명시적 셀렉터 (네이버 블로그 스마트에디터)
                        const selectors = [
                            'button.save_btn__bzc5B',
                            '.save_btn__bzc5B',
                            'button.btn_save_draft',
                            'button[data-log*="save"]',
                            'button[data-log*="draft"]',
                            'button.btn_save',
                        ];
                        for (const sel of selectors) {
                            const btn = document.querySelector(sel);
                            if (btn && btn.offsetWidth > 0 && !btn.disabled) {
                                btn.click();
                                return 'sel:' + sel;
                            }
                        }
                        // 2차: 텍스트 정확 일치 — 다만 '발행' '저장'이 포함된 큰 영역은 제외
                        // 상단바(header/toolbar)에 있는 것만 타겟
                        const all = document.querySelectorAll('button, a');
                        const hits = [];
                        for (const el of all) {
                            const t = (el.textContent || '').trim();
                            if (!el.offsetWidth) continue;
                            if (t === '저장' || t === '임시저장') {
                                hits.push({el, text: t, cls: el.className || ''});
                            }
                        }
                        if (hits.length) {
                            // 최상단(y값 작은) 것을 우선 — 에디터 상단바 버튼
                            hits.sort((a, b) => {
                                const ra = a.el.getBoundingClientRect();
                                const rb = b.el.getBoundingClientRect();
                                return ra.top - rb.top;
                            });
                            const chosen = hits[0];
                            chosen.el.click();
                            return `text:${chosen.text} cls:${chosen.cls} (${hits.length} candidates)`;
                        }
                        return null;
                    }
                """)
                log(f"[진행] 임시저장 클릭: {clicked}")

                # 버튼 못 찾으면 Ctrl+S 키보드 단축키로 시도 (대부분 에디터에서 동작)
                if not clicked:
                    log(f"[진행] 버튼 못 찾음 → Ctrl+S 키보드 단축키 시도", )
                    try:
                        # 본문 영역 포커스 후 Ctrl+S
                        try:
                            frame.evaluate("document.body.focus()")
                        except Exception:
                            pass
                        page.keyboard.press('Control+s')
                        clicked = 'ctrl+s'
                        _sp(0.8, 1.2)
                    except Exception as e:
                        log(f"[경고] Ctrl+S 실패: {e}")

                if not clicked:
                    log(f"[실패] {folder_name}: 저장 버튼 못 찾음")
                    _move_to_failed_blog(manuscript_dir)
                    return False
                _sp(2, 4)

                # 네이버가 '작성 중인 글이 있습니다' / '저장하시겠습니까' 팝업을 띄우는 경우
                # 최대 3번까지 반복해서 팝업 확인 클릭 (중첩 팝업 대응)
                for _try in range(3):
                    popup_clicked = page.evaluate("""
                        () => {
                            const btns = document.querySelectorAll('button, a');
                            for (const b of btns) {
                                const t = (b.textContent || '').trim();
                                if (!b.offsetWidth) continue;
                                // 모달/팝업 안에 있는 확인성 버튼들 광범위하게 클릭
                                const inPopup = b.closest('.se-popup, .se-dialog, [class*="popup"], [class*="modal"], [class*="layer"], [role="dialog"]');
                                if (inPopup && (t === '저장' || t === '확인' || t === '예' || t === '네' || t === '계속' || t === 'OK')) {
                                    b.click();
                                    return t;
                                }
                            }
                            return null;
                        }
                    """)
                    if not popup_clicked:
                        break
                    log(f"[진행] 팝업 확인 클릭: {popup_clicked}")
                    _sp(1, 2)

                # 저장 확인 — 토스트/알림/URL 변경 등 — 최대 8초 폴링
                save_confirmed = False
                for _ in range(16):  # 0.5s × 16 = 8s
                    confirm = page.evaluate("""
                        () => {
                            // 토스트 메시지에 '저장' 단어 포함되면 성공으로 간주
                            const toasts = document.querySelectorAll(
                                '[class*="toast"], [class*="snack"], [class*="noti"], [class*="alert_message"]'
                            );
                            for (const t of toasts) {
                                if (t.offsetWidth > 0 && /저장|임시/.test(t.textContent || '')) {
                                    return 'toast:' + (t.textContent || '').trim().slice(0, 30);
                                }
                            }
                            // postwrite URL에서 다른 곳으로 이동 안 했어도 OK — Naver는 페이지 유지
                            return null;
                        }
                    """)
                    if confirm:
                        save_confirmed = True
                        log(f"[확인] 저장 토스트: {confirm}")
                        break
                    _sp(0.3, 0.5)

                if not save_confirmed:
                    # 토스트 못 잡았어도 — Naver는 토스트 없이 조용히 저장하는 경우도 있음
                    # 폴백: Ctrl+S 한 번 더 (이중 저장 안전망)
                    try:
                        page.keyboard.press('Control+s')
                        _sp(1.5, 2.5)
                        # 팝업 한 번 더
                        page.evaluate("""
                            () => {
                                const btns = document.querySelectorAll('button');
                                for (const b of btns) {
                                    const t = (b.textContent || '').trim();
                                    if ((t === '저장' || t === '확인') && b.offsetWidth > 0 &&
                                        b.closest('[class*="popup"], [class*="modal"], [class*="layer"], [role="dialog"]')) {
                                        b.click(); return;
                                    }
                                }
                            }
                        """)
                        _sp(1, 2)
                        log(f"[진행] Ctrl+S 추가 시도 완료 (이중 저장 안전망)")
                    except Exception:
                        pass

                log(f"[완료] 임시저장 요청됨: {title}")
                _move_to_done_blog(manuscript_dir)
                return True
            except Exception as e:
                log(f"[실패] {folder_name}: 임시저장 실패 - {e}")
                _move_to_failed_blog(manuscript_dir)
                return False

        # 발행 모드
        log(f"[진행] 발행 버튼 클릭...")
        try:
            clicked = page.evaluate("""
                () => {
                    // 발행 버튼 찾기
                    const btns = document.querySelectorAll('button, a, span');
                    for (const btn of btns) {
                        const text = btn.textContent.trim();
                        if ((text === '발행' || text === '발행하기' || text === '등록') &&
                            btn.offsetWidth > 0) {
                            btn.click();
                            return text;
                        }
                    }
                    // 대안: 녹색 발행 버튼
                    const green = document.querySelector('.publish_btn, .btn_publish, '
                        + 'button.se-publish-btn, .se-publish-button');
                    if (green) { green.click(); return 'publish_btn'; }
                    return null;
                }
            """)

            log(f"[진행] 발행 클릭: {clicked}")

            if not clicked:
                log(f"[실패] {folder_name}: 발행 버튼 못 찾음")
                _move_to_failed_blog(manuscript_dir)
                return False

            _sp(3, 5)

        except Exception as e:
            log(f"[실패] {folder_name}: 발행 실패 - {e}")
            _move_to_failed_blog(manuscript_dir)
            return False

        # 7. 발행 확인
        current_url = page.url
        log(f"[진행] 발행 후 URL: {current_url}")

        if 'postwrite' not in current_url:
            log(f"[완료] {current_url}\t{title}")
            _move_to_done_blog(manuscript_dir)
            return True
        else:
            log(f"[완료] {title} (URL 확인 필요)")
            _move_to_done_blog(manuscript_dir)
            return True

    except Exception as e:
        log(f"[실패] {folder_name}: {e}")
        _move_to_failed_blog(manuscript_dir)
        return False


def _upload_image_blog(frame, page: Page, img_path: str):
    """블로그 에디터에 이미지 업로드.
    frame: 에디터 iframe (또는 page)
    page: file_chooser 이벤트 수신용 최상위 page
    """
    try:
        with page.expect_file_chooser(timeout=5000) as fc_info:
            frame.evaluate("""
                () => {
                    const btn = document.querySelector('.se-image-toolbar-button, '
                        + 'button[data-name="image"], .se-toolbar-item-image button');
                    if (btn) btn.click();
                }
            """)
        file_chooser = fc_info.value
        file_chooser.set_files(img_path)
        _sp(2, 3)
    except Exception:
        try:
            file_input = frame.query_selector('input[type="file"][accept*="image"]')
            if not file_input:
                file_input = frame.query_selector('input[type="file"]')
            if file_input:
                file_input.set_input_files(img_path)
                _sp(2, 3)
        except Exception:
            pass


def _insert_link_blog(frame, page: Page, url: str, log=None):
    """블로그 에디터에 링크(OG 카드) 삽입.
    frame: 에디터 iframe (또는 page)
    page: keyboard 입력용 최상위 page
    """
    if not url:
        return False

    # 1. 링크 버튼 클릭
    clicked = frame.evaluate("""
        () => {
            const selectors = [
                '.se-oglink-toolbar-button',
                '.se-link-toolbar-button',
                'button[data-name="oglink"]',
                'button[data-name="link"]',
                '.se-toolbar-item-oglink button',
                '.se-toolbar-item-link button',
            ];
            for (const sel of selectors) {
                const btn = document.querySelector(sel);
                if (btn && btn.offsetWidth > 0) {
                    btn.click();
                    return sel;
                }
            }
            // 텍스트 기반 폴백
            const btns = document.querySelectorAll('button, a');
            for (const b of btns) {
                const t = (b.textContent || b.getAttribute('aria-label') || '').trim();
                if ((t === '링크' || t === 'OG링크') && b.offsetWidth > 0) {
                    b.click();
                    return 'text:' + t;
                }
            }
            return null;
        }
    """)
    if log:
        log(f"[링크] 버튼 클릭: {clicked}")
    if not clicked:
        return False

    _sp(0.8, 1.2)

    # 2. URL 입력창 찾기 → URL 입력
    try:
        frame.wait_for_selector(
            '.se-popup-oglink input[type="text"], '
            '.se-popup-input-url input, '
            'input[placeholder*="URL"], '
            'input[placeholder*="링크"]',
            timeout=3000
        )
    except Exception:
        pass

    typed = frame.evaluate("""
        (url) => {
            const selectors = [
                '.se-popup-oglink input[type="text"]',
                '.se-popup-input-url input',
                'input[placeholder*="URL"]',
                'input[placeholder*="링크"]',
                '.se-popup-input input[type="text"]',
            ];
            for (const sel of selectors) {
                const input = document.querySelector(sel);
                if (input && input.offsetWidth > 0) {
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    setter.call(input, url);
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                    input.dispatchEvent(new Event('change', {bubbles: true}));
                    return sel;
                }
            }
            return null;
        }
    """, url)
    if log:
        log(f"[링크] URL 입력: {typed}")

    _sp(1.5, 2.5)  # OG 프리뷰 로드 대기

    # 3. 확인 버튼 클릭
    confirmed = frame.evaluate("""
        () => {
            const selectors = [
                '.se-popup-button-confirm',
                '.se-popup-button.se-popup-button-confirm',
                'button.btn_apply',
                'button[data-name="confirm"]',
            ];
            for (const sel of selectors) {
                const btn = document.querySelector(sel);
                if (btn && btn.offsetWidth > 0 && !btn.disabled) {
                    btn.click();
                    return sel;
                }
            }
            const btns = document.querySelectorAll('.se-popup button, .se-popup-container button');
            for (const b of btns) {
                const t = (b.textContent || '').trim();
                if ((t === '확인' || t === '삽입' || t === '적용') && b.offsetWidth > 0 && !b.disabled) {
                    b.click();
                    return 'text:' + t;
                }
            }
            return null;
        }
    """)
    if log:
        log(f"[링크] 확인 클릭: {confirmed}")

    _sp(1.5, 2.5)  # OG 카드 삽입 대기
    return bool(confirmed)


def _upload_video_blog(frame, page: Page, video_path: str, log=None):
    """블로그 에디터에 동영상 업로드 (네이버 신규 UI 플로우).
    1) 에디터 툴바 '동영상' 버튼 클릭 → 업로드 모달 열림
    2) 모달의 '동영상 추가' 버튼 클릭 → 파일 선택 창 열림
    3) 파일 선택 → 업로드 진행중 + 제목/정보 입력 모달
    4) 업로드 완료 대기 → 제목 필드에 파일명 입력
    5) '완료' 버튼 클릭
    """
    if not video_path or not os.path.isfile(video_path):
        return False

    keyword = os.path.splitext(os.path.basename(video_path))[0]
    # 40자 제한 (네이버 제목 최대 40)
    video_title = keyword[:40]

    # 1) 에디터 툴바의 동영상 버튼 클릭 (모달 열기)
    clicked = frame.evaluate("""
        () => {
            const selectors = [
                '.se-video-toolbar-button',
                'button[data-name="video"]',
                '.se-toolbar-item-video button',
            ];
            for (const sel of selectors) {
                const btn = document.querySelector(sel);
                if (btn && btn.offsetWidth > 0) {
                    btn.click();
                    return sel;
                }
            }
            return null;
        }
    """)
    if log:
        log(f"[동영상] 툴바 버튼 클릭: {clicked}")
    if not clicked:
        return False
    _sp(1.5, 2.5)

    # 2) 모달의 '동영상 추가' 버튼 클릭 → 파일 선택 창 열기
    try:
        with page.expect_file_chooser(timeout=10000) as fc_info:
            added = page.evaluate("""
                () => {
                    // 모달 내 '동영상 추가' 버튼 탐색
                    const btns = document.querySelectorAll('button, a');
                    for (const b of btns) {
                        const t = (b.textContent || '').trim();
                        if (t === '동영상 추가' && b.offsetWidth > 0) {
                            b.click();
                            return 'text:동영상 추가';
                        }
                    }
                    // 폴백: input[type=file] 직접 트리거
                    const inp = document.querySelector('input[type="file"][accept*="video"]')
                                || document.querySelector('input[type="file"]');
                    if (inp) { inp.click(); return 'file-input'; }
                    return null;
                }
            """)
            if log:
                log(f"[동영상] '동영상 추가' 클릭: {added}")
        file_chooser = fc_info.value
        file_chooser.set_files(video_path)
        if log:
            log(f"[동영상] 파일 선택: {os.path.basename(video_path)}")
    except Exception as e:
        if log:
            log(f"[동영상] 파일 선택 실패: {e}")
        # 폴백: input[type=file] 직접 주입
        try:
            vid_input = page.query_selector('input[type="file"][accept*="video"]')
            if not vid_input:
                vid_input = page.query_selector('input[type="file"]')
            if vid_input:
                vid_input.set_input_files(video_path)
                if log:
                    log(f"[동영상] 파일 직접 주입")
            else:
                return False
        except Exception as e2:
            if log:
                log(f"[동영상] 최종 실패: {e2}")
            return False

    # 3) 업로드/변환 모달 대기 — 제목 입력창이 보일 때까지
    if log:
        log("[동영상] 업로드 진행중 - 제목 입력창 대기...")
    _sp(3, 5)

    # 4) 제목 필드에 파일명 입력
    try:
        page.wait_for_selector('input[placeholder*="제목"]', timeout=15000)
    except Exception:
        pass

    title_filled = page.evaluate("""
        (t) => {
            const inputs = document.querySelectorAll('input[placeholder*="제목"]');
            for (const i of inputs) {
                if (i.offsetWidth > 0) {
                    i.focus();
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    setter.call(i, t);
                    i.dispatchEvent(new Event('input', {bubbles: true}));
                    i.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }
            }
            return false;
        }
    """, video_title)
    if log:
        log(f"[동영상] 제목 입력: '{video_title}' → {title_filled}")
    _sp(0.8, 1.5)

    # 5) 업로드/변환 완료 대기 — '완료' 버튼이 활성화(disabled 해제)될 때까지
    if log:
        log("[동영상] 업로드/변환 완료 대기 (최대 3분)...")
    max_wait = 180
    waited = 0
    done_enabled = False
    while waited < max_wait:
        if _global_stop_check and _global_stop_check():
            if log:
                log("[동영상] 중지 요청 감지")
            return False
        time.sleep(2)
        waited += 2
        try:
            state = page.evaluate("""
                () => {
                    const btns = document.querySelectorAll('button');
                    for (const b of btns) {
                        const t = (b.textContent || '').trim();
                        if (t === '완료' && b.offsetWidth > 0) {
                            return {found: true, disabled: !!b.disabled};
                        }
                    }
                    return {found: false};
                }
            """)
            if state.get('found') and not state.get('disabled'):
                done_enabled = True
                if log:
                    log(f"[동영상] 변환 완료 ({waited}초)")
                break
        except Exception:
            pass

    if not done_enabled:
        if log:
            log(f"[동영상] 타임아웃 ({max_wait}초) - 그대로 완료 시도")

    _sp(1, 2)

    # 6) '완료' 버튼 클릭
    done_clicked = page.evaluate("""
        () => {
            const btns = document.querySelectorAll('button');
            for (const b of btns) {
                const t = (b.textContent || '').trim();
                if (t === '완료' && b.offsetWidth > 0 && !b.disabled) {
                    b.click();
                    return true;
                }
            }
            return false;
        }
    """)
    if log:
        log(f"[동영상] 완료 클릭: {done_clicked}")

    _sp(2, 3)
    return True


def _find_thumbnail_path(manuscript_dir: str, pkg_dir: str, keyword: str) -> str:
    """썸네일 이미지 파일 경로 탐색.
    {keyword}.{jpg|png|webp|...} 를 pkg_dir > manuscript_dir > image/ 순서로 찾음.

    매칭 강화: 공백/언더스코어/하이픈 무시
    (예: '용인 입주청소.jpg' 도 '용인입주청소' 키워드로 매칭됨)
    """
    import re
    exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')

    def _norm(s: str) -> str:
        return re.sub(r'[\s_\-]', '', s).lower()

    search_dirs = []
    if pkg_dir and os.path.isdir(pkg_dir):
        search_dirs.append(pkg_dir)
    if manuscript_dir and os.path.isdir(manuscript_dir):
        search_dirs.append(manuscript_dir)
        image_dir = os.path.join(manuscript_dir, 'image')
        if os.path.isdir(image_dir):
            search_dirs.append(image_dir)

    norm_kw = _norm(keyword)

    for d in search_dirs:
        # 1) 정확 일치: {keyword}.{ext}
        for ext in exts:
            p = os.path.join(d, f'{keyword}{ext}')
            if os.path.isfile(p):
                return p
        # 2) 정규화 일치 (공백/언더스코어/하이픈 무시) — 정확 매칭
        try:
            for f in sorted(os.listdir(d)):
                if not f.lower().endswith(exts):
                    continue
                name_no_ext = os.path.splitext(f)[0]
                if _norm(name_no_ext) == norm_kw:
                    return os.path.join(d, f)
        except Exception:
            pass
        # 3) 정규화 시작-매칭 (썸네일 포맷: 'thumb_{keyword}_...' 등)
        try:
            for f in sorted(os.listdir(d)):
                if not f.lower().endswith(exts):
                    continue
                name_no_ext = os.path.splitext(f)[0]
                norm_name = _norm(name_no_ext)
                if norm_kw and norm_name.startswith(norm_kw):
                    return os.path.join(d, f)
        except Exception:
            pass
        # 4) 정규화 부분 매칭 (어디든 포함) — 최후 폴백
        try:
            for f in sorted(os.listdir(d)):
                if not f.lower().endswith(exts):
                    continue
                name_no_ext = os.path.splitext(f)[0]
                if norm_kw and norm_kw in _norm(name_no_ext):
                    return os.path.join(d, f)
        except Exception:
            pass
    return ''


def _find_video_path(manuscript_dir: str, pkg_dir: str, hint: str = '') -> str:
    """동영상 파일 경로 탐색.
    1) hint가 파일명이면 우선 탐색
    2) pkg_dir 내 mp4 파일 (키워드.mp4 > 첫번째 mp4)
    3) manuscript_dir/image/ 내 mp4
    4) manuscript_dir/ 바로 아래 mp4
    """
    # 1) 명시적 파일명
    if hint:
        for base in (pkg_dir, manuscript_dir, os.path.join(manuscript_dir, 'image')):
            if not base or not os.path.isdir(base):
                continue
            p = os.path.join(base, hint)
            if os.path.isfile(p):
                return p
            # 확장자 없이 준 경우
            if '.' not in hint:
                for ext in ('.mp4', '.mov', '.avi'):
                    p = os.path.join(base, hint + ext)
                    if os.path.isfile(p):
                        return p

    # 2) pkg_dir 내 mp4 — 키워드와 일치하는 것 우선
    if pkg_dir and os.path.isdir(pkg_dir):
        keyword = os.path.basename(pkg_dir)
        preferred = os.path.join(pkg_dir, f'{keyword}.mp4')
        if os.path.isfile(preferred):
            return preferred
        for f in sorted(os.listdir(pkg_dir)):
            if f.lower().endswith(('.mp4', '.mov')):
                return os.path.join(pkg_dir, f)

    # 3) image/ 서브폴더
    image_dir = os.path.join(manuscript_dir, 'image')
    if os.path.isdir(image_dir):
        for f in sorted(os.listdir(image_dir)):
            if f.lower().endswith(('.mp4', '.mov')):
                return os.path.join(image_dir, f)

    # 4) 원고 폴더 바로 아래
    if os.path.isdir(manuscript_dir):
        for f in sorted(os.listdir(manuscript_dir)):
            if f.lower().endswith(('.mp4', '.mov')):
                return os.path.join(manuscript_dir, f)

    return ''


def _move_to_done_blog(manuscript_dir: str):
    os.makedirs(BLOG_DONE_DIR, exist_ok=True)
    dest = os.path.join(BLOG_DONE_DIR, os.path.basename(manuscript_dir))
    try:
        shutil.move(manuscript_dir, dest)
    except Exception:
        pass


def _move_to_failed_blog(manuscript_dir: str):
    os.makedirs(BLOG_FAILED_DIR, exist_ok=True)
    dest = os.path.join(BLOG_FAILED_DIR, os.path.basename(manuscript_dir))
    try:
        shutil.move(manuscript_dir, dest)
    except Exception:
        pass
