"""SEO 글 생성 엔진 — seo-app(React/Node) 생성 로직의 파이썬 이식.

원본: 별도 'newhomeclean-seo-generator' 웹앱(server.js + src/App.jsx).
6개 섹션(도입/목차/핵심기준/체크리스트/후기/마무리)을 순차 생성해 한 편으로 합친다.
본문에는 [이미지: …] / [배너: …] / [영상: …] / [인포그래픽: …] 미디어 마커가 들어간다.
(이미지 실제 삽입은 cafe_editor 가 '지정 폴더 순서매칭'으로 처리)

시스템 프롬프트는 6개 호출 내내 동일 → Anthropic 프롬프트 캐싱 적용(입력비 절감).
"""

import re


# ─────────────────────────────────────────────────────────────
# 스타일 / 공통 규칙 / 섹션 정의 (원본 App.jsx 이식)
# ─────────────────────────────────────────────────────────────

STYLES = {
    'hybrid': {
        'label': '하이브리드 (추천)',
        'mod': ('글 전체 흐름: ① 도입부는 1인칭 경험담으로 공감을 만들고 ② 본문은 객관적 기준·표·체크리스트로 '
                '신뢰를 주며 ③ 후반부는 다시 실제 후기·경험으로 마무리한다. 감성과 논리를 번갈아 사용한다.'),
    },
    'guide': {
        'label': '가이드형',
        'mod': ('전문가가 독자에게 \'고르는 기준\'을 알려주는 객관적·구조적 톤. 표와 체크리스트를 적극 활용하고 '
                '권위(경력·시공 건수)를 강조한다.'),
    },
    'review': {
        'label': '후기형',
        'mod': ('글쓴이가 직접 서비스를 이용한 1인칭 경험담 톤. 감정의 흐름(걱정→안심)을 담고, 현장 묘사를 구체적으로 '
                '하며 키워드를 문장 속에 자연스럽게 더 촘촘히 녹인다.'),
    },
}

RULES = """[작성 규칙]
1. 한국어. 네이버 카페 글 톤. 순수 텍스트로만 작성한다(네이버 카페 에디터는 마크다운을 지원하지 않음).
2. 가독성(가장 중요)
   - 한 문장은 짧게. 가능하면 40자 이내, 길어도 60자를 넘기지 않는다. 길면 두 문장으로 쪼갠다.
   - 한 문장이 끝나면 줄바꿈한다. 한 줄에 한 문장이 기본.
   - 2~3문장마다 빈 줄을 넣어 호흡을 준다. 모바일에서 읽기 좋게.
   - "~하고, ~하며, ~인데, ~지만" 으로 문장을 계속 잇지 말 것. 접속만 나오면 끊어서 새 문장으로.
   - 한 문장에 쉼표는 최대 1개. 쉼표로 길게 늘이지 않는다.
   - 큰따옴표로 묶인 대사는 줄 중간에서 끊지 말고 한 줄에 온전히 쓴다.
   - 쉼표 뒤에는 반드시 공백을 한 칸 둔다.
3. 마크다운 기호를 절대 쓰지 않는다. 금지: 별표 볼드, 헤더(#), 표 파이프(|), 인용(>), 리스트 기호(-, *), 백틱. 강조가 필요하면 그냥 평문으로 쓰거나, 소제목은 한 줄로 적고 다음 줄에 본문을 쓴다.
4. 소제목은 마크다운 헤더 대신 평범한 한 줄 문장으로 쓰고, 앞뒤로 빈 줄을 둔다. 따옴표로 감싸 강조해도 좋다.
5. 표가 필요하면 마크다운 표 대신 '항목: 값' 형태의 텍스트 줄로 나열한다. 한 줄에 한 항목씩.
6. 체크리스트 항목은 각 줄 맨 앞에 "▶ " 를 붙여 쓴다(이모지 아님, 텍스트 기호).
7. 핵심 키워드와 롱테일 키워드를 문장 속에 자연스럽게 반복해 녹인다. 어색한 키워드 나열은 금지. 대표 키워드는 붙여쓰기·띄어쓰기 형태를 모두 자연스럽게 노출한다.
8. 구체적인 숫자를 활용해 신뢰를 준다.
9. 차별점은 자화자찬 대신 "이런 기준으로 골라야 한다 → 참고로 저희(업체명)는 ~합니다" 식의 간접 노출로 처리한다.
10. 과장·허위·단정적 법적 표현은 피하고 사실 기반으로 쓴다.
11. 미디어 배치 표시: 각 섹션 지침에 지정된 자리에만 [이미지: …], [영상: …], [인포그래픽: …], [배너: …] 형태의 표시를 한 줄로 넣는다. 지정되지 않은 곳에 임의로 추가하지 않는다.
12. 이모지·이모티콘을 본문 어디에도 절대 사용하지 않는다.
13. 본문에는 전화번호·휴대폰번호·URL·링크·카카오톡 ID·이메일 등 어떤 연락처나 링크도 절대 넣지 않는다. CTA가 필요하면 "견적을 받아보세요 / 상담받아 보세요 / 비교해 보세요"처럼 연락처 없는 행동 유도 문구로만 쓴다.
14. 출력은 본문 텍스트만. 머리말/설명/코드펜스 없이 바로 본문만 출력한다."""


def _block_hook(i):
    return (
        f"[이번 섹션] 메타 설명, 제목, 도입부를 작성한다.\n"
        f"- 맨 위에 \"메타 설명: \" 으로 시작하는 한 줄(100자 이내, 핵심 키워드 \"{i['keyword']}\" 포함)을 먼저 쓴다.\n"
        f"- 제목: 핵심 키워드 \"{i['keyword']}\"를 포함하고, \"{i['region']}{i['industry']} 잘하는 곳/만족한 곳\" 류의 클릭 유도형으로.\n"
        f"- 도입부: 권위 지표(\"{i['authority']}\")를 활용한 후킹 한 줄로 시작한 뒤, 독자의 고민/걱정에 공감하는 1인칭 도입으로 이어간다.\n"
        f"- 인용구 형태의 헤드라인(제목 재강조)도 한 줄 넣는다.\n"
        f"- [미디어 배치] 제목 바로 아래에 [이미지: 대표 {i['region']} {i['industry']} 대표 썸네일/현장 전경], "
        f"도입부 끝에 [영상: {i['industry']} 작업 과정 30~60초 숏폼 또는 업체 소개] 표시를 각각 한 줄로 넣는다."
    )


def _block_toc(i):
    return (
        "[이번 섹션] 본문 구성을 안내하는 '목차'를 작성한다. 3~4개의 소제목으로 압축하고, \"1. / 2. / 3.\" 번호를 붙인다. "
        "각 항목은 '비교 전 핵심 기준', '필수 체크리스트', '실제 이용 후기/경험' 흐름이 자연스럽게 드러나도록 한다. "
        "(이 섹션에는 미디어 표시를 넣지 않는다.)"
    )


def _block_criteria(i):
    prices = i.get('prices') or '(가격 정보 미입력 — 업종 일반 시세로 예시 작성)'
    return (
        f"[이번 섹션] \"비교 전 꼭 알아야 할 핵심 기준\" 섹션을 작성한다.\n"
        f"- 소제목 한 줄로 시작(마크다운 헤더 금지, 평문이나 따옴표 강조).\n"
        f"- 가격/비용 기준을 설명하고, 아래 가격 정보를 '항목: 값' 텍스트 줄로 정리한다(한 줄에 한 항목, 마크다운 표 금지). "
        f"정보가 없으면 업종에 맞게 현실적인 예시로 만든다:\n{prices}\n"
        f"- 비용 차이가 나는 진짜 원인을 2~3가지 짚는다(공포→해결 구조 1회 포함).\n"
        f"- [미디어 배치] 가격 안내 바로 아래에 [인포그래픽: {i['region']} {i['industry']} 가격/비용 비교 그래픽] 표시를 한 줄 넣는다."
    )


def _block_checklist(i):
    diff = i.get('diff') or '(미입력)'
    return (
        f"[이번 섹션] \"꼭 확인할 필수 체크리스트\" 섹션을 작성한다.\n"
        f"- 각 줄 맨 앞에 \"▶ \"를 붙여 3가지 체크 항목을 만든다(이모지 금지).\n"
        f"- 각 항목은 '문제 상황 → 확인해야 할 기준' 구조.\n"
        f"- 이 기준들이 자연스럽게 업체(\"{i['company']}\")의 차별점과 연결되게 한다. 차별점: {diff}.\n"
        f"- 가능하면 항목 중 하나는 '주의할 점'을 '항목: 값' 텍스트 줄로 보강한다(마크다운 표 금지).\n"
        f"- [미디어 배치] 체크리스트 끝에 [이미지: 체크 기준을 보여주는 현장 사진 — {i['industry']} 작업 디테일], "
        f"이어서 [배너: 강점 3-up(직영·추가금없음·100% A/S)] 표시를 각각 한 줄 넣는다."
    )


def _block_story(i):
    steps = i.get('steps') or '(미입력 — 업종 일반 과정으로)'
    reviews = i.get('reviews') or '(미입력)'
    extra = ''
    if '청소' in i.get('industry', ''):
        extra = ' 추가로 효과 증명을 위해 [이미지: 비포/애프터 - 작업 전후 비교 사진] 표시도 한 줄 넣는다.'
    return (
        f"[이번 섹션] \"실제 이용 후기/경험\" 섹션을 작성한다.\n"
        f"- 1인칭 시점으로 서비스 진행 과정을 시간 순서대로 생생하게 묘사한다. 진행 단계: {steps}.\n"
        f"- 아래 후기/신뢰 요소를 자연스럽게 녹인다: {reviews}.\n"
        f"- 이 섹션에서 롱테일 키워드를 특히 촘촘하게 문장 속에 녹인다: {i['longtail']}.\n"
        f"- [미디어 배치] 각 진행 단계 묘사 끝마다 [이미지: 해당 단계 작업 사진 — (단계명)] 표시를 넣는다(단계 수만큼). "
        f"그리고 후기 부분에 [이미지: 실제후기 - 고객 후기 캡처(개인정보 가림)] 표시를 한 줄 넣는다.{extra}"
    )


def _block_closing(i):
    cta = i.get('cta') or '무료 견적/상담 받아보세요'
    return (
        f"[이번 섹션] 마무리 섹션을 작성한다. 짧고 간결하게(8~12문장 이내).\n"
        f"- 앞 후기 섹션의 내용을 다시 길게 반복하지 않는다. 핵심만 압축해 정리한다.\n"
        f"- 보장/사후관리/추가비용 등 '위험 회피' 메시지로 구매 불안을 1~2문장으로 덜어준다(업체 차별점 활용).\n"
        f"- 핵심 키워드 \"{i['keyword']}\"를 한 번 더 자연스럽게 언급.\n"
        f"- 다음 CTA 문구로 행동을 유도하며 마무리: \"{cta}\".\n"
        f"- [미디어 배치] 마무리 CTA 문구 바로 위에 [배너: 무료 견적/상담 유도 CTA 배너], "
        f"글 맨 끝에 [배너: 전체 서비스 8종 안내 배너] 표시를 각각 한 줄 넣는다."
    )


BLOCKS = [
    {'id': 'hook',      'label': '① 제목 + 도입부(후킹)', 'task': _block_hook},
    {'id': 'toc',       'label': '② 목차',                'task': _block_toc},
    {'id': 'criteria',  'label': '③ 핵심 기준 + 가격표',  'task': _block_criteria},
    {'id': 'checklist', 'label': '④ 필수 체크리스트',      'task': _block_checklist},
    {'id': 'story',     'label': '⑤ 실제 후기 / 경험담',   'task': _block_story},
    {'id': 'closing',   'label': '⑥ 위험회피 + 마무리 CTA', 'task': _block_closing},
]


# 새집느낌 공통 브랜드 정보 (고정)
BRAND = {
    'company': '새집느낌',
    'region': '천안',
    'authority': '본사 직영팀 · 5년 경력 · 4,000세대 시공 · 100% A/S 보장',
}

CATEGORIES = {
    'clean': {
        'label': '청소 계열',
        'focus': ('이 분야(청소·시공)에서 독자가 가장 궁금해하는 것: 평당 단가의 적정선과 미끼 견적 구별, 투입 인원 수와 작업 시간, '
                  '사용 약품·장비 수준, 작업 후 재청소(A/S) 보장 여부, 비포/애프터로 보는 실제 효과. 이 요소들을 중심으로 다룬다.'),
        'preset': {
            'industry': '입주청소',
            'keyword': '입주청소',
            'authority': '본사 직영팀 시공 · 청소 경력 5년 · 누적 4,000세대 시공 · 100% A/S 보장',
            'diff': '본사 직영팀 시공(중개·외주 없음)\n작업 후 미흡 시 무료 재청소(100% A/S)\n작업 완료 후 현장 동선 점검 절차',
            'steps': '사전 실측·상담\n보양 후 위에서 아래로 전체 청소\n줄눈·창틀·실리콘 등 디테일\n살균·마무리 검수, 현장 점검',
            'prices': '구분 | 평당 단가 | 30평 기준\n신축 입주청소 | 1.2~1.5만 원 | 36~45만 원\n이사 입주청소 | 1.0~1.3만 원 | 30~39만 원',
            'reviews': '구석까지 꼼꼼하게\n약속 시간 정확\n재청소 요청에도 친절',
            'cta': '천안 입주청소, 무료 견적 받아보세요',
        },
    },
    'move': {
        'label': '포장이사',
        'focus': ('이 분야(포장이사)에서 독자가 가장 궁금해하는 것: 평수별 트럭 톤수와 부피(CBM)의 적정 산정, 가구·가전 파손과 운송 배상보험, '
                  '손 없는 날 일정과 예약, 계약 후 추가금 발생 여부, 직영 인력 대 용달·외주의 차이. 이 요소들을 중심으로 다룬다.'),
        'preset': {
            'industry': '포장이사',
            'keyword': '포장이사',
            'authority': '국토교통부 정식 허가 화물운송 주선업체 · 이사화물보험 가입(총 보상한도 3억원) · 청소 5년 노하우',
            'diff': ('국토교통부 정식 허가 업체(화물운송 주선면허 보유)\n이사화물보험 가입으로 파손·분실 보상(총 보상한도 3억원)\n'
                     '사전 견적 외 식대·기름값 등 추가 청구 0원\n지역 우수 가맹점 2곳 무료 비교견적'),
            'steps': '사전 방문 실측·물량 산정\n전용 자재로 가구·가전 포장\n탑차 적재·안전 운반\n새 집 동선 맞춤 배치·정리',
            'prices': '구분 | 차량 | 30평 기준\n가정 이사 | 5톤 | 80~120만 원\n원룸 이사 | 1톤 | 30~50만 원',
            'reviews': '가구 흠집 없이 안전\n시간 약속 정확\n배치까지 깔끔',
            'cta': '천안 포장이사, 무료 견적 받아보세요',
        },
    },
    'internet': {
        'label': '인터넷가입',
        'focus': ('이 분야(인터넷가입)에서 독자가 가장 궁금해하는 것: 통신 3사 요금제와 속도 비교, 현금·상품권 등 사은품 규모와 지급 조건, '
                  '약정 기간과 위약금, 인터넷+TV+모바일 결합 할인, 설치 일정. 평당 단가가 아니라 \'요금제·사은품·약정\' 비교표로 풀어야 한다.'),
        'preset': {
            'industry': '인터넷가입',
            'keyword': '인터넷',
            'authority': '통신 3사(KT·LGU+·SK) 전체 비교 후 최저가 설계 · 현금 지원금 법정 한도 100% 지급 · 365일 연중무휴 상담',
            'diff': ('통신 3사 전체를 비교해 최저가로 설계\n현금 지원금을 법정 한도 100%로 지급\n'
                     '인터넷 가입 시 청소 지원금까지 제공(새집느낌 전용)\n개통 당일 30분 내 지원금 송금'),
            'steps': '사용 환경·속도 상담\n3사 요금제·사은품 비교 제시\n최적 결합 상품 설계\n설치 일정 예약·완료 확인',
            'prices': ('구분 | 속도 | 월 요금(결합가)\nKT 슬림 | 100Mbps | 23,100원(결합 19,800원)\n'
                       'KT 베이직 | 500Mbps | 28,600원(결합 23,100원)\nKT 에센스 | 1Gbps | 33,000원(결합 27,500원)'),
            'reviews': '현금 지원금 법정 한도 지급\n개통 당일 빠른 송금\n결합 할인 안내 친절',
            'cta': '천안 인터넷가입, 무료 상담받아 보세요',
        },
    },
}

KW_MODS = {
    'clean': ['업체', '비용', '가격', '추천', '후기', '견적'],
    'move': ['업체', '비용', '추천', '후기', '견적'],
    'internet': ['설치', '요금', '사은품', '추천', '후기', '비교'],
}


def _kw_extra(cat_key, r, s):
    if cat_key == 'clean':
        return [f'{r} 아파트 {s}', f'{s} 업체 추천']
    if cat_key == 'move':
        return [f'{r} 이사업체 추천', f'30평 {s}', f'{s} 비용 아끼는 법']
    if cat_key == 'internet':
        return ['인터넷 결합 할인', '통신사 인터넷 비교']
    return []


def gen_keywords(region, stem, cat_key):
    """지역 + 핵심키워드(스템)로 롱테일 자동 조합."""
    r = (region or '').strip()
    s = (stem or '').strip()
    if not s:
        return ''
    head = f'{r}{s}'
    mods = [f'{r} {s} {m}' for m in KW_MODS.get(cat_key, KW_MODS['clean'])]
    extra = _kw_extra(cat_key, r, s)
    return ', '.join([x for x in ([head] + mods + extra) if x])


def gen_cta(region, industry, cat_key):
    r = (region or '').strip()
    svc = (industry or '').strip()
    action = '무료 상담받아 보세요' if cat_key == 'internet' else '무료 견적 받아보세요'
    return f'{r} {svc}, {action}'


def make_defaults(cat_key):
    """카테고리 프리셋 + 브랜드 기본값 + 파생필드(롱테일·CTA)."""
    base = {
        'region': BRAND['region'],
        'company': BRAND['company'],
        'authority': BRAND['authority'],
    }
    base.update(CATEGORIES[cat_key]['preset'])
    base['longtail'] = gen_keywords(base['region'], base['keyword'], cat_key)
    base['cta'] = gen_cta(base['region'], base['industry'], cat_key)
    return base


# ─────────────────────────────────────────────────────────────
# 프롬프트 빌더
# ─────────────────────────────────────────────────────────────

def build_system(inputs, style_key, cat_key):
    head = f"{inputs['region']}{inputs['keyword']}"
    head_spaced = f"{inputs['region']} {inputs['keyword']}"
    ctx = (
        f"[기본 정보]\n"
        f"- 업체명: {inputs['company']} (충남 천안 기반, 청소·이사·인터넷 통합 서비스)\n"
        f"- 업종: {inputs['industry']}\n"
        f"- 지역: {inputs['region']}\n"
        f"- 대표 키워드(붙여쓰기): {head}  ← 제목·첫 문단·소제목에 이 형태로 노출\n"
        f"- 띄어쓰기 변형: {head_spaced}  ← 본문에 자연스럽게 함께 사용(네이버 검색 대응)\n"
        f"- 함께 녹일 롱테일 키워드: {inputs['longtail']}\n"
        f"- 권위/신뢰 지표: {inputs['authority']}\n"
        f"- ※ 본문에 전화번호·링크·연락처는 절대 포함하지 않는다(연락처는 별도 배너로 처리)."
    )
    focus = f"[이 카테고리의 핵심 포인트]\n{CATEGORIES[cat_key]['focus']}"
    return (
        f"너는 한국어 SEO 블로그 글을 쓰는 전문 카피라이터다.\n{ctx}\n\n{focus}\n\n"
        f"[글의 스타일 지침]\n{STYLES[style_key]['mod']}\n\n{RULES}"
    )


def build_task(block, inputs, prev_tail=''):
    head = f"{inputs['region']}{inputs['keyword']}"
    merged = dict(inputs)
    merged['keyword'] = head  # task 내부에선 대표 키워드(붙여쓰기)를 사용
    prev = ''
    if prev_tail:
        prev = f"\n\n[앞부분 맥락 — 이어서 자연스럽게, 중복 없이]\n...{prev_tail}"
    return block['task'](merged) + prev


# ─────────────────────────────────────────────────────────────
# 후처리 파이프라인 (App.jsx cleanSection 이식)
# ─────────────────────────────────────────────────────────────

def strip_contacts(text):
    if not text:
        return text
    text = re.sub(r'\b[\w.+-]+@[\w.-]+\.\w+\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'https?://\S+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bwww\.\S+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\b[\w.-]+\.(?:com|net|co\.kr|kr|io|org)\b\S*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\b\d{2,4}[-\s.]\d{3,4}[-\s.]\d{4}\b', '', text)
    text = re.sub(r'\b\d{4}[-\s.]\d{4}\b', '', text)
    text = re.sub(r'\b1[568]\d{2}[-\s.]?\d{4}\b', '', text)
    text = re.sub(r'\(\s*\)', '', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def strip_emoji(text):
    if not text:
        return text
    emoji_re = re.compile(
        '[\U0001F000-\U0001FAFF\U0001F300-\U0001F9FF\U00002600-\U000027BF'
        '\U00002190-\U000021FF\U00002B00-\U00002BFF\U0000FE0F\U0000200D]',
        flags=re.UNICODE,
    )
    text = emoji_re.sub('', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text


def strip_markdown(text):
    """네이버 카페용: 마크다운 기호 제거. 표는 '항목 - 값' 텍스트 줄로 변환."""
    if not text:
        return text
    out = []
    for line in text.split('\n'):
        t = line.strip()
        # 마크다운 표 행
        if re.match(r'^\|.*\|', t):
            if re.match(r'^\|[\s:|-]+\|?$', t):  # 구분선
                continue
            cells = [c.strip() for c in re.sub(r'^\||\|$', '', t).split('|') if c.strip()]
            if cells:
                out.append(' - '.join(cells))
            continue
        line = re.sub(r'^\s{0,3}#{1,6}\s+', '', line)        # 헤더
        line = re.sub(r'^\s{0,3}>\s?', '', line)              # 인용
        line = re.sub(r'^\s{0,3}[-*+]\s+', '', line)          # 리스트 기호
        line = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)        # 볼드
        line = re.sub(r'\*([^*]+)\*', r'\1', line)            # 이탤릭
        line = re.sub(r'__([^_]+)__', r'\1', line)
        line = re.sub(r'`([^`]+)`', r'\1', line)              # 인라인 코드
        line = re.sub(r'[`*]', '', line)                      # 남은 기호
        out.append(line)
    result = '\n'.join(out)
    return re.sub(r'\n{3,}', '\n\n', result).strip()


def format_readability(text):
    """문장 끝마다 줄바꿈 보장 (표/리스트/마커 줄은 건드리지 않음)."""
    if not text:
        return text
    out = []
    for line in text.split('\n'):
        t = line.strip()
        if t == '' or t.startswith('▶') or t.startswith('[') or re.match(r'^\d+\.\s', t):
            out.append(line)
            continue
        out.append(re.sub(r'([.!?]["\'”’」』)\]]*)[ \t]+(?=\S)', r'\1\n', t))
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(out)).strip()


def heal_stranded_marks(text):
    """닫는 따옴표가 다음 줄로 떨어진 경우 되돌림."""
    if not text:
        return text
    text = re.sub(r'\n+([”’"\'])\s*$', r'\1', text, flags=re.MULTILINE)
    text = re.sub(r'([^\n])\n([”’"\'])(?=\n|$)', r'\1\2', text)
    text = re.sub(r'(\S)\n+(["\'”’])\n', r'\1\2\n', text)
    return text


def clean_section(raw):
    return heal_stranded_marks(format_readability(strip_markdown(strip_emoji(strip_contacts(raw)))))


# ─────────────────────────────────────────────────────────────
# Claude 호출 (프롬프트 캐싱 + 토큰 절단 시 이어받기)
# ─────────────────────────────────────────────────────────────

_CUT_END_RE = re.compile(r'[.!?…"”』」)\]:|]$')
_END_SUFFIX_RE = re.compile(r'(다|요|죠|음|임|함|됨|까|네)$')


def _looks_cut(text):
    t = (text or '').rstrip()
    return bool(t) and not _CUT_END_RE.search(t) and not _END_SUFFIX_RE.search(t)


def call_claude(user_text, system, claude_key, model='claude-sonnet-4-20250514',
                max_tokens=1500):
    """한 섹션 생성. system 동일 → 프롬프트 캐싱. 토큰 한도로 잘리면 이어받아 완성."""
    import anthropic
    client = anthropic.Anthropic(api_key=claude_key)
    messages = [{'role': 'user', 'content': user_text}]
    system_blocks = None
    if system and system.strip():
        system_blocks = [{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}]

    full = ''
    for guard in range(5):
        kwargs = {'model': model, 'max_tokens': max_tokens, 'messages': messages}
        if system_blocks:
            kwargs['system'] = system_blocks
        resp = client.messages.create(**kwargs)
        text = ''.join(b.text for b in resp.content if getattr(b, 'type', '') == 'text')
        full += text
        truncated = (resp.stop_reason == 'max_tokens') or (resp.stop_reason is None and _looks_cut(text))
        if truncated and guard < 4:
            messages.append({'role': 'assistant', 'content': text})
            messages.append({'role': 'user', 'content':
                             '방금 출력이 토큰 한도로 중간에 끊겼어. 마지막 글자 바로 다음부터 이어서 계속 써줘. '
                             '인사말·반복·재시작 없이 이어지는 내용만 출력해.'})
        else:
            break
    return full.strip()


def generate_article(claude_key, region='', keyword='', industry='', company='',
                     category='clean', style='hybrid', overrides=None,
                     model='claude-sonnet-4-20250514', callback=None,
                     stop_check=None):
    """6개 섹션을 순차 생성해 한 편으로 합친다.

    반환: (article, complete) — complete 는 6개 섹션이 모두 생성됐는지 여부.
    한 섹션이라도 실패/중지로 빠지면 complete=False (부분 원고를 발행에 쓰지 않도록).

    region/keyword/industry/company 가 주어지면 프리셋 기본값 위에 덮어쓴다.
    overrides: 추가로 덮어쓸 필드 dict (diff/steps/prices/reviews/cta/authority/longtail).
    """
    cat_key = category if category in CATEGORIES else 'clean'
    inputs = make_defaults(cat_key)
    if industry:
        inputs['industry'] = industry
    if keyword:
        inputs['keyword'] = keyword
    if region:
        inputs['region'] = region
    if company:
        inputs['company'] = company
    if overrides:
        inputs.update({k: v for k, v in overrides.items() if v})
    # 지역/키워드 바뀌었으면 파생필드 재동기화 (overrides 가 명시 안 했을 때만)
    if not (overrides and overrides.get('longtail')):
        inputs['longtail'] = gen_keywords(inputs['region'], inputs['keyword'], cat_key)
    if not (overrides and overrides.get('cta')):
        inputs['cta'] = gen_cta(inputs['region'], inputs['industry'], cat_key)

    system = build_system(inputs, style if style in STYLES else 'hybrid', cat_key)
    acc = []
    for n, block in enumerate(BLOCKS):
        if stop_check and stop_check():
            break
        if callback:
            callback(f"[I] {block['label']} 생성 중... ({n + 1}/{len(BLOCKS)})")
        tail = ('\n\n'.join(acc))[-500:]
        task = build_task(block, inputs, tail)
        try:
            acc.append(clean_section(call_claude(task, system, claude_key, model=model)))
        except Exception as e:
            if callback:
                callback(f"[!] 생성 오류 ({block['label']}): {e}")
            break
    filled = [s for s in acc if s]
    complete = (len(filled) == len(BLOCKS))
    article = '\n\n'.join(filled)
    if callback:
        if complete:
            callback(f"[I] 글 생성 완료 ({len(article)}자)")
        elif article:
            callback(f"[!] 미완성 — {len(filled)}/{len(BLOCKS)} 섹션만 생성됨 (발행 건너뜀)")
    return article, complete


# ─────────────────────────────────────────────────────────────
# 제목/메타 분리 — 발행용
# ─────────────────────────────────────────────────────────────

def split_title_body(article):
    """생성 본문에서 (제목, 메타설명, 본문) 분리.

    - '메타 설명:' 줄 → meta
    - 그 다음의 첫 비어있지 않은 일반 텍스트 줄(미디어 마커·번호·▶ 제외) → 제목 후보
    제목 후보가 60자를 넘으면(모델이 제목 대신 도입 문단을 먼저 쓴 경우) 제목으로 쓰지 않고
    빈 문자열을 돌려 해당 줄을 본문에 남긴다 → 호출부가 기존 제목으로 폴백하게 한다.
    """
    TITLE_MAX = 60
    meta = ''
    title = ''
    lines = (article or '').split('\n')
    body_start = 0
    for idx, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            continue
        m = re.match(r'^메타\s*설명\s*[:：]\s*(.+)$', s)
        if m and not meta:
            meta = m.group(1).strip()
            body_start = idx + 1  # 메타 줄까지는 본문에서 제외
            continue
        # 제목 후보: 마커/번호/체크기호가 아닌 첫 문장
        if not s.startswith('[') and not s.startswith('▶') and not re.match(r'^\d+\.', s):
            cand = s.strip('"\'')
            if len(cand) <= TITLE_MAX:
                title = cand
                body_start = idx + 1
            # 너무 길면 제목으로 쓰지 않고(빈 문자열) 이 줄을 본문에 남긴다
            break
    body = '\n'.join(lines[body_start:]).strip()
    return title, meta, body
