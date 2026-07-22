"""OurBox 출고 페이지 소스에서 Ajax URL 추출"""
import json, os, sys, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(os.path.dirname(__file__), "config.json"), encoding="utf-8") as f:
    cfg = json.load(f)

from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context()
    page = ctx.new_page()

    page.goto("https://oms.ourbox.co.kr/om/login/login.do", wait_until="networkidle", timeout=30000)
    page.evaluate("""
        async ({ id, pw }) => {
            let res = await fetch('/om/login/ajax/loginProc.do', {
                method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: `user_id=${encodeURIComponent(id)}&user_pwd=${encodeURIComponent(pw)}&second=Y&user_crtfc_cno=&lang=ko_KR`
            }).then(r => r.json());
            if (res.result === 'duplLogin') res = await fetch('/om/login/ajax/loginProc.do', {
                method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: `user_id=${encodeURIComponent(id)}&user_pwd=${encodeURIComponent(pw)}&second=Y&user_crtfc_cno=&lang=ko_KR&duplLoginConfirm=true`
            }).then(r => r.json());
        }
    """, {"id": cfg["ourbox_id"], "pw": cfg["ourbox_pw"]})

    # 출고 목록 페이지 소스 읽기
    page.goto("https://oms.ourbox.co.kr/om/out/out/out_list.do", wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(2000)
    content = page.content()

    # Ajax URL 패턴 추출
    ajax_urls = re.findall(r'["\']([^"\']*\.do[^"\']*)["\']', content)
    unique_urls = sorted(set(u for u in ajax_urls if "ajax" in u or ".do" in u))
    print("=== 출고 페이지에서 발견된 URL 패턴 ===")
    for u in unique_urls[:50]:
        print(u)

    print()
    print("=== ajax 포함 URL만 ===")
    for u in unique_urls:
        if "ajax" in u.lower():
            print(u)

    # JavaScript 내 url/action 관련 변수 찾기
    print()
    print("=== 페이지 JS에서 url 변수 ===")
    url_vars = re.findall(r'url\s*[=:]\s*["\']([^"\']+\.do[^"\']*)["\']', content)
    for u in set(url_vars):
        print(u)

    browser.close()
