"""stockInOutData.do 응답 필드 확인"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(os.path.dirname(__file__), "config.json"), encoding="utf-8") as f:
    cfg = json.load(f)

from playwright.sync_api import sync_playwright

BASE = "https://oms.ourbox.co.kr"
PWN_CD = "RS-1732839019"

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context()
    page = ctx.new_page()

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

    result = page.evaluate("""
        async ({pwn_cd}) => {
            // stockInOut 데이터 (입출고 이력)
            const url1 = `/wm/stock/sach/ajax/stockInOutData.do?pwn_cd=${pwn_cd}&searchColumn=reg_dtm&searchDateRange=2026-05-01+~+2026-05-31&depot_cd=&stock_div_sno=&searchColumn2=&search_txt=&page=1&size=5`;
            const r1 = await fetch(url1);
            const d1 = await r1.json();

            // stockHist 데이터 (재고 이력)
            const url2 = `/wm/stock/sach/ajax/stockHistData.do?pwn_cd=${pwn_cd}&searchColumn=&searchDateRange=2026-05-01+~+2026-05-31&depot_cd=&stock_div_sno=&searchColumn2=&search_txt=&page=1&size=5`;
            const r2 = await fetch(url2);
            const d2 = await r2.json();

            return { stockInOut: d1, stockHist: d2 };
        }
    """, {"pwn_cd": PWN_CD})

    print("=== stockInOutData.do 응답 ===")
    inout_data = result.get("stockInOut", {})
    # 전체 구조 키 출력
    print(f"최상위 키: {list(inout_data.keys()) if isinstance(inout_data, dict) else type(inout_data)}")
    if isinstance(inout_data, dict):
        for k, v in inout_data.items():
            if isinstance(v, list) and v:
                print(f"\n  [{k}] 총 {len(v)}건, 첫 번째 레코드 키: {list(v[0].keys()) if isinstance(v[0], dict) else v[0]}")
                print(f"  샘플: {json.dumps(v[0], ensure_ascii=False)[:500]}")
            else:
                print(f"  {k}: {str(v)[:100]}")
    elif isinstance(inout_data, list) and inout_data:
        print(f"배열, 총 {len(inout_data)}건")
        print(f"첫 번째 키: {list(inout_data[0].keys())}")
        print(f"샘플: {json.dumps(inout_data[0], ensure_ascii=False)[:500]}")

    print()
    print("=== stockHistData.do 응답 ===")
    hist_data = result.get("stockHist", {})
    print(f"최상위 키: {list(hist_data.keys()) if isinstance(hist_data, dict) else type(hist_data)}")
    if isinstance(hist_data, dict):
        for k, v in hist_data.items():
            if isinstance(v, list) and v:
                print(f"\n  [{k}] 총 {len(v)}건, 첫 번째 레코드 키: {list(v[0].keys()) if isinstance(v[0], dict) else v[0]}")
                print(f"  샘플: {json.dumps(v[0], ensure_ascii=False)[:500]}")
            else:
                print(f"  {k}: {str(v)[:100]}")

    browser.close()
