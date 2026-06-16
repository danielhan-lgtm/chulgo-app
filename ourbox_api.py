"""아워박스 REST API 클라이언트 (api.ourbox.co.kr)

확정된 인증 방식 및 엔드포인트:
  - 인증: Header  api_access_key / api_secret_key
  - 스펙: GET  /api-docs  (인증 불필요, 공개)
  - 모든 데이터 엔드포인트: POST + JSON body

⚠️ IP 화이트리스트 필요: OurBox OMS 관리자 페이지에서 서버 IP 등록 필요.
   미등록 시 401 {"code": 4013, "message": "You don't have permission..."}
"""
from __future__ import annotations
import html
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

OURBOX_API = "https://api.ourbox.co.kr"

# 페이지당 최대 건수 (API 기본값으로 추정, 실제 응답에 따라 조정)
_PAGE_SIZE = 100


class OurboxApiClient:
    def __init__(self, access_key: str, secret_key: str):
        self._access_key = access_key
        self._secret_key = secret_key
        self._headers = {
            "api_access_key": access_key,
            "api_secret_key": secret_key,
            "Content-Type": "application/json",
        }

    # ── 내부 요청 헬퍼 ────────────────────────────────────────────

    def _post(self, path: str, body: dict) -> dict:
        url = OURBOX_API + path
        import time as _t
        last_exc = None
        for attempt in range(3):  # ReadTimeout/일시오류 재시도 (병렬 throttle 대비)
            try:
                r = requests.post(url, headers=self._headers, json=body, timeout=45)
                if r.status_code == 401:
                    try:
                        detail = r.json()
                    except Exception:
                        detail = r.text
                    raise PermissionError(
                        f"OurBox API 인증 실패 (IP 화이트리스트 확인 필요): {detail}"
                    )
                r.raise_for_status()
                return r.json()
            except PermissionError:
                raise
            except requests.exceptions.RequestException as e:
                last_exc = e
                _t.sleep(1.5 * (attempt + 1))
        raise last_exc

    def _paginate(self, path: str, base_body: dict) -> list:
        """페이지네이션으로 전체 목록 수집.

        OurBox 응답 구조 (확정):
          {"result": true, "code": "200", "total_cnt": N, "total_page": P,
           "current_page": 1, "datas": [...]}   ← 입출고
          조정 이력은 데이터 키가 "adjust".
        """
        def _extract(data):
            if isinstance(data, list):
                return data
            return (
                data.get("datas") or data.get("adjust") or data.get("product_stock_info")
                or data.get("data") or data.get("list") or data.get("items")
                or data.get("results") or []
            )

        # 1페이지로 total_page 파악
        first = self._post(path, {**base_body, "page": 1})
        items1 = _extract(first)
        all_items: list = list(items1)
        total_page = first.get("total_page") if isinstance(first, dict) else None

        if total_page is None:
            # total_page 없으면 기존 순차 휴리스틱 (페이지크기 미만이면 종료)
            page = 2
            if len(items1) >= _PAGE_SIZE:
                while True:
                    data = self._post(path, {**base_body, "page": page})
                    items = _extract(data)
                    if not items:
                        break
                    all_items.extend(items)
                    if len(items) < _PAGE_SIZE:
                        break
                    page += 1
        elif total_page > 1:
            # 2~total_page 병렬 수집 (순차 라운드트립 제거 → 대폭 단축)
            import concurrent.futures as _cf
            pages = list(range(2, total_page + 1))
            results: dict = {}
            with _cf.ThreadPoolExecutor(max_workers=min(6, len(pages))) as ex:
                futs = {ex.submit(self._post, path, {**base_body, "page": p}): p for p in pages}
                for f in _cf.as_completed(futs):
                    try:
                        results[futs[f]] = _extract(f.result())
                    except Exception:
                        results[futs[f]] = []
            for p in pages:
                all_items.extend(results.get(p, []))
        logger.info(f"OurBox {path} → 총 {len(all_items)}건 수집 (total_page={total_page})")
        return all_items

    # ── 데이터 수집 ──────────────────────────────────────────────

    def fetch_inbounds(self, from_date: str, to_date: str) -> list:
        """입고 실적 조회 (POST /api/wms/put/put_perf).

        ⚠️ 본섭 제약: 날짜 범위 최대 7일 → 7일 단위로 자동 분할.
        """
        from datetime import datetime, timedelta

        import concurrent.futures as _cf
        start = datetime.fromisoformat(from_date)
        end = datetime.fromisoformat(to_date)

        # 7일 구간 목록 생성
        chunks = []
        cur = start
        while cur <= end:
            chunk_end = min(cur + timedelta(days=6), end)
            chunks.append((cur.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
            cur = chunk_end + timedelta(days=1)

        def _fetch_chunk(rng):
            f, t = rng
            try:
                return self._paginate("/api/wms/put/put_perf",
                                      {"input_dt_type": "1", "input_dt_from": f, "input_dt_to": t})
            except Exception as e:
                logger.warning(f"입고 구간 {f}~{t} 실패: {e}")
                return []

        # 구간 병렬 수집 (13구간이면 순차 → 병렬로 대폭 단축)
        all_items: list = []
        with _cf.ThreadPoolExecutor(max_workers=min(6, len(chunks) or 1)) as ex:
            for items in ex.map(_fetch_chunk, chunks):
                all_items.extend(items)

        logger.info(f"입고 전체 {len(all_items)}건 수집 ({len(chunks)}구간 병렬)")
        return all_items

    def fetch_outbounds(self, from_date: str, to_date: str) -> list:
        """출고 실적 조회 (POST /api/wms/out/out_perf_period).

        Request body:
          out_dt_type: "1" (출고일 기준)
          out_dt_from / out_dt_to: YYYY-MM-DD
        """
        return self._paginate(
            "/api/wms/out/out_perf_period",
            {
                "out_dt_type": "1",
                "out_dt_from": from_date,
                "out_dt_to": to_date,
            },
        )

    def _get_ob_code_to_name(self) -> dict:
        """OB sales_product_company_code → product_name 매핑 (캐시)."""
        if hasattr(self, "_code_map"):
            return self._code_map
        import html as _h
        self._code_map = {}
        try:
            for r in self.fetch_stock():
                cc = str(r.get("sales_product_company_code","")).strip()
                nm = _h.unescape(str(r.get("product_name","")).strip())
                if cc and nm: self._code_map[cc] = nm
        except Exception:
            pass
        return self._code_map

    def fetch_adjustments(self, from_date: str, to_date: str) -> list:
        """재고 조정 이력 조회 (POST /api/wms/stock/stock_adj_hist).

        input_type: "2" = 기간 전체 조회 (input_type=1은 stock_adj_sno 필수)
        """
        return self._paginate(
            "/api/wms/stock/stock_adj_hist",
            {
                "input_type": "2",
                "start_reg_dt": from_date,
                "end_reg_dt": to_date,
            },
        )

    def fetch_stock(self, product_codes: Optional[list] = None) -> list:
        """현재 재고 현황 조회 (POST /api/oms/info/product_stock)."""
        return self._paginate(
            "/api/oms/info/product_stock",
            {"sales_product_codes": product_codes or []},
        )

    def fetch_product_list(self) -> list:
        """OurBox 상품 마스터 목록 → [{code, name}] (코드+이름 dedupe).

        product_stock 응답은 lot/유통기한별로 같은 상품이 중복 등장하므로
        (sales_product_code, product_name) 기준으로 중복 제거한다.
        """
        rows = self.fetch_stock()
        seen: set = set()
        products: list = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            code = str(r.get("sales_product_code") or "").strip()
            name = html.unescape(str(r.get("product_name") or "").strip())
            if not code and not name:
                continue
            key = (code, name)
            if key in seen:
                continue
            seen.add(key)
            products.append({"code": code, "name": name})
        products.sort(key=lambda x: x["name"])
        return products

    def fetch_out_channels(self, from_date: str, to_date: str) -> list:
        """기간 내 출고에 등장한 distinct 채널명 목록.

        전체 페이지를 순회하되, 채널명만 추출하고 나머지는 무시.
        채널 수가 포화되면 일찍 종료 (3페이지 연속 신규 채널 없으면 중단).
        """
        seen: set = set()
        no_new = 0
        page = 1
        while True:
            try:
                data = self._post(
                    "/api/wms/out/out_perf_period",
                    {"out_dt_type": "1", "out_dt_from": from_date, "out_dt_to": to_date, "page": page},
                )
                rows = data.get("datas") or []
                before = len(seen)
                for r in rows:
                    c = (r.get("channel") or r.get("mall_name") or "").strip()
                    if c:
                        seen.add(html.unescape(c))
                if len(seen) == before:
                    no_new += 1
                else:
                    no_new = 0
                # 조기 종료: 10페이지 연속 신규 채널 없음 또는 마지막 페이지
                if no_new >= 10 or page >= (data.get("total_page") or 1):
                    break
                page += 1
            except Exception:
                break
        return sorted(seen)

    # ── 탐색 ─────────────────────────────────────────────────────

    def probe(self) -> dict:
        """API 연결 상태 + 스펙 경로 반환 (설정 페이지 테스트 버튼용)."""
        result: dict = {
            "auth": "api_access_key + api_secret_key",
            "spec_paths": [],
            "test_result": None,
            "error": None,
        }

        # 스펙 조회 (인증 불필요)
        try:
            r = requests.get(OURBOX_API + "/api-docs", timeout=10)
            spec = r.json()
            result["spec_paths"] = list(spec.get("paths", {}).keys())
        except Exception as e:
            result["error"] = f"스펙 조회 실패: {e}"

        # 실제 인증 테스트
        try:
            data = self._post("/api/oms/info/product_stock", {"page": 1})
            result["test_result"] = "인증 성공"
            result["stock_sample"] = str(data)[:200]
        except PermissionError as e:
            result["test_result"] = f"IP 화이트리스트 오류: {e}"
        except Exception as e:
            result["test_result"] = f"오류: {e}"

        return result


# ── 팩토리 ────────────────────────────────────────────────────────

def make_client(cfg: dict) -> Optional[OurboxApiClient]:
    """config.json에서 OurboxApiClient 생성. access_key 없으면 None."""
    access_key = cfg.get("ourbox_access_key", "").strip()
    secret_key = cfg.get("ourbox_secret_key", "").strip()
    if not access_key or not secret_key:
        return None
    return OurboxApiClient(access_key, secret_key)
