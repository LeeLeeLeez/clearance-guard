import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Page,
  PageHeader,
  Select,
  Table,
  Text,
  toast,
} from 'oink-ui'
import type { TableColumn } from 'oink-ui'

import { api, errText } from '../api'
import type { CutoutRow, ObjectsResp, PageProps } from '../types'

const ROLE_LABEL: Record<string, string> = {
  slot: '避距槽',
  frame: '板框',
  hole: '圆形孔',
  ignore: '忽略',
  '': '未判定',
}

export default function SlotPage({ setStatus, setStatusError, active, revision }: PageProps) {
  const [rows, setRows] = useState<CutoutRow[]>([])
  const [current, setCurrent] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const r = await api.get<ObjectsResp>('/api/objects')
      setRows(r.cutouts || [])
      setCurrent((cur) => (cur === null && r.cutouts?.length ? r.cutouts[0].index : cur))
    } catch (e) {
      setStatusError(errText(e))
    }
  }, [setStatusError])

  useEffect(() => {
    if (active) load()
  }, [active, revision, load])

  const setRole = async (index: number, role: string) => {
    try {
      await api.post('/api/cutout', { index, patch: { role } })
      await load()
      setStatus(`挖空 #${index} 归属改为 ${ROLE_LABEL[role] || role}`)
    } catch (e) {
      setStatusError(errText(e))
    }
  }

  const loadOutlines = async () => {
    setBusy(true)
    setStatus('读取轮廓坐标（大板约 30 秒）…')
    try {
      await api.post('/api/cutout/outlines', {})
      toast.success('已请求读取轮廓')
    } catch (e) {
      const msg = errText(e)
      setStatusError(msg)
      toast.danger(msg)
    } finally {
      setBusy(false)
    }
  }

  const copyCoords = async () => {
    const cut = rows.find((r) => r.index === current)
    if (!cut?.outline?.length) return
    const text = cut.outline.map(([x, y]) => `${x.toFixed(3)},${y.toFixed(3)}`).join('\n')
    try {
      await navigator.clipboard.writeText(text)
      toast.success('坐标已复制')
    } catch {
      toast.warning('复制失败（浏览器未授权剪贴板）')
    }
  }

  const columns: TableColumn<CutoutRow>[] = [
    { key: 'index', header: '序号', width: 64 },
    {
      key: 'center',
      header: '中心',
      width: 140,
      render: (r) => (
        <span className="cg-num">
          {r.x.toFixed(2)}, {r.y.toFixed(2)}
        </span>
      ),
    },
    {
      key: 'size',
      header: '长 × 宽 mm',
      width: 140,
      render: (r) => (
        <span className="cg-num">
          {r.width.toFixed(2)} × {r.height.toFixed(2)}
        </span>
      ),
    },
    { key: 'points', header: '顶点数', width: 84, align: 'end', hideBelow: 'sm' },
    {
      key: 'role',
      header: '归属',
      width: 132,
      render: (r) => (
        <Select
          size="sm"
          value={r.role || ''}
          options={[
            { value: '', label: '未判定' },
            { value: 'slot', label: '避距槽' },
            { value: 'hole', label: '圆形孔' },
            { value: 'frame', label: '板框' },
            { value: 'ignore', label: '忽略' },
          ]}
          onChange={(v) => setRole(r.index, v)}
        />
      ),
    },
    {
      key: 'status',
      header: '轮廓',
      width: 90,
      render: (r) =>
        r.outline?.length ? (
          <span style={{ color: 'var(--ok-success)' }}>已读取</span>
        ) : (
          <span className="hint">未读取</span>
        ),
    },
  ]

  const cut = rows.find((r) => r.index === current)
  const slots = rows.filter((r) => (r.role || 'slot') === 'slot')

  return (
    <Page size="full">
      <PageHeader
        title="爬电隔离槽"
        description="板级多边形挖空作为避距槽参与间距判定；轮廓坐标需单独读取（遍历整板铜箔，约 30 秒）"
      />

      <div className="pg-col">
        <Card>
          <CardHeader
            title="避距槽列表"
            description={`${slots.length} 个避距槽 / 共 ${rows.length} 个挖空`}
            actions={
              <Button size="sm" variant="accent" onClick={loadOutlines} loading={busy}>
                读取轮廓坐标
              </Button>
            }
          />
          <CardBody>
            {rows.length ? (
              <Table
                className="cg-table"
                label="避距槽"
                columns={columns}
                data={rows}
                rowKey="index"
                scroll
                maxHeight="min(46vh, 620px)"
              />
            ) : (
              <EmptyState
                size="sm"
                title="没有板级挖空"
                description="扫描时读取 Kind = board_cutout 的区域；圆形挖空会进「安装焊盘」页，多边形进这里。"
              />
            )}
          </CardBody>
        </Card>

        {cut && (
          <Card>
            <CardHeader
              title={`轮廓坐标 · 挖空 #${cut.index}`}
              description={
                cut.outline?.length
                  ? `${cut.outline.length} 个顶点（mm，首尾自动闭合）`
                  : '还没读取轮廓 —— 点右上「读取轮廓坐标」'
              }
              actions={
                <Button size="sm" variant="ghost" onClick={copyCoords} disabled={!cut.outline?.length}>
                  复制坐标
                </Button>
              }
            />
            <CardBody>
              {cut.outline?.length ? (
                <pre className="log ok-scroll" style={{ maxHeight: 300 }}>
                  {cut.outline.map(([x, y]) => `${x.toFixed(3)}, ${y.toFixed(3)}`).join('\n')}
                </pre>
              ) : (
                <Text variant="caption" tone="subtle">
                  间距计算用槽的外框，不依赖这些顶点，不读也能算避距。
                </Text>
              )}
            </CardBody>
          </Card>
        )}
      </div>
    </Page>
  )
}
