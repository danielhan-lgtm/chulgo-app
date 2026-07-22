"""아워박스 OMS 주문 자동화 클라이언트 (Playwright 세션 기반).

입고정산기(ourbox_scraper)와 동일하게 oms.ourbox.co.kr 로그인 세션 위에서
주문 수집/조회/단계전환/WMS출고등록 ajax를 호출한다.

처리 단계 (od_prgrs_state) ↔ OMS selOrderList 경로 키:
  order(주문발주) → putOrder(주문서처리) → outReady(출고준비)
  → shipReady(발송준비) → shipWait(발송대기)

⚠️ collect()/advance()/wms_register()는 운영 OMS에 실제 쓰기 동작이다.
   list_*()는 조회 전용(안전).
"""
from contextlib import contextmanager
from datetime import datetime, timedelta

from ourbox_scraper import _login_and_get_page, OURBOX_BASE

# 단계 경로 키 (사람이 읽는 라벨)
STAGE_LABELS = {
    "order": "주문(발주)",
    "putOrder": "주문서처리",
    "outReady": "출고준비",
    "shipReady": "발송준비",
    "shipWait": "발송대기",
}
STAGES = list(STAGE_LABELS.keys())


@contextmanager
def oms_session(ourbox_id: str, ourbox_pw: str):
    """로그인된 OMS 페이지를 제공하는 컨텍스트 매니저."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        try:
            page = _login_and_get_page(ctx, ourbox_id, ourbox_pw)
            yield page
        finally:
            browser.close()


def _get_json(page, path: str):
    """GET → JSON (채널목록 등)."""
    return page.evaluate(
        """async (path) => {
            const res = await fetch(path, {headers:{'AJAX':'true'}});
            const t = await res.text();
            try { return JSON.parse(t); }
            catch(e){ return {_error:true,_status:res.status,_preview:t.slice(0,300)}; }
        }""",
        path,
    )


def _post_multipart(page, path: str, params: dict) -> dict:
    """multipart/form-data POST (selOrderList 등 Tabulator 그리드 조회).

    FormData를 쓰면 브라우저가 boundary를 자동 생성한다 (Content-Type 수동지정 금지).
    """
    return page.evaluate(
        """async ({path, params}) => {
            const fd = new FormData();
            for (const [k,v] of Object.entries(params)) fd.append(k, v==null?'':String(v));
            const res = await fetch(path, {method:'POST', headers:{'AJAX':'true'}, body: fd});
            const t = await res.text();
            try { return JSON.parse(t); }
            catch(e){ return {_error:true,_status:res.status,_preview:t.slice(0,300)}; }
        }""",
        {"path": path, "params": params},
    )


def _post_json(page, path: str, payload) -> dict:
    """application/json POST (단계전환/WMS출고용)."""
    return page.evaluate(
        """async ({path, body}) => {
            const res = await fetch(path, {
                method:'POST',
                headers:{'Content-Type':'application/json; charset=UTF-8','AJAX':'true'},
                body: JSON.stringify(body)
            });
            const t = await res.text();
            try { return JSON.parse(t); }
            catch(e){ return {_error:true,_status:res.status,_preview:t.slice(0,300)}; }
        }""",
        {"path": path, "body": payload},
    )


# ── 조회 (읽기 전용, 안전) ─────────────────────────────────────────

def _date_range(days: int = 7):
    today = datetime.now()
    start = today - timedelta(days=days)
    return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


# 카페24 / 카카오 채널 식별 키워드
CAFE24_KW = ["카페24", "cafe24", "카페 24"]
KAKAO_KW = ["카카오", "kakao", "톡스토어", "톡 스토어"]


def list_collect_channels(page, mall_svc: str = "order") -> list:
    """주문 수집 대상 몰 계정 목록 (GET selOdColctChList).

    반환: [{mall_acc_sno, sach_cd, mall_nm, acc_alias_nm, colct_last_dtm, is_cafe24, is_kakao}]
    """
    raw = _get_json(page, f"/om/sach/colct/ajax/selOdColctChList.do?mall_svc={mall_svc}")
    if isinstance(raw, dict) and raw.get("_error"):
        return []
    out = []
    for c in raw if isinstance(raw, list) else []:
        nm = f"{c.get('mall_nm','')} {c.get('acc_alias_nm','')}".lower()
        out.append({
            "mall_acc_sno": c.get("mall_acc_sno"),
            "sach_cd": c.get("sach_cd"),
            "mall_nm": c.get("mall_nm"),
            "acc_alias_nm": c.get("acc_alias_nm"),
            "colct_last_dtm": c.get("colct_last_dtm"),
            "is_cafe24": any(k in nm for k in CAFE24_KW),
            "is_kakao": any(k in nm for k in KAKAO_KW),
        })
    return out


def list_orders(page, stage: str = "order", start_dt: str = "", end_dt: str = "",
                channel_cd: str = "", od_state: str = "", page_no: int = 1, size: int = 200) -> dict:
    """단계별 주문 목록 조회 (multipart selOrderList).

    stage: order|putOrder|outReady|shipReady|shipWait
    channel_cd: sach_cd로 채널 필터 (예: 카페24 SACH0768)
    반환: {data:[...], last_page, total} (원본 그대로 + 정규화 헬퍼는 라우터에서)
    """
    if stage not in STAGES:
        raise ValueError(f"알 수 없는 단계: {stage}")
    if not start_dt or not end_dt:
        start_dt, end_dt = _date_range(7)
    params = {
        "menu_url": f"/om/order/{stage}/{stage}List.do",
        "tableExcelNm": f"{STAGE_LABELS[stage]}.xlsx",
        "searchDtm": "reg_dtm",
        "search_start_dtm": start_dt,
        "search_end_dtm": end_dt,
        "search_sach_cd": channel_cd,
        "searchSachTxt": "",
        "search_prod_div": "",
        "search_tmprt_type": "",
        "search_order_type": "",
        "search_od_state": od_state,
        "dlvr_div_cd": "",
        "searchColumn": "od_sno",
        "searchTxt": "",
        "del_yn": "",
        "list_cnt": 0,
        "current_page": "",
        "page": page_no,
        "size": size,
    }
    return _post_multipart(page, f"/om/order/{stage}/ajax/selOrderList.do", params)


def channel_tag(row: dict):
    """주문 행의 판매처/몰명으로 카페24/카카오 분류 → 'cafe24'|'kakao'|None.

    주문그리드의 sach_cd(판매처코드)는 수집채널 mall_acc의 sach_cd와 체계가 달라
    이름 기준 매칭이 가장 안전하다.
    """
    blob = f"{row.get('sach_nm','')} {row.get('mall_nm','')} {row.get('sach_prod_nm','')}".lower()
    if any(k in blob for k in CAFE24_KW):
        return "cafe24"
    if any(k in blob for k in KAKAO_KW):
        return "kakao"
    return None


def normalize_order(row: dict) -> dict:
    """주문 그리드 행 → 앱에서 쓰는 정규화 필드 (1행 = 주문상품 1라인)."""
    import html as _h
    clean = lambda s: _h.unescape(str(s)) if s is not None else ""
    return {
        "od_sno": row.get("od_sno"),
        "stage": str(row.get("od_prgrs_state") or ""),
        "stage_nm": clean(row.get("od_prgrs_state_nm")),
        "od_state": str(row.get("od_state") or ""),
        "od_state_nm": clean(row.get("od_state_nm")),
        "channel": channel_tag(row),
        "sach_cd": row.get("sach_cd"),
        "sach_nm": clean(row.get("sach_nm")),
        "mall_nm": clean(row.get("mall_nm")),
        "mall_od_no": row.get("mall_od_no"),
        "prod_cd": row.get("prod_cd"),
        "item_cd": row.get("item_cd"),
        "prod_nm": clean(row.get("prod_nm")),
        "od_qty": row.get("od_qty"),
        "recvr_nm": clean(row.get("recvr_nm")),
        "box_pack_no": row.get("box_pack_no"),
        "reg_dtm": row.get("reg_dtm"),
        "od_dtm": row.get("od_dtm"),
        "colct_yn": row.get("colct_yn"),
    }


def list_target_orders(page, stage: str = "shipReady", start_dt: str = "", end_dt: str = "",
                       channels=("cafe24", "kakao")) -> list:
    """카페24/카카오 등 대상 채널의 주문만 (미필터 조회 후 이름 분류)."""
    raw = list_orders(page, stage, start_dt, end_dt, size=500)
    rows = raw.get("data") or []
    out = []
    for r in rows:
        if channel_tag(r) in channels:
            out.append(normalize_order(r))
    return out


# 단계 키 → od_prgrs_state 코드 (OMS 확정값)
#   10 주문 → 20 주문서처리 → 30 출고준비 → 35 발송준비 → 40 발송대기
STAGE_STATE = {
    "order": "10",
    "putOrder": "20",
    "outReady": "30",
    "shipReady": "35",
    "shipWait": "40",
}


# ── 단계 전환 (쓰기) ───────────────────────────────────────────────

def advance_orders(page, od_snos, current_state: str, direction: str = "next") -> dict:
    """주문 단계 전환 (odStateUpdtChkProc). manage 경로라 확인창 없이 즉시 처리.

    current_state: 현재 od_prgrs_state ('10','20','30','35'...)
    direction: 'next'(다음단계) | 'before'(이전단계)
    od_snos: 주문일련번호 리스트
    """
    page.goto(OURBOX_BASE + "/om/order/order/orderList.do", wait_until="networkidle", timeout=20000)
    return page.evaluate(
        """async ({snos, cur, mode}) => {
            const r = await fetch('/om/order/odStateUpdtChkProc.do', {
                method:'POST',
                headers:{'Content-Type':'application/json; charset=UTF-8','AJAX':'true'},
                body: JSON.stringify({od_prgrs_state: cur, updt_mode: mode,
                                      od_snos: JSON.stringify(snos.map(String))})
            });
            const t = await r.text(); let b; try{b=JSON.parse(t);}catch(e){b=t.slice(0,400);}
            return {status:r.status, body:b};
        }""",
        {"snos": od_snos, "cur": str(current_state), "mode": direction},
    )


# ── 합포 기준 출고 (WMS 출고등록, 쓰기) ──────────────────────────────

def wms_combine_outbound(page, orders, out_reg_type: str = "02") -> dict:
    """합포 기준 출고 = WMS 출고등록 (omsOdReg.do).

    출고준비 주문(정규화 행)을 box_pack_no로 그룹핑해 합포장 단위로 묶어 등록.
    payload(실제 UI 캡처 기반):
      {boxPackNoList:[{total,box_pack_no,prod_cd[],od_sno[],item_cd[]}], dupProdCdList:[], outRegType:"02"}
    → WMS 출고등록 → 발송준비로 진행 + wms_out_req_dtm 설정.
    성공 판정: body.success_fail_yn == 'Y'.
    """
    from collections import OrderedDict
    groups = OrderedDict()
    for o in orders:
        bp = str(o.get("box_pack_no") or "")
        if not bp:
            continue
        g = groups.setdefault(bp, {"total": 0, "box_pack_no": bp, "prod_cd": [], "od_sno": [], "item_cd": []})
        g["total"] += 1
        g["prod_cd"].append(str(o.get("prod_cd") or ""))
        try:
            g["od_sno"].append(int(o.get("od_sno")))
        except Exception:
            g["od_sno"].append(o.get("od_sno"))
        g["item_cd"].append(str(o.get("item_cd") or ""))
    box_list = list(groups.values())
    if not box_list:
        return {"status": 0, "body": {"result": False, "message": "box_pack_no 없는 주문"}}
    page.goto(OURBOX_BASE + "/om/order/outReady/outReadyList.do", wait_until="networkidle", timeout=20000)
    res = page.evaluate(
        """async ({boxes, ort}) => {
            const r = await fetch('/wm/out/reg/ajax/omsOdReg.do', {
                method:'POST', headers:{'Content-Type':'application/json; charset=UTF-8','AJAX':'true'},
                body: JSON.stringify({boxPackNoList: boxes, dupProdCdList: [], outRegType: ort})
            });
            const t = await r.text(); let b; try{b=JSON.parse(t);}catch(e){b=t.slice(0,500);}
            return {status:r.status, body:b};
        }""",
        {"boxes": box_list, "ort": out_reg_type},
    )
    res["box_count"] = len(box_list)
    return res


# ── 발송인 자동 지정 (쓰기) ─────────────────────────────────────────

def get_senders(page) -> list:
    """발송인 목록 (senderList). [{sender_sno, sender_nm, pwn_default_yn, ...}]"""
    d = _get_json(page, "/om/ptn/sender/ajax/senderList.do?page=1&size=100")
    return (d.get("data") or []) if isinstance(d, dict) else []


def assign_sender(page, od_snos, sender_sno, user_info: str = "") -> dict:
    """선택 주문에 발송인 지정 (editSender.do)."""
    page.goto(OURBOX_BASE + "/om/order/putOrder/putOrderList.do", wait_until="networkidle", timeout=20000)
    return page.evaluate(
        """async ({snos, snoSender, ui}) => {
            const r = await fetch('/om/order/putOrder/ajax/editSender.do', {
                method:'POST', headers:{'Content-Type':'application/json; charset=utf-8','AJAX':'true'},
                body: JSON.stringify({senderSno: snoSender, mode:'select',
                                      odSnolist: JSON.stringify(snos.map(String)), user_info: ui})
            });
            const t = await r.text(); let b; try{b=JSON.parse(t);}catch(e){b=t.slice(0,300);}
            return {status:r.status, body:b};
        }""",
        {"snos": od_snos, "snoSender": sender_sno, "ui": user_info},
    )


def assign_default_sender(page, od_snos) -> dict:
    """기본 발송인(없으면 첫 발송인) 자동 지정."""
    senders = get_senders(page)
    if not senders:
        return {"ok": False, "error": "등록된 발송인이 없습니다."}
    default = next((s for s in senders if s.get("pwn_default_yn") == "Y"), senders[0])
    res = assign_sender(page, od_snos, default["sender_sno"])
    ok = isinstance(res.get("body"), dict) and bool(res["body"].get("result"))
    return {"ok": ok, "sender_nm": default.get("sender_nm"),
            "sender_sno": default.get("sender_sno"), "result": res}


# ── 주문 수집 (쓰기) ───────────────────────────────────────────────

COLCT_POPUP = "/om/sach/colct/colctListView.do?kind=order"


def collect_orders(page, mall_acc_snos, date_range: str = None) -> dict:
    """수집실행 버튼(colctConfirm) 재현 — 선택 몰계정 주문을 OMS로 수집.

    팝업 폼(orderColctForm)에 세션값(pwnCd/userId/userIp/corpCd/mallSvc)이
    이미 채워져 있으므로 그대로 읽어 mallAccSnoList/colct_sno/날짜만 덮어쓰고 POST.
    date_range: "YYYY-MM-DD HH:mm ~ YYYY-MM-DD HH:mm" (None이면 폼 기본값=최근7일)
    """
    page.goto(OURBOX_BASE + COLCT_POPUP, wait_until="networkidle", timeout=20000)
    return page.evaluate(
        """async ({snos, dateRange}) => {
            const f = document.getElementById('orderColctForm');
            if (!f) return {_error:'orderColctForm 없음'};
            const set = (n,v) => { const el=f.querySelector(`[name=${n}]`); if(el) el.value=v; };
            set('mallAccSnoList', snos.join(','));
            const uid = (f.querySelector('[name=userId]')||{}).value || '';
            set('colct_sno', String(new Date().getTime()) + '_' + uid);
            if (dateRange) set('srchColctDateRange', dateRange);
            const fd = new FormData(f); const obj = {};
            fd.forEach((v,k)=>obj[k]=v);
            const res = await fetch('https://api.ourbox.co.kr/ajax/colct/execColct.do', {
                method:'POST', headers:{'Content-Type':'application/json'},
                credentials:'include', body: JSON.stringify(obj)
            });
            const t = await res.text();
            let body; try { body = JSON.parse(t); } catch(e){ body = t.slice(0,400); }
            return {status: res.status, body, sent_snos: obj.mallAccSnoList, date_range: obj.srchColctDateRange};
        }""",
        {"snos": [str(s) for s in mall_acc_snos], "dateRange": date_range},
    )


def do_full_auto(oid: str, opw: str, start: str, end: str, channels=("cafe24", "kakao"),
                 do_collect: bool = True, max_passes: int = 5) -> dict:
    """한 세션에서 수집 → 다단계 전진(→발송준비)까지 자동 실행.

    발송준비(35)가 목표. 한 패스에서 30→,20→,10→ 역순으로 1단계씩만 올려
    같은 주문이 한 패스에 중복 전진하지 않게 한다. ⚠️ 운영 OMS 쓰기.
    반환: {collect, advance:[...], shipReady:[정규화 주문], counts}
    """
    import time as _t
    out = {"collect": None, "sender": [], "advance": [], "shipReady": []}
    with oms_session(oid, opw) as page:
        if do_collect:
            chs = list_collect_channels(page)
            snos = [c["mall_acc_sno"] for c in chs
                    if (c["is_cafe24"] and "cafe24" in channels) or (c["is_kakao"] and "kakao" in channels)]
            if snos:
                out["collect"] = collect_orders(page, snos)
                _t.sleep(4)  # 수집 반영 대기
        for p in range(max_passes):
            moved = 0
            for stage, code in [("outReady", "30"), ("putOrder", "20"), ("order", "10")]:
                rows = list_target_orders(page, stage, start, end, channels=channels)
                osnos = sorted({str(r["od_sno"]) for r in rows})
                if not osnos:
                    continue
                if stage == "outReady":
                    # 출고준비→발송준비: 합포 기준 출고(WMS 출고등록) — box_pack_no 그룹 단위
                    res = wms_combine_outbound(page, rows)
                    ok = isinstance(res.get("body"), dict) and res["body"].get("success_fail_yn") == "Y"
                    if ok:
                        moved += len(osnos)
                    out["advance"].append({"pass": p, "stage": "outReady(합포출고)", "count": len(osnos),
                                           "box_count": res.get("box_count"), "ok": ok,
                                           "msg": (res.get("body") or {}).get("message", "")})
                    continue
                # 주문서처리(20)→출고준비(30) 전 발송인 없는 건에 기본 발송인 자동 지정
                if stage == "putOrder":
                    sres = assign_default_sender(page, osnos)
                    out["sender"].append({"pass": p, "count": len(osnos),
                                          "sender_nm": sres.get("sender_nm"), "ok": sres.get("ok")})
                res = advance_orders(page, osnos, code, "next")
                ok = isinstance(res.get("body"), dict) and bool(res["body"].get("result"))
                if ok:
                    moved += len(osnos)
                out["advance"].append({"pass": p, "stage": stage, "count": len(osnos), "ok": ok,
                                       "msg": (res.get("body") or {}).get("message", "")})
            if moved == 0:
                break
        out["shipReady"] = list_target_orders(page, "shipReady", start, end, channels=channels)
        out["counts"] = {"shipReady": len(out["shipReady"])}
    return out


def do_collect(oid: str, opw: str, channels=("cafe24", "kakao"), date_range: str = None) -> dict:
    """대상 채널(카페24/카카오) 몰계정을 자동 탐지해 수집 실행."""
    with oms_session(oid, opw) as page:
        chs = list_collect_channels(page)
        snos, picked = [], []
        for c in chs:
            if (c["is_cafe24"] and "cafe24" in channels) or (c["is_kakao"] and "kakao" in channels):
                snos.append(c["mall_acc_sno"])
                picked.append({"mall_acc_sno": c["mall_acc_sno"], "mall_nm": c["mall_nm"]})
        if not snos:
            return {"ok": False, "error": "대상 채널(카페24/카카오)을 찾지 못함", "channels": chs}
        res = collect_orders(page, snos, date_range)
        ok = isinstance(res.get("body"), dict) and res["body"].get("result") in (True, "true")
        return {"ok": ok, "picked": picked, "result": res}


# ── 고수준 세션 작업 (CLI/서브프로세스에서 호출) ─────────────────────
# ⚠️ Windows에서 uvicorn(asyncio) 프로세스 안에서는 Playwright 동기 API가
#    deadlock 하므로, 백엔드는 이 함수들을 별도 subprocess(run_cli)로 호출한다.

def fetch_channels(oid: str, opw: str) -> dict:
    with oms_session(oid, opw) as page:
        return {"channels": list_collect_channels(page)}


def fetch_overview(oid: str, opw: str, start: str, end: str, channels: tuple,
                   stage_labels: dict) -> dict:
    with oms_session(oid, opw) as page:
        chs = list_collect_channels(page)
        stages_out, counts = {}, {}
        for st in STAGES:
            rows = list_target_orders(page, st, start, end, channels=channels)
            stages_out[st] = rows
            counts[st] = len(rows)
        return {
            "from_date": start, "to_date": end,
            "channels": chs, "target_channels": list(channels),
            "stage_labels": stage_labels, "stages": stages_out, "counts": counts,
        }


def fetch_orders(oid: str, opw: str, stage: str, start: str, end: str, channels: tuple) -> dict:
    with oms_session(oid, opw) as page:
        rows = list_target_orders(page, stage, start, end, channels=channels)
        return {"stage": stage, "label": STAGE_LABELS.get(stage, stage),
                "from_date": start, "to_date": end, "orders": rows}


def run_cli():
    """subprocess 진입점. argv[1]=JSON 명령 → stdout에 JSON 결과 출력.

    명령 예: {"cmd":"overview","oid":..,"opw":..,"start":..,"end":..,"channels":["cafe24","kakao"]}
    출력: {"ok":true,"data":{...}}  또는  {"ok":false,"error":"..."}
    """
    import json as _json
    import sys as _sys
    try:
        req = _json.loads(_sys.argv[2])
        cmd = req["cmd"]
        oid, opw = req["oid"], req["opw"]
        if cmd == "channels":
            data = fetch_channels(oid, opw)
        elif cmd == "overview":
            data = fetch_overview(oid, opw, req["start"], req["end"],
                                  tuple(req.get("channels", ["cafe24", "kakao"])), STAGE_LABELS)
        elif cmd == "orders":
            data = fetch_orders(oid, opw, req["stage"], req["start"], req["end"],
                                tuple(req.get("channels", ["cafe24", "kakao"])))
        elif cmd == "collect":
            data = do_collect(oid, opw, tuple(req.get("channels", ["cafe24", "kakao"])),
                              req.get("date_range"))
        elif cmd == "advance":
            with oms_session(oid, opw) as page:
                data = advance_orders(page, req["od_snos"], req["current_state"],
                                      req.get("direction", "next"))
        elif cmd == "auto":
            data = do_full_auto(oid, opw, req["start"], req["end"],
                                tuple(req.get("channels", ["cafe24", "kakao"])),
                                req.get("do_collect", True))
        else:
            raise ValueError(f"알 수 없는 명령: {cmd}")
        print(_json.dumps({"ok": True, "data": data}, ensure_ascii=False))
    except BaseException as e:
        print(_json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        _sys.exit(1)


# ── 디버그 프로브 (read-only) ──────────────────────────────────────

if __name__ == "__main__":
    import json, sys
    if len(sys.argv) >= 3 and sys.argv[1] == "--cli":
        run_cli()
        sys.exit(0)
    cfg = json.load(open("config.json", encoding="utf-8"))
    OID, OPW = cfg["ourbox_id"], cfg["ourbox_pw"]

    def show_orders(label, data, n=3):
        print("\n" + "=" * 60); print(label); print("=" * 60)
        if not isinstance(data, dict) or data.get("_error"):
            print("  ERROR", data); return
        rows = data.get("data") or []
        print(f"  rows={len(rows)} last_page={data.get('last_page')}")
        for r in rows[:n]:
            print(f"  od_sno={r.get('od_sno')} 단계={r.get('od_prgrs_state_nm')}({r.get('od_prgrs_state')}) "
                  f"채널={r.get('sach_nm')}/{r.get('mall_nm')} 상품={str(r.get('prod_nm'))[:20]} "
                  f"수량={r.get('od_qty')} 수령={r.get('recvr_nm')} box={r.get('box_pack_no')} prod_cd={r.get('prod_cd')}")

    with oms_session(OID, OPW) as page:
        print("로그인 OK")
        chs = list_collect_channels(page)
        print(f"\n수집 채널 {len(chs)}개:")
        for c in chs:
            tag = "★카페24" if c["is_cafe24"] else ("★카카오" if c["is_kakao"] else "")
            print(f"  sno={c['mall_acc_sno']} {c['sach_cd']} | {c['mall_nm']} | {c['acc_alias_nm']} {tag}")
        cafe24 = next((c for c in chs if c["is_cafe24"]), None)
        kakao = next((c for c in chs if c["is_kakao"]), None)
        # 카페24/카카오 채널 주문을 각 단계에서 조회 (최근 30일)
        s, e = "2026-06-01", "2026-06-30"
        for ch, nm in [(cafe24, "카페24"), (kakao, "카카오")]:
            if not ch:
                print(f"\n[{nm}] 채널 없음"); continue
            for st in ["order", "shipReady"]:
                show_orders(f"[{nm}/{ch['sach_cd']}] {st} ({s}~{e})",
                            list_orders(page, st, s, e, channel_cd=ch["sach_cd"]))
