// 화면 조회 결과를 브라우저 IndexedDB에 보관 → 탭이 메모리 절약으로 내려가거나 새로고침돼도 복원.
// localStorage(5MB 한도)로는 재고 대사 결과처럼 큰 데이터를 못 담아서 IndexedDB 사용.
const DB_NAME = 'chulgo_persist'
const STORE = 'kv'

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    try {
      const req = indexedDB.open(DB_NAME, 1)
      req.onupgradeneeded = () => { req.result.createObjectStore(STORE) }
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => reject(req.error)
    } catch (e) { reject(e) }
  })
}

export async function persistGet<T>(key: string): Promise<T | null> {
  try {
    const db = await openDb()
    return await new Promise<T | null>((resolve) => {
      const tx = db.transaction(STORE, 'readonly')
      const req = tx.objectStore(STORE).get(key)
      req.onsuccess = () => resolve((req.result as T) ?? null)
      req.onerror = () => resolve(null)
    })
  } catch { return null }
}

export async function persistSet(key: string, value: unknown): Promise<void> {
  try {
    const db = await openDb()
    await new Promise<void>((resolve) => {
      const tx = db.transaction(STORE, 'readwrite')
      tx.objectStore(STORE).put(value, key)
      tx.oncomplete = () => resolve()
      tx.onerror = () => resolve()
    })
  } catch { /* 저장 못 해도 화면 동작엔 영향 없음 */ }
}

// 잦은 상태 변경을 모아서 저장 (키별 debounce)
const timers: Record<string, number> = {}
export function persistSetDebounced(key: string, value: unknown, ms = 800) {
  if (timers[key]) window.clearTimeout(timers[key])
  timers[key] = window.setTimeout(() => { delete timers[key]; persistSet(key, value) }, ms)
}
