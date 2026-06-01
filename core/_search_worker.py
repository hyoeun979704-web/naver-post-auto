"""별도 프로세스에서 Playwright로 네이버 블로그 검색 (WinError 50 회피용)
사용: python _search_worker.py <keyword> <count>
stdout 마지막 줄에 JSON 배열로 URL 리스트 출력"""
import sys
import json
import re
import os


def main():
    if len(sys.argv) < 3:
        print("[]")
        return

    keyword = sys.argv[1]
    count = int(sys.argv[2])

    try:
        from urllib.parse import quote
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"ERROR: import failed: {e}", file=sys.stderr)
        print("[]")
        return

    q = quote(keyword)
    # rank_check 검증된 방식 — 모바일 블로그 검색 + 인기글 섹션 파싱
    url = f"https://m.search.naver.com/search.naver?where=m_blog&query={q}"

    # 실제 Chrome 경로 찾기
    chrome_path = None
    for p in [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]:
        if os.path.exists(p):
            chrome_path = p
            break

    blog_urls = []
    try:
        with sync_playwright() as pw:
            launch_args = {
                "headless": False,
                "args": [
                    "--incognito",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--lang=ko-KR",
                    "--window-position=-9999,-9999",
                    "--window-size=1280,800",
                ],
            }
            if chrome_path:
                launch_args["executable_path"] = chrome_path

            try:
                browser = pw.chromium.launch(**launch_args)
            except Exception as e1:
                # 실제 Chrome 실패 시 번들 Chromium으로 폴백
                print(f"[INFO] chrome launch failed: {e1}; fallback to chromium", file=sys.stderr)
                launch_args.pop("executable_path", None)
                browser = pw.chromium.launch(**launch_args)

            try:
                context = browser.new_context(
                    no_viewport=True,
                    # 모바일 검색 URL이므로 모바일 UA 사용
                    user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
                               "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                               "Version/17.4 Mobile/15E148 Safari/604.1",
                    locale="ko-KR",
                    timezone_id="Asia/Seoul",
                    color_scheme="light",
                )
                # webdriver 플래그 제거 (간이 스텔스)
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => false});"
                )
                page = context.new_page()
                page.goto(url, timeout=20000, wait_until="domcontentloaded")
                # 인기글 섹션 로드 대기 — rank_check 와 동일한 셀렉터
                try:
                    page.wait_for_selector(
                        'a[data-heatmap-target="articleSourceJSX_title"]',
                        timeout=8000
                    )
                except Exception:
                    # 인기글 셀렉터 못 찾으면 일반 블로그 링크라도 기다림
                    try:
                        page.wait_for_selector('a[href*="blog.naver.com"]', timeout=4000)
                    except Exception:
                        pass
                html = page.content()

                # 1차: 인기글 섹션의 articleSourceJSX_title 앵커 — rank_check 방식
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(html, "html.parser")
                    headers = [h for h in soup.find_all(["h1", "h2", "h3", "h4"])
                               if "인기글" in h.get_text()]
                    if headers:
                        seen = set()
                        anchors = soup.find_all(
                            "a", attrs={"data-heatmap-target": "articleSourceJSX_title"}
                        )
                        blog_re = re.compile(
                            r"https?://(?:m\.)?blog\.naver\.com/([a-zA-Z0-9_.\-]+)(?:/(\d+))?"
                        )
                        for a in anchors:
                            if len(blog_urls) >= count:
                                break
                            href = a.get("href", "") or ""
                            if "ader.naver.com" in href:  # 광고 제외
                                continue
                            m = blog_re.search(href)
                            if not m:
                                continue
                            blog_id, log_no = m.group(1), m.group(2)
                            if blog_id in ("PostView", "PostList", "prologue", "BlogHome"):
                                continue
                            if not log_no:
                                # 카드 내부에서 log_no 포함 링크 찾기
                                card = a
                                for _ in range(10):
                                    card = card.parent
                                    if card is None:
                                        break
                                    if card.get("data-template-id") == "ugcItem":
                                        break
                                else:
                                    card = None
                                if card:
                                    inner = card.find("a", href=re.compile(
                                        r"blog\.naver\.com/[^/]+/\d+"))
                                    if inner:
                                        m2 = blog_re.search(inner.get("href", ""))
                                        if m2 and m2.group(2):
                                            log_no = m2.group(2)
                            if not log_no:
                                continue
                            if blog_id in seen:
                                continue
                            seen.add(blog_id)
                            blog_urls.append(f"https://blog.naver.com/{blog_id}/{log_no}")
                except Exception as e:
                    print(f"[INFO] 인기글 파싱 실패: {e}", file=sys.stderr)

                # 2차 폴백: 인기글 못 찾으면 정규식 (구형)
                if not blog_urls:
                    seen = set()
                    pattern = r'href="(https?://(?:m\.)?blog\.naver\.com/([a-zA-Z0-9_\-]+)/(\d+))"'
                    for m in re.finditer(pattern, html):
                        blog_id = m.group(2)
                        log_no = m.group(3)
                        if blog_id in ("PostView", "PostList", "prologue"):
                            continue
                        if blog_id in seen:
                            continue
                        seen.add(blog_id)
                        blog_urls.append(f"https://blog.naver.com/{blog_id}/{log_no}")
                        if len(blog_urls) >= count:
                            break
            finally:
                try:
                    context.close()
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception as e:
        import traceback
        print(f"ERROR: {e}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)

    # 항상 마지막 줄에 JSON
    print(json.dumps(blog_urls, ensure_ascii=False))


if __name__ == "__main__":
    main()
