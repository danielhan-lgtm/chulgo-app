import requests, json

bh_token = 'e3fca582-a61e-41e5-8cd9-7c1f9410eec4'
h = {'Authorization': f'Bearer {bh_token}'}
BASE = 'https://rest.boxhero-app.com'

# 아워박스 호법 위치의 최근 거래 1개 상세 확인
r = requests.get(f'{BASE}/v1/location-txs',
    headers=h, params={'type': 'out', 'location_id': 228640, 'limit': 3}, timeout=15)
txs = r.json().get('items', [])
print(f"거래 {len(txs)}건")

for tx in txs[:2]:
    tx_id = tx['id']
    print(f"\n--- TX {tx_id} ---")
    print(f"  time: {tx.get('transaction_time')}")
    print(f"  total_qty: {tx.get('total_quantity')}")
    print(f"  count_of_items: {tx.get('count_of_items')}")
    print(f"  memo: {tx.get('memo')}")

    # 상세 조회
    r2 = requests.get(f'{BASE}/v1/location-txs/{tx_id}', headers=h, timeout=15)
    if r2.ok:
        detail = r2.json()
        item_data = detail.get('item', detail)
        items = item_data.get('items', [])
        print(f"  상세 items: {len(items)}개")
        for it in items[:3]:
            print(f"    name={it.get('name')}, sku={it.get('sku')}, qty={it.get('quantity')}")
    else:
        print(f"  상세 오류: {r2.status_code}")

# 입고(in) 거래도 확인
print("\n=== 아워박스 호법 입고 거래 ===")
r3 = requests.get(f'{BASE}/v1/location-txs',
    headers=h, params={'type': 'in', 'location_id': 228640, 'limit': 3}, timeout=15)
txs3 = r3.json().get('items', [])
print(f"입고 거래 {len(txs3)}건")
if txs3:
    sample = txs3[0]
    print(f"샘플: time={sample.get('transaction_time')}, total_qty={sample.get('total_quantity')}")

    # 상세
    r4 = requests.get(f'{BASE}/v1/location-txs/{sample["id"]}', headers=h, timeout=15)
    if r4.ok:
        items4 = r4.json().get('item', {}).get('items', [])
        print(f"아이템 {len(items4)}개")
        for it in items4[:3]:
            print(f"  name={it.get('name')}, sku={it.get('sku')}, qty={it.get('quantity')}")
