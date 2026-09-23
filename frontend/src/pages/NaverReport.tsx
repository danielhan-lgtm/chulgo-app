import { useState, useEffect, useRef } from 'react'
import { Button, DatePicker, Upload, Spin, Alert, Tag, Tooltip, message, Popconfirm, Input, Collapse, Segmented } from 'antd'
import { ReloadOutlined, CopyOutlined, UploadOutlined, DeleteOutlined, SaveOutlined, EditOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import {
  getNaverReport, uploadNaverReportFiles, clearNaverReportUploads, saveNaverReportSettings,
  getNaverReportAliases, saveNaverReportAliases, getChannelExecReport,
} from '../services/api'
import type { NaverReportSummary, NaverReportRow, ChannelExecReport } from '../services/api'

/** 네이버 스마트스토어(도착보장) 출고 보고 — 박스히어로 거래 내역 xlsx 를 올려
 *  '스마트스토어 … DJ&A' 거래처 출고를 일별 판매로 보고 의장 보고문을 만든다.
 *  - 도착보장 출고는 하루 한 회차씩 나가고 메모의 '9월 21일'이 주문일 → 주문일 기준 집계
 *  - 재고는 아워박스 가용재고(연동 시) → 박스히어로 재고 순으로 붙여 소진 예상일을 계산
 */

const CSS = `
.nvr { font-family: Inter, 'Malgun Gothic', sans-serif; color:#111827; }
.nvr .grid { display:grid; grid-template-columns: minmax(0, 1.25fr) minmax(360px, 0.95fr); gap:16px; align-items:start; }
@media (max-width: 1200px) { .nvr .grid { grid-template-columns: 1fr; } }
.nvr .panel { background:#fff; border:1px solid #e5e7eb; border-radius:12px; padding:14px 16px; margin-bottom:14px; }
.nvr .panel h3 { margin:0 0 10px; font-size:0.95rem; font-weight:800; display:flex; align-items:center; gap:8px; }
.nvr .panel h3 .sub { font-weight:500; color:#6b7280; font-size:0.75rem; }
.nvr .kpis { display:grid; grid-template-columns: repeat(4, 1fr); gap:10px; margin-bottom:14px; }
.nvr .kpi { background:#fff; border:1px solid #e5e7eb; border-radius:12px; padding:12px 14px; }
.nvr .kpi .k { font-size:0.72rem; color:#6b7280; font-weight:600; }
.nvr .kpi .v { font-size:1.35rem; font-weight:800; margin-top:4px; letter-spacing:-0.3px; white-space:nowrap; }
.nvr .kpi .v small { font-size:0.72rem; font-weight:500; color:#6b7280; margin-left:4px; }
.nvr .kpi .d { font-size:0.72rem; color:#6b7280; margin-top:4px; }
.nvr table { width:100%; border-collapse:collapse; font-size:0.8rem; }
.nvr th { text-align:left; color:#6b7280; font-weight:600; font-size:0.72rem; padding:6px 8px; border-bottom:1px solid #e5e7eb; background:#f9fafb; }
.nvr td { padding:6px 8px; border-bottom:1px solid #f1f5f9; vertical-align:top; }
.nvr td.num, .nvr th.num { text-align:right; font-variant-numeric: tabular-nums; white-space:nowrap; }
.nvr .muted { color:#9ca3af; }
.nvr .ok { color:#059669; } .nvr .warn { color:#d97706; } .nvr .bad { color:#dc2626; }
.nvr .spark { display:inline-flex; align-items:flex-end; gap:1px; height:18px; }
.nvr .spark i { display:block; width:5px; background:#93c5fd; border-radius:1px; min-height:1px; }
.nvr .spark i.last { background:#2563eb; }
.nvr textarea.report { width:100%; min-height:560px; font-family: 'Malgun Gothic', Inter, sans-serif; font-size:0.82rem; line-height:1.55;
  border:1px solid #e5e7eb; border-radius:10px; padding:12px 14px; resize:vertical; background:#fbfbfc; }
.nvr textarea.report:focus { outline:none; border-color:#10b981; background:#fff; }
.nvr .toolbar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.nvr .hint { font-size:0.74rem; color:#6b7280; line-height:1.5; }
.nvr .files { font-size:0.74rem; color:#4b5563; }
.nvr .daily { display:flex; align-items:flex-end; gap:2px; height:64px; margin-top:6px; }
.nvr .daily i { display:block; flex:1; background:#bfdbfe; border-radius:2px 2px 0 0; min-height:1px; }
.nvr .daily i.wk { background:#93c5fd; }
`

const n = (v: number | null | undefined, d = 0) =>
  v === null || v === undefined ? '-' : v.toLocaleString(undefined, { maximumFractionDigits: d })
const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? <span className="muted">-</span>
    : <span className={v > 0 ? 'ok' : v < 0 ? 'bad' : 'muted'}>{v > 0 ? '+' : ''}{v}%</span>

function Spark({ series }: { series: number[] }) {
  if (!series.length) return <span className="muted">-</span>
  const max = Math.max(...series, 1)
  return (
    <span className="spark" title={series.join(', ')}>
      {series.map((v, i, arr) => (
        <i key={i} className={i === arr.length - 1 ? 'last' : ''} style={{ height: `${Math.max(1, Math.round(v / max * 18))}px` }} />
      ))}
    </span>
  )
}

function dosTag(r: NaverReportRow) {
  if (r.stock === null || r.stock === undefined) return <span className="muted">-</span>
  if (r.stock < 0) return <Tag color="red">재고 {n(r.stock)} 확인</Tag>
  const d = r.days_of_supply
  if (d === null || d === undefined) return <span className="muted">{n(r.stock)}개</span>
  const cls = d < 7 ? 'bad' : d < 14 ? 'warn' : 'ok'
  return <span className={cls}>{n(r.stock)}개 · {d > 90 ? '90일 이상' : `${d}일분`}</span>
}

export default function NaverReport() {
  const [asOf, setAsOf] = useState(dayjs().format('YYYY-MM-DD'))
  const [data, setData] = useState<NaverReportSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [textDirty, setTextDirty] = useState(false)
  // 보고문 형식 — 간결(네이버만) / 경영 요약(쿠팡·올리브영·컬리·네이버 4채널 묶음, 슬랙 붙이기용)
  type Fmt = 'compact' | 'exec'
  const [fmt, setFmt] = useState<Fmt>(() => { try { return localStorage.getItem('nvr_fmt') === 'exec' ? 'exec' : 'compact' } catch { return 'compact' } })
  const [execLoading, setExecLoading] = useState(false)
  const execRef = useRef<ChannelExecReport | null>(null)   // load() 클로저가 최신 전 채널 결과를 보도록 ref 로 보관
  // 형식별 본문 — 전 채널 묶음은 기준일이 같을 때만 쓰고, 아니면 네이버 블록(text_exec)으로 대체
  const pick = (d: NaverReportSummary, f: Fmt) => {
    if (f !== 'exec') return d.text
    const r = execRef.current
    return (r && r.as_of === d.as_of ? r.text : undefined) ?? d.text_exec ?? d.text
  }
  const [uploading, setUploading] = useState(false)
  const [kw, setKw] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [aliasOpen, setAliasOpen] = useState(false)
  const [aliasText, setAliasText] = useState('')
  const pendingFiles = useRef<File[]>([])
  const uploadTimer = useRef<number | null>(null)

  async function load(refresh = false) {
    setLoading(true); setError(null)
    try {
      const d = await getNaverReport(asOf, refresh)
      setData(d)
      setKw(d.partner_kw.join(' + '))
      setNotes(d.notes || '')
      if (!textDirty) setText(pick(d, fmt))
    } catch (e: unknown) {
      setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e))
    } finally { setLoading(false) }
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, [asOf])

  // 경영 요약(전 채널 묶음) — 이 형식을 고를 때만 쿠팡·올리브영·컬리·네이버 엔진을 한 번에 돌린다
  async function loadExec(force = false) {
    if (execLoading) return
    const cur = execRef.current
    if (!force && cur && cur.as_of === asOf) return
    setExecLoading(true)
    try {
      const r = await getChannelExecReport(asOf, asOf.slice(0, 7))
      execRef.current = r
      if (!textDirty) setText(r.text)
      r.channels.filter(c => c.error).forEach(c => message.warning(`${c.name} 집계 실패: ${c.error}`))
    } catch (e: unknown) { message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || (e instanceof Error ? e.message : '경영 요약 조회 실패')) }
    finally { setExecLoading(false) }
  }
  useEffect(() => { if (fmt === 'exec') loadExec() }, [fmt, asOf])  // eslint-disable-line react-hooks/exhaustive-deps

  // 자료가 바뀐 뒤 다시 읽기 — 전 채널 요약 형식이면 묶음도 다시 집계
  async function reload(refresh = false) {
    await load(refresh)
    if (fmt === 'exec') await loadExec(true)
  }

  // 파일 여러 개 드롭 시 한 번에 업로드 (antd Upload 는 파일별로 beforeUpload 호출)
  function queueUpload(file: File) {
    pendingFiles.current.push(file)
    if (uploadTimer.current) window.clearTimeout(uploadTimer.current)
    uploadTimer.current = window.setTimeout(async () => {
      const files = pendingFiles.current; pendingFiles.current = []
      setUploading(true)
      try {
        const res = await uploadNaverReportFiles(files)
        if (res.added.length) message.success(`${res.added.map(a => `${a.filename} (${a.from.slice(5)}~${a.to.slice(5)} · ${n(a.added)}행 추가)`).join(', ')}`)
        res.errors.forEach(e => message.warning(e, 6))
        setTextDirty(false)
        await reload()
      } catch { message.error('업로드 실패') }
      finally { setUploading(false) }
    }, 250)
    return false
  }

  async function saveSettings(partial: { partner_kw?: string[]; notes?: string }) {
    setSaving(true)
    try { await saveNaverReportSettings(partial); message.success('저장됨'); setTextDirty(false); await reload() }
    catch (e: unknown) { message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '저장 실패') }
    finally { setSaving(false) }
  }

  async function copyText() {
    try { await navigator.clipboard.writeText(text); message.success('보고문 복사됨') }
    catch { message.error('클립보드 복사 실패') }
  }

  async function openAliases() {
    try {
      const a = await getNaverReportAliases()
      const lines = Object.keys(a.defaults).sort().map(k => `${k} = ${a.overrides[k] ?? a.defaults[k]}`)
      setAliasText(lines.join('\n')); setAliasOpen(true)
    } catch { message.error('라벨 목록을 불러오지 못했습니다') }
  }
  async function saveAliases() {
    const a = await getNaverReportAliases()
    const out: Record<string, string> = {}
    aliasText.split('\n').forEach(ln => {
      const i = ln.lastIndexOf(' = '); if (i < 0) return
      const k = ln.slice(0, i).trim(), v = ln.slice(i + 3).trim()
      if (k && v && v !== a.defaults[k]) out[k] = v
    })
    await saveNaverReportAliases(out); setAliasOpen(false); setTextDirty(false); message.success('라벨 저장됨'); await reload()
  }

  const rows = data?.rows || []
  const weeks = data?.weeks || []
  const maxDaily = Math.max(1, ...(data?.daily || []).map(d => d.qty))

  return (
    <div className="nvr">
      <style>{CSS}</style>
      <h2 style={{ margin: '0 0 4px', fontSize: '1.25rem', fontWeight: 800 }}>🟢 네이버 출고 보고</h2>
      <div className="hint" style={{ marginBottom: 12 }}>
        박스히어로 거래 내역(Transactions xlsx)에서 <b>{data?.partner_kw.join(' + ') || '스마트스토어 + DJ&A'}</b> 거래처의 출고만 골라 일별 판매로 봅니다.
        도착보장 출고는 하루 한 회차씩 나가고 메모의 '9월 21일'이 주문일이라 주문일 기준으로 집계합니다. 재고는 {data?.stock_basis === 'ob' ? '아워박스 가용재고' : '박스히어로 재고'} 기준입니다.
      </div>

      <div className="toolbar" style={{ marginBottom: 14 }}>
        <span className="hint">기준일</span>
        <DatePicker value={dayjs(asOf)} onChange={d => { if (d) { setTextDirty(false); setAsOf(d.format('YYYY-MM-DD')) } }} allowClear={false} />
        <Button icon={<ReloadOutlined />} onClick={() => { setTextDirty(false); reload(true) }} loading={loading}>새로고침</Button>
        <Upload accept=".xlsx,.xls" multiple showUploadList={false} beforeUpload={queueUpload}>
          <Tooltip title="박스히어로 > 거래 내역 > 엑셀 내보내기. 누적 파일을 다시 올려도 중복은 자동 제거됩니다.">
            <Button icon={<UploadOutlined />} loading={uploading}>거래 내역 업로드</Button>
          </Tooltip>
        </Upload>
        <Button icon={<EditOutlined />} onClick={openAliases}>품목 별칭</Button>
        {data && data.uploads.length > 0 && (
          <Popconfirm title="업로드한 거래 내역을 모두 지울까요?" onConfirm={async () => { await clearNaverReportUploads(); setTextDirty(false); await reload() }}>
            <Button danger icon={<DeleteOutlined />}>자료 초기화</Button>
          </Popconfirm>
        )}
      </div>

      {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 12 }} />}
      {loading && !data && <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>}

      {data && (
        <>
          {data.stale_days !== null && data.stale_days >= 2 && (
            <Alert type="warning" showIcon style={{ marginBottom: 12 }}
              message={`자료가 ${data.last_day_md} 주문분까지입니다 (기준일보다 ${data.stale_days}일 전). 박스히어로 거래 내역을 다시 내보내 올리면 최신화됩니다.`} />
          )}
          {!data.data_to && <Alert type="info" showIcon style={{ marginBottom: 12 }} message="아직 업로드한 거래 내역이 없습니다. 상단 '거래 내역 업로드'로 박스히어로 Transactions xlsx 를 올리세요." />}

          <div className="kpis">
            <div className="kpi"><div className="k">{data.month_label} 출고</div>
              <div className="v">{n(data.month_qty)}<small>개</small></div>
              <div className="d">{data.month_batches}회차 · {data.month_elapsed}일 · 일평균 {n(Math.round(data.month_avg))}개 {pct(data.month_pct)}</div></div>
            <div className="kpi"><div className="k">{data.prev_month_label} 출고</div>
              <div className="v">{n(data.prev_qty)}<small>개</small></div>
              <div className="d">일평균 {n(Math.round(data.prev_avg))}개</div></div>
            <div className="kpi"><div className="k">최근 7일 일평균</div>
              <div className="v">{n(Math.round(data.w1_avg))}<small>개/일</small></div>
              <div className="d">{data.w1_from.slice(5)}~{data.w1_to.slice(5)} · 전주 대비 {pct(data.w1_pct)}</div></div>
            <div className="kpi"><div className="k">마지막 주문일</div>
              <div className="v">{data.last_day_md || '-'}</div>
              <div className="d">{n(data.last_day_qty)}개{data.month_returns ? ` · 취소 보정 ${n(data.month_returns)}개` : ''}</div></div>
          </div>

          <div className="grid">
            <div>
              <div className="panel">
                <h3>일별 출고 <span className="sub">최근 60일 · 주문일 기준</span></h3>
                <div className="daily">
                  {data.daily.map(d => (
                    <Tooltip key={d.date} title={`${d.date} · ${n(d.qty)}개`}>
                      <i className={dayjs(d.date).day() === 0 || dayjs(d.date).day() === 6 ? 'wk' : ''} style={{ height: `${Math.max(1, Math.round(d.qty / maxDaily * 64))}px` }} />
                    </Tooltip>
                  ))}
                </div>
                <table style={{ marginTop: 10 }}>
                  <thead><tr><th>주 (월~일)</th><th className="num">출고</th><th></th></tr></thead>
                  <tbody>
                    {weeks.map(w => {
                      const max = Math.max(1, ...weeks.map(x => x.qty))
                      return (
                        <tr key={w.from}>
                          <td>{w.md}{w.partial && <span className="muted"> (진행 중)</span>}</td>
                          <td className="num">{n(w.qty)}</td>
                          <td><div style={{ height: 8, background: '#eff6ff', borderRadius: 4 }}><div style={{ width: `${w.qty / max * 100}%`, height: 8, background: '#3b82f6', borderRadius: 4 }} /></div></td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              <div className="panel">
                <h3>품목별 <span className="sub">{data.month_label} 출고 · 전월 일평균 대비 · 최근 14일 속도 · 재고 소진 예상일</span></h3>
                <table>
                  <thead><tr><th>품목</th><th className="num">{data.month_label}</th><th className="num">{data.prev_month_label}</th><th className="num">증감</th><th className="num">최근 7일</th><th className="num">14일 평균/일</th><th>8주 추이</th><th>재고 · 소진일</th></tr></thead>
                  <tbody>
                    {rows.map(r => (
                      <tr key={r.label}>
                        <td><Tooltip title={r.names.join(' / ')}>{r.label}</Tooltip></td>
                        <td className="num">{n(r.month_qty)}</td>
                        <td className="num">{n(r.prev_qty)}</td>
                        <td className="num">{pct(r.pct)}</td>
                        <td className="num">{n(r.w1)}</td>
                        <td className="num">{n(r.avg14, 1)}</td>
                        <td><Spark series={r.spark} /></td>
                        <td>{dosTag(r)}</td>
                      </tr>
                    ))}
                    {!rows.length && <tr><td colSpan={8} className="muted">자료 없음</td></tr>}
                  </tbody>
                </table>
                {data.uploads.length > 0 && (
                  <div className="files" style={{ marginTop: 8 }}>
                    업로드: {data.uploads.slice(-5).map(f => `${f.filename} (${f.from.slice(5)}~${f.to.slice(5)} · ${f.uploaded_at.slice(5, 16)})`).join(' · ')}
                    {' '}· 자료 범위 {data.data_from} ~ {data.data_to} · {n(data.tx_rows)}행
                  </div>
                )}
              </div>
            </div>

            <div>
              <div className="panel">
                <h3 style={{ flexWrap: 'wrap' }}>보고문
                  <Segmented size="small" value={fmt} options={[{ label: '간결', value: 'compact' }, { label: '경영 요약 (전 채널)', value: 'exec' }]}
                    onChange={v => { const f = v as Fmt; setFmt(f); try { localStorage.setItem('nvr_fmt', f) } catch { /* ignore */ } if (!textDirty) setText(pick(data, f)) }} />
                  <span className="sub">{textDirty ? '수정 중' : fmt === 'exec' ? (execLoading ? '전 채널 집계 중…' : '전 채널 자동 생성') : '자동 생성'}</span>
                  <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                    {textDirty && <Button size="small" onClick={() => { setTextDirty(false); setText(pick(data, fmt)) }}>자동 생성으로 되돌리기</Button>}
                    {fmt === 'exec' && <Button size="small" icon={<ReloadOutlined />} loading={execLoading} onClick={() => loadExec(true)}>다시 집계</Button>}
                    <Button size="small" type="primary" icon={<CopyOutlined />} onClick={copyText}>복사</Button>
                  </span>
                </h3>
                <textarea className="report" value={text} onChange={e => { setText(e.target.value); setTextDirty(true) }} spellCheck={false} />
                <div className="hint" style={{ marginTop: 6 }}>
                  {fmt === 'exec'
                    ? <>쿠팡·올리브영·컬리·네이버 블록을 번호로 이어 붙인 슬랙용 경영 요약입니다. 네이버 월별 수치는 박스히어로 출고(주문일 기준)이며, 다른 채널의 월별 보정은 각 채널 보고 화면의 '월별 추이 보정'에서 입력하세요. </>
                    : null}
                  출고 수량은 박스히어로에서 도착보장 센터가 아닌 고객 주문 건으로 나간 것이라 그대로 판매량으로 봅니다. 취소 보정(입고)은 해당 주문일에서 뺍니다.
                  재고 소진 예상일은 재고 ÷ 최근 14일 일평균입니다.
                </div>
              </div>

              {fmt === 'exec' && (data.history || []).length > 0 && (
                <div className="panel">
                  <h3>■ 월별 추이 <span className="sub">경영 요약 첫 줄의 월별 수치 · 박스히어로 {data.partner_kw.join(' + ')} 출고 (주문일 기준)</span></h3>
                  <table>
                    <thead><tr><th>월</th><th className="num">출고 수량</th></tr></thead>
                    <tbody>{(data.history || []).map(h => {
                      const isCur = h.ym === data.month
                      return (
                        <tr key={h.ym} style={isCur ? { background: '#f0fdf4' } : undefined}>
                          <td>{h.label}{isCur && <span className="muted"> (이달 · 진행중)</span>}</td>
                          <td className={`num${h.qty == null ? ' muted' : ''}`}>{h.qty == null ? '-' : n(h.qty)}</td>
                        </tr>
                      )
                    })}</tbody>
                  </table>
                  <div className="hint" style={{ marginTop: 6 }}>업로드한 거래 내역 범위 안의 달만 채워집니다. 지난 달 수치가 비어 있으면 그 기간의 박스히어로 거래 내역을 함께 올려주세요.</div>
                </div>
              )}

              <div className="panel">
                <h3>■ 특이사항 <span className="sub">보고문 끝에 붙음</span>
                  <Button size="small" icon={<SaveOutlined />} loading={saving} style={{ marginLeft: 'auto' }} onClick={() => saveSettings({ notes })}>저장</Button>
                </h3>
                <Input.TextArea rows={3} value={notes} onChange={e => setNotes(e.target.value)} placeholder="예: 9/25 라이브 방송 예정, 10월 도착보장 프로모션 …" />
              </div>

              <Collapse size="small" items={[{
                key: 'kw', label: `거래처 필터: ${data.partner_kw.join(' + ')}`,
                children: (
                  <div>
                    <div className="hint" style={{ marginBottom: 6 }}>거래처명에 모두 들어 있어야 하는 단어를 ' + '로 구분해 적습니다. 현재 자료의 거래처: {data.partners.join(', ') || '-'}</div>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <Input value={kw} onChange={e => setKw(e.target.value)} />
                      <Button loading={saving} onClick={() => saveSettings({ partner_kw: kw.split('+').map(s => s.trim()).filter(Boolean) })}>저장</Button>
                    </div>
                    <div className="hint" style={{ marginTop: 6 }}>필터를 바꾸면 다음 업로드부터 적용됩니다. 이미 올린 자료에 다른 거래처를 넣으려면 초기화 후 다시 올리세요.</div>
                  </div>
                ),
              }]} />
            </div>
          </div>
        </>
      )}

      {aliasOpen && (
        <div className="panel" style={{ position: 'fixed', right: 24, bottom: 24, width: 560, zIndex: 50, boxShadow: '0 12px 40px rgba(0,0,0,.18)' }}>
          <h3>품목 별칭 <span className="sub">제품명 = 보고문 표기. 같은 이름으로 적으면 한 품목으로 합칩니다</span>
            <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              <Button size="small" onClick={() => setAliasOpen(false)}>닫기</Button>
              <Button size="small" type="primary" onClick={saveAliases}>저장</Button>
            </span>
          </h3>
          <Input.TextArea rows={14} value={aliasText} onChange={e => setAliasText(e.target.value)} style={{ fontFamily: 'Consolas, monospace', fontSize: 12 }} />
        </div>
      )}
    </div>
  )
}
