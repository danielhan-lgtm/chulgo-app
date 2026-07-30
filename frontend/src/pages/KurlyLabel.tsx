import { useState } from 'react'
import { Button, Upload, message, Spin, Input, Alert } from 'antd'
import { DeleteOutlined, PlusOutlined, FilePptOutlined, CalculatorOutlined } from '@ant-design/icons'
import pptxgen from 'pptxgenjs'
import { parseKurlyLabel } from '../services/api'
import type { KurlyItem } from '../services/api'

const SUPPLIER_DEFAULT = '(주)시나몬랩'

const EMPTY_ITEM: KurlyItem = { name: '', code: '', total: 0, expiry: '', perBox: 0, boxCount: 0, orderCode: '' }

// 박스수 = 올림(총수량 ÷ 박스당입수)
function calcBoxCount(total: number, perBox: number): number {
  if (perBox > 0 && total > 0) return Math.ceil(total / perBox)
  return 0
}

// pptxgenjs 로 마켓컬리 입고 라벨지(.pptx) 생성 — 박스 1개 = 라벨(슬라이드) 1장
// 발주코드는 품목별(orderCode)이 우선, 없으면 기본 발주코드(fallbackOrderCode) 사용
function buildKurlyPptx(fallbackOrderCode: string, supplier: string, items: KurlyItem[]) {
  const pptx = new pptxgen()
  pptx.layout = 'LAYOUT_WIDE' // 13.33 x 7.5"

  const FONT = '맑은 고딕'
  const label = (t: string) => ({ text: t, options: { align: 'center' as const, bold: false } })
  const value = (t: string, bold = false) => ({ text: t, options: { align: 'left' as const, bold } })

  const codes = [...new Set(items.map(it => (it.orderCode || '').trim() || fallbackOrderCode).filter(Boolean))]

  for (const it of items) {
    const itemCode = (it.orderCode || '').trim() || fallbackOrderCode
    const boxTotal = Number(it.boxCount) || 0
    for (let i = 1; i <= boxTotal; i++) {
      const slide = pptx.addSlide()
      const rows = [
        [label('발주코드'), value(itemCode, true)],
        [label('공급사명'), value(supplier || SUPPLIER_DEFAULT)],
        [label('상품명'), value(it.name)],
        [label('상품코드'), value(it.code)],
        [label('유통기한(소비기한)/제조일자'), value(it.expiry)],
        [label('수량/총수량'), value(`박스 내 입수량 ( ${it.perBox} )  /  총 입고수량 ( ${it.total} )`)],
        [label('C/T'), value(`박스 번호 ( ${boxTotal}-${i} )  /  전체 박스 수 ( ${boxTotal} )`)],
      ]
      slide.addTable(rows, {
        x: 0.52,
        y: 0.49,
        w: 12.4,
        colW: [3.16, 9.24],
        rowH: 0.92,
        border: { type: 'solid', color: '000000', pt: 1 },
        fontFace: FONT,
        fontSize: 18,
        valign: 'middle',
      })
    }
  }
  const fileBase = codes.length > 1 ? `${codes[0]} 외 ${codes.length - 1}건` : codes[0] || '마켓컬리'
  return pptx.writeFile({ fileName: `${fileBase}_입고라벨지.pptx` })
}

export default function KurlyLabel() {
  const [files, setFiles] = useState<File[]>([])
  const [parsing, setParsing] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [orderCode, setOrderCode] = useState('')
  const [supplier, setSupplier] = useState(SUPPLIER_DEFAULT)
  const [items, setItems] = useState<KurlyItem[]>([])

  const totalLabels = items.reduce((s, it) => s + (Number(it.boxCount) || 0), 0)

  function handleSelect(file: File) {
    setFiles(prev => (prev.some(f => f.name === file.name) ? prev : [...prev, file]))
    return false
  }

  async function handleParse() {
    if (files.length === 0) return
    setParsing(true)
    try {
      const data = await parseKurlyLabel(files)
      setOrderCode(data.orderCode || '')
      setSupplier(data.supplier || SUPPLIER_DEFAULT)
      setItems(data.items.map(it => ({ ...it })))
      if (data.parse_errors?.length) {
        message.warning(`일부 파일을 인식하지 못했습니다: ${data.parse_errors.join(' / ')}`)
      } else {
        message.success(`거래명세서 ${data.items.length}개 품목을 불러왔습니다.`)
      }
      if ((data.orderCodes?.length || 0) > 1) {
        message.info(`발주코드 ${data.orderCodes!.length}건이 감지되었습니다. 라벨마다 품목별 발주코드가 적용됩니다.`)
      }
    } catch (e: any) {
      message.error('분석 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setParsing(false)
    }
  }

  function updateItem(idx: number, patch: Partial<KurlyItem>) {
    setItems(prev => prev.map((it, i) => (i === idx ? { ...it, ...patch } : it)))
  }

  function removeItem(idx: number) {
    setItems(prev => prev.filter((_, i) => i !== idx))
  }

  function addItem() {
    setItems(prev => [...prev, { ...EMPTY_ITEM }])
  }

  function recalcBoxes() {
    setItems(prev => prev.map(it => ({ ...it, boxCount: calcBoxCount(Number(it.total), Number(it.perBox)) || it.boxCount })))
    message.success('총수량 ÷ 박스당입수로 박스수를 재계산했습니다.')
  }

  async function handleGenerate() {
    const valid = items.filter(it => (Number(it.boxCount) || 0) > 0)
    if (valid.length === 0) {
      message.warning('박스수가 1개 이상인 품목이 없습니다.')
      return
    }
    if (!orderCode.trim() && valid.some(it => !(it.orderCode || '').trim())) {
      message.warning('발주코드를 입력하세요. (품목별 발주코드가 비어있는 항목은 기본 발주코드가 사용됩니다)')
      return
    }
    setGenerating(true)
    try {
      await buildKurlyPptx(orderCode.trim(), supplier.trim() || SUPPLIER_DEFAULT, valid)
      message.success(`라벨 ${valid.reduce((s, it) => s + it.boxCount, 0)}장(.pptx)을 생성했습니다.`)
    } catch (e: any) {
      message.error('생성 실패: ' + (e.message || e))
    } finally {
      setGenerating(false)
    }
  }

  function reset() {
    setFiles([])
    setItems([])
    setOrderCode('')
    setSupplier(SUPPLIER_DEFAULT)
  }

  const parsed = items.length > 0 || !!orderCode
  const cellInput = { width: '100%', border: '1px solid #e5e7eb', borderRadius: 6, padding: '3px 8px', fontSize: '0.82rem' }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">컬리 라벨지</h1>
        <p className="page-desc">마켓컬리 거래명세서 → 입고 라벨지 PPT 자동 생성 (박스 1개 = 라벨 1장)</p>
      </div>

      {/* Flow bar */}
      <div className="flow-bar">
        {[
          { n: '1', label: '📄 거래명세서', done: files.length > 0 },
          { n: '2', label: '🔍 분석', done: items.length > 0 },
          { n: '3', label: '✏️ 품목 확인', done: items.length > 0 },
          { n: '4', label: '🏷️ 라벨 생성', done: false },
        ].map((step, i, arr) => (
          <div key={step.n} style={{ display: 'flex', alignItems: 'center', flex: i < arr.length - 1 ? undefined : 1 }}>
            <div className="flow-step">
              <div className={`flow-num${step.done ? '' : ' pending'}`}>{step.n}</div>
              <div className="flow-label">{step.label}</div>
            </div>
            {i < arr.length - 1 && <span className="flow-arrow-sm">›</span>}
          </div>
        ))}
      </div>

      {/* Upload */}
      <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, marginBottom: 16 }}>
        <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10, borderBottom: '2px solid #a855f7', paddingBottom: 8 }}>
          📄 거래명세서 업로드 <span style={{ textTransform: 'none', fontWeight: 400, marginLeft: 6 }}>마켓컬리 직납 거래명세서 PDF (선택 — 없으면 아래에 직접 입력)</span>
        </div>
        <Upload.Dragger accept=".pdf" showUploadList={false} multiple beforeUpload={handleSelect} style={{ background: '#fafafa', borderColor: '#d1d5db' }}>
          <p style={{ fontSize: '1.5rem', margin: '8px 0' }}>📑</p>
          <p style={{ fontSize: '0.82rem', color: '#6b7280' }}>PDF 파일을 드래그하거나 클릭</p>
        </Upload.Dragger>
        {files.length > 0 && (
          <div style={{ marginTop: 10 }}>
            {files.map(f => (
              <div key={f.name} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.8rem', color: '#374151', padding: '2px 0' }}>
                <span style={{ color: '#7c3aed' }}>✅ {f.name}</span>
                <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => setFiles(prev => prev.filter(x => x.name !== f.name))} />
              </div>
            ))}
            <div style={{ marginTop: 10, display: 'flex', gap: 8 }}>
              <Button type="primary" onClick={handleParse} loading={parsing} style={{ background: '#a855f7' }}>🔍 분석 시작</Button>
              {parsed && <Button onClick={reset}>초기화</Button>}
            </div>
          </div>
        )}
        {files.length === 0 && !parsed && (
          <div style={{ marginTop: 10 }}>
            <Button icon={<PlusOutlined />} onClick={() => { setItems([{ ...EMPTY_ITEM }]) }}>직접 입력으로 시작</Button>
          </div>
        )}
      </div>

      {parsing && <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>}

      {parsed && (
        <>
          {/* Header fields */}
          <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, marginBottom: 16 }}>
            <div style={{ fontWeight: 700, marginBottom: 12 }}>📋 기본 정보</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12 }}>
              <div>
                <div style={{ fontSize: '0.78rem', color: '#6b7280', marginBottom: 4 }}>발주코드 (기본값 — 품목별 발주코드가 비어있을 때 사용)</div>
                <Input value={orderCode} onChange={e => setOrderCode(e.target.value)} placeholder="T20260630_IC2KG" size="small" />
              </div>
              <div>
                <div style={{ fontSize: '0.78rem', color: '#6b7280', marginBottom: 4 }}>공급사명</div>
                <Input value={supplier} onChange={e => setSupplier(e.target.value)} size="small" />
              </div>
            </div>
          </div>

          {/* Items table */}
          <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12, gap: 8 }}>
              <span style={{ flex: 1, fontWeight: 700 }}>
                🏷️ 라벨 품목 <span style={{ fontSize: '0.8rem', color: '#6b7280', fontWeight: 400 }}>· 박스수만큼 라벨(슬라이드)이 생성됩니다</span>
              </span>
              <Button size="small" icon={<CalculatorOutlined />} onClick={recalcBoxes}>박스수 재계산</Button>
              <Button size="small" icon={<PlusOutlined />} onClick={addItem}>품목 추가</Button>
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
              <thead>
                <tr style={{ background: '#f9fafb', borderBottom: '1px solid #e5e7eb' }}>
                  <th style={{ padding: '8px 10px', width: 36, color: '#6b7280', fontWeight: 600 }}>NO</th>
                  <th style={{ padding: '8px 10px', width: 140, textAlign: 'left', color: '#6b7280', fontWeight: 600 }}>발주코드</th>
                  <th style={{ padding: '8px 10px', textAlign: 'left', color: '#6b7280', fontWeight: 600 }}>상품명</th>
                  <th style={{ padding: '8px 10px', width: 130, textAlign: 'left', color: '#6b7280', fontWeight: 600 }}>상품코드</th>
                  <th style={{ padding: '8px 10px', width: 75, color: '#6b7280', fontWeight: 600 }}>총수량</th>
                  <th style={{ padding: '8px 10px', width: 120, textAlign: 'left', color: '#6b7280', fontWeight: 600 }}>유통/소비기한</th>
                  <th style={{ padding: '8px 10px', width: 75, color: '#6b7280', fontWeight: 600 }}>박스당입수</th>
                  <th style={{ padding: '8px 10px', width: 75, color: '#7c3aed', fontWeight: 700 }}>박스수</th>
                  <th style={{ width: 36 }} />
                </tr>
              </thead>
              <tbody>
                {items.map((it, idx) => (
                  <tr key={idx} style={{ borderBottom: '1px solid #f3f4f6' }}>
                    <td style={{ padding: '5px 10px', textAlign: 'center', color: '#374151' }}>{idx + 1}</td>
                    <td style={{ padding: '5px 10px' }}>
                      <input value={it.orderCode || ''} onChange={e => updateItem(idx, { orderCode: e.target.value })} placeholder={orderCode || '기본 발주코드 사용'} style={cellInput} />
                    </td>
                    <td style={{ padding: '5px 10px' }}>
                      <input value={it.name} onChange={e => updateItem(idx, { name: e.target.value })} style={cellInput} />
                    </td>
                    <td style={{ padding: '5px 10px' }}>
                      <input value={it.code} onChange={e => updateItem(idx, { code: e.target.value })} style={cellInput} />
                    </td>
                    <td style={{ padding: '5px 10px' }}>
                      <input type="number" min={0} value={it.total} onChange={e => updateItem(idx, { total: Number(e.target.value) })} style={{ ...cellInput, textAlign: 'center' }} />
                    </td>
                    <td style={{ padding: '5px 10px' }}>
                      <input value={it.expiry} onChange={e => updateItem(idx, { expiry: e.target.value })} placeholder="2027-03-20" style={cellInput} />
                    </td>
                    <td style={{ padding: '5px 10px' }}>
                      <input type="number" min={0} value={it.perBox} onChange={e => updateItem(idx, { perBox: Number(e.target.value) })} style={{ ...cellInput, textAlign: 'center' }} />
                    </td>
                    <td style={{ padding: '5px 10px' }}>
                      <input
                        type="number"
                        min={0}
                        value={it.boxCount}
                        onChange={e => updateItem(idx, { boxCount: Number(e.target.value) })}
                        style={{ ...cellInput, textAlign: 'center', borderColor: it.boxCount ? '#ddd6fe' : '#fca5a5', background: it.boxCount ? '#f5f3ff' : '#fef2f2' }}
                      />
                    </td>
                    <td style={{ padding: '5px 6px' }}>
                      <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removeItem(idx)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div style={{ display: 'flex', gap: 12, marginTop: 14, alignItems: 'center' }}>
              <div style={{ background: '#faf5ff', border: '1px solid #e9d5ff', borderRadius: 10, padding: '8px 16px', textAlign: 'center' }}>
                <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#6b21a8' }}>{items.length}</div>
                <div style={{ fontSize: '0.72rem', fontWeight: 600, color: '#6b21a8', opacity: 0.8 }}>품목 수</div>
              </div>
              <div style={{ background: '#ecfdf5', border: '1px solid #a7f3d0', borderRadius: 10, padding: '8px 16px', textAlign: 'center' }}>
                <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#065f46' }}>{totalLabels}</div>
                <div style={{ fontSize: '0.72rem', fontWeight: 600, color: '#065f46', opacity: 0.8 }}>라벨(장)</div>
              </div>
              <div style={{ flex: 1 }} />
              <Button type="primary" size="large" icon={<FilePptOutlined />} onClick={handleGenerate} loading={generating} disabled={totalLabels === 0} style={{ background: '#a855f7' }}>
                라벨지 PPT 생성 ({totalLabels}장)
              </Button>
            </div>
          </div>

          {totalLabels === 0 && (
            <Alert type="info" showIcon style={{ fontSize: '0.82rem' }} message="박스수가 입력된 품목이 없습니다. '박스수 재계산'을 누르거나 직접 입력하세요." />
          )}
        </>
      )}
    </div>
  )
}
