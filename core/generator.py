"""GPT / Claude API 기반 원고 자동 생성 모듈"""

import os
import re
import random
import shutil
import glob


POSTINGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'postings_cafe')
POSTINGS_BLOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'postings_blog')

# ── 카페 원고 프롬프트 ──
CAFE_SYSTEM_PROMPT = """당신은 네이버 카페 포스팅 원고를 작성하는 전문 작가입니다.

[원고 규칙]
1. 15~20자마다 줄바꿈 (Enter 1번)
2. 문단 사이는 빈 줄 1개 (문단 구분)
3. 총 글자수 600자 내외
4. 자연스러운 후기 형식 (경험담)
5. 이미지 위치에는 숫자만 한 줄에 작성 (0, 1, 2, ...)
6. "인용구 : 청소 전" 과 "인용구 : 청소 후" 를 적절한 위치에 포함
7. 제목 작성 규칙 — 매우 중요:
   - 한 줄로 완결, 길이 18~32자 정도. 너무 짧으면 안 됨.
   - 형태: "{키워드} + 자연스러운 후기 문구" 한 문장으로 합칠 것
   - 예시 OK: "송파구입주청소 후기 이사 전 깨끗해질 줄 몰랐어요"
   - 예시 OK: "천안입주청소 새 아파트도 꼼꼼하게 마무리한 후기"
   - 예시 NG: "송파구입주청소 후기," (짧고 콤마로 끊김 — 절대 금지)
   - 예시 NG: "송파구입주청소 후기..." (말줄임표 금지)
   - 예시 NG: "✨송파구입주청소 후기✨" (이모지 절대 금지)
   - 예시 NG: "[송파구입주청소] 후기" (대괄호 절대 금지)
   - 예시 NG: "# 송파구입주청소 후기" (# 헤더 기호 절대 금지)
   - 끝에 콤마(,) / 마침표(.) / 말줄임표(...) / 줄바꿈 절대 금지
   - 이모지·이모티콘·대괄호([]·{}·<>)·해시(#)·별표(*)·백틱(`) 등 특수문자 절대 금지
   - 본문 첫 문장과 이어지는 인상을 주지 말 것 — 제목 자체가 완결된 한 문장
8. 본문 첫 줄은 제목과 다른 내용이어야 함 (제목의 후속이 아님)

[원고 구조]
제목 : {키워드} + 자연스러운 후기 문구 (한 줄 18~32자, 끝 구두점 X)

0

도입부 텍스트 (3~5줄)

인용구 : 청소 전

1

청소 전 상태 설명 (3~5줄)

2

청소 전 상태 설명 (3~5줄)

3

업체 선택/상담 과정 (3~5줄)

인용구 : 청소 후

4

청소 후 과정 설명 (3~5줄)

5

청소 후 과정 설명 (3~5줄)

6

결과/변화 설명 (3~5줄)

7

후기/추천 (3~5줄)

8

마무리 멘트 (3~5줄)

[중요]
- 각 줄은 15~20자 이내
- 빈 줄로 문단 구분
- 숫자(0~8)는 이미지 위치
- "인용구 : " 뒤에 텍스트는 인용구 블록으로 변환됨
- 광고성 표현 자제, 자연스러운 후기 톤
- 제목: 18~32자 한 줄로 완결. {키워드} + 후기 문구를 한 문장으로 합침. 끝에 구두점 금지.
- 제목 NG 예시 — "송파구입주청소 후기," / "송파구입주청소 후기..." / "송파구입주청소 후기" 같이 너무 짧은 것
- 제목 OK 예시 — "송파구입주청소 후기 이사 전 깨끗해질 줄 몰랐어요" (키워드+후기 문구 한 줄)
- 본문 첫 줄은 제목과 다른 내용으로 시작. 제목 마지막 단어를 이어받지 말 것.
"""


# ── 포장이사 원고 프롬프트 (정보·후기 혼합 톤) ──
PACKING_SYSTEM_PROMPT = """당신은 포장이사 관련 정보·후기 글을 작성하는 전문 작가입니다.

[원고 규칙]
1. 15~20자마다 줄바꿈 (Enter 1번)
2. 문단 사이는 빈 줄 1개 (문단 구분)
3. 총 글자수: 이미지 1장당 ~80자 기준 (예: 9장이면 700~900자, 19장이면 1500~2000자)
4. 정보성 + 후기 혼합 톤 (조심해야 할 점, 견적 팁, 진행 과정 등)
5. 이미지 위치에는 숫자만 한 줄에 작성 (0, 1, 2, ...). 사용자가 user prompt에서 알려준 정확한 장수 사용
6. 작업 전/후 분리 없음 — 단일 시퀀스 흐름
7. 제목: 18~32자 한 줄. {키워드} + 후속 문구. 끝에 구두점 X. 메인키워드 1번만.
   ⚠ 이모지·이모티콘·대괄호([]·{}·<>)·해시(#)·별표(*)·백틱(`) 등 특수문자 절대 금지
   ⚠ 본문에서도 메인키워드를 [] / 【】 / 〈〉 같은 괄호로 감싸지 마라. 평문으로 사용.
       OK: "홍성입주청소를 알아보면서~"
       NG: "[홍성입주청소]를 알아보면서~"
   ⚠ <TEL:전화번호> 마커는 오직 '0' 위에 한 줄 (제목 아닌 썸네일 마커 자리)에만 사용.
       본문 중간이나 마지막 줄에 <TEL:...> 절대 넣지 마라. 전화번호는 본문에선 평문(예: "1660-0240")으로만.
8. 제목 줄 맨 끝에 <tel:1644-0199> 마커를 반드시 붙일 것 (썸네일 표시용 — 본문에는 노출 안 됨)
9. 도입부에 연락처 "1644-0199"를 자연스럽게 1회 포함 (정보 안내 톤)
10. 회사명 언급 금지 (정보성 후기). 유튜브 링크 X.

[원고 구조 — 단일 시퀀스]
제목 : {키워드} 관련 정보성 후기 제목<tel:1644-0199>

0

도입부 — 이사 결정 계기·고민 + 연락처 1644-0199 자연스럽게 1회 (4~6줄)

1

이사 준비·견적 받기 (3~5줄)

2

견적 비교 주의점 (3~5줄)

(... 사용자가 지정한 N장만큼 본문에 0 ~ (N-1) 번호 모두 사용 — 각 위치마다 포장·운반·정리 단계 설명 3~5줄 ...)

(N-1)

마무리 — 추천·당부 (3~5줄)

[중요]
- 작업 전·후 마커("청소 전/후") 사용 금지 — 포장이사는 단일 흐름
- user prompt의 "이미지 수"가 알려주는 정확한 N을 사용 (0번부터 N-1번까지 모두 본문에 등장)
- 0번이 썸네일, 나머지는 본문 흐름 순서
- 회사명·전화번호는 본문에 자연스럽게 1~2회만
- 광고 톤 X, 정보 전달·후기 혼합 톤
"""

# ── 블로그 원고 프롬프트 ──
BLOG_SYSTEM_PROMPT = """당신은 네이버 블로그 포스팅 원고를 작성하는 전문 작가입니다.

[원고 규칙]
1. 20~25자마다 줄바꿈 (Enter 1번)
2. 문단 사이는 빈 줄 1개 (문단 구분)
3. 총 글자수 800~1200자
4. 정보성 + 후기 혼합 톤 (블로그에 적합)
5. 이미지 위치에는 숫자만 한 줄에 작성 (0, 1, 2, ...)
6. 소제목을 활용하여 글 구조 명확하게 ("소제목 : 텍스트")
7. "인용구 : 텍스트" 형식으로 핵심 문장 강조

[원고 구조]
제목 : {키워드} 관련 SEO 친화적 제목 (검색 유입 고려)

0

도입부 - 상황 설명/공감 유도 (4~6줄)

소제목 : 업체 선택 과정

1

업체 탐색/비교/상담 과정 (4~6줄)

2

인용구 : 핵심 포인트 한 줄

3

소제목 : 작업 전 상태

작업 전 상태 상세 설명 (4~6줄)

4

소제목 : 작업 과정

작업 진행 과정 묘사 (4~6줄)

5

6

소제목 : 작업 후 결과

인용구 : 결과 요약 한 줄

작업 후 변화/결과 설명 (4~6줄)

7

소제목 : 총평 및 추천

만족도, 추천 이유, 꿀팁 (4~6줄)

8

마무리 인사/요약 (3~4줄)

[중요]
- 각 줄은 20~25자 이내
- "소제목 : " 뒤에 텍스트는 소제목으로 변환됨
- "인용구 : " 뒤에 텍스트는 인용구 블록으로 변환됨
- 숫자(0~8)는 이미지 위치
- 블로그 SEO를 고려한 키워드 자연 삽입
- 과도한 광고 표현 자제, 정보 제공 중심
"""

# 모델별 제공자 판별
CLAUDE_MODELS = {'claude-sonnet-4-6', 'claude-haiku-4-5'}
OPENAI_MODELS = {'gpt-4o-mini', 'gpt-4o', 'gpt-3.5-turbo'}


def _call_ai(system_prompt: str, user_prompt: str, model: str,
             openai_key: str = '', claude_key: str = '', callback=None) -> str:
    """모델명에 따라 OpenAI 또는 Claude API 호출"""

    # 변형 시드만 박음 — 페르소나 강제 X (시스템 프롬프트의 말투 규칙이 우선)
    import random as _rnd_div
    _sys_seed = _rnd_div.randint(100000, 999999)
    diversity_block = (
        f"[변형 시드: #{_sys_seed}]\n"
        f"동일 키워드라도 도입 배경·구체 예시는 매번 다르게 작성하되, "
        f"말투·조사·문장 톤은 시스템 프롬프트의 규칙을 그대로 유지하세요.\n\n"
    )
    system_prompt = diversity_block + (system_prompt or '')

    if model in CLAUDE_MODELS:
        if not claude_key:
            if callback:
                callback("[!] Claude API 키가 필요합니다")
            return ''
        if callback:
            callback(f"[I] Claude API 호출 중... (모델: {model})")
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=claude_key)
            response = client.messages.create(
                model=model,
                max_tokens=2500,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=1.0,  # 최대 다양성 (중복 회피)
            )
            return response.content[0].text.strip()
        except Exception as e:
            if callback:
                callback(f"[!] Claude API 오류: {e}")
            return ''
    else:
        if not openai_key:
            if callback:
                callback("[!] OpenAI API 키가 필요합니다")
            return ''
        if callback:
            callback(f"[I] GPT API 호출 중... (모델: {model})")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=1.0,  # 최대 다양성 (중복 회피)
                max_tokens=2500,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            if callback:
                callback(f"[!] GPT API 오류: {e}")
            return ''


# ── 카페 원고 ──

def _find_images(folder: str):
    """폴더에서 이미지 파일 목록 반환 (정렬됨)"""
    exts = ('*.jpg', '*.jpeg', '*.png', '*.webp', '*.bmp')
    images = []
    for ext in exts:
        images.extend(glob.glob(os.path.join(folder, ext)))
    images.sort()
    return images


def _parse_image_sections(manuscript: str):
    """원고를 훑어 각 숫자(이미지) 위치의 섹션을 결정.
    '인용구/소제목 : 청소 전' 또는 '작업 전' → before
    '인용구/소제목 : 청소 후' 또는 '작업 후' → after
    반환: [(num:int, section:'before'|'after'), ...]  (원고에 나온 순서)
    """
    positions = []
    section = 'before'  # 기본값: 첫 '청소/작업 후' 마커 전까지는 before
    for line in (manuscript or '').split('\n'):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('인용구 :') or stripped.startswith('인용구:') \
           or stripped.startswith('소제목 :') or stripped.startswith('소제목:'):
            label = stripped.split(':', 1)[1].strip()
            if '청소 후' in label or '작업 후' in label or '청소후' in label or '작업후' in label:
                section = 'after'
            elif '청소 전' in label or '작업 전' in label or '청소전' in label or '작업전' in label:
                section = 'before'
            continue
        if re.match(r'^\d+$', stripped):
            positions.append((int(stripped), section))
    return positions


def _move_used_to_done(used_files, callback=None):
    """사용된 이미지들을 '폴더명(사용완료)'로 이동.
    - 어떤 예외가 나도 나머지 파일 이동은 계속 시도 (전부 커버)
    - move 실패 → copy+delete 폴백 → 그것도 실패 → 원본 삭제만 시도
    """
    moved_count = 0
    for src_path, src_folder in used_files:
        try:
            if not src_folder or not os.path.exists(src_path):
                continue
            folder_name = os.path.basename(src_folder.rstrip('/\\'))
            parent_dir = os.path.dirname(src_folder.rstrip('/\\'))
            done_dir = os.path.join(parent_dir, f'{folder_name}(사용완료)')
            try:
                os.makedirs(done_dir, exist_ok=True)
            except Exception:
                pass
            dst = os.path.join(done_dir, os.path.basename(src_path))
            try:
                shutil.move(src_path, dst)
                moved_count += 1
            except Exception:
                try:
                    shutil.copy2(src_path, dst)
                    os.remove(src_path)
                    moved_count += 1
                except Exception:
                    try:
                        os.remove(src_path)
                    except Exception:
                        pass
        except Exception:
            # 개별 파일 이동 실패해도 루프는 계속
            continue
    return moved_count


def _copy_images(target_dir: str, image_count: int, callback=None,
                 thumb_dir: str = '', before_dir: str = '', after_dir: str = '',
                 before_dirs=None, after_dirs=None,
                 keyword: str = '', manuscript: str = ''):
    """원고 섹션에 맞춰 이미지 복사 — 작업전/후 1~3순위 폴더 지원.
    - 0번: 썸네일 (키워드 파일명 매칭 필수)
    - '청소 전'/'작업 전' 섹션 숫자 → 작업 전 폴더(1순위 → 2순위 → 3순위 순 소비)
    - '청소 후'/'작업 후' 섹션 숫자 → 작업 후 폴더(1순위 → 2순위 → 3순위 순 소비)
    - 섹션 폴더가 모자라면 반대 섹션 폴더로 보충
    """
    # 리스트 정규화 (단수 폴더는 길이 1짜리 리스트로 변환)
    if before_dirs is None:
        before_dirs = [before_dir] if before_dir else []
    if after_dirs is None:
        after_dirs = [after_dir] if after_dir else []
    before_dirs = [d for d in before_dirs if d and os.path.isdir(d)]
    after_dirs  = [d for d in after_dirs  if d and os.path.isdir(d)]

    positions = _parse_image_sections(manuscript) if manuscript else []

    # 썸네일: 키워드 매칭 (필수)
    matched_thumb = None
    if thumb_dir and os.path.isdir(thumb_dir):
        for src in _find_images(thumb_dir):
            name = os.path.splitext(os.path.basename(src))[0]
            if keyword and (keyword in name or name in keyword):
                matched_thumb = src
                break
        if not matched_thumb:
            if callback:
                callback(f"[!] 썸네일 매칭 실패: '{keyword}' 파일 없음 → 생성 중단")
            return False

    # 풀 구성: 우선순위대로 (src, origin_folder) 튜플 리스트
    before_pool = [(src, d) for d in before_dirs for src in _find_images(d)]
    after_pool  = [(src, d) for d in after_dirs  for src in _find_images(d)]

    used_files = []  # (원본경로, 소속폴더) — 복사 성공한 것만 append
    copy_error = None

    try:
        # positions 가 파싱 안 된 경우: 레거시 순차 복사로 폴백
        if not positions:
            idx = 0
            if matched_thumb and idx < image_count:
                try:
                    ext = os.path.splitext(matched_thumb)[1]
                    shutil.copy2(matched_thumb, os.path.join(target_dir, f"{idx}{ext}"))
                    used_files.append((matched_thumb, thumb_dir))
                    idx += 1
                except Exception as e:
                    copy_error = e
            for src, folder in before_pool:
                if idx >= image_count: break
                try:
                    ext = os.path.splitext(src)[1]
                    shutil.copy2(src, os.path.join(target_dir, f"{idx}{ext}"))
                    used_files.append((src, folder))
                    idx += 1
                except Exception as e:
                    copy_error = e
                    break
            for src, folder in after_pool:
                if idx >= image_count: break
                try:
                    ext = os.path.splitext(src)[1]
                    shutil.copy2(src, os.path.join(target_dir, f"{idx}{ext}"))
                    used_files.append((src, folder))
                    idx += 1
                except Exception as e:
                    copy_error = e
                    break
        else:
            # 섹션 기반 매핑: before/after 각각 앞에서부터 소비 (풀이 이미 우선순위 순)
            before_iter = iter(before_pool)
            after_iter = iter(after_pool)
            def take(primary, fallback):
                try: return next(primary), True
                except StopIteration:
                    try: return next(fallback), False
                    except StopIteration: return (None, ''), False

            for num, sec in positions:
                if num >= image_count:
                    continue
                src = None
                folder = ''
                if num == 0 and matched_thumb:
                    src = matched_thumb
                    folder = thumb_dir
                elif sec == 'before':
                    (picked, picked_folder), _ = take(before_iter, after_iter)
                    src, folder = picked, picked_folder
                else:  # 'after'
                    (picked, picked_folder), _ = take(after_iter, before_iter)
                    src, folder = picked, picked_folder

                if src:
                    try:
                        ext = os.path.splitext(src)[1]
                        shutil.copy2(src, os.path.join(target_dir, f"{num}{ext}"))
                        used_files.append((src, folder))
                    except Exception as e:
                        copy_error = e
                        break
    finally:
        # === 어떤 실패(예외/중단/copy 실패)가 있어도 이미 복사된 파일은 사용완료로 이동 ===
        if used_files:
            _move_used_to_done(used_files, callback=callback)

    if copy_error and callback:
        callback(f"[!] 이미지 복사 중 오류: {copy_error} (사용된 {len(used_files)}장은 사용완료로 이동 완료)")

    if callback:
        thumb_used = sum(1 for _, f in used_files if f == thumb_dir)
        before_used = sum(1 for _, f in used_files if f in before_dirs)
        after_used  = sum(1 for _, f in used_files if f in after_dirs)
        parts = []
        if thumb_used: parts.append(f"썸네일 {thumb_used}장")
        if before_used: parts.append(f"작업 전 {before_used}장")
        if after_used: parts.append(f"작업 후 {after_used}장")
        if parts:
            callback(f"[I] 이미지 복사 완료 ({' + '.join(parts)})")
    return True


def generate_manuscript(keyword: str, api_key: str = '', model: str = 'gpt-4o-mini',
                        image_count: int = 9, callback=None,
                        openai_key: str = '', claude_key: str = '',
                        custom_prompt: str = '', brand: str = '',
                        viewpoint: str = '고객', template: str = '청소') -> str:
    """카페 키워드 원고 생성

    brand: 본문에 들어갈 회사명('새집느낌'/'가족사랑클린'). 프롬프트에 명시 + 생성 후 정규화로 강제.
    viewpoint: '고객' (서비스 받은 이용자 후기) | '업체' (시공사가 직접 작성)
    template: '청소'(기본, 작업전·후 분리) | '포장이사'(단일 흐름)
    """
    if callback:
        meta = []
        if brand: meta.append(f"업체: {brand}")
        meta.append(f"관점: {viewpoint or '고객'}")
        callback(f"[I] 카페 원고 생성 시작: {keyword} ({', '.join(meta)})")

    if api_key and not openai_key:
        openai_key = api_key

    # 브랜드 정보 — 프롬프트에 주입해서 Claude가 처음부터 올바른 회사명/전화번호 사용하도록
    from .template_parser import BRAND_TEL, normalize_brand
    brand_block = ''
    is_packing = (template or '').strip() == '포장이사'
    if is_packing:
        # 포장이사 — 회사명 X, 전화번호 1644-0199 고정 (brand 무관)
        brand_block = (
            f"\n\n[포장이사 표기 규칙 — 절대 준수]\n"
            f"- 회사명 절대 언급 금지 (정보성 후기 톤)\n"
            f"- 전화번호는 오직 <tel:1644-0199> 만 사용 (다른 번호 절대 금지)\n"
            f"- 제목 끝에 <tel:1644-0199> 마커 반드시 붙임\n"
            f"- 도입부에 1644-0199 자연스럽게 1회 포함\n"
        )
    elif brand and brand in BRAND_TEL:
        others = ', '.join(b for b in BRAND_TEL if b != brand)
        brand_block = (
            f"\n\n[회사 표기 규칙 — 매우 중요]\n"
            f"- 제목에는 회사명 절대 넣지 말 것 (제목은 키워드만 사용)\n"
            f"- 본문에서 회사를 언급해야 할 때만 '{brand}' 사용 (남발 금지, 1~2회 정도)\n"
            f"- 전화번호 표기: <TEL:{BRAND_TEL[brand]}>\n"
            f"- 다른 회사명({others}) 절대 언급 금지\n"
        )

    # 작성 관점 — '고객'(후기 톤) 또는 '업체'(시공사 소개 톤)
    vp = (viewpoint or '고객').strip()
    brand_token = brand if (brand and brand in BRAND_TEL) else '저희 업체'
    if is_packing:
        # 포장이사 — brand 무관, 정보·후기 혼합 톤
        viewpoint_block = (
            f"\n\n[작성 관점 — 포장이사]\n"
            f"- 정보·후기 혼합 톤. 1인칭 \"저\" 사용 (직접 이사한 경험 후기)\n"
            f"- 회사명 절대 언급 금지. \"업체\" / \"이사 업체\" 정도로만 일반 지칭\n"
            f"- '인용구 : 청소 전' / '인용구 : 청소 후' 마커 사용 금지 — 단일 시퀀스\n"
        )
    elif vp == '업체':
        viewpoint_block = (
            f"\n\n[작성 관점 — 반드시 준수]\n"
            f"- 업체 관점: 시공사가 자기 작업을 소개하는 톤\n"
            f"- 1인칭 \"저희\" 사용 (예: \"저희 {brand_token}에서 시공한~\", \"저희가 작업을 진행~\")\n"
            f"- 고객을 \"고객님\"으로 지칭\n"
            f"- 후기/경험담 톤 금지. 시공 사례 소개·비포애프터 설명 톤으로\n"
            f"- '인용구 : 청소 전' / '인용구 : 청소 후' 마커는 그대로 유지\n"
        )
    else:
        viewpoint_block = (
            f"\n\n[작성 관점 — 반드시 준수]\n"
            f"- 고객 관점: 서비스를 직접 받아본 이용자의 후기 톤\n"
            f"- 1인칭 \"저\" 사용 (예: \"제가 직접 받아봤는데~\", \"이용해 보니~\")\n"
            f"- 업체를 \"{brand_token}\"으로 지칭 (3인칭)\n"
            f"- 시공사 소개 톤 금지. 솔직한 경험담·추천 톤으로\n"
        )

    # 변형 시드만 — 도입 배경·예시는 다르게, 말투는 시스템 규칙 유지
    import random as _rnd
    _variation_seed = _rnd.randint(10000, 99999)
    user_prompt = f"""키워드: {keyword}
이미지 수: {image_count}개 (0번부터 {image_count - 1}번까지)

[변형 시드 #{_variation_seed}] ← 이 글은 #{_variation_seed} 버전. 도입 배경·구체 예시·세부 표현이 이전 글과 겹치지 않게 작성. (말투/톤은 시스템 프롬프트 규칙 그대로 유지){brand_block}{viewpoint_block}

위 키워드로 네이버 카페 포스팅 원고를 작성해주세요.
규칙을 반드시 지켜주세요."""

    # 템플릿별 시스템 프롬프트 분기 (청소·포장이사)
    if custom_prompt and custom_prompt.strip():
        system = custom_prompt.strip()
    elif (template or '').strip() == '포장이사':
        system = PACKING_SYSTEM_PROMPT
    else:
        system = CAFE_SYSTEM_PROMPT
    result = _call_ai(system, user_prompt, model,
                      openai_key=openai_key, claude_key=claude_key, callback=callback)

    # 안전망 1: AI가 다른 브랜드를 섞었어도 강제 치환
    if result and brand:
        result = normalize_brand(result, brand)

    # 안전망 2: 제목줄에 회사명이 박혔으면 제거 (사용자 요구: 제목에는 키워드만)
    if result and brand:
        lines = result.split('\n')
        for i, line in enumerate(lines):
            s = line.strip()
            if s.startswith('제목'):
                cleaned = re.sub(rf'\s*{re.escape(brand)}\s*', ' ', line)
                cleaned = re.sub(r' {2,}', ' ', cleaned).rstrip()
                lines[i] = cleaned
                break
        result = '\n'.join(lines)

    # 안전망 3: 제목 끝 미완결 구두점 제거 (콤마/마침표/말줄임표) — "송파구입주청소 후기," 같은 잘린 제목 방지
    if result:
        lines = result.split('\n')
        for i, line in enumerate(lines):
            s = line.strip()
            if s.startswith('제목'):
                # "제목 :" 또는 "제목:" 분리
                m = re.match(r'^(\s*제목\s*:)(.*)$', line)
                if m:
                    head, val = m.group(1), m.group(2).rstrip()
                    # 끝의 , . … ... 반복 제거
                    val = re.sub(r'[\s,.]*(\.{3}|…)[\s,.]*$', '', val)
                    val = re.sub(r'[,\.]+\s*$', '', val).rstrip()
                    lines[i] = head + ' ' + val.lstrip()
                break
        result = '\n'.join(lines)

    # AI 가 본문 뒤에 붙이는 분석/검토 섹션 제거 (대괄호 키워드 조합 / 글자수 표기 등)
    if result:
        result = _strip_trailing_analysis(result)
    # AI 가 프롬프트 스켈레톤 안내 텍스트(예: '(전환 문단)', '(도입 문단 1 — 3~5줄)')를
    # 그대로 흘린 경우 제거
    if result:
        result = _strip_skeleton_placeholders_inline(result)
    # 메인 키워드를 [] / 【】 / 〈〉로 감싼 케이스 정리 (라벨처럼 따라 한 경우)
    if result:
        result = _strip_keyword_brackets_inline(result)

    if result and callback:
        callback(f"[I] 원고 생성 완료 ({len(result)}자)")

    return result


def _strip_keyword_brackets_inline(txt: str) -> str:
    """본문에 들어간 '[키워드]' / '【키워드】' / '〈키워드〉' 등 괄호 래핑 제거.
    제목·썸네일 마커({키워드}<TEL:>) 줄은 손대지 않고 본문에서만 정리.
    + 본문에 흘러간 <TEL:...> 마커도 함께 제거 (썸네일 마커가 아닌 경우)."""
    if not txt:
        return txt
    import re as _re
    out_lines = []
    # 썸네일 마커 식별 패턴 — '{한글키워드}<TEL:...>' 형태 (앞에 텍스트 있음)
    thumb_marker_re = _re.compile(r'^.+?<tel:[^>]+>\s*$', _re.IGNORECASE)
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
        # 본문 — 우선 <TEL:...>, <tel:...> 단독/인라인 마커 제거
        new = _re.sub(r'\s*<\s*tel\s*:[^>]+>\s*', '', line, flags=_re.IGNORECASE)
        # 키워드 괄호 정리
        new = _re.sub(r'\[([^\[\]]*[가-힣][^\[\]]*?)\]', r'\1', new)
        new = _re.sub(r'【([^【】]*[가-힣][^【】]*?)】', r'\1', new)
        new = _re.sub(r'〈([^〈〉]*[가-힣][^〈〉]*?)〉', r'\1', new)
        out_lines.append(new)
    # 마커 제거로 빈 줄이 된 경우 정리 — 연속 빈 줄 2칸으로 압축
    result = '\n'.join(out_lines)
    result = _re.sub(r'\n{3,}', '\n\n', result)
    return result


def _strip_skeleton_placeholders_inline(txt: str) -> str:
    """카페 원고에서 AI 가 흘린 프롬프트 안내 placeholder 제거.
    blog_generator._strip_skeleton_placeholders 와 동일 로직."""
    if not txt:
        return txt
    import re as _re
    line_re = _re.compile(
        r'^\s*\([^()]*?(문단|문장|소제목|넘버링|줄)[^()]*?\)\s*$'
    )
    inline_re = _re.compile(
        r'\([^()]*?(?:—|-)\s*\d+\s*~?\s*\d*\s*줄[^()]*?\)'
    )
    label_only_re = _re.compile(
        r'\(\s*[가-힣]+\s*(?:문단|문장|소제목)(?:\s*\d+)?\s*\)'
    )
    out_lines = []
    for line in txt.split('\n'):
        s = line.strip()
        if not s:
            out_lines.append(line)
            continue
        if line_re.match(line):
            continue
        if label_only_re.fullmatch(s):
            continue
        cleaned = inline_re.sub('', line)
        cleaned = label_only_re.sub('', cleaned)
        out_lines.append(cleaned)
    result = '\n'.join(out_lines)
    result = _re.sub(r'\n{3,}', '\n\n', result)
    return result.rstrip() + '\n'


def _strip_trailing_analysis(txt: str) -> str:
    """원고 끝에 AI 가 덧붙인 분석 섹션을 제거.

    대상 마커 (이 줄 + 이후 모두 잘라냄):
    - '---' 단독 줄 (본문 종료 후 구분선)
    - '**[대괄호 키워드' / '[대괄호 키워드'
    - '**[글자수' / '[글자수'
    - '**[키워드' / '[키워드' (조합 검토)
    """
    if not txt:
        return txt
    lines = txt.split('\n')
    cut_at = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s == '---':
            # 다음 줄에 분석 마커가 오면 이 줄부터 컷
            if i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if (nxt.startswith('**[') or nxt.startswith('[')) and \
                   any(k in nxt for k in ('대괄호', '글자수', '키워드 앞뒤', '조합 검토')):
                    cut_at = i
                    break
        if (s.startswith('**[') or s.startswith('[')) and \
           any(k in s for k in ('대괄호 키워드', '글자수:', '글자수 :', '키워드 앞뒤', '조합 검토')):
            cut_at = i
            break
    if cut_at is not None:
        return '\n'.join(lines[:cut_at]).rstrip() + '\n'
    return txt


def save_manuscript(keyword: str, manuscript: str, callback=None,
                    image_count: int = 9,
                    thumb_dir: str = '', before_dir: str = '', after_dir: str = '',
                    before_dirs=None, after_dirs=None) -> str:
    """카페 원고를 postings/ 폴더에 저장 + 이미지 자동 복사 (작업전/후 1~3순위 지원)"""
    folder_name = re.sub(r'[^\w가-힣]', '_', keyword).strip('_')
    folder_path = os.path.join(POSTINGS_DIR, folder_name)
    image_dir = os.path.join(folder_path, 'image')

    os.makedirs(image_dir, exist_ok=True)

    manuscript_path = os.path.join(folder_path, '원고.txt')
    with open(manuscript_path, 'w', encoding='utf-8') as f:
        f.write(manuscript)

    has_folders = bool(thumb_dir or before_dir or after_dir or before_dirs or after_dirs)
    if has_folders:
        ok = _copy_images(image_dir, image_count, callback,
                          thumb_dir=thumb_dir,
                          before_dir=before_dir, after_dir=after_dir,
                          before_dirs=before_dirs, after_dirs=after_dirs,
                          keyword=keyword, manuscript=manuscript)
        if ok is False:
            shutil.rmtree(folder_path, ignore_errors=True)
            return ''

    if callback:
        callback(f"[I] 원고 저장: {folder_path}")
    return folder_path


def generate_and_save(keyword: str, api_key: str = '', model: str = 'gpt-4o-mini',
                      image_count: int = 9, callback=None,
                      openai_key: str = '', claude_key: str = '',
                      custom_prompt: str = '',
                      thumb_dir: str = '', before_dir: str = '', after_dir: str = '',
                      before_dirs=None, after_dirs=None, brand: str = '',
                      viewpoint: str = '고객', mode: str = 'default',
                      template: str = '청소', packing_dir: str = '') -> str:
    """카페 원고 생성 + 저장 통합 (작업전/후 1~5순위 지원)

    brand: 본문에 들어갈 회사명. generate_manuscript에 그대로 전달.
    viewpoint: '고객'(후기) | '업체'(시공사 소개)
    mode: 'default' (기존 후기 원고) | 'cardnews' (5장 정보성 카드뉴스)
    template: '청소' | '포장이사'
    packing_dir: '포장이사' 템플릿일 때 단일 사진 폴더 (1.png, 2.png...)
    """
    if api_key and not openai_key:
        openai_key = api_key

    # ── 카드뉴스 모드 ──
    if mode == 'cardnews':
        return _generate_and_save_cardnews(
            keyword, model=model, callback=callback,
            openai_key=openai_key, claude_key=claude_key,
        )

    # ── 포장이사 모드 ──
    if (template or '').strip() == '포장이사':
        manuscript = generate_manuscript(keyword, model=model, image_count=image_count,
                                         callback=callback,
                                         openai_key=openai_key, claude_key=claude_key,
                                         custom_prompt=custom_prompt, brand=brand,
                                         viewpoint=viewpoint, template='포장이사')
        if not manuscript:
            return ''
        return _save_manuscript_packing(keyword, manuscript, image_count,
                                        packing_dir=packing_dir, callback=callback)

    # ── 기본 후기 원고 (청소) ──
    manuscript = generate_manuscript(keyword, model=model, image_count=image_count,
                                     callback=callback,
                                     openai_key=openai_key, claude_key=claude_key,
                                     custom_prompt=custom_prompt, brand=brand,
                                     viewpoint=viewpoint, template=template)
    if manuscript:
        folder_path = save_manuscript(keyword, manuscript, callback,
                                      image_count=image_count,
                                      thumb_dir=thumb_dir,
                                      before_dir=before_dir, after_dir=after_dir,
                                      before_dirs=before_dirs, after_dirs=after_dirs)
        has_folders = bool(thumb_dir or before_dir or after_dir or before_dirs or after_dirs)
        if callback:
            if has_folders:
                callback(f"[I] 완료! 원고+이미지 저장됨")
            else:
                callback(f"[I] 완료! 이미지를 {folder_path}/image/ 에 넣어주세요")
        return folder_path
    return ''


def _save_manuscript_packing(keyword: str, manuscript: str, image_count: int,
                             packing_dir: str = '', callback=None) -> str:
    """포장이사 원고 저장 — 단일 폴더에서 사진 순서대로 0~N.jpg 복사."""
    folder_name = re.sub(r'[^\w가-힣]', '_', keyword).strip('_')
    folder_path = os.path.join(POSTINGS_DIR, folder_name)
    os.makedirs(folder_path, exist_ok=True)

    manuscript_path = os.path.join(folder_path, '원고.txt')
    with open(manuscript_path, 'w', encoding='utf-8') as f:
        f.write(manuscript)
    if callback:
        callback(f"[I] 포장이사 원고 저장: {folder_path}")

    # 사진 폴더에서 순서대로 image_count장 복사
    if packing_dir and os.path.isdir(packing_dir):
        exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
        files = sorted([f for f in os.listdir(packing_dir)
                        if f.lower().endswith(exts) and os.path.isfile(os.path.join(packing_dir, f))])
        if not files:
            if callback:
                callback(f"[!] 포장이사 사진 폴더 비어있음: {packing_dir}")
            return folder_path

        used = []
        idx = 0
        for src_name in files:
            if idx >= image_count:
                break
            src = os.path.join(packing_dir, src_name)
            ext = os.path.splitext(src_name)[1]
            dst = os.path.join(folder_path, f'{idx}{ext}')
            try:
                shutil.copy2(src, dst)
                used.append(src)
                idx += 1
            except Exception as e:
                if callback:
                    callback(f"[!] 사진 복사 실패: {src_name} ({e})")

        if callback:
            callback(f"[I] 포장이사 사진 {idx}장 복사 완료 (0~{idx-1}.jpg)")

        # 사용한 사진을 (사용완료) 폴더로 이동
        if used:
            done_dir = packing_dir.rstrip('/\\') + '(사용완료)'
            try:
                os.makedirs(done_dir, exist_ok=True)
                for src in used:
                    base = os.path.basename(src)
                    dst = os.path.join(done_dir, base)
                    if os.path.exists(dst):
                        # 중복 — 타임스탬프 붙임
                        import time
                        ts = int(time.time())
                        dst = os.path.join(done_dir, f'{ts}_{base}')
                    try:
                        shutil.move(src, dst)
                    except Exception:
                        try:
                            os.remove(src)
                        except Exception:
                            pass
                if callback:
                    callback(f"[I] 사용한 사진 {len(used)}장 → (사용완료) 이동")
            except Exception as e:
                if callback:
                    callback(f"[!] 사용완료 이동 실패: {e}")
    else:
        if callback:
            callback(f"[!] 포장이사 사진 폴더 미설정 — 사진 복사 스킵")

    return folder_path


# ============================================================
# 카드뉴스(정보성) 모드 — 5장 슬라이드 + 짧은 설명
# ============================================================

CARDNEWS_SYSTEM_PROMPT = """당신은 정보성 카드뉴스 콘텐츠 작가입니다.

[규칙]
- 정확히 5개의 슬라이드를 만든다
- 메인제목: 카페 게시글 제목으로 들어갈 한 줄 (18~32자)
  · 형식: "{메인키워드} {세부키워드1} {세부키워드2} ..."
  · 메인키워드는 정확히 1번만 등장 (절대 2번 이상 금지)
  · 세부키워드끼리도 서로 중복 금지 (모두 다른 단어)
  · 끝에 콤마/마침표/말줄임표 금지
- 각 슬라이드:
  · 제목: 15자 이내 (핵심 한 문장, 명사형) — 메인키워드 포함하지 말 것
  · 본문: 60자 이내 (1~2문장, 평서체) — 슬라이드 이미지에 박힐 짧은 텍스트
  · 설명: 카페 글 본문에 들어갈 풍부한 설명 (180~220자, 자연스러운 단락) — 슬라이드 본문을 풀어쓴 친절한 문장으로
- 도입: 5개 슬라이드 시작 전 카페 글 도입 문단 (120~160자, 인사·주제 소개)
- 광고/홍보 톤 금지. 정보 전달 톤. 친근한 후기·정보성 혼합 가능
- 회사명·전화번호 절대 포함하지 말 것
- 각 슬라이드는 서로 다른 주제 (중복 금지)
- 전체 합계: 도입 + 5×설명 = 최소 1000자 이상

[메인제목 OK 예시 — 키워드 "입주청소 창업"]
"입주청소 창업 비용 절차 노하우 한눈에"
"입주청소 창업 준비 자격 시장 정보 정리"

[메인제목 NG 예시 — 절대 이렇게 만들지 말 것]
"입주청소 창업 입주청소 창업의 특징"  (메인키워드 2번)
"입주청소 창업 후기 후기"  (세부키워드 중복)

[출력 형식 — 정확히 이 구조로 출력. 다른 텍스트 절대 추가하지 말 것]
메인제목: ...

도입: ...

슬라이드 1
제목: ...
본문: ...
설명: ...

슬라이드 2
제목: ...
본문: ...
설명: ...

슬라이드 3
제목: ...
본문: ...
설명: ...

슬라이드 4
제목: ...
본문: ...
설명: ...

슬라이드 5
제목: ...
본문: ...
설명: ...
"""


def _split_to_paragraphs(text: str, sentences_per_para: int = 2) -> list:
    """긴 텍스트를 문장 종결부호 기준으로 자른 뒤 1~2문장씩 묶어 단락 리스트 반환.
    가독성을 위해 카페 본문 조립 시 단락 사이를 빈 줄로 띄움.
    문장 묶음 크기는 1~2 사이에서 가변(자연스러운 단조롭지 않은 단락 길이)."""
    if not text:
        return []
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [p.strip() for p in parts if p.strip()]
    paragraphs = []
    rng = random.Random()
    i = 0
    while i < len(sentences):
        # 마지막 한 문장만 남으면 그대로, 아니면 1 또는 2개 랜덤 (2가 더 자주)
        if i + 1 >= len(sentences):
            n = 1
        else:
            n = rng.choice([1, 2, 2])
        chunk = ' '.join(sentences[i:i + n]).strip()
        if chunk:
            paragraphs.append(chunk)
        i += n
    return paragraphs


def _parse_cardnews_response(text: str) -> tuple:
    """Claude 응답을 (main_title, intro, [{title, body, desc}, ...]) 튜플로 파싱.

    main_title: 카페 게시글 제목 한 줄 (없으면 빈 문자열)
    intro: 카페 글 도입 문단 (없으면 빈 문자열)
    slides: 5개 슬라이드 dict 리스트
    """
    main_title = ''
    intro = ''
    slides = []
    cur = {}
    for raw in (text or '').split('\n'):
        line = raw.strip()
        if not line:
            continue
        # 메인제목
        m_main = re.match(r'^메인제목\s*[:：]\s*(.+)$', line)
        if m_main and not main_title and not slides and not cur:
            main_title = m_main.group(1).strip()
            continue
        # 도입 문단
        m_intro = re.match(r'^도입\s*[:：]\s*(.+)$', line)
        if m_intro and not intro and not slides and not cur:
            intro = m_intro.group(1).strip()
            continue
        if line.startswith('슬라이드'):
            if cur:
                slides.append(cur)
            cur = {}
            continue
        m = re.match(r'^(제목|본문|설명)\s*[:：]\s*(.+)$', line)
        if m:
            key_map = {'제목': 'title', '본문': 'body', '설명': 'desc'}
            cur[key_map[m.group(1)]] = m.group(2).strip()
    if cur:
        slides.append(cur)
    # 빈 항목 채움
    for s in slides:
        s.setdefault('title', '')
        s.setdefault('body', '')
        s.setdefault('desc', '')
    return main_title, intro, slides


def _dedupe_title(title: str, keyword: str) -> str:
    """제목에서 메인 키워드가 2회 이상 등장하면 첫 번째만 남기고 제거.
    또한 제목 끝 콤마/마침표/말줄임표 정리."""
    if not title:
        return title
    if not keyword:
        # 끝 구두점만 정리
        title = re.sub(r'[\s,.]*(\.{3}|…)[\s,.]*$', '', title)
        title = re.sub(r'[,\.]+\s*$', '', title).rstrip()
        return title
    # 키워드가 2회 이상 등장하면 두 번째 이후 제거
    kw_pattern = re.escape(keyword)
    matches = list(re.finditer(kw_pattern, title))
    if len(matches) > 1:
        # 첫 번째는 유지, 나머지는 제거
        for m in reversed(matches[1:]):
            title = title[:m.start()] + title[m.end():]
        # 공백 정리
        title = re.sub(r'\s{2,}', ' ', title).strip()
    # 끝 구두점 정리
    title = re.sub(r'[\s,.]*(\.{3}|…)[\s,.]*$', '', title)
    title = re.sub(r'[,\.]+\s*$', '', title).rstrip()
    return title


def _generate_and_save_cardnews(keyword: str, model: str = 'claude-sonnet-4-6',
                                callback=None,
                                openai_key: str = '', claude_key: str = '') -> str:
    """카드뉴스 모드 — 키워드 → 5장 슬라이드 + 원고.txt 저장.

    저장 결과:
      postings_cafe/{keyword}/원고.txt   (제목 + 0~4 마커 + 한 줄 설명)
      postings_cafe/{keyword}/0.jpg ~ 4.jpg  (카드뉴스 슬라이드 PNG)
    """
    if callback:
        callback(f"[I] 카드뉴스 원고 생성 시작: {keyword}")

    import random as _rnd
    _variation_seed = _rnd.randint(10000, 99999)
    user_prompt = f"""키워드: {keyword}
[변형 시드 #{_variation_seed}] ← 이 카드뉴스는 #{_variation_seed} 버전. 이전 카드뉴스와 슬라이드 구성·표현·예시를 완전히 다르게 작성.

위 키워드 관련 정보성 카드뉴스 5장을 만들어주세요. 광고가 아닌 정보 전달 톤으로 작성하고, 형식을 정확히 지켜주세요."""

    raw = _call_ai(CARDNEWS_SYSTEM_PROMPT, user_prompt, model,
                   openai_key=openai_key, claude_key=claude_key, callback=callback)
    if not raw:
        if callback:
            callback("[!] 카드뉴스 생성 실패: AI 응답 없음")
        return ''

    main_title, intro, slides = _parse_cardnews_response(raw)
    if len(slides) < 5:
        # 부족분 채움 (안전망)
        if callback:
            callback(f"[!] 슬라이드 {len(slides)}장만 파싱됨 — 부족분 빈 값으로 채움")
        while len(slides) < 5:
            slides.append({'title': keyword, 'body': '', 'desc': ''})
    slides = slides[:5]

    # 저장 폴더
    folder_name = re.sub(r'[^\w가-힣]', '_', keyword).strip('_')
    folder_path = os.path.join(POSTINGS_DIR, folder_name)
    os.makedirs(folder_path, exist_ok=True)

    # 1) 슬라이드 이미지 5장 생성 (0.jpg ~ 4.jpg)
    try:
        from .thumbnail import create_cardnews_slides
        create_cardnews_slides(folder_path, slides)
        if callback:
            callback(f"[I] 카드뉴스 슬라이드 5장 생성 완료")
    except Exception as e:
        if callback:
            callback(f"[!] 카드뉴스 슬라이드 생성 실패: {e}")
        return ''

    # 2) 원고.txt 저장 — 카페 발행 형식
    #    제목: Claude가 생성한 메인제목 (메인키워드 1번 + 세부키워드 중복 없음)
    #    구조: [제목] [도입 단락들] [0 썸네일] [슬라이드1 설명 단락들] [1] [슬라이드2 설명 단락들] ...
    #    가독성을 위해 도입·설명을 1~2문장 단위로 단락 분할 + 단락 사이 빈 줄.
    if main_title:
        title_line = _dedupe_title(main_title, keyword)
    else:
        # fallback: 키워드 + 첫 슬라이드 제목 (Claude가 메인제목 안 줬을 때)
        fallback_title = slides[0].get('title') or ''
        title_line = _dedupe_title(f"{keyword} {fallback_title}".strip(), keyword)
    lines = [f"제목 : {title_line}"]
    lines.append('')

    def _append_paragraphs(text):
        """텍스트를 1~2문장 단락으로 분할해 lines에 append.
        단락 사이는 빈 줄 2개(=카페에서 두 칸 띄움)로 구분."""
        paras = _split_to_paragraphs(text, sentences_per_para=2)
        for j, para in enumerate(paras):
            lines.append(para)
            lines.append('')
            lines.append('')  # 단락 사이 빈 줄 2개 — 사용자 요청

    # 도입 문단 (있으면)
    if intro:
        _append_paragraphs(intro)

    lines.append('0')  # 첫 슬라이드 = 썸네일
    lines.append('')
    if slides[0].get('desc'):
        _append_paragraphs(slides[0]['desc'])

    for i in range(1, 5):
        lines.append(str(i))
        lines.append('')
        if slides[i].get('desc'):
            _append_paragraphs(slides[i]['desc'])

    manuscript = '\n'.join(lines).rstrip() + '\n'

    manuscript_path = os.path.join(folder_path, '원고.txt')
    with open(manuscript_path, 'w', encoding='utf-8') as f:
        f.write(manuscript)

    # 글자 수 (제목·이미지 마커 제외, 텍스트만)
    text_only = (intro or '') + ''.join(s.get('desc', '') for s in slides)
    char_count = len(text_only.replace(' ', ''))
    if callback:
        callback(f"[I] 카드뉴스 원고 저장: {folder_path} (본문 {char_count}자)")
    return folder_path


# ── 블로그 원고 ──

def generate_blog_manuscript(keyword: str, model: str = 'gpt-4o-mini',
                             image_count: int = 9, callback=None,
                             openai_key: str = '', claude_key: str = '',
                             custom_prompt: str = '') -> str:
    """블로그 키워드 원고 생성"""
    if callback:
        callback(f"[I] 블로그 원고 생성 시작: {keyword}")

    import random as _rnd
    _variation_seed = _rnd.randint(10000, 99999)
    user_prompt = f"""키워드: {keyword}
이미지 수: {image_count}개 (0번부터 {image_count - 1}번까지)
[변형 시드 #{_variation_seed}] ← 이 글은 #{_variation_seed} 버전. 이전 글과 도입·표현·문장 구조·예시를 완전히 다르게 작성할 것.

위 키워드로 네이버 블로그 포스팅 원고를 작성해주세요.
규칙을 반드시 지켜주세요."""

    system = custom_prompt.strip() if custom_prompt and custom_prompt.strip() else BLOG_SYSTEM_PROMPT
    result = _call_ai(system, user_prompt, model,
                      openai_key=openai_key, claude_key=claude_key, callback=callback)

    if result and callback:
        callback(f"[I] 블로그 원고 생성 완료 ({len(result)}자)")

    return result


def save_blog_manuscript(keyword: str, manuscript: str, callback=None,
                         image_count: int = 9,
                         thumb_dir: str = '', before_dir: str = '', after_dir: str = '',
                         before_dirs=None, after_dirs=None) -> str:
    """블로그 원고를 postings_blog/ 폴더에 저장 + 이미지 자동 복사 (작업전/후 1~3순위 지원)"""
    folder_name = re.sub(r'[^\w가-힣]', '_', keyword).strip('_')
    folder_path = os.path.join(POSTINGS_BLOG_DIR, folder_name)
    image_dir = os.path.join(folder_path, 'image')

    os.makedirs(image_dir, exist_ok=True)

    manuscript_path = os.path.join(folder_path, '원고.txt')
    with open(manuscript_path, 'w', encoding='utf-8') as f:
        f.write(manuscript)

    has_folders = bool(thumb_dir or before_dir or after_dir or before_dirs or after_dirs)
    if has_folders:
        ok = _copy_images(image_dir, image_count, callback,
                          thumb_dir=thumb_dir,
                          before_dir=before_dir, after_dir=after_dir,
                          before_dirs=before_dirs, after_dirs=after_dirs,
                          keyword=keyword, manuscript=manuscript)
        if ok is False:
            shutil.rmtree(folder_path, ignore_errors=True)
            return ''

    if callback:
        callback(f"[I] 블로그 원고 저장: {folder_path}")
    return folder_path


def generate_and_save_blog(keyword: str, model: str = 'gpt-4o-mini',
                           image_count: int = 9, callback=None,
                           openai_key: str = '', claude_key: str = '',
                           custom_prompt: str = '',
                           thumb_dir: str = '', before_dir: str = '', after_dir: str = '',
                           before_dirs=None, after_dirs=None) -> str:
    """블로그 원고 생성 + 저장 통합 (작업전/후 1~3순위 지원)"""
    manuscript = generate_blog_manuscript(keyword, model=model, image_count=image_count,
                                          callback=callback,
                                          openai_key=openai_key, claude_key=claude_key,
                                          custom_prompt=custom_prompt)
    if manuscript:
        folder_path = save_blog_manuscript(keyword, manuscript, callback,
                                           image_count=image_count,
                                           thumb_dir=thumb_dir,
                                           before_dir=before_dir, after_dir=after_dir,
                                           before_dirs=before_dirs, after_dirs=after_dirs)
        has_folders = bool(thumb_dir or before_dir or after_dir or before_dirs or after_dirs)
        if callback:
            if has_folders:
                callback(f"[I] 완료! 원고+이미지 저장됨")
            else:
                callback(f"[I] 완료! 이미지를 {folder_path}/image/ 에 넣어주세요")
        return folder_path
    return ''
