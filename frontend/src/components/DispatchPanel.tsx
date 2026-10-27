import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Collapse, Input, InputNumber, Popconfirm, Select, Spin, Table, Tag, Tooltip, Upload, message } from 'antd'
import { DeleteOutlined, PaperClipOutlined, ThunderboltOutlined, UploadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import {
  deleteDispatchAttachment, deleteInvoiceSource, downloadDispatchInvoice, getDispatchProducts, getDispatchRefs, prepareDispatch, runDispatch, setDispatchConfig, uploadDispatchAttachment, uploadInvoiceSource,
  type DispatchInvoice, type DispatchLog, type DispatchPrepare, type DispatchRefs, type DispatchRow, type DispatchRunReq, type DispatchRunRes,
} from '../services/dispatchApi'
import { downloadBlob } from '../services/api'

// 슬랙 출고요청 1건 → 아워박스 발주등록 · BoxHero 출고등록 · 물류센터 메일 · 슬랙 ✅ 를 한 화면에서 검토 후 실행.
// prepare(읽기 전용)로 초안을 받고, 사람이 채널/거래처/상품매칭/메일을 고친 뒤 run(쓰기).

interface Props {
  channelId: string
  ts: string
  title: string
}

type Steps = { ourbox: boolean; oms: boolean; boxhero: boolean; mail: boolean; slack_done: boolean }
const EMPTY_INVOICE: DispatchInvoice = { enabled: false, recipient: '', trade_name: '', issue_date: '', payment_terms: '', doc_number: '', items: [] }

function scoreTag(score: number, via?: string) {
  if (via === '학습' || via === 'product_mapping' || via === 'name_mapping') return <Tag color="blue" style={{ marginLeft: 4 }}>{via}</Tag>
  if (score >= 100) return <Tag color="green" style={{ marginLeft: 4 }}>일치</Tag>
  if (score >= 80) return <Tag color="cyan" style={{ marginLeft: 4 }}>{score}</Tag>
  if (score >= 60) return <Tag color="orange" style={{ marginLeft: 4 }}>{score} 확인</Tag>
  return <Tag color="red" style={{ marginLeft: 4 }}>미매칭</Tag>
}

/** 원격 검색 셀렉트 (아워박스 상품 / BH 상품) — 현재 값은 옵션에 항상 포함 */
function ProductSelect({ kind, value, label, onChange }: {
  kind: 'ob' | 'bh'; value: string; label: string; onChange: (v: string, l: string) => void
}) {
  const [opts, setOpts] = useState<{ value: string; label: string }[]>(value ? [{ value, label }] : [])
  const [loading, setLoading] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  const search = (q: string) => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(async () => {
      setLoading(true)
      try {
        const r = await getDispatchProducts(q)
        const list = kind === 'ob'
          ? r.ob.map(p => ({ value: p.code, label: `${p.name}${p.item_type === 'set' ? ' [세트]' : ''}` }))
          : r.bh.map(p => ({ value: p.sku, label: `${p.name} (${p.sku})` }))
        setOpts(value && !list.some(o => o.value === value) ? [{ value, label }, ...list] : list)
      } catch { /* ignore */ } finally { setLoading(false) }
    }, 250)
  }
  return (
    <Select
      size="small" showSearch value={value || undefined} placeholder={kind === 'ob' ? '아워박스 상품 검색' : 'BH 상품 검색'}
      style={{ width: '100%' }} filterOption={false} onSearch={search} onFocus={() => opts.length <= 1 && search('')}
      notFoundContent={loading ? <Spin size="small" /> : '검색 결과 없음'} options={opts} allowClear
      onChange={(v, o) => onChange(v || '', (o as any)?.label || '')}
    />
  )
}

/** 메일 기본값(받는사람·참조·제목 템플릿·인사말·서명·BH 메모 템플릿) — config.json 에 저장 */
const SETTING_FIELDS: [string, string, 'input' | 'area'][] = [
  ['dispatch_mail_to', '받는사람 (쉼표 구분)', 'input'],
  ['dispatch_mail_cc', '참조 (쉼표 구분)', 'area'],
  ['dispatch_mail_subject', '제목 템플릿 — {tag} {date} {title} 사용 가능', 'input'],
  ['dispatch_mail_greeting', '인사말 (본문 첫 줄)', 'area'],
  ['dispatch_mail_signature', '서명 (본문 끝 "--" 아래)', 'area'],
  ['dispatch_bh_memo', 'BoxHero 메모 템플릿 — {partner} {date_kr} {title}', 'input'],
  ['dispatch_default_tag', '메일 제목 기본 태그 (운송 규칙에 안 걸릴 때)', 'input'],
]

function SettingsBox({ refs, onSaved }: { refs: DispatchRefs | null; onSaved: (s: Record<string, any>) => void }) {
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    if (!refs) return
    const d: Record<string, string> = {}
    for (const [k] of SETTING_FIELDS) d[k] = String(refs.settings[k] ?? '')
    setDraft(d)
  }, [refs])
  async function save() {
    setSaving(true)
    try { onSaved(await setDispatchConfig(draft)); message.success('설정 저장 — 다음 "초안 준비"부터 반영') }
    catch (e: any) { message.error('저장 실패: ' + (e.response?.data?.detail || e.message)) }
    finally { setSaving(false) }
  }
  return (
    <Collapse size="small" style={{ background: '#fff', marginTop: 8 }} items={[{
      key: 'settings',
      label: <span style={{ fontSize: '0.76rem' }}>⚙️ 메일·메모 기본값 설정</span>,
      children: (
        <div style={{ display: 'grid', gap: 6 }}>
          {SETTING_FIELDS.map(([k, label, kind]) => (
            <label key={k} style={{ fontSize: '0.72rem', color: '#374151' }}>{label}
              {kind === 'area'
                ? <Input.TextArea size="small" value={draft[k] || ''} autoSize={{ minRows: 1, maxRows: 6 }} onChange={e => setDraft({ ...draft, [k]: e.target.value })} />
                : <Input size="small" value={draft[k] || ''} onChange={e => setDraft({ ...draft, [k]: e.target.value })} />}
            </label>
          ))}
          <div style={{ fontSize: '0.7rem', color: '#9ca3af' }}>채널·거래처·메일 태그 규칙(키워드 → 아워박스 채널/BH 거래처)은 config.json 의 dispatch_rules / dispatch_tag_rules 에서 조정합니다.</div>
          <Button size="small" type="primary" loading={saving} onClick={save} style={{ justifySelf: 'end' }}>저장</Button>
        </div>
      ),
    }]} />
  )
}

export default function DispatchPanel({ channelId, ts, title }: Props) {
  const [refs, setRefs] = useState<DispatchRefs | null>(null)
  const [prep, setPrep] = useState<DispatchPrepare | null>(null)
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<DispatchRunRes | null>(null)
  const [rows, setRows] = useState<DispatchRow[]>([])
  const [obChannel, setObChannel] = useState<string>('')
  const [orderPrefix, setOrderPrefix] = useState<string>('')
  const [bhPartner, setBhPartner] = useState<number | null>(null)
  const [bhLocation, setBhLocation] = useState<number | null>(null)
  const [bhMemo, setBhMemo] = useState('')
  const [mailTo, setMailTo] = useState<string[]>([])
  const [mailCc, setMailCc] = useState<string[]>([])
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [attachSlack, setAttachSlack] = useState(true)
  const [attachments, setAttachments] = useState<string[]>([])
  const [steps, setSteps] = useState<Steps>({ ourbox: true, oms: true, boxhero: true, mail: true, slack_done: true })
  const [force, setForce] = useState(false)
  const [invoice, setInvoice] = useState<DispatchInvoice>(EMPTY_INVOICE)

  // 메시지가 바뀌면 초안 초기화
  useEffect(() => { setPrep(null); setResult(null); setRows([]) }, [ts])

  const log: DispatchLog | null = useMemo(() => {
    const l = (result?.log || prep?.log) as DispatchLog | undefined
    return l && l.ts ? l : null
  }, [prep, result])
  const done = { ourbox: !!log?.ob?.ok, oms: !!log?.oms?.ok, boxhero: !!log?.bh_tx_id, mail: !!log?.mail_id }

  function buildReq(): DispatchRunReq | null {
    if (!prep) return null
    return {
      channel_id: channelId, ts, title: prep.title, steps, force,
      rows: rows.map(r => ({ order_no: r.order_no, name: r.name, qty: r.qty, receiver: r.receiver, tel: r.tel, zip: r.zip, addr: r.addr, memo: r.memo, ob_code: r.ob_code, ob_name: r.ob_name, bh_sku: r.bh_sku, unit_price: r.unit_price || 0 })),
      ob_channel_code: obChannel, order_prefix: orderPrefix, bh_partner_id: bhPartner, bh_location_id: bhLocation, bh_memo: bhMemo,
      mail: { to: mailTo, cc: mailCc, subject, body, attach_slack_files: attachSlack },
      invoice,
    }
  }

  async function previewInvoice() {
    const req = buildReq()
    if (!req) return
    try {
      const blob = await downloadDispatchInvoice(req)
      downloadBlob(blob, `거래명세서_${invoice.recipient || '거래처'}_${invoice.issue_date}.xlsx`)
    } catch (e: any) { message.error('거래명세서 생성 실패: ' + (e.response?.data?.detail || e.message)) }
  }

  async function prepare() {
    setLoading(true)
    try {
      const [r, p] = await Promise.all([refs ? Promise.resolve(refs) : getDispatchRefs(), prepareDispatch(channelId, ts)])
      setRefs(r); setPrep(p); setResult(null)
      setRows(p.rows)
      setObChannel(p.suggest.ob_channel_code || '')
      setOrderPrefix(['무상', '협찬', '샘플', '마케팅', 'PPL'].includes(p.suggest.rule) ? '무상' : '')
      setBhPartner(p.suggest.bh_partner_id ?? null)
      setBhLocation(r.bh_location_default ?? (r.bh_locations[0]?.id ?? null))
      setBhMemo(p.bh_memo)
      setMailTo(p.mail.to); setMailCc(p.mail.cc); setSubject(p.mail.subject); setBody(p.mail.body)
      setAttachments(p.attachments)
      setInvoice(p.invoice || EMPTY_INVOICE)
      const l = p.log as DispatchLog
      setSteps({ ourbox: !l?.ob?.ok, oms: !l?.oms?.ok, boxhero: !l?.bh_tx_id, mail: !l?.mail_id && r.gmail.can_send, slack_done: true })
      if (r.errors.length) message.warning(r.errors.join(' / '))
    } catch (e: any) {
      message.error('초안 준비 실패: ' + (e.response?.data?.detail || e.message))
    } finally { setLoading(false) }
  }

  function patchRow(idx: number, p: Partial<DispatchRow>) {
    setRows(prev => prev.map(r => r.idx === idx ? { ...r, ...p } : r))
  }
  function patchOrder(orderNo: string, p: Partial<DispatchRow>) {
    // 같은 주문번호의 수취인/주소는 함께 바뀐다
    setRows(prev => prev.map(r => r.order_no === orderNo ? { ...r, ...p } : r))
  }
  function addRow() {
    const idx = rows.length ? Math.max(...rows.map(r => r.idx)) + 1 : 0
    const base = rows[0]
    setRows([...rows, { idx, order_no: base?.order_no || `${(prep?.req_date || new Date().toISOString().slice(0, 10)).replace(/-/g, '').slice(2)}_${idx + 1}`,
      name: '', qty: 1, receiver: base?.receiver || '', tel: base?.tel || '', zip: base?.zip || '', addr: base?.addr || '', memo: '',
      ob_code: '', ob_name: '', ob_score: 0, bh_sku: '', bh_name: '', bh_score: 0 }])
  }

  async function run() {
    if (!prep) return
    const want = Object.entries(steps).filter(([, v]) => v).map(([k]) => k)
    if (!want.length) { message.info('실행할 단계를 선택하세요'); return }
    setRunning(true)
    try {
      const res = await runDispatch(buildReq()!)
      setResult(res)
      if (res.error) message.error('중단: ' + res.error)
      else message.success('자동처리 완료')
      const l = res.log
      setSteps({ ourbox: !l?.ob?.ok, oms: !l?.oms?.ok, boxhero: !l?.bh_tx_id, mail: !l?.mail_id && !!refs?.gmail.can_send, slack_done: true })
      setForce(false)
    } catch (e: any) {
      message.error('실행 실패: ' + (e.response?.data?.detail || e.message))
    } finally { setRunning(false) }
  }

  async function upload(file: File) {
    try { setAttachments((await uploadDispatchAttachment(ts, file)).attachments); message.success(`첨부: ${file.name}`) }
    catch (e: any) { message.error('업로드 실패: ' + (e.response?.data?.detail || e.message)) }
    return false
  }
  async function removeAttachment(name: string) {
    try { setAttachments((await deleteDispatchAttachment(ts, name)).attachments) } catch { /* ignore */ }
  }
  // 올리브영 납품확인서/채널 발주서 → 전표 품목 (그때그때 다른 코드·상품명을 그 파일 기준으로)
  async function uploadInvoiceSrc(file: File) {
    try {
      const r = await uploadInvoiceSource(ts, file)
      setInvoice(v => ({ ...v, enabled: true, items: r.items }))
      message.success(`전표 품목 ${r.items.length}종 읽음 (${r.source})`)
    } catch (e: any) { message.error('전표 파일 읽기 실패: ' + (e.response?.data?.detail || e.message)) }
    return false
  }
  async function clearInvoiceSrc() {
    try { await deleteInvoiceSource(ts) } catch { /* ignore */ }
    setInvoice(v => ({ ...v, items: [] }))
  }
  function patchInvoiceItem(i: number, p: Partial<DispatchInvoice['items'][number]>) {
    setInvoice(v => ({ ...v, items: v.items.map((it, j) => j === i ? { ...it, ...p } : it) }))
  }

  const totalQty = rows.reduce((s, r) => s + (Number(r.qty) || 0), 0)
  const orderNos = Array.from(new Set(rows.map(r => r.order_no)))
  const unmatched = rows.filter(r => (steps.ourbox && !r.ob_code) || (steps.boxhero && !r.bh_sku)).length
  const missingAddr = steps.ourbox && rows.some(r => !r.zip || !r.addr || !r.receiver)

  const columns: ColumnsType<DispatchRow> = [
    { title: '주문번호', dataIndex: 'order_no', width: 110, render: (v, r) => <Input size="small" value={v} onChange={e => patchRow(r.idx, { order_no: e.target.value })} /> },
    { title: '요청 상품명', dataIndex: 'name', width: 190, render: (v, r) => <Input size="small" value={v} onChange={e => patchRow(r.idx, { name: e.target.value })} /> },
    { title: '수량', dataIndex: 'qty', width: 80, render: (v, r) => <InputNumber size="small" min={1} value={v} onChange={n => patchRow(r.idx, { qty: Number(n) || 0 })} style={{ width: 70 }} /> },
    { title: '아워박스 상품', width: 260, render: (_, r) => (
      <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
        <ProductSelect kind="ob" value={r.ob_code} label={r.ob_name} onChange={(v, l) => patchRow(r.idx, { ob_code: v, ob_name: l, ob_score: v ? 100 : 0, ob_via: v ? '수동' : '' })} />
        {scoreTag(r.ob_score, r.ob_via)}
      </div>) },
    { title: 'BoxHero 상품', width: 260, render: (_, r) => (
      <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
        <ProductSelect kind="bh" value={r.bh_sku} label={r.bh_name ? `${r.bh_name} (${r.bh_sku})` : r.bh_sku} onChange={(v, l) => patchRow(r.idx, { bh_sku: v, bh_name: l, bh_score: v ? 100 : 0, bh_via: v ? '수동' : '' })} />
        {scoreTag(r.bh_score, r.bh_via)}
      </div>) },
    { title: <Tooltip title="거래명세서 단가 (부가세 별도). 거래명세서를 첨부할 때만 사용">단가</Tooltip>, dataIndex: 'unit_price', width: 95, render: (v, r) => <InputNumber size="small" min={0} value={v || 0} onChange={n => patchRow(r.idx, { unit_price: Number(n) || 0 })} style={{ width: 88 }} /> },
    { title: '수취인', dataIndex: 'receiver', width: 90, render: (v, r) => <Input size="small" value={v} onChange={e => patchOrder(r.order_no, { receiver: e.target.value })} /> },
    { title: '연락처', dataIndex: 'tel', width: 120, render: (v, r) => <Input size="small" value={v} onChange={e => patchOrder(r.order_no, { tel: e.target.value })} /> },
    { title: '우편번호', dataIndex: 'zip', width: 85, render: (v, r) => <Input size="small" value={v} status={!v && steps.ourbox ? 'error' : undefined} onChange={e => patchOrder(r.order_no, { zip: e.target.value })} /> },
    { title: '주소', dataIndex: 'addr', render: (v, r) => <Input size="small" value={v} onChange={e => patchOrder(r.order_no, { addr: e.target.value })} /> },
    { title: '', width: 36, render: (_, r) => <Button size="small" type="text" icon={<DeleteOutlined />} onClick={() => setRows(prev => prev.filter(x => x.idx !== r.idx))} /> },
  ]

  const statusChips = log && (
    <span style={{ display: 'inline-flex', gap: 4, marginLeft: 8 }}>
      <Tag color={log.ob?.ok ? 'green' : 'default'}>아워박스 {log.ob?.ok ? `✓ ${log.ob.company_order_codes.length}건` : '-'}</Tag>
      <Tag color={log.oms?.ok ? 'green' : 'default'}>OMS 출고등록 {log.oms?.ok ? '✓' : '-'}</Tag>
      <Tag color={log.bh_tx_id ? 'green' : 'default'}>BoxHero {log.bh_tx_id ? `✓ tx ${log.bh_tx_id}` : '-'}</Tag>
      <Tag color={log.mail_id ? 'green' : 'default'}>메일 {log.mail_id ? '✓' : '-'}</Tag>
    </span>
  )

  return (
    <div style={{ marginBottom: 12, border: '1px solid #c7d2fe', background: '#f5f3ff', borderRadius: 10, padding: '10px 14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <ThunderboltOutlined style={{ color: '#6d28d9' }} />
        <span style={{ fontSize: '0.82rem', fontWeight: 700, color: '#4c1d95' }}>출고 자동처리</span>
        <span style={{ fontSize: '0.72rem', color: '#6b7280' }}>아워박스 발주등록 → BoxHero 출고 → 물류센터 메일 → 슬랙 ✅</span>
        {statusChips}
        <Button size="small" type={prep ? 'default' : 'primary'} loading={loading} onClick={prepare} style={{ marginLeft: 'auto' }}>
          {prep ? '초안 다시 준비' : '초안 준비'}
        </Button>
      </div>

      {prep && (
        <div style={{ marginTop: 10 }}>
          {prep.warnings.length > 0 && (
            <Alert type="warning" showIcon style={{ marginBottom: 8 }} message={<ul style={{ margin: 0, paddingLeft: 16 }}>{prep.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>} />
          )}
          {refs && !refs.gmail.can_send && (
            <Alert type="info" showIcon style={{ marginBottom: 8 }}
              message={refs.gmail.connected ? 'Gmail에 보내기 권한이 없습니다 — 설정 페이지에서 Gmail을 다시 연결하면(gmail.send 동의) 메일 발송 단계가 열립니다.' : 'Gmail이 연결되지 않아 메일 발송 단계는 꺼져 있습니다.'} />
          )}

          {/* 1. 아워박스 / BoxHero 설정 */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: 8, marginBottom: 8 }}>
            <label style={{ fontSize: '0.74rem', color: '#374151' }}>아워박스 채널 {prep.suggest.rule && <span style={{ color: '#9ca3af' }}>(규칙: {prep.suggest.rule})</span>}
              <Select size="small" showSearch value={obChannel || undefined} onChange={setObChannel} style={{ width: '100%' }} placeholder="판매채널"
                optionFilterProp="label" options={(refs?.ob_channels || []).map(c => ({ value: c.code, label: `${c.name} [${c.type}]` }))} />
            </label>
            <label style={{ fontSize: '0.74rem', color: '#374151' }}>주문번호 접두어 <span style={{ color: '#9ca3af' }}>(예: 무상 → 무상261026_1)</span>
              <Input size="small" value={orderPrefix} onChange={e => setOrderPrefix(e.target.value)} />
            </label>
            <label style={{ fontSize: '0.74rem', color: '#374151' }}>BoxHero 거래처
              <Select size="small" showSearch allowClear value={bhPartner ?? undefined} onChange={v => setBhPartner(v ?? null)} style={{ width: '100%' }}
                optionFilterProp="label" options={(refs?.bh_partners || []).map(p => ({ value: p.id, label: p.name }))} />
            </label>
            <label style={{ fontSize: '0.74rem', color: '#374151' }}>BoxHero 출고 위치
              <Select size="small" value={bhLocation ?? undefined} onChange={v => setBhLocation(v)} style={{ width: '100%' }}
                options={(refs?.bh_locations || []).map(l => ({ value: l.id, label: l.name }))} />
            </label>
            <label style={{ fontSize: '0.74rem', color: '#374151', gridColumn: '1 / -1' }}>BoxHero 메모
              <Input size="small" value={bhMemo} onChange={e => setBhMemo(e.target.value)} />
            </label>
          </div>

          {/* 2. 품목 */}
          <div style={{ fontSize: '0.76rem', fontWeight: 700, color: '#374151', margin: '6px 0 4px' }}>
            품목 {rows.length}줄 · 주문 {orderNos.length}건 · {totalQty.toLocaleString()}개
            <span style={{ fontWeight: 400, color: '#9ca3af' }}> · 출처: {prep.source === 'excel' ? '첨부 엑셀(주문내역)' : prep.source === 'text' ? '메시지 품목 섹션(추정)' : '없음'}</span>
            <Button size="small" type="link" onClick={addRow}>+ 줄 추가</Button>
          </div>
          <Table size="small" rowKey="idx" columns={columns} dataSource={rows} pagination={rows.length > 20 ? { pageSize: 20, size: 'small' } : false}
            scroll={{ x: 1300 }} style={{ background: '#fff', borderRadius: 8, marginBottom: 8 }} />

          {/* 3. 메일 */}
          <Collapse size="small" style={{ background: '#fff', marginBottom: 8 }} defaultActiveKey={[]} items={[{
            key: 'mail',
            label: <span style={{ fontSize: '0.78rem' }}>✉️ 물류센터 메일 — <b>{subject}</b> <span style={{ color: '#9ca3af' }}>(받는사람 {mailTo.length} · 참조 {mailCc.length} · 첨부 {(attachSlack ? prep.files.length : 0) + attachments.length})</span></span>,
            children: (
              <div style={{ display: 'grid', gap: 6 }}>
                <label style={{ fontSize: '0.72rem', color: '#374151' }}>받는사람
                  <Select mode="tags" size="small" value={mailTo} onChange={setMailTo} style={{ width: '100%' }} tokenSeparators={[',', ' ', ';']} />
                </label>
                <label style={{ fontSize: '0.72rem', color: '#374151' }}>참조
                  <Select mode="tags" size="small" value={mailCc} onChange={setMailCc} style={{ width: '100%' }} tokenSeparators={[',', ' ', ';']} />
                </label>
                <label style={{ fontSize: '0.72rem', color: '#374151' }}>제목
                  <Input size="small" value={subject} onChange={e => setSubject(e.target.value)} />
                </label>
                <Input.TextArea value={body} onChange={e => setBody(e.target.value)} autoSize={{ minRows: 8, maxRows: 24 }} style={{ fontSize: '0.78rem' }} />
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: '0.74rem' }}>
                  <Checkbox checked={attachSlack} onChange={e => setAttachSlack(e.target.checked)}>슬랙 첨부파일 같이 보내기 ({prep.files.map(f => f.name).join(', ') || '없음'})</Checkbox>
                  <Upload beforeUpload={upload} showUploadList={false} multiple>
                    <Button size="small" icon={<UploadOutlined />}>거래명세서·서류 첨부</Button>
                  </Upload>
                  {attachments.map(a => (
                    <Tag key={a} icon={<PaperClipOutlined />} closable onClose={() => removeAttachment(a)}>{a}</Tag>
                  ))}
                </div>
                {/* 거래명세서 자동 생성 (앱 양식: 수신·건명·일자·결제조건 + 품명/수량/단가) */}
                <div style={{ border: '1px dashed #c7d2fe', borderRadius: 8, padding: '8px 10px', display: 'grid', gap: 6 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: '0.74rem' }}>
                    <Checkbox checked={invoice.enabled} onChange={e => setInvoice({ ...invoice, enabled: e.target.checked })}><b>📄 거래명세서 자동 생성해서 첨부</b></Checkbox>
                    <Upload beforeUpload={uploadInvoiceSrc} showUploadList={false} accept=".xlsx,.xls">
                      <Button size="small" icon={<UploadOutlined />}>올영 납품확인서·채널 발주서 올리기</Button>
                    </Upload>
                    <Button size="small" onClick={previewInvoice}>미리보기 다운로드</Button>
                    <span style={{ color: '#9ca3af' }}>양식: 씨제이이엔엠 거래명세서 · 전표 품목은 올린 파일의 상품코드·상품명·수량 기준 (없으면 요청 품목 + 기억된 코드)</span>
                  </div>
                  {invoice.enabled && (
                    <>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 6 }}>
                        {([['recipient', '수신(거래처)'], ['trade_name', '거래 건명'], ['issue_date', '거래일자'], ['payment_terms', '유효기간/결제조건'], ['doc_number', '관리번호(비우면 날짜)']] as [keyof DispatchInvoice, string][]).map(([k, label]) => (
                          <label key={k} style={{ fontSize: '0.72rem', color: '#374151' }}>{label}
                            <Input size="small" value={String(invoice[k] ?? '')} onChange={e => setInvoice({ ...invoice, [k]: e.target.value })} />
                          </label>
                        ))}
                      </div>
                      {invoice.items.length > 0 ? (
                        <div style={{ fontSize: '0.72rem' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '4px 0' }}>
                            <b>전표 품목 {invoice.items.length}종</b> <span style={{ color: '#9ca3af' }}>(채널 파일 기준 — 수량은 이번 출고분으로 고쳐 주세요)</span>
                            <Button size="small" type="link" onClick={clearInvoiceSrc}>지우고 요청 품목으로</Button>
                          </div>
                          <Table size="small" rowKey={(_, i) => String(i)} pagination={false} dataSource={invoice.items} columns={[
                            { title: '상품코드(바코드)', dataIndex: 'code', width: 150, render: (v, _, i) => <Input size="small" value={v} onChange={e => patchInvoiceItem(i, { code: e.target.value })} /> },
                            { title: '상품명', dataIndex: 'name', render: (v, _, i) => <Input size="small" value={v} onChange={e => patchInvoiceItem(i, { name: e.target.value })} /> },
                            { title: '수량', dataIndex: 'qty', width: 90, render: (v, _, i) => <InputNumber size="small" min={0} value={v} onChange={n => patchInvoiceItem(i, { qty: Number(n) || 0 })} style={{ width: 80 }} /> },
                            { title: '단가', dataIndex: 'price', width: 100, render: (v, _, i) => <InputNumber size="small" min={0} value={v || 0} onChange={n => patchInvoiceItem(i, { price: Number(n) || 0 })} style={{ width: 90 }} /> },
                            { title: '', width: 36, render: (_, __, i) => <Button size="small" type="text" icon={<DeleteOutlined />} onClick={() => setInvoice(v => ({ ...v, items: v.items.filter((_, j) => j !== i) }))} /> },
                          ]} />
                        </div>
                      ) : (
                        <div style={{ fontSize: '0.72rem', color: '#b45309' }}>채널 전표 파일이 없어 요청 품목(아워박스 상품명)과 기억된 채널 코드로 만듭니다. 올리브영 건은 납품확인서를 올려 주세요.</div>
                      )}
                    </>
                  )}
                </div>
              </div>
            ),
          }]} />

          {/* 4. 실행 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', fontSize: '0.78rem' }}>
            {([
              ['ourbox', '아워박스 발주등록'], ['oms', 'OMS 단계진행 → WMS 출고등록'], ['boxhero', 'BoxHero 출고등록'], ['mail', '메일 발송'], ['slack_done', '슬랙 ✅ + 스레드 답글'],
            ] as [keyof Steps, string][]).map(([k, label]) => {
              const isDone = k !== 'slack_done' && done[k as keyof typeof done]
              const disabled = (k === 'mail' && !refs?.gmail.can_send) || (isDone && !force)
              return (
                <Tooltip key={k} title={isDone ? '이미 완료된 단계 (다시 실행하려면 아래 "완료 단계도 다시 실행" 체크)' : ''}>
                  <Checkbox checked={steps[k]} disabled={disabled} onChange={e => setSteps({ ...steps, [k]: e.target.checked })}>
                    {label} {isDone && <Tag color="green" style={{ marginLeft: 2 }}>완료</Tag>}
                  </Checkbox>
                </Tooltip>
              )
            })}
            {(done.ourbox || done.boxhero || done.mail) && (
              <Checkbox checked={force} onChange={e => setForce(e.target.checked)} style={{ color: '#b45309' }}>완료 단계도 다시 실행(중복 주의)</Checkbox>
            )}
            <Popconfirm
              title="실제로 실행합니다"
              description={<div style={{ fontSize: '0.78rem' }}>
                {steps.ourbox && <div>· 아워박스에 주문 {orderNos.length}건 발주등록 (채널 {refs?.ob_channels.find(c => c.code === obChannel)?.name || obChannel})</div>}
                {steps.oms && <div>· OMS 주문서처리 → 출고준비 → WMS 출고등록 (저장 세션 사용, 수 분 소요)</div>}
                {steps.boxhero && <div>· BoxHero 출고 1건 ({rows.length}종 {totalQty.toLocaleString()}개, 거래처 {refs?.bh_partners.find(p => p.id === bhPartner)?.name || '없음'})</div>}
                {steps.mail && <div>· 메일 발송: {mailTo.join(', ')} (참조 {mailCc.length})</div>}
                {steps.slack_done && <div>· 슬랙 메시지에 ✅ + 스레드 답글</div>}
              </div>}
              okText="실행" cancelText="취소" onConfirm={run} disabled={unmatched > 0 || missingAddr || running}
            >
              <Button type="primary" loading={running} disabled={unmatched > 0 || missingAddr} style={{ marginLeft: 'auto' }}>
                🚀 실행
              </Button>
            </Popconfirm>
          </div>
          {(unmatched > 0 || missingAddr) && (
            <div style={{ fontSize: '0.72rem', color: '#dc2626', marginTop: 4 }}>
              {unmatched > 0 && `매칭 안 된 품목 ${unmatched}줄 — 상품을 선택하거나 줄을 삭제하세요. `}
              {missingAddr && '수취인·우편번호·주소가 빈 주문이 있습니다 (아워박스 발주 필수).'}
            </div>
          )}

          <SettingsBox refs={refs} onSaved={s => setRefs(r => r ? { ...r, settings: s } : r)} />

          {result && (
            <Alert type={result.error ? 'error' : 'success'} showIcon style={{ marginTop: 8 }}
              message={result.error ? `중단: ${result.error}` : '완료'}
              description={<div style={{ fontSize: '0.76rem' }}>
                {result.ourbox && <div>아워박스 발주등록 {result.ourbox.company_order_codes.length}건 — {result.ourbox.company_order_codes.join(', ')} {result.ourbox.message}</div>}
                {result.oms && <div>OMS 단계진행 {result.oms.ok ? '완료 (WMS 출고등록)' : '미완료'} — {Object.entries(result.oms.final || {}).map(([c, v]) => `${c}: ${v.stage_nm}`).join(', ')}</div>}
                {result.boxhero && <div>BoxHero 출고 tx {result.boxhero.tx_id} ({result.boxhero.skus}종 {result.boxhero.qty.toLocaleString()}개)</div>}
                {result.mail && <div>메일 발송 완료 — {result.mail.subject} (첨부 {result.mail.attachments.length})</div>}
                {result.slack_done && <div>슬랙: {result.slack_done.ok ? '✅ 리액션 + 스레드 답글' : '답글 실패'}</div>}
                {result.skipped.length > 0 && <div style={{ color: '#6b7280' }}>건너뜀(이미 완료): {result.skipped.join(', ')}</div>}
                {result.warnings.map((w, i) => <div key={i} style={{ color: '#b45309' }}>{w}</div>)}
              </div>} />
          )}
        </div>
      )}
    </div>
  )
}
