import requests, json

ak = 'NV5B7eYKk9FUIffu5yk7gfPFKxghIXt2KGdl7/zzKCI='
sk = 'cd390eb25946b8929ebedec17c4cff7e'
h = {'api_access_key': ak, 'api_secret_key': sk, 'Content-Type': 'application/json'}

BASE = 'https://api.ourbox.co.kr'

tests = [
    ('입고 실적',     '/api/wms/put/put_perf',        {'input_dt_type':'1','input_dt_from':'2025-05-01','input_dt_to':'2025-05-31'}),
    ('출고 실적',     '/api/wms/out/out_perf_period',  {'out_dt_type':'1','out_dt_from':'2025-05-01','out_dt_to':'2025-05-31'}),
    ('재고 조정',     '/api/wms/stock/stock_adj_hist', {'input_type':'1','start_reg_dt':'2025-05-01','end_reg_dt':'2025-05-31','page':1}),
]

for name, path, body in tests:
    r = requests.post(BASE + path, headers=h, json=body, timeout=15)
    print(f'{name} ({path}): {r.status_code}')
    if r.ok:
        d = r.json()
        items = d.get('data') or d.get('list') or []
        print(f'  데이터: {len(items)}건, last_page={d.get("last_page")}')
        if items:
            print(f'  샘플 키: {list(items[0].keys())[:10]}')
            print(f'  샘플: {json.dumps(items[0], ensure_ascii=False)[:200]}')
    else:
        print(f'  오류: {r.text[:200]}')
    print()
