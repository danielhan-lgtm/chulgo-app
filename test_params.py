import requests, json

ak = 'NV5B7eYKk9FUIffu5yk7gfPFKxghIXt2KGdl7/zzKCI='
sk = 'cd390eb25946b8929ebedec17c4cff7e'
h = {'api_access_key': ak, 'api_secret_key': sk, 'Content-Type': 'application/json'}
BASE = 'https://api.ourbox.co.kr'

print("=== 입고 (7일 범위) ===")
r = requests.post(BASE + '/api/wms/put/put_perf',
    headers=h, json={'input_dt_type':'1','input_dt_from':'2025-05-01','input_dt_to':'2025-05-07'}, timeout=15)
print(f"status: {r.status_code}")
if r.ok:
    d = r.json(); items = d.get('data') or []
    print(f"데이터: {len(items)}건")
    if items: print(f"샘플: {json.dumps(items[0], ensure_ascii=False)[:300]}")
else:
    print(r.text[:200])

print("\n=== 출고 (최근 7일) ===")
r2 = requests.post(BASE + '/api/wms/out/out_perf_period',
    headers=h, json={'out_dt_type':'1','out_dt_from':'2026-05-01','out_dt_to':'2026-05-31'}, timeout=15)
print(f"status: {r2.status_code}")
if r2.ok:
    d2 = r2.json(); items2 = d2.get('data') or []
    print(f"데이터: {len(items2)}건")
    if items2: print(f"샘플: {json.dumps(items2[0], ensure_ascii=False)[:300]}")
else:
    print(r2.text[:200])

print("\n=== 재고 조정 (input_type 변경 시도) ===")
for itype in ['2', '3', '']:
    body = {'input_type': itype, 'start_reg_dt': '2026-05-01', 'end_reg_dt': '2026-05-31', 'page': 1}
    r3 = requests.post(BASE + '/api/wms/stock/stock_adj_hist', headers=h, json=body, timeout=15)
    print(f"input_type={repr(itype)}: {r3.status_code}")
    if r3.ok:
        d3 = r3.json(); items3 = d3.get('data') or []
        print(f"  데이터: {len(items3)}건")
        if items3: print(f"  샘플 키: {list(items3[0].keys())[:10]}")
        break
    else:
        print(f"  {r3.text[:100]}")
