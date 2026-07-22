"""비어있지 않은 실제 데이터 레코드 확인"""
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
            // 데이터가 있는 페이지 찾기 (2페이지 이후 시도)
            const url = `/wm/stock/sach/ajax/stockInOutData.do?pwn_cd=${pwn_cd}&searchColumn=reg_dtm&searchDateRange=2026-05-01+~+2026-05-31&depot_cd=&stock_div_sno=&searchColumn2=&search_txt=&page=2&size=5`;
            const r = await fetch(url);
            const d = await r.json();

            // 비어있지 않은 레코드 찾기
            const nonEmpty = (d.data || []).filter(item =>
                item.sale_prod_nm && item.sale_prod_nm !== '' &&
                (parseInt(item.put_qty || '0') > 0 || parseInt(item.out_qty || '0') > 0)
            );

            return {
                total_pages: d.last_page,
                sample: nonEmpty.slice(0, 2),
                all_keys: d.data && d.data[0] ? Object.keys(d.data[0]) : []
            };
        }
    """, {"pwn_cd": PWN_CD})

    print(f"전체 페이지 수: {result.get('total_pages')}")
    print(f"\n전체 키 목록: {result.get('all_keys', [])}")

    print("\n=== 비어있지 않은 레코드 ===")
    for rec in result.get("sample", []):
        # 핵심 필드만 출력
        core_fields = {k: v for k, v in rec.items()
                      if not k.startswith(("put_qty_", "out_qty_", "adj_qty_", "rtn_qty_"))}
        print(json.dumps(core_fields, ensure_ascii=False, indent=2))
        print()

    browser.close()
