"""원고.txt 파싱 모듈 - 줄 기반 원고 형식 파싱

원고 형식:
    제목 : 글 제목
    본문 텍스트 (여러 줄)
    링크 : https://...
    인용구 : 인용구 텍스트
    1  (숫자만 있는 줄 = 이미지 1.jpg)
    2
    ...
"""

import re
import os
import random


# ─── 브랜드별 자산 (카페 발행 시 본문 정규화용) ───────────────
# 카페 발행 탭의 '발행 업체' 드롭다운 값에 따라 본문의 다른 브랜드 토큰을
# 선택 브랜드 토큰으로 강제 치환. 원고 파일 자체가 다른 브랜드용으로 생성됐어도
# 카페에는 선택한 브랜드로 나가게 만든다.
BRAND_TEL = {
    '새집느낌': '1660-0240',
    '가족사랑클린': '1833-2436',
}

BRAND_LINKS = {
    '새집느낌': [
        'https://youtu.be/1B22QcRXD0k?si=9CtROH34njgODg1a',
        'https://youtu.be/Ss5Do90Wr6g?si=QABezrUrQeZmxdd4',
        'https://youtu.be/xDbn9-GPDEQ?si=vO9kOLMadiPuTS2S',
        'https://youtu.be/YoPXFtPGL2g?si=yLs5KCQoHNIC1km6',
        'https://youtu.be/ioTo-2NARA0?si=QDSg9G4I-NKMMHqa',
        'https://youtu.be/Ss5Do90Wr6g?si=3eY4Kx7Mr62Vryyt',
        'https://youtu.be/mPyx50bqYto?si=y5GNXQouOxjoHjcG',
    ],
    '가족사랑클린': [
        'https://youtube.com/shorts/qHWjZOcAAO8?si=kqA5kNWJEgYFGgjO',
    ],
}


def normalize_brand(text: str, target_brand: str) -> str:
    """본문 텍스트에서 다른 브랜드 토큰을 target_brand 기준으로 치환.

    1) 다른 브랜드 단어 → target_brand
    2) 다른 브랜드 전화번호 → target_brand 전화번호
    3) 다른 브랜드 유튜브 URL → target_brand 풀에서 랜덤 1개
    """
    if not text or not target_brand:
        return text
    if target_brand not in BRAND_TEL:
        return text

    target_tel = BRAND_TEL.get(target_brand)
    target_links = BRAND_LINKS.get(target_brand) or []

    # 1) 다른 브랜드 단어 → target_brand
    for other in BRAND_TEL.keys():
        if other == target_brand:
            continue
        text = text.replace(other, target_brand)

    # 2) 다른 브랜드 전화번호 → target_brand 전화번호
    if target_tel:
        for other_brand, other_tel in BRAND_TEL.items():
            if other_brand == target_brand or not other_tel:
                continue
            text = text.replace(other_tel, target_tel)

    # 3) 다른 브랜드 유튜브 URL → target_brand 풀에서 랜덤
    if target_links:
        for other_brand, urls in BRAND_LINKS.items():
            if other_brand == target_brand:
                continue
            for url in urls:
                if url and url in text:
                    text = text.replace(url, random.choice(target_links))

    return text


def _merge_split_title(lines: list) -> list:
    """원고가 '제목 :' 마커 없이 첫 줄을 제목으로 쓰는 경우, AI가 제목을
    두 줄에 걸쳐 출력한 케이스를 한 줄로 합쳐줌.

    예: '옥천이사청소 새 집처럼 깔끔해지는 경험을\n하게 됐어요\n\n0\n...'
       → '옥천이사청소 새 집처럼 깔끔해지는 경험을 하게 됐어요\n\n0\n...'

    조건:
    1) 첫 줄이 '제목' 마커가 없음
    2) 첫 줄이 짧고 (조사·연결어로 끝나거나 종결어미 없이) 미완결 느낌
    3) 다음 줄이 비어있지 않고 마커가 아님
    4) 두 줄 합쳐도 80자 이내
    """
    if not lines:
        return lines

    # 첫 비어있지 않은 줄 찾기
    first_idx = None
    for i, ln in enumerate(lines):
        if ln.strip():
            first_idx = i
            break
    if first_idx is None:
        return lines

    first = lines[first_idx].rstrip('\n').strip()
    # 이미 '제목 :' 마커가 있거나 markdown # 헤더면 손대지 않음
    if (first.startswith('제목 :') or first.startswith('제목:')
            or first.startswith('# ') or first.startswith('## ')):
        return lines
    # 첫 줄이 다른 마커면 처리 안 함
    if (first.startswith('인용구') or first.startswith('링크')
            or first.startswith('동영상') or first.startswith('소제목')
            or re.match(r'^\d+$', first)):
        return lines

    # 다음 비어있지 않은 줄 찾기 (단, 빈 줄을 넘으면 합치지 않음 — 명확히 분리된 단락)
    second_idx = None
    for j in range(first_idx + 1, len(lines)):
        if lines[j].strip():
            second_idx = j
            break
        else:
            # 빈 줄 만나면 합치지 않음
            return lines
    if second_idx is None:
        return lines

    second = lines[second_idx].rstrip('\n').strip()
    # 다음 줄이 마커류면 합치지 않음
    if (second.startswith('제목') or second.startswith('인용구')
            or second.startswith('링크') or second.startswith('동영상')
            or second.startswith('소제목') or re.match(r'^\d+$', second)
            or second.startswith('<') or '<TEL:' in second.upper()
            or '<tel:' in second):
        return lines

    # 첫 줄이 미완결 — 조사·연결어로 끝나거나 종결어미 없음
    ends_unfinished = bool(re.search(
        r'(을|를|이|가|은|는|의|에|에서|로|으로|와|과|도|만|까지|부터|보다|처럼|같이|마저|조차|는데|던데|하면|면서|니까|므로|어요|라며|싶어|있어|없어|돼서|되어|되니|됬|됐|어서|아서|하고|이고|이며)\s*$',
        first
    )) or not re.search(r'[.!?…"\'\)\]]\s*$', first)

    # 합친 길이가 80자 이내일 때만
    if ends_unfinished and len(first) + len(second) + 1 <= 80:
        merged = first + ' ' + second
        # lines 를 새로 구성: first 줄을 merged로 교체, second 줄은 제거
        new_lines = list(lines)
        new_lines[first_idx] = merged + '\n'
        new_lines[second_idx] = ''  # 빈 줄로 만들어 다음 빈 줄과 합쳐짐
        return new_lines
    return lines


def _sanitize_title(title: str) -> str:
    """제목에서 이모지·마크다운 특수문자·대괄호·해시 등 제거.

    제거 대상:
    - 이모지 (Emoji 유니코드 블록 — 1F300~1FAFF, 2600~27BF, FE0F 등)
    - Markdown 기호: # * _ ` ~ > | (파괴적이지 않은 일반 사용은 살림 — 해시는 제거)
    - 괄호류: [ ] { } < > (소괄호 () 는 카페 키워드용 → 그대로 둠)
    - 백틱·물결·머리글 기호
    """
    if not title:
        return title
    import re as _re
    s = title
    # 1) 이모지 / pictographs / 변형 selector / 영역 표시자 제거
    emoji_re = _re.compile(
        "["
        "\U0001F300-\U0001FAFF"     # 픽토그래프, 스마일, 음식, 물건 등
        "\U0001F600-\U0001F64F"     # 표정
        "\U0001F680-\U0001F6FF"     # 교통/지도
        "\U0001F900-\U0001F9FF"     # 추가 픽토
        "\U00002600-\U000027BF"     # 잡다 심볼·딩벳
        "\U0001F000-\U0001F02F"     # 카드
        "‍️♀♂☀-⭕"
        "]+",
        flags=_re.UNICODE,
    )
    s = emoji_re.sub('', s)
    # 2) 대괄호·중괄호·꺽쇠 제거 (괄호 안 텍스트는 보존, 기호만 제거)
    #    전각 대괄호 【】 / 모서리 괄호 「」『』 / 빠진 꺽쇠 等 포함
    s = _re.sub(r'[\[\]\{\}<>【】「」『』〔〕〈〉《》]', '', s)
    # 3) Markdown 기호 제거 — # ` ~ * _ | (한국어 본문엔 거의 안 쓰임)
    s = _re.sub(r'[#`~*_|]', '', s)
    # 4) 끝에 남은 구두점·콜론·세미콜론·따옴표 정리
    s = s.strip().strip('"\'·•‧︰︓').strip()
    # 5) 연속된 공백 1칸으로 압축
    s = _re.sub(r'\s+', ' ', s).strip()
    return s


def parse_manuscript(file_path: str) -> dict:
    """원고.txt를 줄 단위로 파싱

    Returns:
        {
            'title': '글 제목',
            'sections': [
                {'type': 'text', 'content': '본문 텍스트'},
                {'type': 'quote', 'content': '청소 전'},
                {'type': 'img', 'content': '1.jpg'},
                {'type': 'link', 'content': 'https://...'},
            ]
        }
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # 전처리 — '제목 :' 마커 없고 첫 줄이 미완결(다음 줄로 이어지는) 형태면 두 줄 합치기
    lines = _merge_split_title(lines)

    result = {'title': '', 'sections': []}
    text_buffer = []
    first_line = True

    for line in lines:
        line = line.rstrip('\n')

        # 빈 줄 — 텍스트 버퍼 플러시
        if not line.strip():
            if text_buffer:
                result['sections'].append({
                    'type': 'text',
                    'content': '\n'.join(text_buffer)
                })
                text_buffer = []
            continue

        stripped = line.strip()

        # 제목 — '제목 :' 우선, AI 가 가끔 markdown '# ' 또는 '## '로 출력하는 경우도 처리
        if stripped.startswith('제목 :') or stripped.startswith('제목:'):
            if text_buffer:
                result['sections'].append({'type': 'text', 'content': '\n'.join(text_buffer)})
                text_buffer = []
            result['title'] = _sanitize_title(stripped.split(':', 1)[1].strip())
            first_line = False
            continue
        # markdown 헤더 '# 제목내용' / '## 제목내용' (제목이 아직 없을 때만)
        if not result['title'] and (stripped.startswith('# ') or stripped.startswith('## ')):
            if text_buffer:
                result['sections'].append({'type': 'text', 'content': '\n'.join(text_buffer)})
                text_buffer = []
            # '# ' 또는 '## ' 접두 제거
            md_title = stripped.lstrip('#').strip()
            # 끝의 '#' 도 제거 (atx-style)
            md_title = md_title.rstrip('#').strip()
            if md_title:
                result['title'] = _sanitize_title(md_title)
                first_line = False
                continue

        # 첫 줄이 제목 태그 없으면 첫 줄을 제목으로 (AI가 '제목 :' 누락 시 폴백)
        if first_line and not result['title']:
            # <title> 태그 호환
            if stripped.startswith('<title>'):
                continue
            if stripped.startswith('</title>'):
                continue
            # 첫 줄이 인용구/이미지번호/링크/마커가 아니면 제목으로 사용
            is_marker = (
                stripped.startswith('인용구') or
                stripped.startswith('링크') or
                stripped.startswith('동영상') or
                re.match(r'^\d+$', stripped) or
                stripped.startswith('소제목')
            )
            if not is_marker:
                result['title'] = _sanitize_title(stripped)
                first_line = False
                continue
            first_line = False

        # 인용구
        if stripped.startswith('인용구 :') or stripped.startswith('인용구:'):
            if text_buffer:
                result['sections'].append({'type': 'text', 'content': '\n'.join(text_buffer)})
                text_buffer = []
            quote_text = stripped.split(':', 1)[1].strip()
            result['sections'].append({'type': 'quote', 'content': quote_text})
            continue

        # 링크
        if stripped.startswith('링크 :') or stripped.startswith('링크:'):
            if text_buffer:
                result['sections'].append({'type': 'text', 'content': '\n'.join(text_buffer)})
                text_buffer = []
            link_url = stripped.split(':', 1)[1].strip()
            # "링크 : https://..." → "https://..." 추출
            if link_url.startswith('//'):
                link_url = 'https:' + link_url
            elif not link_url.startswith('http'):
                # "링크 : https://youtu.be/..." 에서 https부터 추출
                url_match = re.search(r'(https?://\S+)', stripped)
                if url_match:
                    link_url = url_match.group(1)
            result['sections'].append({'type': 'link', 'content': link_url})
            continue

        # 동영상 — '동영상 첨부' (기본) 또는 '동영상 : 파일명.mp4' (파일 지정)
        if stripped == '동영상 첨부' or stripped.startswith('동영상 :') or stripped.startswith('동영상:'):
            if text_buffer:
                result['sections'].append({'type': 'text', 'content': '\n'.join(text_buffer)})
                text_buffer = []
            if ':' in stripped:
                vid_name = stripped.split(':', 1)[1].strip()
            else:
                vid_name = ''  # 빈 값 = 기본 동영상 ({keyword}.mp4)
            result['sections'].append({'type': 'video', 'content': vid_name})
            continue

        # 썸네일 마커 — '{키워드}<TEL:...>' 또는 '{키워드}<tel:...>'
        tel_match = re.match(r'^(.+?)<tel:[^>]+>\s*$', stripped, re.IGNORECASE)
        if tel_match:
            if text_buffer:
                result['sections'].append({'type': 'text', 'content': '\n'.join(text_buffer)})
                text_buffer = []
            kw = tel_match.group(1).strip()
            result['sections'].append({'type': 'thumbnail', 'content': kw})
            continue

        # 이미지 (숫자만 있는 줄)
        if re.match(r'^\d+$', stripped):
            if text_buffer:
                result['sections'].append({'type': 'text', 'content': '\n'.join(text_buffer)})
                text_buffer = []
            num = stripped
            # 이미지 파일 확장자 자동 탐색
            result['sections'].append({'type': 'img', 'content': num})
            continue

        # <title>, <text>, <img>, <quote> 등 기존 태그 호환
        if stripped.startswith('<') and stripped.endswith('>'):
            tag = stripped.strip('<>').strip('/')
            if tag in ('title', 'text', 'img', 'quote', 'hidden', 'link'):
                continue

        # 일반 텍스트
        text_buffer.append(line.rstrip('\n'))

    # 남은 텍스트 버퍼 플러시
    if text_buffer:
        result['sections'].append({'type': 'text', 'content': '\n'.join(text_buffer)})

    return result


def apply_risky_words(text: str, risky_word_path: str) -> str:
    """위험 단어를 안전한 대체어로 치환"""
    if not os.path.exists(risky_word_path):
        return text

    with open(risky_word_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()
        if not line or ':' not in line:
            continue

        parts = line.split(':')
        if len(parts) != 2:
            continue

        original = parts[0]
        replacements = [r.strip() for r in parts[1].split('|')]
        replacement = random.choice(replacements)
        text = text.replace(original, replacement)

    return text


def get_image_path(manuscript_dir: str, image_num: str) -> str:
    """이미지 번호로 실제 파일 경로 찾기 (확장자 자동 탐색)
    image/ 하위 폴더 또는 원고와 같은 폴더 둘 다 지원"""
    # 1순위: 원고와 같은 폴더
    for ext in ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp']:
        path = os.path.join(manuscript_dir, f'{image_num}.{ext}')
        if os.path.exists(path):
            return path

    # 2순위: image/ 하위 폴더
    image_dir = os.path.join(manuscript_dir, 'image')
    if os.path.exists(image_dir):
        for ext in ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp']:
            path = os.path.join(image_dir, f'{image_num}.{ext}')
            if os.path.exists(path):
                return path
        # 파일명 그대로
        path = os.path.join(image_dir, image_num)
        if os.path.exists(path):
            return path

    # 3순위: 원고 폴더에서 파일명 그대로
    path = os.path.join(manuscript_dir, image_num)
    if os.path.exists(path):
        return path

    return ''


def list_postings(postings_dir: str) -> list:
    """postings/ 폴더에서 원고 목록 반환.
    원고 파일명: '원고.txt' 또는 '*_원고.txt' 또는 '*원고*.txt' 패턴 모두 허용.
    """
    if not os.path.exists(postings_dir):
        return []

    result = []
    for name in sorted(os.listdir(postings_dir)):
        folder_path = os.path.join(postings_dir, name)
        if not os.path.isdir(folder_path):
            continue
        manuscript_path = _find_manuscript_file(folder_path)
        if manuscript_path:
            result.append({
                'name': name,
                'path': folder_path,
                'manuscript': manuscript_path
            })
    return result


def _find_manuscript_file(folder_path: str) -> str:
    """폴더 내에서 원고 파일 찾기.
    우선순위: 1) 원고.txt  2) {name}_원고.txt  3) *원고*.txt  4) 첫 .txt
    """
    if not os.path.isdir(folder_path):
        return ''
    # 1) 정확한 이름
    p = os.path.join(folder_path, '원고.txt')
    if os.path.isfile(p):
        return p
    # 2) *_원고.txt
    try:
        candidates_pri = []
        candidates_sec = []
        fallback = []
        for f in sorted(os.listdir(folder_path)):
            if not f.lower().endswith('.txt'):
                continue
            full = os.path.join(folder_path, f)
            if not os.path.isfile(full):
                continue
            if f.endswith('_원고.txt'):
                candidates_pri.append(full)
            elif '원고' in f:
                candidates_sec.append(full)
            else:
                fallback.append(full)
        if candidates_pri:
            return candidates_pri[0]
        if candidates_sec:
            return candidates_sec[0]
        if fallback:
            return fallback[0]
    except Exception:
        pass
    return ''
