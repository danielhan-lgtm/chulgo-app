import { useState, useMemo } from 'react'
import {
  Upload, Button, Table, Row, Col, Select, InputNumber, Input, Tag,
  Empty, Spin, message, Modal, Divider,
} from 'antd'
import { InboxOutlined, DeleteOutlined, GiftOutlined, FileExcelOutlined, FileTextOutlined, EyeOutlined } from '@ant-design/icons'
import { parseDisposal, exportDisposal } from '../services/api'
import type { DisposalItem, DisposalReportRow } from '../services/api'

const DEFAULT_PRICE: Record<string, number> = {
  'DJ&A': 3000, '디제이앤에이': 3000, '트윈픽스': 3000, '팝타임': 1000,
}
const DEFAULT_DISPOSAL_COST = 350000

function won(v: number) {
  return (v / 10000).toLocaleString('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + '만원'
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

function groupRows(items: DisposalItem[], priceMap: Record<string, number>): DisposalReportRow[] {
  const map = new Map<string, DisposalReportRow>()
  for (const it of items) {
    const unit = priceMap[it.brand] ?? 0
    const key = `${it.brand}|||${it.name}|||${unit}`
    const ex = map.get(key)
    if (ex) {
      ex.qty += it.qty
      ex.amount += it.qty * unit
      ex.count += 1
    } else {
      map.set(key, { brand: it.brand, name: it.name, unit_price: unit, qty: it.qty, amount: it.qty * unit, count: 1 })
    }
  }
  return Array.from(map.values()).sort((a, b) => b.amount - a.amount)
}

export default function DisposalReport() {
  const [loading, setLoading] = useState(false)
  const [fileObj, setFileObj] = useState<File | null>(null)
  const [sheets, setSheets] = useState<string[]>([])
  const [sheet, setSheet] = useState<string>('')
  const [items, setItems] = useState<DisposalItem[]>([])
  const [statuses, setStatuses] = useState<string[]>([])
  const [brands, setBrands] = useState<string[]>([])

  const [dispStatuses, setDispStatuses] = useState<string[]>([])
  const [donateStatuses, setDonateStatuses] = useState<string[]>([])
  const [priceMap, setPriceMap] = useState<Record<string, number>>({})
  const [disposalCost, setDisposalCost] = useState<number>(DEFAULT_DISPOSAL_COST)
  const [baseDate, setBaseDate] = useState<string>('')
  const [previewOpen, setPreviewOpen] = useState(false)

  async function loadFile(file: File, sheetName?: string) {
    setLoading(true)
    try {
      const res = await parseDisposal(file, file.name, sheetName)
      setFileObj(file)
      setSheets(res.sheets)
      setSheet(res.sheet)
      setItems(res.items)
      setStatuses(res.statuses)
      setBrands(res.brands)
      // 기본 분류
      setDispStatuses(res.statuses.filter(s => s.includes('만료') || s.includes('폐기')))
      setDonateStatuses(res.statuses.filter(s => s.includes('기부')))
      // 기본 단가
      const pm: Record<string, number> = {}
      for (const b of res.brands) pm[b] = DEFAULT_PRICE[b] ?? 0
      setPriceMap(pm)
      message.success(`${res.sheet} 시트 · ${res.items.length}개 품목 로드됨`)
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      message.error('파싱 실패: ' + (detail || '재고목록 형식을 확인해주세요'))
    } finally {
      setLoading(false)
    }
  }

  function onSheetChange(sn: string) {
    if (fileObj) loadFile(fileObj, sn)
  }

  const dispItems = useMemo(() => items.filter(i => dispStatuses.includes(i.status)), [items, dispStatuses])
  const donaItems = useMemo(() => items.filter(i => donateStatuses.includes(i.status)), [items, donateStatuses])
  const dispRows = useMemo(() => groupRows(dispItems, priceMap), [dispItems, priceMap])
  const donaRows = useMemo(() => groupRows(donaItems, priceMap), [donaItems, priceMap])

  const dispQty = dispRows.reduce((s, r) => s + r.qty, 0)
  const dispAmt = dispRows.reduce((s, r) => s + r.amount, 0)
  const donaQty = donaRows.reduce((s, r) => s + r.qty, 0)
  const donaAmt = donaRows.reduce((s, r) => s + r.amount, 0)
  const totalQty = dispQty + donaQty

  function buildHtml(): string {
    const rowsHtml = (rows: DisposalReportRow[], gubun: string) =>
      rows.map(r => {
        const label = `${r.brand} · ${r.name}` + (r.count > 1 ? ` (${gubun} ${r.count}건 합산)` : '')
        return `<tr><td style="padding:7px 6px;border:1px solid #d7dee8;color:#28325a;">${gubun}</td>`
          + `<td style="padding:7px 6px;border:1px solid #d7dee8;color:#28325a;word-break:keep-all;">${label}</td>`
          + `<td style="padding:7px 6px;border:1px solid #d7dee8;color:#28325a;text-align:right;font-weight:800;">${r.qty.toLocaleString()}개</td>`
          + `<td style="padding:7px 6px;border:1px solid #d7dee8;color:#28325a;text-align:right;font-weight:800;">${won(r.amount)}</td></tr>`
      }).join('')
    const th = 'style="padding:7px 6px;border:1px solid #d7dee8;background:#eef3f8;color:#28325a;font-weight:800;text-align:left;"'
    const sc = 'style="padding:7px 6px;border:1px solid #d7dee8;color:#28325a;font-weight:800;"'
    const scr = 'style="padding:7px 6px;border:1px solid #d7dee8;color:#28325a;text-align:right;font-weight:800;"'
    return `<div style="max-width:760px;color:#28325a;font-family:'Malgun Gothic',Arial,sans-serif;font-size:13px;line-height:1.5;">
<div style="font-size:22px;font-weight:800;border-bottom:3px solid #28325a;padding-bottom:10px;">소비기한 임박 재고 처리안</div>
<div style="margin:5px 0 18px;font-size:12px;color:#5f6f82;">기준일: ${baseDate || '-'}</div>
<div style="margin:0 0 10px;font-size:17px;font-weight:800;">판매 불가 재고 ${dispQty.toLocaleString()}개는 폐기하고, 기부 가능 재고 ${donaQty.toLocaleString()}개는 기부하고자 합니다.</div>
<table style="width:100%;border-collapse:collapse;border:1px solid #d7dee8;">
<tr><th ${th}>폐기 손실</th><th ${th}>기부 전환</th><th ${th}>폐기 처리 비용</th><th ${th}>총 처리 수량</th></tr>
<tr><td ${scr}>${won(dispAmt)}</td><td ${scr}>${won(donaAmt)}</td><td ${scr}>${won(disposalCost)}</td><td ${scr}>${totalQty.toLocaleString()}개</td></tr>
</table>
<div style="margin:18px 0 7px;font-size:15px;font-weight:800;">상세 내역</div>
<table style="width:100%;border-collapse:collapse;border:1px solid #d7dee8;font-size:12px;">
<thead><tr><th ${th} style="width:55px;">구분</th><th ${th}>브랜드 / 품목</th><th ${th} style="width:70px;text-align:right;">수량</th><th ${th} style="width:85px;text-align:right;">금액</th></tr></thead>
<tbody>
${rowsHtml(dispRows, '폐기')}
<tr style="background:#f4f7fa;"><td ${sc}>소계</td><td ${sc}>폐기 합계</td><td ${scr}>${dispQty.toLocaleString()}개</td><td ${scr}>${won(dispAmt)}</td></tr>
<tr><td style="padding:7px 6px;border:1px solid #d7dee8;color:#d9534f;font-weight:800;">비용</td><td style="padding:7px 6px;border:1px solid #d7dee8;font-weight:800;">외부 위탁 폐기 처리 및 차량 운반비</td><td style="padding:7px 6px;border:1px solid #d7dee8;text-align:right;">-</td><td style="padding:7px 6px;border:1px solid #d7dee8;color:#d9534f;text-align:right;font-weight:800;">${won(disposalCost)}</td></tr>
${rowsHtml(donaRows, '기부')}
<tr style="background:#f4f7fa;"><td ${sc}>소계</td><td ${sc}>기부 합계</td><td ${scr}>${donaQty.toLocaleString()}개</td><td ${scr}>${won(donaAmt)}</td></tr>
</tbody>
</table>
</div>`
  }

  async function handleExcel() {
    try {
      const blob = await exportDisposal({
        base_date: baseDate, disposal_cost: disposalCost,
        disposal_rows: dispRows, donate_rows: donaRows,
      })
      downloadBlob(blob, '폐기리포트.xlsx')
    } catch {
      message.error('엑셀 생성 실패')
    }
  }

  function handleHtml() {
    downloadBlob(new Blob([buildHtml()], { type: 'text/html;charset=utf-8' }), '재고처리안.html')
  }

  const detailCols = [
    { title: '브랜드', dataIndex: 'brand', key: 'brand', width: 90 },
    { title: '제품명', dataIndex: 'name', key: 'name', ellipsis: true },
    { title: '수량', dataIndex: 'qty', key: 'qty', width: 80, align: 'right' as const, render: (v: number) => v.toLocaleString() },
    { title: '단가', dataIndex: 'unit_price', key: 'unit_price', width: 80, align: 'right' as const, render: (v: number) => v.toLocaleString() },
    { title: '금액', dataIndex: 'amount', key: 'amount', width: 110, align: 'right' as const, render: (v: number) => <b>{v.toLocaleString()}원</b> },
    { title: '건수', dataIndex: 'count', key: 'count', width: 60, align: 'center' as const, render: (v: number) => v > 1 ? <Tag>{v}건</Tag> : v },
  ]

  const priceCols = [
    { title: '브랜드', dataIndex: 'brand', key: 'brand' },
    {
      title: '단가(원/개)', key: 'price', width: 140,
      render: (_: unknown, r: { brand: string }) => (
        <InputNumber
          size="small" min={0} step={100} style={{ width: 120 }}
          value={priceMap[r.brand] ?? 0}
          formatter={v => `${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
          parser={v => Number((v || '').replace(/,/g, ''))}
          onChange={v => setPriceMap(p => ({ ...p, [r.brand]: Number(v) || 0 }))}
        />
      ),
    },
  ]

  const statBoxes = [
    { label: `폐기 손실 (${dispQty.toLocaleString()}개)`, val: won(dispAmt), bg: '#fef2f2', color: '#991b1b' },
    { label: `기부 전환 (${donaQty.toLocaleString()}개)`, val: won(donaAmt), bg: '#eff6ff', color: '#1e40af' },
    { label: '폐기 처리 비용', val: won(disposalCost), bg: '#fff7ed', color: '#9a3412' },
    { label: '총 처리 수량', val: `${totalQty.toLocaleString()}개`, bg: '#ecfdf5', color: '#065f46' },
  ]

  return (
    <div style={{ padding: 24, overflow: 'auto', height: '100vh' }}>
      <div style={{ marginBottom: 4, fontSize: '1.3rem', fontWeight: 800, color: '#111827' }}>폐기 리포트</div>
      <div style={{ marginBottom: 20, color: '#6b7280', fontSize: '0.85rem' }}>
        소비기한 임박 재고목록 → 폐기·기부 분류 및 처리안 리포트 생성
      </div>

      {/* 업로드 */}
      <Upload.Dragger
        accept=".xlsx,.xls"
        showUploadList={false}
        beforeUpload={(file) => { loadFile(file as File); return false }}
        style={{ marginBottom: 20 }}
      >
        <p style={{ margin: 0 }}><InboxOutlined style={{ fontSize: 28, color: '#10b981' }} /></p>
        <p style={{ margin: '8px 0 0', fontWeight: 600 }}>재고목록 엑셀을 끌어다 놓거나 클릭해서 업로드</p>
        <p style={{ margin: 0, color: '#9ca3af', fontSize: '0.8rem' }}>상태·제품명·수량 컬럼이 있는 재고목록 (.xlsx)</p>
      </Upload.Dragger>

      {loading && <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>}

      {!loading && items.length === 0 && (
        <Empty description="재고목록을 업로드하면 폐기·기부 리포트가 생성됩니다." style={{ marginTop: 40 }} />
      )}

      {!loading && items.length > 0 && (
        <>
          {/* 설정 영역 */}
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            <Col span={14}>
              <div style={{ fontWeight: 700, marginBottom: 8 }}>🏷️ 처리 분류</div>
              {sheets.length > 1 && (
                <div style={{ marginBottom: 8 }}>
                  <span style={{ marginRight: 8, color: '#6b7280', fontSize: '0.82rem' }}>시트</span>
                  <Select size="small" value={sheet} onChange={onSheetChange} style={{ width: 180 }}
                    options={sheets.map(s => ({ value: s, label: s }))} />
                </div>
              )}
              <div style={{ display: 'flex', gap: 12 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: '0.8rem', color: '#991b1b', marginBottom: 4 }}>🗑️ 폐기 대상 상태</div>
                  <Select mode="multiple" value={dispStatuses} onChange={setDispStatuses}
                    style={{ width: '100%' }} options={statuses.map(s => ({ value: s, label: s }))} allowClear />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: '0.8rem', color: '#1e40af', marginBottom: 4 }}>🎁 기부 대상 상태</div>
                  <Select mode="multiple" value={donateStatuses} onChange={setDonateStatuses}
                    style={{ width: '100%' }} options={statuses.map(s => ({ value: s, label: s }))} allowClear />
                </div>
              </div>
              <div style={{ marginTop: 12, display: 'flex', gap: 16 }}>
                <div>
                  <div style={{ fontSize: '0.8rem', color: '#6b7280', marginBottom: 4 }}>🚚 폐기 처리 비용 (원)</div>
                  <InputNumber min={0} step={10000} value={disposalCost} onChange={v => setDisposalCost(Number(v) || 0)}
                    formatter={v => `${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
                    parser={v => Number((v || '').replace(/,/g, ''))} style={{ width: 160 }} />
                </div>
                <div>
                  <div style={{ fontSize: '0.8rem', color: '#6b7280', marginBottom: 4 }}>📅 기준일</div>
                  <Input value={baseDate} onChange={e => setBaseDate(e.target.value)} placeholder="2026-06-10" style={{ width: 160 }} />
                </div>
              </div>
            </Col>
            <Col span={10}>
              <div style={{ fontWeight: 700, marginBottom: 8 }}>💴 브랜드별 공급가</div>
              <Table size="small" rowKey="brand" pagination={false}
                dataSource={brands.map(b => ({ brand: b }))} columns={priceCols}
                scroll={{ y: 200 }} />
            </Col>
          </Row>

          {/* 요약 카드 */}
          <Row gutter={[10, 10]} style={{ marginBottom: 16 }}>
            {statBoxes.map((m, i) => (
              <Col key={i} span={6}>
                <div style={{ background: m.bg, borderRadius: 10, padding: '12px 16px', textAlign: 'center' }}>
                  <div style={{ fontSize: '1.4rem', fontWeight: 800, color: m.color }}>{m.val}</div>
                  <div style={{ fontSize: '0.75rem', color: m.color, fontWeight: 600 }}>{m.label}</div>
                </div>
              </Col>
            ))}
          </Row>

          {/* 다운로드 */}
          <div style={{ marginBottom: 16, display: 'flex', gap: 8 }}>
            <Button type="primary" icon={<FileExcelOutlined />} onClick={handleExcel}>엑셀 다운로드</Button>
            <Button icon={<FileTextOutlined />} onClick={handleHtml}>HTML 리포트</Button>
            <Button icon={<EyeOutlined />} onClick={() => setPreviewOpen(true)}>리포트 미리보기</Button>
          </div>

          {/* 상세 테이블 */}
          <Row gutter={16}>
            <Col span={12}>
              <div style={{ fontWeight: 700, marginBottom: 8, color: '#991b1b' }}>
                <DeleteOutlined /> 폐기 내역 · {dispRows.length}종 / {dispQty.toLocaleString()}개
              </div>
              <Table size="small" rowKey={r => `${r.brand}-${r.name}`} dataSource={dispRows}
                columns={detailCols} pagination={false} scroll={{ y: 360 }}
                locale={{ emptyText: '폐기 대상 없음' }} />
            </Col>
            <Col span={12}>
              <div style={{ fontWeight: 700, marginBottom: 8, color: '#1e40af' }}>
                <GiftOutlined /> 기부 내역 · {donaRows.length}종 / {donaQty.toLocaleString()}개
              </div>
              <Table size="small" rowKey={r => `${r.brand}-${r.name}`} dataSource={donaRows}
                columns={detailCols} pagination={false} scroll={{ y: 360 }}
                locale={{ emptyText: '기부 대상 없음' }} />
            </Col>
          </Row>

          <Modal open={previewOpen} onCancel={() => setPreviewOpen(false)} footer={null} width={820} title="처리안 리포트 미리보기">
            <Divider style={{ margin: '0 0 12px' }} />
            <div dangerouslySetInnerHTML={{ __html: buildHtml() }} />
          </Modal>
        </>
      )}
    </div>
  )
}
