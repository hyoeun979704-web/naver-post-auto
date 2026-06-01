==============================================
  N통합 발행기 — 새 PC 설치 가이드
==============================================

[ 동작 환경 ]

  * Windows 전용 (Windows 10/11)
  * Python 3.12
  → PowerShell·ctypes 등 Windows API에 의존하므로 macOS/Linux에서는 동작하지 않습니다.


[ 처음 사용 시 — 1번만 ]

  install.bat 더블클릭

  → 자동으로 다음을 설치합니다:
     1. Python 3.12 (없으면 자동 다운로드 + 설치)
     2. 필요한 Python 패키지 (PySide6, playwright 등)
     3. Chromium 브라우저 (Playwright)
     4. config.ini 경로 자동 보정

  소요 시간: 5~15분
  필요한 것: 인터넷 연결만


[ 매일 사용 ]

  N통합발행기.pyw 더블클릭


[ 자주 묻는 문제 ]

Q. install.bat 실행했더니 한글이 깨져 보여요
A. 정상입니다. install.bat은 영어로만 표시됩니다 (한글 인코딩 호환성 문제 회피).
   끝까지 진행되면 OK.

Q. "No runtime installed that matches 3.12" 빨간 에러가 떠요
A. Windows의 'py' launcher가 Microsoft Store로 리다이렉트돼 생기는 문제입니다.
   최신 install.bat은 'py' launcher 대신 python.exe 절대경로를 직접 사용해서
   이 문제를 회피합니다. 이 메시지가 보이면 install.bat 다시 받아서 실행해주세요.

Q. .pyw 더블클릭해도 아무 반응이 없어요
A. 의존성이 없어서 그런 경우가 많습니다. install.bat을 다시 실행해주세요.
   .pyw가 설치 안내 메시지 박스를 띄워주는데, 그것도 안 뜨면 의존성 부족.

Q. Python 자동 설치가 실패해요
A. 인터넷 차단 또는 권한 문제. install.bat을 마우스 우클릭 → "관리자 권한으로 실행".
   그래도 안 되면 수동:
     1. https://www.python.org/downloads/release/python-3127/ 에서 직접 설치
        (※ "Add Python 3.12 to PATH" 체크 필수)
     2. install.bat 다시 실행

Q. config.ini 경로가 이상해요 (이전 PC 경로가 박혀있음)
A. 폴더 안에서:  py -3.12 fix_config_paths.py
   → 현재 폴더 기준으로 자동 보정됨 (백업 .bak 파일 생성)


[ 폴더 구성 ]

- N통합발행기.pyw          ← 실행 파일 (더블클릭)
- install.bat               ← 자동 설치 스크립트 (영어 표시)
- requirements.txt          ← Python 패키지 목록
- fix_config_paths.py       ← config.ini 경로 자동 보정 도구
- cafe_poster_qt.py         ← 메인 앱 (PySide6)
- core/                     ← 발행/생성/썸네일 모듈
- config/config.ini         ← 설정 + API 키 + 폴더 경로
- 사진/                     ← 작업 전·후, 썸네일 이미지
- 원고/                     ← 블로그 원고
- postings_cafe/            ← 카페 발행 대기 원고
- postings_blog/            ← 블로그 발행 대기 원고

==============================================
