import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AppShell,
  Badge,
  Button,
  Card,
  CardBody,
  Confirmer,
  Dialog,
  Field,
  Icon,
  Input,
  Nav,
  NavGroup,
  NavItem,
  OinkProvider,
  Select,
  Switch,
  Toaster,
  confirm,
  toast,
  useOink,
} from 'oink-ui'

import { api, errText } from './api'
import type { StatusResp } from './types'
import ScanPage from './pages/ScanPage'
import SlotPage from './pages/SlotPage'
import RulesPage from './pages/RulesPage'
import ViolationPage from './pages/ViolationPage'
import LayoutPage from './pages/LayoutPage'

type IconName = React.ComponentProps<typeof Icon>['name']

interface NavDef {
  group: string
  items: { id: string; text: string; icon?: IconName }[]
}

const NAV: NavDef[] = [
  {
    group: '审核流程',
    items: [
      { id: 'scan', text: '工程与扫描', icon: 'folder' },
      { id: 'slot', text: '爬电隔离槽', icon: 'grid' },
      { id: 'rules', text: '间距规则', icon: 'sliders' },
      { id: 'result', text: '违规结果', icon: 'alert' },
      { id: 'layout', text: '布局视图', icon: 'layout' },
    ],
  },
]

export default function App() {
  return (
    <OinkProvider
      defaultTheme="system"
      defaultAccent="blue"
      locale="zh-CN"
      storageKey="clearance-guard"
    >
      <Shell />
      <Toaster />
      <Confirmer />
    </OinkProvider>
  )
}

function Shell() {
  const [page, setPage] = useState(() => {
    try {
      const p = new URLSearchParams(window.location.search).get('page')
      return NAV.some((g) => g.items.some((i) => i.id === p)) ? (p as string) : 'scan'
    } catch {
      return 'scan'
    }
  })
  const [status, setStatus] = useState<StatusResp | null>(null)
  const [bar, setBar] = useState({ msg: '就绪', error: false })
  const [revision, setRevision] = useState(0)
  const [projects, setProjects] = useState<{ path: string; label: string }[]>([])
  const [projectPath, setProjectPath] = useState('')
  const [withNetlist, setWithNetlist] = useState(true)
  const [busy, setBusy] = useState(false)
  const [snapDlg, setSnapDlg] = useState(false)
  const [lastExport, setLastExport] = useState('')
  const [snaps, setSnaps] = useState<{ name: string; path: string; size: number; mtime: string }[]>([])
  const { resolvedTheme, setTheme } = useOink()
  const prevRunning = useRef(false)

  const setStatusMsg = useCallback((msg: string) => setBar({ msg, error: false }), [])
  const setStatusError = useCallback((msg: string) => setBar({ msg, error: true }), [])

  // ---------------------------------------------------------- 状态轮询
  const refreshProjects = useCallback(async () => {
    try {
      const r = await api.get<{ projects: { path: string; label: string }[]; focused: string }>(
        '/api/projects',
      )
      setProjects(r.projects || [])
      setProjectPath((cur) => cur || r.focused || r.projects?.[0]?.path || '')
    } catch (e) {
      setStatusError(errText(e))
    }
  }, [setStatusError])

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const s = await api.get<StatusResp>('/api/status')
        if (!alive) return
        setStatus(s)
        const running = !!s.task?.running
        if (prevRunning.current && !running) {
          if (s.task?.error) {
            setBar({ msg: `任务失败：${s.task.error}`, error: true })
            toast.danger(s.task.error)
          } else if (s.task?.finished) {
            setRevision((r) => r + 1)
            setBar({ msg: '任务完成', error: false })
          }
        }
        prevRunning.current = running
        setBusy(running)
      } catch {
        /* 后端可能还没起来 */
      }
    }
    tick()
    const id = window.setInterval(tick, 2000)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [])

  // ---------------------------------------------------------- 操作
  const onConnect = async () => {
    if (status?.connected) {
      setStatusMsg('正在断开…')
      try {
        await api.post('/api/disconnect')
        setStatusMsg('已断开 Altium')
      } catch (e) {
        setStatusError(errText(e))
      }
      return
    }
    setStatusMsg('正在连接 Altium…')
    try {
      await api.post('/api/connect')
      setStatusMsg('已连接 Altium')
      toast.success('已连接 Altium')
      await refreshProjects()
    } catch (e) {
      const msg = errText(e)
      setStatusError(msg)
      toast.danger(msg)
    }
  }

  const runTask = async (kind: 'scan') => {
    try {
      await api.post('/api/scan', { project_path: projectPath, with_netlist: withNetlist })
      setStatusMsg('扫描中…')
    } catch (e) {
      const msg = errText(e)
      setStatusError(msg)
      toast.danger(msg)
    }
  }

  /** 只重读 PCB（器件/焊盘/安装孔/挖空），沿用上次分类，不重算违规。 */
  const onScanPcb = async () => {
    try {
      await api.post('/api/scan-pcb', { project_path: projectPath })
      setStatusMsg('正在重扫 PCB…')
    } catch (e) {
      const msg = errText(e)
      setStatusError(msg)
      toast.danger(msg)
    }
  }

  const onCancel = async () => {
    try {
      await api.post('/api/scan-cancel')
      setStatusMsg('已请求取消')
    } catch (e) {
      setStatusError(errText(e))
    }
  }

  const onOpenProject = async () => {
    try {
      const d = await api.post<{ path: string; canceled: boolean }>('/api/dialog/open', {
        kind: 'project',
      })
      if (d.canceled || !d.path) return
      await api.post('/api/open-project', { path: d.path })
      setProjectPath(d.path)
      setStatusMsg('已打开工程')
      await refreshProjects()
    } catch (e) {
      setStatusError(errText(e))
    }
  }

  const onExport = async () => {
    try {
      const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '')
      const d = await api.post<{ path: string; canceled: boolean }>('/api/dialog/save', {
        kind: 'snapshot',
        name: `${summary?.board_name || 'scan'}_${stamp}.json`,
      })
      if (d.canceled || !d.path) return
      const r = await api.post<{ path: string }>('/api/snapshot/export', { path: d.path })
      setLastExport(r.path)
      setStatusMsg(`已导出快照：${r.path}`)
      toast.success('快照已导出')
    } catch (e) {
      setStatusError(errText(e))
    }
  }

  const onImportDialog = async () => {
    try {
      const d = await api.post<{ path: string; canceled: boolean }>('/api/dialog/open', {
        kind: 'snapshot',
      })
      if (d.canceled || !d.path) return
      await onImport(d.path)
    } catch (e) {
      setStatusError(errText(e))
    }
  }

  const onReveal = async (path: string) => {
    try {
      await api.post('/api/reveal', { path })
    } catch (e) {
      setStatusError(errText(e))
    }
  }

  const openSnapshots = async () => {
    setSnapDlg(true)
    try {
      const r = await api.get<{ files: typeof snaps }>('/api/snapshots')
      setSnaps(r.files || [])
    } catch (e) {
      setStatusError(errText(e))
    }
  }

  const onImport = async (path: string) => {
    try {
      await api.post('/api/snapshot/import', { path })
      setSnapDlg(false)
      setRevision((r) => r + 1)
      setStatusMsg('已导入快照')
      toast.success('快照已导入')
    } catch (e) {
      const msg = errText(e)
      setStatusError(msg)
      toast.danger(msg)
    }
  }

  const onQuit = async () => {
    const ok = await confirm({
      title: '退出 Clearance Guard',
      description: '会停止本地服务（端口 8770）并关闭后端；Altium 不受影响。',
      confirmText: '退出',
    })
    if (!ok) return
    try {
      await api.post('/api/shutdown')
    } catch {
      /* 服务端已经开始关闭，连接被断是正常的 */
    }
    setBar({ msg: '服务已停止，可直接关掉这个页面', error: false })
  }

  const summary = status?.summary
  const scanned = !!summary?.scanned
  const tasks = status?.task

  const shared = { setStatus: setStatusMsg, setStatusError, revision, onRefreshGlobal: () => setRevision((r) => r + 1) }

  return (
    <AppShell collapseBelow="md">
      <AppShell.Header>
        <div className="appbar">
          <AppShell.Trigger target="sidebar" />
          <span className="appbar__title">Clearance Guard</span>
          <span className="appbar__sub">避距审核</span>
          <Badge tone={status?.connected ? 'success' : 'neutral'} variant="soft">
            {status?.connected ? 'AD 已连接' : 'AD 未连接'}
          </Badge>
          {summary?.demo && (
            <Badge tone="warning" variant="soft">
              示例工程
            </Badge>
          )}
          <span className="appbar__spacer" />
          <Button
            size="sm"
            variant="ghost"
            leading={<Icon name={resolvedTheme === 'dark' ? 'sun' : 'moon'} />}
            onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
          >
            {resolvedTheme === 'dark' ? '浅色' : '深色'}
          </Button>
          <Button size="sm" variant="ghost" onClick={onQuit}>
            退出服务
          </Button>
        </div>
      </AppShell.Header>

      <AppShell.Sidebar width={214} label="主导航">
        <Nav aria-label="主导航">
          {NAV.map((g) => (
            <NavGroup key={g.group} label={g.group}>
              {g.items.map((it) => (
                <NavItem
                  key={it.id}
                  icon={it.icon ? <Icon name={it.icon} /> : undefined}
                  current={page === it.id}
                  onClick={() => setPage(it.id)}
                >
                  {it.text}
                </NavItem>
              ))}
            </NavGroup>
          ))}
        </Nav>
      </AppShell.Sidebar>

      <AppShell.Main>
        <div className="pg-col">
          <Card>
            <CardBody>
              <div className="toolbar">
                <div className="toolbar__group">
                  <Button
                    size="sm"
                    variant={status?.connected ? 'soft' : 'primary'}
                    onClick={onConnect}
                  >
                    {status?.connected ? '断开 Altium' : '连接 Altium'}
                  </Button>
                  <span className="toolbar__split" />
                  <span className="toolbar__label">工程</span>
                  <Select
                    size="sm"
                    value={projectPath}
                    placeholder={status?.connected ? '选择已打开的工程…' : '（未连接）'}
                    options={projects.map((p) => ({ value: p.path, label: p.label || p.path }))}
                    onChange={(v) => setProjectPath(v)}
                  />
                  <Button size="sm" variant="ghost" onClick={refreshProjects} disabled={!status?.connected}>
                    刷新列表
                  </Button>
                  <Button size="sm" variant="ghost" onClick={onOpenProject}>
                    打开工程…
                  </Button>
                </div>

                <span className="toolbar__grow" />

                <div className="toolbar__group">
                  <Button
                    size="sm"
                    variant="accent"
                    onClick={() => runTask('scan')}
                    loading={busy && tasks?.kind === 'scan'}
                    disabled={!status?.connected || busy}
                  >
                    扫描工程
                  </Button>
                  <Button
                    size="sm"
                    variant="soft"
                    onClick={onScanPcb}
                    loading={busy && tasks?.kind === 'scan_pcb'}
                    disabled={!status?.connected || !scanned || busy}
                  >
                    扫描pcb
                  </Button>
                  {busy && (
                    <Button size="sm" variant="ghost" onClick={onCancel}>
                      取消
                    </Button>
                  )}
                  <span className="toolbar__split" />
                  <Button size="sm" variant="ghost" onClick={onExport} disabled={!scanned}>
                    导出快照…
                  </Button>
                  <Button size="sm" variant="ghost" onClick={onImportDialog}>
                    导入快照…
                  </Button>
                  <Button size="sm" variant="ghost" onClick={openSnapshots}>
                    最近快照
                  </Button>
                  {lastExport && (
                    <Button size="sm" variant="ghost" onClick={() => onReveal(lastExport)}>
                      打开文件夹
                    </Button>
                  )}
                  <span
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 6,
                      fontSize: 12,
                      color: 'var(--ok-text-2)',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    <Switch checked={withNetlist} onChange={setWithNetlist} />
                    含网表
                  </span>
                </div>
              </div>

              <div className="stack-gap" />
              <div className="metrics">
                <div className="metric">
                  <span className="metric__label">器件</span>
                  <span className="metric__value">{summary?.counts?.components ?? '—'}</span>
                </div>
                <div className="metric">
                  <span className="metric__label">焊盘</span>
                  <span className="metric__value">{summary?.counts?.pads ?? '—'}</span>
                </div>
                <div className="metric">
                  <span className="metric__label">网络</span>
                  <span className="metric__value">{summary?.counts?.nets ?? '—'}</span>
                </div>
                <div className="metric">
                  <span className="metric__label">图纸</span>
                  <span className="metric__value">{summary?.counts?.sheets ?? '—'}</span>
                </div>
                <div className="metric" data-tone="warn">
                  <span className="metric__label">原边·恒定</span>
                  <span className="metric__value">
                    {summary?.net_classes?.PRIMARY_CONST ?? 0}
                  </span>
                </div>
                <div className="metric" data-tone="danger">
                  <span className="metric__label">原边·跳变</span>
                  <span className="metric__value">
                    {summary?.net_classes?.PRIMARY_SWITCHING ?? 0}
                  </span>
                </div>
                <div className="metric" data-tone="ok">
                  <span className="metric__label">副边</span>
                  <span className="metric__value">{summary?.net_classes?.SECONDARY ?? 0}</span>
                </div>
                <div
                  className="metric"
                  data-tone={(summary?.violations?.errors ?? 0) > 0 ? 'danger' : 'ok'}
                >
                  <span className="metric__label">违规（ERROR）</span>
                  <span className="metric__value">{summary?.violations?.errors ?? '—'}</span>
                </div>
              </div>
            </CardBody>
          </Card>

          <div style={{ display: page === 'scan' ? 'block' : 'none' }}>
            <ScanPage {...shared} active={page === 'scan'} />
          </div>
          <div style={{ display: page === 'rules' ? 'block' : 'none' }}>
            <RulesPage {...shared} active={page === 'rules'} />
          </div>
          <div style={{ display: page === 'result' ? 'block' : 'none' }}>
            <ViolationPage {...shared} active={page === 'result'} />
          </div>
          <div style={{ display: page === 'layout' ? 'block' : 'none' }}>
            <LayoutPage {...shared} active={page === 'layout'} />
          </div>
          <div style={{ display: page === 'slot' ? 'block' : 'none' }}>
            <SlotPage {...shared} active={page === 'slot'} />
          </div>
        </div>
      </AppShell.Main>

      <AppShell.Footer>
        <div
          className={
            bar.error
              ? 'statusbar statusbar--error'
              : busy
                ? 'statusbar statusbar--busy'
                : 'statusbar'
          }
        >
          <span className="statusbar__dot" />
          <span className="statusbar__text">{bar.msg}</span>
          <span className="statusbar__spacer" />
          {tasks?.running && (
            <span className="statusbar__task">
              {tasks.stage}
              {tasks.detail ? ` · ${tasks.detail}` : ''} · {tasks.elapsed}s
            </span>
          )}
          {!tasks?.running && summary?.scanned_at && (
            <span className="statusbar__task">上次扫描 {summary.scanned_at}</span>
          )}
        </div>
      </AppShell.Footer>

      <Dialog
        open={snapDlg}
        onOpenChange={setSnapDlg}
        title="导入扫描快照"
        description="json/ 目录与上次自动保存的快照"
        size="lg"
      >
        <div className="pg-col" style={{ gap: 6 }}>
          {snaps.length === 0 && <span className="hint">没有找到快照文件。</span>}
          {snaps.map((f) => (
            <Button key={f.path} variant="soft" block onClick={() => onImport(f.path)}>
              {f.name} · {Math.round(f.size / 1024)} KB · {f.mtime}
            </Button>
          ))}
        </div>
      </Dialog>
    </AppShell>
  )
}
