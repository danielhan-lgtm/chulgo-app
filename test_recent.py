import requests, json
from datetime import datetime, timedelta

ak = 'NV5B7eYKk9FUIffu5yk7gfPFKxghIXt2KGdl7/zzKCI='
sk = 'cd390eb25946b8929ebedec17c4cff7e'
h = {'api_access_key': ak, 'api_secret_key': sk, 'Content-Type': 'application/json'}
BASE = 'https://api.ourbox.co.kr'

today = datetime.now()
# 최근 7일
from_dt = (today - timedelta(days=7)).strftime('%Y-%m-%d')
to_dt = today.strftime('%Y-%m-%d')
print(f"조회 기간: {from_dt} ~ {to_dt}")

print("\n=== 입고 실적 (최근 7일) ===")
r = requests.post(BASE + '/api/wms/put/put_perf',
    headers=h,
    json={'input_dt_type':'1', 'input_dt_from': from_dt, 'input_dt_to': to_dt, 'page': 1},
    timeout=15)
print(f"status: {r.status_code}")
if r.ok:
    d = r.json()
    items = d.get('data') or d.get('list') or []
    print(f"데이터: {len(items)}건, last_page={d.get('last_page')}")
    if items:
        print(f"샘플 키: {list(items[0].keys())[:12]}")
        print(f"샘플: {json.dumps(items[0], ensure_ascii=False)[:400]}")
else:
    print(r.text[:200])

print("\n=== 출고 실적 (최근 7일) ===")
r2 = requests.post(BASE + '/api/wms/out/out_perf_period',
    headers=h,
    json={'out_dt_type':'1', 'out_dt_from': from_dt, 'out_dt_to': to_dt, 'page': 1},
    timeout=15)
print(f"status: {r2.status_code}")
if r2.ok:
    d2 = r2.json()
    items2 = d2.get('data') or d2.get('list') or []
    print(f"데이터: {len(items2)}건")
    if items2:
        print(f"샘플 키: {list(items2[0].keys())[:12]}")
        print(f"샘플: {json.dumps(items2[0], ensure_ascii=False)[:400]}")
else:
    print(r2.text[:200])

print("\n=== 재고 조정 (최근 7일, input_type=2) ===")
r3 = requests.post(BASE + '/api/wms/stock/stock_adj_hist',
    headers=h,
    json={'input_type':'2', 'start_reg_dt': from_dt, 'end_reg_dt': to_dt, 'page': 1},
    timeout=15)
print(f"status: {r3.status_code}")
if r3.ok:
    d3 = r3.json()
    items3 = d3.get('data') or d3.get('list') or []
    print(f"데이터: {len(items3)}건")
    if items3:
        print(f"샘플 키: {list(items3[0].keys())[:12]}")
        print(f"샘플: {json.dumps(items3[0], ensure_ascii=False)[:400]}")
else:
    print(r3.text[:200])
