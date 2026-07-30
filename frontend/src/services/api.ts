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

// ── 폐기 리포트 ───────────────────────────────────────────────
export interface DisposalItem {
  brand: string
  name: string
  expiry: string
  qty: number
  status: string
}
export interface DisposalParseResult {
  sheets: string[]
  sheet: string
  columns: { status: string; name: string; qty: string; brand: string | null; expiry: string | null }
  statuses: string[]
  brands: string[]
  items: DisposalItem[]
}
export interface DisposalReportRow {
  brand: string
  name: string
  unit_price: number
  qty: number
  amount: number
  count: number
}

export const parseDisposal = (file: File | Blob, filename = 'inventory.xlsx', sheetName?: string) => {
  const fd = new FormData()
  fd.append('inv_file', file, filename)
  if (sheetName) fd.append('sheet_name', sheetName)
  return api.post<DisposalParseResult>('/disposal/parse', fd).then(r => r.data)
}

export const exportDisposal = (body: {
  base_date: string
  disposal_cost: number
  disposal_rows: DisposalReportRow[]
  donate_rows: DisposalReportRow[]
}) => api.post('/disposal/export', body, { responseType: 'blob' }).then(r => r.data as Blob)

// ── 소비기한 · 기부 리포트 (OB API) ──────────────────────────
export type ExpiryGrade = 'urgent' | 'caution' | 'normal' | 'none'
export interface ExpiryRow {
  code: string
  name: string
  expiry: string
  days_left: number | null
  total: number
  available: number
  unavailable: number
  grade: ExpiryGrade
}
export interface ExpiryReport {
  base_date: string
  warn_days: number
  caution_days: number
  summary: Record<ExpiryGrade, { items: number; total: number; available: number }>
  expired: { items: number; total: number }
  rows: ExpiryRow[]
}

export const getExpiryReport = (warnDays = 60, cautionDays = 120) =>
  api.get<ExpiryReport>('/disposal/expiry-report', {
    params: { warn_days: warnDays, caution_days: cautionDays },
  }).then(r => r.data)

export const exportExpiryReport = (body: {
  base_date: string
  warn_days: number
  caution_days: number
  rows: ExpiryRow[]
}) => api.post('/disposal/expiry-export', body, { responseType: 'blob' }).then(r => r.data as Blob)

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
export const getOurboxProductsForReceiving = () => api.get('/receiving/ourbox-products').then(r => r.data.data)

// ── 서류 검토 (여러 파일 한꺼번에 → 센터별 자동분류·교차검토, API 0원) ──────────
export const reviewCoupangDocs = (files: File[]) => {
  const fd = new FormData()
  files.forEach(f => fd.append('files', f))
  return api.post('/doc-review/coupang', fd, { timeout: 180000 }).then(r => r.data)
}

// ── 메일용 [출고 수량 상세] 자동 생성 (거래명세서 업로드 → 센터별 텍스트) ──────
export interface MailItem { name: string; qty: number; box: number; expire: string }
export interface MailGroup {
  center: string; date: string; items: MailItem[]; totalQty: number; totalBox: number
}
export interface MailBreakdownResult {
  text: string
  groups: MailGroup[]
  summary: { centers: number; totalQty: number; totalBox: number }
  files: { name: string; kind: string }[]
  parse_errors: string[]
}
export const coupangMailBreakdown = (files: File[]) => {
  const fd = new FormData()
  files.forEach(f => fd.append('files', f))
  return api.post<MailBreakdownResult>('/doc-review/coupang-mail', fd, { timeout: 180000 }).then(r => r.data)
}

// ── 쿠팡 적재리스트(PPT) 생성 ───────────────────────────────────────────────
export interface CoupangLoadRow {
  no?: number
  sku: string
  name: string
  box: number
  qty: number
  expire: string
  pallet: number
}

// 적재 설정 — 파레트당 박스 수·1박스당 수량 규칙 (backend data/load_settings.json 에 저장)
export interface LoadRule {
  match: string       // 상품번호(전체 일치) 또는 상품명 키워드(포함)
  per_box: number     // 1박스당 수량
}
export interface CoupangLoadSettings {
  pallet_cap: number                          // 파레트당 최대 박스 수
  pallet_total_mode: 'auto' | 'attach' | 'fixed'  // 팔레트 수 결정 방식
  pallet_total_fixed: number
  default_per_box: number                     // 기본 1박스당 수량
  bundle_is_box: boolean                      // N개입 등 번들 상품은 수량=박스
  rules: LoadRule[]
}
export const getCoupangLoadSettings = () =>
  api.get<CoupangLoadSettings>('/coupang-load/settings').then(r => r.data)
export const saveCoupangLoadSettings = (s: CoupangLoadSettings) =>
  api.post<CoupangLoadSettings>('/coupang-load/settings', s).then(r => r.data)

export interface CoupangLoadParseResult {
  supplier: string
  center: string
  date: string
  milkrun: string
  pallet: string
  pallet_total: number
  rows: CoupangLoadRow[]
  parse_errors: string[]
  warnings: string[]
  has_부착: boolean
  settings?: CoupangLoadSettings
}

export const parseCoupangLoad = (files: File[]) => {
  const fd = new FormData()
  files.forEach(f => fd.append('files', f))
  return api.post<CoupangLoadParseResult>('/coupang-load/parse', fd, { timeout: 120000 }).then(r => r.data)
}

export const generateCoupangLoad = (payload: {
  supplier: string
  center: string
  date: string
  milkrun: string
  pallet: string
  pallet_total: number
  rows: CoupangLoadRow[]
}) => api.post('/coupang-load/generate', payload, { responseType: 'blob' }).then(r => r.data as Blob)

// ── 쿠팡 그로스(로켓그로스) 적재리스트 ──────────────────────────────────────
export interface GrowthLoadRow {
  no?: number
  sku: string
  name: string
  box_no: string      // 박스 번호(박스 바코드)
  box: number         // 박스 수량(총박스 계산용)
  qty: number
  expire: string
  made: string
  pallet?: number     // 이 품목이 실릴 팔레트(=슬라이드) 번호
}
export interface GrowthLoadSettings {
  pallet_cap: number
  pallet_total_mode: 'auto' | 'attach' | 'fixed'
  pallet_total_fixed: number
  default_per_box: number
  bundle_is_box: boolean
  rules: LoadRule[]
}
export const getGrowthLoadSettings = () =>
  api.get<GrowthLoadSettings>('/coupang-growth-load/settings').then(r => r.data)
export const saveGrowthLoadSettings = (s: GrowthLoadSettings) =>
  api.post<GrowthLoadSettings>('/coupang-growth-load/settings', s).then(r => r.data)

export interface GrowthLoadParseResult {
  supplier: string
  supplier_code: string
  request_id: string
  center: string
  date: string
  pallet: string
  pallet_total: number
  box_barcode: string
  rows: GrowthLoadRow[]
  parse_errors: string[]
  has_부착: boolean
  settings?: GrowthLoadSettings
}
export const parseGrowthLoad = (files: File[]) => {
  const fd = new FormData()
  files.forEach(f => fd.append('files', f))
  return api.post<GrowthLoadParseResult>('/coupang-growth-load/parse', fd, { timeout: 120000 }).then(r => r.data)
}
export const generateGrowthLoad = (payload: {
  supplier: string; supplier_code: string; request_id: string; center: string
  date: string; pallet: string; pallet_total: number; total_box: number; rows: GrowthLoadRow[]
}) => api.post('/coupang-growth-load/generate', payload, { responseType: 'blob' }).then(r => r.data as Blob)

// ── 마켓컬리 서류 검토 (거래명세서 ↔ 라벨지) ────────────────────────────────
export const reviewKurlyDocs = (files: File[]) => {
  const fd = new FormData()
  files.forEach(f => fd.append('files', f))
  return api.post('/doc-review/kurly', fd, { timeout: 180000 }).then(r => r.data)
}

// ── 마켓컬리 입고 라벨지 ────────────────────────────────────────────────────
export interface KurlyItem {
  name: string
  code: string
  total: number
  expiry: string
  perBox: number
  boxCount: number
  orderCode?: string
}

export interface KurlyParseResult {
  orderCode: string
  orderCodes?: string[]
  supplier: string
  items: KurlyItem[]
  parse_errors: string[]
}

export const parseKurlyLabel = (files: File[]) => {
  const fd = new FormData()
  files.forEach(f => fd.append('files', f))
  return api.post<KurlyParseResult>('/kurly-label/parse', fd, { timeout: 120000 }).then(r => r.data)
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

// ── 출고 추세 & 재고 소진 예측 ──────────────────────────────────
export interface OutboundForecastRow {
  by_bucket: Record<string, number>
  total_out: number
  daily_avg: number
  monthly_avg: number
  forecast_total: number
}
export interface OutboundItemRow extends OutboundForecastRow {
  sku: string
  name: string
  stock: number
  deplete_months: number | null
  deplete_date: string | null
  remaining_after: number
}
export interface OutboundPartnerRow extends OutboundForecastRow {
  partner: string
  sku_count: number
}
export interface OutboundTargetRow {
  team: string
  brand: string
  name: string
  matched: boolean
  matched_names: string[]
  stock: number
  target_by_month: Record<string, number>
  actual_by_month: Record<string, number>
}
export interface OutboundTargetsBlock {
  enabled: boolean
  teams?: string[]
  team_labels?: Record<string, string>
  months?: string[]
  uploaded_at?: string
  rows?: OutboundTargetRow[]
  totals_by_team?: Record<string, { target: number; actual: number }>
  unmapped_partners?: { partner: string; total_out: number }[]
  mapped_partner_count?: number
  set_expanded_qty?: number
  partner_share?: Record<string, { partner: string; team: string; qty: number }[]>
  partner_share_by_month?: Record<string, Record<string, { partner: string; team: string; qty: number }[]>>
  overrides?: Record<string, number>
}
export interface OutboundForecastResult {
  from_date: string
  to_date: string
  period: 'day' | 'week' | 'month'
  forecast_months: number
  days_span: number
  expand_sets?: boolean
  buckets: string[]
  items: OutboundItemRow[]
  partners: OutboundPartnerRow[]
  targets: OutboundTargetsBlock
  summary: {
    grand_total: number
    daily_avg: number
    monthly_avg: number
    forecast_total: number
    item_count: number
    partner_count: number
    deplete_soon: number
    tx_count: number
  }
}
export const getOutboundForecast = (params: {
  token: string
  from_date: string
  to_date: string
  period: 'day' | 'week' | 'month'
  forecast_months: number
  location_ids?: string
  expand_sets?: boolean
}): Promise<OutboundForecastResult> =>
  api.get('/outbound/forecast', { params, timeout: 600000 }).then(r => r.data)

// 판매 목표치 업로드/조회/삭제
export interface TargetStatus {
  loaded: boolean
  filename?: string
  product_count?: number
  teams?: string[]
  team_labels?: Record<string, string>
  months?: string[]
  uploaded_at?: string
}
export const uploadOutboundTargets = (file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return api.post('/outbound/targets/upload', fd, { timeout: 60000 }).then(r => r.data)
}
export const getOutboundTargets = (): Promise<TargetStatus> =>
  api.get('/outbound/targets').then(r => r.data)
export const deleteOutboundTargets = () =>
  api.delete('/outbound/targets').then(r => r.data)

// 거래처 → 팀 매핑
export const getPartnerTeamMap = (token?: string): Promise<{
  mappings: Record<string, string>; teams: string[]; team_labels: Record<string, string>; partners: string[]
}> => api.get('/outbound/partner-team-map', { params: token ? { token } : {}, timeout: 60000 }).then(r => r.data)
export const savePartnerTeamMap = (mappings: Record<string, string>) =>
  api.post('/outbound/partner-team-map', { mappings }).then(r => r.data)

// 목표 수기 수정 (team|name|month → qty). qty null이면 해제
export const setTargetOverride = (body: { team: string; name: string; month: string; qty: number | null }) =>
  api.post('/outbound/target-override', body).then(r => r.data)

// ── OB 주문 자동화 (카페24/카카오) ─────────────────────────────
export interface OBChannel {
  mall_acc_sno: number
  sach_cd: string
  mall_nm: string
  acc_alias_nm: string
  colct_last_dtm: string
  is_cafe24: boolean
  is_kakao: boolean
}
export interface OBOrder {
  od_sno: number
  stage: string
  stage_nm: string
  od_state: string
  od_state_nm: string
  channel: 'cafe24' | 'kakao' | null
  sach_cd: string
  sach_nm: string
  mall_nm: string
  mall_od_no: string
  prod_cd: string
  prod_nm: string
  od_qty: number | string
  recvr_nm: string
  box_pack_no: string
  reg_dtm: string
  od_dtm: string
  colct_yn: string
}
export interface OBOverview {
  from_date: string
  to_date: string
  channels: OBChannel[]
  target_channels: string[]
  stage_labels: Record<string, string>
  stages: Record<string, OBOrder[]>
  counts: Record<string, number>
}
export const getOBOrdersOverview = (days = 30): Promise<OBOverview> =>
  api.get('/ob-orders/overview', { params: { days }, timeout: 180000 }).then(r => r.data)
export const getOBOrdersChannels = (): Promise<{ channels: OBChannel[] }> =>
  api.get('/ob-orders/channels', { timeout: 120000 }).then(r => r.data)

export interface OBAutoConfig {
  ob_auto_enabled: boolean
  ob_auto_mode: 'times' | 'interval'
  ob_auto_times: string          // "09:00,13:00,17:00"
  ob_auto_interval_min: number
  ob_auto_channels: string
  ob_auto_days: number
  status: { last_run: string | null; last_result: Record<string, number> | null; last_error: string | null; running: boolean; next_runs: string[] }
}
export const getOBAutoConfig = (): Promise<OBAutoConfig> =>
  api.get('/ob-orders/auto-config').then(r => r.data)
export const setOBAutoConfig = (patch: { enabled?: boolean; mode?: 'times' | 'interval'; times?: string; interval_min?: number; channels?: string; days?: number }): Promise<OBAutoConfig> =>
  api.post('/ob-orders/auto-config', patch).then(r => r.data)
// dry_run=false면 실제 수집→전진→BH등록 (운영 쓰기)
export const runOBAutoOnce = (dry_run = true, days = 30): Promise<any> =>
  api.post('/ob-orders/auto-run', { dry_run, days }, { timeout: 600000 }).then(r => r.data)

// 아워박스 저장 세션 상태 / 재로그인 창 실행
export interface OBSessionStatus { ok: boolean | null; detail: string; checked_at: string | null }
export const getOBSessionStatus = (live = false): Promise<OBSessionStatus> =>
  api.get('/ob-orders/session-status', { params: { live }, timeout: 30000 }).then(r => r.data)
export const startOBSessionLogin = (): Promise<{ started: boolean; already_running?: boolean; message: string }> =>
  api.post('/ob-orders/session-login').then(r => r.data)

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
  hide_resolved?: boolean // 정리완료/무시로 마킹된 행 숨김
}) => api.get('/reconcile/compare', { params, timeout: 300000 }).then(r => r.data)

// ── 행 단위 정리(전산정리) 상태 ──────────────────────────────────────
export type ReconcileCleanupStatus = 'reviewing' | 'resolved' | 'hold' | 'ignore'
export interface ReconcileStatusRecord {
  id: number; row_key: string; tx_type: string; sku: string; name: string
  channel: string; period: string; status: ReconcileCleanupStatus
  root_cause: string; bh_qty: number | null; ob_qty: number | null
  memo: string; assignee: string; created_at: string; updated_at: string
}
export const getReconcileStatuses = (params: { from_period?: string; to_period?: string }) =>
  api.get('/reconcile/status', { params, timeout: 30000 }).then(r => r.data as { items: ReconcileStatusRecord[] })

export const setReconcileStatus = (body: {
  tx_type: string; sku: string; period: string; channel?: string
  status: ReconcileCleanupStatus; root_cause?: string; name?: string
  bh_qty?: number | null; ob_qty?: number | null; memo?: string; assignee?: string
}) => api.post('/reconcile/status', body, { timeout: 30000 }).then(r => r.data)

export const clearReconcileStatus = (params: {
  tx_type: string; sku: string; period: string; channel?: string
}) => api.delete('/reconcile/status', { params, timeout: 30000 }).then(r => r.data)

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

// 재고 차이 변화 추적 — 두 리포트(t1→t2) 사이 품목 차이가 왜 벌어졌는지 분해
export const getStockDiffChange = (params: {
  token: string; name: string
  report_t1_id: number; report_t2_id?: number
  location_ids?: string; include_fm?: boolean
}) => api.get('/reconcile/stock-diff-change', { params, timeout: 600000 }).then(r => r.data)

// 거래처(채널)별 또는 제품별 입·출고·조정 총량 BH vs OB 비교
export const getChannelFlow = (params: {
  token: string; from_date: string; to_date: string; location_ids?: string
  group_by?: 'channel' | 'product'; product_filter?: string
}) => api.get('/reconcile/channel-flow', { params, timeout: 600000 }).then(r => r.data)

// 단일 품목의 BH 출고를 채널별 'OB 경유 / OB 미경유(직배송)'로 분해
export type OutDecompChannel = { channel: string; bh_out: number; ob_out: number; diff: number; kind: 'ob_bypass'|'bh_missing'|'diff'|'match'; prebook: number }
export type PrebookTx = { date: string; ship_date: string; channel: string; qty: number; memo: string }
export type OutDecomp = {
  name: string; from: string; to: string
  bh_out_total: number; ob_out_total: number; diff: number
  bypass_bh: number; prebook_bh: number; routed_bh: number; routed_ob: number; routed_diff: number; real_diff: number; bh_missing_ob: number
  channels: OutDecompChannel[]
  prebook: PrebookTx[]
  channel_mapped: boolean; ob_source: string; errors: string[]
}
export const getItemOutDecompose = (params: {
  token: string; name?: string; bh_skus?: string; ob_codes?: string
  from_date: string; to_date: string; location_ids?: string
}): Promise<OutDecomp> => api.get('/reconcile/item-out-decompose', { params, timeout: 600000 }).then(r => r.data)

// 거래 흐름 정밀 대사 — 재고 차이가 '어느 거래에서' 났는지 이벤트 단위 분해
export type TraceEvent = { date: string; qty: number; memo?: string; channel?: string; type?: string; synthetic?: boolean; detail?: string }
export type TraceCause = {
  type: string; impact: number; qty: number; date: string
  memo?: string; channel?: string; ev_type?: string; partner?: string; desc: string
}
export type UnavEvent = { date: string; delta: number }
export type StockDiffTrace = {
  name: string; from: string; to: string
  bh_stock: number | null; ob_total: number | null; ob_unav: number
  totals: { bh_in: number; ob_in: number; bh_out: number; ob_out: number }
  flow_diff: number; opening_gap: number | null; diff_now: number | null
  explained: number; residual: number | null
  causes: TraceCause[]
  matched_in: number; matched_out: number
  unmatched: { bh_in: TraceEvent[]; ob_in: TraceEvent[]; bh_out: TraceEvent[]; ob_out: TraceEvent[] }
  // 참고(−가용) 기준 분해: BH−OB가용 = (BH−OB총재고) + 가용외
  avail_basis?: {
    diff_avail: number | null; ob_unav: number
    unav_events: UnavEvent[]
    snapshots: { from: string; to: string; unav_first: number; unav_last: number } | null
  }
  ob_source: string; errors: string[]
}
export const getStockDiffTrace = (params: {
  token: string; name?: string; bh_skus?: string; ob_codes?: string
  from_date: string; to_date: string; location_ids?: string
  bh_stock?: number; ob_total?: number; ob_unav?: number; tol_days?: number
}): Promise<StockDiffTrace> => api.get('/reconcile/stock-diff-trace', { params, timeout: 600000 }).then(r => r.data)

// OB 가용외(할당) 스냅샷 시계열 — 가용→가용외 할당이 언제 떨어지는지 추적
export type StockSnap = { captured_at: string; total: number; available: number; unavailable: number; codes: number; d_unavail: number; d_avail: number; d_total: number }
export const getStockSnapshots = (params: {
  name?: string; codes?: string; limit?: number
}): Promise<{ name: string; codes: string[]; series: StockSnap[]; count: number }> =>
  api.get('/reconcile/stock-snapshots', { params }).then(r => r.data)
export const captureStockSnapshot = (): Promise<{ saved: boolean; captured_at?: string; count?: number; skipped?: boolean }> =>
  api.post('/reconcile/capture-stock-snapshot', null, { params: { force: true }, timeout: 120000 }).then(r => r.data)

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

// ── 발주 캘린더 (다중 발주서 → 품목×날짜 매트릭스) ────────────────────────
export interface OrderPlanColumns {
  format: 'coupang' | 'kurly' | 'generic'
  columns: string[]
  date_col: string
  name_col: string
  qty_col: string
  recv_col?: string        // 확정 입고 수량 컬럼 (자동 감지, 없으면 '')
  recv_detected?: boolean  // 쿠팡 양식에서 확정수량 컬럼 감지 여부
  channel: string
  preview: Record<string, string | number>[]
  rows: number
  detected_date?: string
  item_count?: number
}

export interface RawBreakdownEntry {
  by_date: Record<string, number>
  by_channel: Record<string, number>
  total: number
  had_bundle?: boolean
  bundle_count?: number
}

export interface OrderPlanItem {
  sku: string
  name: string
  matched: boolean
  match_score: number
  match_source?: 'user' | 'user-unmatch' | 'barcode' | 'fuzzy' | 'extract' | ''
  had_bundle?: boolean
  by_date: Record<string, number>
  by_channel: Record<string, number>
  total: number
  sources: string[]
  raw_names: string[]
  raw_breakdown?: Record<string, RawBreakdownEntry>
}

export interface BundleSplit {
  raw_name: string
  cleaned: string
  count: number
  qty_each: number
  qty_total: number
  source: string
}

export interface OrderPlanResult {
  dates: string[]
  channels: string[]
  items: OrderPlanItem[]
  total_by_date: Record<string, number>
  total_by_channel: Record<string, number>
  grand_total: number
  item_count: number
  matched_count: number
  unmatched_count: number
  per_file: { filename: string; channel: string; rows: number; qty: number; skipped_no_date: number }[]
  master_used: boolean
  errors: string[]
  bundle_splits?: BundleSplit[]
  user_mapping_count?: number
}

export interface OrderPlanUserMapping {
  raw_name: string
  key: string
  sku: string
  master_name: string
  note: string
}

export interface OrderPlanMapping {
  filename: string
  channel: string
  format?: 'coupang' | 'kurly' | 'generic'
  date_col: string
  name_col: string
  qty_col: string
  recv_col?: string   // 확정 입고 수량 컬럼 (선택 — 일반 양식용)
}

export const detectOrderPlanColumns = (file: File): Promise<OrderPlanColumns> => {
  const fd = new FormData()
  fd.append('order_file', file, file.name)
  return api.post('/order-plan/columns', fd).then(r => r.data)
}

export const aggregateOrderPlan = (
  files: File[],
  mappings: OrderPlanMapping[],
  opts: { threshold?: number; useMaster?: boolean; splitBundles?: boolean } = {},
): Promise<OrderPlanResult> => {
  const fd = new FormData()
  files.forEach(f => fd.append('files', f, f.name))
  fd.append('mappings', JSON.stringify(mappings))
  fd.append('threshold', String(opts.threshold ?? 70))
  fd.append('use_master', opts.useMaster === false ? '0' : '1')
  fd.append('split_bundles', opts.splitBundles === false ? '0' : '1')
  return api.post('/order-plan/aggregate', fd, { timeout: 180000 }).then(r => r.data)
}

export const listOrderPlanUserMappings = (): Promise<{ items: OrderPlanUserMapping[]; total: number }> =>
  api.get('/order-plan/user-mappings').then(r => r.data)

export const upsertOrderPlanUserMapping = (payload: {
  raw_name: string
  sku?: string
  master_name?: string
  note?: string
}) => api.post('/order-plan/user-mappings', payload).then(r => r.data)

export const deleteOrderPlanUserMapping = (raw_name: string) =>
  api.post('/order-plan/user-mappings/delete', { raw_name }).then(r => r.data)

export const clearOrderPlanUserMappings = () =>
  api.post('/order-plan/user-mappings/clear', {}).then(r => r.data)

// ── 누적 발주 저장소 (파일 넣을수록 누적/업데이트) ──────────────────
export interface PlanFile {
  filename: string; channel: string; format: string; rows: number; qty: number; ingested_at: string
}
export interface PlanDuplicate { filename: string; existing: string; reason: string }
// 발주서를 누적 저장소에 추가/갱신 → 전체 누적 결과 반환
// 내용이 같은 발주서(바이트/파싱결과 해시)는 중복으로 건너뛰고 duplicates에 보고
export const ingestOrderPlan = (
  files: File[],
  mappings: OrderPlanMapping[],
  opts: { threshold?: number; useMaster?: boolean; splitBundles?: boolean } = {},
): Promise<OrderPlanResult & {
  added?: string[]
  duplicates?: PlanDuplicate[]
  replaced?: { filename: string; replaced: string }[]   // 같은 발주번호 → 기존 파일 대체
  file_count?: number
}> => {
  const fd = new FormData()
  files.forEach(f => fd.append('files', f, f.name))
  fd.append('mappings', JSON.stringify(mappings))
  fd.append('threshold', String(opts.threshold ?? 70))
  fd.append('use_master', opts.useMaster === false ? '0' : '1')
  fd.append('split_bundles', opts.splitBundles === false ? '0' : '1')
  return api.post('/order-plan/ingest', fd, { timeout: 180000 }).then(r => r.data)
}
// 현재 누적 발주 전체 (페이지 진입 시)
export const getOrderPlan = (
  opts: { threshold?: number; useMaster?: boolean; splitBundles?: boolean } = {},
): Promise<OrderPlanResult & { file_count?: number }> =>
  api.get('/order-plan/plan', { params: {
    threshold: opts.threshold ?? 70,
    use_master: opts.useMaster === false ? 0 : 1,
    split_bundles: opts.splitBundles === false ? 0 : 1,
  } }).then(r => r.data)
export const listPlanFiles = (): Promise<{ files: PlanFile[]; total: number }> =>
  api.get('/order-plan/plan/files').then(r => r.data)
export const removePlanFile = (
  filename: string,
  opts: { threshold?: number; useMaster?: boolean; splitBundles?: boolean } = {},
): Promise<OrderPlanResult> =>
  api.post('/order-plan/plan/remove', { filename }, { params: {
    threshold: opts.threshold ?? 70,
    use_master: opts.useMaster === false ? 0 : 1,
    split_bundles: opts.splitBundles === false ? 0 : 1,
  } }).then(r => r.data)
export const clearOrderPlan = (): Promise<{ ok: boolean }> =>
  api.post('/order-plan/plan/clear', {}).then(r => r.data)
// 특정 출고일 발주 행만 초기화
export const clearOrderPlanDate = (
  date: string,
  opts: { threshold?: number; useMaster?: boolean; splitBundles?: boolean } = {},
): Promise<OrderPlanResult & { cleared_date?: string; removed_rows?: number; removed_qty?: number }> =>
  api.post('/order-plan/plan/clear-date', { date }, { params: {
    threshold: opts.threshold ?? 70,
    use_master: opts.useMaster === false ? 0 : 1,
    split_bundles: opts.splitBundles === false ? 0 : 1,
  } }).then(r => r.data)

export interface PlanDashboard {
  daily: { date: string; qty: number }[]
  monthly: { month: string; qty: number }[]
  by_channel: { channel: string; qty: number }[]
  channel_month: { channels: string[]; months: string[]; matrix: Record<string, Record<string, number>> }
  top_items: { sku: string; name: string; total: number; matched: boolean }[]
  grand_total: number; item_count: number; file_count: number
  date_range: { from: string; to: string }
}
export const getPlanDashboard = (
  opts: { threshold?: number; useMaster?: boolean; splitBundles?: boolean } = {},
): Promise<PlanDashboard> =>
  api.get('/order-plan/dashboard', { params: {
    threshold: opts.threshold ?? 70,
    use_master: opts.useMaster === false ? 0 : 1,
    split_bundles: opts.splitBundles === false ? 0 : 1,
  } }).then(r => r.data)

// ── 발주 대비 확정 입고 비교 (발주서 파일 안의 확정수량 컬럼 기준) ──
export interface ReceivingCompareItem {
  sku: string; name: string
  ordered: number; received: number; diff: number; rate: number
  ordered_amt: number; received_amt: number   // 발주금액·입고금액 (파싱 안 되면 0)
  status: 'full' | 'partial' | 'none' | 'over' | 'nodata'
  ordered_by_date: Record<string, number>
  received_by_date: Record<string, number>
  ordered_amt_by_date?: Record<string, number>   // 일자별 발주금액 (병합 후 일별 재계산용)
  received_amt_by_date?: Record<string, number>
  raw_breakdown?: Record<string, ReceivingCompareRaw>  // raw 품명 단위 분해 (매트릭스 세부이동 반영용)
}
export interface ReceivingCompareRaw {
  ordered_by_date: Record<string, number>
  received_by_date: Record<string, number>
  ordered_amt_by_date: Record<string, number>
  received_amt_by_date: Record<string, number>
  has_recv: boolean
}
export interface ReceivingCompareChannel {
  channel: string; ordered: number; received: number; rate: number
  ordered_amt: number; received_amt: number
}
export interface ReceivingCompare {
  range: { from: string; to: string }
  channel?: string             // 적용된 거래처 필터 ('' = 전체)
  channels: string[]           // 전체 거래처 목록
  by_channel: ReceivingCompareChannel[]   // 거래처별 발주/입고 (항상 전체 기준)
  summary: {
    ordered_total: number; ordered_with_data: number; received_total: number; rate: number
    item_count: number; full_count: number; partial_count: number
    none_count: number; over_count: number; nodata_count: number
    ordered_amt_total: number; received_amt_total: number
  }
  items: ReceivingCompareItem[]
  daily: {
    date: string; ordered: number; ordered_with_data: number; received: number
    ordered_amt: number; received_amt: number
  }[]
}
export const getReceivingCompare = (
  opts: { threshold?: number; useMaster?: boolean; splitBundles?: boolean; fromDate?: string; toDate?: string; channel?: string } = {},
): Promise<ReceivingCompare> =>
  api.get('/order-plan/receiving-compare', { params: {
    threshold: opts.threshold ?? 70,
    use_master: opts.useMaster === false ? 0 : 1,
    split_bundles: opts.splitBundles === false ? 0 : 1,
    from_date: opts.fromDate ?? '',
    to_date: opts.toDate ?? '',
    channel: opts.channel ?? '',
  } }).then(r => r.data)

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
