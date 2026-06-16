import axios from 'axios'
import type { Customer, InvoiceItem } from '../types'

const api = axios.create({ baseURL: '/api' })

// ── Config ────────────────────────────────────────────────────
export const getConfig = () => api.get('/config').then(r => r.data)
export const updateConfig = (data: Record<string, unknown>) =>
  api.post('/config', { data }).then(r => r.data)

export const uploadMaster = (file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return api.post('/master/upload', fd).then(r => r.data)
}
export const getMasterStatus = () => api.get('/master/status').then(r => r.data)

// ── BoxHero ──────────────────────────────────────────────────
export const boxheroConnect = (token: string) =>
  api.post('/boxhero/connect', { token }).then(r => r.data)

export const getLocations = (token: string) =>
  api.get('/boxhero/locations', { params: { token } }).then(r => r.data)

export const getPartners = (token: string) =>
  api.get('/boxhero/partners', { params: { token } }).then(r => r.data)

export const sendToBoxhero = (payload: {
  token: string
  location_id: number
  items: { sku: string; quantity: number }[]
  memo?: string
  partner_id?: number
}) => api.post('/boxhero/send', payload).then(r => r.data)

// ── Convert ──────────────────────────────────────────────────
export const getGeneralColumns = (file: File | Blob, filename = 'file.xlsx', sheetName?: string) => {
  const fd = new FormData()
  fd.append('order_file', file, filename)
  if (sheetName) fd.append('sheet_name', sheetName)
  return api.post('/convert/general/columns', fd).then(r => r.data)
}

export const convertGeneral = (
  orderFile: File | Blob,
  filename: string,
  namCol: string,
  qtyCol: string,
  threshold: number,
  masterFile?: File | null,
) => {
  const fd = new FormData()
  fd.append('order_file', orderFile, filename)
  fd.append('name_col', namCol)
  fd.append('qty_col', qtyCol)
  fd.append('threshold', String(threshold))
  if (masterFile) fd.append('master_file', masterFile)
  return api.post('/convert/general', fd).then(r => r.data)
}

export const convertNaver = (
  orderFile: File | Blob,
  filename: string,
  threshold: number,
  masterFile?: File | null,
) => {
  const fd = new FormData()
  fd.append('order_file', orderFile, filename)
  fd.append('threshold', String(threshold))
  if (masterFile) fd.append('master_file', masterFile)
  return api.post('/convert/naver', fd).then(r => r.data)
}

// ── Slack ────────────────────────────────────────────────────
export const slackConnect = (token: string) =>
  api.post('/slack/connect', { token }).then(r => r.data)

export const getSlackMessages = (token: string, channelId: string) =>
  api.get('/slack/messages', { params: { token, channel_id: channelId } }).then(r => r.data)

export const downloadSlackFile = (url: string, token: string) =>
  api.get('/slack/file', { params: { url, token } }).then(r => r.data)

export const toggleSlackReaction = (token: string, channelId: string, ts: string, emoji: string) =>
  api.post('/slack/reaction', { token, channel_id: channelId, ts, emoji }).then(r => r.data)

export const joinSlackChannel = (token: string, channelId: string) =>
  api.post('/slack/join', { token, channel_id: channelId }).then(r => r.data)

// ── Gmail ────────────────────────────────────────────────────
export const getGmailAuthUrl = (clientId: string, clientSecret: string) =>
  api.post('/gmail/auth-url', { client_id: clientId, client_secret: clientSecret }).then(r => r.data)

export const getGmailStatus = () => api.get('/gmail/status').then(r => r.data)

export const disconnectGmail = () => api.post('/gmail/disconnect').then(r => r.data)

export const getGmailMessages = () => api.get('/gmail/messages').then(r => r.data)

export const downloadGmailAttachment = (messageId: string, attachmentId: string) =>
  api.get('/gmail/attachment', { params: { message_id: messageId, attachment_id: attachmentId } }).then(r => r.data)

// ── Logs ─────────────────────────────────────────────────────
export const getLogs = () => api.get('/logs').then(r => r.data)

export const addLog = (entry: {
  level: string
  message: string
  detail?: string
  payload?: Record<string, unknown>
  source?: string
}) => api.post('/logs', entry).then(r => r.data)

export const clearLogs = () => api.delete('/logs').then(r => r.data)

// ── 입고정산기 ─────────────────────────────────────────────────────────────
export const getReceivings = () => api.get('/receiving/receivings').then(r => r.data)
export const approveReceiving = (putSno: string) => api.post(`/receiving/receivings/${putSno}/approve`).then(r => r.data)
export const cancelReceiving = (putSno: string) => api.post(`/receiving/receivings/${putSno}/cancel`).then(r => r.data)
export const ignoreReceiving = (putSno: string) => api.post(`/receiving/receivings/${putSno}/ignore`).then(r => r.data)
export const syncReceiving = () => api.post('/receiving/sync').then(r => r.data)
export const getSyncStatus = () => api.get('/receiving/sync/status').then(r => r.data)
export const getMappings = () => api.get('/receiving/mappings').then(r => r.data)
export const saveMapping = (data: { ourbox_prod_cd: string; ourbox_prod_nm: string; boxhero_item_id: number; boxhero_item_nm: string; boxhero_sku: string }) =>
  api.post('/receiving/mappings', data).then(r => r.data)
export const autoMap = () => api.post('/receiving/mappings/auto').then(r => r.data)
export const deleteMapping = (prodCd: string) => api.delete(`/receiving/mappings/${encodeURIComponent(prodCd)}`).then(r => r.data)
export const getBoxheroItemsForReceiving = () => api.get('/receiving/boxhero-items').then(r => r.data)

// ── 서류 검토 ───────────────────────────────────────────────────────────────
export const reviewCoupangDocs = (files: {
  거래명세서?: File | null
  부착리스트?: File | null
  적재리스트?: File | null
  밀크런등록내역?: File | null
  출고내역?: File | null
}) => {
  const fd = new FormData()
  if (files.거래명세서) fd.append('거래명세서', files.거래명세서)
  if (files.부착리스트) fd.append('부착리스트', files.부착리스트)
  if (files.적재리스트) fd.append('적재리스트', files.적재리스트)
  if (files.밀크런등록내역) fd.append('밀크런등록내역', files.밀크런등록내역)
  if (files.출고내역) fd.append('출고내역', files.출고내역)
  return api.post('/doc-review/coupang', fd, { timeout: 120000 }).then(r => r.data)
}

// ── 거래명세서 ─────────────────────────────────────────────────────────────
export const getCustomers = () => api.get('/invoice/customers').then(r => r.data)

export const createCustomer = (data: Omit<Customer, 'id'>) =>
  api.post('/invoice/customers', data).then(r => r.data)

export const updateCustomer = (id: string, data: Omit<Customer, 'id'>) =>
  api.put(`/invoice/customers/${id}`, data).then(r => r.data)

export const deleteCustomer = (id: string) =>
  api.delete(`/invoice/customers/${id}`).then(r => r.data)

export const getSales = (fromDate: string, toDate: string, locationId?: number) =>
  api.get('/invoice/sales', {
    params: { from_date: fromDate, to_date: toDate, ...(locationId ? { location_id: locationId } : {}) },
  }).then(r => r.data)

export const generateInvoice = (payload: {
  customer_id: string
  issue_date: string
  doc_number?: string
  trade_name?: string
  payment_terms?: string
  items: InvoiceItem[]
}) =>
  api.post('/invoice/generate', payload, { responseType: 'blob' }).then(r => r.data)

// ── 상품 매핑 ─────────────────────────────────────────────────
export const getNameMappings = () => api.get('/mapping/list').then(r => r.data)
export const getBhItemsForMapping = (token: string) =>
  api.get('/mapping/bh-items', { params: { token } }).then(r => r.data)
export const getObProducts = () =>
  api.get('/mapping/ob-products', { timeout: 120000 }).then(r => r.data)

// ── 다대다 링크 ───────────────────────────────────────────────
export const createMappingLink = (body: {
  ob_name: string; bh_sku: string; bh_name?: string; confirmed?: number
}) => api.post('/mapping/link', body).then(r => r.data)
export const removeMappingLink = (body: { ob_name: string; bh_sku: string }) =>
  api.post('/mapping/unlink', body).then(r => r.data)
export const deleteMappingById = (id: number) =>
  api.delete(`/mapping/link/${id}`).then(r => r.data)

// ── 채널 매핑 ─────────────────────────────────────────────────
export const getObChannels = (days = 14) =>
  api.get('/mapping/ob-channels', { params: { days }, timeout: 180000 }).then(r => r.data)
export const getBhChannels = (token: string, days = 14) =>
  api.get('/mapping/bh-channels', { params: { token, days }, timeout: 120000 }).then(r => r.data)
export const getChannelMappings = () => api.get('/mapping/channel-list').then(r => r.data)
export const createChannelLink = (body: { ob_channel: string; bh_keyword: string; confirmed?: number }) =>
  api.post('/mapping/channel-link', body).then(r => r.data)
export const removeChannelLink = (body: { ob_channel: string; bh_keyword: string }) =>
  api.post('/mapping/channel-unlink', body).then(r => r.data)
export const deleteChannelLinkById = (id: number) =>
  api.delete(`/mapping/channel-link/${id}`).then(r => r.data)
export const saveNameMapping = (body: {
  ob_name: string; bh_sku: string; bh_name: string
  score?: number; method?: string; confirmed?: number
}) => api.post('/mapping/save', body).then(r => r.data)
export const confirmNameMapping = (obName: string) =>
  api.post(`/mapping/confirm/${encodeURIComponent(obName)}`).then(r => r.data)
export const deleteNameMapping = (obName: string) =>
  api.delete(`/mapping/delete/${encodeURIComponent(obName)}`).then(r => r.data)
export const autoMatch = (token: string, obNames: string[], threshold = 70) =>
  api.post('/mapping/auto-match', obNames, { params: { token, threshold }, timeout: 120000 }).then(r => r.data)

// 매핑 정합성 검사 — 이름 유사도 낮은 의심 매핑 탐지
export const getMappingAudit = (threshold = 50) =>
  api.get('/reconcile/mapping-audit', { params: { threshold }, timeout: 30000 }).then(r => r.data)

export async function aiSuggestMapping(
  payload: { ob_names: string[]; bh_items: {sku:string;name:string}[]; gemini_api_key?: string },
  onChunk: (text: string) => void,
  onDone: () => void,
  onError: (err: string) => void,
) {
  try {
    const res = await fetch('/api/mapping/ai-suggest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) { onError(`HTTP ${res.status}`); return }
    const reader = res.body!.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n'); buf = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const raw = line.slice(6).trim()
        if (raw === '[DONE]') { onDone(); return }
        try {
          const p = JSON.parse(raw)
          if (p.error) { onError(p.error); return }
          if (p.text) onChunk(p.text)
        } catch { /* ignore */ }
      }
    }
    onDone()
  } catch (e) { onError(String(e)) }
}

// ── 채널별 현황 ───────────────────────────────────────────────
export const getChannelSummary = (params: {
  token: string
  from_date: string
  to_date: string
  tx_type?: 'in' | 'out' | 'both'
  exclude_locations?: string
}) => api.get('/channel/summary', { params, timeout: 300000 }).then(r => r.data)

// ── 재고 대사 ─────────────────────────────────────────────────
export const getReconcile = (params: {
  token: string
  from_date: string
  to_date: string
  period: 'day' | 'week' | 'month' | 'year'
  location_ids?: string  // 콤마구분 location ID 목록
  use_mapping?: boolean
  mode?: 'period' | 'cumulative' | 'total'
  merge_types?: boolean   // 유형합산 모드: in/out/adj 순수량 합산 비교
  by_channel?: boolean
  qty_tolerance?: number  // 수량 허용오차 비율 (0.05 = ±5%)
  bh_lookback?: number    // 날짜 허용 범위 ±N일 (일간 모드에서 사용)
  exclude_adj?: boolean   // 조정 항목 제외 (BH 기초재고 설정 등 노이즈 제거)
  bh_adj_max_qty?: number // BH adj 임계값 (이 이상 qty는 기초재고로 간주 양측 제외)
}) => api.get('/reconcile/compare', { params, timeout: 300000 }).then(r => r.data)

export const getSmartCompare = (params: {
  token: string; from_date: string; to_date: string
  location_ids?: string; use_mapping?: boolean
  qty_tolerance?: number; bh_lookback?: number; name_threshold?: number
}) => api.get('/reconcile/smart-compare', { params, timeout: 300000 }).then(r => r.data)

export const getReconcileDetail = (params: {
  period: string; sku: string; tx_type: string; channel?: string; bh_lookback?: number
}) => api.get('/reconcile/detail', { params, timeout: 60000 }).then(r => r.data)

export const getReconcileItemSearch = (params: {
  token: string; query: string; from_date: string; to_date: string
  location_ids?: string
}) => api.get('/reconcile/item-search', { params, timeout: 120000 }).then(r => r.data)

export const getReconcileMissing = (params: {
  tx_type?: string
}) => api.get('/reconcile/missing', { params, timeout: 60000 }).then(r => r.data)

export const getReconcileMatch = (params: {
  token: string; from_date: string; to_date: string
  tolerance_days?: number; cross_type?: boolean; channel_filter?: string
}) => api.get('/reconcile/match', { params, timeout: 300000 }).then(r => r.data)

export const getReconcileStock = (params: { token: string; location_ids?: string; use_mapping?: boolean }) =>
  api.get('/reconcile/stock', { params, timeout: 120000 }).then(r => r.data)

// 주간 재고 리포트
export const createWeeklyReport = (params: { token: string; location_ids?: string; report_date?: string }) =>
  api.post('/reconcile/weekly-report', null, { params, timeout: 180000 }).then(r => r.data)
export const listWeeklyReports = (limit = 52) =>
  api.get('/reconcile/weekly-reports', { params: { limit }, timeout: 20000 }).then(r => r.data)
export const getWeeklyReport = (id: number) =>
  api.get(`/reconcile/weekly-report/${id}`, { timeout: 20000 }).then(r => r.data)

export const getReconcileQtyGap = (params: {
  token: string; from_date: string; to_date: string; exclude_channels?: string
}) => api.get('/reconcile/qty-gap', { params, timeout: 300000 }).then(r => r.data)

export const getReconcileProductMatch = (params: {
  token: string; from_date: string; to_date: string
  tx_types?: string; tolerance_days?: number; min_score?: number
  exclude_channels?: string; aggregate?: boolean
  wide_mode?: boolean; bh_lookback?: number
}) => api.get('/reconcile/product-match', { params, timeout: 300000 }).then(r => r.data)

// 매핑 제안 — 미매핑 BH/OB 상품 목록 + 이름 유사도 자동 페어
export const getSuggestMapping = (params: {
  token: string; from_date: string; to_date: string; min_score?: number; limit?: number
}) => api.get('/reconcile/suggest-mapping', { params, timeout: 120000 }).then(r => r.data)

// 세트 BOM (세트 구성표) CRUD
export const getSetBoms = () =>
  api.get('/reconcile/set-bom').then(r => r.data as SetBom[])
export const createSetBom = (body: Omit<SetBom, 'id' | 'created_at'>) =>
  api.post('/reconcile/set-bom', body).then(r => r.data)
export const deleteSetBom = (id: number) =>
  api.delete(`/reconcile/set-bom/${id}`).then(r => r.data)

export interface SetBom {
  id: number
  set_sku: string
  set_name: string
  component_sku: string
  component_name: string
  qty_per_set: number
  note: string
  created_at: string
}

// 전체 유형 통합 매칭 — BH(in+out+move+adjust) ↔ OB(입고+출고+조정) 풀 매칭
// 확정 매칭 쌍 조회 (행별 매칭 근거 검증용)
export const getMatchedPairs = (params: { from_date: string; to_date: string; name?: string }) =>
  api.get('/reconcile/matched-pairs', { params, timeout: 30000 }).then(r => r.data)

export const getReconcileFullMatch = (params: {
  token: string; from_date: string; to_date: string
  bh_lookback?: number; min_score?: number; location_ids?: string
}) => api.get('/reconcile/full-match', { params, timeout: 600000 }).then(r => r.data)

export async function analyzeReconcile(
  payload: {
    rows: unknown[]
    summary: unknown
    from_date: string
    to_date: string
    period: string
    claude_api_key: string
    gemini_api_key: string
    groq_api_key: string
  },
  onChunk: (text: string) => void,
  onDone: () => void,
  onError: (err: string) => void,
) {
  try {
    const res = await fetch('/api/reconcile/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      onError(data.detail || `HTTP ${res.status}`)
      return
    }
    const reader = res.body!.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const raw = line.slice(6).trim()
        if (raw === '[DONE]') { onDone(); return }
        try {
          const parsed = JSON.parse(raw)
          if (parsed.error) { onError(parsed.error); return }
          if (parsed.text) onChunk(parsed.text)
        } catch { /* ignore */ }
      }
    }
    onDone()
  } catch (e) {
    onError(String(e))
  }
}

// ── Helpers ──────────────────────────────────────────────────
export function base64ToBlob(b64: string, mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'): Blob {
  const bin = atob(b64)
  const arr = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i)
  return new Blob([arr], { type: mime })
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export default api
