"""OurBox /wm/ 경로 페이지들의 Ajax URL 탐색"""
import json, os, sys, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(os.path.dirname(__file__), "config.json"), encoding="utf-8") as f:
    cfg = json.load(f)

from playwright.sync_api import sync_playwright

BASE = "https://oms.ourbox.co.kr"

captured_all = []

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context()
    page = ctx.new_page()

    def on_request(req):
        url = req.url
        if ("ourbox" in url and "login" not in url and
                ("ajax" in url or "/wm/" in url or ".do" in url)):
            try:
                body = req.post_data or ""
            except Exception:
                body = ""
            captured_all.append({"url": url, "method": req.method, "body": body[:300]})

    page.on("request", on_request)

    # 로그인
    page.goto(f"{BASE}/om/login/login.do", wait_until="networkidle", timeout=30000)
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
    page.goto(f"{BASE}/om/main.do", wait_until="networkidle", timeout=20000)
    captured_all.clear()

    # /wm/ 페이지들 탐색
    wm_pages = [
        "/wm/stock/sach/stockInOut.do",
        "/wm/stock/sach/stockHist.do",
        "/wm/stock/sach/sachProdList.do",
        "/wm/stock/expir/stockList.do",
        "/wm/out/wk/box/boxConfirmB2cCmbForm.do",
    ]

    for wm_path in wm_pages:
        captured_all.clear()
        print(f"\n=== 탐색: {wm_path} ===")
        try:
            page.goto(f"{BASE}{wm_path}", wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(2000)
            print(f"URL: {page.url}")

            # 조회 버튼 클릭 시도
            for sel in ["button:has-text('조회')", "input[value='조회']", "#btnSearch",
                        ".btn-search", "button[type='submit']", "a:has-text('조회')"]:
                try:
                    page.click(sel, timeout=1500)
                    page.wait_for_timeout(1500)
                    print(f"  클릭: {sel}")
                    break
                except Exception:
                    pass

            # 캡처된 요청 출력
            for r in captured_all:
                if "login" not in r["url"]:
                    print(f"  [{r['method']}] {r['url']}")
                    if r["body"]:
                        print(f"         body: {r['body'][:200]}")

        except Exception as e:
            print(f"  오류: {e}")

    # 출고 목록 페이지의 JS 파일에서 Ajax URL 찾기
    print("\n=== /om/out/out/out_list.do JS 소스 분석 ===")
    captured_all.clear()
    page.goto(f"{BASE}/om/out/out/out_list.do", wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(2000)

    # 페이지에서 외부 JS 파일 목록
    js_files = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('script[src]'))
            .map(s => s.src)
            .filter(s => s.includes('ourbox') || !s.includes('http'));
    }""")
    print("JS 파일들:")
    for js in js_files:
        print(f"  {js}")

    # 인라인 스크립트에서 Ajax URL 찾기
    inline_scripts = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('script:not([src])'))
            .map(s => s.textContent)
            .join('\\n');
    }""")
    ajax_matches = re.findall(r'["\']([^"\']*\.do[^"\']*)["\']', inline_scripts)
    ajax_matches += re.findall(r'url\s*[:=]\s*["\']([^"\']+)["\']', inline_scripts)
    print("인라인 스크립트의 URL 후보:")
    for u in sorted(set(ajax_matches))[:30]:
        if ".do" in u:
            print(f"  {u}")

    browser.close()
