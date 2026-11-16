import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  Dialog,
  EmptyState,
  Field,
  Input,
  Page,
  PageHeader,
  Segmented,
  Switch,
  Table,
  Text,
  toast,
} from 'oink-ui'
import type { TableColumn } from 'oink-ui'

import { api, errText } from '../api'
import type { LayoutData, PageProps, SolveRow } from '../types'

const PAD_COLORS: Record<string, string> = {
  PRIMARY_CONST: '#f59e0b',
  PRIMARY_SWITCHING: '#ef4444',
  SECONDARY: '#06b6d4',
  '': '#94a3b8',
}

interface View {
  scale: number
  ox: number
  oy: number
}

export default function LayoutPage({ setStatus, setStatusError, active, revision }: PageProps) {
  const [data, setData] = useState<LayoutData | null>(null)
  const [layer, setLayer] = useState<'all' | 'TOP' | 'BOTTOM'>('all')
  const [labels, setLabels] = useState(false)
  // 违规圈的级别筛选（对应原版 board_view.set_severity_filter + chk_error/chk_warning）
  const [showError, setShowError] = useState(true)
  const [showWarning, setShowWarning] = useState(true)
  const [selecting, setSelecting] = useState(false)
  const [rect, setRect] = useState<[number, number, number, number] | null>(null)
  const [plans, setPlans] = useState<SolveRow[]>([])
  const [busy, setBusy] = useState(false)

  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const viewRef = useRef<View>({ scale: 1, ox: 0, oy: 0 })
  const dragRef = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null)
  const selectRef = useRef<{ x0: number; y0: number } | null>(null)
  const fittedRef = useRef(false)

  const load = useCallback(async () => {
    try {
      const r = await api.get<LayoutData>('/api/layout')
      setData(r)
      fittedRef.current = false
    } catch (e) {
      setStatusError(errText(e))
    }
  }, [setStatusError])

  useEffect(() => {
    if (active) load()
  }, [active, revision, load])

  // ------------------------------------------------------------ 坐标换算
  const bbox = useCallback((d: LayoutData) => {
    if (d.outline?.length >= 3) {
      const xs = d.outline.map((p) => p[0])
      const ys = d.outline.map((p) => p[1])
      return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] as const
    }
    if (d.pads?.length) {
      const xs = d.pads.map((p) => p.x)
      const ys = d.pads.map((p) => p.y)
      return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] as const
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

    // 违规圈：ERROR 红 / WARNING 黄，按开关过滤
    d.violations.forEach((v) => {
      const isError = v.severity === 'error'
      if (isError && !showError) return
      if (!isError && !showWarning) return
      // 半径按缺口放大，但给下限：0.2 mm 的缺口在整板缩放下不到 1px，不设下限就完全看不见
      const r = Math.max(9, Math.min(v.deficit * scale * 5, 44))
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
  }, [data, layer, labels, rect, bbox, showError, showWarning])

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

  const onWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    const canvas = canvasRef.current!
    const box = canvas.getBoundingClientRect()
    const px = e.clientX - box.left
    const py = e.clientY - box.top
    const v = viewRef.current
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15
    const next = Math.min(Math.max(v.scale * factor, 0.3), 60)
    const k = next / v.scale
    viewRef.current = { scale: next, ox: px - (px - v.ox) * k, oy: py - (py - v.oy) * k }
    draw()
  }

  const onDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const [x, y] = toBoard(e)
    if (selecting) {
      selectRef.current = { x0: x, y0: y }
      setRect([x, y, x, y])
      return
    }
    dragRef.current = { x: e.clientX, y: e.clientY, ox: viewRef.current.ox, oy: viewRef.current.oy }
  }

  const onMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (selectRef.current) {
      const [x, y] = toBoard(e)
      const { x0, y0 } = selectRef.current
      setRect([Math.min(x0, x), Math.min(y0, y), Math.max(x0, x), Math.max(y0, y)])
      return
    }
    const drag = dragRef.current
    if (!drag) return
    viewRef.current = {
      ...viewRef.current,
      ox: drag.ox + (e.clientX - drag.x),
      oy: drag.oy + (e.clientY - drag.y),
    }
    draw()
  }

  const onUp = () => {
    dragRef.current = null
    if (selectRef.current) {
      selectRef.current = null
      setSelecting(false)
      setStatus('选区已就绪，点「求解选区」')
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
      await load()
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
      await load()
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

  const errCount = (data?.violations ?? []).filter((v) => v.severity === 'error').length
  const warnCount = (data?.violations ?? []).length - errCount

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
        description="按电压类别给焊盘着色（橙=原边恒定 / 红=原边跳变 / 青=副边），红圈标违规；滚轮缩放、拖动平移"
      />

      <div className="pg-col">
        <Card>
          <CardHeader
            title={data.board_name || 'PCB'}
            description={`${data.components.length} 器件 · ${data.pads.length} 焊盘 · ${data.violations.length} 违规位置`
              + (showError ? '' : ' · 已隐藏 ERROR')
              + (showWarning ? '' : ' · 已隐藏 WARNING')}
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
                    setStatus('在图上拖出矩形选区')
                  }}
                >
                  求解框
                </Button>
                <Button size="sm" variant="accent" onClick={onSolve} loading={busy} disabled={!rect}>
                  求解选区
                </Button>
                <Button size="sm" variant="ghost" onClick={onImportHtml}>
                  导入板框HTML…
                </Button>
              </div>
            }
          />
          <CardBody>
            <div className="canvas-wrap">
              <canvas
                ref={canvasRef}
                style={{ height: 'clamp(320px, 58vh, 760px)' }}
                onWheel={onWheel}
                onMouseDown={onDown}
                onMouseMove={onMove}
                onMouseUp={onUp}
                onMouseLeave={onUp}
              />
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
              <span className="hint">
                {rect
                  ? `选区：(${rect[0].toFixed(1)}, ${rect[1].toFixed(1)}) ~ (${rect[2].toFixed(1)}, ${rect[3].toFixed(1)}) mm`
                  : '提示：先点「求解框」，在图上拖出矩形，再点「求解选区」'}
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
