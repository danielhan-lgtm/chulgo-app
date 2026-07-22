import { useState, useEffect } from 'react'
import { Button, Select, message, Spin, Alert, Table, Tag, Switch, InputNumber, Modal, Popconfirm } from 'antd'
import { ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import {
  getOBOrdersOverview, getOBAutoConfig, setOBAutoConfig, runOBAutoOnce,
  getOBSessionStatus, startOBSessionLogin,
  type OBOverview, type OBOrder, type OBAutoConfig, type OBSessionStatus,
} from '../services/api'

// 단계 파이프라인 순서
const STAGE_ORDER = ['order', 'putOrder', 'outReady', 'shipReady', 'shipWait']

const CHANNEL_TAG: Record<string, { label: string; color: string }> = {
  cafe24: { label: '카페24', color: 'magenta' },
  kakao: { label: '카카오', color: 'gold' },
}

// 30분 단위 시각 옵션 (00:00 ~ 23:30)
const TIME_OPTIONS = Array.from({ length: 48 }, (_, i) => {
  const v = `${String(Math.floor(i / 2)).padStart(2, '0')}:${i % 2 === 0 ? '00' : '30'}`
  return { value: v, label: v }
})

export default function OBOrders() {
  const [data, setData] = useState<OBOverview | null>(null)
  const [loading, setLoading] = useState(false)
  const [days, setDays] = useState(30)
  const [stage, setStage] = useState<string>('shipReady')
  const [auto, setAuto] = useState<OBAutoConfig | null>(null)
  const [running, setRunning] = useState(false)
  const [sess, setSess] = useState<OBSessionStatus | null>(null)
  const [sessLoading, setSessLoading] = useState(false)
  const [reloginBusy, setReloginBusy] = useState(false)

  async function refreshSess(live = false) {
    setSessLoading(true)
    try { setSess(await getOBSessionStatus(live)) } catch { /* ignore */ }
    finally { setSessLoading(false) }
  }

  async function relogin() {
    setReloginBusy(true)
    try {
      const res = await startOBSessionLogin()
      message.info(res.message, 6)
      if (res.started) {
        // 사용자가 뜬 창에서 로그인을 마칠 때까지 10초 간격으로 상태 폴링 (최대 10분)
        for (let i = 0; i < 60; i++) {
          await new Promise(r => setTimeout(r, 10000))
          try {
            const s = await getOBSessionStatus(true)
            setSess(s)
            if (s.ok) { message.success('✅ 아워박스 세션 갱신 완료'); break }
          } catch { /* ignore */ }
        }
      }
    } catch (e: any) {
      message.error('재로그인 실행 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setReloginBusy(false)
    }
  }

  async function load() {
    setLoading(true)
    try {
      const res = await getOBOrdersOverview(days)
      setData(res)
    } catch (e: any) {
      message.error('주문 조회 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setLoading(false)
    }
  }

  async function loadAuto() {
    try { setAuto(await getOBAutoConfig()) } catch { /* ignore */ }
  }

  async function toggleAuto(enabled: boolean) {
    try {
      const res = await setOBAutoConfig({ enabled })
      setAuto(res)
      message.success(enabled ? '무인 자동화 ON' : '자동화 OFF')
    } catch (e: any) {
      message.error('설정 실패: ' + (e.response?.data?.detail || e.message))
    }
  }

  async function runOnce() {
    setRunning(true)
    try {
      const res = await runOBAutoOnce(false, days)
      const reg = res.register?.summary || {}
      Modal.success({
        title: '자동 실행 완료',
        content: `발송준비 ${res.shipReady_count ?? 0}건 · BH 취합등록 ${reg.tx_registered ?? 0}건(주문 ${reg.orders_included ?? 0}건)` +
          (reg.skipped_done ? ` · 기등록 ${reg.skipped_done}건` : '') +
          (reg.unmapped ? ` · 미매핑 ${reg.unmapped}건` : '') + (reg.errors ? ` · 오류 ${reg.errors}건` : ''),
      })
      await load()
    } catch (e: any) {
      message.error('실행 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setRunning(false)
    }
  }

  useEffect(() => { load(); loadAuto(); refreshSess() }, [])  // 최초 1회

  const columns: ColumnsType<OBOrder> = [
    { title: '주문번호', dataIndex: 'od_sno', width: 110 },
    {
      title: '채널', dataIndex: 'channel', width: 90,
      render: (c: string) => c && CHANNEL_TAG[c]
        ? <Tag color={CHANNEL_TAG[c].color}>{CHANNEL_TAG[c].label}</Tag>
        : <span style={{ color: '#9ca3af' }}>{c || '-'}</span>,
    },
    { title: '판매처', dataIndex: 'sach_nm', width: 130, ellipsis: true },
    { title: '상품', dataIndex: 'prod_nm', ellipsis: true },
    { title: '수량', dataIndex: 'od_qty', width: 60, align: 'right' },
    { title: '수령인', dataIndex: 'recvr_nm', width: 90 },
    { title: '단계', dataIndex: 'stage_nm', width: 90, render: (s: string) => <Tag>{s}</Tag> },
    { title: '주문일', dataIndex: 'od_dtm', width: 150 },
  ]

  const cafe24Count = data?.channels.find(c => c.is_cafe24)
  const kakaoCount = data?.channels.find(c => c.is_kakao)
  const rows = data?.stages[stage] || []

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">OB 주문 (카페24·카카오)</h1>
        <p className="page-desc">아워박스 수집 주문을 단계별로 추적하고 발송준비 시 BoxHero로 자동 출고등록</p>
      </div>

      {/* 무인 자동화 제어판 */}
      <div style={{
        background: auto?.ob_auto_enabled ? '#ecfdf5' : '#f9fafb',
        border: `1.5px solid ${auto?.ob_auto_enabled ? '#10b981' : '#e5e7eb'}`,
        borderRadius: 12, padding: 16, marginBottom: 16,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <ThunderboltOutlined style={{ fontSize: '1.3rem', color: auto?.ob_auto_enabled ? '#10b981' : '#9ca3af' }} />
            <div>
              <div style={{ fontWeight: 800, fontSize: '0.95rem' }}>무인 자동화</div>
              <div style={{ fontSize: '0.72rem', color: '#6b7280' }}>수집 → 단계진행 → BH 출고등록 자동</div>
            </div>
          </div>
          <Switch
            checked={!!auto?.ob_auto_enabled}
            onChange={toggleAuto}
            checkedChildren="ON" unCheckedChildren="OFF"
          />
          {auto && (
            <Select
              size="small" value={auto.ob_auto_mode} style={{ width: 110 }}
              onChange={(v) => setOBAutoConfig({ mode: v }).then(setAuto)}
              options={[{ value: 'times', label: '지정 시각' }, { value: 'interval', label: 'N분 주기' }]}
            />
          )}
          {auto?.ob_auto_mode === 'times' && (
            <span style={{ fontSize: '0.78rem', color: '#374151', display: 'flex', alignItems: 'center', gap: 6 }}>
              매일
              <Select
                mode="multiple"
                size="small"
                value={auto.ob_auto_times ? auto.ob_auto_times.split(',').filter(Boolean) : []}
                onChange={(vals: string[]) => setOBAutoConfig({ times: vals.join(',') }).then(setAuto)}
                options={TIME_OPTIONS}
                placeholder="시각 선택"
                style={{ minWidth: 260, maxWidth: 460 }}
                allowClear
              />
            </span>
          )}
          {auto?.ob_auto_mode === 'interval' && (
            <span style={{ fontSize: '0.78rem', color: '#374151', display: 'flex', alignItems: 'center', gap: 6 }}>
              주기
              <InputNumber
                size="small" min={5} max={1440} value={auto.ob_auto_interval_min}
                onChange={(v) => v && setOBAutoConfig({ interval_min: v }).then(setAuto)}
                style={{ width: 70 }}
              /> 분
            </span>
          )}
          <Popconfirm
            title="지금 1회 실제 실행"
            description="카페24·카카오 주문을 수집→발송준비까지 진행하고 BH에 실제 출고등록합니다. 진행할까요?"
            okText="실행" cancelText="취소"
            onConfirm={runOnce}
          >
            <Button type="primary" icon={<ThunderboltOutlined />} loading={running}>
              지금 1회 실행 (확인용)
            </Button>
          </Popconfirm>
        </div>
        {auto?.status && (auto.status.last_run || auto.status.last_error) && (
          <div style={{ marginTop: 10, fontSize: '0.76rem', color: auto.status.last_error ? '#dc2626' : '#6b7280' }}>
            {auto.status.last_error
              ? `⚠️ 마지막 오류 (${auto.status.last_run}): ${auto.status.last_error}`
              : `✅ 마지막 실행 ${auto.status.last_run} · 결과 ${JSON.stringify(auto.status.last_result || {})}`}
            {auto.status.running && ' · 실행 중…'}
          </div>
        )}

        {/* 아워박스 세션 상태 (자동화는 저장 세션으로 로그인 — CAPTCHA 회피) */}
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px dashed #d1d5db', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: '0.78rem' }}>
          <span style={{ fontWeight: 700, color: '#374151' }}>🔐 아워박스 세션</span>
          <Tag color={sess?.ok ? 'green' : sess?.ok === false ? 'red' : 'default'}>
            {sess?.ok ? '정상' : sess?.ok === false ? '만료' : '미확인'}
          </Tag>
          {sess?.checked_at && <span style={{ color: '#9ca3af' }}>확인 {sess.checked_at}</span>}
          {sess?.ok === false && <span style={{ color: '#dc2626' }}>{sess.detail}</span>}
          <Button size="small" onClick={() => refreshSess(true)} loading={sessLoading}>상태 확인</Button>
          <Button size="small" type="primary" ghost onClick={relogin} loading={reloginBusy}>
            세션 재로그인
          </Button>
          <span style={{ color: '#9ca3af' }}>
            · 만료 시 버튼을 누르면 이 PC에 Chrome 창이 떠서 직접 로그인 (만료되면 슬랙으로도 알림)
          </span>
        </div>
      </div>

      {/* 컨트롤 */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <Select
          value={days}
          onChange={(v) => setDays(v)}
          style={{ width: 120 }}
          options={[7, 14, 30, 60, 90].map(d => ({ value: d, label: `최근 ${d}일` }))}
        />
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading} type="primary">조회</Button>
        {data && <span style={{ fontSize: '0.78rem', color: '#9ca3af' }}>{data.from_date} ~ {data.to_date}</span>}
      </div>

      {/* 채널 상태 */}
      {data && (
        <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
          {[
            { name: '카페24', ch: cafe24Count, color: '#c2185b', bg: '#fce4ec' },
            { name: '카카오', ch: kakaoCount, color: '#a16207', bg: '#fef9c3' },
          ].map(({ name, ch, color, bg }) => (
            <div key={name} style={{ background: bg, border: `1px solid ${color}33`, borderRadius: 10, padding: '10px 16px', minWidth: 200 }}>
              <div style={{ fontWeight: 800, color }}>{name}</div>
              {ch ? (
                <div style={{ fontSize: '0.76rem', color: '#6b7280', marginTop: 2 }}>
                  {ch.mall_nm} · 마지막 수집 {ch.colct_last_dtm || '-'}
                </div>
              ) : (
                <div style={{ fontSize: '0.76rem', color: '#ef4444', marginTop: 2 }}>채널 미발견</div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 단계 파이프라인 */}
      {data && (
        <div style={{ display: 'flex', gap: 6, marginBottom: 16, flexWrap: 'wrap' }}>
          {STAGE_ORDER.map((st, i) => {
            const active = stage === st
            const cnt = data.counts[st] ?? 0
            return (
              <div key={st} style={{ display: 'flex', alignItems: 'center' }}>
                <div
                  onClick={() => setStage(st)}
                  style={{
                    cursor: 'pointer', borderRadius: 10, padding: '8px 14px', textAlign: 'center',
                    background: active ? '#10b981' : (cnt > 0 ? '#ecfdf5' : '#f9fafb'),
                    border: `1.5px solid ${active ? '#10b981' : '#e5e7eb'}`,
                    color: active ? '#fff' : '#374151', minWidth: 90,
                  }}
                >
                  <div style={{ fontSize: '1.3rem', fontWeight: 800 }}>{cnt}</div>
                  <div style={{ fontSize: '0.74rem' }}>{data.stage_labels[st]}</div>
                </div>
                {i < STAGE_ORDER.length - 1 && <span style={{ color: '#cbd5e1', margin: '0 2px' }}>→</span>}
              </div>
            )
          })}
        </div>
      )}

      {loading && <div style={{ textAlign: 'center', padding: 40 }}><Spin size="large" tip="아워박스 조회 중... (로그인 포함 최대 1분)" /></div>}

      {!loading && data && (
        <>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message={`'${data.stage_labels[stage]}' 단계 카페24·카카오 주문 ${rows.length}건`}
            description={rows.length === 0 ? '해당 단계에 카페24/카카오 주문이 없습니다. (주문 수집 후 표시됩니다)' : undefined}
          />
          <Table<OBOrder>
            rowKey={(r) => `${r.od_sno}-${r.prod_cd}`}
            columns={columns}
            dataSource={rows}
            size="small"
            pagination={{ pageSize: 20, showSizeChanger: false }}
            scroll={{ x: 900 }}
          />
        </>
      )}
    </div>
  )
}
