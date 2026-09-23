import { useState, useEffect, useMemo, useRef, Fragment } from 'react'
import { Button, DatePicker, Upload, Spin, Alert, Tag, Tooltip, message, Popconfirm, Collapse, Input, Select, InputNumber, Segmented } from 'antd'
import {
  ReloadOutlined, CopyOutlined, UploadOutlined, SaveOutlined, DeleteOutlined, EditOutlined, PlusOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import {
  getCoupangReport, uploadCoupangReportFiles, clearCoupangReportUploads, saveCoupangReportNotes,
  getCoupangReportAliases, saveCoupangReportAliases, saveCoupangIncoming, deleteCoupangIncoming,
  getChannelPrices, saveChannelPrices, uploadChannelPrices, clearChannelPrices,
  getChannelExecReport, saveChannelMonthly,
} from '../services/api'
import type { CoupangReportSummary, CpGroup, CpStockRow, CpTrendRow, ChannelReportBase, CpPrices, ChannelExecReport } from '../services/api'

export interface ChannelCfg {
  base: ChannelReportBase
  title: string          // 페이지 제목
  name: string           // 채널 표기 (쿠팡/올리브영)
  icon: string
  desc: string
  poWord: string         // 발주서 / 납품확인서
  recvWord: string       // 입고등록 / 입고확정
  uploadLabel: string
  uploadHint: React.ReactNode
  monthly: boolean       // 판매/재고 파일이 월 단위(올리브영)
  noUpload?: boolean     // 판매/재고 파일 업로드가 없는 채널(컬리)
  noAmt?: boolean        // 발주 데이터에 금액이 없는 채널(컬리) → 단가표가 없으면 금액을 '-'로 표시
  priceTable?: boolean   // 매입 단가표(상품공급정보 xlsx)를 올려 수량×단가로 금액을 계산하는 채널(컬리)
}

/** 쿠팡 발주 보고 — 발주 캘린더에 쌓인 쿠팡 발주서(납기·확정·입고)와 BH 재고/출고,
 *  업로드한 쿠팡 판매·재고 리포트를 합쳐 의장 보고용 텍스트를 만든다.
 *
 *  - 발주 데이터는 발주 캘린더 저장소를 그대로 읽는다 (여기서 업로드하지 않음)
 *  - 판매/재고 추이는 쿠팡 윙의 basic_operation_rocket_*.csv, *-forecast-*.xlsx 를 여기서 올린다
 *  - 항공편·연장요청 등 시스템이 모르는 내용은 '특이사항' 메모에 적으면 보고문 끝에 붙는다
 */

const CSS = `
.cpr { font-family: Inter, 'Malgun Gothic', sans-serif; color:#111827; }
.cpr .grid { display:grid; grid-template-columns: minmax(0, 1.25fr) minmax(360px, 0.95fr); gap:16px; align-items:start; }
@media (max-width: 1200px) { .cpr .grid { grid-template-columns: 1fr; } }
.cpr .panel { background:#fff; border:1px solid #e5e7eb; border-radius:12px; padding:14px 16px; margin-bottom:14px; }
.cpr .panel h3 { margin:0 0 10px; font-size:0.95rem; font-weight:800; display:flex; align-items:center; gap:8px; }
.cpr .panel h3 .sub { font-weight:500; color:#6b7280; font-size:0.75rem; }
.cpr .kpis { display:grid; grid-template-columns: repeat(4, 1fr); gap:10px; margin-bottom:14px; }
.cpr .kpi { background:#fff; border:1px solid #e5e7eb; border-radius:12px; padding:12px 14px; }
.cpr .kpi .k { font-size:0.72rem; color:#6b7280; font-weight:600; }
.cpr .kpi .v { font-size:1.35rem; font-weight:800; margin-top:4px; letter-spacing:-0.3px; white-space:nowrap; }
.cpr .kpi .v small { font-size:0.72rem; font-weight:500; color:#6b7280; margin-left:4px; }
.cpr .kpi .d { font-size:0.72rem; color:#6b7280; margin-top:4px; }
.cpr table { width:100%; border-collapse:collapse; font-size:0.8rem; }
.cpr th { text-align:left; color:#6b7280; font-weight:600; font-size:0.72rem; padding:6px 8px; border-bottom:1px solid #e5e7eb; background:#f9fafb; }
.cpr td { padding:6px 8px; border-bottom:1px solid #f1f5f9; vertical-align:top; }
.cpr td.num, .cpr th.num { text-align:right; font-variant-numeric: tabular-nums; white-space:nowrap; }
.cpr tr.grp td { background:#f8fafc; font-weight:700; }
.cpr tr.total td { font-weight:800; border-top:2px solid #e5e7eb; }
.cpr .muted { color:#9ca3af; }
.cpr .ok { color:#059669; } .cpr .warn { color:#d97706; } .cpr .bad { color:#dc2626; }
.cpr .spark { display:inline-flex; align-items:flex-end; gap:1px; height:18px; }
.cpr .spark i { display:block; width:4px; background:#93c5fd; border-radius:1px; min-height:1px; }
.cpr .spark i.last { background:#2563eb; }
.cpr textarea.report { width:100%; min-height:560px; font-family: 'Malgun Gothic', Inter, sans-serif; font-size:0.82rem; line-height:1.55;
  border:1px solid #e5e7eb; border-radius:10px; padding:12px 14px; resize:vertical; background:#fbfbfc; }
.cpr textarea.report:focus { outline:none; border-color:#10b981; background:#fff; }
.cpr .toolbar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.cpr .hint { font-size:0.74rem; color:#6b7280; line-height:1.5; }
.cpr .files { font-size:0.74rem; color:#4b5563; }
`

const n = (v: number | null | undefined, d = 0) =>
  v === null || v === undefined ? '-' : v.toLocaleString(undefined, { maximumFractionDigits: d })
let NO_AMT = false
const won = (v: number | null | undefined) => {
  if (NO_AMT || v === null || v === undefined) return '-'
  if (v < 10000) return `${n(v)}원`
  const man = Math.round(v / 10000)
  if (man >= 10000) { const eok = Math.floor(man / 10000), rem = man % 10000; return rem ? `${eok}억 ${n(rem)}만원` : `${eok}억원` }
  return `${n(man)}만원`
}

function Spark({ series }: { series: number[] }) {
  if (!series.length) return <span className="muted">-</span>
  const max = Math.max(...series, 1)
  return (
    <span className="spark" title={series.join(', ')}>
      {series.slice(-14).map((v, i, arr) => (
        <i key={i} className={i === arr.length - 1 ? 'last' : ''} style={{ height: `${Math.max(1, Math.round(v / max * 18))}px` }} />
      ))}
    </span>
  )
}

function stockTag(r: CpStockRow) {
  switch (r.status) {
    case 'ok': return <Tag color="green">대응 가능</Tag>
    case 'short': return <Tag color="orange">부족</Tag>
    case 'out': return <Tag color="red">소진</Tag>
    case 'unmapped': return <Tooltip title="마스터 파일에 매칭되지 않아 BH 재고를 찾을 수 없습니다. 발주 캘린더 > 상품 매핑에서 연결하세요."><Tag>미매핑</Tag></Tooltip>
    default: return <Tag color="default">발주 없음</Tag>
  }
}

export default function ChannelReport({ cfg }: { cfg: ChannelCfg }) {
  const B = cfg.base
  const [asOf, setAsOf] = useState(dayjs().format('YYYY-MM-DD'))
  const [data, setData] = useState<CoupangReportSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [textDirty, setTextDirty] = useState(false)
  type Fmt = 'compact' | 'detail' | 'exec'
  const [fmt, setFmt] = useState<Fmt>(() => { try { return (localStorage.getItem('cpr_fmt') as Fmt) || 'compact' } catch { return 'compact' } })
  const [execRep, setExecRep] = useState<ChannelExecReport | null>(null)
  const [execLoading, setExecLoading] = useState(false)
  // exec: 묶음 응답이 같은 기준일일 때만 쓰고, 아니면 이 채널 단독 text_exec 로 폴백 (기준일 변경 중 옛 텍스트 덮어쓰기 방지)
  const pick = (d: CoupangReportSummary, f: Fmt) => f === 'detail' ? d.text_detail : f === 'exec' ? ((execRep && execRep.as_of === d.as_of ? execRep.text : undefined) ?? d.text_exec) : d.text
  const [monthlyEdit, setMonthlyEdit] = useState<Record<string, { order_qty?: number | null; delivered?: number | null }>>({})
  const [monthlySaving, setMonthlySaving] = useState(false)
  const [notes, setNotes] = useState('')
  const [notesSaving, setNotesSaving] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [aliasText, setAliasText] = useState('')
  const [aliasOpen, setAliasOpen] = useState(false)
  const [incLabel, setIncLabel] = useState<string>('')
  const [incQty, setIncQty] = useState<number | null>(null)
  const [incDate, setIncDate] = useState<string>(dayjs().add(2, 'day').format('YYYY-MM-DD'))
  const [incMemo, setIncMemo] = useState('')
  const [uploadPeriod, setUploadPeriod] = useState(dayjs().format('YYYY-MM'))
  const [incSaving, setIncSaving] = useState(false)
  const pendingFiles = useRef<File[]>([])
  const uploadTimer = useRef<number | null>(null)
  const [prices, setPrices] = useState<CpPrices | null>(null)
  const [priceOpen, setPriceOpen] = useState(false)
  const [priceEdit, setPriceEdit] = useState<Record<string, number | null>>({})
  const [priceBusy, setPriceBusy] = useState(false)
  const pendingPriceFiles = useRef<File[]>([])
  const priceTimer = useRef<number | null>(null)
  // 금액 없는 채널이라도 단가표로 금액이 채워졌으면(has_amt) 표시
  const noAmt = !!cfg.noAmt && !data?.has_amt
  NO_AMT = noAmt

  async function load(refreshBh = false) {
    setLoading(true); setError(null)
    try {
      const d = await getCoupangReport(asOf, asOf.slice(0, 7), refreshBh, B)
      setData(d)
      if (!textDirty) setText(pick(d, fmt))
      setNotes(d.notes || '')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '조회 실패')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { load() }, [asOf])  // eslint-disable-line react-hooks/exhaustive-deps

  // 경영 요약(전 채널 묶음) — 이 형식을 고를 때만 쿠팡·올리브영·컬리·네이버 엔진을 한 번에 돌린다
  async function loadExec(force = false) {
    if (execLoading) return
    if (!force && execRep && execRep.as_of === asOf) return
    setExecLoading(true)
    try {
      const r = await getChannelExecReport(asOf, asOf.slice(0, 7))
      setExecRep(r)
      if (!textDirty) setText(r.text)
      r.channels.filter(c => c.error).forEach(c => message.warning(`${c.name} 집계 실패: ${c.error}`))
    } catch (e: unknown) { message.error(e instanceof Error ? e.message : '경영 요약 조회 실패') }
    finally { setExecLoading(false) }
  }
  useEffect(() => { if (fmt === 'exec') loadExec() }, [fmt, asOf])  // eslint-disable-line react-hooks/exhaustive-deps

  // 저장·업로드 뒤 다시 읽기 — 경영 요약 모드면 4채널 묶음도 다시 집계 (아니면 저장 전 텍스트로 되돌아감)
  async function reload(refreshBh = false) {
    await load(refreshBh)
    if (fmt === 'exec') await loadExec(true)
  }

  async function saveMonthly() {
    setMonthlySaving(true)
    try {
      await saveChannelMonthly(monthlyEdit, B)
      setMonthlyEdit({}); setTextDirty(false); message.success('월별 추이 저장')
      setExecRep(null); await load(); if (fmt === 'exec') await loadExec(true)
    } catch { message.error('저장 실패') } finally { setMonthlySaving(false) }
  }
  async function resetMonthly(ym: string) {
    setMonthlySaving(true)
    try {
      await saveChannelMonthly({ [ym]: null }, B)
      setMonthlyEdit(e => { const c = { ...e }; delete c[ym]; return c }); setTextDirty(false)
      setExecRep(null); await load(); if (fmt === 'exec') await loadExec(true)
    } catch { message.error('초기화 실패') } finally { setMonthlySaving(false) }
  }

  // 파일 여러 개 드롭 시 한 번에 업로드 (antd Upload는 파일별로 beforeUpload 호출)
  function queueUpload(file: File) {
    pendingFiles.current.push(file)
    if (uploadTimer.current) window.clearTimeout(uploadTimer.current)
    uploadTimer.current = window.setTimeout(async () => {
      const files = pendingFiles.current.splice(0)
      setUploading(true)
      try {
        const res = await uploadCoupangReportFiles(files, B, cfg.monthly ? uploadPeriod : undefined)
        res.errors.forEach(e => message.error(e))
        if (res.added.length) message.success(`${res.added.map(a => a.filename).join(', ')} 반영`)
        await reload()
      } catch { message.error('업로드 실패') } finally { setUploading(false) }
    }, 250)
    return false
  }

  async function loadPrices() {
    try { const r = await getChannelPrices(B); setPrices(r.prices); setPriceEdit({}) } catch { message.error('단가표 조회 실패') }
  }
  useEffect(() => { if (cfg.priceTable) loadPrices() }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  function queuePriceUpload(file: File) {
    pendingPriceFiles.current.push(file)
    if (priceTimer.current) window.clearTimeout(priceTimer.current)
    priceTimer.current = window.setTimeout(async () => {
      const files = pendingPriceFiles.current.splice(0)
      setPriceBusy(true)
      try {
        const res = await uploadChannelPrices(files, B)
        res.errors.forEach(e => message.error(e))
        if (res.added) message.success(`단가 ${res.added}건 반영${res.skipped.length ? ` · 단가 0 제외: ${res.skipped.join(', ')}` : ''}`)
        setPrices(res.prices); setPriceEdit({}); setPriceOpen(true); setTextDirty(false)
        await reload()
      } catch { message.error('단가표 업로드 실패') } finally { setPriceBusy(false) }
    }, 250)
    return false
  }
  async function savePrices(vatIncluded?: boolean) {
    setPriceBusy(true)
    try {
      const r = await saveChannelPrices({ items: priceEdit, ...(vatIncluded === undefined ? {} : { vat_included: vatIncluded }) }, B)
      setPrices(r.prices); setPriceEdit({}); setTextDirty(false); message.success('단가표 저장'); await reload()
    } catch { message.error('단가표 저장 실패') } finally { setPriceBusy(false) }
  }
  async function removePrices() {
    setPriceBusy(true)
    try { const r = await clearChannelPrices(B); setPrices(r.prices); setPriceEdit({}); setTextDirty(false); await reload() }
    catch { message.error('단가표 삭제 실패') } finally { setPriceBusy(false) }
  }

  async function saveNotes() {
    setNotesSaving(true)
    try { await saveCoupangReportNotes(notes, B); message.success('특이사항 저장'); setTextDirty(false); await reload() }
    catch { message.error('저장 실패') } finally { setNotesSaving(false) }
  }

  async function addIncoming() {
    if (!incLabel || !incQty || !incDate) { message.warning('품목·수량·도착일을 입력하세요'); return }
    setIncSaving(true)
    try { await saveCoupangIncoming({ label: incLabel, qty: incQty, date: incDate, memo: incMemo }, B); setIncQty(null); setIncMemo(''); setTextDirty(false); await reload() }
    catch { message.error('저장 실패') } finally { setIncSaving(false) }
  }
  async function removeIncoming(id: string) {
    try { await deleteCoupangIncoming(id, B); setTextDirty(false); await reload() } catch { message.error('삭제 실패') }
  }

  async function copyText() {
    try { await navigator.clipboard.writeText(text); message.success('보고문 복사됨') }
    catch { message.error('클립보드 복사 실패') }
  }

  async function openAliases() {
    try {
      const a = await getCoupangReportAliases(B)
      const merged = { ...a.defaults, ...a.overrides }
      setAliasText(Object.entries(merged).map(([k, v]) => `${k} = ${v}`).join('\n'))
      setAliasOpen(true)
    } catch { message.error('별칭 조회 실패') }
  }
  async function saveAliases() {
    const out: Record<string, string> = {}
    for (const ln of aliasText.split('\n')) {
      const i = ln.indexOf('=')
      if (i < 0) continue
      const k = ln.slice(0, i).trim(), v = ln.slice(i + 1).trim()
      if (k && v) out[k] = v
    }
    try { await saveCoupangReportAliases(out, B); message.success('별칭 저장'); setAliasOpen(false); setTextDirty(false); await reload() }
    catch { message.error('별칭 저장 실패') }
  }

  const o = data?.orders
  const dv = data?.delivery
  const coreLabels = useMemo(() => new Set((o?.core || []).map(c => c.label)), [o])

  const renderGroupRows = (g: CpGroup, showRecv: boolean) => (
    <Fragment key={g.date}>{/* 납기 그룹 = 헤더 행 + 품목 행. map 에서 배열로 돌려주므로 Fragment 에 key 필요 (React key 경고 원인) */}
      <tr className="grp">
        <td>{g.md} 납기 <span className="muted" style={{ fontWeight: 400 }}>· {g.po_count}건</span></td>
        <td className="num">{n(g.order_qty ?? g.qty)}{(g.short_vs_order || 0) > 0 ? <span className="bad" style={{ fontSize: '0.7rem' }}> (미납 {n(g.short_vs_order)})</span> : null}</td>
        {data?.has_conf && <td className="num">{g.conf_avail ? n(g.conf) : <span className="muted">-</span>}</td>}
        {showRecv && <td className="num">{g.recv == null ? <span className="muted">-</span> : n(g.recv)}</td>}
        {!showRecv && data?.bh_connected && <td className="num"><span className={(g.shortage || 0) > 0 ? 'warn' : 'ok'}>{n(g.deliverable)}</span> <span className="muted" style={{ fontSize: '0.7rem' }}>({g.fulfil_rate ?? 0}%)</span></td>}
        {!showRecv && data?.bh_connected && <td className="num">{(g.shortage || 0) > 0 ? <span className="bad">{n(g.shortage)}</span> : <span className="ok">0</span>}</td>}
        <td className="num">{won(g.supply)}</td>
      </tr>
      {g.items.map(it => (
        <tr key={g.date + it.label}>
          <td style={{ paddingLeft: 22 }}>
            <Tooltip title={it.names.join(' / ')}><span style={{ fontWeight: coreLabels.has(it.label) ? 700 : 400 }}>{it.label}</span></Tooltip>
          </td>
          <td className="num">{n(it.order_qty ?? it.qty)}{(it.short_vs_order || 0) > 0 ? <span className="bad" style={{ fontSize: '0.7rem' }}> (미납 {n(it.short_vs_order)})</span> : null}</td>
          {data?.has_conf && <td className="num">{it.conf_avail ? <span className={it.conf !== it.qty ? 'warn' : ''}>{n(it.conf)}</span> : <span className="muted">-</span>}</td>}
          {showRecv && <td className="num">{it.recv == null ? <span className="muted">-</span> : <span className={it.recv >= (it.conf_avail ? it.conf : it.qty) ? 'ok' : 'warn'}>{n(it.recv)}</span>}</td>}
          {!showRecv && data?.bh_connected && <td className="num">{it.deliverable == null ? <Tooltip title="재고 미확인(세트·미매핑)"><span className="muted">-</span></Tooltip> : <span className={(it.shortage || 0) > 0 ? 'warn' : 'ok'}>{n(it.deliverable)}</span>}</td>}
          {!showRecv && data?.bh_connected && <td className="num">{it.shortage == null ? <span className="muted">-</span> : it.shortage > 0 ? <span className="bad">{n(it.shortage)}</span> : <span className="ok">0</span>}</td>}
          <td className="num muted">{won(Math.round(it.amt / (data?.amt_includes_vat === false ? 1 : 1.1)))}</td>
        </tr>
      ))}
    </Fragment>
  )

  return (
    <div className="cpr">
      <style>{CSS}</style>
      <div className="page-header" style={{ display: 'flex', alignItems: 'flex-end', gap: 14, flexWrap: 'wrap' }}>
        <div>
          <h1 className="page-title">{cfg.icon} {cfg.title}</h1>
          <div className="hint" style={{ marginTop: 4 }}>
            {cfg.desc}
          </div>
        </div>
        <div className="toolbar" style={{ marginLeft: 'auto' }}>
          <span className="hint">기준일</span>
          <DatePicker value={dayjs(asOf)} allowClear={false} onChange={d => { if (d) { setTextDirty(false); setAsOf(d.format('YYYY-MM-DD')) } }} />
          <Tooltip title="BH 재고는 10분 캐시 — 강제 새로고침">
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => { setTextDirty(false); load(true) }}>새로고침</Button>
          </Tooltip>
          {cfg.monthly && (
            <Tooltip title="업로드하는 매출·재고 파일이 어느 달 것인지">
              <DatePicker picker="month" value={dayjs(uploadPeriod + '-01')} allowClear={false} onChange={d => d && setUploadPeriod(d.format('YYYY-MM'))} style={{ width: 110 }} />
            </Tooltip>
          )}
          {!cfg.noUpload && (
            <Upload accept=".csv,.xlsx,.xls" multiple showUploadList={false} beforeUpload={queueUpload}>
              <Button icon={<UploadOutlined />} loading={uploading}>{cfg.uploadLabel}</Button>
            </Upload>
          )}
          {cfg.priceTable && (
            <Upload accept=".xlsx,.xls" multiple showUploadList={false} beforeUpload={queuePriceUpload}>
              <Tooltip title="컬리 파트너 > 상품 > 상품공급정보 다운로드 xlsx (일반단가 = 매입단가)">
                <Button icon={<UploadOutlined />} loading={priceBusy}>단가표 업로드</Button>
              </Tooltip>
            </Upload>
          )}
          {cfg.priceTable && <Button icon={<EditOutlined />} onClick={() => setPriceOpen(v => !v)}>매입 단가표{prices ? ` (${Object.keys(prices.items).length})` : ''}</Button>}
          <Button icon={<EditOutlined />} onClick={openAliases}>품목 별칭</Button>
        </div>
      </div>

      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />}
      {!data && loading && <div style={{ padding: 60, textAlign: 'center' }}><Spin /></div>}

      {data && o && dv && (
        <>
          <div className="kpis">
            <div className="kpi">
              <div className="k">발주 현황 (기준일 이후 납기)</div>
              <div className="v">{n(o.qty)}<small>개 · {o.po_count}건</small></div>
              <div className="d">{noAmt ? `${cfg.poWord} 기준 · 금액 정보 없음 (단가표 업로드 시 표시)` : <>공급가 {won(o.supply)}{data.amt_includes_vat !== false && <span className="muted"> (매입가 {won(o.amt)})</span>}</>}</div>
            </div>
            <div className="kpi">
              <div className="k">{data.fulfil.incoming_total ? `${data.ob_connected ? '가용재고' : '재고'} + 입고 예정 기준 납품 가능` : `현재 ${data.ob_connected ? '아워박스 가용재고' : '재고'}로 납품 가능`}</div>
              <div className="v">{data.bh_connected ? <><span className={data.fulfil.rate >= 100 ? 'ok' : data.fulfil.rate >= 50 ? 'warn' : 'bad'}>{n(data.fulfil.deliverable)}</span><small>/ {n(data.fulfil.need)}개 · {data.fulfil.rate}%</small></> : <span className="muted" style={{ fontSize: '0.95rem' }}>BH 미연동</span>}</div>
              <div className="d">{data.bh_connected ? (data.fulfil.shortage ? `부족 ${n(data.fulfil.shortage)}개 · ${data.fulfil.short_by_label.slice(0, 2).map(x => `${x.label} ${n(x.qty)}`).join(' · ')}` : '전량 납품 가능') : '설정에서 토큰 연결'}
                {data.has_conf && o.conf_avail && <span className="muted"> · {data.conf_word || '확정'} {o.conf_diff > 0 ? '+' : ''}{n(o.conf_diff)}</span>}</div>
            </div>
            <div className="kpi">
              <div className="k">{data.month_label} 납품 실적</div>
              <div className="v">{n(dv.ship_total)}<small>개{!noAmt && <> · 공급가 {won(dv.ship_supply)}</>}</small></div>
              <div className="d">{noAmt || data.has_recv === false
                ? <>출고 = 입고 확정 ({cfg.name} 별도 입고확정 없음){dv.ship_today ? ` · 오늘 출고 ${n(dv.ship_today)}개` : ''}</>
                : <>매입 확정 {won(dv.billed_supply)}{dv.unbilled_supply ? <span className="warn"> · 매입 미반영 {won(dv.unbilled_supply)}</span> : null} · {data.recv_word} {n(dv.recv_month)}개{dv.ship_total - dv.recv_month > 0 ? <span className="warn"> · {data.recv_word} 대기 {n(Math.max(0, dv.ship_total - dv.recv_month))}개</span> : null}{dv.ship_today ? ` · 오늘 출고 ${n(dv.ship_today)}개` : ''}</>}</div>
            </div>
            <div className="kpi">
              <div className="k">핵심 품목 잔여 발주</div>
              <div className="v" style={{ fontSize: '1rem', whiteSpace: 'normal' }}>
                {o.core.length ? o.core.map(c => `${c.label} ${n(c.qty)}`).join(' · ') : <span className="muted">-</span>}
              </div>
              <div className="d">{cfg.poWord} 최종 반영 {data.last_ingested_at?.slice(0, 16) || '-'}</div>
            </div>
          </div>

          <div className="grid">
            <div>
              {/* 발주 현황 */}
              <div className="panel">
                <h3>■ 발주 현황 <span className="sub">납기(입고예정일)별 · {data.as_of_md} 이후{data.bh_connected ? ' · 납품가능 = BH 현재고를 납기 빠른 순으로 배분' : ''}</span></h3>
                {o.groups.length === 0 ? <div className="hint">기준일 이후 납기 예정 발주가 없습니다. 발주 캘린더에 {cfg.name} {cfg.poWord}를 올려주세요.</div> : (
                  <table>
                    <thead><tr><th>납기 / 품목</th><th className="num">발주</th>{data.has_conf && <th className="num">{data.conf_word || '확정'}</th>}{data.bh_connected && <><th className="num">재고로 납품가능</th><th className="num">부족</th></>}<th className="num">공급가</th></tr></thead>
                    <tbody>
                      {o.groups.map(g => renderGroupRows(g, false))}
                      <tr className="total"><td>합계 · {o.po_count}건</td><td className="num">{n(o.order_qty ?? o.qty)}{(o.short_vs_order || 0) > 0 ? <span className="bad" style={{ fontSize: '0.7rem' }}> (미납 {n(o.short_vs_order)})</span> : null}</td>{data.has_conf && <td className="num">{o.conf_avail ? n(o.conf) : '-'}</td>}
                        {data.bh_connected && <><td className="num">{n(data.fulfil.deliverable)} <span className="muted" style={{ fontSize: '0.7rem' }}>({data.fulfil.rate}%)</span></td><td className="num"><span className={data.fulfil.shortage ? 'bad' : 'ok'}>{n(data.fulfil.shortage)}</span></td></>}
                        <td className="num">{won(o.supply)}</td></tr>
                    </tbody>
                  </table>
                )}
              </div>

              {/* 납품 실적 */}
              <div className="panel">
                <h3>■ 납품 실적 <span className="sub">{data.month_label} · 기준일 이전 납기 · 발주 vs {cfg.name} {data.recv_word}</span></h3>
                {dv.groups.length === 0 ? <div className="hint">{data.month_label} 중 기준일 이전 납기 건이 없습니다.</div> : (
                  <table>
                    <thead><tr><th>납기 / 품목</th><th className="num">발주</th>{data.has_conf && <th className="num">{data.conf_word || '확정'}</th>}<th className="num">{data.recv_word}</th><th className="num">공급가</th></tr></thead>
                    <tbody>{dv.groups.map(g => renderGroupRows(g, true))}</tbody>
                  </table>
                )}
                {data.bh_connected ? (
                  <div style={{ marginTop: 10 }}>
                    <div className="hint" style={{ fontWeight: 700, color: '#374151', marginBottom: 4 }}>박스히어로 {cfg.name} 출고 ({data.delivery.ship_from ? data.delivery.ship_from.slice(5).replace('-', '/') : data.month_label + ' 1일'}~{data.as_of_md})</div>
                    {dv.ship_by_date.length === 0 ? <div className="hint">기간 내 {cfg.name} 거래처 출고 없음</div> : (
                      <table>
                        <thead><tr><th>출고일</th><th className="num">수량</th><th className="num">공급가</th><th>품목</th></tr></thead>
                        <tbody>
                          {dv.ship_by_date.map(s => (
                            <tr key={s.date}><td>{s.md}</td><td className="num">{n(s.qty)}</td><td className="num">{won(s.supply)}</td>
                              <td className="hint">{s.items.slice(0, 6).map(i => `${i.label} ${n(i.qty)}`).join(' · ')}{s.items.length > 6 ? ` 외 ${s.items.length - 6}` : ''}</td></tr>
                          ))}
                          <tr className="total"><td>누계</td><td className="num">{n(dv.ship_total)}</td><td className="num">{won(dv.ship_supply)}</td><td className="hint">{dv.ship_by_label.slice(0, 6).map(i => `${i.label} ${n(i.qty)}`).join(' · ')}</td></tr>
                        </tbody>
                      </table>
                    )}
                  </div>
                ) : <Alert type="warning" showIcon style={{ marginTop: 8 }} message="박스히어로 미연동 — 설정에서 토큰을 연결하면 출고 실적·자사 재고가 채워집니다" />}
              </div>

              {/* 자사 재고 */}
              <div className="panel">
                <h3>■ 자사 재고 vs 잔여 발주 <span className="sub">{data.ob_connected ? '아워박스 가용재고 기준 (할당·불량 제외) · BH 재고는 참고' : `BH 전체 위치 합산${data.ob_error ? ` · 아워박스 조회 실패: ${data.ob_error}` : ''}`}{data.bh_stock_cached_at ? ` · ${data.bh_stock_cached_at} 조회` : ''}</span></h3>
                <table>
                  <thead><tr><th>품목</th>{data.ob_connected && <th className="num">OB 가용</th>}{data.ob_connected && <th className="num">OB 총재고</th>}<th className="num">BH 재고</th><th className="num">잔여 발주</th><th className="num">차이</th><th>판단</th></tr></thead>
                  <tbody>
                    {data.stock.filter(r => r.pending > 0 || (r.stock || 0) > 0).map(r => (
                      <tr key={r.label}>
                        <td><span style={{ fontWeight: coreLabels.has(r.label) ? 700 : 400 }}>{r.label}</span> {r.skus.length ? <span className="muted" style={{ fontSize: '0.7rem' }}>{r.skus.join(', ')}</span> : null}</td>
                        {data.ob_connected && <td className="num">{r.ob_available == null ? <Tooltip title="아워박스에 동일 품목명이 없어 BH 재고로 대체"><span className="muted">-</span></Tooltip> : <b>{n(r.ob_available)}</b>}</td>}
                        {data.ob_connected && <td className="num muted">{r.ob_total == null ? '-' : <>{n(r.ob_total)}{r.ob_unavailable ? <span style={{ fontSize: '0.7rem' }}> (가용외 {n(r.ob_unavailable)})</span> : null}</>}</td>}
                        <td className={`num${data.ob_connected ? ' muted' : ''}`}>{n(r.bh_stock)}</td>
                        <td className="num">{n(r.pending)}</td>
                        <td className="num">{r.gap == null ? '-' : <span className={r.gap < 0 ? 'bad' : 'ok'}>{r.gap > 0 ? '+' : ''}{n(r.gap)}</span>}</td>
                        <td>{stockTag(r)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* 판매/재고 추이 */}
              {!cfg.noUpload && <div className="panel">
                <h3>■ {cfg.name} {cfg.monthly ? '매출·재고 현황' : '판매·재고 추이'}
                  <span className="sub">
                    {cfg.monthly
                      ? (data.uploads.months?.length ? `업로드 월: ${data.uploads.months.join(', ')}${data.trend.period_label ? ` · 최신 ${data.trend.period_label}` : ''}` : '매출·재고 파일 미업로드')
                      : <>{data.trend.has_rocket ? `판매 리포트 ${data.uploads.rocket_range?.[0]} ~ ${data.uploads.rocket_range?.[1]} (${data.uploads.rocket_days}일)` : '판매 리포트 미업로드'}
                         {data.trend.has_forecast ? ` · 예측 ${data.uploads.forecast?.weeks?.[0]?.from}~ (${data.uploads.forecast?.weeks?.length}주)` : ' · 예측 미업로드'}</>}
                  </span>
                  {(data.trend.has_rocket || data.trend.has_forecast) && (
                    <Popconfirm title="업로드한 판매·재고·예측 자료를 모두 지울까요?" onConfirm={async () => { await clearCoupangReportUploads(B); await reload() }}>
                      <Button size="small" type="text" icon={<DeleteOutlined />} style={{ marginLeft: 'auto' }} />
                    </Popconfirm>
                  )}
                </h3>
                {!data.trend.has_rocket && !data.trend.has_forecast ? (
                  <div className="hint">{cfg.uploadHint}</div>
                ) : cfg.monthly ? (
                  <>
                    <div className="hint" style={{ marginBottom: 6 }}>
                      {data.trend.period_label} 매출 <b>{n(data.trend.total_sales)}</b>개 · 공급가(행사원가) <b>{won(data.trend.total_amount)}</b>
                      {data.trend.total_sales_prev != null ? ` · 전월 ${n(data.trend.total_sales_prev)}개` : ''} · 일평균은 경과 {data.trend.elapsed_days}일 기준
                    </div>
                    <table>
                      <thead><tr>
                        <th>품목</th>
                        <th>{(data.trend.months || []).map(m => m.slice(5) + '월').join(' → ')} 매출</th>
                        <th className="num">이달 매출</th>
                        <th className="num">매장 / 온라인</th>
                        <th className="num">일평균</th>
                        <th className="num">전월 대비</th>
                        <th className="num">재고 센터 / 매장</th>
                        <th className="num">재고일수</th>
                        <th className="num">매출액</th>
                      </tr></thead>
                      <tbody>
                        {data.trend.rows.filter(r => r.sales_7d > 0 || (r.stock_last || 0) > 0).map((r: CpTrendRow) => (
                          <tr key={r.label}>
                            <td><Tooltip title={r.names.join(' / ')}><span style={{ fontWeight: coreLabels.has(r.label) ? 700 : 400 }}>{r.label}</span></Tooltip>{r.promo ? <div className="muted" style={{ fontSize: '0.68rem' }}>{r.promo}</div> : null}</td>
                            <td><Spark series={r.sales_series} /> <span className="muted" style={{ fontSize: '0.7rem' }}>{r.sales_series.map(v => n(v)).join(' → ')}</span></td>
                            <td className="num"><b>{n(r.sales_7d)}</b></td>
                            <td className="num muted">{n(r.sales_store)} / {n(r.sales_online)}</td>
                            <td className="num">{n(r.avg_7d, 1)}</td>
                            <td className="num">{r.trend_pct == null ? '-' : <span className={r.trend_pct < -10 ? 'bad' : r.trend_pct > 10 ? 'ok' : ''}>{r.trend_pct > 0 ? '+' : ''}{r.trend_pct}%</span>}</td>
                            <td className="num">{n(r.stock_center)} / {n(r.stock_store)}</td>
                            <td className="num">{r.days_of_supply == null ? '-' : <span className={r.days_of_supply < 7 ? 'bad' : r.days_of_supply < 14 ? 'warn' : ''}>{n(r.days_of_supply, 1)}일</span>}</td>
                            <td className="num">{won(r.sales_amount)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                ) : (
                  <table>
                    <thead><tr>
                      <th>품목</th>
                      <th>일별 판매</th>
                      <th className="num">7일 평균</th>
                      <th className="num">전주 대비</th>
                      <th className="num">{cfg.name} 재고</th>
                      <th className="num">재고일수</th>
                      <th className="num">다음주 예측</th>
                    </tr></thead>
                    <tbody>
                      {data.trend.rows.filter(r => r.sales_7d > 0 || (r.stock_last || 0) > 0 || (r.forecast_next || 0) > 0).map((r: CpTrendRow) => (
                        <tr key={r.label}>
                          <td><Tooltip title={r.names.join(' / ')}><span style={{ fontWeight: coreLabels.has(r.label) ? 700 : 400 }}>{r.label}</span></Tooltip></td>
                          <td><Spark series={r.sales_series} /></td>
                          <td className="num">{n(r.avg_7d, 1)}</td>
                          <td className="num">{r.trend_pct == null ? '-' : <span className={r.trend_pct < -10 ? 'bad' : r.trend_pct > 10 ? 'ok' : ''}>{r.trend_pct > 0 ? '+' : ''}{r.trend_pct}%</span>}</td>
                          <td className="num">{n(r.stock_last)}{r.stock_delta != null && r.stock_delta !== 0 ? <span className={`muted`} style={{ fontSize: '0.7rem' }}> ({r.stock_delta > 0 ? '+' : ''}{n(r.stock_delta)})</span> : null}</td>
                          <td className="num">{r.days_of_supply == null ? '-' : <span className={r.days_of_supply < 7 ? 'bad' : r.days_of_supply < 14 ? 'warn' : ''}>{n(r.days_of_supply, 1)}일</span>}</td>
                          <td className="num">{r.forecast_next == null ? '-' : n(Math.round(r.forecast_next))}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {data.uploads.files.length > 0 && (
                  <div className="files" style={{ marginTop: 8 }}>
                    업로드: {data.uploads.files.map(f => `${f.filename}${f.period ? ` [${f.period}]` : ''} (${f.uploaded_at.slice(5, 16)})`).join(' · ')}
                  </div>
                )}
              </div>}
            </div>

            {/* 우측: 보고문 + 특이사항 */}
            <div>
              <div className="panel">
                <h3 style={{ flexWrap: 'wrap' }}>보고문
                  <Segmented size="small" value={fmt} options={[{ label: '간결', value: 'compact' }, { label: '상세', value: 'detail' }, { label: '경영 요약 (전 채널)', value: 'exec' }]}
                    onChange={v => { const f = v as Fmt; setFmt(f); try { localStorage.setItem('cpr_fmt', f) } catch { /* ignore */ } if (!textDirty) setText(pick(data, f)) }} />
                  <span className="sub">{textDirty ? '수정 중 (새로고침해도 유지)' : fmt === 'exec' ? (execLoading ? '전 채널 집계 중…' : '전 채널 자동 생성') : '자동 생성'}</span>
                  <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                    {textDirty && <Button size="small" onClick={() => { setTextDirty(false); setText(pick(data, fmt)) }}>자동 생성으로 되돌리기</Button>}
                    {fmt === 'exec' && <Button size="small" icon={<ReloadOutlined />} loading={execLoading} onClick={() => loadExec(true)}>다시 집계</Button>}
                    <Button size="small" type="primary" icon={<CopyOutlined />} onClick={copyText}>복사</Button>
                  </span>
                </h3>
                <textarea className="report" value={text} onChange={e => { setText(e.target.value); setTextDirty(true) }} spellCheck={false} />
                <div className="hint" style={{ marginTop: 6 }}>
                  {noAmt ? `${cfg.poWord}에는 단가가 없어 금액은 표시하지 않습니다. 상품공급정보 xlsx를 '단가표 업로드'하면 수량×일반단가로 매입금액을 계산합니다. ${cfg.name}는 별도 입고확정 절차가 없어 박스히어로 ${cfg.name} 출고분을 그대로 입고 확정으로 봅니다. ` : cfg.priceTable && data.has_amt ? `금액은 상품공급정보 단가표의 일반단가 × 수량${data.prices?.vat_included === false ? ' × 1.1(부가세)' : ''}이며, ${cfg.name} 화면의 '공급가 합계'(부가세 포함)와 같은 기준입니다. ${cfg.name}는 별도 입고확정 절차가 없어 출고분을 그대로 매입 반영으로 봅니다. ` : data.amt_includes_vat === false ? '금액은 납품확인서 원가금액(공급가액, 부가세 제외) 그대로이며 협력사정산 마감내역과 같은 기준입니다. ' : '공급가는 발주금액(매입가)에서 부가세 10%를 제외한 금액입니다. '}{noAmt || cfg.priceTable ? '' : data.has_conf ? "확정수량은 발주서의 '업체납품가능수량', 입고등록은 '입고수량' 컬럼이며, 쿠팡 윙에서 발주서를 다시 내려받아 발주 캘린더에 올릴 때 갱신됩니다." : `${data.recv_word}은 ${cfg.poWord}의 납품수량 기준이며, 최신 ${cfg.poWord}를 발주 캘린더에 올릴 때 갱신됩니다.`}
                  납품은 납기일 기준으로 묶으며, 박스히어로 출고 메모의 'N일'을 납기 힌트로 씁니다.{data.unit_basis ? ' 세트 상품(3입·2입·혼합세트)은 구성 단품으로 풀어 수량을 세므로 발주서의 세트 수와 다르고, 금액은 발주서 합계 그대로입니다.' : ''}
                </div>
              </div>

              {fmt === 'exec' && (
                <div className="panel">
                  <h3>■ 월별 추이 보정 <span className="sub">{cfg.name} · 경영 요약 첫 줄의 월별 수치. 자동값은 발주 캘린더(납기월 기준)·BH 출고이며, 자료에 없는 지난 달은 여기서 직접 입력</span>
                    <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                      {Object.keys(monthlyEdit).length > 0 && <Button size="small" onClick={() => setMonthlyEdit({})}>취소</Button>}
                      <Button size="small" type="primary" icon={<SaveOutlined />} loading={monthlySaving} disabled={!Object.keys(monthlyEdit).length} onClick={saveMonthly}>저장</Button>
                    </span>
                  </h3>
                  <table>
                    <thead><tr><th>월</th><th className="num">발주 수량{data.exec_metric === 'order' && <span className="muted"> ★</span>}</th><th className="num">납품 수량{data.exec_metric === 'recv' && <span className="muted"> ★</span>}</th><th className="num"><Tooltip title={`박스히어로 ${cfg.name} 거래처 출고 · 출고월 기준 (읽기 전용)`}>BH 출고</Tooltip></th><th>출처</th><th /></tr></thead>
                    <tbody>{(data.history || []).map(h => {
                      const e = monthlyEdit[h.ym] || {}
                      const cur = (k: 'order_qty' | 'delivered') => (k in e ? e[k] : h[k]) ?? null
                      const fmtN = (v: string | number | undefined) => `${v ?? ''}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
                      const parseN = (v: string | undefined) => { const s = (v || '').replace(/,/g, ''); return s === '' ? null : Number(s) }
                      const isCur = h.ym === data.month
                      return (
                        <tr key={h.ym} style={isCur ? { background: '#f0fdf4' } : undefined}>
                          <td>{h.label}{isCur && <span className="muted"> (이달)</span>}</td>
                          <td className="num"><InputNumber size="small" value={cur('order_qty')} min={0} style={{ width: 110 }} formatter={fmtN} parser={parseN as never}
                            placeholder={h.auto_order_qty == null ? '-' : n(h.auto_order_qty)} onChange={v => setMonthlyEdit(m => ({ ...m, [h.ym]: { ...(m[h.ym] || {}), order_qty: v == null ? null : Number(v) } }))} /></td>
                          <td className="num"><InputNumber size="small" value={cur('delivered')} min={0} style={{ width: 110 }} formatter={fmtN} parser={parseN as never}
                            placeholder={h.auto_delivered == null ? '-' : n(h.auto_delivered)} onChange={v => setMonthlyEdit(m => ({ ...m, [h.ym]: { ...(m[h.ym] || {}), delivered: v == null ? null : Number(v) } }))} /></td>
                          <td className={`num${h.bh_shipped == null ? ' muted' : ''}`}>{h.bh_shipped == null ? '-' : n(h.bh_shipped)}</td>
                          <td>{h.ym in monthlyEdit ? <Tag color="blue">수정 중</Tag> : h.override ? <Tooltip title={`자동값: 발주 ${n(h.auto_order_qty)} · 납품 ${n(h.auto_delivered)}`}><Tag color="orange">수기</Tag></Tooltip> : (h.auto_order_qty == null && h.auto_delivered == null) ? <Tag>데이터 없음</Tag> : <Tag color="green">자동</Tag>}</td>
                          <td>{h.override && <Button size="small" type="text" onClick={() => resetMonthly(h.ym)}>자동값으로</Button>}</td>
                        </tr>
                      )
                    })}</tbody>
                  </table>
                  <div className="hint" style={{ marginTop: 6 }}>★ 표시 열{data.exec_metric_word ? `(${data.exec_metric_word})` : ''}이 경영 요약 첫 줄(월별 추이)에 쓰입니다. 이달 괄호 안 수치는 이달 {data.exec_metric_word || (data.exec_metric === 'recv' ? '납품' : '발주')}(진행중) 수량입니다.{data.exec_metric === 'recv' ? ' 이달 납품 + 미납품 잔량이 전월 실적 이상이면 "전월 실적 초과예상"이 자동으로 붙습니다.' : ''}
                    컬리는 BH 마켓컬리 출고를 납기(입고일) 월로 묶은 값이 납품 자동값입니다(메모 납기, 없으면 다음 거래명세서 입고일 · 이알하나 제외 · 취소 입고 상계). 쿠팡·올리브영은 발주 캘린더의 입고수량이 자동값이며 BH 출고는 참고용입니다.{data.history_order_basis === 'order_date' ? ` ${cfg.name} 발주 수량은 ${cfg.poWord}의 발주일자 월 기준으로 합칩니다(납기월이 아님).` : ''} 다른 채널의 월별 보정은 각 채널 보고 화면에서 입력하세요.</div>
                </div>
              )}

              <div className="panel">
                <h3>■ 입고 예정 (항공분 등) <span className="sub">도착일이 납기 이전이면 납품 가능 계산에 포함</span></h3>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
                  <Select
                    showSearch mode="tags" maxCount={1} placeholder="품목 (목록에 없으면 직접 입력)" value={incLabel ? [incLabel] : []}
                    onChange={v => setIncLabel(((v as string[]) || []).slice(-1)[0]?.trim() || '')} style={{ minWidth: 200, flex: 1 }}
                    options={[...new Set([...data.stock.map(r => r.label), ...(prices ? Object.keys(prices.items) : [])])].sort((a, b) => a.localeCompare(b, 'ko')).map(l => ({ value: l, label: l }))}
                  />
                  <InputNumber placeholder="수량" value={incQty} onChange={v => setIncQty(v)} min={1} style={{ width: 110 }} formatter={v => `${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')} parser={v => Number((v || '').replace(/,/g, ''))} />
                  <DatePicker value={dayjs(incDate)} allowClear={false} onChange={d => d && setIncDate(d.format('YYYY-MM-DD'))} style={{ width: 130 }} />
                  <Input placeholder="메모 (26차 항공 등)" value={incMemo} onChange={e => setIncMemo(e.target.value)} style={{ width: 150 }} />
                  <Button type="primary" icon={<PlusOutlined />} loading={incSaving} onClick={addIncoming}>추가</Button>
                </div>
                {data.fulfil.incoming.length === 0 ? <div className="hint">등록된 입고 예정이 없습니다. 항공 도착 예정 수량을 넣으면 "재고 + 입고 예정 기준" 납품 가능으로 계산됩니다. 목록에 없는 품목은 이름을 직접 입력하세요. 보고서 품목명(별칭)과 같아야 계산에 반영되며, 발주가 없는 품목은 '미매칭'으로 표시되고 계산에서 제외됩니다.</div> : (
                  <table>
                    <thead><tr><th>도착일</th><th>품목</th><th className="num">수량</th><th>메모</th><th>반영</th><th /></tr></thead>
                    <tbody>{data.fulfil.incoming.map(r => (
                      <tr key={r.id}>
                        <td>{r.date}</td><td>{r.label}</td><td className="num">{n(r.qty)}</td><td className="hint">{r.memo}</td>
                        <td>{r.unknown ? <Tooltip title="품목명이 발주 품목과 매칭되지 않음"><Tag color="red">미매칭</Tag></Tooltip> : r.applied ? <Tag color="green">반영</Tag> : <Tooltip title="모든 납기 이후 도착 — 계산에 미포함"><Tag>납기 이후</Tag></Tooltip>}</td>
                        <td><Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removeIncoming(r.id)} /></td>
                      </tr>
                    ))}</tbody>
                  </table>
                )}
              </div>

              <div className="panel">
                <h3>■ 특이사항 <span className="sub">항공편·연장요청 등 (보고문 끝에 붙음)</span>
                  <Button size="small" icon={<SaveOutlined />} loading={notesSaving} onClick={saveNotes} style={{ marginLeft: 'auto' }}>저장</Button>
                </h3>
                <Input.TextArea
                  value={notes} onChange={e => setNotes(e.target.value)} autoSize={{ minRows: 5, maxRows: 14 }}
                  placeholder={'예)\n26차 항공분 9/10(목) 18:15 출발 → 9/11(금) 03:40 인천 도착 예정\n도착 후 통관·검수·입고 등록을 최단으로 진행해 9/15 납기 전 출고 예정\nBE팀 요청 납기 연장 반영: 9/8 납기분 → 9/15·9/17·9/22'}
                />
              </div>

              {cfg.priceTable && data.prices && data.prices.missing_labels.length > 0 && (
                <Alert type="warning" showIcon style={{ marginBottom: 14 }} message={`단가표에 없는 품목 ${data.prices.missing_labels.length}종 — 금액 미계산`}
                  description={<span className="hint">{data.prices.missing_labels.join(' · ')} · 아래 매입 단가표에서 직접 입력하거나 최신 상품공급정보를 다시 올려주세요.</span>} />
              )}
              {cfg.priceTable && priceOpen && (
                <div className="panel">
                  <h3 style={{ flexWrap: 'wrap' }}>■ 매입 단가표 <span className="sub">{cfg.name} 상품공급정보 '일반단가' · 수량 × 단가 = 매입금액{prices?.source ? ` · ${prices.source} (${prices.updated_at})` : ''}</span>
                    <span style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                      <Tooltip title="일반단가에 부가세가 포함돼 있는지. 별도면 매입금액 = 단가 × 1.1 × 수량">
                        <Segmented size="small" value={prices?.vat_included === false ? 'ex' : 'in'} disabled={priceBusy}
                          options={[{ label: '단가 부가세 포함', value: 'in' }, { label: '부가세 별도', value: 'ex' }]}
                          onChange={v => savePrices(v === 'in')} />
                      </Tooltip>
                      <Popconfirm title="단가표를 모두 지울까요? 금액이 다시 '-'로 표시됩니다." onConfirm={removePrices}>
                        <Button size="small" danger icon={<DeleteOutlined />} loading={priceBusy}>전체 삭제</Button>
                      </Popconfirm>
                      <Button size="small" onClick={() => { setPriceOpen(false); setPriceEdit({}) }}>닫기</Button>
                      <Button size="small" type="primary" icon={<SaveOutlined />} loading={priceBusy} disabled={!Object.keys(priceEdit).length} onClick={() => savePrices()}>저장</Button>
                    </span>
                  </h3>
                  {!prices || !Object.keys(prices.items).length
                    ? <div className="hint">단가표가 없습니다. 컬리 파트너 &gt; 상품 &gt; 상품공급정보 다운로드 xlsx를 '단가표 업로드'로 올리면 라벨별 일반단가가 저장됩니다.</div>
                    : (
                      <table>
                        <thead><tr><th>품목</th><th>{cfg.name} 상품명</th><th className="num">박스입수</th><th className="num">매입단가</th><th className="num">공급단가</th><th /></tr></thead>
                        <tbody>{Object.entries(prices.items).sort((a, b) => a[0].localeCompare(b[0], 'ko')).map(([lb, it]) => {
                          const unit = lb in priceEdit ? priceEdit[lb] : it.unit
                          const vatIn = prices.vat_included !== false
                          const supply = unit == null ? null : Math.round(vatIn ? unit / 1.1 : unit)
                          const removed = lb in priceEdit && priceEdit[lb] === null
                          return (
                            <tr key={lb} style={removed ? { opacity: 0.4, textDecoration: 'line-through' } : undefined}>
                              <td style={{ fontWeight: 600 }}>{lb}</td>
                              <td className="hint"><Tooltip title={[it.code, it.barcode].filter(Boolean).join(' · ')}>{it.name || '-'}</Tooltip></td>
                              <td className="num muted">{it.box || '-'}</td>
                              <td className="num">
                                <InputNumber size="small" value={unit ?? undefined} min={0} style={{ width: 100 }} disabled={removed}
                                  formatter={v => `${v ?? ''}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')} parser={v => Number((v || '').replace(/,/g, ''))}
                                  onChange={v => setPriceEdit(e => ({ ...e, [lb]: v == null ? it.unit : Math.round(Number(v)) }))} />
                              </td>
                              <td className="num muted">{supply == null ? '-' : `${n(supply)}원`}</td>
                              <td>{removed
                                ? <Button size="small" type="text" onClick={() => setPriceEdit(e => { const c = { ...e }; delete c[lb]; return c })}>되돌리기</Button>
                                : <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => setPriceEdit(e => ({ ...e, [lb]: null }))} />}</td>
                            </tr>
                          )
                        })}</tbody>
                      </table>
                    )}
                  <div className="hint" style={{ marginTop: 6 }}>품목은 보고서 짧은 이름(별칭) 기준으로 묶입니다. 상품공급정보의 상품명이 별칭표에 없으면 상품명 그대로 표시되니 '품목 별칭'에서 연결해 주세요. 증정품 등 단가 0 행은 무시합니다.</div>
                </div>
              )}

              {aliasOpen && (
                <div className="panel">
                  <h3>품목 별칭 <span className="sub">쿠팡 상품명 = 보고서 짧은 이름 (한 줄에 하나)</span>
                    <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                      <Button size="small" onClick={() => setAliasOpen(false)}>닫기</Button>
                      <Button size="small" type="primary" onClick={saveAliases}>저장</Button>
                    </span>
                  </h3>
                  <Input.TextArea value={aliasText} onChange={e => setAliasText(e.target.value)} autoSize={{ minRows: 10, maxRows: 24 }} style={{ fontSize: '0.78rem' }} />
                </div>
              )}

              <Collapse size="small" items={[{
                key: 'all', label: `전체 납기 목록 (${data.all_groups.length})`,
                children: (
                  <table>
                    <thead><tr><th>납기</th><th className="num">발주</th><th className="num">공급가</th><th>구분</th></tr></thead>
                    <tbody>{data.all_groups.map(g => (
                      <tr key={g.date}><td>{g.date}</td><td className="num">{n(g.qty)}</td><td className="num">{won(g.supply)}</td><td>{g.status === 'pending' ? <Tag color="blue">예정</Tag> : <Tag>지난 납기</Tag>}</td></tr>
                    ))}</tbody>
                  </table>
                ),
              }]} />
            </div>
          </div>
        </>
      )}
    </div>
  )
}
