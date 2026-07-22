"""아워박스 웹 UI에서 출고/조정 Ajax URL을 자동으로 찾는 탐색 스크립트.

실행: python find_ourbox_urls.py
결과: 캡처된 Ajax 요청 목록 출력
"""
import json
import os
import sys

# 프로젝트 루트에서 config 읽기
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

try:
    with open(os.path.join(_ROOT, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
except Exception:
    cfg = {}

OURBOX_ID = cfg.get("ourbox_id", "")
OURBOX_PW = cfg.get("ourbox_pw", "")
OURBOX_BASE = "https://oms.ourbox.co.kr"

if not OURBOX_ID or not OURBOX_PW:
    print("config.json에 ourbox_id/ourbox_pw가 없습니다.")
    sys.exit(1)


def find_urls():
    from playwright.sync_api import sync_playwright

    captured_requests = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)  # GUI로 실행 (확인용)
        context = browser.new_context()

        # 모든 네트워크 요청 캡처
        page = context.new_page()

        def on_request(request):
            url = request.url
            if "ajax" in url and "ourbox" in url:
                captured_requests.append({
                    "method": request.method,
                    "url": url,
                })
                print(f"[캡처] {request.method} {url}")

        page.on("request", on_request)

        # 로그인
        page.goto(f"{OURBOX_BASE}/om/login/login.do", wait_until="networkidle", timeout=30000)
        login_result = page.evaluate("""
            async ({ id, pw }) => {
                const makeBody = (extra) =>
                    `user_id=${encodeURIComponent(id)}&user_pwd=${encodeURIComponent(pw)}&second=Y&user_crtfc_cno=&lang=ko_KR${extra}`;
                const post = (body) => fetch('/om/login/ajax/loginProc.do', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body
                }).then(r => r.json());
                let res = await post(makeBody(''));
                if (res.result === 'duplLogin') {
                    res = await post(makeBody('&duplLoginConfirm=true'));
                }
                return res;
            }
        """, {"id": OURBOX_ID, "pw": OURBOX_PW})

        if str(login_result.get("result")) != "true":
            print(f"로그인 실패: {login_result}")
            browser.close()
            return

        print("[OK] 로그인 성공")
        page.goto(f"{OURBOX_BASE}/om/main.do", wait_until="networkidle", timeout=20000)

        # 출고 페이지로 이동 시도
        print("\n--- 출고 페이지 탐색 중... ---")
        for out_url in [
            f"{OURBOX_BASE}/om/out/out/out_list.do",
            f"{OURBOX_BASE}/om/out/out_list.do",
            f"{OURBOX_BASE}/om/out/outList.do",
            f"{OURBOX_BASE}/om/ship/out/out_list.do",
        ]:
            try:
                page.goto(out_url, wait_until="networkidle", timeout=8000)
                print(f"접속: {page.url}")
                page.wait_for_timeout(2000)
            except Exception as e:
                print(f"  실패: {out_url} → {e}")

        # 재고 조정 페이지로 이동 시도
        print("\n--- 조정 페이지 탐색 중... ---")
        for adj_url in [
            f"{OURBOX_BASE}/om/stock/adj/adj_list.do",
            f"{OURBOX_BASE}/om/adj/adj_list.do",
            f"{OURBOX_BASE}/om/stock/stockAdj.do",
            f"{OURBOX_BASE}/om/wms/adj/adj_list.do",
        ]:
            try:
                page.goto(adj_url, wait_until="networkidle", timeout=8000)
                print(f"접속: {page.url}")
                page.wait_for_timeout(2000)
            except Exception as e:
                print(f"  실패: {adj_url} → {e}")

        print("\n=== 캡처된 Ajax 요청 ===")
        for r in captured_requests:
            print(f"{r['method']} {r['url']}")

        print("\n브라우저를 수동으로 탐색해 출고/조정 페이지를 확인하세요.")
        print("확인 후 Enter를 눌러 종료하세요...")
        input()
        browser.close()

    return captured_requests


if __name__ == "__main__":
    find_urls()
