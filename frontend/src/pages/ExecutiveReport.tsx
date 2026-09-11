import { useState, useEffect, useMemo, useRef } from 'react'
import { Button, DatePicker, Alert, Spin, Tooltip, message } from 'antd'
import { ReloadOutlined, PrinterOutlined } from '@ant-design/icons'
import {
  getLastOutboundForecast, getLastInventoryAnalysis, getExecReport,
} from '../services/api'
import type { OutboundForecastResult, InventoryAnalysisResult } from '../services/api'
import dayjs from 'dayjs'

/** 임원 보고서 — 출고예측·재고분석 마지막 조회 결과를 A4 보고서 양식으로 렌더.
 *
 * 데이터는 각 페이지에서 조회한 서버 스냅샷(/forecast/last, /analysis/last)을 그대로 사용.
 * 화면 상단 툴바(인쇄 시 숨김)에서 기준월 변경·새로고침·인쇄(PDF 저장) 가능.
 */

const CSS = `
.xrp { font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif; color:#1e2a3f; }
.xrp .page { width:210mm; min-height:297mm; background:#fff; margin:0 auto; padding:15mm 16mm;
  display:flex; flex-direction:column; box-shadow:0 2px 14px rgba(30,42,63,0.14); }
.xrp .doc-label { font-size:9pt; color:#5a6779; letter-spacing:2px; }
.xrp h1 { font-size:18pt; margin:2mm 0 1mm; }
.xrp .meta { font-size:8.5pt; color:#5a6779; border-bottom:2px solid #2e4360; padding-bottom:3mm; display:flex; justify-content:space-between; }
.xrp h2 { font-size:11pt; margin:6mm 0 2mm; padding-left:2.5mm; border-left:4px solid #3f6ea8; }
.xrp h2.crit { border-left-color:#b5791b; }
.xrp .hsub { font-size:8pt; font-weight:400; color:#5a6779; margin-left:1.5mm; }
.xrp table { width:100%; border-collapse:collapse; font-size:8.8pt; margin-top:1.5mm; }
.xrp th { background:#2e4360; color:#fff; padding:1.6mm 2mm; font-weight:600; text-align:left; }
.xrp td { padding:1.5mm 2mm; border-bottom:1px solid #ccd6e2; }
.xrp td.num, .xrp th.num { text-align:right; font-variant-numeric:tabular-nums; }
.xrp tr.total td { font-weight:700; background:#e3eaf4; border-top:2px solid #2e4360; }
.xrp .cards { display:flex; gap:3mm; margin-top:2mm; }
.xrp .card { flex:1 1 0; min-width:0; border:1px solid #ccd6e2; border-left:3px solid #3f6ea8; border-radius:1mm; padding:2.8mm 3mm; display:flex; flex-direction:column; min-height:24mm; }
.xrp .card.w { border-left-color:#b5791b; }
.xrp .card .k { font-size:8pt; color:#5a6779; }
.xrp .card .v { font-size:14.5pt; font-weight:700; margin-top:1.5mm; white-space:nowrap; color:#1e2a3f; letter-spacing:-0.2pt; }
.xrp .card .v small { font-size:8pt; font-weight:400; color:#5a6779; letter-spacing:0; }
.xrp .card .d { font-size:7.8pt; margin-top:auto; padding-top:1.5mm; color:#5a6779; }
.xrp .card .d b { color:#1e2a3f; }
.xrp .red { color:#c0392b; } .xrp .amber { color:#b5791b; } .xrp .green { color:#1d7a4f; }
.xrp .box { border-radius:2mm; padding:3mm 4mm; margin-top:3mm; background:#e9f0f8; border:1px solid #c3d2e4; border-left:4px solid #2e4360; }
.xrp .box h3 { font-size:9.5pt; margin-bottom:2mm; color:#1e2a3f; }
.xrp .box ul { list-style:none; margin:0; padding:0; }
.xrp .box li { font-size:9pt; line-height:1.5; margin-bottom:1.2mm; display:flex; gap:3mm; }
.xrp .box li:last-child { margin-bottom:0; }
.xrp .box li .lbl { flex:0 0 34mm; font-weight:700; color:#1e2a3f; }
.xrp .box li .lbl::before { content:'· '; color:#8b98ab; font-weight:400; }
.xrp .box li .txt { color:#3a4759; }
.xrp ul.note { list-style:none; margin:2mm 0 0; padding:0; }
.xrp ul.note li { font-size:8pt; line-height:1.5; color:#5a6779; margin-bottom:1mm; display:flex; gap:2.5mm; }
.xrp ul.note li .lbl { flex:0 0 26mm; color:#1e2a3f; font-weight:700; }
.xrp ul.note li .lbl::before { content:'· '; color:#8b98ab; font-weight:400; }
.xrp .footer { margin-top:auto; border-top:1px solid #ccd6e2; padding-top:2mm; font-size:8pt; color:#5a6779; display:flex; justify-content:space-between; }
.xrp .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:4mm; }
@media print {
  @page { size: A4; margin: 0; }
  body * { visibility: hidden; }
  .xrp-print-root, .xrp-print-root * { visibility: visible; }
  .xrp-print-root { position: absolute; left: 0; top: 0; width: 100%; }
  .xrp .page { box-shadow: none; margin: 0; }
}
`

function n(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return '-'
  return v.toLocaleString(undefined, { maximumFractionDigits: digits })
}

function achColor(pct: number | null) {
  if (pct == null) return '#8b98ab'
  return pct >= 100 ? '#1d7a4f' : pct >= 70 ? '#b5791b' : '#c0392b'
}

export default function ExecutiveReport() {
  const [fc, setFc] = useState<OutboundForecastResult | null>(null)
  const [fcAt, setFcAt] = useState<string | null>(null)
  const [inv, setInv] = useState<InventoryAnalysisResult | null>(null)
  const [invAt, setInvAt] = useState<string | null>(null)
  const [obNote, setObNote] = useState<string | null>(null)   // 경영진 요약 보고서(출고) 수기본
  const [invNote, setInvNote] = useState<string | null>(null) // 경영진 요약 보고서(재고) 수기본
  const [loading, setLoading] = useState(true)
  const [baseMonth, setBaseMonth] = useState(dayjs().format('YYYY-MM'))
  const autoMonthDone = useRef(false)

  async function load() {
    setLoading(true)
    try {
      const [f, i, ro, ri] = await Promise.allSettled([
        getLastOutboundForecast(), getLastInventoryAnalysis(),
        getExecReport('outbound'), getExecReport('inventory'),
      ])
      if (f.status === 'fulfilled') {
        setFc(f.value.result); setFcAt(f.value.saved_at)
        // 이번달 실적이 아직 없으면(월초 등) 실적이 있는 마지막 달을 기준월로 자동 선택
        const t = f.value.result?.targets
        if (!autoMonthDone.current && t?.enabled) {
          autoMonthDone.current = true
          const nowM = dayjs().format('YYYY-MM')
          const hasAct = (m: string) => (t.rows || []).some(r => (r.actual_by_month[m] || 0) > 0)
          if (!hasAct(nowM)) {
            const past = (t.months || []).filter(m => m < nowM && hasAct(m))
            if (past.length) setBaseMonth(past[past.length - 1])
          }
        }
      }
      if (i.status === 'fulfilled') { setInv(i.value.result); setInvAt(i.value.saved_at) }
      if (ro.status === 'fulfilled') setObNote(ro.value.text)
      if (ri.status === 'fulfilled') setInvNote(ri.value.text)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { load() }, [])

  const targets = fc?.targets
  const months = targets?.months || []
  const pastTM = months.filter(m => m <= baseMonth)
  const prevMonth = dayjs(baseMonth + '-01').subtract(1, 'month').format('YYYY-MM')

  const calc = useMemo(() => {
    if (!targets?.enabled) return null
    const teams = targets.teams || []
    const agg: Record<string, { act: Record<string, number>; tgt: Record<string, number>; pastAct: number; pastTgt: number }> = {}
    for (const t of teams) {
      const rs = (targets.rows || []).filter(r => r.team === t)
      const act: Record<string, number> = {}, tgt: Record<string, number> = {}
      for (const r of rs) {
        for (const m of Object.keys(r.actual_by_month)) act[m] = (act[m] || 0) + (r.actual_by_month[m] || 0)
        for (const m of months) tgt[m] = (tgt[m] || 0) + (r.target_by_month[m] || 0)
      }
      agg[t] = {
        act, tgt,
        pastAct: pastTM.reduce((s, m) => s + (act[m] || 0), 0),
        pastTgt: pastTM.reduce((s, m) => s + (tgt[m] || 0), 0),
      }
    }
    return { teams, agg }
  }, [targets, baseMonth])  // eslint-disable-line react-hooks/exhaustive-deps

  // 재고분석 파생
  const invCalc = useMemo(() => {
    if (!inv) return null
    const items = inv.items || []
    // 회전율(월평균 ÷ 현재고, 소진예상과 동일 기준)을 품목마다 계산
    const withTo = items.map(r => ({ ...r, to: r.bh_stock > 0 ? r.monthly_avg / r.bh_stock : null }))
    // 상위 = 회전율 높은 순 (출고가 있고 재고가 있는 품목만 순위 대상)
    const top = withTo
      .filter(r => r.to != null && r.out_qty > 0)
      .sort((a, b) => b.to! - a.to!)
      .slice(0, 10)
    const topKeys = new Set(top.map(r => r.key))
    // 하위 = 회전율 낮은 순 (부진·저회전 품목, 상위 10과 겹치지 않게 제외)
    const slow = withTo
      .filter(r => (r.status === 'slow' || r.status === 'low') && !topKeys.has(r.key))
      .sort((a, b) => {
        const ta = a.to ?? 0, tb = b.to ?? 0
        if (ta !== tb) return ta - tb
        return b.bh_stock - a.bh_stock
      })
      .slice(0, 10)
    const buckets = [
      { label: '기한 만료', cls: 'red', qty: 0, lots: 0, test: (d: number) => d < 0 },
      { label: 'D-90 이내', cls: 'red', qty: 0, lots: 0, test: (d: number) => d >= 0 && d <= 90 },
      { label: 'D-91~180', cls: 'amber', qty: 0, lots: 0, test: (d: number) => d > 90 && d <= 180 },
      { label: 'D-180 초과', cls: 'green', qty: 0, lots: 0, test: (d: number) => d > 180 },
    ]
    const today = dayjs().startOf('day')
    for (const it of items) for (const l of (it.lots || [])) {
      if (!l.expiration_date) continue
      const d = dayjs(l.expiration_date).startOf('day').diff(today, 'day')
      const b = buckets.find(x => x.test(d))
      if (b) { b.qty += l.total; b.lots += 1 }
    }
    return { slow, buckets, top, partners: (inv.partners || []).slice(0, 5) }
  }, [inv])

  // 전체 회전율도 보고서 기준(월평균 ÷ 현재고)으로 통일 — 백엔드 overall_turnover는 평균재고 기준
  const overallTo = inv && inv.summary.total_bh_stock > 0 ? inv.summary.monthly_avg / inv.summary.total_bh_stock : null

  const today = dayjs().format('YYYY-MM-DD')
  const bm = baseMonth.slice(5) + '월'

  // 요지: 수기 보고서(경영진 요약)에서 '■ 종합 의견' 아래 불릿을 추출, 없으면 자동 문구
  function opinionLines(text: string | null): string[] {
    if (!text) return []
    const lines = text.split('\n')
    const i = lines.findIndex(l => l.includes('종합 의견'))
    if (i < 0) return []
    return lines.slice(i + 1).filter(l => l.trim().startsWith('•')).map(l => l.replace(/^\s*•\s*/, ''))
  }
  // 자동 생성 템플릿의 안내 문구("코멘트를 남기세요")는 제외
  const opinions = [...opinionLines(obNote), ...opinionLines(invNote)].filter(t => !t.includes('코멘트를 남기세요'))

  const noData = !loading && !fc && !inv

  return (
    <div>
      {/* 툴바 (인쇄 시 숨김) */}
      <div className="page-header" style={{ display: 'flex', alignItems: 'flex-end', gap: 14, flexWrap: 'wrap' }}>
        <div>
          <h1 className="page-title">임원 보고서</h1>
          <p className="page-desc">출고 예측 · 재고 분석 마지막 조회 결과를 보고서 양식으로 정리 — 인쇄(PDF)용 A4</p>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: '0.74rem', color: '#6b7280', fontWeight: 600 }}>기준월</span>
          <DatePicker picker="month" size="small" allowClear={false}
            value={dayjs(baseMonth + '-01')}
            onChange={d => d && setBaseMonth(d.format('YYYY-MM'))} />
          <Button size="small" icon={<ReloadOutlined />} onClick={load}>새로고침</Button>
          <Tooltip title="브라우저 인쇄 → PDF로 저장하면 그대로 공유할 수 있습니다">
            <Button size="small" type="primary" icon={<PrinterOutlined />}
              onClick={() => { if (noData) { message.warning('보고할 데이터가 없습니다'); return } window.print() }}>
              인쇄 / PDF
            </Button>
          </Tooltip>
        </div>
      </div>

      {noData && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="아직 조회된 데이터가 없습니다. 출고 예측·재고 분석 페이지에서 먼저 조회하면 이곳에 보고서가 만들어집니다." />
      )}
      {loading && <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>}

      {!loading && (fc || inv) && (
        <div className="xrp xrp-print-root">
          <style>{CSS}</style>
          <div className="page">
            <div className="doc-label">물류 · 임원보고</div>
            <h1>출고 실적 및 재고 현황 보고</h1>
            <div className="meta">
              <span>박스히어로·아워박스 데이터 기준 · 기준월 {baseMonth}</span>
              <span>작성일 {today} · 물류팀</span>
            </div>

            {/* KPI 카드 */}
            <div className="cards">
              {calc && calc.teams.map(t => {
                const a = calc.agg[t]
                const tn = a.tgt[baseMonth] || 0, an = a.act[baseMonth] || 0
                const pct = tn > 0 ? Math.round(an / tn * 100) : null
                const cum = a.pastTgt > 0 ? Math.round(a.pastAct / a.pastTgt * 100) : null
                return (
                  <div className="card" key={t}>
                    <div className="k">{targets?.team_labels?.[t] || t} · {bm} 목표 달성</div>
                    <div className="v" style={{ color: achColor(pct) }}>{pct == null ? '—' : `${pct}%`}</div>
                    <div className="d">{n(an)} / {n(tn)}개 · 누적 <b>{cum == null ? '—' : `${cum}%`}</b></div>
                  </div>
                )
              })}
              {inv && invCalc && (
                <div className="card w">
                  <div className="k">소비기한 D-90 이내 재고</div>
                  <div className="v red">{n(invCalc.buckets[0].qty + invCalc.buckets[1].qty)}<small>개</small></div>
                  <div className="d">만료 <b>{n(invCalc.buckets[0].qty)}개</b> 포함 · {invCalc.buckets[0].lots + invCalc.buckets[1].lots}로트</div>
                </div>
              )}
              {inv && (
                <div className="card w">
                  <div className="k">부진 · 저회전 재고</div>
                  <div className="v">{n(inv.summary.slow_count + inv.summary.low_count)}<small>개 품목</small></div>
                  <div className="d">전체 {n(inv.summary.item_count)}개 중 <b>{inv.summary.item_count > 0 ? Math.round((inv.summary.slow_count + inv.summary.low_count) * 100 / inv.summary.item_count) : 0}%</b> · 회전율 {overallTo == null ? '-' : overallTo.toFixed(2)}</div>
                </div>
              )}
            </div>

            {/* 보고 요지 */}
            <div className="box">
              <h3>보고 요지</h3>
              <ul>
                {fc && (
                  <li><span className="lbl">출고 추세</span>
                    <span className="txt">{fc.from_date}~{fc.to_date} 총 {n(fc.summary.grand_total)}개 출고 · 월평균 {n(fc.summary.monthly_avg)}개 · 향후 {fc.forecast_months}개월 예상 {n(fc.summary.forecast_total)}개</span></li>
                )}
                {calc && (
                  <li><span className="lbl">목표 달성 ({bm})</span>
                    <span className="txt">{calc.teams.map(t => {
                      const a = calc.agg[t]; const tn = a.tgt[baseMonth] || 0
                      const pct = tn > 0 ? Math.round((a.act[baseMonth] || 0) / tn * 100) : null
                      return `${targets?.team_labels?.[t] || t} ${pct == null ? '—' : pct + '%'}`
                    }).join(' · ')}</span></li>
                )}
                {inv && invCalc && (
                  <li><span className="lbl">재고 건전성</span>
                    <span className="txt">부진 {n(inv.summary.slow_count)} · 저회전 {n(inv.summary.low_count)}개 품목 · 소비기한 D-90 이내 {n(invCalc.buckets[0].qty + invCalc.buckets[1].qty)}개(만료 포함 {invCalc.buckets[0].lots + invCalc.buckets[1].lots}로트)</span></li>
                )}
                {opinions.map((t, i) => (
                  <li key={i}><span className="lbl">종합 의견</span><span className="txt">{t}</span></li>
                ))}
              </ul>
            </div>

            {/* 1. 팀별 목표 대비 */}
            {calc && (
              <>
                <h2>1. 팀별 목표 대비 달성률 <span className="hsub">기준월 {baseMonth} · 미래월은 목표량 표시</span></h2>
                <table>
                  <tbody>
                    <tr>
                      <th>팀</th>
                      {months.map(m => <th className="num" key={m}>{m.slice(2)}{m === baseMonth ? ' ★' : ''}</th>)}
                      <th className="num">누적 달성률</th>
                    </tr>
                    {calc.teams.map(t => {
                      const a = calc.agg[t]
                      const cum = a.pastTgt > 0 ? Math.round(a.pastAct / a.pastTgt * 100) : null
                      return (
                        <tr key={t}>
                          <td><b>{targets?.team_labels?.[t] || t}</b></td>
                          {months.map(m => {
                            if (m > baseMonth) {
                              return <td className="num" key={m} style={{ color: '#5a6779' }}>목표 {n(a.tgt[m] || 0)}</td>
                            }
                            const tg = a.tgt[m] || 0, ac = a.act[m] || 0
                            const pct = tg > 0 ? Math.round(ac / tg * 100) : null
                            return (
                              <td className="num" key={m}>
                                <b style={{ color: achColor(pct) }}>{pct == null ? '—' : `${pct}%`}</b>
                                <span style={{ color: '#8b98ab', fontSize: '7.4pt' }}> {n(ac)}/{n(tg)}</span>
                              </td>
                            )
                          })}
                          <td className="num"><b style={{ color: achColor(cum) }}>{cum == null ? '—' : `${cum}%`}</b></td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
                <ul className="note">
                  <li><span className="lbl">전월 대비 ({bm})</span>
                    <span>{calc.teams.map(t => {
                      const a = calc.agg[t]
                      const cur = a.act[baseMonth] || 0, prev = a.act[prevMonth] || 0
                      const g = prev > 0 ? Math.round((cur - prev) / prev * 100) : null
                      return `${targets?.team_labels?.[t] || t} ${g == null ? '—' : (g >= 0 ? '+' : '') + g + '%'}`
                    }).join(' · ')}</span></li>
                </ul>
              </>
            )}

            {/* 2. 출고 상위 품목 — 재고분석 기준 (계산 제외 거래처 반영) */}
            {(inv || fc) && (
              <>
                <h2>2. 회전율 상위 품목 <span className="hsub">
                  {inv
                    ? `${inv.from_date} ~ ${inv.to_date} · 회전율(월평균÷현재고) 높은 순 상위 10 · 계산 제외 거래처 ${inv.excluded_partners?.length || 0}곳 반영`
                    : `${fc!.from_date} ~ ${fc!.to_date} · 출고량 상위 10 (출고예측 기준 — 거래처 제외 미반영)`}
                </span></h2>
                <table>
                  <tbody>
                    <tr><th>품목</th><th className="num">기간 출고</th><th className="num">월평균</th><th className="num">현재고</th><th className="num">회전율(월)</th><th className="num">소진 예상</th></tr>
                    {inv && invCalc
                      ? invCalc.top.map(r => (
                          <tr key={r.key}>
                            <td>{r.name}</td>
                            <td className="num">{n(r.out_qty)}</td>
                            <td className="num">{n(r.monthly_avg)}</td>
                            <td className="num">{n(r.bh_stock)}</td>
                            <td className="num"><b className="green">{r.to == null ? '-' : r.to > 99 ? '99+' : r.to.toFixed(2)}</b></td>
                            <td className="num">{r.deplete_months == null ? '-' :
                              <b className={r.deplete_months <= 1 ? 'red' : r.deplete_months <= 3 ? 'amber' : 'green'}>{r.deplete_months.toFixed(1)}개월</b>}</td>
                          </tr>
                        ))
                      : (fc!.items || []).slice(0, 10).map(r => (
                          <tr key={r.sku}>
                            <td>{r.name || r.sku}</td>
                            <td className="num">{n(r.total_out)}</td>
                            <td className="num">{n(r.monthly_avg)}</td>
                            <td className="num">{n(r.stock)}</td>
                            <td className="num">-</td>
                            <td className="num">{r.deplete_months == null ? '-' :
                              <b className={r.deplete_months <= 1 ? 'red' : r.deplete_months <= 3 ? 'amber' : 'green'}>{r.deplete_months}개월</b>}</td>
                          </tr>
                        ))}
                  </tbody>
                </table>
              </>
            )}

            {/* 3. 재고 건전성 */}
            {inv && invCalc && (
              <>
                <h2 className="crit">3. 재고 건전성 <span className="hsub">{inv.from_date} ~ {inv.to_date} · 부진 {inv.slow_days}일 / 저회전 {inv.low_turnover} 기준</span></h2>
                <div className="grid2">
                  <div>
                    <table>
                      <tbody>
                        <tr><th>소비기한 구간</th><th className="num">수량(개)</th><th className="num">로트</th></tr>
                        {invCalc.buckets.map(b => (
                          <tr key={b.label}>
                            <td><b className={b.cls}>{b.label}</b></td>
                            <td className="num">{n(b.qty)}</td>
                            <td className="num">{b.lots}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <ul className="note">
                      <li><span className="lbl">총재고</span><span>BH {n(inv.summary.total_bh_stock)}개{inv.ob_enabled ? ` · OB 가용 ${n(inv.summary.total_ob_available)}개` : ''}</span></li>
                      <li><span className="lbl">기간 출고</span><span>{n(inv.summary.total_out)}개 · 회전율 {overallTo == null ? '-' : overallTo.toFixed(2)}/월 (월평균 ÷ 현재고)</span></li>
                    </ul>
                  </div>
                  <div>
                    <table>
                      <tbody>
                        <tr><th>회전율 하위 품목 <span style={{ fontWeight: 400, fontSize: '7.4pt' }}>(부진·저회전 · 낮은 순)</span></th><th>구분</th><th className="num">회전율</th><th className="num">재고</th><th className="num">무출고</th></tr>
                        {invCalc.slow.map(r => (
                          <tr key={r.key}>
                            <td style={{ maxWidth: '44mm', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</td>
                            <td>{r.status === 'slow' ? <b className="red">부진</b> : <b className="amber">저회전</b>}</td>
                            <td className="num"><b className={((r.to ?? 0) < (inv.low_turnover ?? 0.5)) ? 'red' : 'amber'}>{r.to == null ? '-' : r.to.toFixed(2)}</b></td>
                            <td className="num">{n(r.bh_stock)}</td>
                            <td className="num">{r.days_since_out != null ? <b className={r.days_since_out >= inv.slow_days ? 'red' : 'amber'}>{r.days_since_out}일</b> : '-'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            )}

            <div className="footer">
              <span>데이터 저장: 출고예측 {fcAt || '—'} · 재고분석 {invAt || '—'}{inv?.excluded_partners?.length ? ` · 계산 제외 거래처 ${inv.excluded_partners.length}곳` : ''}</span>
              <span>작성: 물류팀 · {today}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
