"""쿠팡 공급사(로켓/제트) OpenAPI 클라이언트.

표준 쿠팡 OpenAPI HMAC 서명(CEA HmacSHA256) 방식으로 요청한다.
발주서(PO) 조회 엔드포인트/응답 필드는 계정·문서마다 차이가 있어
설정값(coupang_po_path 등)으로 조정 가능하게 했고, 응답 파싱은
흔한 필드명을 폭넓게 시도하는 방어적 방식으로 작성했다.

설정(config) 키:
  coupang_access_key, coupang_secret_key, coupang_vendor_id
  coupang_host       (기본 https://api-gateway.coupang.com)
  coupang_po_path    (발주서 조회 경로; {vendorId} 치환 지원)
  coupang_date_param (조회 시작/종료 쿼리 파라미터명 베이스, 예: 'created' → createdAtFrom/To)
"""
import hmac
import hashlib
import json
import re
from datetime import datetime, timezone

import requests

DEFAULT_HOST = "https://api-gateway.coupang.com"


def _gen_authorization(method: str, path: str, query: str, access_key: str, secret_key: str) -> str:
    """쿠팡 표준 HMAC 서명 헤더 생성.
    message = signed_date + method + path + query (query는 '?' 제외)."""
    signed_date = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
    message = signed_date + method + path + query
    signature = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return (f"CEA algorithm=HmacSHA256, access-key={access_key}, "
            f"signed-date={signed_date}, signature={signature}")


def call(cfg: dict, method: str, path: str, query: str = "") -> dict:
    """서명된 쿠팡 API 요청. 성공 시 JSON(dict) 반환, 실패 시 RuntimeError."""
    access_key = (cfg.get("coupang_access_key") or "").strip()
    secret_key = (cfg.get("coupang_secret_key") or "").strip()
    host = (cfg.get("coupang_host") or DEFAULT_HOST).rstrip("/")
    if not (access_key and secret_key):
        raise RuntimeError("쿠팡 Access Key/Secret Key가 설정되지 않았습니다.")
    auth = _gen_authorization(method, path, query, access_key, secret_key)
    url = host + path + (("?" + query) if query else "")
    r = requests.request(method, url, headers={
        "Authorization": auth,
        "Content-Type": "application/json;charset=UTF-8",
    }, timeout=30)
    if not r.ok:
        raise RuntimeError(f"쿠팡 API {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except Exception:
        raise RuntimeError(f"쿠팡 API 응답 파싱 실패: {r.text[:200]}")


def _resolve_po_path(cfg: dict) -> str:
    path = (cfg.get("coupang_po_path") or "").strip()
    if not path:
        raise RuntimeError("발주서 조회 경로(coupang_po_path)가 설정되지 않았습니다. "
                           "공급사 OpenAPI 문서의 발주서 조회 경로를 설정에 입력하세요.")
    vendor = (cfg.get("coupang_vendor_id") or "").strip()
    return path.replace("{vendorId}", vendor).replace("{vendorCode}", vendor)


def _num(v) -> int:
    try:
        return int(float(re.sub(r"[^\d.\-]", "", str(v)) or 0))
    except Exception:
        return 0


def _norm_date(v) -> str:
    s = str(v or "")
    m = re.search(r"(\d{4})[\-/.](\d{1,2})[\-/.](\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def _walk_items(data):
    """응답 JSON에서 발주 라인 아이템 배열을 폭넓게 탐색."""
    if isinstance(data, dict):
        # 흔한 컨테이너 키
        for key in ("data", "content", "items", "orderItems", "purchaseOrders",
                    "shipmentBoxList", "orderList", "result"):
            if key in data:
                got = _walk_items(data[key])
                if got:
                    return got
        # dict 자체가 라인이면 (상품명/수량 비슷한 키 보유)
        return []
    if isinstance(data, list):
        # 리스트 원소가 라인(상품/수량 키 보유)인지 확인
        flat = []
        for el in data:
            if isinstance(el, dict):
                # 중첩된 items가 있으면 펼침
                nested = None
                for k in ("orderItems", "items", "productList", "lines"):
                    if isinstance(el.get(k), list):
                        nested = el.get(k)
                        # 상위 레벨 날짜를 전파
                        date_hint = _find_first(el, ("입고예정일", "shipmentDate", "deliveryDate",
                                                     "inboundDate", "orderedAt", "createdAt"))
                        for ln in nested:
                            if isinstance(ln, dict) and date_hint and not _find_first(ln, ("입고예정일", "shipmentDate", "deliveryDate", "inboundDate")):
                                ln = {**ln, "_date_hint": date_hint}
                            flat.append(ln)
                        break
                if nested is None:
                    flat.append(el)
        return flat
    return []


def _find_first(d: dict, keys):
    for k in keys:
        for kk in d:
            if str(kk).lower() == k.lower() or k in str(kk):
                if d[kk] not in (None, ""):
                    return d[kk]
    return None


def parse_purchase_orders(data: dict) -> list:
    """발주서 응답 → [{name, qty, date, barcode}] 정규화 (방어적 필드 매핑)."""
    rows = []
    for ln in _walk_items(data):
        if not isinstance(ln, dict):
            continue
        name = _find_first(ln, ("productName", "sellerProductName", "vendorItemName",
                                "상품명", "품명", "itemName", "name"))
        qty = _find_first(ln, ("orderedQuantity", "confirmedQuantity", "quantity",
                               "발주수량", "확정수량", "shippingCount", "qty"))
        date = _find_first(ln, ("입고예정일", "shipmentDate", "deliveryDate", "inboundDate",
                                "expectedDeliveryDate", "orderedAt", "createdAt")) or ln.get("_date_hint")
        barcode = _find_first(ln, ("barcode", "바코드", "eanCode")) or ""
        if not name:
            continue
        q = _num(qty)
        d = _norm_date(date)
        if q <= 0 or not d:
            continue
        rows.append({"name": str(name).strip(), "qty": q, "date": d, "barcode": str(barcode).strip()})
    return rows


def fetch_purchase_orders(cfg: dict, from_date: str, to_date: str) -> tuple:
    """기간 발주서 조회 → (rows, raw_sample). raw_sample은 검증용 원본 일부."""
    path = _resolve_po_path(cfg)
    base = (cfg.get("coupang_date_param") or "createdAt").strip()  # createdAt → createdAtFrom/To
    # 흔한 쿼리 형태: {base}From=YYYY-MM-DD&{base}To=YYYY-MM-DD
    query = f"{base}From={from_date}&{base}To={to_date}"
    data = call(cfg, "GET", path, query)
    rows = parse_purchase_orders(data)
    raw_sample = json.dumps(data, ensure_ascii=False)[:1500]
    return rows, raw_sample
