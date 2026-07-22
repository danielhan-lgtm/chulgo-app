import { useState, useMemo, useCallback } from 'react'
import { Button, Table, Row, Col, Select, InputNumber, Input, Tag, Empty, message, Segmented } from 'antd'
import {
  GiftOutlined, SyncOutlined, FileExcelOutlined, SearchOutlined,
  CopyOutlined, CheckOutlined, FileTextOutlined, UndoOutlined,
} from '@ant-design/icons'
import { getExpiryReport, exportExpiryReport } from '../services/api'
import type { ExpiryReport, ExpiryRow, ExpiryGrade } from '../services/api'

const GRADE_META: Record<ExpiryGrade, { label: string; color: string; bg: string; fg: string }> = {
  urgent: { label: '기부·폐기 대상', color: 'red', bg: '#fef2f2', fg: '#991b1b' },
  caution: { label: '주의', color: 'orange', bg: '#fff7ed', fg: '#9a3412' },
  normal: { label: '정상', color: 'green', bg: '#ecfdf5', fg: '#065f46' },
  none: { label: '기한정보 없음', color: 'default', bg: '#f3f4f6', fg: '#4b5563' },
}

// ── 품의서 편집(수기 오버라이드) ─────────────────────────────────
type DocAction = 'donate' | 'dispose' | 'exclude'
interface DocRow extends ExpiryRow { qty: number }
interface Override { action: 'auto' | DocAction; qty: number | null }

const ACTION_LABEL: Record<DocAction, string> = { donate: '기부', dispose: '폐기', exclude: '제외' }
const ACTION_COLOR: Record<DocAction, string> = { donate: '#1e40af', dispose: '#991b1b', exclude: '#9ca3af' }

function rowKey(r: ExpiryRow) {
  return `${r.code}-${r.expiry}`
}

// 기본 분류: 기한 임박(urgent)이면 잔여일 남음→기부 / 만료→폐기, 그 외 등급은 제외
function defaultAction(r: ExpiryRow): DocAction {
  if (r.grade !== 'urgent') return 'exclude'
  return (r.days_left ?? 0) < 0 ? 'dispose' : 'donate'
}

function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

function daysTag(d: number | null, grade: ExpiryGrade) {
  if (d === null) return <Tag>-</Tag>
  if (d < 0) return <Tag color="magenta">만료 +{-d}일</Tag>
  return <Tag color={GRADE_META[grade].color}>D-{d}</Tag>
}

const fmt = (n: number) => n.toLocaleString('ko-KR')

// ── 품의서 문서 (물류비 지급 품의서와 동일 양식) ─────────────────
const REPORT_CSS = `<style>
    .report-container {
        font-family: 'Malgun Gothic', dotum, sans-serif;
        line-height: 1.55;
        color: #334155;
        max-width: 820px;
        margin: 10px auto;
        padding: 22px 26px;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        background-color: #ffffff;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }
    .report-container h2 {
        text-align: center;
        border-bottom: 1.5px solid #1e293b;
        padding-bottom: 8px;
        margin-top: 0;
        margin-bottom: 6px;
        color: #0f172a;
        letter-spacing: -0.01em;
        font-weight: 700;
        font-size: 1.4em;
    }
    .report-container h3 {
        border-left: 4px solid #4f46e5;
        padding: 4px 10px;
        margin-top: 16px;
        margin-bottom: 6px;
        background-color: #f8fafc;
        color: #1e293b;
        font-size: 1.05em;
        font-weight: 600;
        border-radius: 0 4px 4px 0;
    }
    .report-container ul {
        list-style-type: disc;
        margin-left: 22px;
    }
    .report-container li {
        margin-bottom: 3px;
        font-size: 10pt;
    }
    .report-container li strong {
        color: #1e293b;
        font-weight: 600;
    }
    .report-container p {
        margin-top: 4px;
        margin-bottom: 4px;
        color: #334155;
    }
    .report-container .attachment {
        margin-top: 24px;
        border-top: 1px dashed #cbd5e1;
        padding-top: 12px;
        font-size: 10pt;
        color: #475569;
    }
    .report-container .attachment strong {
        color: #1e293b;
    }
    .report-container .footer {
        margin-top: 28px;
        text-align: right;
        font-weight: 600;
        color: #475569;
    }
    @media print {
        .report-container {
            max-width: 100%;
            width: 100%;
            margin: 0;
            padding: 0;
            border: none;
            box-shadow: none;
            font-size: 8.5pt;
            line-height: 1.25;
        }
        .report-container h2 { font-size: 12pt; padding-bottom: 3px; margin: 0 0 3px; }
        .report-container h3 { margin-top: 6px; margin-bottom: 2px; padding: 1.5px 5px; font-size: 9.5pt; }
        .report-container ul { margin: 3px 0; padding-left: 14px; }
        .report-container li { margin-bottom: 0; font-size: 8.5pt; line-height: 1.3; }
        .report-container * {
            -webkit-print-color-adjust: exact;
            print-color-adjust: exact;
        }
    }
    @page { size: A4; margin: 8mm 10mm; }
</style>`

function daysLabel(d: number | null): string {
  if (d === null) return '-'
  return d < 0 ? `만료 ${-d}일 경과` : `D-${d}`
}

function docTitle(report: ExpiryReport): string {
  const [y, m] = report.base_date.split('-')
  return `${parseInt(y)}년 ${parseInt(m)}월 소비기한 임박 재고 기부·폐기 품의서`
}

function requestSummary(donate: DocRow[], dispose: DocRow[]): string {
  const donateQty = donate.reduce((s, r) => s + r.qty, 0)
  const disposeQty = dispose.reduce((s, r) => s + r.qty, 0)
  const parts: string[] = []
  if (donate.length) parts.push(`기부 ${donate.length}개 품목(${fmt(donateQty)}개)`)
  if (dispose.length) parts.push(`폐기 ${dispose.length}개 품목(${fmt(disposeQty)}개)`)
  return parts.join(', ')
}

function buildDocHtml(report: ExpiryReport, donate: DocRow[], dispose: DocRow[]): string {
  const donateQty = donate.reduce((s, r) => s + r.qty, 0)
  const disposeQty = dispose.reduce((s, r) => s + r.qty, 0)
  const caution = report.summary.caution
  const normal = report.summary.normal

  const today = new Date()
  const dateStr = `${today.getFullYear()}년 ${String(today.getMonth() + 1).padStart(2, '0')}월 ${String(today.getDate()).padStart(2, '0')}일`

  const FONT = `font-family: "Malgun Gothic", dotum, sans-serif; font-size: 10pt; line-height: 150%;`
  const LI = `${FONT} margin-top: 0px; margin-bottom: 0px;`

  const itemLi = (r: DocRow) => {
    const qtyNote = r.qty !== r.total
      ? `(전체 ${fmt(r.total)}개 중)`
      : `(가용 ${fmt(r.available)}개)`
    return `\t<li style="${LI}"><strong>${r.name}</strong> — 소비기한 ${r.expiry || '-'} <span style="color:#c00;">(${daysLabel(r.days_left)})</span> / 수량 ${fmt(r.qty)}개 <span style="color:#94a3b8;">${qtyNote}</span>&nbsp;</li>`
  }

  const donateSection = `<h3 style="${FONT}">3. 기부 대상 내역</h3>
<ul>
${donate.length
    ? donate.map(itemLi).join('\n') + `\n\t<li style="${LI}"><strong>소계:</strong> ${donate.length}개 품목 / ${fmt(donateQty)}개&nbsp;</li>`
    : `\t<li style="${LI}">해당 없음&nbsp;</li>`}
</ul>`

  const disposeSection = dispose.length
    ? `<h3 style="${FONT}">4. 폐기 대상 내역</h3>
<ul>
${dispose.map(itemLi).join('\n')}
\t<li style="${LI}"><strong>소계:</strong> ${dispose.length}개 품목 / ${fmt(disposeQty)}개&nbsp;</li>
</ul>`
    : ''

  const refNo = dispose.length ? 5 : 4
  const summaryLine = requestSummary(donate, dispose)

  return `<div class="report-container" style="${FONT} margin-top: 0px; margin-bottom: 0px;"><h2 style="${FONT}">${docTitle(report)}</h2>
    <h3 style="${FONT}">1. 개요</h3>
<ul>
\t<li style="${LI}">아워박스(주) 보관 재고 전체를 소비기한 기준으로 점검하였습니다. <span style="color:#94a3b8;">(기준일: ${report.base_date}, OB API 재고 조회)</span>&nbsp;</li>
\t<li style="${LI}">잔여 소비기한 <strong>${report.warn_days}일 미만</strong> 재고 ${report.summary.urgent.items}개 품목 / ${fmt(report.summary.urgent.total)}개가 확인되었습니다.&nbsp;</li>${summaryLine ? `
\t<li style="${LI}">이에 아래와 같이 <span style="color:#c00; font-weight:bold;">${summaryLine}</span> 처리를 요청드립니다.&nbsp;</li>` : ''}
</ul>
    <h3 style="${FONT}">2. 목적</h3>
<ul>
\t<li style="${LI}">판매 불가 예정 재고의 기부 전환을 통한 손실 최소화 및 폐기 비용 절감&nbsp;</li>
\t<li style="${LI}">소비기한 경과 재고의 적기 폐기를 통한 보관료 절감&nbsp;</li>
</ul>
    ${donateSection}
    ${disposeSection}
    <h3 style="${FONT}">${refNo}. 참고 사항</h3>
<ul>
\t<li style="${LI}"><strong>주의 재고 (잔여 ${report.warn_days}~${report.caution_days}일):</strong> ${caution.items}개 품목 / ${fmt(caution.total)}개 <span style="color:#94a3b8;">— 차기 점검 시 기부 전환 검토 필요</span>&nbsp;</li>
\t<li style="${LI}"><strong>정상 재고 (잔여 ${report.caution_days}일 이상):</strong> ${normal.items}개 품목 / ${fmt(normal.total)}개&nbsp;</li>
</ul>
    <p style="margin-top: 15px; ${FONT} margin-bottom: 0px;">끝.</p>
<div class="attachment" style="${FONT} margin-top: 0px; margin-bottom: 0px;">
        <strong>첨부 서류</strong><br>
        1. 소비기한 리포트 상세 내역 1부 (기부리포트_${report.base_date}.xlsx)&nbsp;<br></div>
<div class="footer" style="${FONT} margin-top: 0px; margin-bottom: 0px;">
        ${dateStr}<br>(주)시나몬랩</div></div>
${REPORT_CSS}`
}

function buildDocText(report: ExpiryReport, donate: DocRow[], dispose: DocRow[]): string {
  const donateQty = donate.reduce((s, r) => s + r.qty, 0)
  const disposeQty = dispose.reduce((s, r) => s + r.qty, 0)
  const summaryLine = requestSummary(donate, dispose)

  const itemLine = (r: DocRow) => {
    const qtyNote = r.qty !== r.total ? ` (전체 ${fmt(r.total)}개 중)` : ` (가용 ${fmt(r.available)}개)`
    return `   - ${r.name} — 소비기한 ${r.expiry || '-'} (${daysLabel(r.days_left)}) / ${fmt(r.qty)}개${qtyNote}`
  }

  const lines: string[] = []
  lines.push(docTitle(report))
  lines.push('')
  lines.push('1. 개요')
  lines.push(`   - 아워박스(주) 보관 재고 전체를 소비기한 기준으로 점검 (기준일: ${report.base_date}, OB API 재고 조회)`)
  lines.push(`   - 잔여 소비기한 ${report.warn_days}일 미만 재고 ${report.summary.urgent.items}개 품목 / ${fmt(report.summary.urgent.total)}개 확인`)
  if (summaryLine) lines.push(`   - 처리 요청: ${summaryLine}`)
  lines.push('')
  lines.push('2. 목적')
  lines.push('   - 판매 불가 예정 재고의 기부 전환을 통한 손실 최소화 및 폐기 비용 절감')
  lines.push('')
  lines.push('3. 기부 대상 내역')
  if (donate.length) {
    donate.forEach(r => lines.push(itemLine(r)))
    lines.push(`   - 소계: ${donate.length}개 품목 / ${fmt(donateQty)}개`)
  } else {
    lines.push('   - 해당 없음')
  }
  if (dispose.length) {
    lines.push('')
    lines.push('4. 폐기 대상 내역')
    dispose.forEach(r => lines.push(itemLine(r)))
    lines.push(`   - 소계: ${dispose.length}개 품목 / ${fmt(disposeQty)}개`)
  }
  lines.push('')
  lines.push(`${dispose.length ? 5 : 4}. 참고 사항`)
  lines.push(`   - 주의 재고 (잔여 ${report.warn_days}~${report.caution_days}일): ${report.summary.caution.items}개 품목 / ${fmt(report.summary.caution.total)}개`)
  lines.push(`   - 정상 재고 (잔여 ${report.caution_days}일 이상): ${report.summary.normal.items}개 품목 / ${fmt(report.summary.normal.total)}개`)
  lines.push('')
  lines.push('첨부서류')
  lines.push(`   - 소비기한 리포트 상세 내역 1부 (기부리포트_${report.base_date}.xlsx)`)
  lines.push('')
  lines.push('끝.')
  return lines.join('\n')
}

export default function ExpiryDonationReport() {
  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [warnDays, setWarnDays] = useState(60)
  const [cautionDays, setCautionDays] = useState(120)
  const [report, setReport] = useState<ExpiryReport | null>(null)
  const [gradeFilter, setGradeFilter] = useState<ExpiryGrade | 'all'>('urgent')
  const [search, setSearch] = useState('')
  const [view, setView] = useState<'preview' | 'html' | 'text'>('preview')
  const [copiedHtml, setCopiedHtml] = useState(false)
  const [copiedTxt, setCopiedTxt] = useState(false)
  // 수기 오버라이드: 품목별 처리 구분(기부/폐기/제외) + 품의 수량
  const [overrides, setOverrides] = useState<Record<string, Override>>({})

  const effAction = useCallback((r: ExpiryRow): DocAction => {
    const a = overrides[rowKey(r)]?.action
    return !a || a === 'auto' ? defaultAction(r) : a
  }, [overrides])

  const effQty = useCallback((r: ExpiryRow): number => {
    const q = overrides[rowKey(r)]?.qty
    return q === null || q === undefined ? r.total : q
  }, [overrides])

  function setOverride(r: ExpiryRow, patch: Partial<Override>) {
    const k = rowKey(r)
    setOverrides(prev => {
      const base: Override = prev[k] ?? { action: 'auto', qty: null }
      return { ...prev, [k]: { ...base, ...patch } }
    })
  }

  async function load() {
    if (cautionDays <= warnDays) {
      message.warning('주의 기준일은 기부·폐기 기준일보다 커야 합니다.')
      return
    }
    setLoading(true)
    try {
      const res = await getExpiryReport(warnDays, cautionDays)
      setReport(res)
      setOverrides({})
      const n = res.summary.urgent.items
      message.success(`OB 재고 ${res.rows.length}개 lot 조회 완료 — 기부·폐기 대상 ${n}건`)
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      message.error('조회 실패: ' + (detail || 'OB API 연결을 확인해주세요'))
    } finally {
      setLoading(false)
    }
  }

  const donateRows = useMemo<DocRow[]>(() =>
    report ? report.rows.filter(r => effAction(r) === 'donate').map(r => ({ ...r, qty: effQty(r) })).filter(r => r.qty > 0) : [],
  [report, effAction, effQty])

  const disposeRows = useMemo<DocRow[]>(() =>
    report ? report.rows.filter(r => effAction(r) === 'dispose').map(r => ({ ...r, qty: effQty(r) })).filter(r => r.qty > 0) : [],
  [report, effAction, effQty])

  const docHtml = useMemo(() => (report ? buildDocHtml(report, donateRows, disposeRows) : ''), [report, donateRows, disposeRows])
  const docText = useMemo(() => (report ? buildDocText(report, donateRows, disposeRows) : ''), [report, donateRows, disposeRows])

  const editedCount = Object.keys(overrides).length

  async function handleCopyHtml() {
    try {
      const blob = new Blob([docHtml], { type: 'text/html' })
      const text = new Blob([docHtml], { type: 'text/plain' })
      await navigator.clipboard.write([new ClipboardItem({ 'text/html': blob, 'text/plain': text })])
    } catch {
      await navigator.clipboard.writeText(docHtml)
    }
    setCopiedHtml(true)
    setTimeout(() => setCopiedHtml(false), 2000)
  }

  async function handleCopyText() {
    await navigator.clipboard.writeText(docText)
    setCopiedTxt(true)
    setTimeout(() => setCopiedTxt(false), 2000)
  }

  const filtered = useMemo(() => {
    if (!report) return []
    const q = search.trim().toLowerCase()
    return report.rows.filter(r =>
      (gradeFilter === 'all' || r.grade === gradeFilter) &&
      (!q || r.name.toLowerCase().includes(q) || r.code.includes(q))
    )
  }, [report, gradeFilter, search])

  async function handleExcel() {
    if (!report) return
    setExporting(true)
    try {
      const blob = await exportExpiryReport({
        base_date: report.base_date,
        warn_days: report.warn_days,
        caution_days: report.caution_days,
        rows: report.rows,
      })
      downloadBlob(blob, `기부리포트_${report.base_date}.xlsx`)
    } catch {
      message.error('엑셀 생성 실패')
    } finally {
      setExporting(false)
    }
  }

  const columns = [
    {
      title: '구분', dataIndex: 'grade', key: 'grade', width: 110,
      render: (g: ExpiryGrade) => <Tag color={GRADE_META[g].color}>{GRADE_META[g].label}</Tag>,
    },
    { title: '상품코드', dataIndex: 'code', key: 'code', width: 115 },
    { title: '제품명', dataIndex: 'name', key: 'name', ellipsis: true },
    { title: '소비기한', dataIndex: 'expiry', key: 'expiry', width: 105, render: (v: string) => v || '-' },
    {
      title: '잔여일', dataIndex: 'days_left', key: 'days_left', width: 105,
      render: (v: number | null, r: ExpiryRow) => daysTag(v, r.grade),
      sorter: (a: ExpiryRow, b: ExpiryRow) => (a.days_left ?? 99999) - (b.days_left ?? 99999),
    },
    {
      title: '전체재고', dataIndex: 'total', key: 'total', width: 85, align: 'right' as const,
      render: (v: number) => v.toLocaleString(),
      sorter: (a: ExpiryRow, b: ExpiryRow) => a.total - b.total,
    },
    {
      title: '가용재고', dataIndex: 'available', key: 'available', width: 85, align: 'right' as const,
      render: (v: number) => <b>{v.toLocaleString()}</b>,
    },
    {
      title: '품의 처리', key: 'doc_action', width: 130,
      filters: [
        { text: '기부', value: 'donate' },
        { text: '폐기', value: 'dispose' },
        { text: '제외', value: 'exclude' },
      ],
      onFilter: (v: unknown, r: ExpiryRow) => effAction(r) === v,
      render: (_: unknown, r: ExpiryRow) => {
        const cur = overrides[rowKey(r)]?.action ?? 'auto'
        const auto = defaultAction(r)
        return (
          <Select
            size="small"
            value={cur}
            onChange={a => setOverride(r, { action: a as Override['action'] })}
            style={{ width: 118, fontWeight: cur !== 'auto' ? 700 : 400 }}
            popupMatchSelectWidth={false}
            options={[
              { value: 'auto', label: <span style={{ color: ACTION_COLOR[auto] }}>자동 · {ACTION_LABEL[auto]}</span> },
              { value: 'donate', label: <span style={{ color: ACTION_COLOR.donate }}>기부</span> },
              { value: 'dispose', label: <span style={{ color: ACTION_COLOR.dispose }}>폐기</span> },
              { value: 'exclude', label: <span style={{ color: ACTION_COLOR.exclude }}>제외</span> },
            ]}
          />
        )
      },
    },
    {
      title: '품의 수량', key: 'doc_qty', width: 115,
      render: (_: unknown, r: ExpiryRow) => {
        const eff = effAction(r)
        const edited = overrides[rowKey(r)]?.qty !== null && overrides[rowKey(r)]?.qty !== undefined
        return (
          <InputNumber
            size="small"
            min={0}
            max={r.total}
            value={effQty(r)}
            disabled={eff === 'exclude'}
            onChange={v => setOverride(r, { qty: v === null ? null : Number(v) })}
            style={{ width: 100, fontWeight: edited ? 700 : 400 }}
            formatter={v => `${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
            parser={v => Number((v || '').replace(/,/g, ''))}
          />
        )
      },
    },
  ]

  const gradeOrder: ExpiryGrade[] = ['urgent', 'caution', 'normal', 'none']

  return (
    <div style={{ padding: 24, overflow: 'auto', height: '100vh' }}>
      <div style={{ marginBottom: 4, fontSize: '1.3rem', fontWeight: 800, color: '#111827' }}>
        <GiftOutlined style={{ marginRight: 8, color: '#10b981' }} />기부 리포트 (소비기한)
      </div>
      <div style={{ marginBottom: 20, color: '#6b7280', fontSize: '0.85rem' }}>
        OB API 현재고를 lot(소비기한)별로 조회 — 잔여 {warnDays}일 미만은 기부·폐기, {warnDays}~{cautionDays}일은 주의, 그 이상은 정상
      </div>

      {/* 조회 조건 */}
      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-end', marginBottom: 20, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: '0.8rem', color: '#991b1b', marginBottom: 4 }}>기부·폐기 기준 (잔여일 미만)</div>
          <InputNumber min={1} value={warnDays} onChange={v => setWarnDays(Number(v) || 60)} style={{ width: 120 }} addonAfter="일" />
        </div>
        <div>
          <div style={{ fontSize: '0.8rem', color: '#9a3412', marginBottom: 4 }}>주의 기준 (잔여일 미만)</div>
          <InputNumber min={2} value={cautionDays} onChange={v => setCautionDays(Number(v) || 120)} style={{ width: 120 }} addonAfter="일" />
        </div>
        <Button type="primary" icon={<SyncOutlined />} loading={loading} onClick={load}>
          OB 재고 조회
        </Button>
        {report && (
          <Button icon={<FileExcelOutlined />} loading={exporting} onClick={handleExcel}>
            기부 리포트 엑셀
          </Button>
        )}
      </div>

      {!report && !loading && (
        <Empty description="OB 재고 조회를 누르면 소비기한 잔여일 기준 기부·폐기 품의서가 생성됩니다." style={{ marginTop: 60 }} />
      )}

      {report && (
        <>
          {/* 품의서 (HTML 양식) */}
          <div style={{ border: '1px solid #e5e7eb', borderRadius: 12, background: '#fff', marginBottom: 20 }}>
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              flexWrap: 'wrap', gap: 8, padding: '12px 16px', borderBottom: '1px solid #f3f4f6',
            }}>
              <div style={{ fontWeight: 700 }}>
                📝 {docTitle(report)}
                <span style={{ marginLeft: 10, fontWeight: 500, fontSize: '0.8rem', color: '#6b7280' }}>
                  기부 {donateRows.length}건 · 폐기 {disposeRows.length}건
                  {editedCount > 0 && <Tag color="blue" style={{ marginLeft: 8 }}>수기 편집 {editedCount}건</Tag>}
                </span>
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <Segmented
                  size="small"
                  value={view}
                  onChange={v => setView(v as typeof view)}
                  options={[
                    { value: 'preview', label: '미리보기' },
                    { value: 'html', label: 'HTML 소스' },
                    { value: 'text', label: '텍스트' },
                  ]}
                />
                <Button
                  size="small"
                  icon={copiedHtml ? <CheckOutlined /> : <FileTextOutlined />}
                  onClick={handleCopyHtml}
                  style={copiedHtml ? { borderColor: '#10b981', color: '#059669', background: '#ecfdf5' } : { borderColor: '#818cf8', color: '#4f46e5' }}
                  title="그룹웨어/메일에 서식 유지하며 붙여넣기 가능"
                >
                  {copiedHtml ? 'HTML 복사됨' : 'HTML 양식 복사'}
                </Button>
                <Button
                  size="small"
                  icon={copiedTxt ? <CheckOutlined /> : <CopyOutlined />}
                  onClick={handleCopyText}
                  style={copiedTxt ? { borderColor: '#10b981', color: '#059669', background: '#ecfdf5' } : {}}
                >
                  {copiedTxt ? '복사됨' : '텍스트 복사'}
                </Button>
              </div>
            </div>
            <div style={{ padding: 12 }}>
              {view === 'preview' && (
                <div
                  style={{ background: '#fff', border: '1px solid #f3f4f6', borderRadius: 8, overflow: 'auto', padding: 8, maxHeight: '75vh' }}
                  dangerouslySetInnerHTML={{ __html: docHtml }}
                />
              )}
              {view === 'html' && (
                <pre style={{
                  background: '#f9fafb', border: '1px solid #f3f4f6', borderRadius: 8, padding: 16,
                  fontSize: 11, fontFamily: 'monospace', whiteSpace: 'pre-wrap', lineHeight: 1.6,
                  overflow: 'auto', maxHeight: '60vh', margin: 0,
                }}>{docHtml}</pre>
              )}
              {view === 'text' && (
                <pre style={{
                  background: '#f9fafb', border: '1px solid #f3f4f6', borderRadius: 8, padding: 16,
                  fontSize: 12, fontFamily: 'monospace', whiteSpace: 'pre-wrap', lineHeight: 1.6,
                  overflow: 'auto', maxHeight: '60vh', margin: 0,
                }}>{docText}</pre>
              )}
            </div>
          </div>

          {/* 요약 카드 */}
          <Row gutter={[10, 10]} style={{ marginBottom: 16 }}>
            {gradeOrder.map(g => {
              const s = report.summary[g]
              const m = GRADE_META[g]
              const sub = g === 'urgent'
                ? `가용 ${s.available.toLocaleString()}개` + (report.expired.items ? ` · 만료 ${report.expired.items}건` : '')
                : `가용 ${s.available.toLocaleString()}개`
              return (
                <Col key={g} span={6}>
                  <div
                    onClick={() => setGradeFilter(gradeFilter === g ? 'all' : g)}
                    style={{
                      background: m.bg, borderRadius: 10, padding: '12px 16px', textAlign: 'center',
                      cursor: 'pointer', border: gradeFilter === g ? `2px solid ${m.fg}` : '2px solid transparent',
                    }}
                  >
                    <div style={{ fontSize: '1.4rem', fontWeight: 800, color: m.fg }}>
                      {s.items.toLocaleString()}건 / {s.total.toLocaleString()}개
                    </div>
                    <div style={{ fontSize: '0.75rem', color: m.fg, fontWeight: 600 }}>{m.label} · {sub}</div>
                  </div>
                </Col>
              )
            })}
          </Row>

          {/* 필터 + 편집 안내 */}
          <div style={{ display: 'flex', gap: 12, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <Select
              value={gradeFilter}
              onChange={setGradeFilter}
              style={{ width: 180 }}
              options={[
                { value: 'all', label: '전체 보기' },
                ...gradeOrder.map(g => ({ value: g, label: GRADE_META[g].label })),
              ]}
            />
            <Input
              prefix={<SearchOutlined style={{ color: '#9ca3af' }} />}
              placeholder="제품명 · 상품코드 검색"
              value={search}
              onChange={e => setSearch(e.target.value)}
              allowClear
              style={{ width: 260 }}
            />
            <span style={{ color: '#6b7280', fontSize: '0.82rem' }}>
              기준일 {report.base_date} · {filtered.length}건 표시 —
              <b style={{ color: '#4f46e5' }}> 품의 처리·수량을 바꾸면 품의서에 즉시 반영됩니다</b>
            </span>
            {editedCount > 0 && (
              <Button size="small" icon={<UndoOutlined />} onClick={() => setOverrides({})}>
                편집 초기화 ({editedCount})
              </Button>
            )}
          </div>

          <Table
            size="small"
            rowKey={rowKey}
            dataSource={filtered}
            columns={columns}
            pagination={{ pageSize: 50, showSizeChanger: false }}
            scroll={{ y: 480 }}
            locale={{ emptyText: '해당 등급의 재고가 없습니다' }}
          />
        </>
      )}
    </div>
  )
}
