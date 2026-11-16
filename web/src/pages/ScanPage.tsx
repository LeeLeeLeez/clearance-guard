import { useCallback, useEffect, useMemo, useState } from 'react'
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
  Tabs,
  Text,
  toast,
} from 'oink-ui'
import type { TableColumn } from 'oink-ui'

import { api, errText } from '../api'
import type {
  ComponentRow,
  ComponentsResp,
  CutoutRow,
  FloatingRow,
  MountRow,
  NetClassOption,
  NetRow,
  NetsResp,
  ObjectsResp,
  PageProps,
} from '../types'

type SubTab = 'log' | 'components' | 'nets' | 'mounts' | 'floating'

export default function ScanPage({ setStatus, setStatusError, active, revision, onRefreshGlobal }: PageProps) {
  const [sub, setSub] = useState<SubTab>('nets')

  return (
    <Page size="full">
      <PageHeader title="工程与扫描" description="连接 Altium → 扫描工程 → 核对电压类别 → 配置规则 → 校验避距" />
      <Tabs
        label="子页"
        value={sub}
        onChange={(v) => setSub(v as SubTab)}
        items={[
          {
            value: 'log',
            label: '扫描日志',
            content: <LogPanel active={active && sub === 'log'} />,
          },
          {
            value: 'components',
            label: '器件列表',
            content: (
              <ComponentsPanel
                {...{ setStatus, setStatusError, active, revision, onRefreshGlobal }}
              />
            ),
          },
          {
            value: 'nets',
            label: '网络电压分类',
            content: <NetsPanel {...{ setStatus, setStatusError, active, revision, onRefreshGlobal }} />,
          },
          {
            value: 'mounts',
            label: '安装焊盘',
            content: <MountsPanel {...{ setStatus, setStatusError, active, revision, onRefreshGlobal }} />,
          },
          {
            value: 'floating',
            label: '无网络器件',
            content: <FloatingPanel {...{ setStatus, setStatusError, active, revision, onRefreshGlobal }} />,
          },
        ]}
      />
    </Page>
  )
}

// ---------------------------------------------------------------- 日志
function LogPanel({ active }: { active: boolean }) {
  const [logs, setLogs] = useState<string[]>([])

  useEffect(() => {
    if (!active) return
    let alive = true
    const tick = async () => {
      try {
        const r = await api.get<{ logs: string[] }>('/api/task')
        if (alive) setLogs(r.logs || [])
      } catch {
        /* ignore */
      }
    }
    tick()
    const id = window.setInterval(tick, 1500)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [active])

  return (
    <Card>
      <CardHeader title="扫描日志" description={`${logs.length} 行`} />
      <CardBody>
        <pre className="log ok-scroll">{logs.join('\n')}</pre>
      </CardBody>
    </Card>
  )
}

// ---------------------------------------------------------------- 器件列表
function ComponentsPanel({ setStatus, setStatusError, active, revision }: PageProps) {
  const [data, setData] = useState<ComponentsResp | null>(null)
  const [search, setSearch] = useState('')

  const load = useCallback(async () => {
    try {
      setData(await api.get<ComponentsResp>(`/api/components?q=${encodeURIComponent(search)}`))
    } catch (e) {
      setStatusError(errText(e))
    }
  }, [search, setStatusError])

  useEffect(() => {
    if (!active) return
    const t = window.setTimeout(load, search ? 250 : 0)
    return () => window.clearTimeout(t)
  }, [active, revision, load, search])

  const setCategory = async (designator: string, category: string) => {
    try {
      await api.post('/api/component', { designator, patch: { category } })
      await load()
      setStatus(`${designator} 类别改为 ${category === 'burn_terminal' ? '烧录端子' : '普通器件'}`)
    } catch (e) {
      setStatusError(errText(e))
    }
  }

  const columns: TableColumn<ComponentRow>[] = [
    {
      key: 'designator',
      header: '位号',
      width: 92,
      render: (r) => <span className="cg-mono">{r.designator}</span>,
    },
    { key: 'comment', header: '注释', width: '18%', render: (r) => r.comment || '—' },
    { key: 'footprint', header: '封装', width: '16%', hideBelow: 'md', render: (r) => r.footprint || '—' },
    {
      key: 'category',
      header: '类别',
      width: 132,
      render: (r) => (
        <Select
          size="sm"
          value={r.category}
          options={(data?.categories ?? []).map((c) => ({ value: c.value, label: c.label }))}
          onChange={(v) => setCategory(r.designator, v)}
        />
      ),
    },
    { key: 'layer', header: '层', width: 84, hideBelow: 'sm' },
    { key: 'x', header: 'X (mm)', width: 88, align: 'end', render: (r) => r.x.toFixed(2) },
    { key: 'y', header: 'Y (mm)', width: 88, align: 'end', render: (r) => r.y.toFixed(2) },
    {
      key: 'rotation',
      header: '旋转',
      width: 72,
      align: 'end',
      hideBelow: 'lg',
      render: (r) => `${Math.round(r.rotation)}°`,
    },
    { key: 'pad_count', header: '焊盘数', width: 84, align: 'end', hideBelow: 'sm' },
  ]

  return (
    <Card>
      <CardHeader
        title="器件列表"
        description={
          data ? `${data.rows.length} / ${data.total} 个器件（可改类别：烧录端子等）` : '尚未扫描'
        }
        actions={
          <div style={{ width: 260 }}>
            <Input
              size="sm"
              value={search}
              placeholder="搜索位号 / 注释 / 封装，如 T21、aux_core、QFN"
              onChange={setSearch}
            />
          </div>
        }
      />
      <CardBody>
        {data?.rows?.length ? (
          <Table
            className="cg-table"
            label="器件列表"
            columns={columns}
            data={data.rows}
            rowKey="designator"
            scroll
            maxHeight="min(56vh, 760px)"
          />
        ) : (
          <EmptyState size="sm" title="没有器件" description="扫描工程后这里会列出板上所有器件。" />
        )}
      </CardBody>
    </Card>
  )
}

// ---------------------------------------------------------------- 分类
function NetsPanel({ setStatus, setStatusError, active, revision }: PageProps) {
  const [data, setData] = useState<NetsResp | null>(null)
  const [view, setView] = useState<'sheet' | 'all'>('sheet')
  const [sheet, setSheet] = useState('common')
  const [side, setSide] = useState<'power' | 'control'>('power')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const r = await api.get<NetsResp>('/api/nets')
      setData(r)
    } catch (e) {
      setStatusError(errText(e))
    }
  }, [setStatusError])

  useEffect(() => {
    if (active) load()
  }, [active, revision, load])

  const nets = data?.nets ?? []
  const classes = data?.classes ?? []

  const sheetNames = useMemo(() => {
    const names = new Set<string>()
    nets.forEach((n) => n.sheets?.forEach((s) => names.add(s)))
    return ['common', ...Array.from(names).sort()]
  }, [nets])

  const rows = useMemo(() => {
    if (view === 'all') return nets
    if (sheet === 'common') {
      return nets.filter((n) => n.shared)
    }
    return nets.filter((n) => (n.sheets || []).includes(sheet) && !n.shared)
  }, [nets, view, sheet])

  const shown = useMemo(() => {
    if (view !== 'sheet' || sheet !== 'common') return rows
    return rows.filter((n) => (n.sheet_side || 'control') === side)
  }, [rows, view, sheet, side])

  const patchNet = async (name: string, patch: Record<string, unknown>) => {
    try {
      const updated = await api.post<NetRow>('/api/net', { name, patch })
      setData((cur) =>
        cur
          ? { ...cur, nets: cur.nets.map((n) => (n.net === name ? { ...n, ...updated } : n)) }
          : cur,
      )
      setStatus(`已更新 ${name}`)
    } catch (e) {
      const msg = errText(e)
      setStatusError(msg)
      toast.danger(msg)
    }
  }

  const onReclassify = async () => {
    setBusy(true)
    try {
      await api.post('/api/reclassify')
      await load()
      toast.success('已重新自动分类')
    } catch (e) {
      setStatusError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  const onSheetSecondary = async () => {
    setBusy(true)
    try {
      const r = await api.post<{ changed: number }>('/api/sheet/secondary', { sheet })
      await load()
      toast.success(`已把 ${r.changed} 个网络设为副边`)
      onRefresh()
    } catch (e) {
      setStatusError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  const onRoute = async (target: 'power' | 'control') => {
    const names = shown.map((n) => n.net)
    if (!names.length) return
    setBusy(true)
    try {
      await api.post('/api/common/route', { names, side: target })
      await load()
      toast.success(`已移到${target === 'power' ? '功率侧' : '控制侧'}`)
    } catch (e) {
      setStatusError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  const onRefresh = () => {
    /* 触发 App 重新拉状态（违规数等） */
    load()
  }

  const columns: TableColumn<NetRow>[] = [
    {
      key: 'net',
      header: '网络名',
      width: '16%',
      render: (r) => (
        <span className="cg-mono">
          <i className="cg-dot" data-cls={r.net_class} />
          {r.net}
        </span>
      ),
    },
    {
      key: 'net_class',
      header: '电压类别',
      width: 148,
      render: (r) => (
        <Select
          size="sm"
          value={r.net_class}
          options={classes.map((c: NetClassOption) => ({ value: c.value, label: c.label }))}
          onChange={(v) => patchNet(r.net, { net_class: v })}
        />
      ),
    },
    {
      key: 'voltage',
      header: '电压 V',
      width: 84,
      render: (r) => (
        <Input
          size="sm"
          value={r.voltage === null || r.voltage === undefined ? '' : String(r.voltage)}
          onChange={() => undefined}
          onBlur={(e) => {
            const v = (e.target as HTMLInputElement).value.trim()
            if (v !== String(r.voltage ?? '')) patchNet(r.net, { voltage: v === '' ? null : v })
          }}
        />
      ),
    },
    {
      key: 'voltage_min',
      header: '下限 V',
      width: 80,
      hideBelow: 'xl',
      render: (r) => (
        <Input
          size="sm"
          value={r.voltage_min === null || r.voltage_min === undefined ? '' : String(r.voltage_min)}
          onChange={() => undefined}
          onBlur={(e) => {
            const v = (e.target as HTMLInputElement).value.trim()
            if (v !== String(r.voltage_min ?? '')) patchNet(r.net, { voltage_min: v === '' ? null : v })
          }}
        />
      ),
    },
    {
      key: 'voltage_max',
      header: '上限 V',
      width: 80,
      hideBelow: 'xl',
      render: (r) => (
        <Input
          size="sm"
          value={r.voltage_max === null || r.voltage_max === undefined ? '' : String(r.voltage_max)}
          onChange={() => undefined}
          onBlur={(e) => {
            const v = (e.target as HTMLInputElement).value.trim()
            if (v !== String(r.voltage_max ?? '')) patchNet(r.net, { voltage_max: v === '' ? null : v })
          }}
        />
      ),
    },
    {
      key: 'ref_ground',
      header: '参考地',
      width: 108,
      render: (r) => (
        <Input
          size="sm"
          value={r.ref_ground || ''}
          onChange={() => undefined}
          onBlur={(e) => {
            const v = (e.target as HTMLInputElement).value.trim()
            if (v !== (r.ref_ground || '')) patchNet(r.net, { ref_ground: v })
          }}
        />
      ),
    },
    {
      key: 'ref_ground_voltage',
      header: '参考地电压',
      width: 112,
      hideBelow: 'md',
      render: (r) => <span className="cg-num">{r.ref_ground_voltage || '—'}</span>,
    },
    {
      key: 'follow_net',
      header: '跟随网络',
      width: 116,
      hideBelow: 'xl',
      render: (r) => (
        <Input
          size="sm"
          value={r.follow_net || ''}
          onChange={() => undefined}
          onBlur={(e) => {
            const v = (e.target as HTMLInputElement).value.trim()
            if (v !== (r.follow_net || '')) patchNet(r.net, { follow_net: v })
          }}
        />
      ),
    },
    {
      key: 'confidence',
      header: '置信度',
      width: 76,
      align: 'end',
      hideBelow: 'md',
      render: (r) =>
        r.user_edited ? (
          <span title={`自动置信度 ${Math.round((r.confidence || 0) * 100)}%`} style={{ color: 'var(--ok-success)' }}>
            ✓ 人工
          </span>
        ) : (
          <span className="cg-num">{Math.round((r.confidence || 0) * 100)}%</span>
        ),
    },
    {
      key: 'reasons',
      header: '判定依据',
      width: '20%',
      hideBelow: 'xl',
      render: (r) => (
        <span className="hint" title={(r.reasons || []).join('；')}>
          {(r.reasons || []).join('；').slice(0, 60)}
        </span>
      ),
    },
  ]

  if (!data?.scanned) {
    return (
      <Card>
        <CardBody>
          <EmptyState
            size="sm"
            title="还没有扫描结果"
            description="在顶部工具条连接 Altium 后点「扫描工程」，或「示例工程」先体验流程。"
          />
        </CardBody>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader
        title="网络电压分类"
        description={`共 ${nets.length} 个网络 · 跨页 ${nets.filter((n) => n.shared).length} 个`}
        actions={
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Segmented
              size="sm"
              value={view}
              options={[
                { value: 'sheet', label: '按原理图图纸' },
                { value: 'all', label: '全表编辑与确认' },
              ]}
              onChange={(v) => setView(v as 'sheet' | 'all')}
            />
            <Button size="sm" variant="ghost" onClick={onReclassify} loading={busy}>
              重新自动分类
            </Button>
          </div>
        }
      />
      <CardBody>
        {view === 'sheet' && (
          <>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <Tabs
                label="图纸"
                value={sheet}
                onChange={(v) => setSheet(v)}
                items={sheetNames.map((name) => ({ value: name, label: name, content: null }))}
              />
            </div>
            <div className="stack-gap" />
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              {sheet === 'common' ? (
                <>
                  <Segmented
                    size="sm"
                    value={side}
                    options={[
                      { value: 'power', label: `功率侧 (${rows.filter((r) => (r.sheet_side || 'control') === 'power').length})` },
                      { value: 'control', label: `控制侧 (${rows.filter((r) => (r.sheet_side || 'control') !== 'power').length})` },
                    ]}
                    onChange={(v) => setSide(v as 'power' | 'control')}
                  />
                  <Button size="sm" variant="soft" onClick={() => onRoute('power')} disabled={busy}>
                    移到功率侧
                  </Button>
                  <Button size="sm" variant="soft" onClick={() => onRoute('control')} disabled={busy}>
                    移到控制侧
                  </Button>
                </>
              ) : (
                <>
                  <Text variant="caption" tone="subtle">
                    本页专属 {rows.length} 个网络（跨页网络见 common）
                  </Text>
                  <Button size="sm" variant="soft" onClick={onSheetSecondary} loading={busy}>
                    整页设为副边
                  </Button>
                </>
              )}
            </div>
            <div className="stack-gap" />
          </>
        )}

        <Table
          className="cg-table"
          label="网络电压分类"
          columns={columns}
          data={shown}
          rowKey="net"
          scroll
          maxHeight="min(58vh, 780px)"
          empty={<span className="hint">没有网络</span>}
        />
      </CardBody>
    </Card>
  )
}

// ---------------------------------------------------------------- 安装焊盘
function MountsPanel({ setStatus, setStatusError, active, revision }: PageProps) {
  const [data, setData] = useState<ObjectsResp | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await api.get<ObjectsResp>('/api/objects'))
    } catch (e) {
      setStatusError(errText(e))
    }
  }, [setStatusError])

  useEffect(() => {
    if (active) load()
  }, [active, revision, load])

  const patch = async (name: string, p: Record<string, unknown>) => {
    try {
      await api.post('/api/mount', { name, patch: p })
      await load()
      setStatus(`已更新 ${name}`)
    } catch (e) {
      setStatusError(errText(e))
    }
  }

  const classes = data?.net_classes ?? []
  const columns: TableColumn<MountRow>[] = [
    { key: 'name', header: '名称', width: 88, render: (r) => <span className="cg-mono">{r.name}</span> },
    {
      key: 'pos',
      header: '位置',
      width: 128,
      render: (r) => (
        <span className="cg-num">
          {r.x.toFixed(1)}, {r.y.toFixed(1)}
        </span>
      ),
    },
    { key: 'hole', header: '孔径', width: 76, align: 'end', render: (r) => r.hole.toFixed(2) },
    { key: 'width', header: '外径', width: 76, align: 'end', render: (r) => r.width.toFixed(2) },
    { key: 'net', header: '原网络', width: '14%', hideBelow: 'md', render: (r) => r.net || '—' },
    {
      key: 'net_class',
      header: '电位归属',
      width: 140,
      render: (r) => (
        <Select
          size="sm"
          value={r.net_class}
          options={classes.map((c) => ({ value: c.value, label: c.label }))}
          onChange={(v) => patch(r.name, { net_class: v })}
        />
      ),
    },
    {
      key: 'voltage',
      header: '电压 V',
      width: 84,
      hideBelow: 'sm',
      render: (r) => (
        <Input
          size="sm"
          value={r.voltage === null ? '' : String(r.voltage)}
          onChange={() => undefined}
          onBlur={(e) => patch(r.name, { voltage: (e.target as HTMLInputElement).value.trim() || null })}
        />
      ),
    },
    {
      key: 'enabled',
      header: '参与检查',
      width: 96,
      render: (r) => <Switch checked={r.enabled} onChange={(v) => patch(r.name, { enabled: v })} />,
    },
  ]

  if (!data?.mounts?.length) {
    return (
      <Card>
        <CardBody>
          <EmptyState size="sm" title="没有安装焊盘" description="扫描后这里会列出安装孔/安装焊盘候选。" />
        </CardBody>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader title="安装焊盘" description={`${data.mounts.length} 个候选（孔径只用于识别，避距按外径算）`} />
      <CardBody>
        <Table
          className="cg-table"
          label="安装焊盘"
          columns={columns}
          data={data.mounts}
          rowKey="name"
          scroll
          maxHeight="min(52vh, 700px)"
        />
      </CardBody>
    </Card>
  )
}

// ---------------------------------------------------------------- 无网络器件
function FloatingPanel({ setStatus, setStatusError, active, revision }: PageProps) {
  const [data, setData] = useState<ObjectsResp | null>(null)
  const [cutouts, setCutouts] = useState<CutoutRow[]>([])

  const load = useCallback(async () => {
    try {
      const r = await api.get<ObjectsResp>('/api/objects')
      setData(r)
      setCutouts(r.cutouts || [])
    } catch (e) {
      setStatusError(errText(e))
    }
  }, [setStatusError])

  useEffect(() => {
    if (active) load()
  }, [active, revision, load])

  const patch = async (designator: string, p: Record<string, unknown>) => {
    try {
      await api.post('/api/floating', { designator, patch: p })
      await load()
      setStatus(`已更新 ${designator}`)
    } catch (e) {
      setStatusError(errText(e))
    }
  }

  const classes = data?.net_classes ?? []
  const columns: TableColumn<FloatingRow>[] = [
    {
      key: 'designator',
      header: '位号',
      width: 92,
      render: (r) => <span className="cg-mono">{r.designator}</span>,
    },
    { key: 'comment', header: '注释', width: '16%', hideBelow: 'md', render: (r) => r.comment || '—' },
    {
      key: 'size',
      header: '外形 mm',
      width: 118,
      render: (r) =>
        r.width > 0 ? `${r.width.toFixed(2)} × ${r.height.toFixed(2)}` : '未读到外形',
    },
    { key: 'pad_count', header: '焊盘', width: 70, align: 'end', hideBelow: 'lg' },
    {
      key: 'net_class',
      header: '电位归属',
      width: 140,
      render: (r) => (
        <Select
          size="sm"
          value={r.net_class}
          options={classes.map((c) => ({ value: c.value, label: c.label }))}
          onChange={(v) => patch(r.designator, { net_class: v })}
        />
      ),
    },
    {
      key: 'voltage',
      header: '电压 V',
      width: 84,
      hideBelow: 'sm',
      render: (r) => (
        <Input
          size="sm"
          value={r.voltage === null ? '' : String(r.voltage)}
          onChange={() => undefined}
          onBlur={(e) =>
            patch(r.designator, { voltage: (e.target as HTMLInputElement).value.trim() || null })
          }
        />
      ),
    },
    {
      key: 'enabled',
      header: '参与检查',
      width: 96,
      render: (r) => (
        <Switch checked={r.enabled} onChange={(v) => patch(r.designator, { enabled: v })} />
      ),
    },
  ]

  const cutoutRows = cutouts.filter((c) => c.kind === 'circle' || c.diameter > 0)

  return (
    <div className="pg-col">
      <Card>
        <CardHeader
          title="无网络器件"
          description={`${data?.floating?.length ?? 0} 个（磁芯 / 结构件；未指定电位的不参与检查）`}
        />
        <CardBody>
          {data?.floating?.length ? (
            <Table
              className="cg-table"
              label="无网络器件"
              columns={columns}
              data={data.floating}
              rowKey="designator"
              scroll
              maxHeight="min(42vh, 560px)"
            />
          ) : (
            <EmptyState size="sm" title="没有无网络器件" description="扫描后自动识别（所有焊盘都没有网络的器件）。" />
          )}
        </CardBody>
      </Card>

      {cutoutRows.length > 0 && (
        <Card>
          <CardHeader title="板级圆形挖空" description="自动加入安装焊盘候选（CH1、CH2…）" />
          <CardBody>
            <div className="hint">
              {cutoutRows
                .slice(0, 12)
                .map((c) => `${c.name || 'CH'}（Ø${c.diameter.toFixed(2)} mm）`)
                .join('、')}
              {cutoutRows.length > 12 ? ` …共 ${cutoutRows.length} 个` : ''}
            </div>
          </CardBody>
        </Card>
      )}
    </div>
  )
}
