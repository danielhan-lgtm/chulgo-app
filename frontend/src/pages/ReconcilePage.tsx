import { useState, useRef, useEffect, useMemo } from 'react'
import { Button, DatePicker, Radio, Table, Tabs, Tag, Alert, Spin, Row, Col, Select, Switch, Tooltip, Drawer, Empty, Modal, Badge, Slider, Input, message, Dropdown } from 'antd'
import { SearchOutlined, WarningOutlined, RobotOutlined, CopyOutlined, ApartmentOutlined, TagsOutlined, SwapOutlined, ExportOutlined } from '@ant-design/icons'
import type { AppConfig, ReconcileResult, ReconcileRow, ReconcileSummary } from '../types'
import { getReconcile, analyzeReconcile, getLocations, getReconcileDetail, getReconcileMissing, getReconcileFullMatch, getReconcileQtyGap, getReconcileStock, getReconcileItemSearch, getSmartCompare, getMatchedPairs, createWeeklyReport, listWeeklyReports, getWeeklyReport, getStockDiffChange, getChannelFlow, getItemOutDecompose, getStockSnapshots, captureStockSnapshot, setReconcileStatus, clearReconcileStatus, getStockDiffTrace } from '../services/api'
import type { StockDiffTrace } from '../services/api'
import type { OutDecomp, StockSnap, ReconcileCleanupStatus } from '../services/api'
import dayjs from 'dayjs'

interface Props {
  config: AppConfig
}

function MarkdownText({ text }: { text: string }) {
  // 간단한 마크다운 렌더링: **bold**, ## heading, - list
  const lines = text.split('\n')
  return (
    <div style={{ lineHeight: 1.8, fontSize: '0.88rem', color: '#1f2937' }}>
      {lines.map((line, i) => {
        if (line.startsWith('## ')) return <h3 key={i} style={{ marginTop: 16, marginBottom: 4, fontSize: '0.95rem', color: '#111827', fontWeight: 700 }}>{line.slice(3)}</h3>
        if (line.startsWith('### ')) return <h4 key={i} style={{ marginTop: 10, marginBottom: 2, fontSize: '0.88rem', color: '#374151', fontWeight: 600 }}>{line.slice(4)}</h4>
        if (line.startsWith('**') && line.endsWith('**')) return <p key={i} style={{ fontWeight: 700, marginBottom: 2 }}>{line.slice(2, -2)}</p>
        if (line.startsWith('- ') || line.startsWith('• ')) return <li key={i} style={{ marginLeft: 16, marginBottom: 2 }}>{line.slice(2).replace(/\*\*([^*]+)\*\*/g, '$1')}</li>
        if (line.trim() === '') return <br key={i} />
        // inline bold
        const rendered = line.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        return <p key={i} style={{ marginBottom: 2 }} dangerouslySetInnerHTML={{ __html: rendered }} />
      })}
    </div>
  )
}

const STATUS_CFG = {
  ok:       { label: '정상',     color: '#065f46', bg: '#d1fae5', tag: 'success' as const },
  mismatch: { label: '수량불일치', color: '#92400e', bg: '#fef3c7', tag: 'warning' as const },
  bh_only:  { label: 'BH만 존재', color: '#1e3a8a', bg: '#dbeafe', tag: 'processing' as const },
  ob_only:  { label: 'OB만 존재', color: '#991b1b', bg: '#fee2e2', tag: 'error' as const },
}

const TX_LABEL = { in: '입고', out: '출고', adjustment: '조정' }

// 원인 자동 분류 표시 설정 (백엔드 root_cause와 1:1)
const ROOT_CAUSE_CFG: Record<string, { label: string; tag: string; desc: string }> = {
  adj_initial:      { label: '기초재고조정', tag: 'purple',  desc: 'IN/OUT은 일치, 조정(ADJ)만 차이 — BH 기초재고 설정 추정. 조정 제외 토글 또는 OB 조정으로 정리.' },
  set_bom:          { label: '세트분해',     tag: 'orange',  desc: '세트 수량 비율 차이 — BH 세트단위 / OB 개별단위 추정. 세트 BOM 등록 후 재대사.' },
  timing:           { label: '시점차이',     tag: 'gold',    desc: '반대 시스템의 다른 기간에 동일 SKU 존재 — 전산 입력 시점 차이. 누적 모드로 재확인 (실제 누락 아닐 가능성).' },
  product_unmapped: { label: '상품미매핑',   tag: 'geekblue',desc: '매핑 그룹에 없는 품목 — BH SKU ↔ OB 코드 매핑을 추가하면 해소됨.' },
  channel_unmapped: { label: '채널미매핑',   tag: 'cyan',    desc: '채널 매핑 규칙 누락 — 채널 매핑을 추가하면 해소됨.' },
  qty_mismatch:     { label: '수량불일치',   tag: 'volcano', desc: '양쪽 모두 기록되었으나 수량이 다름 — 한쪽 전산 오입력 의심. 전표 대조 필요.' },
  true_missing:     { label: '한쪽누락',     tag: 'red',     desc: '한쪽 시스템에만 기록 — 실제 누락/오입력 의심. 전표 확인 후 정리 필요.' },
}

// 행 단위 정리(전산정리) 상태 표시 설정
const CLEANUP_CFG: Record<string, { label: string; tag: string }> = {
  reviewing: { label: '검토중',   tag: 'processing' },
  resolved:  { label: '정리완료', tag: 'success' },
  hold:      { label: '보류',     tag: 'warning' },
  ignore:    { label: '무시',     tag: 'default' },
}
const CLEANUP_ORDER = ['reviewing', 'resolved', 'hold', 'ignore'] as const

function SummaryCards({ s, matchedCount }: { s: ReconcileSummary; matchedCount?: number }) {
  const cards = [
    { key: 'total', label: '전체', val: s.total, bg: '#f3f4f6', color: '#111827' },
    { key: 'ok', label: '정상', val: s.ok, bg: '#d1fae5', color: '#065f46' },
    { key: 'mismatch', label: '수량불일치', val: s.mismatch, bg: '#fef3c7', color: '#92400e' },
    { key: 'bh_only', label: 'BH만', val: s.bh_only, bg: '#dbeafe', color: '#1e3a8a' },
    { key: 'ob_only', label: 'OB만', val: s.ob_only, bg: '#fee2e2', color: '#991b1b' },
  ]
  return (
    <Row gutter={[10, 10]} style={{ marginBottom: 16 }}>
      {cards.map(m => (
        <Col key={m.key} span={4}>
          <div style={{ background: m.bg, borderRadius: 10, padding: '10px 14px', textAlign: 'center' }}>
            <div style={{ fontSize: '1.5rem', fontWeight: 800, color: m.color }}>{m.val}</div>
            <div style={{ fontSize: '0.75rem', color: m.color, fontWeight: 600 }}>{m.label}</div>
          </div>
        </Col>
      ))}
      {matchedCount != null && matchedCount > 0 && (
        <Col span={4}>
          <Tooltip title="입고매칭 확정으로 BH/OB 수량이 보정된 건수">
            <div style={{ background: '#ecfeff', borderRadius: 10, padding: '10px 14px', textAlign: 'center', border: '1px solid #a5f3fc', cursor: 'help' }}>
              <div style={{ fontSize: '1.5rem', fontWeight: 800, color: '#0891b2' }}>{matchedCount}</div>
              <div style={{ fontSize: '0.75rem', color: '#0891b2', fontWeight: 600 }}>✓ 매칭확정</div>
            </div>
          </Tooltip>
        </Col>
      )}
    </Row>
  )
}

// 이름 간단 정규화 (비교용)
function normName(s: string) {
  return (s || '').replace(/[^가-힣a-zA-Z0-9]/g, '').toLowerCase()
}

type CleanupActions = {
  set: (r: ReconcileRow, status: ReconcileCleanupStatus) => void
  editMemo: (r: ReconcileRow) => void
  clear: (r: ReconcileRow) => void
}

function ReconcileTable({ rows, showChannel, onRowClick, matchedNames, diffMatchedNames, unmatchedNames, stockMap, showBreakdown, cleanup }: {
  rows: ReconcileRow[]
  showChannel?: boolean
  onRowClick?: (r: ReconcileRow) => void
  matchedNames?: Set<string>
  diffMatchedNames?: Set<string>
  unmatchedNames?: Set<string>
  stockMap?: Map<string,{bh:number|null;ob:number|null;obAvail:number|null}>
  showBreakdown?: boolean  // total/product_all 모드에서 in/out/adj 분해 컬럼 표시
  cleanup?: CleanupActions // 행 단위 정리 상태 설정 액션
}) {
  const columns = [
    { title: '기간', dataIndex: 'period', key: 'period', width: 120 },
    { title: 'SKU', dataIndex: 'sku', key: 'sku', width: 130, ellipsis: true },
    { title: '상품명', dataIndex: 'name', key: 'name', ellipsis: true },
    {
      title: '박스히어로', dataIndex: 'bh_qty', key: 'bh_qty', width: 100, align: 'right' as const,
      render: (v: number | null) => v === null ? <span style={{ color: '#9ca3af' }}>—</span> : v.toLocaleString(),
    },
    {
      title: '아워박스', dataIndex: 'ob_qty', key: 'ob_qty', width: 100, align: 'right' as const,
      render: (v: number | null) => v === null ? <span style={{ color: '#9ca3af' }}>—</span> : v.toLocaleString(),
    },
    {
      title: '차이', key: 'diff', width: 100, align: 'right' as const,
      render: (_: unknown, r: ReconcileRow) => {
        if (r.bh_qty === null || r.ob_qty === null) return <span style={{ color: '#9ca3af' }}>—</span>
        const diff = r.bh_qty - r.ob_qty
        const cause = (r as unknown as Record<string,string>).mismatch_cause
        const setRatio = (r as unknown as Record<string,number>).set_ratio_hint
        return (
          <div style={{ textAlign: 'right' }}>
            {diff === 0
              ? <span style={{ color: '#10b981' }}>0</span>
              : <span style={{ color: '#ef4444', fontWeight: 600 }}>{diff > 0 ? '+' : ''}{diff}</span>
            }
            {cause === 'adj_only' && (
              <Tooltip title="IN/OUT은 일치, 조정(ADJ)만 다름 — BH 기초재고 설정 추정. 조정 제외 토글로 제거 가능.">
                <div style={{ fontSize: '0.62rem', color: '#7c3aed', cursor: 'help' }}>ADJ만 다름</div>
              </Tooltip>
            )}
            {cause === 'set_ratio' && setRatio && (
              <Tooltip title={`세트 수량 비율: ${setRatio}배 차이. BH는 세트 단위, OB는 개별 단위로 기록했을 수 있음.`}>
                <div style={{ fontSize: '0.62rem', color: '#d97706', cursor: 'help' }}>세트×{setRatio}?</div>
              </Tooltip>
            )}
          </div>
        )
      },
    },
    {
    ...(stockMap && stockMap.size > 0 ? [{
      title: '현재 재고', key: 'stock', width: 110,
      render: (_: unknown, r: ReconcileRow) => {
        const s = stockMap.get(normName(r.name))
        if (!s) return <span style={{color:'#d1d5db',fontSize:'0.72rem'}}>—</span>
        const diff = s.bh !== null && s.ob !== null ? s.bh - s.ob : null
        return (
          <Tooltip title={`BH: ${s.bh ?? '?'} / OB총: ${s.ob ?? '?'} / OB가용: ${s.obAvail ?? '?'}`}>
            <div style={{cursor:'help',textAlign:'right'}}>
              <div style={{fontSize:'0.78rem',fontWeight:600,color: diff===0?'#10b981':diff!==null&&Math.abs(diff)>100?'#ef4444':'#f59e0b'}}>
                BH {s.bh?.toLocaleString() ?? '—'}
              </div>
              <div style={{fontSize:'0.7rem',color:'#6b7280'}}>
                OB {s.ob?.toLocaleString() ?? '—'}
                {diff !== null && diff !== 0 && <span style={{color:'#ef4444',marginLeft:3}}>{diff>0?'+':''}{diff}</span>}
              </div>
            </div>
          </Tooltip>
        )
      }
    }] : []),
      title: '상태', key: 'status', width: 180,
      render: (_: unknown, r: ReconcileRow) => {
        const nn = normName(r.name)
        const isExact = matchedNames?.has(nn)
        // full-match 수량일치 품목은 compare 상태도 "정상"으로 오버라이드
        const effectiveStatus = (r.status !== 'ok' && isExact) ? 'ok' : r.status
        const cfg = STATUS_CFG[effectiveStatus as keyof typeof STATUS_CFG]
        const hasExtra = matchedNames || diffMatchedNames || unmatchedNames || r.matched_confirmed
        if (hasExtra) {
          const isDiff = !isExact && diffMatchedNames?.has(nn)
          const isUnmatched = !isExact && !isDiff && unmatchedNames?.has(nn)
          return (
            <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
              <Tag color={cfg.tag} style={{ margin: 0 }}>{cfg.label}</Tag>
              {r.matched_confirmed && (
                <Tooltip title="입고매칭 확정으로 BH/OB 수량이 보정됨">
                  <Tag color="cyan" style={{ margin: 0, fontSize: '0.68rem', cursor: 'help' }}>✓매칭확정</Tag>
                </Tooltip>
              )}
              {isExact && !r.matched_confirmed && (
                <Tooltip title="full-match에서 수량 일치 확인됨 (날짜 차이 포함)">
                  <Tag color="success" style={{ margin: 0, fontSize: '0.68rem', cursor: 'help' }}>✓full매칭</Tag>
                </Tooltip>
              )}
              {isDiff && (
                <Tooltip title="입고 매칭: 대응 건 있으나 수량 다름 — 차이 원인 확인 필요">
                  <Tag color="warning" style={{ margin: 0, fontSize: '0.68rem', cursor: 'help' }}>~수량차이</Tag>
                </Tooltip>
              )}
              {isUnmatched && (
                <Tooltip title="입고 매칭: 대응 건 없음 — 실제 누락 가능">
                  <Tag color="error" style={{ margin: 0, fontSize: '0.68rem', cursor: 'help' }}>⚠미매칭</Tag>
                </Tooltip>
              )}
            </div>
          )
        }
        return <Tag color={cfg.tag}>{cfg.label}</Tag>
      },
    },
    {
      title: () => <Tooltip title="규칙 기반 자동 원인 분류 — 전산 정리 방향 제시"><span>원인</span></Tooltip>,
      key: 'root_cause', width: 150,
      render: (_: unknown, r: ReconcileRow) => {
        if (!r.root_cause || r.root_cause === 'ok') return null
        const c = ROOT_CAUSE_CFG[r.root_cause]
        if (!c) return null
        const tip = r.correction?.action || r.fix_hint || c.desc
        return (
          <Tooltip title={<span>{tip}<br/><span style={{ opacity: 0.7, fontSize: '0.72rem' }}>행 클릭 → 수정안·복붙값</span></span>}>
            <Tag color={c.tag} style={{ margin: 0, cursor: 'help' }}>{c.label}</Tag>
          </Tooltip>
        )
      },
    },
    ...(cleanup ? [{
      title: () => <Tooltip title="전산 정리 진행 상태 — 정리완료/무시는 숨김 토글로 제외 가능"><span>정리</span></Tooltip>,
      key: 'cleanup', width: 120,
      render: (_: unknown, r: ReconcileRow) => {
        if (r.status === 'ok') return null
        const cur = r.cleanup_status
        const cfg = cur ? CLEANUP_CFG[cur] : null
        const menuItems = [
          ...CLEANUP_ORDER.map(s => ({ key: s, label: `${CLEANUP_CFG[s].label}${cur === s ? ' ✓' : ''}` })),
          { type: 'divider' as const },
          { key: '__memo', label: '📝 메모 편집…' },
          ...(cur ? [{ key: '__clear', label: '↩ 미처리로 초기화' }] : []),
        ]
        return (
          <Dropdown
            trigger={['click']}
            menu={{
              items: menuItems,
              onClick: ({ key }) => {
                if (key === '__memo') cleanup.editMemo(r)
                else if (key === '__clear') cleanup.clear(r)
                else cleanup.set(r, key as ReconcileCleanupStatus)
              },
            }}
          >
            <span onClick={e => e.stopPropagation()} style={{ cursor: 'pointer' }}>
              {cfg
                ? <Tooltip title={r.cleanup_memo ? `메모: ${r.cleanup_memo}${r.cleanup_assignee ? ` (${r.cleanup_assignee})` : ''}` : undefined}>
                    <Tag color={cfg.tag} style={{ margin: 0 }}>{cfg.label}{r.cleanup_memo ? ' 📝' : ''} ▾</Tag>
                  </Tooltip>
                : <Tag style={{ margin: 0, borderStyle: 'dashed', color: '#9ca3af', cursor: 'pointer' }}>정리 ▾</Tag>}
            </span>
          </Dropdown>
        )
      },
    }] : []),
    // ── 분해 컬럼 (total/product_all 모드에서 mismatch 원인 파악용) ──
    ...(showBreakdown ? [
      {
        title: () => <Tooltip title="입고(IN) 수량 비교"><span>입고</span></Tooltip>,
        key: 'in_breakdown', width: 110,
        render: (_: unknown, r: ReconcileRow) => {
          const bi = (r as unknown as Record<string,number>).bh_in_qty ?? 0
          const oi = (r as unknown as Record<string,number>).ob_in_qty ?? 0
          if (!bi && !oi) return null
          return (
            <div style={{ fontSize: '0.72rem', lineHeight: 1.4 }}>
              <div>BH <b style={{ color: bi===oi?'#10b981':'#f59e0b' }}>{bi.toLocaleString()}</b></div>
              <div>OB <b style={{ color: bi===oi?'#10b981':'#f59e0b' }}>{oi.toLocaleString()}</b></div>
            </div>
          )
        },
      },
      {
        title: () => <Tooltip title="출고(OUT) 수량 비교"><span>출고</span></Tooltip>,
        key: 'out_breakdown', width: 110,
        render: (_: unknown, r: ReconcileRow) => {
          const bo = (r as unknown as Record<string,number>).bh_out_qty ?? 0
          const oo = (r as unknown as Record<string,number>).ob_out_qty ?? 0
          if (!bo && !oo) return null
          return (
            <div style={{ fontSize: '0.72rem', lineHeight: 1.4 }}>
              <div>BH <b style={{ color: bo===oo?'#10b981':'#f59e0b' }}>{bo.toLocaleString()}</b></div>
              <div>OB <b style={{ color: bo===oo?'#10b981':'#f59e0b' }}>{oo.toLocaleString()}</b></div>
            </div>
          )
        },
      },
      {
        title: () => <Tooltip title="조정(ADJ) 수량 비교"><span>조정</span></Tooltip>,
        key: 'adj_breakdown', width: 110,
        render: (_: unknown, r: ReconcileRow) => {
          const ba = (r as unknown as Record<string,number>).bh_adj_qty ?? 0
          const oa = (r as unknown as Record<string,number>).ob_adj_qty ?? 0
          if (!ba && !oa) return null
          const cause = (r as unknown as Record<string,string>).mismatch_cause
          return (
            <Tooltip title={cause === 'adj_only' ? '⚠️ IN/OUT은 일치, ADJ만 다름 (BH 기초재고 설정 추정)' : undefined}>
              <div style={{ fontSize: '0.72rem', lineHeight: 1.4 }}>
                <div>BH <b style={{ color: ba===oa?'#10b981':cause==='adj_only'?'#7c3aed':'#ef4444' }}>{ba.toLocaleString()}</b></div>
                <div>OB <b style={{ color: ba===oa?'#10b981':cause==='adj_only'?'#7c3aed':'#ef4444' }}>{oa.toLocaleString()}</b></div>
                {cause === 'adj_only' && <div style={{ color: '#7c3aed', fontSize: '0.65rem' }}>ADJ만 다름</div>}
              </div>
            </Tooltip>
          )
        },
      },
    ] : []),
  ]

  // 채널 → 품목 2단계 소계 표시 모드
  if (showChannel) {
    // 1단계: 채널 그룹핑
    const chGrouped = new Map<string, ReconcileRow[]>()
    for (const r of rows) {
      const ch = r.channel || '채널미상'
      if (!chGrouped.has(ch)) chGrouped.set(ch, [])
      chGrouped.get(ch)!.push(r)
    }
    const channelOrder = Array.from(chGrouped.keys()).sort((a, b) => {
      const sa = chGrouped.get(a)!.reduce((s,r) => s+(r.bh_qty||0)+(r.ob_qty||0), 0)
      const sb = chGrouped.get(b)!.reduce((s,r) => s+(r.bh_qty||0)+(r.ob_qty||0), 0)
      return sb - sa
    })

    const SumBadge = ({ bh, ob }: { bh: number; ob: number }) => {
      const diff = bh - ob
      return (
        <span style={{ fontSize: '0.78rem', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span>BH <strong style={{ color: '#1e3a8a' }}>{bh.toLocaleString()}</strong></span>
          <span style={{ color: '#d1d5db' }}>vs</span>
          <span>OB <strong style={{ color: '#991b1b' }}>{ob.toLocaleString()}</strong></span>
          <strong style={{ color: diff===0?'#10b981':diff>0?'#2563eb':'#ef4444', marginLeft: 2 }}>
            {diff===0 ? '✓' : (diff>0?'+':'')+diff.toLocaleString()}
          </strong>
        </span>
      )
    }

    return (
      <div>
        {channelOrder.map(ch => {
          const chRows = chGrouped.get(ch)!
          const chBh = chRows.reduce((s,r) => s+(r.bh_qty||0), 0)
          const chOb = chRows.reduce((s,r) => s+(r.ob_qty||0), 0)
          const chDiff = chBh - chOb
          const chIssues = chRows.filter(r => r.status !== 'ok').length
          const chOk = chIssues === 0
          const chColor = chOk ? '#a7f3d0' : chDiff !== 0 ? '#fca5a5' : '#fed7aa'
          const chBg = chOk ? '#f0fdf4' : chDiff !== 0 ? '#fff1f2' : '#fff7ed'

          // 2단계: 채널 내 품목 그룹핑 (name 기준)
          const prodGrouped = new Map<string, ReconcileRow[]>()
          for (const r of chRows) {
            const prod = r.name || r.sku || '미상'
            if (!prodGrouped.has(prod)) prodGrouped.set(prod, [])
            prodGrouped.get(prod)!.push(r)
          }
          const prodOrder = Array.from(prodGrouped.keys()).sort((a, b) => {
            const sa = prodGrouped.get(a)!.reduce((s,r) => s+(r.bh_qty||0)+(r.ob_qty||0), 0)
            const sb = prodGrouped.get(b)!.reduce((s,r) => s+(r.bh_qty||0)+(r.ob_qty||0), 0)
            return sb - sa
          })

          return (
            <div key={ch} style={{ marginBottom: 16, borderRadius: 10, border: `1.5px solid ${chColor}`, overflow: 'hidden' }}>
              {/* ① 채널 소계 헤더 */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', background: chBg }}>
                <Tag color={ch==='채널미상'?'default':'blue'} style={{ margin: 0, fontSize: '0.82rem' }}>{ch}</Tag>
                <SumBadge bh={chBh} ob={chOb} />
                <span style={{ fontSize: '0.75rem', color: '#9ca3af' }}>{prodOrder.length}종 · {chRows.length}건</span>
                {chIssues > 0
                  ? <Tag color="warning" style={{ margin: 0, marginLeft: 'auto' }}>불일치 {chIssues}건</Tag>
                  : <span style={{ marginLeft: 'auto', fontSize: '0.75rem', color: '#10b981', fontWeight: 600 }}>✓ 채널 일치</span>}
              </div>

              {/* ② 품목별 서브그룹 */}
              {prodOrder.map(prod => {
                const pRows = prodGrouped.get(prod)!
                const pBh = pRows.reduce((s,r) => s+(r.bh_qty||0), 0)
                const pOb = pRows.reduce((s,r) => s+(r.ob_qty||0), 0)
                const pDiff = pBh - pOb
                const pOk = pRows.every(r => r.status === 'ok')
                return (
                  <div key={prod}>
                    {/* 품목 소계 행 */}
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: 10,
                      padding: '5px 14px 5px 24px',
                      background: pOk ? '#f9fafb' : '#fffbeb',
                      borderTop: `1px solid ${chColor}`,
                    }}>
                      <span style={{ fontWeight: 600, fontSize: '0.82rem', color: '#374151', flex: 1,
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={prod}>
                        {prod}
                      </span>
                      <SumBadge bh={pBh} ob={pOb} />
                      <span style={{ fontSize: '0.72rem', color: '#9ca3af' }}>{pRows.length}건</span>
                      {!pOk && <Tag color={pDiff!==0?'error':'warning'} style={{ margin: 0 }}>
                        {pDiff!==0 ? '수량차이' : '불일치'}
                      </Tag>}
                    </div>
                    {/* 품목 내 개별 행 */}
                    {pRows.map((r, ri) => {
                      const diff = (r.bh_qty??0) - (r.ob_qty??0)
                      const rowBg: Record<string,string> = { ok:'#fff', mismatch:'#fffbeb', bh_only:'#eff6ff', ob_only:'#fef2f2' }
                      return (
                        <div key={ri} onClick={() => onRowClick?.(r)}
                          style={{ display: 'flex', alignItems: 'center', gap: 6,
                            padding: '4px 14px 4px 36px', fontSize: '0.78rem',
                            borderTop: '1px solid #f3f4f6',
                            background: rowBg[r.status] || '#fff',
                            cursor: onRowClick ? 'pointer' : 'default',
                          }}>
                          <span style={{ color: '#9ca3af', minWidth: 90, flexShrink: 0 }}>{r.period}</span>
                          <span style={{ color: '#6b7280', minWidth: 110, flexShrink: 0, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{r.sku}</span>
                          <span style={{ flex: 1 }} />
                          <span style={{ minWidth: 80, textAlign: 'right', color: '#1e3a8a' }}>
                            {r.bh_qty !== null ? r.bh_qty.toLocaleString() : <span style={{color:'#d1d5db'}}>—</span>}
                          </span>
                          <span style={{ minWidth: 80, textAlign: 'right', color: '#991b1b' }}>
                            {r.ob_qty !== null ? r.ob_qty.toLocaleString() : <span style={{color:'#d1d5db'}}>—</span>}
                          </span>
                          <span style={{ minWidth: 70, textAlign: 'right', fontWeight: 600,
                            color: diff===0?'#10b981': '#ef4444' }}>
                            {r.bh_qty===null||r.ob_qty===null ? '—' : diff===0?'✓':(diff>0?'+':'')+diff}
                          </span>
                          <span style={{ minWidth: 120, textAlign: 'right', display:'flex', gap:3, justifyContent:'flex-end', flexWrap:'wrap' }}>
                            <Tag color={STATUS_CFG[r.status as keyof typeof STATUS_CFG]?.tag} style={{ margin: 0, fontSize: '0.7rem' }}>
                              {STATUS_CFG[r.status as keyof typeof STATUS_CFG]?.label}
                            </Tag>
                            {r.status !== 'ok' && (() => {
                              const nn = normName(r.name)
                              if (matchedNames?.has(nn)) return <Tag color="success" style={{margin:0,fontSize:'0.68rem'}}>✓수량일치</Tag>
                              if (diffMatchedNames?.has(nn)) return <Tag color="warning" style={{margin:0,fontSize:'0.68rem'}}>~수량차이</Tag>
                              if (unmatchedNames?.has(nn)) return <Tag color="error" style={{margin:0,fontSize:'0.68rem'}}>⚠미매칭</Tag>
                              return null
                            })()}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                )
              })}
            </div>
          )
        })}
        {rows.length === 0 && <div style={{ textAlign: 'center', padding: 32, color: '#9ca3af' }}>데이터 없음</div>}
      </div>
    )
  }

  return (
    <Table
      dataSource={rows}
      columns={columns}
      size="small"
      rowKey={(r, i) => `${r.period}-${r.sku}-${i}`}
      pagination={{ pageSize: 50, showSizeChanger: true }}
      onRow={(r) => ({
        onClick: () => onRowClick?.(r),
        style: { cursor: onRowClick ? 'pointer' : 'default' },
      })}
      rowClassName={(r) => {
        const map: Record<string, string> = {
          ok: '', mismatch: 'row-warn', bh_only: 'row-info', ob_only: 'row-error',
        }
        return map[r.status] || ''
      }}
      style={{ fontSize: '0.82rem' }}
    />
  )
}

interface DetailItem { side: string; date: string; qty: number; name: string; ref: string; extra: string }
interface DetailResult {
  period: string; sku: string; tx_type: string; channel: string
  bh_total: number; ob_total: number; bh_count: number; ob_count: number
  pairs: { bh: DetailItem; ob: DetailItem }[]
  bh_only: DetailItem[]; ob_only: DetailItem[]
}

interface Location { id: number; name: string }

export default function ReconcilePage({ config }: Props) {
  const [period, setPeriod] = useState<'day' | 'week' | 'month' | 'year'>('day')
  const [fromDate, setFromDate] = useState<string>(dayjs().subtract(7, 'day').format('YYYY-MM-DD'))
  const [toDate, setToDate] = useState<string>(dayjs().format('YYYY-MM-DD'))
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<ReconcileResult | null>(null)
  const [activeTab, setActiveTab] = useState('in')
  const [analyzing, setAnalyzing] = useState(false)
  const [analysisText, setAnalysisText] = useState<string>('')
  const [analysisError, setAnalysisError] = useState<string>('')
  const analysisRef = useRef<HTMLDivElement>(null)

  // 채널(위치) 필터
  const [locations, setLocations] = useState<Location[]>([])
  const [selectedLocIds, setSelectedLocIds] = useState<number[]>([])
  // 상품 매핑
  const [useMapping, setUseMapping] = useState(true)
  // 비교 모드: 기간별 / 기간 누적 / 재고역산 / 유형합산 / 스마트매칭
  const [mode, setMode] = useState<'period' | 'cumulative' | 'total' | 'product_all' | 'smart'>('period')
  // 스마트 매칭 결과
  type SmartPair = {
    match_grade: number; score: number
    bh_date: string; bh_name: string; bh_qty: number; bh_type: string; bh_partner: string; bh_sku: string
    ob_date: string; ob_name: string; ob_qty: number; ob_type: string; ob_channel: string; ob_sku: string
    qty_diff: number; date_gap: number
  }
  type SmartItem = { date: string; name: string; qty: number; tx_type: string; partner?: string; channel?: string; sku: string }
  type SmartSummary = {
    total_bh: number; total_ob: number; matched: number; bh_only: number; ob_only: number
    grade1: number; grade2: number; grade3: number
    match_rate_bh: number; match_rate_ob: number
  }
  type SmartResult = { matched: SmartPair[]; bh_only: SmartItem[]; ob_only: SmartItem[]; summary: SmartSummary; errors: string[] }
  const [smartResult, setSmartResult] = useState<SmartResult | null>(null)
  // 채널별 구분
  const [byChannel, setByChannel] = useState(true)
  // 입고 매칭 탭 (품목별 유사도 매칭)
  const [matchLoading, setMatchLoading] = useState(false)
  const [matchData, setMatchData] = useState<{
    total_bh: number; total_ob: number
    matched_count: number; exact_count: number; probable_count: number
    bh_only_count: number; ob_only_count: number
    match_rate_bh: number; match_rate_ob: number
    by_type: Record<string,{matched:number;bh_only:number;ob_only:number;bh_total:number;ob_total:number;bh_match_rate:number;ob_match_rate:number}>
    by_channel: {channel:string;bh_qty:number;ob_qty:number;diff:number;bh_matched_qty:number;ob_matched_qty:number;bh_match_rate:number;ob_match_rate:number}[]
    excluded_qty?: number; excluded_channels?: string[]
    stocktake_count?: number; stocktake_qty?: number
    bulk_init_count?: number; excluded_count?: number
    set_work_count?: number
    matched: unknown[]; bh_only: unknown[]; ob_only: unknown[]
  } | null>(null)
  const [matchTolerance, setMatchTolerance] = useState(4)
  const [matchMinScore, setMatchMinScore] = useState(60)
  const [matchTab, setMatchTab] = useState<'matched'|'bh_only'|'ob_only'>('matched')
  const [matchTxTypes, setMatchTxTypes] = useState<string[]>(['in','out','adjustment'])
  const [matchExclude, setMatchExclude] = useState('샘플(임박),샘플(정상소비기한)')
  const [matchAggregate, setMatchAggregate] = useState(true)
  const [wideMode, setWideMode] = useState(true)   // 전체 기간 탐색 기본 ON
  const [bhLookback, setBhLookback] = useState(7) // BH ±7일 탐색
  const [qtyTolerance, setQtyTolerance] = useState(10) // 수량 허용오차 % (0=완전일치)
  const [dayLookback, setDayLookback] = useState(0)  // 날짜 ±N일 허용 (일간 모드)
  const [excludeAdj, setExcludeAdj] = useState(false) // 조정 항목 제외 (BH 기초재고 노이즈 제거)
  const [bhAdjMaxQty, setBhAdjMaxQty] = useState(0)  // BH adj 임계값 (0=비활성, 5000=기초재고 자동 필터)
  // 수동 조정: {bh_idx: ob_idx} 오버라이드 맵 (자동 매칭 결과를 사용자가 변경)
  const [matchOverrides, setMatchOverrides] = useState<Record<number,number|null>>({})
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set())

  // 재고 현황 (캐시 — 비교 테이블에서도 사용)
  const [stockOpen, setStockOpen] = useState(false)
  const [stockLoading, setStockLoading] = useState(false)
  type StockCause = { type: string; qty: number|null; desc: string }
  type StockFlow = { bh:{in:number;out:number;adjustment:number}; ob:{in:number;out:number;adjustment:number}; from?:string; to?:string }
  type StockRow = {
    name:string; sku:string; ob_code:string; ob_codes?:string[]; bh_skus?:string[]; bh_names?:string[]; ob_names?:string[]
    bh_stock:number|null; ob_stock_total:number|null; ob_stock_available:number|null
    ob_unusable?:number; diff:number|null; diff_vs_total?:number|null; diff_vs_available?:number|null; residual?:number|null; causes?:StockCause[]
    flow?:StockFlow  // 주간 리포트에서 미리 계산된 거래 분해
    fm?:{ bh_only:FMItem[]; ob_only:FMItem[]; qty_diff:FMMatched[]; from?:string; to?:string }  // 사전계산된 개별 거래 매칭
  }
  const [stockData, setStockData] = useState<{
    total: number; ok_count: number; diff_count: number; need_trace_count?: number
    rows: StockRow[]
  } | null>(null)

  async function handleStock() {
    if (!config.api_token) return
    setStockOpen(true); setStockLoading(true); setStockData(null)
    refreshWeeklyList()
    try {
      const d = await getReconcileStock({
        token: config.api_token,
        location_ids: selectedLocIds.length ? selectedLocIds.join(',') : undefined,
        use_mapping: useMapping,
      })
      setStockData(d)
    } catch { setStockData(null) }
    finally { setStockLoading(false) }
  }

  // 거래처(채널)별 또는 제품별 입·출고·조정 비교
  type ProductSubRow = { product:string; bh_in:number; bh_out:number; bh_adj:number; ob_in:number; ob_out:number; ob_adj:number; diff_in:number; diff_out:number; diff_adj:number; kind:'bh_missing'|'diff'|'ob_bypass'|'match'|'unknown' }
  type ChannelRow = { channel:string; product?:string; bh_in:number; bh_out:number; bh_adj:number; ob_in:number; ob_out:number; ob_adj:number; diff_in:number; diff_out:number; diff_adj:number; kind:'bh_missing'|'diff'|'ob_bypass'|'match'|'unknown'; products?:ProductSubRow[] }
  const [cfOpen, setCfOpen] = useState(false)
  const [cfLoading, setCfLoading] = useState(false)
  const [cfData, setCfData] = useState<{rows:ChannelRow[]; from:string; to:string; group_by?:string; channel_mapped:boolean; ob_source:string}|null>(null)
  const [cfDays, setCfDays] = useState(7)
  const [cfGroupBy, setCfGroupBy] = useState<'channel'|'product'>('channel')
  const [cfProductFilter, setCfProductFilter] = useState('')
  const [cfExpanded, setCfExpanded] = useState<Set<string>>(new Set())
  async function fetchChannelFlow(days: number, groupBy?: 'channel'|'product', productFilter?: string) {
    if (!config.api_token) return
    const gb = groupBy ?? cfGroupBy
    const pf = productFilter ?? cfProductFilter
    const to = dayjs(); const from = to.subtract(days, 'day')
    setCfOpen(true); setCfLoading(true); setCfData(null)
    try {
      const d = await getChannelFlow({
        token: config.api_token,
        from_date: from.format('YYYY-MM-DD'), to_date: to.format('YYYY-MM-DD'),
        location_ids: selectedLocIds.length ? selectedLocIds.join(',') : '228640',
        group_by: gb,
        product_filter: gb === 'product' ? pf : undefined,
      })
      setCfData(d); setCfExpanded(new Set())
    } catch { message.error('비교 데이터 조회 실패'); setCfData(null) }
    finally { setCfLoading(false) }
  }

  // 주간 리포트
  const [weeklyList, setWeeklyList] = useState<{id:number;report_date:string;diff_count:number;need_trace_count:number;total:number}[]>([])
  const [weeklySaving, setWeeklySaving] = useState(false)
  async function refreshWeeklyList() {
    try { const d = await listWeeklyReports(); setWeeklyList(d.reports || []) } catch { /* ignore */ }
  }
  async function saveWeeklyReport() {
    if (!config.api_token) return
    setWeeklySaving(true)
    try {
      await createWeeklyReport({
        token: config.api_token,
        location_ids: selectedLocIds.length ? selectedLocIds.join(',') : '228640',
      })
      message.success('이번 주 재고 리포트가 저장되었습니다')
      await refreshWeeklyList()
    } catch { message.error('리포트 저장 실패') }
    finally { setWeeklySaving(false) }
  }
  async function loadWeeklyReport(id: number) {
    try {
      const d = await getWeeklyReport(id)
      setStockData({ total: d.summary?.total ?? d.total ?? 0, ok_count: d.summary?.ok_count ?? 0,
        diff_count: d.summary?.diff_count ?? 0, need_trace_count: d.summary?.need_trace_count ?? 0,
        rows: d.rows || [] })
      message.info(`${d.report_date} 리포트를 불러왔습니다`)
    } catch { message.error('리포트 불러오기 실패') }
  }

  // 재고 차이 행 클릭 → 차이 원인 상세 설명 + 그 품목의 기간 거래 추적
  const [stockTraceName, setStockTraceName] = useState<string | null>(null)
  const [stockTraceRow, setStockTraceRow] = useState<StockRow | null>(null)
  const [stockTracePairs, setStockTracePairs] = useState<{bh_qty:number;ob_qty:number;bh_date:string;ob_date:string;bh_name:string;ob_name:string;qty_diff:number}[]>([])
  // 재고현황 필터: 불용 외 차이(잔여)만 보기
  const [stockResidualOnly, setStockResidualOnly] = useState(false)
  const [stockCardFilter, setStockCardFilter] = useState<null|'ok'|'diff'|'trace'>(null)  // 상단 카드 클릭 필터
  const [stockCauseFilter, setStockCauseFilter] = useState<string[]>([])                   // 원인 태그 필터
  const [stockDirFilter, setStockDirFilter] = useState<null|'plus'|'minus'>(null)          // 차이 방향 필터
  // 재고현황 상품명 검색
  const [stockSearch, setStockSearch] = useState('')
  // 유형별 거래 비교 (입고/출고/조정 BH vs OB)
  type TxTotals = { in:number; out:number; move:number; adjustment:number }
  const [stockFlow, setStockFlow] = useState<{bh:TxTotals; ob:TxTotals; bh_rows:unknown[]; ob_rows:unknown[]}|null>(null)
  const [stockFlowLoading, setStockFlowLoading] = useState(false)
  const [stockFlowMonths, setStockFlowMonths] = useState(1)  // 거래 분석 기간(개월) — 기본 1개월(빠름), 필요시 늘림
  // OB 가용외 스냅샷 추적 — 가용→가용외(할당)가 언제 떨어지는지 시계열
  const [snaps, setSnaps] = useState<StockSnap[]|null>(null)
  const [snapsLoading, setSnapsLoading] = useState(false)
  const [snapCapturing, setSnapCapturing] = useState(false)
  function fetchSnaps(name: string, obCodes?: string[]) {
    setSnapsLoading(true); setSnaps(null)
    getStockSnapshots({ name, codes: (obCodes||[]).join(','), limit: 2000 })
      .then(d => setSnaps(d.series || []))
      .catch(() => setSnaps([]))
      .finally(() => setSnapsLoading(false))
  }
  async function captureSnapNow(name: string, obCodes?: string[]) {
    setSnapCapturing(true)
    try { await captureStockSnapshot(); fetchSnaps(name, obCodes) }
    catch { /* noop */ }
    finally { setSnapCapturing(false) }
  }
  // 거래 정밀 대사 — 재고 차이가 '어느 거래에서' 났는지 이벤트 단위 분해
  const [deepTrace, setDeepTrace] = useState<StockDiffTrace|null>(null)
  const [deepTraceLoading, setDeepTraceLoading] = useState(false)
  function fetchDeepTrace(row: StockRow, months: number) {
    if (!config.api_token) return
    const to = dayjs(); const from = to.subtract(months, 'month')
    setDeepTraceLoading(true); setDeepTrace(null)
    getStockDiffTrace({
      token: config.api_token, name: row.name,
      bh_skus: (row.bh_skus||[]).join(','), ob_codes: (row.ob_codes||[]).join(','),
      from_date: from.format('YYYY-MM-DD'), to_date: to.format('YYYY-MM-DD'),
      location_ids: selectedLocIds.length ? selectedLocIds.join(',') : '228640',
      bh_stock: row.bh_stock ?? undefined, ob_total: row.ob_stock_total ?? undefined,
      ob_unav: row.ob_unusable ?? 0,
    })
      .then(setDeepTrace)
      .catch(() => { message.error('거래 정밀 대사 실패'); setDeepTrace(null) })
      .finally(() => setDeepTraceLoading(false))
  }

  // 출고 채널 분해 (OB 경유 vs OB 미경유(직배송)) — 출고 차이 원인 자동 분류
  const [outDecomp, setOutDecomp] = useState<OutDecomp|null>(null)
  const [outDecompLoading, setOutDecompLoading] = useState(false)
  function fetchOutDecomp(name: string, months: number, skus?: string[], obCodes?: string[]) {
    if (!config.api_token) return
    const to = dayjs(); const from = to.subtract(months, 'month')
    setOutDecompLoading(true); setOutDecomp(null)
    getItemOutDecompose({
      token: config.api_token, name,
      bh_skus: (skus||[]).join(','), ob_codes: (obCodes||[]).join(','),
      from_date: from.format('YYYY-MM-DD'), to_date: to.format('YYYY-MM-DD'),
      location_ids: selectedLocIds.length ? selectedLocIds.join(',') : '228640',
    })
      .then(setOutDecomp)
      .catch(() => setOutDecomp(null))
      .finally(() => setOutDecompLoading(false))
  }
  // 개별 거래 매칭 (full-match) — 짝 안 맞는 거래로 전산오류 위치 찾기
  type FMItem = { date:string; name:string; qty:number; bh_type?:string; ob_type?:string }
  type FMMatched = { bh_date:string; ob_date:string; bh_qty:number; ob_qty:number; qty_diff:number; bh_name:string; ob_name:string; day_gap?:number; match_reason?:string }
  const [stockFM, setStockFM] = useState<{matched:FMMatched[]; bh_only:FMItem[]; ob_only:FMItem[]}|null>(null)
  const [stockFMLoading, setStockFMLoading] = useState(false)

  // 재고 차이 변화 추적 — 두 리포트(t1→t2) 사이 품목 차이가 왜 벌어졌는지 분해
  type DiffSnap = { date:string; bh_stock:number|null; ob_available:number|null; ob_total:number|null; ob_unusable:number|null; diff:number|null; diff_vs_available?:number|null }
  type DiffTrace = {
    name:string; t1:DiffSnap; t2:DiffSnap
    delta_diff:number; delta_diff_available?:number; delta_bh_stock:number; delta_ob_total:number; delta_ob_available:number; delta_ob_unusable:number
    unavail_returned?:number; prebook_bh?:number
    prebook?:{date:string; ship_date:string; channel:string; qty:number; memo:string}[]
    group_change?:{changed:boolean; t1_only_codes:string[]; t2_only_codes:string[]; t1_only_skus:string[]; t2_only_skus:string[]}
    flow:{bh_in:number;bh_out:number;bh_move:number;bh_adj:number;ob_in:number;ob_out:number;ob_adj:number}
    contrib:{net_stock_flow:number; ob_unavail_change:number}
    explained:number; residual:number
    bh_only:FMItem[]; ob_only:FMItem[]; qty_diff:FMMatched[]
  }
  const [diffTrace, setDiffTrace] = useState<DiffTrace|null>(null)
  const [diffTraceLoading, setDiffTraceLoading] = useState(false)
  const [diffT1Id, setDiffT1Id] = useState<number|null>(null)
  function fetchDiffTrace(name: string, t1Id: number) {
    if (!config.api_token || !t1Id) return
    setDiffTraceLoading(true); setDiffTrace(null)
    getStockDiffChange({
      token: config.api_token, name,
      report_t1_id: t1Id, report_t2_id: 0,  // t2=최신 저장 리포트
      location_ids: selectedLocIds.length ? selectedLocIds.join(',') : '228640',
      include_fm: true,
    })
      .then((d: DiffTrace) => setDiffTrace(d))
      .catch(() => message.error('차이 변화 분석 실패'))
      .finally(() => setDiffTraceLoading(false))
  }

  function fetchStockFM(months: number) {
    if (!config.api_token) return
    const to = dayjs(); const from = to.subtract(months, 'month')
    setStockFMLoading(true)
    getReconcileFullMatch({
      token: config.api_token,
      from_date: from.format('YYYY-MM-DD'), to_date: to.format('YYYY-MM-DD'),
      location_ids: selectedLocIds.length ? selectedLocIds.join(',') : undefined,
    })
      .then((d: { matched?: FMMatched[]; bh_only?: FMItem[]; ob_only?: FMItem[] }) =>
        setStockFM({ matched: d.matched||[], bh_only: d.bh_only||[], ob_only: d.ob_only||[] }))
      .catch(() => setStockFM(null))
      .finally(() => setStockFMLoading(false))
  }

  function fetchStockFlow(name: string, months: number, skus?: string[], obCodes?: string[]) {
    if (!config.api_token) return
    // 재고역산(total) compare 재활용 — OB는 ob_txs_cache로 캐싱되어 빠름 (item-search보다 훨씬 빠름)
    // compare total 행에 bh_in_qty/bh_out_qty/bh_adj_qty/ob_* 분해 필드가 들어있음
    const to = dayjs()
    const from = to.subtract(months, 'month')
    const nkey = normName(name)
    // SKU/OB코드도 매칭 키로 사용 (위치 필터 시 그룹명이 달라지면 이름만으로는 매칭 실패)
    const skuSet = new Set([...(skus||[]), ...(obCodes||[])].map(s=>String(s||'')).filter(Boolean))
    setStockFlowLoading(true)
    // 위치 필터를 재고 현황과 동일하게 적용 (기본 호법센터 228640).
    //   거래 비교와 재고 비교의 위치 기준을 일치시켜야 차이 방향/크기가 맞음.
    //   (과거 'OB 0 누락' 우려로 필터를 뺐으나, 실측 결과 OB 합계는 위치필터 ON/OFF 동일 —
    //    위치 필터는 BH 거래에만 적용돼 타센터(CJ 군포 등) 거래만 정상 제외됨. 검증: 2026-06-22)
    getReconcile({
      token: config.api_token,
      from_date: from.format('YYYY-MM-DD'), to_date: to.format('YYYY-MM-DD'),
      period: 'month', mode: 'total',
      location_ids: selectedLocIds.length ? selectedLocIds.join(',') : '228640',
      use_mapping: useMapping,
    })
      .then((d: { rows?: Record<string, unknown>[] }) => {
        const rows = d.rows || []
        // 같은 품목(norm 일치) 행들의 분해 합산
        // ⚠ compare total은 한 품목을 in/out/adj 3행으로 주는데 각 행에 동일한 분해값(sku 전체)이
        //   복제됨 → sku 중복 제거 후 합산해야 3배 부풀림 방지
        const acc = { bh:{in:0,out:0,move:0,adjustment:0}, ob:{in:0,out:0,move:0,adjustment:0} }
        const seen = new Set<string>()
        for (const r of rows) {
          const rSku = String(r.sku || '')
          const nameMatch = normName(String(r.name || '')) === nkey
          const skuMatch = skuSet.size > 0 && rSku && [...skuSet].some(s => rSku.includes(s) || s.includes(rSku))
          if (!nameMatch && !skuMatch) continue
          const dedupKey = rSku || String(r.name || '')
          if (seen.has(dedupKey)) continue
          seen.add(dedupKey)
          acc.bh.in += Number(r.bh_in_qty||0); acc.bh.out += Number(r.bh_out_qty||0); acc.bh.move += Number(r.bh_move_qty||0); acc.bh.adjustment += Number(r.bh_adj_qty||0)
          acc.ob.in += Number(r.ob_in_qty||0); acc.ob.out += Number(r.ob_out_qty||0); acc.ob.adjustment += Number(r.ob_adj_qty||0)
        }
        setStockFlow({ bh: acc.bh, ob: acc.ob, bh_rows: [], ob_rows: [] })
      })
      .catch(() => setStockFlow(null))
      .finally(() => setStockFlowLoading(false))
  }

  function handleStockRowClick(r: StockRow) {
    setStockTraceName(r.name)
    setStockTraceRow(r)
    setStockTracePairs([])
    setStockFlow(null)
    setOutDecomp(null)
    setSnaps(null)
    setDeepTrace(null)
    // 차이 변화 추적 초기화 — 기본 t1 = 현재 리포트(t2=최신) 직전 리포트
    setDiffTrace(null)
    setDiffT1Id(weeklyList.length > 1 ? weeklyList[1].id : (weeklyList[0]?.id ?? null))
    getMatchedPairs({ from_date: fromDate, to_date: toDate, name: r.name })
      .then(d => setStockTracePairs(d.pairs || []))
      .catch(() => setStockTracePairs([]))
    // 주간 리포트에 미리 계산된 거래 분해가 있으면 즉시 표시 (API 호출 없음)
    // 없으면 자동 호출하지 않음 — 사용자가 "분석 실행" 버튼을 눌러야 수집 (OB API 느림 대기 방지)
    if (r.flow) {
      setStockFlow({ bh: { move:0, ...r.flow.bh }, ob: { move:0, ...r.flow.ob }, bh_rows: [], ob_rows: [] })
    }
    setStockFlowLoading(false)
    // 주간 리포트에 사전계산된 개별 거래 매칭(fm)이 있으면 즉시 표시 (name 주입해 필터 통과)
    if (r.fm) {
      const nm = r.name
      setStockFM({
        bh_only: (r.fm.bh_only||[]).map(x=>({...x, name:nm})),
        ob_only: (r.fm.ob_only||[]).map(x=>({...x, name:nm})),
        matched: (r.fm.qty_diff||[]).map(x=>({...x, bh_name:nm, ob_name:nm})),
      })
    }
  }

  function exportStockCsv() {
    if (!stockData) return
    const hdr = ['상품명','BH재고','OB재고(총)','OB재고(가용)','OB가용외','차이(BH-총)','참고(BH-가용)','SKU','OB코드']
    const rows = stockData.rows.map(r => [
      r.name, r.bh_stock ?? '-', r.ob_stock_total ?? '-',
      r.ob_stock_available ?? '-', r.ob_unusable ?? '-', r.diff ?? '-',
      r.diff_vs_available ?? '-', r.sku, r.ob_code
    ])
    const csv = [hdr,...rows].map(r=>r.map(v=>`"${String(v??'').replace(/"/g,'""')}"`).join(',')).join('\n')
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob(['﻿'+csv],{type:'text/csv;charset=utf-8'}))
    a.download = `재고현황_${new Date().toISOString().slice(0,10)}.csv`; a.click(); URL.revokeObjectURL(a.href)
  }

  // 수량 대사 모달
  const [qtyGapOpen, setQtyGapOpen] = useState(false)
  const [qtyGapLoading, setQtyGapLoading] = useState(false)
  const [qtyGapData, setQtyGapData] = useState<{
    total_bh: number; total_ob: number; total_gap: number; bh_match_rate: number
    bh_missing_count: number; bh_missing_qty: number; ok_count: number
    excluded_channels: string[]
    bh_missing: {name:string;bh_qty:number;ob_qty:number;diff:number;channels:Record<string,number>;top_channel:string}[]
    ok: {name:string;bh_qty:number;ob_qty:number}[]
    bh_excess: {name:string;bh_qty:number;ob_qty:number;diff:number}[]
  } | null>(null)

  async function handleQtyGap() {
    if (!config.api_token) return
    setQtyGapOpen(true); setQtyGapLoading(true); setQtyGapData(null)
    try {
      const d = await getReconcileQtyGap({
        token: config.api_token, from_date: fromDate, to_date: toDate,
        exclude_channels: matchExclude,
      })
      setQtyGapData(d)
    } catch { setQtyGapData(null) }
    finally { setQtyGapLoading(false) }
  }

  function exportQtyGapCsv() {
    if (!qtyGapData) return
    const hdr = ['상태','상품명','BH출고','OB출고','차이(OB-BH)','주요채널','채널별상세']
    const rows = qtyGapData.bh_missing.map(r => [
      'BH미입력', r.name, r.bh_qty, r.ob_qty, r.diff, r.top_channel,
      Object.entries(r.channels).map(([c,q])=>`${c}:${q}`).join(' / ')
    ])
    qtyGapData.ok.forEach(r => rows.push(['일치', r.name, r.bh_qty, r.ob_qty, 0, '', '']))
    qtyGapData.bh_excess?.forEach(r => rows.push(['BH과다', r.name, r.bh_qty, r.ob_qty, r.diff, '', '']))
    const csv = [hdr,...rows].map(r=>r.map(v=>`"${String(v??'').replace(/"/g,'""')}"`).join(',')).join('\n')
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob(['﻿'+csv],{type:'text/csv;charset=utf-8'}))
    a.download = `출고수량대사_${fromDate}_${toDate}.csv`; a.click(); URL.revokeObjectURL(a.href)
  }

  // 재고 Map (이름 → {bh_stock, ob_stock_total, ob_stock_available})
  const stockMap = useMemo(() => {
    if (!stockData) return new Map()
    const m = new Map<string, {bh:number|null;ob:number|null;obAvail:number|null}>()
    for (const r of stockData.rows) {
      m.set(normName(r.name), {bh:r.bh_stock, ob:r.ob_stock_total, obAvail:r.ob_stock_available})
    }
    return m
  }, [stockData])

  // 매칭 요약 뷰 토글
  const [showMatchSummary, setShowMatchSummary] = useState(false)

  // 매칭 요약: 상품별 BH/OB 합계 + 매칭 상태 (matchData 있을 때만)
  const matchSummaryRows = useMemo(() => {
    if (!matchData || !result) return []
    type R = Record<string, unknown>
    // 매칭된 상품명 집합 (bh_name, ob_name 모두)
    const exactNm = new Set<string>()
    const diffNm = new Set<string>()
    for (const m of matchData.matched as R[]) {
      const qd = m.qty_diff as number
      const n1 = normName(m.bh_name as string), n2 = normName(m.ob_name as string)
      if (qd === 0) { exactNm.add(n1); exactNm.add(n2) }
      else { diffNm.add(n1); diffNm.add(n2) }
    }
    const matchedNm = new Set([...exactNm, ...diffNm])
    // 현재 탭 비교 결과를 상품별 합산
    const allRows = result.rows.filter((r: ReconcileRow) => r.tx_type === activeTab)
    const prodMap = new Map<string, { name: string; bh: number; ob: number; statuses: string[] }>()
    for (const r of allRows) {
      const k = normName(r.name) || normName(r.sku)
      if (!prodMap.has(k)) prodMap.set(k, { name: r.name || r.sku, bh: 0, ob: 0, statuses: [] })
      const e = prodMap.get(k)!
      e.bh += r.bh_qty ?? 0
      e.ob += r.ob_qty ?? 0
      e.statuses.push(r.status)
    }
    return Array.from(prodMap.entries()).map(([k, v]) => {
      const diff = v.bh - v.ob
      const isMatched = matchedNm.has(k)
      const hasOk = v.statuses.every(s => s === 'ok')
      const isExact = exactNm.has(k)
      const isDiff = !isExact && diffNm.has(k)
      return { name: v.name, bh: v.bh, ob: v.ob, diff, isMatched, isExact, isDiff, hasOk }
    }).sort((a, b) => {
      // 정렬: 실제 미매칭 → 수량차이 → 수량일치 → 정상
      const rankA = a.hasOk ? 3 : a.isExact ? 2 : a.isDiff ? 1 : 0
      const rankB = b.hasOk ? 3 : b.isExact ? 2 : b.isDiff ? 1 : 0
      if (rankA !== rankB) return rankA - rankB
      if (!a.hasOk && !a.isMatched && (b.hasOk || b.isMatched)) return -1
      if (!b.hasOk && !b.isMatched && (a.hasOk || a.isMatched)) return 1
      return Math.abs(b.diff) - Math.abs(a.diff)
    })
  }, [matchData, result, activeTab])

  // 입고 매칭 결과 → 비교 테이블에 표시할 이름 집합 (3단계 구분)
  const { exactMatchedNames, diffMatchedNames, unmatchedNameSet } = useMemo(() => {
    if (!matchData) return { exactMatchedNames: undefined, diffMatchedNames: undefined, unmatchedNameSet: undefined }
    type R = Record<string, unknown>
    const exact = new Set<string>()   // 수량 완전 일치
    const diff = new Set<string>()    // 대응 건 있지만 수량 다름
    const unmatched = new Set<string>()
    for (const m of matchData.matched as R[]) {
      const qd = m.qty_diff as number
      const nn1 = normName(m.bh_name as string)
      const nn2 = normName(m.ob_name as string)
      if (qd === 0) { exact.add(nn1); exact.add(nn2) }
      else { diff.add(nn1); diff.add(nn2) }
    }
    for (const b of matchData.bh_only as R[]) unmatched.add(normName(b.name as string))
    for (const o of matchData.ob_only as R[]) unmatched.add(normName(o.name as string))
    // exact 우선, diff가 있으면 unmatched에서 제거
    for (const n of exact) { diff.delete(n); unmatched.delete(n) }
    for (const n of diff) unmatched.delete(n)
    return { exactMatchedNames: exact, diffMatchedNames: diff, unmatchedNameSet: unmatched }
  }, [matchData])

  // full-match exactMatchedNames 반영 → summary 카운터 보정 (mismatch → ok 승격)
  const adjustedResult = useMemo(() => {
    if (!result || !exactMatchedNames || exactMatchedNames.size === 0) return result
    const boost = (rows: ReconcileRow[]) =>
      rows.filter(r => r.status === 'mismatch' && exactMatchedNames.has(normName(r.name))).length
    const adj = (s: ReconcileSummary, b: number) =>
      ({ ...s, ok: s.ok + b, mismatch: Math.max(0, s.mismatch - b) })
    const allRows = result.rows
    return {
      ...result,
      summary: {
        in:         adj(result.summary.in,         boost(allRows.filter(r => r.tx_type === 'in'))),
        out:        adj(result.summary.out,        boost(allRows.filter(r => r.tx_type === 'out'))),
        adjustment: adj(result.summary.adjustment, boost(allRows.filter(r => r.tx_type === 'adjustment'))),
        total:      adj(result.summary.total,      boost(allRows)),
      }
    }
  }, [result, exactMatchedNames])

  // 오버라이드 반영된 채널별 통계 (실시간 재계산)
  const effectiveChannelStats = useMemo(() => {
    if (!matchData) return []
    type MI = Record<string,unknown>

    // 전체 BH/OB 항목 수집 (matched + only)
    const allBh: {bh_idx:number; ch:string; qty:number}[] = []
    for (const m of matchData.matched as MI[])
      allBh.push({bh_idx:m.bh_idx as number, ch:(m.bh_partner as string)||'채널미상', qty:m.bh_qty as number})
    for (const b of matchData.bh_only as MI[])
      allBh.push({bh_idx:b.bh_idx as number, ch:(b.partner as string)||'채널미상', qty:b.qty as number})

    const allOb: {ob_idx:number; ch:string; qty:number}[] = []
    const obByIdx = new Map<number, {ch:string; qty:number}>()
    for (const m of matchData.matched as MI[]) {
      const e = {ob_idx:m.ob_idx as number, ch:(m.ob_channel as string)||'채널미상', qty:m.ob_qty as number}
      allOb.push(e); obByIdx.set(e.ob_idx, e)
    }
    for (const o of matchData.ob_only as MI[]) {
      const e = {ob_idx:o.ob_idx as number, ch:(o.channel as string)||'채널미상', qty:o.qty as number}
      allOb.push(e); obByIdx.set(e.ob_idx, e)
    }
    // bh_only 후보에서도 ob 정보 보강
    for (const b of matchData.bh_only as MI[]) {
      for (const c of (b.candidates as MI[])||[]) {
        const oi = c.ob_idx as number
        if (!obByIdx.has(oi))
          obByIdx.set(oi, {ch:(c.ob_channel as string)||'채널미상', qty:c.ob_qty as number})
      }
    }

    // 오버라이드 반영해 실제 매칭된 bh/ob idx 집합 결정
    const matchedBh = new Set<number>()
    const matchedOb = new Set<number>()
    for (const m of matchData.matched as MI[]) {
      const bi = m.bh_idx as number
      if (bi in matchOverrides) {
        const newOi = matchOverrides[bi]
        if (newOi !== null) { matchedBh.add(bi); matchedOb.add(newOi) }
      } else {
        matchedBh.add(bi); matchedOb.add(m.ob_idx as number)
      }
    }
    for (const b of matchData.bh_only as MI[]) {
      const bi = b.bh_idx as number
      const newOi = matchOverrides[bi]
      if (newOi !== undefined && newOi !== null) { matchedBh.add(bi); matchedOb.add(newOi) }
    }

    // 채널별 집계
    const stats = new Map<string,{bh:number;ob:number;mbh:number;mob:number}>()
    const get = (ch: string) => { if (!stats.has(ch)) stats.set(ch,{bh:0,ob:0,mbh:0,mob:0}); return stats.get(ch)! }
    for (const b of allBh) {
      get(b.ch).bh += b.qty
      if (matchedBh.has(b.bh_idx)) get(b.ch).mbh += b.qty
    }
    for (const o of allOb) {
      get(o.ch).ob += o.qty
      if (matchedOb.has(o.ob_idx)) get(o.ch).mob += o.qty
    }
    return Array.from(stats.entries())
      .map(([channel, s]) => ({
        channel,
        bh_qty: s.bh, ob_qty: s.ob, diff: s.bh - s.ob,
        bh_matched_qty: s.mbh, ob_matched_qty: s.mob,
        bh_match_rate: Math.round(s.mbh/Math.max(s.bh,1)*1000)/10,
        ob_match_rate: Math.round(s.mob/Math.max(s.ob,1)*1000)/10,
      }))
      .sort((a,b) => (b.bh_qty+b.ob_qty)-(a.bh_qty+a.ob_qty))
  }, [matchData, matchOverrides])

  async function handleMatch() {
    if (!config.api_token) return
    setMatchLoading(true); setMatchData(null)
    try {
      const d = await getReconcileFullMatch({
        token: config.api_token,
        from_date: fromDate, to_date: toDate,
        bh_lookback: bhLookback,
        min_score: matchMinScore,
        location_ids: selectedLocIds.length ? selectedLocIds.join(',') : undefined,
      })
      setMatchData(d)
    } catch { setMatchData(null) }
    finally { setMatchLoading(false) }
  }

  const [confirmLoading, setConfirmLoading] = useState(false)
  const [confirmedAt, setConfirmedAt] = useState<string | null>(null)

  async function confirmMatch() {
    if (!matchData) return
    const rawMatched = matchData.matched as Record<string, unknown>[]
    if (!rawMatched || rawMatched.length === 0) {
      message.warning('확정할 매칭 건이 없습니다')
      return
    }
    // full-match 구조: flat { bh_name, bh_date, bh_qty, ob_name, ob_date, ob_qty, ... }
    // → save_matched_pairs가 기대하는 { sku(=bh_name), bh_date, ob_date, bh_qty, ob_qty, ... } 로 변환
    const matched = rawMatched.map(m => {
      const bhQty = Number(m.bh_qty ?? 0)
      const obQty = Number(m.ob_qty ?? 0)
      return {
        sku: String(m.bh_name ?? ''),        // sku 대신 bh_name을 키로 사용
        bh_name: String(m.bh_name ?? ''),
        ob_name: String(m.ob_name ?? ''),
        bh_date: String(m.bh_date ?? ''),
        ob_date: String(m.ob_date ?? ''),
        bh_qty: bhQty,
        ob_qty: obQty,
        qty_diff: bhQty - obQty,
        status: String(m.status ?? 'matched'),
        ob_put_sno: String(m.ob_put_sno ?? ''),
        match_method: 'full_match',
      }
    }).filter(m => m.sku && m.bh_date)  // sku·날짜 없으면 제외
    setConfirmLoading(true)
    try {
      const resp = await fetch(`${import.meta.env.VITE_API_BASE ?? ''}/api/reconcile/match/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ matched, from_date: fromDate, to_date: toDate }),
      })
      if (!resp.ok) throw new Error(await resp.text())
      const data = await resp.json()
      message.success(`${data.saved}건 확정 저장 완료 — 비교 조회를 다시 실행하면 반영됩니다`)
      setConfirmedAt(new Date().toLocaleString())
      // 비교 조회 자동 재실행
      if (result) handleCompare()
    } catch (e: unknown) {
      message.error('확정 저장 실패: ' + String(e))
    } finally {
      setConfirmLoading(false)
    }
  }

  function exportMatchCsv() {
    if (!matchData) return
    const rows: unknown[][] = []
    const hdr = ['유형','점수','BH날짜','OB날짜','일차이','BH수량','OB수량','차이','상품(BH)','상품(OB)','OB입고번호','상태']
    ;(matchData.matched as Record<string,unknown>[]).forEach(r => rows.push(['매칭',r.score,r.bh_date,r.ob_date,r.day_gap,r.bh_qty,r.ob_qty,r.qty_diff,r.bh_name,r.ob_name,r.ob_put_sno,r.status]))
    ;(matchData.bh_only as Record<string,unknown>[]).forEach(r => rows.push(['BH단독','',r.date,'','',r.qty,'',r.qty,r.name,'','','bh_only']))
    ;(matchData.ob_only as Record<string,unknown>[]).forEach(r => rows.push(['OB단독','','',r.date,'','',r.qty,-(r.qty as number),'',r.name,r.put_sno,'ob_only']))
    const csv=[hdr,...rows].map(r=>r.map(v=>`"${String(v??'').replace(/"/g,'""')}"`).join(',')).join('\n')
    const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob(['﻿'+csv],{type:'text/csv;charset=utf-8'}))
    a.download=`입고매칭_${fromDate}_${toDate}.csv`; a.click(); URL.revokeObjectURL(a.href)
  }

  // 누락건 추출 모달
  const [missingOpen, setMissingOpen] = useState(false)
  const [missingLoading, setMissingLoading] = useState(false)
  const [missingData, setMissingData] = useState<{
    count: number; total_need_boxhero: number; total_need_ourbox: number
    rows: { tx_type: string; sku: string; channel: string; name: string; bh_qty: number; ob_qty: number; diff: number; need_boxhero: number; need_ourbox: number; dates: string[] }[]
  } | null>(null)
  const [missingTxType, setMissingTxType] = useState<'out'|'in'|'adjustment'|'all'>('out')

  // ── 건별 조회 ────────────────────────────────────────────────
  const [searchQuery, setSearchQuery] = useState('')
  const [searchLoading, setSearchLoading] = useState(false)
  const [searchResult, setSearchResult] = useState<{
    query: string; from_date: string; to_date: string; errors: string[]
    bh: {date:string;tx_type:string;name:string;qty:number;sku:string;memo:string;tx_id:number}[]
    ob: {date:string;tx_type:string;name:string;qty:number;prod_cd:string;channel:string;invoice:string}[]
    summary?: {
      bh_by_sku: {sku:string;name:string;in:number;out:number;adjustment:number}[]
      ob_by_code: {prod_cd:string;name:string;in:number;out:number;adjustment:number;mapped_sku:string}[]
      bh_total: {in:number;out:number;adjustment:number}
      ob_total: {in:number;out:number;adjustment:number}
      ob_dup_inbound: {qty:number;codes:string[];names:string[]}[]
    }
  } | null>(null)

  async function handleItemSearch() {
    if (!config.api_token || !searchQuery.trim()) return
    setSearchLoading(true); setSearchResult(null)
    try {
      const d = await getReconcileItemSearch({
        token: config.api_token,
        query: searchQuery.trim(),
        from_date: fromDate,
        to_date: toDate,
        location_ids: selectedLocIds.length ? selectedLocIds.join(',') : undefined,
      })
      setSearchResult(d)
    } catch { setSearchResult(null) }
    finally { setSearchLoading(false) }
  }

  async function handleMissing() {
    if (!result) return
    setMissingOpen(true)
    setMissingLoading(true)
    setMissingData(null)
    try {
      const d = await getReconcileMissing({ tx_type: missingTxType })
      setMissingData(d)
    } catch {
      setMissingData(null)
    } finally {
      setMissingLoading(false)
    }
  }

  function exportMissingCsv() {
    if (!missingData) return
    const header = ['구분', 'SKU', '상품명', '채널', 'BH수량', 'OB수량', '차이', 'BH추가필요', '날짜목록']
    const typeLabel: Record<string, string> = { out:'출고', in:'입고', adjustment:'조정' }
    const rows = missingData.rows.map(r => [
      typeLabel[r.tx_type] || r.tx_type, r.sku, r.name, r.channel || '',
      r.bh_qty, r.ob_qty, r.diff, r.need_boxhero, r.dates.join(' / '),
    ])
    const csv = [header, ...rows].map(r => r.map(v => `"${String(v).replace(/"/g,'""')}"`).join(',')).join('\n')
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob); a.download = `BH_입력누락_${new Date().toISOString().slice(0,10)}.csv`; a.click()
    URL.revokeObjectURL(a.href)
  }

  // 수정안 일괄 CSV — 미해결 행의 반자동 조치안 + 복붙용 값 추출
  function exportCorrectionsCsv() {
    if (!result) return
    const hidden = new Set(['resolved', 'ignore'])
    const rows = result.rows.filter(r =>
      r.status !== 'ok' && r.correction &&
      !hidden.has(r.cleanup_status || ''))
    if (rows.length === 0) { message.info('내보낼 미해결 수정안이 없습니다'); return }
    const TL: Record<string, string> = { in: '입고', out: '출고', adjustment: '조정' }
    const header = ['날짜', 'SKU', '상품명', '유형', '채널', 'BH수량', 'OB수량', '원인', '조치시스템', '작업', '수량', '조치내용', '정리상태', '메모']
    const body = rows.map(r => {
      const c = r.correction!
      return [
        r.period, r.sku, r.name, TL[r.tx_type] || r.tx_type, r.channel || '',
        r.bh_qty ?? '', r.ob_qty ?? '',
        r.root_cause && ROOT_CAUSE_CFG[r.root_cause] ? ROOT_CAUSE_CFG[r.root_cause].label : (r.root_cause || ''),
        c.system, c.op, c.qty, c.action,
        r.cleanup_status ? (CLEANUP_CFG[r.cleanup_status]?.label || r.cleanup_status) : '미처리',
        r.cleanup_memo || '',
      ]
    })
    const csv = [header, ...body].map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n')
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `재고대사_수정안_${fromDate}_${toDate}.csv`; a.click()
    URL.revokeObjectURL(a.href)
  }

  // 드릴다운 (행 클릭 → 개별 거래 매칭)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detail, setDetail] = useState<DetailResult | null>(null)
  const [detailRow, setDetailRow] = useState<ReconcileRow | null>(null)

  // 행별 매칭 근거 (확정 쌍 목록)
  type MatchedPairRow = { sku: string; bh_name: string; ob_name: string; bh_date: string; ob_date: string; bh_qty: number; ob_qty: number; qty_diff: number; status: string }
  const [rowPairs, setRowPairs] = useState<MatchedPairRow[]>([])

  async function handleRowClick(r: ReconcileRow) {
    setDrawerOpen(true)
    setDetailRow(r)
    setDetail(null)
    setDetailLoading(true)
    // 이 품목을 설명한 확정 매칭 쌍 조회 (검증용)
    setRowPairs([])
    getMatchedPairs({ from_date: fromDate, to_date: toDate, name: r.name || r.sku || '' })
      .then(d => setRowPairs(d.pairs || []))
      .catch(() => setRowPairs([]))
    try {
      const d = await getReconcileDetail({ period: r.period, sku: r.sku, tx_type: r.tx_type, channel: r.channel || '', bh_lookback: dayLookback > 0 ? dayLookback : 3 })
      setDetail(d)
    } catch (e: unknown) {
      // 캐시 없음(400) → 비교 조회를 자동으로 재실행 후 재시도
      const status = (e as { response?: { status?: number } })?.response?.status
      if (status === 400 && config.api_token) {
        try {
          // 백그라운드 compare 재실행 (UI 전환 없이 캐시만 채움)
          await getReconcile({
            token: config.api_token,
            from_date: fromDate,
            to_date: toDate,
            period,
            location_ids: selectedLocIds.length ? selectedLocIds.join(',') : undefined,
            use_mapping: useMapping,
            mode: (mode === 'product_all' ? 'total' : mode) as 'period' | 'cumulative' | 'total',
            merge_types: mode === 'product_all' ? true : undefined,
            by_channel: byChannel,
          })
          // 재시도
          const d = await getReconcileDetail({ period: r.period, sku: r.sku, tx_type: r.tx_type, channel: r.channel || '', bh_lookback: dayLookback > 0 ? dayLookback : 3 })
          setDetail(d)
        } catch {
          setDetail(null)
        }
      } else {
        setDetail(null)
      }
    } finally {
      setDetailLoading(false)
    }
  }

  useEffect(() => {
    if (config.api_token) {
      getLocations(config.api_token)
        .then((d: { locations?: Location[] }) => setLocations(d.locations || []))
        .catch(() => {})
    }
  }, [config.api_token])

  async function handleCompare() {
    if (!config.api_token) return
    setLoading(true)
    setAnalysisText('')
    setAnalysisError('')

    // 스마트 매칭 모드 — 별도 엔드포인트 호출
    if (mode === 'smart') {
      try {
        const data = await getSmartCompare({
          token: config.api_token,
          from_date: fromDate,
          to_date: toDate,
          location_ids: selectedLocIds.length ? selectedLocIds.join(',') : undefined,
          use_mapping: useMapping,
          qty_tolerance: qtyTolerance / 100,
          bh_lookback: bhLookback,
        })
        setSmartResult(data as SmartResult)
        setResult(null)
      } catch (e: unknown) {
        const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
        setSmartResult({ matched: [], bh_only: [], ob_only: [], summary: { total_bh:0,total_ob:0,matched:0,bh_only:0,ob_only:0,grade1:0,grade2:0,grade3:0,match_rate_bh:0,match_rate_ob:0 }, errors: [msg] })
      } finally {
        setLoading(false)
      }
      return
    }

    try {
      const effectiveMode = mode === 'product_all' ? 'total' : mode
      const data = await getReconcile({
        token: config.api_token,
        from_date: fromDate,
        to_date: toDate,
        period,
        location_ids: selectedLocIds.length ? selectedLocIds.join(',') : undefined,
        use_mapping: useMapping,
        mode: effectiveMode,
        merge_types: mode === 'product_all' ? true : undefined,
        by_channel: byChannel,
        qty_tolerance: qtyTolerance / 100,
        bh_lookback: dayLookback > 0 ? dayLookback : undefined,
        exclude_adj: excludeAdj || undefined,
        bh_adj_max_qty: bhAdjMaxQty > 0 ? bhAdjMaxQty : undefined,
      })
      setResult(data)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      setResult({ summary: { in: { total:0,ok:0,mismatch:0,bh_only:0,ob_only:0 }, out: { total:0,ok:0,mismatch:0,bh_only:0,ob_only:0 }, adjustment: { total:0,ok:0,mismatch:0,bh_only:0,ob_only:0 }, total: { total:0,ok:0,mismatch:0,bh_only:0,ob_only:0 } }, rows: [], has_ourbox: false, errors: [msg], period, from_date: fromDate, to_date: toDate })
    } finally {
      setLoading(false)
    }
  }

  async function handleAnalyze() {
    if (!result || !config.claude_api_key) return
    setAnalyzing(true)
    setAnalysisText('')
    setAnalysisError('')
    setTimeout(() => analysisRef.current?.scrollIntoView({ behavior: 'smooth' }), 100)
    await analyzeReconcile(
      {
        rows: result.rows,
        summary: result.summary,
        from_date: result.from_date,
        to_date: result.to_date,
        period: result.period,
        claude_api_key: config.claude_api_key || '',
        gemini_api_key: config.gemini_api_key || '',
        groq_api_key: config.groq_api_key || '',
      },
      (text) => setAnalysisText(prev => prev + text),
      () => setAnalyzing(false),
      (err) => { setAnalysisError(err); setAnalyzing(false) },
    )
  }

  // 원인 자동 분류 필터 ('' = 전체)
  const [causeFilter, setCauseFilter] = useState<string>('')

  // 정리완료/무시 행 숨김 토글 (클라이언트 측 필터, 즉시 반영)
  const [hideResolved, setHideResolved] = useState(false)
  // 정리 담당자 (마지막 사용값 기억)
  const [cleanupAssignee, setCleanupAssignee] = useState<string>(
    () => localStorage.getItem('recon_cleanup_assignee') || ''
  )
  // 메모 편집 모달
  const [memoModal, setMemoModal] = useState<{ row: ReconcileRow; memo: string; status: ReconcileCleanupStatus } | null>(null)

  const filteredRows = (type: string) =>
    (result?.rows || [])
      .filter(r => r.tx_type === type)
      .filter(r => !causeFilter || r.root_cause === causeFilter)
      .filter(r => !hideResolved || (r.cleanup_status !== 'resolved' && r.cleanup_status !== 'ignore'))

  // ── 정리 상태 저장/갱신 (낙관적 로컬 반영) ──────────────────────────
  function _patchRow(target: ReconcileRow, patch: Partial<ReconcileRow>) {
    setResult(prev => {
      if (!prev) return prev
      return {
        ...prev,
        rows: prev.rows.map(r =>
          r === target ||
          (r.tx_type === target.tx_type && r.sku === target.sku &&
           (r.channel || '') === (target.channel || '') && r.period === target.period)
            ? { ...r, ...patch } : r
        ),
      }
    })
  }

  async function applyCleanup(r: ReconcileRow, status: ReconcileCleanupStatus, memo?: string) {
    try {
      await setReconcileStatus({
        tx_type: r.tx_type, sku: r.sku, period: r.period, channel: r.channel || '',
        status, root_cause: r.root_cause || '', name: r.name,
        bh_qty: r.bh_qty, ob_qty: r.ob_qty,
        memo: memo ?? r.cleanup_memo ?? '', assignee: cleanupAssignee,
      })
      _patchRow(r, { cleanup_status: status, ...(memo !== undefined ? { cleanup_memo: memo } : {}), cleanup_assignee: cleanupAssignee })
      message.success(`정리 상태: ${CLEANUP_CFG[status].label}`)
    } catch (e: unknown) {
      message.error('정리 상태 저장 실패: ' + String(e))
    }
  }

  async function clearCleanup(r: ReconcileRow) {
    try {
      await clearReconcileStatus({ tx_type: r.tx_type, sku: r.sku, period: r.period, channel: r.channel || '' })
      _patchRow(r, { cleanup_status: undefined, cleanup_memo: '', cleanup_assignee: '' })
      message.success('미처리로 초기화')
    } catch (e: unknown) {
      message.error('초기화 실패: ' + String(e))
    }
  }

  const cleanupActions: CleanupActions = {
    set: (r, status) => applyCleanup(r, status),
    editMemo: (r) => setMemoModal({ row: r, memo: r.cleanup_memo || '', status: r.cleanup_status || 'reviewing' }),
    clear: (r) => clearCleanup(r),
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">재고 대사</h1>
        <p className="page-desc">박스히어로 ↔ 아워박스 Mate 입출고·조정 비교</p>
      </div>

      {/* 컨트롤 */}
      <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px 20px', marginBottom: 16, display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>기간 단위</div>
          <Radio.Group value={period} onChange={e => setPeriod(e.target.value)} size="small">
            <Radio.Button value="day">일별</Radio.Button>
            <Radio.Button value="week">주별</Radio.Button>
            <Radio.Button value="month">월별</Radio.Button>
            <Radio.Button value="year">년별</Radio.Button>
          </Radio.Group>
        </div>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>
            비교 방식
            <Tooltip title="기간별: 날짜×품목 비교. 누적: 누적 수량 비교(타이밍 상쇄). 재고역산: 날짜 무시·품목별 기간 전체 합산 비교 — BH/OB 재고가 일치하면 99% 정상 처리됩니다.">
              <span style={{ marginLeft: 4, color: '#9ca3af', cursor: 'help' }}>ⓘ</span>
            </Tooltip>
          </div>
          <Radio.Group value={mode} onChange={e => setMode(e.target.value)} size="small">
            <Radio.Button value="period">기간별</Radio.Button>
            <Radio.Button value="cumulative">기간 누적</Radio.Button>
            <Radio.Button value="total" style={mode === 'total' ? { background: '#d1fae5', borderColor: '#059669', color: '#065f46', fontWeight: 700 } : {}}>재고역산</Radio.Button>
            <Tooltip title="날짜·유형 무관 품목별 순수량 합산 비교 (입고-출고+조정 net 수량)">
              <Radio.Button value="product_all" style={mode === 'product_all' ? { background: '#ede9fe', borderColor: '#7c3aed', color: '#4c1d95', fontWeight: 700 } : {}}>유형합산</Radio.Button>
            </Tooltip>
            <Tooltip title="날짜 무관 1:1 건별 자동 매칭 — 품목+수량 기준, 유형·거래처로 변별. BH/OB 전체 내역을 스캔해 매칭">
              <Radio.Button value="smart" style={mode === 'smart' ? { background: '#ecfdf5', borderColor: '#059669', color: '#065f46', fontWeight: 700 } : {}}>🔗 스마트</Radio.Button>
            </Tooltip>
          </Radio.Group>
        </div>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>시작일</div>
          <DatePicker
            size="small"
            value={dayjs(fromDate)}
            onChange={d => d && setFromDate(d.format('YYYY-MM-DD'))}
            allowClear={false}
          />
        </div>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>종료일</div>
          <DatePicker
            size="small"
            value={dayjs(toDate)}
            onChange={d => d && setToDate(d.format('YYYY-MM-DD'))}
            allowClear={false}
          />
        </div>

        {/* 채널(위치) 필터 */}
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>
            <ApartmentOutlined style={{ marginRight: 4 }} />채널 필터 (BH 위치)
          </div>
          <Select
            mode="multiple"
            size="small"
            style={{ minWidth: 200, maxWidth: 320 }}
            placeholder="전체 채널 (미선택 시 전체)"
            allowClear
            value={selectedLocIds}
            onChange={setSelectedLocIds}
            options={locations.map(l => ({ value: l.id, label: l.name }))}
            maxTagCount={2}
            disabled={!config.api_token || locations.length === 0}
          />
        </div>

        {/* 상품 매핑 토글 */}
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>
            <TagsOutlined style={{ marginRight: 4 }} />상품 매핑 적용
          </div>
          <Tooltip title="상품 매핑 페이지에 저장된 OB↔BH 이름 매핑을 비교 시 자동 적용합니다">
            <Switch
              size="small"
              checked={useMapping}
              onChange={setUseMapping}
              checkedChildren="ON"
              unCheckedChildren="OFF"
            />
          </Tooltip>
        </div>

        {/* 채널별 구분 토글 */}
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>
            <ApartmentOutlined style={{ marginRight: 4 }} />채널별 구분
          </div>
          <Tooltip title="OB 채널 ↔ BH memo 키워드 매핑(상품 매핑 → 채널 매핑)을 사용해 채널 단위로 비교합니다">
            <Switch
              size="small"
              checked={byChannel}
              onChange={setByChannel}
              checkedChildren="ON"
              unCheckedChildren="OFF"
            />
          </Tooltip>
        </div>

        {/* 수량 허용오차 */}
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>
            수량 허용오차
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Slider min={0} max={20} value={qtyTolerance} onChange={v => setQtyTolerance(v as number)}
              style={{ width: 80 }} tooltip={{ formatter: v => `±${v}%` }} />
            <span style={{ fontSize: '0.78rem', fontWeight: 600 }}>±{qtyTolerance}%</span>
          </div>
        </div>

        {/* 조정 제외 */}
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>
            조정 제외
            <Tooltip title="BH 기초재고 설정 등 adj(조정) 항목을 비교에서 제외합니다. IN/OUT 수량만 대조할 때 사용. 재고역산·유형합산 모드에서 효과적입니다.">
              <span style={{ marginLeft: 4, color: '#9ca3af', cursor: 'help' }}>ⓘ</span>
            </Tooltip>
          </div>
          <Switch
            size="small"
            checked={excludeAdj}
            onChange={v => setExcludeAdj(v)}
            checkedChildren="제외"
            unCheckedChildren="포함"
            style={excludeAdj ? { background: '#7c3aed' } : {}}
          />
        </div>

        {/* 기초재고 임계값 필터 */}
        <div>
          <Tooltip title="이 수량 이상의 BH 조정 항목(기초재고 설정 추정)을 양측에서 자동 제외합니다. 5000~10000 권장. 0=비활성">
            <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>
              기초재고 임계값 <span style={{ color: '#d97706' }}>{bhAdjMaxQty > 0 ? `≥${bhAdjMaxQty.toLocaleString()}` : '(비활성)'}</span>
            </div>
          </Tooltip>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Input
              size="small"
              type="number"
              min={0}
              style={{ width: 90, fontSize: '0.78rem' }}
              value={bhAdjMaxQty || ''}
              placeholder="0=꺼짐"
              onChange={e => {
                const v = parseInt(e.target.value || '0', 10)
                setBhAdjMaxQty(isNaN(v) ? 0 : v)
              }}
            />
            {bhAdjMaxQty > 0 && (
              <Button size="small" type="text" style={{ padding: 0, color: '#9ca3af', fontSize: '0.72rem' }}
                onClick={() => setBhAdjMaxQty(0)}>초기화</Button>
            )}
          </div>
        </div>

        {/* 최적 설정 프리셋 */}
        <Tooltip title="재고역산(total) + 기초재고 자동제외(5000) + 허용오차 10% — 매칭률 최대화 설정">
          <Button
            size="small"
            style={{ background: '#f0fdf4', border: '1px solid #bbf7d0', color: '#15803d', fontWeight: 600, fontSize: '0.72rem' }}
            onClick={() => {
              setMode('total')
              setExcludeAdj(false)
              setBhAdjMaxQty(5000)
              setQtyTolerance(10)
              setDayLookback(0)
            }}
          >
            ✨ 최적 설정 자동
          </Button>
        </Tooltip>

        {/* 날짜 허용 범위 (일간 모드에서만 유효) */}
        {period === 'day' && (
          <div>
            <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>
              날짜 허용 ±N일
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <Slider min={0} max={7} value={dayLookback} onChange={v => setDayLookback(v as number)}
                style={{ width: 80 }} tooltip={{ formatter: v => v === 0 ? '정확일치' : `±${v}일` }} />
              <span style={{ fontSize: '0.78rem', fontWeight: 600 }}>
                {dayLookback === 0 ? '정확일치' : `±${dayLookback}일`}
              </span>
            </div>
          </div>
        )}

        <div style={{ marginTop: 20, display: 'flex', gap: 8, flexDirection: 'column' }}>
          <Button
            type="primary"
            icon={<SearchOutlined />}
            onClick={handleCompare}
            loading={loading}
            disabled={!config.api_token}
          >
            비교 조회
          </Button>
          <Tooltip title="캐시를 지우고 BH·OB API에서 최신 데이터를 새로 수집합니다 (첫 조회처럼 시간이 걸립니다)">
            <Button
              size="small"
              icon={<span>🔄</span>}
              onClick={async () => {
                try {
                  await fetch(
                    `${import.meta.env.VITE_API_BASE ?? ''}/api/reconcile/cache?from_date=${fromDate}&to_date=${toDate}`,
                    { method: 'DELETE' }
                  )
                  message.success('캐시 초기화 완료 — 다음 조회 시 최신 데이터로 수집합니다')
                } catch {
                  message.error('캐시 초기화 실패')
                }
              }}
            >
              캐시 새로고침
            </Button>
          </Tooltip>
          <Tooltip title="비교 조회 후 상품별 BH↔OB 총량 차이를 추출합니다. BoxHero 입력 누락 건을 CSV로 다운로드할 수 있습니다.">
            <Button icon={<ExportOutlined />} onClick={handleMissing} disabled={!result} size="small">
              누락건 추출
            </Button>
          </Tooltip>
          <Tooltip title="출고 전기간 총량 대사 — 상품별 BH vs OB 합계 비교, BH 미입력 목록 CSV">
            <Button icon={<ExportOutlined />} onClick={handleQtyGap} disabled={!config.api_token} size="small" type="dashed">
              출고 수량 대사
            </Button>
          </Tooltip>
          <Tooltip title="BH·OB 현재 재고 비교 — 잔여 재고 현황">
            <Button onClick={handleStock} disabled={!config.api_token} size="small" type="dashed">
              📦 재고 현황
            </Button>
          </Tooltip>
          <Tooltip title="거래처(채널)별 입·출고·조정을 BH vs OB로 비교 — 채널 단위 누락 즉시 식별">
            <Button onClick={()=>fetchChannelFlow(cfDays)} disabled={!config.api_token} size="small" type="dashed">
              🏷️ 거래처별 비교
            </Button>
          </Tooltip>
        </div>
        {!config.api_token && (
          <div style={{ marginTop: 20, fontSize: '0.8rem', color: '#ef4444' }}>
            ⚠️ 박스히어로 API 토큰이 필요합니다 (설정에서 연결)
          </div>
        )}
      </div>

      {/* ── 건별 조회 패널 ────────────────────────────────────── */}
      <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px 20px', marginBottom: 16 }}>
        <div style={{ fontWeight: 700, fontSize: '0.92rem', marginBottom: 10 }}>🔍 건별 조회</div>
        <div style={{ fontSize: '0.78rem', color: '#6b7280', marginBottom: 10 }}>
          품목명 일부를 입력하면 위 날짜 범위 내 BH·OB 양쪽에서 해당 건을 검색합니다.
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Input
            placeholder="품목명 검색 (예: 이알히나, 면역, 메노포즈)"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            onPressEnter={handleItemSearch}
            style={{ maxWidth: 360 }}
            allowClear
          />
          <Button type="primary" onClick={handleItemSearch} loading={searchLoading} disabled={!config.api_token || !searchQuery.trim()}>
            조회
          </Button>
        </div>

        {searchLoading && <div style={{ padding: '16px 0', color: '#6b7280', fontSize: '0.85rem' }}><Spin size="small" style={{ marginRight: 8 }} />조회 중...</div>}

        {searchResult && !searchLoading && (
          <div style={{ marginTop: 14 }}>
            {searchResult.errors.length > 0 && <Alert type="warning" message={searchResult.errors.join(' / ')} style={{ marginBottom: 8 }} showIcon />}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              {/* BH 결과 */}
              <div>
                <div style={{ fontWeight: 600, fontSize: '0.82rem', color: '#1e3a8a', marginBottom: 6 }}>
                  📦 BoxHero ({searchResult.bh.length}건)
                </div>
                {searchResult.bh.length === 0 ? (
                  <div style={{ color: '#9ca3af', fontSize: '0.8rem' }}>해당 건 없음</div>
                ) : (
                  <div style={{ maxHeight: 320, overflowY: 'auto' }}>
                    {searchResult.bh.map((r, i) => (
                      <div key={i} style={{ padding: '6px 10px', borderBottom: '1px solid #f3f4f6', fontSize: '0.8rem', display: 'flex', flexDirection: 'column', gap: 2 }}>
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                          <Tag color={r.tx_type === 'in' ? 'blue' : r.tx_type === 'out' ? 'volcano' : 'gold'} style={{ margin: 0, fontSize: '0.68rem' }}>
                            {r.tx_type === 'in' ? '입고' : r.tx_type === 'out' ? '출고' : '조정'}
                          </Tag>
                          <span style={{ color: '#374151', fontWeight: 600 }}>{r.name}</span>
                          <span style={{ marginLeft: 'auto', fontWeight: 700, color: '#1e3a8a' }}>{r.qty > 0 ? '+' : ''}{r.qty.toLocaleString()}</span>
                        </div>
                        <div style={{ color: '#6b7280', fontSize: '0.72rem', display: 'flex', gap: 8 }}>
                          <span>📅 {r.date}</span>
                          {r.memo && <span>📝 {r.memo}</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              {/* OB 결과 */}
              <div>
                <div style={{ fontWeight: 600, fontSize: '0.82rem', color: '#991b1b', marginBottom: 6 }}>
                  🏭 OurBox ({searchResult.ob.length}건)
                </div>
                {searchResult.ob.length === 0 ? (
                  <div style={{ color: '#9ca3af', fontSize: '0.8rem' }}>해당 건 없음</div>
                ) : (
                  <div style={{ maxHeight: 320, overflowY: 'auto' }}>
                    {searchResult.ob.map((r, i) => (
                      <div key={i} style={{ padding: '6px 10px', borderBottom: '1px solid #f3f4f6', fontSize: '0.8rem', display: 'flex', flexDirection: 'column', gap: 2 }}>
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                          <Tag color={r.tx_type === 'in' ? 'blue' : r.tx_type === 'out' ? 'volcano' : 'gold'} style={{ margin: 0, fontSize: '0.68rem' }}>
                            {r.tx_type === 'in' ? '입고' : r.tx_type === 'out' ? '출고' : '조정'}
                          </Tag>
                          <span style={{ color: '#374151', fontWeight: 600 }}>{r.name}</span>
                          <span style={{ marginLeft: 'auto', fontWeight: 700, color: '#991b1b' }}>{r.qty > 0 ? '+' : ''}{r.qty.toLocaleString()}</span>
                        </div>
                        <div style={{ color: '#6b7280', fontSize: '0.72rem', display: 'flex', gap: 8 }}>
                          <span>📅 {r.date}</span>
                          {r.channel && <span>🏪 {r.channel}</span>}
                          {r.invoice && <span>🧾 {r.invoice}</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
            {/* 수량 합계 비교 */}
            {(searchResult.bh.length > 0 || searchResult.ob.length > 0) && (() => {
              const types = ['in', 'out', 'adjustment'] as const
              const typeLabel = { in: '입고', out: '출고', adjustment: '조정' }
              return (
                <div style={{ marginTop: 10, padding: '10px 14px', background: '#f9fafb', borderRadius: 8, fontSize: '0.8rem' }}>
                  <div style={{ fontWeight: 600, marginBottom: 6 }}>수량 합계 비교</div>
                  <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
                    {types.map(t => {
                      const bTotal = searchResult.bh.filter(r => r.tx_type === t).reduce((s, r) => s + r.qty, 0)
                      const oTotal = searchResult.ob.filter(r => r.tx_type === t).reduce((s, r) => s + r.qty, 0)
                      if (bTotal === 0 && oTotal === 0) return null
                      const ok = bTotal === oTotal
                      return (
                        <div key={t} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                          <Tag color={t === 'in' ? 'blue' : t === 'out' ? 'volcano' : 'gold'} style={{ margin: 0 }}>{typeLabel[t]}</Tag>
                          <span style={{ color: '#1e3a8a' }}>BH {bTotal.toLocaleString()}</span>
                          <span style={{ color: '#9ca3af' }}>vs</span>
                          <span style={{ color: '#991b1b' }}>OB {oTotal.toLocaleString()}</span>
                          <Tag color={ok ? 'success' : 'error'} style={{ margin: 0 }}>{ok ? '✓일치' : `차이 ${bTotal - oTotal > 0 ? '+' : ''}${(bTotal - oTotal).toLocaleString()}`}</Tag>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })()}

            {/* OB 중복 입고 경고 */}
            {searchResult.summary && searchResult.summary.ob_dup_inbound.length > 0 && (
              <Alert
                type="warning"
                showIcon
                style={{ marginTop: 10 }}
                message="OB 중복 입고 의심"
                description={
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {searchResult.summary.ob_dup_inbound.map((d, i) => (
                      <li key={i} style={{ fontSize: '0.8rem' }}>
                        입고 수량 <b>{d.qty.toLocaleString()}</b>가 {d.codes.length}개 코드에 동일 기록: {d.codes.join(', ')} ({d.names.join(' / ')})
                      </li>
                    ))}
                  </ul>
                }
              />
            )}

            {/* 코드/SKU별 집계 테이블 */}
            {searchResult.summary && (
              <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 10 }}>
                <div style={{ flex: 1, minWidth: 300 }}>
                  <div style={{ fontWeight: 600, fontSize: '0.82rem', marginBottom: 4, color: '#1e3a8a' }}>📦 BH SKU별 집계</div>
                  <table style={{ width: '100%', fontSize: '0.76rem', borderCollapse: 'collapse' }}>
                    <thead><tr style={{ background: '#eff6ff' }}>
                      <th style={{ textAlign: 'left', padding: '3px 6px' }}>SKU</th>
                      <th style={{ textAlign: 'right', padding: '3px 6px' }}>입고</th>
                      <th style={{ textAlign: 'right', padding: '3px 6px' }}>출고</th>
                      <th style={{ textAlign: 'right', padding: '3px 6px' }}>조정</th>
                    </tr></thead>
                    <tbody>
                      {searchResult.summary.bh_by_sku.map((r, i) => (
                        <tr key={i} style={{ borderBottom: '1px solid #f1f5f9' }}>
                          <td style={{ padding: '3px 6px' }} title={r.name}>{r.sku || '-'}</td>
                          <td style={{ textAlign: 'right', padding: '3px 6px' }}>{r.in.toLocaleString()}</td>
                          <td style={{ textAlign: 'right', padding: '3px 6px' }}>{r.out.toLocaleString()}</td>
                          <td style={{ textAlign: 'right', padding: '3px 6px' }}>{r.adjustment.toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div style={{ flex: 1, minWidth: 300 }}>
                  <div style={{ fontWeight: 600, fontSize: '0.82rem', marginBottom: 4, color: '#991b1b' }}>🏭 OB 코드별 집계</div>
                  <table style={{ width: '100%', fontSize: '0.76rem', borderCollapse: 'collapse' }}>
                    <thead><tr style={{ background: '#fef2f2' }}>
                      <th style={{ textAlign: 'left', padding: '3px 6px' }}>코드 → SKU</th>
                      <th style={{ textAlign: 'right', padding: '3px 6px' }}>입고</th>
                      <th style={{ textAlign: 'right', padding: '3px 6px' }}>출고</th>
                      <th style={{ textAlign: 'right', padding: '3px 6px' }}>조정</th>
                    </tr></thead>
                    <tbody>
                      {searchResult.summary.ob_by_code.map((r, i) => (
                        <tr key={i} style={{ borderBottom: '1px solid #f1f5f9' }}>
                          <td style={{ padding: '3px 6px' }} title={r.name}>
                            {r.prod_cd || '-'}
                            {r.mapped_sku && <Tag color="green" style={{ margin: '0 0 0 4px', fontSize: '0.68rem' }}>→{r.mapped_sku}</Tag>}
                          </td>
                          <td style={{ textAlign: 'right', padding: '3px 6px' }}>{r.in.toLocaleString()}</td>
                          <td style={{ textAlign: 'right', padding: '3px 6px' }}>{r.out.toLocaleString()}</td>
                          <td style={{ textAlign: 'right', padding: '3px 6px' }}>{r.adjustment.toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {loading && (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" />
          <div style={{ marginTop: 12, color: '#6b7280', fontSize: '0.85rem' }}>
            데이터 수집 중... 아워박스 스크래핑으로 인해 최대 2~3분 소요될 수 있습니다.
          </div>
        </div>
      )}

      {/* ── 스마트 매칭 결과 ─────────────────────────────── */}
      {smartResult && !loading && mode === 'smart' && (() => {
        const sr = smartResult
        const GRADE_CFG = {
          1: { label: '✅ 완전', color: '#065f46', bg: '#d1fae5', border: '#6ee7b7' },
          2: { label: '⚠️ 거래처차이', color: '#92400e', bg: '#fef3c7', border: '#fcd34d' },
          3: { label: '🔶 유형차이', color: '#7c2d12', bg: '#ffedd5', border: '#fdba74' },
        } as Record<number, {label:string;color:string;bg:string;border:string}>
        const TX_KO: Record<string,string> = { in: '입고', out: '출고', adjustment: '조정' }
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {/* 에러 */}
            {sr.errors.filter(e => !e.startsWith('[정보]') && !e.startsWith('[캐시]')).map((e, i) => (
              <Alert key={i} type="error" message={e} showIcon style={{ marginBottom: 4 }} />
            ))}

            {/* 요약 카드 */}
            <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px 20px' }}>
              <div style={{ fontWeight: 700, fontSize: '0.95rem', marginBottom: 12 }}>🔗 스마트 매칭 결과</div>
              <Row gutter={[10,10]} style={{ marginBottom: 10 }}>
                {[
                  { label: 'BH 전체', val: sr.summary.total_bh, bg: '#f3f4f6', color: '#111827' },
                  { label: 'OB 전체', val: sr.summary.total_ob, bg: '#f3f4f6', color: '#111827' },
                  { label: '매칭', val: sr.summary.matched, bg: '#d1fae5', color: '#065f46' },
                  { label: 'BH 미매칭', val: sr.summary.bh_only, bg: '#dbeafe', color: '#1e3a8a' },
                  { label: 'OB 미매칭', val: sr.summary.ob_only, bg: '#fee2e2', color: '#991b1b' },
                ].map(c => (
                  <Col key={c.label} span={4}>
                    <div style={{ background: c.bg, borderRadius: 10, padding: '10px 14px', textAlign: 'center' }}>
                      <div style={{ fontSize: '1.4rem', fontWeight: 800, color: c.color }}>{c.val}</div>
                      <div style={{ fontSize: '0.72rem', color: c.color, fontWeight: 600 }}>{c.label}</div>
                    </div>
                  </Col>
                ))}
              </Row>
              <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', fontSize: '0.82rem' }}>
                <span style={{ fontWeight: 700 }}>매칭률: BH <span style={{ color: '#059669', fontSize: '1.1rem' }}>{Math.round(sr.summary.match_rate_bh * 100)}%</span> / OB <span style={{ color: '#059669', fontSize: '1.1rem' }}>{Math.round(sr.summary.match_rate_ob * 100)}%</span></span>
                <span style={{ color: '#6b7280' }}>|</span>
                {[1,2,3].map(g => (
                  <Tag key={g} style={{ background: GRADE_CFG[g].bg, borderColor: GRADE_CFG[g].border, color: GRADE_CFG[g].color, fontWeight: 600 }}>
                    {GRADE_CFG[g].label} {sr.matched.filter(m => m.match_grade === g).length}건
                  </Tag>
                ))}
              </div>
            </div>

            {/* 매칭 테이블 */}
            {sr.matched.length > 0 && (
              <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px 20px' }}>
                <div style={{ fontWeight: 700, fontSize: '0.88rem', marginBottom: 10 }}>매칭된 건 ({sr.matched.length}건)</div>
                <Table
                  size="small"
                  dataSource={sr.matched.map((m, i) => ({ ...m, key: i }))}
                  scroll={{ x: 1100 }}
                  pagination={{ pageSize: 50, showSizeChanger: true }}
                  rowClassName={(r: unknown) => {
                    const m = r as SmartPair
                    if (m.match_grade === 2) return 'row-warn'
                    if (m.match_grade === 3) return 'row-cross-type'
                    return ''
                  }}
                  columns={[
                    { title: '등급', width: 90, render: (_: unknown, r: unknown) => {
                      const m = r as SmartPair
                      const cfg = GRADE_CFG[m.match_grade]
                      return <Tag style={{ background: cfg.bg, borderColor: cfg.border, color: cfg.color, fontSize: '0.68rem' }}>{cfg.label}</Tag>
                    }},
                    { title: 'BH 날짜', dataIndex: 'bh_date', width: 90, render: (v: string) => v?.slice(0,10) },
                    { title: 'BH 품목', dataIndex: 'bh_name', ellipsis: true, render: (v: string, r: unknown) => {
                      const m = r as SmartPair
                      return <Tooltip title={`SKU: ${m.bh_sku}`}>{v}</Tooltip>
                    }},
                    { title: 'BH 수량', dataIndex: 'bh_qty', width: 70, align: 'right' as const },
                    { title: 'BH 유형', dataIndex: 'bh_type', width: 60, render: (v: string) => <Tag>{TX_KO[v]||v}</Tag> },
                    { title: 'BH 거래처', dataIndex: 'bh_partner', width: 90, ellipsis: true },
                    { title: '', width: 24, render: () => <span style={{ color: '#9ca3af' }}>↔</span> },
                    { title: 'OB 날짜', dataIndex: 'ob_date', width: 90, render: (v: string) => v?.slice(0,10) },
                    { title: 'OB 품목', dataIndex: 'ob_name', ellipsis: true, render: (v: string, r: unknown) => {
                      const m = r as SmartPair
                      return <Tooltip title={`SKU: ${m.ob_sku}`}>{v}</Tooltip>
                    }},
                    { title: 'OB 수량', dataIndex: 'ob_qty', width: 70, align: 'right' as const },
                    { title: 'OB 유형', dataIndex: 'ob_type', width: 60, render: (v: string) => <Tag>{TX_KO[v]||v}</Tag> },
                    { title: 'OB 채널', dataIndex: 'ob_channel', width: 90, ellipsis: true },
                    { title: '수량차', dataIndex: 'qty_diff', width: 65, align: 'right' as const, render: (v: number) =>
                      v !== 0 ? <span style={{ color: '#dc2626', fontWeight: 600 }}>{v > 0 ? '+' : ''}{v}</span> : <span style={{ color: '#059669' }}>0</span>
                    },
                    { title: '날짜차', dataIndex: 'date_gap', width: 65, align: 'right' as const, render: (v: number) =>
                      v > 0 ? <span style={{ color: '#6b7280' }}>±{v}일</span> : <span style={{ color: '#059669' }}>동일</span>
                    },
                  ]}
                />
              </div>
            )}

            {/* 미매칭 */}
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              {sr.bh_only.length > 0 && (
                <div style={{ flex: 1, minWidth: 380, background: '#eff6ff', borderRadius: 14, border: '1px solid #bfdbfe', padding: '14px 18px' }}>
                  <div style={{ fontWeight: 700, fontSize: '0.88rem', color: '#1e3a8a', marginBottom: 8 }}>❌ BH 미매칭 ({sr.bh_only.length}건) — OB에 없는 거래</div>
                  <Table size="small" dataSource={sr.bh_only.map((r,i)=>({...r,key:i}))} pagination={{ pageSize: 20 }}
                    columns={[
                      { title: '날짜', dataIndex: 'date', width: 90 },
                      { title: '품목', dataIndex: 'name', ellipsis: true },
                      { title: '수량', dataIndex: 'qty', width: 65, align: 'right' as const },
                      { title: '유형', dataIndex: 'tx_type', width: 60, render: (v: string) => <Tag>{TX_KO[v]||v}</Tag> },
                      { title: '거래처', dataIndex: 'partner', width: 90, ellipsis: true },
                    ]} />
                </div>
              )}
              {sr.ob_only.length > 0 && (
                <div style={{ flex: 1, minWidth: 380, background: '#fef2f2', borderRadius: 14, border: '1px solid #fecaca', padding: '14px 18px' }}>
                  <div style={{ fontWeight: 700, fontSize: '0.88rem', color: '#991b1b', marginBottom: 8 }}>❌ OB 미매칭 ({sr.ob_only.length}건) — BH에 없는 거래</div>
                  <Table size="small" dataSource={sr.ob_only.map((r,i)=>({...r,key:i}))} pagination={{ pageSize: 20 }}
                    columns={[
                      { title: '날짜', dataIndex: 'date', width: 90 },
                      { title: '품목', dataIndex: 'name', ellipsis: true },
                      { title: '수량', dataIndex: 'qty', width: 65, align: 'right' as const },
                      { title: '유형', dataIndex: 'tx_type', width: 60, render: (v: string) => <Tag>{TX_KO[v]||v}</Tag> },
                      { title: '채널', dataIndex: 'channel', width: 90, ellipsis: true },
                    ]} />
                </div>
              )}
            </div>
          </div>
        )
      })()}

      {result && !loading && mode !== 'smart' && (
        <>
          {result.mode === 'cumulative' && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 8 }}
              message="기간 누적 비교 모드"
              description="각 행의 수량은 시작일부터 해당 기간까지의 누적값입니다. 전산 시점 차이는 누적으로 상쇄되며, 누적값(차이)이 벌어지기 시작하는 행이 실제 오차 발생 지점입니다."
            />
          )}
          {/* 데이터 수집 건수 현황 (항목 6) */}
          {result.data_counts && (
            <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
              {(['bh', 'ob'] as const).map(side => {
                const dc = (result.data_counts as Record<string, Record<string, number>>)[side]
                const label = side === 'bh' ? '📦 BH' : '🏬 OB'
                const total = (dc.in || 0) + (dc.out || 0) + (dc.adj || 0)
                return (
                  <Tooltip key={side} title={`입고 ${dc.in}건 / 출고 ${dc.out}건 / 조정 ${dc.adj}건`}>
                    <Tag color={side === 'bh' ? 'blue' : 'green'} style={{ fontSize: '0.78rem', padding: '2px 8px' }}>
                      {label} 수집: {total.toLocaleString()}건 (입 {dc.in} / 출 {dc.out} / 조 {dc.adj})
                    </Tag>
                  </Tooltip>
                )
              })}
            </div>
          )}

          {/* 오류 */}
          {result.errors.length > 0 && (() => {
            const infoMsgs = result.errors.filter(e => e.startsWith('[정보]'))
            const errMsgs  = result.errors.filter(e => !e.startsWith('[정보]'))
            return (
              <>
                {infoMsgs.length > 0 && (
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 8 }}
                    message="수집 방식 안내"
                    description={
                      <ul style={{ margin: 0, paddingLeft: 18 }}>
                        {infoMsgs.map((e, i) => (
                          <li key={i} style={{ fontSize: '0.8rem' }}>{e.replace('[정보] ', '')}</li>
                        ))}
                      </ul>
                    }
                  />
                )}
                {errMsgs.length > 0 && (
                  <Alert
                    type="warning"
                    icon={<WarningOutlined />}
                    showIcon
                    style={{ marginBottom: 8 }}
                    message="일부 데이터 수집 실패"
                    description={
                      <ul style={{ margin: 0, paddingLeft: 18 }}>
                        {errMsgs.map((e, i) => <li key={i} style={{ fontSize: '0.8rem' }}>{e}</li>)}
                      </ul>
                    }
                  />
                )}
              </>
            )
          })()}

          {!result.has_ourbox && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message="아워박스 Mate 미연결"
              description="설정에서 아워박스 ID/PW를 입력하면 양쪽 비교가 가능합니다. 현재는 박스히어로 데이터만 표시됩니다."
            />
          )}

          {/* 조회 조건 배지 */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            {result.filtered_locations && result.filtered_locations.length > 0 && (
              <Tag icon={<ApartmentOutlined />} color="blue">
                채널 필터: {result.filtered_locations
                  .map(id => locations.find(l => l.id === id)?.name || String(id))
                  .join(', ')}
              </Tag>
            )}
            {result.mapping_applied !== undefined && (
              <Tag icon={<TagsOutlined />} color={result.mapping_applied > 0 ? 'green' : 'default'}>
                상품 매핑 {result.mapping_applied > 0 ? `${result.mapping_applied}건 적용됨` : '미적용'}
              </Tag>
            )}
          </div>

          {/* 미매핑 상품 알림 패널 */}
          {(() => {
            type UnmItem = { sku: string; name: string; qty: number; tx_type: string }
            type SuggestMatch = { score: number; ob_sku: string; ob_name: string; ob_qty: number }
            type SuggestItem = { bh_sku: string; bh_name: string; bh_qty: number; matches: SuggestMatch[] }
            const unm = (result as unknown as Record<string,unknown>).unmapped_products as {
              bh_only: UnmItem[]; ob_only: UnmItem[]; suggestions?: SuggestItem[]
            } | undefined
            if (!unm || (unm.bh_only.length === 0 && unm.ob_only.length === 0)) return null
            const bhItems = unm.bh_only
            const obItems = unm.ob_only
            const suggestions = unm.suggestions || []
            return (
              <div style={{ background: '#fef9c3', borderRadius: 14, border: '1px solid #fde047', padding: '14px 20px', marginBottom: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                  <span style={{ fontWeight: 700, fontSize: '0.92rem' }}>🔗 미매핑 상품 — OB↔BH 연결하면 불일치 해소 가능</span>
                  <Tag color="warning">BH {bhItems.length}개 · OB {obItems.length}개</Tag>
                  <Button size="small" type="primary" ghost
                    onClick={() => window.dispatchEvent(new CustomEvent('navigate-mapping'))}
                    style={{ fontSize: '0.78rem' }}>
                    매핑 페이지로 →
                  </Button>
                </div>
                {/* 자동 이름 유사도 제안 */}
                {suggestions.length > 0 && (
                  <div style={{ background: '#fffbeb', border: '1px solid #fcd34d', borderRadius: 8, padding: '10px 14px', marginBottom: 12 }}>
                    <div style={{ fontSize: '0.78rem', fontWeight: 700, color: '#92400e', marginBottom: 8 }}>
                      💡 이름 유사도 자동 제안 — 매핑 페이지에서 연결하세요 ({suggestions.length}쌍)
                    </div>
                    {suggestions.map(sg => (
                      <div key={sg.bh_sku} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
                        <div style={{ minWidth: 200 }}>
                          <Tag color="blue" style={{ fontSize: '0.65rem', margin: 0 }}>{sg.bh_sku}</Tag>
                          <span style={{ fontSize: '0.76rem', marginLeft: 4 }}>{sg.bh_name}</span>
                          <span style={{ fontSize: '0.68rem', color: '#6b7280', marginLeft: 4 }}>({sg.bh_qty.toLocaleString()})</span>
                        </div>
                        <span style={{ color: '#d97706', fontSize: '0.8rem', fontWeight: 600 }}>↔</span>
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                          {sg.matches.map((m, mi) => (
                            <Tooltip key={mi} title={`유사도 ${Math.round(m.score * 100)}%`}>
                              <Tag color={m.score >= 0.6 ? 'orange' : 'default'}
                                style={{ fontSize: '0.68rem', cursor: 'default', margin: 0 }}>
                                {m.ob_name} <span style={{ color: '#9ca3af' }}>({Math.round(m.score * 100)}%)</span>
                              </Tag>
                            </Tooltip>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap' }}>
                  {bhItems.length > 0 && (
                    <div style={{ flex: 1, minWidth: 280 }}>
                      <div style={{ fontSize: '0.72rem', color: '#1e3a8a', fontWeight: 600, marginBottom: 6 }}>📦 BH만 존재 (OB 연결 필요)</div>
                      {bhItems.map(it => (
                        <div key={it.sku} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                          <Tag color="blue" style={{ fontSize: '0.68rem', fontFamily: 'monospace', margin: 0 }}>{it.sku}</Tag>
                          <span style={{ fontSize: '0.78rem', flex: 1 }}>{it.name}</span>
                          <span style={{ fontSize: '0.72rem', color: '#6b7280' }}>{it.qty.toLocaleString()}개</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {obItems.length > 0 && (
                    <div style={{ flex: 1, minWidth: 280 }}>
                      <div style={{ fontSize: '0.72rem', color: '#991b1b', fontWeight: 600, marginBottom: 6 }}>🏬 OB만 존재 (BH 연결 필요)</div>
                      {obItems.map(it => (
                        <div key={it.sku} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                          <Tag color="red" style={{ fontSize: '0.68rem', fontFamily: 'monospace', margin: 0 }}>{it.sku}</Tag>
                          <span style={{ fontSize: '0.78rem', flex: 1 }}>{it.name}</span>
                          <span style={{ fontSize: '0.72rem', color: '#6b7280' }}>{it.qty.toLocaleString()}개</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )
          })()}

          {/* 전체 요약 */}
          <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px 20px', marginBottom: 16 }}>
            <div style={{ fontWeight: 700, fontSize: '0.92rem', marginBottom: 12 }}>전체 요약</div>
            <SummaryCards
              s={(adjustedResult ?? result).summary.total}
              matchedCount={(adjustedResult ?? result).rows.filter(r => r.matched_confirmed).length || undefined}
            />
            {/* mismatch 원인별 해소 가능 건수 힌트 */}
            {(() => {
              const misRows = (adjustedResult ?? result).rows.filter(r => r.status === 'mismatch')
              if (misRows.length === 0) return null
              const adjOnly = misRows.filter(r => (r as unknown as Record<string,unknown>).mismatch_cause === 'adj_only').length
              const setRatio = misRows.filter(r => (r as unknown as Record<string,unknown>).mismatch_cause === 'set_ratio').length
              const inOutDiff = misRows.filter(r => (r as unknown as Record<string,unknown>).mismatch_cause === 'in_out_diff').length
              const mixed = misRows.filter(r => (r as unknown as Record<string,unknown>).mismatch_cause === 'mixed').length
              const hasBhAdjHint = adjOnly > 0 || inOutDiff > 0
              return (
                <div style={{ marginTop: 10, padding: '10px 14px', background: '#f8fafc', borderRadius: 8, border: '1px solid #e2e8f0', fontSize: '0.78rem', lineHeight: 2 }}>
                  <span style={{ fontWeight: 700, color: '#374151' }}>🔍 mismatch {misRows.length}건 원인 분석: </span>
                  {adjOnly > 0 && <Tag color="purple">ADJ만 다름 {adjOnly}건 → 조정 제외 토글로 해소 가능</Tag>}
                  {setRatio > 0 && <Tag color="orange">세트 비율 {setRatio}건 → 매핑에서 세트 배수 연결 필요</Tag>}
                  {inOutDiff > 0 && <Tag color="blue">IN/OUT 차이 {inOutDiff}건 {bhAdjMaxQty === 0 ? '→ 기초재고 임계값 설정 시 일부 해소' : ''}</Tag>}
                  {mixed > 0 && <Tag color="default">복합 원인 {mixed}건</Tag>}
                  {hasBhAdjHint && bhAdjMaxQty === 0 && !excludeAdj && (
                    <div style={{ marginTop: 4, color: '#92400e', fontWeight: 600 }}>
                      💡 "최적 설정 자동" 버튼을 누르면 기초재고 임계값 5000 + 허용오차 10%가 적용됩니다
                    </div>
                  )}
                </div>
              )
            })()}
            {/* 원인 자동 분류 요약 (규칙 기반, 전 모드 공통) — 칩 클릭 시 해당 원인만 필터 */}
            {(() => {
              const rcs = (result as ReconcileResult).root_cause_summary
              if (!rcs) return null
              const entries = Object.entries(rcs).filter(([k]) => k !== 'ok' && ROOT_CAUSE_CFG[k])
              if (entries.length === 0) return null
              return (
                <div style={{ marginTop: 10, padding: '10px 14px', background: '#fefce8', borderRadius: 8, border: '1px solid #fde68a', fontSize: '0.78rem', lineHeight: 2.2 }}>
                  <span style={{ fontWeight: 700, color: '#374151' }}>🧭 원인 자동 분류 (전산 정리 방향): </span>
                  {entries.map(([k, v]) => {
                    const c = ROOT_CAUSE_CFG[k]
                    const active = causeFilter === k
                    return (
                      <Tooltip key={k} title={c.desc}>
                        <Tag
                          color={active ? c.tag : undefined}
                          style={{ cursor: 'pointer', margin: '0 4px 0 0', fontWeight: active ? 700 : 400,
                                   borderColor: active ? undefined : '#d1d5db' }}
                          onClick={() => setCauseFilter(active ? '' : k)}
                        >
                          {c.label} {v.count}건
                        </Tag>
                      </Tooltip>
                    )
                  })}
                  {causeFilter && (
                    <Tag color="default" style={{ cursor: 'pointer', margin: 0 }} onClick={() => setCauseFilter('')}>
                      ✕ 필터 해제
                    </Tag>
                  )}
                  <div style={{ marginTop: 4, color: '#92400e', fontSize: '0.72rem', fontWeight: 600 }}>
                    💡 칩을 클릭하면 아래 표가 해당 원인만 표시됩니다. 각 행의 "원인" 컬럼에 정리 방향이 표시됩니다.
                  </div>
                </div>
              )
            })()}
            {/* 전산 정리 진행 현황 + 담당자/숨김 토글 */}
            {(() => {
              const issueCount = result.rows.filter(r => r.status !== 'ok').length
              const cc = (result as ReconcileResult).cleanup_counts || {}
              const done = (cc.resolved || 0) + (cc.ignore || 0)
              return (
                <div style={{ marginTop: 10, padding: '10px 14px', background: '#f0fdf4', borderRadius: 8, border: '1px solid #bbf7d0', fontSize: '0.78rem', lineHeight: 2.2, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontWeight: 700, color: '#374151' }}>🧹 전산 정리 현황: </span>
                  {CLEANUP_ORDER.map(s => (cc[s] ? (
                    <Tag key={s} color={CLEANUP_CFG[s].tag} style={{ margin: 0 }}>{CLEANUP_CFG[s].label} {cc[s]}건</Tag>
                  ) : null))}
                  <span style={{ color: '#15803d', fontWeight: 600 }}>
                    정리율 {issueCount > 0 ? Math.round(done / issueCount * 100) : 0}% ({done}/{issueCount})
                  </span>
                  <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <Button size="small" icon={<ExportOutlined />} onClick={exportCorrectionsCsv}>수정안 CSV</Button>
                    <span style={{ fontSize: '0.72rem', color: '#6b7280' }}>담당자</span>
                    <Input size="small" style={{ width: 90 }} value={cleanupAssignee} placeholder="이름"
                      onChange={e => { setCleanupAssignee(e.target.value); localStorage.setItem('recon_cleanup_assignee', e.target.value) }} />
                    <Switch size="small" checked={hideResolved} onChange={setHideResolved}
                      checkedChildren="정리완료 숨김" unCheckedChildren="전체 표시" />
                  </span>
                </div>
              )
            })()}
          </div>

          {/* 탭별 상세 */}
          <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px 20px' }}>
            <Tabs
              activeKey={activeTab}
              onChange={setActiveTab}
              items={(['in', 'out', 'adjustment'] as const).map(type => {
                const s = (adjustedResult ?? result).summary[type]
                const hasIssue = s.mismatch + s.bh_only + s.ob_only > 0
                return {
                  key: type,
                  label: (
                    <span>
                      {TX_LABEL[type]}
                      {hasIssue && (
                        <Tag color="error" style={{ marginLeft: 6, fontSize: '0.7rem', lineHeight: '16px', padding: '0 4px' }}>
                          {s.mismatch + s.bh_only + s.ob_only}
                        </Tag>
                      )}
                    </span>
                  ),
                  children: (
                    <>
                      <SummaryCards
                        s={s}
                        matchedCount={(adjustedResult ?? result).rows.filter(r => r.tx_type === type && r.matched_confirmed).length || undefined}
                      />

                      {/* 매칭 요약 뷰 토글 — 입고 매칭 실행 후 표시 */}
                      {matchData && (
                        <div style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 10 }}>
                          <Switch size="small" checked={showMatchSummary} onChange={setShowMatchSummary}
                            checkedChildren="요약" unCheckedChildren="상세" />
                          <span style={{ fontSize: '0.78rem', color: '#6b7280' }}>
                            {showMatchSummary ? '상품별 요약 (매칭 결과 반영)' : '기간·채널별 상세 보기'}
                          </span>
                        </div>
                      )}

                      {/* 매칭 요약 뷰 */}
                      {matchData && showMatchSummary ? (
                        <div>
                          {/* 헤더 */}
                          <div style={{ display:'flex', gap:0, fontSize:'0.75rem', color:'#6b7280', fontWeight:600,
                            padding:'6px 14px', background:'#f9fafb', borderRadius:'8px 8px 0 0', border:'1px solid #e5e7eb', borderBottom:'none' }}>
                            <span style={{ flex:3 }}>상품명</span>
                            <span style={{ width:90, textAlign:'right' }}>BH합계</span>
                            <span style={{ width:90, textAlign:'right' }}>OB합계</span>
                            <span style={{ width:80, textAlign:'right' }}>차이</span>
                            <span style={{ width:110, textAlign:'right' }}>매칭 결과</span>
                          </div>
                          <div style={{ border:'1px solid #e5e7eb', borderRadius:'0 0 8px 8px', overflow:'hidden' }}>
                            {matchSummaryRows.filter(r => r.name).map((r, i) => {
                              const rowBg = r.hasOk ? '#f0fdf4'
                                : r.isMatched ? '#fff'
                                : '#fff1f2'
                              return (
                                <div key={i} style={{ display:'flex', alignItems:'center', gap:0,
                                  padding:'8px 14px', borderBottom:'1px solid #f3f4f6',
                                  background: i%2===0 ? rowBg : rowBg, fontSize:'0.82rem' }}>
                                  <span style={{ flex:3, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap',
                                    fontWeight: (!r.hasOk && !r.isMatched) ? 600 : 400 }}
                                    title={r.name}>{r.name}</span>
                                  <span style={{ width:90, textAlign:'right', color:'#1e3a8a' }}>{r.bh.toLocaleString()}</span>
                                  <span style={{ width:90, textAlign:'right', color:'#991b1b' }}>{r.ob.toLocaleString()}</span>
                                  <span style={{ width:80, textAlign:'right', fontWeight:600,
                                    color: r.diff===0?'#10b981': r.diff>0?'#2563eb':'#ef4444' }}>
                                    {r.diff===0 ? '✓' : (r.diff>0?'+':'')+r.diff.toLocaleString()}
                                  </span>
                                  <span style={{ width:110, textAlign:'right' }}>
                                    {r.hasOk
                                      ? <Tag color="success" style={{margin:0}}>✓ 정상</Tag>
                                      : (r as {isExact?:boolean;isDiff?:boolean}).isExact
                                        ? <Tooltip title="수량 일치 — 날짜/채널 차이로 매칭됨"><Tag color="success" style={{margin:0}}>✓ 수량일치</Tag></Tooltip>
                                        : (r as {isExact?:boolean;isDiff?:boolean}).isDiff
                                          ? <Tooltip title="대응 건 있으나 수량 다름 — 차이 원인 확인"><Tag color="warning" style={{margin:0}}>~ 수량차이</Tag></Tooltip>
                                          : <Tooltip title="대응 건 없음 — BoxHero 입력 누락 확인 필요"><Tag color="error" style={{margin:0}}>⚠ 미매칭</Tag></Tooltip>
                                    }
                                  </span>
                                </div>
                              )
                            })}
                            {matchSummaryRows.length === 0 && (
                              <div style={{ textAlign:'center', padding:24, color:'#9ca3af', fontSize:'0.82rem' }}>데이터 없음</div>
                            )}
                          </div>
                          <div style={{ marginTop:8, fontSize:'0.74rem', color:'#6b7280' }}>
                            <span style={{color:'#065f46'}}>✓ 수량일치</span>: BH·OB 수량 완전 일치 &nbsp;|&nbsp;
                            <span style={{color:'#92400e'}}>~ 수량차이</span>: 대응 건 있지만 수량 다름 — 원인 확인 &nbsp;|&nbsp;
                            <span style={{color:'#ef4444'}}>⚠ 미매칭</span>: 대응 건 없음 — BoxHero 입력 누락 확인 필요
                          </div>
                        </div>
                      ) : filteredRows(type).length === 0 ? (
                        <div style={{ textAlign: 'center', padding: 32, color: '#9ca3af', fontSize: '0.85rem' }}>
                          해당 기간 데이터가 없습니다.
                        </div>
                      ) : (
                        <ReconcileTable rows={filteredRows(type)} showChannel={result.by_channel} onRowClick={handleRowClick}
                          matchedNames={exactMatchedNames} diffMatchedNames={diffMatchedNames} unmatchedNames={unmatchedNameSet}
                          stockMap={stockMap.size > 0 ? stockMap : undefined}
                          showBreakdown={mode === 'total' || mode === 'product_all'}
                          cleanup={cleanupActions} />
                      )}
                    </>
                  ),
                }
              })}
            />
          </div>
        </>
      )}

      {/* OB ADJ 식별불가 패널 */}
      {result && (result as any).ob_adj_unknown && (result as any).ob_adj_unknown.length > 0 && (() => {
        const unknowns: {date: string; qty: number; item_cd: string; reason: string}[] = (result as any).ob_adj_unknown
        const totalQty = unknowns.reduce((s, r) => s + Math.abs(r.qty), 0)
        return (
          <div style={{ background: '#fffbeb', borderRadius: 14, border: '1px solid #fcd34d', padding: '14px 20px', marginTop: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
              <span style={{ fontWeight: 700, fontSize: '0.92rem' }}>⚠️ OB 재고조정 식별불가</span>
              <Tag color="orange">{unknowns.length}건</Tag>
              <Tag color="orange">합계 {totalQty.toLocaleString()}개</Tag>
              <span style={{ fontSize: '0.75rem', color: '#92400e' }}>물류센터 직접 조정 — 상품 코드 없어 매핑 불가. 재고 차이 확인 시 참고.</span>
            </div>
            <Table
              dataSource={unknowns}
              rowKey={(_, i) => String(i)}
              size="small"
              pagination={{ pageSize: 10, showSizeChanger: false }}
              style={{ fontSize: '0.82rem' }}
              columns={[
                { title: '날짜', dataIndex: 'date', key: 'date', width: 110 },
                {
                  title: '수량',
                  dataIndex: 'qty', key: 'qty', width: 80, align: 'right' as const,
                  render: (v: number) => (
                    <span style={{ color: v > 0 ? '#10b981' : '#ef4444', fontWeight: 600 }}>
                      {v > 0 ? '+' : ''}{v}
                    </span>
                  ),
                },
                {
                  title: '항목코드 (item_cd)',
                  dataIndex: 'item_cd', key: 'item_cd', width: 160,
                  render: (v: string) => v ? <code style={{ fontSize: '0.75rem' }}>{v}</code> : <span style={{ color: '#d1d5db' }}>없음</span>,
                },
                {
                  title: '조정 사유',
                  dataIndex: 'reason', key: 'reason',
                  render: (v: string) => <span style={{ color: '#78716c' }}>{v || '-'}</span>,
                },
              ]}
            />
          </div>
        )
      })()}

      {/* 입고 매칭 패널 */}
      <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px 20px', marginTop: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 700, fontSize: '0.95rem' }}>🔗 전체 수량 매칭</span>
          <span style={{ fontSize: '0.75rem', color: '#6b7280' }}>품목+수량 기준 (날짜·거래처·유형은 검토용)</span>
          <Tooltip title="전체 기간 탐색: OB는 검색 기간만, BH는 앞뒤로 확장해서 대응 건 탐색. 날짜가 달라도 이름+수량으로 매칭.">
            <Switch size="small" checked={wideMode} onChange={setWideMode}
              checkedChildren="전체탐색" unCheckedChildren="기간내" />
          </Tooltip>
          {wideMode && (
            <div style={{ display:'flex', alignItems:'center', gap:4 }}>
              <span style={{ fontSize:'0.72rem', color:'#6b7280' }}>BH ±</span>
              <Slider min={7} max={60} value={bhLookback} onChange={v => setBhLookback(v as number)}
                style={{ width: 60 }} tooltip={{ formatter: v => `±${v}일` }} />
              <span style={{ fontSize:'0.72rem', fontWeight:600 }}>{bhLookback}일</span>
            </div>
          )}
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            {(['in','out','adjustment'] as const).map(t => (
              <Tag key={t} color={matchTxTypes.includes(t)?'blue':undefined} style={{cursor:'pointer',margin:0}}
                onClick={() => setMatchTxTypes(prev => prev.includes(t)?prev.filter(x=>x!==t):[...prev,t])}>
                {t==='in'?'입고':t==='out'?'출고':'조정'}
              </Tag>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: '0.78rem' }}>허용날짜</span>
            <Slider min={0} max={7} value={matchTolerance} onChange={v => setMatchTolerance(v as number)}
              style={{ width: 70 }} tooltip={{ formatter: v => `±${v}일` }} />
            <span style={{ fontSize: '0.78rem', fontWeight: 600 }}>±{matchTolerance}일</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: '0.78rem' }}>최소점수</span>
            <Slider min={40} max={90} value={matchMinScore} onChange={v => setMatchMinScore(v as number)}
              style={{ width: 70 }} tooltip={{ formatter: v => `${v}점` }} />
            <span style={{ fontSize: '0.78rem', fontWeight: 600 }}>{matchMinScore}점</span>
          </div>
          <Tooltip title="출고를 (상품,날짜) 단위 합산 비교 — 채널별 분산을 흡수합니다">
            <Switch size="small" checked={matchAggregate} onChange={setMatchAggregate}
              checkedChildren="합산" unCheckedChildren="개별" />
          </Tooltip>
          <Tooltip title="OB 내부 처리 채널 제외 (콤마구분)">
            <Input size="small" style={{width:220,fontSize:'0.72rem'}}
              value={matchExclude} onChange={e=>setMatchExclude(e.target.value)}
              placeholder="제외채널 (콤마구분)" allowClear />
          </Tooltip>
          <Button size="small" type="primary" onClick={handleMatch} loading={matchLoading}
            disabled={!config.api_token} style={{ marginLeft: 'auto' }}>
            매칭 실행
          </Button>
          {matchData && <Button size="small" icon={<ExportOutlined />} onClick={exportMatchCsv}>CSV</Button>}
          {matchData && (
            <Button
              size="small"
              type="primary"
              danger={false}
              loading={confirmLoading}
              style={{ background: '#059669', borderColor: '#059669' }}
              onClick={confirmMatch}
            >
              ✅ 매칭 확정 → 비교에 반영
            </Button>
          )}
          {confirmedAt && (
            <>
              <span style={{ fontSize: '0.75rem', color: '#059669' }}>확정: {confirmedAt}</span>
              <Button
                size="small"
                danger
                onClick={async () => {
                  try {
                    const resp = await fetch(
                      `${import.meta.env.VITE_API_BASE ?? ''}/api/reconcile/match/confirm?from_date=${fromDate}&to_date=${toDate}`,
                      { method: 'DELETE' }
                    )
                    if (!resp.ok) throw new Error(await resp.text())
                    message.success('매칭 확정 취소 완료')
                    setConfirmedAt(null)
                    if (result) handleCompare()
                  } catch (e: unknown) {
                    message.error('취소 실패: ' + String(e))
                  }
                }}
              >
                확정 취소
              </Button>
            </>
          )}
        </div>

        {matchLoading && (
          <div style={{ textAlign: 'center', padding: 32 }}>
            <Spin /><div style={{ marginTop: 8, fontSize: '0.82rem', color: '#6b7280' }}>품목별 유사도 매칭 중... (BH 배치 분해 + OB 입고와 이름/수량 비교)</div>
          </div>
        )}

        {matchData && !matchLoading && (
          <>
            {/* 요약 카드 */}
            <Row gutter={10} style={{ marginBottom: 12 }}>
              {([
                { key: 'matched', label: `매칭됨 (확정${matchData.exact_count}+추정${matchData.probable_count})`, val: matchData.matched_count, color: '#065f46', bg: '#d1fae5' },
                { key: 'bh_only', label: 'BH단독 (OB 미확인)', val: matchData.bh_only_count, color: '#1e3a8a', bg: '#dbeafe' },
                { key: 'ob_only', label: 'OB단독 (BH 미입력?)', val: matchData.ob_only_count, color: '#991b1b', bg: '#fee2e2' },
              ] as const).map(m => (
                <Col span={6} key={m.key}>
                  <div onClick={() => setMatchTab(m.key as 'matched'|'bh_only'|'ob_only')}
                    style={{ background: m.bg, borderRadius: 8, padding: '8px 12px', textAlign: 'center', cursor: 'pointer',
                      border: matchTab === m.key ? `2px solid ${m.color}` : '2px solid transparent' }}>
                    <div style={{ fontSize: '1.3rem', fontWeight: 800, color: m.color }}>{m.val}</div>
                    <div style={{ fontSize: '0.7rem', color: m.color }}>{m.label}</div>
                  </div>
                </Col>
              ))}
              <Col span={6}>
                <div style={{ background: '#f9fafb', borderRadius: 8, padding: '8px 12px', textAlign: 'center' }}>
                  <div style={{ fontSize: '0.65rem', color: '#6b7280', marginBottom: 2 }}>OB 매칭률 (기준)</div>
                  <div style={{ fontSize: '1.5rem', fontWeight: 900,
                    color: matchData.match_rate_ob >= 95 ? '#065f46' : matchData.match_rate_ob >= 80 ? '#92400e' : '#991b1b' }}>
                    {matchData.match_rate_ob}%
                  </div>
                  <div style={{ fontSize: '0.7rem', color: '#9ca3af' }}>BH {matchData.match_rate_bh}%</div>
                  <div style={{ fontSize: '0.7rem', color: '#6b7280' }}>BH {matchData.total_bh}행 · OB {matchData.total_ob}행</div>
                  {(matchData.stocktake_count || matchData.bulk_init_count || matchData.excluded_count) ? (
                    <div style={{ fontSize: '0.66rem', color: '#9ca3af', marginTop: 3, lineHeight: 1.5 }}>
                      {matchData.stocktake_count ? <div>📋 재고실사·수동조정 {matchData.stocktake_count - (matchData.bulk_init_count||0)}건</div> : null}
                      {matchData.bulk_init_count ? <div>📦 기초재고이관 {matchData.bulk_init_count}건</div> : null}
                      {matchData.excluded_count ? <div>🚫 부자재·샘플 {matchData.excluded_count}건 제외</div> : null}
                      {matchData.set_work_count ? <div style={{color:'#0e7490'}}>🔧 세트조립 매칭 {matchData.set_work_count}건</div> : null}
                      {(matchData as Record<string,unknown>).set_dismantle_count ? <div style={{color:'#c2410c'}}>🔩 세트해체 매칭 {(matchData as Record<string,unknown>).set_dismantle_count as number}건</div> : null}
                      <div style={{ color: '#d1d5db', fontSize: '0.62rem' }}>(매칭률 분모에서 제외)</div>
                    </div>
                  ) : null}
                </div>
              </Col>
            </Row>

            {/* 유형별 매칭률 */}
            {matchData.by_type && (
              <div style={{ display:'flex', gap:8, marginBottom:12, flexWrap:'wrap' }}>
                {Object.entries(matchData.by_type).map(([t,v]) => {
                  const label = {in:'입고',out:'출고',adjustment:'조정'}[t]||t
                  const obr = v.ob_match_rate
                  const color = obr>=95?'#065f46':obr>=80?'#92400e':'#991b1b'
                  const bg = obr>=95?'#d1fae5':obr>=80?'#fef3c7':'#fee2e2'
                  return (
                    <div key={t} style={{background:bg,borderRadius:8,padding:'6px 12px',fontSize:'0.78rem',color}}>
                      <strong>[{label}]</strong> OB 매칭률 <strong>{obr}%</strong>
                      ({v.matched}/{v.ob_total}) · BH {v.bh_match_rate}% ({v.matched}/{v.bh_total})
                      {v.ob_only>0 && <span style={{marginLeft:6,color:'#991b1b'}}>OB단독 {v.ob_only}건</span>}
                    </div>
                  )
                })}
              </div>
            )}

            {/* 채널별 수량 대시보드 */}
            {effectiveChannelStats.length > 0 && (
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontWeight: 700, fontSize: '0.85rem', marginBottom: 8 }}>
                  📊 채널별 수량 현황
                  <span style={{ fontSize: '0.72rem', color: '#6b7280', marginLeft: 8, fontWeight: 400 }}>
                    BH 거래처 vs OB 채널 — 매칭 조정 시 실시간 반영
                  </span>
                  {Object.keys(matchOverrides).length > 0 && (
                    <Tag color="warning" style={{ marginLeft: 8 }}>수동조정 {Object.keys(matchOverrides).length}건 반영</Tag>
                  )}
                </div>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
                    <thead>
                      <tr style={{ background: '#f9fafb', borderBottom: '2px solid #e5e7eb' }}>
                        <th style={{ padding: '6px 10px', textAlign: 'left', fontWeight: 600 }}>채널 (BH거래처/OB채널)</th>
                        <th style={{ padding: '6px 10px', textAlign: 'right', color: '#1e3a8a', fontWeight: 600 }}>BH수량</th>
                        <th style={{ padding: '6px 10px', textAlign: 'right', color: '#991b1b', fontWeight: 600 }}>OB수량</th>
                        <th style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 600 }}>차이</th>
                        <th style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 600 }}>BH매칭률</th>
                        <th style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 600 }}>OB매칭률</th>
                        <th style={{ padding: '6px 10px', textAlign: 'center', fontWeight: 600 }}>상태</th>
                      </tr>
                    </thead>
                    <tbody>
                      {effectiveChannelStats.map((row, i) => {
                        const diff = row.diff
                        const pct = Math.min(100, Math.max(row.bh_match_rate, row.ob_match_rate))
                        const rowBg = i % 2 === 0 ? '#fff' : '#fafafa'
                        return (
                          <tr key={row.channel} style={{ borderBottom: '1px solid #f3f4f6', background: rowBg }}>
                            <td style={{ padding: '5px 10px', fontWeight: 500 }}>{row.channel || '채널미상'}</td>
                            <td style={{ padding: '5px 10px', textAlign: 'right', color: '#1e3a8a' }}>
                              {row.bh_qty.toLocaleString()}
                            </td>
                            <td style={{ padding: '5px 10px', textAlign: 'right', color: '#991b1b' }}>
                              {row.ob_qty.toLocaleString()}
                            </td>
                            <td style={{ padding: '5px 10px', textAlign: 'right', fontWeight: 600,
                              color: diff === 0 ? '#10b981' : diff > 0 ? '#1e3a8a' : '#991b1b' }}>
                              {diff === 0 ? '✓' : (diff > 0 ? '+' : '') + diff.toLocaleString()}
                            </td>
                            <td style={{ padding: '5px 10px', textAlign: 'right' }}>
                              <span style={{ color: row.bh_match_rate >= 90 ? '#065f46' : row.bh_match_rate >= 70 ? '#92400e' : '#991b1b' }}>
                                {row.bh_match_rate}%
                              </span>
                            </td>
                            <td style={{ padding: '5px 10px', textAlign: 'right' }}>
                              <span style={{ color: row.ob_match_rate >= 90 ? '#065f46' : row.ob_match_rate >= 70 ? '#92400e' : '#991b1b' }}>
                                {row.ob_match_rate}%
                              </span>
                            </td>
                            <td style={{ padding: '5px 10px', textAlign: 'center' }}>
                              <div style={{ background: '#e5e7eb', borderRadius: 4, overflow: 'hidden', height: 6, width: 60, display: 'inline-block' }}>
                                <div style={{ width: `${pct}%`, height: '100%',
                                  background: pct >= 90 ? '#10b981' : pct >= 70 ? '#f59e0b' : '#ef4444' }} />
                              </div>
                            </td>
                          </tr>
                        )
                      })}
                      {/* 합계 행 */}
                      <tr style={{ borderTop: '2px solid #e5e7eb', fontWeight: 700, background: '#f3f4f6' }}>
                        <td style={{ padding: '6px 10px' }}>합계</td>
                        <td style={{ padding: '6px 10px', textAlign: 'right', color: '#1e3a8a' }}>
                          {effectiveChannelStats.reduce((s,r) => s+r.bh_qty, 0).toLocaleString()}
                        </td>
                        <td style={{ padding: '6px 10px', textAlign: 'right', color: '#991b1b' }}>
                          {effectiveChannelStats.reduce((s,r) => s+r.ob_qty, 0).toLocaleString()}
                        </td>
                        <td style={{ padding: '6px 10px', textAlign: 'right' }}>
                          {(() => { const d=effectiveChannelStats.reduce((s,r)=>s+r.diff,0); return d===0?'✓':(d>0?'+':'')+d.toLocaleString() })()}
                        </td>
                        <td style={{ padding: '6px 10px', textAlign: 'right' }}>
                          {(effectiveChannelStats.reduce((s,r)=>s+r.bh_matched_qty,0)/Math.max(effectiveChannelStats.reduce((s,r)=>s+r.bh_qty,0),1)*100).toFixed(1)}%
                        </td>
                        <td style={{ padding: '6px 10px', textAlign: 'right' }}>
                          {(effectiveChannelStats.reduce((s,r)=>s+r.ob_matched_qty,0)/Math.max(effectiveChannelStats.reduce((s,r)=>s+r.ob_qty,0),1)*100).toFixed(1)}%
                        </td>
                        <td />
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {matchTab === 'matched' && (
              <Table
                size="small"
                dataSource={matchData.matched as Record<string,unknown>[]}
                rowKey={(_,i) => `m${i}`}
                pagination={{ pageSize: 20, showSizeChanger: true, showTotal: t => `총 ${t}건` }}
                expandable={{
                  expandedRowKeys: [...expandedRows].map(i => `m${i}`),
                  onExpand: (_, r) => {
                    const i = (matchData.matched as Record<string,unknown>[]).indexOf(r as Record<string,unknown>)
                    setExpandedRows(prev => { const s=new Set(prev); s.has(i)?s.delete(i):s.add(i); return s })
                  },
                  expandedRowRender: (r: unknown) => {
                    const m = r as Record<string,unknown>
                    const alts = m.alternatives as Record<string,unknown>[] | undefined
                    const detail = m.score_detail as Record<string,unknown> | undefined
                    const bh_idx = m.bh_idx as number
                    return (
                      <div style={{padding:'8px 16px', background:'#f9fafb'}}>
                        {detail && (
                          <div style={{marginBottom:8,fontSize:'0.75rem',color:'#6b7280'}}>
                            📊 점수 근거: <strong>{detail.reason as string}</strong>
                          </div>
                        )}
                        {alts && alts.length > 0 && (
                          <>
                            <div style={{fontSize:'0.75rem',color:'#6b7280',marginBottom:4}}>🔄 대안 선택 (클릭하면 교체):</div>
                            {alts.map((alt, ai) => {
                              const qd = alt.qty_diff as number
                              return (
                                <div key={ai} onClick={() => {
                                  setMatchOverrides(prev=>({...prev,[bh_idx]:alt.ob_idx as number}))
                                  setExpandedRows(prev=>{const s=new Set(prev);const i=(matchData.matched as Record<string,unknown>[]).indexOf(m);s.delete(i);return s})
                                }} style={{cursor:'pointer',display:'inline-flex',alignItems:'center',gap:6,
                                  margin:'2px 4px',padding:'4px 10px',borderRadius:6,fontSize:'0.78rem',
                                  background:'#fff',border:'1px solid #d1d5db',
                                  transition:'all 0.1s'}}>
                                  <Tag color="orange" style={{margin:0}}>{alt.score as number}점</Tag>
                                  <span style={{color:'#6b7280'}}>{alt.ob_date as string}</span>
                                  <span style={{fontWeight:600}}>{(alt.ob_qty as number).toLocaleString()}개</span>
                                  <span style={{maxWidth:160,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{alt.ob_name as string}</span>
                                  <span style={{fontWeight:700,color:qd===0?'#10b981':'#ef4444'}}>{qd===0?'✓수량일치':(qd>0?'+':'')+qd}</span>
                                </div>
                              )
                            })}
                          </>
                        )}
                      </div>
                    )
                  },
                  rowExpandable: (r: unknown) => ((r as Record<string,unknown>).alternatives as unknown[])?.length > 0,
                }}
                columns={[
                  {
                    title: '상태', width: 90,
                    render: (_: unknown, r: unknown) => {
                      const m = r as Record<string,unknown>
                      const sc = m.score as number
                      const qd = m.qty_diff as number
                      const isOverridden = (m.bh_idx as number) in matchOverrides
                      const icon = qd===0 ? '✓' : '△'
                      const color = qd===0 ? '#10b981' : sc>=85 ? '#f59e0b' : '#ef4444'
                      return (
                        <div style={{display:'flex',alignItems:'center',gap:4}}>
                          <span style={{fontSize:'1rem',color,fontWeight:700}}>{icon}</span>
                          <Tooltip title={(m.score_detail as Record<string,unknown>|undefined)?.reason as string || ''}>
                            <Tag style={{margin:0,cursor:'help',fontSize:'0.7rem'}}
                              color={sc>=100?'green':sc>=85?'blue':'orange'}>{sc}</Tag>
                          </Tooltip>
                          {isOverridden && <Tag color="warning" style={{margin:0,fontSize:'0.68rem'}}>수동</Tag>}
                        </div>
                      )
                    }
                  },
                  {
                    title: '매칭근거', width: 96,
                    render: (_: unknown, r: unknown) => {
                      const m = r as Record<string,unknown>
                      const reason = m.match_reason as string || ''
                      const cfg: Record<string, {label: string; color: string; tip: string}> = {
                        'SKU완전매칭': { label: 'SKU일치', color: 'green', tip: '상품 매핑 코드(SKU)가 동일 — 날짜·유형 무관 확실 매칭' },
                        '이름수량완전매칭': { label: '이름+수량', color: 'cyan', tip: '상품명 90%+ 유사 & 수량 일치 — 날짜·유형 무관 매칭' },
                        '유사도매칭': { label: '유사도', color: 'orange', tip: '이름·수량·날짜 종합 점수로 매칭 — 검토 권장' },
                      }
                      const c = cfg[reason]
                      if (!c) return <span style={{color:'#d1d5db'}}>—</span>
                      return <Tooltip title={c.tip}><Tag color={c.color} style={{margin:0,fontSize:'0.7rem',cursor:'help'}}>{c.label}</Tag></Tooltip>
                    }
                  },
                  {
                    title: '유형', width: 90,
                    render: (_: unknown, r: unknown) => {
                      const m = r as Record<string,unknown>
                      const bhType = m.bh_tx_type as string || m.tx_type as string
                      const obType = m.ob_tx_type as string || m.tx_type as string
                      const label = (t: string) => ({in:'입고',out:'출고',move:'이동',adjustment:'조정'})[t] || t
                      const color = (t: string) => ({in:'blue',out:'green',move:'orange',adjustment:'purple'})[t] || 'default'
                      // 크로스 타입이면 두 유형 모두 표시
                      if (bhType !== obType) {
                        return <div style={{lineHeight:1.2}}>
                          <div><Tag style={{margin:0,fontSize:'0.68rem'}} color={color(bhType)}>BH {label(bhType)}</Tag></div>
                          <div style={{marginTop:2}}><Tag style={{margin:0,fontSize:'0.68rem'}} color={color(obType)}>OB {label(obType)}</Tag></div>
                        </div>
                      }
                      return <Tag style={{margin:0}} color={color(bhType)}>{label(bhType)}</Tag>
                    },
                    filters: [{text:'입고',value:'in'},{text:'출고',value:'out'},{text:'이동(move)',value:'move'}],
                    onFilter: (v: unknown, r: unknown) => (r as Record<string,unknown>).bh_tx_type === v || (r as Record<string,unknown>).tx_type === v,
                  },
                  {
                    title: '박스히어로', width: 280,
                    render: (_: unknown, r: unknown) => {
                      const m = r as Record<string,unknown>
                      return (
                        <div>
                          <div style={{fontWeight:600,color:'#1e3a8a',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}} title={m.bh_name as string}>
                            {!!m.is_smartstore && <Tag color="green" style={{fontSize:'0.65rem',padding:'0 3px',marginRight:3}}>스마트스토어합계</Tag>}
                            {m.match_type === 'set_work' && <Tag color="cyan" style={{fontSize:'0.65rem',padding:'0 3px',marginRight:3}}>🔧세트조립</Tag>}
                            {m.match_type === 'set_dismantle' && <Tag color="orange" style={{fontSize:'0.65rem',padding:'0 3px',marginRight:3}}>🔩세트해체</Tag>}
                            {m.bh_name as string}
                          </div>
                          <div style={{fontSize:'0.72rem',color:'#6b7280'}}>{m.bh_date as string} · {(m.bh_qty as number).toLocaleString()}개{m.bh_partner?' · '+(m.bh_partner as string):''}</div>
                        </div>
                      )
                    }
                  },
                  {
                    title: '차이', width: 70, align: 'center' as const,
                    render: (_: unknown, r: unknown) => {
                      const m = r as Record<string,unknown>
                      const qd = m.qty_diff as number
                      const gap = m.day_gap as number
                      return (
                        <div style={{textAlign:'center'}}>
                          <div style={{fontWeight:700,fontSize:'0.9rem',color:qd===0?'#10b981':'#ef4444'}}>{qd===0?'✓':(qd>0?'+':'')+qd}</div>
                          {gap>0 && <div style={{fontSize:'0.68rem',color:'#f59e0b'}}>{gap}일차이</div>}
                        </div>
                      )
                    }
                  },
                  {
                    title: '아워박스', width: 280,
                    render: (_: unknown, r: unknown) => {
                      const m = r as Record<string,unknown>
                      return (
                        <div>
                          <div style={{fontWeight:600,color:'#991b1b',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}} title={m.ob_name as string}>
                            {(m.ob_group_count as number) > 1 && (
                              <Tag color="purple" style={{fontSize:'0.65rem',padding:'0 3px',marginRight:3}}>
                                {m.ob_group_count as number}건합산
                              </Tag>
                            )}
                            {m.ob_name as string}
                          </div>
                          <div style={{fontSize:'0.72rem',color:'#6b7280'}}>{m.ob_date as string} · {(m.ob_qty as number).toLocaleString()}개{m.ob_channel?' · '+(m.ob_channel as string):''}</div>
                        </div>
                      )
                    }
                  },
                  {
                    title: '', width: 60, align: 'right' as const,
                    render: (_: unknown, r: unknown) => {
                      const m = r as Record<string,unknown>
                      const bh_idx = m.bh_idx as number
                      return (
                        <Button size="small" type="text" danger style={{fontSize:'0.72rem'}}
                          onClick={() => setMatchOverrides(prev=>({...prev,[bh_idx]:null}))}>해제</Button>
                      )
                    }
                  },
                ]}
                rowClassName={(r: unknown) => {
                  const m = r as Record<string,unknown>
                  const classes = []
                  if ((m.qty_diff as number) !== 0) classes.push('row-warn')
                  // BH/OB 거래 유형이 다른 크로스 매칭 → amber 하이라이트
                  const bhT = m.bh_tx_type as string || m.tx_type as string
                  const obT = m.ob_tx_type as string || m.tx_type as string
                  if (bhT !== obT || m.cross_type) classes.push('row-cross-type')
                  if (m.match_type === 'set_work') classes.push('row-set-work')
                  if (m.match_type === 'set_dismantle') classes.push('row-set-dismantle')
                  return classes.join(' ')
                }}
              />
            )}

            {matchTab === 'bh_only' && (
              <Table
                size="small"
                dataSource={matchData.bh_only as Record<string,unknown>[]}
                rowKey={(_,i) => `b${i}`}
                pagination={{ pageSize: 20, showSizeChanger: true, showTotal: t=>`총 ${t}건` }}
                expandable={{
                  expandedRowRender: (r: unknown) => {
                    const b = r as Record<string,unknown>
                    const cands = b.candidates as Record<string,unknown>[] | undefined
                    const bh_idx = b.bh_idx as number
                    if (!cands || cands.length===0) return <span style={{color:'#9ca3af',fontSize:'0.78rem',paddingLeft:8}}>OB 후보 없음 — OB 미기록이거나 날짜 범위 밖입니다.</span>
                    return (
                      <div style={{padding:'8px 16px',background:'#f9fafb'}}>
                        <div style={{fontSize:'0.75rem',color:'#6b7280',marginBottom:6}}>🔗 OB 후보 — 클릭하면 연결:</div>
                        <div style={{display:'flex',flexWrap:'wrap',gap:6}}>
                          {cands.map((c,ci) => {
                            const qd = c.qty_diff as number
                            const isSelected = matchOverrides[bh_idx]===c.ob_idx
                            return (
                              <div key={ci} onClick={()=>setMatchOverrides(prev=>({...prev,[bh_idx]:c.ob_idx as number}))}
                                style={{cursor:'pointer',padding:'6px 12px',borderRadius:8,border:`2px solid ${isSelected?'#10b981':'#e5e7eb'}`,
                                  background:isSelected?'#f0fdf4':'#fff',minWidth:200}}>
                                <div style={{display:'flex',alignItems:'center',gap:6,marginBottom:2}}>
                                  <Tag color="orange" style={{margin:0,fontSize:'0.7rem'}}>{c.score as number}점</Tag>
                                  <span style={{fontWeight:700,color:qd===0?'#10b981':'#ef4444'}}>{qd===0?'✓ 수량일치':(qd>0?'+':'')+qd}</span>
                                  {isSelected && <Tag color="success" style={{margin:0}}>선택됨</Tag>}
                                </div>
                                <div style={{fontSize:'0.78rem',fontWeight:600,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{c.ob_name as string}</div>
                                <div style={{fontSize:'0.72rem',color:'#6b7280'}}>{c.ob_date as string} · {(c.ob_qty as number).toLocaleString()}개</div>
                                <div style={{fontSize:'0.68rem',color:'#9ca3af'}}>{c.reason as string}</div>
                              </div>
                            )
                          })}
                        </div>
                        {matchOverrides[bh_idx] != null && (
                          <Button size="small" type="text" danger style={{marginTop:6,fontSize:'0.72rem'}}
                            onClick={()=>setMatchOverrides(prev=>{const n={...prev};delete n[bh_idx];return n})}>연결 해제</Button>
                        )}
                      </div>
                    )
                  },
                  rowExpandable: () => true,
                }}
                columns={[
                  {
                    title: '상태', width: 80,
                    render: (_: unknown, r: unknown) => {
                      const b = r as Record<string,unknown>
                      const bh_idx = b.bh_idx as number
                      const isLinked = bh_idx in matchOverrides && matchOverrides[bh_idx] !== null
                      return isLinked
                        ? <Tag color="success" style={{margin:0}}>✓ 연결됨</Tag>
                        : <Tag color="default" style={{margin:0}}>미매칭</Tag>
                    }
                  },
                  { title: '유형', dataIndex: 'tx_type', width: 56,
                    render: (v: string) => <Tag style={{margin:0}} color={({in:'blue',out:'green',adjustment:'orange'})[v]}>{({in:'입고',out:'출고',adjustment:'조정'})[v]}</Tag> },
                  { title: '날짜', dataIndex: 'date', width: 95 },
                  { title: '수량', dataIndex: 'qty', width: 75, align:'right' as const,
                    render: (v: number) => <span style={{fontWeight:600}}>{v.toLocaleString()}</span> },
                  { title: 'BH 상품명', dataIndex: 'name', ellipsis: true },
                  { title: '입고번호', dataIndex: 'put_sno', width: 85,
                    render: (v: string) => v ? <Tag color="blue" style={{margin:0}}>{v}</Tag> : <span style={{color:'#d1d5db'}}>—</span> },
                  { title: 'memo', dataIndex: 'memo', ellipsis: true, width: 160,
                    render: (v: string) => <span style={{fontSize:'0.72rem',color:'#9ca3af'}}>{(v||'').slice(0,30)}</span> },
                ]}
                rowClassName={(r: unknown) => {
                  const b = r as Record<string,unknown>
                  const bh_idx = b.bh_idx as number
                  return (bh_idx in matchOverrides && matchOverrides[bh_idx]!==null) ? '' : 'row-info'
                }}
              />
            )}

            {matchTab === 'ob_only' && (
              <Table
                size="small"
                dataSource={matchData.ob_only as Record<string,unknown>[]}
                rowKey={(_,i) => `o${i}`}
                pagination={{ pageSize: 20, showSizeChanger: true, showTotal: t=>`총 ${t}건 — BoxHero에 미입력됐을 가능성이 높습니다` }}
                columns={[
                  { title: '유형', dataIndex: 'tx_type', width: 56,
                    render: (v: string) => <Tag style={{margin:0}} color={({in:'blue',out:'green',adjustment:'orange'})[v]}>{({in:'입고',out:'출고',adjustment:'조정'})[v]}</Tag>,
                    filters: [{text:'입고',value:'in'},{text:'출고',value:'out'}],
                    onFilter: (v: unknown, r: unknown) => (r as Record<string,unknown>).tx_type === v },
                  { title: '날짜', dataIndex: 'date', width: 95, sorter: (a: unknown,b: unknown) => ((a as Record<string,unknown>).date as string).localeCompare((b as Record<string,unknown>).date as string) },
                  { title: '수량', dataIndex: 'qty', width: 85, align:'right' as const,
                    render: (v: number) => <span style={{fontWeight:700,color:'#991b1b'}}>{v.toLocaleString()}</span>,
                    sorter: (a: unknown,b: unknown) => (a as Record<string,unknown>).qty as number - ((b as Record<string,unknown>).qty as number), defaultSortOrder:'descend' as const },
                  { title: 'OB 상품명', dataIndex: 'name', ellipsis: true },
                  { title: 'OB채널', dataIndex: 'channel', width: 140, ellipsis: true,
                    render: (v: string) => v ? <Tag style={{margin:0}}>{v}</Tag> : <span style={{color:'#d1d5db'}}>—</span> },
                  { title: '입고번호', dataIndex: 'put_sno', width: 85,
                    render: (v: string) => v ? <Tag color="red" style={{margin:0}}>{v}</Tag> : <span style={{color:'#d1d5db'}}>—</span> },
                ]}
                rowClassName={() => 'row-error'}
              />
            )}
          </>
        )}

        {!matchData && !matchLoading && (
          <div style={{ textAlign: 'center', padding: 24, color: '#9ca3af', fontSize: '0.85rem' }}>
            기간 설정 후 "매칭 실행"을 클릭하면 BH·OB 입고를 품목별 유사도로 자동 매칭합니다.
            <br />이름유사도(40%) + 수량일치도(40%) + 날짜근접도(20%) 점수로 확정/추정/미매칭을 분류합니다.
          </div>
        )}
      </div>

      {/* AI 분석 패널 */}
      <div ref={analysisRef} style={{ marginTop: 16 }}>
        <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px 20px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
            <span style={{ fontWeight: 700, fontSize: '0.95rem' }}>🤖 AI 불일치 원인 분석</span>
            {!config.claude_api_key && !config.gemini_api_key && !config.groq_api_key && (
              <span style={{ fontSize: '0.78rem', color: '#ef4444' }}>
                ⚠️ AI API Key 필요 (설정 → Gemini 무료 키 입력)
              </span>
            )}
            {(config.gemini_api_key || config.groq_api_key || config.claude_api_key) && (
              <span style={{ fontSize: '0.75rem', color: '#10b981' }}>
                {config.gemini_api_key ? '🟢 Gemini' : config.groq_api_key ? '🟡 Groq' : '🔵 Claude'} 연결됨
              </span>
            )}
            <Button
              type="primary"
              icon={<RobotOutlined />}
              onClick={handleAnalyze}
              loading={analyzing}
              disabled={!result || (!config.claude_api_key && !config.gemini_api_key && !config.groq_api_key) || result.rows.length === 0}
              style={{ marginLeft: 'auto' }}
            >
              {analyzing ? 'AI 분석 중...' : 'AI 분석 시작'}
            </Button>
            {analysisText && !analyzing && (
              <Button
                icon={<CopyOutlined />}
                size="small"
                onClick={() => navigator.clipboard.writeText(analysisText)}
              >
                복사
              </Button>
            )}
          </div>

          {analysisError && (
            <Alert type="error" message={`분석 오류: ${analysisError}`} style={{ marginBottom: 12 }} />
          )}

          {analyzing && !analysisText && (
            <div style={{ textAlign: 'center', padding: 24, color: '#6b7280', fontSize: '0.85rem' }}>
              <Spin size="small" style={{ marginRight: 8 }} />
              Claude AI가 불일치 데이터를 분석 중입니다...
            </div>
          )}

          {analysisText ? (
            <div style={{ background: '#f9fafb', borderRadius: 10, padding: '16px 20px', minHeight: 80 }}>
              <MarkdownText text={analysisText} />
              {analyzing && <span style={{ display: 'inline-block', width: 8, height: 16, background: '#10b981', marginLeft: 2, animation: 'blink 1s infinite' }} />}
            </div>
          ) : !analyzing && !analysisError ? (
            <div style={{ background: '#f9fafb', borderRadius: 10, padding: 24, textAlign: 'center', color: '#9ca3af', fontSize: '0.85rem' }}>
              조회 완료 후 "AI 분석 시작"을 클릭하면 불일치 원인, 시점 차이 패턴, 우선 조사 항목을 AI가 분석합니다.
            </div>
          ) : null}
        </div>
      </div>

      {/* 정리 메모 편집 Modal */}
      <Modal
        open={!!memoModal}
        onCancel={() => setMemoModal(null)}
        width={520}
        title="🧹 전산 정리 메모"
        okText="저장"
        cancelText="취소"
        onOk={async () => {
          if (!memoModal) return
          await applyCleanup(memoModal.row, memoModal.status, memoModal.memo)
          setMemoModal(null)
        }}
      >
        {memoModal && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ fontSize: '0.82rem', color: '#374151' }}>
              <b>{memoModal.row.name || memoModal.row.sku}</b>
              <span style={{ color: '#9ca3af', marginLeft: 8 }}>
                {memoModal.row.period} · {TX_LABEL[memoModal.row.tx_type]}
                {memoModal.row.channel ? ` · ${memoModal.row.channel}` : ''}
              </span>
              <div style={{ marginTop: 4, color: '#6b7280' }}>
                BH {memoModal.row.bh_qty ?? '—'} / OB {memoModal.row.ob_qty ?? '—'}
                {memoModal.row.root_cause && ROOT_CAUSE_CFG[memoModal.row.root_cause]
                  ? <Tag color={ROOT_CAUSE_CFG[memoModal.row.root_cause].tag} style={{ marginLeft: 8 }}>
                      {ROOT_CAUSE_CFG[memoModal.row.root_cause].label}
                    </Tag>
                  : null}
              </div>
            </div>
            <div>
              <div style={{ fontSize: '0.74rem', color: '#6b7280', marginBottom: 4 }}>정리 상태</div>
              <Radio.Group size="small" value={memoModal.status}
                onChange={e => setMemoModal({ ...memoModal, status: e.target.value })}>
                {CLEANUP_ORDER.map(s => <Radio.Button key={s} value={s}>{CLEANUP_CFG[s].label}</Radio.Button>)}
              </Radio.Group>
            </div>
            <div>
              <div style={{ fontSize: '0.74rem', color: '#6b7280', marginBottom: 4 }}>메모 (원인·조치 내용)</div>
              <Input.TextArea rows={3} value={memoModal.memo}
                onChange={e => setMemoModal({ ...memoModal, memo: e.target.value })}
                placeholder="예: OB 4/19 출고 누락 → BH에 출고 수기 입력함 / 세트 분해건, 정상" />
            </div>
            <div style={{ fontSize: '0.74rem', color: '#9ca3af' }}>담당자: {cleanupAssignee || '(미지정 — 상단에서 입력)'}</div>
          </div>
        )}
      </Modal>

      {/* 누락건 추출 Modal */}
      <Modal
        open={missingOpen}
        onCancel={() => setMissingOpen(false)}
        width={900}
        footer={null}
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <ExportOutlined /> 누락건 추출
            <Select
              size="small"
              value={missingTxType}
              onChange={(v) => { setMissingTxType(v as typeof missingTxType); }}
              style={{ width: 100 }}
              options={[
                { value: 'out', label: '출고' },
                { value: 'in', label: '입고' },
                { value: 'adjustment', label: '조정' },
                { value: 'all', label: '전체' },
              ]}
            />
            <Button size="small" onClick={handleMissing} loading={missingLoading}>재조회</Button>
            {missingData && (
              <Button size="small" icon={<ExportOutlined />} onClick={exportMissingCsv} style={{ marginLeft: 'auto' }}>
                CSV 다운로드
              </Button>
            )}
          </div>
        }
      >
        {missingLoading ? (
          <div style={{ textAlign: 'center', padding: 60 }}><Spin /><div style={{ marginTop: 10, color: '#6b7280', fontSize: '0.82rem' }}>누락 건 집계 중...</div></div>
        ) : !missingData ? (
          <Empty description="데이터 없음. '비교 조회' 후 다시 시도하세요." />
        ) : (
          <>
            <Row gutter={12} style={{ marginBottom: 14 }}>
              {[
                { label: '상품 수 (차이 있음)', val: missingData.count, color: '#374151', bg: '#f3f4f6' },
                { label: 'BH 추가 입력 필요 (OB>BH)', val: missingData.total_need_boxhero, color: '#991b1b', bg: '#fee2e2' },
                { label: 'OB 누락 또는 BH 과입력 (BH>OB)', val: missingData.total_need_ourbox, color: '#1e3a8a', bg: '#dbeafe' },
              ].map(m => (
                <Col span={8} key={m.label}>
                  <div style={{ background: m.bg, borderRadius: 8, padding: '8px 12px', textAlign: 'center' }}>
                    <div style={{ fontSize: '1.4rem', fontWeight: 800, color: m.color }}>{m.val.toLocaleString()}</div>
                    <div style={{ fontSize: '0.72rem', color: m.color }}>{m.label}</div>
                  </div>
                </Col>
              ))}
            </Row>
            <Alert
              type="info" showIcon style={{ marginBottom: 10, fontSize: '0.78rem' }}
              message="BH 추가 입력 필요(빨간): OurBox에 출고됐는데 BoxHero에 없는 수량. 해당 상품을 BoxHero에 입력하면 차이가 줄어듭니다."
            />
            <Table
              size="small"
              dataSource={missingData.rows}
              rowKey={(_r: unknown, i?: number) => `row-${i}`}
              pagination={{ pageSize: 20, showSizeChanger: true }}
              columns={[
                { title: '구분', dataIndex: 'tx_type', key: 'tx_type', width: 55,
                  render: (v: string) => ({ out:'출고', in:'입고', adjustment:'조정' }[v] || v) },
                { title: 'SKU', dataIndex: 'sku', key: 'sku', width: 160, ellipsis: true,
                  render: (v: string) => <code style={{ fontSize: '0.72rem' }}>{v}</code> },
                { title: '상품명', dataIndex: 'name', key: 'name', ellipsis: true },
                { title: '채널', dataIndex: 'channel', key: 'channel', width: 90, ellipsis: true,
                  render: (v: string) => v || <span style={{ color: '#9ca3af' }}>—</span> },
                { title: 'BH', dataIndex: 'bh_qty', key: 'bh_qty', width: 70, align: 'right' as const,
                  render: (v: number) => v.toLocaleString() },
                { title: 'OB', dataIndex: 'ob_qty', key: 'ob_qty', width: 70, align: 'right' as const,
                  render: (v: number) => v.toLocaleString() },
                { title: '차이', dataIndex: 'diff', key: 'diff', width: 80, align: 'right' as const,
                  render: (v: number) => (
                    <span style={{ fontWeight: 700, color: v > 0 ? '#ef4444' : '#2563eb' }}>
                      {v > 0 ? '+' : ''}{v.toLocaleString()}
                    </span>
                  ),
                  sorter: (a: unknown, b: unknown) => Math.abs((b as { diff: number }).diff) - Math.abs((a as { diff: number }).diff),
                  defaultSortOrder: 'ascend' as const,
                },
                { title: '활동 날짜', dataIndex: 'dates', key: 'dates', ellipsis: true,
                  render: (v: string[]) => <span style={{ fontSize: '0.72rem', color: '#6b7280' }}>{v.join(' / ')}</span> },
              ]}
              rowClassName={(r: unknown) => (r as { need_boxhero: number }).need_boxhero > 0 ? 'row-error' : 'row-info'}
              style={{ fontSize: '0.82rem' }}
            />
          </>
        )}
      </Modal>

      {/* 재고 현황 모달 */}
      <Modal open={stockOpen} onCancel={()=>setStockOpen(false)} width={1180} footer={null}
        title={
          <div style={{display:'flex',alignItems:'center',gap:12,flexWrap:'wrap'}}>
            <span>📦 재고 현황 — BH vs OB 잔여 재고 비교</span>
            {stockData && <Button size="small" icon={<ExportOutlined />} onClick={exportStockCsv}>CSV</Button>}
            <Button size="small" type="primary" loading={weeklySaving} onClick={saveWeeklyReport}>📸 이번주 리포트 저장</Button>
            {weeklyList.length > 0 && (
              <Select size="small" style={{minWidth:200}} placeholder="📚 지난 리포트 보기"
                onChange={(v:number)=>loadWeeklyReport(v)}
                options={weeklyList.map(r=>({value:r.id, label:`${r.report_date} · 차이 ${r.diff_count} · 추적 ${r.need_trace_count}`}))} />
            )}
          </div>
        }>
        {stockLoading ? (
          <div style={{textAlign:'center',padding:60}}><Spin /><div style={{marginTop:10,color:'#6b7280',fontSize:'0.82rem'}}>재고 조회 중...</div></div>
        ) : !stockData ? (
          <Empty description="데이터 없음" />
        ) : (
          <>
            <Row gutter={12} style={{marginBottom:14}}>
              {[
                {label:'전체 상품', val:stockData.total, color:'#374151', bg:'#f3f4f6', filter:null as null|'ok'|'diff'|'trace'},
                {label:'재고 일치', val:stockData.ok_count, color:'#065f46', bg:'#d1fae5', filter:'ok' as const},
                {label:'재고 차이', val:stockData.diff_count, color:'#991b1b', bg:'#fee2e2', filter:'diff' as const},
                {label:'거래추적 필요', val:stockData.need_trace_count ?? 0, color:'#7c2d12', bg:'#ffedd5', filter:'trace' as const},
              ].map(m=>{
                const active = stockCardFilter === m.filter
                return (
                  <Col span={6} key={m.label}>
                    <div
                      onClick={()=>setStockCardFilter(m.filter)}
                      style={{background:m.bg,borderRadius:8,padding:'8px 12px',textAlign:'center',cursor:'pointer',
                        border:`${active?2:1}px solid ${active?m.color:'transparent'}`,
                        boxShadow:active?'0 2px 6px rgba(0,0,0,0.10)':'none',transition:'all 0.12s'}}
                    >
                      <div style={{fontSize:'1.4rem',fontWeight:800,color:m.color}}>{m.val}</div>
                      <div style={{fontSize:'0.72rem',color:m.color,fontWeight:active?700:400}}>{m.label}{active&&m.filter?' ✓':''}</div>
                    </div>
                  </Col>
                )
              })}
            </Row>
            <div style={{fontSize:'0.74rem',color:'#6b7280',marginBottom:8,display:'flex',alignItems:'center',gap:10,flexWrap:'wrap'}}>
              <span><b>차이 = BH재고 − OB총재고</b>(가용+가용외). 가용외(할당·보류)는 OB 내부 상태라 비교 기준에서 빠져, 할당 타이밍에 따른 출렁임 없이 진짜 정합오차만 잡힙니다. <span style={{color:'#9ca3af'}}>참고(−가용)</span>은 기존 엑셀 기준(BH−OB가용). 행 클릭 시 거래 분석.</span>
              <span style={{marginLeft:'auto',display:'flex',alignItems:'center',gap:6}}>
                <Switch size="small" checked={stockResidualOnly} onChange={setStockResidualOnly} />
                <span style={{color:'#7c2d12',fontWeight:600}}>⚠ 정합오차 있는 것만 보기</span>
              </span>
            </div>
            <div style={{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap',marginBottom:10}}>
              <Input.Search
                allowClear
                placeholder="상품명 · SKU · OB코드 검색"
                value={stockSearch}
                onChange={e=>setStockSearch(e.target.value)}
                style={{maxWidth:300}}
              />
              <Select
                mode="multiple"
                allowClear
                size="middle"
                placeholder="원인 필터"
                value={stockCauseFilter}
                onChange={setStockCauseFilter}
                style={{minWidth:180}}
                options={[
                  {value:'가용외', label:'🟡 가용외'},
                  {value:'매핑다중', label:'🟣 매핑다중'},
                  {value:'미분류', label:'🔴 미분류(거래추적)'},
                ]}
              />
              <Select
                allowClear
                placeholder="차이 방향"
                value={stockDirFilter ?? undefined}
                onChange={v=>setStockDirFilter(v ?? null)}
                style={{width:130}}
                options={[
                  {value:'plus', label:'+ BH가 많음'},
                  {value:'minus', label:'− OB가 많음'},
                ]}
              />
              {(stockCardFilter||stockCauseFilter.length>0||stockDirFilter||stockSearch)&&(
                <Button size="small" onClick={()=>{setStockCardFilter(null);setStockCauseFilter([]);setStockDirFilter(null);setStockSearch('')}}>
                  필터 초기화
                </Button>
              )}
            </div>
            <Table size="small"
              dataSource={(() => {
                let rows = stockResidualOnly ? stockData.rows.filter(r=>r.residual!==null && r.residual!==undefined && r.residual!==0) : stockData.rows
                // 상단 카드 필터 (전체/일치/차이/추적필요 — 백엔드 카운트와 동일 기준)
                if (stockCardFilter==='ok') rows = rows.filter(r=>r.diff===0)
                else if (stockCardFilter==='diff') rows = rows.filter(r=>r.diff!==null && r.diff!==undefined && r.diff!==0)
                else if (stockCardFilter==='trace') rows = rows.filter(r=>r.residual!==null && r.residual!==undefined && r.residual!==0)
                // 원인 태그 필터 (하나라도 해당하면 표시)
                if (stockCauseFilter.length>0) rows = rows.filter(r=>{
                  const types=(r.causes||[]).map(c=>c.type)
                  return stockCauseFilter.some(f=>
                    f==='미분류' ? (r.diff!==null && r.diff!==undefined && r.diff!==0 && types.length===0) : types.includes(f)
                  )
                })
                // 차이 방향 필터
                if (stockDirFilter==='plus') rows = rows.filter(r=>(r.diff??0)>0)
                else if (stockDirFilter==='minus') rows = rows.filter(r=>(r.diff??0)<0)
                const q = stockSearch.trim().toLowerCase()
                if (q) rows = rows.filter(r =>
                  (r.name||'').toLowerCase().includes(q) ||
                  (r.sku||'').toLowerCase().includes(q) ||
                  (r.ob_code||'').toLowerCase().includes(q) ||
                  (r.bh_skus||[]).some(s=>(s||'').toLowerCase().includes(q)) ||
                  (r.ob_codes||[]).some(c=>(c||'').toLowerCase().includes(q)) ||
                  (r.bh_names||[]).some(n=>(n||'').toLowerCase().includes(q)) ||
                  (r.ob_names||[]).some(n=>(n||'').toLowerCase().includes(q))
                )
                return rows
              })()}
              rowKey="name"
              pagination={{pageSize:20,showSizeChanger:true,showTotal:t=>`총 ${t}개 상품`}}
              onRow={(r:unknown)=>({onClick:()=>handleStockRowClick(r as StockRow), style:{cursor:'pointer'}})}
              columns={[
                {title:'상품명', dataIndex:'name', ellipsis:{showTitle:false}, width:260, showSorterTooltip:false,
                  sorter:(a:unknown,b:unknown)=>((a as StockRow).name||'').localeCompare((b as StockRow).name||'','ko'),
                  render:(v:string,r:unknown)=>{
                    const row=r as StockRow
                    const members=Array.from(new Set([...(row.bh_names||[]),...(row.ob_names||[])])).filter(n=>n&&n!==v)
                    const isGroup=(row.bh_skus?.length||0)>1||(row.ob_codes?.length||0)>1||members.length>0
                    const tip = isGroup
                      ? <div><b>{v}</b><div style={{marginTop:4,fontSize:'0.72rem'}}>묶음 {(row.bh_skus?.length||0)} SKU ↔ {(row.ob_codes?.length||0)} 코드</div>
                          {members.length>0&&<div style={{marginTop:3}}>{[v,...members].map((n,i)=><div key={i}>· {n}</div>)}</div>}</div>
                      : v
                    return <Tooltip title={tip} placement="topLeft">
                      <span style={{fontWeight:row.diff!==0&&row.diff!==null?600:400}}>
                        {isGroup&&<Tag color="purple" style={{margin:'0 4px 0 0',fontSize:'0.62rem',padding:'0 4px',lineHeight:'16px'}}>묶음{members.length>0?`+${members.length}`:''}</Tag>}
                        {v}
                      </span>
                    </Tooltip>
                  }},
                {title:'BH 재고', dataIndex:'bh_stock', width:84, align:'right' as const, showSorterTooltip:false,
                  sorter:(a:unknown,b:unknown)=>(((a as StockRow).bh_stock??-Infinity))-(((b as StockRow).bh_stock??-Infinity)),
                  render:(v:number|null)=>v===null?<span style={{color:'#d1d5db'}}>—</span>:<span style={{color:'#1e3a8a',fontWeight:600}}>{v.toLocaleString()}</span>},
                {title:<Tooltip title="OurBox 총재고 = 가용 + 가용외. 비교 기준(차이 = BH재고 − OB총)">OB 총<span style={{color:'#9ca3af'}}>ⓘ</span></Tooltip>, dataIndex:'ob_stock_total', width:84, align:'right' as const, showSorterTooltip:false,
                  sorter:(a:unknown,b:unknown)=>(((a as StockRow).ob_stock_total??-Infinity))-(((b as StockRow).ob_stock_total??-Infinity)),
                  render:(v:number|null)=>v===null?<span style={{color:'#d1d5db'}}>—</span>:<span style={{color:'#111827',fontWeight:600}}>{v.toLocaleString()}</span>},
                {title:'OB 가용', dataIndex:'ob_stock_available', width:84, align:'right' as const, showSorterTooltip:false,
                  sorter:(a:unknown,b:unknown)=>(((a as StockRow).ob_stock_available??-Infinity))-(((b as StockRow).ob_stock_available??-Infinity)),
                  render:(v:number|null)=>v===null?<span style={{color:'#d1d5db'}}>—</span>:<span style={{color:'#92400e'}}>{v.toLocaleString()}</span>},
                {title:'OB 가용외', dataIndex:'ob_unusable', width:78, align:'right' as const, showSorterTooltip:false,
                  sorter:(a:unknown,b:unknown)=>(((a as StockRow).ob_unusable??0))-(((b as StockRow).ob_unusable??0)),
                  render:(v:number)=>!v?<span style={{color:'#d1d5db'}}>0</span>:<Tooltip title="OurBox 가용외 수량(unavailable) = 출고 할당·작업중·불용 등. 총재고엔 포함되므로 차이엔 영향 없음(참고)"><span style={{color:'#a16207',cursor:'help'}}>{v.toLocaleString()}</span></Tooltip>},
                {title:<Tooltip title="차이 = BH재고 − OB총재고. 가용외(할당) 타이밍에 흔들리지 않는 기준. 클릭 정렬: 값 기준 (기본 정렬은 |차이| 큰 순)">차이<span style={{color:'#9ca3af'}}>ⓘ</span></Tooltip>, dataIndex:'diff', width:80, align:'right' as const, showSorterTooltip:false,
                  render:(v:number|null)=>{
                    if(v===null) return <span style={{color:'#d1d5db'}}>—</span>
                    if(v===0) return <Tag color="success" style={{margin:0}}>✓</Tag>
                    return <span style={{color:v>0?'#2563eb':'#ef4444',fontWeight:700}}>{v>0?'+':''}{v.toLocaleString()}</span>
                  },
                  sorter:(a:unknown,b:unknown)=>(((a as StockRow).diff??-Infinity))-(((b as StockRow).diff??-Infinity))},
                {title:<Tooltip title="참고: BH재고 − OB가용 (기존 엑셀 기준). 가용외 할당 타이밍에 따라 출렁임">참고(−가용)<span style={{color:'#9ca3af'}}>ⓘ</span></Tooltip>, dataIndex:'diff_vs_available', width:90, align:'right' as const, showSorterTooltip:false,
                  sorter:(a:unknown,b:unknown)=>(((a as StockRow).diff_vs_available??-Infinity))-(((b as StockRow).diff_vs_available??-Infinity)),
                  render:(v:number|null)=>{
                    if(v===null||v===undefined) return <span style={{color:'#d1d5db'}}>—</span>
                    if(v===0) return <span style={{color:'#9ca3af'}}>0</span>
                    return <span style={{color:'#9ca3af'}}>{v>0?'+':''}{v.toLocaleString()}</span>
                  }},
                {title:'잔여(거래차)', dataIndex:'residual', width:90, align:'right' as const, showSorterTooltip:false,
                  sorter:(a:unknown,b:unknown)=>(((a as StockRow).residual??-Infinity))-(((b as StockRow).residual??-Infinity)),
                  render:(v:number|null)=>{
                    if(v===null||v===undefined) return <span style={{color:'#d1d5db'}}>—</span>
                    if(v===0) return <Tag color="success" style={{margin:0,fontSize:'0.68rem'}}>설명됨</Tag>
                    return <span style={{color:'#7c2d12',fontWeight:700}}>{v>0?'+':''}{v.toLocaleString()}</span>
                  }},
                {title:'원인', key:'causes', width:200,
                  render:(_:unknown,r:unknown)=>{
                    const row=r as StockRow
                    const cs=row.causes||[]
                    if(!cs.length) {
                      if(row.diff && row.diff!==0) return <Tooltip title="행을 클릭한 뒤 '🧭 거래 정밀 대사'를 실행하면 어느 거래에서 차이났는지 자동 분해됩니다"><Tag color="error" style={{margin:0,fontSize:'0.68rem',cursor:'help'}}>미분류(거래추적)</Tag></Tooltip>
                      return <span style={{color:'#d1d5db'}}>—</span>
                    }
                    return <div style={{display:'flex',gap:3,flexWrap:'wrap'}}>
                      {cs.map((c,i)=>(
                        <Tooltip key={i} title={c.desc}>
                          <Tag color={c.type==='가용외'?'gold':c.type==='매핑다중'?'purple':'default'} style={{margin:0,fontSize:'0.68rem',cursor:'help'}}>
                            {c.type}{c.qty?` ${c.qty.toLocaleString()}`:''}
                          </Tag>
                        </Tooltip>
                      ))}
                    </div>
                  }},
              ]}
              rowClassName={(r:unknown)=>{
                const row=r as {diff:number|null;bh_stock:number|null;ob_stock_total:number|null}
                if(row.diff===0) return ''
                if(row.bh_stock===null) return 'row-error'
                if(row.ob_stock_total===null) return 'row-info'
                return 'row-warn'
              }}
            />
            {/* 차이 원인 상세 설명 — 차이 행 클릭 시 (수식으로 풀어 설명) */}
            {stockTraceRow && (() => {
              const r = stockTraceRow
              const bh = r.bh_stock ?? 0, av = r.ob_stock_available ?? 0
              const tot = r.ob_stock_total ?? 0, un = r.ob_unusable ?? 0
              const diff = r.diff ?? 0, resid = r.residual ?? 0
              const fmt = (n:number)=>n.toLocaleString()
              const sign = (n:number)=>n>0?`+${fmt(n)}`:fmt(n)
              return (
                <div style={{marginTop:14, background:'#fffbeb', border:'1px solid #fde68a', borderRadius:8, padding:'12px 16px', fontSize:'0.82rem', lineHeight:1.7}}>
                  <div style={{fontWeight:700, marginBottom:8, color:'#92400e'}}>🔍 {r.name} — 차이 원인 상세</div>
                  <div style={{display:'flex', gap:18, flexWrap:'wrap', marginBottom:10}}>
                    <span>박스히어로 재고 <b style={{color:'#1e3a8a'}}>{fmt(bh)}</b></span>
                    <span>OB 총재고 <b>{fmt(tot)}</b></span>
                    <span style={{color:'#6b7280'}}>(가용 {fmt(av)} + 가용외 {fmt(un)})</span>
                    <span>차이 <b style={{color:diff>=0?'#2563eb':'#ef4444'}}>{sign(diff)}</b></span>
                  </div>
                  {/* 단계별 설명 */}
                  <div style={{background:'#fff', borderRadius:6, padding:'8px 12px', border:'1px solid #fef3c7'}}>
                    <div>① <b>차이 {sign(diff)}</b> = BH재고 {fmt(bh)} − OB총재고 {fmt(tot)}</div>
                    {un > 0 && (
                      <div>② <b style={{color:'#a16207'}}>OB 가용외 {fmt(un)}개</b>: OurBox가 가용에서 뺀 수량(출고 할당·작업중·불용 등).
                        {' '}<b>총재고에 이미 포함</b>돼 있어 이 차이엔 영향이 없습니다(참고). 가용 기준 차이는 {sign(r.diff_vs_available ?? (bh - av))} — 할당 타이밍에 따라 출렁이므로 비교 기준으로 쓰지 않습니다.
                      </div>
                    )}
                    {(r.causes||[]).some(c=>c.type==='매핑다중') && (
                      <div>③ <b style={{color:'#7c3aed'}}>매핑 다중</b>: BH SKU {r.bh_skus?.length||0}개 ↔ OB 코드 {r.ob_codes?.length||0}개가 한 그룹으로 합산됨. 묶음이 정확한지 상품 매핑 확인 권장.
                        {(r.bh_names?.length||0)>0 && (
                          <div style={{marginTop:2,paddingLeft:14,fontSize:'0.76rem',color:'#6b21a8'}}>
                            · BH({r.bh_names!.length}): {r.bh_names!.join(', ')}
                          </div>
                        )}
                        {(r.ob_names?.length||0)>0 && (
                          <div style={{paddingLeft:14,fontSize:'0.76rem',color:'#6b21a8'}}>
                            · OB({r.ob_names!.length}): {r.ob_names!.join(', ')}
                          </div>
                        )}
                        {(r.ob_codes?.length||0)>0 && (
                          <div style={{paddingLeft:14,fontSize:'0.72rem',color:'#9ca3af'}}>
                            · OB코드: {r.ob_codes!.join(', ')}
                          </div>
                        )}
                      </div>
                    )}
                    <div style={{marginTop:4, paddingTop:4, borderTop:'1px dashed #fde68a'}}>
                      ④ <b style={{color:'#7c2d12'}}>잔여 {sign(resid)}</b>:
                      {resid === 0
                        ? ' BH재고와 OB총재고가 일치합니다. 추가 조치 불필요 ✅'
                        : ` BH·OB 총재고가 실제로 어긋난 정합오차입니다(기초재고 베이스·입출고 누락 등). 아래 거래 내역에서 한쪽에만 기록된 입·출고를 확인하세요.`}
                    </div>
                  </div>
                  {(!r.causes || r.causes.length===0) && diff !== 0 && (
                    <div style={{marginTop:8, color:'#991b1b'}}>
                      ⚠ 자동 분류된 원인이 없습니다(부자재이거나 매핑 미등록일 수 있음). 아래 거래 비교로 확인하세요.
                    </div>
                  )}

                  {/* 재고 차이 변화 추적 — 두 시점(t1→t2) 사이 차이가 왜 벌어졌나 */}
                  <div style={{marginTop:10, paddingTop:10, borderTop:'1px dashed #fcd34d'}}>
                    <div style={{fontWeight:700, marginBottom:4, color:'#7c2d12', display:'flex', alignItems:'center', gap:8, flexWrap:'wrap'}}>
                      <span>📈 재고 차이 변화 추적 — 언제·왜 벌어졌나</span>
                      <Select size="small" style={{minWidth:160}} placeholder="비교 기준(이전) 리포트"
                        value={diffT1Id ?? undefined}
                        options={weeklyList.map(w=>({value:w.id, label:`${w.report_date} 기준`}))}
                        onChange={(v:number)=>setDiffT1Id(v)} />
                      <Button size="small" type="primary" loading={diffTraceLoading} disabled={!diffT1Id}
                        onClick={()=>fetchDiffTrace(r.name, diffT1Id!)}>
                        {diffTrace ? '↻ 다시' : '▶ 변화 분석'}
                      </Button>
                      <span style={{fontSize:'0.72rem',color:'#9ca3af'}}>(최신 리포트 대비)</span>
                    </div>
                    {!diffTrace ? (
                      <div style={{color:'#9ca3af',fontSize:'0.76rem'}}>
                        {diffTraceLoading ? '두 시점 재고차 분해 중... (거래 수집 포함, 수분 걸릴 수 있어요)'
                          : '↑ 이전 리포트를 골라 누르면, 그 시점부터 지금까지 BH·OB 차이가 왜 벌어졌는지(OB 가용외 변동 vs 실제 입·출고 흐름)를 분해합니다.'}
                      </div>
                    ) : (() => {
                      const dt = diffTrace
                      const sign = (n:number)=> n>0?`+${n.toLocaleString()}`:n.toLocaleString()
                      const fmtn = (n:number)=> Math.abs(n).toLocaleString()
                      // 한 줄 결론(verdict): Δ변화의 주원인 + 선등록(절대차이 기여) 요약
                      const unav = dt.contrib.ob_unavail_change || 0
                      const flowc = dt.contrib.net_stock_flow || 0
                      const returned = dt.unavail_returned || 0
                      const prebook = dt.prebook_bh || 0
                      const unavT2 = dt.t2.ob_unusable || 0
                      const unavInvolved = Math.abs(unav) >= 50 || unavT2 >= 50
                      const deltaAv = dt.delta_diff_available ?? (flowc + unav)
                      let vColor = '#0e7490', vBg = '#ecfeff', vBorder = '#a5f3fc'
                      const parts: string[] = []
                      const caveats: string[] = []
                      // 주지표는 총재고 기준 → 차이 변화 = 입·출고 순흐름차(가용외는 비교에서 제외)
                      if (Math.abs(flowc) >= 50) {
                        vColor = '#9a3412'; vBg = '#fff7ed'; vBorder = '#fed7aa'
                        parts.push(`총재고 기준 차이 변화는 입·출고 순흐름차(${sign(flowc)})입니다 — 실제 거래 누락/중복 가능성. 아래 개별 거래를 확인하세요.`)
                      } else {
                        vColor = '#166534'; vBg = '#f0fdf4'; vBorder = '#bbf7d0'
                        parts.push(`총재고 기준 차이 변화는 ${sign(flowc)}로 작습니다 — 실제 거래 누락은 아닙니다.`)
                      }
                      // 가용외 변동은 총재고 비교에서 제외됨을 명시 (참고)
                      if (unavInvolved) {
                        if (returned > 0)
                          parts.push(`참고로 이 기간 OB 가용외(할당·보류)가 ${fmtn(returned)}개 가용으로 환원됐지만, 총재고엔 포함되므로 차이엔 영향 없습니다(가용 기준이었다면 ${sign(deltaAv)} 출렁였을 부분).`)
                        else
                          parts.push(`참고로 OB 가용외가 ${sign(unav)} 변동했지만 총재고 비교라 차이엔 영향 없습니다(가용 기준이었다면 ${sign(deltaAv)}).`)
                      }
                      // 선등록 ↔ 가용외 자동 연결: BH가 미리 차감한 발송예정분이 OB엔 할당(가용외)으로 잡혀있을 가능성
                      if (prebook > 0) {
                        const shipDates = (dt.prebook || []).map(p=>p.ship_date).filter(Boolean).sort()
                        const shipHint = shipDates.length ? ` (발송예정 ${shipDates[0]}${shipDates.length>1?` 외`:''})` : ''
                        if (unavT2 >= prebook)
                          parts.push(`또한 현재 차이엔 BH 선등록(발송예정) ${fmtn(prebook)}개가 포함${shipHint} — BH는 이미 차감했고 OurBox엔 발송용으로 할당(가용외 ${fmtn(unavT2)}개에 포함 추정) 상태일 가능성이 큽니다. 발송완료되면 가용외·전체 동시 감소하며 자동 정합됩니다.`)
                        else
                          parts.push(`또한 현재 차이엔 BH 선등록(발송예정) ${fmtn(prebook)}개가 포함${shipHint} — BH가 미래 발송분을 미리 차감했고, OurBox엔 실제 발송 시점에 할당(가용외)으로 잡혔다가 출고완료되며 자동 정합됩니다(시점차).`)
                      }
                      // 가용외 변동성 경고
                      if (unavInvolved)
                        caveats.push('가용외는 OurBox 출고 주문 할당분이라 주문 할당 타이밍에 따라 스냅샷마다 크게 출렁입니다. 이 순간 차이값보다 입·출고 흐름(순흐름차)으로 판단하세요.')
                      // 매핑 그룹 구성 변화 — 거래 없이 가용/가용외가 변한 '흔적'의 실체일 수 있음
                      const gc = dt.group_change
                      const gcChanged = !!(gc && gc.changed)
                      let gcMsg = ''
                      if (gcChanged) {
                        const seg: string[] = []
                        if (gc!.t1_only_codes.length) seg.push(`${dt.t1.date}에만 포함된 OB코드 ${gc!.t1_only_codes.join(', ')}`)
                        if (gc!.t2_only_codes.length) seg.push(`${dt.t2.date}에만 포함된 OB코드 ${gc!.t2_only_codes.join(', ')}`)
                        if (gc!.t1_only_skus.length)  seg.push(`${dt.t1.date}에만 포함된 BH SKU ${gc!.t1_only_skus.join(', ')}`)
                        if (gc!.t2_only_skus.length)  seg.push(`${dt.t2.date}에만 포함된 BH SKU ${gc!.t2_only_skus.join(', ')}`)
                        gcMsg = `두 시점의 매핑 그룹 구성이 다릅니다 — ${seg.join(' · ')}. 같은 '${dt.name}' 행이라도 속을 구성하는 코드가 달라, 빠진 코드의 재고(가용·가용외 포함)가 거래 없이 사라지거나 합쳐집니다. 위 가용외 변동의 상당 부분이 실제 할당 해제가 아니라 이 그룹 재구성에서 왔을 수 있습니다.`
                      }
                      return (
                        <div style={{fontSize:'0.78rem'}}>
                          <div style={{marginBottom:6}}>
                            차이 <b>{dt.t1.date}</b> {sign(dt.t1.diff??0)} → <b>{dt.t2.date}</b> {sign(dt.t2.diff??0)}
                            {'  '}<b style={{color: dt.delta_diff===0?'#10b981':'#ef4444'}}>(Δ {sign(dt.delta_diff)})</b>
                          </div>
                          {gcChanged && (
                            <div style={{background:'#fef2f2', border:'1px solid #fecaca', borderRadius:6, padding:'7px 10px', marginBottom:8, color:'#b91c1c', lineHeight:1.5}}>
                              <b>🚨 그룹 구성 불일치(비교 왜곡 주의):</b> {gcMsg}
                            </div>
                          )}
                          <div style={{background:vBg, border:`1px solid ${vBorder}`, borderRadius:6, padding:'7px 10px', marginBottom:8, color:vColor, lineHeight:1.5}}>
                            <b>📌 결론:</b> {parts.join(' ').replace(/\*\*/g,'')}
                            {caveats.length>0 && <div style={{marginTop:5, paddingTop:5, borderTop:`1px dashed ${vBorder}`, fontSize:'0.72rem', opacity:0.92}}>⚠ {caveats.join(' ')}</div>}
                          </div>
                          <table style={{width:'100%',borderCollapse:'collapse',marginBottom:6,fontSize:'0.76rem'}}>
                            <tbody>
                              <tr style={{borderBottom:'1px solid #fef3c7'}}>
                                <td style={{padding:'3px 6px'}}>OB 가용외(할당·보류) 변동</td>
                                <td style={{padding:'3px 6px',textAlign:'right',fontWeight:700,color:'#9ca3af'}}>{sign(dt.contrib.ob_unavail_change)}</td>
                                <td style={{padding:'3px 6px',color:'#9ca3af',fontSize:'0.72rem'}}>총재고 비교라 차이엔 영향 없음(참고). 가용 기준일 때만 출렁임</td>
                              </tr>
                              <tr style={{borderBottom:'1px solid #fef3c7'}}>
                                <td style={{padding:'3px 6px'}}>입·출고 순흐름 차이 (BH−OB)</td>
                                <td style={{padding:'3px 6px',textAlign:'right',fontWeight:700,color:'#7c2d12'}}>{sign(dt.contrib.net_stock_flow)}</td>
                                <td style={{padding:'3px 6px',color:'#9ca3af',fontSize:'0.72rem'}}>크면 실제 입·출고 누락 의심 → 아래 거래 확인</td>
                              </tr>
                              {Math.abs(dt.residual)>0 && (
                                <tr>
                                  <td style={{padding:'3px 6px',color:'#9ca3af'}}>잔차(스냅샷 경계)</td>
                                  <td style={{padding:'3px 6px',textAlign:'right',color:'#9ca3af'}}>{sign(dt.residual)}</td>
                                  <td/>
                                </tr>
                              )}
                            </tbody>
                          </table>
                          <div style={{fontSize:'0.72rem',color:'#6b7280',lineHeight:1.5}}>
                            BH재고 {dt.t1.bh_stock?.toLocaleString()} → {dt.t2.bh_stock?.toLocaleString()} ({sign(dt.delta_bh_stock)}) · {' '}
                            OB전체 {dt.t1.ob_total?.toLocaleString()} → {dt.t2.ob_total?.toLocaleString()} ({sign(dt.delta_ob_total)}) · {' '}
                            OB가용 {dt.t1.ob_available?.toLocaleString()} → {dt.t2.ob_available?.toLocaleString()} ({sign(dt.delta_ob_available)}) · {' '}
                            OB가용외 {dt.t1.ob_unusable?.toLocaleString()} → {dt.t2.ob_unusable?.toLocaleString()} ({sign(dt.delta_ob_unusable)})
                          </div>
                          {(dt.bh_only.length>0 || dt.ob_only.length>0 || dt.qty_diff.length>0) && (
                            <div style={{marginTop:6, paddingTop:6, borderTop:'1px dashed #fde68a'}}>
                              <b style={{color:'#7c2d12'}}>이 기간 한쪽에만 기록된 거래 (참고용 단서)</b>
                              <div style={{fontSize:'0.7rem',color:'#9ca3af',marginBottom:3}}>※ 아래 거래는 위 분해 수치에 이미 합산 반영됨. 메모를 보고 "직배송/누락/보정" 성격을 판단하세요.</div>
                              {dt.ob_only.map((x: FMItem & {channel?:string},i:number)=><div key={'o'+i} style={{paddingLeft:8,color:'#991b1b'}}>· OB에만 {x.date} [{x.ob_type}] <b>{x.qty.toLocaleString()}</b>{x.channel?<span style={{color:'#9ca3af'}}> · {x.channel}</span>:null}</div>)}
                              {dt.bh_only.map((x: FMItem & {memo?:string;channel?:string},i:number)=><div key={'b'+i} style={{paddingLeft:8,color:'#1e3a8a'}}>· BH에만 {x.date} [{x.bh_type}] <b>{x.qty.toLocaleString()}</b>{x.memo?<span style={{color:'#6b7280'}}> · {x.memo}</span>:null}</div>)}
                              {dt.qty_diff.map((x: FMMatched & {bh_memo?:string},i:number)=><div key={'q'+i} style={{paddingLeft:8,color:'#7c2d12'}}>· 수량차 {x.bh_date} BH {x.bh_qty?.toLocaleString()} / OB {x.ob_qty?.toLocaleString()} ({sign(x.qty_diff)}){x.bh_memo?<span style={{color:'#6b7280'}}> · {x.bh_memo}</span>:null}</div>)}
                            </div>
                          )}
                        </div>
                      )
                    })()}
                  </div>

                  {/* 거래 정밀 대사 — 이벤트 단위로 차이 발생 지점·원인 자동 분해 */}
                  <div style={{marginTop:10, paddingTop:10, borderTop:'1px dashed #fcd34d'}}>
                    <div style={{fontWeight:700, marginBottom:4, color:'#7c2d12', display:'flex', alignItems:'center', gap:8, flexWrap:'wrap'}}>
                      <span>🧭 거래 정밀 대사 — 어느 거래에서 차이났나</span>
                      <Button size="small" type="primary" loading={deepTraceLoading}
                        onClick={()=>fetchDeepTrace(r, stockFlowMonths)}>
                        {deepTrace ? '↻ 다시 대사' : '▶ 정밀 대사 실행'}
                      </Button>
                      <span style={{fontSize:'0.72rem',color:'#9ca3af'}}>(최근 {stockFlowMonths}개월 · 기간은 아래 라디오와 공유)</span>
                    </div>
                    {!deepTrace ? (
                      <div style={{color:'#9ca3af',fontSize:'0.76rem'}}>
                        {deepTraceLoading
                          ? '입·출고 이벤트 짝짓기 중... (같은 수량 ±3일 → 분할기록 상쇄 → 잔여 원인 분류)'
                          : '↑ 실행하면 BH·OB 입출고를 이벤트 단위로 짝짓고, 남는 거래를 교차기록·선차감(가용외)·기간이전·누락으로 자동 분류합니다.'}
                      </div>
                    ) : (() => {
                      const dt2 = deepTrace
                      const sign = (n:number)=> n>0?`+${n.toLocaleString()}`:n.toLocaleString()
                      const tagColor = (t:string)=>
                        t.startsWith('교차기록') ? 'magenta'
                        : t.startsWith('선차감') ? 'gold'
                        : t.startsWith('기간이전 선반영') ? 'cyan'
                        : t==='기간이전차이' ? 'orange'
                        : t.startsWith('기간경계') ? 'default'
                        : t==='BH만 기록' ? 'geekblue'
                        : t==='OB만 기록' ? 'volcano' : 'default'
                      const eqOk = dt2.residual === 0 || dt2.residual === null
                      return (
                        <div style={{fontSize:'0.78rem'}}>
                          <div style={{background: eqOk?'#f0fdf4':'#fef2f2', border:`1px solid ${eqOk?'#bbf7d0':'#fecaca'}`, borderRadius:6, padding:'7px 10px', marginBottom:8, lineHeight:1.6}}>
                            <b>📌 등식 검증:</b> 차이 {sign(dt2.diff_now ?? 0)} = 기간이전 {sign(dt2.opening_gap ?? 0)} + 잔여거래 합 {sign(dt2.explained - (dt2.opening_gap ?? 0))}
                            {'  '}→ 미해소 <b style={{color: eqOk?'#166534':'#b91c1c'}}>{sign(dt2.residual ?? 0)}</b>
                            {eqOk ? ' ✅ 전부 설명됨' : ' ⚠ 설명 안 된 잔차 있음'}
                            <span style={{marginLeft:10, color:'#9ca3af', fontSize:'0.72rem'}}>
                              (짝지어진 거래: 입고 {dt2.matched_in} · 출고 {dt2.matched_out}건 — 시점차 ±3일·분할기록 흡수)
                            </span>
                          </div>
                          {dt2.avail_basis && dt2.avail_basis.diff_avail !== null && (dt2.avail_basis.ob_unav !== 0 || dt2.avail_basis.diff_avail !== (dt2.diff_now ?? 0)) && (
                            <div style={{background:'#fffbeb', border:'1px solid #fde68a', borderRadius:6, padding:'7px 10px', marginBottom:8, lineHeight:1.6}}>
                              <b>📎 참고(−가용) 기준 분해:</b> BH−OB가용 <b>{sign(dt2.avail_basis.diff_avail)}</b> = 차이(총재고) {sign(dt2.diff_now ?? 0)} + 가용외 {sign(dt2.avail_basis.ob_unav)}
                              <span style={{marginLeft:8, color:'#9ca3af', fontSize:'0.72rem'}}>— 총재고 차이 원인은 아래 목록, 가용외 몫은 할당 시점으로 분해:</span>
                              {dt2.avail_basis.unav_events.length > 0 ? (
                                <div style={{fontSize:'0.72rem', color:'#78716c', marginTop:2}}>
                                  가용외 변동(스냅샷): {dt2.avail_basis.unav_events.map(u=>`${u.date} ${u.delta>0?'+':''}${u.delta.toLocaleString()}`).join(' · ')}
                                  <span style={{color:'#a8a29e'}}> (+는 주문 할당 = 가용→가용외, −는 발송 완료·할당 해제)</span>
                                </div>
                              ) : (
                                <div style={{fontSize:'0.72rem', color:'#a8a29e', marginTop:2}}>
                                  기간 내 가용외 변동 스냅샷 없음 — 지금 가용외 {dt2.avail_basis.ob_unav.toLocaleString()}개는 스냅샷 보관(60일) 이전 또는 조회 기간 밖에 할당된 것. 위 스냅샷 그래프에서 할당 시점을 확인하세요.
                                </div>
                              )}
                            </div>
                          )}
                          <div style={{fontSize:'0.72rem', color:'#6b7280', marginBottom:6}}>
                            기간 흐름: BH 입고 {dt2.totals.bh_in.toLocaleString()} / OB 입고 {dt2.totals.ob_in.toLocaleString()} ·
                            BH 출고 {dt2.totals.bh_out.toLocaleString()} / OB 출고 {dt2.totals.ob_out.toLocaleString()}
                          </div>
                          {dt2.causes.length === 0 ? (
                            <div style={{color:'#166534'}}>잔여 거래 없음 — 기간 내 모든 입·출고가 양쪽에서 일치합니다 ✅</div>
                          ) : dt2.causes.map((c,i)=>(
                            <div key={i} style={{display:'flex', gap:8, alignItems:'flex-start', padding:'5px 8px', borderBottom:'1px solid #fef3c7', background: c.type.startsWith('교차기록')?'#fdf2f8':undefined}}>
                              <Tag color={tagColor(c.type)} style={{margin:0, fontSize:'0.68rem', flexShrink:0}}>{c.type}</Tag>
                              <b style={{color: c.impact>0?'#2563eb':c.impact<0?'#ef4444':'#9ca3af', flexShrink:0, minWidth:52, textAlign:'right'}}>{sign(c.impact)}</b>
                              <span style={{color:'#374151', lineHeight:1.5}}>
                                {c.desc}
                                {c.partner && <b style={{color:'#be185d'}}> ↔ {c.partner}</b>}
                              </span>
                            </div>
                          ))}
                          {(dt2.errors||[]).length > 0 && (
                            <div style={{marginTop:6, color:'#b45309', fontSize:'0.72rem'}}>⚠ {dt2.errors.join(' · ')}</div>
                          )}
                        </div>
                      )
                    })()}
                  </div>

                  {/* 유형별 거래 비교 — 잔여 차이가 입고/출고/조정 어디서 났는지 */}
                  <div style={{marginTop:10}}>
                    <div style={{fontWeight:700, marginBottom:4, color:'#7c2d12', display:'flex', alignItems:'center', gap:8, flexWrap:'wrap'}}>
                      <span>📊 최근 거래 비교 — 어느 유형에서 어긋났나</span>
                      <Radio.Group size="small" value={stockFlowMonths} onChange={e=>setStockFlowMonths(e.target.value)}>
                        <Radio.Button value={1}>1개월</Radio.Button>
                        <Radio.Button value={3}>3개월</Radio.Button>
                        <Radio.Button value={6}>6개월</Radio.Button>
                        <Radio.Button value={12}>1년</Radio.Button>
                      </Radio.Group>
                      <Button size="small" type="primary" loading={stockFlowLoading}
                        onClick={()=>fetchStockFlow(r.name, stockFlowMonths, r.bh_skus, r.ob_codes)}>
                        {stockFlow ? '↻ 다시 분석' : '▶ 거래 분석 실행'}
                      </Button>
                    </div>
                    {!stockFlow ? (
                      <div style={{color:'#9ca3af',fontSize:'0.76rem'}}>
                        {stockFlowLoading
                          ? '거래 수집 중... (이 기간을 처음 보면 OurBox API가 느려 수분 걸릴 수 있어요. 같은 기간으로 비교조회를 먼저 실행하면 캐시되어 즉시 표시됩니다)'
                          : '↑ "거래 분석 실행"을 누르면 이 품목의 입고/출고/조정을 BH·OB 비교합니다. (주간 리포트로 보면 미리 계산되어 즉시 표시됩니다)'}
                      </div>
                    ) : (() => {
                      const types: {k:keyof TxTotals;label:string}[] = [{k:'in',label:'입고'},{k:'out',label:'출고'},{k:'move',label:'이동(위치이전)'},{k:'adjustment',label:'조정/등록'}]
                      return (
                        <>
                        <table style={{width:'100%',borderCollapse:'collapse',fontSize:'0.78rem'}}>
                          <thead><tr style={{background:'#fef3c7'}}>
                            <th style={{padding:'3px 6px',textAlign:'left'}}>유형</th>
                            <th style={{padding:'3px 6px',textAlign:'right'}}>BH</th>
                            <th style={{padding:'3px 6px',textAlign:'right'}}>OB</th>
                            <th style={{padding:'3px 6px',textAlign:'right'}}>차이</th>
                            <th style={{padding:'3px 6px',textAlign:'left'}}>해석</th>
                          </tr></thead>
                          <tbody>
                            {types.map(t=>{
                              const b=stockFlow.bh[t.k]||0, o=stockFlow.ob[t.k]||0, df=b-o
                              if(b===0&&o===0) return null
                              const interp = df===0 ? '일치' :
                                t.k==='in' ? (df>0?'BH 입고가 더 많음 (OB 입고 누락/미등록 의심)':'OB 입고가 더 많음 (BH 입고 누락 의심)') :
                                t.k==='out' ? (df>0?'BH 출고가 더 많음 (OB 출고 누락)':'OB 출고가 더 많음 (BH 출고 미입력 의심)') :
                                t.k==='move' ? 'BH 위치이전 — 총 재고 불변, 출고와 별개' :
                                '참고용 — BH 실사·기초정리 조정이 섞여 1:1 대사 어려움'
                              return (
                                <tr key={t.k} style={{borderBottom:'1px solid #fef3c7'}}>
                                  <td style={{padding:'3px 6px'}}>{t.label}</td>
                                  <td style={{padding:'3px 6px',textAlign:'right',color:'#1e3a8a'}}>{b.toLocaleString()}</td>
                                  <td style={{padding:'3px 6px',textAlign:'right',color:'#92400e'}}>{o.toLocaleString()}</td>
                                  <td style={{padding:'3px 6px',textAlign:'right',fontWeight:700,color:(t.k==='adjustment'||t.k==='move')?'#9ca3af':(df===0?'#10b981':df>0?'#2563eb':'#ef4444')}}>{df>0?'+':''}{df.toLocaleString()}</td>
                                  <td style={{padding:'3px 6px',color:(t.k==='adjustment'||t.k==='move')?'#9ca3af':(df===0?'#6b7280':'#7c2d12'),fontSize:'0.74rem'}}>{interp}</td>
                                </tr>
                              )
                            })}
                          </tbody>
                        </table>
                        <div style={{fontSize:'0.7rem',color:'#9ca3af',marginTop:4,lineHeight:1.4}}>
                          ※ 조정/등록은 세트조립(OB 전산처리용↔BH 조정)·재고 실사·기초재고 정리가 섞여 있어 BH·OB 1:1 대사가 어렵습니다 — <b>참고용</b>. 차이 판단은 입고·출고 위주로 보세요.
                        </div>
                        </>
                      )
                    })()}
                  </div>

                  {/* 출고 채널 분해 — BH출고−OB출고 차이를 'OB 미경유(직배송)' vs '경유 채널 실제차이'로 분류 */}
                  <div style={{marginTop:12, paddingTop:10, borderTop:'1px solid #fde68a'}}>
                    <div style={{fontWeight:700, marginBottom:6, color:'#7c2d12', display:'flex', alignItems:'center', gap:8, flexWrap:'wrap'}}>
                      <span>🚚 출고 채널 분해 — 차이가 직배송인가, 진짜 누락인가</span>
                      <Button size="small" type="primary" loading={outDecompLoading}
                        onClick={()=>fetchOutDecomp(r.name, stockFlowMonths, r.bh_skus, r.ob_codes)}>
                        {outDecomp ? '↻ 다시' : '▶ 출고 채널 분해'}
                      </Button>
                      <span style={{fontSize:'0.72rem',color:'#9ca3af'}}>(최근 {stockFlowMonths}개월, 채널별 BH vs OB)</span>
                    </div>
                    {!outDecomp ? (
                      <div style={{color:'#9ca3af',fontSize:'0.76rem'}}>
                        {outDecompLoading ? '채널별 출고 수집·분류 중... (OB 수집 포함, 수분 걸릴 수 있어요)'
                          : '↑ 누르면 이 품목의 BH 출고를 채널별로 나눠 "OB 미경유(직배송)"과 "OB 경유 채널의 실제 차이"로 분해합니다.'}
                      </div>
                    ) : (() => {
                      const od = outDecomp
                      const fmt = (n:number)=>n.toLocaleString()
                      const sgn = (n:number)=>(n>0?'+':'')+n.toLocaleString()
                      const KIND_LABEL: Record<string,string> = { ob_bypass:'직배송(OB 미경유)', bh_missing:'OB만 기록', diff:'양쪽 차이', match:'일치' }
                      const KIND_COLOR: Record<string,string> = { ob_bypass:'#7c3aed', bh_missing:'#ef4444', diff:'#d97706', match:'#10b981' }
                      return (
                        <>
                          <div style={{display:'flex',gap:8,flexWrap:'wrap',marginBottom:8}}>
                            {[
                              {label:'BH 출고', val:fmt(od.bh_out_total), color:'#1e3a8a', bg:'#dbeafe', hint:'기간 내 BH 출고 합계'},
                              {label:'OB 출고', val:fmt(od.ob_out_total), color:'#92400e', bg:'#fef3c7', hint:'기간 내 OB(아워박스) 출고 합계'},
                              {label:'차이(BH−OB)', val:sgn(od.diff), color: od.diff===0?'#10b981':'#2563eb', bg:'#e0e7ff', hint:'아래 직배송+선등록+순미스매칭으로 분해됨'},
                              {label:'직배송(OB 미경유)', val:fmt(od.bypass_bh), color:'#7c3aed', bg:'#f3e8ff', hint:'BH만 출고·OB 0인 채널 합 — 아워박스 미경유(직배송). 차이의 정상 설명분'},
                              {label:'선등록(발송예정)', val:fmt(od.prebook_bh), color:'#0891b2', bg:'#cffafe', hint:'메모 발송일이 조회 종료일보다 미래 — BH가 미리 등록, OB는 실제 발송 후 기록. 곧 해소될 시점차'},
                              {label:'순수 미스매칭', val:sgn(od.real_diff), color: od.real_diff===0?'#10b981':'#dc2626', bg:'#fee2e2', hint:'직배송·선등록 제외한 나머지 — 진짜 점검 대상(한쪽 누락/중복 의심)'},
                            ].map((c,i)=>(
                              <Tooltip key={i} title={c.hint||''}>
                                <div style={{flex:'1 1 120px',background:c.bg,borderRadius:6,padding:'6px 10px',minWidth:110}}>
                                  <div style={{fontSize:'0.68rem',color:'#6b7280'}}>{c.label}</div>
                                  <div style={{fontSize:'1rem',fontWeight:700,color:c.color}}>{c.val}</div>
                                </div>
                              </Tooltip>
                            ))}
                          </div>
                          <table style={{width:'100%',borderCollapse:'collapse',fontSize:'0.76rem'}}>
                            <thead><tr style={{background:'#fef3c7'}}>
                              <th style={{padding:'3px 6px',textAlign:'left'}}>채널</th>
                              <th style={{padding:'3px 6px',textAlign:'right'}}>BH 출고</th>
                              <th style={{padding:'3px 6px',textAlign:'right'}}>OB 출고</th>
                              <th style={{padding:'3px 6px',textAlign:'right'}}>차이</th>
                              <th style={{padding:'3px 6px',textAlign:'left'}}>분류</th>
                            </tr></thead>
                            <tbody>
                              {od.channels.map((c,i)=>(
                                <tr key={i} style={{borderBottom:'1px solid #fef3c7'}}>
                                  <td style={{padding:'3px 6px'}}>{c.channel}</td>
                                  <td style={{padding:'3px 6px',textAlign:'right',color:'#1e3a8a'}}>{fmt(c.bh_out)}</td>
                                  <td style={{padding:'3px 6px',textAlign:'right',color:'#92400e'}}>{fmt(c.ob_out)}</td>
                                  <td style={{padding:'3px 6px',textAlign:'right',fontWeight:700,color:c.diff===0?'#10b981':c.diff>0?'#2563eb':'#ef4444'}}>{sgn(c.diff)}</td>
                                  <td style={{padding:'3px 6px'}}>
                                    <Tag style={{margin:0}} color={KIND_COLOR[c.kind]}>{KIND_LABEL[c.kind]||c.kind}</Tag>
                                    {c.prebook>0 && <Tag style={{margin:'0 0 0 4px'}} color="#0891b2">선등록 {fmt(c.prebook)}</Tag>}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                          {od.prebook.length>0 && (
                            <div style={{marginTop:8, background:'#ecfeff', border:'1px solid #a5f3fc', borderRadius:6, padding:'6px 10px'}}>
                              <b style={{color:'#0891b2',fontSize:'0.78rem'}}>📅 BH 선등록(발송예정) 거래 — OB는 실제 발송 후 기록되어 아직 없음</b>
                              {od.prebook.map((p,i)=>(
                                <div key={i} style={{fontSize:'0.74rem',color:'#155e75',paddingLeft:8,marginTop:2}}>
                                  · {p.date} 등록 → <b>{p.ship_date} 발송예정</b> · {p.qty.toLocaleString()} · {p.channel}
                                  <span style={{color:'#6b7280'}}> · {p.memo}</span>
                                </div>
                              ))}
                              <div style={{fontSize:'0.7rem',color:'#0e7490',marginTop:3}}>※ 조회 종료일을 발송예정일 이후로 늘리면 OB에도 잡혀 차이가 사라집니다.</div>
                            </div>
                          )}
                          <div style={{fontSize:'0.7rem',color:'#9ca3af',marginTop:4,lineHeight:1.4}}>
                            ※ <b style={{color:'#7c3aed'}}>직배송(OB 미경유)</b>: BH엔 출고됐으나 OB엔 없는 채널(아워박스 미경유).
                            {' '}<b style={{color:'#0891b2'}}>선등록</b>: 발송예정일이 미래라 OB 미반영(시점차).
                            {' '}<b style={{color:'#d97706'}}>양쪽 차이</b>(선등록 제외분)가 진짜 점검 대상(한쪽 누락/중복 의심).
                          </div>
                          {od.errors.length>0 && <div style={{fontSize:'0.68rem',color:'#ef4444',marginTop:3}}>{od.errors.join(' · ')}</div>}
                        </>
                      )
                    })()}
                  </div>

                  {/* OB 가용외 추적 — 가용→가용외(할당)가 언제 떨어지는지 시계열 */}
                  <div style={{marginTop:12, paddingTop:10, borderTop:'1px solid #fde68a'}}>
                    <div style={{fontWeight:700, marginBottom:6, color:'#7c2d12', display:'flex', alignItems:'center', gap:8, flexWrap:'wrap'}}>
                      <span>📈 가용외 추적 — 가용→가용외(할당) 언제 떨어지나</span>
                      <Button size="small" type="primary" loading={snapsLoading}
                        onClick={()=>fetchSnaps(r.name, r.ob_codes)}>
                        {snaps ? '↻ 다시' : '▶ 스냅샷 추이 보기'}
                      </Button>
                      <Button size="small" loading={snapCapturing}
                        onClick={()=>captureSnapNow(r.name, r.ob_codes)}>
                        지금 1회 캡처
                      </Button>
                      <span style={{fontSize:'0.72rem',color:'#9ca3af'}}>(2시간마다 자동 기록)</span>
                    </div>
                    {!snaps ? (
                      <div style={{color:'#9ca3af',fontSize:'0.76rem'}}>
                        {snapsLoading ? '스냅샷 불러오는 중...'
                          : '↑ OurBox 가용/가용외/전체를 시각별로 기록한 추이입니다. 가용외(할당)가 뛰는 시점 = 가용에서 할당이 떨어진 때.'}
                      </div>
                    ) : snaps.length===0 ? (
                      <div style={{color:'#9ca3af',fontSize:'0.76rem'}}>아직 기록된 스냅샷이 없습니다. "지금 1회 캡처"를 누르면 현재 상태가 저장되고, 이후 2시간마다 자동 기록됩니다.</div>
                    ) : (() => {
                      const f=(n:number)=>n.toLocaleString()
                      const sg=(n:number)=>(n>0?'+':'')+n.toLocaleString()
                      return (
                        <>
                          <table style={{width:'100%',borderCollapse:'collapse',fontSize:'0.76rem'}}>
                            <thead><tr style={{background:'#fef3c7'}}>
                              <th style={{padding:'3px 6px',textAlign:'left'}}>시각</th>
                              <th style={{padding:'3px 6px',textAlign:'right'}}>전체</th>
                              <th style={{padding:'3px 6px',textAlign:'right'}}>가용</th>
                              <th style={{padding:'3px 6px',textAlign:'right'}}>가용외(할당)</th>
                              <th style={{padding:'3px 6px',textAlign:'right'}}>Δ가용외</th>
                            </tr></thead>
                            <tbody>
                              {snaps.map((s,i)=>{
                                const big = Math.abs(s.d_unavail) >= 100
                                return (
                                  <tr key={i} style={{borderBottom:'1px solid #fef3c7', background: big?'#eff6ff':undefined}}>
                                    <td style={{padding:'3px 6px'}}>{s.captured_at}</td>
                                    <td style={{padding:'3px 6px',textAlign:'right',color:'#6b7280'}}>{f(s.total)}</td>
                                    <td style={{padding:'3px 6px',textAlign:'right',color:'#1e3a8a'}}>{f(s.available)}</td>
                                    <td style={{padding:'3px 6px',textAlign:'right',fontWeight:700,color:'#a16207'}}>{f(s.unavailable)}</td>
                                    <td style={{padding:'3px 6px',textAlign:'right',fontWeight:big?700:400,color:i===0?'#9ca3af':s.d_unavail>0?'#2563eb':s.d_unavail<0?'#ef4444':'#9ca3af'}}>{i===0?'—':sg(s.d_unavail)}</td>
                                  </tr>
                                )
                              })}
                            </tbody>
                          </table>
                          <div style={{fontSize:'0.7rem',color:'#9ca3af',marginTop:4,lineHeight:1.4}}>
                            ※ <b style={{color:'#2563eb'}}>Δ가용외 +</b>: 그 시점에 가용→가용외 <b>할당이 떨어짐</b>(주문 접수/지시). <b style={{color:'#ef4444'}}>Δ가용외 −</b>: 출고완료(발송)되거나 할당 취소로 풀림. 파란 행 = 변동 큰 시점.
                            {' '}자동 기록은 2시간 간격이라 더 정밀히 보려면 "지금 1회 캡처"로 수동 추가하세요.
                          </div>
                        </>
                      )
                    })()}
                  </div>

                  {/* 개별 거래 매칭 (full-match) — 짝 안 맞는 거래로 전산오류 위치 찾기 */}
                  <div style={{marginTop:12, paddingTop:10, borderTop:'1px solid #fde68a'}}>
                    <div style={{fontWeight:700, marginBottom:6, color:'#7c2d12', display:'flex', alignItems:'center', gap:8, flexWrap:'wrap'}}>
                      <span>🔍 개별 거래 매칭 — 어느 거래가 한쪽에만 있나</span>
                      <Button size="small" type="primary" loading={stockFMLoading}
                        onClick={()=>fetchStockFM(stockFlowMonths)}>
                        {stockFM ? '↻ 다시' : '▶ 개별 거래 매칭'}
                      </Button>
                      <span style={{fontSize:'0.72rem',color:'#9ca3af'}}>(최근 {stockFlowMonths}개월, BH↔OB 1:1 매칭)</span>
                    </div>
                    {!stockFM ? (
                      <div style={{color:'#9ca3af',fontSize:'0.76rem'}}>
                        {stockFMLoading ? '거래 1:1 매칭 중... (OB 수집 포함, 수분 걸릴 수 있어요)'
                          : '↑ 누르면 이 품목의 BH·OB 거래를 1:1 매칭해 "어느 거래가 한쪽에만 기록됐는지"를 콕 집어줍니다.'}
                      </div>
                    ) : (() => {
                      const nk = normName(r.name)
                      const bo = stockFM.bh_only.filter(x=>normName(x.name)===nk)
                      const oo = stockFM.ob_only.filter(x=>normName(x.name)===nk)
                      const qd = stockFM.matched.filter(x=>(normName(x.bh_name)===nk||normName(x.ob_name)===nk) && x.qty_diff)
                      if(!bo.length && !oo.length && !qd.length)
                        return <div style={{color:'#10b981',fontSize:'0.78rem'}}>✓ 이 품목은 모든 거래가 BH·OB 1:1로 맞습니다.</div>
                      return (
                        <div style={{fontSize:'0.78rem'}}>
                          {oo.length>0 && <div style={{marginBottom:4}}>
                            <b style={{color:'#991b1b'}}>OB에만 있음 (BH 미입력 의심)</b>
                            {oo.map((x,i)=><div key={i} style={{paddingLeft:8}}>· {x.date} [{x.ob_type}] <b>{x.qty.toLocaleString()}</b></div>)}
                          </div>}
                          {bo.length>0 && <div style={{marginBottom:4}}>
                            <b style={{color:'#1e3a8a'}}>BH에만 있음 (OB 미반영 의심)</b>
                            {bo.map((x,i)=><div key={i} style={{paddingLeft:8}}>· {x.date} [{x.bh_type}] <b>{x.qty.toLocaleString()}</b></div>)}
                          </div>}
                          {qd.length>0 && <div>
                            <b style={{color:'#92400e'}}>수량 차이</b>
                            {qd.map((x,i)=><div key={i} style={{paddingLeft:8}}>· {x.bh_date}↔{x.ob_date} BH{x.bh_qty.toLocaleString()} / OB{x.ob_qty.toLocaleString()} (차 {x.qty_diff>0?'+':''}{x.qty_diff}{x.day_gap?`, ${x.day_gap}일차`:''})</div>)}
                          </div>}
                        </div>
                      )
                    })()}
                  </div>
                </div>
              )
            })()}

            {/* 거래 추적 패널 — 차이 행 클릭 시 */}
            {stockTraceName && (
              <div style={{marginTop:14, background:'#ecfeff', border:'1px solid #a5f3fc', borderRadius:8, padding:'10px 14px'}}>
                <div style={{fontWeight:700, marginBottom:6, color:'#155e75', display:'flex', justifyContent:'space-between'}}>
                  <span>🔗 {stockTraceName} — 확정 매칭 거래 ({stockTracePairs.length}건, {fromDate}~{toDate})</span>
                  <a style={{fontSize:'0.78rem',cursor:'pointer'}} onClick={()=>{setStockTraceName(null);setStockTraceRow(null)}}>닫기</a>
                </div>
                {stockTracePairs.length===0 ? (
                  <div style={{color:'#6b7280',fontSize:'0.78rem'}}>이 기간에 확정된 매칭 거래가 없습니다. (전체수량 매칭 → 매칭 확정 먼저 실행)</div>
                ) : (
                  <div style={{maxHeight:200,overflowY:'auto',fontSize:'0.78rem'}}>
                    {stockTracePairs.map((p,i)=>(
                      <div key={i} style={{display:'flex',gap:8,alignItems:'center',padding:'3px 0',borderBottom:'1px solid #cffafe'}}>
                        <span style={{flex:1}}>BH <strong>{p.bh_qty.toLocaleString()}</strong> · {p.bh_date}</span>
                        <SwapOutlined style={{color:p.bh_date!==p.ob_date?'#f59e0b':'#10b981'}}/>
                        <span style={{flex:1}}>OB <strong>{p.ob_qty.toLocaleString()}</strong> · {p.ob_date}
                          {p.qty_diff!==0 && <Tag color="warning" style={{marginLeft:4,fontSize:'0.68rem'}}>{p.qty_diff>0?'+':''}{p.qty_diff}</Tag>}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </Modal>

      {/* 거래처(채널)별 / 제품별 비교 모달 */}
      <Modal open={cfOpen} onCancel={()=>setCfOpen(false)} width={1080} footer={null}
        title={<span>🏷️ {cfGroupBy === 'product' ? '제품별' : '거래처별'} 비교 — BH vs OB 입·출고·조정 (누락 즉시 식별)</span>}>
        <div style={{display:'flex', alignItems:'center', gap:10, marginBottom:10, flexWrap:'wrap'}}>
          <Radio.Group size="small" value={cfGroupBy} onChange={e=>{setCfGroupBy(e.target.value); fetchChannelFlow(cfDays, e.target.value)}}>
            <Radio.Button value="channel">거래처별</Radio.Button>
            <Radio.Button value="product">제품별</Radio.Button>
          </Radio.Group>
          <span style={{color:'#d1d5db'}}>|</span>
          <Radio.Group size="small" value={cfDays} onChange={e=>{setCfDays(e.target.value); fetchChannelFlow(e.target.value)}}>
            <Radio.Button value={7}>최근 1주</Radio.Button>
            <Radio.Button value={14}>2주</Radio.Button>
            <Radio.Button value={30}>1개월</Radio.Button>
          </Radio.Group>
          <Input.Search
            size="small" placeholder="제품명 검색" allowClear
            style={{width:220}}
            value={cfProductFilter}
            onChange={e=>setCfProductFilter(e.target.value)}
            onSearch={v=>fetchChannelFlow(cfDays, undefined, v)}
            enterButton="조회"
          />
          {cfData && <span style={{fontSize:'0.78rem', color:'#6b7280'}}>{cfData.from} ~ {cfData.to}{cfGroupBy==='channel' && !cfData.channel_mapped && ' · ⚠ 채널 매핑 없음'}</span>}
        </div>
        {cfLoading ? (
          <div style={{padding:'40px 0', textAlign:'center'}}><Spin tip="거래처별 거래 수집·집계 중... (OB 수집 포함, 수분 걸릴 수 있어요)" /></div>
        ) : !cfData ? (
          <Empty description="기간을 선택하면 거래처별 BH·OB 비교를 보여줍니다" />
        ) : (
          <>
            <div style={{fontSize:'0.75rem', color:'#6b7280', marginBottom:6, lineHeight:1.6}}>
              우선순위로 정렬됨: <Tag color="red">🔴 {cfGroupBy==='product'?'BH에만':'BH 등록 누락 의심'}</Tag> (OB만 있음) → <Tag color="orange">🟡 차이</Tag> (양쪽 다 있으나 차이) → <Tag color="blue">🔵 {cfGroupBy==='product'?'OB에만':'직배송 의심'}</Tag> (BH만 있음) → <Tag color="green">🟢 일치</Tag>
            </div>
            {cfGroupBy === 'product' && <div style={{fontSize:'0.72rem', color:'#9ca3af', marginBottom:6}}>총 {cfData.rows.length}개 제품 · 차이 있는 제품 {cfData.rows.filter(r=>r.kind==='diff'||r.kind==='bh_missing'||r.kind==='ob_bypass').length}개</div>}
            <div style={{overflowX:'auto'}}>
            <table style={{width:'100%', borderCollapse:'collapse', fontSize:'0.8rem', minWidth:820}}>
              <thead>
                <tr style={{background:'#f9fafb', borderBottom:'2px solid #e5e7eb'}}>
                  <th style={{padding:'6px 8px', textAlign:'left'}}>분류</th>
                  <th style={{padding:'6px 8px', textAlign:'left'}}>{cfGroupBy==='product'?'제품명':'거래처'}</th>
                  <th style={{padding:'6px 8px', textAlign:'right'}}>BH 출고</th>
                  <th style={{padding:'6px 8px', textAlign:'right'}}>OB 출고</th>
                  <th style={{padding:'6px 8px', textAlign:'right'}}>출고 차이</th>
                  <th style={{padding:'6px 8px', textAlign:'right'}}>BH 입고</th>
                  <th style={{padding:'6px 8px', textAlign:'right'}}>OB 입고</th>
                  <th style={{padding:'6px 8px', textAlign:'right'}}>BH 조정</th>
                  <th style={{padding:'6px 8px', textAlign:'right'}}>OB 조정</th>
                </tr>
              </thead>
              <tbody>
                {(()=>{
                  const kindMeta: Record<string,{tag:string;color:string;bg:string;label:string}> = {
                    bh_missing: {tag:'🔴', color:'red',    bg:'#fef2f2', label:'BH 누락'},
                    diff:       {tag:'🟡', color:'orange', bg:'#fffbeb', label:'차이'},
                    ob_bypass:  {tag:'🔵', color:'blue',   bg:'#eff6ff', label:'직배송'},
                    match:      {tag:'🟢', color:'green',  bg:'',        label:'일치'},
                    unknown:    {tag:'⚪', color:'default',bg:'#f9fafb', label:'채널미상'},
                  }
                  const pf = cfProductFilter.trim().toLowerCase()
                  const renderRow = (r: ChannelRow | ProductSubRow, i: number, indent=false) => {
                    const km = kindMeta[r.kind] || kindMeta.unknown
                    const dColor = r.diff_out===0 ? '#10b981' : r.diff_out>0 ? '#2563eb' : '#ef4444'
                    const emph = r.kind==='bh_missing' || (r.kind==='diff' && Math.abs(r.diff_out) >= 100)
                    const label = ('product' in r && r.product) || ('channel' in r && (r as ChannelRow).channel) || ''
                    return (
                      <tr key={`${indent?'p':'c'}-${i}`} style={{borderBottom:'1px solid #f3f4f6', background: indent ? '#fafbfc' : (km.bg || undefined)}}>
                        <td style={{padding:'5px 8px'}}><Tag color={km.color} style={indent?{fontSize:'0.65rem'}:undefined}>{km.tag} {km.label}</Tag></td>
                        <td style={{padding:'5px 8px', fontWeight: emph?700:400, paddingLeft: indent?28:8, fontSize: indent?'0.76rem':undefined, color: indent?'#374151':undefined}}>{label}</td>
                        <td style={{padding:'5px 8px', textAlign:'right', color: indent?'#6b7280':'#1e3a8a'}}>{r.bh_out.toLocaleString()}</td>
                        <td style={{padding:'5px 8px', textAlign:'right', color: indent?'#6b7280':'#92400e'}}>{r.ob_out.toLocaleString()}</td>
                        <td style={{padding:'5px 8px', textAlign:'right', fontWeight:700, color:dColor}}>{r.diff_out>0?'+':''}{r.diff_out.toLocaleString()}</td>
                        <td style={{padding:'5px 8px', textAlign:'right', color:'#9ca3af'}}>{r.bh_in.toLocaleString()}</td>
                        <td style={{padding:'5px 8px', textAlign:'right', color:'#9ca3af'}}>{r.ob_in.toLocaleString()}</td>
                        <td style={{padding:'5px 8px', textAlign:'right', color:'#9ca3af'}}>{r.bh_adj.toLocaleString()}</td>
                        <td style={{padding:'5px 8px', textAlign:'right', color:'#9ca3af'}}>{r.ob_adj.toLocaleString()}</td>
                      </tr>
                    )
                  }
                  const out: React.ReactNode[] = []
                  cfData.rows.forEach((r,i) => {
                    const hasProducts = cfGroupBy==='channel' && r.products && r.products.length > 0
                    const filteredProducts = hasProducts
                      ? (pf ? r.products!.filter(p=>p.product.toLowerCase().includes(pf)) : r.products!)
                      : []
                    // 채널모드+제품필터 시: 매칭 제품 없으면 채널 행 자체를 숨김
                    if (cfGroupBy==='channel' && pf && filteredProducts.length === 0) return
                    const isExpanded = cfExpanded.has(r.channel)
                    const km = kindMeta[r.kind] || kindMeta.unknown
                    const dColor = r.diff_out===0 ? '#10b981' : r.diff_out>0 ? '#2563eb' : '#ef4444'
                    const emph = r.kind==='bh_missing' || (r.kind==='diff' && Math.abs(r.diff_out) >= 100)
                    out.push(
                      <tr key={`c-${i}`} style={{borderBottom:'1px solid #f3f4f6', background: km.bg || undefined, cursor: hasProducts?'pointer':undefined}}
                        onClick={()=>{
                          if (!hasProducts) return
                          setCfExpanded(prev=>{const n=new Set(prev); n.has(r.channel)?n.delete(r.channel):n.add(r.channel); return n})
                        }}>
                        <td style={{padding:'5px 8px'}}><Tag color={km.color}>{km.tag} {km.label}</Tag></td>
                        <td style={{padding:'5px 8px', fontWeight: emph?700:400}}>
                          {hasProducts && <span style={{color:'#9ca3af', marginRight:4, fontSize:'0.7rem'}}>{isExpanded?'▼':'▶'}</span>}
                          {r.product || r.channel}
                          {hasProducts && <span style={{color:'#9ca3af', fontSize:'0.7rem', marginLeft:6}}>({filteredProducts.length}개 제품)</span>}
                        </td>
                        <td style={{padding:'5px 8px', textAlign:'right', color:'#1e3a8a'}}>{r.bh_out.toLocaleString()}</td>
                        <td style={{padding:'5px 8px', textAlign:'right', color:'#92400e'}}>{r.ob_out.toLocaleString()}</td>
                        <td style={{padding:'5px 8px', textAlign:'right', fontWeight:700, color:dColor}}>{r.diff_out>0?'+':''}{r.diff_out.toLocaleString()}</td>
                        <td style={{padding:'5px 8px', textAlign:'right', color:'#9ca3af'}}>{r.bh_in.toLocaleString()}</td>
                        <td style={{padding:'5px 8px', textAlign:'right', color:'#9ca3af'}}>{r.ob_in.toLocaleString()}</td>
                        <td style={{padding:'5px 8px', textAlign:'right', color:'#9ca3af'}}>{r.bh_adj.toLocaleString()}</td>
                        <td style={{padding:'5px 8px', textAlign:'right', color:'#9ca3af'}}>{r.ob_adj.toLocaleString()}</td>
                      </tr>
                    )
                    if (isExpanded && filteredProducts.length > 0) {
                      filteredProducts.forEach((p,j) => out.push(renderRow(p, j, true)))
                    }
                  })
                  return out
                })()}
              </tbody>
            </table>
            </div>
            <div style={{fontSize:'0.72rem', color:'#9ca3af', marginTop:8, lineHeight:1.6}}>
              {cfGroupBy === 'product' ? (
                <>※ <b>🔴 BH에만</b>: OB엔 있는데 BH 출고 0 — 누락 가능성. <b>🔵 OB에만</b>: BH에만 출고 있음 — OB 미등록. <b>🟡 차이</b>: 양쪽 다 있으나 수량 다름. 매핑 설정에 따라 BH·OB 상품명이 통합됩니다.</>
              ) : (
                <>※ 거래처 행을 <b>클릭</b>하면 해당 거래처의 <b>제품별 내역</b>이 펼쳐집니다. 제품명 검색으로 특정 제품만 볼 수도 있어요.{' '}
                <b>🔴 BH 누락</b>: OB엔 있는데 BH가 0. <b>🔵 직배송</b>: BH에만 있음(호법 미경유). <b>🟡 차이</b>: 양쪽 수량 어긋남.</>
              )}
            </div>
          </>
        )}
      </Modal>

      {/* 출고 수량 대사 모달 */}
      <Modal open={qtyGapOpen} onCancel={()=>setQtyGapOpen(false)} width={960} footer={null}
        title={
          <div style={{display:'flex',alignItems:'center',gap:12}}>
            <span>📊 출고 수량 대사 — 전기간 BH vs OB 총량 비교</span>
            {qtyGapData && <Button size="small" icon={<ExportOutlined />} onClick={exportQtyGapCsv}>CSV</Button>}
          </div>
        }>
        {qtyGapLoading ? (
          <div style={{textAlign:'center',padding:60}}><Spin /><div style={{marginTop:10,color:'#6b7280',fontSize:'0.82rem'}}>출고 데이터 수집 중 (1~2분 소요)...</div></div>
        ) : !qtyGapData ? (
          <Empty description="데이터 없음" />
        ) : (
          <>
            {/* 요약 카드 */}
            <Row gutter={12} style={{marginBottom:14}}>
              {[
                {label:'OB 출고 총량', val:qtyGapData.total_ob.toLocaleString(), color:'#991b1b', bg:'#fee2e2'},
                {label:'BH 출고 총량', val:qtyGapData.total_bh.toLocaleString(), color:'#1e3a8a', bg:'#dbeafe'},
                {label:'BH 미입력', val:`${qtyGapData.bh_missing_qty.toLocaleString()}개`, color:'#991b1b', bg:'#fff1f2'},
                {label:'수량 일치율', val:`${qtyGapData.bh_match_rate}%`, color: qtyGapData.bh_match_rate>=95?'#065f46':'#92400e', bg: qtyGapData.bh_match_rate>=95?'#d1fae5':'#fef3c7'},
              ].map(m=>(
                <Col span={6} key={m.label}>
                  <div style={{background:m.bg,borderRadius:8,padding:'8px 12px',textAlign:'center'}}>
                    <div style={{fontSize:'1.3rem',fontWeight:800,color:m.color}}>{m.val}</div>
                    <div style={{fontSize:'0.72rem',color:m.color}}>{m.label}</div>
                  </div>
                </Col>
              ))}
            </Row>
            <Alert type="info" showIcon style={{marginBottom:10,fontSize:'0.78rem'}}
              message={`제외 채널: ${qtyGapData.excluded_channels.join(', ')} — BH 미입력 목록을 CSV로 다운로드해 BoxHero에 입력하면 수량 일치율이 올라갑니다.`} />
            {/* BH 미입력 목록 */}
            {qtyGapData.bh_missing.length > 0 && (
              <>
                <div style={{fontWeight:700,fontSize:'0.85rem',marginBottom:6,color:'#991b1b'}}>
                  ⚠ BH 미입력 ({qtyGapData.bh_missing_count}개 상품, 총 {qtyGapData.bh_missing_qty.toLocaleString()}개)
                </div>
                <Table size="small" dataSource={qtyGapData.bh_missing} rowKey="name"
                  pagination={{pageSize:15,showSizeChanger:true,showTotal:t=>`총 ${t}건`}}
                  columns={[
                    {title:'상품명', dataIndex:'name', ellipsis:true},
                    {title:'BH출고', dataIndex:'bh_qty', width:80, align:'right' as const, render:(v:number)=>v.toLocaleString()},
                    {title:'OB출고', dataIndex:'ob_qty', width:80, align:'right' as const, render:(v:number)=><span style={{color:'#991b1b',fontWeight:600}}>{v.toLocaleString()}</span>},
                    {title:'차이(미입력)', dataIndex:'diff', width:90, align:'right' as const,
                      render:(v:number)=><span style={{color:'#ef4444',fontWeight:700}}>+{v.toLocaleString()}</span>,
                      sorter:(a:unknown,b:unknown)=>(b as {diff:number}).diff-(a as {diff:number}).diff,
                      defaultSortOrder:'ascend' as const},
                    {title:'주요 채널', dataIndex:'top_channel', width:200, ellipsis:true,
                      render:(_v:unknown,r:unknown)=>{const row=r as {channels:Record<string,number>;top_channel:string};return(
                        <Tooltip title={Object.entries(row.channels).map(([c,q])=>`${c}: ${q}개`).join(' / ')}>
                          <span style={{cursor:'help'}}>{row.top_channel} <span style={{color:'#9ca3af',fontSize:'0.72rem'}}>({Object.keys(row.channels).length}채널)</span></span>
                        </Tooltip>
                      );}},
                  ]}
                  rowClassName={()=>'row-error'}
                />
              </>
            )}
            {/* 일치 목록 */}
            {qtyGapData.ok.length > 0 && (
              <div style={{marginTop:12}}>
                <div style={{fontWeight:700,fontSize:'0.85rem',marginBottom:6,color:'#065f46'}}>
                  ✓ 수량 일치 ({qtyGapData.ok_count}개 상품)
                </div>
                <div style={{display:'flex',flexWrap:'wrap',gap:6}}>
                  {qtyGapData.ok.map(r=>(
                    <div key={r.name} style={{padding:'3px 10px',background:'#d1fae5',borderRadius:6,fontSize:'0.78rem',color:'#065f46'}}>
                      {r.name} ({r.bh_qty.toLocaleString()})
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </Modal>

      {/* 드릴다운 Drawer */}
      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={620}
        title={
          detailRow
            ? <span style={{ fontSize: '0.92rem' }}>
                <SwapOutlined /> 개별 거래 매칭 — {detailRow.name || detailRow.sku}
                <Tag style={{ marginLeft: 8 }}>{TX_LABEL[detailRow.tx_type]}</Tag>
                <span style={{ fontSize: '0.78rem', color: '#6b7280', marginLeft: 6 }}>{detailRow.period}{detailRow.channel ? ` · ${detailRow.channel}` : ''}</span>
              </span>
            : '개별 거래 매칭'
        }
      >
        {/* 반자동 수정안 — 이 행을 어떻게 정리하면 되는지 + 복붙용 값 */}
        {detailRow?.correction && detailRow.status !== 'ok' && (() => {
          const c = detailRow.correction!
          const sysCfg: Record<string, { label: string; color: string }> = {
            BH: { label: '박스히어로에 입력', color: '#1e3a8a' },
            OB: { label: '아워박스에 입력', color: '#991b1b' },
            MAPPING: { label: '매핑/세트 등록', color: '#7c3aed' },
            REVIEW: { label: '대조 검토', color: '#b45309' },
            NONE: { label: '조치 불필요', color: '#6b7280' },
          }
          const sc = sysCfg[c.system] || sysCfg.REVIEW
          return (
            <div style={{ fontSize: '0.82rem', marginBottom: 16, background: '#f5f3ff', border: '1px solid #ddd6fe', borderRadius: 8, padding: '10px 12px' }}>
              <div style={{ fontWeight: 700, marginBottom: 6, color: '#5b21b6', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                💊 수정안 <Tag style={{ margin: 0, color: sc.color, borderColor: sc.color }}>{sc.label}</Tag>
                {detailRow.root_cause && ROOT_CAUSE_CFG[detailRow.root_cause] &&
                  <Tag color={ROOT_CAUSE_CFG[detailRow.root_cause].tag} style={{ margin: 0 }}>{ROOT_CAUSE_CFG[detailRow.root_cause].label}</Tag>}
              </div>
              <div style={{ color: '#374151', marginBottom: 8 }}>{c.action}</div>
              {(c.system === 'BH' || c.system === 'OB' || c.system === 'REVIEW') && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <code style={{ flex: 1, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 6, padding: '4px 8px', fontSize: '0.74rem', overflowX: 'auto', whiteSpace: 'nowrap' }}>
                    {c.copy_text.replace(/\t/g, ' | ')}
                  </code>
                  <Button size="small" icon={<CopyOutlined />}
                    onClick={() => { navigator.clipboard.writeText(c.copy_text); message.success('복사됨 (탭 구분 — 스프레드시트 붙여넣기 가능)') }}>
                    복사
                  </Button>
                </div>
              )}
              <div style={{ marginTop: 8, display: 'flex', gap: 6 }}>
                <Button size="small" type="primary" style={{ background: '#059669', borderColor: '#059669' }}
                  onClick={() => { applyCleanup(detailRow, 'resolved'); }}>✓ 정리완료 표시</Button>
                <Button size="small" onClick={() => cleanupActions.editMemo(detailRow)}>📝 메모</Button>
              </div>
            </div>
          )
        })()}

        {/* 확정 매칭 근거 — 전체수량 매칭에서 확정된 쌍 (검증용) */}
        {rowPairs.length > 0 && (
          <div style={{ fontSize: '0.8rem', marginBottom: 16, background: '#ecfeff', border: '1px solid #a5f3fc', borderRadius: 8, padding: '8px 12px' }}>
            <div style={{ fontWeight: 700, marginBottom: 6, color: '#155e75' }}>
              🔗 확정 매칭 근거 ({rowPairs.length}쌍) — 전체수량 매칭에서 확정됨
            </div>
            <div style={{ maxHeight: 220, overflowY: 'auto' }}>
              {rowPairs.map((p, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '3px 0', borderBottom: '1px solid #cffafe' }}>
                  <span style={{ flex: 1 }}>BH <strong>{p.bh_qty.toLocaleString()}</strong> · {p.bh_date} <span style={{ color: '#6b7280', fontSize: '0.72rem' }}>{p.bh_name?.slice(0, 22)}</span></span>
                  <SwapOutlined style={{ color: p.bh_date !== p.ob_date ? '#f59e0b' : '#10b981' }} />
                  <span style={{ flex: 1 }}>OB <strong>{p.ob_qty.toLocaleString()}</strong> · {p.ob_date} <span style={{ color: '#6b7280', fontSize: '0.72rem' }}>{p.ob_name?.slice(0, 22)}</span>
                    {p.qty_diff !== 0 && <Tag color="warning" style={{ marginLeft: 4, fontSize: '0.68rem' }}>차이 {p.qty_diff > 0 ? '+' : ''}{p.qty_diff}</Tag>}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
        {detailLoading ? (
          <div style={{ textAlign: 'center', padding: 60 }}><Spin /><div style={{ marginTop: 10, color: '#6b7280', fontSize: '0.82rem' }}>개별 거래를 찾는 중...</div></div>
        ) : !detail ? (
          <Empty description="드릴다운 데이터가 없습니다. '비교 조회'를 다시 실행해 주세요." />
        ) : (
          <div style={{ fontSize: '0.82rem' }}>
            <div style={{ display: 'flex', gap: 12, marginBottom: 14 }}>
              <div style={{ flex: 1, background: '#eff6ff', borderRadius: 8, padding: '8px 12px' }}>
                박스히어로 <strong>{detail.bh_count}</strong>건 / 합 <strong>{detail.bh_total}</strong>
              </div>
              <div style={{ flex: 1, background: '#fef2f2', borderRadius: 8, padding: '8px 12px' }}>
                아워박스 <strong>{detail.ob_count}</strong>건 / 합 <strong>{detail.ob_total}</strong>
              </div>
            </div>

            {/* 매칭 추정 쌍 */}
            <div style={{ fontWeight: 700, margin: '10px 0 6px' }}>✓ 매칭 추정 ({detail.pairs.length}쌍)</div>
            {detail.pairs.length === 0 ? (
              <div style={{ color: '#9ca3af', padding: 8 }}>수량이 일치하는 쌍이 없습니다.</div>
            ) : detail.pairs.map((p, i) => {
              const gap = p.bh.date.slice(0, 10) !== p.ob.date.slice(0, 10)
              return (
                <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '6px 0', borderBottom: '1px solid #f3f4f6' }}>
                  <div style={{ flex: 1 }}>
                    <div>BH <strong>{p.bh.qty}</strong> · {p.bh.date}</div>
                    <div style={{ color: '#6b7280', fontSize: '0.74rem' }}>{p.bh.ref}</div>
                  </div>
                  <SwapOutlined style={{ color: gap ? '#f59e0b' : '#10b981' }} />
                  <div style={{ flex: 1 }}>
                    <div>OB <strong>{p.ob.qty}</strong> · {p.ob.date} {gap && <Tag color="warning" style={{ marginLeft: 4 }}>일자차이</Tag>}</div>
                    <div style={{ color: '#6b7280', fontSize: '0.74rem' }}>송장 {p.ob.ref} {p.ob.extra ? `· ${p.ob.extra}` : ''}</div>
                  </div>
                </div>
              )
            })}

            {/* 미매칭 */}
            {detail.bh_only.length > 0 && (
              <>
                <div style={{ fontWeight: 700, margin: '14px 0 6px', color: '#1e3a8a' }}>박스히어로에만 ({detail.bh_only.length})</div>
                {detail.bh_only.map((b, i) => (
                  <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid #f3f4f6' }}>
                    <strong>{b.qty}</strong> · {b.date} <span style={{ color: '#6b7280', fontSize: '0.74rem' }}>{b.ref}</span>
                  </div>
                ))}
              </>
            )}
            {detail.ob_only.length > 0 && (
              <>
                <div style={{ fontWeight: 700, margin: '14px 0 6px', color: '#991b1b' }}>아워박스에만 ({detail.ob_only.length})</div>
                {detail.ob_only.map((o, i) => (
                  <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid #f3f4f6' }}>
                    <strong>{o.qty}</strong> · {o.date} <span style={{ color: '#6b7280', fontSize: '0.74rem' }}>송장 {o.ref} {o.extra ? `· ${o.extra}` : ''}</span>
                  </div>
                ))}
              </>
            )}
            {detail.bh_only.length === 0 && detail.ob_only.length === 0 && detail.pairs.length > 0 && (
              <div style={{ marginTop: 12, color: '#10b981', fontWeight: 600 }}>✅ 모든 거래가 매칭되었습니다.</div>
            )}
          </div>
        )}
      </Drawer>

      <style>{`
        .row-warn td { background: #fffbeb !important; }
        .row-info td { background: #eff6ff !important; }
        .row-error td { background: #fef2f2 !important; }
        .row-cross-type td { background: #fff7ed !important; }
        .row-cross-type.row-warn td { background: #fed7aa !important; }
        .row-set-work td { background: #ecfdf5 !important; }
        .row-set-work.row-warn td { background: #d1fae5 !important; }
        .row-set-dismantle td { background: #fff7ed !important; }
        .row-set-dismantle.row-warn td { background: #fed7aa !important; }
        @keyframes blink { 0%, 100% { opacity: 1 } 50% { opacity: 0 } }
      `}</style>
    </div>
  )
}
