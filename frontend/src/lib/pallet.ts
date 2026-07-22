// 팔레트 분배 유틸 — 쿠팡 적재리스트 / 쿠팡 그로스 적재리스트 공용
export const DEFAULT_PALLET_CAP = 112   // 팔레트당 기본 최대 박스 수

export interface PalletDistRow {
  no?: number
  box: number
  qty: number
  pallet?: number
}

// 품목을 '팔레트당 최대 박스 용량(cap)' 기준으로 채워 분배한다.
//  · 현재 팔레트가 cap 박스에 도달하면 다음 팔레트로 넘어감
//  · 같은 SKU(한 행)는 가급적 한 팔레트에 통째로 — 현재 팔레트에 안 들어가면 새 팔레트로 통째 이동
//  · 단, 한 품목의 박스 수가 cap 자체를 넘으면 어쩔 수 없이 경계에서 쪼갬(수량은 박스당 개입수로 비례)
// 반환: { rows, count } (count = 사용된 팔레트 수)
export function distributeByCapacity<T extends PalletDistRow>(src: T[], cap: number): { rows: T[]; count: number } {
  const C = Math.max(1, Math.floor(cap) || DEFAULT_PALLET_CAP)
  const out: T[] = []
  let pallet = 1
  let used = 0
  for (const r of src) {
    let box = Number(r.box) || 0
    let qty = Number(r.qty) || 0
    if (box <= 0) {                          // 박스 없는 품목은 현재 팔레트에 그대로
      out.push({ ...r, pallet })
      continue
    }
    const perBox = qty / box
    if (box <= C) {
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
  const count = out.reduce((m, r) => Math.max(m, r.pallet || 1), 1)
  return { rows: out.map((r, i) => ({ ...r, no: i + 1 })), count }
}

// 목표 팔레트 수(target)가 있으면 박스를 고르게 나누되 파레트당 최대(cap)는 넘지 않는 유효 cap 계산
export function effectiveCap(totalBox: number, cap: number, target: number): number {
  if (target <= 0) return cap
  return Math.min(cap, Math.max(1, Math.ceil(totalBox / target)))
}
