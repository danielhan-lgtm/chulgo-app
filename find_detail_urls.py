"""OurBox 출고/조정 상세 목록 Ajax URL 탐색 스크립트."""
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
        if "ajax" in req.url or ".do" in req.url:
            if "ourbox" in req.url and "login" not in req.url:
                try:
                    body = req.post_data or ""
                except Exception:
                    body = ""
                captured.append({"url": req.url, "method": req.method, "body": body[:200]})

    page.on("request", on_request)

    # 로그인
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
            return res;
        }
    """, {"id": cfg["ourbox_id"], "pw": cfg["ourbox_pw"]})
    page.goto("https://oms.ourbox.co.kr/om/main.do", wait_until="networkidle", timeout=20000)
    captured.clear()

    # 출고 관련 URL 후보 직접 POST 테스트
    print("=== 출고 목록 URL 후보 테스트 ===")
    out_result = page.evaluate("""
        async () => {
            const urls = [
                '/om/out/out/ajax/selOutList.do',
                '/om/wms/out/ajax/selOutList.do',
                '/om/out/ajax/selOutList.do',
                '/om/ajax/selOutList.do',
                '/om/out/out/ajax/selOutPerfList.do',
                '/om/out/perf/ajax/selOutPerfList.do',
            ];
            const results = [];
            for (const url of urls) {
                const r = await fetch(url, {
                    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                    body: 'page=1&size=5&searchDateType=out_req_dt&searchDateRange=2026-05-01+%7E+2026-05-31'
                });
                const text = await r.text();
                results.push({url, status: r.status, preview: text.slice(0, 200)});
            }
            return results;
        }
    """)
    for r in out_result:
        print(f"[{r['status']}] {r['url']}")
        if r['status'] == 200:
            print(f"  -> {r['preview']}")

    print()
    print("=== 조정 목록 URL 후보 테스트 ===")
    adj_result = page.evaluate("""
        async () => {
            const urls = [
                '/om/stock/adj/ajax/selStockAdjList.do',
                '/om/wms/stock/ajax/selStockAdjList.do',
                '/om/stock/ajax/selStockAdjList.do',
                '/om/ajax/selStockAdjList.do',
                '/om/stock/adj/ajax/selAdjList.do',
            ];
            const results = [];
            for (const url of urls) {
                const r = await fetch(url, {
                    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                    body: 'page=1&size=5&searchDateType=reg_dt&searchDateRange=2026-05-01+%7E+2026-05-31'
                });
                const text = await r.text();
                results.push({url, status: r.status, preview: text.slice(0, 200)});
            }
            return results;
        }
    """)
    for r in adj_result:
        print(f"[{r['status']}] {r['url']}")
        if r['status'] == 200:
            print(f"  -> {r['preview']}")

    # 출고 메뉴 페이지 이동 후 Ajax 캡처
    print()
    print("=== 출고 메뉴 페이지 탐색 ===")
    captured.clear()
    try:
        page.goto("https://oms.ourbox.co.kr/om/out/out/out_list.do", wait_until="networkidle", timeout=10000)
        page.wait_for_timeout(2000)
        # 조회 버튼 클릭 시도
        for sel in ["button:has-text('조회')", "input[value='조회']", "#btnSearch", ".btn-search"]:
            try:
                page.click(sel, timeout=2000)
                page.wait_for_timeout(1500)
                break
            except Exception:
                pass
        print(f"현재 URL: {page.url}")
        for r in captured:
            print(f"  캡처: {r['method']} {r['url']} | body: {r['body'][:100]}")
    except Exception as e:
        print(f"  실패: {e}")

    browser.close()
