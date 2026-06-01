"""Playwright 브라우저 제어 모듈 - 자동화 감지 우회 + 로그인/쿠키 관리"""

import json
import os
import time
import random
from playwright.sync_api import sync_playwright, BrowserContext, Page


# 여러 인스턴스 동시 실행 지원: PID 기반 고유 경로
_PID = os.getpid()
COOKIES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cookies', f'naver_cookies_{_PID}.json')
PROFILE_DIR = f'C:\\CafePoster_Profile_{_PID}'


def _find_chrome_path():
    paths = [
        os.path.expandvars(r'%ProgramFiles%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%LocalAppData%\Google\Chrome\Application\chrome.exe'),
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None


# 스텔스 스크립트 — 모든 항목을 try/catch로 감싸서 실제 크롬에서도 에러 없음
STEALTH_SCRIPT = """
(() => {
    // webdriver 제거 (핵심)
    try {
        const p = navigator.__proto__;
        delete p.webdriver;
        navigator.__proto__ = p;
    } catch(e) {}
    try {
        Object.defineProperty(navigator, 'webdriver', { get: () => false, configurable: true });
    } catch(e) {}

    // 자동화 DOM 속성 제거
    ['__webdriver_evaluate','__selenium_evaluate','__fxdriver_evaluate',
     '__driver_evaluate','__webdriver_unwrap','__selenium_unwrap',
     '__fxdriver_unwrap','__driver_unwrap','_Selenium_IDE_Recorder',
     '_selenium','calledSelenium','__nightmare','__phantomas',
     'domAutomation','domAutomationController'
    ].forEach(p => { try { delete window[p]; } catch(e) {} });

    // chrome 객체 보강 (없을 때만)
    try {
        if (!window.chrome) {
            window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
        }
        if (!window.chrome.runtime) window.chrome.runtime = {};
    } catch(e) {}

    // Permissions API
    try {
        const oq = window.navigator.permissions.query;
        window.navigator.permissions.__proto__.query = p =>
            p.name === 'notifications' ? Promise.resolve({state:Notification.permission}) : oq(p);
    } catch(e) {}

    // iframe webdriver 제거
    try {
        const og = HTMLIFrameElement.prototype.__lookupGetter__('contentWindow');
        if (og) {
            Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
                get: function() {
                    const r = og.call(this);
                    try { Object.defineProperty(r.navigator,'webdriver',{get:()=>false}); } catch(e){}
                    return r;
                }
            });
        }
    } catch(e) {}
})();
"""


class NaverBrowser:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context: BrowserContext = None
        self._page: Page = None

    def _ensure_cookies_dir(self):
        os.makedirs(os.path.dirname(COOKIES_PATH), exist_ok=True)

    def login_manual(self, username='', password='', callback=None) -> bool:
        """시크릿 모드 로그인 — 프로필 저장 안 함, 쿠키만 JSON으로 별도 보관"""
        self._ensure_cookies_dir()

        pw = sync_playwright().start()
        chrome_path = _find_chrome_path()

        stealth_args = [
            '--incognito',  # 시크릿 모드
            '--disable-blink-features=AutomationControlled',
            '--disable-infobars',
            '--no-first-run',
            '--no-default-browser-check',
            '--lang=ko-KR',
            '--window-position=-9999,-9999',
        ]

        browser = None
        context = None
        try:
            if callback:
                callback("[I] 스텔스 브라우저 시작 중 (시크릿 모드)...")

            launch_args = {
                'headless': False,
                'args': stealth_args,
            }

            if chrome_path:
                launch_args['executable_path'] = chrome_path
                if callback:
                    callback("[I] Chrome 감지 - 실제 Chrome으로 열기")

            try:
                browser = pw.chromium.launch(**launch_args)
            except Exception:
                launch_args.pop('executable_path', None)
                browser = pw.chromium.launch(**launch_args)

            context = browser.new_context(
                no_viewport=True,
                locale='ko-KR',
                timezone_id='Asia/Seoul',
                color_scheme='light',
            )

            # init_script로 등록 (새 페이지마다 자동 적용)
            context.add_init_script(STEALTH_SCRIPT)

            page = context.new_page()

            # 현재 페이지에 스텔스 적용 (에러 무시)
            try:
                page.evaluate(STEALTH_SCRIPT)
            except Exception:
                pass

            if callback:
                callback("[I] 네이버 로그인 페이지로 이동")

            page.goto('https://nid.naver.com/nidlogin.login', timeout=15000)

            try:
                page.evaluate(STEALTH_SCRIPT)
            except Exception:
                pass

            time.sleep(random.uniform(1.0, 1.5))

            # 캡스락 해제 (Windows API)
            try:
                import ctypes
                VK_CAPITAL = 0x14
                if ctypes.windll.user32.GetKeyState(VK_CAPITAL) & 1:
                    ctypes.windll.user32.keybd_event(VK_CAPITAL, 0x45, 1, 0)
                    ctypes.windll.user32.keybd_event(VK_CAPITAL, 0x45, 3, 0)
                    if callback:
                        callback("[I] 캡스락 해제됨")
            except Exception:
                pass

            # 아이디/비밀번호 자동 입력
            if username and password:
                try:
                    if callback:
                        callback("[I] 아이디/비밀번호 자동 입력 중...")

                    id_input = page.query_selector('#id')
                    if id_input:
                        id_input.click()
                        time.sleep(0.3)
                        for char in username:
                            page.keyboard.type(char, delay=random.randint(30, 80))
                            time.sleep(random.uniform(0.02, 0.05))

                    time.sleep(random.uniform(0.3, 0.5))

                    pw_input = page.query_selector('#pw')
                    if pw_input:
                        pw_input.click()
                        time.sleep(0.3)
                        for char in password:
                            page.keyboard.type(char, delay=random.randint(30, 80))
                            time.sleep(random.uniform(0.02, 0.05))

                    time.sleep(random.uniform(0.3, 0.5))

                    login_btn = page.query_selector('.btn_login, #log\\.login')
                    if login_btn:
                        login_btn.click()
                        if callback:
                            callback("[I] 로그인 버튼 클릭!")
                    time.sleep(2)
                except Exception as e:
                    if callback:
                        callback(f"[!] 자동 입력 중 오류 (직접 입력해주세요): {e}")
            else:
                if callback:
                    callback("[I] 직접 아이디/비밀번호를 입력해주세요")

            if callback:
                callback("[I] 로그인 완료 대기 중... (최대 5분)")

            # 로그인 완료 대기
            try:
                page.wait_for_url(
                    lambda url: ('naver.com' in url
                                 and 'nidlogin' not in url
                                 and 'captcha' not in url),
                    timeout=300000
                )
            except Exception:
                if callback:
                    callback("[!] 시간 초과 (5분)")
                context.close()
                pw.stop()
                return False

            time.sleep(2)

            # 쿠키 저장
            cookies = context.cookies()
            with open(COOKIES_PATH, 'w', encoding='utf-8') as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)

            if callback:
                callback("[I] 로그인 성공! 쿠키 저장 완료")

            try:
                context.close()
            except Exception:
                pass
            try:
                if browser:
                    browser.close()
            except Exception:
                pass
            pw.stop()
            return True

        except Exception as e:
            if callback:
                callback(f"[!] 오류: {e}")
            try:
                if context:
                    context.close()
            except Exception:
                pass
            try:
                if browser:
                    browser.close()
            except Exception:
                pass
            try:
                pw.stop()
            except Exception:
                pass
            return False

    def has_cookies(self) -> bool:
        return os.path.exists(COOKIES_PATH)

    def start_headless(self, visible: bool = False) -> Page:
        """발행용 브라우저.
        visible=True : 창이 보이게 (테스트/디버깅 용)
        visible=False: 화면 밖으로 밀어 숨김 (기본)
        """
        self._playwright = sync_playwright().start()

        chrome_path = _find_chrome_path()
        args = [
            '--incognito',  # 시크릿 모드
            '--disable-blink-features=AutomationControlled',
            '--lang=ko-KR',
        ]
        if not visible:
            args.append('--window-position=-9999,-9999')
        launch_args = {'headless': False, 'args': args}
        if chrome_path:
            launch_args['executable_path'] = chrome_path

        self._browser = self._playwright.chromium.launch(**launch_args)
        self._context = self._browser.new_context(
            no_viewport=True,
            storage_state=None,  # 시크릿 — 저장된 데이터 없이 시작
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.86 Safari/537.36',
            locale='ko-KR',
            timezone_id='Asia/Seoul',
            color_scheme='light'
        )
        self._context.add_init_script(STEALTH_SCRIPT)

        if self.has_cookies():
            with open(COOKIES_PATH, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            self._context.add_cookies(cookies)

        self._page = self._context.new_page()
        return self._page

    def get_page(self) -> Page:
        return self._page

    def close(self):
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None

    def update_cookies(self):
        if self._context:
            self._ensure_cookies_dir()
            cookies = self._context.cookies()
            with open(COOKIES_PATH, 'w', encoding='utf-8') as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)

    def delete_cookies(self):
        if os.path.exists(COOKIES_PATH):
            os.remove(COOKIES_PATH)
