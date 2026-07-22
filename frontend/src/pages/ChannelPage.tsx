import { useState } from 'react'
import { Button, DatePicker, Radio, Table, Tag, Alert, Spin, Input, Tooltip } from 'antd'
import { SearchOutlined, DownloadOutlined } from '@ant-design/icons'
import type { AppConfig } from '../types'
import { getChannelSummary } from '../services/api'
import dayjs from 'dayjs'

interface Props { config: AppConfig }

interface ChannelRow {
  sku: string
  name: string
  channels: Record<string, { in_qty: number; out_qty: number; memo_tags: string[] }>
  total_in: number
  total_out: number
}

interface ChannelResult {
  locations: string[]
  rows: ChannelRow[]
  from_date: string
  to_date: string
  tx_type: string
  total_skus: number
}

function qty(n: number) { return n > 0 ? n.toLocaleString() : '' }

export default function ChannelPage({ config }: Props) {
  const [txType, setTxType] = useState<'in' | 'out' | 'both'>('out')
  const [fromDate, setFromDate] = useState(dayjs().subtract(30, 'day').format('YYYY-MM-DD'))
  const [toDate, setToDate] = useState(dayjs().format('YYYY-MM-DD'))
  const [exclude, setExclude] = useState('폐기예정존(z)')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<ChannelResult | null>(null)
  const [search, setSearch] = useState('')
  const [error, setError] = useState('')

  async function handleSearch() {
    if (!config.api_token) return
    setLoading(true)
    setError('')
    try {
      const data = await getChannelSummary({
        token: config.api_token,
        from_date: fromDate,
        to_date: toDate,
        tx_type: txType,
        exclude_locations: exclude,
      })
      setResult(data)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  function handleExport() {
    if (!result) return
    const locs = result.locations
    const header = ['SKU', '상품명', ...locs.flatMap(l => txType === 'both' ? [`${l}_입고`, `${l}_출고`] : [l]), '합계']
    const rows = result.rows.map(r => {
      const locCols = locs.flatMap(l => {
        const ch = r.channels[l] || { in_qty: 0, out_qty: 0 }
        if (txType === 'both') return [ch.in_qty || '', ch.out_qty || '']
        return [txType === 'in' ? ch.in_qty || '' : ch.out_qty || '']
      })
      const total = txType === 'in' ? r.total_in : txType === 'out' ? r.total_out : r.total_in + r.total_out
      return [r.sku, r.name, ...locCols, total]
    })
    const csv = [header, ...rows].map(r => r.map(v => `"${v}"`).join(',')).join('\n')
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `채널별현황_${fromDate}_${toDate}.csv`; a.click()
    URL.revokeObjectURL(url)
  }

  const filtered = (result?.rows || []).filter(r =>
    !search || r.name.includes(search) || r.sku.includes(search)
  )

  // 동적 컬럼 생성
  const locColumns = (result?.locations || []).map(loc => {
    if (txType === 'both') {
      return {
        title: <span style={{ fontSize: '0.78rem' }}>{loc}</span>,
        children: [
          {
            title: <span style={{ fontSize: '0.72rem', color: '#10b981' }}>입고</span>,
            key: `${loc}_in`, width: 80, align: 'right' as const,
            render: (_: unknown, r: ChannelRow) => {
              const v = r.channels[loc]?.in_qty || 0
              return v ? <span style={{ color: '#10b981', fontWeight: 600 }}>{v.toLocaleString()}</span> : ''
            },
          },
          {
            title: <span style={{ fontSize: '0.72rem', color: '#ef4444' }}>출고</span>,
            key: `${loc}_out`, width: 80, align: 'right' as const,
            render: (_: unknown, r: ChannelRow) => {
              const v = r.channels[loc]?.out_qty || 0
              return v ? <span style={{ color: '#ef4444', fontWeight: 600 }}>{v.toLocaleString()}</span> : ''
            },
          },
        ],
      }
    }
    return {
      title: <Tooltip title={loc}><span style={{ fontSize: '0.78rem' }}>{loc.length > 8 ? loc.slice(0, 7) + '…' : loc}</span></Tooltip>,
      key: loc, width: 90, align: 'right' as const,
      render: (_: unknown, r: ChannelRow) => {
        const ch = r.channels[loc]
        if (!ch) return ''
        const v = txType === 'in' ? ch.in_qty : ch.out_qty
        const tags = ch.memo_tags
        return (
          <div>
            {v ? <span style={{ fontWeight: 600 }}>{v.toLocaleString()}</span> : ''}
            {tags.map(t => <Tag key={t} style={{ fontSize: '0.65rem', padding: '0 3px', marginLeft: 2 }}>{t}</Tag>)}
          </div>
        )
      },
    }
  })

  const columns = [
    {
      title: 'SKU',
      dataIndex: 'sku', key: 'sku', width: 130,
      render: (v: string) => <span style={{ fontSize: '0.75rem', fontFamily: 'monospace' }}>{v}</span>,
    },
    {
      title: '상품명',
      dataIndex: 'name', key: 'name', width: 200, ellipsis: true,
      render: (v: string) => <span style={{ fontSize: '0.82rem' }}>{v}</span>,
    },
    ...locColumns,
    {
      title: '합계',
      key: 'total', width: 80, align: 'right' as const,
      fixed: 'right' as const,
      render: (_: unknown, r: ChannelRow) => {
        const total = txType === 'in' ? r.total_in : txType === 'out' ? r.total_out : r.total_in + r.total_out
        return <span style={{ fontWeight: 800, color: '#111827' }}>{total.toLocaleString()}</span>
      },
    },
  ]

  // 위치별 합계 (하단 요약용)
  const locTotals = result ? result.locations.map(loc => {
    const inSum = result.rows.reduce((s, r) => s + (r.channels[loc]?.in_qty || 0), 0)
    const outSum = result.rows.reduce((s, r) => s + (r.channels[loc]?.out_qty || 0), 0)
    return { loc, in: inSum, out: outSum }
  }) : []

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">채널별 현황</h1>
        <p className="page-desc">박스히어로 위치(채널)별 품목 입출고 수량 비교</p>
      </div>

      {/* 컨트롤 */}
      <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px 20px', marginBottom: 16, display: 'flex', gap: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>데이터 유형</div>
          <Radio.Group value={txType} onChange={e => setTxType(e.target.value)} size="small">
            <Radio.Button value="out">출고</Radio.Button>
            <Radio.Button value="in">입고</Radio.Button>
            <Radio.Button value="both">입출고 모두</Radio.Button>
          </Radio.Group>
        </div>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>시작일</div>
          <DatePicker size="small" value={dayjs(fromDate)} onChange={d => d && setFromDate(d.format('YYYY-MM-DD'))} allowClear={false} />
        </div>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>종료일</div>
          <DatePicker size="small" value={dayjs(toDate)} onChange={d => d && setToDate(d.format('YYYY-MM-DD'))} allowClear={false} />
        </div>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>제외 위치 (쉼표 구분)</div>
          <Input size="small" value={exclude} onChange={e => setExclude(e.target.value)} style={{ width: 160 }} placeholder="폐기예정존(z)" />
        </div>
        <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch} loading={loading} disabled={!config.api_token}>
          조회
        </Button>
        {result && (
          <Button icon={<DownloadOutlined />} onClick={handleExport} size="small">
            CSV 다운로드
          </Button>
        )}
      </div>

      {error && <Alert type="error" message={error} style={{ marginBottom: 12 }} />}

      {loading && (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" />
          <div style={{ marginTop: 12, color: '#6b7280', fontSize: '0.85rem' }}>
            위치별 거래 상세를 병렬로 수집 중... 위치/기간에 따라 1~2분 소요될 수 있습니다.
          </div>
        </div>
      )}

      {result && !loading && (
        <>
          {/* 요약 카드 */}
          <div style={{ display: 'flex', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
            {locTotals.filter(lt => (txType === 'in' ? lt.in : lt.out) > 0).map(lt => (
              <div key={lt.loc} style={{ background: '#fff', borderRadius: 10, border: '1px solid #e5e7eb', padding: '10px 16px', minWidth: 120 }}>
                <div style={{ fontSize: '0.72rem', color: '#6b7280', fontWeight: 600, marginBottom: 4 }}>{lt.loc}</div>
                {(txType === 'out' || txType === 'both') && (
                  <div style={{ fontSize: '1.2rem', fontWeight: 800, color: '#ef4444' }}>
                    {lt.out.toLocaleString()}
                    <span style={{ fontSize: '0.65rem', color: '#9ca3af', marginLeft: 2 }}>출고</span>
                  </div>
                )}
                {(txType === 'in' || txType === 'both') && (
                  <div style={{ fontSize: txType === 'both' ? '1rem' : '1.2rem', fontWeight: 700, color: '#10b981' }}>
                    {lt.in.toLocaleString()}
                    <span style={{ fontSize: '0.65rem', color: '#9ca3af', marginLeft: 2 }}>입고</span>
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* 검색 + 테이블 */}
          <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px 20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12, gap: 10 }}>
              <span style={{ fontWeight: 700, fontSize: '0.92rem' }}>📊 품목 × 채널 매트릭스</span>
              <span style={{ fontSize: '0.78rem', color: '#6b7280' }}>{result.total_skus}개 품목</span>
              <Input.Search
                size="small"
                placeholder="SKU 또는 상품명 검색"
                value={search}
                onChange={e => setSearch(e.target.value)}
                style={{ width: 200, marginLeft: 'auto' }}
              />
            </div>
            <Table
              dataSource={filtered}
              columns={columns}
              rowKey="sku"
              size="small"
              scroll={{ x: 'max-content' }}
              pagination={{ pageSize: 50, showSizeChanger: true }}
              style={{ fontSize: '0.82rem' }}
              summary={() => {
                if (!filtered.length) return null
                const totals = (result?.locations || []).map(loc => ({
                  loc,
                  in: filtered.reduce((s, r) => s + (r.channels[loc]?.in_qty || 0), 0),
                  out: filtered.reduce((s, r) => s + (r.channels[loc]?.out_qty || 0), 0),
                }))
                const grandIn = filtered.reduce((s, r) => s + r.total_in, 0)
                const grandOut = filtered.reduce((s, r) => s + r.total_out, 0)
                return (
                  <Table.Summary fixed>
                    <Table.Summary.Row style={{ background: '#f3f4f6', fontWeight: 700 }}>
                      <Table.Summary.Cell index={0} colSpan={2}>합계 ({filtered.length}개 품목)</Table.Summary.Cell>
                      {txType === 'both'
                        ? totals.flatMap((lt, i) => [
                            <Table.Summary.Cell key={`i${i}`} index={i * 2 + 2} align="right">
                              <span style={{ color: '#10b981' }}>{lt.in.toLocaleString()}</span>
                            </Table.Summary.Cell>,
                            <Table.Summary.Cell key={`o${i}`} index={i * 2 + 3} align="right">
                              <span style={{ color: '#ef4444' }}>{lt.out.toLocaleString()}</span>
                            </Table.Summary.Cell>,
                          ])
                        : totals.map((lt, i) => (
                            <Table.Summary.Cell key={i} index={i + 2} align="right">
                              {(txType === 'in' ? lt.in : lt.out).toLocaleString()}
                            </Table.Summary.Cell>
                          ))
                      }
                      <Table.Summary.Cell index={totals.length * (txType === 'both' ? 2 : 1) + 2} align="right">
                        {(txType === 'in' ? grandIn : txType === 'out' ? grandOut : grandIn + grandOut).toLocaleString()}
                      </Table.Summary.Cell>
                    </Table.Summary.Row>
                  </Table.Summary>
                )
              }}
            />
          </div>
        </>
      )}
    </div>
  )
}
