import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Input,
  Page,
  PageHeader,
  Segmented,
  Select,
  Switch,
  Table,
  Text,
  toast,
} from 'oink-ui'
import type { TableColumn } from 'oink-ui'

import { api, errText } from '../api'
import type {
  LayoutData,
  PageProps,
  ScanBoxResp,
  SolveRow,
  ViolationRow,
  ViolationsResp,
} from '../types'

const PAD_COLORS: Record<string, string> = {
  PRIMARY_CONST: '#f59e0b',
  PRIMARY_SWITCHING: '#ef4444',
  SECONDARY: '#06b6d4',
  '': '#94a3b8',
}

function withAlpha(hex: string, alpha: number): string {
  const h = hex.replace('#', '')
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

//: 违规圈固定半径（屏幕像素）：所有 ERROR / WARNING 圈一样大，只靠颜色区分，不随缺口变化
const MARK_RADIUS = 12

interface View {
  scale: number
  ox: number
  oy: number
}

type HoverInfo =
  | { kind: 'violation'; px: number; py: number; v: ViolationRow }
  | { kind: 'mount'; px: number; py: number; m: LayoutData['mounts'][number] }

export default function LayoutPage({ setStatus, setStatusError, active, revision }: PageProps) {
  const [data, setData] = useState<LayoutData | null>(null)
  const [viol, setViol] = useState<ViolationsResp | null>(null)
  const [layer, setLayer] = useState<'all' | 'TOP' | 'BOTTOM'>('all')
  const [labels, setLabels] = useState(false)
  // 与违规清单页完全一致的筛选维度（直接透传 /api/violations）
  const [kind, setKind] = useState<'component' | 'pad'>('component')
  const [showError, setShowError] = useState(true)
  const [showWarning, setShowWarning] = useState(true)
  const [rule, setRule] = useState('')
  const [isolation, setIsolation] = useState(false)
  const [search, setSearch] = useState('')
  const [hover, setHover] = useState<HoverInfo | null>(null)
  const [selecting, setSelecting] = useState(false)
  const [rect, setRect] = useState<[number, number, number, number] | null>(null)
  const [plans, setPlans] = useState<SolveRow[]>([])
  const [busy, setBusy] = useState(false)
  // 扫描框（区域重扫）：板坐标 mm
  const [scanBox, setScanBox] = useState<[number, number, number, number] | null>(null)
  const [boxMode, setBoxMode] = useState(false)

  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const viewRef = useRef<View>({ scale: 1, ox: 0, oy: 0 })
  const dragRef = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null)
  const selectRef = useRef<{ x0: number; y0: number } | null>(null)
  const fittedRef = useRef(false)
  // 扫描框拖拽：new=新画 / move=整体移动 / resize=拖角
  const boxDragRef = useRef<
    | { kind: 'new'; x0: number; y0: number }
    | { kind: 'move'; x0: number; y0: number; box: [number, number, number, number] }
    | { kind: 'resize'; handle: string; box: [number, number, number, number] }
    | null
  >(null)
  const scanBoxRef = useRef<[number, number, number, number] | null>(null)

  const severity = showError && showWarning ? 'all' : showError ? 'error' : showWarning ? 'warning' : 'none'

  // ------------------------------------------------------------ 扫描框
  const applyScanBox = (box: [number, number, number, number] | null) => {
    scanBoxRef.current = box
    setScanBox(box)
  }

  const loadScanBox = useCallback(async () => {
    try {
      const r = await api.get<ScanBoxResp>('/api/scan-box')
      applyScanBox(r.box ? [r.box.x1, r.box.y1, r.box.x2, r.box.y2] : null)
    } catch {
      // 旧版后端没有该接口时静默忽略
    }
  }, [])

  const saveScanBox = useCallback(
    async (box: [number, number, number, number] | null) => {
      try {
        const r = await api.post<ScanBoxResp>('/api/scan-box', {
          box: box ? { x1: box[0], y1: box[1], x2: box[2], y2: box[3] } : null,
        })
        applyScanBox(r.box ? [r.box.x1, r.box.y1, r.box.x2, r.box.y2] : null)
        if (r.clamped) toast.warning('扫描框已自动裁剪到板框范围内')
        if (r.box) {
          setStatus(
            `扫描框：${(r.box.x2 - r.box.x1).toFixed(1)} × ${(r.box.y2 - r.box.y1).toFixed(1)} mm`,
          )
        } else {
          setStatus('已清除扫描框')
        }
      } catch (e) {
        const msg = errText(e)
        setStatusError(msg)
        toast.danger(msg)
        applyScanBox(null)
      }
    },
    [setStatus, setStatusError],
  )

  const loadLayout = useCallback(async () => {
    try {
      setData(await api.get<LayoutData>('/api/layout'))
      fittedRef.current = false
    } catch (e) {
      setStatusError(errText(e))
    }
  }, [setStatusError])

  const loadViol = useCallback(async () => {
    try {
      const r = await api.get<ViolationsResp>(
        `/api/violations?kind=${kind}&severity=${severity === 'none' ? 'all' : severity}`
          + `&rule=${encodeURIComponent(rule)}`
          + `&isolation=${isolation ? 1 : 0}`
          + `&search=${encodeURIComponent(search)}`,
      )
      setViol(severity === 'none' ? { ...r, rows: [], filtered: 0 } : r)
    } catch (e) {
      setStatusError(errText(e))
    }
  }, [kind, severity, rule, isolation, search, setStatusError])

  const reload = useCallback(async () => {
    await Promise.all([loadLayout(), loadViol(), loadScanBox()])
  }, [loadLayout, loadViol, loadScanBox])

  useEffect(() => {
    if (active) loadLayout()
  }, [active, revision, loadLayout])

  useEffect(() => {
    if (active) loadViol()
  }, [active, revision, loadViol])

  useEffect(() => {
    if (active) loadScanBox()
  }, [active, revision, loadScanBox])

  // ------------------------------------------------------------ 坐标换算
  const bbox = useCallback((d: LayoutData) => {
    if (d.outline?.length >= 3) {
      const xs = d.outline.map((p) => p[0])
      const ys = d.outline.map((p) => p[1])
      return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] as const
    }
    const padPts = [
      ...(d.pads ?? []).map((p) => ({ x: p.x, y: p.y, r: Math.max(p.w, p.h) / 2 })),
      ...(d.mounts ?? []).map((m) => ({ x: m.x, y: m.y, r: Math.max(m.outer, m.hole) / 2 })),
    ]
    if (padPts.length) {
      const xs = padPts.map((p) => p.x - p.r)
      const ys = padPts.map((p) => p.y - p.r)
      const xe = padPts.map((p) => p.x + p.r)
      const ye = padPts.map((p) => p.y + p.r)
      return [Math.min(...xs), Math.min(...ys), Math.max(...xe), Math.max(...ye)] as const
    }
    return [0, 0, 100, 100] as const
  }, [])

  // ------------------------------------------------------------ 绘制
  const draw = useCallback(() => {
    const canvas = canvasRef.current
    const d = data
    if (!canvas || !d?.scanned) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const cssW = canvas.clientWidth || 900
    const cssH = canvas.clientHeight || 520
    const dpr = window.devicePixelRatio || 1
    if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
      canvas.width = Math.round(cssW * dpr)
      canvas.height = Math.round(cssH * dpr)
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, cssW, cssH)

    const [minX, minY, maxX, maxY] = bbox(d)
    const bw = Math.max(maxX - minX, 1e-3)
    const bh = Math.max(maxY - minY, 1e-3)
    const pad = 18
    if (!fittedRef.current) {
      const s = Math.min((cssW - pad * 2) / bw, (cssH - pad * 2) / bh)
      // 居中（板子通常是扁长形，顶部对齐会让下半屏全空）
      viewRef.current = {
        scale: s,
        ox: cssW / 2 - ((minX + maxX) / 2) * s,
        oy: cssH / 2 + ((minY + maxY) / 2) * s,
      }
      fittedRef.current = true
    }
    const { scale, ox, oy } = viewRef.current
    const X = (x: number) => x * scale + ox
    const Y = (y: number) => oy - y * scale

    // 底色
    ctx.fillStyle = 'rgba(148,163,184,0.06)'
    ctx.fillRect(0, 0, cssW, cssH)

    // 板框
    if (d.outline?.length >= 3) {
      ctx.beginPath()
      d.outline.forEach(([x, y], i) => (i ? ctx.lineTo(X(x), Y(y)) : ctx.moveTo(X(x), Y(y))))
      ctx.closePath()
      ctx.fillStyle = 'rgba(37,99,235,0.05)'
      ctx.fill()
      ctx.strokeStyle = 'rgba(100,116,139,0.8)'
      ctx.lineWidth = 1.2
      ctx.stroke()
    }

    // 挖空
    ctx.strokeStyle = 'rgba(239,68,68,0.5)'
    ctx.setLineDash([3, 3])
    d.cutouts.forEach((c) => {
      if (c.points?.length >= 3) {
        ctx.beginPath()
        c.points.forEach(([x, y], i) => (i ? ctx.lineTo(X(x), Y(y)) : ctx.moveTo(X(x), Y(y))))
        ctx.closePath()
        ctx.stroke()
      } else if (c.w > 0 && c.h > 0) {
        ctx.strokeRect(X(c.x - c.w / 2), Y(c.y + c.h / 2), c.w * scale, c.h * scale)
      }
    })
    ctx.setLineDash([])

    // 焊盘（按类别着色）
    d.pads.forEach((p) => {
      if (layer !== 'all' && p.layer !== layer && p.layer !== 'MULTI') return
      ctx.fillStyle = PAD_COLORS[p.cls] || PAD_COLORS['']
      const w = Math.max(p.w * scale, 1.2)
      const h = Math.max(p.h * scale, 1.2)
      ctx.fillRect(X(p.x) - w / 2, Y(p.y) - h / 2, w, h)
      if (p.hole > 0) {
        ctx.fillStyle = 'rgba(15,23,42,0.55)'
        const r = Math.max((p.hole * scale) / 2, 0.6)
        ctx.beginPath()
        ctx.arc(X(p.x), Y(p.y), r, 0, Math.PI * 2)
        ctx.fill()
      }
    })

    // 安装焊盘：外径圆环 + 内径孔（与原版 board_view 一致；CH* 挖空 / MH* 安装孔统一在此体现）
    ;(d.mounts ?? []).forEach((m) => {
      if (m.enabled === false) return
      if (layer !== 'all' && m.layer !== layer && m.layer !== 'MULTI') return
      const cx = X(m.x)
      const cy = Y(m.y)
      const outer = Math.max(m.outer || 0, m.hole || 0)
      const outerR = (outer * scale) / 2
      if (outerR <= 0) return
      const innerR = ((m.hole || 0) * scale) / 2
      const ring = m.assigned && m.cls ? PAD_COLORS[m.cls] || PAD_COLORS[''] : '#64748b'
      ctx.beginPath()
      ctx.arc(cx, cy, outerR, 0, Math.PI * 2)
      ctx.fillStyle = withAlpha(ring, 0.28)
      ctx.fill()
      ctx.strokeStyle = ring
      ctx.lineWidth = Math.max(outerR - innerR, 1.4)
      ctx.stroke()
      if (innerR > 0.6) {
        ctx.beginPath()
        ctx.arc(cx, cy, innerR, 0, Math.PI * 2)
        ctx.fillStyle = 'rgba(15,23,42,0.92)'
        ctx.fill()
        ctx.strokeStyle = 'rgba(100,116,139,0.9)'
        ctx.lineWidth = 0.8
        ctx.stroke()
      }
    })

    // 器件本体（只在放得下时画）
    if (scale > 3) {
      ctx.strokeStyle = 'rgba(148,163,184,0.55)'
      ctx.lineWidth = 0.8
      d.components.forEach((c) => {
        const box = c.body_bbox?.length === 4 ? c.body_bbox : c.bbox
        if (!box || box.length !== 4) return
        const [x0, y0, x1, y1] = box
        ctx.strokeRect(X(x0), Y(y1), (x1 - x0) * scale, (y1 - y0) * scale)
      })
    }

    // 扫描框：框外压暗，框内青色虚线 + 四角手柄（拖动/缩放时用实线强调）
    if (scanBox) {
      const [bx1, by1, bx2, by2] = scanBox
      const sx = X(bx1)
      const sy = Y(by2)
      const sw = (bx2 - bx1) * scale
      const sh = (by2 - by1) * scale
      ctx.save()
      ctx.beginPath()
      ctx.rect(0, 0, cssW, cssH)
      ctx.rect(sx, sy, sw, sh)
      ctx.fillStyle = 'rgba(2,6,23,0.42)'
      ctx.fill('evenodd')
      ctx.restore()

      ctx.strokeStyle = boxMode ? '#22d3ee' : 'rgba(34,211,238,0.75)'
      ctx.lineWidth = boxMode ? 2 : 1.6
      ctx.setLineDash(boxMode ? [] : [7, 4])
      ctx.strokeRect(sx, sy, sw, sh)
      ctx.setLineDash([])

      const hs = boxMode ? 5 : 3.5
      ctx.fillStyle = '#22d3ee'
      for (const [hx, hy] of [
        [sx, sy],
        [sx + sw, sy],
        [sx, sy + sh],
        [sx + sw, sy + sh],
      ]) {
        ctx.fillRect(hx - hs, hy - hs, hs * 2, hs * 2)
      }

      ctx.fillStyle = 'rgba(34,211,238,0.95)'
      ctx.font = '11px ui-monospace, monospace'
      ctx.fillText(
        `扫描框 ${(bx2 - bx1).toFixed(1)} × ${(by2 - by1).toFixed(1)} mm`,
        sx + 4,
        sy - 6,
      )
    }

    // 违规圈：ERROR 红 / WARNING 黄，数据来自 /api/violations（已按全部维度筛选）
    ;(viol?.rows ?? []).forEach((v) => {
      const isError = v.severity === 'error'
      // 所有圈统一大小，只靠颜色区分，避免缺口大小不同导致圈有大有小
      const r = MARK_RADIUS
      ctx.beginPath()
      ctx.arc(X(v.x), Y(v.y), r, 0, Math.PI * 2)
      ctx.fillStyle = isError ? 'rgba(239,68,68,0.10)' : 'rgba(245,158,11,0.10)'
      ctx.fill()
      ctx.strokeStyle = isError ? 'rgba(239,68,68,0.95)' : 'rgba(245,158,11,0.95)'
      ctx.lineWidth = 1.6
      ctx.stroke()
    })

    // 选区
    if (rect) {
      const [x0, y0, x1, y1] = rect
      ctx.fillStyle = 'rgba(37,99,235,0.12)'
      ctx.fillRect(X(x0), Y(y1), (x1 - x0) * scale, (y1 - y0) * scale)
      ctx.strokeStyle = 'rgba(37,99,235,0.9)'
      ctx.setLineDash([5, 3])
      ctx.strokeRect(X(x0), Y(y1), (x1 - x0) * scale, (y1 - y0) * scale)
      ctx.setLineDash([])
    }

    // 位号
    if (labels && scale > 6) {
      ctx.fillStyle = 'rgba(226,232,240,0.9)'
      ctx.font = '10px ui-monospace, monospace'
      d.components.forEach((c) => ctx.fillText(c.designator, X(c.x) + 3, Y(c.y) - 3))
    }
  }, [data, layer, labels, rect, bbox, viol, scanBox, boxMode])

  useEffect(() => {
    if (!active) return
    const id = window.requestAnimationFrame(draw)
    return () => window.cancelAnimationFrame(id)
  }, [active, draw])

  // ------------------------------------------------------------ 交互
  const toBoard = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current!
    const box = canvas.getBoundingClientRect()
    const px = e.clientX - box.left
    const py = e.clientY - box.top
    const { scale, ox, oy } = viewRef.current
    return [(px - ox) / scale, (oy - py) / scale] as const
  }

  const zoomAt = useCallback(
    (px: number, py: number, deltaY: number) => {
      const v = viewRef.current
      const factor = deltaY < 0 ? 1.15 : 1 / 1.15
      const next = Math.min(Math.max(v.scale * factor, 0.3), 60)
      const k = next / v.scale
      viewRef.current = { scale: next, ox: px - (px - v.ox) * k, oy: py - (py - v.oy) * k }
      draw()
    },
    [draw],
  )

  // React 的 wheel 监听是 passive 的，preventDefault 不生效；用原生非 passive 监听，
  // 鼠标在画布内时只缩放，滚轮不再冒泡去滚动右侧页面。
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const handler = (e: WheelEvent) => {
      e.preventDefault()
      e.stopPropagation()
      const box = canvas.getBoundingClientRect()
      zoomAt(e.clientX - box.left, e.clientY - box.top, e.deltaY)
    }
    canvas.addEventListener('wheel', handler, { passive: false })
    return () => canvas.removeEventListener('wheel', handler)
  }, [zoomAt])

  const onDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    setHover(null)
    const [x, y] = toBoard(e)
    if (selecting) {
      selectRef.current = { x0: x, y0: y }
      setRect([x, y, x, y])
      return
    }
    if (boxMode) {
      const canvas = canvasRef.current!
      const rect = canvas.getBoundingClientRect()
      const px = e.clientX - rect.left
      const py = e.clientY - rect.top
      const v = viewRef.current
      const cur = scanBoxRef.current
      if (cur) {
        const sx = cur[0] * v.scale + v.ox
        const sy = v.oy - cur[3] * v.scale
        const sw = (cur[2] - cur[0]) * v.scale
        const sh = (cur[3] - cur[1]) * v.scale
        const corners: [string, number, number][] = [
          ['nw', sx, sy],
          ['ne', sx + sw, sy],
          ['sw', sx, sy + sh],
          ['se', sx + sw, sy + sh],
        ]
        const corner = corners.find(
          ([, cx, cy]) => Math.abs(cx - px) <= 8 && Math.abs(cy - py) <= 8,
        )
        if (corner) {
          boxDragRef.current = { kind: 'resize', handle: corner[0], box: cur }
          return
        }
        if (px >= sx && px <= sx + sw && py >= sy && py <= sy + sh) {
          boxDragRef.current = { kind: 'move', x0: x, y0: y, box: cur }
          return
        }
      }
      boxDragRef.current = { kind: 'new', x0: x, y0: y }
      applyScanBox([x, y, x, y])
      return
    }
    dragRef.current = { x: e.clientX, y: e.clientY, ox: viewRef.current.ox, oy: viewRef.current.oy }
  }

  const onMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const boxDrag = boxDragRef.current
    if (boxDrag) {
      const [x, y] = toBoard(e)
      if (boxDrag.kind === 'new') {
        applyScanBox([
          Math.min(boxDrag.x0, x),
          Math.min(boxDrag.y0, y),
          Math.max(boxDrag.x0, x),
          Math.max(boxDrag.y0, y),
        ])
      } else if (boxDrag.kind === 'move') {
        const dx = x - boxDrag.x0
        const dy = y - boxDrag.y0
        const [bx1, by1, bx2, by2] = boxDrag.box
        applyScanBox([bx1 + dx, by1 + dy, bx2 + dx, by2 + dy])
      } else {
        const [bx1, by1, bx2, by2] = boxDrag.box
        let nx1 = bx1
        let ny1 = by1
        let nx2 = bx2
        let ny2 = by2
        if (boxDrag.handle.includes('w')) nx1 = x
        if (boxDrag.handle.includes('e')) nx2 = x
        if (boxDrag.handle.includes('s')) ny1 = y
        if (boxDrag.handle.includes('n')) ny2 = y
        applyScanBox([
          Math.min(nx1, nx2),
          Math.min(ny1, ny2),
          Math.max(nx1, nx2),
          Math.max(ny1, ny2),
        ])
      }
      return
    }
    if (selectRef.current) {
      const [x, y] = toBoard(e)
      const { x0, y0 } = selectRef.current
      setRect([Math.min(x0, x), Math.min(y0, y), Math.max(x0, x), Math.max(y0, y)])
      return
    }
    const drag = dragRef.current
    if (!drag) {
      // 悬停命中：找鼠标下最近的违规圈（半径算法与 draw 保持一致）
      const canvas = canvasRef.current!
      const box = canvas.getBoundingClientRect()
      const px = e.clientX - box.left
      const py = e.clientY - box.top
      const v = viewRef.current
      let hit: HoverInfo | null = null
      for (const mark of viol?.rows ?? []) {
        const r = MARK_RADIUS
        const dx = mark.x * v.scale + v.ox - px
        const dy = v.oy - mark.y * v.scale - py
        if (dx * dx + dy * dy <= (r + 4) * (r + 4)) {
          hit = { kind: 'violation', px, py, v: mark }
          break
        }
      }
      if (!hit) {
        for (const m of data?.mounts ?? []) {
          if (m.enabled === false) continue
          if (layer !== 'all' && m.layer !== layer && m.layer !== 'MULTI') continue
          const outer = Math.max(m.outer || 0, m.hole || 0)
          const outerR = (outer * v.scale) / 2
          const dx = m.x * v.scale + v.ox - px
          const dy = v.oy - m.y * v.scale - py
          if (outerR > 0 && dx * dx + dy * dy <= (outerR + 3) * (outerR + 3)) {
            hit = { kind: 'mount', px, py, m }
            break
          }
        }
      }
      setHover(hit)
      return
    }
    setHover(null)
    viewRef.current = {
      ...viewRef.current,
      ox: drag.ox + (e.clientX - drag.x),
      oy: drag.oy + (e.clientY - drag.y),
    }
    draw()
  }

  const onUp = () => {
    setHover(null)
    if (boxDragRef.current) {
      boxDragRef.current = null
      const box = scanBoxRef.current
      if (box) {
        const w = box[2] - box[0]
        const h = box[3] - box[1]
        if (w < 1 || h < 1) {
          toast.warning('扫描框太小（至少 1×1 mm），请重新框选')
          applyScanBox(null)
        } else {
          void saveScanBox(box)
        }
      }
      return
    }
    dragRef.current = null
    if (selectRef.current) {
      selectRef.current = null
      setSelecting(false)
      setStatus('选区已就绪，点「求解选区」')
    }
  }

  /** 双击扫描框内部：只对框内区域重扫 PCB。 */
  const onDoubleClick = async (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (boxMode) return
    const box = scanBoxRef.current
    if (!box) return
    const [x, y] = toBoard(e)
    if (x < box[0] || x > box[2] || y < box[1] || y > box[3]) return
    setBusy(true)
    try {
      await api.post('/api/scan-pcb', { use_scan_box: true, mode: 'local' })
      setStatus('正在局部刷新扫描框内器件，完成后自动重新校验…')
      toast.success('已提交：只刷新框内器件，完成后自动重新校验')
    } catch (err) {
      const msg = errText(err)
      setStatusError(msg)
      toast.danger(msg)
    } finally {
      setBusy(false)
    }
  }

  // ------------------------------------------------------------ 动作
  const onSolve = async () => {
    if (!rect) {
      toast.warning('请先点「求解框」并在图上拖出矩形')
      return
    }
    setBusy(true)
    try {
      const r = await api.post<{ rows: SolveRow[]; movable: number; elapsed: number }>('/api/solve', {
        rect,
      })
      setPlans(r.rows || [])
      setStatus(`求解完成：${r.movable} 个器件可移动（${r.elapsed}s）`)
      toast.success(`求解完成：${r.movable} 个可移动`)
    } catch (e) {
      const msg = errText(e)
      setStatusError(msg)
      toast.danger(msg)
    } finally {
      setBusy(false)
    }
  }

  const onApply = async () => {
    setBusy(true)
    try {
      const r = await api.post<{ moved: number; backup: string }>('/api/apply-moves')
      setStatus(`已移动 ${r.moved} 个器件，备份在 ${r.backup}`)
      toast.success(`已移动 ${r.moved} 个器件`)
      await reload()
    } catch (e) {
      const msg = errText(e)
      setStatusError(msg)
      toast.danger(msg)
    } finally {
      setBusy(false)
    }
  }

  const onImportHtml = async () => {
    try {
      const d = await api.post<{ path: string; canceled: boolean }>('/api/dialog/open', {
        kind: 'html',
      })
      if (d.canceled || !d.path) return
      const r = await api.post<{ points: number }>('/api/outline-html', { path: d.path })
      toast.success(`板框已导入（${r.points} 点）`)
      await loadLayout()
    } catch (e) {
      const msg = errText(e)
      setStatusError(msg)
      toast.danger(msg)
    }
  }

  const columns: TableColumn<SolveRow>[] = [
    {
      key: 'designator',
      header: '位号',
      width: 90,
      render: (r) => <span className="cg-mono">{r.designator}</span>,
    },
    {
      key: 'from',
      header: '原位置',
      width: 140,
      render: (r) => (
        <span className="cg-num">
          {r.x0.toFixed(2)}, {r.y0.toFixed(2)}
        </span>
      ),
    },
    {
      key: 'to',
      header: '新位置',
      width: 140,
      render: (r) => (
        <span className="cg-num">
          {r.moved ? `${r.x1.toFixed(2)}, ${r.y1.toFixed(2)}` : '—'}
        </span>
      ),
    },
    {
      key: 'shift',
      header: '位移 mm',
      width: 90,
      align: 'end',
      render: (r) => <span className="cg-num">{r.shift_mm.toFixed(2)}</span>,
    },
    {
      key: 'result',
      header: '结果',
      width: '26%',
      render: (r) =>
        r.moved ? (
          <span style={{ color: 'var(--ok-success)' }}>可移动</span>
        ) : (
          <span className="hint">{r.reason || '无可行解'}</span>
        ),
    },
  ]

  const rows = viol?.rows ?? []
  const errCount = rows.filter((v) => v.severity === 'error').length
  const warnCount = rows.length - errCount
  const filterActive = Boolean(rule || isolation || search || !showError || !showWarning)

  if (!data?.scanned) {
    return (
      <Page size="full">
        <PageHeader title="布局视图" />
        <Card>
          <CardBody>
            <EmptyState
              size="sm"
              title="还没有板数据"
              description="先扫描工程（或载入示例）后这里会画出板框、焊盘与违规位置。"
            />
          </CardBody>
        </Card>
      </Page>
    )
  }

  return (
    <Page size="full">
      <PageHeader
        title="布局视图"
        description="按电压类别给焊盘着色（橙=原边恒定 / 红=原边跳变 / 青=副边），安装焊盘画成同心圆环（外圈类别色 / 内孔深色），红圈标违规；滚轮缩放、拖动平移"
      />

      <div className="pg-col">
        <Card>
          <CardHeader
            title={data.board_name || 'PCB'}
            description={`${data.components.length} 器件 · ${data.pads.length} 焊盘 · ${data.mounts?.length ?? 0} 安装焊盘 · 违规标记 ${viol?.filtered ?? 0} / ${viol?.total ?? 0}`
              + (kind === 'pad' ? '（焊盘级）' : '')
              + (!showError ? ' · 已隐藏 ERROR' : '')
              + (!showWarning ? ' · 已隐藏 WARNING' : '')}
            actions={
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <Segmented
                  size="sm"
                  value={layer}
                  options={[
                    { value: 'all', label: '全部层' },
                    { value: 'TOP', label: '顶层' },
                    { value: 'BOTTOM', label: '底层' },
                  ]}
                  onChange={(v) => setLayer(v as 'all' | 'TOP' | 'BOTTOM')}
                />
                <Button size="sm" variant={labels ? 'soft' : 'ghost'} onClick={() => setLabels((v) => !v)}>
                  位号
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    fittedRef.current = false
                    draw()
                  }}
                >
                  适应窗口
                </Button>
                <Button
                  size="sm"
                  variant={selecting ? 'primary' : 'ghost'}
                  onClick={() => {
                    setSelecting(true)
                    setBoxMode(false)
                    setStatus('在图上拖出矩形选区')
                  }}
                >
                  求解框
                </Button>
                <Button size="sm" variant="accent" onClick={onSolve} loading={busy} disabled={!rect}>
                  求解选区
                </Button>
                <Button
                  size="sm"
                  variant={boxMode ? 'primary' : 'ghost'}
                  onClick={() => {
                    setBoxMode((v) => !v)
                    setSelecting(false)
                    setStatus(
                      boxMode
                        ? '已退出扫描框'
                        : '扫描框：在图上拖动绘制；拖框内移动，拖四角调整',
                    )
                  }}
                >
                  扫描框
                </Button>
                {scanBox && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      void saveScanBox(null)
                      setBoxMode(false)
                    }}
                  >
                    清除扫描框
                  </Button>
                )}
                <Button size="sm" variant="ghost" onClick={onImportHtml}>
                  导入板框HTML…
                </Button>
              </div>
            }
          />
          <CardBody>
            <div className="toolbar" style={{ marginBottom: 10 }}>
              <div className="toolbar__group">
                <Segmented
                  size="sm"
                  value={kind}
                  options={[
                    { value: 'component', label: '器件级' },
                    { value: 'pad', label: '焊盘级' },
                  ]}
                  onChange={(v) => setKind(v as 'component' | 'pad')}
                />
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    fontSize: 12,
                    whiteSpace: 'nowrap',
                    color: 'var(--ok-danger)',
                  }}
                  title="不同器件焊盘之间的违规（红圈）"
                >
                  <Switch checked={showError} onChange={setShowError} />
                  ERROR
                </span>
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    fontSize: 12,
                    whiteSpace: 'nowrap',
                    color: 'var(--ok-warning)',
                  }}
                  title="同一器件内部焊盘之间的违规（黄圈）"
                >
                  <Switch checked={showWarning} onChange={setShowWarning} />
                  WARNING
                </span>
                <Select
                  size="sm"
                  value={rule}
                  options={[
                    { value: '', label: '全部违规项' },
                    ...(viol?.rules ?? []).map((r) => ({
                      value: r.key,
                      label: `${r.label}（${r.count}）`,
                    })),
                  ]}
                  onChange={setRule}
                />
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    fontSize: 12,
                    whiteSpace: 'nowrap',
                    color: 'var(--ok-text-2)',
                  }}
                  title="只看原边 ↔ 副边的组合（隔离带）"
                >
                  <Switch checked={isolation} onChange={setIsolation} />
                  只看隔离带
                </span>
                <div style={{ width: 180 }}>
                  <Input size="sm" value={search} placeholder="搜索位号 / 网络" onChange={setSearch} />
                </div>
                {filterActive && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setRule('')
                      setIsolation(false)
                      setSearch('')
                      setShowError(true)
                      setShowWarning(true)
                    }}
                  >
                    清除筛选
                  </Button>
                )}
              </div>
            </div>
            <div className="canvas-wrap">
              <canvas
                ref={canvasRef}
                style={{ height: 'clamp(320px, 58vh, 760px)', touchAction: 'none' }}
                onMouseDown={onDown}
                onMouseMove={onMove}
                onMouseUp={onUp}
                onMouseLeave={onUp}
                onDoubleClick={onDoubleClick}
              />
              {hover && (
                <div
                  className="cg-hover"
                  style={{
                    left: Math.min(hover.px + 16, 560),
                    top: Math.max(hover.py - 12, 6),
                  }}
                >
                  {hover.kind === 'mount' ? (
                    <>
                      <strong>安装焊盘 {hover.m.name || '—'}</strong>
                      <span>
                        外径 {hover.m.outer.toFixed(2)} mm / 内径 {hover.m.hole.toFixed(2)} mm
                      </span>
                      <span>
                        {hover.m.cls_label || hover.m.cls || '未分类'} · 网络 {hover.m.net || '—'}
                      </span>
                      <span className="cg-hover__foot">
                        {hover.m.enabled ? '已启用' : '已停用'}
                        {' · '}
                        {hover.m.layer}
                      </span>
                    </>
                  ) : (
                    <>
                      <strong>
                        {hover.v.designator_a ?? hover.v.item_a} ↔ {hover.v.designator_b ?? hover.v.item_b}
                      </strong>
                      <span>
                        {hover.v.net_a || '—'} / {hover.v.net_b || '—'}
                      </span>
                      <span>
                        {hover.v.class_a_label || hover.v.class_a || '—'} ↔{' '}
                        {hover.v.class_b_label || hover.v.class_b || '—'}
                      </span>
                      <span>
                        实测 {hover.v.actual_mm} · 要求 {hover.v.required_mm} · 缺口 {hover.v.deficit_mm} mm
                      </span>
                      <span>{hover.v.rule_name || '—'}</span>
                      <span className="cg-hover__foot">
                        {hover.v.severity === 'error' ? 'ERROR' : 'WARNING'}
                        {hover.v.count ? ` · ${hover.v.count} 对焊盘` : ''}
                      </span>
                    </>
                  )}
                </div>
              )}
            </div>
            <div className="stack-gap" />
            <div className="canvas-legend">
              <span>
                <i style={{ background: PAD_COLORS.PRIMARY_CONST }} />
                原边·恒定
              </span>
              <span>
                <i style={{ background: PAD_COLORS.PRIMARY_SWITCHING }} />
                原边·跳变
              </span>
              <span>
                <i style={{ background: PAD_COLORS.SECONDARY }} />
                副边
              </span>
              <span>
                <i style={{ background: 'transparent', border: '1.5px solid #ef4444' }} />
                ERROR 违规（{errCount}）
              </span>
              <span>
                <i style={{ background: 'transparent', border: '1.5px solid #f59e0b' }} />
                WARNING 违规（{warnCount}）
              </span>
              <span>
                <i style={{ background: '#475569', borderRadius: '50%' }} />
                安装焊盘（{(data.mounts ?? []).filter((m) => m.enabled !== false).length}）
              </span>
              <span className="hint">
                {scanBox
                  ? `扫描框：(${scanBox[0].toFixed(1)}, ${scanBox[1].toFixed(1)}) ~ (${scanBox[2].toFixed(1)}, ${scanBox[3].toFixed(1)}) mm · ${(scanBox[2] - scanBox[0]).toFixed(1)} × ${(scanBox[3] - scanBox[1]).toFixed(1)} mm · 双击框内按区域重扫`
                  : rect
                    ? `选区：(${rect[0].toFixed(1)}, ${rect[1].toFixed(1)}) ~ (${rect[2].toFixed(1)}, ${rect[3].toFixed(1)}) mm`
                    : '提示：点「扫描框」可在图上框选区域；点「求解框」用于拖出求解选区'}
              </span>
            </div>
          </CardBody>
        </Card>

        {plans.length > 0 && (
          <Card>
            <CardHeader
              title="求解方案"
              description={`${plans.filter((p) => p.moved).length} / ${plans.length} 个器件可移动（单器件位移上限 1 mm）`}
              actions={
                <Button
                  size="sm"
                  variant="accent"
                  onClick={onApply}
                  loading={busy}
                  disabled={!plans.some((p) => p.moved)}
                >
                  应用移动（先备份工程）
                </Button>
              }
            />
            <CardBody>
              <Table
                className="cg-table"
                label="求解方案"
                columns={columns}
                data={plans}
                rowKey="designator"
                scroll
                maxHeight="min(40vh, 520px)"
              />
              <div className="stack-gap" />
              <Text variant="caption" tone="subtle">
                应用前会把工程文件备份到 %APPDATA%\ClearanceGuard\backup\&lt;时间戳&gt;\pcb\，
                回滚时按原结构拷回即可。
              </Text>
            </CardBody>
          </Card>
        )}
      </div>

    </Page>
  )
}
