import { useCallback, useEffect, useState } from 'react'
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
  Stat,
  Switch,
  Table,
  Text,
  toast,
} from 'oink-ui'
import type { TableColumn } from 'oink-ui'

import { api, errText } from '../api'
import type { PageProps, ViolationRow, ViolationsResp } from '../types'

export default function ViolationPage({ setStatus, setStatusError, active, revision }: PageProps) {
  const [data, setData] = useState<ViolationsResp | null>(null)
  const [kind, setKind] = useState<'component' | 'pad'>('component')
  const [severity, setSeverity] = useState('all')
  const [rule, setRule] = useState('')
  const [isolation, setIsolation] = useState(false)
  const [search, setSearch] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const r = await api.get<ViolationsResp>(
        `/api/violations?kind=${kind}&severity=${severity}`
          + `&rule=${encodeURIComponent(rule)}`
          + `&isolation=${isolation ? 1 : 0}`
          + `&search=${encodeURIComponent(search)}`,
      )
      setData(r)
    } catch (e) {
      setStatusError(errText(e))
    }
  }, [kind, severity, rule, isolation, search, setStatusError])

  useEffect(() => {
    if (active) load()
  }, [active, revision, load])

  const runCheck = async () => {
    setBusy(true)
    setStatus('正在计算避距…')
    try {
      const r = await api.post<{ components: { total: number; errors: number }; worst_deficit: number }>(
        '/api/clearance',
      )
      await load()
      setStatus(`校验完成：${r.components.total} 条器件级违规`)
      toast.success(`校验完成：${r.components.total} 条（最差缺口 ${r.worst_deficit} mm）`)
    } catch (e) {
      const msg = errText(e)
      setStatusError(msg)
      toast.danger(msg)
    } finally {
      setBusy(false)
    }
  }

  const exportCsv = async () => {
    try {
      const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '')
      const d = await api.post<{ path: string; canceled: boolean }>('/api/dialog/save', {
        kind: 'csv',
        name: `clearance_${stamp}.csv`,
      })
      if (d.canceled || !d.path) return
      const r = await api.post<{ path: string }>('/api/export-csv', { path: d.path })
      setStatus(`已导出：${r.path}`)
      toast.success('CSV 已导出')
    } catch (e) {
      const msg = errText(e)
      setStatusError(msg)
      toast.danger(msg)
    }
  }

  const locate = async (designator: string) => {
    if (!designator) return
    try {
      await api.post('/api/locate', { designator })
      setStatus(`已在 Altium 中定位 ${designator}`)
    } catch (e) {
      const msg = errText(e)
      setStatusError(msg)
      toast.warning(msg)
    }
  }

  const columns: TableColumn<ViolationRow>[] = [
    {
      key: 'pair',
      header: '器件对',
      width: '18%',
      render: (r) => (
        <span className="cg-mono">
          {r.designator_a ?? r.item_a}
          {r.designator_b ?? r.item_b ? ' ↔ ' : ''}
          {r.designator_b ?? r.item_b}
        </span>
      ),
    },
    {
      key: 'nets',
      header: '网络',
      width: '16%',
      hideBelow: 'md',
      render: (r) => (
        <span className="cg-mono" style={{ fontSize: 12 }}>
          {r.net_a} / {r.net_b}
        </span>
      ),
    },
    {
      key: 'classes',
      header: '类别组合',
      width: 168,
      hideBelow: 'sm',
      render: (r) => (
        <span className="hint">
          {r.class_a_label} ↔ {r.class_b_label}
        </span>
      ),
    },
    {
      key: 'actual',
      header: '实测',
      width: 82,
      align: 'end',
      render: (r) => <span className="cg-num">{r.actual_mm.toFixed(2)}</span>,
    },
    {
      key: 'required',
      header: '要求',
      width: 82,
      align: 'end',
      render: (r) => <span className="cg-num">{r.required_mm.toFixed(2)}</span>,
    },
    {
      key: 'deficit',
      header: '缺口 mm',
      width: 88,
      align: 'end',
      render: (r) => (
        <span className="cg-num" style={{ color: 'var(--ok-danger)', fontWeight: 600 }}>
          {r.deficit_mm.toFixed(2)}
        </span>
      ),
    },
    {
      key: 'rule',
      header: '依据规则',
      width: '20%',
      hideBelow: 'lg',
      render: (r) => (
        <span className="hint" title={r.rule_name}>
          {r.rule_name}
        </span>
      ),
    },
    {
      key: 'severity',
      header: '类型',
      width: 80,
      render: (r) => (
        <span
          className="cg-num"
          style={{ color: r.severity === 'error' ? 'var(--ok-danger)' : 'var(--ok-warning)' }}
        >
          {r.severity === 'error' ? 'ERROR' : 'WARN'}
        </span>
      ),
    },
    {
      key: 'act',
      header: '',
      width: 66,
      render: (r) => (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => locate(String(r.designator_a ?? r.item_a ?? ''))}
        >
          定位
        </Button>
      ),
    },
  ]

  const rowLimitHint =
    (data?.filtered ?? 0) > 800 ? '表格最多铺 800 行（统计仍是全量）' : ''

  const summary = data?.summary
  const rows = data?.rows ?? []

  return (
    <Page size="full">
      <PageHeader
        title="违规结果"
        description="焊盘铜箔外沿两两求最小距离；器件级每对只留最严重的一条，缺口 = 要求 − 实测"
      />

      <div className="pg-col">
        <div className="metrics">
          <Stat
            variant="card"
            label="器件级违规"
            value={String(summary?.components.total ?? '—')}
          />
          <Stat
            variant="card"
            label="ERROR"
            value={String(summary?.components.errors ?? '—')}
          />
          <Stat
            variant="card"
            label="WARNING"
            value={String(summary?.components.warnings ?? '—')}
          />
          <Stat
            variant="card"
            label="焊盘级明细"
            value={String(summary?.pads.total ?? '—')}
          />
          <Stat
            variant="card"
            label="最差缺口"
            value={summary ? `${summary.worst_deficit} mm` : '—'}
          />
        </div>

        <Card>
          <CardHeader
            title="违规清单"
            description={
              data
                ? `筛选后 ${data.filtered} / 共 ${data.total} 条` +
                  (rowLimitHint ? ` · ${rowLimitHint}` : '')
                : '—'
            }
            actions={
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <Button size="sm" variant="accent" onClick={runCheck} loading={busy}>
                  重新校验
                </Button>
                <Button size="sm" variant="ghost" onClick={exportCsv}>
                  导出 CSV
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
                <Select
                  size="sm"
                  value={severity}
                  options={[
                    { value: 'all', label: '全部类型' },
                    { value: 'error', label: '仅 ERROR（器件间）' },
                    { value: 'warning', label: '仅 WARNING（器件内）' },
                  ]}
                  onChange={(v) => setSeverity(v)}
                />
                <Select
                  size="sm"
                  value={rule}
                  options={[
                    { value: '', label: '全部违规项' },
                    ...(data?.rules ?? []).map((r) => ({
                      value: r.key,
                      label: `${r.label}（${r.count}）`,
                    })),
                  ]}
                  onChange={(v) => setRule(v)}
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
                {(rule || isolation || severity !== 'all' || search) && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setRule('')
                      setIsolation(false)
                      setSeverity('all')
                      setSearch('')
                    }}
                  >
                    清除筛选
                  </Button>
                )}
              </div>
            </div>
            {rows.length ? (
              <Table
                className="cg-table"
                label="违规清单"
                columns={columns}
                data={rows.slice(0, 800)}
                rowKey={(r: ViolationRow, i: number) =>
                  `${r.designator_a ?? r.item_a}-${r.designator_b ?? r.item_b}-${i}`
                }
                scroll
                maxHeight="min(58vh, 800px)"
              />
            ) : (
              <EmptyState
                size="sm"
                title="没有违规结果"
                description="点右上「重新校验」跑一次避距计算；或规则较松时本来就没有违规。"
              />
            )}
            <div className="stack-gap" />
            <Text variant="caption" tone="subtle">
              双击「定位」会在 Altium 中跳到对应器件（需已连接且工程已打开）。
            </Text>
          </CardBody>
        </Card>
      </div>
    </Page>
  )
}
