"""아워박스 스크래퍼 (Playwright 기반) - 입고정산기 기능"""
import os
import json
from datetime import datetime, timedelta
import receiving_db as db

OURBOX_BASE = "https://oms.ourbox.co.kr"
# 수동 로그인으로 저장한 세션(쿠키). CAPTCHA가 걸려 자동 로그인이 막혀도 이 세션으로 동작.
# 생성: 아워박스_세션로그인.py 실행 후 브라우저에서 직접 로그인.
SESSION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ourbox_session.json")


def _is_authed_page(page) -> bool:
    """세션이 실제로 인증 상태인지 확인.

    주의: 세션이 끊겨도 main.do는 error.do로 리다이렉트되며 200이 오므로
    URL 검사만으로는 부족 — 인증 필요한 가벼운 ajax의 상태코드(401 여부)로 판정.
    """
    if "login" in page.url or "error" in page.url:
        return False
    try:
        status = page.evaluate(
            "async () => (await fetch('/om/sach/colct/ajax/selOdColctChList.do?mall_svc=order',"
            " {headers:{'AJAX':'true'}})).status"
        )
        return int(status) == 200
    except Exception:
        return False


def _try_saved_session(pw_context):
    """저장된 세션 쿠키로 로그인 없이 진입 시도. 성공 시 page, 실패 시 None."""
    try:
        if not os.path.exists(SESSION_PATH):
            return None
        with open(SESSION_PATH, encoding="utf-8") as f:
            cookies = (json.load(f) or {}).get("cookies") or []
        if not cookies:
            return None
        pw_context.add_cookies(cookies)
        page = pw_context.new_page()
        page.goto(f"{OURBOX_BASE}/om/main.do", wait_until="domcontentloaded", timeout=30000)
        if _is_authed_page(page):
            return page
        page.close()
    except Exception:
        pass
    return None


def _save_session(pw_context):
    """로그인 성공한 컨텍스트의 세션을 저장 (다음 실행에서 로그인 생략)."""
    try:
        with open(SESSION_PATH, "w", encoding="utf-8") as f:
            json.dump(pw_context.storage_state(), f)
    except Exception:
        pass


def _login_and_get_page(pw_context, ourbox_id: str, ourbox_pw: str):
    page = _try_saved_session(pw_context)
    if page is not None:
        return page
    page = pw_context.new_page()
    # networkidle은 사이트가 느리거나 백그라운드 요청이 이어지면 타임아웃 — 로그인은
    # 페이지 컨텍스트의 fetch만 필요하므로 domcontentloaded로 충분. 1회 재시도 포함.
    try:
        page.goto(f"{OURBOX_BASE}/om/login/login.do", wait_until="domcontentloaded", timeout=30000)
    except Exception:
        page.goto(f"{OURBOX_BASE}/om/login/login.do", wait_until="domcontentloaded", timeout=45000)

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
    """, {"id": ourbox_id, "pw": ourbox_pw})

    if str(login_result.get("result")) != "true":
        msg = login_result.get("message") or f"서버 응답: {login_result}"
        raise RuntimeError(f"아워박스 로그인 실패: {msg}")

    page.goto(f"{OURBOX_BASE}/om/main.do", wait_until="domcontentloaded", timeout=30000)
    if not _is_authed_page(page):
        raise RuntimeError("아워박스 세션 오류. 로그인 후 리다이렉트 실패.")
    _save_session(pw_context)
    return page


def _fetch_json(page, url: str) -> dict:
    return page.evaluate("""
        async (url) => {
            const res = await fetch(url);
            const text = await res.text();
            try {
                return JSON.parse(text);
            } catch(e) {
                return { _error: true, _status: res.status, _preview: text.slice(0, 120) };
            }
        }
    """, url)


def _test_login(ourbox_id: str, ourbox_pw: str):
    """로그인만 테스트 (데이터 조회 없음)"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        try:
            _login_and_get_page(context, ourbox_id, ourbox_pw)
        finally:
            browser.close()


def sync_new_receivings(ourbox_id: str, ourbox_pw: str) -> int:
    """아워박스 최근 30일 입고완료 건 DB 동기화. 새로 추가된 건수 반환."""
    from playwright.sync_api import sync_playwright

    today = datetime.now()
    month_ago = today - timedelta(days=30)
    fmt = lambda d: d.strftime("%Y-%m-%d")

    url = (
        f"{OURBOX_BASE}/om/put/put/ajax/selPutList.do?"
        f"searchDateType=put_req_dt&searchDateRange={fmt(month_ago)}+%7E+{fmt(today)}"
        f"&put_state=11&put_type=&vendor_cd=&depot_cd=&page=1&size=100"
    )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        try:
            page = _login_and_get_page(context, ourbox_id, ourbox_pw)
            data = _fetch_json(page, url)
            records = data.get("data", [])

            new_count = 0
            for row in records:
                if row.get("put_state_nm") != "입고완료":
                    continue
                put_sno = str(row["put_sno"])
                if db.exists(put_sno):
                    continue

                detail_url = (
                    f"{OURBOX_BASE}/om/put/expect/ajax/selExpectDetailList.do"
                    f"?put_sno={put_sno}&stype=02&page=1&size=100"
                )
                detail = _fetch_json(page, detail_url)
                items = detail.get("data", [])

                db.insert_record({
                    "put_sno": put_sno,
                    "put_depot_nm": row.get("put_depot_nm"),
                    "vendor_nm": row.get("vendor_nm"),
                    "put_req_dt": row.get("put_req_dt"),
                    "put_compt_dtm": row.get("put_compt_dtm"),
                    "put_type_nm": row.get("put_type_nm"),
                    "item_cnt": row.get("item_cnt"),
                    "tot_put_qty": row.get("tot_put_qty"),
                    "raw_data": str(row),
                })

                if items:
                    db.insert_items(put_sno, [
                        {
                            "put_sno": put_sno,
                            "prod_cd": i.get("prod_cd"),
                            "sale_prod_nm": i.get("sale_prod_nm"),
                            "put_qty": i.get("putaway_qty") if i.get("putaway_qty") is not None else i.get("put_qty"),
                            "put_detail_sno": str(i.get("put_detail_sno", "")),
                            "raw_data": str(i),
                        }
                        for i in items
                    ])

                new_count += 1
        finally:
            browser.close()

    return new_count


def _fetch_range(page, list_url: str, detail_url_fn, id_key: str) -> list:
    """범용 목록+상세 수집기."""
    first = _fetch_json(page, list_url + "&page=1&size=200")
    if first.get("_error"):
        raise RuntimeError(
            f"URL이 JSON을 반환하지 않음 (status={first.get('_status')}). "
            f"URL 확인 필요: {list_url[:80]} | 응답 미리보기: {first.get('_preview', '')[:80]}"
        )
    total_pages = first.get("last_page", 1)
    rows = list(first.get("data", []))
    for p in range(2, total_pages + 1):
        res = _fetch_json(page, list_url + f"&page={p}&size=200")
        rows.extend(res.get("data", []))

    results = []
    for row in rows:
        sno = str(row.get(id_key, ""))
        if not sno:
            results.append({"header": row, "items": []})
            continue
        try:
            detail = _fetch_json(page, detail_url_fn(sno))
            items = detail.get("data", [])
        except Exception:
            items = []
        results.append({"header": row, "items": items})
    return results


def fetch_inbound_range(ourbox_id: str, ourbox_pw: str, from_date: str, to_date: str) -> list:
    """입고 완료 내역 조회 (날짜 범위)."""
    from playwright.sync_api import sync_playwright
    date_range = f"{from_date}+%7E+{to_date}"
    list_url = (
        f"{OURBOX_BASE}/om/put/put/ajax/selPutList.do?"
        f"searchDateType=put_req_dt&searchDateRange={date_range}&put_state=11&put_type=&vendor_cd=&depot_cd="
    )
    detail_url_fn = lambda sno: (
        f"{OURBOX_BASE}/om/put/expect/ajax/selExpectDetailList.do?put_sno={sno}&stype=02&page=1&size=200"
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        try:
            page = _login_and_get_page(context, ourbox_id, ourbox_pw)
            return _fetch_range(page, list_url, detail_url_fn, "put_sno")
        finally:
            browser.close()


def fetch_stock_inout(ourbox_id: str, ourbox_pw: str, from_date: str, to_date: str) -> list:
    """재고 입출고 이력 조회 (GET /wm/stock/sach/ajax/stockInOutData.do).

    응답 구조 (per product, wide format):
      sale_prod_nm, brand, prod_group
      put_qty, out_qty, adj_qty, rtn_qty  (기간 합계)
      put_qty_YYYYMMDD, out_qty_YYYYMMDD, adj_qty_YYYYMMDD, rtn_qty_YYYYMMDD (일별)
    """
    from playwright.sync_api import sync_playwright

    date_range = f"{from_date} ~ {to_date}"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        try:
            page = _login_and_get_page(context, ourbox_id, ourbox_pw)

            all_items = []
            p = 1
            while True:
                url = (
                    f"{OURBOX_BASE}/wm/stock/sach/ajax/stockInOutData.do"
                    f"?searchColumn=reg_dtm"
                    f"&searchDateRange={date_range.replace(' ', '+')}"
                    f"&depot_cd=&stock_div_sno=&searchColumn2=&search_txt="
                    f"&page={p}&size=100"
                )
                data = _fetch_json(page, url)
                if data.get("_error"):
                    raise RuntimeError(
                        f"stockInOutData.do 오류 (status={data.get('_status')}): "
                        f"{data.get('_preview', '')}"
                    )
                items = data.get("data", [])
                if not items:
                    break
                all_items.extend(items)
                last_page = int(data.get("last_page", 1))
                if p >= last_page:
                    break
                p += 1

            return all_items
        finally:
            browser.close()


def fetch_outbound_range(ourbox_id: str, ourbox_pw: str, from_date: str, to_date: str) -> list:
    """출고 내역 — fetch_stock_inout()으로 통합됨."""
    return fetch_stock_inout(ourbox_id, ourbox_pw, from_date, to_date)


def fetch_adjustment_range(ourbox_id: str, ourbox_pw: str, from_date: str, to_date: str) -> list:
    """조정 내역 — fetch_stock_inout()으로 통합됨."""
    return fetch_stock_inout(ourbox_id, ourbox_pw, from_date, to_date)


def fetch_all_ourbox_products(ourbox_id: str, ourbox_pw: str) -> list:
    """아워박스 전체 상품 목록 조회 (자동 매핑용)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        try:
            page = _login_and_get_page(context, ourbox_id, ourbox_pw)
            first = _fetch_json(
                page,
                f"{OURBOX_BASE}/om/prod/manage/ajax/selProductList.do?page=1&size=100"
            )
            total_pages = first.get("last_page", 1)
            all_products = list(first.get("data", []))

            for p in range(2, total_pages + 1):
                res = _fetch_json(
                    page,
                    f"{OURBOX_BASE}/om/prod/manage/ajax/selProductList.do?page={p}&size=100"
                )
                all_products.extend(res.get("data", []))
        finally:
            browser.close()

    return all_products
