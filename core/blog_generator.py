"""블로그 원고 생성기 — blog-generator.js Python 포팅

UI와 분리된 순수 Python 로직. Claude API로 블로그 원고를 자동 생성.
"""

import os
import re
import random

# ============================================================
# 1) 업체별 유튜브 링크 풀
# ============================================================
LINKS_MAP = {
    "새집느낌": [
        "https://youtu.be/1B22QcRXD0k?si=9CtROH34njgODg1a",
        "https://youtu.be/Ss5Do90Wr6g?si=QABezrUrQeZmxdd4",
        "https://youtu.be/xDbn9-GPDEQ?si=vO9kOLMadiPuTS2S",
        "https://youtu.be/YoPXFtPGL2g?si=yLs5KCQoHNIC1km6",
        "https://youtu.be/ioTo-2NARA0?si=QDSg9G4I-NKMMHqa",
        "https://youtu.be/Ss5Do90Wr6g?si=3eY4Kx7Mr62Vryyt",
        "https://youtu.be/mPyx50bqYto?si=y5GNXQouOxjoHjcG",
    ],
    "가족사랑클린": [
        "https://youtube.com/shorts/qHWjZOcAAO8?si=kqA5kNWJEgYFGgjO",
    ],
    "새집환경365": [
        "https://youtube.com/shorts/EseQxL7TXdE?si=SxxXDBu8FyWtTBGu",
        "https://youtube.com/shorts/4F3-mvXH2co?si=-FBjIXVvWOrdsNjJ",
        "https://youtube.com/shorts/3t5zWCwa1Oo?si=bTnU5IN_xbAVwR7V",
        "https://youtube.com/shorts/r7ipcUOvaUg?si=VtlT5ziMPN4dfqoW",
    ],
}

BRAND_NAMES = list(LINKS_MAP.keys())

# ============================================================
# 2) 업체별 전화번호
# ============================================================
TEL_MAP = {
    "가족사랑클린": "1833-2436",
}

def get_tel(brand_name: str) -> str:
    return TEL_MAP.get(brand_name, "1660-0240")

# ============================================================
# 3) 참고원고
# ============================================================
REF = """■ 참고원고 (구조만 따를 것 — 문장·표현 사용 금지)

제목 : 영등포입주청소 꼼꼼하게 깔끔하게

영등포입주청소<TEL:1660-0240>
안녕하세요. 저는 최근 결혼 준비와 함께
영등포 지역에서 신혼집을 구하게 되었는데,
이사 전에 반드시 입주청소를 받아야겠다는
생각이 강하게 들었던 경험을 공유하려고 해요.

남편과 함께 처음으로 꾸미는 집이라
모든 과정을 꼼꼼하게 챙기고 싶었고,
그 시작을 청소부터 하는 게 맞다고
판단했어요.

입주청소를 결심한 계기는
친구의 조언이었어요.

링크 : https://youtu.be/ioTo-2NARA0?si=QDSg9G4I-NKMMHqa

친구가 작년에 입주청소 없이 짐을 들였다가
천장 구석에서 곰팡이를 발견하고
한참을 고생했다는 이야기를 듣고,
저는 그런 상황을 미리 예방하고 싶었어요.

계약 후 처음 집 열쇠를 받아
문을 열었을 때, 눈에 보이는 곳은
비교적 괜찮아 보였어요.

인용구 : 청소 전
1
하지만 세면대 아래쪽을 확인해 보니 물 자국이 누렇게 남아 있었고, 화장실 타일 틈새에는 묵은 때가 자리 잡고 있었어요.
창틀을 손으로 쓸어보니 검은 먼지가 손끝에 묻어 나왔고, 주방 후드 필터를 열어보자 기름때가 두껍게 쌓여 있는 상태였어요.
2
싱크대 내부도 열어보니 이전 거주자의 생활 흔적이 곳곳에 남아 있었고, 바닥 모서리에도 끈적한 오염이 묻어 있어서 걱정이 앞섰어요.
이 정도 상태라면 혼자 힘으로 관리하기엔 시간도 부족하고 전문적인 장비가 필요하다는 걸 직감했어요.
3
그래서 바로 영등포입주청소 업체를 검색하기 시작했고, 여러 곳에 문의를 넣었지만 일정이 빠듯한 곳이 많아 쉽지 않았어요.
다행히 새집느낌이라는 곳에서 원하는 날짜에 진행이 가능하다는 안내를 받았고, 상담 과정에서 작업 범위와 비용에 대해 투명하게 설명해 주셔서 신뢰가 갔어요.

인용구 : 청소 후
4
급하게 잡은 일정이었지만 영등포입주청소의 전 과정을 세밀하게 안내받을 수 있었어요.
입주청소 당일 새집느낌 팀이 도착했을 때, 전문 장비와 세정제를 가득 준비해 오신 모습에 기대감이 올라갔어요.
5
하청 없이 자체 인력으로 작업한다는 점도 믿음이 갔고, 시작 전 집 전체를 꼼꼼히 확인하는 모습에서 프로다운 자세가 느껴졌어요.
가장 먼저 진행된 곳은 화장실이었어요.
6
타일 줄눈 사이에 박혀 있던 곰팡이를 전용 약품으로 분리 제거하고, 세면대 주변의 물때도 깔끔하게 닦아내는 과정을 직접 지켜봤는데 눈으로 보면서도 놀라웠어요.
천장 모서리에 숨어 있던 미세 오염까지 하나하나 손이 닿더라고요.
7
이어서 주방 작업으로 넘어갔는데, 싱크대를 분리해서 내부 곳곳을 세척하는 모습이 인상 깊었어요.
후드 필터에 겹겹이 쌓인 기름때를 제거하고 나니 환풍 기능이 살아나는 게 체감될 정도였어요.
8
바닥 청소 역시 단순히 물을 뿌리고 닦는 수준이 아니었어요.
입주청소 작업 중 바닥재 이음부마다 전용 도구로 먼지와 이물질을 빼내는 과정이 세심하게 이루어졌어요.
9
창틀도 물을 활용한 집중 청소를 통해 먼지 한 톨 남지 않는 상태로 마무리되었고, 이 과정에서 공기 흐름이 달라졌다는 걸 실감할 수 있었어요.
영등포입주청소를 맡기면서 기대했던 것 이상의 결과가 하나씩 눈앞에 펼쳐지니 정말 뿌듯했어요.
10
세탁기 내부 청소도 포함되어 있었는데, 겉보기에는 문제없어 보여도 분해해 보니 안쪽에 세균과 찌꺼기가 쌓여 있었어요.
입주청소 이후 세탁기를 다시 조립하고 나니 새 제품 같은 상태로 돌아왔고, 추가 구매 없이도 안심하고 사용할 수 있게 되었어요.
11
모든 영등포입주청소 작업이 마무리된 후 집 안을 천천히 둘러보았는데, 이전과는 비교할 수 없을 만큼 공간이 쾌적하게 변해 있었어요.
바닥을 맨발로 밟아도 쾌적한 느낌이었고, 공기에서 느껴지던 텁텁함도 사라져 있었어요.
12
화장실에서 올라오던 묵은 냄새가 제거된 것은 물론이고, 주방에서도 상쾌한 공기가 순환하는 게 느껴졌어요.
이런 변화를 직접 경험하니 영등포입주청소에 비용을 투자한 것이 후회되지 않았어요.
13
솔직히 입주청소 비용이 처음에는 부담으로 다가왔던 것도 사실이에요.
하지만 처음 안내받은 금액 그대로 추가 없이 진행되었고, 작업 결과를 보면 지불한 비용 대비 충분히 가치 있는 선택이었다고 느꼈어요.
14
입주 전에 한꺼번에 해결하지 않으면 이후에 각 공간을 따로 관리해야 하는 번거로움이 생기기 때문에, 미리 영등포입주청소를 예약하고 한 번에 정리하는 편이 훨씬 효율적이에요.
일정만 확보된다면 입주 날짜보다 여유 있게 잡아두는 것을 권해 드리고 싶어요.
15
이번 경험으로 깨달은 건, 겉으로 멀쩡해 보이는 집이라도 구석구석 열어보면 생활 오염이 상당히 남아 있다는 점이에요.
전문적인 입주청소가 필요한 이유가 바로 여기에 있고, 혼자 해결하려다 시간만 허비하는 것보다 처음부터 전문가에게 의뢰하는 게 현명하다는 생각이 확실히 들었어요.
16
결과적으로 이번 영등포입주청소 덕분에 곰팡이, 세균, 묵은 먼지, 기름때, 찌든 오염을 한꺼번에 제거할 수 있었고, 깨끗한 공간에서 새 출발을 시작하게 되어 정말 기뻤어요.
입주청소 전과 후를 비교해 보면 같은 집이라고 믿기 어려울 정도로 차이가 뚜렷했어요.
17
준비가 늦어졌다고 걱정하지 마시고, 전문 업체를 통한 체계적인 관리를 꼭 받아보시길 추천드려요.
제대로 된 입주청소 한 번이 생활 환경 자체를 바꿔 준다는 걸 몸소 체험했기에 자신 있게 말씀드릴 수 있어요.
18
입주청소 서비스를 처음 알아보시는 분들도 부담 갖지 않으셔도 돼요.
영등포입주청소를 고민 중이신 분들이라면 이 후기가 도움이 되었으면 좋겠어요.

동영상 첨부"""


# ============================================================
# 4) 유틸 함수
# ============================================================

def cns(text: str) -> int:
    """공백 제외 글자수"""
    return len(re.sub(r'[\s\n]', '', text))


def ckw(text: str, keyword: str) -> int:
    """키워드 빈도"""
    if not keyword:
        return 0
    return len(re.findall(re.escape(keyword), text))


def parse_paren_kw(raw: str) -> str:
    """형태소 분석 결과에서 핵심 키워드 추출.

    지원 형식:
    - '[5/5]: 키워드, 키워드, ...'  (콜론 분리식 - 구형)
    - '[5/9] 입주청소'              (한 줄에 한 키워드 - 신형)
    - '[4/7] 청소업체' 등 N/M 모든 분모 지원
    - '■ 등장빈도 높은 핵심어휘' 같은 헤더 줄은 그대로 보존
    """
    if not raw:
        return ""

    # 1) 구형 '[5/5]: 키워드, 키워드' 형식 — 콜론 필수
    if re.search(r'\[\d+/\d+\]\s*:', raw):
        keywords = []
        has_legacy = False
        for line in raw.split("\n"):
            m = re.match(r'^\[(\d+)/(\d+)\]\s*:\s*(.+)', line)
            if m:
                has_legacy = True
                keywords.extend([s.strip() for s in m.group(3).split(",") if s.strip()])
        if has_legacy and keywords:
            return ", ".join(keywords)

    # 2) 신형 '[5/9] 키워드' (한 줄 한 키워드) 또는 자유 텍스트 → 그대로 반환
    return raw


def extract_body(txt: str) -> str:
    """본문 추출 ('제목 :' ~ '동영상 첨부') + 끝에 붙은 분석 섹션 제거"""
    start = txt.find("제목 :")
    end = txt.find("동영상 첨부")
    if start == -1:
        body = txt
    elif end != -1:
        body = txt[start:end + len("동영상 첨부")].strip()
    else:
        body = txt[start:].strip()

    # AI 가 본문 뒤에 붙이는 분석/검토 섹션 제거 (메인 키워드 조합 / 글자수 표기 등)
    lines = body.split('\n')
    cut_at = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s == '---':
            if i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if (nxt.startswith('**[') or nxt.startswith('[')) and \
                   any(k in nxt for k in ('대괄호', '글자수', '키워드 앞뒤', '조합 검토')):
                    cut_at = i
                    break
        if (s.startswith('**[') or s.startswith('[')) and \
           any(k in s for k in ('메인 키워드', '글자수:', '글자수 :', '키워드 앞뒤', '조합 검토')):
            cut_at = i
            break
    if cut_at is not None:
        body = '\n'.join(lines[:cut_at]).rstrip()

    # AI 가 프롬프트 스켈레톤 안내 텍스트를 본문에 그대로 흘리는 경우 제거
    # 예: '(도입 문단 1 — 3~5줄, 넘버링 없음)', '(연결 문단 1)', '(전환 문단 — 1~2줄)'
    body = _strip_skeleton_placeholders(body)

    # 메인 키워드를 []·【】·〈〉로 감싼 케이스 정리 (AI가 라벨처럼 따라 한 경우)
    body = _strip_keyword_brackets(body)

    return body


def _strip_keyword_brackets(txt: str) -> str:
    """본문에 들어간 '[키워드]' / '【키워드】' / '〈키워드〉' 등 괄호 래핑 제거.

    제목·썸네일 마커({키워드}<TEL:>) 줄은 손대지 않고 본문에서만 정리.
    + 본문에 흘러간 <TEL:...> 마커도 함께 제거 (썸네일 마커가 아닌 경우).
    예: '[홍성입주청소]를 알아보면서~' → '홍성입주청소를 알아보면서~'
    예: '...이용할 의향이 있어요. <TEL:1660-0240>' → '...이용할 의향이 있어요.'
    """
    if not txt:
        return txt
    import re as _re
    # 썸네일 마커 식별 — '{키워드}<TEL:...>' (앞에 텍스트 있음, 줄 끝에 마커)
    thumb_marker_re = _re.compile(r'^.+?<tel:[^>]+>\s*$', _re.IGNORECASE)
    out_lines = []
    for line in txt.split('\n'):
        s = line.strip()
        # 제목·기타 마커 라인 보존
        if (s.startswith('제목 :') or s.startswith('제목:')
                or s.startswith('인용구') or s.startswith('링크')
                or s.startswith('소제목') or s.startswith('동영상')):
            out_lines.append(line)
            continue
        # 썸네일 마커 라인('{키워드}<TEL:...>')은 그대로 보존
        if thumb_marker_re.match(s):
            out_lines.append(line)
            continue
        # 본문 — <TEL:...>, <tel:...> 단독/인라인 마커 제거
        new = _re.sub(r'\s*<\s*tel\s*:[^>]+>\s*', '', line, flags=_re.IGNORECASE)
        # 키워드 괄호 정리
        new = _re.sub(r'\[([^\[\]]*[가-힣][^\[\]]*?)\]', r'\1', new)
        new = _re.sub(r'【([^【】]*[가-힣][^【】]*?)】', r'\1', new)
        new = _re.sub(r'〈([^〈〉]*[가-힣][^〈〉]*?)〉', r'\1', new)
        out_lines.append(new)
    result = '\n'.join(out_lines)
    result = _re.sub(r'\n{3,}', '\n\n', result)
    return result


def _strip_skeleton_placeholders(txt: str) -> str:
    r"""AI 가 프롬프트의 안내 텍스트를 그대로 출력한 줄 제거.

    제거 대상 패턴 (한 줄 전체가 매치되거나, 줄 안에 포함된 경우):
    - '(도입 문단 1 — 3~5줄, 넘버링 없음)'
    - '(연결 문단 1)' / '(전환 문단 — 1~2줄)'
    - '(브릿지 문장)' / '(소제목)'
    - '(이사 결정 계기·배경 문단 — 3~5줄)' (포장이사 라벨류)
    - '(작업 시작 문단 — 3~5줄)' 등 청소 라벨
    - 더 일반적으로 '( ... — \d+~?\d*줄 ...)' 형태
    """
    import re as _re
    if not txt:
        return txt

    # 줄 단위 처리
    out_lines = []
    # 단독 줄 패턴 (양옆 공백 제외하고 전체가 (... 문단/문장 ...) 인 경우)
    line_re = _re.compile(
        r'^\s*\([^()]*?(문단|문장|소제목|넘버링|줄)[^()]*?\)\s*$'
    )
    # 줄 일부에 박힌 placeholder 패턴 (라벨 + 줄수 안내)
    inline_re = _re.compile(
        r'\([^()]*?(?:—|-)\s*\d+\s*~?\s*\d*\s*줄[^()]*?\)'
    )
    # 라벨만 있는 단순 placeholder (예: '(전환 문단)', '(연결 문단 1)')
    label_only_re = _re.compile(
        r'\(\s*[가-힣]+\s*(?:문단|문장|소제목)(?:\s*\d+)?\s*\)'
    )

    for line in txt.split('\n'):
        s = line.strip()
        if not s:
            out_lines.append(line)
            continue
        # 1) 줄 전체가 placeholder → 통째로 버림
        if line_re.match(line):
            continue
        # 2) 라벨 only 단독 줄도 버림
        if label_only_re.fullmatch(s):
            continue
        # 3) 줄 안에 inline placeholder 박힌 경우 제거 (라인은 살리되 그 부분만)
        cleaned = inline_re.sub('', line)
        cleaned = label_only_re.sub('', cleaned)
        # 한 줄이 placeholder만 가지고 있던 경우 빈 줄이 됨 → 빈 줄로 유지
        out_lines.append(cleaned)
    result = '\n'.join(out_lines)
    # 연속 빈 줄 2개 초과로 늘어났을 때 2개로 압축
    result = _re.sub(r'\n{3,}', '\n\n', result)
    return result.rstrip() + '\n'


# ============================================================
# 5) 프롬프트 빌더
# ============================================================

def build_prompt(t: dict) -> str:
    """blog-generator.js의 buildPrompt() 1:1 변환"""
    links = LINKS_MAP.get(t.get('brand_name', '새집느낌'), LINKS_MAP['새집느낌'])
    rl = random.choice(links)
    tel = get_tel(t.get('brand_name', '새집느낌'))

    paren_kw = parse_paren_kw(t.get('paren_kw', ''))
    image_count = t.get('image_count', 18)
    numbering = t.get('numbering', True)
    viewpoint = t.get('viewpoint', '고객')

    if viewpoint == '업체':
        viewpoint_desc = (
            "**업체 관점 (필수)** — 시공사가 자기 작업을 직접 소개하는 톤. "
            "1인칭 '저희' 사용 (예: '저희 새집느낌에서 시공한~', '저희가 작업을 진행~'). "
            "고객을 '고객님'으로 지칭. 후기·경험담 톤 절대 금지. 시공 사례 소개·비포애프터 설명 톤. "
            "고객 관점 ('저', '제가') 사용 금지."
        )
        viewpoint_strict = (
            "★ 업체 관점 (Business POV) 만 사용 ★\n"
            "- '저희가 시공한~', '저희 {brand_name}에서 작업한~' 처럼 시공사가 직접 쓰는 톤\n"
            "- 1인칭 = '저희' / 고객 = '고객님' / 업체 = 우리 자신\n"
            "- 비포·애프터 사례 소개 형식. 후기·경험담 톤 ABSOLUTELY 금지\n"
            "- '제가', '저는', '받아봤는데', '이용해 보니' 같은 고객 관점 표현 절대 금지"
        ).format(brand_name=t.get('brand_name', '새집느낌'))
    else:
        viewpoint_desc = (
            "**고객 관점 (필수)** — 서비스를 직접 받아본 이용자의 후기 톤. "
            "1인칭 '저' 사용 (예: '제가 직접 받아봤는데~', '이용해 보니~'). "
            "업체를 업체명으로 3인칭 지칭. 시공사 소개 톤 절대 금지. 솔직한 경험담·추천 톤. "
            "업체 관점 ('저희', '시공한') 사용 금지."
        )
        viewpoint_strict = (
            "★ 고객 관점 (Customer POV) 만 사용 ★\n"
            "- '제가 직접 받아봤는데~', '{brand_name}을 이용해 보니~' 처럼 후기를 쓰는 톤\n"
            "- 1인칭 = '저' / 업체 = '{brand_name}' (3인칭)\n"
            "- 솔직한 경험담·추천 톤. 시공사 소개 형식 ABSOLUTELY 금지\n"
            "- '저희가', '시공한', '저희 {brand_name}에서' 같은 업체 관점 표현 절대 금지"
        ).format(brand_name=t.get('brand_name', '새집느낌'))

    template = t.get('template', '청소')

    # 스켈레톤 생성 — 템플릿별 분기
    skeleton_lines = []
    if template == '포장이사':
        # 포장이사: 작업 전/후 분리 없는 단일 시퀀스
        # 이미지 1 은 유튜브 링크 자리에 배치 (skel_start=2 부터 본문 스켈레톤)
        packing_labels = [
            "이사 결정 계기·배경 문단", "견적 받기 안내 문단", "견적 비교 주의점 문단",
            "업체 선정 기준 문단", "포장 시작 문단", "포장 진행 문단", "포장 진행 문단",
            "운반 준비 문단", "운반 진행 문단", "운반 진행 문단", "정리 시작 문단",
            "정리 진행 문단", "결과 확인 문단", "비용 정산 문단", "느낀 점 문단",
            "팁·당부 문단", "추천 이유 문단", "마무리·인사 문단",
        ]
        # 이미지 2 부터 image_count 까지 — packing_labels[1] 부터 사용 (이미지 1은 인트로 자리에서 사용)
        for i in range(2, image_count + 1):
            if numbering:
                skeleton_lines.append(str(i))
            label = packing_labels[i - 1] if i - 1 < len(packing_labels) else "문단"
            skeleton_lines.append(f"({label} — 3~5줄)")
    else:
        # 청소(기본): 작업 전/후 분리
        pre = min(3, image_count)
        post = image_count - pre
        pre_labels = ["현장 상태 묘사 문단", "현장 상태 묘사 문단", "업체 선택 과정 문단"]
        post_base = ["작업 시작 문단", "작업 문단", "작업 문단", "작업 문단", "작업 문단",
                     "작업 문단", "작업 문단", "결과 문단", "결과 문단", "비용 문단",
                     "효율 문단", "깨달음 문단", "마무리 문단", "추천 문단", "마지막 인사 문단"]
        for i in range(1, pre + 1):
            if numbering:
                skeleton_lines.append(str(i))
            label = pre_labels[i - 1] if i - 1 < len(pre_labels) else "문단"
            skeleton_lines.append(f"({label} — 3~5줄)")
        skeleton_lines.append("")
        skeleton_lines.append("인용구 : 청소 후")
        for i in range(1, post + 1):
            num = pre + i
            if numbering:
                skeleton_lines.append(str(num))
            label = post_base[i - 1] if i - 1 < len(post_base) else "문단"
            skeleton_lines.append(f"({label} — 3~5줄)")

    skeleton = "\n".join(skeleton_lines)

    numbering_rule = ("넘버링은 번호만 표기 (마침표 없이). 각 번호는 반드시 별도 줄에 단독 배치할 것."
                      if numbering else
                      "넘버링 번호를 넣지 말 것. 문단 사이 빈 줄로만 구분할 것.")

    # 템플릿별 가변 부분
    if template == '포장이사':
        keyword_intro = '지역+포장이사'
        pre_quote = ''  # 인용구 안 씀 — 단일 시퀀스
        tel = '1644-0199'  # 포장이사 전용 연락처 (브랜드 무관)
        # 유튜브 링크 자리 → 이미지 1 + 첫 본문 (이사 결정 계기·배경 문단)
        link_line = '1\n(이사 결정 계기·배경 문단 — 3~5줄, 넘버링 없음)\n\n'
        ref_block = (
            "■ 참고 흐름 (구조만 따를 것 — 문장 사용 금지)\n"
            "포장이사 후기는 단일 시퀀스로 진행 (작업 전·후 분리 X).\n"
            "이사 결정 → 견적 → 업체 선정 → 포장 → 운반 → 정리 → 결과 → 비용 → 마무리 흐름.\n"
            "각 단계마다 사진 1장 + 3~5줄 본문.\n"
            "유튜브 링크 사용 금지."
        )
    else:
        keyword_intro = '지역+입주청소'
        pre_quote = '인용구 : 청소 전\n'
        link_line = '링크 : [랜덤 링크]\n\n'
        ref_block = REF

    # 랜덤 변형 시드 — 같은 키워드라도 매번 다른 출력 유도 (말투/톤은 참고원고 따라감)
    variation_seed = random.randint(10000, 99999)

    return f"""====== 입력 정보 ======
[변형 시드: {variation_seed}] ← 이 글은 #{variation_seed} 버전. 도입 구성·예시·세부 표현이 이전 글과 겹치지 않게.
(말투·조사·문장 톤은 아래 참고원고의 스타일을 그대로 따를 것)
[제목] {t.get('title', '')}
[업체명] {t.get('brand_name', '새집느낌')}
[메인 키워드] {t.get('bracket_kw', '')}
[소괄호 키워드] ({paren_kw})
[작성 관점] {viewpoint_desc}
[랜덤 링크] {rl}

⚠ 메인 키워드를 본문에 쓸 때 절대 [] / 【】 / 〈〉 같은 괄호로 감싸지 마라. 그냥 평문으로 사용.
   예시 OK: "홍성입주청소를 알아보면서~"
   예시 NG: "[홍성입주청소]를 알아보면서~" (절대 금지)

( {t.get('manuscript', '')} )

====== 아래는 고정 규칙 ======

위 원고를 기반으로 1차 리라이팅 → 2차 형식 변환까지 한 번에 진행해줘.
최종 결과물만 출력해. 중간 과정 설명 불필요.

■ 1차 리라이팅 규칙

[차별화 규칙]
원고의 문장을 그대로 옮기거나 어순만 바꿔 쓰는 것을 금지해. 모든 문장을 새로 만들어줘.
도입부 상황 설정을 원고와 다르게 구성해줘 (예: 동기, 계기, 배경을 새롭게 창작)
본문에서 공간별 서술 순서를 원고와 다르게 배치해줘
같은 의미를 전달하더라도 주어, 목적어, 수식어, 비유 표현을 원고와 겹치지 않게 작성해줘
단어를 풍부하게 작성해줘
네이버 블로그 SEO에 맞춰진 글로 재구성해줘
다시 강조하지만 말투는 무조건 위 원고대로 해야 돼

[문장 유사도 규칙 — 반드시 준수]
원고의 어떤 문장이든 연속 5어절 이상 동일하게 가져오는 것을 금지해.
메인 키워드 조합 부분(예: "입주청소를", "입주청소의")은 어절 카운트에서 제외하고, 그 외 일반 서술 부분에서 연속 5어절이 겹치면 위반이야.
글 작성 후, 원고와 새 글의 문장을 대조해서 연속 5어절 이상 겹치는 부분이 있으면 자동으로 수정해줘.

1. [메인 키워드]는 원고에서 등장한 횟수와 동일하게 반복해줘.
단, 메인 키워드의 앞뒤 조합 다양성 규칙:
"앞뒤 조합"이란 키워드 앞 1~2어절 + 키워드 + 뒤 조사·1어절까지를 하나의 묶음으로 본 것이야.
같은 앞뒤 조합이 전체 글에서 2회를 초과하면 안 돼. 나머지는 모두 서로 다른 조합으로 배치해줘.
예시) 아래는 모두 "다른 조합"이야:
· "입주청소의 서비스"
· "입주청소를 맡긴"
· "입주청소가 선택이"
· "입주청소 이후에는"
· "입주청소를 진행한"
예시) 아래는 "같은 조합"으로 간주해:
· "입주청소를 선택한" ↔ "입주청소를 선택이" (뒤 핵심어 '선택'이 동일)
· "입주청소의 서비스" ↔ "입주청소의 서비스를" (동일 구조)
글 작성 후, 메인 키워드의 앞뒤 조합 목록을 정리해서 보여줘. 같은 조합이 3회 이상 반복되면 수정해줘.

2. (소괄호 키워드)는 본문에 자연스럽게 녹여내줘

3. [글자수 규칙 — 반드시 준수]
공백 제외 글자수 기준 2300자를 목표로 작성해줘.
먼저 넉넉하게 초안을 작성한 뒤, 공백·줄바꿈을 제외한 순수 글자수를 세어봐.
측정 결과가 1900~2400자 사이면 그대로 출력하고, 범위를 벗어나면 해당 분량만큼 조정해줘.
최종 글자수(공백 제외)를 글 맨 마지막에 [글자수: ○○○○자] 형식으로 표기해줘.
글자수가 1900자 미만이면 절대 출력하지 말고 반드시 내용을 보강해서 범위 안에 맞출 것.

4. 본문 키워드 빈도수는 최대한 풍성하게 작성해줘
5. 문장은 1문장 or 2문장 적절히 섞어주고 2문장 다음에는 무조건 엔터를 쳐서 한칸씩 띄어줘
6. 빈도수 2회 미만 중복어는 최대한 줄여줘
7. 금칙어(알레르기,질냄새,호흡기질환,걸레,걸 레,고 환,할부,특별,완전,최신,특히,너무,에 미,걸레,새까,시다) 제공키워드에 포함되어있어도 사용금지
8. 업체 이름이 나온다면 위에서 지정한 [업체명]으로 바꿔줘
9. "체험", "리뷰" 단어는 반드시 "후기"로 바꿔줘 (제목·본문 모두 해당). 체험기, 체험담, 리뷰, 리뷰기 등 파생어도 모두 "후기"로 통일할 것.
10. [작성 관점]을 반드시 따를 것 (위 [작성 관점] 라인에 명시된 한 가지만 사용 — 다른 관점 절대 금지).
{viewpoint_strict}

■ 2차 형식 변환 규칙 — 아래 구조를 100% 그대로 따를 것. 순서 변경, 누락, 추가 절대 금지.

[제목 변환 규칙 — 반드시 준수]
입력된 [제목]에서 [메인 키워드] 부분만 그대로 유지하고, 나머지 모든 단어는 뜻이 같은 유의어로 반드시 변경해줘.
원본 제목의 키워드 외 단어를 그대로 쓰면 실패로 간주함.

예시1) 원본: "분당입주청소 신중하게 고른 업체 후기" / 키워드: 분당입주청소
→ "분당입주청소 꼼꼼히 선택한 전문 후기" (신중하게→꼼꼼히, 고른→선택한, 업체→전문, 후기는 유지)

※ 주의: "체험", "리뷰" 같은 단어가 제목에 있으면 반드시 "후기"로 변경할 것. 반대로 원본에 "후기"가 있으면 그대로 유지.

예시2) 원본: "영등포입주청소 꼼꼼하게 깔끔하게" / 키워드: 영등포입주청소
→ "영등포입주청소 세심하게 산뜻하게" (꼼꼼하게→세심하게, 깔끔하게→산뜻하게)

[필수 구조 스켈레톤] — 반드시 이 순서대로 출력할 것. 총 이미지(문단) 수: {image_count}개

⚠ 첫 줄은 반드시 "제목 : " 으로 시작 (Markdown 헤더 '#' / '##' 절대 금지)
⚠ 제목에 이모지·이모티콘·대괄호([]·{{}}·<>)·해시(#)·별표(*)·백틱(`) 등 특수문자 절대 금지
⚠ 아래 스켈레톤의 괄호 안 안내 텍스트(예: '(도입 문단 1 — 3~5줄, 넘버링 없음)', '(전환 문단)', '(연결 문단 1)' 등)는 절대 출력에 포함하지 말 것. 안내 텍스트 자리에는 반드시 실제 한국어 문장을 채워서 작성

제목 : (위 제목 변환 규칙에 따라 변환된 제목)

{keyword_intro}<TEL:{tel}>
(도입 문단 1 — 3~5줄, 넘버링 없음)

(도입 문단 2 — 3~5줄, 넘버링 없음)

(브릿지 문장 — 1~2줄, 넘버링 없음)

{link_line}(연결 문단 1 — 3~5줄, 넘버링 없음)

(전환 문단 — 1~2줄, 넘버링 없음)

{pre_quote}{skeleton}

동영상 첨부

[추가 형식 규칙]
- 참고원고의 문장·단어·표현은 절대 가져오지 말 것. 구조(뼈대)만 따를 것.
- {numbering_rule}
- 1차 리라이팅 결과의 단어는 절대 변경 금지
- "제목 :" 부터 "동영상 첨부" 까지 위 스켈레톤 순서를 반드시 지킬 것. 이 구조를 따르지 않으면 실패로 간주함.

[줄바꿈·문단 규칙 — 반드시 준수]
이 규칙을 지키지 않으면 실패로 간주함.

1) 한 줄에 15~25자(공백 포함) 내외로 끊을 것. 한 줄이 25자를 넘기면 안 됨.
2) 한 문단은 3~5줄로 구성할 것. 6줄 이상 이어지면 반드시 빈 줄로 나눌 것.
3) 2문장이 연속되면 그 뒤에 반드시 빈 줄(엔터)을 넣어 문단을 나눌 것.
4) 줄바꿈은 의미 단위로 자연스럽게 끊을 것. 조사나 어미 중간에서 끊지 말 것.

[올바른 예시]
이번 경험으로 깨달은 건,
겉으로 멀쩡해 보이는 집이라도
구석구석 열어보면 생활 오염이
상당히 남아 있다는 점이에요.

전문적인 입주청소가 필요한 이유가
바로 여기에 있고, 혼자 해결하려다
시간만 허비하는 것보다
처음부터 전문가에게 의뢰하는 게
현명하다는 생각이 확실히 들었어요.

[잘못된 예시 — 이렇게 쓰면 안 됨]
이번 경험으로 깨달은 건, 겉으로 멀쩡해 보이는 집이라도 구석구석 열어보면 생활 오염이 상당히 남아 있다는 점이에요. 전문적인 입주청소가 필요한 이유가 바로 여기에 있고, 혼자 해결하려다 시간만 허비하는 것보다 처음부터 전문가에게 의뢰하는 게 현명하다는 생각이 확실히 들었어요.

{ref_block}"""


# ============================================================
# 6) Claude API 호출
# ============================================================

def generate_blog_post(input_data: dict, api_key: str,
                       model: str = 'claude-sonnet-4-5-20250929',
                       callback=None) -> dict:
    """블로그 원고 생성

    Args:
        input_data: {title, brand_name, bracket_kw, paren_kw, viewpoint,
                     numbering, image_count, manuscript}
        api_key: Anthropic API 키
        model: Claude 모델명
        callback: 진행 상황 콜백

    Returns:
        {full_text, body, char_count, keyword_count, prompt}
    """
    t = {
        'brand_name': '새집느낌',
        'viewpoint': '고객',
        'numbering': True,
        'image_count': 18,
        'paren_kw': '',
        **input_data,
    }

    if not t.get('title') or not t.get('bracket_kw') or not t.get('manuscript'):
        raise ValueError("필수 입력 누락: title, bracket_kw, manuscript")
    if not api_key:
        raise ValueError("Claude API 키가 필요합니다")

    prompt = build_prompt(t)

    if callback:
        callback(f"[I] Claude API 호출 중... (모델: {model})")

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    # 시스템 프롬프트 — 참고원고 말투/조사 유지 강조 (페르소나 강제 안 함 — 그래야 1위 글 톤 따라감)
    _sys_seed = random.randint(100000, 999999)
    system_prompt = (
        f"[변형 시드: #{_sys_seed}]\n"
        f"당신은 참고원고의 말투·조사·문장 구조·어휘 사용 패턴을 그대로 따르면서 "
        f"내용만 새로 재구성하는 리라이팅 전문 작가입니다.\n"
        f"참고원고의 톤(존댓말 정도·어미·접속어 등)은 절대 바꾸지 마세요. "
        f"동일 키워드라도 도입 배경·구체 예시는 매번 다르게 작성하되 말투는 동일하게 유지합니다."
    )

    response = client.messages.create(
        model=model,
        max_tokens=8000,
        temperature=1.0,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
    )

    full_text = "".join(
        block.text for block in response.content
        if hasattr(block, 'text')
    )

    body = extract_body(full_text)

    result = {
        'full_text': full_text,
        'body': body,
        'char_count': cns(body),
        'keyword_count': ckw(body, t.get('bracket_kw', '')),
        'prompt': prompt,
    }

    if callback:
        callback(f"[I] 생성 완료 — 글자수: {result['char_count']}자, "
                 f"키워드 빈도: {result['keyword_count']}회")

    return result


# ============================================================
# 7) 저장
# ============================================================

POSTINGS_BLOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'postings_blog')


def save_blog_post(keyword: str, body: str, callback=None, save_dir: str = '') -> str:
    """블로그 원고 저장. 키워드_원고.txt 형태로 저장."""
    base_dir = save_dir if save_dir and os.path.isdir(save_dir) else POSTINGS_BLOG_DIR
    os.makedirs(base_dir, exist_ok=True)

    safe_name = re.sub(r'[^\w가-힣]', '_', keyword).strip('_')
    file_path = os.path.join(base_dir, f'{safe_name}_원고.txt')

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(body)

    if callback:
        callback(f"[I] 원고 저장: {file_path}")
    return file_path
