import requests, json
from datetime import datetime, timedelta
from collections import defaultdict

bh_token = 'e3fca582-a61e-41e5-8cd9-7c1f9410eec4'
h = {'Authorization': f'Bearer {bh_token}', 'Content-Type': 'application/json'}
BASE = 'https://rest.boxhero-app.com'

today = datetime.now()
from_dt = (today - timedelta(days=30)).strftime('%Y-%m-%d')
to_dt = today.strftime('%Y-%m-%d')
print(f"조회 기간: {from_dt} ~ {to_dt}\n")

# 1. 전체 위치 목록
locs_r = requests.get(f'{BASE}/v1/locations', headers=h, timeout=15)
locations = {loc['id']: loc['name'] for loc in locs_r.json().get('items', [])}
print("위치 목록:", locations)

# 2. 위치별 최근 출고 거래 (처음 3개만 샘플)
print("\n=== 위치별 출고 거래 샘플 ===")
for loc_id, loc_name in list(locations.items())[:4]:
    r = requests.get(f'{BASE}/v1/location-txs',
        headers=h,
        params={'type': 'out', 'location_id': loc_id, 'limit': 5},
        timeout=15)
    if r.ok:
        txs = r.json().get('items', [])
        total_qty = sum(abs(item.get('quantity', 0)) for tx in txs for item in tx.get('items', []))
        print(f"[{loc_name}] 최근 {len(txs)}건, 총수량={total_qty}")
        if txs:
            first_tx = txs[0]
            print(f"  tx 키: {list(first_tx.keys())}")
            if first_tx.get('items'):
                sample_item = first_tx['items'][0]
                print(f"  item 키: {list(sample_item.keys())}")
                print(f"  item 샘플: name={sample_item.get('name')}, sku={sample_item.get('sku')}, qty={sample_item.get('quantity')}")

# 3. 전체 위치 × 품목 매트릭스 (출고 기준)
print("\n=== 전체 위치별 품목 출고 집계 ===")
# 각 위치별 출고 집계
matrix = defaultdict(lambda: defaultdict(int))  # {sku: {loc_name: qty}}
sku_names = {}

for loc_id, loc_name in locations.items():
    cursor = None
    while True:
        params = {'type': 'out', 'location_id': loc_id, 'limit': 100}
        if cursor:
            params['cursor'] = cursor
        r = requests.get(f'{BASE}/v1/location-txs', headers=h, params=params, timeout=15)
        if not r.ok:
            break
        d = r.json()
        txs = d.get('items', [])

        for tx in txs:
            tx_time = tx.get('transaction_time', '')[:10]
            if tx_time < from_dt:
                break
            for item in tx.get('items', []):
                sku = item.get('sku') or str(item.get('id', ''))
                name = item.get('name', '')
                qty = abs(int(item.get('quantity', 0)))
                if qty > 0:
                    matrix[sku][loc_name] += qty
                    sku_names[sku] = name
        else:
            if d.get('has_more') and txs and txs[-1].get('transaction_time', '')[:10] >= from_dt:
                cursor = d.get('cursor')
                continue
        break

# 출력
loc_names = list(locations.values())
print(f"\n{'SKU':<20} {'상품명':<25} " + " ".join(f"{n[:10]:>12}" for n in loc_names))
print("-" * (20+25+13*len(loc_names)))
for sku, locs in sorted(matrix.items(), key=lambda x: -sum(x[1].values()))[:20]:
    name = sku_names.get(sku, '')[:24]
    row = f"{sku:<20} {name:<25} "
    row += " ".join(f"{matrix[sku].get(loc,0):>12,}" for loc in loc_names)
    print(row)
