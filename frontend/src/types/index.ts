export interface AppConfig {
  api_token?: string
  slack_token?: string
  selected_location_id?: number
  selected_location_name?: string
  gmail_client_id?: string
  gmail_client_secret?: string
  gmail_token?: GmailToken
  ourbox_id?: string
  ourbox_pw?: string
}

export interface GmailToken {
  token: string
  refresh_token: string
  token_uri: string
  client_id: string
  client_secret: string
  scopes: string[]
  expiry?: string
}

export interface Location {
  id: number
  name: string
}

export interface Partner {
  id: number
  name: string
}

export interface SlackOrder {
  ts: string
  dt: string
  title: string
  parsed: Record<string, string>
  files: SlackFile[]
  raw: string
  reactions: SlackReaction[]
}

export interface SlackFile {
  name: string
  url: string
  size: number
}

export interface SlackReaction {
  name: string
  count: number
}

export interface ReactionStatus {
  label: string
  color: string
  bg: string
  count: number
}

export interface GmailOrder {
  id: string
  dt: string
  subject: string
  sender: string
  files: GmailFile[]
}

export interface GmailFile {
  name: string
  attachment_id: string
  message_id: string
}

export interface ConvertResult {
  original_name: string
  quantity: number
  sku: string
  price: number
  matched_name: string
  score?: number
  method?: string
}

export interface ConvertUnmatched {
  original_name: string
  quantity: number
  score: number
  best_candidate: string
}

export interface StagedItem {
  sku: string
  quantity: number
  price: number
}

export interface LogEntry {
  ts: string
  level: 'success' | 'error' | 'warning' | 'info'
  message: string
  detail?: string
  payload?: Record<string, unknown>
  source?: string
}

export type Page = 'dashboard' | 'general' | 'naver' | 'slack' | 'gmail' | 'receiving' | 'docreview' | 'invoice' | 'settings'

// ─── 거래명세서 ───────────────────────────────────────────────────────────────

export interface Customer {
  id: string
  name: string
  business_no: string
  representative: string
  address: string
  phone: string
  email: string
}

export interface InvoiceItem {
  item_name: string
  sku: string
  qty: number
  unit_price: number
  remark: string
}

export interface SalesTransaction {
  tx_id: number | string
  date: string
  memo: string
  items: InvoiceItem[]
}

// ─── 입고정산기 ──────────────────────────────────────────────────────────────

export interface ReceivingRecord {
  id: number
  put_sno: string
  put_depot_nm: string
  vendor_nm: string
  put_req_dt: string
  put_compt_dtm: string
  put_type_nm: string
  item_cnt: number
  tot_put_qty: number
  status: 'pending' | 'approved' | 'ignored'
  boxhero_tx_id?: number
  approved_at?: string
  created_at: string
  items?: ReceivingItem[]
}

export interface ReceivingItem {
  id: number
  put_sno: string
  prod_cd: string
  sale_prod_nm: string
  put_qty: number
  put_detail_sno: string
  boxhero_item_id?: number
  boxhero_item_nm?: string
  boxhero_sku?: string
}

export interface ProductMapping {
  id: number
  ourbox_prod_cd: string
  ourbox_prod_nm: string
  boxhero_item_id: number
  boxhero_item_nm: string
  boxhero_sku: string
  created_at: string
}

export interface BoxheroItem {
  id: number
  name: string
  sku?: string
  barcode?: string
}
