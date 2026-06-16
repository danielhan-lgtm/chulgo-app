"""상품 이름 매핑 관리 — OurBox 이름 ↔ BoxHero SKU"""
import sys, os, json
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".."))

import utils_core as U
import receiving_db as db
import state

router = APIRouter()

BH_BASE = "https://rest.boxhero-app.com"


# ── BoxHero 상품 목록 ─────────────────────────────────────────

@router.get("/bh-items")
def get_bh_items(token: str = Query(...)):
    """BoxHero 전체 상품 목록 (id, name, sku)."""
    try:
        items = U.fetch_all_items_list(token)
        return {"items": items, "total": len(items)}
    except Exception as e:
        raise HTTPException(502, str(e))


# ── OurBox 상품 목록 ──────────────────────────────────────────

@router.get("/ob-products")
def get_ob_products():
    """OurBox 상품 마스터 목록 (code, name) — product_stock 기반 dedupe.

    매핑된 OB 이름은 mapped=True로 표시(다대다이므로 이름당 연결 수도 포함)."""
    cfg = U.load_config()
    try:
        import ourbox_api as api_mod
        client = api_mod.make_client(cfg)
        if not client:
            raise HTTPException(400, "OurBox API Key가 설정되지 않았습니다 (설정 페이지)")
        products = client.fetch_product_list()
    except HTTPException:
        raise
    except PermissionError as e:
        raise HTTPException(403, f"OurBox API IP 미등록: {e}")
    except Exception as e:
        raise HTTPException(502, f"OurBox 상품 조회 실패: {str(e)[:120]}")

    # 이름별 매핑 연결 수
    from collections import Counter
    link_counts = Counter(m["ob_name"] for m in db.get_name_mappings())
    for p in products:
        p["mapped_count"] = link_counts.get(p["name"], 0)
    return {"products": products, "total": len(products)}


# ── 채널 매핑 (OurBox 채널 ↔ BoxHero memo 키워드) ─────────────

def _date_range(days: int):
    from datetime import datetime, timedelta
    to_dt = datetime.now()
    from_dt = to_dt - timedelta(days=max(1, days))
    return from_dt.strftime("%Y-%m-%d"), to_dt.strftime("%Y-%m-%d")


_BH_CH_STOPWORDS = {
    "미리주문", "본방", "누락건", "미리", "주문", "처리", "전산처리", "오입력",
    "센터", "호법센터", "재고", "출고", "입고", "건", "월", "일", "샘플",
}


def _extract_bh_channel_tokens(memo: str) -> list:
    """BoxHero memo에서 채널 키워드 후보 추출.
    예) '# 스마트 스토어 (dj&a) 5월 23일' → ['스마트', '스토어', '(dj&a)']
        '# 롯데, 현대, 씨제이 미리주문' → ['롯데', '현대', '씨제이']
    """
    import re
    if not memo:
        return []
    m = memo.strip().lstrip("#").strip()
    # 날짜 패턴 제거 (5월 23일, 2026-05-26, 26-03-23)
    m = re.sub(r"\d{1,4}[\-./]\d{1,2}([\-./]\d{1,2})?", " ", m)
    m = re.sub(r"\d+\s*월\s*\d+\s*일?", " ", m)
    m = re.sub(r"\d+\s*개입", " ", m)
    tokens = re.split(r"[\s,/]+", m)
    out = []
    for t in tokens:
        t = t.strip().strip("()[]")
        if not t or t.isdigit() or len(t) < 2:
            continue
        if t in _BH_CH_STOPWORDS:
            continue
        out.append(t)
    return out


@router.get("/ob-channels")
def get_ob_channels(days: int = Query(30), refresh: bool = Query(False)):
    """OurBox 최근 출고에 등장한 distinct 채널명 (직접 API 호출, 캐시).

    첫 호출 ~30초, 이후 캐시에서 즉시 반환. refresh=true로 강제 갱신.
    """
    import requests as _req, html as _html

    # 캐시 반환 (refresh 아닌 경우)
    if state.ob_channels_cache and not refresh:
        mapped = {m["ob_channel"] for m in db.get_channel_mappings()}
        return {"channels": [{"name": c, "mapped": c in mapped} for c in state.ob_channels_cache],
                "total": len(state.ob_channels_cache), "cached": True}

    cfg = U.load_config()
    access_key = cfg.get("ourbox_access_key", "").strip()
    secret_key = cfg.get("ourbox_secret_key", "").strip()
    if not access_key or not secret_key:
        raise HTTPException(400, "OurBox API Key가 설정되지 않았습니다")

    headers = {"api_access_key": access_key, "api_secret_key": secret_key, "Content-Type": "application/json"}
    fr, to = _date_range(days)

    seen: set = set()
    page = 1
    max_pages = 20  # 최대 페이지 (20*100=2000행, ~30초)
    while page <= max_pages:
        try:
            r = _req.post(
                "https://api.ourbox.co.kr/api/wms/out/out_perf_period",
                headers=headers,
                json={"out_dt_type": "1", "out_dt_from": fr, "out_dt_to": to, "page": page},
                timeout=20,
            )
            if r.status_code == 401:
                raise PermissionError("OurBox API IP 미등록")
            r.raise_for_status()
            data = r.json()
            for row in data.get("datas") or []:
                c = (row.get("channel") or row.get("mall_name") or "").strip()
                if c:
                    seen.add(_html.unescape(c))
            if page >= (data.get("total_page") or 1):
                break
            page += 1
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except Exception as e:
            import logging
            logging.warning(f"ob-channels page {page} error: {e}")
            break

    channels = sorted(seen)
    state.ob_channels_cache = channels  # 캐시 저장
    mapped = {m["ob_channel"] for m in db.get_channel_mappings()}
    return {"channels": [{"name": c, "mapped": c in mapped} for c in channels], "total": len(channels), "cached": False}


@router.get("/bh-channels")
def get_bh_channels(token: str = Query(...), days: int = Query(14)):
    """BoxHero 최근 입출고의 거래처(partner) 목록 (건수순)."""
    from collections import Counter
    fr, to = _date_range(days)
    counter: Counter = Counter()
    for tx_type in ("in", "out"):
        try:
            txs = U.fetch_transactions(token, tx_type, fr, to, None)
        except Exception:
            continue
        for tx in txs:
            p = tx.get("partner") or {}
            pname = p.get("name", "") if isinstance(p, dict) else ""
            if pname:
                counter[pname] += 1
    mapped = {m["bh_keyword"] for m in db.get_channel_mappings()}
    keywords = [
        {"keyword": k, "count": n, "sample": "", "mapped": k in mapped}
        for k, n in counter.most_common()
    ]
    return {"keywords": keywords, "total": len(keywords)}


@router.get("/channel-list")
def list_channel_mappings():
    return db.get_channel_mappings()


class ChannelLinkBody(BaseModel):
    ob_channel: str
    bh_keyword: str
    confirmed: int = 1


@router.post("/channel-link")
def create_channel_link(body: ChannelLinkBody):
    if not body.ob_channel.strip() or not body.bh_keyword.strip():
        raise HTTPException(400, "ob_channel과 bh_keyword가 필요합니다")
    db.upsert_channel_mapping(body.ob_channel.strip(), body.bh_keyword.strip(), body.confirmed)
    return {"ok": True}


class ChannelUnlinkBody(BaseModel):
    ob_channel: str
    bh_keyword: str


@router.post("/channel-unlink")
def remove_channel_link(body: ChannelUnlinkBody):
    db.delete_channel_mapping_pair(body.ob_channel, body.bh_keyword)
    return {"ok": True}


@router.delete("/channel-link/{mapping_id}")
def delete_channel_link(mapping_id: int):
    db.delete_channel_mapping_by_id(mapping_id)
    return {"ok": True}


# ── 매핑 CRUD ─────────────────────────────────────────────────

@router.get("/list")
def list_mappings():
    """저장된 전체 이름 매핑 목록."""
    return db.get_name_mappings()


class SaveMappingBody(BaseModel):
    ob_name: str
    bh_sku: str
    bh_name: str = ""
    score: float = 0.0
    method: str = "manual"
    confirmed: int = 1


@router.post("/save")
def save_mapping(body: SaveMappingBody):
    db.upsert_name_mapping(
        ob_name=body.ob_name,
        bh_sku=body.bh_sku,
        bh_name=body.bh_name,
        score=body.score,
        method=body.method,
        confirmed=body.confirmed,
    )
    return {"ok": True}


@router.post("/confirm/{ob_name}")
def confirm_mapping(ob_name: str):
    db.confirm_name_mapping(ob_name)
    return {"ok": True}


@router.delete("/delete/{ob_name}")
def delete_mapping(ob_name: str):
    import urllib.parse
    db.delete_name_mapping(urllib.parse.unquote(ob_name))
    return {"ok": True}


# ── 다대다 링크 (쌍/ID 기반) ──────────────────────────────────

class LinkBody(BaseModel):
    ob_name: str
    bh_sku: str
    bh_name: str = ""
    confirmed: int = 1


@router.post("/link")
def create_link(body: LinkBody):
    """OB 이름 ↔ BH SKU 쌍 연결 생성 (다대다, 중복 쌍은 갱신)."""
    if not body.ob_name.strip() or not body.bh_sku.strip():
        raise HTTPException(400, "ob_name과 bh_sku가 필요합니다")
    db.upsert_name_mapping(
        ob_name=body.ob_name.strip(),
        bh_sku=body.bh_sku.strip(),
        bh_name=body.bh_name,
        score=100.0,
        method="manual",
        confirmed=body.confirmed,
    )
    return {"ok": True}


class UnlinkBody(BaseModel):
    ob_name: str
    bh_sku: str


@router.post("/unlink")
def remove_link(body: UnlinkBody):
    """특정 (ob_name, bh_sku) 쌍 연결 해제."""
    db.delete_name_mapping_pair(body.ob_name, body.bh_sku)
    return {"ok": True}


@router.post("/confirm-id/{mapping_id}")
def confirm_link(mapping_id: int):
    db.confirm_name_mapping_by_id(mapping_id)
    return {"ok": True}


@router.delete("/link/{mapping_id}")
def delete_link_by_id(mapping_id: int):
    db.delete_name_mapping_by_id(mapping_id)
    return {"ok": True}


# ── 자동 퍼지 매칭 ───────────────────────────────────────────

@router.post("/auto-match")
def auto_match(
    token: str = Query(...),
    threshold: int = Query(70),
    ob_names: Optional[List[str]] = None,
):
    """OurBox 상품명 목록을 BoxHero 상품명과 퍼지 매칭."""
    from fastapi import Body

    # BoxHero 상품 목록
    try:
        bh_items = U.fetch_all_items_list(token)
    except Exception as e:
        raise HTTPException(502, f"BoxHero 상품 조회 실패: {e}")

    if not bh_items:
        raise HTTPException(400, "BoxHero 상품이 없습니다")

    # 정규화된 BH 이름 → (sku, name) 매핑
    bh_lookup: dict = {}
    bh_norm_list: list = []
    for item in bh_items:
        norm = U.normalize(item.get("name", ""))
        bh_lookup[norm] = {"sku": item.get("sku", ""), "name": item.get("name", "")}
        bh_norm_list.append(norm)

    # OurBox 이름 목록: 요청 body에서 받거나 기존 매핑 DB에서 추출
    if not ob_names:
        # 기존 수신 아이템에서 미매핑 이름 추출
        all_items = []
        for rec in db.get_all():
            all_items.extend(db.get_items(rec["put_sno"]))
        ob_names = list({i["sale_prod_nm"] for i in all_items if i.get("sale_prod_nm") and not i.get("boxhero_sku")})

    results = []
    existing = {m["ob_name"] for m in db.get_name_mappings()}

    for ob_name in ob_names:
        if not ob_name:
            continue
        norm_ob = U.normalize(ob_name)
        best, score = U.best_match(norm_ob, bh_norm_list)
        if score >= threshold and best in bh_lookup:
            bh = bh_lookup[best]
            is_new = ob_name not in existing
            if is_new:
                db.upsert_name_mapping(
                    ob_name=ob_name,
                    bh_sku=bh["sku"],
                    bh_name=bh["name"],
                    score=score,
                    method="fuzzy",
                    confirmed=0,
                )
            results.append({
                "ob_name": ob_name,
                "bh_sku": bh["sku"],
                "bh_name": bh["name"],
                "score": score,
                "is_new": is_new,
            })
        else:
            results.append({
                "ob_name": ob_name,
                "bh_sku": "",
                "bh_name": "",
                "score": score,
                "is_new": False,
                "unmatched": True,
            })

    results.sort(key=lambda x: -x["score"])
    return {"results": results, "matched": sum(1 for r in results if r.get("bh_sku")), "total": len(results)}


# ── AI 매핑 제안 (스트리밍) ───────────────────────────────────

class AiMatchRequest(BaseModel):
    ob_names: List[str]      # OurBox 상품명 목록
    bh_items: List[dict]     # [{sku, name}, ...]
    gemini_api_key: str = ""
    groq_api_key: str = ""
    claude_api_key: str = ""


async def _stream_ai_match(prompt: str, req: AiMatchRequest):
    import requests as _req

    # Gemini 우선
    if req.gemini_api_key:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"gemini-2.5-flash:streamGenerateContent?alt=sse&key={req.gemini_api_key}")
        payload = {"contents": [{"parts": [{"text": prompt}]}],
                   "generationConfig": {"maxOutputTokens": 3000}}
        try:
            resp = _req.post(url, json=payload, stream=True, timeout=120)
            if resp.ok:
                for raw in resp.iter_lines(decode_unicode=True):
                    if not raw or not raw.startswith("data:"): continue
                    raw = raw[5:].strip()
                    if not raw or raw == "[DONE]": continue
                    try:
                        chunk = json.loads(raw)
                        parts = (chunk.get("candidates", [{}])[0]
                                 .get("content", {}).get("parts", []))
                        for p in parts:
                            text = p.get("text", "")
                            if text:
                                yield f"data: {json.dumps({'text': text}, ensure_ascii=False)}\n\n"
                    except Exception:
                        pass
                yield "data: [DONE]\n\n"
                return
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield "data: [DONE]\n\n"
            return

    yield f"data: {json.dumps({'error': 'AI API Key 없음'})}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/ai-suggest")
async def ai_suggest(body: AiMatchRequest):
    """AI(Gemini)로 OurBox ↔ BoxHero 상품 매핑 제안."""
    if not body.ob_names or not body.bh_items:
        raise HTTPException(400, "ob_names와 bh_items가 필요합니다")

    # 이미 매핑된 것 제외
    existing_map = db.get_name_mapping_dict()
    unmatched_ob = [n for n in body.ob_names if n not in existing_map][:50]

    bh_list = "\n".join(f"  - SKU:{i.get('sku','')} | 이름:{i.get('name','')}"
                        for i in body.bh_items[:100])
    ob_list = "\n".join(f"  - {n}" for n in unmatched_ob)

    prompt = f"""당신은 물류 상품 매핑 전문가입니다.
아래 두 시스템의 상품 목록을 보고, 같은 실제 상품에 해당하는 것끼리 매핑해 주세요.
이름 표기가 다소 달라도 같은 상품이면 매핑하세요.

## OurBox 상품명 목록 (매핑 필요)
{ob_list}

## BoxHero 상품 목록 (SKU | 이름)
{bh_list}

## 출력 형식 (JSON 배열만, 설명 없이)
[
  {{"ob_name": "OurBox 이름", "bh_sku": "BoxHero SKU", "bh_name": "BoxHero 이름", "confidence": 95, "reason": "이름이 동일한 상품"}},
  ...
]

매핑이 불확실하면 confidence를 낮게 (0-100), 확실하면 높게 설정하세요.
매핑이 전혀 불가능한 항목은 제외하세요."""

    return StreamingResponse(
        _stream_ai_match(prompt, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
