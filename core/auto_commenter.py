"""카페 신규 글 자동 댓글 모듈.

흐름:
1. 모니터링 게시판 목록을 주기적으로 polling (UI에서 분 단위 설정)
2. 각 게시판의 최신 글 article_id를 수집
3. 처리 안 한 article_id 만 추출 (state 파일로 추적)
4. 자기 글이면 스킵
5. 댓글 풀에서 랜덤 선택 (없거나 옵션 켜져있으면 AI 생성)
6. 댓글 작성 (게시글 진입 → 댓글 입력 → 등록)
7. 처리 완료 표시 (state 저장)
8. 다음 글까지 랜덤 딜레이 후 진행
"""

import os
import re
import json
import time
import random
from typing import Optional


# ── 상태 파일 ──
STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                          'config', 'auto_comment_state.json')

# 최근 처리한 article_id를 카페·게시판별로 최대 N개 보관
MAX_PROCESSED_PER_BOARD = 200


def _load_state() -> dict:
    if not os.path.isfile(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    try:
        with open(STATE_PATH, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _board_key(cafe_id: str, menu_id: str) -> str:
    return f'{cafe_id}_{menu_id}'


def parse_board_url(url: str) -> Optional[dict]:
    """
    'https://cafe.naver.com/ca-fe/cafes/10174516/menus/664' 같은 URL → {'cafe_id', 'menu_id'}
    또는 'https://cafe.naver.com/getamped2' 같은 카페 메인 → cafe_id 못 잡음 (None 반환)
    """
    m = re.search(r'cafes/(\d+)/menus/(\d+)', url)
    if m:
        return {'cafe_id': m.group(1), 'menu_id': m.group(2)}
    return None


def fetch_recent_articles(page, cafe_id: str, menu_id: str,
                          log_callback=None) -> list:
    """게시판 페이지에서 최근 글의 (article_id, title, writer_id) 리스트 반환.
    article_id 정수가 큰 순 = 최신순.
    """
    url = f'https://cafe.naver.com/ca-fe/cafes/{cafe_id}/menus/{menu_id}?viewType=L'
    try:
        page.goto(url, timeout=30000)
        time.sleep(1.5)
    except Exception as e:
        if log_callback:
            log_callback(f'  [에러] 게시판 이동 실패: {e}')
        return []

    # iframe 안에 게시판 리스트가 있는 경우와 없는 경우 모두 대응
    frames = [page] + list(page.frames)
    rows = []
    for frm in frames:
        try:
            data = frm.evaluate("""
                () => {
                    // 게시글 행: <a class="article" href="..."> 또는 .ArticleBoardList li
                    const out = [];
                    const links = document.querySelectorAll('a[href*="/articles/"]');
                    const seen = new Set();
                    for (const a of links) {
                        const href = a.getAttribute('href') || '';
                        const m = href.match(/articles\\/(\\d+)/);
                        if (!m) continue;
                        const aid = m[1];
                        if (seen.has(aid)) continue;
                        seen.add(aid);
                        const title = (a.textContent || '').trim().substring(0, 80);
                        // 작성자 링크 찾기 (같은 행 내)
                        let writer = '';
                        try {
                            const row = a.closest('tr, li, div');
                            if (row) {
                                const w = row.querySelector('a[href*="/cafes/"][href*="/members/"], .nick_name, .ellip');
                                if (w) writer = (w.textContent || '').trim();
                            }
                        } catch(e) {}
                        out.push({aid, title, writer});
                    }
                    return out;
                }
            """)
            if data:
                rows.extend(data)
                if len(rows) >= 5:
                    break
        except Exception:
            continue

    # article_id 정수 큰 순 정렬, 중복 제거
    seen = set()
    uniq = []
    for r in rows:
        if r['aid'] in seen:
            continue
        seen.add(r['aid'])
        uniq.append(r)
    uniq.sort(key=lambda x: int(x['aid']) if x['aid'].isdigit() else 0, reverse=True)
    return uniq[:30]  # 최대 30개만


def post_comment(page, cafe_id: str, menu_id: str, article_id: str,
                 comment_text: str, log_callback=None,
                 stop_check=None) -> bool:
    """주어진 글에 댓글 작성. 성공 시 True."""
    url = f'https://cafe.naver.com/ca-fe/cafes/{cafe_id}/menus/{menu_id}/articles/{article_id}'
    try:
        page.goto(url, timeout=30000)
        time.sleep(2)
    except Exception as e:
        if log_callback:
            log_callback(f'  [에러] 글 진입 실패 ({article_id}): {e}')
        return False

    if stop_check and stop_check():
        return False

    # 댓글 입력란 찾기 (iframe 안일 수도 있음)
    frames = [page] + list(page.frames)
    target_frame = None
    target_input = None
    for frm in frames:
        try:
            found = frm.evaluate("""
                () => {
                    const sels = [
                        'textarea[placeholder*="댓글"]',
                        'div.comment_inbox_text[contenteditable]',
                        'div[contenteditable="true"][placeholder*="댓글"]',
                        '.CommentWriterTextArea',
                    ];
                    for (const s of sels) {
                        const el = document.querySelector(s);
                        if (el && el.offsetWidth > 0) { return s; }
                    }
                    return null;
                }
            """)
            if found:
                target_frame = frm
                target_input = found
                break
        except Exception:
            continue

    if not target_frame or not target_input:
        if log_callback:
            log_callback(f'  [실패] 댓글 입력란 못 찾음 ({article_id})')
        return False

    try:
        # 클릭해서 포커스
        target_frame.evaluate(f"""
            () => {{
                const el = document.querySelector("{target_input}");
                if (el) {{ el.click(); el.focus(); }}
            }}
        """)
        time.sleep(0.5)

        # 텍스트 입력 — 사람처럼 천천히
        page.keyboard.type(comment_text, delay=random.randint(15, 35))
        time.sleep(random.uniform(0.5, 1.0))

        # 등록 버튼 클릭
        clicked = target_frame.evaluate("""
            () => {
                const btns = document.querySelectorAll('button, a');
                for (const b of btns) {
                    const t = (b.textContent || '').trim();
                    if ((t === '등록' || t === '댓글 등록') && b.offsetWidth > 0) {
                        b.click();
                        return true;
                    }
                }
                return false;
            }
        """)
        time.sleep(1.5)
        if not clicked:
            if log_callback:
                log_callback(f'  [실패] 등록 버튼 못 찾음 ({article_id})')
            return False
        if log_callback:
            log_callback(f'  [완료] 댓글 작성 — {article_id}: "{comment_text[:30]}..."')
        return True
    except Exception as e:
        if log_callback:
            log_callback(f'  [에러] 댓글 입력 실패 ({article_id}): {e}')
        return False


def pick_comment(pool: list, keywords: list = None,
                 post_title: str = '', post_body: str = '',
                 use_ai_fallback: bool = True,
                 ai_callback=None) -> str:
    """댓글 텍스트 선택 — 풀에 있으면 랜덤, 없으면 AI fallback.

    keywords: 메인 키워드 풀. 댓글마다 그중 랜덤 1개 선택.
              풀 댓글에 '{키워드}' 마커 있으면 그 자리에 삽입.
              마커 없으면 그대로 사용 (자연스러운 톤 유지).
              AI 모드면 prompt에 키워드를 자연스럽게 포함하도록 지시.
    """
    pool_clean = [s.strip() for s in (pool or []) if s.strip()]
    keywords_clean = [k.strip() for k in (keywords or []) if k.strip()]
    chosen_kw = random.choice(keywords_clean) if keywords_clean else ''

    if pool_clean:
        comment = random.choice(pool_clean)
        if chosen_kw and '{키워드}' in comment:
            comment = comment.replace('{키워드}', chosen_kw)
        elif chosen_kw and '{keyword}' in comment.lower():
            # 영어 마커도 호환
            comment = re.sub(r'\{keyword\}', chosen_kw, comment, flags=re.IGNORECASE)
        # 마커 없으면 그대로 — 사용자가 의도적으로 키워드 빼고 쓴 댓글로 간주
        return comment

    if use_ai_fallback and ai_callback:
        try:
            return ai_callback(post_title, post_body, chosen_kw) or ''
        except Exception:
            return ''
    return ''


def generate_ai_comment(post_title: str, post_body: str = '',
                        claude_key: str = '', model: str = 'claude-sonnet-4-6',
                        keyword: str = '') -> str:
    """Claude로 자연스러운 댓글 생성 (60자 이내).

    keyword: 메인 키워드. 비어있지 않으면 prompt에 자연스럽게 포함하도록 지시.
    """
    try:
        import anthropic
    except ImportError:
        return ''
    if not claude_key:
        return ''
    try:
        client = anthropic.Anthropic(api_key=claude_key)
        kw_block = ''
        if keyword:
            kw_block = (
                f"\n[키워드 — 댓글에 자연스럽게 1번 포함]\n"
                f"{keyword}\n"
            )
        prompt = f"""아래는 네이버 카페에 올라온 글입니다. 자연스럽고 친근한 댓글 한 줄을 작성해주세요.

[글 제목]
{post_title}

[글 일부]
{(post_body or '')[:300]}{kw_block}

[댓글 규칙]
- 25~60자 한 줄
- 광고 톤 X, 친근한 톤
- 끝에 이모지 1개 정도 OK (선택)
- "안녕하세요" 같은 인사말로 시작하지 말 것 (이미 글 본문에 있음)
- 글 내용에 호응하는 자연스러운 한 마디
- 광고 링크/연락처 절대 포함 금지
{f"- '{keyword}' 단어를 댓글에 자연스럽게 1번 포함" if keyword else ""}

댓글만 한 줄로 출력. 다른 텍스트 X."""
        resp = client.messages.create(
            model=model,
            max_tokens=200,
            messages=[{'role': 'user', 'content': prompt}],
        )
        text = ''.join(block.text for block in resp.content if hasattr(block, 'text')).strip()
        # 줄바꿈 제거
        text = text.split('\n')[0].strip()
        return text[:80]  # 안전 cap
    except Exception:
        return ''


def update_processed(state: dict, board_key: str, article_id: str):
    """state에 처리한 article_id 추가."""
    info = state.setdefault(board_key, {'processed_ids': []})
    if article_id not in info['processed_ids']:
        info['processed_ids'].append(article_id)
    # 오래된 것 trim
    if len(info['processed_ids']) > MAX_PROCESSED_PER_BOARD:
        info['processed_ids'] = info['processed_ids'][-MAX_PROCESSED_PER_BOARD:]


def is_processed(state: dict, board_key: str, article_id: str) -> bool:
    info = state.get(board_key)
    if not info:
        return False
    return article_id in info.get('processed_ids', [])
