"""사진 → 슬라이드쇼 동영상 생성 모듈 (ffmpeg) — 효과 포함"""

import os
import sys
import glob
import tempfile
import subprocess
import random
from PIL import Image, ImageOps


# Windows에서 ffmpeg 실행 시 콘솔 창 숨김
_SUBPROCESS_FLAGS = {}
if sys.platform == 'win32':
    _SUBPROCESS_FLAGS['creationflags'] = 0x08000000  # CREATE_NO_WINDOW


def _run_ffmpeg(cmd, timeout=None):
    """ffmpeg 실행 래퍼 - Windows 콘솔 창 숨김"""
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace',
                          timeout=timeout, **_SUBPROCESS_FLAGS)


def _get_ffmpeg():
    """ffmpeg 바이너리 경로"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return 'ffmpeg'


_ENCODER_CACHE = None

def _detect_encoder(ffmpeg_path):
    """사용 가능한 하드웨어 인코더 탐색 (NVIDIA → Intel → AMD → CPU)."""
    global _ENCODER_CACHE
    if _ENCODER_CACHE is not None:
        return _ENCODER_CACHE

    for encoder in ['h264_nvenc', 'h264_qsv', 'h264_amf']:
        try:
            cmd = [ffmpeg_path, '-y', '-f', 'lavfi', '-i', 'color=black:s=16x16:d=0.04',
                   '-frames:v', '1', '-c:v', encoder, '-f', 'null', '-']
            r = _run_ffmpeg(cmd, timeout=5)
            if r.returncode == 0:
                _ENCODER_CACHE = encoder
                return encoder
        except Exception:
            continue
    _ENCODER_CACHE = 'libx264'
    return 'libx264'


def _encoder_args(encoder):
    """인코더별 최적 파라미터 (하드웨어는 preset 문법이 다름).
    파일 크기 폭주 방지 위해 최대 비트레이트(4Mbps) 캡 적용 — 1080p 슬라이드에 충분."""
    if encoder == 'h264_nvenc':
        return ['-c:v', 'h264_nvenc', '-preset', 'p4', '-tune', 'hq', '-rc', 'vbr',
                '-cq', '26', '-b:v', '0', '-maxrate', '4M', '-bufsize', '8M']
    if encoder == 'h264_qsv':
        return ['-c:v', 'h264_qsv', '-preset', 'veryfast', '-global_quality', '26',
                '-maxrate', '4M', '-bufsize', '8M']
    if encoder == 'h264_amf':
        return ['-c:v', 'h264_amf', '-quality', 'speed', '-rc', 'vbr_peak',
                '-qp_i', '26', '-qp_p', '26', '-maxrate', '4M', '-bufsize', '8M']
    # CPU fallback
    return ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '26',
            '-maxrate', '4M', '-bufsize', '8M', '-threads', '0']


def _find_images(folder):
    """폴더에서 이미지 파일 목록"""
    exts = ('*.jpg', '*.jpeg', '*.png', '*.webp', '*.bmp')
    images = []
    for ext in exts:
        images.extend(glob.glob(os.path.join(folder, ext)))
    images.sort()
    return images


def _prepare_image(img_path, width=1920, height=1080):
    """이미지를 동영상 프레임 크기로 맞추기 (EXIF 회전 + 중앙 크롭)"""
    img = Image.open(img_path)
    img = ImageOps.exif_transpose(img)
    img = img.convert('RGB')

    img_ratio = img.width / img.height
    target_ratio = width / height

    if img_ratio > target_ratio:
        new_h = height
        new_w = int(height * img_ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - width) // 2
        img = img.crop((left, 0, left + width, new_h))
    else:
        new_w = width
        new_h = int(width / img_ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        top = (new_h - height) // 2
        img = img.crop((0, top, new_w, top + height))

    return img


def create_slideshow(image_folder: str, output_path: str,
                     duration: float = 3.0,
                     transition: float = 1.0,
                     width: int = 1920, height: int = 1080,
                     fps: int = 24,
                     effect: str = 'kenburns',
                     callback=None) -> str:
    """사진 폴더 → 슬라이드쇼 MP4 생성 (효과 포함)

    Args:
        effect: 'kenburns' (줌인/줌아웃) | 'fade' (페이드) | 'simple' (단순)
    """
    images = _find_images(image_folder)
    if not images:
        raise ValueError(f"이미지가 없습니다: {image_folder}")

    ffmpeg = _get_ffmpeg()
    encoder = _detect_encoder(ffmpeg)
    n = len(images)
    if callback:
        callback(f"[I] {n}장 사진 → 동영상 생성 (효과: {effect}, 인코더: {encoder})")

    # 임시 폴더에 리사이즈된 이미지 저장
    temp_dir = tempfile.mkdtemp(prefix='slideshow_')
    prepared = []

    for i, img_path in enumerate(images):
        if callback:
            callback(f"[I] [{i+1}/{n}] 이미지 준비 중...")
        # 켄 번스용으로 약간 크게 준비 (1.2배)
        scale = 1.2 if effect == 'kenburns' else 1.0
        img = _prepare_image(img_path, int(width * scale), int(height * scale))
        out = os.path.join(temp_dir, f'frame_{i:04d}.jpg')
        img.save(out, quality=95)
        prepared.append(out)

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

    if effect == 'kenburns':
        _build_kenburns(ffmpeg, prepared, output_path, duration, transition,
                        width, height, fps, temp_dir, callback, encoder)
    elif effect == 'fadeblur':
        _build_fadeblur(ffmpeg, prepared, output_path, duration, transition,
                        width, height, fps, temp_dir, callback, encoder)
    elif effect == 'fade':
        _build_fade(ffmpeg, prepared, output_path, duration, transition,
                    width, height, fps, temp_dir, callback, encoder)
    else:
        _build_simple(ffmpeg, prepared, output_path, duration,
                      width, height, fps, temp_dir, callback, encoder)

    # 정리
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)

    if callback:
        total_sec = n * duration
        callback(f"[I] 동영상 생성 완료! ({n}장, {total_sec:.0f}초)")

    return output_path


def _build_kenburns(ffmpeg, images, output, duration, transition,
                    w, h, fps, temp_dir, callback, encoder='libx264'):
    """켄 번스 효과 — 줌인/줌아웃 + 페이드 전환"""
    n = len(images)
    frames_per = int(duration * fps)

    if callback:
        callback(f"[I] 켄 번스 효과 적용 중...")

    # 각 이미지에 zoompan + xfade 적용
    inputs = []
    filters = []

    for i, img in enumerate(images):
        inputs.extend(['-loop', '1', '-t', str(duration + transition), '-i', img])

        # 랜덤 줌 방향: 줌인 or 줌아웃
        if random.random() > 0.5:
            # 줌인: 1.0 → 1.15
            zoom = f"min(zoom+0.0008,1.15)"
            x = f"iw/2-(iw/zoom/2)"
            y = f"ih/2-(ih/zoom/2)"
        else:
            # 줌아웃: 1.15 → 1.0
            zoom = f"if(eq(on,1),1.15,max(zoom-0.0008,1.0))"
            x = f"iw/2-(iw/zoom/2)"
            y = f"ih/2-(ih/zoom/2)"

        total_frames = int((duration + transition) * fps)
        # trim으로 세그먼트 길이를 강제 캡 — zoompan의 d는 "입력 프레임당 출력 프레임 수"라
        # 루프 입력이 여러 프레임일 때 영상이 수십 배로 부풀어버림.
        # trim 뒤 fps 재고정 — xfade는 CFR 입력만 받음.
        filters.append(
            f"[{i}:v]zoompan=z='{zoom}':x='{x}':y='{y}'"
            f":d={total_frames}:s={w}x{h}:fps={fps},"
            f"trim=duration={duration + transition},setpts=PTS-STARTPTS,"
            f"fps={fps},format=yuv420p[v{i}]"
        )

    # xfade 전환 연결
    if n == 1:
        filter_str = ';'.join(filters)
        map_label = f'[v0]'
    else:
        xfade_parts = list(filters)
        prev = 'v0'
        for i in range(1, n):
            offset = i * duration - (i - 1) * transition + (i - 1) * transition
            offset = max(0, i * duration - transition * (i > 0))
            out_label = f'xf{i}'
            xfade_parts.append(
                f"[{prev}][v{i}]xfade=transition=fade:duration={transition}"
                f":offset={i * duration - transition * i + transition * (i-1)}[{out_label}]"
            )
            prev = out_label
        filter_str = ';'.join(xfade_parts)
        map_label = f'[{prev}]'

    cmd = [ffmpeg, '-y'] + inputs + [
        '-filter_complex', filter_str,
        '-map', map_label,
    ] + _encoder_args(encoder) + [
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
        output
    ]

    if callback:
        callback(f"[I] ffmpeg 인코딩 중... ({encoder})")

    result = _run_ffmpeg(cmd)
    if result.returncode != 0:
        if callback:
            callback(f"[!] 켄 번스 실패, 페이드 모드로 전환...")
        _build_fade(ffmpeg, images, output, duration, transition,
                    w, h, fps, os.path.dirname(images[0]), callback, encoder)


def _build_fadeblur(ffmpeg, images, output, duration, transition,
                    w, h, fps, temp_dir, callback, encoder='libx264'):
    """페이드 + 블러 전환 효과"""
    n = len(images)
    if callback:
        callback(f"[I] 페이드+블러 효과 적용 중...")

    inputs = []
    filters = []

    for i, img in enumerate(images):
        inputs.extend(['-loop', '1', '-t', str(duration + transition), '-i', img])
        filters.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                      f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}[v{i}]")

    if n == 1:
        filter_str = ';'.join(filters)
        map_label = '[v0]'
    else:
        xfade_parts = list(filters)
        prev = 'v0'
        # 전환 효과 목록 — 번갈아 사용
        transitions = ['fadeblack', 'fadewhite', 'smoothleft', 'smoothright',
                       'circlecrop', 'dissolve', 'pixelize', 'radial']
        for i in range(1, n):
            out_label = f'xf{i}'
            offset = max(0, duration * i - transition * i + transition * (i - 1))
            tr = transitions[(i - 1) % len(transitions)]
            xfade_parts.append(
                f"[{prev}][v{i}]xfade=transition={tr}:duration={transition}"
                f":offset={offset}[{out_label}]"
            )
            prev = out_label
        filter_str = ';'.join(xfade_parts)
        map_label = f'[{prev}]'

    cmd = [ffmpeg, '-y'] + inputs + [
        '-filter_complex', filter_str,
        '-map', map_label,
    ] + _encoder_args(encoder) + [
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
        output
    ]

    if callback:
        callback(f"[I] ffmpeg 인코딩 중... ({encoder})")

    result = _run_ffmpeg(cmd)
    if result.returncode != 0:
        if callback:
            callback(f"[!] 페이드+블러 실패, 페이드 모드로 전환...")
        _build_fade(ffmpeg, images, output, duration, transition,
                    w, h, fps, temp_dir, callback, encoder)


def _build_fade(ffmpeg, images, output, duration, transition,
                w, h, fps, temp_dir, callback, encoder='libx264'):
    """페이드 전환 효과"""
    n = len(images)
    if callback:
        callback(f"[I] 페이드 전환 효과 적용 중...")

    inputs = []
    filters = []

    for i, img in enumerate(images):
        inputs.extend(['-loop', '1', '-t', str(duration + transition), '-i', img])
        filters.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                      f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}[v{i}]")

    if n == 1:
        filter_str = ';'.join(filters)
        map_label = '[v0]'
    else:
        xfade_parts = list(filters)
        prev = 'v0'
        for i in range(1, n):
            out_label = f'xf{i}'
            offset = max(0, duration * i - transition * i + transition * (i - 1))
            xfade_parts.append(
                f"[{prev}][v{i}]xfade=transition=fade:duration={transition}"
                f":offset={offset}[{out_label}]"
            )
            prev = out_label
        filter_str = ';'.join(xfade_parts)
        map_label = f'[{prev}]'

    cmd = [ffmpeg, '-y'] + inputs + [
        '-filter_complex', filter_str,
        '-map', map_label,
    ] + _encoder_args(encoder) + [
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
        output
    ]

    result = _run_ffmpeg(cmd)
    if result.returncode != 0:
        if callback:
            callback(f"[!] 페이드 실패, 심플 모드로 전환...")
        _build_simple(ffmpeg, images, output, duration, w, h, fps, temp_dir, callback, encoder)


def _build_simple(ffmpeg, images, output, duration, w, h, fps, temp_dir, callback, encoder='libx264'):
    """심플 모드 — 단순 이어붙이기"""
    if callback:
        callback(f"[I] 심플 모드...")

    concat_file = os.path.join(temp_dir, 'input.txt')
    with open(concat_file, 'w', encoding='utf-8') as f:
        for img in images:
            f.write(f"file '{img.replace(os.sep, '/')}'\n")
            f.write(f"duration {duration}\n")
        f.write(f"file '{images[-1].replace(os.sep, '/')}'\n")

    cmd = [
        ffmpeg, '-y',
        '-f', 'concat', '-safe', '0', '-i', concat_file,
        '-vf', f'scale={w}:{h}:force_original_aspect_ratio=decrease,'
               f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,fps={fps}',
    ] + _encoder_args(encoder) + [
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
        output
    ]

    result = _run_ffmpeg(cmd)
    if result.returncode != 0:
        # HW 인코더 실패 시 CPU 폴백
        if encoder != 'libx264':
            if callback:
                callback(f"[!] {encoder} 실패, libx264 폴백...")
            cmd = [
                ffmpeg, '-y',
                '-f', 'concat', '-safe', '0', '-i', concat_file,
                '-vf', f'scale={w}:{h}:force_original_aspect_ratio=decrease,'
                       f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,fps={fps}',
            ] + _encoder_args('libx264') + [
                '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                output
            ]
            result = _run_ffmpeg(cmd)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 오류: {result.stderr[:500]}")
