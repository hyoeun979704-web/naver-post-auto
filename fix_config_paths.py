"""config.ini의 절대경로를 현재 폴더 기준으로 자동 보정.

다른 PC로 이전 시 사용자명·드라이브·OneDrive 경로 등이 다 다르므로
'포스팅 자동화' 이전까지의 prefix를 모두 현재 BASE_DIR로 강제 치환한다.

실행: py -3.12 fix_config_paths.py
"""
import os
import re
import sys


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR_FWD = BASE_DIR.replace('\\', '/')
CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'config.ini')


def fix_paths():
    if not os.path.isfile(CONFIG_PATH):
        print(f'[!] config.ini가 없습니다: {CONFIG_PATH}')
        return False

    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        content = f.read()
    original = content

    # 1) 정방향/역방향 슬래시 모두 매칭. "포스팅 자동화"까지를 prefix로 보고 통째 치환.
    pattern = re.compile(
        r'[A-Za-z]:[/\\][^=\n\r]*?[/\\]포스팅\s*자동화(?=[/\\]|\s*$|\b)'
    )

    def _replace(m):
        # 매치된 부분 전체를 새 BASE_DIR로
        return BASE_DIR_FWD

    content = pattern.sub(_replace, content)

    # 2) 슬래시 정규화 — 모든 백슬래시를 슬래시로 (configparser는 둘 다 OK지만 일관성)
    #    단, INI key=value 라인 형태는 보존
    new_lines = []
    for line in content.splitlines():
        if '=' in line and (':' in line or '/' in line or '\\' in line):
            # 경로일 가능성 있는 줄 — \ → / 변환 (이중 백슬래시 \\n 같은 줄바꿈 메타는 건드리지 않음)
            if line.lstrip().startswith('cafe_list') or line.lstrip().startswith('account_list'):
                # 줄 단위 데이터 \\n 사용 — 그대로 유지
                new_lines.append(line)
                continue
            # = 뒤만 변환
            head, sep, tail = line.partition('=')
            if BASE_DIR_FWD in tail or 'C:/' in tail.replace('\\', '/'):
                tail = tail.replace('\\', '/')
            new_lines.append(head + sep + tail)
        else:
            new_lines.append(line)
    content = '\n'.join(new_lines)
    if not content.endswith('\n'):
        content += '\n'

    if content == original:
        print('[i] 변경할 경로 없음 — 이미 올바름')
        return True

    # 백업
    backup_path = CONFIG_PATH + '.bak'
    if not os.path.exists(backup_path):
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(original)
        print(f'[i] 백업 생성: {os.path.basename(backup_path)}')

    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f'[OK] config.ini 경로 갱신 완료')
    print(f'     BASE_DIR = {BASE_DIR_FWD}')

    # 변경 통계 — 새 BASE_DIR이 들어간 라인 수
    count = sum(1 for line in content.splitlines() if BASE_DIR_FWD in line)
    print(f'     {count}개 라인에 새 경로 적용됨')
    return True


if __name__ == '__main__':
    ok = fix_paths()
    sys.exit(0 if ok else 1)
