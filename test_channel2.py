import requests, json
from datetime import datetime, timedelta

ak = 'NV5B7eYKk9FUIffu5yk7gfPFKxghIXt2KGdl7/zzKCI='
sk = 'cd390eb25946b8929ebedec17c4cff7e'
h = {'api_access_key': ak, 'api_secret_key': sk, 'Content-Type': 'application/json'}
BASE = 'https://api.ourbox.co.kr'

today = datetime.now()
from_dt = (today - timedelta(days=30)).strftime('%Y-%m-%d')
to_dt = today.strftime('%Y-%m-%d')

# order_dt 필수 → 날짜 포함해서 재시도
print("=== /api/oms/order/orders (날짜 포함) ===")
for body in [
    {'page': 1, 'size': 5, 'order_dt': to_dt},
    {'page': 1, 'size': 5, 'order_dt_from': from_dt, 'order_dt_to': to_dt},
    {'page': 1, 'size': 5, 'start_dt': from_dt, 'end_dt': to_dt},
    {'page': 1, 'size': 5, 'reg_dt_from': from_dt, 'reg_dt_to': to_dt},
]:
    r = requests.post(BASE + '/api/oms/order/orders', headers=h, json=body, timeout=15)
    print(f"  body={list(body.keys())}: {r.status_code}")
    if r.ok:
        d = r.json()
        items = d.get('data') or d.get('list') or []
        print(f"  데이터: {len(items)}건")
        if items:
            print(f"  샘플 키: {list(items[0].keys())}")
            print(f"  샘플: {json.dumps(items[0], ensure_ascii=False)[:400]}")
        break
    else:
        print(f"  오류: {r.text[:150]}")

# seller_list (판매자/채널 목록)
print("\n=== /api/oms/info/seller_list ===")
r2 = requests.post(BASE + '/api/oms/info/seller_list', headers=h, json={'page': 1}, timeout=15)
print(f"status: {r2.status_code}")
if r2.ok:
    d2 = r2.json()
    items2 = d2.get('data') or d2.get('list') or (d2 if isinstance(d2, list) else [])
    print(f"데이터: {len(items2)}건")
    if items2:
        print(f"샘플 키: {list(items2[0].keys())}")
        print(f"샘플: {json.dumps(items2[0], ensure_ascii=False)[:300]}")
else:
    print(r2.text[:200])

# delivery_agency (배송 대행 = 채널)
print("\n=== /api/oms/info/delivery_agency ===")
r3 = requests.post(BASE + '/api/oms/info/delivery_agency', headers=h, json={'page': 1}, timeout=15)
print(f"status: {r3.status_code}")
if r3.ok:
    d3 = r3.json()
    items3 = d3.get('data') or d3.get('list') or (d3 if isinstance(d3, list) else [])
    print(f"데이터: {len(items3)}건")
    if items3:
        print(f"샘플 키: {list(items3[0].keys())}")
        print(f"샘플: {json.dumps(items3[:3], ensure_ascii=False)[:400]}")
else:
    print(r3.text[:200])

# BoxHero 위치별 거래 확인
print("\n=== BoxHero 위치 목록 ===")
bh_token = 'e3fca582-a61e-41e5-8cd9-7c1f9410eec4'
bh_h = {'Authorization': f'Bearer {bh_token}', 'Content-Type': 'application/json'}
r4 = requests.get('https://rest.boxhero-app.com/v1/locations', headers=bh_h, timeout=15)
print(f"status: {r4.status_code}")
if r4.ok:
    locs = r4.json().get('items', [])
    print(f"위치 {len(locs)}개:")
    for loc in locs:
        print(f"  [{loc['id']}] {loc['name']}")
