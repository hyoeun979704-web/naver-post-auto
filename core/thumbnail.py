"""썸네일 이미지 편집 모듈 — 레이어 합성 + 동적 텍스트 + 커스텀 스타일"""

import os
import random
from PIL import Image, ImageDraw, ImageFont


# ─── 카드뉴스 랜덤 스타일 팔레트 ─────────────────────────────
# 매 카드뉴스 세트마다 랜덤 선택 → 같은 디자인이 반복되지 않게.
# 한 세트(5장)는 동일 스타일로 일관성 유지.

CARDNEWS_BG_COLORS = [
    '#FFFFFF',  # 순백
    '#FFF8E7',  # 미색
    '#F5F5F5',  # 연회색
    '#E3F2FD',  # 연파랑
    '#FFF9C4',  # 연노랑
    '#FCE4EC',  # 연핑크
    '#E0F2F1',  # 연민트
    '#F3E5F5',  # 연보라
    '#FFF3E0',  # 연주황
    '#ECEFF1',  # 차가운 회색
]

CARDNEWS_ACCENT_COLORS = [
    '#22C55E',  # 초록
    '#3B82F6',  # 파랑
    '#F59E0B',  # 주황
    '#EF4444',  # 빨강
    '#8B5CF6',  # 보라
    '#06B6D4',  # 청록
    '#EC4899',  # 분홍
    '#0F172A',  # 검정
    '#10B981',  # 에메랄드
    '#D946EF',  # 마젠타
]

CARDNEWS_BODY_COLORS = ['#444444', '#333333', '#1F2937', '#374151', '#525252']

# 한글 지원 폰트만
CARDNEWS_FONTS = ['맑은 고딕 Bold', '맑은 고딕', '굴림', '바탕']

CARDNEWS_BAR_HEIGHTS = [10, 14, 16, 20, 24]

CARDNEWS_TITLE_SIZES = [80, 90, 100, 110]
CARDNEWS_BODY_SIZES = [48, 52, 56, 60]


def random_cardnews_style(seed=None) -> dict:
    """카드뉴스 한 세트(5장)에서 공통으로 쓸 랜덤 스타일 dict.

    시드를 주면 같은 키워드는 같은 스타일이 나오게 할 수 있음.
    None이면 매 호출마다 완전히 새 스타일.
    """
    rng = random.Random(seed) if seed is not None else random
    bg = rng.choice(CARDNEWS_BG_COLORS)
    accent = rng.choice(CARDNEWS_ACCENT_COLORS)
    # 배경이 너무 어두울 일은 거의 없지만, accent와 너무 가까우면 다시 뽑음
    for _ in range(3):
        if accent != bg:
            break
        accent = rng.choice(CARDNEWS_ACCENT_COLORS)
    return {
        'bg_color': bg,
        'accent_color': accent,
        'body_color': rng.choice(CARDNEWS_BODY_COLORS),
        'font_name': rng.choice(CARDNEWS_FONTS),
        'title_font_size': rng.choice(CARDNEWS_TITLE_SIZES),
        'body_font_size': rng.choice(CARDNEWS_BODY_SIZES),
        'bar_height': rng.choice(CARDNEWS_BAR_HEIGHTS),
    }


TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'template')

# 사용 가능한 폰트 목록 (이름 → 경로)
FONT_MAP = {
    '맑은 고딕 Bold': 'C:/Windows/Fonts/malgunbd.ttf',
    '맑은 고딕': 'C:/Windows/Fonts/malgun.ttf',
    '굴림': 'C:/Windows/Fonts/gulim.ttc',
    '바탕': 'C:/Windows/Fonts/batang.ttc',
    'Gothic Bold': 'C:/Windows/Fonts/GOTHICB.TTF',
    'Gothic': 'C:/Windows/Fonts/GOTHIC.TTF',
    'Arial Bold': 'C:/Windows/Fonts/ARIALNB.TTF',
    'Impact': 'C:/Windows/Fonts/impact.ttf',
}


def get_available_fonts() -> list:
    """시스템에 실제 존재하는 폰트 목록 반환"""
    return [name for name, path in FONT_MAP.items() if os.path.exists(path)]


def _get_font(font_name: str, size: int) -> ImageFont.FreeTypeFont:
    """폰트 이름으로 폰트 객체 반환"""
    path = FONT_MAP.get(font_name, '')
    if path and os.path.exists(path):
        return ImageFont.truetype(path, size)
    # 기본 폰트
    for fp in FONT_MAP.values():
        if os.path.exists(fp):
            return ImageFont.truetype(fp, size)
    return ImageFont.load_default()


def _hex_to_rgba(hex_color: str) -> tuple:
    """#RRGGBB → (R, G, B, 255)"""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 6:
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        return (r, g, b, 255)
    return (74, 144, 205, 255)


def create_thumbnail(background_path: str, output_path: str,
                     size: tuple = (1080, 1080),
                     template_dir: str = None,
                     bg_offset_x: int = 0, bg_offset_y: int = 0,
                     bg_end_y: int = 650,
                     text1: str = '', text2: str = '',
                     font_name: str = '맑은 고딕 Bold',
                     font_size1: int = 100, font_size2: int = 85,
                     font_color: str = '#4A90CD',
                     text1_y: int = 850, text2_y: int = 960,
                     outline: str = '보통') -> str:
    """배경 사진 위에 템플릿 + 텍스트 합성"""
    if template_dir is None:
        template_dir = TEMPLATE_DIR

    w, h = size

    # 1. 흰색 캔버스
    result = Image.new('RGBA', (w, h), (255, 255, 255, 255))

    # 2. 배경 사진 상단에 배치 (EXIF 회전 보정)
    bg = Image.open(background_path)
    from PIL import ImageOps
    bg = ImageOps.exif_transpose(bg)
    bg = bg.convert('RGBA')
    margin = 200
    bg = _cover_resize(bg, w + margin * 2, bg_end_y + margin * 2)
    cx = margin - bg_offset_x
    cy = margin - bg_offset_y
    bg = bg.crop((cx, cy, cx + w, cy + bg_end_y))
    result.paste(bg, (0, 0))

    # 3~5. 템플릿 레이어
    for layer_name in ['1.png', '2.png', '3_eco_only.png']:
        layer_path = os.path.join(template_dir, layer_name)
        if os.path.exists(layer_path):
            layer = Image.open(layer_path).convert('RGBA')
            layer = layer.resize((w, h), Image.LANCZOS)
            result = Image.alpha_composite(result, layer)

    # 6. 텍스트 렌더링
    if text1 or text2:
        # 두께 설정
        outline_map = {'없음': 0, '얇게': 1, '보통': 2, '두껍게': 3, '매우 두껍게': 5}
        ol_size = outline_map.get(outline, 2)

        txt_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(txt_layer)
        color = _hex_to_rgba(font_color)
        outline_color = (max(0, color[0]-50), max(0, color[1]-50), max(0, color[2]-50), 255)

        def _draw_text_with_outline(draw, x, y, text, font, fill, ol):
            if ol > 0:
                for dx in range(-ol, ol+1):
                    for dy in range(-ol, ol+1):
                        if dx != 0 or dy != 0:
                            draw.text((x+dx, y+dy), text, fill=outline_color, font=font)
            draw.text((x, y), text, fill=fill, font=font)

        if text1:
            font1 = _get_font(font_name, font_size1)
            bbox1 = draw.textbbox((0, 0), text1, font=font1)
            tw1 = bbox1[2] - bbox1[0]
            tx1 = (w - tw1) // 2
            _draw_text_with_outline(draw, tx1, text1_y, text1, font1, color, ol_size)

        if text2:
            font2 = _get_font(font_name, font_size2)
            bbox2 = draw.textbbox((0, 0), text2, font=font2)
            tw2 = bbox2[2] - bbox2[0]
            tx2 = (w - tw2) // 2
            _draw_text_with_outline(draw, tx2, text2_y, text2, font2, color, ol_size)

        result = Image.alpha_composite(result, txt_layer)

    result = result.convert('RGB')
    result.save(output_path, quality=95)
    return output_path


def create_thumbnail_family(template_path: str, output_path: str,
                            size: tuple = (1080, 1080),
                            text: str = '',
                            font_name: str = '맑은 고딕 Bold',
                            font_size: int = 120,
                            font_color: str = '#3AB94A',
                            text_y: int = 830,
                            outline: str = '보통',
                            fit_mode: str = 'cover',
                            offset_x: int = 0,
                            offset_y: int = 0) -> str:
    """가족사랑클린/포장이사 스타일 — 템플릿 이미지 위에 키워드 텍스트만 오버레이.
    - 사진 합성, 레이어 컴포지트 없음
    - template_path 이미지를 그대로 캔버스로 사용
    - 키워드 텍스트를 중앙 가로정렬로 오버레이 (검은색 외곽선)
    - fit_mode='cover'(기본): size에 맞춰 cover-crop. 'fit': 원본 비율 유지 (짤림 없음).
    - offset_x/offset_y: 텍스트 위치 미세조정 (기본 0). 좌우 -면 왼쪽, +면 오른쪽 / 상하 -면 위, +면 아래
    """
    # 템플릿 이미지를 캔버스로
    from PIL import ImageOps
    base = Image.open(template_path)
    base = ImageOps.exif_transpose(base)
    base = base.convert('RGBA')

    if fit_mode == 'fit':
        # 원본 비율 유지 — size의 최대치 기준으로 fit (짤림 없음, 캔버스도 비례)
        max_target = max(size)
        ratio = max_target / max(base.width, base.height)
        new_w = max(1, int(base.width * ratio))
        new_h = max(1, int(base.height * ratio))
        base = base.resize((new_w, new_h), Image.LANCZOS)
        w, h = new_w, new_h
    else:
        w, h = size
        base = _cover_resize(base, w, h)
    result = base

    if text:
        outline_map = {'없음': 0, '얇게': 1, '보통': 2, '두껍게': 3, '매우 두껍게': 5}
        ol_size = outline_map.get(outline, 2)

        txt_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(txt_layer)
        color = _hex_to_rgba(font_color)
        outline_color = (0, 0, 0, 255)  # 검은색 외곽선

        # 텍스트가 캔버스 너비를 넘으면 폰트를 자동 축소 (좌우 여백 60px 보장)
        max_text_w = w - 60 - ol_size * 2
        cur_size = font_size
        font = _get_font(font_name, cur_size)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        while tw > max_text_w and cur_size > 20:
            cur_size = max(20, int(cur_size * 0.92))
            font = _get_font(font_name, cur_size)
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
        tx = (w - tw) // 2 + int(offset_x)  # 좌우 오프셋 반영

        # 텍스트가 캔버스 아래로 잘리지 않도록 y 클램프 (offset_y 반영)
        th = bbox[3] - bbox[1]
        max_y = h - th - ol_size - 10
        clamped_y = max(0, min(text_y + int(offset_y), max_y))

        if ol_size > 0:
            for dx in range(-ol_size, ol_size + 1):
                for dy in range(-ol_size, ol_size + 1):
                    if dx != 0 or dy != 0:
                        draw.text((tx + dx, clamped_y + dy), text, fill=outline_color, font=font)
        draw.text((tx, clamped_y), text, fill=color, font=font)

        result = Image.alpha_composite(result, txt_layer)

    result = result.convert('RGB')
    result.save(output_path, quality=95)
    return output_path


def _cover_resize(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """center crop + 리사이즈"""
    img_ratio = img.width / img.height
    target_ratio = target_w / target_h
    if img_ratio > target_ratio:
        new_h = target_h
        new_w = int(target_h * img_ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - target_w) // 2
        img = img.crop((left, 0, left + target_w, new_h))
    else:
        new_w = target_w
        new_h = int(target_w / img_ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        top = (new_h - target_h) // 2
        img = img.crop((0, top, new_w, top + target_h))
    return img


def _wrap_text_by_width(text: str, font, max_width: int, draw) -> list:
    """주어진 폰트·최대너비에 맞춰 텍스트를 여러 줄로 wrap.
    띄어쓰기 단위 우선, 단어 자체가 너비 초과 시 글자 단위 fallback."""
    if not text:
        return []

    def _measure(s):
        bb = draw.textbbox((0, 0), s, font=font)
        return bb[2] - bb[0]

    words = text.split(' ')
    lines = []
    cur = ''
    for w_idx, word in enumerate(words):
        # 단어 자체가 너비 초과면 글자 단위로 강제 분할
        if _measure(word) > max_width:
            if cur:
                lines.append(cur)
                cur = ''
            char_buf = ''
            for ch in word:
                if _measure(char_buf + ch) <= max_width:
                    char_buf += ch
                else:
                    if char_buf:
                        lines.append(char_buf)
                    char_buf = ch
            cur = char_buf
            continue
        # 일반 단어 — 현재 줄에 추가 시도
        trial = (cur + ' ' + word).strip() if cur else word
        if _measure(trial) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def create_cardnews_slide(output_path: str,
                          slide_num: int, total: int,
                          title: str, body: str,
                          size: tuple = (1080, 1080),
                          bg_color: str = '#FFFFFF',
                          accent_color: str = '#22C55E',
                          body_color: str = '#444444',
                          font_name: str = '맑은 고딕 Bold',
                          title_font_size: int = 90,
                          body_font_size: int = 54,
                          bar_height: int = 16) -> str:
    """카드뉴스 슬라이드 1장 생성 — 단색 배경 + 텍스트 (직접 디자인).

    레이아웃 (1080x1080 기준):
      - 상단 우측: 슬라이드 번호 (1/5, 2/5, ...)
      - 중앙 상단(y≈260~): 제목 (큰 글씨, accent_color)
      - 중앙 하단(y≈500~): 본문 (자동 줄바꿈, body_color)
      - 하단 좌측: 작은 강조 라인 (accent_color)
    """
    w, h = size
    bg_rgba = _hex_to_rgba(bg_color)
    accent_rgba = _hex_to_rgba(accent_color)
    body_rgba = _hex_to_rgba(body_color)

    # 1. 캔버스 (단색 배경)
    result = Image.new('RGBA', (w, h), bg_rgba)
    draw = ImageDraw.Draw(result)

    # (상단 페이지 번호 + accent 막대 제거 — 사용자 요청. 하단 선으로만 구분)

    # 2. 제목 — 중앙 정렬, 자동 줄바꿈 (좌우 70px 여백)
    max_title_w = w - 140
    title_font = _get_font(font_name, title_font_size)
    # 제목이 너무 길면 폰트 자동 축소
    chosen_title_size = title_font_size
    while chosen_title_size > 50:
        title_font = _get_font(font_name, chosen_title_size)
        title_lines = _wrap_text_by_width(title, title_font, max_title_w, draw)
        if len(title_lines) <= 2:
            break
        chosen_title_size -= 6

    line_gap_t = int(chosen_title_size * 0.35)
    line_h_t = chosen_title_size + line_gap_t
    title_block_h = len(title_lines) * line_h_t - line_gap_t if title_lines else 0
    # 상단 비워졌으니 좀 더 위로 (260)
    title_y = 260
    for i, line in enumerate(title_lines):
        bb = draw.textbbox((0, 0), line, font=title_font)
        lw = bb[2] - bb[0]
        x = (w - lw) // 2
        y = title_y + i * line_h_t
        draw.text((x, y), line, fill=accent_rgba, font=title_font)

    # 3. 본문 — 제목 아래
    max_body_w = w - 160
    body_font = _get_font(font_name, body_font_size)
    body_lines = _wrap_text_by_width(body, body_font, max_body_w, draw)
    line_gap_b = int(body_font_size * 0.45)
    line_h_b = body_font_size + line_gap_b
    body_y = title_y + max(title_block_h, line_h_t) + 80
    for i, line in enumerate(body_lines):
        bb = draw.textbbox((0, 0), line, font=body_font)
        lw = bb[2] - bb[0]
        x = (w - lw) // 2
        y = body_y + i * line_h_b
        draw.text((x, y), line, fill=body_rgba, font=body_font)

    # 4. 하단 강조 라인 (이것만 유지) — bar_height 만큼
    draw.rectangle([0, h - bar_height, w, h], fill=accent_rgba)

    result = result.convert('RGB')
    result.save(output_path, quality=95)
    return output_path


def create_cardnews_slides(output_dir: str, slides: list,
                           size: tuple = (1080, 1080),
                           style: dict = None,
                           seed=None) -> list:
    """카드뉴스 슬라이드 N장을 0.jpg ~ (N-1).jpg 로 일괄 생성.

    slides: [{'title': '...', 'body': '...'}, ...]
    output_dir: 저장 폴더 (없으면 생성)
    style: 디자인 스타일 dict (bg_color/accent_color/body_color/font_name/title_font_size/body_font_size/bar_height).
           None이면 random_cardnews_style()로 자동 결정 → 매번 다른 디자인.
    seed: 시드값 (None이면 호출마다 진짜 랜덤)
    return: 생성된 파일 경로 리스트
    """
    os.makedirs(output_dir, exist_ok=True)
    if style is None:
        style = random_cardnews_style(seed=seed)
    out_paths = []
    total = len(slides)
    for i, s in enumerate(slides):
        out_path = os.path.join(output_dir, f'{i}.jpg')
        create_cardnews_slide(
            out_path,
            slide_num=i + 1, total=total,
            title=s.get('title', ''),
            body=s.get('body', ''),
            size=size,
            bg_color=style.get('bg_color', '#FFFFFF'),
            accent_color=style.get('accent_color', '#22C55E'),
            body_color=style.get('body_color', '#444444'),
            font_name=style.get('font_name', '맑은 고딕 Bold'),
            title_font_size=style.get('title_font_size', 90),
            body_font_size=style.get('body_font_size', 54),
            bar_height=style.get('bar_height', 16),
        )
        out_paths.append(out_path)
    return out_paths


def batch_create_thumbnails(backgrounds: list, output_dir: str,
                            text1: str = '', text2: str = '',
                            **kwargs) -> list:
    """여러 배경 사진으로 썸네일 일괄 생성"""
    os.makedirs(output_dir, exist_ok=True)
    results = []
    for bg_path in backgrounds:
        fname = os.path.basename(bg_path)
        out_name = f'thumb_{os.path.splitext(fname)[0]}.jpg'
        out_path = os.path.join(output_dir, out_name)
        create_thumbnail(bg_path, out_path, text1=text1, text2=text2, **kwargs)
        results.append(out_path)
    return results
