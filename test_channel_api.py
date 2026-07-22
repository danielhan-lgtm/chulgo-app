import requests, json
from datetime import datetime, timedelta

ak = 'NV5B7eYKk9FUIffu5yk7gfPFKxghIXt2KGdl7/zzKCI='
sk = 'cd390eb25946b8929ebedec17c4cff7e'
h = {'api_access_key': ak, 'api_secret_key': sk, 'Content-Type': 'application/json'}
BASE = 'https://api.ourbox.co.kr'

today = datetime.now()
from_dt = (today - timedelta(days=30)).strftime('%Y-%m-%d')
to_dt = today.strftime('%Y-%m-%d')
print(f"기간: {from_dt} ~ {to_dt}\n")

# 1. OMS 주문 목록 (채널 정보 포함)
print("=== /api/oms/order/orders ===")
r = requests.post(BASE + '/api/oms/order/orders',
    headers=h,
    json={'page': 1, 'size': 5},
    timeout=15)
print(f"status: {r.status_code}")
if r.ok:
    d = r.json()
    print(f"최상위 키: {list(d.keys()) if isinstance(d, dict) else type(d)}")
    items = d.get('data') or d.get('list') or d.get('orders') or (d if isinstance(d, list) else [])
    print(f"데이터: {len(items)}건")
    if items:
        print(f"샘플 키: {list(items[0].keys())}")
        print(f"샘플: {json.dumps(items[0], ensure_ascii=False)[:500]}")
else:
    print(r.text[:300])

# 2. 재고 조회 (채널별 분리 여부 확인)
print("\n=== /api/oms/info/product_stock ===")
r2 = requests.post(BASE + '/api/oms/info/product_stock',
    headers=h,
    json={'page': 1},
    timeout=15)
print(f"status: {r2.status_code}")
if r2.ok:
    d2 = r2.json()
    items2 = d2.get('data') or d2.get('list') or []
    print(f"데이터: {len(items2)}건")
    if items2:
        print(f"샘플 키: {list(items2[0].keys())}")
        print(f"샘플: {json.dumps(items2[0], ensure_ascii=False)[:500]}")
else:
    print(r2.text[:200])

# 3. 출고 실적 상세 (채널 정보 포함 여부)
print("\n=== /api/wms/out/out_perf (단일일) ===")
r3 = requests.post(BASE + '/api/wms/out/out_perf',
    headers=h,
    json={'out_dt_type': '1', 'out_dt': to_dt, 'page': 1},
    timeout=15)
print(f"status: {r3.status_code}")
if r3.ok:
    d3 = r3.json()
    items3 = d3.get('data') or d3.get('list') or []
    print(f"데이터: {len(items3)}건")
    if items3:
        print(f"샘플 키: {list(items3[0].keys())}")
        print(f"샘플: {json.dumps(items3[0], ensure_ascii=False)[:400]}")
else:
    print(r3.text[:200])
