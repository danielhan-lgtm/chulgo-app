import { useEffect, useState } from 'react'
import { Button, Upload, message, Spin, Input, Alert, Checkbox, Tooltip, Select } from 'antd'
import { DownloadOutlined, DeleteOutlined, PlusOutlined, FilePptOutlined, SettingOutlined, PushpinOutlined } from '@ant-design/icons'
import {
  parseGrowthLoad, generateGrowthLoad, getGrowthLoadSettings, saveGrowthLoadSettings,
  type GrowthLoadRow, type GrowthLoadSettings,
} from '../services/api'
import { DEFAULT_PALLET_CAP, distributeByCapacity, effectiveCap } from '../lib/pallet'

interface Header {
  supplier: string
  supplier_code: string
  request_id: string
  center: string
  date: string
  pallet: string
  pallet_total: number
  box_barcode: string
  total_box: number
}
const EMPTY: Header = { supplier: '', supplier_code: '', request_id: '', center: '', date: '', pallet: '1-1', pallet_total: 1, box_barcode: '', total_box: 0 }

function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url; a.download = name; a.click()
  URL.revokeObjectURL(url)
}

export default function CoupangGrowthLoad() {
  const [files, setFiles] = useState<File[]>([])
  const [parsing, setParsing] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [parsed, setParsed] = useState(false)
  const [header, setHeader] = useState<Header>(EMPTY)
  const [rows, setRows] = useState<GrowthLoadRow[]>([])
  const [hasAttach, setHasAttach] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const [settings, setSettings] = useState<GrowthLoadSettings | null>(null)
  const [showSettings, setShowSettings] = useState(false)
  const [savingSettings, setSavingSettings] = useState(false)
  const [palletCap, setPalletCap] = useState(DEFAULT_PALLET_CAP)
  const [attachPalletTotal, setAttachPalletTotal] = useState(1)

  // 저장된 입수 설정 불러오기 (1박스당 수량 규칙·파레트당 박스 수)
  useEffect(() => {
    getGrowthLoadSettings()
      .then(s => {
        setSettings(s)
        setPalletCap(s.pallet_cap || DEFAULT_PALLET_CAP)
      })
      .catch(() => message.warning('입수 설정을 불러오지 못했습니다. 기본값을 사용합니다.'))
  }, [])

  async function handleSaveSettings(next?: GrowthLoadSettings) {
    const s = next || settings
    if (!s) return
    setSavingSettings(true)
    try {
      const saved = await saveGrowthLoadSettings({ ...s, rules: s.rules.filter(r => r.match.trim() !== '') })
      setSettings(saved)
      setPalletCap(saved.pallet_cap || DEFAULT_PALLET_CAP)
      message.success('입수 설정을 저장했습니다. 다음 분석부터 자동 적용됩니다.')
    } catch (e: any) {
      message.error('설정 저장 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setSavingSettings(false)
    }
  }

  // 표에서 수정한 박스수를 '1박스당 수량' 규칙으로 저장
  async function saveRowRule(r: GrowthLoadRow) {
    if (!settings) return
    const box = Number(r.box) || 0
    const qty = Number(r.qty) || 0
    if (box <= 0 || qty <= 0) {
      message.warning('박스수와 상품 수량을 먼저 입력하세요.')
      return
    }
    const per = Math.max(1, Math.round(qty / box))
    const match = (r.sku || r.name || '').trim()
    if (!match) {
      message.warning('SKU ID 또는 상품명을 먼저 입력하세요.')
      return
    }
    const next = { ...settings, rules: [{ match, per_box: per }, ...settings.rules.filter(x => x.match !== match)] }
    setSettings(next)
    try {
      await saveGrowthLoadSettings(next)
      message.success(`'${match}' → 1박스당 ${per}개 규칙을 저장했습니다.`)
    } catch (e: any) {
      message.error('규칙 저장 실패: ' + (e.response?.data?.detail || e.message))
    }
  }

  const totalQty = rows.reduce((s, r) => s + (Number(r.qty) || 0), 0)
  const totalBox = rows.reduce((s, r) => s + (Number(r.box) || 0), 0)
  const slideCount = Math.max(1, header.pallet_total || 1)
  const clampPallet = (p?: number) => Math.min(slideCount, Math.max(1, Number(p) || 1))
  // 팔레트별 박스 합계 (1..slideCount)
  const palletBox: number[] = Array.from({ length: slideCount }, (_, i) =>
    rows.filter(r => clampPallet(r.pallet) === i + 1).reduce((s, r) => s + (Number(r.box) || 0), 0),
  )

  function handleSelect(file: File) {
    setFiles(prev => (prev.some(f => f.name === file.name) ? prev : [...prev, file]))
    return false
  }

  async function handleParse() {
    if (files.length === 0) return
    setParsing(true)
    try {
      const d = await parseGrowthLoad(files)
      setAttachPalletTotal(d.pallet_total || 1)
      const baseRows = d.rows.map((r, i) => ({ ...r, no: i + 1, box: Number(r.box) || 1, pallet: r.pallet || 1 }))
      // 팔레트 수 결정 방식(설정): auto=용량으로 계산 / attach=부착문서 값 / fixed=고정값
      const mode = settings?.pallet_total_mode || 'auto'
      let target = 0
      if (mode === 'attach' && d.pallet_total) target = d.pallet_total
      else if (mode === 'fixed') target = Math.max(1, settings?.pallet_total_fixed || 1)
      const totalBoxSum = baseRows.reduce((s, r) => s + (Number(r.box) || 0), 0)
      const { rows: distRows, count } = distributeByCapacity(baseRows, effectiveCap(totalBoxSum, palletCap, target))
      setRows(distRows)
      setHeader({
        supplier: d.supplier || '', supplier_code: d.supplier_code || '',
        request_id: d.request_id || '', center: d.center || '', date: d.date || '',
        pallet: d.pallet || '1-1', pallet_total: Math.max(count, target),
        box_barcode: d.box_barcode || '', total_box: 0,
      })
      setHasAttach(d.has_부착)
      setErrors(d.parse_errors || [])
      setParsed(true)
      message.success(`상품 ${d.rows.length}개를 불러왔습니다.`)
    } catch (e: any) {
      message.error('분석 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setParsing(false)
    }
  }

  function updateRow(i: number, patch: Partial<GrowthLoadRow>) {
    setRows(prev => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
  }
  function removeRow(i: number) {
    setRows(prev => prev.filter((_, idx) => idx !== i).map((r, idx) => ({ ...r, no: idx + 1 })))
  }
  function addRow() {
    setRows(prev => [...prev, { no: prev.length + 1, sku: '', name: '', box_no: header.box_barcode, box: 1, qty: 0, expire: '', made: '', pallet: 1 }])
  }

  function handleAutoDistribute() {
    const { rows: distRows, count } = distributeByCapacity(rows, palletCap)
    setRows(distRows)
    setHeader(h => ({ ...h, pallet_total: count }))
    message.success(`팔레트당 최대 ${palletCap}박스 기준으로 ${count}개 팔레트에 분배했습니다.`)
  }

  async function handleGenerate() {
    if (rows.length === 0) return
    setGenerating(true)
    try {
      const blob = await generateGrowthLoad({
        ...header,
        pallet_total: slideCount,
        total_box: header.total_box || totalBox,
        rows: rows.map(r => ({ ...r, box: Number(r.box) || 0, qty: Number(r.qty) || 0, pallet: clampPallet(r.pallet) })),
      })
      downloadBlob(blob, `${header.center || '쿠팡그로스'}_적재리스트.pptx`)
      message.success('그로스 적재리스트 PPT를 생성했습니다.')
    } catch (e: any) {
      message.error('생성 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setGenerating(false)
    }
  }

  function reset() {
    setFiles([]); setParsed(false); setRows([]); setHeader(EMPTY); setErrors([])
    setPalletCap(settings?.pallet_cap || DEFAULT_PALLET_CAP); setAttachPalletTotal(1)
  }

  const labelStyle = { fontSize: '0.78rem', color: '#6b7280', marginBottom: 4 }
  const cellInput = { width: '100%', border: '1px solid #e5e7eb', borderRadius: 6, padding: '3px 8px', fontSize: '0.82rem' }

  return (
    <div>
      <div className="page-header" style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ flex: 1 }}>
          <h1 className="page-title">쿠팡 그로스 적재리스트</h1>
          <p className="page-desc">동봉문서 · 부착문서 PDF → 로켓그로스 팔레트 적재리스트 PPT 자동 생성</p>
        </div>
        <Button icon={<SettingOutlined />} onClick={() => setShowSettings(v => !v)}>입수 설정</Button>
      </div>

      {/* 입수 설정 — 저장하면 다음 분석부터 자동 적용 */}
      {showSettings && settings && (
        <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #c7d2fe', padding: 16, marginBottom: 16 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>⚙️ 입수 설정</div>
          <div style={{ fontSize: '0.78rem', color: '#6b7280', marginBottom: 12 }}>
            저장하면 다음 분석부터 자동 적용됩니다. 박스수 계산 순서: ① 상품별 규칙 → ② 번들 표기(수량=박스, 켠 경우) → ③ 기본 1박스당 수량
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
            <div>
              <div style={labelStyle}>파레트당 최대 박스 수</div>
              <Input
                type="number" min={1} size="small" suffix="박스"
                value={settings.pallet_cap}
                onChange={e => setSettings({ ...settings, pallet_cap: Math.max(1, Number(e.target.value) || 1) })}
              />
            </div>
            <div>
              <div style={labelStyle}>팔레트 개수 결정</div>
              <Select
                size="small" style={{ width: '100%' }}
                value={settings.pallet_total_mode}
                onChange={v => setSettings({ ...settings, pallet_total_mode: v })}
                options={[
                  { value: 'auto', label: '자동 (용량으로 계산)' },
                  { value: 'attach', label: '부착문서 값 사용' },
                  { value: 'fixed', label: '고정값 사용' },
                ]}
              />
            </div>
            {settings.pallet_total_mode === 'fixed' && (
              <div>
                <div style={labelStyle}>고정 팔레트 수</div>
                <Input
                  type="number" min={1} size="small" suffix="개"
                  value={settings.pallet_total_fixed}
                  onChange={e => setSettings({ ...settings, pallet_total_fixed: Math.max(1, Number(e.target.value) || 1) })}
                />
              </div>
            )}
            <div>
              <div style={labelStyle}>기본 1박스당 수량</div>
              <Input
                type="number" min={1} size="small" suffix="개"
                value={settings.default_per_box}
                onChange={e => setSettings({ ...settings, default_per_box: Math.max(1, Number(e.target.value) || 1) })}
              />
            </div>
            <div style={{ display: 'flex', alignItems: 'flex-end', paddingBottom: 4 }}>
              <Checkbox
                checked={settings.bundle_is_box}
                onChange={e => setSettings({ ...settings, bundle_is_box: e.target.checked })}
              >
                <span style={{ fontSize: '0.8rem' }}>N개입·x3 등 번들 상품은 수량=박스</span>
              </Checkbox>
            </div>
          </div>
          <div style={{ fontSize: '0.8rem', fontWeight: 700, marginBottom: 6 }}>
            상품별 1박스당 수량 규칙{' '}
            <span style={{ fontWeight: 400, color: '#6b7280' }}>· SKU ID(전체 일치) 또는 상품명 키워드(포함) — 위 규칙부터 먼저 적용</span>
          </div>
          {settings.rules.map((rule, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 6, alignItems: 'center' }}>
              <Input
                size="small" style={{ flex: 1 }} placeholder="SKU ID 또는 상품명 키워드 (예: 멜라토닌)"
                value={rule.match}
                onChange={e => setSettings({ ...settings, rules: settings.rules.map((x, xi) => (xi === i ? { ...x, match: e.target.value } : x)) })}
              />
              <Input
                size="small" type="number" min={1} style={{ width: 130 }} suffix="개/박스"
                value={rule.per_box}
                onChange={e => setSettings({ ...settings, rules: settings.rules.map((x, xi) => (xi === i ? { ...x, per_box: Math.max(1, Number(e.target.value) || 1) } : x)) })}
              />
              <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => setSettings({ ...settings, rules: settings.rules.filter((_, xi) => xi !== i) })} />
            </div>
          ))}
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <Button size="small" icon={<PlusOutlined />} onClick={() => setSettings({ ...settings, rules: [...settings.rules, { match: '', per_box: settings.default_per_box }] })}>
              규칙 추가
            </Button>
            <div style={{ flex: 1 }} />
            <Button size="small" type="primary" loading={savingSettings} onClick={() => handleSaveSettings()}>💾 설정 저장</Button>
          </div>
        </div>
      )}

      {/* Upload */}
      <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, marginBottom: 16 }}>
        <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10, borderBottom: '2px solid #10b981', paddingBottom: 8 }}>
          📄 서류 업로드 <span style={{ textTransform: 'none', fontWeight: 400, marginLeft: 6 }}>동봉문서 · 부착문서 PDF</span>
        </div>
        <Upload.Dragger accept=".pdf" showUploadList={false} multiple beforeUpload={handleSelect} style={{ background: '#fafafa', borderColor: '#d1d5db' }}>
          <p style={{ fontSize: '1.5rem', margin: '8px 0' }}>📑</p>
          <p style={{ fontSize: '0.82rem', color: '#6b7280' }}>동봉문서·부착문서 PDF를 드래그하거나 클릭</p>
        </Upload.Dragger>
        {files.length > 0 && (
          <div style={{ marginTop: 10 }}>
            {files.map(f => (
              <div key={f.name} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.8rem', color: '#374151', padding: '2px 0' }}>
                <span style={{ color: '#059669' }}>✅ {f.name}</span>
                <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => setFiles(prev => prev.filter(x => x.name !== f.name))} />
              </div>
            ))}
            <div style={{ marginTop: 10, display: 'flex', gap: 8 }}>
              <Button type="primary" onClick={handleParse} loading={parsing}>🔍 분석 시작</Button>
              {parsed && <Button onClick={reset}>초기화</Button>}
            </div>
          </div>
        )}
      </div>

      {parsing && <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>}

      {parsed && (
        <>
          {errors.length > 0 && <Alert type="warning" showIcon style={{ marginBottom: 16 }} message="일부 파일 인식 실패" description={<ul style={{ margin: 0, paddingLeft: 18 }}>{errors.map((w, i) => <li key={i}>{w}</li>)}</ul>} />}
          {!hasAttach && <Alert type="info" showIcon style={{ marginBottom: 16, fontSize: '0.82rem' }} message="부착문서를 인식하지 못해 요청ID·물류센터·팔레트번호가 비어있을 수 있습니다. 아래에서 직접 입력하세요." />}

          {/* Header */}
          <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, marginBottom: 16 }}>
            <div style={{ fontWeight: 700, marginBottom: 12 }}>📋 입고 예약 정보</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
              <div><div style={labelStyle}>업체명</div><Input size="small" value={header.supplier} onChange={e => setHeader({ ...header, supplier: e.target.value })} /></div>
              <div><div style={labelStyle}>업체코드</div><Input size="small" value={header.supplier_code} onChange={e => setHeader({ ...header, supplier_code: e.target.value })} /></div>
              <div><div style={labelStyle}>요청 ID (밀크런ID)</div><Input size="small" value={header.request_id} onChange={e => setHeader({ ...header, request_id: e.target.value })} /></div>
              <div><div style={labelStyle}>물류센터</div><Input size="small" value={header.center} onChange={e => setHeader({ ...header, center: e.target.value })} /></div>
              <div><div style={labelStyle}>도착예정일</div><Input size="small" value={header.date} onChange={e => setHeader({ ...header, date: e.target.value })} placeholder="2026-07-07" /></div>
              <div><div style={labelStyle}>팔레트 번호 (단일 팔레트일 때 표기)</div><Input size="small" value={header.pallet} onChange={e => setHeader({ ...header, pallet: e.target.value })} /></div>
              <div>
                <div style={labelStyle}>총 팔레트 수 (= 슬라이드 장수)</div>
                <Input
                  size="small" type="number" min={1}
                  value={header.pallet_total}
                  onChange={e => setHeader({ ...header, pallet_total: Math.max(1, Number(e.target.value) || 1) })}
                />
              </div>
              <div><div style={labelStyle}>총 박스 (비우면 자동합계 {totalBox})</div><Input size="small" type="number" value={header.total_box || ''} onChange={e => setHeader({ ...header, total_box: Number(e.target.value) || 0 })} placeholder={String(totalBox)} /></div>
            </div>
            <div style={{ marginTop: 12, fontSize: '0.8rem', color: '#374151' }}>
              📊 <b>{slideCount}장</b>의 슬라이드가 생성됩니다 (팔레트 {slideCount}개)
              {hasAttach && attachPalletTotal !== slideCount && (
                <span style={{ color: '#b45309' }}>
                  {' '}⚠️ 부착문서 기준은 {attachPalletTotal}개입니다 — 용량(팔레트당 {palletCap}박스)으로 계산한 값과 다릅니다. 확인하세요.
                </span>
              )}
              {slideCount > 1 && (
                <span style={{ color: '#6b7280' }}>
                  {' '}— 표의 <b>팔레트</b> 칸에서 직접 조정하거나 <b>자동 분배</b>를 다시 누르세요.
                </span>
              )}
              <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                {palletBox.map((b, i) => (
                  <span key={i} style={{ background: b ? '#ecfdf5' : '#fef2f2', border: `1px solid ${b ? '#a7f3d0' : '#fca5a5'}`, borderRadius: 8, padding: '3px 10px', fontSize: '0.78rem', fontWeight: 600, color: b ? '#065f46' : '#b91c1c' }}>
                    {slideCount}-{i + 1} : {b} BOX
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* Product table */}
          <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12, gap: 8 }}>
              <span style={{ flex: 1, fontWeight: 700 }}>
                📦 상품 정보 <span style={{ fontSize: '0.8rem', color: '#6b7280', fontWeight: 400 }}>
                  · 박스수는 저장된 입수 설정(기본 {settings?.default_per_box ?? 132}개/박스 · 상품 규칙 {settings?.rules.length ?? 0}개)으로 자동 계산 — 수정 후 📌을 누르면 다음부터 자동 적용
                </span>
              </span>
              <span style={{ fontSize: '0.78rem', color: '#6b7280' }}>팔레트당 최대</span>
              <Input
                type="number"
                min={1}
                value={palletCap}
                onChange={e => setPalletCap(Math.max(1, Number(e.target.value) || DEFAULT_PALLET_CAP))}
                size="small"
                style={{ width: 70 }}
                suffix="박스"
              />
              <Button size="small" type="primary" ghost onClick={handleAutoDistribute}>
                🔀 자동 분배
              </Button>
              <Button size="small" icon={<PlusOutlined />} onClick={addRow}>품목 추가</Button>
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
              <thead>
                <tr style={{ background: '#f9fafb', borderBottom: '1px solid #e5e7eb' }}>
                  <th style={{ padding: '8px 6px', width: 36, color: '#6b7280' }}>No</th>
                  <th style={{ padding: '8px 6px', width: 100, textAlign: 'left', color: '#6b7280' }}>SKU ID</th>
                  <th style={{ padding: '8px 6px', textAlign: 'left', color: '#6b7280' }}>상품명 / 옵션명</th>
                  <th style={{ padding: '8px 6px', width: 90, color: '#10b981', fontWeight: 700 }}>박스 번호<br/><span style={{ fontWeight: 400, fontSize: '0.68rem' }}>(박스수)</span></th>
                  <th style={{ padding: '8px 6px', width: 80, color: '#6b7280' }}>상품 수량</th>
                  <th style={{ padding: '8px 6px', width: 140, textAlign: 'left', color: '#6b7280' }}>소비기한/제조일자</th>
                  {slideCount > 1 && <th style={{ padding: '8px 6px', width: 70, color: '#7c3aed', fontWeight: 700 }}>팔레트</th>}
                  <th style={{ width: 68 }} />
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid #f3f4f6' }}>
                    <td style={{ textAlign: 'center', color: '#374151' }}>{i + 1}</td>
                    <td style={{ padding: '5px 6px' }}><input value={r.sku} onChange={e => updateRow(i, { sku: e.target.value })} style={cellInput} /></td>
                    <td style={{ padding: '5px 6px' }}><input value={r.name} onChange={e => updateRow(i, { name: e.target.value })} style={cellInput} /></td>
                    <td style={{ padding: '5px 6px' }}><input type="number" min={0} value={r.box} onChange={e => updateRow(i, { box: Number(e.target.value) })} style={{ ...cellInput, textAlign: 'center', borderColor: '#a7f3d0', background: '#f0fdf4' }} /></td>
                    <td style={{ padding: '5px 6px' }}><input type="number" min={0} value={r.qty} onChange={e => updateRow(i, { qty: Number(e.target.value) })} style={{ ...cellInput, textAlign: 'center' }} /></td>
                    <td style={{ padding: '5px 6px' }}>
                      <div style={{ display: 'flex', gap: 4 }}>
                        <input value={r.expire} onChange={e => updateRow(i, { expire: e.target.value })} placeholder="소비기한" style={{ ...cellInput }} />
                        <input value={r.made} onChange={e => updateRow(i, { made: e.target.value })} placeholder="제조일자" style={{ ...cellInput }} />
                      </div>
                    </td>
                    {slideCount > 1 && (
                      <td style={{ padding: '5px 6px' }}>
                        <input
                          type="number"
                          min={1}
                          max={slideCount}
                          value={r.pallet || 1}
                          onChange={e => updateRow(i, { pallet: Math.min(slideCount, Math.max(1, Number(e.target.value) || 1)) })}
                          style={{ ...cellInput, textAlign: 'center', borderColor: '#ddd6fe', background: '#f5f3ff' }}
                        />
                      </td>
                    )}
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <Tooltip title="이 상품의 1박스당 수량(수량÷박스)을 규칙으로 저장 — 다음부터 자동 적용">
                        <Button size="small" type="text" icon={<PushpinOutlined />} onClick={() => saveRowRule(r)} />
                      </Tooltip>
                      <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removeRow(i)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div style={{ display: 'flex', gap: 12, marginTop: 14, alignItems: 'center' }}>
              <div style={{ background: '#ecfdf5', border: '1px solid #a7f3d0', borderRadius: 10, padding: '8px 16px', textAlign: 'center' }}>
                <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#065f46' }}>{totalBox}</div>
                <div style={{ fontSize: '0.72rem', fontWeight: 600, color: '#065f46' }}>총 박스</div>
              </div>
              <div style={{ background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 10, padding: '8px 16px', textAlign: 'center' }}>
                <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#1e40af' }}>{totalQty}</div>
                <div style={{ fontSize: '0.72rem', fontWeight: 600, color: '#1e40af' }}>총 수량</div>
              </div>
              <div style={{ flex: 1 }} />
              <Button type="primary" size="large" icon={<FilePptOutlined />} onClick={handleGenerate} loading={generating} disabled={rows.length === 0}>
                <DownloadOutlined /> 적재리스트 PPT 생성
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
