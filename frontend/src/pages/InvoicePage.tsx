import { useState, useEffect } from 'react'
import {
  Button, Select, DatePicker, Table, Input, InputNumber,
  Modal, Form, message, Alert, Tag, Space, Popconfirm, Tooltip, Collapse,
} from 'antd'
import {
  PlusOutlined, DeleteOutlined, EditOutlined, DownloadOutlined,
  ReloadOutlined, FileTextOutlined, UserOutlined, UnorderedListOutlined,
} from '@ant-design/icons'
import type { Customer, InvoiceItem, SalesTransaction } from '../types'
import {
  getCustomers, createCustomer, updateCustomer, deleteCustomer,
  getSales, generateInvoice, downloadBlob,
} from '../services/api'
import dayjs from 'dayjs'

const S = {
  page: {
    padding: '28px 32px', flex: 1, overflowY: 'auto' as const,
    background: '#0f1117', color: '#e5e7eb', minHeight: '100vh',
  },
  card: {
    background: '#1a1d27', border: '1px solid #2e2f45', borderRadius: 10,
    padding: '20px 24px', marginBottom: 20,
  },
  sectionTitle: {
    color: '#9ca3af', fontSize: '0.72rem', fontWeight: 700,
    letterSpacing: '0.1em', textTransform: 'uppercase' as const,
    marginBottom: 14,
  },
  label: { color: '#9ca3af', fontSize: '0.8rem', marginBottom: 4 },
}

const EMPTY_CUSTOMER: Omit<Customer, 'id'> = {
  name: '', business_no: '', representative: '', address: '', phone: '', email: '',
}

export default function InvoicePage() {
  const [customers, setCustomers] = useState<Customer[]>([])
  const [selectedCustomerId, setSelectedCustomerId] = useState<string | null>(null)
  const [dateRange, setDateRange] = useState<[dayjs.Dayjs, dayjs.Dayjs] | null>(null)
  const [transactions, setTransactions] = useState<SalesTransaction[]>([])
  const [selectedTxId, setSelectedTxId] = useState<string | number | null>(null)
  const [items, setItems] = useState<InvoiceItem[]>([])
  const [issueDate, setIssueDate] = useState<dayjs.Dayjs>(dayjs())
  const [docNumber, setDocNumber] = useState(() => genDocNumber(dayjs()))

  function genDocNumber(d: dayjs.Dayjs) {
    const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    const rand = Array.from({ length: 4 }, () => chars[Math.floor(Math.random() * chars.length)]).join('')
    return `CINNAMONLAB${d.format('YYMMDD')}-${rand}`
  }
  const [tradeName, setTradeName] = useState('')
  const [paymentTerms, setPaymentTerms] = useState('')
  const [loadingSales, setLoadingSales] = useState(false)
  const [loadingGenerate, setLoadingGenerate] = useState(false)
  const [salesError, setSalesError] = useState<string | null>(null)

  // 거래처 관리 모달
  const [modalOpen, setModalOpen] = useState(false)
  const [editingCustomer, setEditingCustomer] = useState<Customer | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [form] = Form.useForm()

  useEffect(() => { loadCustomers() }, [])

  async function loadCustomers() {
    try {
      const data = await getCustomers()
      setCustomers(Array.isArray(data) ? data : [])
    } catch {
      message.error('거래처 목록 로드 실패')
    }
  }

  function openAddModal() {
    setEditingCustomer(null)
    form.setFieldsValue(EMPTY_CUSTOMER)
    setShowForm(true)
  }

  function openEditModal(c: Customer) {
    setEditingCustomer(c)
    form.setFieldsValue(c)
    setShowForm(true)
  }

  async function handleSaveCustomer() {
    try {
      const values = await form.validateFields()
      if (editingCustomer) {
        await updateCustomer(editingCustomer.id, values)
        message.success('거래처 수정 완료')
      } else {
        await createCustomer(values)
        message.success('거래처 추가 완료')
      }
      setShowForm(false)
      setEditingCustomer(null)
      loadCustomers()
    } catch {
      // validation error — form shows inline messages
    }
  }

  async function handleDeleteCustomer(id: string) {
    try {
      await deleteCustomer(id)
      if (selectedCustomerId === id) setSelectedCustomerId(null)
      message.success('거래처 삭제됨')
      loadCustomers()
    } catch {
      message.error('삭제 실패')
    }
  }

  async function handleFetchSales() {
    if (!dateRange) { message.warning('날짜 범위를 선택해주세요.'); return }
    setSalesError(null)
    setLoadingSales(true)
    setTransactions([])
    setItems([])
    try {
      const from = dateRange[0].format('YYYY-MM-DD')
      const to = dateRange[1].format('YYYY-MM-DD')
      const data = await getSales(from, to)
      const txs: SalesTransaction[] = data.transactions || []
      setTransactions(txs)
      setSelectedTxId(null)
      setItems([])
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? '판매 내역 조회 실패'
      setSalesError(msg)
    } finally {
      setLoadingSales(false)
    }
  }

  function selectTransaction(tx: SalesTransaction) {
    setSelectedTxId(tx.tx_id)
    setItems(tx.items.map(it => ({ ...it })))
  }

  function handleItemChange(index: number, field: keyof InvoiceItem, value: string | number) {
    setItems(prev => prev.map((it, i) => i === index ? { ...it, [field]: value } : it))
  }

  function addEmptyItem() {
    setItems(prev => [...prev, { item_name: '', sku: '', qty: 1, unit_price: 0, remark: '' }])
  }

  function removeItem(index: number) {
    setItems(prev => prev.filter((_, i) => i !== index))
  }

  async function handleGenerate() {
    if (!selectedCustomerId) { message.warning('거래처를 먼저 선택해주세요.'); return }
    if (items.length === 0) { message.warning('품목이 없습니다. 위 거래건을 클릭하거나 행을 추가해주세요.'); return }

    setLoadingGenerate(true)
    try {
      const blob = await generateInvoice({
        customer_id: selectedCustomerId,
        issue_date: issueDate.format('YYYY-MM-DD'),
        doc_number: docNumber || undefined,
        trade_name: tradeName || undefined,
        payment_terms: paymentTerms || undefined,
        items,
      })
      const customer = customers.find(c => c.id === selectedCustomerId)
      const filename = `거래명세서_${customer?.name ?? '거래처'}_${issueDate.format('YYYY-MM-DD')}.xlsx`
      downloadBlob(blob, filename)
      message.success('거래명세서가 다운로드되었습니다.')
    } catch (e: unknown) {
      const err = e as { response?: { data?: unknown } }
      let msg = '생성 실패'
      if (err?.response?.data instanceof Blob) {
        try {
          const text = await (err.response.data as Blob).text()
          const parsed = JSON.parse(text)
          msg = parsed?.detail ?? text
        } catch { msg = '생성 실패 (응답 파싱 오류)' }
      } else if (err?.response?.data) {
        msg = (err.response.data as { detail?: string })?.detail ?? msg
      }
      message.error(msg, 10)
    } finally {
      setLoadingGenerate(false)
    }
  }

  const totalSupply = items.reduce((s, it) => s + it.qty * it.unit_price, 0)
  const totalTax = Math.round(totalSupply * 0.1)

  const INPUT_STYLE = { borderColor: '#4b5563' }

  const itemColumns = [
    {
      title: '품명', dataIndex: 'item_name', width: '30%',
      render: (v: string, _: InvoiceItem, i: number) => (
        <Input
          value={v} size="small" style={INPUT_STYLE}
          onChange={e => handleItemChange(i, 'item_name', e.target.value)}
        />
      ),
    },
    {
      title: '수량', dataIndex: 'qty', width: '10%',
      render: (v: number, _: InvoiceItem, i: number) => (
        <InputNumber
          value={v} min={0} size="small"
          style={{ width: '100%', ...INPUT_STYLE }}
          onChange={val => handleItemChange(i, 'qty', val ?? 0)}
        />
      ),
    },
    {
      title: '단가 (원)', dataIndex: 'unit_price', width: '18%',
      render: (v: number, _: InvoiceItem, i: number) => (
        <InputNumber
          value={v} min={0} size="small"
          style={{ width: '100%', ...INPUT_STYLE }}
          formatter={(val) => `${val}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
          parser={(val) => Number((val ?? '').replace(/,/g, ''))}
          onChange={val => handleItemChange(i, 'unit_price', val ?? 0)}
        />
      ),
    },
    {
      title: '공급가액', width: '15%',
      render: (_: unknown, record: InvoiceItem) => (
        <span style={{ color: '#10b981', fontWeight: 600 }}>
          {(record.qty * record.unit_price).toLocaleString()}원
        </span>
      ),
    },
    {
      title: '비고', dataIndex: 'remark', width: '20%',
      render: (v: string, _: InvoiceItem, i: number) => (
        <Input
          value={v} size="small" placeholder="선택사항"
          style={INPUT_STYLE}
          onChange={e => handleItemChange(i, 'remark', e.target.value)}
        />
      ),
    },
    {
      title: '', width: '7%',
      render: (_: unknown, __: InvoiceItem, i: number) => (
        <Tooltip title="행 삭제">
          <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removeItem(i)} />
        </Tooltip>
      ),
    },
  ]

  const customerColumns = [
    { title: '상호', dataIndex: 'name', key: 'name' },
    { title: '사업자번호', dataIndex: 'business_no', key: 'business_no' },
    { title: '대표자', dataIndex: 'representative', key: 'representative' },
    { title: '주소', dataIndex: 'address', key: 'address', ellipsis: true },
    { title: '연락처', dataIndex: 'phone', key: 'phone' },
    {
      title: '', key: 'actions', width: 80,
      render: (_: unknown, c: Customer) => (
        <Space size={4}>
          <Button size="small" type="text" icon={<EditOutlined />} onClick={() => openEditModal(c)} />
          <Popconfirm title="삭제하시겠습니까?" onConfirm={() => handleDeleteCustomer(c.id)} okText="삭제" cancelText="취소">
            <Button size="small" type="text" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={S.page}>
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
          <FileTextOutlined style={{ color: '#10b981', fontSize: '1.3rem' }} />
          <span style={{ color: '#fff', fontSize: '1.25rem', fontWeight: 700 }}>거래명세서 생성</span>
        </div>
        <div style={{ color: '#6b7280', fontSize: '0.83rem' }}>
          박스히어로 판매 내역을 불러와 거래명세서 엑셀 파일을 자동 생성합니다.
        </div>
      </div>

      {/* ① 거래처 + 날짜 설정 */}
      <div style={S.card}>
        <div style={S.sectionTitle}>① 거래처 및 기간 설정</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 20, alignItems: 'end' }}>
          <div>
            <div style={S.label}>거래처</div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Select
                style={{ flex: 1 }}
                placeholder="거래처 선택"
                value={selectedCustomerId}
                onChange={setSelectedCustomerId}
                options={customers.map(c => ({ value: c.id, label: c.name }))}
                allowClear
              />
              <Button icon={<UserOutlined />} onClick={() => setModalOpen(true)}>관리</Button>
            </div>
          </div>

          <div>
            <div style={S.label}>판매 기간</div>
            <div style={{ display: 'flex', gap: 8 }}>
              <DatePicker.RangePicker
                style={{ flex: 1 }}
                value={dateRange}
                onChange={v => setDateRange(v as [dayjs.Dayjs, dayjs.Dayjs] | null)}
              />
              <Button
                icon={<ReloadOutlined />}
                loading={loadingSales}
                onClick={handleFetchSales}
              >
                가져오기
              </Button>
            </div>
          </div>

          <div>
            <div style={S.label}>거래일자</div>
            <DatePicker
              value={issueDate}
              onChange={v => {
                if (!v) return
                setIssueDate(v)
                setDocNumber(prev => {
                  const autoPattern = /^CINNAMONLAB\d{6}-[A-Z0-9]{4}$/
                  return autoPattern.test(prev) ? genDocNumber(v) : prev
                })
              }}
              style={{ width: '100%' }}
            />
          </div>
        </div>

        {/* 2행 — 관리번호 · 거래건명 · 결제조건 */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 20, marginTop: 16 }}>
          <div>
            <div style={S.label}>관리번호</div>
            <Input
              value={docNumber}
              onChange={e => setDocNumber(e.target.value)}
              style={{ background: '#2a2d3e', borderColor: '#4b5563', color: '#f3f4f6' }}
              addonAfter={
                <Tooltip title="새 번호 생성">
                  <ReloadOutlined
                    style={{ cursor: 'pointer', color: '#9ca3af' }}
                    onClick={() => setDocNumber(genDocNumber(issueDate))}
                  />
                </Tooltip>
              }
            />
          </div>
          <div>
            <div style={S.label}>거래건명 <span style={{ color: '#6b7280', fontSize: '0.72rem' }}>(선택)</span></div>
            <Input
              placeholder="예) 5월 정기 납품"
              value={tradeName}
              onChange={e => setTradeName(e.target.value)}
              style={{ background: '#2a2d3e', borderColor: '#4b5563', color: '#f3f4f6' }}
            />
          </div>
          <div>
            <div style={S.label}>결제조건 <span style={{ color: '#6b7280', fontSize: '0.72rem' }}>(선택)</span></div>
            <Input
              placeholder="예) 현금, 외상 30일"
              value={paymentTerms}
              onChange={e => setPaymentTerms(e.target.value)}
              style={{ background: '#2a2d3e', borderColor: '#4b5563', color: '#f3f4f6' }}
            />
          </div>
        </div>

        {salesError && (
          <Alert type="error" message={salesError} style={{ marginTop: 16 }} />
        )}

        {transactions.length > 0 && (
          <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Tag color="blue">{transactions.length}건 거래 조회됨</Tag>
            <Tag color="green">{items.length}개 품목 합산</Tag>
          </div>
        )}
      </div>

      {/* ② 조회된 거래 내역 — 판매건 단위로 묶어서 표시 */}
      {transactions.length > 0 && (
        <Collapse
          style={{ marginBottom: 20, background: '#1a1d27', border: '1px solid #2e2f45', borderRadius: 10 }}
          items={[{
            key: 'txs',
            label: (
              <span style={{ color: '#9ca3af', fontSize: '0.82rem', fontWeight: 600 }}>
                <UnorderedListOutlined style={{ marginRight: 8 }} />
                조회된 거래 내역 ({transactions.length}건) — 대조용
              </span>
            ),
            children: (
              <Table
                size="small"
                pagination={{ pageSize: 20, size: 'small' }}
                rowKey={r => String(r.tx_id)}
                dataSource={transactions.map(tx => ({
                  ...tx,
                  total_qty: tx.items.reduce((s, it) => s + it.qty, 0),
                  item_count: tx.items.length,
                }))}
                onRow={(tx) => ({
                  onClick: () => selectTransaction(tx),
                  style: {
                    cursor: 'pointer',
                    background: selectedTxId === tx.tx_id ? 'rgba(16,185,129,0.08)' : undefined,
                    borderLeft: selectedTxId === tx.tx_id ? '3px solid #10b981' : '3px solid transparent',
                  },
                })}
                expandable={{
                  expandedRowRender: (tx) => (
                    <Table
                      size="small"
                      pagination={false}
                      rowKey={(_, i) => String(i)}
                      dataSource={tx.items}
                      columns={[
                        { title: '품명', dataIndex: 'item_name', ellipsis: true },
                        { title: 'SKU', dataIndex: 'sku', width: 150 },
                        { title: '수량', dataIndex: 'qty', width: 80, align: 'right' as const },
                      ]}
                      style={{ margin: '4px 0 8px 24px', background: 'transparent' }}
                      showHeader
                    />
                  ),
                  rowExpandable: tx => tx.items.length > 0,
                }}
                columns={[
                  { title: '날짜', dataIndex: 'date', width: 105 },
                  { title: '메모 (판매건명)', dataIndex: 'memo', ellipsis: true },
                  { title: '품목 수', dataIndex: 'item_count', width: 80, align: 'right' as const,
                    render: (v: number) => <Tag style={{ margin: 0 }}>{v}종</Tag> },
                  { title: '총 수량', dataIndex: 'total_qty', width: 90, align: 'right' as const,
                    render: (v: number) => <span style={{ color: '#10b981', fontWeight: 600 }}>{v.toLocaleString()}</span> },
                ]}
                style={{ background: 'transparent' }}
              />
            ),
          }]}
        />
      )}

      {/* ③ 품목 테이블 */}
      <div style={S.card}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={S.sectionTitle}>③ 품목 확인 및 편집</div>
            {selectedTxId && (() => {
              const tx = transactions.find(t => t.tx_id === selectedTxId)
              return tx ? (
                <Tag color="green" style={{ fontSize: '0.78rem' }}>
                  {tx.date} · {tx.memo || '선택된 거래'}
                </Tag>
              ) : null
            })()}
            {transactions.length > 0 && !selectedTxId && (
              <span style={{ color: '#6b7280', fontSize: '0.78rem' }}>← 위 거래건을 클릭하세요</span>
            )}
          </div>
          <Button size="small" icon={<PlusOutlined />} onClick={addEmptyItem} disabled={items.length >= 7}>
            행 추가
          </Button>
        </div>

        <Table
          dataSource={items}
          columns={itemColumns}
          rowKey={(_, i) => String(i)}
          pagination={false}
          size="small"
          locale={{ emptyText: '위 거래건을 클릭하면 품목이 자동으로 채워집니다.' }}
          className="invoice-item-table"
        />

        {items.length > 0 && (
          <div style={{
            marginTop: 16, display: 'flex', justifyContent: 'flex-end',
            gap: 24, borderTop: '1px solid #2e2f45', paddingTop: 14,
          }}>
            <div style={{ textAlign: 'right' }}>
              <div style={{ color: '#9ca3af', fontSize: '0.78rem' }}>공급가액 합계</div>
              <div style={{ color: '#e5e7eb', fontWeight: 700, fontSize: '1rem' }}>
                {totalSupply.toLocaleString()}원
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ color: '#9ca3af', fontSize: '0.78rem' }}>세액 (10%)</div>
              <div style={{ color: '#e5e7eb', fontWeight: 700, fontSize: '1rem' }}>
                {totalTax.toLocaleString()}원
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ color: '#9ca3af', fontSize: '0.78rem' }}>합계금액</div>
              <div style={{ color: '#10b981', fontWeight: 700, fontSize: '1.15rem' }}>
                {(totalSupply + totalTax).toLocaleString()}원
              </div>
            </div>
          </div>
        )}

        {items.length > 7 && (
          <Alert
            type="info"
            message={`품목이 7개를 초과하여 ${Math.ceil(items.length / 7)}페이지로 분할된 엑셀 파일이 생성됩니다.`}
            style={{ marginTop: 12 }}
          />
        )}
      </div>

      {/* ④ 생성 */}
      <div style={{ ...S.card, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ fontSize: '0.82rem' }}>
          {!selectedCustomerId && <div style={{ color: '#f59e0b' }}>⚠ 거래처를 선택해주세요</div>}
          {items.length === 0 && <div style={{ color: '#f59e0b' }}>⚠ 위 거래건을 클릭해 품목을 불러오세요</div>}
          {selectedCustomerId && items.length > 0 && items.length <= 7 && (
            <div style={{ color: '#10b981' }}>✓ 준비 완료 — 거래명세서를 생성할 수 있습니다</div>
          )}
          {selectedCustomerId && items.length > 7 && (
            <div style={{ color: '#10b981' }}>
              ✓ 준비 완료 — 품목 {items.length}개 ({Math.ceil(items.length / 7)}페이지로 분할 생성)
            </div>
          )}
        </div>
        <Button
          type="primary"
          size="large"
          icon={<DownloadOutlined />}
          loading={loadingGenerate}
          onClick={handleGenerate}
          style={{
            minWidth: 220,
            background: '#10b981',
            borderColor: '#10b981',
            color: '#fff',
            fontWeight: 700,
            height: 44,
            fontSize: '0.95rem',
          }}
        >
          거래명세서 생성 및 다운로드
        </Button>
      </div>

      {/* 거래처 관리 모달 */}
      <Modal
        title="거래처 관리"
        open={modalOpen}
        onCancel={() => { setModalOpen(false); setShowForm(false); setEditingCustomer(null) }}
        footer={null}
        width={700}
      >
        <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'flex-end' }}>
          <Button type="primary" icon={<PlusOutlined />} onClick={openAddModal}>거래처 추가</Button>
        </div>
        <Table
          dataSource={customers}
          columns={customerColumns}
          rowKey="id"
          size="small"
          pagination={false}
          locale={{ emptyText: '등록된 거래처가 없습니다.' }}
        />

        {/* 추가/수정 인라인 폼 */}
        {showForm && (
          <div style={{ marginTop: 24, borderTop: '1px solid #f0f0f0', paddingTop: 16 }}>
            <div style={{ fontWeight: 600, marginBottom: 12 }}>
              {editingCustomer ? '거래처 수정' : '새 거래처 추가'}
            </div>
            <Form form={form} layout="vertical">
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 16px' }}>
                <Form.Item label="상호 *" name="name" rules={[{ required: true, message: '상호를 입력해주세요.' }]}>
                  <Input placeholder="(주)회사명" />
                </Form.Item>
                <Form.Item label="사업자번호" name="business_no">
                  <Input placeholder="000-00-00000" />
                </Form.Item>
                <Form.Item label="대표자명" name="representative">
                  <Input placeholder="홍길동" />
                </Form.Item>
                <Form.Item label="연락처" name="phone">
                  <Input placeholder="02-0000-0000" />
                </Form.Item>
                <Form.Item label="이메일" name="email">
                  <Input placeholder="example@company.com" />
                </Form.Item>
                <Form.Item label="주소" name="address" style={{ gridColumn: '1 / -1' }}>
                  <Input placeholder="서울시 강남구..." />
                </Form.Item>
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                <Button onClick={() => { setShowForm(false); setEditingCustomer(null) }}>취소</Button>
                <Button type="primary" onClick={handleSaveCustomer}>
                  {editingCustomer ? '수정 완료' : '추가'}
                </Button>
              </div>
            </Form>
          </div>
        )}
      </Modal>
    </div>
  )
}
