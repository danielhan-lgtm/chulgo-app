import { useState, useEffect } from 'react'
import { Button, DatePicker, Spin, Alert, Tag, Input, message } from 'antd'
import { ReloadOutlined, CopyOutlined, SaveOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { getErhanaReport, saveErhanaNotes } from '../services/api'
import type { ErhanaReport as Rep } from '../services/api'

/** 이알하나 통합 보고 — 채널을 나누지 않고 한 화면에.
 *  출고: 아워박스 MATE 출고 실적(채널별) · 재고: 아워박스 가용재고 · 발주: 발주 캘린더의 컬리 거래명세서(이알하나 행).
 *  보고문은 DJ&A 경영진보고와 같은 슬랙 양식이며, 제목·한줄정리·의견은 서버에 저장된다. */

const CSS = `
.erh { font-family: Inter, 'Malgun Gothic', sans-serif; color:#111827; }
.erh .grid { display:grid; grid-template-columns: minmax(0, 1.15fr) minmax(380px, 1fr); gap:16px; align-items:start; }
@media (max-width: 1200px) { .erh .grid { grid-template-columns: minmax(0, 1fr); } }
.erh .grid > * { min-width:0; }
.erh .panel { background:#fff; border:1px solid #e5e7eb; border-radius:12px; padding:14px 16px; margin-bottom:14px; }
.erh .panel h3 { margin:0 0 10px; font-size:0.95rem; font-weight:800; display:flex; align-items:center; gap:8px; }
.erh .panel h3 .sub { font-weight:500; color:#6b7280; font-size:0.75rem; }
.erh .kpis { display:grid; grid-template-columns: repeat(4, 1fr); gap:10px; margin-bottom:14px; }
.erh .kpi { background:#fff; border:1px solid #e5e7eb; border-radius:12px; padding:12px 14px; }
.erh .kpi .k { font-size:0.72rem; color:#6b7280; font-weight:600; }
.erh .kpi .v { font-size:1.35rem; font-weight:800; margin-top:4px; letter-spacing:-0.3px; white-space:nowrap; }
.erh .kpi .v small { font-size:0.72rem; font-weight:500; color:#6b7280; margin-left:4px; }
.erh .kpi .d { font-size:0.72rem; color:#6b7280; margin-top:4px; }
.erh table { width:100%; border-collapse:collapse; font-size:0.8rem; }
.erh th { text-align:left; color:#6b7280; font-weight:600; font-size:0.72rem; padding:6px 8px; border-bottom:1px solid #e5e7eb; background:#f9fafb; }
.erh td { padding:6px 8px; border-bottom:1px solid #f1f5f9; vertical-align:top; }
.erh td:first-child, .erh th:first-child { white-space:nowrap; }
.erh td.num, .erh th.num { text-align:right; font-variant-numeric: tabular-nums; white-space:nowrap; }
.erh tr.total td { font-weight:700; background:#f9fafb; }
.erh .muted { color:#9ca3af; }
.erh .ok { color:#059669; } .erh .warn { color:#d97706; } .erh .bad { color:#dc2626; }
.erh textarea.report { width:100%; min-height:520px; font-family: 'Malgun Gothic', Inter, sans-serif; font-size:0.82rem; line-height:1.55;
  border:1px solid #e5e7eb; border-radius:10px; padding:12px 14px; resize:vertical; background:#fbfbfc; }
.erh textarea.report:focus { outline:none; border-color:#7c3aed; background:#fff; }
.erh .toolbar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.erh .hint { font-size:0.74rem; color:#6b7280; line-height:1.5; }
`

const n = (v: number | null | undefined) => (v === null || v === undefined ? '-' : v.toLocaleString())
const won = (v: number | null | undefined) => (v === null || v === undefined ? '-' : v >= 10000 ? `${Math.round(v / 10000).toLocaleString()}만원` : `${v.toLocaleString()}원`)
function Pct({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return <span className="muted">-</span>
  return <span className={v > 0 ? 'ok' : v < 0 ? 'bad' : 'muted'}>{v > 0 ? '+' : ''}{v}%</span>
}

export default function ErhanaReport() {
  const [asOf, setAsOf] = useState(dayjs().format('YYYY-MM-DD'))
  const [data, setData] = useState<Rep | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [textDirty, setTextDirty] = useState(false)
  const [title, setTitle] = useState('')
  const [headline, setHeadline] = useState('')
  const [opinion, setOpinion] = useState('')
  const [notesDirty, setNotesDirty] = useState(false)
  const [saving, setSaving] = useState(false)

  async function load(refresh = false) {
    setLoading(true); setError(null)
    try {
      const d = await getErhanaReport(asOf, refresh)
      setData(d)
      if (!textDirty) setText(d.text)
      if (!notesDirty) { setTitle(d.notes.title || ''); setHeadline(d.notes.headline || ''); setOpinion(d.notes.opinion || '') }
      if (d.ob_error) message.warning(`아워박스 출고 조회 실패: ${d.ob_error}`)
    } catch (e: unknown) {
      setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || (e instanceof Error ? e.message : '조회 실패'))
    } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [asOf])  // eslint-disable-line react-hooks/exhaustive-deps

  async function saveNotes() {
    setSaving(true)
    try {
      await saveErhanaNotes({ title, headline, opinion })
      setNotesDirty(false); setTextDirty(false)
      message.success('보고 의견 저장')
      await load()
    } catch { message.error('저장 실패') } finally { setSaving(false) }
  }
  function copyText() {
    navigator.clipboard.writeText(text).then(() => message.success('보고문 복사됨')).catch(() => message.error('복사 실패'))
  }

  const t = data?.total
  const pendingQty = (data?.kurly.pending || []).reduce((a, g) => a + g.qty, 0)

  return (
    <div className="erh">
      <style>{CSS}</style>
      <h2 style={{ margin: '0 0 4px', fontSize: '1.25rem', fontWeight: 800 }}>💊 이알하나 통합 보고</h2>
      <div className="hint" style={{ marginBottom: 12 }}>
        아워박스 MATE 출고 실적을 채널별로 묶어 한 화면에 봅니다(내부 세트구성 이동 제외). 재고는 아워박스 가용재고, 발주는 발주 캘린더의 컬리 거래명세서 중 이알하나 행입니다.
        비교 기간은 이달 1일~기준일 대 지난달 같은 날짜까지입니다.
      </div>

      <div className="toolbar" style={{ marginBottom: 14 }}>
        <span className="hint">기준일</span>
        <DatePicker value={dayjs(asOf)} onChange={d => { if (d) { setTextDirty(false); setAsOf(d.format('YYYY-MM-DD')) } }} allowClear={false} />
        <Button icon={<ReloadOutlined />} onClick={() => { setTextDirty(false); load(true) }} loading={loading}>아워박스 다시 조회</Button>
        {data?.ob.fetched_at && <span className="hint">아워박스 출고 {data.ob.from.slice(5)}~{data.ob.to.slice(5)} · 조회 {data.ob.fetched_at.slice(5)}{data.ob.cached ? ' (캐시)' : ''}</span>}
      </div>

      {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 12 }} />}
      {loading && !data && <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /><div className="hint" style={{ marginTop: 10 }}>아워박스 출고·재고 조회 중 (10~20초)</div></div>}

      {data && t && (
        <>
          {data.ob_error && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={`아워박스 출고 조회 실패: ${data.ob_error}`} />}
          {data.stock_error && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={`아워박스 재고 조회 실패: ${data.stock_error}`} />}
          {data.pay_error && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={`결제 금액(아워박스 주문) 조회 실패: ${data.pay_error}`} />}

          <div className="kpis">
            <div className="kpi"><div className="k">{data.period} 출고</div>
              <div className="v">{n(t.mtd)}<small>개</small></div>
              <div className="d">{data.prev_month_label} 같은 기간 {n(t.prev)}개 · <Pct v={t.pct} /> · 일평균 {n(Math.round(t.daily))}개</div></div>
            <div className="kpi"><div className="k">최근 7일 출고</div>
              <div className="v">{n(t.w1)}<small>개</small></div>
              <div className="d">{data.windows.w1[0].slice(5)}~{data.windows.w1[1].slice(5)} · 직전 7일 {n(t.w0)}개 · <Pct v={t.w1_pct} /></div></div>
            <div className="kpi"><div className="k">{data.period} 결제 (스마트스토어·자사몰 등)</div>
              <div className="v">{won(t.pay_mtd)}</div>
              <div className="d">{data.prev_month_label} 같은 기간 {won(t.pay_prev)} · <Pct v={t.pay_pct} /> · 최근 7일 {won(t.pay_w1)}</div></div>
            <div className="kpi"><div className="k">컬리 발주 대기</div>
              <div className="v">{n(pendingQty)}<small>개</small></div>
              <div className="d">{data.kurly.pending.length ? data.kurly.pending.map(g => `${g.md} ${n(g.qty)}`).join(' · ') : '납기 대기 없음'} · {data.period} 납기 {n(data.kurly.mtd)}개</div></div>
          </div>

          <div className="grid">
            <div>
              <div className="panel">
                <h3>채널별 출고 <span className="sub">{data.period} · {data.prev_month_label} 같은 기간 대비 · 아워박스 출고일 기준</span></h3>
                <table>
                  <thead><tr><th>채널</th><th className="num">{data.month_label} 출고</th><th className="num">{data.prev_month_label} 같은 기간</th><th className="num">증감</th><th className="num">최근 7일</th><th className="num">{data.month_label} 결제</th><th className="num">{data.prev_month_label} 같은 기간</th><th className="num">증감</th></tr></thead>
                  <tbody>
                    {data.channels.map(c => (
                      <tr key={c.name}><td>{c.name}</td><td className="num">{n(c.mtd)}</td><td className="num muted">{n(c.prev)}</td><td className="num"><Pct v={c.pct} /></td><td className="num">{n(c.w1)}</td>
                        <td className="num">{c.pay_mtd ? won(c.pay_mtd) : <span className="muted">-</span>}</td><td className="num muted">{c.pay_prev ? won(c.pay_prev) : '-'}</td><td className="num"><Pct v={c.pay_pct} /></td></tr>
                    ))}
                    <tr className="total"><td>합계</td><td className="num">{n(t.mtd)}</td><td className="num">{n(t.prev)}</td><td className="num"><Pct v={t.pct} /></td><td className="num">{n(t.w1)}</td><td className="num">{won(t.pay_mtd)}</td><td className="num">{won(t.pay_prev)}</td><td className="num"><Pct v={t.pay_pct} /></td></tr>
                  </tbody>
                </table>
              </div>

              <div className="panel">
                <h3>품목별 출고·재고 <span className="sub">아워박스 가용재고 · 재고 일수 = 가용재고 ÷ 이달 일평균 출고</span></h3>
                <table>
                  <thead><tr><th>품목</th><th className="num">{data.month_label} 출고</th><th className="num">{data.prev_month_label} 같은 기간</th><th className="num">증감</th><th className="num">가용재고</th><th className="num">전체재고</th><th className="num">재고 일수</th></tr></thead>
                  <tbody>
                    {data.products.map(p => (
                      <tr key={p.label}>
                        <td>{p.label}</td><td className="num">{n(p.mtd)}</td><td className="num muted">{n(p.prev)}</td><td className="num"><Pct v={p.pct} /></td>
                        <td className="num">{n(p.available)}</td><td className="num muted">{n(p.total_stock)}</td>
                        <td className="num">{p.days === null ? <span className="muted">-</span> : <span className={p.days <= 7 ? 'bad' : p.days <= 14 ? 'warn' : ''}>{p.days}일</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="panel">
                <h3>로켓그로스 판매 <span className="sub">쿠팡 Open API · 결제일 기준 · 이알하나 품목{data.rg?.fetched_at ? ` · 조회 ${data.rg.fetched_at.slice(5)}` : ''}</span></h3>
                {data.rg?.error && !data.rg.mtd.qty ? <Alert type="warning" showIcon message={`쿠팡 API 조회 실패: ${data.rg.error}`} /> : (
                  <div className="hint">{data.period} {n(data.rg?.mtd.qty)}개 · 매출 {won(data.rg?.mtd.amount)} · {data.prev_month_label} 같은 기간 {won(data.rg?.prev.amount)} <Pct v={data.rg?.pct} />
                    {data.rg?.items?.length ? <div style={{ marginTop: 4 }}>{data.rg.items.map(i => `${i.label} ${n(i.qty)}개 ${won(i.amount)}`).join(' · ')}</div> : null}</div>
                )}
              </div>

              <div className="panel">
                <h3>컬리 발주 <span className="sub">발주 캘린더 거래명세서의 이알하나 행 · 납기일 기준</span></h3>
                {data.kurly.error ? <Alert type="warning" showIcon message={data.kurly.error} /> : (
                  <>
                    <div className="hint" style={{ marginBottom: 8 }}>{data.period} 납기 {n(data.kurly.mtd)}개 · {data.prev_month_label} 같은 기간 {n(data.kurly.prev)}개 <Pct v={data.kurly.pct} /></div>
                    {data.kurly.pending.length === 0 ? <div className="muted" style={{ fontSize: '0.8rem' }}>납기 미도래 발주 없음</div> : (
                      <table>
                        <thead><tr><th>납기</th><th className="num">수량</th><th>품목</th></tr></thead>
                        <tbody>{data.kurly.pending.map(g => (
                          <tr key={g.date}><td>{g.md}</td><td className="num">{n(g.qty)}</td><td>{g.items.map(i => `${i.label} ${n(i.qty)}`).join(' · ')}</td></tr>
                        ))}</tbody>
                      </table>
                    )}
                  </>
                )}
              </div>
            </div>

            <div>
              <div className="panel">
                <h3>보고문 <span className="sub">{textDirty ? '수정 중' : '자동 생성 · 슬랙 붙이기용'}</span>
                  <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                    {textDirty && <Button size="small" onClick={() => { setTextDirty(false); setText(data.text) }}>자동 생성으로 되돌리기</Button>}
                    <Button size="small" type="primary" icon={<CopyOutlined />} onClick={copyText}>복사</Button>
                  </span>
                </h3>
                <textarea className="report" value={text} onChange={e => { setText(e.target.value); setTextDirty(true) }} spellCheck={false} />
              </div>

              <div className="panel">
                <h3>보고 의견 <span className="sub">비워 두면 자동 문구 · 저장하면 보고문에 반영</span>
                  <span style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
                    {data.notes.updated_at && <span className="hint">저장 {data.notes.updated_at}</span>}
                    <Button size="small" type="primary" icon={<SaveOutlined />} loading={saving} disabled={!notesDirty} onClick={saveNotes}>저장</Button>
                  </span>
                </h3>
                <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr', gap: '6px 10px', alignItems: 'start', fontSize: '0.82rem' }}>
                  <div style={{ paddingTop: 4 }}>보고 제목</div>
                  <Input size="small" value={title} placeholder="이알하나 출고·발주 현황" onChange={e => { setTitle(e.target.value); setNotesDirty(true) }} />
                  <div style={{ paddingTop: 4 }}>한줄정리<div className="hint">줄마다 한 항목</div></div>
                  <Input.TextArea size="small" value={headline} autoSize={{ minRows: 2, maxRows: 6 }} placeholder="이알하나 전체 — 출고 보합 ➡️ (자동)" onChange={e => { setHeadline(e.target.value); setNotesDirty(true) }} />
                  <div style={{ paddingTop: 4 }}>의견 <Tag style={{ fontSize: '0.66rem' }}>{t.pct === null ? '-' : t.pct >= 10 ? '📈 증가' : t.pct <= -10 ? '📉 감소' : '➡️ 보합'}</Tag></div>
                  <Input size="small" value={opinion} placeholder="예) 홈쇼핑 방송분 출고 집중, 메노포즈 재고 8일분 — 입고 일정 확인" onChange={e => { setOpinion(e.target.value); setNotesDirty(true) }} />
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
