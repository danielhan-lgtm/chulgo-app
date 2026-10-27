// 슬랙 #물류_출고 출고요청 자동처리 (아워박스 발주등록 → BoxHero 출고 → 물류센터 메일 → 슬랙 ✅)
import axios from 'axios'

const api = axios.create({ baseURL: '/api/slack-dispatch' })

export interface DispatchRow {
  idx: number
  order_no: string
  name: string
  qty: number
  receiver: string
  tel: string
  zip: string
  addr: string
  memo: string
  courier?: string
  sender?: string
  ob_code: string
  ob_name: string
  ob_score: number
  ob_via?: string
  bh_sku: string
  bh_name: string
  bh_score: number
  bh_via?: string
  unit_price?: number
}

export interface DispatchInvoiceItem {
  code: string
  name: string
  qty: number
  price?: number
}

export interface DispatchInvoice {
  enabled: boolean
  recipient: string
  trade_name: string
  issue_date: string
  payment_terms: string
  doc_number: string
  items: DispatchInvoiceItem[]   // 채널 전표 품목(납품확인서 업로드). 비면 요청 품목 + 기억된 코드로 생성
}

export interface DispatchPrepare {
  ts: string
  channel_id: string
  title: string
  text: string
  sections: Record<string, string>
  req_date: string
  date_txt: string
  files: { name: string; url: string; size: number; parsed: number; used?: boolean }[]
  rows: DispatchRow[]
  orders: { order_no: string; receiver: string; tel: string; zip: string; addr: string; memo: string; lines: number; qty: number }[]
  source: 'excel' | 'text' | 'none'
  suggest: { ob_channel_code: string; bh_partner_id: number | null; bh_partner_name: string; rule: string; tag: string }
  mail: { to: string[]; cc: string[]; subject: string; body: string }
  bh_memo: string
  attachments: string[]
  invoice: DispatchInvoice
  log: DispatchLog | Record<string, never>
  warnings: string[]
}

export interface DispatchLog {
  ts: string
  channel_id: string
  title: string
  ob: { ok: boolean; company_order_codes: string[]; order_codes: string[]; channel_code: string; message: string } | null
  bh_tx_id: string | null
  bh: { ok: boolean; tx_id: string; skus: number; qty: number } | null
  mail_id: string | null
  mail: { ok: boolean; id: string; subject: string; to: string[]; attachments: string[] } | null
  oms: { ok: boolean; final: Record<string, { od_sno: number; stage: string; stage_nm: string }>; remaining: string[]; missing: string[] } | null
  updated_at: string
}

export interface DispatchRefs {
  settings: Record<string, any>
  ob_channels: { code: string; name: string; type: string }[]
  bh_partners: { id: number; name: string }[]
  bh_locations: { id: number; name: string }[]
  bh_location_default?: number
  gmail: { connected: boolean; can_send: boolean }
  errors: string[]
}

export interface DispatchRunReq {
  channel_id: string
  ts: string
  title: string
  steps: { ourbox: boolean; oms: boolean; boxhero: boolean; mail: boolean; slack_done: boolean }
  rows: Pick<DispatchRow, 'order_no' | 'name' | 'qty' | 'receiver' | 'tel' | 'zip' | 'addr' | 'memo' | 'ob_code' | 'ob_name' | 'bh_sku' | 'unit_price'>[]
  ob_channel_code: string
  order_prefix: string
  bh_partner_id: number | null
  bh_location_id: number | null
  bh_memo: string
  mail: { to: string[]; cc: string[]; subject: string; body: string; attach_slack_files: boolean }
  invoice: DispatchInvoice
  force: boolean
}

export interface DispatchRunRes {
  skipped: string[]
  warnings: string[]
  error?: string
  ourbox?: { ok: boolean; company_order_codes: string[]; order_codes: string[]; message: string }
  oms?: { ok: boolean; final: Record<string, { od_sno: number; stage: string; stage_nm: string }>; remaining: string[]; missing: string[] }
  boxhero?: { ok: boolean; tx_id: string; skus: number; qty: number }
  mail?: { ok: boolean; id: string; subject: string; attachments: string[] }
  slack_done?: { ok: boolean; text?: string }
  log: DispatchLog
}

export const getDispatchRefs = (): Promise<DispatchRefs> => api.get('/refs').then(r => r.data)
export const getDispatchProducts = (q: string): Promise<{ ob: { code: string; name: string; item_type: string }[]; bh: { sku: string; name: string }[] }> =>
  api.get('/products', { params: { q } }).then(r => r.data)
export const prepareDispatch = (channel_id: string, ts: string): Promise<DispatchPrepare> =>
  api.post('/prepare', { channel_id, ts }, { timeout: 120000 }).then(r => r.data)
export const runDispatch = (body: DispatchRunReq): Promise<DispatchRunRes> =>
  api.post('/run', body, { timeout: 900000 }).then(r => r.data)
export const downloadDispatchInvoice = (body: DispatchRunReq): Promise<Blob> =>
  api.post('/invoice', body, { responseType: 'blob' }).then(r => r.data)
export const uploadDispatchAttachment = (ts: string, file: File): Promise<{ attachments: string[] }> => {
  const fd = new FormData()
  fd.append('ts', ts)
  fd.append('file', file)
  return api.post('/attach', fd).then(r => r.data)
}
export const deleteDispatchAttachment = (ts: string, name: string): Promise<{ attachments: string[] }> =>
  api.delete('/attach', { params: { ts, name } }).then(r => r.data)
export const uploadInvoiceSource = (ts: string, file: File): Promise<{ ok: boolean; items: DispatchInvoiceItem[]; source: string }> => {
  const fd = new FormData()
  fd.append('ts', ts)
  fd.append('file', file)
  return api.post('/invoice-source', fd).then(r => r.data)
}
export const deleteInvoiceSource = (ts: string): Promise<{ ok: boolean }> =>
  api.delete('/invoice-source', { params: { ts } }).then(r => r.data)
export const getDispatchConfig = (): Promise<Record<string, any>> => api.get('/config').then(r => r.data)
export const setDispatchConfig = (data: Record<string, any>): Promise<Record<string, any>> =>
  api.post('/config', { data }).then(r => r.data)
