"""양쪽 시스템 상품 목록 비교 샘플"""
import requests, json, sys
sys.path.insert(0, 'backend')
import utils_core as U

cfg = U.load_config()
bh_token = cfg.get('api_token', '')
ob_ak = cfg.get('ourbox_access_key', '')
ob_sk = cfg.get('ourbox_secret_key', '')

# 1. BoxHero 상품 목록 (처음 20개)
print("=== BoxHero 상품 목록 (처음 20개) ===")
r = requests.get('https://rest.boxhero-app.com/v1/items',
    headers={'Authorization': f'Bearer {bh_token}'},
    params={'limit': 20}, timeout=15)
bh_items = r.json().get('items', [])
for item in bh_items:
    print(f"  SKU={item.get('sku',''):<20} 이름={item.get('name','')}")

print(f"\n총 BoxHero 상품 (has_more={r.json().get('has_more')})")

# 2. OurBox 상품 목록
print("\n=== OurBox 상품 목록 (처음 20개) ===")
ob_h = {'api_access_key': ob_ak, 'api_secret_key': ob_sk, 'Content-Type': 'application/json'}
r2 = requests.post('https://api.ourbox.co.kr/api/oms/info/products',
    headers=ob_h, json={'page': 1, 'size': 20}, timeout=15)
if r2.ok:
    d2 = r2.json()
    ob_items = d2.get('data') or d2.get('list') or []
    for item in ob_items:
        print(f"  키: {list(item.keys())}")
        break
    for item in ob_items[:10]:
        keys = list(item.keys())
        print(f"  {json.dumps(item, ensure_ascii=False)[:200]}")
else:
    print(f"오류: {r2.status_code} {r2.text[:200]}")

# 3. 기존 매핑 DB 확인
print("\n=== 기존 매핑 DB ===")
import receiving_db as db
mappings = db.get_mappings()
print(f"기존 매핑: {len(mappings)}건")
for m in mappings[:5]:
    print(f"  OB:{m.get('ourbox_prod_nm','')[:20]} → BH:{m.get('boxhero_item_nm','')[:20]} (SKU:{m.get('boxhero_sku','')})")
