"""재고 대사(Reconciliation): 박스히어로 ↔ 아워박스 Mate 입출고·조정 비교 + AI 분석"""
import sys, os, json, html, re
import threading as _threading
import time as _time_mod
import concurrent.futures
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional, List

import requests as _req
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils_core as U
import state

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

router = APIRouter()

BH_BASE = "https://rest.boxhero-app.com"

# ── 영구 파일 캐시 (BH TX items) ──────────────────────────────────────────
# 서버 재시작 후에도 유지. 한 번 조회한 TX는 재조회 안 함.
_TX_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "bh_tx_items_cache.json")
_tx_file_cache: dict = {}  # {str(tx_id): [items...]}
_tx_file_cache_loaded = False

def _load_tx_file_cache():
    global _tx_file_cache, _tx_file_cache_loaded
    if _tx_file_cache_loaded:
        return
    try:
        if os.path.exists(_TX_CACHE_PATH):
            with open(_TX_CACHE_PATH, "r", encoding="utf-8") as f:
                _tx_file_cache = json.load(f)
    except Exception:
        _tx_file_cache = {}
    _tx_file_cache_loaded = True

_tx_cache_dirty = 0
_tx_cache_last_flush = 0.0
_tx_cache_flush_lock = _threading.Lock()

def _flush_tx_file_cache():
    """메모리 캐시를 파일로 기록. 대량 보강 후엔 명시적으로 호출."""
    global _tx_cache_dirty, _tx_cache_last_flush
    with _tx_cache_flush_lock:
        if not _tx_cache_dirty:
            return
        try:
            os.makedirs(os.path.dirname(_TX_CACHE_PATH), exist_ok=True)
            with open(_TX_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(_tx_file_cache, f, ensure_ascii=False)
            _tx_cache_dirty = 0
            _tx_cache_last_flush = _time_mod.time()
        except Exception:
            pass

def _save_tx_file_cache_entry(tx_id: int, items: list):
    """단일 TX 항목을 파일 캐시에 저장.
    항목마다 전체 파일을 재작성하면 수천 건 보강 시 O(n²) 디스크 쓰기가 되므로
    40건 또는 3초 단위로 모아서 기록 (대량 루프 끝에는 _flush_tx_file_cache 호출)."""
    global _tx_file_cache, _tx_cache_dirty
    _tx_file_cache[str(tx_id)] = items
    _tx_cache_dirty += 1
    if _tx_cache_dirty >= 40 or _time_mod.time() - _tx_cache_last_flush >= 3:
        _flush_tx_file_cache()


# ── 영구 파일 캐시 (OB 수집 데이터) ─────────────────────────────────────────
# 날짜 범위별 OB in/out/adj 원시 데이터 캐시. 과거 날짜 → 영구, 오늘 포함 → 1시간 TTL.
_OB_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "ob_txs_cache.json")
_ob_file_cache: dict = {}
_ob_file_cache_loaded = False


def _load_ob_file_cache():
    global _ob_file_cache, _ob_file_cache_loaded
    if _ob_file_cache_loaded:
        return
    try:
        if os.path.exists(_OB_CACHE_PATH):
            with open(_OB_CACHE_PATH, "r", encoding="utf-8") as f:
                _ob_file_cache = json.load(f)
    except Exception:
        _ob_file_cache = {}
    _ob_file_cache_loaded = True


def _save_ob_file_cache_entry(cache_key: str, in_list: list, out_list: list, adj_list: list, source: str):
    """OB 수집 결과를 파일 캐시에 저장."""
    global _ob_file_cache
    _ob_file_cache[cache_key] = {
        "in": in_list, "out": out_list, "adj": adj_list,
        "source": source,
        "ts": datetime.now().isoformat(),
    }
    try:
        os.makedirs(os.path.dirname(_OB_CACHE_PATH), exist_ok=True)
        with open(_OB_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_ob_file_cache, f, ensure_ascii=False)
    except Exception:
        pass


def _get_ob_cache(cache_key: str) -> Optional[dict]:
    """OB 캐시 조회. to_date가 오늘 이전이면 영구, 오늘 포함이면 1시간 TTL."""
    entry = _ob_file_cache.get(cache_key)
    if not entry:
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    # 키 형식: "{from_date}|{to_date}"  →  마지막 파이프 뒤
    try:
        to_date_part = cache_key.split("|")[-1]
    except Exception:
        to_date_part = today
    if to_date_part < today:
        # 완전한 과거 범위 → 영구 캐시
        return entry
    # 오늘 포함 → 1시간 TTL
    try:
        ts = datetime.fromisoformat(entry.get("ts", ""))
        if (datetime.now() - ts).seconds < 3600:
            return entry
    except Exception:
        pass
    return None


def _ob_month_chunks(from_date: str, to_date: str) -> list:
    """[from,to]를 (chunk_from, chunk_to) 월 단위로 분해.
    완결된 달은 청크 키가 날마다 바뀌지 않아 영구 캐시가 재사용된다."""
    chunks = []
    cur = datetime.strptime(from_date, "%Y-%m-%d").date()
    end = datetime.strptime(to_date, "%Y-%m-%d").date()
    while cur <= end:
        if cur.month == 12:
            nxt = cur.replace(year=cur.year + 1, month=1, day=1)
        else:
            nxt = cur.replace(month=cur.month + 1, day=1)
        c_to = min(nxt - timedelta(days=1), end)
        chunks.append((cur.isoformat(), c_to.isoformat()))
        cur = nxt
    return chunks


def _fetch_bh_tx_items(token: str, tx_id: int, tx_type: str = "location") -> list:
    """BoxHero 거래 상세 → items 리스트. 목록 API는 헤더만 주므로 상세 조회 필요.
    tx_type='adjust': /v1/txs/{id} (조정 TX), 기타: /v1/location-txs/{id}
    우선순위: 파일 캐시(영구) → 메모리 캐시(15분) → API 조회."""
    import time as _time2, datetime as _dt_c
    _load_tx_file_cache()
    cache_key = f"{tx_id}:{tx_type}"
    # 1. 파일 캐시 (서버 재시작 후에도 유지)
    if cache_key in _tx_file_cache or str(tx_id) in _tx_file_cache:
        return _tx_file_cache.get(cache_key) or _tx_file_cache.get(str(tx_id), [])
    # 2. 메모리 캐시 (15분 TTL, 서버 내 중복 호출 방지)
    _cached = state.bh_tx_items_cache.get(cache_key) or state.bh_tx_items_cache.get(tx_id)
    if _cached and (_dt_c.datetime.now() - _cached["ts"]).seconds < 900:
        return _cached["items"]
    # 3. API 조회
    if tx_type == "adjust":
        endpoint = f"{BH_BASE}/v1/txs/{tx_id}"
    else:
        endpoint = f"{BH_BASE}/v1/location-txs/{tx_id}"
    for attempt in range(4):
        try:
            r = _req.get(endpoint, headers={"Authorization": f"Bearer {token}"}, timeout=10)
            if r.ok:
                _detail = r.json().get("item", {})
                items = _detail.get("items", [])
                # adjust TX의 items는 quantity가 음수/양수로 방향을 나타냄 — 보존
                state.bh_tx_items_cache[cache_key] = {"items": items, "ts": _dt_c.datetime.now()}
                _save_tx_file_cache_entry(cache_key, items)
                # 위치 정보도 별도 키로 캐시 — move는 from/to 둘 다 있어 방향 보존 필요
                _from_obj = _detail.get("from_location") or {}
                _to_obj = _detail.get("to_location") or {}
                _from_id = _from_obj.get("id") if isinstance(_from_obj, dict) else None
                _to_id = _to_obj.get("id") if isinstance(_to_obj, dict) else None
                if _from_id or _to_id:
                    _save_tx_file_cache_entry(f"{cache_key}:locft", [_from_id, _to_id])
                    _save_tx_file_cache_entry(f"{cache_key}:loc", _to_id or _from_id)
                return items
            if r.status_code == 429:
                _time2.sleep(2.0 * (attempt + 1))
                continue
        except Exception:
            pass
        break
    return []


def _get_bh_tx_loc_id(token: str, tx_id: int, tx_type: str = "location", prefer_from: bool = False):
    """BH TX의 위치 id 반환. in은 to_location, out은 from_location만 있어 자동.
    move는 from/to 둘 다 있으므로 방향이 중요: prefer_from=True면 출발지(from) 반환
    (move를 '해당 위치에서 나간 출고'로 필터링할 때 사용).
    캐시에 없으면 상세 1회 조회 (이후 영구 캐시). 실패 시 None."""
    import time as _time3
    _load_tx_file_cache()
    ft_key = f"{tx_id}:{tx_type}:locft"
    if ft_key in _tx_file_cache:
        _ft = _tx_file_cache.get(ft_key) or [None, None]
        return (_ft[0] or _ft[1]) if prefer_from else (_ft[1] or _ft[0])
    loc_key = f"{tx_id}:{tx_type}:loc"
    if not prefer_from and loc_key in _tx_file_cache:
        # 구버전 캐시(to 우선 단일값) — in/out엔 그대로 유효, move 방향 구분엔 사용 불가
        return _tx_file_cache.get(loc_key)
    if tx_type == "adjust":
        endpoint = f"{BH_BASE}/v1/txs/{tx_id}"
    else:
        endpoint = f"{BH_BASE}/v1/location-txs/{tx_id}"
    for attempt in range(4):
        try:
            r = _req.get(endpoint, headers={"Authorization": f"Bearer {token}"}, timeout=10)
            if r.ok:
                _detail = r.json().get("item", {})
                _from_obj = _detail.get("from_location") or {}
                _to_obj = _detail.get("to_location") or {}
                _from_id = _from_obj.get("id") if isinstance(_from_obj, dict) else None
                _to_id = _to_obj.get("id") if isinstance(_to_obj, dict) else None
                _save_tx_file_cache_entry(ft_key, [_from_id, _to_id])
                return (_from_id or _to_id) if prefer_from else (_to_id or _from_id)
            if r.status_code == 429:
                _time3.sleep(2.0 * (attempt + 1))
                continue
        except Exception:
            pass
        break
    return None


def _enrich_bh_items(token: str, txs: list, tx_type: str = "location") -> None:
    """거래 목록의 각 tx에 items를 병렬로 채워 넣음 (in-place).
    BoxHero /v1/location-txs는 헤더만 반환하고 items는 빈 배열이므로,
    /v1/location-txs/{id} 상세를 병렬 조회해 보강한다.
    tx_type='adjust': /v1/txs/{id} 엔드포인트 사용 (조정 TX용).
    429 레이트 리밋 방지: max_workers=6, 실패 시 기존 items 보존."""
    targets = [tx for tx in txs if not tx.get("items")]
    if not targets:
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(_fetch_bh_tx_items, token, tx["id"], tx_type): tx
                   for tx in targets if tx.get("id")}
        for fut in concurrent.futures.as_completed(futures):
            tx = futures[fut]
            result = fut.result()
            if result:
                tx["items"] = result
            elif tx.get("items") is None:
                tx["items"] = []
    _flush_tx_file_cache()


# ── 드릴다운: 개별 라인아이템 추출 (집계 행 → 원시 거래) ──────────

def _flat_bh(txs: list, tx_type: str, period: str, groups: dict,
             bh_ch_res, by_channel: bool) -> list:
    """BoxHero 개별 거래 라인아이템. display_sku/channel/period로 라벨링."""
    bh_to_group = groups.get("bh_to_group", {}) if groups else {}
    bh_name_to_group = groups.get("bh_name_to_group", {}) if groups else {}
    group_label = groups.get("group_label", {}) if groups else {}
    out = []
    for tx in txs:
        tx_time = tx.get("transaction_time") or tx.get("created_at", "")
        try:
            dt = datetime.fromisoformat(tx_time[:19])
        except Exception:
            continue
        memo = tx.get("memo") or ""
        partner = (tx.get("partner") or {}).get("name", "") if isinstance(tx.get("partner"), dict) else ""
        channel = bh_ch_res(memo) if (by_channel and bh_ch_res) else ""
        for item in tx.get("items", []):
            sku = str(item.get("sku") or item.get("id") or "UNKNOWN").strip()
            item_name = str(item.get("name", "")).strip()
            gid = bh_to_group.get(sku) or bh_name_to_group.get(item_name)
            display_sku = group_label[gid] if gid else sku
            out.append({
                "side": "bh", "tx_type": tx_type,
                "period": _period_key(dt, period),
                "display_sku": display_sku, "channel": channel,
                "date": dt.strftime("%Y-%m-%d %H:%M"),
                "qty": abs(int(item.get("quantity", 0))),
                "name": str(item.get("name", "")).strip(),
                "ref": memo, "extra": partner,
            })
    return out


def _flat_ob(raw: list, tx_type: str, period: str, source: str, groups: dict,
             ob_ch_res, by_channel: bool) -> list:
    """OurBox 개별 주문 라인아이템 (rest 소스). display_sku/channel/period로 라벨링."""
    if source != "rest" or not raw:
        return []
    ob_to_group = groups.get("ob_to_group", {}) if groups else {}
    group_label = groups.get("group_label", {}) if groups else {}
    qty_keys = {"in": ["input_qty"], "out": ["out_qty"], "adjustment": ["adj_qty"]}.get(tx_type, [])
    date_keys = {
        "in": ["input_dt", "input_complete_dt", "input_req_dt"],
        "out": ["out_dt", "out_complete_dt", "out_req_dt"],
        "adjustment": ["reg_dt", "adj_dt"],
    }.get(tx_type, ["reg_dt"])
    out = []
    for rec in raw:
        if not isinstance(rec, dict):
            continue
        prod_cd = str(rec.get("product_code") or rec.get("prod_cd") or "").strip()
        name = html.unescape(str(rec.get("product_name") or rec.get("sale_prod_nm") or "").strip())
        qty = 0
        for qk in qty_keys + ["out_qty", "input_qty", "adj_qty", "quantity"]:
            if rec.get(qk) not in (None, ""):
                qty = abs(int(float(str(rec.get(qk)).replace(",", "") or 0)))
                break
        if qty == 0:
            continue
        date_str = ""
        for dk in date_keys:
            v = str(rec.get(dk, "")).strip()
            if v and v != "None":
                date_str = v[:16].replace("/", "-")
                break
        try:
            dt = datetime.fromisoformat(date_str[:19]) if date_str else None
        except Exception:
            dt = None
        pk = _period_key(dt, period) if dt else ""
        ch_raw = str(rec.get("channel") or rec.get("mall_name") or "").strip()
        channel = ob_ch_res(ch_raw) if (by_channel and ob_ch_res) else ""
        gid = ob_to_group.get(name)
        display_sku = group_label[gid] if gid else prod_cd
        out.append({
            "side": "ob", "tx_type": tx_type,
            "period": pk, "display_sku": display_sku, "channel": channel,
            "date": date_str or "?",
            "qty": qty, "name": name,
            "ref": str(rec.get("invoice") or ""), "extra": ch_raw,
        })
    return out


def _greedy_pair(bh_items: list, ob_items: list) -> dict:
    """수량 기준 그리디 매칭: 같은 수량끼리 우선 짝지음.
    Returns {"pairs": [{bh, ob, date_gap}], "bh_only": [...], "ob_only": [...]}
    """
    bh = sorted(bh_items, key=lambda x: (x["qty"], x["date"]))
    ob = sorted(ob_items, key=lambda x: (x["qty"], x["date"]))
    used_ob = set()
    pairs, bh_only = [], []
    for b in bh:
        match_idx = None
        for i, o in enumerate(ob):
            if i in used_ob:
                continue
            if o["qty"] == b["qty"]:
                match_idx = i
                break
        if match_idx is not None:
            used_ob.add(match_idx)
            pairs.append({"bh": b, "ob": ob[match_idx]})
        else:
            bh_only.append(b)
    ob_only = [o for i, o in enumerate(ob) if i not in used_ob]
    return {"pairs": pairs, "bh_only": bh_only, "ob_only": ob_only}


# ── 날짜 그룹 키 ──────────────────────────────────────────────

def _period_key(dt: datetime, period: str) -> str:
    if period == "day":
        return dt.strftime("%Y-%m-%d")
    if period == "week":
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if period == "year":
        return dt.strftime("%Y")
    return dt.strftime("%Y-%m")


# ── BoxHero 데이터 정규화 ──────────────────────────────────────

# 상품키와 채널을 한 키 안에 합쳐 운반하기 위한 구분자 (SKU/채널명에 등장하지 않는 문자)
CH_SEP = "\x1f"

# OB "전산처리용" 채널 = 세트 조립 처리 (단품 출고 + 세트 입고)
# - 단품 출고(전산처리용): BH adj 음수(단품 소진)와 매칭 → adj 버킷으로 라우팅
# - 세트 입고(전산처리용): 매칭 불필요 → 제외
ASSEMBLY_CHANNELS: frozenset = frozenset({"전산처리용"})

# 사장된 OB 상품코드 — 비교 대상에서 완전 제외
DEPRECATED_OB_CODES: frozenset = frozenset({"P000000000011556"})

# OB 기초재고 일괄입고 전표번호(input_code) — OurBox 도입 시점 재고 초기등록.
# 매입 입고가 아니라 시스템 전환용 일괄 등록(2026-05-16, 55품목 44만개, status=0 다수)으로
# BoxHero에는 대응 입고가 없어 입고 비교를 통째로 왜곡함 → 입고 raw에서 제외.
# (BH는 bh_adj_max_qty로 기초재고 adj를 거르는데, OB 입고엔 대칭 필터가 없어 추가)
OB_INITIAL_STOCK_INPUT_CODES: frozenset = frozenset({"6496"})


def _ob_rec_channel(rec: dict) -> str:
    """OB 원시 레코드에서 채널명 추출 (header/flat 양쪽 대응)."""
    if not isinstance(rec, dict):
        return ""
    h = rec.get("header", rec)
    return str(h.get("channel") or h.get("mall_name") or "").strip()


def _ob_rec_prod_cd(rec: dict) -> str:
    """OB 원시 레코드에서 상품코드 추출."""
    if not isinstance(rec, dict):
        return ""
    h = rec.get("header", rec)
    for k in ("product_code", "prod_cd", "sale_prod_cd", "item_code"):
        v = str(h.get(k) or "").strip()
        if v:
            return v
    # items 구조일 경우
    for item in rec.get("items", []):
        for k in ("product_code", "prod_cd", "sale_prod_cd"):
            v = str(item.get(k) or "").strip()
            if v:
                return v
    return ""


def _ob_rec_input_code(rec: dict) -> str:
    """OB 입고 레코드에서 입고전표번호(input_code) 추출."""
    if not isinstance(rec, dict):
        return ""
    h = rec.get("header", rec)
    return str(h.get("input_code") or "").strip()


def _filter_deprecated(raw: list) -> list:
    """사장된 OB 상품코드 레코드를 제거."""
    return [r for r in raw if _ob_rec_prod_cd(r) not in DEPRECATED_OB_CODES]


def _filter_initial_stock(raw: list) -> list:
    """기초재고 일괄입고(OurBox 도입 초기 재고등록) 전표 레코드를 제거. 입고에만 적용."""
    if not OB_INITIAL_STOCK_INPUT_CODES:
        return raw
    return [r for r in raw if _ob_rec_input_code(r) not in OB_INITIAL_STOCK_INPUT_CODES]


def _route_ob_assembly(in_raw: list, out_raw: list, adj_raw: list) -> tuple:
    """전산처리용 채널 처리:
    - 출고(단품 소진) → adj 버킷으로 이동 (BH adj 음수와 매칭)
    - 입고(세트 추가) → 제외 (매칭 불필요, BH adj 양수도 제외)
    Returns: (new_in_raw, new_out_raw, new_adj_raw, n_routed)"""
    # 전산처리용 출고만 adj로 이동 (단품 소진)
    out_assembly = [r for r in out_raw if _ob_rec_channel(r) in ASSEMBLY_CHANNELS]
    # 전산처리용 입고(세트)는 제외
    n = len(out_assembly)
    if n == 0 and not any(_ob_rec_channel(r) in ASSEMBLY_CHANNELS for r in in_raw):
        return in_raw, out_raw, adj_raw, 0
    new_in  = [r for r in in_raw  if _ob_rec_channel(r) not in ASSEMBLY_CHANNELS]
    new_out = [r for r in out_raw if _ob_rec_channel(r) not in ASSEMBLY_CHANNELS]
    # adj_raw(fetch_adjustments)는 product_code 없는 실제 재고조정 → 제외
    # 전산처리용 출고만 adj 버킷에 포함
    new_adj = out_assembly
    n_total = len(out_assembly) + sum(1 for r in in_raw if _ob_rec_channel(r) in ASSEMBLY_CHANNELS)
    return new_in, new_out, new_adj, n_total


def _filter_bh_adj_negative(txs: list) -> list:
    """BH adjust TX에서 음수 항목(단품 소진)만 남긴 TX 리스트 반환.
    각 TX의 items 중 quantity < 0인 항목만 남김 (양수=세트 추가는 매칭 불필요).
    items가 아직 없는 TX(enrich 전)는 그대로 통과시켜 이후 enrich에서 처리."""
    result = []
    for tx in txs:
        items = tx.get("items")
        if items is None:
            # enrich 전 — 그대로 포함 (enrich 이후 재필터링 불필요, normalize 시 qty로 판단)
            result.append(tx)
            continue
        neg_items = [it for it in items if int(it.get("quantity", 0)) < 0]
        if neg_items:
            import copy
            tx2 = copy.copy(tx)
            tx2["items"] = neg_items
            result.append(tx2)
    return result


def _normalize_bh_txs(txs: list, tx_label: str, period: str, ch_resolver=None) -> dict:
    """
    {(period_key, item_key): {"name": ..., "qty": ...}}
    item_key = sku  (채널 모드면 f"{sku}{CH_SEP}{채널라벨}")
    ch_resolver: memo -> 채널 라벨 (채널별 대사 시에만 전달)
    tx_label: "in" | "out" | "adjustment"
    """
    grouped: dict = defaultdict(lambda: {"name": "", "qty": 0})
    for tx in txs:
        tx_time_str = tx.get("transaction_time") or tx.get("created_at", "")
        try:
            tx_dt = datetime.fromisoformat(tx_time_str[:19])
        except Exception:
            continue
        key_date = _period_key(tx_dt, period)
        channel = ch_resolver(tx.get("memo") or "") if ch_resolver else None
        items = tx.get("items") or []
        # BH adjust TX: items가 없을 경우 TX 레벨 item 필드에서 fallback
        if not items and tx.get("sku"):
            items = [{"sku": tx.get("sku"), "name": tx.get("name",""), "quantity": tx.get("quantity", tx.get("total_quantity", 0))}]
        for item in items:
            sku = str(item.get("sku") or item.get("id") or "UNKNOWN").strip()
            name = str(item.get("name", "")).strip()
            raw_qty = int(item.get("quantity", 0))
            # adjust TX: quantity 부호 그대로 유지 (음수=감소/단품소진, 양수=증가/세트추가)
            # SKU별로 독립 집계되므로 abs() 없이 합산해도 됨
            qty = abs(raw_qty)
            item_key = f"{sku}{CH_SEP}{channel}" if channel is not None else sku
            k = (key_date, item_key)
            grouped[k]["name"] = name or grouped[k]["name"]
            grouped[k]["qty"] += qty
            # adjust 방향 메타 (양수=추가, 음수=소진) — compare 비교용
            if tx_label in ("adjust", "adjustment"):
                grouped[k]["is_positive"] = grouped[k].get("is_positive", raw_qty > 0)
    return dict(grouped)


# ── OurBox 데이터 정규화 ──────────────────────────────────────

def _parse_ob_date(row: dict, date_keys: list) -> Optional[datetime]:
    for k in date_keys:
        v = str(row.get(k, "")).strip()
        if v and v != "None":
            try:
                return datetime.fromisoformat(v[:19].replace("/", "-"))
            except Exception:
                pass
    return None


def _normalize_ob_txs(ob_list: list, period: str, date_keys: list,
                       prod_cd_key: str, prod_nm_key: str, qty_key: str) -> dict:
    """
    {(period_key, prod_cd): {"name": ..., "qty": ...}}
    """
    grouped: dict = defaultdict(lambda: {"name": "", "qty": 0})
    for rec in ob_list:
        header = rec.get("header", {})
        items = rec.get("items", [])
        header_dt = _parse_ob_date(header, date_keys)

        for item in items:
            dt = _parse_ob_date(item, date_keys) or header_dt
            if not dt:
                continue
            key_date = _period_key(dt, period)
            prod_cd = str(item.get(prod_cd_key, "")).strip()
            prod_nm = str(item.get(prod_nm_key, "")).strip()
            qty = abs(int(float(item.get(qty_key, 0) or 0)))
            if not prod_cd:
                continue
            k = (key_date, prod_cd)
            grouped[k]["name"] = prod_nm or grouped[k]["name"]
            grouped[k]["qty"] += qty
    return dict(grouped)


# ── 비교 ──────────────────────────────────────────────────────

def _compare(bh: dict, ob: dict, day_lookback: int = 0,
             display_from: str = None, display_to: str = None,
             qty_tolerance: float = 0.0) -> list:
    """BH와 OB를 (period_key, sku) 기준으로 비교.

    day_lookback > 0 이면: OB 행에 정확히 매칭되는 BH가 없을 때 ±day_lookback일 내
    BH 기록을 fuzzy 매칭해 날짜 오프셋(BH가 OB보다 1~2일 앞서 기록)을 자동 보정.
    display_from/display_to: BH-only 행을 표시할 날짜 범위 (확장 조회된 외부 기록 숨김).
    """
    import re as _re2
    from collections import defaultdict as _dd3

    bh_consumed: set = set()

    # sku별 날짜→entry 인덱스 (fuzzy용)
    bh_by_sku_date: dict = _dd3(dict)
    if day_lookback > 0:
        for (pk, sku), entry in bh.items():
            bh_by_sku_date[sku][pk] = entry

    # OB 키 + 화면 범위 내 BH 키만 포함
    ob_keys = set(ob.keys())
    if display_from and display_to:
        bh_display_keys = {
            (pk, sku) for (pk, sku) in bh
            if display_from <= pk <= display_to
        }
    else:
        bh_display_keys = set(bh.keys())
    all_keys = ob_keys | bh_display_keys

    rows = []
    for (period_key, sku) in sorted(all_keys):
        bh_entry = bh.get((period_key, sku))
        ob_entry = ob.get((period_key, sku))

        # OB는 있고 BH 정확 매칭 없으면 ±N일 fuzzy 탐색
        if ob_entry and not bh_entry and day_lookback > 0:
            if _re2.match(r'\d{4}-\d{2}-\d{2}$', period_key):
                ob_dt = datetime.strptime(period_key, "%Y-%m-%d")
                sku_dates = bh_by_sku_date.get(sku, {})
                for delta in range(1, day_lookback + 1):
                    found = False
                    for sign in (-1, 1):  # -1: BH가 앞서 기록된 경우 우선
                        cand_date = (ob_dt + timedelta(days=delta * sign)).strftime("%Y-%m-%d")
                        cand_key = (cand_date, sku)
                        if cand_date in sku_dates and cand_key not in bh_consumed:
                            bh_entry = sku_dates[cand_date]
                            bh_consumed.add(cand_key)
                            found = True
                            break
                    if found:
                        break

        # fuzzy로 이미 소비된 BH-only 행 → 중복 방지
        if (period_key, sku) in bh_consumed and not ob_entry:
            continue

        bh_qty = bh_entry["qty"] if bh_entry else None
        ob_qty = ob_entry["qty"] if ob_entry else None
        name = (bh_entry or ob_entry or {}).get("name", "")

        if bh_qty is not None and ob_qty is not None:
            if bh_qty == ob_qty:
                status = "ok"
            elif qty_tolerance > 0 and max(bh_qty, ob_qty) > 0:
                diff_ratio = abs(bh_qty - ob_qty) / max(bh_qty, ob_qty)
                status = "ok" if diff_ratio <= qty_tolerance else "mismatch"
            else:
                status = "mismatch"
        elif bh_qty is not None:
            status = "bh_only"
        else:
            status = "ob_only"

        # 모든 행: display 범위 밖이면 제외 (BH lookback/OB 캐시 확장으로 유입된 외부 기록 숨김)
        # 단, period_key가 "YYYY-MM-DD" 형식일 때만 날짜 비교 (week/month/year 모드는 그대로 통과)
        if display_from and display_to and len(period_key) == 10 and period_key[4] == '-' and period_key[7] == '-':
            if not (display_from <= period_key <= display_to):
                continue

        real_sku, channel = (sku.split(CH_SEP, 1) if CH_SEP in sku else (sku, ""))
        rows.append({
            "period": period_key,
            "sku": real_sku,
            "channel": channel,
            "name": name,
            "bh_qty": bh_qty,
            "ob_qty": ob_qty,
            "status": status,
        })
    return rows


def _compare_total(bh: dict, ob: dict, from_date: str, to_date: str, qty_tolerance: float = 0.0) -> list:
    """기간 합산 비교 (재고 역산 모드): 날짜 무시, 품목별 기간 전체 수량 합산 비교.

    BH는 from_date~to_date 범위, OB는 ±3일 허용 범위 합산.
    날짜 차이가 있어도 수량이 맞으면 정상 처리.
    """
    from datetime import datetime as _dt3, timedelta as _td3
    try:
        _ob_from = (_dt3.strptime(from_date, "%Y-%m-%d") - _td3(days=3)).strftime("%Y-%m-%d")
        _ob_to   = (_dt3.strptime(to_date,   "%Y-%m-%d") + _td3(days=3)).strftime("%Y-%m-%d")
    except Exception:
        _ob_from, _ob_to = from_date, to_date

    def _is_date_key(pk: str) -> bool:
        return len(pk) == 10 and pk[4] == '-' and pk[7] == '-'

    # BH: OB와 동일하게 ±3일 허용 범위 합산 (월말/월초 경계 거래 대칭 처리)
    # 예: BH 3/31 입고 ↔ OB 3/31 입고 — 4월 조회 시 양쪽 모두 포함되어야 일치
    bh_agg: dict = {}
    for (pk, sku), v in bh.items():
        if _is_date_key(pk) and not (_ob_from <= pk <= _ob_to):
            continue  # 날짜 키이고 범위 밖이면 제외
        if sku not in bh_agg:
            bh_agg[sku] = {"qty": 0, "name": v.get("name", "")}
        bh_agg[sku]["qty"] += v["qty"]
        if v.get("name"):
            bh_agg[sku]["name"] = v["name"]

    # OB: ±3일 허용 범위 합산 (처리일 오차 흡수, 캐시 과다 확장 방지)
    ob_agg: dict = {}
    for (pk, sku), v in ob.items():
        if _is_date_key(pk) and not (_ob_from <= pk <= _ob_to):
            continue  # YYYY-MM-DD 형식이고 범위 밖이면 제외
        if sku not in ob_agg:
            ob_agg[sku] = {"qty": 0, "name": v.get("name", "")}
        ob_agg[sku]["qty"] += v["qty"]
        if v.get("name"):
            ob_agg[sku]["name"] = v["name"]

    all_skus = set(bh_agg) | set(ob_agg)
    rows = []
    for sku in sorted(all_skus):
        b = bh_agg.get(sku)
        o = ob_agg.get(sku)
        bh_qty = b["qty"] if b and b["qty"] != 0 else None
        ob_qty = o["qty"] if o and o["qty"] != 0 else None
        if bh_qty is None and ob_qty is None:
            continue
        name = (b or o or {}).get("name", "")
        if bh_qty is not None and ob_qty is not None:
            if bh_qty == ob_qty:
                status = "ok"
            elif qty_tolerance > 0 and max(bh_qty, ob_qty) > 0:
                diff_ratio = abs(bh_qty - ob_qty) / max(bh_qty, ob_qty)
                status = "ok" if diff_ratio <= qty_tolerance else "mismatch"
            else:
                status = "mismatch"
        elif bh_qty is not None:
            status = "bh_only"
        else:
            status = "ob_only"
        real_sku, channel = (sku.split(CH_SEP, 1) if CH_SEP in sku else (sku, ""))
        rows.append({
            "period": f"{from_date}~{to_date}",
            "sku": real_sku,
            "channel": channel,
            "name": name,
            "bh_qty": bh_qty,
            "ob_qty": ob_qty,
            "status": status,
        })
    return rows


def _compare_cumulative(bh: dict, ob: dict) -> list:
    """기간 누적 비교: 상품별로 시작 기간부터 각 기간까지 누적 수량을 비교.

    BH/OB의 전산 시점이 달라도(예: BH 5/26, OB 5/27) 누적값은 결국 같아지므로
    타이밍 차이가 자동 상쇄되고, 누적값이 벌어지는 시점이 실제 오차 발생 지점이 된다.
    각 (상품, 활동 기간)마다 그 시점까지의 누적 BH/OB를 한 행으로 출력.
    """
    skus = {s for (_, s) in bh} | {s for (_, s) in ob}
    periods = sorted({p for (p, _) in bh} | {p for (p, _) in ob})

    # 상품명 사전
    name_of: dict = {}
    for src in (ob, bh):
        for (_, s), v in src.items():
            if v.get("name"):
                name_of[s] = v["name"]

    rows = []
    for sku in sorted(skus):
        cum_bh = 0
        cum_ob = 0
        for p in periods:
            b = bh.get((p, sku))
            o = ob.get((p, sku))
            db = b["qty"] if b else 0
            do = o["qty"] if o else 0
            if db == 0 and do == 0:
                continue  # 이 기간 활동 없음 → 행 생략
            cum_bh += db
            cum_ob += do
            if cum_bh and cum_ob:
                status = "ok" if cum_bh == cum_ob else "mismatch"
            elif cum_bh:
                status = "bh_only"
            else:
                status = "ob_only"
            real_sku, channel = (sku.split(CH_SEP, 1) if CH_SEP in sku else (sku, ""))
            rows.append({
                "period": p,
                "sku": real_sku,
                "channel": channel,
                "name": name_of.get(sku, ""),
                "bh_qty": cum_bh,
                "ob_qty": cum_ob,
                "status": status,
            })
    return rows


# ── OurBox 수집 (REST API → Playwright fallback) ─────────────

def _collect_ourbox(
    ourbox_id: str, ourbox_pw: str,
    from_date: str, to_date: str,
    in_list: list, out_list: list, adj_list: list,
    errors: list,
) -> str:
    """REST API 우선 시도, 실패 시 Playwright fallback. 수집 소스 반환."""
    rest_err_msg: str = ""

    try:
        import ourbox_api as api_mod
        cfg = U.load_config()
        client = api_mod.make_client(cfg)
        if not client:
            raise RuntimeError("OurBox API Key 없음 (설정 → ourbox_access_key/secret_key)")

        fetched_in  = client.fetch_inbounds(from_date, to_date)
        fetched_out = client.fetch_outbounds(from_date, to_date)
        fetched_adj = client.fetch_adjustments(from_date, to_date)

        in_list.extend(fetched_in)
        out_list.extend(fetched_out)
        adj_list.extend(fetched_adj)

        if not fetched_in and not fetched_out and not fetched_adj:
            errors.append("REST API 연결됨 but 데이터 0건 (날짜 범위 확인 필요)")

        return "rest"

    except PermissionError as e:
        # IP 화이트리스트 오류 — Playwright fallback 시도 후 결과에 따라 메시지 결정
        rest_err_msg = f"OurBox REST API IP 미등록 (OurBox 관리자 → API → IP 화이트리스트 추가 필요)"
    except Exception as e:
        rest_err_msg = f"OurBox REST API 실패: {str(e)[:80]}"

    # Playwright fallback — stockInOutData.do로 입출고+조정 통합 수집
    import ourbox_scraper as scraper
    try:
        stock_data = scraper.fetch_stock_inout(ourbox_id, ourbox_pw, from_date, to_date)
        # 같은 데이터를 in/out/adj 모두에 넣고 정규화 시 tx_type별로 분리
        in_list.extend(stock_data)
        out_list.extend(stock_data)
        adj_list.extend(stock_data)
        # Playwright 성공 → REST API 실패는 정보 메시지로만 표시
        if rest_err_msg:
            errors.append(f"[정보] {rest_err_msg} → 웹 스크래핑으로 대체 수집 완료")
        return "playwright"
    except Exception as pw_err:
        # 둘 다 실패한 경우에만 에러로 표시
        if rest_err_msg:
            errors.append(f"REST API: {rest_err_msg}")
        errors.append(f"OurBox Playwright 실패: {str(pw_err)[:150]}")
        return "failed"


def _norm_ob_stock_inout(raw: list, period: str, tx_type: str) -> dict:
    """stockInOutData.do wide-format 응답 정규화.

    tx_type별 사용 컬럼:
      "in"         → put_qty (합계) + put_qty_YYYYMMDD (일별)
      "out"        → out_qty + out_qty_YYYYMMDD
      "adjustment" → adj_qty + adj_qty_YYYYMMDD

    상품 식별자: sale_prod_nm (SKU 없음)
    """
    if not raw:
        return {}

    prefix = {"in": "put_qty", "out": "out_qty", "adjustment": "adj_qty"}.get(tx_type, "put_qty")
    grouped: dict = defaultdict(lambda: {"name": "", "qty": 0})

    for rec in raw:
        if not isinstance(rec, dict):
            continue

        prod_nm = html.unescape(str(rec.get("sale_prod_nm") or rec.get("prod_nm") or "").strip())
        if not prod_nm:
            continue

        # 날짜별 컬럼 파싱: put_qty_YYYYMMDD
        daily_keys = {k: v for k, v in rec.items() if k.startswith(prefix + "_") and len(k) == len(prefix) + 9}

        if daily_keys:
            # 일별 데이터 → period_key 기반 집계
            for col, val in daily_keys.items():
                date_str = col[len(prefix) + 1:]  # YYYYMMDD
                try:
                    dt = datetime.strptime(date_str, "%Y%m%d")
                except ValueError:
                    continue
                qty = abs(int(float(str(val).replace(",", "") or 0)))
                if qty == 0:
                    continue
                pk = _period_key(dt, period)
                k = (pk, prod_nm)
                grouped[k]["name"] = prod_nm
                grouped[k]["qty"] += qty
        else:
            # 일별 컬럼 없으면 합계 컬럼 사용 (기간 전체를 하나의 키로)
            total_val = rec.get(prefix, "0")
            qty = abs(int(float(str(total_val).replace(",", "") or 0)))
            if qty == 0:
                continue
            pk = _period_key(datetime.now(), period)
            k = (pk, prod_nm)
            grouped[k]["name"] = prod_nm
            grouped[k]["qty"] += qty

    return dict(grouped)


def _norm_ob_auto(raw: list, period: str, source: str, tx_type: str, ch_resolver=None) -> dict:
    """수집 소스별 필드명 추정으로 정규화.
    ch_resolver: OB channel 값 -> 채널 라벨 (채널별 대사 시에만 전달)
    """
    if not raw:
        return {}

    # REST API 응답: OurBox API 확정 필드명 + 일반 REST 필드명 후보
    if source == "rest":
        # 날짜 필드 후보 (OurBox 확정 필드명 우선)
        date_candidates = [
            # OurBox REST API 확정 필드명 (put_perf / out_perf_period / stock_adj_hist)
            # 출고일/입고일(지시일) 우선 — 완료일은 하루 이상 밀릴 수 있음
            "input_dt", "input_complete_dt", "input_req_dt",   # 입고
            "out_dt", "out_complete_dt", "out_req_dt",         # 출고
            "reg_dt", "adj_dt", "adj_compt_dt",                # 조정
            # 구버전/일반 REST 후보
            "put_compt_dt", "put_req_dt", "put_dt",
            "out_compt_dt",
            "completed_at", "created_at", "date", "datetime",
            "inbound_date", "outbound_date", "adjusted_at",
        ]
        # 제품 코드/이름/수량 후보 (OurBox 확정 필드명 우선)
        code_candidates = ["product_code", "prod_cd", "sale_prod_cd", "item_code", "item_cd", "sku"]
        name_candidates = ["product_name", "sale_prod_nm", "prod_nm", "item_name", "name"]
        qty_candidates  = ["input_qty", "out_qty", "adj_qty", "put_qty",
                           "quantity", "qty", "amount", "count"]

        def _find_key(obj: dict, candidates: list):
            for k in candidates:
                if k in obj:
                    return k
            return None

        grouped: dict = defaultdict(lambda: {"name": "", "qty": 0})

        def _process_item(item: dict, dt: datetime, channel_raw: str = ""):
            # ⚠ stock_status=0 컷 제거 — 실데이터 검증 결과 stock_status=0도 정상 입고(78건/101건, 77%)
            # 6/17 팝타임 입고 20,184처럼 명백한 정상 거래가 status=0로 와 누락되던 버그
            # OurBox stock_status 의미가 데이터마다 일관되지 않아 컷이 오히려 위험

            code_k = _find_key(item, code_candidates)
            name_k = _find_key(item, name_candidates)
            qty_k  = _find_key(item, qty_candidates)
            if not code_k and not qty_k:
                return
            # 키 존재해도 값이 None일 수 있음 → "None" 문자열로 묶이는 버그 방지
            # (예: 6/17 팝타임 입고 20,184처럼 OB가 product_code를 누락한 데이터)
            _raw_cd = item.get(code_k) if code_k else None
            prod_cd = str(_raw_cd).strip() if _raw_cd not in (None, "", "None") else ""
            prod_nm = html.unescape(str(item.get(name_k, "")).strip()) if name_k else ""
            qty = abs(int(float(item.get(qty_k, 0) or 0))) if qty_k else 0
            # 코드 없으면 이름 기반 폴백 (안 그러면 코드 없는 모든 OB 거래가 한 그룹으로 묶여 사라짐)
            if not prod_cd:
                prod_cd = ("NM:" + prod_nm) if prod_nm else "UNKNOWN"
            if ch_resolver is not None:
                channel = ch_resolver(channel_raw)
                item_key = f"{prod_cd}{CH_SEP}{channel}"
            else:
                item_key = prod_cd
            key = (_period_key(dt, period), item_key)
            grouped[key]["name"] = prod_nm or grouped[key]["name"]
            grouped[key]["qty"] += qty

        for rec in raw:
            if isinstance(rec, dict):
                # header + items 구조 or flat 구조
                header = rec.get("header", rec)
                items  = rec.get("items", [])

                dt = None
                for dk in date_candidates:
                    v = str(header.get(dk, "")).strip()
                    if v and v != "None":
                        try:
                            dt = datetime.fromisoformat(v[:19].replace("/", "-"))
                            break
                        except Exception:
                            pass
                if dt is None:
                    dt = datetime.now()

                ch_raw = str(header.get("channel") or header.get("mall_name") or "").strip()

                if items:
                    for item in items:
                        item_dt = dt
                        for dk in date_candidates:
                            v = str(item.get(dk, "")).strip()
                            if v and v != "None":
                                try:
                                    item_dt = datetime.fromisoformat(v[:19].replace("/", "-"))
                                    break
                                except Exception:
                                    pass
                        item_ch = str(item.get("channel") or item.get("mall_name") or "").strip() or ch_raw
                        _process_item(item, item_dt, item_ch)
                else:
                    _process_item(header, dt, ch_raw)

        return dict(grouped)

    # Playwright fallback: stockInOutData.do wide-format 또는 기존 put_perf 형식
    return _norm_ob_stock_inout(raw, period, tx_type)


# ── 엔드포인트 ────────────────────────────────────────────────

@router.get("/probe")
def probe_ourbox():
    """아워박스 REST API 탐색 - 인증 테스트 + 엔드포인트 경로 반환"""
    cfg = U.load_config()
    try:
        import ourbox_api as api_mod
        client = api_mod.make_client(cfg)
        if not client:
            raise HTTPException(400, "OurBox API Key가 설정되지 않았습니다 (설정 페이지에서 입력)")
        return client.probe()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


def _build_mapping_groups() -> dict:
    """name_mapping + product_mapping 쌍들로 연결요소(그룹)를 구성.

    OB(상품명/상품코드)와 BH SKU를 노드로 하는 그래프에서 union-find로 그룹을 만든다.
    다대다(OB 1개↔BH 여러개, BH 1개↔OB 여러개)를 모두 하나의 그룹으로 묶는다.

    product_mapping(OB prod_cd ↔ BH SKU, 코드 직접 매핑)을 우선 통합하고,
    name_mapping(OB 상품명 ↔ BH SKU)을 보조로 같은 그래프에 합친다.
    prod_cd와 ob_name을 같은 bh_sku로 union하므로 하나의 group_label(BH SKU 조합)로
    수렴해 REST(코드키)·Playwright(이름키)·name_mapping이 동일 그룹으로 합산된다.

    Returns {
      "ob_to_group":      {ob_name: group_id},
      "ob_code_to_group": {ob_prod_cd: group_id},   # product_mapping 코드 직접 매칭용
      "bh_to_group":      {bh_sku: group_id},
      "group_label":      {group_id: 표시용 라벨(SKU 조합)},
      "group_name":       {group_id: 대표 상품명},
    }
    """
    try:
        import sys as _sys
        _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        import receiving_db as _db
        pairs = _db.get_name_mapping_pairs()  # [{ob_name, bh_sku, bh_name}]
    except Exception:
        return {}

    try:
        prod_pairs = _db.get_product_mapping_pairs()  # [{ob_prod_cd, ob_name, bh_sku, bh_name}]
    except Exception:
        prod_pairs = []

    if not pairs and not prod_pairs:
        return {}

    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    bh_name_of: dict = {}
    # name_mapping: OB 상품명 ↔ BH SKU
    for p in pairs:
        union(("ob", p["ob_name"]), ("bh", p["bh_sku"]))
        if p.get("bh_name"):
            bh_name_of[p["bh_sku"]] = p["bh_name"]
            union(("bh_name", p["bh_name"]), ("bh", p["bh_sku"]))  # BH 이름도 같은 그룹
    # product_mapping: OB 코드 ↔ BH SKU (+ OB 상품명도 같은 그룹으로 흡수)
    for p in prod_pairs:
        sku = p["bh_sku"]
        union(("obc", p["ob_prod_cd"]), ("bh", sku))
        if p.get("ob_name"):
            union(("ob", p["ob_name"]), ("bh", sku))
        if p.get("bh_name"):
            bh_name_of.setdefault(sku, p["bh_name"])
            union(("bh_name", p["bh_name"]), ("bh", sku))  # BH 이름도 같은 그룹

    # 그룹별 멤버 수집
    from collections import defaultdict as _dd
    members: dict = _dd(lambda: {"ob": set(), "obc": set(), "bh": set()})
    for p in pairs:
        root = find(("bh", p["bh_sku"]))
        members[root]["ob"].add(p["ob_name"])
        members[root]["bh"].add(p["bh_sku"])
    for p in prod_pairs:
        root = find(("bh", p["bh_sku"]))
        members[root]["obc"].add(p["ob_prod_cd"])
        members[root]["bh"].add(p["bh_sku"])
        if p.get("ob_name"):
            members[root]["ob"].add(p["ob_name"])

    ob_to_group: dict = {}
    ob_code_to_group: dict = {}
    bh_to_group: dict = {}
    bh_name_to_group: dict = {}  # BH 이름 → group (SKU 미등록 아이템 폴백용)
    group_label: dict = {}
    group_name: dict = {}
    for root, m in members.items():
        skus = sorted(m["bh"])
        gid = "GRP:" + "+".join(skus)
        label = "+".join(skus)
        # 대표 이름: BH 이름 우선, 없으면 OB 이름
        rep_name = next((bh_name_of[s] for s in skus if bh_name_of.get(s)), None)
        if not rep_name:
            rep_name = sorted(m["ob"])[0] if m["ob"] else label
        group_label[gid] = label
        group_name[gid] = rep_name
        for ob in m["ob"]:
            ob_to_group[ob] = gid
        for cd in m["obc"]:
            ob_code_to_group[cd] = gid
        for sku in m["bh"]:
            bh_to_group[sku] = gid
        # BH 이름으로도 같은 그룹 조회 가능하도록
        if rep_name:
            bh_name_to_group[rep_name] = gid
        for s in skus:
            if bh_name_of.get(s):
                bh_name_to_group[bh_name_of[s]] = gid

    return {
        "ob_to_group": ob_to_group,
        "ob_code_to_group": ob_code_to_group,
        "bh_to_group": bh_to_group,
        "bh_name_to_group": bh_name_to_group,
        "group_label": group_label,
        "group_name": group_name,
    }


def _apply_group_mapping(bh_data: dict, ob_data: dict, groups: dict) -> tuple:
    """BH(SKU 키)·OB(상품명/코드 키) 데이터를 그룹 키로 재매핑하고 합산.

    - BH 항목: bh_sku가 그룹에 속하면 (period, group_label)로 합산
    - OB 항목: val["name"]이 그룹에 속하면 (period, group_label)로 합산
    그룹에 안 속한 항목은 원래 키 유지.
    Returns (bh2, ob2, 적용된 OB 항목 수)
    """
    if not groups:
        return bh_data, ob_data, 0

    ob_to_group = groups["ob_to_group"]
    ob_code_to_group = groups.get("ob_code_to_group", {})
    bh_to_group = groups["bh_to_group"]
    group_label = groups["group_label"]
    group_name = groups["group_name"]

    def _emit(out: dict, period_key: str, gid: str, suffix: str, val: dict):
        new_key = (period_key, group_label[gid] + suffix)
        if new_key in out:
            out[new_key]["qty"] += val["qty"]
        else:
            out[new_key] = {"name": group_name[gid], "qty": val["qty"]}

    def _remap_bh(data: dict) -> tuple:
        """BH: item_key = sku → bh_to_group 직접 매칭, 실패 시 이름(bh_name_to_group) 폴백."""
        bh_name_to_group = groups.get("bh_name_to_group", {})
        out: dict = {}
        applied = 0
        for (period_key, item_key), val in data.items():
            if CH_SEP in item_key:
                base, ch = item_key.split(CH_SEP, 1)
                suffix = CH_SEP + ch
            else:
                base, suffix = item_key, ""
            gid = bh_to_group.get(base)
            # SKU 미등록 시 BH 이름으로 폴백 (동일 품목 다른 SKU 처리)
            if not gid:
                import re as _re_nm2
                nm = val.get("name", "")
                gid = bh_name_to_group.get(nm)
                # 유통기한 suffix 제거 후 재시도
                if not gid:
                    nm_s = _re_nm2.sub(r'-\d{4}-\d{2}-\d{2}$', '', nm).strip()
                    if nm_s != nm:
                        gid = bh_name_to_group.get(nm_s)
            if gid:
                _emit(out, period_key, gid, suffix, val)
                applied += 1
            else:
                out[(period_key, item_key)] = dict(val)
        return out, applied

    def _remap_ob(data: dict) -> tuple:
        """OB: 1단계 prod_cd(=base) 코드 매칭 → 2단계 상품명 매칭 → 실패 시 원본 유지."""
        out: dict = {}
        applied = 0
        for (period_key, item_key), val in data.items():
            if CH_SEP in item_key:
                base, ch = item_key.split(CH_SEP, 1)
                suffix = CH_SEP + ch
            else:
                base, suffix = item_key, ""
            # 1단계: prod_cd 직접 코드 매칭 (REST 경로, 핵심)
            gid = ob_code_to_group.get(base)
            # 1.5단계: OB product_code가 BH SKU와 동일한 바코드인 경우 (ex. 9345544007117)
            if not gid:
                gid = bh_to_group.get(base)
            # 2단계: 상품명 매칭 (Playwright/이름 경로, name_mapping)
            if not gid:
                import re as _re_nm
                nm = val.get("name", "")
                gid = ob_to_group.get(nm) or ob_to_group.get(html.unescape(nm))
                # 2.5단계: 유통기한 suffix 제거 후 재시도 (예: "상품명-2028-01-15" → "상품명")
                if not gid:
                    nm_stripped = _re_nm.sub(r'-\d{4}-\d{2}-\d{2}$', '', nm).strip()
                    if nm_stripped != nm:
                        gid = ob_to_group.get(nm_stripped) or ob_to_group.get(html.unescape(nm_stripped))
            if gid:
                _emit(out, period_key, gid, suffix, val)
                applied += 1
            else:
                out[(period_key, item_key)] = dict(val)
        return out, applied

    bh2, _ = _remap_bh(bh_data)
    ob2, n = _remap_ob(ob_data)
    return bh2, ob2, n


def _build_channel_resolvers():
    """channel_mapping으로 OB채널/BH memo → 공통 채널 라벨 변환기 생성.

    Returns (ob_resolver, bh_resolver, enabled)
      ob_resolver(channel) -> 라벨 (미매핑이면 원본 채널)
      bh_resolver(memo)    -> 라벨 (매핑 키워드 미포함이면 '채널미상')
    """
    try:
        import sys as _sys
        _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        import receiving_db as _db
        pairs = _db.get_channel_mapping_pairs()
    except Exception:
        pairs = []

    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for p in pairs:
        union(("ob", p["ob_channel"]), ("bh", p["bh_keyword"]))

    from collections import defaultdict as _dd
    members: dict = _dd(lambda: {"ob": set(), "bh": set()})
    for p in pairs:
        root = find(("ob", p["ob_channel"]))
        members[root]["ob"].add(p["ob_channel"])
        members[root]["bh"].add(p["bh_keyword"])

    # ── 정규화: 날짜·공백·특수문자 제거(소문자), '&'는 변별용으로 보존 ──
    def _norm_ch(s: str) -> str:
        s = html.unescape(str(s or ""))
        s = re.sub(r'\d+\s*월\s*\d+\s*일?', ' ', s)
        s = re.sub(r'\d{2,4}[-./]\d{1,2}([-./]\d{1,2})?', ' ', s)
        return re.sub(r'[\s\-_·•\[\]()（）#,]', '', s).lower()

    # 채널 변별력 없는 흔한 토큰 (이것만으로는 채널 구분 불가)
    _STOP_CH = {"스마트스토어", "스마트", "스토어", "홈쇼핑", "도착보장", "네이버",
                "미리주문", "본방", "택배", "대리점", "무상", "주문", "출고", "처리"}

    def _tokens(s: str) -> list:
        s = html.unescape(str(s or ""))
        s = re.sub(r'\d+\s*월\s*\d+\s*일?', ' ', s)
        s = re.sub(r'\d{2,4}[-./]\d{1,2}([-./]\d{1,2})?', ' ', s)
        # 흔한 접두/접미어를 분리해 '지에스홈쇼핑'→'지에스', '마켓컬리'→'컬리', '스마트스토어'→'스마트' 등 변별어 노출
        s = re.sub(r'(홈쇼핑|쇼핑|스토아|스토어|택배|대리점|미리주문|본방|세트)', r' \1 ', s)
        s = re.sub(r'^(마켓|네이버|카카오|기내|홈)', r'\1 ', s.strip())   # 접두어 분리
        out = []
        for t in re.split(r'[\s\-_·•\[\]()（）#,/]+', s):
            tn = re.sub(r'[\s]', '', t).lower().strip()
            if len(tn) >= 2 and tn not in _STOP_CH:
                out.append(tn)
        return out

    ob_to_label: dict = {}
    bh_kw_label: list = []   # (norm_keyword, label) — 전체 부분매칭용
    from collections import defaultdict as _dd2
    tok_labels: dict = _dd2(set)   # 토큰 → {라벨...} (변별 토큰 식별용)
    for root, m in members.items():
        label = sorted(m["ob"])[0] if m["ob"] else sorted(m["bh"])[0]
        for ob in m["ob"]:
            ob_to_label[ob] = label
        for kw in m["bh"]:
            nkw = _norm_ch(kw)
            if nkw:
                bh_kw_label.append((nkw, label))
            for tok in _tokens(kw):
                tok_labels[tok].add(label)
        for ob in m["ob"]:        # OB 채널명의 변별 토큰도 BH 메모 매칭에 활용
            for tok in _tokens(ob):
                tok_labels[tok].add(label)
    # 변별 토큰 = 정확히 한 라벨에만 속한 토큰 (긴 토큰 우선)
    disc_tok = sorted(
        ((tok, next(iter(labs))) for tok, labs in tok_labels.items() if len(labs) == 1),
        key=lambda x: -len(x[0]))
    bh_kw_label.sort(key=lambda x: -len(x[0]))

    def ob_resolver(channel: str) -> str:
        channel = (channel or "").strip()
        if channel in ob_to_label:
            return ob_to_label[channel]
        nm = _norm_ch(channel)
        for kw, label in bh_kw_label:
            if kw and kw in nm:
                return label
        for tok, label in disc_tok:
            if tok in nm:
                return label
        return channel or "채널미상"

    def bh_resolver(memo: str) -> str:
        nm = _norm_ch(memo)
        if not nm:
            return "채널미상"
        # 1) 정규화 키워드 전체 부분매칭 (긴 것 우선)
        for kw, label in bh_kw_label:
            if kw and kw in nm:
                return label
        # 2) 변별 토큰 매칭 (예: 'dj&a','이알하나','지에스','쿠팡','올리브영')
        for tok, label in disc_tok:
            if tok in nm:
                return label
        return "채널미상"

    return ob_resolver, bh_resolver, bool(pairs)


@router.delete("/cache")
def clear_cache(from_date: str = Query(""), to_date: str = Query("")):
    """BH TX 목록 캐시 + OB 캐시 무효화 → 다음 조회 시 API에서 새로 수집."""
    import utils_core as U2
    U2.invalidate_bh_txlist_cache(from_date, to_date)
    # OB 캐시도 초기화
    global _ob_file_cache
    if from_date and to_date:
        keys = [k for k in _ob_file_cache if from_date in k or to_date in k]
        for k in keys:
            del _ob_file_cache[k]
    else:
        _ob_file_cache = {}
    try:
        import os as _os
        if _os.path.exists(_OB_CACHE_PATH):
            with open(_OB_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(_ob_file_cache, f, ensure_ascii=False)
    except Exception:
        pass
    # full_match 메모리 캐시 초기화
    state.full_match_cache = {}
    # full_match 파일 캐시 초기화
    _FM_CACHE_PATH2 = os.path.join(os.path.dirname(__file__), "..", "data", "full_match_cache.json")
    try:
        if os.path.exists(_FM_CACHE_PATH2):
            with open(_FM_CACHE_PATH2, "r", encoding="utf-8") as f:
                _fc2 = json.load(f)
            if from_date and to_date:
                _fc2 = {k: v for k, v in _fc2.items() if from_date not in k and to_date not in k}
            else:
                _fc2 = {}
            with open(_FM_CACHE_PATH2, "w", encoding="utf-8") as f:
                json.dump(_fc2, f, ensure_ascii=False)
    except Exception:
        pass
    return {"cleared": True, "from_date": from_date, "to_date": to_date}


def _build_unmapped(all_rows: list, groups: dict) -> dict:
    """미매핑 상품 목록 구성 + 이름 유사도 자동 제안 (rapidfuzz 없으면 토큰 기반 폴백)"""
    import re as _re

    def _tok(s: str) -> set:
        """한글/영문/숫자 2글자 이상 토큰 집합"""
        return set(t for t in _re.findall(r"[가-힣]+|[a-z0-9]+", s.lower()) if len(t) >= 2)

    def _sim(a: str, b: str) -> float:
        """토큰 자카드 유사도 (0~1)"""
        ta, tb = _tok(a), _tok(b)
        if not ta or not tb:
            return 0.0
        inter = len(ta & tb)
        union = len(ta | tb)
        return inter / union if union else 0.0

    bh_only_raw = sorted(
        (r for r in all_rows if r["status"] == "bh_only" and r["sku"] not in groups.get("bh_to_group", {})),
        key=lambda x: -(x.get("bh_qty") or 0),
    )
    ob_only_raw = sorted(
        (r for r in all_rows if r["status"] == "ob_only"
         and r["sku"] not in groups.get("ob_code_to_group", {})
         and r["name"] not in groups.get("ob_to_group", {})),
        key=lambda x: -(x.get("ob_qty") or 0),
    )

    # 중복 제거 (동일 SKU 복수 행)
    bh_seen: dict = {}
    for r in bh_only_raw:
        sku = r["sku"]
        if sku not in bh_seen:
            bh_seen[sku] = {"sku": sku, "name": r["name"], "qty": r.get("bh_qty", 0), "tx_type": r.get("tx_type", "")}

    ob_seen: dict = {}
    for r in ob_only_raw:
        sku = r["sku"]
        if sku not in ob_seen:
            ob_seen[sku] = {"sku": sku, "name": r["name"], "qty": r.get("ob_qty", 0), "tx_type": r.get("tx_type", "")}

    bh_list = list(bh_seen.values())
    ob_list = list(ob_seen.values())

    # 이름 유사도 제안: BH-only ↔ OB-only 이름 매칭 (점수 0.3 이상)
    suggestions: list = []
    _MIN_SCORE = 0.30
    for bh in bh_list:
        best: list = []
        for ob in ob_list:
            score = _sim(bh["name"], ob["name"])
            if score >= _MIN_SCORE:
                best.append({"score": round(score, 2), "ob_sku": ob["sku"], "ob_name": ob["name"], "ob_qty": ob["qty"]})
        best.sort(key=lambda x: -x["score"])
        if best:
            suggestions.append({
                "bh_sku": bh["sku"],
                "bh_name": bh["name"],
                "bh_qty": bh["qty"],
                "matches": best[:3],
            })

    return {
        "bh_only": bh_list,
        "ob_only": ob_list,
        "suggestions": suggestions,
    }


# ── 원인 자동 분류기 ──────────────────────────────────────────────
# 비교 결과 각 불일치 행에 재현 가능한 규칙 기반 원인(root_cause)과
# 정리 방향(fix_target/fix_hint)을 라벨링한다. AI 분석과 달리 결정론적이라
# "전산 정리" 작업의 1차 분류 토대로 사용한다.
ROOT_CAUSE_LABELS = {
    "ok":               "정상",
    "adj_initial":      "기초재고 조정",
    "set_bom":          "세트 분해",
    "timing":           "시점 차이",
    "product_unmapped": "상품 미매핑",
    "channel_unmapped": "채널 미매핑",
    "qty_mismatch":     "수량 불일치",
    "true_missing":     "한쪽 누락",
}


def _build_correction(r: dict, cause: str, fix_target: str) -> dict:
    """행 하나에 대한 반자동 수정안 생성.

    실제 시스템에 쓰지 않고, 담당자가 해당 시스템에 직접 입력할 수 있도록
    조치 설명(action)과 복붙용 한 줄(copy_text)을 만든다.

    반환: {system, op, qty, action, copy_text}
      system: BH(박스히어로 입력) / OB(아워박스 입력) / MAPPING(매핑·세트) / REVIEW(대조 필요) / NONE
      op:     in/out/adjust/map/review
      qty:    입력·조정 수량 (부호 포함, 없으면 0)
      copy_text: 스프레드시트 붙여넣기용 탭 구분 한 줄
    """
    tx = r.get("tx_type", "")
    op_label = {"in": "입고", "out": "출고", "adjustment": "조정"}.get(tx, tx)
    sku = r.get("sku", "")
    name = r.get("name", "")
    channel = r.get("channel", "") or ""
    period = r.get("period", "")
    bh_q = r.get("bh_qty")
    ob_q = r.get("ob_qty")

    def _row(system, op, qty, action):
        # 탭 구분: 날짜 / SKU / 상품명 / 시스템 / 작업 / 수량 / 채널
        copy_text = "\t".join([
            str(period), str(sku), str(name),
            system, f"{op_label}", str(qty), channel,
        ])
        return {"system": system, "op": op, "qty": qty,
                "action": action, "copy_text": copy_text}

    if cause == "true_missing":
        if bh_q is None:
            q = int(ob_q or 0)
            return _row("BH", tx, q,
                        f"박스히어로에 {op_label} {q}개 입력 (아워박스에만 기록됨)")
        else:
            q = int(bh_q or 0)
            return _row("OB", tx, q,
                        f"아워박스에 {op_label} {q}개 입력/확인 (박스히어로에만 기록됨)")

    if cause == "qty_mismatch":
        bv = int(bh_q or 0); ov = int(ob_q or 0)
        diff = bv - ov
        # 어느 쪽이 정답인지 모르므로 양방향 후보 제시 (조정 수량 = 차이)
        action = (f"수량 대조 필요: BH {bv} ↔ OB {ov} (차이 {diff:+d}). "
                  f"정답 확인 후 → BH를 OB에 맞추려면 조정 {(-diff):+d}, "
                  f"OB를 BH에 맞추려면 조정 {diff:+d}")
        return {"system": "REVIEW", "op": "review", "qty": abs(diff),
                "action": action,
                "copy_text": "\t".join([str(period), str(sku), str(name),
                                        "REVIEW", op_label, f"{diff:+d}", channel])}

    if cause == "set_bom":
        return _row("MAPPING", "map", 0,
                    "세트 BOM 등록/확인 후 재대사 (세트↔단품 수량 비율 연결)")
    if cause == "product_unmapped":
        return _row("MAPPING", "map", 0,
                    "상품 매핑 추가 (박스히어로 SKU ↔ 아워박스 코드 연결)")
    if cause == "channel_unmapped":
        return _row("MAPPING", "map", 0, "채널 매핑 규칙 추가")
    if cause == "adj_initial":
        return _row("REVIEW", "review", 0,
                    "기초재고/조정 노이즈 — 조정 제외 토글 또는 아워박스 조정으로 정리")
    if cause == "timing":
        return _row("NONE", "review", 0,
                    "시점 차이 — 누적 모드로 재확인, 대개 조치 불필요")
    return _row("REVIEW", "review", 0, "원인 확인 필요")


def _classify_root_causes(all_rows: list, groups: dict, period: str,
                          by_channel: bool = False) -> dict:
    """all_rows의 각 행에 root_cause / fix_target / fix_hint를 부여 (in-place).

    우선순위(강한 구조적 신호 → 약한 신호):
      1) adj_initial   : in/out 동일, adj만 차이 (BH 기초재고 설정 노이즈)
      2) set_bom       : 세트 비율 패턴 or set_bom 등록 품목
      3) timing        : 반대 시스템이 인접/다른 기간에 동일 SKU 보유 → 시점 차이
      4) product_unmapped : *_only인데 매핑 그룹에 없음 → 매핑 추가하면 해소
      5) channel_unmapped : by_channel 모드에서 채널 미해소
      6) qty_mismatch  : 양쪽 모두 존재하나 수량 다름 (실제 오입력 의심)
      7) true_missing  : 한쪽에만 존재 + 위 모두 아님 (전표 확인 필요)

    반환: {root_cause: {count, label, fix_target}} 요약.
    """
    from collections import defaultdict as _dd

    # set_bom 등록 SKU/이름 집합
    set_bom_keys: set = set()
    try:
        import receiving_db as _rdb
        for _b in (_rdb.get_set_boms() or []):
            for _k in (_b.get("set_sku"), _b.get("set_name"),
                       _b.get("component_sku"), _b.get("component_name")):
                if _k:
                    set_bom_keys.add(str(_k).strip())
    except Exception:
        pass

    # 매핑 그룹 인덱스 (안전 접근)
    bh_to_group      = groups.get("bh_to_group", {}) or {}
    bh_name_to_group = groups.get("bh_name_to_group", {}) or {}
    ob_code_to_group = groups.get("ob_code_to_group", {}) or {}
    ob_to_group      = groups.get("ob_to_group", {}) or {}

    def _is_mapped(r: dict) -> bool:
        sku = r.get("sku", "")
        name = r.get("name", "")
        if r.get("status") == "bh_only":
            return sku in bh_to_group or name in bh_name_to_group
        if r.get("status") == "ob_only":
            return sku in ob_code_to_group or name in ob_to_group
        return True  # 양쪽 존재 행은 이미 매칭됨

    # 시점 차이 색인: (tx_type, sku|name) → 반대 시스템이 보유한 기간 집합
    bh_periods: dict = _dd(set)
    ob_periods: dict = _dd(set)
    for r in all_rows:
        key = (r.get("tx_type", ""), r.get("sku") or r.get("name"))
        if r.get("bh_qty"):
            bh_periods[key].add(r.get("period"))
        if r.get("ob_qty"):
            ob_periods[key].add(r.get("period"))

    summary: dict = _dd(int)
    for r in all_rows:
        status = r.get("status")
        if status == "ok":
            r["root_cause"] = "ok"
            r["fix_target"] = ""
            r["fix_hint"] = ""
            summary["ok"] += 1
            continue

        cause = None
        mc = r.get("mismatch_cause", "")
        key = (r.get("tx_type", ""), r.get("sku") or r.get("name"))
        _bq = r.get("bh_qty") or 0
        _oq = r.get("ob_qty") or 0

        # 세트 비율 폴백: mismatch_cause 미설정(period/cumulative 모드)에서도 감지
        _ratio_hit = False
        if status == "mismatch" and _bq > 0 and _oq > 0:
            _rt = max(_bq, _oq) / min(_bq, _oq)
            if 2 <= _rt <= 20 and abs(_rt - round(_rt)) < 0.1:
                _ratio_hit = True

        if mc == "adj_only":
            cause = "adj_initial"
        elif mc == "set_ratio" or _ratio_hit \
                or r.get("sku") in set_bom_keys or r.get("name") in set_bom_keys:
            cause = "set_bom"
        elif status in ("bh_only", "ob_only"):
            # *_only: 매핑 누락 → 시점 차이 → 실제 누락 순으로 판정
            if not _is_mapped(r):
                cause = "product_unmapped"
            elif (status == "bh_only" and ob_periods.get(key)) or \
                 (status == "ob_only" and bh_periods.get(key)):
                cause = "timing"
            elif by_channel and not r.get("channel"):
                cause = "channel_unmapped"
            else:
                cause = "true_missing"
        elif status == "mismatch":
            cause = "qty_mismatch"
        else:
            cause = "true_missing"

        # 정리 방향 결정 (어느 시스템을 손봐야 하는가)
        if cause in ("product_unmapped", "channel_unmapped", "set_bom"):
            fix_target = "MAPPING"
        elif cause == "adj_initial":
            fix_target = "REVIEW"   # 비교 제외 또는 OB 조정 입력 검토
        elif cause == "timing":
            fix_target = "NONE"     # 대개 실제 오차 아님
        elif cause == "true_missing":
            # 기록이 없는 쪽에 추가하는 것이 정답 (None인 시스템이 보정 대상)
            fix_target = "BH" if r.get("bh_qty") is None else "OB"
        elif cause == "qty_mismatch":
            fix_target = "REVIEW"   # 양쪽 존재·수량 상이 — 어느 쪽이 정답인지 대조 필요
        else:
            fix_target = "REVIEW"

        _HINTS = {
            "adj_initial": "BH 기초재고/조정 노이즈 — 비교에서 제외하거나 OB 조정으로 맞춤",
            "set_bom": "세트 분해 의심 — 세트 BOM 등록/확인 후 재대사",
            "timing": "시점 차이 — 누적(cumulative) 모드로 재확인, 실제 누락 아닐 가능성 높음",
            "product_unmapped": "상품 매핑 누락 — BH SKU ↔ OB 코드 매핑 추가하면 해소",
            "channel_unmapped": "채널 매핑 누락 — 채널 매핑 규칙 추가",
            "qty_mismatch": "수량 불일치 — 한쪽 전산 오입력 의심, 전표 대조 필요",
            "true_missing": "한쪽에만 기록 — 실제 누락/오입력 의심, 전표 확인 필요",
        }
        r["root_cause"] = cause
        r["fix_target"] = fix_target
        r["fix_hint"] = _HINTS.get(cause, "")
        r["correction"] = _build_correction(r, cause, fix_target)
        summary[cause] += 1

    return {
        k: {"count": v, "label": ROOT_CAUSE_LABELS.get(k, k)}
        for k, v in sorted(summary.items(), key=lambda x: -x[1])
    }


@router.get("/compare")
def compare(
    token: str = Query(...),
    from_date: str = Query(...),
    to_date: str = Query(...),
    period: str = Query("day"),
    location_id: Optional[int] = Query(None),
    location_ids: Optional[str] = Query(None),  # 콤마구분 복수 location, location_id보다 우선
    use_mapping: bool = Query(True),
    mode: str = Query("period"),  # "period"=기간별 / "cumulative"=기간 누적
    by_channel: bool = Query(False),  # 채널별 구분 비교
    bh_lookback: int = Query(7),  # BH 날짜 오프셋 보정: OB ±N일 내 BH 기록 fuzzy 매칭 (기본 7일 — 월말/월초 경계 커버)
    qty_tolerance: float = Query(0.0),  # 수량 허용 오차 비율 (0.05 = ±5%)
    merge_types: bool = Query(False),  # True: in/out/adj 유형 무관 품목별 순수량 합산 비교
    exclude_adj: bool = Query(False),  # True: 조정(adjustment) 항목 제외 — BH 기초재고 설정 등 노이즈 제거
    bh_adj_max_qty: int = Query(0),    # BH adj 임계값: qty >= 이값 SKU를 양측 adj에서 제외 (기초재고 자동 필터, 0=비활성)
    hide_resolved: bool = Query(False), # True: 정리완료(resolved)·무시(ignore)로 마킹된 행을 비교 결과에서 제외
):
    if period not in ("day", "week", "month", "year"):
        raise HTTPException(400, "period must be day, week, month, or year")
    if mode not in ("period", "cumulative", "total"):
        raise HTTPException(400, "mode must be period, cumulative, or total")

    # location_ids 파싱 (콤마구분 문자열 → int 리스트)
    loc_id_list: list = []
    if location_ids:
        for v in location_ids.split(","):
            v = v.strip()
            if v.isdigit():
                loc_id_list.append(int(v))
    elif location_id:
        loc_id_list = [location_id]

    cfg = U.load_config()
    ourbox_id = cfg.get("ourbox_id")
    ourbox_pw = cfg.get("ourbox_pw")
    has_ourbox = bool(ourbox_id and ourbox_pw)

    # ── BoxHero 데이터 수집 (in/out/adjust) ──
    # BH는 OB보다 1~2일 앞서 기록되는 경우가 많으므로 ±bh_lookback일 확장 조회
    errors = []
    bh_from = (datetime.strptime(from_date, "%Y-%m-%d") - timedelta(days=bh_lookback)).strftime("%Y-%m-%d")
    bh_to   = (datetime.strptime(to_date,   "%Y-%m-%d") + timedelta(days=bh_lookback)).strftime("%Y-%m-%d")
    bh_in_raw, bh_out_raw, bh_adj_raw = [], [], []
    for tx_type in ("in", "out"):
        try:
            if loc_id_list:
                # 복수 location: 각각 조회 후 합산
                combined = []
                for lid in loc_id_list:
                    combined.extend(U.fetch_transactions(token, tx_type, bh_from, bh_to, lid))
                txs = combined
            else:
                txs = U.fetch_transactions(token, tx_type, bh_from, bh_to, None)
            if tx_type == "in":
                bh_in_raw = txs
            else:
                bh_out_raw = txs
        except Exception as e:
            errors.append(f"BoxHero {tx_type} 조회 실패: {str(e)[:80]}")

    # BH 이동(move) 수집 — OB 출고가 BH에서 '이동'으로 기록되는 케이스 (full-match와 동일 처리)
    bh_move_raw = []
    try:
        bh_move_raw = U.fetch_transactions(token, "move", bh_from, bh_to, None)
    except Exception as e:
        errors.append(f"BoxHero move 조회 실패: {str(e)[:80]}")

    # BH 조정(adjust) 수집 — 세트 조립 처리와 매칭 (OB 전산처리용 ↔ BH 조정)
    try:
        bh_adj_raw = U.fetch_transactions(token, "adjust", bh_from, bh_to, None)
    except Exception as e:
        errors.append(f"BoxHero adjust 조회 실패: {str(e)[:80]}")

    # 거래 상세(items) 병렬 보강 — 목록 API는 헤더만 반환
    try:
        _enrich_bh_items(token, bh_in_raw)
        _enrich_bh_items(token, bh_out_raw)
        _enrich_bh_items(token, bh_move_raw)
        _enrich_bh_items(token, bh_adj_raw, tx_type="adjust")  # adjust: /v1/txs/{id}
    except Exception as e:
        errors.append(f"BoxHero 거래 상세 조회 실패: {str(e)[:80]}")

    # 위치 필터: BoxHero 목록 API는 location_id 파라미터를 무시하므로 (실측 확인)
    # TX 상세의 to/from_location 기준으로 수집 후 필터링 (full-match와 동일 방식)
    if loc_id_list:
        try:
            _loc_set_cmp = {int(x) for x in loc_id_list if str(x).strip().isdigit()}
            if _loc_set_cmp:
                import concurrent.futures as _cf_cmp
                def _filter_by_loc(_txs: list, _prefer_from: bool = False) -> list:
                    _locs: dict = {}
                    with _cf_cmp.ThreadPoolExecutor(max_workers=3) as _ex:
                        _fs = {_ex.submit(_get_bh_tx_loc_id, token, t["id"], "location", _prefer_from): t["id"]
                               for t in _txs if t.get("id")}
                        for _f in _cf_cmp.as_completed(_fs):
                            try: _locs[_fs[_f]] = _f.result()
                            except Exception: _locs[_fs[_f]] = None
                    # 위치 미상은 보수적으로 포함, 명확히 다른 위치만 제외
                    return [t for t in _txs
                            if _locs.get(t.get("id")) is None or _locs.get(t.get("id")) in _loc_set_cmp]
                bh_in_raw = _filter_by_loc(bh_in_raw)
                bh_out_raw = _filter_by_loc(bh_out_raw)
                # move는 출발지(from) 기준 — 이 위치에서 나간 이동(폐기·타창고 이송)을 출고로 포함
                bh_move_raw = _filter_by_loc(bh_move_raw, _prefer_from=True)
        except Exception as _e_loc:
            errors.append(f"[정보] BH 위치 필터 실패(전체 위치로 진행): {str(_e_loc)[:60]}")

    # 채널별 대사면 채널 변환기 준비
    ob_ch_res = bh_ch_res = None
    channel_mapped = False
    if by_channel:
        ob_ch_res, bh_ch_res, channel_mapped = _build_channel_resolvers()

    # 정규화 기간 키: 총량/유형합산 모드는 day 단위로 만들어 날짜 범위 필터가 작동하게 함.
    #   (period="month"면 키가 "2026-05"라 _build_sku_qty_map·_compare_total의 날짜필터가
    #    무력화돼 bh_lookback로 당겨온 범위 밖 거래까지 합산되는 버그 → exact 기간 합과 불일치)
    _norm_period = "day" if (mode == "total" or merge_types) else period
    bh_in  = _normalize_bh_txs(bh_in_raw,  "in",     _norm_period, bh_ch_res)
    # move는 출고와 분리 — 이동은 재고 총량 불변이므로 순수 출고와 별도 집계
    bh_out = _normalize_bh_txs(bh_out_raw, "out", _norm_period, bh_ch_res)
    bh_move = _normalize_bh_txs(bh_move_raw, "out", _norm_period, bh_ch_res)
    # full-match 비교에서는 out+move 합산본도 필요 (OB 출고와 크로스 매칭)
    bh_out_plus_move = _normalize_bh_txs(bh_out_raw + bh_move_raw, "out", _norm_period, bh_ch_res)
    # BH adj: 음수 항목(단품 소진)만 포함. 양수(세트 추가)는 매칭 불필요 → 제외
    bh_adj_raw_neg = _filter_bh_adj_negative(bh_adj_raw)
    bh_adj = _normalize_bh_txs(bh_adj_raw_neg, "adjust", _norm_period, bh_ch_res)

    # ── OurBox Mate 데이터 수집 (REST API 우선, Playwright fallback) ──
    ob_in_raw, ob_out_raw, ob_adj_raw = [], [], []
    ob_source = "none"
    if has_ourbox:
        # OB도 ±2일 확장 범위로 수집 — 드릴다운에서 날짜 크로스 매칭(OB 먼저→BH 나중) 지원
        ob_ext_from = (datetime.strptime(from_date, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
        ob_ext_to   = (datetime.strptime(to_date,   "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")
        ob_cache_key = f"{ob_ext_from}|{ob_ext_to}"
        _load_ob_file_cache()
        ob_cached = _get_ob_cache(ob_cache_key)
        if ob_cached:
            ob_in_raw.extend(ob_cached["in"])
            ob_out_raw.extend(ob_cached["out"])
            ob_adj_raw.extend(ob_cached["adj"])
            ob_source = ob_cached.get("source", "rest")
            errors.append(f"[캐시] OB 데이터 파일 캐시 사용 ({ob_ext_from}~{ob_ext_to})")
        else:
            ob_source = _collect_ourbox(
                ourbox_id, ourbox_pw, ob_ext_from, ob_ext_to,
                ob_in_raw, ob_out_raw, ob_adj_raw, errors,
            )
            # 수집 성공 시 파일 캐시에 저장 (다음 조회부터 빠름)
            if ob_source != "failed":
                try:
                    _save_ob_file_cache_entry(ob_cache_key, ob_in_raw, ob_out_raw, ob_adj_raw, ob_source)
                except Exception:
                    pass

    # 사장된 OB 코드 제거 (P000000000011556 등)
    if DEPRECATED_OB_CODES:
        n_dep_before = len(ob_in_raw) + len(ob_out_raw)
        ob_in_raw  = _filter_deprecated(ob_in_raw)
        ob_out_raw = _filter_deprecated(ob_out_raw)
        n_dep_after = len(ob_in_raw) + len(ob_out_raw)
        n_dep = n_dep_before - n_dep_after
        if n_dep:
            errors.append(f"[정보] 사장 OB 코드 {n_dep}건 제외: {', '.join(DEPRECATED_OB_CODES)}")

    # 기초재고 일괄입고 제거 (OurBox 도입 초기 재고등록 — BH 미대응, 입고 비교 왜곡 방지)
    if OB_INITIAL_STOCK_INPUT_CODES:
        _n_init_before = len(ob_in_raw)
        ob_in_raw = _filter_initial_stock(ob_in_raw)
        _n_init = _n_init_before - len(ob_in_raw)
        if _n_init:
            errors.append(f"[정보] 기초재고 일괄입고 {_n_init}건 제외: input_code {', '.join(sorted(OB_INITIAL_STOCK_INPUT_CODES))}")

    # 원본 raw 건수 보존 (data_counts용)
    _ob_in_raw_cnt  = len(ob_in_raw)
    _ob_out_raw_cnt = len(ob_out_raw)
    _ob_adj_raw_cnt = len(ob_adj_raw)

    # 전산처리용 = 세트 조립 처리
    # - 출고(단품 소진) → adj 버킷 (BH adj 음수와 매칭)
    # - 입고(세트 추가) → 제외 (매칭 불필요)
    if ob_source == "rest":
        ob_in_raw, ob_out_raw, ob_adj_raw, n_asm = _route_ob_assembly(ob_in_raw, ob_out_raw, ob_adj_raw)
        if n_asm:
            errors.append(f"[정보] 세트조립(전산처리용) {n_asm}건 처리: 출고→adj, 입고→제외")

    # OB ADJ: item_cd → product_name 보강
    # ADJ 레코드에는 product_code가 없고 item_cd만 있는 경우가 많음.
    # _get_ob_code_to_name()으로 company_code → 상품명 변환 후 product_name 필드 추가.
    # _norm_ob_auto가 name_candidates에서 product_name을 읽어 _remap_ob의 이름 매칭에 활용됨.
    if has_ourbox and ob_adj_raw:
        try:
            import ourbox_api as _api_mod_adj
            _adj_client = _api_mod_adj.make_client(cfg)
            if _adj_client:
                _ob_code_map = _adj_client._get_ob_code_to_name()
                if _ob_code_map:
                    _enriched = 0
                    for _rec in ob_adj_raw:
                        # product_code/prod_cd 없는 레코드만 보강
                        if not _rec.get("product_code") and not _rec.get("prod_cd") and not _rec.get("sale_prod_cd"):
                            _icd = str(_rec.get("item_cd") or "").strip()
                            if _icd:
                                _base = _icd.split("-")[0] if "-" in _icd else _icd
                                _nm = _ob_code_map.get(_icd) or _ob_code_map.get(_base)
                                if _nm and not _rec.get("product_name"):
                                    _rec["product_name"] = _nm
                                    _enriched += 1
                    if _enriched:
                        errors.append(f"[정보] OB ADJ item_cd → 상품명 보강 {_enriched}건")
        except Exception:
            pass

    ob_in  = _norm_ob_auto(ob_in_raw,  _norm_period, ob_source, "in",  ob_ch_res)
    ob_out = _norm_ob_auto(ob_out_raw, _norm_period, ob_source, "out", ob_ch_res)
    ob_adj = _norm_ob_auto(ob_adj_raw, _norm_period, ob_source, "adjustment", ob_ch_res)

    if by_channel and not channel_mapped:
        errors.append("[정보] 채널 매핑이 없어 채널이 매칭되지 않습니다 (상품 매핑 → 채널 매핑에서 연결)")

    # ── 상품 매핑 적용 (OB 상품명 ↔ BH SKU 그룹 합산) ──────────
    mapping_applied = 0
    groups: dict = {}
    if use_mapping:
        groups = _build_mapping_groups()
        bh_in,  ob_in,  n1 = _apply_group_mapping(bh_in,  ob_in,  groups)
        bh_out, ob_out, n2 = _apply_group_mapping(bh_out, ob_out, groups)
        bh_adj, ob_adj, n3 = _apply_group_mapping(bh_adj, ob_adj, groups)
        mapping_applied = n1 + n2 + n3

    # ── 드릴다운용 개별 라인아이템 캐시 ──────────────────────────
    try:
        flat = []
        flat += _flat_bh(bh_in_raw,  "in",         period, groups, bh_ch_res, by_channel)
        flat += _flat_bh(bh_out_raw, "out",        period, groups, bh_ch_res, by_channel)
        flat += _flat_bh(bh_adj_raw, "adjustment", period, groups, bh_ch_res, by_channel)
        flat += _flat_ob(ob_in_raw,  "in",         period, ob_source, groups, ob_ch_res, by_channel)
        flat += _flat_ob(ob_out_raw, "out",        period, ob_source, groups, ob_ch_res, by_channel)
        flat += _flat_ob(ob_adj_raw, "adjustment", period, ob_source, groups, ob_ch_res, by_channel)
        cache_key = f"{token[:8]}|{from_date}|{to_date}|{period}|{','.join(map(str, loc_id_list))}|{use_mapping}|{by_channel}"
        state.reconcile_cache = {"key": cache_key, "items": flat}
    except Exception as e:
        errors.append(f"[정보] 드릴다운 캐시 실패: {str(e)[:60]}")

    # ── 조정 제외 옵션 ───────────────────────────────────────────
    # BH 기초재고 설정 등 초기화 adj가 OB와 차이 나는 경우 제외
    if exclude_adj:
        bh_adj = {}
        ob_adj = {}

    # ── 대형 BH adj 임계값 필터 (기초재고 설정 자동 제거) ──────────
    # bh_adj_max_qty > 0 이면: BH adj qty >= 임계값인 SKU를 BH·OB 양측 adj에서 제거
    # (OB adj만 남으면 ob_only 양산되므로 OB도 대칭 제거)
    if bh_adj_max_qty > 0 and bh_adj:
        _large_adj_skus: set = set()
        for (pk, sku_raw), v in bh_adj.items():
            real_sku = sku_raw.split(CH_SEP, 1)[0] if CH_SEP in sku_raw else sku_raw
            if v.get("qty", 0) >= bh_adj_max_qty:
                _large_adj_skus.add(real_sku)
        if _large_adj_skus:
            bh_adj = {
                (pk, sku): v for (pk, sku), v in bh_adj.items()
                if (sku.split(CH_SEP, 1)[0] if CH_SEP in sku else sku) not in _large_adj_skus
            }
            ob_adj = {
                (pk, sku): v for (pk, sku), v in ob_adj.items()
                if (sku.split(CH_SEP, 1)[0] if CH_SEP in sku else sku) not in _large_adj_skus
            }
            errors.append(
                f"[정보] BH adj ≥{bh_adj_max_qty} 기초재고 {len(_large_adj_skus)}개 SKU 양측 제외: "
                + ", ".join(sorted(_large_adj_skus))[:120]
            )

    # ── 분해용 SKU 수량 맵 (total/merge 모드에서 in/out/adj 각각 얼마인지 표시) ──────
    def _build_sku_qty_map(agg_dict: dict, fd: str, td: str) -> dict:
        """(period_key, sku) → qty 집계를 real_sku → qty로 합산 (날짜 범위 필터 + CH_SEP 제거)"""
        result: dict = {}
        for (pk, sku_raw), v in agg_dict.items():
            if len(pk) == 10 and pk[4] == '-' and pk[7] == '-':
                if not (fd <= pk <= td):
                    continue
            # CH_SEP 있으면 실제 SKU만 추출
            real_sku = sku_raw.split(CH_SEP, 1)[0] if CH_SEP in sku_raw else sku_raw
            result[real_sku] = result.get(real_sku, 0) + v.get("qty", 0)
        return result

    bh_in_map   = _build_sku_qty_map(bh_in,   from_date, to_date)
    bh_out_map  = _build_sku_qty_map(bh_out,  from_date, to_date)
    bh_move_map = _build_sku_qty_map(bh_move, from_date, to_date)
    bh_adj_map  = _build_sku_qty_map(bh_adj,  from_date, to_date)
    ob_in_map   = _build_sku_qty_map(ob_in,   from_date, to_date)
    ob_out_map  = _build_sku_qty_map(ob_out,  from_date, to_date)
    ob_adj_map  = _build_sku_qty_map(ob_adj,  from_date, to_date)

    # ── 비교 ─────────────────────────────────────────────────
    if merge_types:
        # 유형합산 모드: 날짜·유형 무관 품목별 순수량(IN-OUT+ADJ) 합산 비교
        # 모든 유형의 BH / OB 데이터를 단일 누적 dict으로 합산
        def _merge_all_types(pos_dict, neg_dict, adj_dict):
            """in(+), out(-), adj(부호 유지) → 품목별 net 수량 dict"""
            from collections import defaultdict as _dd_m
            net: dict = _dd_m(lambda: {"name": "", "qty": 0.0})
            for (pk, sku), v in pos_dict.items():
                k2 = ("ALL", sku)
                net[k2]["name"] = v.get("name", sku)
                net[k2]["qty"] += v.get("qty", 0)
            for (pk, sku), v in neg_dict.items():
                k2 = ("ALL", sku)
                if not net[k2]["name"]:
                    net[k2]["name"] = v.get("name", sku)
                net[k2]["qty"] -= v.get("qty", 0)
            for (pk, sku), v in adj_dict.items():
                k2 = ("ALL", sku)
                if not net[k2]["name"]:
                    net[k2]["name"] = v.get("name", sku)
                net[k2]["qty"] += v.get("qty", 0)
            return dict(net)

        bh_all = _merge_all_types(bh_in, bh_out_plus_move, bh_adj)
        ob_all = _merge_all_types(ob_in, ob_out, ob_adj)
        merged_rows = _compare_total(bh_all, ob_all, from_date, to_date, qty_tolerance=qty_tolerance)
        # 유형합산 행은 tx_type="all"로 표시
        all_rows = [{"tx_type": "adjustment", "merged_type": True, **r} for r in merged_rows]
        in_rows, out_rows, adj_rows = [], [], merged_rows
    elif mode == "cumulative":
        in_rows  = _compare_cumulative(bh_in,  ob_in)
        out_rows = _compare_cumulative(bh_out_plus_move, ob_out)
        adj_rows = _compare_cumulative(bh_adj, ob_adj)
        all_rows = (
            [{"tx_type": "in", **r} for r in in_rows]
            + [{"tx_type": "out", **r} for r in out_rows]
            + [{"tx_type": "adjustment", **r} for r in adj_rows]
        )
    elif mode == "total":
        # 재고 역산 모드: 날짜 무시, 품목별 기간 합산 비교
        # 출고 비교는 out+move 합산으로 OB 매칭, 별도 bh_move_qty 필드로 분리 보고
        in_rows  = _compare_total(bh_in,  ob_in,  from_date, to_date, qty_tolerance=qty_tolerance)
        out_rows = _compare_total(bh_out_plus_move, ob_out, from_date, to_date, qty_tolerance=qty_tolerance)
        adj_rows = _compare_total(bh_adj, ob_adj, from_date, to_date, qty_tolerance=qty_tolerance)
        all_rows = (
            [{"tx_type": "in", **r} for r in in_rows]
            + [{"tx_type": "out", **r} for r in out_rows]
            + [{"tx_type": "adjustment", **r} for r in adj_rows]
        )
    else:
        _lb = bh_lookback if period == "day" else 0  # 주/월 단위는 날짜 오프셋 무시
        in_rows  = _compare(bh_in,  ob_in,  day_lookback=_lb, display_from=from_date, display_to=to_date, qty_tolerance=qty_tolerance)
        out_rows = _compare(bh_out_plus_move, ob_out, day_lookback=_lb, display_from=from_date, display_to=to_date, qty_tolerance=qty_tolerance)
        adj_rows = _compare(bh_adj, ob_adj, day_lookback=_lb, display_from=from_date, display_to=to_date, qty_tolerance=qty_tolerance)
        all_rows = (
            [{"tx_type": "in", **r} for r in in_rows]
            + [{"tx_type": "out", **r} for r in out_rows]
            + [{"tx_type": "adjustment", **r} for r in adj_rows]
        )

    # ── 분해 컬럼 추가 (total/merge 모드) ────────────────────────────────
    # 각 행에 bh/ob별 in/out/adj qty를 붙여 mismatch 원인 파악 용이하게
    if mode in ("total",) or merge_types:
        for r in all_rows:
            sku = r.get("sku", "")
            r["bh_in_qty"]   = bh_in_map.get(sku, 0)
            r["bh_out_qty"]  = bh_out_map.get(sku, 0)
            r["bh_move_qty"] = bh_move_map.get(sku, 0)
            r["bh_adj_qty"]  = bh_adj_map.get(sku, 0)
            r["ob_in_qty"]   = ob_in_map.get(sku, 0)
            r["ob_out_qty"]  = ob_out_map.get(sku, 0)
            r["ob_adj_qty"]  = ob_adj_map.get(sku, 0)
            # mismatch 원인 힌트: adj만 다르고 in/out은 같으면 "adj_diff"
            bh_in_v  = r["bh_in_qty"];  bh_out_v = r["bh_out_qty"]; bh_adj_v = r["bh_adj_qty"]
            ob_in_v  = r["ob_in_qty"];  ob_out_v = r["ob_out_qty"]; ob_adj_v = r["ob_adj_qty"]
            if r["status"] == "mismatch":
                if bh_in_v == ob_in_v and bh_out_v == ob_out_v and bh_adj_v != ob_adj_v:
                    r["mismatch_cause"] = "adj_only"
                elif bh_adj_v == ob_adj_v and (bh_in_v != ob_in_v or bh_out_v != ob_out_v):
                    # 세트 수량 비율 패턴 감지: BH/OB 비율이 정수배인지 확인
                    _bh_q = r.get("bh_qty", 0) or 0
                    _ob_q = r.get("ob_qty", 0) or 0
                    _set_ratio: float = 0.0
                    if _bh_q > 0 and _ob_q > 0:
                        _ratio = max(_bh_q, _ob_q) / min(_bh_q, _ob_q)
                        # 2~20 사이의 정수배이면 세트 비율 의심
                        if 2 <= _ratio <= 20 and abs(_ratio - round(_ratio)) < 0.1:
                            _set_ratio = round(_ratio)
                    if _set_ratio >= 2:
                        r["mismatch_cause"] = "set_ratio"
                        r["set_ratio_hint"] = _set_ratio  # 예: 12 → 12개짜리 세트
                    else:
                        r["mismatch_cause"] = "in_out_diff"
                else:
                    r["mismatch_cause"] = "mixed"
            else:
                r["mismatch_cause"] = ""
                r["set_ratio_hint"] = 0

    # ── 확정 매칭(matched_pairs) 반영 ──────────────────────────────────
    # full-match 확정 결과: matched_pairs.sku = bh norm(정규화 상품명)
    # compare rows의 name 필드를 norm으로 변환해 매칭
    try:
        import receiving_db as _rdb
        _mp_list = _rdb.get_matched_pairs(from_date, to_date)
        if _mp_list:
            def _norm_simple(s: str) -> str:
                import re as _re2
                return _re2.sub(r"[\s\-_·•\[\]()（）]", "", s).lower()

            from datetime import datetime as _dt2
            _DATE_WINDOW = 14  # BH/OB 처리날짜 오차 허용 범위 (일)

            def _to_days(s):
                try:
                    return _dt2.strptime(str(s)[:10], "%Y-%m-%d").toordinal()
                except Exception:
                    return None

            # 이름 기반 인덱스: norm_name → list[mp]
            _mp_bh_name: dict = {}   # norm(bh_name) → list[mp]
            _mp_ob_name: dict = {}   # norm(ob_name or sku) → list[mp]

            for _mp in _mp_list:
                _raw_bh = str(_mp.get("sku", "") or _mp.get("bh_name", "")).strip()
                if _raw_bh:
                    _mp_bh_name.setdefault(_norm_simple(_raw_bh), []).append(_mp)
                _raw_ob = str(_mp.get("ob_name", "") or _mp.get("sku", "")).strip()
                if _raw_ob:
                    _mp_ob_name.setdefault(_norm_simple(_raw_ob), []).append(_mp)

            def _find_best(cands, period, qty, date_field, qty_field, used_ids):
                """날짜 ±window + 수량 가장 가까운 미사용 pair 반환"""
                p_days = _to_days(period)
                scored = []
                for c in cands:
                    if id(c) in used_ids:
                        continue
                    c_days = _to_days(c.get(date_field, ""))
                    if p_days is not None and c_days is not None and abs(p_days - c_days) > _DATE_WINDOW:
                        continue
                    scored.append((abs((c.get(qty_field) or 0) - (qty or 0)), c))
                scored.sort(key=lambda x: x[0])
                return scored[0][1] if scored else None

            # ── tx_type별로 독립 처리 (pair 크로스타입 소비 방지) ─────────
            _rows_by_type: dict = {"in": [], "out": [], "adjustment": []}
            for _r in all_rows:
                _rows_by_type.setdefault(_r.get("tx_type", ""), []).append(_r)

            _rows_merged: list = []
            for _tx, _type_rows in _rows_by_type.items():
                _used_in_type: set = set()
                _type_rows = list(_type_rows)

                # 패스1: bh_only → OB 수량 주입
                for _i, _r in enumerate(_type_rows):
                    if _r.get("status") != "bh_only":
                        continue
                    _period = _r.get("period", "")
                    _norm_name = _norm_simple(str(_r.get("name") or _r.get("sku") or ""))
                    _bh_qty = _r.get("bh_qty") or 0
                    _tol = qty_tolerance or 0.0
                    cands = _mp_bh_name.get(_norm_name, [])
                    _mp_hit = _find_best(cands, _period, _bh_qty, "bh_date", "bh_qty", _used_in_type)
                    if _mp_hit:
                        _used_in_type.add(id(_mp_hit))
                        _new_ob = _mp_hit["ob_qty"]
                        _diff = _bh_qty - _new_ob
                        _big = max(_bh_qty, _new_ob, 1)
                        _new_status = "ok" if (_tol > 0 and abs(_diff) / _big <= _tol) or (_tol == 0 and _diff == 0) else "mismatch"
                        _type_rows[_i] = {**_r, "ob_qty": _new_ob, "status": _new_status, "matched_confirmed": True}

                # 패스2: ob_only → BH 수량 주입
                _type_final: list = []
                for _r in _type_rows:
                    if _r.get("status") == "ob_only" and not _r.get("matched_confirmed"):
                        _period = _r.get("period", "")
                        _norm_name = _norm_simple(str(_r.get("name") or _r.get("sku") or ""))
                        _ob_qty = _r.get("ob_qty") or 0
                        _tol = qty_tolerance or 0.0
                        cands2 = _mp_ob_name.get(_norm_name, [])
                        _mp_ob_hit = _find_best(cands2, _period, _ob_qty, "ob_date", "ob_qty", _used_in_type)
                        if _mp_ob_hit:
                            _used_in_type.add(id(_mp_ob_hit))
                            _new_bh = _mp_ob_hit["bh_qty"]
                            _diff = _new_bh - _ob_qty
                            _big = max(_new_bh, _ob_qty, 1)
                            _new_status = "ok" if (_tol > 0 and abs(_diff) / _big <= _tol) or (_tol == 0 and _diff == 0) else "mismatch"
                            _type_final.append({**_r, "bh_qty": _new_bh, "status": _new_status, "matched_confirmed": True})
                            continue
                    _type_final.append(_r)
                _rows_merged.extend(_type_final)

            # ── 쌍 이름 정규화: 행은 매핑 그룹 대표명으로 합산되므로 쌍 이름도 동일 변환 ──
            # 예: "메노포즈-2028-03-08", "홈쇼핑(공통)_메노포즈 12개입" → 그룹 대표명
            try:
                _gg_mp = _build_mapping_groups()
                _bn2g_mp = _gg_mp.get("bh_name_to_group", {}) or {}
                _on2g_mp = _gg_mp.get("ob_to_group", {}) or {}
                _glab_mp = _gg_mp.get("group_label", {}) or {}
            except Exception:
                _bn2g_mp, _on2g_mp, _glab_mp = {}, {}, {}
            import re as _re_canon

            def _canon_nm(nm: str) -> str:
                nm = str(nm or "").strip()
                if not nm:
                    return nm
                g = _bn2g_mp.get(nm) or _on2g_mp.get(nm)
                if not g:
                    nm2 = _re_canon.sub(r'-\d{4}-\d{2}-\d{2}$', '', nm).strip()
                    if nm2 != nm:
                        g = _bn2g_mp.get(nm2) or _on2g_mp.get(nm2)
                return str(_glab_mp.get(g, nm)) if g else nm

            # ── 패스2.5: 날짜별 상쇄 — full-match 확정쌍의 날짜 어긋남 교통정리 ──
            # BH 행(날짜 D)은 pair.bh_date==D 의 bh_qty 합으로, OB 행은 pair.ob_date==D 의
            # ob_qty 합으로 소진 평가. 양쪽 잔여가 같으면 매칭으로 설명된 것 → ok 승격.
            # 예: BH 4/17 출고 98 ↔ OB 4/19 출고 98 확정 → 4/17 행(BH만)·4/19 행(OB만) 모두 ok
            try:
                _pb_by_key: dict = {}
                _po_by_key: dict = {}
                for _mp in _mp_list:
                    _nm_keys = {_norm_simple(_canon_nm(str(_mp.get("sku") or _mp.get("bh_name") or ""))),
                                _norm_simple(_canon_nm(str(_mp.get("ob_name") or "")))}
                    _nm_keys.discard("")
                    _bd = str(_mp.get("bh_date") or "")[:10]
                    _od = str(_mp.get("ob_date") or "")[:10]
                    for _nk in _nm_keys:
                        if _bd:
                            _pb_by_key[(_nk, _bd)] = _pb_by_key.get((_nk, _bd), 0) + (_mp.get("bh_qty") or 0)
                        if _od:
                            _po_by_key[(_nk, _od)] = _po_by_key.get((_nk, _od), 0) + (_mp.get("ob_qty") or 0)
                for _r in _rows_merged:
                    if _r.get("status") == "ok" or _r.get("matched_confirmed"):
                        continue
                    _nk = _norm_simple(_canon_nm(str(_r.get("name") or _r.get("sku") or "")))
                    _pd = str(_r.get("period") or "")[:10]
                    _mb = _pb_by_key.get((_nk, _pd), 0)
                    _mo = _po_by_key.get((_nk, _pd), 0)
                    if not _mb and not _mo:
                        continue
                    _rb = (_r.get("bh_qty") or 0) - _mb   # BH 잔여 (이 날짜에서 매칭쌍으로 소진 후)
                    _ro = (_r.get("ob_qty") or 0) - _mo   # OB 잔여
                    _tol25 = qty_tolerance or 0.0
                    _base25 = max((_r.get("bh_qty") or 0), (_r.get("ob_qty") or 0), 1)
                    if (_tol25 > 0 and abs(_rb - _ro) / _base25 <= _tol25) or (_tol25 == 0 and _rb == _ro):
                        _r["status"] = "ok"
                        _r["matched_confirmed"] = True
            except Exception:
                pass

            # ── 패스3 (total/merge 모드): 품목 단위 전체 상쇄 — 완전한 교통정리 ──
            # full-match 확정 쌍이 유형 크로스(BH출고↔OB조정 등)여도 품목 전체로 보면
            # BH총합-OB총합 차이가 매칭쌍 qty_diff 합으로 설명되는 경우 → 전부 ok 승격
            if mode == "total" or merge_types:
                from collections import defaultdict as _dd_mp
                _pair_by_key: dict = {}  # norm key → set of pair ids (양쪽 이름 인덱싱)
                _pair_store: dict = {}   # id → pair
                for _mp in _mp_list:
                    _pid = id(_mp)
                    _pair_store[_pid] = _mp
                    for _nm_key in (str(_mp.get("sku") or _mp.get("bh_name") or ""),
                                    str(_mp.get("ob_name") or "")):
                        _nk = _norm_simple(_canon_nm(_nm_key))
                        if _nk:
                            _pair_by_key.setdefault(_nk, set()).add(_pid)
                _rows_by_item: dict = _dd_mp(list)
                for _r in _rows_merged:
                    _rows_by_item[_norm_simple(_canon_nm(str(_r.get("name") or _r.get("sku") or "")))].append(_r)
                def _in_win(_dt: str) -> bool:
                    # total 모드 합산 윈도우(±3일)와 동일하게 평가
                    from datetime import datetime as _dtw, timedelta as _tdw
                    _s = str(_dt or "")[:10]
                    if not _s:
                        return False
                    try:
                        _wf = (_dtw.strptime(from_date, "%Y-%m-%d") - _tdw(days=3)).strftime("%Y-%m-%d")
                        _wt = (_dtw.strptime(to_date, "%Y-%m-%d") + _tdw(days=3)).strftime("%Y-%m-%d")
                    except Exception:
                        _wf, _wt = from_date, to_date
                    return _wf <= _s <= _wt
                for _nitem, _rlist in _rows_by_item.items():
                    if not _nitem or all(_r.get("status") == "ok" for _r in _rlist):
                        continue
                    _pids = _pair_by_key.get(_nitem)
                    if not _pids:
                        continue
                    # 행 합산은 조회 기간 내 수량만 포함하므로, 쌍의 수량도
                    # 해당 쪽 날짜가 기간 내일 때만 차감 (BH 3/31 ↔ OB 4/1 크로스 케이스)
                    _pb = sum((_pair_store[_p].get("bh_qty") or 0) for _p in _pids
                              if _in_win(_pair_store[_p].get("bh_date")))
                    _po = sum((_pair_store[_p].get("ob_qty") or 0) for _p in _pids
                              if _in_win(_pair_store[_p].get("ob_date")))
                    _bh_sum = sum((_r.get("bh_qty") or 0) for _r in _rlist)
                    _ob_sum = sum((_r.get("ob_qty") or 0) for _r in _rlist)
                    # 잔여 차이 = (행 차이) - (매칭쌍 차이): 0이면 매칭으로 완전 설명됨
                    _resid = (_bh_sum - _ob_sum) - (_pb - _po)
                    _tol3 = qty_tolerance or 0.0
                    _base3 = max(_bh_sum, _ob_sum, 1)
                    if (_tol3 > 0 and abs(_resid) / _base3 <= _tol3) or (_tol3 == 0 and _resid == 0):
                        for _r in _rlist:
                            if _r.get("status") != "ok":
                                _r["status"] = "ok"
                                _r["matched_confirmed"] = True

            all_rows = _rows_merged
            in_rows   = [_r for _r in all_rows if _r.get("tx_type") == "in"]
            out_rows  = [_r for _r in all_rows if _r.get("tx_type") == "out"]
            # (부자재 제외는 아래 공통 필터에서 처리)
            adj_rows  = [_r for _r in all_rows if _r.get("tx_type") == "adjustment"]
    except Exception as _e:
        errors.append(f"[정보] 확정 매칭 반영 실패: {str(_e)[:80]}")

    # ── mismatch_cause 누락 행 보완 (matched_pairs 처리 후 추가된 행) ─────
    # matched_pairs로 status가 ob_only→mismatch로 바뀐 경우 mismatch_cause 재할당
    if mode in ("total",) or merge_types:
        for r in all_rows:
            if r.get("status") == "mismatch" and not r.get("mismatch_cause"):
                _bh_q2 = r.get("bh_qty", 0) or 0
                _ob_q2 = r.get("ob_qty", 0) or 0
                _big2 = max(_bh_q2, _ob_q2)
                _set_r2: float = 0.0
                if _bh_q2 > 0 and _ob_q2 > 0:
                    _ratio2 = _big2 / min(_bh_q2, _ob_q2)
                    if 2 <= _ratio2 <= 20 and abs(_ratio2 - round(_ratio2)) < 0.1:
                        _set_r2 = round(_ratio2)
                if _set_r2 >= 2:
                    r["mismatch_cause"] = "set_ratio"
                    r["set_ratio_hint"] = _set_r2
                else:
                    r["mismatch_cause"] = "in_out_diff"

    # ── 부자재/포장재 행 제외 — OB 미관리 품목 (별도 목록으로 보고) ──
    _MAT_KW = ["기타부자재", "부자재", "포장재", "박스", "테이프", "택배비", "단상자", "(원료)"]
    def _is_material_row(r: dict) -> bool:
        nm = str(r.get("name") or "")
        return any(k in nm for k in _MAT_KW)
    excluded_material = [r for r in all_rows if _is_material_row(r)]
    if excluded_material:
        all_rows = [r for r in all_rows if not _is_material_row(r)]
        in_rows  = [r for r in in_rows  if not _is_material_row(r)]
        out_rows = [r for r in out_rows if not _is_material_row(r)]
        adj_rows = [r for r in adj_rows if not _is_material_row(r)]

    # ── 원인 자동 분류 (각 행에 root_cause/fix_target/fix_hint 부여) ──
    try:
        root_cause_summary = _classify_root_causes(all_rows, groups, period, by_channel)
    except Exception as e:
        root_cause_summary = {}
        errors.append(f"[정보] 원인 분류 실패: {str(e)[:60]}")

    # ── 저장된 정리(전산정리) 상태 조인 ──────────────────────────────
    # 각 행에 cleanup_status/cleanup_memo/cleanup_assignee/cleanup_updated_at 부착.
    # hide_resolved=True면 resolved/ignore 행은 결과에서 제외.
    cleanup_counts: dict = {}
    try:
        import receiving_db as _rdb_cs
        # 전체 상태 로드 후 정확 키 매칭 (week/month/total 등 period 키 형식 차이 무관)
        _cs_list = _rdb_cs.get_reconcile_statuses()
        _cs_by_key = {c["row_key"]: c for c in _cs_list}
        from collections import Counter as _Counter
        _joined_statuses: list = []
        if _cs_by_key:
            for r in all_rows:
                _k = _recon_row_key(r.get("tx_type", ""), r.get("sku", ""),
                                    r.get("channel", ""), r.get("period", ""))
                _c = _cs_by_key.get(_k)
                if _c:
                    r["cleanup_status"] = _c.get("status", "")
                    r["cleanup_memo"] = _c.get("memo", "")
                    r["cleanup_assignee"] = _c.get("assignee", "")
                    r["cleanup_updated_at"] = _c.get("updated_at", "")
                    _joined_statuses.append(_c.get("status", ""))
        # 현재 비교 결과에 실제로 매칭된 행만 집계 (이 화면의 정리 진행도)
        cleanup_counts = dict(_Counter(_joined_statuses))
        if hide_resolved and _cs_by_key:
            # cleanup_status는 all_rows에만 부착됨 → all_rows 필터 후 tx_type별 재구성
            _hidden = {"resolved", "ignore"}
            all_rows = [r for r in all_rows if r.get("cleanup_status") not in _hidden]
            in_rows  = [r for r in all_rows if r.get("tx_type") == "in"]
            out_rows = [r for r in all_rows if r.get("tx_type") == "out"]
            adj_rows = [r for r in all_rows if r.get("tx_type") == "adjustment"]
    except Exception as e:
        errors.append(f"[정보] 정리 상태 조인 실패: {str(e)[:60]}")

    def _summarize(rows):
        s = {"total": len(rows), "ok": 0, "mismatch": 0, "bh_only": 0, "ob_only": 0}
        for r in rows:
            s[r["status"]] = s.get(r["status"], 0) + 1
        return s

    # ── OB ADJ 식별불가 건 수집 ────────────────────────────────────
    # product_code/prod_cd/sale_prod_cd 없고, item_cd → 상품명 변환도 안 된 건
    ob_adj_unknown: list = []
    try:
        for _rec in ob_adj_raw:
            _has_code = bool(
                _rec.get("product_code") or _rec.get("prod_cd") or _rec.get("sale_prod_cd")
            )
            _has_name = bool(_rec.get("product_name") or _rec.get("prod_nm"))
            if not _has_code and not _has_name:
                _icd = str(_rec.get("item_cd") or "").strip()
                _qty = int(float(str(_rec.get("adj_qty") or 0)))
                _dt  = str(_rec.get("reg_dtm") or _rec.get("reg_dt") or "")[:10]
                _rsn = str(_rec.get("stock_adj_resn_nm") or "")
                ob_adj_unknown.append({
                    "date": _dt,
                    "qty": _qty,
                    "item_cd": _icd,
                    "reason": _rsn,
                })
    except Exception:
        pass

    # 데이터 수집 건수 현황 (항목 6 — 전산 전부 확인용)
    data_counts = {
        "bh": {
            "in":  len(bh_in_raw),
            "out": len(bh_out_raw),
            "move": len(bh_move_raw),
            "adj": len(bh_adj_raw),
        },
        "ob": {
            "in":  _ob_in_raw_cnt,
            "out": _ob_out_raw_cnt,
            "adj": _ob_adj_raw_cnt,
        },
    }

    return {
        "summary": {
            "in": _summarize(in_rows),
            "out": _summarize(out_rows),
            "adjustment": _summarize(adj_rows),
            "total": _summarize(all_rows),
        },
        "rows": all_rows,
        "has_ourbox": has_ourbox,
        "errors": errors,
        "period": period,
        "from_date": from_date,
        "to_date": to_date,
        "mapping_applied": mapping_applied,
        "filtered_locations": loc_id_list,
        "mode": mode,
        "by_channel": by_channel,
        "qty_tolerance": qty_tolerance,
        "data_counts": data_counts,
        "ob_adj_unknown": ob_adj_unknown,
        # 원인 자동 분류 요약 {root_cause: {count, label}} — 행별 root_cause/fix_target/fix_hint도 rows에 포함
        "root_cause_summary": root_cause_summary,
        # 정리(전산정리) 상태 집계 {status: count} — 행별 cleanup_status/cleanup_memo도 rows에 포함
        "cleanup_counts": cleanup_counts,
        # 부자재/포장재 — OB 미관리 품목으로 비교에서 제외된 행 (별도 표시용)
        "excluded_material": excluded_material,
        # 미매핑 상품 목록: 매핑에 없는 bh_only/ob_only SKU + 상품명 (항목 1)
        "unmapped_products": _build_unmapped(all_rows, groups),
    }


@router.get("/smart-compare")
def smart_compare(
    token: str = Query(...),
    from_date: str = Query(...),
    to_date: str = Query(...),
    location_ids: Optional[str] = Query(None),
    use_mapping: bool = Query(True),
    qty_tolerance: float = Query(0.10),   # 수량 허용오차 (기본 10%)
    bh_lookback: int = Query(30),          # BH 날짜 확장 ±N일 (OB 기준으로 BH를 넓게 탐색)
    name_threshold: float = Query(0.65),   # 이름 유사도 최소값 — 매핑된 SKU끼리는 적용 안 함
):
    """
    스마트 매칭 — OB는 지정 날짜, BH는 넓은 범위에서 1:1 건별 자동 매칭.

    매칭 우선순위:
      [SKU 매칭] 매핑으로 같은 그룹이 된 SKU → 이름 비교 없이 즉시 매칭 (수량만 확인)
      [이름 매칭] 매핑 없는 것 → 이름 유사도로 매칭

    3단계 그리디 (각 단계에서 위 우선순위 동일 적용):
      Pass 1: 유형 일치 + 거래처 일치  → grade=1 (완전)
      Pass 2: 유형 일치 (거래처 무관) → grade=2 (거래처 차이)
      Pass 3: 유형 무관               → grade=3 (유형 차이)
      나머지 → bh_only / ob_only
    """
    from rapidfuzz import fuzz as _rfuzz

    errors: list = []

    # ── location_ids 파싱 ─────────────────────────────────────────
    loc_id_list: list = []
    if location_ids:
        for v in location_ids.split(","):
            v = v.strip()
            if v.isdigit():
                loc_id_list.append(int(v))

    cfg = U.load_config()
    ourbox_id = cfg.get("ourbox_id")
    ourbox_pw = cfg.get("ourbox_pw")
    has_ourbox = bool(ourbox_id and ourbox_pw)

    # ── BH 데이터 수집 ────────────────────────────────────────────
    bh_from = (datetime.strptime(from_date, "%Y-%m-%d") - timedelta(days=bh_lookback)).strftime("%Y-%m-%d")
    bh_to   = (datetime.strptime(to_date,   "%Y-%m-%d") + timedelta(days=bh_lookback)).strftime("%Y-%m-%d")
    bh_in_raw, bh_out_raw, bh_adj_raw = [], [], []
    for tx_type in ("in", "out"):
        try:
            if loc_id_list:
                combined = []
                for lid in loc_id_list:
                    combined.extend(U.fetch_transactions(token, tx_type, bh_from, bh_to, lid))
                txs = combined
            else:
                txs = U.fetch_transactions(token, tx_type, bh_from, bh_to, None)
            if tx_type == "in":
                bh_in_raw = txs
            else:
                bh_out_raw = txs
        except Exception as e:
            errors.append(f"BoxHero {tx_type} 조회 실패: {str(e)[:80]}")
    try:
        bh_adj_raw = U.fetch_transactions(token, "adjust", bh_from, bh_to, None)
    except Exception as e:
        errors.append(f"BoxHero adjust 조회 실패: {str(e)[:80]}")
    try:
        _enrich_bh_items(token, bh_in_raw)
        _enrich_bh_items(token, bh_out_raw)
        _enrich_bh_items(token, bh_adj_raw, tx_type="adjust")
    except Exception as e:
        errors.append(f"BoxHero 상세 조회 실패: {str(e)[:80]}")

    # ── OB 데이터 수집 ────────────────────────────────────────────
    ob_in_raw, ob_out_raw, ob_adj_raw = [], [], []
    ob_source = "none"
    if has_ourbox:
        ob_ext_from = (datetime.strptime(from_date, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
        ob_ext_to   = (datetime.strptime(to_date,   "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")
        ob_cache_key = f"{ob_ext_from}|{ob_ext_to}"
        _load_ob_file_cache()
        ob_cached = _get_ob_cache(ob_cache_key)
        if ob_cached:
            ob_in_raw.extend(ob_cached["in"])
            ob_out_raw.extend(ob_cached["out"])
            ob_adj_raw.extend(ob_cached["adj"])
            ob_source = ob_cached.get("source", "rest")
            errors.append(f"[캐시] OB 파일 캐시 사용 ({ob_ext_from}~{ob_ext_to})")
        else:
            ob_source = _collect_ourbox(
                ourbox_id, ourbox_pw, ob_ext_from, ob_ext_to,
                ob_in_raw, ob_out_raw, ob_adj_raw, errors,
            )
            if ob_source != "failed":
                try:
                    _save_ob_file_cache_entry(ob_cache_key, ob_in_raw, ob_out_raw, ob_adj_raw, ob_source)
                except Exception:
                    pass
    if DEPRECATED_OB_CODES:
        ob_in_raw  = _filter_deprecated(ob_in_raw)
        ob_out_raw = _filter_deprecated(ob_out_raw)
    # 기초재고 일괄입고 제거 (OurBox 도입 초기 재고등록 — BH 미대응)
    ob_in_raw = _filter_initial_stock(ob_in_raw)
    if ob_source == "rest":
        ob_in_raw, ob_out_raw, ob_adj_raw, n_asm = _route_ob_assembly(ob_in_raw, ob_out_raw, ob_adj_raw)
        if n_asm:
            errors.append(f"[정보] 세트조립 {n_asm}건 처리")

    # ── 채널 매핑 로드 (거래처 정규화용) ─────────────────────────
    ob_ch_res, bh_ch_res, _ = _build_channel_resolvers()

    # ── 정규화·집계 ───────────────────────────────────────────────
    period = "day"
    bh_adj_raw_neg = _filter_bh_adj_negative(bh_adj_raw)
    bh_in  = _normalize_bh_txs(bh_in_raw,      "in",     period, bh_ch_res)
    bh_out = _normalize_bh_txs(bh_out_raw,     "out",    period, bh_ch_res)
    bh_adj = _normalize_bh_txs(bh_adj_raw_neg, "adjust", period, bh_ch_res)
    ob_in  = _norm_ob_auto(ob_in_raw,  period, ob_source, "in",         ob_ch_res)
    ob_out = _norm_ob_auto(ob_out_raw, period, ob_source, "out",        ob_ch_res)
    ob_adj = _norm_ob_auto(ob_adj_raw, period, ob_source, "adjustment", ob_ch_res)

    # ── 상품 매핑 적용 ────────────────────────────────────────────
    groups: dict = {}
    if use_mapping:
        groups = _build_mapping_groups()
        bh_in,  ob_in,  _ = _apply_group_mapping(bh_in,  ob_in,  groups)
        bh_out, ob_out, _ = _apply_group_mapping(bh_out, ob_out, groups)
        bh_adj, ob_adj, _ = _apply_group_mapping(bh_adj, ob_adj, groups)

    # ── 평탄화: 집계 dict → 개별 아이템 리스트 ─────────────────────
    def _to_flat_items(agg_dict: dict, date_from: str, date_to: str, tx_type_label: str) -> list:
        """(period_key, sku) 집계 dict → flat item 리스트.
        date_from/date_to: 이 범위 밖 날짜 키는 제외 (날짜 키가 아닌 ALL 등은 항상 포함).
        """
        items = []
        for (pk, sku), v in agg_dict.items():
            real_date = pk if (len(pk) == 10 and pk[4] == '-') else from_date
            # 날짜 필터 적용
            if len(real_date) == 10 and real_date[4] == '-':
                if not (date_from <= real_date <= date_to):
                    continue
            qty = v.get("qty", 0)
            if qty == 0:
                continue
            name = v.get("name", sku)
            real_sku = sku.split(CH_SEP, 1)[0] if CH_SEP in sku else sku
            channel_part = sku.split(CH_SEP, 1)[1] if CH_SEP in sku else ""
            items.append({
                "date": real_date,
                "name": name,
                "norm": U.normalize(name),
                "qty": qty,
                "tx_type": tx_type_label,
                "sku": real_sku,          # 매핑 적용 후 그룹 canonical SKU
                "partner_raw": channel_part,
            })
        return items

    # OB: 지정 날짜 범위만 (사용자가 확인하고 싶은 기간)
    # BH: 넓은 범위 (bh_from ~ bh_to) — OB 기준으로 대응 BH 거래를 넓게 탐색
    bh_items_in  = _to_flat_items(bh_in,  bh_from, bh_to, "in")
    bh_items_out = _to_flat_items(bh_out, bh_from, bh_to, "out")
    bh_items_adj = _to_flat_items(bh_adj, bh_from, bh_to, "adjustment")
    ob_items_in  = _to_flat_items(ob_in,  from_date, to_date, "in")
    ob_items_out = _to_flat_items(ob_out, from_date, to_date, "out")
    ob_items_adj = _to_flat_items(ob_adj, from_date, to_date, "adjustment")

    # 원본 raw에서 거래처 정보를 보강 (bh: partner, ob: channel/mall_name)
    def _enrich_bh_partner(items: list, raw_txs: list):
        """BH flat items에 partner 이름 추가 (raw_txs에서 추출)"""
        # name → partner 매핑 (간단화: 마지막 raw tx의 partner 사용)
        name_to_partner: dict = {}
        for tx in raw_txs:
            p = tx.get("partner") or {}
            pname = p.get("name", "") if isinstance(p, dict) else ""
            for it in tx.get("items", []):
                nm = str(it.get("name", "")).strip()
                if nm and pname:
                    name_to_partner[nm] = pname
        for item in items:
            if not item["partner_raw"]:
                item["partner_raw"] = name_to_partner.get(item["name"], "")

    def _enrich_ob_channel(items: list, raw_txs: list):
        """OB flat items에 channel 정보 추가 (raw_txs에서 추출)"""
        name_to_channel: dict = {}
        for rec in raw_txs:
            ch = str(rec.get("channel") or rec.get("mall_name") or "").strip()
            purch = str(rec.get("purch_company") or "").strip()
            nm_raw = (
                str(rec.get("prod_nm") or rec.get("product_name") or rec.get("sale_prod_nm") or "")
                .strip()
            )
            if nm_raw:
                name_to_channel[nm_raw] = ch or purch
        for item in items:
            if not item["partner_raw"]:
                item["partner_raw"] = name_to_channel.get(item["name"], "")

    _enrich_bh_partner(bh_items_in,  bh_in_raw)
    _enrich_bh_partner(bh_items_out, bh_out_raw)
    _enrich_bh_partner(bh_items_adj, bh_adj_raw)
    _enrich_ob_channel(ob_items_in,  ob_in_raw)
    _enrich_ob_channel(ob_items_out, ob_out_raw)
    _enrich_ob_channel(ob_items_adj, ob_adj_raw)

    # 거래처 정규화 (채널 매핑 적용)
    def _norm_partner_label(raw: str, is_bh: bool) -> str:
        if not raw:
            return ""
        if is_bh and bh_ch_res:
            return bh_ch_res(raw) or U.normalize(raw)
        if not is_bh and ob_ch_res:
            return ob_ch_res(raw) or U.normalize(raw)
        return U.normalize(raw)

    for it in bh_items_in + bh_items_out + bh_items_adj:
        it["partner_norm"] = _norm_partner_label(it["partner_raw"], is_bh=True)
    for it in ob_items_in + ob_items_out + ob_items_adj:
        it["partner_norm"] = _norm_partner_label(it["partner_raw"], is_bh=False)

    # ── 3단계 그리디 매칭 ─────────────────────────────────────────
    def _partner_ok(b: dict, o: dict) -> bool:
        """거래처 일치 여부 (한쪽 비어있으면 통과)"""
        bp = b.get("partner_norm", "")
        op = o.get("partner_norm", "")
        if not bp or not op:
            return True   # 정보 없으면 필터 불가
        return bp == op

    def _greedy_match(
        bh_list: list, ob_list: list,
        require_type: bool, require_partner: bool,
    ) -> tuple:
        """greedy 1:1 매칭. (matched_pairs, bh_remaining, ob_remaining) 반환.

        매칭 우선순위:
          1. 매핑 SKU 동일 (sku 같음) → 이름 비교 없이 즉시 매칭, 수량만 확인, score=2.0+
          2. 매핑 없는 것 → 이름 유사도 ≥ name_threshold + 수량 허용오차 이내
        """
        candidates: list = []
        for bi, b in enumerate(bh_list):
            for oi, o in enumerate(ob_list):
                # 유형 필터
                if require_type and b["tx_type"] != o["tx_type"]:
                    continue
                # 거래처 필터
                if require_partner and not _partner_ok(b, o):
                    continue

                max_q = max(b["qty"], o["qty"], 1)
                qty_ratio = abs(b["qty"] - o["qty"]) / max_q
                qty_sim = 1.0 - qty_ratio

                # ── 1순위: 매핑된 SKU 동일 ──────────────────────────
                b_sku = b.get("sku", "")
                o_sku = o.get("sku", "")
                if b_sku and o_sku and b_sku == o_sku:
                    # 수량 허용오차 2배 적용 (같은 상품이 확실하므로 관대하게)
                    if qty_ratio > qty_tolerance * 2:
                        continue
                    # 스코어 2.0 기반 (이름 매칭 최고점 ~1.35 보다 항상 높음)
                    score = 2.0 + qty_sim * 0.5
                    candidates.append((score, bi, oi))
                    continue

                # ── 2순위: 이름 유사도 (매핑 없는 것) ────────────────
                nsim = _rfuzz.token_set_ratio(b["norm"], o["norm"]) / 100.0
                if nsim < name_threshold:
                    continue
                if qty_ratio > qty_tolerance:
                    continue
                score = nsim * 0.65 + qty_sim * 0.35
                candidates.append((score, bi, oi))

        # 스코어 내림차순 greedy 1:1 배정
        candidates.sort(key=lambda x: -x[0])
        used_b: set = set()
        used_o: set = set()
        pairs: list = []
        for score, bi, oi in candidates:
            if bi in used_b or oi in used_o:
                continue
            used_b.add(bi)
            used_o.add(oi)
            pairs.append((bh_list[bi], ob_list[oi], score))
        bh_rem = [b for bi, b in enumerate(bh_list) if bi not in used_b]
        ob_rem = [o for oi, o in enumerate(ob_list) if oi not in used_o]
        return pairs, bh_rem, ob_rem

    all_matched: list = []
    all_bh_only: list = []
    all_ob_only: list = []

    bh_all = bh_items_in + bh_items_out + bh_items_adj
    ob_all = ob_items_in + ob_items_out + ob_items_adj

    # Pass 1: 완전 매칭 (유형 + 거래처)
    p1, bh_rem, ob_rem = _greedy_match(bh_all, ob_all, require_type=True, require_partner=True)
    for b, o, sc in p1:
        all_matched.append(_make_match_row(b, o, sc, grade=1))

    # Pass 2: 거래처 차이 (유형만)
    p2, bh_rem, ob_rem = _greedy_match(bh_rem, ob_rem, require_type=True, require_partner=False)
    for b, o, sc in p2:
        all_matched.append(_make_match_row(b, o, sc, grade=2))

    # Pass 3: 유형 차이 (품목+수량만)
    p3, bh_rem, ob_rem = _greedy_match(bh_rem, ob_rem, require_type=False, require_partner=False)
    for b, o, sc in p3:
        all_matched.append(_make_match_row(b, o, sc, grade=3))

    all_bh_only = [
        {"date": b["date"], "name": b["name"], "qty": b["qty"],
         "tx_type": b["tx_type"], "partner": b.get("partner_raw", ""), "sku": b["sku"]}
        for b in bh_rem
    ]
    all_ob_only = [
        {"date": o["date"], "name": o["name"], "qty": o["qty"],
         "tx_type": o["tx_type"], "channel": o.get("partner_raw", ""), "sku": o["sku"]}
        for o in ob_rem
    ]

    total_bh = len(bh_all)
    total_ob = len(ob_all)
    n_matched = len(all_matched)
    grade1 = sum(1 for m in all_matched if m["match_grade"] == 1)
    grade2 = sum(1 for m in all_matched if m["match_grade"] == 2)
    grade3 = sum(1 for m in all_matched if m["match_grade"] == 3)

    return {
        "matched": sorted(all_matched, key=lambda x: -x["score"]),
        "bh_only": sorted(all_bh_only, key=lambda x: (-x["qty"], x["name"])),
        "ob_only": sorted(all_ob_only, key=lambda x: (-x["qty"], x["name"])),
        "summary": {
            "total_bh": total_bh,
            "total_ob": total_ob,
            "matched": n_matched,
            "bh_only": len(all_bh_only),
            "ob_only": len(all_ob_only),
            "grade1": grade1,
            "grade2": grade2,
            "grade3": grade3,
            "match_rate_bh": round(n_matched / total_bh, 3) if total_bh else 0,
            "match_rate_ob": round(n_matched / total_ob, 3) if total_ob else 0,
        },
        "errors": errors,
    }


def _make_match_row(b: dict, o: dict, score: float, grade: int) -> dict:
    """매칭 쌍 → 응답 row 딕셔너리"""
    try:
        from datetime import datetime as _dt
        d1 = _dt.strptime(b["date"][:10], "%Y-%m-%d").toordinal()
        d2 = _dt.strptime(o["date"][:10], "%Y-%m-%d").toordinal()
        date_gap = abs(d1 - d2)
    except Exception:
        date_gap = -1
    return {
        "match_grade": grade,
        "score": round(score, 3),
        "bh_date": b["date"],
        "bh_name": b["name"],
        "bh_qty": b["qty"],
        "bh_type": b["tx_type"],
        "bh_partner": b.get("partner_raw", ""),
        "bh_sku": b.get("sku", ""),
        "ob_date": o["date"],
        "ob_name": o["name"],
        "ob_qty": o["qty"],
        "ob_type": o["tx_type"],
        "ob_channel": o.get("partner_raw", ""),
        "ob_sku": o.get("sku", ""),
        "qty_diff": b["qty"] - o["qty"],
        "date_gap": date_gap,
    }


@router.get("/product-match")
def product_match(
    token: str = Query(...),
    from_date: str = Query(...),
    to_date: str = Query(...),
    tx_types: str = Query("in,out,adjustment"),  # 콤마구분: in,out,adjustment
    tolerance_days: int = Query(4),
    min_score: int = Query(45),                  # 낮춰서 힌트 없이도 매칭 가능
    exclude_channels: str = Query("샘플(임박),샘플(정상소비기한)"),  # OB 제외 채널
    aggregate: bool = Query(True),               # 출고를 (상품,날짜) 단위 합산 비교
    wide_mode: bool = Query(True),               # 기본: BH ±lookback일 전체 탐색
    bh_lookback: int = Query(30),                # BH 조회 앞뒤 확장 일수
    channel_required: bool = Query(False),       # True면 거래처/채널 불일치 시 페널티 (SKU 일치는 면제)
):
    """입고·출고·조정·이동 품목별 유사도 매칭 (이름 40% + 수량 40% + 날짜 20%).

    wide_mode=True: OB는 지정 기간만, BH는 ±lookback일 전체를 탐색해 대응 건 탐색.
    → OB 5/26 입력 건을 BH 5/28 또는 6/1 입력과도 매칭 가능.
    이름·수량이 일치하면 날짜가 달라도 99%+ 달성 가능.
    """
    import ourbox_api as api_mod, re as _re
    from rapidfuzz import fuzz as _fuzz
    from collections import defaultdict as _dd

    cfg = U.load_config()
    types = [t.strip() for t in tx_types.split(",") if t.strip()]

    # ── 상품·채널 매핑 ────────────────────────────────────────────
    try:
        import receiving_db as _db
        nm_pairs = _db.get_name_mapping_pairs()
        ob_to_norm: dict = {m["ob_name"]: U.normalize(m.get("bh_name") or m["ob_name"]) for m in nm_pairs}
        # product_mapping: OB prod_cd → BH SKU (코드 직접 매칭) + 이름 → SKU 보조
        try:
            pm_pairs = _db.get_product_mapping_pairs()
        except Exception:
            pm_pairs = []
        ob_code_to_sku: dict = {p["ob_prod_cd"]: p["bh_sku"] for p in pm_pairs if p.get("ob_prod_cd") and p.get("bh_sku")}
        ob_name_to_sku: dict = {p["ob_name"]: p["bh_sku"] for p in pm_pairs if p.get("ob_name") and p.get("bh_sku")}
        for m in nm_pairs:
            if m.get("ob_name") and m.get("bh_sku"):
                ob_name_to_sku.setdefault(m["ob_name"], m["bh_sku"])
        # BH 이름 → SKU (미등록 SKU 아이템이 BH 이름으로 매핑될 수 있도록)
        bh_name_to_sku: dict = {p["bh_name"]: p["bh_sku"] for p in pm_pairs if p.get("bh_name") and p.get("bh_sku")}
        ch_pairs = _db.get_channel_mapping_pairs()
        ob_ch_to_lbl: dict = {}
        bh_kw_to_lbl: dict = {}
        from collections import defaultdict as _dd2
        ch_groups: dict = _dd2(lambda: {"ob": set(), "bh": set()})
        for p in ch_pairs:
            lbl = p["ob_channel"]
            ch_groups[lbl]["ob"].add(p["ob_channel"])
            ch_groups[lbl]["bh"].add(p["bh_keyword"])
        for lbl, g in ch_groups.items():
            for ob in g["ob"]: ob_ch_to_lbl[ob] = lbl
            for kw in g["bh"]: bh_kw_to_lbl[kw] = lbl
    except Exception:
        ob_to_norm = {}; ob_ch_to_lbl = {}; bh_kw_to_lbl = {}
        ob_code_to_sku = {}; ob_name_to_sku = {}

    def _ob_code(rec: dict) -> str:
        """OB 레코드에서 상품코드 추출 (_norm_ob_auto와 동일 후보)."""
        for k in ("product_code", "prod_cd", "sale_prod_cd", "item_code", "sku"):
            v = str(rec.get(k) or "").strip()
            if v:
                return v
        return ""

    def _ob_map_sku(rec: dict, nm: str) -> str:
        """OB 레코드 → 매핑된 BH SKU (코드 우선, 이름 보조)."""
        sku = ob_code_to_sku.get(_ob_code(rec))
        if not sku:
            sku = ob_name_to_sku.get(nm) or ob_name_to_sku.get(html.unescape(nm or ""))
        return sku or ""

    def _norm_ob(s: str) -> str:
        s = html.unescape(s or "")
        return ob_to_norm.get(s) or U.normalize(s)

    def _norm_ob_ch(ch: str) -> str:
        return ob_ch_to_lbl.get(ch, U.normalize(ch))

    def _norm_bh_partner(p: str) -> str:
        for kw, lbl in bh_kw_to_lbl.items():
            if kw and kw in p: return lbl
        return U.normalize(p)

    # ── BH 데이터 수집 (모든 유형: in/out/move 전부) ──────────────
    # OB 출고가 BH에서 조정·이동으로 기록될 수 있으므로 tx_type에 관계없이 수집
    # 매칭 시 tx_type 필터 제거 → 상품+수량+날짜로만 짝 찾기
    bh_items: list = []

    def _add_bh_tx(txs: list, bh_tx_type: str):
        """BH 거래를 bh_items에 추가. bh_tx_type은 표시용(in/out/move/adjustment)."""
        # adjustment: OB 전산처리용 출고와 매칭하기 위해 tx_type="adjustment"로 설정
        match_tx_type = "adjustment" if bh_tx_type == "adjustment" else "out"
        for tx in txs:
            tx_time = tx.get("transaction_time") or tx.get("created_at", "")
            try: dt = datetime.fromisoformat(tx_time[:19])
            except: continue
            date_str = dt.strftime("%Y-%m-%d")
            memo = tx.get("memo") or ""
            partner_obj = tx.get("partner") or {}
            partner = partner_obj.get("name","") if isinstance(partner_obj,dict) else ""
            sno_m = _re.search(r"입고번호[:\s]*(\d+)", memo)
            put_sno = sno_m.group(1) if sno_m else ""
            norm_partner = _norm_bh_partner(partner)
            for it in tx.get("items", []):
                nm = str(it.get("name") or "")
                qty = abs(int(it.get("quantity", 0)))
                if qty == 0: continue
                raw_sku = str(it.get("sku") or "").strip()
                # 미등록 SKU면 BH 이름으로 대표 SKU 조회 (예: 8806136021689 → 2041810849234)
                canonical_sku = raw_sku if raw_sku in ob_code_to_sku.values() else (bh_name_to_sku.get(nm) or raw_sku)
                # ob_code_to_sku의 키(OB코드) 중 BH sku와 동일한 것도 확인 (바코드 공유 케이스)
                if not canonical_sku or canonical_sku == raw_sku:
                    _rev = {v: k for k, v in ob_code_to_sku.items()}.get(raw_sku)
                    if _rev:
                        canonical_sku = raw_sku  # BH sku가 OB map에서 value로 이미 있음 → 그대로 사용
                bh_items.append({
                    "bh_tx_type": bh_tx_type,
                    "tx_type": match_tx_type,
                    "date": date_str,
                    "name": nm, "norm": U.normalize(nm),
                    "qty": qty, "memo": memo,
                    "partner": partner, "norm_partner": norm_partner,
                    "put_sno": put_sno,
                    "map_sku": canonical_sku,
                })

    # BH in: 입고 (wide_mode시 확장 기간으로 탐색)
    if "in" in types:
        from datetime import timedelta as _td2
        bh_in_fr = (datetime.strptime(from_date,"%Y-%m-%d")-_td2(days=bh_lookback)).strftime("%Y-%m-%d") if wide_mode else from_date
        bh_in_to = (datetime.strptime(to_date,"%Y-%m-%d")+_td2(days=bh_lookback)).strftime("%Y-%m-%d") if wide_mode else to_date
        bh_in = U.fetch_transactions(token, "in", bh_in_fr, bh_in_to, None)
        _enrich_bh_items(token, bh_in)
        for tx in bh_in:
            tx_time = tx.get("transaction_time") or tx.get("created_at", "")
            try: dt = datetime.fromisoformat(tx_time[:19])
            except: continue
            date_str = dt.strftime("%Y-%m-%d")
            memo = tx.get("memo") or ""
            partner_obj = tx.get("partner") or {}
            partner = partner_obj.get("name","") if isinstance(partner_obj,dict) else ""
            sno_m = _re.search(r"입고번호[:\s]*(\d+)", memo)
            put_sno = sno_m.group(1) if sno_m else ""
            for it in tx.get("items", []):
                nm = str(it.get("name") or "")
                qty = abs(int(it.get("quantity", 0)))
                if qty == 0: continue
                raw_sku_in = str(it.get("sku") or "").strip()
                # BH in도 canonical SKU 조회 (out/move/adj와 동일 로직)
                canonical_sku_in = raw_sku_in if raw_sku_in in ob_code_to_sku.values() else (bh_name_to_sku.get(nm) or raw_sku_in)
                bh_items.append({
                    "bh_tx_type": "in", "tx_type": "in",
                    "date": date_str, "name": nm, "norm": U.normalize(nm),
                    "qty": qty, "memo": memo, "partner": partner,
                    "norm_partner": _norm_bh_partner(partner), "put_sno": put_sno,
                    "map_sku": canonical_sku_in,
                })

    if "out" in types:
        # wide_mode: BH 조회 기간을 앞뒤로 확장 (OB 특정 날짜 → BH는 넓게 탐색)
        from datetime import timedelta as _td
        if wide_mode:
            bh_from = (datetime.strptime(from_date, "%Y-%m-%d") - _td(days=bh_lookback)).strftime("%Y-%m-%d")
            bh_to   = (datetime.strptime(to_date,   "%Y-%m-%d") + _td(days=bh_lookback)).strftime("%Y-%m-%d")
        else:
            bh_from, bh_to = from_date, to_date

        # BH out: 실제 출고 (확장된 기간)
        bh_out = U.fetch_transactions(token, "out", bh_from, bh_to, None)
        _enrich_bh_items(token, bh_out)
        _add_bh_tx(bh_out, "out")

        # BH move: 이동 (폐기/소비기한 이동, 창고간 이동 등 전부 포함)
        try:
            import time as _time; _time.sleep(0.5)  # rate limit 방지
            bh_move = U.fetch_transactions(token, "move", bh_from, bh_to, None)
            if bh_move:
                _enrich_bh_items(token, bh_move)
                _add_bh_tx(bh_move, "move")
        except Exception:
            pass  # move 조회 실패 시 무시하고 계속

        # BH adjust 단품 소진 — 세트 조립 시 단품 재고 감소 (OB 전산처리용 출고와 매칭)
        try:
            bh_adj_txs = U.fetch_transactions(token, "adjust", bh_from, bh_to, None)
            _enrich_bh_items(token, bh_adj_txs, tx_type="adjust")
            bh_adj_txs = _filter_bh_adj_negative(bh_adj_txs)  # 음수(단품 소진)만
            _add_bh_tx(bh_adj_txs, "adjustment")
        except Exception:
            pass

    # aggregate=True: BH 출고도 (상품norm, 날짜) 합산
    if aggregate:
        bh_agg_map: dict = _dd(lambda: {"qty":0,"name":"","norm":"","date":"","tx_type":"","memo":"","partner":"","norm_partner":"","put_sno":"","map_sku":""})
        bh_other: list = []
        for it in bh_items:
            if it["tx_type"] == "out":
                k = (it["norm"], it["date"])
                bh_agg_map[k]["qty"] += it["qty"]
                bh_agg_map[k]["name"] = it["name"]
                bh_agg_map[k]["norm"] = it["norm"]
                bh_agg_map[k]["date"] = it["date"]
                bh_agg_map[k]["tx_type"] = "out"
                if it.get("memo"): bh_agg_map[k]["memo"] = it["memo"]
                if it.get("partner"): bh_agg_map[k]["partner"] = it["partner"]
                bh_agg_map[k]["norm_partner"] = it.get("norm_partner","")
                if it.get("put_sno"): bh_agg_map[k]["put_sno"] = it["put_sno"]
                if it.get("map_sku"): bh_agg_map[k]["map_sku"] = it["map_sku"]
            else:
                bh_other.append(it)
        bh_items = bh_other + [dict(v) for v in bh_agg_map.values()]

    # ── OB 데이터 수집·집계 ──────────────────────────────────────
    try:
        client = api_mod.make_client(cfg)
        if not client: raise RuntimeError("OurBox API Key 없음")
    except Exception as e:
        return {"error": str(e), "matched":[], "bh_only":bh_items, "ob_only":[]}

    ob_items: list = []
    excl_set: set = {c.strip() for c in exclude_channels.split(",") if c.strip()} if exclude_channels else set()
    ob_out_excluded_qty = 0

    if "in" in types:
        for rec in client.fetch_inbounds(from_date, to_date):
            if not isinstance(rec, dict): continue
            prod_cd_v = str(rec.get("product_code") or rec.get("prod_cd") or "").strip()
            if prod_cd_v in DEPRECATED_OB_CODES: continue  # 사장된 코드 제외
            if str(rec.get("input_code") or "").strip() in OB_INITIAL_STOCK_INPUT_CODES: continue  # 기초재고 일괄입고 제외
            nm = html.unescape(str(rec.get("product_name","")).strip())
            qty = abs(int(float(str(rec.get("input_qty") or 0))))
            dt = str(rec.get("input_dt") or rec.get("input_complete_dt",""))[:10]
            ch = str(rec.get("channel") or rec.get("mall_name") or "").strip()
            if not nm or qty==0 or not dt: continue
            if ch in ASSEMBLY_CHANNELS: continue  # 전산처리용 입고(세트) = 매칭 불필요 → 제외
            ob_items.append({
                "tx_type": "in", "date":dt,
                "name":nm, "norm":_norm_ob(nm), "qty":qty,
                "channel":ch, "norm_channel":"",
                "put_sno": str(rec.get("input_code") or ""),
                "purch": str(rec.get("purch_company") or ""),
                "map_sku": _ob_map_sku(rec, nm),
            })

    if "out" in types:
        # OB 출고: 전산처리용(세트조립) → 조정 타입으로 라우팅, 나머지는 일반 출고
        # aggregate=True: (상품norm, 날짜) 단위 합산 — 채널 무관하게 총량 비교
        if aggregate:
            ob_out_agg: dict = _dd(lambda:{"qty":0,"name":"","norm":"","channels":set(),"map_sku":""})
            ob_asm_agg: dict = _dd(lambda:{"qty":0,"name":"","norm":"","map_sku":""})
            for rec in client.fetch_outbounds(from_date, to_date):
                if not isinstance(rec, dict): continue
                prod_cd_v = str(rec.get("product_code") or rec.get("prod_cd") or "").strip()
                if prod_cd_v in DEPRECATED_OB_CODES: continue  # 사장된 코드 제외
                nm = html.unescape(str(rec.get("product_name","")).strip())
                qty = abs(int(float(str(rec.get("out_qty") or 0))))
                dt = str(rec.get("out_dt",""))[:10]
                ch = str(rec.get("channel") or rec.get("mall_name") or "")
                if not nm or qty==0 or not dt: continue
                if ch in {"샘플(임박)","샘플(정상소비기한)"}:
                    ob_out_excluded_qty += qty
                    continue
                # 전산처리용 = 세트 조립 출고(단품 소진) → 조정 버킷
                if ch in ASSEMBLY_CHANNELS:
                    norm_nm = _norm_ob(nm)
                    k = (norm_nm, dt)
                    ob_asm_agg[k]["qty"] += qty
                    ob_asm_agg[k]["name"] = nm
                    ob_asm_agg[k]["norm"] = norm_nm
                    if not ob_asm_agg[k]["map_sku"]:
                        ob_asm_agg[k]["map_sku"] = _ob_map_sku(rec, nm)
                    continue
                norm_nm = _norm_ob(nm)
                k = (norm_nm, dt)
                ob_out_agg[k]["qty"] += qty
                ob_out_agg[k]["name"] = nm
                ob_out_agg[k]["norm"] = norm_nm
                ob_out_agg[k]["channels"].add(ch)
                if not ob_out_agg[k]["map_sku"]:
                    ob_out_agg[k]["map_sku"] = _ob_map_sku(rec, nm)
            for (norm_nm, dt), v in ob_out_agg.items():
                ob_items.append({
                    "tx_type":"out", "date":dt,
                    "name":v["name"], "norm":v["norm"], "qty":v["qty"],
                    "channel":", ".join(sorted(v["channels"])) if len(v["channels"])<=3 else f"{len(v['channels'])}개 채널",
                    "norm_channel":"",
                    "put_sno":"", "purch":"",
                    "map_sku": v["map_sku"],
                })
            # 전산처리용 출고 → adjustment 타입으로 추가
            for (norm_nm, dt), v in ob_asm_agg.items():
                ob_items.append({
                    "tx_type":"adjustment", "date":dt,
                    "name":v["name"], "norm":v["norm"], "qty":v["qty"],
                    "channel":"전산처리용", "norm_channel":"전산처리용",
                    "put_sno":"", "purch":"",
                    "map_sku": v["map_sku"],
                })
        else:
            ob_out_agg2: dict = _dd(lambda:{"qty":0,"name":"","norm":"","channel":"","norm_channel":"","put_sno":"","map_sku":""})
            for rec in client.fetch_outbounds(from_date, to_date):
                if not isinstance(rec, dict): continue
                prod_cd_v = str(rec.get("product_code") or rec.get("prod_cd") or "").strip()
                if prod_cd_v in DEPRECATED_OB_CODES: continue  # 사장된 코드 제외
                nm = html.unescape(str(rec.get("product_name","")).strip())
                qty = abs(int(float(str(rec.get("out_qty") or 0))))
                dt = str(rec.get("out_dt",""))[:10]
                ch = str(rec.get("channel") or rec.get("mall_name") or "")
                if not nm or qty==0 or not dt: continue
                if ch in excl_set:
                    ob_out_excluded_qty += qty
                    continue
                # 전산처리용 = 세트 조립 출고(단품 소진) → 조정 타입
                if ch in ASSEMBLY_CHANNELS:
                    ob_items.append({
                        "tx_type":"adjustment", "date":dt,
                        "name":nm, "norm":_norm_ob(nm), "qty":qty,
                        "channel":ch, "norm_channel":ch,
                        "put_sno":"", "purch":"",
                        "map_sku": _ob_map_sku(rec, nm),
                    })
                    continue
                norm_ch = _norm_ob_ch(ch)
                k = (_norm_ob(nm), dt, norm_ch)
                ob_out_agg2[k]["qty"] += qty
                ob_out_agg2[k]["name"] = nm
                ob_out_agg2[k]["norm"] = _norm_ob(nm)
                ob_out_agg2[k]["channel"] = ch
                ob_out_agg2[k]["norm_channel"] = norm_ch
                if not ob_out_agg2[k]["map_sku"]:
                    ob_out_agg2[k]["map_sku"] = _ob_map_sku(rec, nm)
            for (norm_nm, dt, norm_ch), v in ob_out_agg2.items():
                ob_items.append({
                    "tx_type":"out", "date":dt,
                    "name":v["name"], "norm":v["norm"], "qty":v["qty"],
                    "channel":v["channel"], "norm_channel":norm_ch,
                    "put_sno":"", "purch":"",
                    "map_sku": v["map_sku"],
                })

    if "adjustment" in types:
        for rec in client.fetch_adjustments(from_date, to_date):
            if not isinstance(rec, dict): continue
            adj = int(float(str(rec.get("adj_qty") or 0)))
            if adj == 0: continue
            dt = str(rec.get("reg_dtm") or rec.get("reg_dt",""))[:10]
            reason = str(rec.get("stock_adj_resn_nm") or "")
            ob_items.append({
                "tx_type":"adjustment", "date":dt,
                "name":f"[조정]{reason}", "norm":U.normalize(reason),
                "qty":abs(adj), "channel":"", "norm_channel":"",
                "put_sno":"", "purch":"", "map_sku": "",
            })

    # ── 점수 함수 ────────────────────────────────────────────────
    def _calc(b: dict, o: dict) -> dict:
        """
        핵심 철학: 품목(SKU/이름) + 수량이 일치하면 날짜·유형·세트작업·이동·조정 초월 매칭.
        - 날짜 차이는 '참고용'이지 탈락 기준이 아님 (wide_mode)
        - 유형 차이 (out↔adj, move↔out, in↔out)도 SKU/이름+수량이 확실하면 매칭
        - 세트작업(전산처리용 출고) ↔ BH 조정/이동: 동일 처리
        """
        # ── 수량 계산 (먼저 — 10배 초과는 어떤 경우에도 탈락) ──
        big = max(b["qty"], o["qty"])
        small = min(b["qty"], o["qty"])
        if big == 0:
            return {"total": 0.0}
        if small > 0 and big / small > 10:
            return {"total": 0.0}  # 10배 초과 수량 차이 → 다른 물건
        ratio = abs(b["qty"] - o["qty"]) / big
        if ratio == 0:       qty_sc = 100.0
        elif ratio <= 0.05:  qty_sc = 95.0
        elif ratio <= 0.10:  qty_sc = 88.0
        elif ratio <= 0.20:  qty_sc = 78.0
        elif ratio <= 0.30:  qty_sc = 65.0
        elif ratio <= 0.50:  qty_sc = max(40, 100 - ratio * 150)
        else:                qty_sc = max(0, 100 - ratio * 100)

        # ── SKU 매칭 확인 ──
        b_sku = b.get("map_sku") or ""
        o_sku = o.get("map_sku") or ""
        sku_match = bool(b_sku and o_sku and b_sku == o_sku)

        # ── 날짜 차이 계산 (탈락 기준 아님, 표시·참고용) ──
        try:
            gap = abs((datetime.strptime(b["date"], "%Y-%m-%d")
                       - datetime.strptime(o["date"], "%Y-%m-%d")).days)
        except Exception:
            return {"total": 0.0}
        if not wide_mode and gap > tolerance_days:
            return {"total": 0.0}

        # ── 품목명 유사도 ──
        name_sc = _fuzz.token_set_ratio(b["norm"], o["norm"])

        # ══════════════════════════════════════════════════════════════
        # 핵심 매칭 1: SKU 직접 확인 + 수량 근사
        #   → 날짜·유형·방향 완전 초월 매칭 (세트작업·이동·조정 포함)
        # ══════════════════════════════════════════════════════════════
        if wide_mode and sku_match and qty_sc >= 70:
            bh_t = b.get("bh_tx_type", b["tx_type"])
            ob_t = o.get("tx_type", "")
            cross_label = f"BH:{bh_t}↔OB:{ob_t}" if bh_t != ob_t else f"유형:{bh_t}"
            score = round(95 + min(5, qty_sc / 20), 1)
            return {
                "total": score,
                "name_sc": round(name_sc), "qty_sc": round(qty_sc),
                "date_sc": 0, "day_gap": gap,
                "ch_bonus": 0, "sno_bonus": 0, "sku_match": True,
                "reason": f"SKU완전매칭({cross_label})+수량{qty_sc:.0f}+날짜{gap}일차이",
            }

        # ══════════════════════════════════════════════════════════════
        # 핵심 매칭 2: 이름 매우 유사 + 수량 완전 일치 (SKU 없는 경우)
        #   → 날짜·유형 초월 (단, 이름 90% + 수량 완전 일치만)
        # ══════════════════════════════════════════════════════════════
        if wide_mode and name_sc >= 90 and qty_sc >= 95:
            bh_t = b.get("bh_tx_type", b["tx_type"])
            ob_t = o.get("tx_type", "")
            cross_label = f"BH:{bh_t}↔OB:{ob_t}" if bh_t != ob_t else f"유형:{bh_t}"
            return {
                "total": 88.0,
                "name_sc": round(name_sc), "qty_sc": round(qty_sc),
                "date_sc": 0, "day_gap": gap,
                "ch_bonus": 0, "sno_bonus": 0, "sku_match": False,
                "reason": f"이름수량완전매칭({cross_label})+이름{name_sc:.0f}+날짜{gap}일차이",
            }

        # ══════════════════════════════════════════════════════════════
        # 일반 매칭: 날짜·유형 포함 채점 (위 조건 미충족 케이스)
        # ══════════════════════════════════════════════════════════════
        b_in = b["tx_type"] == "in"
        o_in = o["tx_type"] == "in"
        b_adj = b["tx_type"] == "adjustment"
        o_adj = o["tx_type"] == "adjustment"
        # adj는 방향 없음 → cross_dir 면제; move도 out으로 처리되므로 면제
        cross_dir = b_in != o_in and not (b_adj or o_adj)

        if wide_mode:
            date_sc = max(0, 100 - gap * 2)
            name_w, qty_w, date_w = 0.45, 0.45, 0.10
        else:
            date_sc = max(0, 100 - gap * (100 // (tolerance_days + 1)))
            name_w, qty_w, date_w = 0.38, 0.37, 0.15

        # ── 힌트 보너스 ─────────────────────────────────────────
        bonuses: list = []
        bonus_total = 0

        # 1. 입고번호(put_sno) 일치: 가장 강력한 힌트 (+25)
        if b.get("put_sno") and o.get("put_sno") and b["put_sno"] == o["put_sno"]:
            bonus_total += 25; bonuses.append(f"입고번호+25")

        # 2. 거래처/채널 매칭 (+15)
        b_partner = b.get("norm_partner") or U.normalize(b.get("partner",""))
        o_channel  = o.get("norm_channel") or U.normalize(o.get("channel",""))
        if b_partner and o_channel and b_partner == o_channel:
            bonus_total += 15; bonuses.append(f"거래처+15")
        elif b_partner and o_channel:
            ch_sim = _fuzz.partial_ratio(b_partner, o_channel)
            if ch_sim >= 80:
                bonus_total += 8; bonuses.append(f"거래처유사+8")

        # 3. BH memo에 OB 채널명 키워드 포함 (+10)
        bh_memo_norm = U.normalize(b.get("memo",""))
        ob_ch_norm = U.normalize(o.get("channel",""))
        if bh_memo_norm and ob_ch_norm and len(ob_ch_norm) >= 2:
            if ob_ch_norm[:4] in bh_memo_norm or bh_memo_norm[:4] in ob_ch_norm:
                bonus_total += 10; bonuses.append(f"메모채널+10")

        # 4. BH 구매처(partner) ↔ OB 구매처(purch) 일치 (+10, 입고용)
        b_partner_raw = U.normalize(b.get("partner",""))
        o_purch = U.normalize(o.get("purch",""))
        if b_partner_raw and o_purch and b_partner_raw == o_purch:
            bonus_total += 10; bonuses.append(f"구매처+10")

        # 5. BH 이동(move) + OB 전산처리용 → 보너스 (+8)
        if b.get("bh_tx_type") == "move" and "전산처리용" in o.get("channel",""):
            bonus_total += 8; bonuses.append(f"이동↔전산+8")

        total = name_sc * name_w + qty_sc * qty_w + date_sc * date_w + bonus_total

        # cross_dir (in↔out 방향 반대): SKU 확인 시 85, 이름유사 시 65
        if cross_dir:
            if sku_match and qty_sc >= 70:
                total = min(total, 85.0)
                bonuses = ["방향반대+SKU확인"]
            elif name_sc >= 80 and qty_sc >= 30:
                total = min(total, 65.0)
                bonuses = ["방향반대(물류↔내부)"]
            else:
                return {"total": 0.0}

        # SKU 일치 부스트 (일반 매칭 경로)
        if sku_match and not cross_dir:
            total = max(total, 90 + min(5, qty_sc / 20))
            bonuses.append("코드일치SKU")

        # 채널 필수 모드: 채널 불일치 시 페널티 (SKU 일치는 면제)
        if channel_required and not sku_match:
            if b_partner and o_channel:
                ch_sim2 = _fuzz.partial_ratio(b_partner, o_channel)
                if b_partner != o_channel and ch_sim2 < 80:
                    total *= 0.6
                    bonuses.append("채널불일치-40%")

        parts = [f"이름{name_sc:.0f}", f"수량{qty_sc:.0f}", f"날짜({gap}일){date_sc:.0f}"]
        parts.extend(bonuses)
        return {
            "total": round(total, 1),
            "name_sc": round(name_sc), "qty_sc": round(qty_sc),
            "date_sc": round(date_sc), "day_gap": gap,
            "ch_bonus": bonus_total, "sno_bonus": 25 if bonuses and "입고번호" in bonuses[0] else 0,
            "sku_match": sku_match,
            "reason": "+".join(parts),
        }

    def _score(b: dict, o: dict) -> float:
        return _calc(b, o).get("total", 0.0)

    # ── 그리디 매칭 ──────────────────────────────────────────────
    candidates = []
    for i, b in enumerate(bh_items):
        for j, o in enumerate(ob_items):
            sc = _score(b, o)
            if sc >= min_score:
                candidates.append((sc, i, j))
    candidates.sort(key=lambda x: -x[0])

    used_bh: set = set(); used_ob: set = set(); matched: list = []
    for sc, bi, oi in candidates:
        if bi in used_bh or oi in used_ob: continue
        used_bh.add(bi); used_ob.add(oi)
        b, o = bh_items[bi], ob_items[oi]
        qty_diff = b["qty"] - o["qty"]
        day_gap = abs((datetime.strptime(b["date"],"%Y-%m-%d")-datetime.strptime(o["date"],"%Y-%m-%d")).days)
        status = ("exact" if sc>=85 else "probable") if qty_diff==0 else \
                 ("qty_diff_high" if sc>=85 else "qty_diff_low")
        detail = _calc(b, o)
        # 대안 후보 (다른 OB 항목 top-3) — 이미 계산된 candidates 재사용 (재계산 없음)
        alt_from_candidates = [
            (s, j2) for s, i2, j2 in candidates
            if i2 == bi and j2 != oi and s >= 40
        ]
        alt_from_candidates.sort(key=lambda x: -x[0])
        alt_cands = alt_from_candidates[:3]
        alternatives = []
        for alt_sc, aj in alt_cands:
            ao = ob_items[aj]
            ad = _calc(b, ao)
            alternatives.append({
                "ob_idx": aj, "score": alt_sc, "reason": ad.get("reason",""),
                "ob_date": ao["date"], "ob_name": ao["name"],
                "ob_qty": ao["qty"], "ob_channel": ao.get("channel",""),
                "ob_put_sno": ao.get("put_sno",""),
                "qty_diff": b["qty"] - ao["qty"],
            })
        matched.append({
            "bh_idx": bi, "ob_idx": oi,
            "score": round(sc,1), "status": status,
            "tx_type": b["tx_type"],  # 매칭 기준 유형
            "bh_tx_type": b.get("bh_tx_type", b["tx_type"]),  # BH 실제 유형 (out/move/in)
            "ob_tx_type": o.get("tx_type", "out"),  # OB 실제 유형
            "score_detail": detail,
            "bh_date":b["date"], "ob_date":o["date"], "day_gap":day_gap,
            "bh_name":b["name"], "ob_name":o["name"],
            "bh_qty":b["qty"], "ob_qty":o["qty"], "qty_diff":qty_diff,
            "bh_memo":b.get("memo",""), "bh_partner":b.get("partner",""),
            "ob_channel":o.get("channel",""), "ob_purch":o.get("purch",""),
            "bh_put_sno":b.get("put_sno",""), "ob_put_sno":o.get("put_sno",""),
            "cross_type": b.get("tx_type","") != o.get("tx_type",""),  # BH/OB 거래유형 다름
            "alternatives": alternatives,
        })

    # BH단독: 아직 매칭 안 된 OB 항목 후보 상위 5 첨부
    ob_unused = [j for j in range(len(ob_items)) if j not in used_ob]
    bh_only = []
    for i in range(len(bh_items)):
        if i in used_bh: continue
        b = bh_items[i]
        cands = sorted(
            [(round(_score(b, ob_items[j]),1), j) for j in ob_unused if _score(b, ob_items[j]) >= 30],
            key=lambda x: -x[0]
        )[:5]
        cand_list = []
        for csc, cj in cands:
            co = ob_items[cj]
            cd = _calc(b, co)
            cand_list.append({
                "ob_idx":cj, "score":csc, "reason":cd.get("reason",""),
                "ob_date":co["date"], "ob_name":co["name"],
                "ob_qty":co["qty"], "ob_channel":co.get("channel",""),
                "ob_put_sno":co.get("put_sno",""),
                "qty_diff":b["qty"]-co["qty"],
            })
        b2 = dict(b)
        b2["bh_idx"] = i
        b2["candidates"] = cand_list
        bh_only.append(b2)

    ob_only = [{"ob_idx":j, **ob_items[j]} for j in range(len(ob_items)) if j not in used_ob]
    matched.sort(key=lambda x: -x["score"])
    ob_only.sort(key=lambda x: -x["qty"])
    bh_only.sort(key=lambda x: -x["qty"])
    exact = sum(1 for m in matched if m["status"] in ("exact","qty_diff_high"))
    by_type = {}
    for t in ["in","out","adjustment"]:
        tm = [m for m in matched if m["tx_type"]==t]
        tb = [x for x in bh_only if x["tx_type"]==t]
        to_ = [x for x in ob_only if x["tx_type"]==t]
        bh_t = sum(1 for x in bh_items if x["tx_type"]==t)
        ob_t = sum(1 for x in ob_items if x["tx_type"]==t)
        by_type[t] = {
            "matched": len(tm), "bh_only": len(tb), "ob_only": len(to_),
            "bh_total": bh_t, "ob_total": ob_t,
            "bh_match_rate": round(len(tm)/max(bh_t,1)*100,1),
            "ob_match_rate": round(len(tm)/max(ob_t,1)*100,1),
        }

    # ── 채널별 집계 대시보드 ──────────────────────────────────────
    # BH partner / OB channel 단위로 수량 집계
    from collections import defaultdict as _dd3
    ch_bh: dict = _dd3(lambda: {"qty": 0, "matched_qty": 0})
    ch_ob: dict = _dd3(lambda: {"qty": 0, "matched_qty": 0})

    for b in bh_items:
        lbl = b.get("partner") or "채널미상"
        ch_bh[lbl]["qty"] += b["qty"]
    for o in ob_items:
        lbl = o.get("channel") or o.get("norm_channel") or "채널미상"
        ch_ob[lbl]["qty"] += o["qty"]
    for m in matched:
        bh_lbl = m.get("bh_partner") or "채널미상"
        ob_lbl = m.get("ob_channel") or "채널미상"
        bq = m["bh_qty"]; oq = m["ob_qty"]
        ch_bh[bh_lbl]["matched_qty"] += bq
        ch_ob[ob_lbl]["matched_qty"] += oq

    # 모든 채널 합집합으로 행 생성
    all_ch = sorted(set(ch_bh.keys()) | set(ch_ob.keys()),
                    key=lambda c: -(ch_bh[c]["qty"] + ch_ob[c]["qty"]))
    by_channel = []
    for ch in all_ch:
        bq = ch_bh[ch]["qty"]; oq = ch_ob[ch]["qty"]
        bmq = ch_bh[ch]["matched_qty"]; omq = ch_ob[ch]["matched_qty"]
        diff = bq - oq
        by_channel.append({
            "channel": ch,
            "bh_qty": bq, "ob_qty": oq, "diff": diff,
            "bh_matched_qty": bmq, "ob_matched_qty": omq,
            "bh_match_rate": round(bmq/max(bq,1)*100, 1),
            "ob_match_rate": round(omq/max(oq,1)*100, 1),
        })

    return {
        "from_date":from_date, "to_date":to_date,
        "tolerance_days":tolerance_days, "min_score":min_score,
        "aggregate":aggregate,
        "excluded_channels":list(excl_set) if 'excl_set' in dir() else [],
        "excluded_qty":ob_out_excluded_qty if 'ob_out_excluded_qty' in dir() else 0,
        "total_bh":len(bh_items), "total_ob":len(ob_items),
        "matched_count":len(matched), "exact_count":exact,
        "probable_count":len(matched)-exact,
        "bh_only_count":len(bh_only), "ob_only_count":len(ob_only),
        "match_rate_bh":round(len(matched)/max(len(bh_items),1)*100,1),
        "match_rate_ob":round(len(matched)/max(len(ob_items),1)*100,1),
        "by_type":by_type,
        "by_channel":by_channel,
        "matched":matched, "bh_only":bh_only, "ob_only":ob_only,
    }


@router.get("/stock")
def get_stock(
    token: str = Query(...),
    location_ids: str = Query(""),   # BH 위치 필터 (콤마구분) — 빈값이면 전체 합산. OB는 호법센터만.
    use_mapping: bool = Query(True),
):
    """BH·OB 현재 재고 스냅샷 비교 + 차이 원인 자동 분해.

    차이 = BH재고 - OB가용. 각 차이를 다음 원인으로 자동 분류:
      · 불용재고: OB가 판매불가로 빼둔 수량(total-available) → BH가 그만큼 많아 보임
      · 매핑1:N: BH 1개 SKU가 여러 OB 상품에 매핑되어 합산 왜곡
      · 재고차: 위로 설명 안 되는 순수 재고 차이 (거래 흐름 추적 필요)
    """
    import ourbox_api as api_mod
    import requests as _req

    cfg = U.load_config()
    _loc_ids = {int(x) for x in location_ids.split(",") if x.strip().isdigit()}

    # 유통기한 날짜 suffix 제거: "상품명-2028-05-04", "상품명 28-03-23" 등
    # (BH는 유통기한을 상품명에 붙이는데 OB는 안 붙여서 norm이 갈라짐 → 같은 제품 분리 방지)
    import re as _re_sd
    def _strip_date(nm: str) -> str:
        s = str(nm or "")
        s = _re_sd.sub(r'[\s\-_]+\d{2,4}[-.]\d{1,2}[-.]\d{1,2}\s*$', '', s)  # 끝의 날짜
        return s.strip()
    def _snorm(nm: str) -> str:
        return U.normalize(_strip_date(nm))

    # ── BH 재고: items API의 quantities[] (위치별) 사용 ──────────────
    # 주의: 같은 상품명(norm)이 여러 SKU에 걸칠 수 있으므로 SKU 단위 리스트로 수집
    # (norm 키로 덮어쓰면 동명 SKU 재고가 누락됨 — 메노포즈 케이스)
    bh_items_list: list = []
    try:
        cursor = None
        _seen_cur: set = set()
        _pages = 0
        while True:
            params: dict = {"limit": 100}
            if cursor: params["cursor"] = cursor
            r = _req.get("https://rest.boxhero-app.com/v1/items",
                         headers={"Authorization": f"Bearer {token}"},
                         params=params, timeout=20)
            if not r.ok: break
            d = r.json()
            for it in d.get("items", []):
                sku = str(it.get("sku") or it.get("id") or "")
                name = str(it.get("name") or "")
                if not (sku or name): continue
                if _loc_ids:
                    qty = sum(int(q.get("quantity") or 0)
                              for q in (it.get("quantities") or [])
                              if int(q.get("location_id") or 0) in _loc_ids)
                else:
                    qty = int(it.get("quantity") or 0)
                bh_items_list.append({"sku": sku, "name": name, "norm": _snorm(name), "quantity": qty})
            _pages += 1
            if not d.get("has_more"): break
            _nc = d.get("cursor")
            if not _nc or _nc in _seen_cur or _pages >= 100:
                break  # cursor 반복/소진/과다 → 무한루프 방지
            _seen_cur.add(_nc); cursor = _nc
    except Exception:
        bh_items_list = []

    # ── OB 재고: product_stock (가용/총/불용) ────────────────────────
    ob_stock: dict = {}
    try:
        client = api_mod.make_client(cfg)
        if client:
            raw = client.fetch_stock()
            from collections import defaultdict as _dd2
            agg: dict = _dd2(lambda: {"total": 0, "available": 0, "unavail": 0, "name": "", "code": "", "codes": set()})
            for r in raw:
                nm = html.unescape(str(r.get("product_name") or "").strip())
                code = str(r.get("sales_product_code") or "")
                total = int(r.get("total_stock") or 0)
                avail = int(r.get("available_stock") or 0)
                # OurBox unavailable_stock = 가용외(출고 할당·작업중·불용 등). 필드 직접 사용
                unav = int(r.get("unavailable_stock") or 0)
                k = _snorm(nm)
                agg[k]["total"] += total
                agg[k]["available"] += avail
                agg[k]["unavail"] += unav
                agg[k]["name"] = nm
                agg[k]["code"] = code
                if code: agg[k]["codes"].add(code)
            ob_stock = {k: {**v, "codes": sorted(v["codes"])} for k, v in agg.items()}
    except Exception:
        ob_stock = {}

    # ── 매핑 그룹: BH SKU ↔ OB 상품 (1:N 공유 탐지) ──────────────────
    bh_to_group = ob_to_group = ob_code_to_group = group_label = group_name = bh_name_to_group = {}
    if use_mapping:
        try:
            _g = _build_mapping_groups()
            bh_to_group      = _g.get("bh_to_group", {}) or {}
            ob_to_group      = _g.get("ob_to_group", {}) or {}        # OB 원본 상품명 → group
            ob_code_to_group = _g.get("ob_code_to_group", {}) or {}   # OB 코드 → group
            group_label      = _g.get("group_label", {}) or {}
            group_name       = _g.get("group_name", {}) or {}         # group → 대표 상품명(표시용)
            bh_name_to_group = _g.get("bh_name_to_group", {}) or {}   # BH 원본 이름 → group
        except Exception:
            pass

    def _glabel(g: str, fallback: str) -> str:
        return str(group_name.get(g) or group_label.get(g) or fallback)

    # ── 그룹 단위로 BH/OB 재고 병합 ──────────────────────────────────
    # 그룹 키: 매핑 group이 있으면 group id, 없으면 norm 상품명. 매핑은 원본 이름/코드/SKU로 조회.
    def _grp_key_bh(orig_nm: str, norm_nm: str, sku: str):
        g = bh_to_group.get(sku) or bh_name_to_group.get(orig_nm)
        return (g, _glabel(g, orig_nm or norm_nm)) if g else (None, norm_nm)

    def _grp_key_ob(orig_nm: str, norm_nm: str, codes: list):
        for c in codes:
            g = ob_code_to_group.get(c)
            if g: return (g, _glabel(g, orig_nm or norm_nm))
        g = ob_to_group.get(orig_nm)
        if g: return (g, _glabel(g, orig_nm or norm_nm))
        return (None, norm_nm)

    from collections import defaultdict as _ddg
    merged: dict = _ddg(lambda: {
        "name": "", "sku": "", "ob_code": "", "ob_codes": set(),
        "bh_stock": None, "ob_total": None, "ob_avail": None, "ob_unav": None,
        "bh_skus": set(), "bh_names": set(), "ob_names": set(),
    })
    # 병합 키: 그룹이든 미매핑이든 '대표이름의 정규화값'으로 통일
    # → OB는 매핑 그룹, BH는 미매핑(NM)이어도 같은 제품이면 동일 키로 합쳐짐
    for bh in bh_items_list:
        norm = bh["norm"]
        g, label = _grp_key_bh(bh.get("name", ""), norm, bh.get("sku", ""))
        gk = _snorm(label) if g else norm
        m = merged[gk]
        m["bh_stock"] = (m["bh_stock"] or 0) + bh.get("quantity", 0)
        if not m["name"]: m["name"] = label
        if bh.get("sku"): m["bh_skus"].add(bh["sku"]); m["sku"] = m["sku"] or bh["sku"]
        if bh.get("name"): m["bh_names"].add(bh["name"])
    for norm, ob in ob_stock.items():
        g, label = _grp_key_ob(ob.get("name", ""), norm, ob.get("codes", []))
        gk = _snorm(label) if g else norm
        m = merged[gk]
        m["ob_total"] = (m["ob_total"] or 0) + ob.get("total", 0)
        m["ob_avail"] = (m["ob_avail"] or 0) + ob.get("available", 0)
        m["ob_unav"] = (m["ob_unav"] or 0) + ob.get("unavail", 0)
        if not m["name"]: m["name"] = label
        for c in ob.get("codes", []): m["ob_codes"].add(c)
        m["ob_code"] = m["ob_code"] or ob.get("code", "")
        if ob.get("name"): m["ob_names"].add(ob["name"])

    # ── 행 구성 + 차이 원인 자동 분해 ────────────────────────────────
    rows = []
    for gk, m in merged.items():
        bh_q = m["bh_stock"]
        ob_total = m["ob_total"]
        ob_avail = m["ob_avail"]
        # OurBox unavailable_stock 직접 사용 (가용외 = 출고할당·작업중·불용 등). total-available 계산 아님
        unusable = m["ob_unav"] if m["ob_unav"] is not None else ((ob_total - ob_avail) if (ob_total is not None and ob_avail is not None) else 0)
        # 차이 = BH재고 - OB총재고 (가용외 포함). 가용외는 OB 내부 상태(할당/보류)라 비교 기준에서 제외 →
        # 가용 기준 비교의 ±수천 출렁임(할당 타이밍 노이즈) 제거, 진짜 정합오차만 남김.
        diff = (bh_q or 0) - (ob_total or 0) if (bh_q is not None and ob_total is not None) else None
        # 참고용: BH - OB가용 (기존 엑셀 기준). 가용/가용외 표기와 함께 보조 지표로 노출
        diff_vs_available = (bh_q or 0) - (ob_avail or 0) if (bh_q is not None and ob_avail is not None) else None

        # 원인 분해 (총재고 기준이므로 가용외는 차이를 '설명'하지 않음 — 참고 표기만)
        causes = []
        residual = diff  # 총재고 비교라 잔여 = 차이. 매핑다중은 정량화 불가라 차감하지 않음
        if diff is not None:
            # 1) 매핑 1:N (BH 1 SKU ↔ OB 여러 코드)
            if len(m["ob_codes"]) > 1 or len(m["bh_skus"]) > 1:
                causes.append({"type": "매핑다중", "qty": None,
                               "desc": f"BH SKU {len(m['bh_skus'])}개 ↔ OB 코드 {len(m['ob_codes'])}개 그룹 합산"})
            # 2) 가용외 정보(참고): 총재고엔 이미 포함돼 차이에 영향 없음. 가용/가용외 분리 표기용
            if unusable and unusable > 0:
                causes.append({"type": "가용외", "qty": None,
                               "desc": f"OB 가용외 {unusable:,}개 (출고 할당·작업중·불용 등). 총재고 비교라 차이엔 영향 없음(참고)"})

        rows.append({
            "name": m["name"] or gk,
            "sku": m["sku"],
            "ob_code": m["ob_code"],
            "ob_codes": sorted(m["ob_codes"]),
            "bh_skus": sorted(m["bh_skus"]),
            "bh_names": sorted(m["bh_names"]),
            "ob_names": sorted(m["ob_names"]),
            "bh_stock": bh_q,
            "ob_stock_total": ob_total,
            "ob_stock_available": ob_avail,
            "ob_unusable": unusable,
            "diff": diff,                       # BH - OB총재고 (주지표)
            "diff_vs_total": diff,              # 하위호환(= diff)
            "diff_vs_available": diff_vs_available,  # BH - OB가용 (참고 보조지표)
            "residual": residual,               # 매핑 외 설명 안 된 진짜 차이 (= diff)
            "causes": causes,
        })

    rows.sort(key=lambda x: -(abs(x["diff"]) if x["diff"] is not None else 0))
    ok = [r for r in rows if r["diff"] == 0]
    diff_rows = [r for r in rows if r["diff"] is not None and r["diff"] != 0]
    only_bh = [r for r in rows if r["bh_stock"] is not None and r["ob_stock_total"] is None]
    only_ob = [r for r in rows if r["bh_stock"] is None and r["ob_stock_total"] is not None]
    # 잔여(불용/매핑으로 설명 안 됨)가 0이 아닌 행 = 진짜 거래 추적 필요
    need_trace = [r for r in rows if r.get("residual") not in (None, 0)]

    return {
        "total": len(rows), "ok_count": len(ok),
        "diff_count": len(diff_rows), "only_bh": len(only_bh), "only_ob": len(only_ob),
        "need_trace_count": len(need_trace),
        "filtered_locations": sorted(_loc_ids),
        "rows": rows,
    }


# ── 주간 재고 비교 리포트 (자동 스냅샷 저장 + 조회) ─────────────────────────

_WR_ENRICHING: set = set()  # (report_date, location_ids) — 사전계산 중복 실행 방지


@router.post("/weekly-report")
def create_weekly_report(
    token: str = Query(...),
    location_ids: str = Query("228640"),  # 기본: 아워박스 호법
    report_date: str = Query(""),         # 빈값이면 오늘
    trace_months: int = Query(3),         # 거래 분해 사전계산 기간(개월)
):
    """재고 스냅샷 비교 + 원인분해를 계산해 DB에 저장.
    Windows 작업스케줄러가 매주 월요일 이 엔드포인트를 호출하면 됨.
    """
    import receiving_db as _db
    from datetime import datetime as _dtw, timedelta as _tdw
    import re as _re_wr
    rd = report_date or _dtw.now().strftime("%Y-%m-%d")
    # 기존 stock 계산 로직 재사용
    result = get_stock(token=token, location_ids=location_ids, use_mapping=True)
    if result.get("rows") is None:
        return {"saved": False, "error": "재고 조회 실패"}

    # ── 리포트 먼저 저장 (재고 조회는 수 초) ──
    # 아래 사전계산(3개월 거래 대조)은 수 분~수십 분 걸릴 수 있어 동기로 하면
    # 프론트(타임아웃)와 월요일 스케줄러가 전부 실패한다 (6/24 이후 저장 안 되던 원인).
    # 먼저 저장하고, 사전계산은 백그라운드 스레드가 끝나는 대로 같은 리포트를 덮어쓴다.
    _db.save_stock_report(rd, location_ids, result)

    def _enrich_report():
        # ── 차이 품목(잔여≠0)의 거래 분해 사전 계산 ──
        # 최근 trace_months개월 compare(total)를 1회 호출 → 품목별 입고/출고/조정 BH/OB를 행에 첨부
        # → 앱에서 행 클릭 시 API 호출 없이 즉시 표시
        flow_filled = 0
        try:
            _to = _dtw.strptime(rd, "%Y-%m-%d")
            _from = (_to - _tdw(days=trace_months * 30)).strftime("%Y-%m-%d")
            # compare는 FastAPI 엔드포인트 — 직접 호출 시 모든 Query 파라미터를 명시해야
            # (누락 시 Query 객체가 그대로 들어가 timedelta 등에서 터짐)
            cmp = compare(token=token, from_date=_from, to_date=rd, period="month",
                          location_id=None, location_ids=location_ids or None,
                          use_mapping=True, mode="total", by_channel=False,
                          bh_lookback=7, qty_tolerance=0.0, merge_types=False,
                          exclude_adj=False, bh_adj_max_qty=0)
            def _nrm(s): return _re_wr.sub(r"[\s\-_·•\[\]()（）]", "", str(s or "")).lower()
            flow_map: dict = {}
            # ⚠ compare total은 한 품목을 in/out/adj 3행으로 주고 각 행에 동일한 분해값(sku 전체)을
            #   복제함 → sku 중복 제거 후 합산 (안 하면 3배 부풀림)
            _flow_seen: set = set()
            for cr in (cmp.get("rows") or []):
                _sku = str(cr.get("sku") or cr.get("name") or "")
                if _sku in _flow_seen:
                    continue
                _flow_seen.add(_sku)
                k = _nrm(cr.get("name") or cr.get("sku") or "")
                f = flow_map.setdefault(k, {"bh": {"in":0,"out":0,"move":0,"adjustment":0}, "ob": {"in":0,"out":0,"adjustment":0}})
                f["bh"]["in"] += cr.get("bh_in_qty",0) or 0; f["bh"]["out"] += cr.get("bh_out_qty",0) or 0; f["bh"]["move"] += cr.get("bh_move_qty",0) or 0; f["bh"]["adjustment"] += cr.get("bh_adj_qty",0) or 0
                f["ob"]["in"] += cr.get("ob_in_qty",0) or 0; f["ob"]["out"] += cr.get("ob_out_qty",0) or 0; f["ob"]["adjustment"] += cr.get("ob_adj_qty",0) or 0
            for r in result["rows"]:
                if r.get("residual") in (None, 0):
                    continue  # 잔여 없는 건(불용/매핑으로 설명됨) 생략
                fl = flow_map.get(_nrm(r.get("name") or ""))
                if fl:
                    r["flow"] = {**fl, "from": _from, "to": rd}
                    flow_filled += 1
        except Exception as _e_fl:
            result.setdefault("errors", []).append(f"[정보] 거래분해 사전계산 실패: {str(_e_fl)[:60]}")

        # ── 개별 거래 매칭(full-match) 사전계산 — 짝 안 맞는 거래를 차이 품목에 첨부 ──
        # 평일에 행 클릭 시 즉시 "전산오류 거래 위치" 표시 (OB 캐시는 위 compare가 채워둠)
        fm_filled = 0
        try:
            _to2 = _dtw.strptime(rd, "%Y-%m-%d")
            _from = (_to2 - _tdw(days=trace_months * 30)).strftime("%Y-%m-%d")
            # 유통기한 날짜 suffix 제거 후 정규화 (full-match는 원본명+날짜, stock은 대표명 → 통일)
            def _nrm_d(s):
                s2 = _re_wr.sub(r'[\s\-_]+\d{2,4}[-.]\d{1,2}[-.]\d{1,2}\s*$', '', str(s or ''))
                return _re_wr.sub(r"[\s\-_·•\[\]()（）]", "", s2).lower()
            fm = full_match(token=token, from_date=_from, to_date=rd,
                            min_score=60, bh_lookback=5, location_ids=location_ids,
                            bh_internal_keywords="일원화,품목통합,기초재고,재고이관,창고이동")
            from collections import defaultdict as _dd_fm
            _fm_bo = _dd_fm(list); _fm_oo = _dd_fm(list); _fm_qd = _dd_fm(list)
            for x in fm.get("bh_only", []):
                _fm_bo[_nrm_d(x.get("name") or "")].append({"date": x.get("date"), "qty": x.get("qty"), "bh_type": x.get("bh_type")})
            for x in fm.get("ob_only", []):
                _fm_oo[_nrm_d(x.get("name") or "")].append({"date": x.get("date"), "qty": x.get("qty"), "ob_type": x.get("ob_type")})
            for x in fm.get("matched", []):
                if x.get("qty_diff"):
                    for nm in (x.get("bh_name"), x.get("ob_name")):
                        _fm_qd[_nrm_d(nm or "")].append({"bh_date": x.get("bh_date"), "ob_date": x.get("ob_date"),
                            "bh_qty": x.get("bh_qty"), "ob_qty": x.get("ob_qty"), "qty_diff": x.get("qty_diff"), "day_gap": x.get("day_gap")})
            for r in result["rows"]:
                if r.get("residual") in (None, 0):
                    continue
                k = _nrm_d(r.get("name") or "")
                bo, oo, qd = _fm_bo.get(k, []), _fm_oo.get(k, []), _fm_qd.get(k, [])
                if bo or oo or qd:
                    # 수량차 중복 제거 (bh_name/ob_name 둘 다 등록되어 2배 가능)
                    _seen = set(); _qd2 = []
                    for q in qd:
                        key = (q["bh_date"], q["ob_date"], q["bh_qty"], q["ob_qty"])
                        if key not in _seen:
                            _seen.add(key); _qd2.append(q)
                    r["fm"] = {"bh_only": bo, "ob_only": oo, "qty_diff": _qd2, "from": _from, "to": rd}
                    fm_filled += 1
        except Exception as _e_fm:
            result.setdefault("errors", []).append(f"[정보] 개별매칭 사전계산 실패: {str(_e_fm)[:60]}")

        try:
            _db.save_stock_report(rd, location_ids, result)  # 사전계산 반영해 덮어쓰기
        finally:
            _WR_ENRICHING.discard((rd, location_ids))

    import threading as _th_wr
    if (rd, location_ids) not in _WR_ENRICHING:
        _WR_ENRICHING.add((rd, location_ids))
        _th_wr.Thread(target=_enrich_report, daemon=True, name="weekly-report-enrich").start()

    return {
        "saved": True, "report_date": rd, "location_ids": location_ids,
        "total": result.get("total"), "diff_count": result.get("diff_count"),
        "need_trace_count": result.get("need_trace_count"),
        "enrich": "background",
    }


@router.get("/stock-diff-change")
def stock_diff_change(
    token: str = Query(...),
    name: str = Query(...),                    # 품목명 (재고현황 행 name)
    report_t1_id: int = Query(...),            # 이전(기준) 리포트 id
    report_t2_id: int = Query(0),              # 비교(나중) 리포트 id (0=최신 저장 리포트)
    location_ids: str = Query("228640"),
    include_fm: bool = Query(True),            # 개별 거래 매칭(한쪽에만 있는 거래) 포함
):
    """두 재고 스냅샷(t1→t2) 사이에 품목의 BH재고−OB가용 차이가 왜 벌어졌는지 분해.

    - 스냅샷 변화(ΔBH재고·ΔOB가용·Δ가용외·ΔD)는 저장된 리포트 값에서 정확히 계산.
    - 그 기간 거래 흐름(입고/출고/조정 BH vs OB)은 compare(total)로 근사 분해.
    - ΔD = (입고차) − (출고차) + (조정차) + (가용외변동) + 잔차(기간경계·미기록).
    - include_fm: 그 기간 한쪽에만 기록된 개별 거래(full-match)도 첨부.
    """
    import receiving_db as _db
    import re as _re_dc

    def _nrm(s: str) -> str:
        return _re_dc.sub(r"[\s\-_·•\[\]()（）]", "", str(s or "")).lower()
    def _nrm_d(s: str) -> str:
        s2 = _re_dc.sub(r'[\s\-_]+\d{2,4}[-.]\d{1,2}[-.]\d{1,2}\s*$', '', str(s or ''))
        return _re_dc.sub(r"[\s\-_·•\[\]()（）]", "", s2).lower()

    # 1) 리포트 로드 (t2 미지정 시 최신)
    r1 = _db.get_stock_report(report_t1_id)
    if report_t2_id:
        r2 = _db.get_stock_report(report_t2_id)
    else:
        _reps = _db.list_stock_reports(1)
        r2 = _db.get_stock_report(_reps[0]["id"]) if _reps else {}
    if not r1 or not r2:
        raise HTTPException(404, "비교할 재고 리포트를 찾을 수 없습니다")

    # 2) 두 리포트에서 해당 품목 행 찾기 (이름 정규화 매칭)
    nkey = _nrm(name)
    def _find(rows):
        for row in rows or []:
            if _nrm(row.get("name")) == nkey:
                return row
        return None
    row1 = _find(r1.get("rows", []))
    row2 = _find(r2.get("rows", []))
    if not row1 and not row2:
        raise HTTPException(404, f"'{name}' 품목을 두 리포트에서 찾을 수 없습니다")

    def _g(row, k, default=None):
        return (row or {}).get(k, default)

    t1d, t2d = r1.get("report_date", ""), r2.get("report_date", "")
    bh1, bh2 = _g(row1, "bh_stock"), _g(row2, "bh_stock")
    oba1, oba2 = _g(row1, "ob_stock_available"), _g(row2, "ob_stock_available")
    obt1, obt2 = _g(row1, "ob_stock_total"), _g(row2, "ob_stock_total")
    obu1, obu2 = _g(row1, "ob_unusable") or 0, _g(row2, "ob_unusable") or 0
    # 주지표(총재고 기준)로 통일. 저장된 'diff'는 리포트 시점에 따라 가용/총 기준이 섞일 수 있어
    # bh_stock·ob_total에서 직접 재계산(구 리포트도 일관). 가용 기준은 보조로 별도 계산.
    d1 = (bh1 - obt1) if (bh1 is not None and obt1 is not None) else _g(row1, "diff")
    d2 = (bh2 - obt2) if (bh2 is not None and obt2 is not None) else _g(row2, "diff")
    d1_av = (bh1 - oba1) if (bh1 is not None and oba1 is not None) else None
    d2_av = (bh2 - oba2) if (bh2 is not None and oba2 is not None) else None
    delta_d = (d2 or 0) - (d1 or 0)
    delta_d_av = (d2_av or 0) - (d1_av or 0)
    delta_bh = (bh2 or 0) - (bh1 or 0)
    delta_ob_total = (obt2 or 0) - (obt1 or 0)
    delta_unavail = obu2 - obu1

    # 3) 기간 거래 흐름 분해 (compare total) — 품목의 BH SKU 그룹으로 합산
    bh_skus = set(_g(row2, "bh_skus") or _g(row1, "bh_skus") or [])
    flow = {"bh_in": 0, "bh_out": 0, "bh_move": 0, "bh_adj": 0, "ob_in": 0, "ob_out": 0, "ob_adj": 0}
    cmp_err = None
    try:
        cmp = compare(token=token, from_date=t1d, to_date=t2d, period="month",
                      location_id=None, location_ids=location_ids or None,
                      use_mapping=True, mode="total", by_channel=False,
                      bh_lookback=0, qty_tolerance=0.0, merge_types=False,
                      exclude_adj=False, bh_adj_max_qty=0)
        _seen = set()
        for cr in cmp.get("rows", []):
            sk = str(cr.get("sku") or "")
            matched = (any(s and s in sk for s in bh_skus) if bh_skus
                       else _nrm(cr.get("name")) == nkey)
            if not matched or sk in _seen:
                continue
            _seen.add(sk)
            for k in flow:
                flow[k] += cr.get(k + "_qty", 0) or 0
    except Exception as e:
        cmp_err = str(e)[:80]

    # 4) 차이 변화 분해 (총재고 기준, 스냅샷 기반):
    #    ΔD(총) = ΔBH재고 − ΔOB총재고 = net_stock_flow (입출고·조정 순차이) — 가용외는 총재고에 포함돼 빠짐.
    #    참고로 가용 기준 변화 ΔD(가용) = net_stock_flow + ΔOB가용외 인데, 가용외 변동은
    #    OB 할당/보류 상태 변화(거래 아님)이므로 총재고 기준에선 노이즈로 제거된다.
    net_stock_flow = delta_bh - delta_ob_total       # BH·OB 순재고증감 차이 (입출고/조정 합) = ΔD(총)
    contrib = {
        "net_stock_flow":     net_stock_flow,        # 입·출고·조정의 BH−OB 순차이 (= 총재고 차이 변화)
        "ob_unavail_change":  delta_unavail,         # OB 가용외(할당/보류) 변동 — 가용 기준에만 영향(노이즈)
    }
    explained = net_stock_flow                       # 총재고 기준 차이변화는 순재고흐름이 전부
    residual = delta_d - explained                   # 스냅샷 정합성 체크(보통 ~0)
    # net_stock_flow의 입고/출고 세부는 flow(거래흐름, 근사)로 참고 제공

    # 4-b) 가용외 '환원'량 — 가용외 감소분 중 출고가 아니라 가용으로 되돌아온 양.
    #   ΔOB가용 − ΔOB전체 > 0 이면 전체는 그대로인데 가용만 늘어남 = 가용외→가용 환원(할당/보류 해제).
    delta_ob_avail = (oba2 or 0) - (oba1 or 0)
    unavail_returned = delta_ob_avail - delta_ob_total   # >0: 가용외에서 가용으로 환원된 수량

    # 4-c) BH 선등록(발송예정) — 같은 기간 채널분해로 계산(캐시 재사용). 절대 차이 수준에 기여.
    prebook_bh = 0
    prebook_list: list = []
    try:
        _ob_codes_s = ",".join(str(c) for c in (_g(row2, "ob_codes") or _g(row1, "ob_codes") or []))
        _dec = item_out_decompose(
            token=token, name=name,
            bh_skus=",".join(sorted(str(s) for s in bh_skus)),
            ob_codes=_ob_codes_s, from_date=t1d, to_date=t2d,
            location_ids=location_ids or "228640")
        prebook_bh = _dec.get("prebook_bh", 0) or 0
        prebook_list = _dec.get("prebook", []) or []
    except Exception:
        pass

    # 4-d) 매핑 그룹 구성 변화 — 두 스냅샷의 OB코드/BH SKU 집합이 다르면 같은 '품목명' 행이라도
    #   속을 구성하는 코드가 달라 ob_total/가용/가용외 비교가 왜곡됨(사과↔오렌지). 거래 없이 가용이
    #   변한 '흔적'의 상당수가 실은 이 그룹 재구성에서 옴 → 명시적으로 감지·노출.
    c1 = set(str(x) for x in (_g(row1, "ob_codes") or []))
    c2 = set(str(x) for x in (_g(row2, "ob_codes") or []))
    s1 = set(str(x) for x in (_g(row1, "bh_skus") or []))
    s2 = set(str(x) for x in (_g(row2, "bh_skus") or []))
    group_change = {
        "changed": (c1 != c2) or (s1 != s2),
        "t1_only_codes": sorted(c1 - c2),   # t1에만 있던 OB코드 (t2에서 빠짐 → 그 코드 재고가 비교에서 사라짐)
        "t2_only_codes": sorted(c2 - c1),   # t2에만 있는 OB코드 (새로 합쳐짐)
        "t1_only_skus":  sorted(s1 - s2),
        "t2_only_skus":  sorted(s2 - s1),
    }

    # 5) 개별 거래 (한쪽에만 기록) — full-match
    bh_only, ob_only, qty_diff = [], [], []
    if include_fm:
        try:
            fm = full_match(token=token, from_date=t1d, to_date=t2d,
                            min_score=60, bh_lookback=3, location_ids=location_ids,
                            bh_internal_keywords="일원화,품목통합,기초재고,재고이관,창고이동")
            dkey = _nrm_d(name)
            for x in fm.get("bh_only", []):
                if _nrm_d(x.get("name")) == dkey:
                    # BH 메모·채널을 직배송/누락 판별 단서로 노출 (full_match는 memo 키 사용)
                    bh_only.append({"date": x.get("date"), "qty": x.get("qty"), "bh_type": x.get("bh_type"),
                                    "memo": (x.get("memo") or x.get("ref") or "")[:80],
                                    "channel": x.get("channel") or ""})
            for x in fm.get("ob_only", []):
                if _nrm_d(x.get("name")) == dkey:
                    ob_only.append({"date": x.get("date"), "qty": x.get("qty"), "ob_type": x.get("ob_type"),
                                    "channel": x.get("channel") or x.get("extra") or ""})
            _seen_qd = set()
            for x in fm.get("matched", []):
                if not x.get("qty_diff"):
                    continue
                if _nrm_d(x.get("bh_name")) != dkey and _nrm_d(x.get("ob_name")) != dkey:
                    continue
                key = (x.get("bh_date"), x.get("ob_date"), x.get("bh_qty"), x.get("ob_qty"))
                if key in _seen_qd:
                    continue
                _seen_qd.add(key)
                qty_diff.append({"bh_date": x.get("bh_date"), "ob_date": x.get("ob_date"),
                                 "bh_qty": x.get("bh_qty"), "ob_qty": x.get("ob_qty"),
                                 "qty_diff": x.get("qty_diff"), "day_gap": x.get("day_gap"),
                                 "bh_memo": (x.get("bh_memo") or "")[:80]})
        except Exception:
            pass

    return {
        "name": name,
        "t1": {"date": t1d, "bh_stock": bh1, "ob_available": oba1, "ob_total": obt1, "ob_unusable": obu1, "diff": d1, "diff_vs_available": d1_av},
        "t2": {"date": t2d, "bh_stock": bh2, "ob_available": oba2, "ob_total": obt2, "ob_unusable": obu2, "diff": d2, "diff_vs_available": d2_av},
        "delta_diff": delta_d,
        "delta_diff_available": delta_d_av,     # 참고: 가용 기준 차이 변화 (가용외 노이즈 포함)
        "delta_bh_stock": delta_bh,
        "delta_ob_total": delta_ob_total,
        "delta_ob_available": delta_ob_avail,
        "delta_ob_unusable": delta_unavail,
        "unavail_returned": unavail_returned,   # 가용외→가용 환원 추정량 (거래 아님)
        "prebook_bh": prebook_bh,               # BH 선등록(발송예정) — OB 미반영, 시점차
        "prebook": prebook_list,
        "group_change": group_change,           # 두 스냅샷 매핑 그룹 구성 변화 (비교 왜곡 원인)
        "flow": flow,
        "contrib": contrib,
        "explained": explained,
        "residual": residual,
        "bh_only": bh_only, "ob_only": ob_only, "qty_diff": qty_diff,
        "errors": ([f"기간 거래 분해 실패: {cmp_err}"] if cmp_err else []),
    }


def _ob_rec_prod_nm(rec: dict) -> str:
    """OB 원시 레코드에서 상품명 추출."""
    if not isinstance(rec, dict):
        return ""
    h = rec.get("header", rec)
    for k in ("product_name", "sale_prod_nm", "prod_nm", "item_name"):
        v = str(h.get(k) or "").strip()
        if v and v != "None":
            return html.unescape(v)
    return ""


def _collect_bh_ob_raw(token, from_date, to_date, loc_set, errors, cfg):
    """BH·OB 원시 거래를 수집하고 위치 필터·코드 필터까지 적용해 반환하는 공통 헬퍼."""
    bh_in_raw, bh_out_raw, bh_move_raw, bh_adj_raw = [], [], [], []
    try:
        bh_in_raw   = U.fetch_transactions(token, "in",     from_date, to_date, None)
        bh_out_raw  = U.fetch_transactions(token, "out",    from_date, to_date, None)
        bh_move_raw = U.fetch_transactions(token, "move",   from_date, to_date, None)
        bh_adj_raw  = U.fetch_transactions(token, "adjust", from_date, to_date, None)
        _enrich_bh_items(token, bh_in_raw)
        _enrich_bh_items(token, bh_out_raw)
        _enrich_bh_items(token, bh_move_raw)
        _enrich_bh_items(token, bh_adj_raw, tx_type="adjust")
    except Exception as e:
        errors.append(f"BoxHero 거래 조회 실패: {str(e)[:80]}")

    if loc_set:
        def _filt(txs, prefer_from: bool = False):
            _locs: dict = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as _ex:
                _fs = {_ex.submit(_get_bh_tx_loc_id, token, t["id"], "location", prefer_from): t["id"]
                       for t in txs if t.get("id")}
                for _f in concurrent.futures.as_completed(_fs):
                    try: _locs[_fs[_f]] = _f.result()
                    except Exception: _locs[_fs[_f]] = None
            _flush_tx_file_cache()
            return [t for t in txs if _locs.get(t.get("id")) is None or _locs.get(t.get("id")) in loc_set]
        try:
            bh_in_raw = _filt(bh_in_raw); bh_out_raw = _filt(bh_out_raw)
            # move는 출발지(from) 기준 필터 — 이 위치에서 나간 이동을 출고로 포함
            bh_move_raw = _filt(bh_move_raw, prefer_from=True)
        except Exception as e:
            errors.append(f"[정보] BH 위치 필터 실패(전체 위치로 진행): {str(e)[:50]}")

    ourbox_id, ourbox_pw = cfg.get("ourbox_id"), cfg.get("ourbox_pw")
    ob_in_raw, ob_out_raw, ob_adj_raw = [], [], []
    ob_source = "none"
    if ourbox_id and ourbox_pw:
        ob_ext_from = (datetime.strptime(from_date, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
        ob_ext_to   = (datetime.strptime(to_date,   "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")
        ck = f"{ob_ext_from}|{ob_ext_to}"
        _load_ob_file_cache()
        cached = _get_ob_cache(ck)
        if cached:
            ob_in_raw, ob_out_raw, ob_adj_raw = list(cached["in"]), list(cached["out"]), list(cached["adj"])
            ob_source = cached.get("source", "rest")
        else:
            # 월 단위 청크로 수집 — 완결된 달은 키가 고정돼 영구 캐시 재사용.
            # 긴 기간(6개월·1년)도 미캐시 달(보통 이번 달)만 새로 수집한다.
            chunk_entries: list = []
            failed = False
            fallback_whole = False
            for c_from, c_to in _ob_month_chunks(ob_ext_from, ob_ext_to):
                cck = f"{c_from}|{c_to}"
                centry = _get_ob_cache(cck)
                if centry is None:
                    ci, co, ca = [], [], []
                    _errs: list = []
                    src = _collect_ourbox(ourbox_id, ourbox_pw, c_from, c_to, ci, co, ca, _errs)
                    for m in _errs:
                        # 월별 호출이라 빈 달은 정상 — '데이터 0건' 안내는 청크에선 무시
                        if "데이터 0건" not in m and m not in errors:
                            errors.append(m)
                    if src == "failed":
                        failed = True
                        break
                    if src != "rest":
                        # 스크래핑 fallback은 달마다 브라우저 세션이 떠서 월별 반복이 더 느림 → 전체 범위 일괄 수집
                        fallback_whole = True
                        break
                    centry = {"in": ci, "out": co, "adj": ca, "source": src}
                    try: _save_ob_file_cache_entry(cck, ci, co, ca, src)
                    except Exception: pass
                chunk_entries.append(centry)
            if failed:
                ob_source = "failed"
            elif fallback_whole:
                ob_in_raw, ob_out_raw, ob_adj_raw = [], [], []
                ob_source = _collect_ourbox(ourbox_id, ourbox_pw, ob_ext_from, ob_ext_to, ob_in_raw, ob_out_raw, ob_adj_raw, errors)
                if ob_source != "failed":
                    try: _save_ob_file_cache_entry(ck, ob_in_raw, ob_out_raw, ob_adj_raw, ob_source)
                    except Exception: pass
            else:
                for ce in chunk_entries:
                    ob_in_raw.extend(ce.get("in") or [])
                    ob_out_raw.extend(ce.get("out") or [])
                    ob_adj_raw.extend(ce.get("adj") or [])
                srcs = {ce.get("source", "rest") for ce in chunk_entries}
                ob_source = "rest" if (not srcs or srcs == {"rest"}) else next(iter(srcs - {"rest"}))
    else:
        errors.append("[정보] OurBox 미설정 — BH만 표시")

    ob_in_raw  = [r for r in ob_in_raw  if _ob_rec_prod_cd(r) not in DEPRECATED_OB_CODES]
    ob_in_raw  = _filter_initial_stock(ob_in_raw)
    ob_out_raw = [r for r in ob_out_raw if _ob_rec_prod_cd(r) not in DEPRECATED_OB_CODES]

    return (bh_in_raw, bh_out_raw, bh_move_raw, bh_adj_raw,
            ob_in_raw, ob_out_raw, ob_adj_raw, ob_source)


@router.get("/channel-flow")
def channel_flow(
    token: str = Query(...),
    from_date: str = Query(...),
    to_date: str = Query(...),
    location_ids: str = Query("228640"),
    group_by: str = Query("channel"),
    product_filter: str = Query(""),
):
    """거래처(채널)별 또는 제품별 입·출고·조정 총량을 BH vs OB로 비교.

    group_by='channel' (기본): 채널 단위 집계.
    group_by='product': 제품(상품명) 단위 집계. product_filter로 검색 가능.
    """
    errors: list = []
    loc_set = {int(x) for x in location_ids.split(",") if str(x).strip().isdigit()}
    ob_ch_res, bh_ch_res, ch_mapped = _build_channel_resolvers()
    if group_by == "channel" and not ch_mapped:
        errors.append("[정보] 채널 매핑이 없어 채널이 정확히 묶이지 않습니다 (상품 매핑 → 채널 매핑)")
    cfg = U.load_config()

    (bh_in_raw, bh_out_raw, bh_move_raw, bh_adj_raw,
     ob_in_raw, ob_out_raw, ob_adj_raw, ob_source) = _collect_bh_ob_raw(
        token, from_date, to_date, loc_set, errors, cfg)

    mat: dict = defaultdict(lambda: {"bh_in": 0, "bh_out": 0, "bh_adj": 0, "ob_in": 0, "ob_out": 0, "ob_adj": 0})
    prod_mat: dict = {}  # 채널별 모드에서만 채움 — {channel: {product: {bh_in,...}}}

    # ── OB 헬퍼 (범위·수량) ──
    def _in_range(rec, dks):
        for dk in dks:
            v = str(rec.get(dk) or "")[:10]
            if v and v != "None":
                return from_date <= v <= to_date
        return True
    def _qty(rec, ks):
        for k in ks:
            v = rec.get(k)
            if v not in (None, "", "None"):
                try: return abs(int(float(str(v).replace(",", ""))))
                except Exception: return 0
        return 0

    if group_by == "product":
        # ── 제품별 집계 ──
        # 매핑 그룹으로 BH·OB 상품명 통합
        mg = _build_mapping_groups()
        bh_to_group = mg.get("bh_to_group", {}) if mg else {}
        ob_to_group = mg.get("ob_to_group", {}) if mg else {}
        ob_code_to_group = mg.get("ob_code_to_group", {}) if mg else {}
        group_label = mg.get("group_label", {}) if mg else {}
        group_name = mg.get("group_name", {}) if mg else {}

        def _bh_prod_label(item_name: str) -> str:
            nm = str(item_name or "").strip()
            gid = bh_to_group.get(nm)
            if gid:
                return group_name.get(gid) or group_label.get(gid) or nm
            return nm or "상품미상"

        def _ob_prod_label(rec) -> str:
            nm = _ob_rec_prod_nm(rec)
            cd = _ob_rec_prod_cd(rec)
            gid = ob_code_to_group.get(cd) or ob_to_group.get(nm)
            if gid:
                return group_name.get(gid) or group_label.get(gid) or nm
            return nm or cd or "상품미상"

        pf = product_filter.strip().lower()

        for tx in bh_in_raw:
            for it in tx.get("items", []):
                q = abs(int(it.get("quantity", 0) or 0))
                if q:
                    label = _bh_prod_label(it.get("name", ""))
                    mat[label]["bh_in"] += q
        for tx in (bh_out_raw + bh_move_raw):
            for it in tx.get("items", []):
                q = abs(int(it.get("quantity", 0) or 0))
                if q:
                    label = _bh_prod_label(it.get("name", ""))
                    mat[label]["bh_out"] += q
        for tx in bh_adj_raw:
            for it in tx.get("items", []):
                q_raw = int(it.get("quantity", 0) or 0)
                if q_raw < 0:
                    label = _bh_prod_label(it.get("name", ""))
                    mat[label]["bh_adj"] += abs(q_raw)
        for rec in ob_in_raw:
            if not _in_range(rec, ["input_dt", "input_complete_dt"]): continue
            q = _qty(rec, ["input_qty"])
            if q: mat[_ob_prod_label(rec)]["ob_in"] += q
        for rec in ob_out_raw:
            if not _in_range(rec, ["out_dt", "out_complete_dt"]): continue
            q = _qty(rec, ["out_qty"])
            if not q: continue
            mat[_ob_prod_label(rec)]["ob_out"] += q
        for rec in ob_adj_raw:
            if not _in_range(rec, ["adj_dt", "reg_dt"]): continue
            q = _qty(rec, ["adj_qty"])
            if q: mat[_ob_prod_label(rec)]["ob_adj"] += q

        if pf:
            mat = {k: v for k, v in mat.items() if pf in k.lower()}

    else:
        # ── 채널별 집계 + 채널×제품 하위 집계 ──
        mg = _build_mapping_groups()
        bh_to_group = mg.get("bh_to_group", {}) if mg else {}
        ob_to_group = mg.get("ob_to_group", {}) if mg else {}
        ob_code_to_group = mg.get("ob_code_to_group", {}) if mg else {}
        _group_label = mg.get("group_label", {}) if mg else {}
        _group_name = mg.get("group_name", {}) if mg else {}

        def _bh_prod(item_name: str) -> str:
            nm = str(item_name or "").strip()
            gid = bh_to_group.get(nm)
            if gid:
                return _group_name.get(gid) or _group_label.get(gid) or nm
            return nm or "상품미상"

        def _ob_prod(rec) -> str:
            nm = _ob_rec_prod_nm(rec)
            cd = _ob_rec_prod_cd(rec)
            gid = ob_code_to_group.get(cd) or ob_to_group.get(nm)
            if gid:
                return _group_name.get(gid) or _group_label.get(gid) or nm
            return nm or cd or "상품미상"

        # 채널×제품 이중 키
        prod_mat: dict = defaultdict(lambda: defaultdict(lambda: {"bh_in": 0, "bh_out": 0, "bh_adj": 0, "ob_in": 0, "ob_out": 0, "ob_adj": 0}))

        def _bh_label(tx):
            return (bh_ch_res(tx.get("memo") or "") if bh_ch_res else "") or "채널미상"
        for tx in bh_in_raw:
            ch = _bh_label(tx)
            for it in tx.get("items", []):
                q = abs(int(it.get("quantity", 0) or 0))
                if q:
                    mat[ch]["bh_in"] += q
                    prod_mat[ch][_bh_prod(it.get("name", ""))]["bh_in"] += q
        for tx in (bh_out_raw + bh_move_raw):
            ch = _bh_label(tx)
            for it in tx.get("items", []):
                q = abs(int(it.get("quantity", 0) or 0))
                if q:
                    mat[ch]["bh_out"] += q
                    prod_mat[ch][_bh_prod(it.get("name", ""))]["bh_out"] += q
        for tx in bh_adj_raw:
            ch = _bh_label(tx)
            for it in tx.get("items", []):
                q_raw = int(it.get("quantity", 0) or 0)
                if q_raw < 0:
                    mat[ch]["bh_adj"] += abs(q_raw)
                    prod_mat[ch][_bh_prod(it.get("name", ""))]["bh_adj"] += abs(q_raw)
        def _ob_label(rec):
            raw = _ob_rec_channel(rec)
            return (ob_ch_res(raw) if ob_ch_res else raw) or "채널미상"
        for rec in ob_in_raw:
            if not _in_range(rec, ["input_dt", "input_complete_dt"]): continue
            q = _qty(rec, ["input_qty"])
            if q:
                ch = _ob_label(rec)
                mat[ch]["ob_in"] += q
                prod_mat[ch][_ob_prod(rec)]["ob_in"] += q
        for rec in ob_out_raw:
            if not _in_range(rec, ["out_dt", "out_complete_dt"]): continue
            q = _qty(rec, ["out_qty"])
            if not q: continue
            ch = _ob_label(rec)
            if _ob_rec_channel(rec) in ASSEMBLY_CHANNELS:
                mat[ch]["ob_adj"] += q
                prod_mat[ch][_ob_prod(rec)]["ob_adj"] += q
            else:
                mat[ch]["ob_out"] += q
                prod_mat[ch][_ob_prod(rec)]["ob_out"] += q
        for rec in ob_adj_raw:
            if not _in_range(rec, ["adj_dt", "reg_dt"]): continue
            q = _qty(rec, ["adj_qty"])
            if q:
                ch = _ob_label(rec)
                mat[ch]["ob_adj"] += q
                prod_mat[ch][_ob_prod(rec)]["ob_adj"] += q

    rows = []
    label_key = "product" if group_by == "product" else "channel"
    unknown_label = "상품미상" if group_by == "product" else "채널미상"
    _ord = {"bh_missing": 0, "diff": 1, "ob_bypass": 2, "match": 3, "unknown": 4}

    def _classify(ch_name, m):
        d = m["bh_out"] - m["ob_out"]
        if ch_name == unknown_label:
            return "unknown"
        if m["bh_out"] == 0 and m["ob_out"] > 0:
            return "bh_missing"
        if m["ob_out"] == 0 and m["bh_out"] > 0:
            return "ob_bypass"
        if d == 0:
            return "match"
        return "diff"

    for ch, m in mat.items():
        kind = _classify(ch, m)
        row = {
            "channel": ch, label_key: ch, **m,
            "diff_in":  m["bh_in"]  - m["ob_in"],
            "diff_out": m["bh_out"] - m["ob_out"],
            "diff_adj": m["bh_adj"] - m["ob_adj"],
            "kind":     kind,
        }
        # 채널별 모드일 때 하위 제품 내역 첨부
        if group_by == "channel" and ch in prod_mat:
            prods = []
            pf = product_filter.strip().lower()
            for pname, pm in prod_mat[ch].items():
                if pf and pf not in pname.lower():
                    continue
                pk = _classify(pname, pm)
                prods.append({
                    "product": pname, **pm,
                    "diff_in": pm["bh_in"] - pm["ob_in"],
                    "diff_out": pm["bh_out"] - pm["ob_out"],
                    "diff_adj": pm["bh_adj"] - pm["ob_adj"],
                    "kind": pk,
                })
            prods.sort(key=lambda r: (_ord[r["kind"]], -(abs(r["diff_out"]) + abs(r["diff_in"]))))
            row["products"] = prods
        rows.append(row)

    rows.sort(key=lambda r: (_ord[r["kind"]], -(abs(r["diff_out"]) + abs(r["diff_in"]))))
    return {
        "rows": rows, "from": from_date, "to": to_date,
        "group_by": group_by,
        "channel_mapped": ch_mapped, "ob_source": ob_source, "errors": errors,
    }


@router.get("/item-out-decompose")
def item_out_decompose(
    token: str = Query(...),
    name: str = Query(""),
    bh_skus: str = Query(""),       # 콤마구분 — 이 품목의 BH SKU 그룹
    ob_codes: str = Query(""),      # 콤마구분 — 이 품목의 OB 상품코드 그룹
    from_date: str = Query(...),
    to_date: str = Query(...),
    location_ids: str = Query("228640"),
):
    """단일 품목의 BH 출고를 채널별로 분해해 'OB 경유' vs 'OB 미경유(직배송)'로 분류.

    BH는 모든 출고를 기록하지만 OB(아워박스)는 OB 경유 출고만 기록한다.
    → BH출고 − OB출고 차이가 "OB 미경유 채널(직배송·타물류)"로 설명되는지 자동 판단.
    - ob_bypass(미경유): 해당 채널에 BH 출고는 있으나 OB 출고가 0 → 직배송 추정.
    - 경유(routed): OB에도 출고가 잡힌 채널. 이 안의 BH−OB 차이가 진짜 대사 차이.
    """
    import re as _re_iod
    errors: list = []
    loc_set = {int(x) for x in location_ids.split(",") if str(x).strip().isdigit()}
    ob_ch_res, bh_ch_res, ch_mapped = _build_channel_resolvers()
    cfg = U.load_config()

    def _nrm(s: str) -> str:
        return _re_iod.sub(r"[\s\-_·•\[\]()（）]", "", str(s or "")).lower()

    sku_set = {s.strip() for s in bh_skus.split(",") if s.strip()}
    code_set = {c.strip() for c in ob_codes.split(",") if c.strip()}
    nkey = _nrm(name)

    # ── 상품 매칭(매핑 그룹)을 근거로 BH↔OB를 묶는다 ──
    #   부분문자열 휴리스틱 대신 사용자가 확정한 상품 매칭 그룹으로 판정 →
    #   같은 그룹이면 코드/SKU 문자열이 달라도 매칭, 다른 그룹(예: 번들)이면 substring 비슷해도 제외.
    mg = _build_mapping_groups() or {}
    bh_to_group      = mg.get("bh_to_group", {}) or {}
    ob_code_to_group = mg.get("ob_code_to_group", {}) or {}
    ob_to_group      = mg.get("ob_to_group", {}) or {}
    bh_name_to_group = mg.get("bh_name_to_group", {}) or {}
    target_groups = set()
    for s in sku_set:
        g = bh_to_group.get(s)
        if g: target_groups.add(g)
    for c in code_set:
        g = ob_code_to_group.get(c)
        if g: target_groups.add(g)

    (bh_in_raw, bh_out_raw, bh_move_raw, bh_adj_raw,
     ob_in_raw, ob_out_raw, ob_adj_raw, ob_source) = _collect_bh_ob_raw(
        token, from_date, to_date, loc_set, errors, cfg)

    def _bh_match(it: dict) -> bool:
        sku = str(it.get("sku") or "")
        nm  = it.get("name") or ""
        # 1순위: 상품 매칭 그룹 일치
        if target_groups:
            g = bh_to_group.get(sku) or bh_name_to_group.get(nm)
            if g and g in target_groups:
                return True
        # 2순위: 매핑 누락분 대비 정확 SKU 일치 (substring 금지 — 오매칭 방지)
        if sku_set:
            return sku in sku_set
        # 매핑·SKU 정보 없을 때만 이름 폴백
        return _nrm(nm) == nkey if nkey else False

    def _ob_match(rec: dict) -> bool:
        cd  = _ob_rec_prod_cd(rec)
        onm = _ob_rec_prod_nm(rec)
        if target_groups:
            g = ob_code_to_group.get(cd) or ob_to_group.get(onm)
            if g and g in target_groups:
                return True
        if code_set:
            return cd in code_set
        return _nrm(onm) == nkey if nkey else False

    def _in_range(rec, dks):
        for dk in dks:
            v = str(rec.get(dk) or rec.get("header", {}).get(dk) if isinstance(rec.get("header"), dict) else rec.get(dk) or "")[:10]
            if v and v != "None":
                return from_date <= v <= to_date
        return True
    def _ob_qty(rec):
        h = rec.get("header", rec) if isinstance(rec, dict) else {}
        v = h.get("out_qty")
        if v in (None, "", "None"): return 0
        try: return abs(int(float(str(v).replace(",", ""))))
        except Exception: return 0

    from collections import defaultdict as _dd_iod
    bh_by_ch: dict = _dd_iod(int)
    ob_by_ch: dict = _dd_iod(int)
    future_by_ch: dict = _dd_iod(int)   # 채널별 '발송예정(메모일>조회종료일)' BH 출고
    prebook_list: list = []             # 선등록 거래 상세 [{date, ship_date, channel, qty, memo}]

    def _bh_label(tx):
        return (bh_ch_res(tx.get("memo") or "") if bh_ch_res else "") or "채널미상"
    def _ob_label(rec):
        raw = _ob_rec_channel(rec)
        return (ob_ch_res(raw) if ob_ch_res else raw) or "채널미상"

    def _memo_ship_date(memo: str, tx_date: str):
        """메모에서 발송(예정)일 추출. 'N월M일' 우선, 없으면 'M일'(거래월 기준).
        반환: 'YYYY-MM-DD' 또는 None."""
        try:
            tx_dt = datetime.strptime(str(tx_date)[:10], "%Y-%m-%d")
        except Exception:
            return None
        m = _re_iod.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", memo or "")
        if m:
            mo, dy = int(m.group(1)), int(m.group(2))
            for yr in (tx_dt.year, tx_dt.year + 1):
                try:
                    cand = datetime(yr, mo, dy)
                    if (cand - tx_dt).days >= -300:
                        return cand.strftime("%Y-%m-%d")
                except Exception:
                    pass
            return None
        m2 = _re_iod.search(r"(?<!\d)(\d{1,2})\s*일(?!\s*분)", memo or "")
        if m2:
            dy = int(m2.group(1))
            mo, yr = tx_dt.month, tx_dt.year
            try:
                cand = datetime(yr, mo, dy)
            except Exception:
                return None
            # 메모일이 거래일보다 한참 작으면 익월 발송으로 간주 (예: 거래 6/28 메모 '2일' → 7/2)
            if dy < tx_dt.day - 10:
                mo2, yr2 = (mo + 1, yr) if mo < 12 else (1, yr + 1)
                try: cand = datetime(yr2, mo2, dy)
                except Exception: pass
            return cand.strftime("%Y-%m-%d")
        return None

    # BH 출고(순수 out — move 제외; 표의 BH출고 값과 일치)
    for tx in bh_out_raw:
        ch = _bh_label(tx)
        memo = tx.get("memo") or ""
        tx_date = str(tx.get("transaction_time") or tx.get("created_at") or "")[:10]
        ship = _memo_ship_date(memo, tx_date)
        is_future = bool(ship and ship > to_date)   # 메모 발송일이 조회 종료일보다 미래 → 선등록
        for it in tx.get("items", []):
            if not _bh_match(it):
                continue
            q = abs(int(it.get("quantity", 0) or 0))
            if q:
                bh_by_ch[ch] += q
                if is_future:
                    future_by_ch[ch] += q
                    prebook_list.append({"date": tx_date, "ship_date": ship,
                                         "channel": ch, "qty": q, "memo": memo[:50]})
    # OB 출고 (전산처리용=세트조립은 출고 아님 → 제외)
    for rec in ob_out_raw:
        if _ob_rec_channel(rec) in ASSEMBLY_CHANNELS:
            continue
        if not _ob_match(rec):
            continue
        if not _in_range(rec, ["out_dt", "out_complete_dt"]):
            continue
        q = _ob_qty(rec)
        if q:
            ob_by_ch[_ob_label(rec)] += q

    channels = set(bh_by_ch) | set(ob_by_ch)
    ch_rows = []
    bypass_bh = routed_bh = routed_ob = bh_missing_ob = prebook_bh = 0
    for ch in channels:
        b, o = bh_by_ch.get(ch, 0), ob_by_ch.get(ch, 0)
        # 선등록(발송예정)은 채널 미매칭 초과분 한도 내에서만 인정 (OB가 이미 잡았으면 제외)
        pre = min(future_by_ch.get(ch, 0), max(0, b - o))
        prebook_bh += pre
        if o == 0 and b > 0:
            kind = "ob_bypass"; bypass_bh += b
        elif b == 0 and o > 0:
            kind = "bh_missing"; bh_missing_ob += o
        else:
            kind = "match" if b == o else "diff"
            routed_bh += b; routed_ob += o
        ch_rows.append({"channel": ch, "bh_out": b, "ob_out": o, "diff": b - o,
                        "kind": kind, "prebook": pre})

    _ord = {"ob_bypass": 0, "diff": 1, "bh_missing": 2, "match": 3}
    ch_rows.sort(key=lambda r: (_ord.get(r["kind"], 9), -abs(r["diff"])))
    prebook_list.sort(key=lambda x: (x.get("ship_date") or "", -x.get("qty", 0)))

    bh_total = sum(bh_by_ch.values())
    ob_total = sum(ob_by_ch.values())
    return {
        "name": name, "from": from_date, "to": to_date,
        "bh_out_total": bh_total,
        "ob_out_total": ob_total,
        "diff": bh_total - ob_total,
        "bypass_bh": bypass_bh,            # OB 미경유(직배송) BH 출고 — 차이의 설명분
        "prebook_bh": prebook_bh,          # BH 선등록(발송예정, 메모일>종료일) — OB 미반영, 곧 해소
        "routed_bh": routed_bh,            # OB 경유 채널의 BH 출고
        "routed_ob": routed_ob,            # OB 경유 채널의 OB 출고
        "routed_diff": routed_bh - routed_ob,  # 경유 채널 내 진짜 대사 차이
        "real_diff": (bh_total - ob_total) - bypass_bh - prebook_bh,  # 직배송·선등록 제외한 순수 미스매칭
        "bh_missing_ob": bh_missing_ob,    # BH엔 없고 OB에만 있는 출고
        "channels": ch_rows,
        "prebook": prebook_list,           # 선등록 거래 상세
        "channel_mapped": ch_mapped, "ob_source": ob_source, "errors": errors,
    }


@router.get("/stock-diff-trace")
def stock_diff_trace(
    token: str = Query(...),
    name: str = Query(""),
    bh_skus: str = Query(""),        # 콤마구분 — 이 품목의 BH SKU 그룹
    ob_codes: str = Query(""),       # 콤마구분 — 이 품목의 OB 상품코드 그룹
    from_date: str = Query(...),
    to_date: str = Query(...),
    location_ids: str = Query("228640"),
    bh_stock: Optional[int] = Query(None),   # 현재 BH 재고 (재고현황 행 값)
    ob_total: Optional[int] = Query(None),   # 현재 OB 총재고
    ob_unav: int = Query(0),                 # 현재 OB 가용외
    tol_days: int = Query(3),                # 전표 시점차 허용일
):
    """단일 품목 거래 흐름 정밀 대사 — 재고 차이가 '어느 거래에서' 났는지 분해.

    방법: BH·OB의 입고/출고 이벤트를 수량 기준 ±tol_days로 짝짓고(1:1 + 1:N 조합),
    남는 이벤트를 원인 분류: 교차기록(품목 엇갈림)·선차감(가용외)·기간이전차이·
    기간경계 시점차·한쪽만 기록. 등식: 차이 = 기간이전차이 + Σ(잔여 이벤트 영향).
    """
    import flow_trace as FT
    errors: list = []
    loc_set = {int(x) for x in location_ids.split(",") if str(x).strip().isdigit()}
    cfg = U.load_config()

    sku_set = {s.strip() for s in bh_skus.split(",") if s.strip()}
    code_set = {c.strip() for c in ob_codes.split(",") if c.strip()}
    import re as _re_dt
    def _nrm(s: str) -> str:
        return _re_dt.sub(r"[\s\-_·•\[\]()（）]", "", str(s or "")).lower()
    nkey = _nrm(name)

    # 상품 매칭 그룹 (item_out_decompose와 동일 기준)
    mg = _build_mapping_groups() or {}
    bh_to_group      = mg.get("bh_to_group", {}) or {}
    ob_code_to_group = mg.get("ob_code_to_group", {}) or {}
    ob_to_group      = mg.get("ob_to_group", {}) or {}
    bh_name_to_group = mg.get("bh_name_to_group", {}) or {}
    group_name       = mg.get("group_name", {}) or {}
    group_label      = mg.get("group_label", {}) or {}
    target_groups = set()
    for s in sku_set:
        g = bh_to_group.get(s)
        if g: target_groups.add(g)
    for c in code_set:
        g = ob_code_to_group.get(c)
        if g: target_groups.add(g)

    (bh_in_raw, bh_out_raw, bh_move_raw, bh_adj_raw,
     ob_in_raw, ob_out_raw, ob_adj_raw, ob_source) = _collect_bh_ob_raw(
        token, from_date, to_date, loc_set, errors, cfg)

    def _bh_match(it: dict) -> bool:
        sku = str(it.get("sku") or "")
        nm  = it.get("name") or ""
        if target_groups:
            g = bh_to_group.get(sku) or bh_name_to_group.get(nm)
            if g and g in target_groups:
                return True
        if sku_set:
            return sku in sku_set
        return _nrm(nm) == nkey if nkey else False

    def _ob_match(rec: dict) -> bool:
        cd  = _ob_rec_prod_cd(rec)
        onm = _ob_rec_prod_nm(rec)
        if target_groups:
            g = ob_code_to_group.get(cd) or ob_to_group.get(onm)
            if g and g in target_groups:
                return True
        if code_set:
            return cd in code_set
        return _nrm(onm) == nkey if nkey else False

    # ── 상품 라벨 (교차기록 탐지용 — 전 품목 공통) ──
    def _bh_prod_label(nm: str, sku: str = "") -> str:
        gid = bh_to_group.get(sku) or bh_name_to_group.get(str(nm or "").strip())
        if gid:
            return str(group_name.get(gid) or group_label.get(gid) or nm)
        return str(nm or "상품미상")

    def _ob_prod_label(rec) -> str:
        nm = _ob_rec_prod_nm(rec)
        cd = _ob_rec_prod_cd(rec)
        gid = ob_code_to_group.get(cd) or ob_to_group.get(nm)
        if gid:
            return str(group_name.get(gid) or group_label.get(gid) or nm)
        return str(nm or cd or "상품미상")

    # ── 이벤트 빌드 ──
    def _tx_date(tx) -> str:
        return str(tx.get("transaction_time") or tx.get("created_at") or "")[:10]

    def _ob_date(rec, dks) -> str:
        h = rec.get("header", rec) if isinstance(rec, dict) else {}
        for dk in dks:
            v = str(h.get(dk) or rec.get(dk) or "")[:10]
            if v and v != "None":
                return v
        return ""

    def _ob_qty_k(rec, key) -> int:
        h = rec.get("header", rec) if isinstance(rec, dict) else {}
        v = h.get(key, rec.get(key))
        if v in (None, "", "None"):
            return 0
        try:
            return int(float(str(v).replace(",", "")))
        except Exception:
            return 0

    bh_in_ev, bh_out_ev = [], []
    ob_in_ev, ob_out_ev = [], []
    # 교차기록 탐지용: 전 품목 일자별 BH/OB 입·출고 합
    daily_out: dict = defaultdict(lambda: defaultdict(lambda: {"bh": 0, "ob": 0}))
    daily_in:  dict = defaultdict(lambda: defaultdict(lambda: {"bh": 0, "ob": 0}))

    for tx, ttype, sign in ([(t, "입고", 1) for t in bh_in_raw] +
                            [(t, "출고", -1) for t in bh_out_raw] +
                            [(t, "이동", -1) for t in bh_move_raw] +
                            [(t, "조정", 0) for t in bh_adj_raw]):
        d = _tx_date(tx)
        if not d or not (from_date <= d <= to_date):
            continue
        memo = str(tx.get("memo") or "")[:60]
        for it in tx.get("items", []):
            q = int(it.get("quantity", 0) or 0)
            if not q:
                continue
            eff = q if ttype == "조정" else sign * abs(q)   # 조정은 부호 그대로
            label = _bh_prod_label(it.get("name", ""), str(it.get("sku") or ""))
            (daily_in if eff > 0 else daily_out)[label][d]["bh"] += abs(eff)
            if not _bh_match(it):
                continue
            ev = {"date": d, "qty": abs(eff), "memo": memo, "channel": "", "type": ttype}
            (bh_in_ev if eff > 0 else bh_out_ev).append(ev)

    for rec in ob_in_raw:
        d = _ob_date(rec, ["input_dt", "input_complete_dt"])
        if not d or not (from_date <= d <= to_date):
            continue
        q = abs(_ob_qty_k(rec, "input_qty"))
        if not q:
            continue
        label = _ob_prod_label(rec)
        daily_in[label][d]["ob"] += q
        if _ob_match(rec):
            ob_in_ev.append({"date": d, "qty": q, "memo": "",
                             "channel": _ob_rec_channel(rec), "type": "입고"})
    for rec in ob_out_raw:
        d = _ob_date(rec, ["out_dt", "out_complete_dt"])
        if not d or not (from_date <= d <= to_date):
            continue
        q = abs(_ob_qty_k(rec, "out_qty"))
        if not q:
            continue
        ch = _ob_rec_channel(rec)
        label = _ob_prod_label(rec)
        daily_out[label][d]["ob"] += q
        if _ob_match(rec):
            ob_out_ev.append({"date": d, "qty": q, "memo": "",
                              "channel": ch,
                              "type": "세트" if ch in ASSEMBLY_CHANNELS else "출고"})
    for rec in ob_adj_raw:
        d = _ob_date(rec, ["adj_dt", "reg_dt"])
        if not d or not (from_date <= d <= to_date):
            continue
        q = _ob_qty_k(rec, "adj_qty")
        if not q:
            continue
        label = _ob_prod_label(rec)
        (daily_in if q > 0 else daily_out)[label][d]["ob"] += abs(q)
        if _ob_match(rec):
            ev = {"date": d, "qty": abs(q), "memo": "", "channel": _ob_rec_channel(rec), "type": "조정"}
            (ob_in_ev if q > 0 else ob_out_ev).append(ev)

    # OB 출고는 주문라인 단위(수량 1~2 수천 건)라 그대로 매칭하면 조합 탐색이 폭발.
    # 같은 (일자, 채널, 유형)은 합산 — BH도 일 단위 배치 출고라 매칭 의미는 동일.
    def _agg_ob_ev(evs: list) -> list:
        agg: dict = {}
        for e in evs:
            k = (e["date"], e.get("channel", ""), e.get("type", ""))
            if k in agg:
                agg[k]["qty"] += e["qty"]
                agg[k]["_n"] += 1
            else:
                agg[k] = dict(e)
                agg[k]["_n"] = 1
        out = []
        for e in agg.values():
            n = e.pop("_n", 1)
            if n > 1:
                e["memo"] = f"주문 {n}건 합산"
            out.append(e)
        out.sort(key=lambda x: (x["date"], -x["qty"]))
        return out

    ob_in_ev = _agg_ob_ev(ob_in_ev)
    ob_out_ev = _agg_ob_ev(ob_out_ev)

    diff_now = (bh_stock - ob_total) if (bh_stock is not None and ob_total is not None) else None

    # ── OB 가용외 스냅샷 타임라인 — 할당(가용→가용외) 시점을 일별 변화량으로 ──
    # 선차감 판정을 '추정'→스냅샷 '확인'으로 승격하고, 참고(−가용) 기준 차이를 분해하는 데 사용
    unav_events: list = []
    snap_info = None
    try:
        snaps = stock_snapshots(name=name, codes=ob_codes, limit=2000)
        series = snaps.get("series") or []
        in_range = [s for s in series if from_date <= str(s.get("captured_at") or "")[:10] <= to_date]
        daily_unav: dict = {}
        for s in in_range:
            dv = int(s.get("d_unavail") or 0)
            if dv:
                d = str(s.get("captured_at") or "")[:10]
                daily_unav[d] = daily_unav.get(d, 0) + dv
        unav_events = [{"date": d, "delta": v} for d, v in sorted(daily_unav.items()) if v]
        if in_range:
            snap_info = {
                "from": in_range[0]["captured_at"], "to": in_range[-1]["captured_at"],
                "unav_first": in_range[0]["unavailable"], "unav_last": in_range[-1]["unavailable"],
            }
    except Exception:
        pass

    result = FT.trace_item(
        bh_in_ev, bh_out_ev, ob_in_ev, ob_out_ev,
        diff_now=diff_now, ob_unav=ob_unav,
        from_date=from_date, to_date=to_date, tol_days=tol_days,
        daily_out_by_prod={k: dict(v) for k, v in daily_out.items()},
        daily_in_by_prod={k: dict(v) for k, v in daily_in.items()},
        target_label=name,
        unav_events=unav_events,
    )
    ob_avail = (ob_total - ob_unav) if ob_total is not None else None
    result["avail_basis"] = {
        # 참고(−가용) 기준: BH − OB가용 = (BH − OB총재고) + 가용외
        "diff_avail": (bh_stock - ob_avail) if (bh_stock is not None and ob_avail is not None) else None,
        "ob_unav": ob_unav,
        "unav_events": unav_events,
        "snapshots": snap_info,
    }
    result.update({
        "name": name, "from": from_date, "to": to_date,
        "bh_stock": bh_stock, "ob_total": ob_total, "ob_unav": ob_unav,
        "ob_source": ob_source, "errors": errors,
    })
    return result


@router.get("/mapping-audit")
def mapping_audit(threshold: int = Query(50)):
    """매핑 정합성 검사 — OB 상품명과 BH 상품명 유사도가 낮은(의심) 매핑을 찾아 반환.
    메노포즈↔트윈픽스 같은 오매핑을 사전 탐지.
    """
    import receiving_db as _db
    import re as _re_au
    try:
        from rapidfuzz import fuzz as _fz
        def _sim(a, b): return _fz.token_set_ratio(a, b)
    except ImportError:
        import difflib as _dl
        def _sim(a, b): return _dl.SequenceMatcher(None, a, b).ratio() * 100

    def _core(s: str) -> str:
        s = _re_au.sub(r'-?\d{4}-\d{2}-\d{2}', '', str(s or ''))
        s = _re_au.sub(r'홈쇼핑|\(공통\)|\(CJ\)|\(GS\)|\[.*?\]|★|\(.*?\)', '', s)
        return _re_au.sub(r'[\s_]', '', s)

    suspicious = []
    # product_mapping
    try:
        for p in _db.get_product_mapping_pairs():
            on = str(p.get("ourbox_prod_nm") or "")
            bn = str(p.get("boxhero_item_nm") or p.get("bh_name") or "")
            if not on or not bn: continue
            s = _sim(_core(on), _core(bn))
            if s < threshold:
                suspicious.append({
                    "table": "product_mapping", "score": round(s),
                    "ob_name": on, "bh_name": bn,
                    "ob_code": p.get("ourbox_prod_cd", ""), "bh_sku": p.get("boxhero_sku", ""),
                })
    except Exception:
        pass
    # name_mapping
    try:
        for p in _db.get_name_mapping_pairs():
            on = str(p.get("ob_name") or "")
            bn = str(p.get("bh_name") or "")
            if not on or not bn: continue
            s = _sim(_core(on), _core(bn))
            if s < threshold:
                suspicious.append({
                    "table": "name_mapping", "score": round(s),
                    "ob_name": on, "bh_name": bn,
                    "ob_code": "", "bh_sku": p.get("bh_sku", ""),
                })
    except Exception:
        pass
    suspicious.sort(key=lambda x: x["score"])
    return {"suspicious": suspicious, "count": len(suspicious), "threshold": threshold}


@router.get("/weekly-reports")
def list_weekly_reports(limit: int = Query(52)):
    """저장된 주간 리포트 목록 (요약, 최신순)."""
    import receiving_db as _db
    return {"reports": _db.list_stock_reports(limit)}


@router.get("/weekly-report/{report_id}")
def get_weekly_report(report_id: int):
    """특정 주간 리포트 상세 (전체 행 + 원인분해)."""
    import receiving_db as _db
    r = _db.get_stock_report(report_id)
    if not r:
        raise HTTPException(404, "리포트를 찾을 수 없습니다")
    return r


# ── OB 가용외(할당) 스냅샷 추적 ──────────────────────────────────────────────
def _capture_ob_stock_snapshot(force: bool = False, min_gap_min: int = 90) -> dict:
    """현재 OB 재고(total/available/unavailable)를 품목별로 1회 스냅샷 저장.

    OurBox가 할당 이벤트 로그를 안 주므로, 주기적 스냅샷으로 가용→가용외 전환 시점을 추적.
    force=False면 최근 스냅샷이 min_gap_min분 이내일 때 건너뜀(리로드 중복 방지).
    """
    import receiving_db as _db
    import ourbox_api as api_mod
    from datetime import datetime as _dt, timedelta as _td
    _db.init_db()
    if not force:
        last = _db.last_ob_snapshot_at()
        if last:
            try:
                if _dt.now() - _dt.strptime(last, "%Y-%m-%d %H:%M:%S") < _td(minutes=min_gap_min):
                    return {"saved": False, "skipped": True, "last": last}
            except Exception:
                pass
    cfg = U.load_config()
    client = api_mod.make_client(cfg)
    if not client:
        return {"saved": False, "error": "OurBox 미설정"}
    raw = client.fetch_stock()
    items = []
    for r in raw:
        cd = str(r.get("sales_product_code") or "")
        if not cd:
            continue
        items.append({
            "code": cd,
            "name": (r.get("product_name") or ""),
            "total": int(r.get("total_stock") or 0),
            "available": int(r.get("available_stock") or 0),
            "unavailable": int(r.get("unavailable_stock") or 0),
        })
    ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    n = _db.save_ob_stock_snapshot(ts, items)
    try:
        _db.prune_ob_snapshots(60)
    except Exception:
        pass
    return {"saved": True, "captured_at": ts, "count": n}


@router.post("/capture-stock-snapshot")
def capture_stock_snapshot(force: bool = Query(True)):
    """OB 가용/가용외 스냅샷을 지금 1회 찍어 저장 (수동/스케줄 트리거)."""
    return _capture_ob_stock_snapshot(force=force)


@router.get("/stock-snapshots")
def stock_snapshots(
    name: str = Query("", description="품목명 (상품 매칭 그룹으로 코드 확장)"),
    codes: str = Query("", description="OB 코드 직접 지정 (콤마)"),
    limit: int = Query(2000),
):
    """한 품목의 가용/가용외/전체 스냅샷 시계열 — 상품 매칭 그룹 단위로 합산해 반환.

    가용외(할당)가 언제 뛰는지 = 가용→가용외 할당이 떨어진 시점을 d_unavail로 표시.
    """
    import receiving_db as _db
    import re as _re_wr
    # 상품 매칭 그룹으로 코드 확장 (단일 코드만 보면 그룹 합과 어긋남)
    code_set = {c.strip() for c in codes.split(",") if c.strip()}
    nkey = _re_wr.sub(r"[\s\-_·•\[\]()（）]", "", str(name or "")).lower()
    try:
        mg = _build_mapping_groups() or {}
        oc2g = mg.get("ob_code_to_group", {}) or {}
        o2g = mg.get("ob_to_group", {}) or {}
        gname = mg.get("group_name", {}) or {}
        target_groups = set()
        for c in code_set:
            g = oc2g.get(c)
            if g: target_groups.add(g)
        if name and not target_groups:
            for onm, g in o2g.items():
                if _re_wr.sub(r"[\s\-_·•\[\]()（）]", "", str(onm)).lower() == nkey:
                    target_groups.add(g)
        if target_groups:
            for c, g in oc2g.items():
                if g in target_groups:
                    code_set.add(c)
    except Exception:
        pass

    rows = _db.get_ob_stock_timeline(
        codes=sorted(code_set) if code_set else None,
        name_like=name if (name and not code_set) else "",
        limit=limit,
    )
    # captured_at별 그룹 합산
    from collections import OrderedDict as _OD
    agg: dict = _OD()
    for r in rows:
        t = r["captured_at"]
        a = agg.setdefault(t, {"captured_at": t, "total": 0, "available": 0, "unavailable": 0, "codes": 0})
        a["total"] += r["total"]; a["available"] += r["available"]; a["unavailable"] += r["unavailable"]
        a["codes"] += 1
    series = list(agg.values())
    # 연속 스냅샷 간 변화량 (가용외 할당 시점 식별)
    prev = None
    for s in series:
        if prev is not None:
            s["d_unavail"] = s["unavailable"] - prev["unavailable"]
            s["d_avail"] = s["available"] - prev["available"]
            s["d_total"] = s["total"] - prev["total"]
        else:
            s["d_unavail"] = s["d_avail"] = s["d_total"] = 0
        prev = s
    return {"name": name, "codes": sorted(code_set), "series": series, "count": len(series)}


@router.get("/qty-gap")
def qty_gap(
    token: str = Query(...),
    from_date: str = Query(...),
    to_date: str = Query(...),
    exclude_channels: str = Query("전산처리용,CS출고(파손,누락등),샘플(임박),샘플(정상소비기한)"),
):
    """OB와 BH 출고 총량 차이 — 상품별 전기간 합산 비교.

    BH에 입력해야 할 출고 목록(OB > BH인 상품)을 채널 정보와 함께 반환.
    """
    import ourbox_api as api_mod, re as _re

    cfg = U.load_config()
    excl = {c.strip() for c in exclude_channels.split(",") if c.strip()}

    # 상품 매핑
    try:
        import receiving_db as _db
        nm_pairs = _db.get_name_mapping_pairs()
        ob_to_bh_nm = {m["ob_name"]: m.get("bh_name","") for m in nm_pairs if m.get("bh_name")}
    except Exception:
        ob_to_bh_nm = {}

    # BH 출고 — 상품별 합산 (out + 폐기/소비기한 move)
    try:
        bh_txs = U.fetch_transactions(token, "out", from_date, to_date, None)
        _enrich_bh_items(token, bh_txs)
        # move 거래 중 폐기/소비기한 이동 포함
        bh_move_txs = U.fetch_transactions(token, "move", from_date, to_date, None)
        if bh_move_txs:
            _enrich_bh_items(token, bh_move_txs)
            disposal = [tx for tx in bh_move_txs if any(k in (tx.get("memo") or "")
                        for k in ["폐기","소비기한","미달","유통기한","만료","소진","폐기예정"])]
            bh_txs = bh_txs + disposal
    except Exception as e:
        raise HTTPException(502, f"BoxHero 출고 조회 실패: {e}")

    from collections import defaultdict
    bh_by_prod: dict = defaultdict(int)
    for tx in bh_txs:
        for it in tx.get("items", []):
            nm = U.normalize(str(it.get("name","")))
            bh_by_prod[nm] += abs(int(it.get("quantity",0)))

    # OB 출고 — 상품×채널 합산
    try:
        client = api_mod.make_client(cfg)
        if not client: raise RuntimeError("OurBox API Key 없음")
        ob_raw = client.fetch_outbounds(from_date, to_date)
    except Exception as e:
        raise HTTPException(502, f"OurBox 출고 조회 실패: {e}")

    ob_by_prod: dict = defaultdict(int)
    ob_by_prod_ch: dict = defaultdict(lambda: defaultdict(int))
    for r in ob_raw:
        ch = str(r.get("channel") or r.get("mall_name") or "")
        if ch in excl: continue
        nm_raw = html.unescape(str(r.get("product_name","")).strip())
        mapped = ob_to_bh_nm.get(nm_raw,"")
        nm = U.normalize(mapped or nm_raw)
        qty = abs(int(float(str(r.get("out_qty",0) or 0))))
        dt = str(r.get("out_dt",""))[:10]
        ob_by_prod[nm] += qty
        ob_by_prod_ch[nm][ch] += qty

    # 차이 계산
    all_nm = set(bh_by_prod) | set(ob_by_prod)
    rows = []
    for nm in all_nm:
        bq = bh_by_prod[nm]; oq = ob_by_prod[nm]
        diff = oq - bq  # OB - BH (양수 = BH 미입력)
        chs = dict(sorted(ob_by_prod_ch[nm].items(), key=lambda x: -x[1]))
        rows.append({
            "name": nm, "bh_qty": bq, "ob_qty": oq, "diff": diff,
            "channels": chs,
            "top_channel": max(chs, key=chs.get) if chs else "",
            "status": "ok" if diff == 0 else ("bh_missing" if diff > 0 else "bh_excess"),
        })

    rows.sort(key=lambda x: -x["diff"])  # 차이 큰 순 (BH 미입력이 상위)
    bh_missing = [r for r in rows if r["diff"] > 0]
    ok = [r for r in rows if r["diff"] == 0]
    bh_excess = [r for r in rows if r["diff"] < 0]

    total_bh = sum(bh_by_prod.values())
    total_ob = sum(ob_by_prod.values())

    return {
        "from_date": from_date, "to_date": to_date,
        "excluded_channels": list(excl),
        "total_bh": total_bh, "total_ob": total_ob,
        "total_gap": total_ob - total_bh,
        "bh_match_rate": round(min(total_bh, total_ob) / max(total_ob, 1) * 100, 1),
        "ok_count": len(ok), "bh_missing_count": len(bh_missing), "bh_excess_count": len(bh_excess),
        "bh_missing_qty": sum(r["diff"] for r in bh_missing),
        "rows": rows,
        "bh_missing": bh_missing,
        "ok": ok,
        "bh_excess": bh_excess,
    }


@router.get("/set-bom")
def get_set_bom():
    """세트 BOM 구성표 조회."""
    import receiving_db as _rdb
    return _rdb.get_set_boms()


@router.post("/set-bom")
def create_set_bom(body: dict):
    """세트 BOM 등록/수정."""
    import receiving_db as _rdb
    _rdb.upsert_set_bom(
        set_sku=body.get("set_sku", ""),
        set_name=body.get("set_name", ""),
        component_sku=body.get("component_sku", ""),
        component_name=body.get("component_name", ""),
        qty_per_set=float(body.get("qty_per_set", 1)),
        note=body.get("note", ""),
    )
    return {"ok": True}


@router.delete("/set-bom/{bom_id}")
def delete_set_bom(bom_id: int):
    """세트 BOM 항목 삭제."""
    import receiving_db as _rdb
    _rdb.delete_set_bom(bom_id)
    return {"ok": True}


# ── 행 단위 정리(전산정리) 상태 ──────────────────────────────────────
def _recon_row_key(tx_type: str, sku: str, channel: str, period: str) -> str:
    """대사 행 고유 키 — 프론트/백엔드 동일 규칙 유지."""
    return f"{tx_type}|{sku}|{channel or ''}|{period}"


@router.get("/status")
def list_reconcile_status(from_period: str = Query(""), to_period: str = Query("")):
    """기간 내 행 정리 상태 목록."""
    import receiving_db as _rdb
    return {"items": _rdb.get_reconcile_statuses(from_period, to_period)}


@router.post("/status")
def set_reconcile_status(body: dict):
    """대사 행 정리 상태/메모 저장.

    body: {tx_type, sku, channel, period, status, root_cause?, name?,
           bh_qty?, ob_qty?, memo?, assignee?}
    status: reviewing/resolved/hold/ignore
    """
    import receiving_db as _rdb
    tx_type = str(body.get("tx_type", ""))
    sku     = str(body.get("sku", ""))
    channel = str(body.get("channel", "") or "")
    period  = str(body.get("period", ""))
    status  = str(body.get("status", "reviewing"))
    if not (tx_type and sku and period):
        raise HTTPException(400, "tx_type, sku, period는 필수입니다")
    row_key = _recon_row_key(tx_type, sku, channel, period)
    try:
        rec = _rdb.upsert_reconcile_status(
            row_key, status,
            tx_type=tx_type, sku=sku, name=str(body.get("name", "")),
            channel=channel, period=period,
            root_cause=str(body.get("root_cause", "")),
            bh_qty=body.get("bh_qty"), ob_qty=body.get("ob_qty"),
            memo=str(body.get("memo", "")), assignee=str(body.get("assignee", "")),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "record": rec}


@router.delete("/status")
def clear_reconcile_status(tx_type: str = Query(...), sku: str = Query(...),
                           period: str = Query(...), channel: str = Query("")):
    """정리 상태 삭제 (미처리로 되돌림)."""
    import receiving_db as _rdb
    _rdb.delete_reconcile_status(_recon_row_key(tx_type, sku, channel, period))
    return {"ok": True}


@router.get("/full-match")
def full_match(
    token: str = Query(...),
    from_date: str = Query(...),
    to_date: str = Query(...),
    bh_lookback: int = Query(5),    # BH ±일수 확장 탐색 (기본 5일)
    min_score: float = Query(50),   # 최소 매칭 점수
    location_ids: str = Query(""),  # BH 위치 필터 (콤마구분) — OB는 호법센터만 있으므로 BH도 맞춰야 함
    bh_internal_keywords: str = Query("일원화,품목통합,기초재고,재고이관,창고이동"),  # BH 내부 전산정리 메모 키워드 (adjust/move 제외용)
):
    """전체 유형 통합 매칭 — BH(in+out+move) ↔ OB(입고+출고+조정) 전체를 풀에 놓고 최적 매칭.

    유형 사전 필터 없음. 품목명(45%)+수량(45%)+날짜(10%)+힌트 보너스로 최적 쌍 탐색.
    같은 방향(입↔입, 출↔출) 우선, 반대 방향도 고점수면 허용(반품/전산처리 케이스).
    결과는 5분간 캐시됩니다.
    """
    import ourbox_api as api_mod, re as _re
    from rapidfuzz import fuzz as _fuzz
    from datetime import timedelta as _td
    import datetime as _dt_mod

    # ── 캐시 확인 (메모리 30분 + 파일 캐시) ──────────────────────────
    _FM_LOGIC_VER = "v9"  # 매칭 로직 변경 시 증가 → stale 캐시 자동 무효화 (v9: 날짜 하드상한10일+방향가드+다른SKU차단)
    # 월간+(15일 초과) 조회는 BH 범위가 ±7일 고정이라 lookback이 결과에 영향 없음
    # → 키에 실효값 사용: 슬라이더 변경으로 인한 무의미한 전체 재계산 방지
    _span_days = (datetime.strptime(to_date, "%Y-%m-%d") - datetime.strptime(from_date, "%Y-%m-%d")).days
    _eff_lookback = 7 if _span_days > 14 else bh_lookback
    _loc_id_list = [s.strip() for s in location_ids.split(",") if s.strip()]
    _loc_key = ",".join(sorted(_loc_id_list))
    _cache_key = f"{_FM_LOGIC_VER}|{token[:8]}|{from_date}|{to_date}|{_eff_lookback}|{min_score}|{_loc_key}"
    _FM_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "full_match_cache.json")

    def _load_fm_file_cache() -> Optional[dict]:
        try:
            if not os.path.exists(_FM_CACHE_PATH):
                return None
            with open(_FM_CACHE_PATH, "r", encoding="utf-8-sig") as f:
                _fc = json.load(f)
            entry = _fc.get(_cache_key)
            if not entry:
                return None
            today = _dt_mod.datetime.now().strftime("%Y-%m-%d")
            ts = _dt_mod.datetime.fromisoformat(entry["ts"])
            age_h = (_dt_mod.datetime.now() - ts).total_seconds() / 3600
            # 과거 기간: 24시간 TTL / 오늘 포함: 1시간 TTL
            ttl = 24 if to_date < today else 1
            if age_h < ttl:
                return entry["result"]
        except Exception:
            pass
        return None

    def _save_fm_file_cache(result: dict):
        try:
            os.makedirs(os.path.dirname(_FM_CACHE_PATH), exist_ok=True)
            _fc: dict = {}
            if os.path.exists(_FM_CACHE_PATH):
                try:
                    with open(_FM_CACHE_PATH, "r", encoding="utf-8-sig") as f:
                        _fc = json.load(f)
                except Exception:
                    _fc = {}  # 손상/BOM 파일이어도 저장은 진행
            _fc[_cache_key] = {"result": result, "ts": _dt_mod.datetime.now().isoformat()}
            # 캐시 파일 크기 제한: 키 50개 초과 시 오래된 것 제거
            if len(_fc) > 50:
                oldest = sorted(_fc.items(), key=lambda x: x[1].get("ts",""))[:len(_fc)-40]
                for k, _ in oldest:
                    del _fc[k]
            with open(_FM_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(_fc, f, ensure_ascii=False)
        except Exception:
            pass

    # 1. 메모리 캐시 (30분)
    _cached = state.full_match_cache.get(_cache_key)
    if _cached and (_dt_mod.datetime.now() - _cached["ts"]).total_seconds() < 1800:
        return _cached["result"]
    # 2. 파일 캐시
    _fm_file_result = _load_fm_file_cache()
    if _fm_file_result is not None:
        state.full_match_cache[_cache_key] = {"result": _fm_file_result, "ts": _dt_mod.datetime.now()}
        return _fm_file_result

    cfg = U.load_config()

    # ── 상품 매핑 ─────────────────────────────────────────────────
    try:
        import receiving_db as _db
        nm_pairs = _db.get_name_mapping_pairs()
        ob_to_norm = {m["ob_name"]: U.normalize(m.get("bh_name") or m["ob_name"])
                      for m in nm_pairs if m.get("bh_name")}
    except Exception:
        nm_pairs = []
        ob_to_norm = {}
    # product_mapping: 코드 직접 매칭용 (SKU 일치 = 날짜·유형 초월 매칭 근거)
    try:
        import receiving_db as _db_pm
        pm_pairs = _db_pm.get_product_mapping_pairs()
    except Exception:
        pm_pairs = []
    fm_ob_code_to_sku: dict = {p["ob_prod_cd"]: p["bh_sku"] for p in pm_pairs if p.get("ob_prod_cd") and p.get("bh_sku")}
    fm_ob_name_to_sku: dict = {p["ob_name"]: p["bh_sku"] for p in pm_pairs if p.get("ob_name") and p.get("bh_sku")}
    fm_bh_name_to_sku: dict = {p["bh_name"]: p["bh_sku"] for p in pm_pairs if p.get("bh_name") and p.get("bh_sku")}
    for m in nm_pairs:
        if m.get("ob_name") and m.get("bh_sku"):
            fm_ob_name_to_sku.setdefault(m["ob_name"], m["bh_sku"])
    fm_sku_values: set = set(fm_ob_code_to_sku.values())

    def _bh_map_sku(raw_sku: str, nm: str) -> str:
        """BH 아이템 → canonical SKU (매핑 등록 SKU 우선, 이름 폴백)."""
        raw_sku = (raw_sku or "").strip()
        if raw_sku in fm_sku_values:
            return raw_sku
        return fm_bh_name_to_sku.get(nm) or raw_sku

    def _ob_map_sku_fm(code: str, nm: str) -> str:
        """OB 코드/이름 → canonical BH SKU."""
        code = (code or "").strip()
        sku = fm_ob_code_to_sku.get(code)
        if not sku and code in fm_sku_values:
            sku = code  # OB 코드가 BH SKU와 동일 바코드인 케이스
        if not sku:
            sku = fm_ob_name_to_sku.get(nm) or fm_ob_name_to_sku.get(html.unescape(nm or ""))
        return sku or ""

    def _n_bh(s): return U.normalize(s or "")
    def _n_ob(s): return ob_to_norm.get(html.unescape(s or "").strip()) or U.normalize(s or "")

    # ── BH 전체 수집 (in + out + move, ±lookback일 확장) ──────────
    # 월간+ 쿼리는 BH를 원래 기간만 조회 (API 한계)
    # 이후 미매칭 OB 건에 대해서만 확장 탐색
    date_span = (datetime.strptime(to_date,"%Y-%m-%d") - datetime.strptime(from_date,"%Y-%m-%d")).days
    if date_span > 14:
        # 주간/월간: BH는 원래 기간 + 여유 7일만 (API 과부하 방지)
        bh_fr = (datetime.strptime(from_date,"%Y-%m-%d")-_td(days=7)).strftime("%Y-%m-%d")
        bh_to = (datetime.strptime(to_date,"%Y-%m-%d")+_td(days=7)).strftime("%Y-%m-%d")
    else:
        # 일/주간: lookback 전체 적용
        bh_fr = (datetime.strptime(from_date,"%Y-%m-%d")-_td(days=bh_lookback)).strftime("%Y-%m-%d")
        bh_to = (datetime.strptime(to_date,"%Y-%m-%d")+_td(days=bh_lookback)).strftime("%Y-%m-%d")

    bh_flat: list = []
    _bh_stats: dict = {}  # 타입별 TX 수, items 수 추적
    # BH 내부 전산정리 (일원화 등) — 매칭 풀에서 제외하고 별도 보고
    _BH_INTERNAL_KW = [k.strip() for k in bh_internal_keywords.split(",") if k.strip()]
    bh_internal: list = []
    # BH 부자재/포장재 — OB 미관리 품목이므로 매칭 풀에서 제외하고 별도 보고
    _BH_EXCL_NAMES = ["기타부자재", "부자재", "포장재", "박스", "테이프", "택배비", "단상자", "(원료)"]
    def _is_bh_excl_name(nm: str) -> bool:
        return any(k in nm for k in _BH_EXCL_NAMES)
    bh_excluded: list = []

    # ── 스마트스토어 판별 헬퍼 (TX 루프 이전에 정의) ──────────────────
    # BH memo: "# 스마트 스토어 (이알하나) 5월 18일" 등 공백 있는 형태
    _SS_BH_KEYWORDS = ("스마트스토어", "스마트 스토어", "smartstore", "네이버쇼핑", "naver쇼핑")
    def _is_ss_memo(memo_str: str) -> bool:
        ml = memo_str.lower()
        return any(k in ml for k in _SS_BH_KEYWORDS)

    def _extract_memo_date(memo_str: str, tx_year: int) -> Optional[str]:
        """BH memo에서 'N월 M일' 패턴 추출 → YYYY-MM-DD 반환. 실패 시 None."""
        m = re.search(r'(\d{1,2})월\s*(\d{1,2})일', memo_str)
        if not m:
            return None
        try:
            month, day = int(m.group(1)), int(m.group(2))
            if month > 12 or day > 31:
                return None
            return f"{tx_year:04d}-{month:02d}-{day:02d}"
        except Exception:
            return None

    for tx_type in ("in","out","move","adjust"):
        _bh_agg: dict = {}  # (norm_nm, date_str, is_pos, tx_type) → 집계 dict (in/out/move용)
        _ss_bh_daily: dict = {}  # 스마트스토어 날짜별 합계: {memo_date → {qty, memos}}
        try:
            txs = U.fetch_transactions(token, tx_type, bh_fr, bh_to, None)
            if tx_type not in ("adjust",):
                _enrich_bh_items(token, txs)
                # 위치 필터: BoxHero 목록 API는 location_id 파라미터를 무시함 (실측 확인)
                # → TX 상세의 to/from_location으로 수집 후 필터 (OB=호법센터에 맞춤)
                if _loc_id_list:
                    _loc_ids_int = {int(x) for x in _loc_id_list if str(x).isdigit()}
                    import concurrent.futures as _cf_loc
                    _tx_locs: dict = {}
                    with _cf_loc.ThreadPoolExecutor(max_workers=3) as _ex_loc:
                        # move는 출발지(from) 기준 — 이 위치發 이동을 놓치지 않도록
                        _futs = {_ex_loc.submit(_get_bh_tx_loc_id, token, t["id"], "location",
                                                tx_type == "move"): t["id"]
                                 for t in txs if t.get("id")}
                        for _f in _cf_loc.as_completed(_futs):
                            try:
                                _tx_locs[_futs[_f]] = _f.result()
                            except Exception:
                                _tx_locs[_futs[_f]] = None
                    # 위치 미상(None)은 보수적으로 포함, 명확히 다른 위치만 제외
                    txs = [t for t in txs
                           if _tx_locs.get(t.get("id")) is None
                           or _tx_locs.get(t.get("id")) in _loc_ids_int]
            else:
                # adjust 상세 조회 — 파일 캐시 우선 (_fetch_bh_tx_items), 미스 시에만 API
                # 캐시 적중 시 sleep 없이 즉시 → 재실행 속도 대폭 개선
                for _tx in txs:
                    try:
                        _tx["items"] = _fetch_bh_tx_items(token, _tx["id"], "adjust")
                    except Exception:
                        _tx["items"] = []
        except Exception:
            continue
        for tx in txs:
            tx_time = tx.get("transaction_time") or tx.get("created_at","")
            try: dt = datetime.fromisoformat(tx_time[:19])
            except: continue
            memo = tx.get("memo") or ""
            p = tx.get("partner") or {}
            partner = p.get("name","") if isinstance(p,dict) else ""
            sno_m = _re.search(r"입고번호[:\s]*(\d+)", memo)
            put_sno = sno_m.group(1) if sno_m else ""
            is_adj = any(k in memo for k in ["조정","잉여","잉여분","재고조사","보정","전산"])
            date_str = dt.strftime("%Y-%m-%d")
            # BH 내부 전산정리 TX (일원화·품목통합·기초재고·이관 등):
            # 품목을 합치거나 옮기는 BH 장부 작업 — OB에 대응 거래가 없는 게 정상 → 매칭 풀 제외
            if tx_type in ("adjust", "move") and any(k in memo for k in _BH_INTERNAL_KW):
                _tx_qty_int = sum(abs(int(it.get("quantity", 0))) for it in tx.get("items", []))
                bh_internal.append({
                    "date": date_str, "bh_type": tx_type, "memo": memo[:80],
                    "qty": _tx_qty_int,
                    "items": [{"name": str(it.get("name") or "")[:40],
                               "qty": int(it.get("quantity", 0))}
                              for it in tx.get("items", [])][:30],
                })
                continue
            # 스마트스토어 out TX: TX 레벨에서 직접 날짜별 합산
            # (품목별 _bh_agg 집계를 거치면 같은 처리일에 다날짜 TX가 합산되는 버그 발생)
            if tx_type == "out" and _is_ss_memo(memo):
                tx_year = dt.year
                memo_date = _extract_memo_date(memo, tx_year)
                dt_k = memo_date if memo_date else date_str
                tx_items = tx.get("items", [])
                tx_qty = sum(abs(int(it.get("quantity", 0))) for it in tx_items if it.get("quantity"))
                if tx_qty > 0:
                    if dt_k not in _ss_bh_daily:
                        _ss_bh_daily[dt_k] = {"qty": 0, "memos": []}
                    _ss_bh_daily[dt_k]["qty"] += tx_qty
                    _ss_bh_daily[dt_k]["memos"].append(memo)
                continue  # items 루프 건너뜀 (개별 품목은 _bh_agg에 추가 안 함)

            for it in tx.get("items",[]):
                nm = str(it.get("name") or "")
                raw_qty = int(it.get("quantity",0))
                qty = abs(raw_qty)
                if qty == 0: continue
                # 부자재/포장재: 매칭 대상 아님 → 별도 목록으로 분리 (분모 제외)
                if _is_bh_excl_name(nm):
                    bh_excluded.append({
                        "date": date_str, "bh_type": tx_type,
                        "name": nm, "qty": qty, "memo": memo[:60],
                    })
                    continue
                map_sku = _bh_map_sku(str(it.get("sku") or ""), nm)
                # adjust는 qty 부호가 방향을 나타냄 (+는 입고성, -는 출고성)
                if tx_type == "adjust":
                    is_pos = raw_qty > 0
                else:
                    is_pos = tx_type == "in"

                if tx_type == "adjust":
                    # adjust: 건별 개별 추가 (각 조정 건을 개별 매칭)
                    bh_flat.append({
                        "id": f"bh_{len(bh_flat)}",
                        "side": "bh", "bh_type": tx_type,
                        "date": date_str,
                        "name": nm, "norm": _n_bh(nm), "qty": qty,
                        "memo": memo, "partner": partner, "put_sno": put_sno,
                        "is_positive": is_pos,
                        "is_adj": True,
                        "raw_qty": raw_qty,
                        "map_sku": map_sku,
                    })
                else:
                    # in/out/move: (품목명, 날짜, 방향, 타입) 기준으로 집계
                    # OB 출고가 (품목명, 날짜) 합산인 것과 동일 레벨 비교
                    k = (_n_bh(nm), date_str, is_pos, tx_type)
                    if k not in _bh_agg:
                        _bh_agg[k] = {
                            "name": nm, "norm": _n_bh(nm), "qty": 0,
                            "memos": [], "partners": set(), "put_snos": [],
                            "is_adj": is_adj, "bh_type": tx_type,
                            "date": date_str, "is_positive": is_pos,
                            "map_sku": map_sku,
                        }
                    _bh_agg[k]["qty"] += qty
                    if memo: _bh_agg[k]["memos"].append(memo)
                    if partner: _bh_agg[k]["partners"].add(partner)
                    if put_sno: _bh_agg[k]["put_snos"].append(put_sno)

        # 집계된 in/out/move 항목들을 bh_flat에 추가 (adjust는 위에서 이미 추가됨)
        # 스마트스토어 out TX는 이미 TX 루프에서 _ss_bh_daily에 누적됨 (위 continue)
        for v in _bh_agg.values():
            bh_flat.append({
                "id": f"bh_{len(bh_flat)}",
                "side": "bh", "bh_type": v["bh_type"],
                "date": v["date"],
                "name": v["name"], "norm": v["norm"], "qty": v["qty"],
                "memo": v["memos"][0][:80] if v["memos"] else "",
                "partner": ",".join(sorted(v["partners"]))[:60],
                "put_sno": ",".join(v["put_snos"])[:40],
                "is_positive": v["is_positive"],
                "is_adj": v["is_adj"],
                "raw_qty": v["qty"],
                "is_smartstore": False,
                "is_ss_agg": False,
                "map_sku": v.get("map_sku", ""),
            })

        # 스마트스토어 날짜 합계 → 별도 bh_flat 항목 (OB 날짜 합계와 1:1 매칭용)
        # date = memo에서 추출한 실제 출고일 (예: "5월 18일" → "2026-05-18")
        for dt_k, sv in _ss_bh_daily.items():
            bh_flat.append({
                "id": f"bh_{len(bh_flat)}",
                "side": "bh", "bh_type": "out",
                "date": dt_k,
                "name": "스마트스토어 출고 합계", "norm": "스마트스토어출고합계",
                "qty": sv["qty"],
                "memo": sv["memos"][0][:80] if sv["memos"] else "스마트스토어",
                "partner": "", "put_sno": "",
                "is_positive": False, "is_adj": False, "raw_qty": sv["qty"],
                "is_smartstore": True, "is_ss_agg": True,  # ← 날짜 합계 행
            })

        # 타입별 수집 현황 기록
        _bh_stats[tx_type] = {
            "tx_count": len(txs),
            "item_count": sum(len(tx.get("items", [])) for tx in txs),
        }

    # ── OB 전체 수집 (입고 + 출고 전채널 + 조정) ─────────────────
    try:
        client = api_mod.make_client(cfg)
        if not client: raise RuntimeError("OurBox API Key 없음")
    except Exception as e:
        return {"error": str(e), "matched":[], "bh_only":bh_flat, "ob_only":[]}

    ob_flat: list = []

    # BH 미관리(매칭 분모 제외) 판정 헬퍼
    _EXCL_NAMES = ["기타부자재", "부자재", "포장재", "박스", "테이프", "택배비", "단상자", "(원료)"]
    _EXCL_CHANNELS = ["샘플(정상소비기한)", "샘플(임박)", "샘플", "CS출고", "폐기"]
    def _is_excluded(nm: str, ch: str) -> bool:
        if any(k in nm for k in _EXCL_NAMES): return True
        if ch and any(k in ch for k in _EXCL_CHANNELS): return True
        return False

    # OB 조회도 BH와 동일하게 ±lookback 확장 범위 사용
    # 이유: "OB 먼저 전산처리 → BH 며칠 후 처리" 패턴에서 날짜가 달라도 매칭 가능하게
    ob_fr = bh_fr
    ob_to = bh_to

    # ── OB 거래 수집: ob_txs_cache 공유 (compare/주간리포트가 채운 캐시 재사용 → 1분 내 응답) ──
    _ob_ck = f"{ob_fr}|{ob_to}"
    _load_ob_file_cache()
    _obc = _get_ob_cache(_ob_ck)
    if _obc:
        _inbounds = _obc["in"]; _ob_outbounds_cached = _obc["out"]; _ob_adj_cached = _obc["adj"]
    else:
        _inbounds = client.fetch_inbounds(ob_fr, ob_to)
        _ob_outbounds_cached = client.fetch_outbounds(ob_fr, ob_to)
        _ob_adj_cached = list(client.fetch_adjustments(ob_fr, ob_to))
        try:
            _save_ob_file_cache_entry(_ob_ck, _inbounds, _ob_outbounds_cached, _ob_adj_cached, "rest")
        except Exception:
            pass
    _BULK_INIT_THRESHOLD = 20  # 한 입고번호에 20품목 이상 = 기초재고 이관/재고 재구축
    _sno_count: dict = {}
    for rec in _inbounds:
        sno = str(rec.get("input_code") or "")
        if sno: _sno_count[sno] = _sno_count.get(sno, 0) + 1

    # OB 입고: (품목명, 날짜)별로 그룹화 후 합산 → BH 일별 합산과 1:1 매칭 가능
    # (BH IN은 _bh_agg에서 이미 날짜별로 취합됨)
    from collections import defaultdict as _dd_in
    _in_agg: dict = _dd_in(lambda: {"name":"","norm":"","qty":0,"snos":[],"purch":"","is_bulk":False,"count":0})
    for rec in _inbounds:
        nm = html.unescape(str(rec.get("product_name","")).strip())
        qty = abs(int(float(str(rec.get("input_qty") or 0))))
        dt = str(rec.get("input_dt") or rec.get("input_complete_dt",""))[:10]
        if not nm or qty==0 or not dt: continue
        sno = str(rec.get("input_code") or "")
        is_bulk_init = sno and _sno_count.get(sno, 0) >= _BULK_INIT_THRESHOLD
        k = (_n_ob(nm), dt)
        _in_agg[k]["name"] = nm
        _in_agg[k]["norm"] = _n_ob(nm)
        if not _in_agg[k].get("map_sku"):
            _in_agg[k]["map_sku"] = _ob_map_sku_fm(str(rec.get("product_code") or rec.get("prod_cd") or ""), nm)
        _in_agg[k]["qty"] += qty
        _in_agg[k]["count"] += 1
        if sno and sno not in _in_agg[k]["snos"]:
            _in_agg[k]["snos"].append(sno)
        if not _in_agg[k]["purch"]:
            _in_agg[k]["purch"] = str(rec.get("purch_company") or "")
        if is_bulk_init:
            _in_agg[k]["is_bulk"] = True

    for (norm_nm, dt), v in _in_agg.items():
        snos = v["snos"]
        sno_str = snos[0] if len(snos) == 1 else f"{snos[0]}외{len(snos)-1}건" if snos else ""
        ob_flat.append({
            "id": f"ob_{len(ob_flat)}",
            "side": "ob", "ob_type": "in",
            "date": dt, "name": v["name"], "norm": v["norm"], "qty": v["qty"],
            "channel": "", "put_sno": sno_str,
            "purch": v["purch"],
            "is_positive": True,
            "is_excluded": _is_excluded(v["name"], ""),
            "is_bulk_init": v["is_bulk"],
            "ob_group_count": v["count"],  # 그룹화된 원본 건수
            "map_sku": v.get("map_sku", ""),
        })

    # OB 출고 (전채널, 집계)
    # 스마트스토어 판별: 채널명에 아래 키워드 포함 시 날짜 합계 방식으로 별도 집계
    _SS_KEYWORDS = ("스마트스토어", "smartstore", "네이버쇼핑", "naver")
    def _is_smartstore_ch(ch: str) -> bool:
        ch_l = ch.lower()
        return any(k in ch_l for k in _SS_KEYWORDS)

    from collections import defaultdict as _dd2
    out_agg   = _dd2(lambda: {"qty":0,"name":"","norm":"","channels":set()})  # 일반 채널: (norm_nm, dt)
    ss_dt_agg = _dd2(lambda: {"qty":0,"channels":set()})                      # 스마트스토어: (dt,)

    _ob_outbounds = _ob_outbounds_cached
    for rec in _ob_outbounds:
        nm = html.unescape(str(rec.get("product_name","")).strip())
        qty = abs(int(float(str(rec.get("out_qty") or 0))))
        dt = str(rec.get("out_dt",""))[:10]
        ch = str(rec.get("channel") or rec.get("mall_name") or "")
        if not nm or qty==0 or not dt: continue
        if _is_smartstore_ch(ch):
            # 스마트스토어: 날짜별 합계만 집계 (상품 구분 없이 전체)
            ss_dt_agg[dt]["qty"] += qty
            ss_dt_agg[dt]["channels"].add(ch)
        else:
            k = (_n_ob(nm), dt)
            out_agg[k]["qty"] += qty
            out_agg[k]["name"] = nm
            out_agg[k]["norm"] = _n_ob(nm)
            out_agg[k]["channels"].add(ch)
            if not out_agg[k].get("map_sku"):
                out_agg[k]["map_sku"] = _ob_map_sku_fm(str(rec.get("product_code") or rec.get("prod_cd") or ""), nm)

    # 일반 채널 출고 → ob_flat
    for (norm_nm, dt), v in out_agg.items():
        ch_str = ",".join(sorted(v["channels"]))[:50]
        all_excl = all(_is_excluded("", c) for c in v["channels"]) if v["channels"] else False
        ob_flat.append({
            "id": f"ob_{len(ob_flat)}",
            "side": "ob", "ob_type": "out",
            "date": dt, "name": v["name"], "norm": v["norm"], "qty": v["qty"],
            "channel": ch_str, "put_sno": "", "purch": "",
            "is_positive": False,
            "is_excluded": _is_excluded(v["name"], "") or all_excl,
            "is_smartstore": False,
            "map_sku": v.get("map_sku", ""),
        })

    # 스마트스토어 날짜 합계 → ob_flat (1건/날짜, "스마트스토어 합계" 이름)
    for dt, v in ss_dt_agg.items():
        ch_str = ",".join(sorted(v["channels"]))[:80]
        ob_flat.append({
            "id": f"ob_{len(ob_flat)}",
            "side": "ob", "ob_type": "out",
            "date": dt, "name": "스마트스토어 출고 합계", "norm": "스마트스토어출고합계",
            "qty": v["qty"], "channel": ch_str, "put_sno": "", "purch": "",
            "is_positive": False, "is_excluded": False,
            "is_smartstore": True, "is_ss_agg": True,  # ← 날짜 합계 행
        })

    # OB 조정 (adj_qty 부호 + item_cd → 상품명 변환)
    ob_code_map = {}
    try:
        ob_code_map = client._get_ob_code_to_name()
    except Exception:
        pass

    # BH SKU 기반 이름 조회도 추가
    bh_sku_map = {}
    try:
        bh_items_list = U.fetch_all_items_list(token)
        bh_sku_map = {it.get("sku",""): it.get("name","") for it in bh_items_list if it.get("sku")}
    except Exception:
        pass

    for rec in _ob_adj_cached:
        adj = int(float(str(rec.get("adj_qty") or 0)))
        if adj == 0: continue
        dt = str(rec.get("reg_dtm") or rec.get("reg_dt",""))[:10]
        reason = str(rec.get("stock_adj_resn_nm") or "")

        # item_cd로 상품명 조회: OB company_code 또는 BH SKU(앞부분) 사용
        item_cd = str(rec.get("item_cd") or "").strip()
        base_cd = item_cd.split("-")[0] if "-" in item_cd else item_cd
        prod_nm = (ob_code_map.get(item_cd) or ob_code_map.get(base_cd)
                   or bh_sku_map.get(item_cd) or bh_sku_map.get(base_cd) or "")

        if prod_nm:
            name = prod_nm
            norm_val = _n_ob(prod_nm)
        else:
            name = f"[조정]{reason}"
            norm_val = U.normalize(reason)

        # 재고실사·수동조정 — 1:1 매칭 불가한 보정성 조정은 별도 분류
        # (재고조사 누적오차 보정 + 운영자 직접입력 수동조정)
        is_stocktake = any(k in reason for k in ["재고조사","차이조정","실사","직접입력","수기","수동"])

        ob_flat.append({
            "id": f"ob_{len(ob_flat)}",
            "side": "ob", "ob_type": "adjustment",
            "date": dt, "name": name, "norm": norm_val,
            "qty": abs(adj), "channel": "", "put_sno": "", "purch": "",
            "is_positive": adj > 0,
            "item_cd": item_cd, "reason": reason,
            "is_stocktake": is_stocktake,
            "is_excluded": _is_excluded(name, ""),
            "map_sku": _ob_map_sku_fm(item_cd, name) or _ob_map_sku_fm(base_cd, name),
        })

    # OB 타입별 수집 현황
    _ob_stats = {
        "in":  sum(1 for o in ob_flat if o["ob_type"]=="in"),
        "out": sum(1 for o in ob_flat if o["ob_type"]=="out"),
        "adjustment": sum(1 for o in ob_flat if o["ob_type"]=="adjustment"),
    }

    # ── 종합 스코어링 ─────────────────────────────────────────────
    def _score(b: dict, o: dict) -> float:
        # 스마트스토어 날짜 합계끼리 → 이름 대신 날짜+수량 기준 매칭
        b_agg = b.get("is_ss_agg", False)
        o_agg = o.get("is_ss_agg", False)
        if b_agg != o_agg:
            return 0.0  # 한쪽만 날짜합계 행 → 매칭 안 함 (개별행과 혼동 방지)
        if b_agg and o_agg:
            # 스마트스토어 날짜합계: 날짜 일치 + 수량 유사도로만 스코어
            # BH date = memo에서 추출한 실제 출고일, OB date = out_dt
            try:
                gap = abs((datetime.strptime(b["date"],"%Y-%m-%d")-datetime.strptime(o["date"],"%Y-%m-%d")).days)
            except: gap = 999
            if gap > 3: return 0.0  # 날짜 ±3일 초과 → 매칭 안 함
            big = max(b["qty"], o["qty"])
            if big == 0: return 0.0
            ratio = abs(b["qty"]-o["qty"])/big
            if ratio > 0.30: return 0.0  # 30% 초과 수량 차이 → 탈락
            qty_sc = 100.0 if ratio==0 else (95 if ratio<=0.05 else (88 if ratio<=0.10 else (78 if ratio<=0.20 else 65.0)))
            date_sc = max(0, 100 - gap * 20)  # 0일=100, 1일=80, 2일=60, 3일=40
            return round(qty_sc * 0.6 + date_sc * 0.4 + 5, 1)  # +5: 스마트스토어합계 보너스

        # ── 공통: 날짜 격차 & 방향 (이하 모든 경로가 공유) ──
        try:
            gap = abs((datetime.strptime(b["date"],"%Y-%m-%d")-datetime.strptime(o["date"],"%Y-%m-%d")).days)
        except Exception:
            gap = 999
        same_dir = b["is_positive"] == o["is_positive"]

        # 수정①: 하드 날짜 상한 — 상시출고 품목의 한 달치 거래가 '우연한 수량 근사'만으로
        #   뒤섞이는 오매칭 차단(예: 메노포즈 5/21출고 ↔ 6/22출고, 32일차). 초월 매칭 포함 전 경로 적용.
        #   (스마트스토어 합계행은 위에서 ±3일로 이미 처리)
        _MAX_GAP = 10
        if gap > _MAX_GAP:
            return 0.0

        # 수량 유사도 (이름보다 먼저 — SKU 초월 매칭에 필요)
        big = max(b["qty"], o["qty"])
        small = min(b["qty"], o["qty"])
        if big == 0: return 0.0
        if small > 0 and big / small > 10:
            return 0.0  # 10배 이상 수량 차이 → 즉시 탈락
        ratio = abs(b["qty"]-o["qty"])/big if big else 1
        if ratio == 0:       qty_sc = 100.0
        elif ratio <= 0.05:  qty_sc = 95.0
        elif ratio <= 0.10:  qty_sc = 88.0
        elif ratio <= 0.20:  qty_sc = 78.0
        elif ratio <= 0.30:  qty_sc = 65.0
        elif ratio <= 0.50:  qty_sc = 50.0
        else:                qty_sc = max(0, 100-ratio*100)

        # ══ 초월 매칭 1: SKU 직접 일치 + 수량 근사 ══
        # 날짜·유형·방향(입출고)·세트작업·이동·조정 전부 무시하고 매칭
        # 동점 방지: 수량 근접도(연속값) + 날짜 근접도로 미세 차등
        #   → 같은 SKU 후보 여럿일 때 수량이 더 가깝고 날짜가 가까운 쌍이 우선
        b_sku = b.get("map_sku") or ""
        o_sku = o.get("map_sku") or ""
        # 수정③: 양쪽 모두 SKU가 등록돼 있는데 서로 다르면 다른 상품 → 즉시 탈락
        #   (이름에 공통 토큰만 있어도 묶이던 다른 SKU 오매칭 차단)
        if b_sku and o_sku and b_sku != o_sku:
            return 0.0
        if b_sku and o_sku and b_sku == o_sku and qty_sc >= 70:
            # 수정②: 방향 반대(출고↔입고)는 이동/조정 등 명시 맥락이거나 날짜 매우 근접(≤2일)일 때만
            #   초월 매칭 허용. (move↔in 같은 정상 케이스는 유지, 먼 날짜 out↔in 오매칭은 일반 경로로)
            _mv_adj = (b.get("bh_type") in ("move", "adjustment")) or b.get("is_adj") or (o.get("ob_type") == "adjustment")
            if same_dir or _mv_adj or gap <= 2:
                # 날짜 페널티 강화: 가까운 날짜 우선 (단 초월 매칭은 유지 — 최대 -15)
                return round(150 - min(ratio * 100, 8) - min(gap * 0.15, 15), 2)  # 127~150

        # 이름 유사도
        if not b["norm"] or not o["norm"]: return 0.0
        name_sc = _fuzz.token_set_ratio(b["norm"], o["norm"])
        if name_sc < 60: return 0.0  # 이름이 너무 다르면 즉시 탈락

        # ══ 초월 매칭 2: 이름 매우 유사 + 수량 완전 일치 (SKU 미등록) ══
        # 날짜·유형 무시 (단, 이름 90%+ AND 수량 95%+만) — 동점은 날짜 근접도로 차등
        if name_sc >= 90 and qty_sc >= 95:
            # 수정②: 방향 반대는 날짜 매우 근접(≤2일)일 때만 초월 매칭. 그 외엔 아래 일반 경로(방향 감점 적용)로.
            if same_dir or gap <= 2:
                # 날짜 페널티 강화 (가까운 날짜 우선, 초월 매칭 유지 — 최대 -12)
                return round(130 - min(ratio * 100, 5) - min(gap * 0.15, 12), 2)  # 113~130

        # 날짜 근접도 (wide: 감점 최소화) — gap은 위에서 계산됨
        date_sc = max(0, 100 - gap * 1.5)  # 67일 = 0점

        # 방향 일치 보너스 / 반대는 소폭 감점 (크로스 방향도 허용 — 유형·날짜·거래처는 UI에서 검토)
        dir_bonus = 8 if same_dir else -8

        # 힌트 보너스
        sno_bonus = 25 if b.get("put_sno") and o.get("put_sno") and b["put_sno"]==o["put_sno"] else 0
        # 파트너/채널 유사 — BH partner 없으면 memo에서 채널 힌트 추출 (스마트스토어 등)
        bp = U.normalize(b.get("partner",""))
        if not bp:
            bp = U.normalize(b.get("memo","")[:60])  # memo 앞부분에서 채널 힌트 (partner 폴백)
        oc = U.normalize(o.get("channel",""))
        ch_bonus = 12 if bp and oc and _fuzz.partial_ratio(bp,oc)>=80 else 0
        # 메모 조정 + OB 조정 크로스
        adj_bonus = 8 if b.get("is_adj") and o.get("ob_type")=="adjustment" else 0
        # move + 전산처리용
        move_bonus = 8 if b.get("bh_type")=="move" and "전산처리용" in o.get("channel","") else 0

        total = name_sc*0.45 + qty_sc*0.45 + date_sc*0.10 + dir_bonus + sno_bonus + ch_bonus + adj_bonus + move_bonus
        return round(max(0, total), 1)

    # ── 세트 BOM 기반 세트작업 이벤트 매칭 (조립 + 해체) ───────────
    # 조립: BH ADJ 세트+/단품-  ↔  OB IN 세트+/OUT 단품-
    # 해체: BH ADJ 세트-/단품+  ↔  OB OUT 세트-/IN 단품+
    set_work_matches: list = []
    try:
        import receiving_db as _rdb_set
        _set_boms = _rdb_set.get_set_boms()
        if _set_boms:
            # BOM 인덱스: norm(set_name) → [(norm(comp_name), qty_ratio), ...]
            _s2c: dict = {}  # set_norm → [(comp_norm, ratio)]
            for _b in _set_boms:
                sn = U.normalize(_b.get("set_name") or _b.get("set_sku",""))
                cn = U.normalize(_b.get("component_name") or _b.get("component_sku",""))
                if not sn or not cn: continue
                _s2c.setdefault(sn, []).append((cn, float(_b["qty_per_set"])))

            def _dt_gap(d1, d2):
                try:
                    return abs((datetime.strptime(d1,"%Y-%m-%d")-datetime.strptime(d2,"%Y-%m-%d")).days)
                except: return 999

            def _detect_set_events(flat_list, type_set, type_comp, set_positive, comp_positive):
                """flat_list에서 BOM 기반 세트 작업 이벤트 감지.
                set_positive=True  → 조립 (세트 증가)
                set_positive=False → 해체 (세트 감소)
                type_set:  세트 트랜잭션 유형 ('adjustment'=BH, 'in'/'out'=OB)
                type_comp: 단품 트랜잭션 유형 ('adjustment'=BH, 'out'/'in'=OB)
                """
                events = []
                used = set()
                for si, s in enumerate(flat_list):
                    if si in used: continue
                    s_type = s.get("bh_type") or s.get("ob_type","")
                    if s_type != type_set: continue
                    if s["is_positive"] != set_positive: continue
                    sn = s["norm"]
                    if sn not in _s2c: continue
                    comps_needed = _s2c[sn]
                    set_qty = s["qty"]
                    matched_comps = []
                    all_found = True
                    for cn, ratio in comps_needed:
                        expected = set_qty * ratio
                        best_ci = None; best_diff = float('inf')
                        for ci, c in enumerate(flat_list):
                            if ci in used or ci == si: continue
                            c_type = c.get("bh_type") or c.get("ob_type","")
                            if c_type != type_comp: continue
                            if c["is_positive"] != comp_positive: continue
                            if c["norm"] != cn: continue
                            if _dt_gap(s["date"], c["date"]) > 5: continue
                            diff = abs(c["qty"] - expected)
                            if diff < best_diff:
                                best_diff = diff; best_ci = ci
                        if best_ci is None or best_diff / max(expected, 1) > 0.1:
                            all_found = False; break
                        matched_comps.append(best_ci)
                    if all_found:
                        events.append({
                            "set_idx": si, "comp_idxs": matched_comps,
                            "set_norm": sn, "set_name": s["name"], "set_qty": set_qty,
                            "date": s["date"],
                        })
                        used.add(si)
                        for ci in matched_comps: used.add(ci)
                return events, used

            def _match_set_evts(bh_evts, ob_evts, match_type, bh_tx, ob_tx):
                """BH 세트 이벤트 ↔ OB 세트 이벤트 1:1 매칭 후 set_work_matches에 추가."""
                ob_used = set()
                for be in bh_evts:
                    best_oe = None; best_gap = float('inf')
                    for oi_e, oe in enumerate(ob_evts):
                        if oi_e in ob_used: continue
                        if be["set_norm"] != oe["set_norm"]: continue
                        qty_diff = abs(be["set_qty"] - oe["set_qty"]) / max(be["set_qty"], 1)
                        if qty_diff > 0.1: continue
                        gap = _dt_gap(be["date"], oe["date"])
                        if gap < best_gap:
                            best_gap = gap; best_oe = oi_e
                    if best_oe is not None and best_gap <= 7:
                        oe = ob_evts[best_oe]
                        qd = be["set_qty"] - oe["set_qty"]
                        set_work_matches.append({
                            "match_type": match_type,
                            "score": max(0, 100 - best_gap * 5 - abs(qd)),
                            "qty_diff": qd, "same_direction": True,
                            "status": "ok" if qd == 0 else "qty_diff",
                            "bh_type": bh_tx, "ob_type": ob_tx,
                            "bh_date": be["date"], "ob_date": oe["date"],
                            "day_gap": best_gap,
                            "bh_name": be["set_name"], "ob_name": ob_flat[oe["set_idx"]]["name"],
                            "bh_qty": be["set_qty"], "ob_qty": oe["set_qty"],
                            "bh_partner": "", "ob_channel": "",
                            "ob_group_count": 1,
                            "set_bh_indices": [be["set_idx"]] + be["comp_idxs"],
                            "set_ob_indices": [oe["set_idx"]] + oe["comp_idxs"],
                            "set_detail": {
                                "set_name": be["set_name"], "set_qty": be["set_qty"],
                                "comp_count": len(be["comp_idxs"]),
                                "direction": "assemble" if match_type == "set_work" else "dismantle",
                            },
                        })
                        ob_used.add(best_oe)
                        for idx in [be["set_idx"]] + be["comp_idxs"]: used_bh.add(idx)
                        for idx in [oe["set_idx"]] + oe["comp_idxs"]: used_ob.add(idx)

            # ── 조립 이벤트 감지: 세트+ / 단품- ──────────────────────
            bh_asm_evts, _ = _detect_set_events(bh_flat, "adjustment", "adjustment",
                                                 set_positive=True, comp_positive=False)
            ob_asm_evts, _ = _detect_set_events(ob_flat, "in", "out",
                                                 set_positive=True, comp_positive=False)
            _match_set_evts(bh_asm_evts, ob_asm_evts, "set_work", "adjustment", "in")

            # ── 해체 이벤트 감지: 세트- / 단품+ ──────────────────────
            bh_dis_evts, _ = _detect_set_events(bh_flat, "adjustment", "adjustment",
                                                 set_positive=False, comp_positive=True)
            ob_dis_evts, _ = _detect_set_events(ob_flat, "out", "in",
                                                 set_positive=False, comp_positive=True)
            _match_set_evts(bh_dis_evts, ob_dis_evts, "set_dismantle", "adjustment", "out")

    except Exception as _e_set:
        pass  # BOM 매칭 실패 시 무시하고 일반 매칭 진행

    # ── 그리디 매칭 (점수 내림차순) ──────────────────────────────
    pairs = []
    for bi, b in enumerate(bh_flat):
        for oi, o in enumerate(ob_flat):
            sc = _score(b, o)
            if sc >= min_score:
                pairs.append((sc, bi, oi))
    pairs.sort(key=lambda x: -x[0])

    used_bh, used_ob = set(), set()
    matched = []
    for sc, bi, oi in pairs:
        if bi in used_bh or oi in used_ob: continue
        used_bh.add(bi); used_ob.add(oi)
        b, o = bh_flat[bi], ob_flat[oi]
        qd = b["qty"] - o["qty"]
        same_dir = b["is_positive"] == o["is_positive"]
        _b_sku = b.get("map_sku") or ""
        _o_sku = o.get("map_sku") or ""
        _sku_hit = bool(_b_sku and _o_sku and _b_sku == _o_sku)
        _reason = ("SKU완전매칭" if sc >= 140 else
                   "이름수량완전매칭" if sc >= 120 else "유사도매칭")
        matched.append({
            "score": sc, "qty_diff": qd,
            "same_direction": same_dir,
            "sku_match": _sku_hit,
            "match_reason": _reason,
            "bh_idx": bi, "ob_idx": oi,
            "status": "ok" if qd==0 and same_dir else ("qty_diff" if same_dir else "cross_dir"),
            "bh_type": b["bh_type"], "ob_type": o["ob_type"],
            "bh_date": b["date"], "ob_date": o["date"],
            "day_gap": abs((datetime.strptime(b["date"],"%Y-%m-%d")-datetime.strptime(o["date"],"%Y-%m-%d")).days),
            "bh_name": b["name"], "ob_name": o["name"],
            "bh_qty": b["qty"], "ob_qty": o["qty"],
            "bh_memo": b.get("memo","")[:40], "bh_partner": b.get("partner",""),
            "ob_channel": o.get("channel","")[:50], "ob_put_sno": o.get("put_sno",""),
            "bh_put_sno": b.get("put_sno",""),
            "is_smartstore": b.get("is_smartstore", False) or o.get("is_smartstore", False),
            "ob_group_count": o.get("ob_group_count", 1),  # OB 그룹화 원본 건수 (>1이면 합산된 것)
        })

    bh_only = [{**bh_flat[i], "bh_idx": i, "candidates": []} for i in range(len(bh_flat)) if i not in used_bh
               and from_date <= bh_flat[i]["date"] <= to_date]  # 요청 기간 내 BH 건만 보고
    ob_only_all = [{**ob_flat[j], "ob_idx": j} for j in range(len(ob_flat)) if j not in used_ob
                   and from_date <= ob_flat[j]["date"] <= to_date]  # 요청 기간 내 OB 건만 보고

    # 미매칭 분류: 재고실사/기초이관 / BH미관리(부자재·샘플) / 일반
    def _is_special(o: dict) -> bool:
        return bool(o.get("is_stocktake") or o.get("is_bulk_init"))
    stocktake = [o for o in ob_only_all if _is_special(o)]
    excluded  = [o for o in ob_only_all if o.get("is_excluded") and not _is_special(o)]
    ob_only   = [o for o in ob_only_all if not _is_special(o) and not o.get("is_excluded")]

    # 매칭률 분모: 재고실사+기초이관 + BH미관리 항목 전체 제외 (매칭 여부 무관)
    n_stocktake_total = sum(1 for o in ob_flat if _is_special(o))
    n_excluded_total  = sum(1 for o in ob_flat if o.get("is_excluded") and not _is_special(o))
    ob_denom = max(len(ob_flat) - n_stocktake_total - n_excluded_total, 1)
    bh_denom = max(len(bh_flat), 1)

    # 매칭 중 제외성 항목 수 (분자에서도 빼서 정합성 유지)
    matched_excl = sum(1 for j in used_ob
                       if _is_special(ob_flat[j]) or ob_flat[j].get("is_excluded"))
    matched_real = len(matched) - matched_excl

    exact = sum(1 for m in matched if m["status"]=="ok")
    cross = sum(1 for m in matched if m["status"]=="cross_dir")
    bh_rate = round(len(matched)/bh_denom*100,1)
    ob_rate = round(matched_real/ob_denom*100,1)

    stocktake_qty = sum(o["qty"] for o in stocktake)
    excluded_qty  = sum(o["qty"] for o in excluded)

    # probable = 매칭됐지만 완전일치(수량0·동방향)는 아닌 것
    probable = len(matched) - exact

    # 채널별 집계 (OB 채널/BH 파트너 기준) — 대시보드용
    from collections import defaultdict as _dd3
    ch_agg = _dd3(lambda: {"bh_qty":0,"ob_qty":0,"matched_qty":0,"count":0})
    for m in matched:
        ch = (m.get("ob_channel") or m.get("bh_partner") or "(미지정)")[:30]
        ch_agg[ch]["bh_qty"] += m["bh_qty"]; ch_agg[ch]["ob_qty"] += m["ob_qty"]
        ch_agg[ch]["matched_qty"] += min(m["bh_qty"], m["ob_qty"]); ch_agg[ch]["count"]+=1
    for o in ob_only:
        ch = (o.get("channel") or "(미지정)")[:30]
        ch_agg[ch]["ob_qty"] += o["qty"]; ch_agg[ch]["count"]+=1
    for b in bh_only:
        ch = (b.get("partner") or "(미지정)")[:30]
        ch_agg[ch]["bh_qty"] += b["qty"]; ch_agg[ch]["count"]+=1
    by_channel = []
    for ch, v in ch_agg.items():
        by_channel.append({
            "channel": ch, "bh_qty": v["bh_qty"], "ob_qty": v["ob_qty"],
            "matched_qty": v["matched_qty"], "diff": v["ob_qty"]-v["bh_qty"],
            "bh_match_rate": round(v["matched_qty"]/max(v["bh_qty"],1)*100,1),
            "ob_match_rate": round(v["matched_qty"]/max(v["ob_qty"],1)*100,1),
        })
    by_channel.sort(key=lambda x:-(x["ob_qty"]+x["bh_qty"]))

    # 유형별 집계 (OB 기준 in/out/adjustment) — 프론트 유형별 매칭률 렌더용
    from collections import defaultdict as _dd4
    type_agg = _dd4(lambda: {"matched":0,"bh_total":0,"ob_total":0,"ob_only":0})
    for m in matched:
        t = m.get("ob_type","?")
        type_agg[t]["matched"] += 1
        type_agg[t]["bh_total"] += 1; type_agg[t]["ob_total"] += 1
    for o in ob_only:
        type_agg[o.get("ob_type","?")]["ob_total"] += 1
        type_agg[o.get("ob_type","?")]["ob_only"] += 1
    for b in bh_only:
        # BH 유형은 in/out/move/adjust → in만 입고로, 나머지는 out으로 매핑
        bt = "in" if b.get("bh_type")=="in" else ("adjustment" if b.get("bh_type")=="adjust" else "out")
        type_agg[bt]["bh_total"] += 1
    by_type = {}
    for t, v in type_agg.items():
        by_type[t] = {
            "matched": v["matched"], "bh_total": v["bh_total"], "ob_total": v["ob_total"],
            "ob_only": v["ob_only"],
            "bh_match_rate": round(v["matched"]/max(v["bh_total"],1)*100,1),
            "ob_match_rate": round(v["matched"]/max(v["ob_total"],1)*100,1),
        }

    # OB 날짜 기준으로 결과를 요청 범위(from_date~to_date)로 필터링
    # BH는 확장 조회(±lookback)이므로 범위 밖 BH-only는 제외, matched/ob_only는 OB 날짜 기준
    def _in_range(dt_str: str) -> bool:
        s = str(dt_str or "")[:10]
        return bool(s) and from_date <= s <= to_date

    _all_matched = matched + set_work_matches
    _matched_f  = [m for m in _all_matched if _in_range(m.get("ob_date") or m.get("bh_date",""))]
    _bh_only_f  = [b for b in bh_only   if _in_range(b.get("date",""))]
    _ob_only_f  = [o for o in ob_only   if _in_range(o.get("date",""))]
    _stocktake_f = [s for s in stocktake if _in_range(s.get("date",""))]
    _excluded_f  = [e for e in excluded  if _in_range(e.get("date",""))]

    _result = {
        "from_date": from_date, "to_date": to_date,
        "bh_lookback": bh_lookback,
        "total_bh": len(bh_flat), "total_ob": len(ob_flat),
        "matched_count": len(_matched_f),
        "exact_count": exact, "probable_count": probable, "cross_dir_count": cross,
        "bh_only_count": len(_bh_only_f), "ob_only_count": len(_ob_only_f),
        "bh_match_rate": bh_rate, "ob_match_rate": ob_rate,
        "match_rate_bh": bh_rate, "match_rate_ob": ob_rate,
        "by_type": by_type,
        "stocktake_count": len(_stocktake_f), "stocktake_qty": stocktake_qty,
        "bulk_init_count": sum(1 for o in _stocktake_f if o.get("is_bulk_init")),
        "excluded_count": len(_excluded_f), "excluded_qty": excluded_qty,
        "set_work_count": sum(1 for m in _matched_f if m.get("match_type") == "set_work"),
        "set_dismantle_count": sum(1 for m in _matched_f if m.get("match_type") == "set_dismantle"),
        "matched": sorted(_matched_f, key=lambda x:-x["score"]),
        "bh_only": sorted(_bh_only_f, key=lambda x:-x["qty"]),
        "ob_only": sorted(_ob_only_f, key=lambda x:-x["qty"]),
        "stocktake": sorted(_stocktake_f, key=lambda x:-x["qty"]),
        "excluded": sorted(_excluded_f, key=lambda x:-x["qty"]),
        "by_channel": by_channel,
        "bh_stats": _bh_stats,
        "ob_stats": _ob_stats,
        # BH 내부 전산정리 (일원화·품목통합 등) — 매칭 풀에서 제외된 TX 목록
        "bh_internal": sorted(bh_internal, key=lambda x: -x["qty"]),
        "bh_internal_count": len(bh_internal),
        "bh_internal_qty": sum(x["qty"] for x in bh_internal),
        # BH 부자재/포장재 — OB 미관리 품목, 매칭 풀에서 제외 (별도 표시용)
        "bh_excluded": sorted(bh_excluded, key=lambda x: -x["qty"]),
        "bh_excluded_count": len(bh_excluded),
        "bh_excluded_qty": sum(x["qty"] for x in bh_excluded),
    }
    # 결과 메모리 + 파일 캐시 저장
    state.full_match_cache[_cache_key] = {"result": _result, "ts": _dt_mod.datetime.now()}
    _save_fm_file_cache(_result)
    return _result


@router.get("/match")
def match_transactions(
    token: str = Query(...),
    from_date: str = Query(...),
    to_date: str = Query(...),
    tolerance_days: int = Query(3),           # 날짜 허용 범위 ±N일
    cross_type: bool = Query(True),           # OB 조정→BH 입고 등 유형 교차 허용
    channel_filter: Optional[str] = Query(None),  # BH partner 이름 부분일치 필터
):
    """BH ↔ OB 입고 트랜잭션 수량 매칭.

    알고리즘:
    1. OB 입고(+ cross_type이면 조정 증가분)를 상품 매핑으로 BH SKU 변환 후
       (SKU, 날짜) 기준으로 품목별 집계 → BH 배치 단위와 맞춤
    2. BH 입고를 (SKU, 날짜) 집계
    3. 각 BH 배치에 대해 ±tolerance_days 창 내 OB 집계와 수량 비교
       → 정확히 일치: matched / OB가 큰 경우: ob_excess / BH만: bh_only / OB만: ob_only
    """
    import ourbox_api as api_mod

    cfg = U.load_config()
    errors: list = []

    # ── 상품 매핑 그룹 ──────────────────────────────────────────
    try:
        import receiving_db as _db
        mapping = _db.get_name_mapping_pairs()  # [{ob_name, bh_sku, bh_name}]
        ob_name_to_sku: dict = {m["ob_name"]: m["bh_sku"] for m in mapping if m["bh_sku"]}
    except Exception:
        ob_name_to_sku = {}

    # ── BH 입고 수집·상세 ────────────────────────────────────────
    import re as _re
    bh_raw = U.fetch_transactions(token, "in", from_date, to_date, None)
    _enrich_bh_items(token, bh_raw)

    if channel_filter:
        bh_raw = [tx for tx in bh_raw if channel_filter.lower() in
                  ((tx.get("partner") or {}).get("name", "") if isinstance(tx.get("partner"), dict) else "").lower()]

    # BH 배치 단위 집계: {(sku, date_str): {qty, name, memo, partner, tx_id, put_sno}}
    from collections import defaultdict
    bh_agg: dict = defaultdict(lambda: {"qty": 0, "name": "", "memo": "", "partner": "", "tx_ids": [], "put_sno": ""})
    for tx in bh_raw:
        tx_time = tx.get("transaction_time") or tx.get("created_at", "")
        try:
            tx_dt = datetime.fromisoformat(tx_time[:19])
        except Exception:
            continue
        memo = tx.get("memo") or ""
        partner = (tx.get("partner") or {}).get("name", "") if isinstance(tx.get("partner"), dict) else ""
        # BH memo에서 OB put_sno 추출: "아워박스 입고번호: 6785 (이천)"
        put_sno_m = _re.search(r"입고번호[:\s]*(\d+)", memo)
        put_sno = put_sno_m.group(1) if put_sno_m else ""
        for item in tx.get("items", []):
            sku = str(item.get("sku") or item.get("id") or "").strip()
            if not sku:
                continue
            qty = abs(int(item.get("quantity", 0)))
            k = (sku, tx_dt.strftime("%Y-%m-%d"))
            bh_agg[k]["qty"] += qty
            bh_agg[k]["name"] = item.get("name", "") or bh_agg[k]["name"]
            bh_agg[k]["memo"] = memo
            bh_agg[k]["partner"] = partner
            bh_agg[k]["tx_ids"].append(tx.get("id"))
            if put_sno:
                bh_agg[k]["put_sno"] = put_sno  # OB 입고번호 기록

    # ── OB 입고(+ cross_type이면 조정 증가분) 수집 ──────────────
    try:
        client = api_mod.make_client(cfg)
        if not client:
            raise RuntimeError("OurBox API Key 없음")
        ob_in_raw = client.fetch_inbounds(from_date, to_date)
        ob_adj_raw = client.fetch_adjustments(from_date, to_date) if cross_type else []
    except Exception as e:
        return {"error": f"OurBox 수집 실패: {e}", "matched": [], "bh_only": [], "ob_only": []}

    # OB 품목별 집계: {(bh_sku, date_str): {qty, ob_name, purch}}
    ob_agg: dict = defaultdict(lambda: {"qty": 0, "ob_name": "", "purch": "", "dates": []})

    def _add_ob_item(prod_nm: str, qty: int, date_str: str, purch: str = "", tx_type: str = "in", put_sno: str = ""):
        if not prod_nm or qty == 0:
            return
        bh_sku = ob_name_to_sku.get(prod_nm, "")
        key_sku = bh_sku or prod_nm
        # put_sno가 있으면 키에 포함 → 같은 입고번호끼리 정확 매칭
        k = (key_sku, date_str, put_sno)
        ob_agg[k]["qty"] += qty
        ob_agg[k]["ob_name"] = prod_nm
        ob_agg[k]["purch"] = purch
        ob_agg[k]["dates"].append(date_str)
        ob_agg[k]["mapped"] = bool(bh_sku)
        ob_agg[k]["tx_type"] = tx_type
        ob_agg[k]["put_sno"] = put_sno

    for rec in ob_in_raw:
        if not isinstance(rec, dict):
            continue
        prod_nm = html.unescape(str(rec.get("product_name") or rec.get("sale_prod_nm") or "").strip())
        qty = abs(int(float(str(rec.get("input_qty") or 0))))
        date_str = str(rec.get("input_dt") or rec.get("input_complete_dt") or "")[:10]
        purch = str(rec.get("purch_company") or "")
        put_sno = str(rec.get("input_code") or rec.get("put_sno") or "")
        _add_ob_item(prod_nm, qty, date_str, purch, "in", put_sno)

    # cross_type: OB 조정 증가분(adj_qty > 0)을 입고로 처리
    if cross_type:
        for rec in ob_adj_raw:
            if not isinstance(rec, dict):
                continue
            adj_qty = int(float(str(rec.get("adj_qty") or 0)))
            if adj_qty <= 0:
                continue  # 감소 조정은 제외
            # OB 조정은 product_name 없음 → stock_adj_resn_nm(사유)만 기록
            reason = str(rec.get("stock_adj_resn_nm") or "")
            date_str = str(rec.get("reg_dtm") or rec.get("reg_dt") or "")[:10]
            # 상품 미상이지만 사유와 수량은 기록 (bh_only 후보로 남김)
            k = (f"[OB조정] {reason}", date_str, "")
            ob_agg[k]["qty"] += adj_qty
            ob_agg[k]["ob_name"] = f"[조정] {reason}"
            ob_agg[k]["tx_type"] = "adjustment"
            ob_agg[k]["mapped"] = False
            ob_agg[k]["purch"] = ""
            ob_agg[k]["put_sno"] = ""

    # ── 수량 매칭 (±tolerance_days) ──────────────────────────────
    from datetime import timedelta

    matched = []
    bh_only = []
    ob_used: set = set()

    bh_list = sorted(bh_agg.items(), key=lambda x: x[0][1])  # 날짜순
    ob_list  = sorted(ob_agg.items(), key=lambda x: x[0][1])

    for (bh_sku, bh_date), bv in bh_list:
        bh_dt = datetime.strptime(bh_date, "%Y-%m-%d")
        bh_put_sno = bv.get("put_sno", "")
        best_key = None
        best_diff = float("inf")
        exact_by_sno = False

        for (ob_sku, ob_date, ob_put_sno), ov in ob_agg.items():
            if (ob_sku, ob_date, ob_put_sno) in ob_used:
                continue
            if ob_sku != bh_sku:
                continue
            try:
                ob_dt = datetime.strptime(ob_date, "%Y-%m-%d")
            except Exception:
                continue

            # 1순위: BH memo의 OB 입고번호와 OB put_sno가 일치 → 정확 매칭
            if bh_put_sno and ob_put_sno and bh_put_sno == ob_put_sno:
                best_key = (ob_sku, ob_date, ob_put_sno)
                exact_by_sno = True
                break

            # 2순위: ±tolerance_days + 수량 유사도
            day_gap = abs((bh_dt - ob_dt).days)
            if day_gap > tolerance_days:
                continue
            qty_diff = abs(bv["qty"] - ov["qty"])
            score = day_gap * 1000 + qty_diff
            if score < best_diff:
                best_diff = score
                best_key = (ob_sku, ob_date, ob_put_sno)

        if best_key:
            ob_used.add(best_key)
            ov = ob_agg[best_key]
            qty_diff = bv["qty"] - ov["qty"]
            day_gap = abs((bh_dt - datetime.strptime(best_key[1], "%Y-%m-%d")).days)
            matched.append({
                "sku": bh_sku,
                "bh_name": bv["name"],
                "ob_name": ov["ob_name"],
                "bh_date": bh_date,
                "ob_date": best_key[1],
                "day_gap": day_gap,
                "bh_qty": bv["qty"],
                "ob_qty": ov["qty"],
                "qty_diff": qty_diff,
                "status": "ok" if qty_diff == 0 else ("bh_excess" if qty_diff > 0 else "ob_excess"),
                "bh_memo": bv["memo"],
                "bh_partner": bv["partner"],
                "ob_purch": ov["purch"],
                "ob_put_sno": best_key[2],
                "mapped": ov.get("mapped", False),
                "match_method": "put_sno" if exact_by_sno else "qty_date",
            })
        else:
            bh_only.append({
                "sku": bh_sku, "name": bv["name"], "date": bh_date,
                "qty": bv["qty"], "memo": bv["memo"], "partner": bv["partner"],
                "put_sno": bh_put_sno,
            })

    ob_only = [
        {
            "sku": ob_sku, "ob_name": ov["ob_name"], "date": ob_date,
            "qty": ov["qty"], "purch": ov["purch"],
            "ob_put_sno": ob_put_sno,
            "tx_type": ov.get("tx_type", "in"), "mapped": ov.get("mapped", False),
        }
        for (ob_sku, ob_date, ob_put_sno), ov in ob_agg.items()
        if (ob_sku, ob_date, ob_put_sno) not in ob_used
    ]
    ob_only.sort(key=lambda x: -x["qty"])

    return {
        "from_date": from_date, "to_date": to_date,
        "tolerance_days": tolerance_days,
        "matched_count": len(matched),
        "bh_only_count": len(bh_only),
        "ob_only_count": len(ob_only),
        "matched": sorted(matched, key=lambda x: x["bh_date"]),
        "bh_only": bh_only,
        "ob_only": ob_only,
        "errors": errors,
    }


@router.post("/match/confirm")
def confirm_matches(body: dict):
    """입고 매칭 결과를 DB에 저장 → compare 조회 시 반영."""
    import receiving_db as _db
    matched = body.get("matched", [])
    from_date = body.get("from_date", "")
    to_date = body.get("to_date", "")
    if not matched:
        return {"saved": 0, "message": "저장할 매칭 건이 없습니다"}
    _db.save_matched_pairs(matched, from_date=from_date, to_date=to_date)
    return {"saved": len(matched), "from_date": from_date, "to_date": to_date}


@router.get("/matched-pairs")
def get_matched_pairs_api(from_date: str = Query(...), to_date: str = Query(...), name: str = Query("")):
    """확정 매칭 쌍 조회 — 비교조회 행의 매칭 근거 검증용. name으로 품목 필터."""
    import receiving_db as _db
    import re as _re2
    rows = _db.get_matched_pairs(from_date, to_date)
    if name:
        def _n(s): return _re2.sub(r"[\s\-_·•\[\]()（）]", "", str(s or "")).lower()
        key = _n(_re2.sub(r'-\d{4}-\d{2}-\d{2}$', '', name))
        def _hit(r):
            for f in (r.get("sku",""), r.get("bh_name",""), r.get("ob_name","")):
                fn = _n(_re2.sub(r'-\d{4}-\d{2}-\d{2}$', '', str(f or "")))
                if fn and (key in fn or fn in key):
                    return True
            return False
        rows = [r for r in rows if _hit(r)]
    return {"pairs": rows, "count": len(rows)}


@router.delete("/match/confirm")
def clear_matches(from_date: str = Query(...), to_date: str = Query(...)):
    """확정된 매칭 초기화."""
    import receiving_db as _db
    _db.clear_matched_pairs(from_date, to_date)
    return {"cleared": True}


@router.get("/detail")
def detail(
    period: str = Query(...),
    sku: str = Query(...),
    tx_type: str = Query(...),
    channel: str = Query(""),
    bh_lookback: int = Query(3),  # BH 날짜 ±N일 탐색 (OB 기준 날짜 맞춤용)
):
    """집계 행 1건을 클릭했을 때, 그 뒤의 개별 거래를 양쪽에서 찾아 수량 매칭.

    OB: period 정확 날짜만 조회 (OB가 날짜 기준)
    BH: period ±bh_lookback일 윈도우 조회 (BH가 OB보다 1~N일 앞/뒤에 처리될 수 있음)
    week/month 형식이면 양쪽 모두 정확 매칭.
    """
    cache = state.reconcile_cache or {}
    items = cache.get("items", [])
    if not items:
        raise HTTPException(400, "먼저 '비교 조회'를 실행해 주세요 (드릴다운 데이터 없음)")

    is_daily = False
    bh_date_window: set = {period}
    try:
        _center_dt = datetime.strptime(period, "%Y-%m-%d")
        is_daily = True
        if bh_lookback > 0:
            bh_date_window = {
                (_center_dt + timedelta(days=d)).strftime("%Y-%m-%d")
                for d in range(-bh_lookback, bh_lookback + 1)
            }
    except ValueError:
        pass

    def _base_match(it):
        return (it["tx_type"] == tx_type and it["display_sku"] == sku
                and (it.get("channel") or "") == (channel or ""))

    if is_daily:
        # OB: 정확한 날짜만 (OB가 기준)
        ob_items = [it for it in items if _base_match(it) and it["side"] == "ob" and it["period"] == period]
        # BH: ±lookback 창 (BH는 OB보다 앞/뒤에 처리될 수 있음)
        bh_items = [it for it in items if _base_match(it) and it["side"] == "bh" and it["period"] in bh_date_window]
    else:
        sel = [it for it in items if _base_match(it) and it["period"] == period]
        bh_items = [it for it in sel if it["side"] == "bh"]
        ob_items = [it for it in sel if it["side"] == "ob"]

    paired = _greedy_pair(bh_items, ob_items)
    return {
        "period": period, "sku": sku, "tx_type": tx_type, "channel": channel,
        "bh_total": sum(i["qty"] for i in bh_items),
        "ob_total": sum(i["qty"] for i in ob_items),
        "bh_count": len(bh_items), "ob_count": len(ob_items),
        **paired,
    }


@router.get("/missing")
def missing(
    tx_type: str = Query("out"),  # out | in | adjustment | all
):
    """직전 /compare 결과에서 상품(×채널)별 BH↔OB 총량 차이를 전 기간 합산해 추출.

    OB는 주문 단위, BH는 묶음 입력이라 건별 1:1 매칭은 불가능하므로,
    상품·채널별 총량을 합산해 '차이(diff=OB합-BH합)'를 낸다.
    diff>0 = BoxHero에 그만큼 추가 입력 필요 / diff<0 = OurBox 누락(또는 BH 과입력).
    """
    cache = state.reconcile_cache or {}
    items = cache.get("items", [])
    if not items:
        raise HTTPException(400, "먼저 '비교 조회'를 실행해 주세요 (추출 데이터 없음)")

    tx_filter = None if tx_type == "all" else tx_type
    from collections import defaultdict
    agg: dict = defaultdict(lambda: {"bh": 0, "ob": 0, "name": "", "dates": set()})
    for it in items:
        if tx_filter and it["tx_type"] != tx_filter:
            continue
        key = (it["tx_type"], it["display_sku"], it.get("channel") or "")
        a = agg[key]
        a[it["side"]] += it["qty"]
        if it.get("name"):
            a["name"] = it["name"]
        if it.get("date"):
            a["dates"].add(it["date"][:10])

    rows = []
    for (tt, sku, ch), a in agg.items():
        diff = a["ob"] - a["bh"]
        if diff == 0:
            continue
        rows.append({
            "tx_type": tt, "sku": sku, "channel": ch, "name": a["name"],
            "bh_qty": a["bh"], "ob_qty": a["ob"], "diff": diff,
            "need_boxhero": diff if diff > 0 else 0,   # BH에 추가 입력 필요량
            "need_ourbox": -diff if diff < 0 else 0,   # OB쪽 부족(또는 BH 과입력)
            "dates": sorted(a["dates"]),
        })
    rows.sort(key=lambda x: -abs(x["diff"]))
    return {
        "tx_type": tx_type,
        "count": len(rows),
        "total_need_boxhero": sum(r["need_boxhero"] for r in rows),
        "total_need_ourbox": sum(r["need_ourbox"] for r in rows),
        "rows": rows,
    }


# ── AI 분석 ──────────────────────────────────────────────────────

class AnalyzeRow(BaseModel):
    period: str
    tx_type: str
    sku: str
    name: str
    bh_qty: Optional[int] = None
    ob_qty: Optional[int] = None
    status: str

class AnalyzeSummaryItem(BaseModel):
    total: int; ok: int; mismatch: int; bh_only: int; ob_only: int

class AnalyzeSummary(BaseModel):
    total: AnalyzeSummaryItem
    inbound: Optional[AnalyzeSummaryItem] = None
    outbound: Optional[AnalyzeSummaryItem] = None
    adjustment: Optional[AnalyzeSummaryItem] = None

class AnalyzeRequest(BaseModel):
    rows: List[AnalyzeRow]
    summary: dict
    from_date: str
    to_date: str
    period: str
    # AI 제공자 설정 (하나만 입력)
    claude_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""


def _detect_timing_gaps(rows: List[AnalyzeRow], period: str) -> list:
    """인접 기간에서 같은 SKU가 한쪽만 있으면 시점 차이 후보로 분류."""
    from collections import defaultdict

    # SKU별 BH/OB 보유 기간 색인
    bh_periods: dict = defaultdict(set)
    ob_periods: dict = defaultdict(set)
    for r in rows:
        key = (r.tx_type, r.sku or r.name)
        if r.bh_qty:
            bh_periods[key].add(r.period)
        if r.ob_qty:
            ob_periods[key].add(r.period)

    candidates = []
    for r in rows:
        if r.status not in ("bh_only", "ob_only"):
            continue
        key = (r.tx_type, r.sku or r.name)
        if r.status == "bh_only":
            # BH에만 있음 → OB 인접 기간에 있는지 확인
            if ob_periods.get(key):
                candidates.append({
                    "period": r.period,
                    "tx_type": r.tx_type,
                    "sku": r.sku,
                    "name": r.name,
                    "bh_qty": r.bh_qty,
                    "ob_qty": r.ob_qty,
                    "reason": f"OurBox에서 인접 기간({sorted(ob_periods[key])[-1]})에 동일 SKU 존재 → 시점 차이 가능",
                })
        else:
            if bh_periods.get(key):
                candidates.append({
                    "period": r.period,
                    "tx_type": r.tx_type,
                    "sku": r.sku,
                    "name": r.name,
                    "bh_qty": r.bh_qty,
                    "ob_qty": r.ob_qty,
                    "reason": f"박스히어로에서 인접 기간({sorted(bh_periods[key])[-1]})에 동일 SKU 존재 → 시점 차이 가능",
                })
    return candidates[:20]  # 상위 20개


def _analyze_patterns(rows: List[AnalyzeRow]) -> list:
    """SKU별 불일치 패턴 분석."""
    from collections import defaultdict, Counter

    sku_issues: dict = defaultdict(lambda: {"count": 0, "bh_excess": 0, "ob_excess": 0, "name": ""})
    for r in rows:
        if r.status == "ok":
            continue
        key = r.sku or r.name
        sku_issues[key]["count"] += 1
        sku_issues[key]["name"] = r.name
        if r.bh_qty and r.ob_qty:
            if r.bh_qty > r.ob_qty:
                sku_issues[key]["bh_excess"] += 1
            else:
                sku_issues[key]["ob_excess"] += 1
        elif r.bh_qty:
            sku_issues[key]["bh_excess"] += 1
        else:
            sku_issues[key]["ob_excess"] += 1

    # 3회 이상 반복 불일치 SKU
    repeated = [
        {"sku": k, "name": v["name"], "issue_count": v["count"],
         "direction": "BH>OB" if v["bh_excess"] > v["ob_excess"] else "OB>BH"}
        for k, v in sku_issues.items() if v["count"] >= 2
    ]
    return sorted(repeated, key=lambda x: -x["issue_count"])[:15]


def _build_analysis_prompt(req: AnalyzeRequest, timing: list, patterns: list) -> str:
    s = req.summary.get("total", {})
    mismatch_rows = [r for r in req.rows if r.status != "ok"][:30]

    period_label = {"day": "일별", "week": "주별", "month": "월별", "year": "연별"}.get(req.period, req.period)

    mismatch_text = "\n".join(
        f"- [{r.tx_type}] {r.name or r.sku} | {r.period} | BH:{r.bh_qty} / OB:{r.ob_qty} | {r.status}"
        for r in mismatch_rows
    ) or "없음"

    timing_text = "\n".join(
        f"- [{t['tx_type']}] {t['name'] or t['sku']} ({t['period']}): {t['reason']}"
        for t in timing
    ) or "없음"

    pattern_text = "\n".join(
        f"- {p['name'] or p['sku']}: {p['issue_count']}회 불일치, 방향={p['direction']}"
        for p in patterns
    ) or "없음"

    return f"""당신은 물류 재고 대사 전문 컨설턴트입니다. 아래 박스히어로(WMS) ↔ 아워박스 Mate(풀필먼트) 재고 불일치 데이터를 분석해 주세요.

## 분석 기간
{req.from_date} ~ {req.to_date} ({period_label})

## 전체 요약
- 전체: {s.get('total', 0)}건
- 정상 일치: {s.get('ok', 0)}건
- 수량 불일치: {s.get('mismatch', 0)}건
- 박스히어로에만 존재: {s.get('bh_only', 0)}건
- 아워박스에만 존재: {s.get('ob_only', 0)}건

## 시점 차이 추정 항목 (인접 기간 동일 SKU 존재)
{timing_text}

## 반복 불일치 패턴 (2회 이상 동일 SKU)
{pattern_text}

## 주요 불일치 상세 (최대 30건)
{mismatch_text}

---

위 데이터를 바탕으로 다음 6가지를 **한국어**로 구체적으로 분석해 주세요:

1. **전체 불일치 개요**: 심각도 평가와 전반적 특징
2. **시점 차이 항목**: 단순 입력 시점 차이(처리 지연, 마감 시점 차이 등)로 보이는 항목과 판단 근거
3. **실제 오류 의심 항목**: 시점 차이로 설명되지 않는 항목과 원인 추정 (인적 오류, 시스템 오류 등)
4. **반복 패턴 분석**: 특정 SKU나 날짜에 집중된 문제의 원인과 의미
5. **우선 조사 순위 Top 5**: 즉시 확인이 필요한 항목과 근거
6. **프로세스 개선 권고**: 재발 방지를 위한 실질적 조치 방안

분석은 실무자가 바로 조치를 취할 수 있도록 구체적이고 실용적으로 작성해 주세요."""


async def _stream_gemini(api_key: str, prompt: str):
    """Google Gemini 2.5 Flash — REST API 직접 호출 (패키지 deprecation 우회)."""
    import requests as _req

    GEMINI_MODEL = "gemini-2.5-flash"
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:streamGenerateContent?alt=sse&key={api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 2500},
    }
    try:
        resp = _req.post(url, json=payload, stream=True, timeout=120)
        if not resp.ok:
            err = resp.text[:200]
            yield f"data: {json.dumps({'error': f'Gemini API 오류 {resp.status_code}: {err}'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return

        buf = ""
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            if raw.startswith("data:"):
                raw = raw[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                chunk = json.loads(raw)
                parts = (chunk.get("candidates", [{}])[0]
                         .get("content", {})
                         .get("parts", []))
                for p in parts:
                    text = p.get("text", "")
                    if text:
                        yield f"data: {json.dumps({'text': text}, ensure_ascii=False)}\n\n"
            except Exception:
                pass
        yield "data: [DONE]\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"


async def _stream_groq(api_key: str, prompt: str):
    """Groq — LLaMA 3 기반, 무료 티어 지원."""
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        stream = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            stream=True,
            max_tokens=2500,
        )
        for chunk in stream:
            text = chunk.choices[0].delta.content or ""
            if text:
                yield f"data: {json.dumps({'text': text}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"


async def _stream_claude(api_key: str, prompt: str):
    """Anthropic Claude."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        with client.messages.stream(
            model="claude-3-5-haiku-20241022",
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                yield f"data: {json.dumps({'text': text}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"


@router.post("/analyze")
async def analyze_reconcile(body: AnalyzeRequest):
    """재고 대사 AI 분석 — Gemini / Groq / Claude 스트리밍."""
    if not body.rows:
        raise HTTPException(400, "분석할 데이터가 없습니다")

    timing = _detect_timing_gaps(body.rows, body.period)
    patterns = _analyze_patterns(body.rows)
    prompt = _build_analysis_prompt(body, timing, patterns)

    # 우선순위: Gemini → Groq → Claude
    if body.gemini_api_key:
        gen = _stream_gemini(body.gemini_api_key, prompt)
    elif body.groq_api_key:
        gen = _stream_groq(body.groq_api_key, prompt)
    elif body.claude_api_key:
        gen = _stream_claude(body.claude_api_key, prompt)
    else:
        raise HTTPException(400, "AI API Key가 필요합니다 (Gemini / Groq / Claude 중 하나를 설정에서 입력)")

    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 특정 건 조회 ───────────────────────────────────────────────────────────────

@router.get("/item-search")
def item_search(
    token: str = Query(...),
    query: str = Query(...),        # 품목명 검색어 (일부 일치)
    from_date: str = Query(...),
    to_date: str = Query(...),
    location_id: Optional[int] = Query(None),
    location_ids: Optional[str] = Query(None),
):
    """특정 품목/건 조회 — BH와 OB 양쪽에서 검색어로 필터링해 원시 건별 내역 반환.

    사용 예: '이알히나' 검색 → BH에서 해당 품목 거래 건 + OB에서 해당 품목 건 나란히 표시.
    날짜 크로스·채널 차이 유무와 무관하게 건별 상세를 직접 확인 가능.
    """
    q_norm = re.sub(r'[^가-힣a-zA-Z0-9]', '', query).lower()
    if not q_norm:
        raise HTTPException(400, "검색어를 입력하세요")

    # location 파싱
    loc_id_list: list = []
    if location_ids:
        for v in location_ids.split(","):
            v = v.strip()
            if v.isdigit():
                loc_id_list.append(int(v))
    elif location_id:
        loc_id_list = [location_id]

    cfg = U.load_config()
    ourbox_id = cfg.get("ourbox_id")
    ourbox_pw = cfg.get("ourbox_pw")
    errors = []

    # ── BH 수집 (in + out + adjust) ────────────────────────────
    bh_results = []
    # in / out
    for tx_type in ("in", "out"):
        try:
            if loc_id_list:
                txs: list = []
                for lid in loc_id_list:
                    txs.extend(U.fetch_transactions(token, tx_type, from_date, to_date, lid))
            else:
                txs = U.fetch_transactions(token, tx_type, from_date, to_date, None)
            _enrich_bh_items(token, txs)
            for tx in txs:
                dt_str = (tx.get("created_at") or tx.get("reg_dt") or "")[:10]
                memo = (tx.get("memo") or "")[:80]
                for it in tx.get("items") or []:
                    # BH TX 상세 items 구조: {name, sku, quantity, item:{name,sku,...}}
                    nm = it.get("name", "") or (it.get("item") or {}).get("name", "") or ""
                    nm_n = re.sub(r'[^가-힣a-zA-Z0-9]', '', nm).lower()
                    if q_norm in nm_n:
                        qty_val = it.get("quantity") or it.get("qty") or 0
                        bh_results.append({
                            "date": dt_str,
                            "tx_type": tx_type,
                            "name": nm,
                            "qty": abs(int(qty_val)),
                            "sku": it.get("sku", "") or str((it.get("item") or {}).get("sku", "")),
                            "memo": memo,
                            "tx_id": tx.get("id"),
                        })
        except Exception as e:
            errors.append(f"BH {tx_type} 조회 실패: {str(e)[:60]}")

    # adjust — fetch_transactions(캐시) + _fetch_bh_tx_items(파일캐시)로 상세 보강
    # (기존엔 건별 /v1/txs/{id}를 캐시 없이 매번 호출해 매우 느렸음)
    try:
        adj_txs = U.fetch_transactions(token, "adjust", from_date, to_date, None)
        for tx in adj_txs:
            tx_id = tx.get("id")
            tx_time = tx.get("transaction_time") or tx.get("created_at") or ""
            dt_str = str(tx_time)[:10]
            items = tx.get("items") or _fetch_bh_tx_items(token, tx_id, "adjust")
            for it in items or []:
                nm = it.get("name", "") or (it.get("item") or {}).get("name", "") or ""
                nm_n = re.sub(r'[^가-힣a-zA-Z0-9]', '', nm).lower()
                if q_norm in nm_n:
                    qty_raw = it.get("quantity") or it.get("qty") or 0
                    bh_results.append({
                        "date": dt_str,
                        "tx_type": "adjustment",
                        "name": nm,
                        "qty": int(qty_raw),
                        "sku": it.get("sku", "") or str((it.get("item") or {}).get("sku", "")),
                        "memo": (tx.get("memo") or "")[:80],
                        "tx_id": tx_id,
                    })
    except Exception as e:
        errors.append(f"BH adjust 조회 실패: {str(e)[:60]}")

    # ── OurBox 수집 ──────────────────────────────────────────────
    ob_results = []
    if ourbox_id and ourbox_pw:
        ob_in_r, ob_out_r, ob_adj_r = [], [], []
        _collect_ourbox(ourbox_id, ourbox_pw, from_date, to_date, ob_in_r, ob_out_r, ob_adj_r, errors)

        def _ob_rows(raw: list, tx_type: str) -> list:
            rows_out = []
            qty_keys = {"in": ["in_qty","input_qty"], "out": ["out_qty","quantity"], "adjustment": ["adj_qty","quantity"]}.get(tx_type, ["quantity"])
            date_keys = {"in": ["in_dt","reg_dt"], "out": ["out_dt","reg_dt"], "adjustment": ["adj_dt","reg_dt"]}.get(tx_type, ["reg_dt"])
            for rec in raw:
                nm = html.unescape(str(rec.get("product_name") or rec.get("sale_prod_nm") or "").strip())
                nm_n = re.sub(r'[^가-힣a-zA-Z0-9]', '', nm).lower()
                if q_norm not in nm_n:
                    continue
                qty = 0
                for qk in qty_keys + ["out_qty","input_qty","adj_qty"]:
                    if rec.get(qk) not in (None, ""):
                        qty = int(float(str(rec.get(qk)).replace(",", "") or 0))
                        break
                date_str = ""
                for dk in date_keys:
                    v = str(rec.get(dk, "")).strip()
                    if v and v != "None":
                        date_str = v[:10].replace("/", "-")
                        break
                rows_out.append({
                    "date": date_str,
                    "tx_type": tx_type,
                    "name": nm,
                    "qty": qty,
                    "prod_cd": str(rec.get("prod_cd") or rec.get("product_code") or ""),
                    "channel": str(rec.get("channel") or rec.get("mall_name") or ""),
                    "invoice": str(rec.get("invoice") or ""),
                })
            return rows_out

        # 사장된 코드 제거
        ob_in_r  = [r for r in ob_in_r  if str(r.get("prod_cd") or r.get("product_code") or "") not in DEPRECATED_OB_CODES]
        ob_out_r = [r for r in ob_out_r if str(r.get("prod_cd") or r.get("product_code") or "") not in DEPRECATED_OB_CODES]
        # 기초재고 일괄입고 제거 (OurBox 도입 초기 재고등록 — BH 미대응)
        ob_in_r  = [r for r in ob_in_r  if str(r.get("input_code") or "").strip() not in OB_INITIAL_STOCK_INPUT_CODES]
        # 전산처리용: 출고(단품) → adj, 입고(세트) → 제외
        ob_out_normal = [r for r in ob_out_r if str(r.get("channel") or r.get("mall_name") or "") not in ASSEMBLY_CHANNELS]
        ob_out_asm    = [r for r in ob_out_r if str(r.get("channel") or r.get("mall_name") or "") in ASSEMBLY_CHANNELS]
        ob_in_normal  = [r for r in ob_in_r  if str(r.get("channel") or r.get("mall_name") or "") not in ASSEMBLY_CHANNELS]
        # ob_in_asm (세트 입고) 제외
        ob_results += _ob_rows(ob_in_normal,  "in")
        ob_results += _ob_rows(ob_out_normal, "out")
        ob_results += _ob_rows(ob_out_asm,    "adjustment")   # 단품 소진만
        ob_results += _ob_rows(ob_adj_r,      "adjustment")

    # 날짜순 정렬
    bh_results.sort(key=lambda x: x.get("date", ""))
    ob_results.sort(key=lambda x: x.get("date", ""))

    # ── 전표 대조 summary 집계 ──────────────────────────────────
    # product_mapping: OB 코드 → BH SKU (다중코드 정황·매핑 확인용)
    try:
        import receiving_db as _db
        _pm = _db.get_product_mapping_pairs()
        ob_code_to_sku = {p["ob_prod_cd"]: p["bh_sku"] for p in _pm if p.get("ob_prod_cd") and p.get("bh_sku")}
    except Exception:
        ob_code_to_sku = {}

    def _agg(rows: list, key_field: str) -> list:
        from collections import defaultdict as _dd
        g: dict = _dd(lambda: {"in": 0, "out": 0, "adjustment": 0, "name": ""})
        for r in rows:
            k = r.get(key_field) or ""
            g[k][r["tx_type"]] += r["qty"]
            if r.get("name"):
                g[k]["name"] = r["name"]
        out = []
        for k, v in g.items():
            row = {key_field: k, "name": v["name"],
                   "in": v["in"], "out": v["out"], "adjustment": v["adjustment"]}
            if key_field == "prod_cd":
                row["mapped_sku"] = ob_code_to_sku.get(k, "")
            out.append(row)
        out.sort(key=lambda x: -(abs(x["in"]) + abs(x["out"]) + abs(x["adjustment"])))
        return out

    bh_by_sku = _agg(bh_results, "sku")
    ob_by_code = _agg(ob_results, "prod_cd")

    def _totals(rows: list) -> dict:
        t = {"in": 0, "out": 0, "adjustment": 0}
        for r in rows:
            t[r["tx_type"]] += r["qty"]
        return t

    bh_total = _totals(bh_results)
    ob_total = _totals(ob_results)

    # OB 중복 입고 탐지: 동일 in 수량이 2개 이상 prod_cd에 걸친 경우
    from collections import defaultdict as _dd2
    in_by_qty: dict = _dd2(list)
    for c in ob_by_code:
        if c["in"] > 0:
            in_by_qty[c["in"]].append(c)
    ob_dup_inbound = [
        {"qty": qty,
         "codes": [c["prod_cd"] for c in lst],
         "names": [c["name"] for c in lst]}
        for qty, lst in in_by_qty.items() if len(lst) >= 2
    ]
    ob_dup_inbound.sort(key=lambda x: -x["qty"])

    return {
        "query": query,
        "from_date": from_date,
        "to_date": to_date,
        "bh": bh_results,
        "ob": ob_results,
        "errors": errors,
        "summary": {
            "bh_by_sku": bh_by_sku,
            "ob_by_code": ob_by_code,
            "bh_total": bh_total,
            "ob_total": ob_total,
            "ob_dup_inbound": ob_dup_inbound,
        },
    }


@router.get("/suggest-mapping")
def suggest_mapping(
    token: str = Query(...),
    from_date: str = Query(...),
    to_date: str = Query(...),
    min_score: int = Query(70),  # 이름 유사도 최소 점수
    limit: int = Query(50),
):
    """매핑되지 않은 BH·OB 상품 목록과 이름 유사도 기반 자동 제안.

    - BH: 현재 product_mapping·name_mapping에 없는 SKU
    - OB: 현재 매핑에 없는 prod_cd
    - 양쪽을 이름 유사도(rapidfuzz)로 페어 제안
    - 사용자가 MappingPage에서 선택해 추가할 수 있도록 목록 제공
    """
    import sys as _sys, os as _os
    _root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    import receiving_db as _db
    import utils_core as U
    from rapidfuzz import fuzz as _fuzz, process as _proc

    cfg = U.load_config()
    errors = []

    # ── 현재 매핑된 SKU / prod_cd 수집 ────────────────────────
    try:
        pm = _db.get_product_mapping_pairs()
        nm = _db.get_name_mapping_pairs()
    except Exception as e:
        pm, nm = [], []
        errors.append(f"매핑 DB 조회 실패: {e}")

    mapped_skus: set = {p["bh_sku"] for p in pm if p.get("bh_sku")}
    mapped_skus |= {p["bh_sku"] for p in nm if p.get("bh_sku")}
    mapped_ob_cds: set = {p["ob_prod_cd"] for p in pm if p.get("ob_prod_cd")}
    mapped_ob_names: set = {p["ob_name"] for p in nm if p.get("ob_name")}
    mapped_bh_names: set = {p["bh_name"] for p in nm if p.get("bh_name")}
    mapped_bh_names |= {p["bh_name"] for p in pm if p.get("bh_name")}

    # ── BH 미매핑 아이템 수집 ──────────────────────────────────
    bh_unmapped: list = []
    try:
        bh_in = U.fetch_transactions(token, "in",  from_date, to_date, None)
        bh_out= U.fetch_transactions(token, "out", from_date, to_date, None)
        bh_seen_sku: set = set()
        for tx in bh_in + bh_out:
            for it in tx.get("items", []):
                sku  = str(it.get("sku") or it.get("id") or "").strip()
                nm_  = str(it.get("name") or "").strip()
                if sku and sku not in mapped_skus and sku not in bh_seen_sku and nm_ not in mapped_bh_names:
                    bh_unmapped.append({"sku": sku, "name": nm_})
                    bh_seen_sku.add(sku)
    except Exception as e:
        errors.append(f"BH 조회 실패: {str(e)[:80]}")

    # ── OB 미매핑 상품 수집 (캐시에서) ────────────────────────
    ob_unmapped: list = []
    try:
        _load_ob_file_cache()
        cached_keys = list(_ob_file_cache.keys())
        ob_seen: set = set()
        for ck in cached_keys:
            entry = _ob_file_cache.get(ck, {})
            for rec in entry.get("in", []) + entry.get("out", []):
                cd = str(rec.get("product_code") or rec.get("prod_cd") or "").strip()
                nm_= html.unescape(str(rec.get("product_name") or rec.get("prod_nm") or "").strip())
                if cd and cd not in mapped_ob_cds and cd not in DEPRECATED_OB_CODES and cd not in ob_seen and nm_ not in mapped_ob_names:
                    ob_unmapped.append({"prod_cd": cd, "name": nm_})
                    ob_seen.add(cd)
    except Exception as e:
        errors.append(f"OB 캐시 조회 실패: {str(e)[:80]}")

    # ── 이름 유사도 기반 페어 제안 ────────────────────────────
    suggestions: list = []
    if bh_unmapped and ob_unmapped:
        ob_names = [o["name"] for o in ob_unmapped]
        for bh in bh_unmapped:
            if not bh["name"]:
                continue
            matches = _proc.extract(
                bh["name"], ob_names,
                scorer=_fuzz.token_set_ratio,
                limit=3, score_cutoff=min_score,
            )
            for match_name, score, idx in matches:
                ob = ob_unmapped[idx]
                suggestions.append({
                    "score": score,
                    "bh_sku": bh["sku"],
                    "bh_name": bh["name"],
                    "ob_prod_cd": ob["prod_cd"],
                    "ob_name": ob["name"],
                })
        suggestions.sort(key=lambda x: -x["score"])

    return {
        "bh_unmapped_count": len(bh_unmapped),
        "ob_unmapped_count": len(ob_unmapped),
        "bh_unmapped": bh_unmapped[:limit],
        "ob_unmapped": ob_unmapped[:limit],
        "suggestions": suggestions[:limit],
        "errors": errors,
    }

