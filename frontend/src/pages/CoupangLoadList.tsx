import { useEffect, useState } from 'react'
import { Button, Upload, message, Spin, Input, Alert, Select, Checkbox, Tooltip } from 'antd'
import { DownloadOutlined, DeleteOutlined, PlusOutlined, FilePptOutlined, SettingOutlined, PushpinOutlined } from '@ant-design/icons'
import { parseCoupangLoad, generateCoupangLoad, getCoupangLoadSettings, saveCoupangLoadSettings } from '../services/api'
import type { CoupangLoadRow, CoupangLoadSettings } from '../services/api'
import { DEFAULT_PALLET_CAP, distributeByCapacity, effectiveCap } from '../lib/pallet'

interface Header {
  supplier: string
  center: string
  date: string
  milkrun: string
  pallet: string
  pallet_total: number
}

const EMPTY_HEADER: Header = { supplier: '', center: '', date: '', milkrun: '', pallet: '1-1', pallet_total: 1 }

function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

export default function CoupangLoadList() {
  const [files, setFiles] = useState<File[]>([])
  const [parsing, setParsing] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [parsed, setParsed] = useState(false)
  const [header, setHeader] = useState<Header>(EMPTY_HEADER)
  const [rows, setRows] = useState<CoupangLoadRow[]>([])
  const [hasAttach, setHasAttach] = useState(false)
  const [warnings, setWarnings] = useState<string[]>([])
  const [palletCap, setPalletCap] = useState(DEFAULT_PALLET_CAP)
  const [attachPalletTotal, setAttachPalletTotal] = useState(1)
  const [settings, setSettings] = useState<CoupangLoadSettings | null>(null)
  const [showSettings, setShowSettings] = useState(false)
  const [savingSettings, setSavingSettings] = useState(false)

  // 저장된 적재 설정 불러오기 (파레트당 박스 수·입수 규칙)
  useEffect(() => {
    getCoupangLoadSettings()
      .then(s => {
        setSettings(s)
        setPalletCap(s.pallet_cap || DEFAULT_PALLET_CAP)
      })
      .catch(() => message.warning('적재 설정을 불러오지 못했습니다. 기본값을 사용합니다.'))
  }, [])

  async function handleSaveSettings(next?: CoupangLoadSettings) {
    const s = next || settings
    if (!s) return
    setSavingSettings(true)
    try {
      const saved = await saveCoupangLoadSettings({
        ...s,
        rules: s.rules.filter(r => r.match.trim() !== ''),
      })
      setSettings(saved)
      setPalletCap(saved.pallet_cap || DEFAULT_PALLET_CAP)
      message.success('적재 설정을 저장했습니다. 다음 분석부터 자동 적용됩니다.')
    } catch (e: any) {
      message.error('설정 저장 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setSavingSettings(false)
    }
  }

  // 표에서 수정한 BOX 수량을 '1박스당 수량' 규칙으로 저장 (다음부터 자동 적용)
  async function saveRowRule(r: CoupangLoadRow) {
    if (!settings) return
    const box = Number(r.box) || 0
    const qty = Number(r.qty) || 0
    if (box <= 0 || qty <= 0) {
      message.warning('BOX 수량과 수량을 먼저 입력하세요.')
      return
    }
    const per = Math.max(1, Math.round(qty / box))
    const match = (r.sku || r.name || '').trim()
    if (!match) {
      message.warning('상품번호 또는 상품명을 먼저 입력하세요.')
      return
    }
    const next = {
      ...settings,
      rules: [{ match, per_box: per }, ...settings.rules.filter(x => x.match !== match)],
    }
    setSettings(next)
    try {
      await saveCoupangLoadSettings(next)
      message.success(`'${match}' → 1박스당 ${per}개 규칙을 저장했습니다.`)
    } catch (e: any) {
      message.error('규칙 저장 실패: ' + (e.response?.data?.detail || e.message))
    }
  }

  const totalBox = rows.reduce((s, r) => s + (Number(r.box) || 0), 0)
  const totalQty = rows.reduce((s, r) => s + (Number(r.qty) || 0), 0)
  const slideCount = Math.max(1, header.pallet_total || 1)
  const clampPallet = (p: number) => Math.min(slideCount, Math.max(1, Number(p) || 1))
  // 팔레트별 BOX 합계 (1..slideCount)
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
      const data = await parseCoupangLoad(files)
      setAttachPalletTotal(data.pallet_total || 1)
      const baseRows = data.rows.map((r, i) => ({ ...r, no: i + 1, pallet: r.pallet || 1 }))
      // 팔레트 수 결정 방식(설정): auto=용량으로 계산 / attach=부착리스트 값 / fixed=고정값
      const mode = settings?.pallet_total_mode || 'auto'
      let target = 0
      if (mode === 'attach' && data.pallet_total) target = data.pallet_total
      else if (mode === 'fixed') target = Math.max(1, settings?.pallet_total_fixed || 1)
      // 목표 팔레트 수가 있으면 박스를 고르게 나누되, 파레트당 최대 박스는 넘지 않게
      const totalBoxSum = baseRows.reduce((s, r) => s + (Number(r.box) || 0), 0)
      const cap = effectiveCap(totalBoxSum, palletCap, target)
      const { rows: distRows, count } = distributeByCapacity(baseRows, cap)
      setRows(distRows)
      setHeader({
        supplier: data.supplier || '',
        center: data.center || '',
        date: data.date || '',
        milkrun: data.milkrun || '',
        pallet: data.pallet || '1-1',
        pallet_total: Math.max(count, target),
      })
      setHasAttach(data.has_부착)
      setWarnings(data.warnings || [])
      setParsed(true)
      if (data.parse_errors?.length) {
        message.warning(`일부 파일을 인식하지 못했습니다: ${data.parse_errors.join(' / ')}`)
      } else {
        message.success(`거래명세서 ${data.rows.length}개 품목을 불러왔습니다.`)
      }
    } catch (e: any) {
      message.error('분석 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setParsing(false)
    }
  }

  function updateRow(idx: number, patch: Partial<CoupangLoadRow>) {
    setRows(prev => prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)))
  }

  function removeRow(idx: number) {
    setRows(prev => prev.filter((_, i) => i !== idx).map((r, i) => ({ ...r, no: i + 1 })))
  }

  function addRow() {
    setRows(prev => [...prev, { no: prev.length + 1, sku: '', name: '', box: 0, qty: 0, expire: '', pallet: 1 }])
  }

  async function handleGenerate() {
    if (rows.length === 0) return
    setGenerating(true)
    try {
      const blob = await generateCoupangLoad({
        ...header,
        pallet_total: slideCount,
        rows: rows.map(r => ({ ...r, box: Number(r.box) || 0, qty: Number(r.qty) || 0, pallet: clampPallet(r.pallet) })),
      })
      const center = header.center || '적재리스트'
      downloadBlob(blob, `${center}_적재리스트.pptx`)
      message.success('적재리스트 PPT를 생성했습니다.')
    } catch (e: any) {
      message.error('생성 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setGenerating(false)
    }
  }

  function reset() {
    setFiles([])
    setParsed(false)
    setRows([])
    setHeader(EMPTY_HEADER)
    setWarnings([])
    setPalletCap(settings?.pallet_cap || DEFAULT_PALLET_CAP)
    setAttachPalletTotal(1)
  }

  function handleAutoDistribute() {
    const { rows: distRows, count } = distributeByCapacity(rows, palletCap)
    setRows(distRows)
    setHeader(h => ({ ...h, pallet_total: count }))
    message.success(`팔레트당 최대 ${palletCap}박스 기준으로 ${count}개 팔레트에 분배했습니다.`)
  }

  const labelStyle = { fontSize: '0.78rem', color: '#6b7280', marginBottom: 4 }
  const cellInput = { width: '100%', border: '1px solid #e5e7eb', borderRadius: 6, padding: '3px 8px', fontSize: '0.82rem' }

  return (
    <div>
      <div className="page-header" style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ flex: 1 }}>
          <h1 className="page-title">쿠팡 적재리스트</h1>
          <p className="page-desc">거래명세서 · 부착리스트 PDF → 쿠팡 팔레트 적재리스트 PPT 자동 생성</p>
        </div>
        <Button icon={<SettingOutlined />} onClick={() => setShowSettings(v => !v)}>적재 설정</Button>
      </div>

      {/* 적재 설정 — 저장하면 다음 분석부터 자동 적용 */}
      {showSettings && settings && (
        <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #c7d2fe', padding: 16, marginBottom: 16 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>⚙️ 적재 설정</div>
          <div style={{ fontSize: '0.78rem', color: '#6b7280', marginBottom: 12 }}>
            저장하면 다음 분석부터 자동 적용됩니다. BOX 수량 계산 순서: ① 상품별 규칙 → ② 번들 표기(N개입·x3 → 수량=박스) → ③ 기본 1박스당 수량
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
                  { value: 'attach', label: '부착리스트 값 사용' },
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
            <span style={{ fontWeight: 400, color: '#6b7280' }}>· 상품번호(전체 일치) 또는 상품명 키워드(포함) — 위 규칙부터 먼저 적용</span>
          </div>
          {settings.rules.map((rule, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 6, alignItems: 'center' }}>
              <Input
                size="small" style={{ flex: 1 }} placeholder="상품번호 또는 상품명 키워드 (예: 스내피)"
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

      {/* Flow bar */}
      <div className="flow-bar">
        {[
          { n: '1', label: '📄 서류 업로드', done: files.length > 0 },
          { n: '2', label: '🔍 분석', done: parsed },
          { n: '3', label: '✏️ 확인·수정', done: parsed && rows.length > 0 },
          { n: '4', label: '📊 PPT 생성', done: false },
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
        <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10, borderBottom: '2px solid #10b981', paddingBottom: 8 }}>
          📄 서류 업로드 <span style={{ textTransform: 'none', fontWeight: 400, marginLeft: 6 }}>거래명세서(필수) · 부착리스트(선택) PDF</span>
        </div>
        <Upload.Dragger
          accept=".pdf"
          showUploadList={false}
          multiple
          beforeUpload={handleSelect}
          style={{ background: '#fafafa', borderColor: '#d1d5db' }}
        >
          <p style={{ fontSize: '1.5rem', margin: '8px 0' }}>📑</p>
          <p style={{ fontSize: '0.82rem', color: '#6b7280' }}>PDF 파일을 드래그하거나 클릭 (여러 개 가능)</p>
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
          {warnings.length > 0 && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16, fontSize: '0.82rem' }}
              message="거래명세서 ↔ 부착리스트 정보 불일치"
              description={<ul style={{ margin: 0, paddingLeft: 18 }}>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
            />
          )}
          {!hasAttach && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16, fontSize: '0.82rem' }}
              message="부착리스트가 없어 입고요청서번호·팔레트번호를 채우지 못했습니다. 아래에서 직접 입력하세요."
            />
          )}

          {/* Header fields */}
          <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, marginBottom: 16 }}>
            <div style={{ fontWeight: 700, marginBottom: 12 }}>📋 기본 정보</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
              <div>
                <div style={labelStyle}>업체명</div>
                <Input value={header.supplier} onChange={e => setHeader({ ...header, supplier: e.target.value })} size="small" />
              </div>
              <div>
                <div style={labelStyle}>납품센터명</div>
                <Input value={header.center} onChange={e => setHeader({ ...header, center: e.target.value })} size="small" />
              </div>
              <div>
                <div style={labelStyle}>입고예정일자 (YYYY-MM-DD)</div>
                <Input value={header.date} onChange={e => setHeader({ ...header, date: e.target.value })} placeholder="2026-06-30" size="small" />
              </div>
              <div>
                <div style={labelStyle}>입고요청서번호 (밀크런 번호)</div>
                <Input value={header.milkrun} onChange={e => setHeader({ ...header, milkrun: e.target.value })} size="small" />
              </div>
              <div>
                <div style={labelStyle}>총 팔레트 수 (= 슬라이드 장수)</div>
                <Input
                  type="number"
                  min={1}
                  value={header.pallet_total}
                  onChange={e => setHeader({ ...header, pallet_total: Math.max(1, Number(e.target.value) || 1) })}
                  size="small"
                />
              </div>
            </div>
            <div style={{ marginTop: 12, fontSize: '0.8rem', color: '#374151' }}>
              📊 <b>{slideCount}장</b>의 슬라이드가 생성됩니다 (팔레트 {slideCount}개)
              {hasAttach && attachPalletTotal !== slideCount && (
                <span style={{ color: '#b45309' }}>
                  {' '}⚠️ 부착리스트 기준은 {attachPalletTotal}개입니다 — 용량(팔레트당 {palletCap}박스)으로 계산한 값과 다릅니다. 확인하세요.
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

          {/* SKU table */}
          <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12, gap: 8 }}>
              <span style={{ flex: 1, fontWeight: 700 }}>
                📦 적재 품목 <span style={{ fontSize: '0.8rem', color: '#6b7280', fontWeight: 400 }}>
                  · BOX 수량은 저장된 적재 설정(기본 {settings?.default_per_box ?? 9}개입 · 상품 규칙 {settings?.rules.length ?? 0}개)으로 자동 계산 — 수정 후 📌을 누르면 다음부터 자동 적용
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
                  <th style={{ padding: '8px 10px', width: 40, color: '#6b7280', fontWeight: 600 }}>NO</th>
                  <th style={{ padding: '8px 10px', width: 110, textAlign: 'left', color: '#6b7280', fontWeight: 600 }}>상품번호</th>
                  <th style={{ padding: '8px 10px', textAlign: 'left', color: '#6b7280', fontWeight: 600 }}>상품명 + 옵션명</th>
                  <th style={{ padding: '8px 10px', width: 90, color: '#10b981', fontWeight: 700 }}>BOX 수량</th>
                  <th style={{ padding: '8px 10px', width: 80, color: '#6b7280', fontWeight: 600 }}>수량</th>
                  <th style={{ padding: '8px 10px', width: 130, textAlign: 'left', color: '#6b7280', fontWeight: 600 }}>유통기한/제조일자</th>
                  {slideCount > 1 && <th style={{ padding: '8px 10px', width: 80, color: '#7c3aed', fontWeight: 700 }}>팔레트</th>}
                  <th style={{ width: 72 }} />
                </tr>
              </thead>
              <tbody>
                {rows.map((r, idx) => (
                  <tr key={idx} style={{ borderBottom: '1px solid #f3f4f6' }}>
                    <td style={{ padding: '5px 10px', textAlign: 'center', color: '#374151' }}>{idx + 1}</td>
                    <td style={{ padding: '5px 10px' }}>
                      <input value={r.sku} onChange={e => updateRow(idx, { sku: e.target.value })} style={cellInput} />
                    </td>
                    <td style={{ padding: '5px 10px' }}>
                      <input value={r.name} onChange={e => updateRow(idx, { name: e.target.value })} style={cellInput} />
                    </td>
                    <td style={{ padding: '5px 10px' }}>
                      <input
                        type="number"
                        min={0}
                        value={r.box}
                        onChange={e => updateRow(idx, { box: Number(e.target.value) })}
                        style={{ ...cellInput, textAlign: 'center', borderColor: r.box ? '#a7f3d0' : '#fca5a5', background: r.box ? '#f0fdf4' : '#fef2f2' }}
                      />
                    </td>
                    <td style={{ padding: '5px 10px' }}>
                      <input type="number" min={0} value={r.qty} onChange={e => updateRow(idx, { qty: Number(e.target.value) })} style={{ ...cellInput, textAlign: 'center' }} />
                    </td>
                    <td style={{ padding: '5px 10px' }}>
                      <input value={r.expire} onChange={e => updateRow(idx, { expire: e.target.value })} style={cellInput} />
                    </td>
                    {slideCount > 1 && (
                      <td style={{ padding: '5px 10px' }}>
                        <input
                          type="number"
                          min={1}
                          max={slideCount}
                          value={r.pallet || 1}
                          onChange={e => updateRow(idx, { pallet: Math.min(slideCount, Math.max(1, Number(e.target.value) || 1)) })}
                          style={{ ...cellInput, textAlign: 'center', borderColor: '#ddd6fe', background: '#f5f3ff' }}
                        />
                      </td>
                    )}
                    <td style={{ padding: '5px 6px', whiteSpace: 'nowrap' }}>
                      <Tooltip title="이 상품의 1박스당 수량(수량÷박스)을 규칙으로 저장 — 다음부터 자동 적용">
                        <Button size="small" type="text" icon={<PushpinOutlined />} onClick={() => saveRowRule(r)} />
                      </Tooltip>
                      <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removeRow(idx)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div style={{ display: 'flex', gap: 12, marginTop: 14, alignItems: 'center' }}>
              <div style={{ background: '#ecfdf5', border: '1px solid #a7f3d0', borderRadius: 10, padding: '8px 16px', textAlign: 'center' }}>
                <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#065f46' }}>{totalBox}</div>
                <div style={{ fontSize: '0.72rem', fontWeight: 600, color: '#065f46', opacity: 0.8 }}>총 BOX</div>
              </div>
              <div style={{ background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 10, padding: '8px 16px', textAlign: 'center' }}>
                <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#1e40af' }}>{totalQty}</div>
                <div style={{ fontSize: '0.72rem', fontWeight: 600, color: '#1e40af', opacity: 0.8 }}>총 수량</div>
              </div>
              <div style={{ flex: 1 }} />
              <Button
                type="primary"
                size="large"
                icon={<FilePptOutlined />}
                onClick={handleGenerate}
                loading={generating}
                disabled={rows.length === 0}
              >
                <DownloadOutlined /> 적재리스트 PPT 생성
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
