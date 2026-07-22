"""OurBox 메인 페이지 Ajax 요청 바디 파라미터 캡처"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(os.path.dirname(__file__), "config.json"), encoding="utf-8") as f:
    cfg = json.load(f)

from playwright.sync_api import sync_playwright

captured = []

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context()
    page = ctx.new_page()

    def on_request(req):
        if "ajax" in req.url and "ourbox" in req.url:
            try:
                body = req.post_data or ""
            except Exception:
                body = ""
            captured.append({"url": req.url, "method": req.method, "body": body})

    page.on("request", on_request)

    page.goto("https://oms.ourbox.co.kr/om/login/login.do", wait_until="networkidle", timeout=30000)
    page.evaluate("""
        async ({ id, pw }) => {
            let res = await fetch('/om/login/ajax/loginProc.do', {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: `user_id=${encodeURIComponent(id)}&user_pwd=${encodeURIComponent(pw)}&second=Y&user_crtfc_cno=&lang=ko_KR`
            }).then(r => r.json());
            if (res.result === 'duplLogin') {
                res = await fetch('/om/login/ajax/loginProc.do', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                    body: `user_id=${encodeURIComponent(id)}&user_pwd=${encodeURIComponent(pw)}&second=Y&user_crtfc_cno=&lang=ko_KR&duplLoginConfirm=true`
                }).then(r => r.json());
            }
            return res;
        }
    """, {"id": cfg["ourbox_id"], "pw": cfg["ourbox_pw"]})

    page.goto("https://oms.ourbox.co.kr/om/main.do", wait_until="networkidle", timeout=20000)
    page.wait_for_timeout(3000)

    # selOutChangeList / selStockChangeList 에 직접 POST 테스트
    print("=== 직접 POST 테스트 ===")
    test_results = page.evaluate("""
        async () => {
            const urls = [
                '/om/ajax/selOutChangeList.do',
                '/om/ajax/selStockChangeList.do',
            ];
            const results = [];
            for (const url of urls) {
                try {
                    const r = await fetch(url, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                        body: 'page=1&size=10'
                    });
                    const text = await r.text();
                    results.push({url, status: r.status, preview: text.slice(0, 300)});
                } catch(e) {
                    results.push({url, error: String(e)});
                }
            }
            return results;
        }
    """)
    for r in test_results:
        print(f"URL: {r['url']}")
        print(f"Status: {r.get('status')}")
        print(f"Preview: {r.get('preview', r.get('error', ''))[:300]}")
        print()

    print("=== 캡처된 Ajax 요청 (body 포함) ===")
    for r in captured:
        if "login" not in r["url"]:
            print(f"URL: {r['url']}")
            print(f"BODY: {r['body'][:300]}")
            print()

    browser.close()
