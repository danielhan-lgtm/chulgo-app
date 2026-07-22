"""OB(아워박스) 주문 자동화 라우터 — 카페24/카카오 주문 수집→단계진행→BH 출고등록.

Phase 1: 읽기 전용 (채널 목록 + 단계별 대상주문 조회).
수집/단계전환/BH등록(쓰기)은 이후 단계에서 추가.

⚠️ Windows에서 uvicorn(asyncio ProactorEventLoop) 프로세스 안에서는 Playwright 동기
   API가 Chromium 서브프로세스 생성 중 deadlock 한다(스레드로도 회피 불가). 따라서
   OMS 작업은 ourbox_oms_orders.py를 **별도 파이썬 subprocess(--cli)** 로 띄워 격리한다.
   동시 OMS 로그인 충돌 방지용 전역 락 + 짧은 TTL 캐시로 중복호출/연타를 흡수한다.
"""
import sys
import os
import re
import json
import time
import threading
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import ourbox_oms_orders as oms  # STAGES/STAGE_LABELS 참조용 (Playwright 미실행)
import receiving_db as db
import utils_core as U
import state

router = APIRouter()

# OB 주문 BH 출고등록 추적 테이블 (중복등록 방지)
with db._conn() as _c:
    _c.execute("""
        CREATE TABLE IF NOT EXISTS ob_order_reg (
            od_sno TEXT PRIMARY KEY,
            channel TEXT,
            mall_od_no TEXT,
            bh_tx_id TEXT,
            items_json TEXT,
            qty INTEGER,
            status TEXT DEFAULT 'registered',
            registered_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    _c.commit()

STAGES = oms.STAGES
STAGE_LABELS = oms.STAGE_LABELS
_OMS_SCRIPT = os.path.join(_ROOT, "ourbox_oms_orders.py")

_oms_lock = threading.Lock()
_cache: dict = {}
_CACHE_TTL = 20
_SUBPROC_TIMEOUT = 150       # 초 (로그인+5단계 조회 여유)
_SUBPROC_TIMEOUT_AUTO = 600  # 초 — auto(수집+다패스 전진+합포출고)는 조회보다 훨씬 오래 걸림


def _load_cfg() -> dict:
    try:
        with open(os.path.join(_ROOT, "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _creds():
    cfg = _load_cfg()
    oid, opw = cfg.get("ourbox_id", ""), cfg.get("ourbox_pw", "")
    # 저장 세션(ourbox_session.json)이 있으면 비밀번호 없이도 동작(세션 재사용).
    # 비번은 세션 만료 시 폴백 로그인용이라 필수 아님.
    has_session = os.path.exists(_SESSION_FILE)
    if not oid or (not opw and not has_session):
        raise HTTPException(400, "아워박스 OMS 로그인 정보(ourbox_id/pw)가 설정되지 않았습니다.")
    return oid, opw


def _run_oms(req: dict, timeout: int = _SUBPROC_TIMEOUT) -> dict:
    """ourbox_oms_orders.py를 subprocess로 실행하고 JSON 결과 반환."""
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", _OMS_SCRIPT, "--cli", json.dumps(req, ensure_ascii=False)],
            cwd=_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"OMS 작업({req.get('cmd')})이 {timeout}초를 초과해 중단됨")
    out = (proc.stdout or "").strip()
    # 마지막 JSON 라인 추출 (Playwright 로그 등 섞일 수 있음)
    payload = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line); break
            except Exception:
                continue
    if payload is None:
        err = (proc.stderr or "")[-300:] or out[-300:] or "출력 없음"
        raise RuntimeError(f"OMS 프로세스 응답 파싱 실패: {err}")
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "알 수 없는 오류"))
    return payload["data"]


def _cached(key: str, req: dict) -> dict:
    now = time.time()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    with _oms_lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        value = _run_oms(req)
        _cache[key] = (time.time() + _CACHE_TTL, value)
        return value


def _date_range(days: int):
    today = datetime.now()
    return (today - timedelta(days=days)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def _channel_list(channels: str):
    return [c.strip() for c in channels.split(",") if c.strip()]


@router.get("/channels")
def get_channels():
    oid, opw = _creds()
    try:
        return _cached("channels", {"cmd": "channels", "oid": oid, "opw": opw})
    except Exception as e:
        raise HTTPException(400, f"채널 조회 실패: {e}")


@router.get("/overview")
def get_overview(days: int = 30, channels: str = "cafe24,kakao"):
    oid, opw = _creds()
    start, end = _date_range(days)
    try:
        return _cached(f"overview:{days}:{channels}", {
            "cmd": "overview", "oid": oid, "opw": opw,
            "start": start, "end": end, "channels": _channel_list(channels),
        })
    except Exception as e:
        raise HTTPException(400, f"주문 조회 실패: {e}")


@router.get("/orders")
def get_orders(stage: str = "shipReady", days: int = 30, channels: str = "cafe24,kakao"):
    if stage not in STAGES:
        raise HTTPException(400, f"알 수 없는 단계: {stage}")
    oid, opw = _creds()
    start, end = _date_range(days)
    try:
        return _cached(f"orders:{stage}:{days}:{channels}", {
            "cmd": "orders", "oid": oid, "opw": opw, "stage": stage,
            "start": start, "end": end, "channels": _channel_list(channels),
        })
    except Exception as e:
        raise HTTPException(400, f"주문 조회 실패: {e}")


# ── 쓰기: 수집 / 단계전환 / BH 출고등록 ─────────────────────────────

def _bust_cache():
    _cache.clear()


class CollectReq(BaseModel):
    channels: str = "cafe24,kakao"
    date_range: str | None = None


@router.post("/collect")
def collect(body: CollectReq):
    """카페24/카카오 주문 수집 실행 (execColct)."""
    oid, opw = _creds()
    try:
        with _oms_lock:
            res = _run_oms({"cmd": "collect", "oid": oid, "opw": opw,
                            "channels": _channel_list(body.channels), "date_range": body.date_range})
        _bust_cache()
        state.add_log("success" if res.get("ok") else "warning",
                      f"OB 주문수집 실행 ({body.channels})",
                      json.dumps(res.get("picked", []), ensure_ascii=False), source="ob-orders")
        return res
    except Exception as e:
        raise HTTPException(400, f"수집 실패: {e}")


class AdvanceReq(BaseModel):
    od_snos: list[str]
    current_state: str           # '10'|'20'|'30'|'35'
    direction: str = "next"      # next|before


@router.post("/advance")
def advance(body: AdvanceReq):
    """주문 단계 전환 (odStateUpdtChkProc). ⚠️ 운영 OMS 쓰기."""
    if not body.od_snos:
        raise HTTPException(400, "od_snos 비어있음")
    oid, opw = _creds()
    try:
        with _oms_lock:
            res = _run_oms({"cmd": "advance", "oid": oid, "opw": opw, "od_snos": body.od_snos,
                            "current_state": body.current_state, "direction": body.direction})
        _bust_cache()
        ok = isinstance(res.get("body"), dict) and res["body"].get("result")
        state.add_log("success" if ok else "warning",
                      f"OB 단계전환 {body.current_state}→{body.direction} ({len(body.od_snos)}건)",
                      (res.get("body") or {}).get("message", ""), source="ob-orders")
        return res
    except Exception as e:
        raise HTTPException(400, f"단계전환 실패: {e}")


# 채널 → BH 거래처 이름 매칭 키워드
_CHANNEL_PARTNER_KW = {"cafe24": ["카페24", "cafe24"], "kakao": ["카카오", "kakao"]}


def _resolve_partners(token, channels):
    """channel → {id, name}. config(ob_channel_partner)로 수동 지정 우선, 없으면 이름 자동매칭."""
    cfg = _load_cfg()
    override = cfg.get("ob_channel_partner") or {}
    out, partners = {}, None
    for ch in set(channels):
        ov = str(override.get(ch) or "").strip()
        if ov:
            out[ch] = {"id": int(ov), "name": "(수동지정)"}
            continue
        if partners is None:
            try:
                partners = U.fetch_partners(token)
            except Exception:
                partners = []
        kws = [k.lower() for k in _CHANNEL_PARTNER_KW.get(ch, [])]
        m = next((p for p in partners if any(k in str(p.get("name", "")).lower() for k in kws)), None)
        if m:
            out[ch] = {"id": m["id"], "name": m.get("name")}
    return out


def _resolve_bh(conn, prod_cd, qty: int):
    """OB 상품코드 → BH (sku, qty) 리스트. set_bom 우선 전개, 없으면 product_mapping.

    반환: (items, unmapped) — items=[(sku, qty)], unmapped=True면 매핑 없음
    """
    cur = conn.cursor()
    cur.execute("SELECT component_sku, qty_per_set FROM set_bom WHERE set_sku=?", (str(prod_cd),))
    boms = cur.fetchall()
    if boms:
        return [(b["component_sku"], qty * (b["qty_per_set"] or 1)) for b in boms], False
    cur.execute("SELECT boxhero_sku FROM product_mapping WHERE ourbox_prod_cd=?", (str(prod_cd),))
    pm = cur.fetchone()
    if pm and pm["boxhero_sku"]:
        return [(pm["boxhero_sku"], qty)], False
    return [], True


def _notify_unmapped(unmapped: list):
    """미매핑 주문 발생 시 슬랙 알림 — 같은 상품코드는 하루 1회만 (조용한 누락 방지).

    미매핑 주문은 발송준비→발송대기로 넘어가면 자동 사이클이 영영 못 잡으므로
    즉시 알려서 매핑 추가 + 수동 등록을 유도한다. 알림 실패는 등록 흐름에 영향 없음.
    """
    try:
        cfg = _load_cfg()
        slack = cfg.get("slack_token", "")
        if not slack or not unmapped:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        prods = {}  # prod_cd → {nm, ods[]}
        for u in unmapped:
            for it in u.get("items", []):
                pc = str(it.get("prod_cd") or "")
                p = prods.setdefault(pc, {"nm": it.get("prod_nm") or "", "ods": []})
                p["ods"].append(str(u.get("od_sno")))
        conn = db._conn()
        try:
            cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS ob_unmapped_notified (
                prod_cd TEXT, notified_date TEXT, PRIMARY KEY (prod_cd, notified_date))""")
            fresh = {}
            for pc, info in prods.items():
                cur.execute("SELECT 1 FROM ob_unmapped_notified WHERE prod_cd=? AND notified_date=?",
                            (pc, today))
                if cur.fetchone():
                    continue
                fresh[pc] = info
                cur.execute("INSERT OR IGNORE INTO ob_unmapped_notified VALUES (?,?)", (pc, today))
            conn.commit()
        finally:
            conn.close()
        if not fresh:
            return
        ch = str(cfg.get("slack_outbound_channel") or "물류_출고").strip()
        channel_id = ch
        if not re.fullmatch(r"[CGD][A-Z0-9]{6,}", ch):
            channel_id = U.fetch_slack_channels(slack).get(ch, "")
        if not channel_id:
            return
        lines = [f"⚠️ *OB 주문 미매핑 {len(fresh)}종* — BoxHero 출고등록에서 제외됨"]
        for pc, info in fresh.items():
            ods = ", ".join(info["ods"][:5])
            lines.append(f"• `{pc}` {info['nm']} (주문 {ods})")
        lines.append("→ *상품 매핑* 페이지에서 세트 구성/매핑 추가 필요. 이미 발송대기로 넘어간 주문은 수동 등록 필요.")
        U.slack_post_message(slack, channel_id, "\n".join(lines))
        state.add_log("warning", f"OB 미매핑 {len(fresh)}종 슬랙 알림", str(list(fresh)), source="ob-orders")
    except Exception as e:
        state.add_log("error", "OB 미매핑 슬랙 알림 실패", str(e)[:150], source="ob-orders")


def _register_orders(orders, dry_run: bool) -> dict:
    """발송준비 주문을 BH 출고등록 — 채널별로 취합해 채널당 1 트랜잭션.

    set_bom 전개 + product_mapping으로 SKU 해석, 채널별 SKU 수량 합산 후 1건 등록.
    포함된 모든 od_sno는 같은 bh_tx_id로 추적(중복방지). 미매핑 주문은 제외+로그.
    """
    cfg = _load_cfg()
    token, loc = cfg.get("api_token", ""), cfg.get("selected_location_id")
    if not dry_run and (not token or not loc):
        raise HTTPException(400, "BH 토큰/출고위치(selected_location_id) 설정 필요")

    by_od = defaultdict(list)
    for o in orders:
        by_od[str(o["od_sno"])].append(o)

    conn = db._conn()
    cur = conn.cursor()
    results = {"registered": [], "skipped_done": [], "unmapped": [], "errors": []}

    # 1) 채널별로 취합 (이미 등록/미매핑 제외)
    per_ch = defaultdict(lambda: {"sku_qty": defaultdict(float), "ods": []})
    for od_sno, lines in by_od.items():
        cur.execute("SELECT bh_tx_id FROM ob_order_reg WHERE od_sno=?", (od_sno,))
        if cur.fetchone():
            results["skipped_done"].append(od_sno)
            continue
        sku_qty = defaultdict(float)
        unmapped = []
        for ln in lines:
            qty = int(ln.get("od_qty") or 0)
            items, unm = _resolve_bh(conn, ln.get("prod_cd"), qty)
            if unm:
                unmapped.append({"prod_cd": ln.get("prod_cd"), "prod_nm": ln.get("prod_nm")})
            for sku, q in items:
                sku_qty[sku] += q
        if unmapped:
            results["unmapped"].append({"od_sno": od_sno, "items": unmapped})
            continue
        ch = lines[0].get("channel") or "기타"
        agg = per_ch[ch]
        for sku, q in sku_qty.items():
            agg["sku_qty"][sku] += q
        agg["ods"].append({"od_sno": od_sno, "mall_od_no": lines[0].get("mall_od_no"),
                           "items": {sku: int(q) for sku, q in sku_qty.items()}})

    sku_to_id = U.fetch_all_items(token) if (not dry_run and per_ch) else {}
    partner_map = _resolve_partners(token, per_ch.keys()) if per_ch else {}
    # (슬랙 출고 알림은 slack_outbound 폴러가 BH 트랜잭션 감지해 처리 — 여기서 중복 포스팅 안 함)

    # 2) 채널별 1건 등록 (채널 거래처 partner_id 포함)
    for ch, agg in per_ch.items():
        items_payload = [{"sku": sku, "quantity": int(q)} for sku, q in agg["sku_qty"].items() if int(q) > 0]
        od_snos = [o["od_sno"] for o in agg["ods"]]
        partner = partner_map.get(ch)
        if dry_run:
            results["registered"].append({"channel": ch, "dry_run": True, "od_count": len(od_snos),
                                           "partner": partner, "items": items_payload})
            continue
        try:
            bh_items, miss = [], []
            for it in items_payload:
                iid = sku_to_id.get(str(it["sku"]).strip())
                (bh_items.append({"item_id": iid, "quantity": -it["quantity"]}) if iid
                 else miss.append(it["sku"]))
            if not bh_items:
                results["errors"].append({"channel": ch, "error": f"BH 미등록 SKU {miss}"})
                continue
            payload = {"type": "out", "to_location_id": loc, "items": bh_items,
                       "memo": f"{ch} 자동출고 통합 {len(od_snos)}건 (od:{od_snos[0]}~{od_snos[-1]})"}
            if partner and partner.get("id"):
                payload["partner_id"] = partner["id"]
            tx = U.post_transaction(token, payload)
            tx_id = str(tx.get("id", ""))
            for o in agg["ods"]:
                cur.execute("""INSERT OR REPLACE INTO ob_order_reg
                    (od_sno, channel, mall_od_no, bh_tx_id, items_json, qty, status)
                    VALUES (?,?,?,?,?,?, 'registered')""",
                    (o["od_sno"], ch, o["mall_od_no"], tx_id,
                     json.dumps(o["items"], ensure_ascii=False), int(sum(o["items"].values()))))
            conn.commit()
            results["registered"].append({"channel": ch, "tx_id": tx_id, "od_count": len(od_snos),
                                           "partner": partner, "items": items_payload})
            state.add_log("success", f"OB→BH 취합 출고등록 [{ch}] {len(od_snos)}건",
                          f"tx={tx_id} | 거래처={(partner or {}).get('name')} | {items_payload}",
                          source="ob-orders")
        except Exception as e:
            results["errors"].append({"channel": ch, "error": str(e)[:150]})
    conn.close()
    if not dry_run and results["unmapped"]:
        _notify_unmapped(results["unmapped"])
    results["summary"] = {
        "tx_registered": len([r for r in results["registered"]]),
        "orders_included": sum(r.get("od_count", 0) for r in results["registered"]),
        "skipped_done": len(results["skipped_done"]),
        "unmapped": len(results["unmapped"]),
        "errors": len(results["errors"]),
    }
    results["dry_run"] = dry_run
    return results


@router.post("/register-bh")
def register_bh(days: int = 30, channels: str = "cafe24,kakao", dry_run: bool = True):
    """발송준비(35) 단계 카페24/카카오 주문을 BH 출고등록.

    set_bom 전개 + product_mapping으로 SKU 해석, od_sno 단위 1 트랜잭션, 중복방지.
    dry_run=True(기본)면 실제 전송 없이 해석 결과만 미리보기.
    """
    oid, opw = _creds()
    start, end = _date_range(days)
    with _oms_lock:
        data = _run_oms({"cmd": "orders", "oid": oid, "opw": opw, "stage": "shipReady",
                         "start": start, "end": end, "channels": _channel_list(channels)})
    return _register_orders(data.get("orders", []), dry_run)


def _do_auto_run(channels: str, days: int, do_collect: bool, do_register: bool, dry_run: bool) -> dict:
    """완전 자동화 1사이클: 수집 → 발송준비까지 전진 → BH 출고등록.

    무인 스케줄러와 수동 확인실행이 공유. dry_run이면 현재 상태 미리보기만.
    비매칭(nomatch) 주문은 단계 진행에서 자연히 제외(사용자 별도 처리).
    """
    oid, opw = _creds()
    start, end = _date_range(days)
    ch_list = _channel_list(channels)

    if dry_run:
        # 미리보기: 현재 단계별 카페24/카카오 주문 수만 (쓰기 없음)
        with _oms_lock:
            ov = _run_oms({"cmd": "overview", "oid": oid, "opw": opw,
                           "start": start, "end": end, "channels": ch_list})
        ship = ov.get("stages", {}).get("shipReady", [])
        reg = _register_orders(ship, dry_run=True)
        return {"dry_run": True, "counts": ov.get("counts"), "register_preview": reg}

    # 실제 실행: 수집 + 전진 (한 세션)
    with _oms_lock:
        auto = _run_oms({"cmd": "auto", "oid": oid, "opw": opw, "start": start, "end": end,
                         "channels": ch_list, "do_collect": do_collect},
                        timeout=_SUBPROC_TIMEOUT_AUTO)
    _bust_cache()
    result = {"dry_run": False, "collect": auto.get("collect"), "sender": auto.get("sender"),
              "advance": auto.get("advance"), "shipReady_count": len(auto.get("shipReady", []))}
    if do_register:
        result["register"] = _register_orders(auto.get("shipReady", []), dry_run=False)
    state.add_log("success", "OB 완전자동화 1사이클 실행",
                  f"발송준비 {result['shipReady_count']}건 · 등록 {result.get('register',{}).get('summary',{})}",
                  source="ob-orders")
    return result


class AutoRunReq(BaseModel):
    channels: str = "cafe24,kakao"
    days: int = 30
    do_collect: bool = True
    do_register: bool = True
    dry_run: bool = True


@router.post("/auto-run")
def auto_run(body: AutoRunReq):
    """완전 자동화 1사이클 실행 (수집→전진→BH등록). dry_run 기본 미리보기."""
    try:
        return _do_auto_run(body.channels, body.days, body.do_collect, body.do_register, body.dry_run)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"자동실행 실패: {e}")


# ── 무인 자동화 스케줄러 ────────────────────────────────────────────
# config.json:
#   ob_auto_enabled(bool), ob_auto_channels, ob_auto_days
#   ob_auto_mode: 'times'(하루 지정시각) | 'interval'(N분 주기)
#   ob_auto_times: 콤마구분 "HH:MM" (예: "09:00,13:00,17:00") — times 모드
#   ob_auto_interval_min(int) — interval 모드
_AUTO_DEFAULTS = {"ob_auto_enabled": False, "ob_auto_mode": "times",
                  "ob_auto_times": "09:00,13:00,17:00", "ob_auto_interval_min": 30,
                  "ob_auto_channels": "cafe24,kakao", "ob_auto_days": 30}
_auto_status = {"last_run": None, "last_result": None, "last_error": None,
                "running": False, "next_runs": []}


class AutoConfigReq(BaseModel):
    enabled: bool | None = None
    mode: str | None = None            # 'times' | 'interval'
    times: str | None = None           # "09:00,13:00,17:00"
    interval_min: int | None = None
    channels: str | None = None
    days: int | None = None


def _auto_cfg():
    cfg = _load_cfg()
    # 저장값이 None(JSON null)이면 기본값으로 대체 (UI가 일부 키만 patch한 경우 대비)
    return {k: (cfg.get(k) if cfg.get(k) is not None else v) for k, v in _AUTO_DEFAULTS.items()}


def _parse_times(s: str):
    """"09:00,13:00" → ['09:00','13:00'] (유효한 HH:MM만, 정렬)."""
    out = []
    for t in str(s or "").replace(" ", "").split(","):
        if not t:
            continue
        try:
            h, m = t.split(":")
            h, m = int(h), int(m)
            if 0 <= h < 24 and 0 <= m < 60:
                out.append(f"{h:02d}:{m:02d}")
        except Exception:
            continue
    return sorted(set(out))


@router.get("/auto-config")
def get_auto_config():
    c = _auto_cfg()
    _auto_status["next_runs"] = _parse_times(c.get("ob_auto_times")) if c.get("ob_auto_mode") == "times" else []
    return {**c, "status": _auto_status}


@router.post("/auto-config")
def set_auto_config(body: AutoConfigReq):
    patch = {}
    if body.enabled is not None:
        patch["ob_auto_enabled"] = body.enabled
    if body.mode in ("times", "interval"):
        patch["ob_auto_mode"] = body.mode
    if body.times is not None:
        patch["ob_auto_times"] = ",".join(_parse_times(body.times))
    if body.interval_min is not None:
        patch["ob_auto_interval_min"] = max(5, int(body.interval_min))
    if body.channels is not None:
        patch["ob_auto_channels"] = body.channels
    if body.days is not None:
        patch["ob_auto_days"] = int(body.days)
    if patch:
        U.save_config(patch)
    return get_auto_config()


def _run_cycle(c: dict):
    """완전자동화 1사이클 실행 + 상태 갱신.

    일시 오류(사이트 지연, Playwright 타임아웃 등) 대비 90초 간격 최대 3회 시도.
    등록은 ob_order_reg로 od_sno별 중복 방지되므로 재시도 안전.
    """
    _auto_status["running"] = True
    try:
        last_err = None
        for attempt in range(3):
            try:
                res = _do_auto_run(c["ob_auto_channels"], int(c["ob_auto_days"]),
                                   do_collect=True, do_register=True, dry_run=False)
                _auto_status.update(last_run=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    last_result=res.get("register", {}).get("summary"), last_error=None)
                return
            except Exception as e:
                last_err = e
                state.add_log("error", f"OB 완전자동화 시도 {attempt + 1}/3 실패",
                              str(e)[:300], source="ob-orders")
                if attempt < 2:
                    time.sleep(90)
        _auto_status.update(last_run=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            last_error=str(last_err)[:200])
    finally:
        _auto_status["running"] = False


def _auto_scheduler_loop():
    """30초마다 점검.
    times 모드: 오늘 '지난 지정시각' 중 아직 안 돈 게 있으면 실행(catch-up). 백엔드가 그 분에
      꺼져 있었어도 켜지면 놓친 슬롯을 따라잡음. 실행 이력은 config(ob_auto_last_fired)에 저장해
      재시작해도 같은 슬롯을 중복 실행하지 않음.
    interval 모드: N분 경과 시 실행.
    """
    last_interval = 0.0
    while True:
        try:
            c = _auto_cfg()
            if c.get("ob_auto_enabled"):
                now = datetime.now()
                mode = c.get("ob_auto_mode", "times")
                if mode == "times":
                    today = now.strftime("%Y-%m-%d")
                    hm = now.strftime("%H:%M")
                    lastf = _load_cfg().get("ob_auto_last_fired") or {}
                    for slot in _parse_times(c.get("ob_auto_times")):
                        if hm >= slot and lastf.get(slot) != today:
                            lastf[slot] = today
                            U.save_config({"ob_auto_last_fired": lastf})  # 실행 전 기록(중복방지)
                            _run_cycle(c)
                            break  # 한 틱에 하나씩
                else:  # interval
                    interval_sec = max(5, int(c.get("ob_auto_interval_min", 30))) * 60
                    if time.time() - last_interval >= interval_sec:
                        last_interval = time.time()
                        _run_cycle(c)
        except Exception:
            pass
        time.sleep(30)


def start_auto_scheduler():
    t = threading.Thread(target=_auto_scheduler_loop, daemon=True, name="ob-auto")
    t.start()


# ── 아워박스 세션 관리 (상태 확인 / keep-alive / 재로그인) ─────────────
# 자동화는 ourbox_session.json 세션을 재사용한다 (ID/PW 로그인은 CAPTCHA로 막힘).
# keep-alive가 10분 간격으로 세션을 살려두고, 만료되면 슬랙으로 즉시 알린다.
_SESSION_FILE = os.path.join(_ROOT, "ourbox_session.json")
_SESSION_LOGIN_SCRIPT = os.path.join(_ROOT, "아워박스_세션로그인.py")
_session_status = {"ok": None, "detail": "미확인", "checked_at": None}
_session_expiry_notified = False
_login_proc = None


def check_ourbox_session(notify_on_expire: bool = False) -> dict:
    """저장 세션으로 OMS 메인 진입을 시도해 유효성 확인 + 세션 유휴시간 갱신.

    만료 감지 시(1회): 슬랙 알림 + 근무시간이면 로그인 창 자동 오픈.
    복구 감지 시(만료→정상 전환): 그날 실패했던 자동화 사이클을 자동 재실행.
    """
    global _session_expiry_notified
    import requests as _rq
    ok, detail = False, ""
    prev_ok = _session_status.get("ok")
    try:
        if not os.path.exists(_SESSION_FILE):
            detail = "세션 파일 없음 — 아워박스_세션로그인.py 실행 필요"
        else:
            with open(_SESSION_FILE, encoding="utf-8") as f:
                cookies = {c["name"]: c["value"] for c in (json.load(f).get("cookies") or [])}
            if not cookies:
                detail = "세션 쿠키 없음"
            else:
                # 세션이 끊겨도 main.do는 error.do 리다이렉트로 200이 옴 —
                # 인증 필요한 ajax의 상태코드(200=정상/401=만료)로 판정 + 세션 유휴시간 갱신
                r = _rq.get("https://oms.ourbox.co.kr/om/sach/colct/ajax/selOdColctChList.do?mall_svc=order",
                            cookies=cookies, headers={"AJAX": "true"}, timeout=15)
                ok = r.status_code == 200
                detail = "정상" if ok else f"만료됨 (HTTP {r.status_code})"
    except Exception as e:
        # 일시 네트워크 오류는 만료로 취급하지 않음 (이전 상태 유지)
        _session_status.update(detail=f"확인 실패(네트워크): {str(e)[:80]}",
                               checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return dict(_session_status)

    if ok:
        _session_expiry_notified = False
        if prev_ok is False:
            _rerun_failed_cycle_after_recovery()
    elif notify_on_expire and not _session_expiry_notified and not _expiry_recently_handled():
        _session_expiry_notified = True
        U.save_config({"ourbox_expiry_notified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        state.add_log("warning", "아워박스 세션 만료 감지",
                      "다음 자동화는 ID/PW 로그인 폴백 — CAPTCHA로 실패 가능", source="ourbox-session")
        window_opened = _launch_login_window() if 8 <= datetime.now().hour < 19 else False
        _notify_session_expired(detail, window_opened)
    _session_status.update(ok=ok, detail=detail,
                           checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return dict(_session_status)


def _expiry_recently_handled(minutes: int = 120) -> bool:
    """최근 N분 내 만료 알림/창 오픈을 이미 했는지 (백엔드 재시작에도 유지 — 알림 스팸 방지)."""
    try:
        ts = _load_cfg().get("ourbox_expiry_notified_at")
        if not ts:
            return False
        return datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") < timedelta(minutes=minutes)
    except Exception:
        return False


def _rerun_failed_cycle_after_recovery():
    """세션 복구(만료→정상) 감지 시, 오늘 실패한 자동화 사이클을 1회 자동 재실행."""
    try:
        c = _auto_cfg()
        if not c.get("ob_auto_enabled") or not _auto_status.get("last_error") or _auto_status.get("running"):
            return
        state.add_log("success", "아워박스 세션 복구 감지 — 실패했던 자동화 재실행",
                      f"마지막 오류: {str(_auto_status.get('last_error'))[:100]}", source="ourbox-session")
        threading.Thread(target=_run_cycle, args=(c,), daemon=True, name="ob-auto-recovery").start()
    except Exception:
        pass


def _launch_login_window() -> bool:
    """세션 재로그인 창 실행 (이미 열려 있으면 스킵). 성공 여부 반환."""
    global _login_proc
    try:
        if _login_proc is not None and _login_proc.poll() is None:
            return True
        if not os.path.exists(_SESSION_LOGIN_SCRIPT):
            return False
        CREATE_NO_WINDOW = 0x08000000
        _login_proc = subprocess.Popen([sys.executable, "-X", "utf8", _SESSION_LOGIN_SCRIPT],
                                       cwd=_ROOT, creationflags=CREATE_NO_WINDOW)
        state.add_log("info", "아워박스 세션 재로그인 창 자동 실행", "", source="ourbox-session")
        return True
    except Exception:
        return False


def _notify_session_expired(detail: str, window_opened: bool = False):
    """세션 만료를 슬랙으로 알림 (만료 이벤트당 1회)."""
    try:
        cfg = _load_cfg()
        slack = cfg.get("slack_token", "")
        if not slack:
            return
        ch = str(cfg.get("slack_outbound_channel") or "물류_출고").strip()
        channel_id = ch
        if not re.fullmatch(r"[CGD][A-Z0-9]{6,}", ch):
            channel_id = U.fetch_slack_channels(slack).get(ch, "")
        if not channel_id:
            return
        action = ("→ 사무실 PC에 *로그인 창(Chrome)을 자동으로 띄웠습니다.* 그 창에서 로그인만 해주세요.\n"
                  "   (창이 닫혔으면 앱 *OB 주문* 페이지 → *세션 재로그인* 버튼)"
                  if window_opened else
                  "→ 앱 *OB 주문* 페이지의 *세션 재로그인* 버튼을 누르고 뜨는 창에서 로그인해 주세요.")
        U.slack_post_message(slack, channel_id,
                             f"🔒 *아워박스 세션 만료* ({detail})\n"
                             f"{action}\n"
                             "로그인하면 실패했던 자동화가 자동으로 다시 실행됩니다.")
    except Exception as e:
        state.add_log("error", "아워박스 세션만료 슬랙알림 실패", str(e)[:150], source="ourbox-session")


def _session_keepalive_loop():
    while True:
        try:
            check_ourbox_session(notify_on_expire=True)
        except Exception:
            pass
        time.sleep(600)


def start_session_keepalive():
    threading.Thread(target=_session_keepalive_loop, daemon=True, name="ourbox-keepalive").start()


@router.get("/session-status")
def session_status(live: bool = False):
    """저장 세션 상태. live=true면 즉시 재확인."""
    if live or not _session_status.get("checked_at"):
        return check_ourbox_session()
    return dict(_session_status)


@router.post("/session-login")
def session_login():
    """세션 재로그인 창 실행 — 이 PC에 일반 Chrome이 떠서 사용자가 직접 로그인."""
    if _login_proc is not None and _login_proc.poll() is None:
        return {"started": False, "already_running": True,
                "message": "이미 로그인 창이 열려 있습니다. 해당 창에서 로그인해 주세요."}
    if not _launch_login_window():
        raise HTTPException(400, "로그인 창 실행 실패 (아워박스_세션로그인.py 확인 필요)")
    return {"started": True,
            "message": "Chrome 창이 열렸습니다. 아워박스에 로그인하면 세션이 자동 저장됩니다."}
