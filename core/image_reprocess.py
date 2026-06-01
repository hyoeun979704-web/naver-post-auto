"""이미지 재가공 — 네이버 SEO 중복 검출 회피용 미세 변형 + EXIF 제거.

각 옵션은 매 이미지마다 랜덤 값으로 적용 → 같은 원본이라도 매번 다른 결과.
픽셀 해시·perceptual hash가 다른 값이 나오게 하는 게 목적.

옵션:
- EXIF/메타데이터 완전 제거 (항상)
- 색상 미세 조정 (밝기·대비·채도 ±3%)
- 미세 회전 (±0.5도)
- 미세 리사이즈 (98~102%)
- 가우시안 노이즈 미세 추가
- 샤프닝 약간
- 랜덤 테두리 (옵션)
"""

import os
import random
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageDraw


# ─── 테두리 스타일 ─────────────────────────
BORDER_STYLES = [
    '흰색', '검정', '회색', '랜덤 단색',
    '체크무늬 (흑백)', '체크무늬 (파스텔)',
    '도트 (흰바탕 검점)', '도트 (검바탕 흰점)',
    '가로 그라디언트', '세로 그라디언트',
    '랜덤 파스텔',
]


def _pastel_color(rng=None) -> tuple:
    rng = rng or random
    return (rng.randint(180, 255), rng.randint(180, 255), rng.randint(180, 255))


def _add_border(im: Image.Image, thickness: int, style: str = '흰색') -> Image.Image:
    """이미지 둘레에 테두리 추가. 다양한 스타일 지원."""
    if thickness <= 0:
        return im
    w, h = im.size
    new_w, new_h = w + thickness * 2, h + thickness * 2

    # 단색 — ImageOps.expand가 빠름
    if style == '흰색':
        return ImageOps.expand(im, border=thickness, fill=(255, 255, 255))
    if style == '검정':
        return ImageOps.expand(im, border=thickness, fill=(0, 0, 0))
    if style == '회색':
        return ImageOps.expand(im, border=thickness, fill=(180, 180, 180))
    if style == '랜덤 단색':
        c = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        return ImageOps.expand(im, border=thickness, fill=c)
    if style == '랜덤 파스텔':
        return ImageOps.expand(im, border=thickness, fill=_pastel_color())

    # 패턴 테두리 — 새 캔버스 + 패턴 그리고 중앙에 원본 paste
    if style == '체크무늬 (흑백)':
        c1, c2 = (255, 255, 255), (0, 0, 0)
        return _border_checker(im, thickness, c1, c2)
    if style == '체크무늬 (파스텔)':
        return _border_checker(im, thickness, _pastel_color(), _pastel_color())
    if style == '도트 (흰바탕 검점)':
        return _border_dots(im, thickness, bg=(255, 255, 255), dot=(0, 0, 0))
    if style == '도트 (검바탕 흰점)':
        return _border_dots(im, thickness, bg=(0, 0, 0), dot=(255, 255, 255))
    if style == '가로 그라디언트':
        c1 = _pastel_color()
        c2 = _pastel_color()
        return _border_gradient(im, thickness, c1, c2, horizontal=True)
    if style == '세로 그라디언트':
        c1 = _pastel_color()
        c2 = _pastel_color()
        return _border_gradient(im, thickness, c1, c2, horizontal=False)

    # 기본 폴백 — 흰색
    return ImageOps.expand(im, border=thickness, fill=(255, 255, 255))


def _border_checker(im: Image.Image, t: int, c1: tuple, c2: tuple) -> Image.Image:
    """체크무늬 테두리. 셀 크기는 thickness/3 정도 자동."""
    w, h = im.size
    canvas = Image.new('RGB', (w + t * 2, h + t * 2), c1)
    draw = ImageDraw.Draw(canvas)
    cell = max(4, t // 3)
    for y in range(0, canvas.height, cell):
        for x in range(0, canvas.width, cell):
            if ((x // cell) + (y // cell)) % 2 == 0:
                draw.rectangle([x, y, x + cell, y + cell], fill=c2)
    canvas.paste(im, (t, t))
    return canvas


def _border_dots(im: Image.Image, t: int, bg: tuple, dot: tuple) -> Image.Image:
    """도트 패턴 테두리. 점 간격·크기는 thickness 비례."""
    w, h = im.size
    canvas = Image.new('RGB', (w + t * 2, h + t * 2), bg)
    draw = ImageDraw.Draw(canvas)
    spacing = max(8, t // 2)
    radius = max(2, t // 8)
    for y in range(spacing // 2, canvas.height, spacing):
        for x in range(spacing // 2, canvas.width, spacing):
            # 원본 영역 안쪽 점은 그리지 않음
            if t <= x < t + w and t <= y < t + h:
                continue
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=dot)
    canvas.paste(im, (t, t))
    return canvas


def _border_gradient(im: Image.Image, t: int, c1: tuple, c2: tuple, horizontal: bool = True) -> Image.Image:
    """그라디언트 테두리."""
    w, h = im.size
    canvas = Image.new('RGB', (w + t * 2, h + t * 2), c1)
    draw = ImageDraw.Draw(canvas)
    if horizontal:
        for x in range(canvas.width):
            ratio = x / max(1, canvas.width - 1)
            r = int(c1[0] + (c2[0] - c1[0]) * ratio)
            g = int(c1[1] + (c2[1] - c1[1]) * ratio)
            b = int(c1[2] + (c2[2] - c1[2]) * ratio)
            draw.line([(x, 0), (x, canvas.height)], fill=(r, g, b))
    else:
        for y in range(canvas.height):
            ratio = y / max(1, canvas.height - 1)
            r = int(c1[0] + (c2[0] - c1[0]) * ratio)
            g = int(c1[1] + (c2[1] - c1[1]) * ratio)
            b = int(c1[2] + (c2[2] - c1[2]) * ratio)
            draw.line([(0, y), (canvas.width, y)], fill=(r, g, b))
    canvas.paste(im, (t, t))
    return canvas


def _strip_exif_save(im: Image.Image, dst_path: str, fmt: str = None, quality: int = 92):
    """EXIF·ICC 등 메타 완전 제거하고 저장."""
    # 새 이미지 객체로 복사하면 메타데이터 안 따라옴
    data = list(im.getdata())
    clean = Image.new(im.mode, im.size)
    clean.putdata(data)
    fmt = fmt or ('JPEG' if dst_path.lower().endswith(('.jpg', '.jpeg')) else 'PNG')
    save_kwargs = {}
    if fmt == 'JPEG':
        save_kwargs.update({'quality': quality, 'optimize': True})
    else:
        save_kwargs.update({'optimize': True})
    clean.save(dst_path, format=fmt, **save_kwargs)


def _apply_pipeline(im: Image.Image, opts: dict) -> Image.Image:
    """공통 변형 파이프라인 — opts에 강도 값 포함. PIL 이미지 반환."""
    im = ImageOps.exif_transpose(im)
    if im.mode != 'RGB':
        im = im.convert('RGB')

    # 1) 색상 — color_pct: 퍼센트 (예: 3 → ±3%)
    if opts.get('color', True):
        pct = float(opts.get('color_pct', 3)) / 100.0
        lo, hi = max(0.0, 1.0 - pct), 1.0 + pct
        im = ImageEnhance.Brightness(im).enhance(random.uniform(lo, hi))
        im = ImageEnhance.Contrast(im).enhance(random.uniform(lo, hi))
        im = ImageEnhance.Color(im).enhance(random.uniform(lo, hi))

    # 2) 회전 — rotate_deg: ±도 (예: 0.5)
    if opts.get('rotate', True):
        deg = float(opts.get('rotate_deg', 0.5))
        angle = random.uniform(-deg, deg)
        im = im.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=(255, 255, 255))

    # 3) 리사이즈 — resize_min/max: 퍼센트 (예: 98, 102)
    if opts.get('resize', True):
        rmin = float(opts.get('resize_min', 98)) / 100.0
        rmax = float(opts.get('resize_max', 102)) / 100.0
        if rmax < rmin: rmin, rmax = rmax, rmin
        scale = random.uniform(rmin, rmax)
        new_size = (max(1, int(im.width * scale)), max(1, int(im.height * scale)))
        im = im.resize(new_size, Image.LANCZOS)

    # 4) 노이즈 — noise_amp: ± (예: 3)
    if opts.get('noise', True):
        try:
            import numpy as np
            amp = max(1, int(opts.get('noise_amp', 3)))
            arr = np.array(im).astype(np.int16)
            n = np.random.randint(-amp, amp + 1, arr.shape, dtype=np.int16)
            arr = np.clip(arr + n, 0, 255).astype(np.uint8)
            im = Image.fromarray(arr)
        except Exception:
            pass

    # 5) 샤프닝 — sharpen_pct: 퍼센트 (예: 60)
    if opts.get('sharpen', True):
        pct = max(0, min(150, int(opts.get('sharpen_pct', 60))))
        im = im.filter(ImageFilter.UnsharpMask(radius=1, percent=pct, threshold=2))

    # 6) 테두리 — border_min/max: px, border_style: 스타일 이름
    if opts.get('border', False):
        bmin = max(0, int(opts.get('border_min', 2)))
        bmax = max(bmin, int(opts.get('border_max', 10)))
        b = random.randint(bmin, bmax) if bmax > bmin else bmin
        if b > 0:
            style = opts.get('border_style', '흰색')
            im = _add_border(im, b, style=style)
    return im


def reprocess_one(src_path: str, dst_path: str, **opts) -> bool:
    """이미지 1장 재가공 → 디스크 저장. opts는 _apply_pipeline 옵션."""
    try:
        im = Image.open(src_path)
        im = _apply_pipeline(im, opts)
        _strip_exif_save(im, dst_path, quality=opts.get('quality', 92))
        return True
    except Exception:
        return False


def reprocess_in_memory(src_path: str, **opts) -> bytes:
    """메모리에서만 처리 — 디스크 저장 X. PNG 바이트 반환 (미리보기용).
    매 호출마다 다른 결과 (랜덤 시드 안 고정). 실패 시 빈 bytes."""
    try:
        from io import BytesIO
        im = Image.open(src_path)
        im = _apply_pipeline(im, opts)
        # EXIF 없이 PNG로 메모리 저장
        data = list(im.getdata())
        clean = Image.new(im.mode, im.size)
        clean.putdata(data)
        buf = BytesIO()
        clean.save(buf, format='PNG', optimize=False)
        return buf.getvalue()
    except Exception:
        return b''


def find_images(folder: str) -> list:
    """폴더에서 이미지 파일 경로 리스트 (이름순)."""
    exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
    if not os.path.isdir(folder):
        return []
    return sorted(
        os.path.join(folder, f) for f in os.listdir(folder)
        if f.lower().endswith(exts) and os.path.isfile(os.path.join(folder, f))
    )


def reprocess_batch(src_paths: list, output_dir: str,
                    options: dict = None,
                    stop_check=None, callback=None) -> int:
    """여러 이미지 일괄 재가공. 성공 장수 반환. options는 _apply_pipeline 전체 옵션."""
    options = options or {}
    os.makedirs(output_dir, exist_ok=True)
    total = len(src_paths)
    success = 0
    for i, src in enumerate(src_paths, 1):
        if stop_check and stop_check():
            if callback:
                callback(f"[!] 중지 요청 — {success}장 완료")
            break
        name = os.path.basename(src)
        base, ext = os.path.splitext(name)
        if os.path.abspath(os.path.dirname(src)) == os.path.abspath(output_dir):
            dst_name = f'{base}_new{ext}'
        else:
            dst_name = name
        dst = os.path.join(output_dir, dst_name)
        if callback:
            callback(f"[{i}/{total}] {name} → {dst_name}")
        ok = reprocess_one(src, dst, **options)
        if ok:
            success += 1
        elif callback:
            callback(f"  [실패] {name}")
    return success
