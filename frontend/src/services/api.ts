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
