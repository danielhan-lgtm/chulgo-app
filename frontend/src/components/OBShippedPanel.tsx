import { useEffect, useState } from 'react'
import { Alert, Button, DatePicker, InputNumber, Modal, Popconfirm, Select, Switch, Table, Tag, Tooltip, message } from 'antd'
import { CheckCircleOutlined, ReloadOutlined, TruckOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import {
  getOBShippedConfig, getOBShippedPreview, registerOBShipped, setOBShippedConfig,
  type OBShippedConfig, type OBShippedGroup, type OBShippedPreview,
} from '../services/api'

// 아워박스 물류센터가 직접 출고하는 카페24·DJ&A 건을 BoxHero에 '거래처 × 출고일' 1건으로 전산처리
// (스마트스토어 도착보장과 같은 구조). 수기 출고가 이미 있는 날은 보류하고 사람이 정리한다.

const CH_COLOR: Record<string, string> = { cafe24: 'magenta', cafe24_djna: 'purple', kakao: 'gold', coupang: 'volcano' }
const STATUS: Record<string, { label: string; color: string; help: string }> = {
  ready: { label: '등록 대기', color: 'green', help: 'BoxHero에 아직 없음 — 자동/수동 등록 대상' },
  manual_found: { label: '수기 출고 있음', color: 'orange', help: '같은 거래처로 그날(또는 다음날) 수기 출고가 있어 자동등록 보류. 차액 등록 또는 수기 처리됨으로 정리' },
  before_start: { label: '시작일 이전', color: 'default', help: '자동화 시작일 이전 출고 — 수기 처리 기간. 필요하면 이 줄만 골라 등록 가능' },
  registered: { label: '처리 완료', color: 'blue', help: '모든 주문이 BoxHero에 등록됨' },
  unmapped_only: { label: '미매핑', color: 'red', help: '남은 주문이 전부 BH 매핑 없는 상품 — 상품 매핑 페이지에서 추가 필요' },
}
const TIME_OPTIONS = Array.from({ length: 48 }, (_, i) => {
  const v = `${String(Math.floor(i / 2)).padStart(2, '0')}:${i % 2 === 0 ? '00' : '30'}`
  return { value: v, label: v }
})

function itemsText(items: { name: string; sku: string; qty: number }[], max = 4) {
  const s = items.slice(0, max).map(i => `${(i.name || i.sku).slice(0, 18)} ${i.qty}`).join(' · ')
  return items.length > max ? `${s} 외 ${items.length - max}종` : s
}

export default function OBShippedPanel() {
  const [cfg, setCfg] = useState<OBShippedConfig | null>(null)
  const [data, setData] = useState<OBShippedPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [busyKey, setBusyKey] = useState<string>('')   // 등록 중인 그룹 key ('*' = 전체)
  const [days, setDays] = useState<number | undefined>(undefined)

  async function loadCfg() {
    try { const c = await getOBShippedConfig(); setCfg(c); if (days === undefined) setDays(c.ob_shipped_days) } catch { /* ignore */ }
  }
  async function load() {
    setLoading(true)
    try { setData(await getOBShippedPreview(days)) }
    catch (e: any) { message.error('출고실적 조회 실패: ' + (e.response?.data?.detail || e.message)) }
    finally { setLoading(false) }
  }
  async function patch(p: Parameters<typeof setOBShippedConfig>[0]) {
    try { setCfg(await setOBShippedConfig(p)) }
    catch (e: any) { message.error('설정 실패: ' + (e.response?.data?.detail || e.message)) }
  }
  async function run(keys?: string[], force = false) {
    setBusyKey(keys ? keys[0] : '*')
    try {
      const res = await registerOBShipped({ days, dry_run: false, force, keys })
      const s = res.summary
      Modal.success({
        title: '전산처리 완료',
        content: `BH 등록 ${s.tx_registered}건 (주문 ${s.orders_included}건 · ${s.qty}개)` +
          (s.skipped ? ` · 보류 ${s.skipped}` : '') + (s.errors ? ` · 오류 ${s.errors}` : '') +
          (res.errors.length ? `\n${res.errors.map(e => `${e.key}: ${e.error}`).join('\n')}` : ''),
      })
      setData(d => d ? { ...d, groups: res.groups } : d)
      loadCfg()
    } catch (e: any) {
      message.error('등록 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setBusyKey('')
    }
  }

  useEffect(() => { loadCfg() }, [])

  const groups = data?.groups || []
  const readyCount = groups.filter(g => g.status === 'ready').length
  const readyQty = groups.filter(g => g.status === 'ready').reduce((n, g) => n + g.new_qty, 0)
  const enabled = !!cfg?.ob_shipped_auto_enabled

  const columns: ColumnsType<OBShippedGroup> = [
    { title: '출고일', dataIndex: 'out_dt', width: 100 },
    { title: '채널', dataIndex: 'label', width: 120, render: (v: string, r) => <Tag color={CH_COLOR[r.channel] || 'default'}>{v}</Tag> },
    {
      title: '아워박스 출고', width: 150, align: 'right',
      render: (_, r) => <span>{r.order_count}건 · <b>{r.qty_total}</b>개</span>,
    },
    {
      title: 'BH 등록됨', width: 110, align: 'right',
      render: (_, r) => r.registered_count ? <span style={{ color: '#2563eb' }}>{r.registered_count}건 · {r.registered_qty}개</span> : <span style={{ color: '#9ca3af' }}>-</span>,
    },
    {
      title: '미등록', width: 110, align: 'right',
      render: (_, r) => r.new_count ? <b style={{ color: '#059669' }}>{r.new_count}건 · {r.new_qty}개</b> : <span style={{ color: '#9ca3af' }}>-</span>,
    },
    {
      title: 'BH 수기 출고', width: 130, align: 'right',
      render: (_, r) => r.bh_manual.tx_count
        ? <Tooltip title={r.bh_manual.txs.map(t => `${t.date} ${t.memo} ${t.qty}개 (${t.by})`).join(' / ')}>
            <span style={{ color: '#d97706' }}>{r.bh_manual.tx_count}건 · {r.bh_manual.qty}개</span>
          </Tooltip>
        : <span style={{ color: '#9ca3af' }}>-</span>,
    },
    {
      title: '상태', dataIndex: 'status', width: 120,
      render: (s: string, r) => (
        <Tooltip title={STATUS[s]?.help}>
          <Tag color={STATUS[s]?.color}>{STATUS[s]?.label || s}</Tag>
          {r.unmapped_count ? <Tag color="red">미매핑 {r.unmapped_count}건</Tag> : null}
        </Tooltip>
      ),
    },
    {
      title: '등록할 품목', ellipsis: true,
      render: (_, r) => r.items.length
        ? <Tooltip title={r.items.map(i => `${i.name || i.sku} ${i.qty}`).join(' / ')}><span style={{ fontSize: '0.78rem' }}>{itemsText(r.items)}</span></Tooltip>
        : r.unmapped.length ? <span style={{ fontSize: '0.78rem', color: '#dc2626' }}>미매핑: {r.unmapped.map(u => u.prod_nm).join(', ')}</span> : null,
    },
    {
      title: '', width: 170, fixed: 'right',
      render: (_, r) => {
        if (r.status === 'ready' || r.status === 'before_start') {
          return (
            <Popconfirm title={`${r.label} ${r.out_dt} 출고 ${r.new_qty}개를 BoxHero에 등록`} description={r.memo} okText="등록" cancelText="취소" onConfirm={() => run([r.key])}>
              <Button size="small" type="primary" loading={busyKey === r.key}>BH 등록</Button>
            </Popconfirm>
          )
        }
        if (r.status === 'manual_found') {
          const diffQty = r.diff_items.reduce((n, i) => n + i.qty, 0)
          return (
            <Popconfirm
              title={diffQty ? `수기분을 뺀 차액 ${diffQty}개만 등록` : '수기 출고가 전부 덮음 — 등록 없이 처리됨으로 기록'}
              description={diffQty ? itemsText(r.diff_items, 6) : '아워박스 주문을 수기 거래에 연결해 다시 잡히지 않게 합니다'}
              okText={diffQty ? '차액 등록' : '수기 처리됨'} cancelText="취소" onConfirm={() => run([r.key], true)}
            >
              <Button size="small" loading={busyKey === r.key} style={{ borderColor: '#f59e0b', color: '#b45309' }}>
                {diffQty ? `차액 ${diffQty}개 등록` : '수기 처리됨'}
              </Button>
            </Popconfirm>
          )
        }
        return null
      },
    },
  ]

  return (
    <div style={{ background: enabled ? '#eff6ff' : '#f9fafb', border: `1.5px solid ${enabled ? '#3b82f6' : '#e5e7eb'}`, borderRadius: 12, padding: 16, marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <TruckOutlined style={{ fontSize: '1.3rem', color: enabled ? '#2563eb' : '#9ca3af' }} />
          <div>
            <div style={{ fontWeight: 800, fontSize: '0.95rem' }}>아워박스 자동출고 → BoxHero 전산처리 (카페24 · 자사몰 DJ&A)</div>
            <div style={{ fontSize: '0.72rem', color: '#6b7280' }}>
              물류센터가 직접 출고한 실적을 받아 거래처 × 출고일 1건으로 BH 출고등록 (스마트스토어 도착보장과 같은 방식) · OMS 세션 불필요
            </div>
          </div>
        </div>
        <Switch checked={enabled} onChange={(v) => patch({ enabled: v })} checkedChildren="자동 ON" unCheckedChildren="OFF" />
        {cfg && (
          <span style={{ fontSize: '0.78rem', color: '#374151', display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            매일
            <Select mode="multiple" size="small" options={TIME_OPTIONS} allowClear placeholder="시각"
              value={cfg.ob_shipped_auto_times ? cfg.ob_shipped_auto_times.split(',').filter(Boolean) : []}
              onChange={(vals: string[]) => patch({ times: vals.join(',') })} style={{ minWidth: 140, maxWidth: 320 }} />
            · 최근
            <InputNumber size="small" min={1} max={30} value={cfg.ob_shipped_days} style={{ width: 60 }}
              onChange={(v) => v && patch({ days: v }).then(() => setDays(v))} />
            일 출고실적
            · 시작일
            <DatePicker size="small" allowClear placeholder="제한 없음"
              value={cfg.ob_shipped_start_date ? dayjs(cfg.ob_shipped_start_date) : null}
              onChange={(d) => patch({ start_date: d ? d.format('YYYY-MM-DD') : '' })} />
            <Tooltip title="이 날짜 이전 출고는 수기 처리 기간으로 보고 자동등록하지 않습니다 (줄마다 수동 등록은 가능)"><span style={{ color: '#9ca3af' }}>?</span></Tooltip>
          </span>
        )}
      </div>
      {cfg?.status && (cfg.status.last_run || cfg.status.last_error) && (
        <div style={{ marginTop: 8, fontSize: '0.76rem', color: cfg.status.last_error ? '#dc2626' : '#6b7280' }}>
          {cfg.status.last_error
            ? `⚠️ 마지막 오류 (${cfg.status.last_run}): ${cfg.status.last_error}`
            : `✅ 마지막 자동 실행 ${cfg.status.last_run} · ${JSON.stringify(cfg.status.last_result || {})}`}
          {cfg.status.running && ' · 실행 중…'}
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
        <Select size="small" value={days} onChange={(v) => setDays(v)} style={{ width: 110 }}
          options={[1, 2, 3, 5, 7, 14].map(d => ({ value: d, label: `최근 ${d}일` }))} />
        <Button size="small" icon={<ReloadOutlined />} onClick={load} loading={loading}>출고실적 조회 · BH 확인</Button>
        {data && (
          <Popconfirm
            title={`등록 대기 ${readyCount}일치 (${readyQty}개)를 BoxHero에 등록`}
            description="수기 출고가 있는 날·시작일 이전은 건너뜁니다 (줄별 버튼으로 따로 처리)"
            okText="전부 등록" cancelText="취소" onConfirm={() => run()} disabled={!readyCount}
          >
            <Button size="small" type="primary" icon={<CheckCircleOutlined />} disabled={!readyCount} loading={busyKey === '*'}>
              등록 대기 전부 BH 등록 ({readyCount})
            </Button>
          </Popconfirm>
        )}
        {data && <span style={{ fontSize: '0.76rem', color: '#9ca3af' }}>{data.from_date} ~ {data.to_date} · 출고실적 {data.record_count}행{data.start_date ? ` · 시작일 ${data.start_date}` : ''}</span>}
      </div>

      {data && groups.length === 0 && <Alert type="info" showIcon style={{ marginTop: 10 }} message="기간 내 카페24·DJ&A 출고실적이 없습니다." />}
      {groups.length > 0 && (
        <Table<OBShippedGroup>
          style={{ marginTop: 10 }} rowKey="key" columns={columns} dataSource={groups} size="small"
          pagination={groups.length > 12 ? { pageSize: 12, showSizeChanger: false } : false} scroll={{ x: 1100 }}
        />
      )}
    </div>
  )
}
