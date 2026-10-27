"""아워박스 자동출고(물류센터 직접 출고) → BoxHero 전산처리 — 카페24 · 자사몰 DJ&A.

기존 카페24 흐름(ob_orders: OMS 수집→단계전진→BH등록)은 우리가 OMS를 움직여야 했지만,
이제 카페24·DJ&A는 아워박스 물류센터가 알아서 출고한다. 스마트스토어(도착보장)와 같은 구조로
BH에는 '거래처 × 출고일' 1건으로 기록한다 (메모 '# 자사몰 DJ&A(카페24) 10월 27일 · 아워박스 자동출고 N건').

흐름
  1) WMS 출고실적 API(out_perf_period) — API 키 인증이라 OMS 세션·CAPTCHA와 무관
  2) 채널 분류(카페24 / 카페24 디제이앤에이) → (채널, 출고일) 취합, set_bom·product_mapping으로 SKU 해석
  3) BoxHero 확인 — 같은 거래처의 출고일(±1일) 출고 중 우리가 올린 게 아닌 건(수기)이 있으면 자동등록 보류
  4) 미등록 주문만 BH 출고등록. od_sno 단위 중복방지(ob_order_reg 테이블을 OMS 흐름과 공유)

config.json
  ob_shipped_auto_enabled(bool)  ob_shipped_auto_times("00:30")  ob_shipped_channels("cafe24_djna,cafe24")
  ob_shipped_days(3, 되돌아보는 일수)  ob_shipped_start_date(YYYY-MM-DD, 이 날짜 이후 출고만 자동 — 수기 처리 기간과 분리)
"""
import sys
import os
import re
import json
import time
import threading
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import ourbox_api
import ourbox_oms_orders as oms
import receiving_db as db
import utils_core as U
import state
from routers import ob_orders
from routers.slack_outbound import _fetch_tx_items, _kst_date

router = APIRouter()

CHANNEL_DEFS = oms.CHANNEL_DEFS
DEFAULT_CHANNELS = "cafe24_djna,cafe24"
# BH 메모의 거래처 표기 (거래처 이름과 맞춤) — 스마트스토어 '# 스마트 스토어 (dj&a) 10월 26일' 과 같은 꼴
MEMO_NAME = {"cafe24": "자사몰(카페24)", "cafe24_djna": "자사몰 DJ&A(카페24)"}
MEMO_MARK = "아워박스 자동출고"   # 우리가 올린 BH 거래 식별 표식

_DEFAULTS = {"ob_shipped_auto_enabled": False, "ob_shipped_auto_times": "00:30",
             "ob_shipped_channels": DEFAULT_CHANNELS, "ob_shipped_days": 3,
             "ob_shipped_start_date": ""}
_status = {"last_run": None, "last_result": None, "last_error": None, "running": False}
_lock = threading.Lock()

with db._conn() as _c:
    _c.execute("""CREATE TABLE IF NOT EXISTS ob_shipped_alert (
        key TEXT PRIMARY KEY, alerted_at TEXT DEFAULT (datetime('now','localtime')))""")
    _c.commit()


# ── 설정 ─────────────────────────────────────────────────────────
def _settings() -> dict:
    cfg = ob_orders._load_cfg()
    return {k: (cfg.get(k) if cfg.get(k) is not None else v) for k, v in _DEFAULTS.items()}


def _channels(s: str) -> list:
    out = [c.strip() for c in str(s or "").split(",") if c.strip()]
    return [c for c in out if c in CHANNEL_DEFS] or [c for c in DEFAULT_CHANNELS.split(",")]


def _range(days: int):
    today = datetime.now()
    return (today - timedelta(days=max(0, days))).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def _kor_date(d: str) -> str:
    try:
        dt = datetime.strptime(d, "%Y-%m-%d")
        return f"{dt.month}월 {dt.day}일"
    except Exception:
        return d


# ── 아워박스 출고실적 ────────────────────────────────────────────
def _fetch_shipped(from_date: str, to_date: str) -> list:
    client = ourbox_api.make_client(ob_orders._load_cfg())
    if not client:
        raise HTTPException(400, "아워박스 API 키(ourbox_access_key/secret_key)가 설정되지 않았습니다.")
    return client.fetch_outbounds(from_date, to_date) or []


def _classify(rec: dict):
    import html as _h
    h = rec.get("header", rec) if isinstance(rec, dict) else {}
    ch = _h.unescape(str(h.get("channel") or ""))
    mall = _h.unescape(str(h.get("mall_name") or ""))
    return oms.classify_channel("", ch, mall)


_bh_name_cache = {"at": 0.0, "map": {}}


def _bh_names(token: str) -> dict:
    """BH SKU → 제품명 (10분 캐시, 미리보기 표시용)."""
    if time.time() - _bh_name_cache["at"] < 600 and _bh_name_cache["map"]:
        return _bh_name_cache["map"]
    try:
        m = {str(it.get("sku") or ""): str(it.get("name") or "") for it in U.fetch_all_items_list(token)}
    except Exception:
        m = {}
    _bh_name_cache.update(at=time.time(), map=m)
    return m


# ── BoxHero 확인 (수기 출고 탐지) ──────────────────────────────────
def _bh_out_window(token: str, from_date: str, to_date: str) -> list:
    """기간(KST 날짜) 내 BH 출고 거래 — 캐시 없이 최신 상태로."""
    try:
        txs = U.fetch_transactions(token, "out",
                                   (datetime.strptime(from_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d"),
                                   to_date, None, use_cache=False)
    except Exception as e:
        state.add_log("warning", "BH 출고 확인 조회 실패 — 수기 출고 탐지 생략", str(e)[:120], source="ob-shipped")
        return []
    out = []
    for t in txs:
        d = _kst_date(t.get("transaction_time") or t.get("created_at") or "")
        if from_date <= d <= to_date:
            out.append({**t, "_kst_date": d})
    return out


def _bh_manual_for(bh_txs: list, partner_id, out_dt: str, our_ids: set, token: str) -> dict:
    """같은 거래처의 출고일·다음날(KST) 출고 중 우리 자동등록이 아닌 것(수기) 집계."""
    nxt = (datetime.strptime(out_dt, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    hits, sku_qty, qty = [], defaultdict(int), 0
    for t in bh_txs:
        p = t.get("partner") or {}
        pid = p.get("id") if isinstance(p, dict) else None
        if partner_id is None or str(pid) != str(partner_id):
            continue
        if t["_kst_date"] not in (out_dt, nxt):
            continue
        memo = str(t.get("memo") or "")
        if str(t.get("id")) in our_ids or MEMO_MARK in memo or "자동출고 통합" in memo:
            continue
        items = _fetch_tx_items(token, t["id"])
        q = sum(i["qty"] for i in items)
        for i in items:
            sku_qty[str(i.get("sku") or "")] += i["qty"]
        qty += q
        hits.append({"tx_id": str(t.get("id")), "date": t["_kst_date"], "memo": memo[:60], "qty": q,
                     "by": ((t.get("created_by") or {}).get("name") if isinstance(t.get("created_by"), dict) else "")})
    return {"tx_count": len(hits), "qty": qty, "sku_qty": dict(sku_qty), "txs": hits}


# ── 취합 ─────────────────────────────────────────────────────────
def _build(days: int, channels: list, start_date: str) -> dict:
    """출고실적 → (채널, 출고일) 그룹 + 상태 판정. 쓰기 없음."""
    cfg = ob_orders._load_cfg()
    token = cfg.get("api_token", "")
    from_date, to_date = _range(days)
    recs = _fetch_shipped(from_date, to_date)

    conn = db._conn()
    cur = conn.cursor()
    # od_sno별 라인 모으기 (대상 채널만)
    by_od: dict = defaultdict(list)
    od_meta: dict = {}
    for r in recs:
        ch = _classify(r)
        if ch not in channels:
            continue
        h = r.get("header", r)
        od = str(h.get("od_sno") or "").strip()
        out_dt = str(h.get("out_dt") or "")[:10]
        if not od or not out_dt:
            continue
        by_od[od].append(h)
        od_meta[od] = (ch, out_dt)

    # 기등록 od_sno + 우리가 올린 BH tx id
    reg = {}
    ods = list(by_od)
    for i in range(0, len(ods), 500):
        chunk = ods[i:i + 500]
        cur.execute(f"SELECT od_sno, bh_tx_id, status FROM ob_order_reg WHERE od_sno IN ({','.join('?' * len(chunk))})", chunk)
        for row in cur.fetchall():
            reg[str(row["od_sno"])] = (str(row["bh_tx_id"] or ""), str(row["status"] or ""))
    cur.execute("SELECT DISTINCT bh_tx_id FROM ob_order_reg WHERE bh_tx_id IS NOT NULL AND bh_tx_id != ''")
    our_ids = set()
    for row in cur.fetchall():
        for x in str(row[0]).replace("manual:", "").split(","):
            if x.strip():
                our_ids.add(x.strip())

    groups: dict = {}
    for od, lines in by_od.items():
        ch, out_dt = od_meta[od]
        g = groups.setdefault((ch, out_dt), {
            "key": f"{ch}|{out_dt}", "channel": ch, "label": CHANNEL_DEFS[ch]["label"], "out_dt": out_dt,
            "order_count": 0, "qty_total": 0, "registered_count": 0, "registered_qty": 0,
            "new_count": 0, "new_qty": 0, "new_sku_qty": defaultdict(int), "new_ods": [],
            "unmapped": {}, "unmapped_count": 0,
        })
        g["order_count"] += 1
        od_qty = sum(int(float(str(l.get("out_qty") or 0))) for l in lines)
        g["qty_total"] += od_qty
        if od in reg:
            g["registered_count"] += 1
            g["registered_qty"] += od_qty
            continue
        sku_qty: dict = defaultdict(int)
        unm = []
        for l in lines:
            q = int(float(str(l.get("out_qty") or 0)))
            if q <= 0:
                continue
            items, missing = ob_orders._resolve_bh(conn, str(l.get("product_code") or ""), q)
            if missing:
                unm.append((str(l.get("product_code") or ""), str(l.get("product_name") or "")))
            for sku, qq in items:
                sku_qty[sku] += int(qq)
        if unm:
            for pc, nm in unm:
                u = g["unmapped"].setdefault(pc, {"prod_cd": pc, "prod_nm": nm, "ods": []})
                u["ods"].append(od)
            g["unmapped_count"] += 1
            continue
        g["new_count"] += 1
        g["new_qty"] += od_qty
        for sku, q in sku_qty.items():
            g["new_sku_qty"][sku] += q
        g["new_ods"].append({"od_sno": od, "mall_od_no": "", "items": dict(sku_qty)})
    conn.close()

    # BoxHero 확인 — 거래처 해석 + 창 내 출고 한 번만 조회
    partner_map = ob_orders._resolve_partners(token, channels) if token else {}
    bh_txs = []
    if token and groups:
        dts = sorted(g["out_dt"] for g in groups.values())
        bh_to = (datetime.strptime(dts[-1], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        bh_txs = _bh_out_window(token, dts[0], min(bh_to, datetime.now().strftime("%Y-%m-%d")))
    names = _bh_names(token) if token else {}

    out = []
    for (ch, out_dt), g in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0]), reverse=True):
        partner = partner_map.get(ch)
        manual = _bh_manual_for(bh_txs, (partner or {}).get("id"), out_dt, our_ids, token) if token else \
            {"tx_count": 0, "qty": 0, "sku_qty": {}, "txs": []}
        diff = {sku: q - int(manual["sku_qty"].get(sku, 0)) for sku, q in g["new_sku_qty"].items()}
        diff = {sku: q for sku, q in diff.items() if q > 0}
        if g["new_count"] == 0 and g["unmapped_count"] == 0:
            status = "registered"
        elif g["new_count"] == 0:
            status = "unmapped_only"
        elif start_date and out_dt < start_date:
            status = "before_start"
        elif manual["tx_count"] > 0:
            status = "manual_found"
        else:
            status = "ready"
        out.append({
            **{k: v for k, v in g.items() if k not in ("new_sku_qty", "new_ods")},
            "status": status,
            "partner": partner,
            "items": [{"sku": sku, "name": names.get(sku, ""), "qty": q}
                      for sku, q in sorted(g["new_sku_qty"].items(), key=lambda kv: -kv[1])],
            "diff_items": [{"sku": sku, "name": names.get(sku, ""), "qty": q}
                           for sku, q in sorted(diff.items(), key=lambda kv: -kv[1])],
            "bh_manual": manual,
            "unmapped": list(g["unmapped"].values()),
            "_new_ods": g["new_ods"],
            "memo": f"# {MEMO_NAME.get(ch, g['label'])} {_kor_date(out_dt)} · {MEMO_MARK} {g['new_count']}건",
        })
    return {"from_date": from_date, "to_date": to_date, "channels": channels, "start_date": start_date,
            "record_count": len(recs), "groups": out}


def _public(groups: list) -> list:
    return [{k: v for k, v in g.items() if not k.startswith("_")} for g in groups]


# ── 등록 ─────────────────────────────────────────────────────────
def _register(days: int, channels: list, start_date: str, dry_run: bool, force: bool, keys: list | None) -> dict:
    """ready 그룹을 BH 출고등록. force=True면 manual_found 그룹도 수기분을 뺀 차액으로 등록.
    keys가 있으면 그 그룹만(화면에서 골라 실행)."""
    cfg = ob_orders._load_cfg()
    token, loc = cfg.get("api_token", ""), cfg.get("selected_location_id")
    if not dry_run and (not token or not loc):
        raise HTTPException(400, "BH 토큰/출고위치(selected_location_id) 설정 필요")
    built = _build(days, channels, start_date)
    results = {"registered": [], "skipped": [], "errors": [], "dry_run": dry_run}
    targets = []
    for g in built["groups"]:
        if keys and g["key"] not in keys:
            continue
        if g["status"] == "ready" or (g["status"] == "manual_found" and force) \
                or (g["status"] == "before_start" and keys):   # 화면에서 콕 집어 실행하면 시작일 이전도 허용
            targets.append(g)
        elif g["status"] in ("manual_found", "before_start"):
            results["skipped"].append({"key": g["key"], "status": g["status"], "new_count": g["new_count"]})
    if not targets:
        results["summary"] = _summary(results)
        results["groups"] = _public(built["groups"])
        return results

    sku_to_id = U.fetch_all_items(token) if not dry_run else {}
    conn = db._conn()
    cur = conn.cursor()
    for g in targets:
        use_diff = g["status"] == "manual_found"
        items = g["diff_items"] if use_diff else g["items"]
        memo = g["memo"] + (" (수기분 차액)" if use_diff else "")
        ods = g["_new_ods"]
        if dry_run:
            results["registered"].append({"key": g["key"], "dry_run": True, "od_count": len(ods),
                                          "qty": sum(i["qty"] for i in items), "items": items, "memo": memo})
            continue
        try:
            tx_id = ""
            if items:
                bh_items, miss = [], []
                for it in items:
                    iid = sku_to_id.get(str(it["sku"]).strip())
                    (bh_items.append({"item_id": iid, "quantity": -int(it["qty"])}) if iid else miss.append(it["sku"]))
                if not bh_items:
                    results["errors"].append({"key": g["key"], "error": f"BH 미등록 SKU {miss}"})
                    continue
                payload = {"type": "out", "to_location_id": loc, "items": bh_items, "memo": memo}
                if g.get("partner") and g["partner"].get("id"):
                    payload["partner_id"] = g["partner"]["id"]
                tx = U.post_transaction(token, payload)
                tx_id = str(tx.get("id", ""))
                if miss:
                    results["errors"].append({"key": g["key"], "error": f"일부 SKU BH 미등록(제외됨) {miss}"})
            # 차액이 0이면 수기 처리로 간주 — 등록 없이 od_sno만 기록해 다시 잡히지 않게
            ref = tx_id or ("manual:" + ",".join(t["tx_id"] for t in g["bh_manual"]["txs"]))
            st = "shipped_auto" if tx_id else "manual"
            for o in ods:
                cur.execute("""INSERT OR REPLACE INTO ob_order_reg
                    (od_sno, channel, mall_od_no, bh_tx_id, items_json, qty, status) VALUES (?,?,?,?,?,?,?)""",
                            (o["od_sno"], g["channel"], o["mall_od_no"], ref,
                             json.dumps(o["items"], ensure_ascii=False), int(sum(o["items"].values())), st))
            conn.commit()
            results["registered"].append({"key": g["key"], "tx_id": tx_id, "od_count": len(ods),
                                          "qty": sum(i["qty"] for i in items), "items": items, "memo": memo,
                                          "partner": g.get("partner")})
            state.add_log("success", f"아워박스 자동출고→BH 등록 [{g['label']} {g['out_dt']}] {len(ods)}건 {sum(i['qty'] for i in items)}개",
                          f"tx={tx_id or '등록없음(수기분으로 충당)'} | {memo}", source="ob-shipped")
        except Exception as e:
            results["errors"].append({"key": g["key"], "error": str(e)[:150]})
            state.add_log("error", f"아워박스 자동출고→BH 등록 실패 [{g['key']}]", str(e)[:200], source="ob-shipped")
    conn.close()
    if not dry_run:
        unm = [{"od_sno": u["ods"][0], "items": [{"prod_cd": u["prod_cd"], "prod_nm": u["prod_nm"]}]}
               for g in built["groups"] for u in g["unmapped"]]
        if unm:
            ob_orders._notify_unmapped(unm)
    results["summary"] = _summary(results)
    # 등록된 그룹은 다시 조회하지 않고 상태만 갱신 (출고실적 재수집은 분 단위로 느림)
    done = {r["key"] for r in results["registered"] if not r.get("dry_run")}
    for g in built["groups"]:
        if g["key"] in done:
            g.update(status="registered", registered_count=g["registered_count"] + g["new_count"],
                     registered_qty=g["registered_qty"] + g["new_qty"], new_count=0, new_qty=0, items=[], diff_items=[])
    results["groups"] = _public(built["groups"])
    return results


def _summary(r: dict) -> dict:
    # tx_registered: BH 거래가 실제로(또는 dry-run에서) 생기는 그룹 수. 수기분으로 충당돼 거래 없이 기록만 한 그룹은 manual_marked
    return {"tx_registered": len([x for x in r["registered"] if x.get("tx_id") or (x.get("dry_run") and x.get("qty", 0) > 0)]),
            "manual_marked": len([x for x in r["registered"] if not x.get("tx_id") and x.get("qty", 0) <= 0]),
            "orders_included": sum(x.get("od_count", 0) for x in r["registered"]),
            "qty": sum(x.get("qty", 0) for x in r["registered"]),
            "skipped": len(r["skipped"]), "errors": len(r["errors"])}


# ── 엔드포인트 ───────────────────────────────────────────────────
@router.get("/preview")
def preview(days: int | None = None, channels: str | None = None):
    """출고실적 → BH 등록 미리보기 (쓰기 없음)."""
    s = _settings()
    try:
        with _lock:
            b = _build(int(days if days is not None else s["ob_shipped_days"]),
                       _channels(channels or s["ob_shipped_channels"]), s["ob_shipped_start_date"])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"미리보기 실패: {e}")
    b["groups"] = _public(b["groups"])
    b["status"] = dict(_status)
    return b


class RegisterReq(BaseModel):
    days: int | None = None
    channels: str | None = None
    dry_run: bool = True
    force: bool = False          # 수기 출고가 있는 날도 차액 등록
    keys: list[str] | None = None  # "cafe24_djna|2026-10-27" 처럼 그룹 지정


@router.post("/register")
def register(body: RegisterReq):
    s = _settings()
    try:
        with _lock:
            return _register(int(body.days if body.days is not None else s["ob_shipped_days"]),
                             _channels(body.channels or s["ob_shipped_channels"]), s["ob_shipped_start_date"],
                             body.dry_run, body.force, body.keys)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"등록 실패: {e}")


class ConfigReq(BaseModel):
    enabled: bool | None = None
    times: str | None = None
    channels: str | None = None
    days: int | None = None
    start_date: str | None = None


@router.get("/config")
def get_config():
    s = _settings()
    return {**s, "status": dict(_status),
            "channel_defs": {k: CHANNEL_DEFS[k]["label"] for k in CHANNEL_DEFS}}


@router.post("/config")
def set_config(body: ConfigReq):
    patch = {}
    if body.enabled is not None:
        patch["ob_shipped_auto_enabled"] = body.enabled
        # 처음 켤 때 시작일이 비어 있으면 오늘부터 — 그 전 출고는 수기 처리분으로 보고 건드리지 않는다
        if body.enabled and not _settings().get("ob_shipped_start_date") and body.start_date is None:
            patch["ob_shipped_start_date"] = datetime.now().strftime("%Y-%m-%d")
    if body.times is not None:
        patch["ob_shipped_auto_times"] = ",".join(ob_orders._parse_times(body.times))
    if body.channels is not None:
        patch["ob_shipped_channels"] = ",".join(_channels(body.channels))
    if body.days is not None:
        patch["ob_shipped_days"] = max(1, min(30, int(body.days)))
    if body.start_date is not None:
        sd = body.start_date.strip()
        if sd:
            datetime.strptime(sd, "%Y-%m-%d")
        patch["ob_shipped_start_date"] = sd
    if patch:
        U.save_config(patch)
    return get_config()


# ── 무인 스케줄러 ─────────────────────────────────────────────────
def _alert_skipped(skipped: list, groups: list):
    """수기 출고가 섞여 자동등록을 보류한 날을 슬랙으로 1회 알림 (같은 그룹은 한 번만)."""
    if not skipped:
        return
    try:
        cfg = ob_orders._load_cfg()
        slack = cfg.get("slack_token", "")
        conn = db._conn()
        cur = conn.cursor()
        fresh = []
        for sk in skipped:
            if sk["status"] != "manual_found":
                continue
            cur.execute("SELECT 1 FROM ob_shipped_alert WHERE key=?", (sk["key"],))
            if cur.fetchone():
                continue
            cur.execute("INSERT OR IGNORE INTO ob_shipped_alert (key) VALUES (?)", (sk["key"],))
            fresh.append(sk["key"])
        conn.commit()
        conn.close()
        if not fresh or not slack:
            return
        ch = str(cfg.get("slack_outbound_channel") or "물류_출고").strip()
        channel_id = ch if re.fullmatch(r"[CGDUW][A-Z0-9]{6,}", ch) else U.fetch_slack_channels(slack).get(ch, "")
        if not channel_id:
            return
        gi = {g["key"]: g for g in groups}
        lines = ["⚠️ *아워박스 자동출고 → BoxHero 전산처리 보류* — 같은 날 수기 출고가 이미 있어 자동등록하지 않았습니다"]
        for k in fresh:
            g = gi.get(k, {})
            m = g.get("bh_manual", {})
            lines.append(f"• {g.get('label', k)} {g.get('out_dt', '')} — 아워박스 출고 {g.get('new_qty', 0)}개(미등록 {g.get('new_count', 0)}건)"
                         f" / BH 수기 {m.get('qty', 0)}개 {m.get('tx_count', 0)}건")
        lines.append("→ 앱 *OB 주문* 페이지 → *자동출고 전산처리* 에서 확인 후 '차액 등록' 또는 '수기 처리됨'으로 정리해 주세요.")
        U.slack_post_message(slack, channel_id, "\n".join(lines))
    except Exception as e:
        state.add_log("error", "자동출고 보류 슬랙 알림 실패", str(e)[:150], source="ob-shipped")


def run_cycle() -> dict:
    s = _settings()
    _status["running"] = True
    try:
        with _lock:
            res = _register(int(s["ob_shipped_days"]), _channels(s["ob_shipped_channels"]),
                            s["ob_shipped_start_date"], dry_run=False, force=False, keys=None)
        _status.update(last_run=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       last_result=res.get("summary"), last_error=None)
        state.add_log("success", "아워박스 자동출고 전산처리 1사이클", json.dumps(res.get("summary"), ensure_ascii=False),
                      source="ob-shipped")
        _alert_skipped(res.get("skipped", []), res.get("groups", []))
        return res
    except Exception as e:
        _status.update(last_run=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), last_error=str(e)[:200])
        state.add_log("error", "아워박스 자동출고 전산처리 실패", str(e)[:300], source="ob-shipped")
        raise
    finally:
        _status["running"] = False


@router.post("/run-once")
def run_once():
    """스케줄과 같은 실제 실행 1회 (ready 그룹만 등록)."""
    try:
        return run_cycle()
    except Exception as e:
        raise HTTPException(400, f"실행 실패: {e}")


def _loop():
    """30초마다 점검 — 오늘 지난 지정시각 중 아직 안 돈 슬롯을 실행(catch-up). 재시작해도 중복 실행 없음."""
    while True:
        try:
            s = _settings()
            if s.get("ob_shipped_auto_enabled"):
                now = datetime.now()
                today, hm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
                fired = ob_orders._load_cfg().get("ob_shipped_last_fired") or {}
                for slot in ob_orders._parse_times(s.get("ob_shipped_auto_times")):
                    if hm >= slot and fired.get(slot) != today:
                        fired[slot] = today
                        U.save_config({"ob_shipped_last_fired": fired})
                        for attempt in range(3):   # 일시 오류(API 지연) 재시도 — od_sno 중복방지로 안전
                            try:
                                run_cycle()
                                break
                            except Exception:
                                if attempt < 2:
                                    time.sleep(90)
                        break
        except Exception:
            pass
        time.sleep(30)


def start_scheduler():
    threading.Thread(target=_loop, daemon=True, name="ob-shipped").start()
