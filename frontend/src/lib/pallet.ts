// 팔레트 분배 유틸 — 쿠팡 적재리스트 / 쿠팡 그로스 적재리스트 공용
export const DEFAULT_PALLET_CAP = 112   // 팔레트당 기본 최대 박스 수

export interface PalletDistRow {
  no?: number
  sku?: string
  box: number
  qty: number
  pallet?: number
}

// 품목을 '팔레트당 최대 박스 용량(cap)' 기준으로 채워 분배한다.
//  · 현재 팔레트가 cap 박스에 도달하면 다음 팔레트로 넘어감
//  · 같은 SKU(한 행)는 가급적 한 팔레트에 통째로 — 현재 팔레트에 안 들어가면 새 팔레트로 통째 이동
//  · 단, 한 품목의 박스 수가 cap 자체를 넘으면 어쩔 수 없이 경계에서 쪼갬(수량은 박스당 개입수로 비례)
//  · singleSku=true(예: RC 센터)면 한 팔레트에 한 SKU만 — 다른 SKU와 혼적 금지.
//    같은 SKU가 여러 행으로 들어와도 하나로 묶어 현재 팔레트를 cap까지 채운 뒤에야 다음 팔레트로 넘어감
//    (행 순서는 같은 SKU끼리 인접하도록 재배열, 최초 등장 순서 유지)
// 반환: { rows, count } (count = 사용된 팔레트 수)
export function distributeByCapacity<T extends PalletDistRow>(src: T[], cap: number, singleSku = false): { rows: T[]; count: number } {
  const C = Math.max(1, Math.floor(cap) || DEFAULT_PALLET_CAP)
  const out: T[] = []
  let pallet = 1
  let used = 0
  const skuOf = (r: T) => String(r.sku ?? '').trim()
  const ordered = singleSku ? groupBySku(src, skuOf) : src
  let curSku: string | null = null
  for (const r of ordered) {
    let box = Number(r.box) || 0
    let qty = Number(r.qty) || 0
    if (box <= 0) {                          // 박스 없는 품목은 현재 팔레트에 그대로
      out.push({ ...r, pallet })
      continue
    }
    const perBox = qty / box
    if (singleSku) {
      // 1팔레트 1SKU: SKU가 바뀔 때만 새 팔레트. 같은 SKU의 다음 행은 현재 팔레트를 이어서 채움
      const k = skuOf(r)
      if (used > 0 && (!k || k !== curSku)) { pallet++; used = 0 }
      curSku = k
    }
    // 같은 SKU를 이어 채우는 중(RC)이면 통째 이동 대신 경계에서 쪼개서 현재 팔레트를 먼저 채운다
    const fillContinue = singleSku && used > 0
    if (box <= C && !fillContinue) {
      // 통째로 넣을 수 있는 SKU → 현재 팔레트에 안 맞으면 새 팔레트로 통째 이동
      if (used > 0 && used + box > C) { pallet++; used = 0 }
      out.push({ ...r, box, qty, pallet })
      used += box
    } else {
      // 한 품목이 팔레트 용량보다 큼 → 경계에서 분할
      let remBox = box
      let remQty = qty
      while (remBox > 0) {
        if (used >= C) { pallet++; used = 0 }
        const space = C - used
        const take = Math.min(remBox, space)
        const last = take >= remBox
        const takeQty = last ? remQty : Math.round(take * perBox)
        out.push({ ...r, box: take, qty: takeQty, pallet })
        used += take
        remBox -= take
        remQty -= takeQty
      }
    }
  }
  const merged = singleSku ? mergeSamePalletRows(out) : out
  const count = merged.reduce((m, r) => Math.max(m, r.pallet || 1), 1)
  return { rows: merged.map((r, i) => ({ ...r, no: i + 1 })), count }
}

// 같은 팔레트에 같은 SKU(그 외 필드도 동일)가 연속으로 두 줄 이상 실리면 한 줄로 합산 (RC 이어채우기 결과 정리)
function mergeSamePalletRows<T extends PalletDistRow>(rows: T[]): T[] {
  const keyOf = (r: T) => {
    const { no: _n, box: _b, qty: _q, ...rest } = r as any
    return JSON.stringify(rest)
  }
  const out: T[] = []
  for (const r of rows) {
    const prev = out[out.length - 1]
    if (prev && prev.pallet === r.pallet && String(prev.sku ?? '') !== '' && keyOf(prev) === keyOf(r)) {
      out[out.length - 1] = { ...prev, box: (Number(prev.box) || 0) + (Number(r.box) || 0), qty: (Number(prev.qty) || 0) + (Number(r.qty) || 0) }
    } else {
      out.push(r)
    }
  }
  return out
}

// 같은 SKU 행이 서로 떨어져 있어도 인접하도록 묶는다 (SKU 최초 등장 순서 유지, 빈 SKU는 각각 별개 취급)
function groupBySku<T>(src: T[], skuOf: (r: T) => string): T[] {
  const buckets = new Map<string, T[]>()
  const solo: { idx: number; row: T }[] = []
  const order: string[] = []
  src.forEach((r, idx) => {
    const k = skuOf(r)
    if (!k) { solo.push({ idx, row: r }); return }
    if (!buckets.has(k)) { buckets.set(k, []); order.push(k) }
    buckets.get(k)!.push(r)
  })
  if (solo.length === src.length) return src
  const out: T[] = []
  for (const k of order) out.push(...buckets.get(k)!)
  // SKU 없는 행은 뒤에 원래 순서대로
  for (const s of solo) out.push(s.row)
  return out
}

// 목표 팔레트 수(target)가 있으면 박스를 고르게 나누되 파레트당 최대(cap)는 넘지 않는 유효 cap 계산
export function effectiveCap(totalBox: number, cap: number, target: number): number {
  if (target <= 0) return cap
  return Math.min(cap, Math.max(1, Math.ceil(totalBox / target)))
}

// 센터명에 'RC'가 포함되면 1팔레트 1SKU 규칙 적용 대상 (예: '고양RC', '안성 RC(01)')
export function isSingleSkuCenter(center: string): boolean {
  return /RC/i.test(String(center || ''))
}
