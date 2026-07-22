import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Button, Upload, Select, Table, Tag, message, Spin, Alert, Slider, Switch, Tooltip, Empty,
  Modal, Input, List, Popconfirm,
} from 'antd'
import {
  UploadOutlined, DeleteOutlined, ThunderboltOutlined, DownloadOutlined,
  CalendarOutlined, ReloadOutlined, EditOutlined, LinkOutlined, ApiOutlined,
} from '@ant-design/icons'
import type {
  OrderPlanColumns, OrderPlanResult, OrderPlanItem, OrderPlanMapping,
  OrderPlanUserMapping, PlanFile, PlanDashboard, ReceivingCompare, ReceivingCompareItem, ReceivingCompareRaw,
} from '../services/api'
import {
  detectOrderPlanColumns, downloadBlob,
  listOrderPlanUserMappings, upsertOrderPlanUserMapping, deleteOrderPlanUserMapping,
  ingestOrderPlan, getOrderPlan, listPlanFiles, removePlanFile, clearOrderPlan, clearOrderPlanDate,
  getReceivingCompare,
} from '../services/api'

interface FileRow {
  file: File
  filename: string
  channel: string
  format: 'coupang' | 'kurly' | 'generic'
  date_col: string
  name_col: string
  qty_col: string
  recv_col: string          // 확정 입고 수량 컬럼 (선택)
  columns: string[]
  rows: number
  loading: boolean
  error?: string
  detected_date?: string
  recv_detected?: boolean   // 쿠팡 양식 확정수량 자동 감지
}

const CHANNEL_OPTIONS = ['컬리', '올리브영', '쿠팡', '네이버', '11번가', '지마켓', '카카오', '자사몰', '기타']

// 집계 결과·수동 조정·옵션을 localStorage에 저장 → 메뉴 전환/새로고침 후에도 유지
// (업로드한 원본 File 객체는 직렬화 불가라 제외 — 캘린더 매트릭스 자체는 복원됨)
const STORAGE_KEY = 'orderplan_state_v1'

interface PersistedState {
  result: OrderPlanResult | null
  removedKeys: string[]
  mergeMap: Record<string, string>
  rawMoves: Record<string, string>
  threshold: number
  useMaster: boolean
  splitBundles: boolean
}

function loadPersisted(): Partial<PersistedState> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

export default function OrderPlannerPage() {
  const [persisted] = useState(loadPersisted)
  const [fileRows, setFileRows] = useState<FileRow[]>([])
  // 업로드 큐 파일명 — state는 같은 배치 안에서 갱신이 안 보여 ref로 동기 중복방지
  const queuedNamesRef = useRef<Set<string>>(new Set())
  const [threshold, setThreshold] = useState(persisted.threshold ?? 70)
  const [useMaster, setUseMaster] = useState(persisted.useMaster ?? true)
  const [splitBundles, setSplitBundles] = useState(persisted.splitBundles ?? true)
  const [aggregating, setAggregating] = useState(false)
  const [result, setResult] = useState<OrderPlanResult | null>(persisted.result ?? null)
  const [channelFilter, setChannelFilter] = useState<string[]>([])
  const [showOnlyUnmatched, setShowOnlyUnmatched] = useState(false)
  const [userMappings, setUserMappings] = useState<OrderPlanUserMapping[]>([])
  const [mapModal, setMapModal] = useState<{ open: boolean; item: OrderPlanItem | null }>({ open: false, item: null })
  const [mapMgrOpen, setMapMgrOpen] = useState(false)
  // 누적 발주 저장소 — 서버에 파일별 누적, 진입 시 로드
  const [planFiles, setPlanFiles] = useState<PlanFile[]>([])
  const [dashCollapsed, setDashCollapsed] = useState(false)
  // 발주 대비 확정 입고 비교
  const [recvCompare, setRecvCompare] = useState<ReceivingCompare | null>(null)
  const [recvLoading, setRecvLoading] = useState(false)
  const [recvCollapsed, setRecvCollapsed] = useState(false)
  const [recvChannel, setRecvChannel] = useState('')   // '' = 전체 거래처
  // 세션 수동 조정 — 행 삭제 / 드래그 합치기 / raw_name 개별 이동
  const [removedKeys, setRemovedKeys] = useState<Set<string>>(new Set(persisted.removedKeys ?? []))
  const [mergeMap, setMergeMap] = useState<Record<string, string>>(persisted.mergeMap ?? {})
  // rawMoves: raw_name 문자열 → 목적지 item key
  const [rawMoves, setRawMoves] = useState<Record<string, string>>(persisted.rawMoves ?? {})

  // 상태가 바뀔 때마다 localStorage에 저장
  useEffect(() => {
    try {
      const payload: PersistedState = {
        result,
        removedKeys: Array.from(removedKeys),
        mergeMap,
        rawMoves,
        threshold,
        useMaster,
        splitBundles,
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
    } catch {
      /* localStorage 용량 초과/비활성 — 저장 생략 */
    }
  }, [result, removedKeys, mergeMap, rawMoves, threshold, useMaster, splitBundles])

  // 키 헬퍼 — sku가 있으면 sku, 없으면 name으로 안정 식별
  // 분리(extract)로 생긴 가상 행은 저장 키(__extracted__:...)를 _vkey로 들고 다니며 그걸 우선 사용
  // — 안 그러면 행 키가 __name__:품명으로 계산되어 합치기/삭제가 엉뚱한(빈) 행에 적용됨
  const itemKey = (it: { sku: string; name: string; _vkey?: string }) => it._vkey || it.sku || `__name__:${it.name}`

  function resetAdjustments() {
    setRemovedKeys(new Set())
    setMergeMap({})
    setRawMoves({})
    message.success('수동 조정 초기화됨')
  }

  function moveRaw(rawName: string, targetKey: string) {
    setRawMoves(prev => ({ ...prev, [rawName]: targetKey }))
  }

  function extractRaw(rawName: string) {
    const virtualKey = `__extracted__:${rawName}`
    setRawMoves(prev => ({ ...prev, [rawName]: virtualKey }))
  }

  function undoRawMove(rawName: string) {
    setRawMoves(prev => { const next = { ...prev }; delete next[rawName]; return next })
  }

  function removeItem(key: string) {
    setRemovedKeys(prev => { const next = new Set(prev); next.add(key); return next })
  }

  function undoRemove(key: string) {
    setRemovedKeys(prev => { const next = new Set(prev); next.delete(key); return next })
  }

  function mergeInto(sourceKey: string, targetKey: string) {
    if (sourceKey === targetKey) return
    // 이미 다른 곳으로 합쳐진 키면 그쪽으로 체이닝
    let realTarget = targetKey
    while (mergeMap[realTarget]) realTarget = mergeMap[realTarget]
    if (sourceKey === realTarget) return
    setMergeMap(prev => ({ ...prev, [sourceKey]: realTarget }))
  }

  function undoMerge(sourceKey: string) {
    setMergeMap(prev => { const next = { ...prev }; delete next[sourceKey]; return next })
  }

  async function refreshUserMappings() {
    try {
      const data = await listOrderPlanUserMappings()
      setUserMappings(data.items)
    } catch (e: any) {
      console.warn('user mappings load failed', e)
    }
  }

  useEffect(() => { refreshUserMappings() }, [])

  // 진입 시 서버에 누적된 발주 전체를 로드 (localStorage 캐시보다 우선 — 다른 기기/세션 누적 반영)
  async function refreshPlanFiles() {
    try { const d = await listPlanFiles(); setPlanFiles(d.files) }
    catch { /* noop */ }
  }
  // 발주 대비 입고 비교 로드 — 발주 데이터가 바뀔 때마다 갱신
  // ch를 넘기면 해당 거래처로 필터 (state 반영 전에도 즉시 적용되도록 인자 우선)
  async function refreshRecvCompare(ch?: string) {
    setRecvLoading(true)
    try {
      const channel = ch !== undefined ? ch : recvChannel
      const data = await getReceivingCompare({ threshold, useMaster, splitBundles, channel })
      setRecvCompare(data.summary.item_count > 0 || data.daily.length > 0 || data.by_channel.length > 0 ? data : null)
    } catch { setRecvCompare(null) }
    finally { setRecvLoading(false) }
  }

  useEffect(() => {
    (async () => {
      try {
        const data = await getOrderPlan({ threshold, useMaster, splitBundles })
        if (data && data.item_count > 0) setResult(data)
        await refreshPlanFiles()
        await refreshRecvCompare()
      } catch { /* 서버 미응답 시 localStorage 캐시 유지 */ }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleClearPlan() {
    try {
      await clearOrderPlan()
      setResult(null); setPlanFiles([]); setRecvCompare(null); resetAdjustments()
      message.success('누적 발주 전체 초기화됨')
    } catch (e: any) {
      message.error('초기화 실패: ' + (e.response?.data?.detail || e.message))
    }
  }

  async function handleClearDate(date: string) {
    try {
      const data = await clearOrderPlanDate(date, { threshold, useMaster, splitBundles })
      setResult(data.item_count > 0 ? data : null)
      await refreshPlanFiles()
      await refreshRecvCompare()
      message.success(`${date} 발주 초기화됨 (${(data.removed_qty ?? 0).toLocaleString()}개 제거)`)
    } catch (e: any) {
      message.error('일별 초기화 실패: ' + (e.response?.data?.detail || e.message))
    }
  }

  async function handleRemovePlanFile(filename: string) {
    try {
      const data = await removePlanFile(filename, { threshold, useMaster, splitBundles })
      setResult(data.item_count > 0 ? data : null)
      await refreshPlanFiles()
      await refreshRecvCompare()
      message.success(`${filename} 제거됨`)
    } catch (e: any) {
      message.error('제거 실패: ' + (e.response?.data?.detail || e.message))
    }
  }


  async function handleFileSelect(file: File) {
    if (queuedNamesRef.current.has(file.name)) {
      message.warning(`이미 추가된 파일: ${file.name}`)
      return false
    }
    queuedNamesRef.current.add(file.name)
    const tempRow: FileRow = {
      file, filename: file.name, channel: '', format: 'generic', date_col: '',
      name_col: '', qty_col: '', recv_col: '', columns: [], rows: 0, loading: true,
    }
    setFileRows(prev => [...prev, tempRow])
    try {
      const data: OrderPlanColumns = await detectOrderPlanColumns(file)
      setFileRows(prev => prev.map(r =>
        r.filename === file.name
          ? { ...r, format: data.format, channel: data.channel,
              date_col: data.date_col, name_col: data.name_col, qty_col: data.qty_col,
              recv_col: data.format === 'generic' ? (data.recv_col || '') : '',
              recv_detected: data.recv_detected,
              columns: data.columns, rows: data.rows, loading: false,
              detected_date: data.detected_date }
          : r
      ))
    } catch (e: any) {
      setFileRows(prev => prev.map(r =>
        r.filename === file.name
          ? { ...r, loading: false, error: e.response?.data?.detail || e.message }
          : r
      ))
      message.error(`${file.name} 분석 실패: ${e.response?.data?.detail || e.message}`)
    }
    return false
  }

  function updateRow(filename: string, patch: Partial<FileRow>) {
    setFileRows(prev => prev.map(r => r.filename === filename ? { ...r, ...patch } : r))
  }

  function removeRow(filename: string) {
    queuedNamesRef.current.delete(filename)
    setFileRows(prev => prev.filter(r => r.filename !== filename))
  }

  function clearAll() {
    queuedNamesRef.current.clear()
    setFileRows([])
    setResult(null)
  }

  async function handleAggregate() {
    if (fileRows.length === 0) {
      message.warning('파일을 먼저 업로드하세요')
      return
    }
    const incomplete = fileRows.filter(r => {
      if (r.loading || !r.channel) return true
      if (r.format === 'coupang' || r.format === 'kurly') return !r.detected_date && !r.date_col
      return !r.date_col || !r.name_col || !r.qty_col
    })
    if (incomplete.length > 0) {
      message.warning(`${incomplete.length}개 파일의 매핑이 비어있어요`)
      return
    }
    setAggregating(true)
    try {
      const files = fileRows.map(r => r.file)
      const mappings: OrderPlanMapping[] = fileRows.map(r => ({
        filename: r.filename, channel: r.channel, format: r.format,
        date_col: r.format === 'coupang' ? (r.detected_date || r.date_col || '') : r.date_col,
        name_col: r.name_col, qty_col: r.qty_col, recv_col: r.recv_col || '',
      }))
      // 누적 저장: 기존 데이터에 더해짐 (같은 파일명은 갱신). 새 파일이 기존 걸 지우지 않음.
      const data = await ingestOrderPlan(files, mappings, { threshold, useMaster, splitBundles })
      setResult(data)
      await refreshPlanFiles()
      await refreshRecvCompare()
      setFileRows([])  // 업로드 큐 비움 — 파일은 서버에 누적 저장됨
      queuedNamesRef.current.clear()
      if (data.errors?.length) message.warning(`${data.errors.length}건 경고`)
      if (data.duplicates?.length) {
        message.info({
          content: (
            <span>
              중복 발주서 <b>{data.duplicates.length}건</b> 건너뜀 (이미 누적된 내용):
              <br />
              {data.duplicates.map(d => `· ${d.filename} — ${d.reason} (기존: ${d.existing})`).join('\n')
                .split('\n').map((l, i) => <span key={i}>{l}<br /></span>)}
            </span>
          ),
          duration: 6,
        })
      }
      if (data.replaced?.length) {
        message.info(
          `같은 발주번호 갱신 ${data.replaced.length}건 — 기존 파일을 새 파일로 대체 (이중 집계 방지): ` +
          data.replaced.map(r => `${r.replaced} → ${r.filename}`).join(', '),
          6,
        )
      }
      const addedMsg = data.added && data.added.length ? `${data.added.length}개 파일 누적` : '신규 없음'
      message.success(`${addedMsg} · 총 품목 ${data.item_count}개 · 날짜 ${data.dates.length}일`)
    } catch (e: any) {
      message.error('누적 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setAggregating(false)
    }
  }

  // 매핑 저장 후 자동 재집계 — 서버 누적 발주를 현재 매핑으로 다시 집계 (업로드 안 했어도 동작)
  async function reAggregateAfterMappingChange() {
    if (!result && planFiles.length === 0) return
    try {
      const data = await getOrderPlan({ threshold, useMaster, splitBundles })
      setResult(data.item_count > 0 ? data : null)
    } catch { /* noop */ }
  }

  async function saveUserMapping(rawName: string, sku: string, masterName: string) {
    try {
      await upsertOrderPlanUserMapping({ raw_name: rawName, sku, master_name: masterName })
      message.success(sku ? `매핑 저장: ${masterName || sku}` : '강제 미매칭 저장')
      await refreshUserMappings()
      await reAggregateAfterMappingChange()
    } catch (e: any) {
      message.error('저장 실패: ' + (e.response?.data?.detail || e.message))
    }
  }

  async function removeUserMapping(rawName: string) {
    try {
      await deleteOrderPlanUserMapping(rawName)
      message.success('매핑 삭제됨')
      await refreshUserMappings()
      await reAggregateAfterMappingChange()
    } catch (e: any) {
      message.error('삭제 실패: ' + (e.response?.data?.detail || e.message))
    }
  }

  function handleDownloadCsv() {
    if (!result) return
    const cols = ['SKU', '품목명', '매칭', ...result.dates, '합계']
    const lines = [cols.join(',')]
    for (const it of filteredItems) {
      const row = [
        it.sku || '',
        `"${it.name.replace(/"/g, '""')}"`,
        it.matched ? 'O' : 'X',
        ...result.dates.map(d => String(it.by_date[d] || 0)),
        String(it.total),
      ]
      lines.push(row.join(','))
    }
    const totalRow = [
      '', '합계', '',
      ...result.dates.map(d => String(displayTotals.byDate[d] || 0)),
      String(displayTotals.grand),
    ]
    lines.push(totalRow.join(','))
    const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })
    downloadBlob(blob, `발주캘린더_${new Date().toISOString().slice(0, 10)}.csv`)
  }

  // 수동 조정(raw 이동 → 행 합치기 → 삭제) 적용된 아이템 — 필터 전 단계
  const adjustedItems = useMemo<OrderPlanItem[]>(() => {
    if (!result) return []
    // 모든 아이템을 깊은 복제하여 키로 인덱싱
    const byKey = new Map<string, OrderPlanItem>()
    for (const it of result.items) {
      const rb: Record<string, any> = {}
      for (const [k, v] of Object.entries(it.raw_breakdown || {})) {
        rb[k] = { ...v, by_date: { ...v.by_date }, by_channel: { ...v.by_channel } }
      }
      byKey.set(itemKey(it), {
        ...it,
        by_date: { ...it.by_date },
        by_channel: { ...it.by_channel },
        raw_names: [...it.raw_names],
        sources: [...it.sources],
        raw_breakdown: rb,
      })
    }
    // 1) raw_name 단위 이동 — rawMoves에 있는 raw들을 원 행에서 빼고 목적지에 더함
    for (const [rawName, tgtKey] of Object.entries(rawMoves)) {
      let srcEntry: OrderPlanItem | null = null
      for (const item of byKey.values()) {
        if (item.raw_breakdown && rawName in item.raw_breakdown) {
          srcEntry = item
          break
        }
      }
      if (!srcEntry) continue
      const breakdown = srcEntry.raw_breakdown![rawName]
      if (!breakdown) continue
      // 원 행에서 차감
      for (const [d, q] of Object.entries(breakdown.by_date)) {
        srcEntry.by_date[d] = (srcEntry.by_date[d] || 0) - q
        if (srcEntry.by_date[d] <= 0) delete srcEntry.by_date[d]
      }
      for (const [c, q] of Object.entries(breakdown.by_channel)) {
        srcEntry.by_channel[c] = (srcEntry.by_channel[c] || 0) - q
        if (srcEntry.by_channel[c] <= 0) delete srcEntry.by_channel[c]
      }
      srcEntry.total -= breakdown.total
      srcEntry.raw_names = srcEntry.raw_names.filter(n => n !== rawName)
      delete srcEntry.raw_breakdown![rawName]

      // 목적지 행에 더함 — 없으면 가상 행 생성 (분리 기능)
      if (!byKey.has(tgtKey)) {
        byKey.set(tgtKey, {
          sku: '', name: rawName, matched: false, match_score: 0, match_source: 'extract',
          by_date: {}, by_channel: {}, total: 0,
          raw_names: [], sources: [], had_bundle: false,
          raw_breakdown: {},
          _vkey: tgtKey,
        } as OrderPlanItem)
      }
      const tgt = byKey.get(tgtKey)!
      for (const [d, q] of Object.entries(breakdown.by_date)) {
        tgt.by_date[d] = (tgt.by_date[d] || 0) + q
      }
      for (const [c, q] of Object.entries(breakdown.by_channel)) {
        tgt.by_channel[c] = (tgt.by_channel[c] || 0) + q
      }
      tgt.total += breakdown.total
      if (!tgt.raw_names.includes(rawName)) tgt.raw_names = [...tgt.raw_names, rawName].sort()
      if (!tgt.raw_breakdown) tgt.raw_breakdown = {}
      tgt.raw_breakdown[rawName] = breakdown
    }

    // 2) 행 합치기 — 체이닝 해소 (A→B→C 시 A를 최종 C에 합산)
    function resolveTarget(k: string): string {
      const seen = new Set<string>()
      let cur = k
      while (mergeMap[cur] && !seen.has(cur)) {
        seen.add(cur)
        cur = mergeMap[cur]
      }
      return cur
    }
    for (const srcKey of Object.keys(mergeMap)) {
      const src = byKey.get(srcKey)
      if (!src) continue
      const tgtKey = resolveTarget(srcKey)
      const tgt = byKey.get(tgtKey)
      if (!tgt || tgtKey === srcKey) continue
      for (const [d, q] of Object.entries(src.by_date)) {
        tgt.by_date[d] = (tgt.by_date[d] || 0) + q
      }
      for (const [c, q] of Object.entries(src.by_channel)) {
        tgt.by_channel[c] = (tgt.by_channel[c] || 0) + q
      }
      tgt.total += src.total
      const rn = new Set(tgt.raw_names)
      src.raw_names.forEach(n => rn.add(n))
      tgt.raw_names = Array.from(rn).sort()
      const ss = new Set(tgt.sources)
      src.sources.forEach(s => ss.add(s))
      tgt.sources = Array.from(ss).sort()
      if (!tgt.raw_breakdown) tgt.raw_breakdown = {}
      for (const [k, v] of Object.entries(src.raw_breakdown || {})) {
        tgt.raw_breakdown[k] = v
      }
      byKey.delete(srcKey)
    }
    // 3) 삭제 적용 — itemKey 기준으로 매칭.
    //    분리된(extract) 가상 행은 저장 키(__extracted__:...)와 itemKey(__name__:...)가
    //    달라서 removedKeys로 직접 byKey.delete가 안 먹는다. 두 경우 모두 처리.
    if (removedKeys.size > 0) {
      for (const [storeKey, it] of byKey) {
        if (removedKeys.has(storeKey) || removedKeys.has(itemKey(it))) {
          byKey.delete(storeKey)
        }
      }
    }
    // 4) total<=0 행은 자동 숨김 (raw가 전부 빠진 경우)
    const arr: OrderPlanItem[] = []
    for (const it of byKey.values()) {
      if (it.total > 0 || it.raw_names.length > 0) arr.push(it)
    }
    return arr.sort(
      (a, b) => (a.matched === b.matched ? b.total - a.total : a.matched ? -1 : 1)
    )
  }, [result, rawMoves, mergeMap, removedKeys])

  // 발주 대비 입고 — 매트릭스의 수동 조정(raw 세부이동 → 행 합치기 → 삭제)을
  // 동일 키(sku/품명) 기준으로 순서대로 반영 (adjustedItems와 같은 순서)
  const adjustedRecv = useMemo<ReceivingCompare | null>(() => {
    if (!recvCompare) return null
    if (Object.keys(mergeMap).length === 0 && removedKeys.size === 0
        && Object.keys(rawMoves).length === 0) return recvCompare

    type RecvWork = ReceivingCompareItem & { _hasData: boolean; _rb: Record<string, ReceivingCompareRaw> }
    const byKey = new Map<string, RecvWork>()
    for (const it of recvCompare.items) {
      const rb: Record<string, ReceivingCompareRaw> = {}
      for (const [k, v] of Object.entries(it.raw_breakdown || {})) {
        rb[k] = {
          ordered_by_date: { ...v.ordered_by_date },
          received_by_date: { ...v.received_by_date },
          ordered_amt_by_date: { ...v.ordered_amt_by_date },
          received_amt_by_date: { ...v.received_amt_by_date },
          has_recv: v.has_recv,
        }
      }
      byKey.set(itemKey(it), {
        ...it,
        ordered_by_date: { ...it.ordered_by_date },
        received_by_date: { ...it.received_by_date },
        ordered_amt_by_date: { ...(it.ordered_amt_by_date || {}) },
        received_amt_by_date: { ...(it.received_amt_by_date || {}) },
        _hasData: it.status !== 'nodata',
        _rb: rb,
      })
    }
    const addInto = (dst: Record<string, number>, src: Record<string, number>) => {
      for (const [d, q] of Object.entries(src)) dst[d] = (dst[d] || 0) + q
    }
    const subFrom = (dst: Record<string, number>, src: Record<string, number>) => {
      for (const [d, q] of Object.entries(src)) {
        dst[d] = (dst[d] || 0) - q
        if (dst[d] <= 0) delete dst[d]
      }
    }
    // 1) raw 품명 단위 이동 — 원 행에서 빼고 목적지 행에 더함 (없으면 가상 행 생성)
    for (const [rawName, tgtKey] of Object.entries(rawMoves)) {
      let srcEntry: RecvWork | null = null
      for (const w of byKey.values()) {
        if (w._rb[rawName]) { srcEntry = w; break }
      }
      if (!srcEntry) continue
      const b = srcEntry._rb[rawName]
      subFrom(srcEntry.ordered_by_date, b.ordered_by_date)
      subFrom(srcEntry.received_by_date, b.received_by_date)
      subFrom(srcEntry.ordered_amt_by_date!, b.ordered_amt_by_date)
      subFrom(srcEntry.received_amt_by_date!, b.received_amt_by_date)
      delete srcEntry._rb[rawName]
      srcEntry._hasData = Object.values(srcEntry._rb).some(r => r.has_recv)
      let tgt = byKey.get(tgtKey)
      if (!tgt) {
        // 입고 데이터에 없는 행이 목적지 — 매트릭스 행에서 sku/품명을 찾아 가상 행 생성
        const proto = result?.items.find(i => itemKey(i) === tgtKey)
        tgt = {
          sku: proto?.sku ?? '',
          name: proto?.name ?? (tgtKey.startsWith('__extracted__:') ? rawName : tgtKey.replace(/^__name__:/, '')),
          ordered: 0, received: 0, diff: 0, rate: 0, ordered_amt: 0, received_amt: 0,
          status: 'nodata',
          ordered_by_date: {}, received_by_date: {},
          ordered_amt_by_date: {}, received_amt_by_date: {},
          _hasData: false, _rb: {},
        }
        byKey.set(tgtKey, tgt)
      }
      addInto(tgt.ordered_by_date, b.ordered_by_date)
      addInto(tgt.received_by_date, b.received_by_date)
      addInto(tgt.ordered_amt_by_date!, b.ordered_amt_by_date)
      addInto(tgt.received_amt_by_date!, b.received_amt_by_date)
      tgt._rb[rawName] = b
      tgt._hasData = tgt._hasData || b.has_recv
    }
    // 2) 행 합치기 — 체이닝 해소 (A→B→C 시 A를 최종 C에 합산)
    const resolveTarget = (k: string) => {
      const seen = new Set<string>()
      let cur = k
      while (mergeMap[cur] && !seen.has(cur)) { seen.add(cur); cur = mergeMap[cur] }
      return cur
    }
    for (const srcKey of Object.keys(mergeMap)) {
      const src = byKey.get(srcKey)
      if (!src) continue
      const tgtKey = resolveTarget(srcKey)
      const tgt = byKey.get(tgtKey)
      if (!tgt || tgtKey === srcKey) continue
      addInto(tgt.ordered_by_date, src.ordered_by_date)
      addInto(tgt.received_by_date, src.received_by_date)
      addInto(tgt.ordered_amt_by_date!, src.ordered_amt_by_date!)
      addInto(tgt.received_amt_by_date!, src.received_amt_by_date!)
      for (const [k, v] of Object.entries(src._rb)) tgt._rb[k] = v
      tgt._hasData = tgt._hasData || src._hasData
      byKey.delete(srcKey)
    }
    // 3) 삭제 적용
    if (removedKeys.size > 0) {
      for (const k of Array.from(byKey.keys())) {
        if (removedKeys.has(k)) byKey.delete(k)
      }
    }
    // 4) 이동/병합 후 품목 지표를 일자별 분해에서 다시 계산 (백엔드와 동일 규칙)
    const sumOf = (m: Record<string, number> | undefined) =>
      Object.values(m || {}).reduce((s, q) => s + q, 0)
    const items: ReceivingCompareItem[] = []
    for (const w of byKey.values()) {
      const { _hasData, _rb, ...it } = w
      it.ordered = sumOf(it.ordered_by_date)
      it.received = sumOf(it.received_by_date)
      if (it.ordered <= 0 && it.received <= 0) continue
      it.ordered_amt = sumOf(it.ordered_amt_by_date)
      it.received_amt = sumOf(it.received_amt_by_date)
      it.raw_breakdown = _rb
      it.diff = it.received - it.ordered
      it.rate = it.ordered ? Math.round((it.received / it.ordered) * 1000) / 10 : 0
      it.status = !_hasData ? 'nodata'
        : it.received <= 0 ? 'none'
        : it.received < it.ordered ? 'partial'
        : it.received === it.ordered ? 'full' : 'over'
      items.push(it)
    }
    items.sort((a, b) =>
      (a.status === 'nodata' ? 1 : 0) - (b.status === 'nodata' ? 1 : 0)
      || a.diff - b.diff || b.ordered - a.ordered)

    const withData = items.filter(x => x.status !== 'nodata')
    const orderedWithData = withData.reduce((s, x) => s + x.ordered, 0)
    const receivedTotal = withData.reduce((s, x) => s + x.received, 0)
    const summary = {
      ordered_total: items.reduce((s, x) => s + x.ordered, 0),
      ordered_with_data: orderedWithData,
      received_total: receivedTotal,
      rate: orderedWithData ? Math.round((receivedTotal / orderedWithData) * 1000) / 10 : 0,
      item_count: items.length,
      full_count: withData.filter(x => x.status === 'full' || x.status === 'over').length,
      partial_count: withData.filter(x => x.status === 'partial').length,
      none_count: withData.filter(x => x.status === 'none').length,
      over_count: withData.filter(x => x.status === 'over').length,
      nodata_count: items.length - withData.length,
      ordered_amt_total: items.reduce((s, x) => s + x.ordered_amt, 0),
      received_amt_total: items.reduce((s, x) => s + x.received_amt, 0),
    }
    // 일별도 조정된 품목 기준으로 재집계 (삭제 반영·nodata 병합 시 입고율 분모 보정)
    const dOrdered: Record<string, number> = {}
    const dOrderedWd: Record<string, number> = {}
    const dReceived: Record<string, number> = {}
    const dOrderedAmt: Record<string, number> = {}
    const dReceivedAmt: Record<string, number> = {}
    for (const it of items) {
      const hasData = it.status !== 'nodata'
      for (const [d, q] of Object.entries(it.ordered_by_date)) {
        dOrdered[d] = (dOrdered[d] || 0) + q
        if (hasData) dOrderedWd[d] = (dOrderedWd[d] || 0) + q
      }
      for (const [d, q] of Object.entries(it.received_by_date)) dReceived[d] = (dReceived[d] || 0) + q
      for (const [d, a] of Object.entries(it.ordered_amt_by_date || {})) dOrderedAmt[d] = (dOrderedAmt[d] || 0) + a
      for (const [d, a] of Object.entries(it.received_amt_by_date || {})) dReceivedAmt[d] = (dReceivedAmt[d] || 0) + a
    }
    const days = Array.from(new Set([...Object.keys(dOrdered), ...Object.keys(dReceived)])).sort()
    const daily = days.map(d => ({
      date: d,
      ordered: dOrdered[d] || 0,
      ordered_with_data: dOrderedWd[d] || 0,
      received: dReceived[d] || 0,
      ordered_amt: dOrderedAmt[d] || 0,
      received_amt: dReceivedAmt[d] || 0,
    }))
    return { ...recvCompare, summary, items, daily }
  }, [recvCompare, mergeMap, removedKeys, rawMoves, result])

  const filteredItems = useMemo<OrderPlanItem[]>(() => {
    let arr = adjustedItems
    if (showOnlyUnmatched) arr = arr.filter(i => !i.matched)
    if (channelFilter.length > 0) {
      arr = arr.filter(i => channelFilter.some(ch => (i.by_channel[ch] || 0) > 0))
    }
    return arr
  }, [adjustedItems, showOnlyUnmatched, channelFilter])

  // 화면용 합계 — adjustedItems 기준으로 재계산
  const displayTotals = useMemo(() => {
    const byDate: Record<string, number> = {}
    const byChannel: Record<string, number> = {}
    let grand = 0
    for (const it of adjustedItems) {
      for (const [d, q] of Object.entries(it.by_date)) byDate[d] = (byDate[d] || 0) + q
      for (const [c, q] of Object.entries(it.by_channel)) byChannel[c] = (byChannel[c] || 0) + q
      grand += it.total
    }
    return { byDate, byChannel, grand }
  }, [adjustedItems])

  const adjustmentCount = removedKeys.size + Object.keys(mergeMap).length + Object.keys(rawMoves).length

  // 대시보드 — 화면에 정리한 결과(adjustedItems)에서 직접 계산 → 삭제/합치기/세부이동 즉시 반영
  const dashboard = useMemo<PlanDashboard | null>(() => {
    if (adjustedItems.length === 0) return null
    const daily: Record<string, number> = {}
    const byCh: Record<string, number> = {}
    for (const it of adjustedItems) {
      for (const [d, q] of Object.entries(it.by_date)) daily[d] = (daily[d] || 0) + q
      for (const [c, q] of Object.entries(it.by_channel)) byCh[c] = (byCh[c] || 0) + q
    }
    const monthly: Record<string, number> = {}
    for (const [d, q] of Object.entries(daily)) { const mo = d.slice(0, 7); monthly[mo] = (monthly[mo] || 0) + q }
    const dates = Object.keys(daily).sort()
    const grand = adjustedItems.reduce((s, it) => s + it.total, 0)
    return {
      daily: dates.map(d => ({ date: d, qty: daily[d] })),
      monthly: Object.keys(monthly).sort().map(mo => ({ month: mo, qty: monthly[mo] })),
      by_channel: Object.entries(byCh).sort((a, b) => b[1] - a[1]).map(([channel, qty]) => ({ channel, qty })),
      // 정리(adjustedItems)는 날짜×채널 교차값을 갖지 않아 채널×월 매트릭스는 생략
      channel_month: { channels: [], months: [], matrix: {} },
      top_items: [...adjustedItems].sort((a, b) => b.total - a.total).slice(0, 20)
        .map(it => ({ sku: it.sku, name: it.name, total: it.total, matched: it.matched })),
      grand_total: grand,
      item_count: adjustedItems.length,
      file_count: planFiles.length,
      date_range: { from: dates[0] || '', to: dates[dates.length - 1] || '' },
    }
  }, [adjustedItems, planFiles.length])

  // 매핑 모달용 마스터 옵션 — 이미 매칭된 SKU 모음에서 추출
  const masterOptions = useMemo(() => {
    if (!result) return [] as { value: string; label: string; name: string }[]
    const seen = new Map<string, { value: string; label: string; name: string }>()
    for (const it of result.items) {
      if (it.sku && it.matched && !seen.has(it.sku)) {
        seen.set(it.sku, { value: it.sku, label: `${it.sku} · ${it.name}`, name: it.name })
      }
    }
    return Array.from(seen.values()).sort((a, b) => a.value.localeCompare(b.value))
  }, [result])

  return (
    <div style={{ padding: '24px 28px', height: '100vh', overflowY: 'auto', background: '#f8fafc' }}>
      <div className="page-header">
        <h1 className="page-title">📅 발주 캘린더</h1>
        <p className="page-desc">컬리·올리브영·쿠팡 등 여러 발주서를 한꺼번에 올리면 품목별로 어느 날짜에 몇 개가 필요한지 한눈에 보여줍니다.</p>
      </div>

      {/* 통계 대시보드 — 상단 자동 표시 (누적 데이터 기준) */}
      {dashboard && dashboard.item_count > 0 && (
        <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: dashCollapsed ? 0 : 12 }}>
            <span style={{ fontWeight: 700, color: '#111827' }}>
              📊 발주 통계 대시보드 <span style={{ fontWeight: 400, color: '#6b7280', fontSize: '0.8rem' }}>· 일·월·거래처별 · 정리(삭제·합치기·이동) 반영{adjustmentCount > 0 ? ` ${adjustmentCount}건` : ''} (누적 {dashboard.file_count}개 파일)</span>
            </span>
            <span style={{ display: 'flex', gap: 8 }}>
              <Button size="small" type="text" onClick={() => setDashCollapsed(c => !c)}>{dashCollapsed ? '펼치기 ▾' : '접기 ▴'}</Button>
            </span>
          </div>
          {!dashCollapsed && <DashboardView d={dashboard} items={adjustedItems} onClearDate={handleClearDate} />}
        </div>
      )}

      {/* 발주 대비 확정 입고 현황 — 입고정산기 DB 연동 */}
      {adjustedRecv && (
        <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: recvCollapsed ? 0 : 12 }}>
            <span style={{ fontWeight: 700, color: '#111827' }}>
              📥 발주 대비 확정 입고 <span style={{ fontWeight: 400, color: '#6b7280', fontSize: '0.8rem' }}>
                · 발주서 안의 확정수량 컬럼 기준 · {adjustedRecv.range.from} ~ {adjustedRecv.range.to}
                {adjustmentCount > 0 &&
                  ` · 매트릭스 정리(합치기 ${Object.keys(mergeMap).length}·세부이동 ${Object.keys(rawMoves).length}·삭제 ${removedKeys.size}) 반영`}
              </span>
            </span>
            <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <Select
                size="small" allowClear placeholder="전체 거래처" style={{ minWidth: 130 }}
                value={recvChannel || undefined}
                onChange={(v) => { const ch = v || ''; setRecvChannel(ch); refreshRecvCompare(ch) }}
                options={(adjustedRecv.channels || []).map(ch => ({ value: ch, label: ch }))}
              />
              <Button size="small" icon={<ReloadOutlined />} loading={recvLoading} onClick={() => refreshRecvCompare()}>새로고침</Button>
              <Button size="small" type="text" onClick={() => setRecvCollapsed(c => !c)}>{recvCollapsed ? '펼치기 ▾' : '접기 ▴'}</Button>
            </span>
          </div>
          {!recvCollapsed && (
            <ReceivingCompareView
              c={adjustedRecv}
              onSelectChannel={(ch) => { setRecvChannel(ch); refreshRecvCompare(ch) }}
            />
          )}
        </div>
      )}

      {/* 좌: 업로드 시트 / 우: 옵션 + 집계 */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.7fr 1fr', gap: 16, marginBottom: 16 }}>
        {/* 업로드 시트 */}
        <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <span style={{ fontWeight: 700, color: '#111827' }}>📁 발주서 파일 ({fileRows.length})</span>
            {fileRows.length > 0 && (
              <Button size="small" type="text" danger onClick={clearAll}>모두 비우기</Button>
            )}
          </div>

          <Upload
            accept=".xlsx,.xls,.pdf"
            multiple
            showUploadList={false}
            beforeUpload={(file) => {
              // beforeUpload는 파일마다 1번씩 호출됨 — fileList를 다시 돌면 N² 중복 처리됨
              handleFileSelect(file)
              return false
            }}
          >
            <div style={{
              border: '2px dashed #d1d5db', borderRadius: 10, padding: '18px 16px',
              textAlign: 'center', background: '#fafafa', cursor: 'pointer', marginBottom: 12,
            }}>
              <UploadOutlined style={{ fontSize: '1.4rem', color: '#10b981' }} />
              <div style={{ marginTop: 6, fontSize: '0.85rem', color: '#374151' }}>
                Excel / PDF 파일을 드래그하거나 클릭 (여러 개 가능)
              </div>
              <div style={{ marginTop: 4, fontSize: '0.72rem', color: '#9ca3af' }}>
                쿠팡 발주서리스트 · 올리브영 납품확인서 · 컬리 거래명세서(PDF) 자동 인식
              </div>
            </div>
          </Upload>

          {fileRows.length === 0 ? (
            <Empty description="업로드된 파일 없음" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
                <thead>
                  <tr style={{ background: '#f9fafb', borderBottom: '1px solid #e5e7eb' }}>
                    <th style={th}>파일</th>
                    <th style={th}>채널</th>
                    <th style={th}>출고일 컬럼</th>
                    <th style={th}>품목 컬럼</th>
                    <th style={th}>수량 컬럼</th>
                    <th style={th}>확정수량 컬럼</th>
                    <th style={{ ...th, width: 50, textAlign: 'right' }}>행</th>
                    <th style={{ width: 30 }} />
                  </tr>
                </thead>
                <tbody>
                  {fileRows.map(r => {
                    const isAuto = r.format === 'coupang' || r.format === 'kurly'
                    const formatBadge =
                      r.format === 'coupang' ? <Tag color="purple" style={badgeStyle}>쿠팡 양식</Tag> :
                      r.format === 'kurly' ? <Tag color="magenta" style={badgeStyle}>컬리 PDF</Tag> :
                      null
                    return (
                    <tr key={r.filename} style={{ borderBottom: '1px solid #f3f4f6' }}>
                      <td style={td}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <div style={{ fontWeight: 600, color: '#1f2937', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {r.filename}
                          </div>
                          {formatBadge}
                        </div>
                        {r.error && <div style={{ color: '#dc2626', fontSize: '0.7rem' }}>⚠ {r.error}</div>}
                      </td>
                      <td style={td}>
                        {r.loading ? <Spin size="small" /> : (
                          <Select
                            size="small" value={r.channel || undefined} style={{ width: 110 }}
                            onChange={v => updateRow(r.filename, { channel: v })}
                            options={CHANNEL_OPTIONS.map(c => ({ value: c, label: c }))}
                            showSearch allowClear placeholder="채널"
                          />
                        )}
                      </td>
                      <td style={td} colSpan={isAuto ? 4 : 1}>
                        {r.loading ? <Spin size="small" /> : isAuto ? (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.78rem', color: '#374151' }}>
                            <Tag color="green" style={badgeStyle}>자동 인식</Tag>
                            {r.format === 'kurly' ? '입고일' : '입고예정일'}: <b style={{ color: '#0369a1' }}>{r.detected_date || '없음'}</b>
                            <span style={{ color: '#6b7280' }}>· 품목 {r.rows}개</span>
                            {r.format === 'coupang' && (
                              r.recv_detected
                                ? <Tag color="blue" style={badgeStyle}>확정수량 감지</Tag>
                                : <Tag style={badgeStyle}>확정수량 없음</Tag>
                            )}
                          </div>
                        ) : (
                          <Select
                            size="small" value={r.date_col || undefined} style={{ width: 130 }}
                            onChange={v => updateRow(r.filename, { date_col: v })}
                            options={r.columns.map(c => ({ value: c, label: c }))}
                            showSearch placeholder="선택"
                          />
                        )}
                      </td>
                      {!isAuto && (
                        <td style={td}>
                          {r.loading ? <Spin size="small" /> : (
                            <Select
                              size="small" value={r.name_col || undefined} style={{ width: 130 }}
                              onChange={v => updateRow(r.filename, { name_col: v })}
                              options={r.columns.map(c => ({ value: c, label: c }))}
                              showSearch placeholder="선택"
                            />
                          )}
                        </td>
                      )}
                      {!isAuto && (
                        <td style={td}>
                          {r.loading ? <Spin size="small" /> : (
                            <Select
                              size="small" value={r.qty_col || undefined} style={{ width: 100 }}
                              onChange={v => updateRow(r.filename, { qty_col: v })}
                              options={r.columns.map(c => ({ value: c, label: c }))}
                              showSearch placeholder="선택"
                            />
                          )}
                        </td>
                      )}
                      {!isAuto && (
                        <td style={td}>
                          {r.loading ? <Spin size="small" /> : (
                            <Select
                              size="small" value={r.recv_col || undefined} style={{ width: 110 }}
                              onChange={v => updateRow(r.filename, { recv_col: v || '' })}
                              options={r.columns.map(c => ({ value: c, label: c }))}
                              showSearch allowClear placeholder="없음 (선택)"
                            />
                          )}
                        </td>
                      )}
                      <td style={{ ...td, textAlign: 'right', color: '#6b7280' }}>{r.rows || '-'}</td>
                      <td style={{ ...td, textAlign: 'center' }}>
                        <Button type="text" size="small" danger icon={<DeleteOutlined />} onClick={() => removeRow(r.filename)} />
                      </td>
                    </tr>
                  )})}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* 옵션 + 집계 버튼 */}
        <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16 }}>
          <div style={{ fontWeight: 700, color: '#111827', marginBottom: 12 }}>⚙️ 집계 옵션</div>

          <div style={{ marginBottom: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <span style={{ fontSize: '0.8rem', color: '#374151' }}>마스터 매핑 사용</span>
              <Switch checked={useMaster} onChange={setUseMaster} size="small" />
            </div>
            <div style={{ fontSize: '0.72rem', color: '#9ca3af' }}>
              ON: 마스터 파일의 SKU 기준으로 동일 품목 자동 통합
            </div>
          </div>

          <div style={{ marginBottom: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <span style={{ fontSize: '0.8rem', color: '#374151' }}>번들 자동 분리</span>
              <Switch checked={splitBundles} onChange={setSplitBundles} size="small" />
            </div>
            <div style={{ fontSize: '0.72rem', color: '#9ca3af' }}>
              "3ea / 5개입 / x2" 등 → 단품 × N개로 자동 변환
            </div>
          </div>

          <div style={{ marginBottom: 14, opacity: useMaster ? 1 : 0.4 }}>
            <div style={{ fontSize: '0.8rem', color: '#374151', marginBottom: 4 }}>매칭 임계값: {threshold}%</div>
            <Slider min={50} max={100} value={threshold} onChange={setThreshold} disabled={!useMaster} />
          </div>

          <div style={{ marginBottom: 14 }}>
            <Button
              block size="small" icon={<LinkOutlined />}
              onClick={() => setMapMgrOpen(true)}
            >
              수동 매핑 관리 ({userMappings.length})
            </Button>
          </div>

          <Button
            type="primary" block size="large" icon={<ThunderboltOutlined />}
            onClick={handleAggregate} loading={aggregating}
            disabled={fileRows.length === 0}
            style={{ marginBottom: 8 }}
          >
            누적 추가 ({fileRows.length}개 파일)
          </Button>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 8, lineHeight: 1.4 }}>
            ※ 파일을 넣을수록 <b>기존 발주에 누적</b>됩니다. 같은 파일명은 갱신돼요. (기존 데이터는 안 사라짐)
          </div>

          <Button block icon={<CalendarOutlined />} disabled={!result}
            onClick={() => { setDashCollapsed(false); window.scrollTo({ top: 0, behavior: 'smooth' }) }}
            style={{ marginBottom: 8 }}>
            📊 통계 대시보드 (맨 위)
          </Button>

          {result && (
            <Button block icon={<DownloadOutlined />} onClick={handleDownloadCsv} style={{ marginBottom: 8 }}>
              CSV 다운로드
            </Button>
          )}
          {planFiles.length > 0 && (
            <Popconfirm title="누적된 발주를 모두 지울까요?" okText="전체 초기화" cancelText="취소"
              onConfirm={handleClearPlan}>
              <Button block danger icon={<DeleteOutlined />}>누적 전체 초기화 ({planFiles.length}개 파일)</Button>
            </Popconfirm>
          )}
        </div>
      </div>

      {/* 결과 */}
      {result && (
        <>
          {/* 요약 카드 — 수동 조정 반영 */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12, marginBottom: 16 }}>
            <SummaryCard label="품목 종류" value={adjustedItems.length} color="#0369a1" bg="#e0f2fe" />
            <SummaryCard label="총 수량" value={displayTotals.grand} color="#065f46" bg="#d1fae5" />
            <SummaryCard label="출고일 수" value={result.dates.length} color="#7c3aed" bg="#ede9fe" />
            <SummaryCard label="매칭 성공" value={adjustedItems.filter(i => i.matched).length} color="#059669" bg="#ecfdf5" />
            <SummaryCard label="매칭 실패" value={adjustedItems.filter(i => !i.matched).length} color="#b91c1c" bg="#fee2e2" />
          </div>

          {/* 누적된 발주 파일 — 파일별 통계 + 개별 제거 */}
          {result.per_file.length > 0 && (
            <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 12, marginBottom: 16 }}>
              <div style={{ fontSize: '0.8rem', fontWeight: 700, color: '#374151', marginBottom: 8 }}>
                📦 누적된 발주 파일 ({result.per_file.length}개) — 넣을수록 합산됩니다
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {result.per_file.map(p => (
                  <Tag key={p.filename} color="blue" closable
                    onClose={(e) => { e.preventDefault(); handleRemovePlanFile(p.filename) }}
                    style={{ fontSize: '0.75rem', padding: '4px 8px' }}>
                    📄 {p.filename} · {p.channel} · {p.rows}행 / {p.qty.toLocaleString()}개
                    {(p as any).ingested_at ? <span style={{ color: '#9ca3af' }}> · {String((p as any).ingested_at).slice(5, 16)}</span> : null}
                    {p.skipped_no_date > 0 && <span style={{ color: '#dc2626' }}> (날짜없음 {p.skipped_no_date} 제외)</span>}
                  </Tag>
                ))}
              </div>
              <div style={{ fontSize: '0.7rem', color: '#9ca3af', marginTop: 6 }}>※ 태그의 ✕를 누르면 해당 파일분만 빠지고 나머지는 유지됩니다.</div>
            </div>
          )}
          {result.errors.length > 0 && (
            <Alert
              type="warning" showIcon style={{ marginBottom: 16 }}
              message="경고"
              description={<ul style={{ margin: 0, paddingLeft: 18 }}>{result.errors.map((e, i) => <li key={i}>{e}</li>)}</ul>}
            />
          )}

          {/* 필터 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10, flexWrap: 'wrap' }}>
            <span style={{ fontSize: '0.82rem', color: '#374151', fontWeight: 600 }}>
              <CalendarOutlined /> 매트릭스 ({filteredItems.length} / {result.items.length} 품목)
            </span>
            <Select
              mode="multiple" allowClear placeholder="채널 필터"
              value={channelFilter} onChange={setChannelFilter}
              style={{ minWidth: 200 }} size="small"
              options={result.channels.map(c => ({ value: c, label: c }))}
            />
            <label style={{ fontSize: '0.78rem', color: '#374151', display: 'flex', alignItems: 'center', gap: 6 }}>
              <Switch size="small" checked={showOnlyUnmatched} onChange={setShowOnlyUnmatched} />
              매칭 실패만
            </label>
            <div style={{ flex: 1 }} />
            {adjustmentCount > 0 && (
              <Tag color="orange" style={{ fontSize: '0.7rem' }}>
                수동 조정 {adjustmentCount}건 — 삭제 {removedKeys.size} / 합치기 {Object.keys(mergeMap).length} / 세부이동 {Object.keys(rawMoves).length}
              </Tag>
            )}
            <Button size="small" disabled={adjustmentCount === 0} onClick={resetAdjustments}>
              조정 초기화
            </Button>
            <Button size="small" icon={<ReloadOutlined />} onClick={() => { setResult(null); resetAdjustments() }}>결과 초기화</Button>
          </div>

          <div style={{ fontSize: '0.72rem', color: '#9ca3af', marginBottom: 6 }}>
            💡 행을 드래그해서 다른 행에 떨어뜨리면 수량이 합쳐집니다. 단종품은 X 버튼으로 삭제.
          </div>

          {/* 매트릭스 테이블 */}
          <MatrixTable
            result={result}
            items={filteredItems}
            displayTotals={displayTotals}
            mergeMap={mergeMap}
            rawMoves={rawMoves}
            itemKey={itemKey}
            onEditItem={(it) => setMapModal({ open: true, item: it })}
            onRemove={(key) => removeItem(key)}
            onMerge={(src, tgt) => mergeInto(src, tgt)}
            onUndoMerge={(src) => undoMerge(src)}
            onMoveRaw={(raw, tgt) => moveRaw(raw, tgt)}
            onUndoMoveRaw={(raw) => undoRawMove(raw)}
            onExtractRaw={(raw) => extractRaw(raw)}
          />
        </>
      )}

      {/* 단일 품목 매핑 편집 모달 */}
      <ItemMappingModal
        open={mapModal.open}
        item={mapModal.item}
        masterOptions={masterOptions}
        userMappings={userMappings}
        onClose={() => setMapModal({ open: false, item: null })}
        onSave={saveUserMapping}
        onRemove={removeUserMapping}
      />

      {/* 전체 수동 매핑 관리 모달 */}
      <UserMappingsManagerModal
        open={mapMgrOpen}
        mappings={userMappings}
        onClose={() => setMapMgrOpen(false)}
        onRemove={removeUserMapping}
      />

    </div>
  )
}

function DashboardView({ d, items, onClearDate }: { d: PlanDashboard; items: OrderPlanItem[]; onClearDate?: (date: string) => void }) {
  const fmt = (n: number) => n.toLocaleString()
  const maxMonthly = Math.max(1, ...d.monthly.map(m => m.qty))
  const maxChannel = Math.max(1, ...d.by_channel.map(c => c.qty))
  const maxDaily = Math.max(1, ...d.daily.map(x => x.qty))
  // 날짜 클릭 시 펼쳐지는 세부내역
  const [openDate, setOpenDate] = useState<string | null>(null)
  // 날짜별 품목 세부내역 (수량 내림차순)
  const dailyDetail = useMemo(() => {
    const map: Record<string, { name: string; sku: string; matched: boolean; qty: number }[]> = {}
    for (const it of items) {
      for (const [dt, q] of Object.entries(it.by_date)) {
        if (q <= 0) continue
        ;(map[dt] ||= []).push({ name: it.name, sku: it.sku, matched: it.matched, qty: q })
      }
    }
    for (const k of Object.keys(map)) map[k].sort((a, b) => b.qty - a.qty)
    return map
  }, [items])
  return (
    <div style={{ fontSize: '0.82rem' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 16 }}>
        <SummaryCard label="총 발주수량" value={d.grand_total} color="#065f46" bg="#d1fae5" />
        <SummaryCard label="품목 종류" value={d.item_count} color="#0369a1" bg="#e0f2fe" />
        <SummaryCard label="누적 파일" value={d.file_count} color="#7c3aed" bg="#ede9fe" />
        <SummaryCard label="거래처 수" value={d.by_channel.length} color="#b45309" bg="#fef3c7" />
      </div>
      {(d.date_range.from || d.date_range.to) && (
        <div style={{ color: '#6b7280', marginBottom: 16 }}>기간: <b>{d.date_range.from}</b> ~ <b>{d.date_range.to}</b></div>
      )}

      {/* 월별 */}
      <div style={{ fontWeight: 700, color: '#374151', margin: '4px 0 8px' }}>📅 월별 발주수량</div>
      <div style={{ marginBottom: 18 }}>
        {d.monthly.map(m => (
          <div key={m.month} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <span style={{ width: 64, color: '#6b7280' }}>{m.month}</span>
            <div style={{ flex: 1, background: '#f3f4f6', borderRadius: 4, height: 18 }}>
              <div style={{ width: `${(m.qty / maxMonthly) * 100}%`, background: '#7c3aed', height: 18, borderRadius: 4 }} />
            </div>
            <span style={{ width: 80, textAlign: 'right', fontWeight: 600 }}>{fmt(m.qty)}</span>
          </div>
        ))}
      </div>

      {/* 일별 — 날짜 클릭 시 품목별 세부내역 펼침 */}
      <div style={{ fontWeight: 700, color: '#374151', margin: '4px 0 8px' }}>
        📆 일별 발주수량 <span style={{ fontWeight: 400, color: '#9ca3af', fontSize: '0.78rem' }}>· 날짜를 클릭하면 품목별 세부내역</span>
      </div>
      <div style={{ marginBottom: 18 }}>
        {d.daily.map(x => {
          const isOpen = openDate === x.date
          const detail = dailyDetail[x.date] || []
          return (
            <div key={x.date}>
              <div
                onClick={() => setOpenDate(isOpen ? null : x.date)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4,
                  cursor: 'pointer', borderRadius: 4, padding: '1px 2px',
                  background: isOpen ? '#ecfdf5' : undefined,
                }}
              >
                <span style={{ width: 14, color: '#9ca3af', fontSize: '0.7rem' }}>{isOpen ? '▾' : '▸'}</span>
                <span style={{ width: 84, color: '#6b7280' }}>{formatDateShort(x.date)}</span>
                <div style={{ flex: 1, background: '#f3f4f6', borderRadius: 4, height: 18 }}>
                  <div style={{ width: `${(x.qty / maxDaily) * 100}%`, background: '#10b981', height: 18, borderRadius: 4 }} />
                </div>
                <span style={{ width: 80, textAlign: 'right', fontWeight: 600 }}>{fmt(x.qty)}</span>
                {onClearDate && (
                  <Popconfirm
                    title={`${x.date} 발주 초기화`}
                    description={`이 날짜(${fmt(x.qty)}개)의 발주 데이터를 모두 삭제합니다.`}
                    okText="초기화" cancelText="취소" okButtonProps={{ danger: true }}
                    onConfirm={() => { if (isOpen) setOpenDate(null); onClearDate(x.date) }}
                  >
                    <Button size="small" type="text" danger icon={<DeleteOutlined />}
                      onClick={e => e.stopPropagation()}
                      style={{ width: 24 }} />
                  </Popconfirm>
                )}
              </div>
              {isOpen && (
                <div style={{ margin: '2px 0 10px 36px', background: '#fafafa', border: '1px solid #eef2f7', borderRadius: 6, padding: '6px 10px' }}>
                  {detail.length === 0 ? (
                    <div style={{ color: '#9ca3af', fontSize: '0.76rem' }}>세부내역 없음</div>
                  ) : detail.map((row, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '2px 0', fontSize: '0.78rem', borderTop: i ? '1px solid #f3f4f6' : undefined }}>
                      <span style={{ flex: 1, color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {row.sku && <span style={{ color: '#9ca3af', marginRight: 6 }}>{row.sku}</span>}
                        {row.name}
                        {!row.matched && <Tag color="red" style={{ marginLeft: 6, fontSize: '0.62rem' }}>미매칭</Tag>}
                      </span>
                      <span style={{ width: 70, textAlign: 'right', fontWeight: 600, color: '#065f46' }}>{fmt(row.qty)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* 거래처별 */}
      <div style={{ fontWeight: 700, color: '#374151', margin: '4px 0 8px' }}>🏷️ 거래처별 발주수량</div>
      <div style={{ marginBottom: 18 }}>
        {d.by_channel.map(c => (
          <div key={c.channel} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <span style={{ width: 130, color: '#374151', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.channel}</span>
            <div style={{ flex: 1, background: '#f3f4f6', borderRadius: 4, height: 18 }}>
              <div style={{ width: `${(c.qty / maxChannel) * 100}%`, background: '#0ea5e9', height: 18, borderRadius: 4 }} />
            </div>
            <span style={{ width: 80, textAlign: 'right', fontWeight: 600 }}>{fmt(c.qty)}</span>
          </div>
        ))}
      </div>

      {/* 거래처 × 월 매트릭스 */}
      {d.channel_month.months.length > 0 && (
        <>
          <div style={{ fontWeight: 700, color: '#374151', margin: '4px 0 8px' }}>📊 거래처 × 월</div>
          <div style={{ overflowX: 'auto', marginBottom: 18 }}>
            <table style={{ borderCollapse: 'collapse', fontSize: '0.78rem', width: '100%' }}>
              <thead><tr style={{ background: '#f9fafb' }}>
                <th style={{ padding: '5px 8px', textAlign: 'left', position: 'sticky', left: 0, background: '#f9fafb' }}>거래처</th>
                {d.channel_month.months.map(mo => <th key={mo} style={{ padding: '5px 8px', textAlign: 'right' }}>{mo}</th>)}
              </tr></thead>
              <tbody>
                {d.channel_month.channels.map(ch => (
                  <tr key={ch} style={{ borderTop: '1px solid #f3f4f6' }}>
                    <td style={{ padding: '5px 8px', position: 'sticky', left: 0, background: '#fff' }}>{ch}</td>
                    {d.channel_month.months.map(mo => {
                      const v = d.channel_month.matrix[ch]?.[mo] || 0
                      return <td key={mo} style={{ padding: '5px 8px', textAlign: 'right', color: v ? '#111827' : '#d1d5db' }}>{v ? fmt(v) : '·'}</td>
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* 상위 품목 */}
      <div style={{ fontWeight: 700, color: '#374151', margin: '4px 0 8px' }}>🥇 상위 품목 (총수량)</div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
        <thead><tr style={{ background: '#f9fafb' }}>
          <th style={{ padding: '5px 8px', textAlign: 'left' }}>#</th>
          <th style={{ padding: '5px 8px', textAlign: 'left' }}>품목</th>
          <th style={{ padding: '5px 8px', textAlign: 'right' }}>수량</th>
        </tr></thead>
        <tbody>
          {d.top_items.map((it, i) => (
            <tr key={i} style={{ borderTop: '1px solid #f3f4f6' }}>
              <td style={{ padding: '5px 8px', color: '#9ca3af' }}>{i + 1}</td>
              <td style={{ padding: '5px 8px' }}>{it.name}{!it.matched && <Tag color="red" style={{ marginLeft: 6, fontSize: '0.65rem' }}>미매칭</Tag>}</td>
              <td style={{ padding: '5px 8px', textAlign: 'right', fontWeight: 600 }}>{fmt(it.total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────
// 발주 대비 확정 입고 현황 — 요약 카드 + 일별 차트 + 품목별 입고율
// ─────────────────────────────────────────────────────────────────
const RECV_STATUS_META: Record<string, { label: string; color: string; tag: string }> = {
  full: { label: '입고완료', color: '#059669', tag: 'green' },
  over: { label: '초과입고', color: '#2563eb', tag: 'blue' },
  partial: { label: '부분입고', color: '#d97706', tag: 'orange' },
  none: { label: '미입고', color: '#dc2626', tag: 'red' },
  nodata: { label: '확정정보없음', color: '#9ca3af', tag: 'default' },
}

// 일별 입고율 색상 — 100%↑ 초록 / 일부 주황 / 0% 빨강 / 발주없음 회색
function dailyRateMeta(ordered: number, received: number): { text: string; color: string } {
  if (ordered <= 0) return { text: received > 0 ? '발주없음' : '−', color: '#9ca3af' }
  const rate = Math.round((received / ordered) * 1000) / 10
  if (rate >= 100) return { text: `${rate}%`, color: '#059669' }
  if (rate > 0) return { text: `${rate}%`, color: '#d97706' }
  return { text: '0%', color: '#dc2626' }
}

function ReceivingCompareView({ c, onSelectChannel }: { c: ReceivingCompare; onSelectChannel?: (ch: string) => void }) {
  const fmt = (n: number) => n.toLocaleString()
  const [statusFilter, setStatusFilter] = useState<string[]>([])
  const [openDate, setOpenDate] = useState<string | null>(null)
  const s = c.summary
  const maxDaily = Math.max(1, ...c.daily.map(x => Math.max(x.ordered, x.received)))
  const items = statusFilter.length > 0 ? c.items.filter(it => statusFilter.includes(it.status)) : c.items

  // 날짜별 품목 세부내역 — 발주·입고 있는 품목만, 발주량 내림차순
  const dailyDetail = useMemo(() => {
    const map: Record<string, { sku: string; name: string; ordered: number; received: number }[]> = {}
    for (const it of c.items) {
      const dates = new Set([...Object.keys(it.ordered_by_date || {}), ...Object.keys(it.received_by_date || {})])
      for (const d of dates) {
        const o = it.ordered_by_date?.[d] || 0
        const r = it.received_by_date?.[d] || 0
        if (o <= 0 && r <= 0) continue
        ;(map[d] ||= []).push({ sku: it.sku, name: it.name, ordered: o, received: r })
      }
    }
    for (const k of Object.keys(map)) map[k].sort((a, b) => b.ordered - a.ordered || b.received - a.received)
    return map
  }, [c.items])

  return (
    <div style={{ fontSize: '0.82rem' }}>
      {/* 요약 카드 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 10, marginBottom: 16 }}>
        <SummaryCard label="총 발주량" value={s.ordered_total} color="#065f46" bg="#d1fae5"
          sub={s.ordered_amt_total > 0 ? fmtWon(s.ordered_amt_total) : undefined} />
        <SummaryCard label="확정 입고량" value={s.received_total} color="#1d4ed8" bg="#dbeafe"
          sub={s.ordered_amt_total > 0 ? fmtWon(s.received_amt_total) : undefined} />
        <div style={{ background: '#f5f3ff', border: '1px solid #7c3aed30', borderRadius: 10, padding: '12px 14px' }}>
          <div style={{ fontSize: '1.6rem', fontWeight: 800, color: '#7c3aed' }}>{s.rate}%</div>
          <div style={{ fontSize: '0.72rem', color: '#7c3aed', fontWeight: 600, opacity: 0.85 }}>입고율</div>
        </div>
        <SummaryCard label="입고완료 품목" value={s.full_count} color="#059669" bg="#ecfdf5" />
        <SummaryCard label="부분입고 품목" value={s.partial_count} color="#d97706" bg="#fef3c7" />
        <SummaryCard label="미입고 품목" value={s.none_count} color="#b91c1c" bg="#fee2e2" />
      </div>
      {s.nodata_count > 0 && (
        <div style={{ color: '#9ca3af', fontSize: '0.74rem', marginBottom: 12 }}>
          ※ 확정수량 정보가 없는 품목 {s.nodata_count}개(발주 {fmt(s.ordered_total - s.ordered_with_data)}개)는
          입고율 계산에서 제외 — 발주서에 확정수량 컬럼을 지정하면 반영됩니다
        </div>
      )}

      {/* 거래처별 발주 vs 입고 — 클릭하면 해당 거래처로 전체 필터 */}
      {c.by_channel.length > 0 && (
        <>
          <div style={{ fontWeight: 700, color: '#374151', margin: '4px 0 8px' }}>
            🏷️ 거래처별 발주 vs 입고
            <span style={{ fontWeight: 400, color: '#9ca3af', fontSize: '0.76rem', marginLeft: 10 }}>
              · 거래처를 클릭하면 아래 일별·품목별이 해당 거래처 기준으로 바뀝니다
            </span>
          </div>
          <div style={{ marginBottom: 18 }}>
            {(() => {
              const maxCh = Math.max(1, ...c.by_channel.map(x => Math.max(x.ordered, x.received)))
              return c.by_channel.map(x => {
                const active = c.channel === x.channel
                const rateColor = x.rate >= 100 ? '#059669' : x.rate > 0 ? '#d97706' : '#dc2626'
                return (
                  <div
                    key={x.channel}
                    onClick={() => onSelectChannel?.(active ? '' : x.channel)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6,
                      cursor: onSelectChannel ? 'pointer' : undefined, borderRadius: 4, padding: '2px 4px',
                      background: active ? '#eff6ff' : undefined,
                      outline: active ? '1px solid #bfdbfe' : undefined,
                    }}
                  >
                    <span style={{ width: 100, color: '#374151', fontWeight: active ? 700 : 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {x.channel}{active && ' ✓'}
                    </span>
                    <div style={{ flex: 1 }}>
                      <div style={{ background: '#f3f4f6', borderRadius: 3, height: 10, marginBottom: 2 }}>
                        <div style={{ width: `${(x.ordered / maxCh) * 100}%`, background: '#10b981', height: 10, borderRadius: 3 }} />
                      </div>
                      <div style={{ background: '#f3f4f6', borderRadius: 3, height: 10 }}>
                        <div style={{ width: `${(x.received / maxCh) * 100}%`, background: '#3b82f6', height: 10, borderRadius: 3 }} />
                      </div>
                    </div>
                    <span style={{ width: 130, textAlign: 'right' }}>
                      <span style={{ fontWeight: 600, color: '#065f46' }}>{fmt(x.ordered)}</span>
                      <span style={{ color: '#9ca3af' }}> / </span>
                      <span style={{ fontWeight: 600, color: '#1d4ed8' }}>{fmt(x.received)}</span>
                    </span>
                    {(x.ordered_amt > 0 || x.received_amt > 0) ? (
                      <span style={{ width: 210, textAlign: 'right', fontSize: '0.72rem', color: '#6b7280' }}>
                        {fmtWon(x.ordered_amt)} <span style={{ color: '#d1d5db' }}>/</span> {fmtWon(x.received_amt)}
                      </span>
                    ) : <span style={{ width: 210 }} />}
                    <span style={{ width: 64, textAlign: 'right', fontWeight: 700, color: rateColor }}>{x.rate}%</span>
                  </div>
                )
              })
            })()}
          </div>
        </>
      )}

      {/* 일별 발주 vs 입고 차트 */}
      {c.daily.length > 0 && (
        <>
          <div style={{ fontWeight: 700, color: '#374151', margin: '4px 0 8px' }}>
            📊 일별 발주 vs 입고
            <span style={{ fontWeight: 400, color: '#9ca3af', fontSize: '0.76rem', marginLeft: 10 }}>
              <span style={{ display: 'inline-block', width: 10, height: 10, background: '#10b981', borderRadius: 2, marginRight: 4 }} />발주
              <span style={{ display: 'inline-block', width: 10, height: 10, background: '#3b82f6', borderRadius: 2, margin: '0 4px 0 12px' }} />입고
              <span style={{ marginLeft: 12 }}>· 날짜를 클릭하면 그 날의 품목별 발주/입고</span>
            </span>
          </div>
          <div style={{ marginBottom: 18 }}>
            {c.daily.map(x => {
              const isOpen = openDate === x.date
              // 일별 입고율 분모는 확정수량 정보가 있는 품목의 발주량만
              const rate = dailyRateMeta(x.ordered_with_data, x.received)
              const detail = dailyDetail[x.date] || []
              return (
                <div key={x.date}>
                  <div
                    onClick={() => setOpenDate(isOpen ? null : x.date)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6,
                      cursor: 'pointer', borderRadius: 4, padding: '1px 2px',
                      background: isOpen ? '#eff6ff' : undefined,
                    }}
                  >
                    <span style={{ width: 14, color: '#9ca3af', fontSize: '0.7rem' }}>{isOpen ? '▾' : '▸'}</span>
                    <span style={{ width: 84, color: '#6b7280' }}>{formatDateShort(x.date)}</span>
                    <div style={{ flex: 1 }}>
                      <div style={{ background: '#f3f4f6', borderRadius: 3, height: 10, marginBottom: 2 }}>
                        <div style={{ width: `${(x.ordered / maxDaily) * 100}%`, background: '#10b981', height: 10, borderRadius: 3 }} />
                      </div>
                      <div style={{ background: '#f3f4f6', borderRadius: 3, height: 10 }}>
                        <div style={{ width: `${(x.received / maxDaily) * 100}%`, background: '#3b82f6', height: 10, borderRadius: 3 }} />
                      </div>
                    </div>
                    <span style={{ width: 130, textAlign: 'right' }}>
                      <span style={{ fontWeight: 600, color: '#065f46' }}>{fmt(x.ordered)}</span>
                      <span style={{ color: '#9ca3af' }}> / </span>
                      <span style={{ fontWeight: 600, color: '#1d4ed8' }}>{fmt(x.received)}</span>
                    </span>
                    {(x.ordered_amt > 0 || x.received_amt > 0) && (
                      <span style={{ width: 210, textAlign: 'right', fontSize: '0.72rem', color: '#6b7280' }}>
                        {fmtWon(x.ordered_amt)} <span style={{ color: '#d1d5db' }}>/</span> {fmtWon(x.received_amt)}
                      </span>
                    )}
                    <span style={{ width: 64, textAlign: 'right', fontWeight: 700, color: rate.color }}>{rate.text}</span>
                  </div>
                  {isOpen && (
                    <div style={{ margin: '0 0 10px 36px', background: '#fafafa', border: '1px solid #eef2f7', borderRadius: 6, padding: '6px 10px' }}>
                      <div style={{ display: 'flex', gap: 8, fontSize: '0.68rem', color: '#9ca3af', fontWeight: 600, padding: '0 0 3px' }}>
                        <span style={{ flex: 1 }}>품목</span>
                        <span style={{ width: 70, textAlign: 'right' }}>발주</span>
                        <span style={{ width: 70, textAlign: 'right' }}>입고</span>
                        <span style={{ width: 64, textAlign: 'right' }}>입고율</span>
                      </div>
                      {detail.length === 0 ? (
                        <div style={{ color: '#9ca3af', fontSize: '0.76rem' }}>세부내역 없음</div>
                      ) : detail.map((row, i) => {
                        const rr = dailyRateMeta(row.ordered, row.received)
                        return (
                          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '2px 0', fontSize: '0.78rem', borderTop: i ? '1px solid #f3f4f6' : undefined }}>
                            <span style={{ flex: 1, color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {row.sku && <span style={{ color: '#9ca3af', marginRight: 6 }}>{row.sku}</span>}
                              {row.name}
                            </span>
                            <span style={{ width: 70, textAlign: 'right', fontWeight: 600, color: '#065f46' }}>{row.ordered ? fmt(row.ordered) : '·'}</span>
                            <span style={{ width: 70, textAlign: 'right', fontWeight: 600, color: '#1d4ed8' }}>{row.received ? fmt(row.received) : '·'}</span>
                            <span style={{ width: 64, textAlign: 'right', fontWeight: 700, color: rr.color }}>{rr.text}</span>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}

      {/* 품목별 입고율 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '4px 0 8px' }}>
        <span style={{ fontWeight: 700, color: '#374151' }}>📦 품목별 발주 대비 입고 ({items.length})</span>
        <Select
          mode="multiple" allowClear placeholder="상태 필터" size="small"
          value={statusFilter} onChange={setStatusFilter} style={{ minWidth: 180 }}
          options={Object.entries(RECV_STATUS_META).map(([v, m]) => ({ value: v, label: m.label }))}
        />
      </div>
      <div style={{ overflowX: 'auto', marginBottom: 12 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
          <thead><tr style={{ background: '#f9fafb' }}>
            <th style={{ padding: '5px 8px', textAlign: 'left' }}>품목</th>
            <th style={{ padding: '5px 8px', textAlign: 'right', width: 80 }}>발주량</th>
            <th style={{ padding: '5px 8px', textAlign: 'right', width: 80 }}>입고량</th>
            <th style={{ padding: '5px 8px', textAlign: 'right', width: 70 }}>차이</th>
            <th style={{ padding: '5px 8px', textAlign: 'right', width: 105 }}>발주금액</th>
            <th style={{ padding: '5px 8px', textAlign: 'right', width: 105 }}>입고금액</th>
            <th style={{ padding: '5px 8px', textAlign: 'left', width: 190 }}>입고율</th>
            <th style={{ padding: '5px 8px', textAlign: 'center', width: 80 }}>상태</th>
          </tr></thead>
          <tbody>
            {items.map(it => {
              const meta = RECV_STATUS_META[it.status] || RECV_STATUS_META.none
              const pct = Math.min(100, it.rate)
              return (
                <tr key={it.sku || it.name} style={{ borderTop: '1px solid #f3f4f6' }}>
                  <td style={{ padding: '5px 8px' }}>
                    <span style={{ color: '#9ca3af', marginRight: 6 }}>{it.sku}</span>{it.name}
                  </td>
                  <td style={{ padding: '5px 8px', textAlign: 'right', fontWeight: 600, color: '#065f46' }}>{fmt(it.ordered)}</td>
                  <td style={{ padding: '5px 8px', textAlign: 'right', fontWeight: 600, color: '#1d4ed8' }}>{fmt(it.received)}</td>
                  <td style={{ padding: '5px 8px', textAlign: 'right', fontWeight: 600, color: it.diff < 0 ? '#dc2626' : it.diff > 0 ? '#2563eb' : '#9ca3af' }}>
                    {it.diff > 0 ? `+${fmt(it.diff)}` : fmt(it.diff)}
                  </td>
                  <td style={{ padding: '5px 8px', textAlign: 'right', color: '#374151' }}>{fmtWon(it.ordered_amt)}</td>
                  <td style={{ padding: '5px 8px', textAlign: 'right', color: '#374151' }}>{fmtWon(it.received_amt)}</td>
                  <td style={{ padding: '5px 8px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <div style={{ flex: 1, background: '#f3f4f6', borderRadius: 4, height: 14 }}>
                        <div style={{ width: `${pct}%`, background: meta.color, height: 14, borderRadius: 4, opacity: 0.85 }} />
                      </div>
                      <span style={{ width: 48, textAlign: 'right', fontWeight: 600, color: meta.color }}>{it.rate}%</span>
                    </div>
                  </td>
                  <td style={{ padding: '5px 8px', textAlign: 'center' }}>
                    <Tag color={meta.tag} style={{ fontSize: '0.68rem', marginRight: 0 }}>{meta.label}</Tag>
                  </td>
                </tr>
              )
            })}
            {items.length === 0 && (
              <tr><td colSpan={8} style={{ padding: 12, textAlign: 'center', color: '#9ca3af' }}>해당 상태의 품목 없음</td></tr>
            )}
          </tbody>
        </table>
      </div>

    </div>
  )
}

const th: React.CSSProperties = { padding: '8px 10px', textAlign: 'left', fontWeight: 600, color: '#6b7280' }
const td: React.CSSProperties = { padding: '6px 10px', verticalAlign: 'middle' }
const badgeStyle: React.CSSProperties = { fontSize: '0.65rem', padding: '0 5px', marginRight: 0 }

function SummaryCard({ label, value, color, bg, sub }: { label: string; value: number; color: string; bg: string; sub?: string }) {
  return (
    <div style={{ background: bg, border: `1px solid ${color}30`, borderRadius: 10, padding: '12px 14px' }}>
      <div style={{ fontSize: '1.6rem', fontWeight: 800, color }}>{value.toLocaleString()}</div>
      <div style={{ fontSize: '0.72rem', color, fontWeight: 600, opacity: 0.85 }}>{label}</div>
      {sub && <div style={{ fontSize: '0.72rem', color, fontWeight: 700, marginTop: 2, opacity: 0.95 }}>{sub}</div>}
    </div>
  )
}

// 금액 표기 — 0/미파싱은 '-'
function fmtWon(n: number | undefined | null): string {
  return n ? `₩${n.toLocaleString()}` : '-'
}

function MatrixTable({
  result, items, displayTotals, mergeMap, rawMoves, itemKey,
  onEditItem, onRemove, onMerge, onUndoMerge, onMoveRaw, onUndoMoveRaw, onExtractRaw,
}: {
  result: OrderPlanResult
  items: OrderPlanItem[]
  displayTotals: { byDate: Record<string, number>; byChannel: Record<string, number>; grand: number }
  mergeMap: Record<string, string>
  rawMoves: Record<string, string>
  itemKey: (it: { sku: string; name: string }) => string
  onEditItem: (it: OrderPlanItem) => void
  onRemove: (key: string) => void
  onMerge: (sourceKey: string, targetKey: string) => void
  onUndoMerge: (sourceKey: string) => void
  onMoveRaw: (rawName: string, targetKey: string) => void
  onUndoMoveRaw: (rawName: string) => void
  onExtractRaw: (rawName: string) => void
}) {
  // 드래그 상태 — 행 단위 또는 raw 단위
  // type 'row': 행 전체 합치기, type 'raw': raw_name 1개 이동
  const [dragInfo, setDragInfo] = useState<
    | { type: 'row'; key: string }
    | { type: 'raw'; rawName: string; sourceKey: string; total: number; displayName: string }
    | null
  >(null)
  const [hoverKey, setHoverKey] = useState<string | null>(null)

  // 셀 값에 따라 배경 강도 표시 (최대값 대비)
  const maxQty = useMemo(() => {
    let m = 0
    for (const it of items) {
      for (const d of result.dates) {
        const v = it.by_date[d] || 0
        if (v > m) m = v
      }
    }
    return m || 1
  }, [items, result.dates])

  // 합쳐진 원본 키 → 타깃 키 역인덱스 (타깃 행에 "← 합쳐짐 N건" 뱃지 표시용)
  const mergedIntoCount = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const tgt of Object.values(mergeMap)) {
      counts[tgt] = (counts[tgt] || 0) + 1
    }
    return counts
  }, [mergeMap])

  const columns: any[] = [
    {
      title: '품목명', dataIndex: 'name', key: 'name', fixed: 'left', width: 320,
      render: (_: any, it: OrderPlanItem) => {
        const key = itemKey(it)
        const mergedInto = mergedIntoCount[key] || 0
        return (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ color: '#9ca3af', cursor: 'grab', fontSize: '0.75rem' }} title="드래그해서 다른 행에 합치기">⋮⋮</span>
              <div style={{ fontWeight: 600, color: '#111827', fontSize: '0.82rem', flex: 1 }}>{it.name}</div>
            </div>
            <div style={{ display: 'flex', gap: 4, alignItems: 'center', marginTop: 2, flexWrap: 'wrap' }}>
              {it.sku && <Tag color="green" style={{ fontSize: '0.65rem', padding: '0 5px', marginRight: 0 }}>{it.sku}</Tag>}
              {!it.matched && <Tag color="red" style={{ fontSize: '0.65rem', padding: '0 5px', marginRight: 0 }}>미매칭</Tag>}
              {it.match_source === 'user' && (
                <Tag color="gold" style={{ fontSize: '0.65rem', padding: '0 5px', marginRight: 0 }}>수동</Tag>
              )}
              {it.match_source === 'extract' && (
                <Tag color="geekblue" style={{ fontSize: '0.65rem', padding: '0 5px', marginRight: 0 }}>분리됨</Tag>
              )}
              {it.had_bundle && (
                <Tag color="cyan" style={{ fontSize: '0.65rem', padding: '0 5px', marginRight: 0 }}>번들분리</Tag>
              )}
              {mergedInto > 0 && (
                <Tag color="purple" style={{ fontSize: '0.65rem', padding: '0 5px', marginRight: 0 }}>
                  ← 합쳐짐 {mergedInto}
                </Tag>
              )}
              {it.raw_names.length > 1 && (
                <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{it.raw_names.join('\n')}</div>}>
                  <Tag
                    style={{ fontSize: '0.65rem', padding: '0 5px', marginRight: 0, cursor: 'pointer' }}
                    onClick={() => onEditItem(it)}
                  >
                    이명 {it.raw_names.length} <EditOutlined />
                  </Tag>
                </Tooltip>
              )}
              <Tooltip title="원본명 → SKU 수동 매핑">
                <Button
                  type="text" size="small" icon={<EditOutlined />}
                  onClick={() => onEditItem(it)}
                  style={{ height: 18, padding: '0 4px', fontSize: '0.65rem', color: '#6b7280' }}
                />
              </Tooltip>
              <Popconfirm
                title="이 품목을 표에서 삭제할까요?"
                description="단종품 등 — 다시 보고싶으면 '조정 초기화' 사용"
                onConfirm={() => onRemove(key)}
                okText="삭제" cancelText="취소"
              >
                <Tooltip title="이 행 삭제 (세션)">
                  <Button
                    type="text" size="small" danger icon={<DeleteOutlined />}
                    style={{ height: 18, padding: '0 4px', fontSize: '0.65rem' }}
                  />
                </Tooltip>
              </Popconfirm>
            </div>
          </div>
        )
      },
    },
    ...result.dates.map(d => ({
      title: <div style={{ fontSize: '0.72rem', textAlign: 'center' }}>{formatDateHeader(d)}</div>,
      dataIndex: ['by_date', d], key: d, align: 'center' as const, width: 76,
      render: (_: any, it: OrderPlanItem) => {
        const v = it.by_date[d] || 0
        if (v === 0) return <span style={{ color: '#e5e7eb' }}>·</span>
        const intensity = Math.min(1, v / maxQty)
        const bg = `rgba(16, 185, 129, ${0.08 + intensity * 0.35})`
        return (
          <div style={{ background: bg, borderRadius: 4, padding: '4px 2px', fontWeight: 600, color: '#065f46', fontSize: '0.82rem' }}>
            {v.toLocaleString()}
          </div>
        )
      },
    })),
    {
      title: '합계', dataIndex: 'total', key: 'total', fixed: 'right', width: 80, align: 'center' as const,
      render: (v: number) => <span style={{ fontWeight: 700, color: '#1e40af' }}>{v.toLocaleString()}</span>,
    },
  ]

  // 합계 행 — displayTotals 기준
  const summary = () => {
    const dateTotals = result.dates.map(d => displayTotals.byDate[d] || 0)
    return (
      <Table.Summary fixed>
        <Table.Summary.Row style={{ background: '#f0fdf4', fontWeight: 700 }}>
          {/* expandable이 만드는 펼침(+) 컬럼까지 colSpan=2로 덮어야 날짜 컬럼과 정렬됨 */}
          <Table.Summary.Cell index={0} colSpan={2}>날짜 합계</Table.Summary.Cell>
          {dateTotals.map((v, i) => (
            <Table.Summary.Cell key={i} index={i + 2} align="center">
              <span style={{ color: '#065f46', fontWeight: 700 }}>{v.toLocaleString()}</span>
            </Table.Summary.Cell>
          ))}
          <Table.Summary.Cell index={dateTotals.length + 2} align="center">
            <span style={{ color: '#1e40af', fontWeight: 800 }}>{displayTotals.grand.toLocaleString()}</span>
          </Table.Summary.Cell>
        </Table.Summary.Row>
      </Table.Summary>
    )
  }

  return (
    <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', overflow: 'hidden' }}>
      <Table
        columns={columns}
        dataSource={items.map((it, i) => ({ ...it, key: `${itemKey(it)}-${i}`, _key: itemKey(it) }))}
        scroll={{ x: 'max-content', y: 480 }}
        size="small"
        pagination={false}
        summary={summary}
        sticky
        expandable={{
          expandedRowRender: (record: any) => {
            const it = record as OrderPlanItem
            const breakdown = it.raw_breakdown || {}
            const raws = Object.keys(breakdown).sort()
            if (raws.length === 0) {
              return <div style={{ padding: 8, color: '#9ca3af', fontSize: '0.78rem' }}>세부 품목 정보 없음</div>
            }
            const DCELL = 64  // 날짜 칸 폭 (세부내역 표 정렬용)
            return (
              <div style={{ padding: '6px 4px', background: '#fafafa' }}>
                <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 6, paddingLeft: 4 }}>
                  💡 드래그 → 다른 행에 드롭: 이동 | "분리" 버튼: 별도 품목으로 새 행 생성
                </div>
                {/* 날짜 헤더 — 메인 매트릭스와 동일한 날짜 컬럼 */}
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8, padding: '0 8px 2px', fontSize: '0.66rem', color: '#9ca3af', fontWeight: 600 }}>
                  <span style={{ width: 12 }} />
                  <span style={{ flex: 1, minWidth: 0 }}>세부내역</span>
                  {result.dates.map(d => (
                    <span key={d} style={{ width: DCELL, textAlign: 'center' }}>{formatDateHeader(d)}</span>
                  ))}
                  <span style={{ width: 60, textAlign: 'right' }}>합계</span>
                  <span style={{ width: 56 }} />
                </div>
                {raws.map(raw => {
                  const b = breakdown[raw]
                  const movedTo = rawMoves[raw]
                  return (
                    <div
                      key={raw}
                      draggable
                      onDragStart={(e: React.DragEvent) => {
                        setDragInfo({ type: 'raw', rawName: raw, sourceKey: itemKey(it), total: b.total, displayName: it.name })
                        e.dataTransfer.effectAllowed = 'move'
                        try { e.dataTransfer.setData('text/plain', `raw:${raw}`) } catch { /* ignore */ }
                      }}
                      onDragEnd={() => { setDragInfo(null); setHoverKey(null) }}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        padding: '4px 8px', margin: '2px 0',
                        background: '#fff', border: '1px solid #e5e7eb', borderRadius: 6,
                        cursor: 'grab', fontSize: '0.78rem',
                      }}
                    >
                      <span style={{ color: '#9ca3af', fontSize: '0.7rem', width: 12 }}>⋮⋮</span>
                      <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{raw}</span>
                        {b.had_bundle && (
                          <Tag color="cyan" style={{ fontSize: '0.62rem', padding: '0 4px', marginRight: 0, flexShrink: 0 }}>
                            ×{b.bundle_count} 번들분리
                          </Tag>
                        )}
                      </div>
                      {/* 날짜별 세부 수량 */}
                      {result.dates.map(d => {
                        const dv = b.by_date[d] || 0
                        return (
                          <span
                            key={d}
                            style={{
                              width: DCELL, textAlign: 'center', fontSize: '0.76rem',
                              color: dv ? '#065f46' : '#d1d5db', fontWeight: dv ? 600 : 400,
                            }}
                          >
                            {dv ? dv.toLocaleString() : '·'}
                          </span>
                        )
                      })}
                      <span style={{ color: '#1e40af', fontWeight: 700, width: 60, textAlign: 'right' }}>
                        {b.total.toLocaleString()}개
                      </span>
                      <span style={{ width: 56, textAlign: 'right' }}>
                      {movedTo ? (
                        <Tooltip title="이 항목 이동 되돌리기">
                          <Button
                            size="small" type="link"
                            onClick={() => onUndoMoveRaw(raw)}
                            style={{ fontSize: '0.7rem', padding: '0 4px' }}
                          >되돌리기</Button>
                        </Tooltip>
                      ) : raws.length > 1 ? (
                        <Tooltip title="별도 품목으로 분리 — 새 행으로 빠짐">
                          <Button
                            size="small" type="link"
                            onClick={() => {
                              onExtractRaw(raw)
                              message.success({
                                content: (
                                  <span>
                                    "{raw}" → 별도 행으로 분리됨.{' '}
                                    <a onClick={() => { onUndoMoveRaw(raw); message.success('분리 되돌림') }} style={{ marginLeft: 8 }}>되돌리기</a>
                                  </span>
                                ),
                                duration: 5,
                              })
                            }}
                            style={{ fontSize: '0.7rem', padding: '0 4px', color: '#7c3aed' }}
                          >분리</Button>
                        </Tooltip>
                      ) : null}
                      </span>
                    </div>
                  )
                })}
              </div>
            )
          },
          rowExpandable: (record: any) => {
            const it = record as OrderPlanItem
            return Object.keys(it.raw_breakdown || {}).length > 0
          },
        }}
        onRow={(record: any) => {
          const key = record._key as string
          const isRowDragging = dragInfo?.type === 'row' && dragInfo.key === key
          // raw 드래그: 자기 행도 드롭 가능 (=원위치, 무시) — 표시는 다른 행만 하이라이트
          const showHover = hoverKey === key && (
            (dragInfo?.type === 'row' && dragInfo.key !== key) ||
            (dragInfo?.type === 'raw' && dragInfo.sourceKey !== key)
          )
          return {
            draggable: true,
            onDragStart: (e: React.DragEvent) => {
              setDragInfo({ type: 'row', key })
              e.dataTransfer.effectAllowed = 'move'
              try { e.dataTransfer.setData('text/plain', `row:${key}`) } catch { /* ignore */ }
            },
            onDragEnd: () => { setDragInfo(null); setHoverKey(null) },
            onDragOver: (e: React.DragEvent) => {
              if (!dragInfo) return
              if (dragInfo.type === 'row' && dragInfo.key === key) return
              e.preventDefault()
              e.dataTransfer.dropEffect = 'move'
              if (hoverKey !== key) setHoverKey(key)
            },
            onDragLeave: () => { if (hoverKey === key) setHoverKey(null) },
            onDrop: (e: React.DragEvent) => {
              e.preventDefault()
              if (!dragInfo) return
              if (dragInfo.type === 'row' && dragInfo.key !== key) {
                onMerge(dragInfo.key, key)
                const src = dragInfo.key
                message.success({
                  content: (
                    <span>
                      "{record.name}" 행으로 합쳤습니다.{' '}
                      <a onClick={() => { onUndoMerge(src); message.success('합치기 되돌림') }} style={{ marginLeft: 8 }}>되돌리기</a>
                    </span>
                  ),
                  duration: 5,
                })
              } else if (dragInfo.type === 'raw' && dragInfo.sourceKey !== key) {
                const { rawName, total } = dragInfo
                onMoveRaw(rawName, key)
                message.success({
                  content: (
                    <span>
                      "{rawName}" ({total.toLocaleString()}개) → "{record.name}"로 이동.{' '}
                      <a onClick={() => { onUndoMoveRaw(rawName); message.success('이동 되돌림') }} style={{ marginLeft: 8 }}>되돌리기</a>
                    </span>
                  ),
                  duration: 5,
                })
              }
              setDragInfo(null); setHoverKey(null)
            },
            style: {
              cursor: 'grab',
              opacity: isRowDragging ? 0.4 : 1,
              background: showHover ? 'rgba(124, 58, 237, 0.10)' : undefined,
              outline: showHover ? '2px dashed #7c3aed' : undefined,
              outlineOffset: showHover ? '-2px' : undefined,
              transition: 'background 0.15s, outline 0.15s',
            },
          }
        }}
      />
    </div>
  )
}

// 2026-06-30 → "06/30 (화)" (한 줄)
function formatDateShort(d: string): string {
  try {
    const dt = new Date(d + 'T00:00:00')
    const md = `${String(dt.getMonth() + 1).padStart(2, '0')}/${String(dt.getDate()).padStart(2, '0')}`
    const dow = ['일', '월', '화', '수', '목', '금', '토'][dt.getDay()]
    return `${md} (${dow})`
  } catch {
    return d
  }
}

function formatDateHeader(d: string): React.ReactNode {
  // 2026-06-23 → "06/23 (화)"
  try {
    const dt = new Date(d + 'T00:00:00')
    const md = `${String(dt.getMonth() + 1).padStart(2, '0')}/${String(dt.getDate()).padStart(2, '0')}`
    const dow = ['일', '월', '화', '수', '목', '금', '토'][dt.getDay()]
    return (
      <div style={{ lineHeight: 1.15 }}>
        <div>{md}</div>
        <div style={{ fontSize: '0.65rem', color: dow === '일' ? '#dc2626' : dow === '토' ? '#2563eb' : '#9ca3af' }}>({dow})</div>
      </div>
    )
  } catch {
    return d
  }
}

// ─────────────────────────────────────────────────────────────────
// 단일 품목 매핑 편집 모달
// 한 행에 묶인 raw_names(원본명)들을 개별로 다른 SKU로 옮기거나 미매칭 처리
// ─────────────────────────────────────────────────────────────────
function ItemMappingModal({
  open, item, masterOptions, userMappings, onClose, onSave, onRemove,
}: {
  open: boolean
  item: OrderPlanItem | null
  masterOptions: { value: string; label: string; name: string }[]
  userMappings: OrderPlanUserMapping[]
  onClose: () => void
  onSave: (rawName: string, sku: string, masterName: string) => Promise<void>
  onRemove: (rawName: string) => Promise<void>
}) {
  // 각 원본명별 선택값 (sku) — '' = 강제 미매칭, undefined = 미설정
  const [edits, setEdits] = useState<Record<string, string | undefined>>({})

  useEffect(() => {
    if (open && item) setEdits({})
  }, [open, item])

  if (!item) return null

  const existingByRaw = new Map(userMappings.map(m => [m.raw_name, m]))

  return (
    <Modal
      open={open}
      title={
        <div>
          <ApiOutlined /> 매핑 편집 — <span style={{ color: '#0369a1' }}>{item.name}</span>
        </div>
      }
      onCancel={onClose}
      footer={<Button onClick={onClose}>닫기</Button>}
      width={780}
      destroyOnClose
    >
      <div style={{ fontSize: '0.78rem', color: '#6b7280', marginBottom: 12 }}>
        현재 행에 묶인 원본명을 다른 SKU로 옮기거나, 퍼지 매칭이 잘못 잡지 않도록 "강제 미매칭"으로 설정할 수 있어요.
        저장 시 자동으로 다시 집계합니다.
      </div>

      <List
        size="small"
        dataSource={item.raw_names}
        renderItem={(raw) => {
          const existing = existingByRaw.get(raw)
          const current = edits[raw] !== undefined ? edits[raw] : existing?.sku
          return (
            <List.Item
              actions={[
                <Select
                  key="sel"
                  size="small"
                  showSearch
                  allowClear
                  style={{ width: 280 }}
                  placeholder="이 SKU로 매핑"
                  value={current === undefined ? undefined : (current || '__UNMATCH__')}
                  onChange={v => setEdits(prev => ({
                    ...prev,
                    [raw]: v === '__UNMATCH__' ? '' : (v || undefined),
                  }))}
                  filterOption={(input, opt) => String(opt?.label || '').toLowerCase().includes(input.toLowerCase())}
                  options={[
                    { value: '__UNMATCH__', label: '⛔ 강제 미매칭 (퍼지 차단)' },
                    ...masterOptions.map(o => ({ value: o.value, label: o.label })),
                  ]}
                />,
                <Button
                  key="save"
                  type="primary" size="small"
                  disabled={edits[raw] === undefined}
                  onClick={async () => {
                    const sel = edits[raw] ?? ''
                    const masterName = sel
                      ? (masterOptions.find(o => o.value === sel)?.name || '')
                      : ''
                    await onSave(raw, sel, masterName)
                    setEdits(prev => { const c = { ...prev }; delete c[raw]; return c })
                  }}
                >저장</Button>,
                existing ? (
                  <Popconfirm
                    key="del"
                    title="이 매핑을 삭제할까요?"
                    onConfirm={async () => { await onRemove(raw) }}
                    okText="삭제" cancelText="취소"
                  >
                    <Button danger size="small" icon={<DeleteOutlined />} />
                  </Popconfirm>
                ) : null,
              ].filter(Boolean) as any}
            >
              <List.Item.Meta
                title={<span style={{ fontSize: '0.85rem' }}>{raw}</span>}
                description={
                  existing ? (
                    <span style={{ fontSize: '0.72rem', color: '#059669' }}>
                      현재: {existing.sku
                        ? <>→ <b>{existing.sku}</b> {existing.master_name && `· ${existing.master_name}`}</>
                        : <>→ <b>강제 미매칭</b></>}
                    </span>
                  ) : (
                    <span style={{ fontSize: '0.72rem', color: '#9ca3af' }}>자동 매칭 결과 사용 중</span>
                  )
                }
              />
            </List.Item>
          )
        }}
      />
    </Modal>
  )
}

// ─────────────────────────────────────────────────────────────────
// 전체 수동 매핑 관리 모달
// ─────────────────────────────────────────────────────────────────
function UserMappingsManagerModal({
  open, mappings, onClose, onRemove,
}: {
  open: boolean
  mappings: OrderPlanUserMapping[]
  onClose: () => void
  onRemove: (rawName: string) => Promise<void>
}) {
  const [filter, setFilter] = useState('')
  const filtered = useMemo(() => {
    if (!filter.trim()) return mappings
    const q = filter.trim().toLowerCase()
    return mappings.filter(m =>
      m.raw_name.toLowerCase().includes(q) ||
      m.sku.toLowerCase().includes(q) ||
      m.master_name.toLowerCase().includes(q)
    )
  }, [mappings, filter])

  return (
    <Modal
      open={open}
      title={<><LinkOutlined /> 수동 매핑 관리 ({mappings.length})</>}
      onCancel={onClose}
      footer={<Button onClick={onClose}>닫기</Button>}
      width={780}
      destroyOnClose
    >
      <Input.Search
        placeholder="원본명·SKU·마스터명 검색"
        allowClear value={filter} onChange={e => setFilter(e.target.value)}
        style={{ marginBottom: 12 }}
      />
      {filtered.length === 0 ? (
        <Empty description="저장된 수동 매핑이 없어요" />
      ) : (
        <List
          size="small"
          dataSource={filtered}
          renderItem={(m) => (
            <List.Item
              actions={[
                <Popconfirm
                  key="del"
                  title="이 매핑을 삭제할까요?"
                  onConfirm={async () => { await onRemove(m.raw_name) }}
                  okText="삭제" cancelText="취소"
                >
                  <Button danger size="small" icon={<DeleteOutlined />}>삭제</Button>
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={<span style={{ fontSize: '0.85rem' }}>{m.raw_name}</span>}
                description={
                  <span style={{ fontSize: '0.72rem', color: '#374151' }}>
                    {m.sku
                      ? <>→ <Tag color="green" style={{ fontSize: '0.65rem' }}>{m.sku}</Tag> {m.master_name}</>
                      : <Tag color="red" style={{ fontSize: '0.65rem' }}>강제 미매칭</Tag>}
                  </span>
                }
              />
            </List.Item>
          )}
        />
      )}
    </Modal>
  )
}
