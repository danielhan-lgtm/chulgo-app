"""아워박스 거래 캐시 백필 — 한 달치를 주 단위·페이지 순차로 천천히 받아 월 캐시 파일을 만든다.

백엔드의 병렬 수집(7일 구간 4개 × 페이지 6개 동시)이 아워박스 API 응답 지연으로 끊길 때 쓰는 우회 경로.
  python -X utf8 scripts/ob_backfill.py 2026-05 2026-06
결과: backend/data/ob_txs_cache/2026-05-01_2026-05-31.json 등 (reconcile 월 캐시 형식과 동일).
주 단위 진행분은 backend/data/ob_backfill/ 에 저장돼 재실행 시 이어서 받는다.
"""
import sys, os, json, time, calendar
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)

import utils_core as U
import ourbox_api as api_mod
from routers import reconcile as R

PART_DIR = os.path.join(ROOT, "backend", "data", "ob_backfill")
os.makedirs(PART_DIR, exist_ok=True)


def _log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def _paginate_seq(client, path, body, label, max_attempts: int = 6):
    """페이지 순차 수집. 페이지마다 최대 max_attempts회 재시도(점증 대기)."""
    items = []
    page = 1
    total_page = None
    while True:
        data = None
        for attempt in range(max_attempts):
            try:
                data = client._post(path, {**body, "page": page})
                break
            except Exception as e:
                wait = 10 * (attempt + 1)
                _log(f"  {label} p{page} 실패({str(e)[:60]}) → {wait}s 후 재시도")
                time.sleep(wait)
        if data is None:
            raise RuntimeError(f"{label} p{page} {max_attempts}회 실패")
        rows = (data.get("datas") or data.get("adjust") or data.get("data") or []) if isinstance(data, dict) else data
        items.extend(rows)
        if total_page is None:
            total_page = int(data.get("total_page") or 1) if isinstance(data, dict) else 1
            _log(f"  {label}: 총 {total_page}페이지 / {data.get('total_cnt') if isinstance(data, dict) else '?'}건")
        if page >= total_page or not rows:
            break
        page += 1
        time.sleep(0.4)   # API에 숨 돌릴 시간
    return items


def _weeks(y, m):
    last = calendar.monthrange(y, m)[1]
    cur = date(y, m, 1)
    end = date(y, m, last)
    while cur <= end:
        e = min(cur + timedelta(days=6), end)
        yield cur.isoformat(), e.isoformat()
        cur = e + timedelta(days=1)


def backfill_month(client, ym: str):
    y, m = int(ym[:4]), int(ym[5:7])
    last = calendar.monthrange(y, m)[1]
    m_from, m_to = f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}"
    key = f"{m_from}|{m_to}"
    if R._ob_cache_read(key):
        _log(f"{ym}: 이미 캐시 있음 → 건너뜀")
        return
    part_path = os.path.join(PART_DIR, f"{ym}.json")
    part = {"in": {}, "out": {}, "adj": None}
    if os.path.exists(part_path):
        part = json.load(open(part_path, encoding="utf-8"))
        _log(f"{ym}: 진행분 복원 (입고 {len(part['in'])}주, 출고 {len(part['out'])}주)")

    def _save():
        tmp = part_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(part, f, ensure_ascii=False)
        for attempt in range(8):   # Windows에서 백신/동기화가 파일을 잠시 잡으면 교체가 거부됨 → 잠깐 기다려 재시도
            try:
                os.replace(tmp, part_path)
                return
            except PermissionError:
                time.sleep(1.5 * (attempt + 1))
        os.replace(tmp, part_path)

    for wf, wt in _weeks(y, m):
        wk = f"{wf}|{wt}"
        if wk not in part["out"]:
            _log(f"{ym} 출고 {wf}~{wt}")
            part["out"][wk] = _paginate_seq(client, "/api/wms/out/out_perf_period",
                                            {"out_dt_type": "1", "out_dt_from": wf, "out_dt_to": wt}, "출고")
            _save()
        if wk not in part["in"]:
            _log(f"{ym} 입고 {wf}~{wt}")
            part["in"][wk] = _paginate_seq(client, "/api/wms/put/put_perf",
                                           {"input_dt_type": "1", "input_dt_from": wf, "input_dt_to": wt}, "입고")
            _save()
    if part["adj"] is None:
        # 조정 이력(stock_adj_hist)은 월 단위 조회가 매우 느려 시간 초과가 잦다 → 주 단위, 실패하면 빈 값으로 넘어간다.
        # (조정 레코드는 상품코드가 비어 있어 품목별 대조엔 쓰이지 않는다)
        adj_all = []
        for wf, wt in _weeks(y, m):
            try:
                _log(f"{ym} 조정 {wf}~{wt}")
                adj_all += _paginate_seq(client, "/api/wms/stock/stock_adj_hist",
                                         {"input_type": "2", "start_reg_dt": wf, "end_reg_dt": wt}, "조정", max_attempts=2)
            except Exception as e:
                _log(f"  조정 {wf}~{wt} 포기: {str(e)[:60]}")
        part["adj"] = adj_all
        _save()

    in_list = [r for w in sorted(part["in"]) for r in part["in"][w]]
    out_list = [r for w in sorted(part["out"]) for r in part["out"][w]]
    adj_list = part["adj"] or []
    R._save_ob_file_cache_entry(key, in_list, out_list, adj_list, "rest")
    _log(f"{ym} 완료 → 캐시 저장: 입고 {len(in_list)} / 출고 {len(out_list)} / 조정 {len(adj_list)}")


if __name__ == "__main__":
    months = sys.argv[1:] or ["2026-05", "2026-06"]
    cfg = U.load_config()
    client = api_mod.make_client(cfg)
    if not client:
        sys.exit("OurBox API 키 없음")
    for ym in months:
        try:
            backfill_month(client, ym)
        except Exception as e:
            _log(f"{ym} 중단: {e} (다시 실행하면 이어서 받음)")
            sys.exit(1)
    _log("전부 완료")
