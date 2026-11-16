import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  Field,
  Grid,
  Input,
  Page,
  PageHeader,
  Switch,
  Text,
  toast,
} from 'oink-ui'

import { api, errText } from '../api'
import type { PageProps, RulesResp } from '../types'

const ORDER = ['PRIMARY_CONST', 'PRIMARY_SWITCHING', 'SECONDARY']
const LABEL: Record<string, string> = {
  PRIMARY_CONST: '原边·恒定',
  PRIMARY_SWITCHING: '原边·跳变',
  SECONDARY: '副边',
}

/**
 * 数字输入框：输入过程用本地草稿，失焦或回车才提交。
 *
 * oink-ui 的 Input 是受控组件（onChange 给的是新值字符串），如果直接把
 * rules 里的值绑回 value、onChange 又什么都不做，按键会被立刻回滚成旧值。
 */
function NumField({
  value,
  onCommit,
}: {
  value: string | number | undefined
  onCommit: (value: string) => void
}) {
  const text = value === undefined ? '' : String(value)
  const [draft, setDraft] = useState(text)

  useEffect(() => {
    setDraft(text)
  }, [text])

  return (
    <Input
      size="sm"
      value={draft}
      onChange={(next) => setDraft(next)}
      onBlur={() => {
        const next = draft.trim()
        if (next === text) return
        // 空值或非数字直接还原，避免后端 float('') 解析失败后静默丢弃整次修改
        if (next === '' || !Number.isFinite(Number(next))) {
          setDraft(text)
          return
        }
        onCommit(next)
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
        if (e.key === 'Escape') setDraft(text)
      }}
    />
  )
}

export default function RulesPage({ setStatus, setStatusError, active, revision }: PageProps) {
  const [rules, setRules] = useState<RulesResp | null>(null)
  const [busy, setBusy] = useState(false)
  const [running, setRunning] = useState(false)
  const [lastCheck, setLastCheck] = useState<{
    components: { total: number; errors: number; warnings: number }
    pads: { total: number }
    worst_deficit: number
  } | null>(null)

  const load = useCallback(async () => {
    try {
      setRules(await api.get<RulesResp>('/api/rules'))
    } catch (e) {
      setStatusError(errText(e))
    }
  }, [setStatusError])

  useEffect(() => {
    if (active) load()
  }, [active, revision, load])

  const patch = async (p: Record<string, unknown>) => {
    setBusy(true)
    try {
      const r = await api.post<RulesResp>('/api/rules', p)
      setRules(r)
      setStatus('规则已保存')
    } catch (e) {
      setStatusError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  /** 保存当前所有输入（靠输入框 onBlur 落盘）后再跑一次避距校验。
   *
   * 避距计算在 700+ 器件的板子上要 5~10 秒，所以必须有明确的 running 反馈，
   * 否则按钮看起来「点了没反应」。校验完把结果摘要显在卡片里。
   */
  const onSaveAndCheck = async () => {
    setRunning(true)
    setStatus('正在计算避距…')
    try {
      // 点按钮时焦点离开输入框，onBlur 的保存还在飞 —— 稍等一拍，确保规则已落盘
      await new Promise((r) => setTimeout(r, 300))
      const r = await api.post<{
        components: { total: number; errors: number; warnings: number }
        pads: { total: number }
        worst_deficit: number
      }>('/api/clearance')
      setLastCheck({
        components: r.components,
        pads: r.pads,
        worst_deficit: r.worst_deficit,
      })
      setStatus(
        `校验完成：器件级 ${r.components.total} 条（ERROR ${r.components.errors}），最差缺口 ${r.worst_deficit} mm`,
      )
      toast.success(
        `校验完成：${r.components.total} 条器件级违规（ERROR ${r.components.errors}）`,
      )
    } catch (e) {
      const msg = errText(e)
      setStatusError(msg)
      toast.danger(msg)
    } finally {
      setRunning(false)
    }
  }

  const patchMatrix = async (a: string, b: string, value: string) => {
    const key = ORDER.indexOf(a) <= ORDER.indexOf(b) ? `${a}|${b}` : `${b}|${a}`
    const num = Number(value)
    if (!Number.isFinite(num)) return
    await patch({ matrix: { [key]: num } })
  }

  if (!rules) {
    return (
      <Page size="full">
        <PageHeader title="间距规则" />
        <Card>
          <CardBody>
            <Text variant="caption" tone="subtle">
              正在读取规则…
            </Text>
          </CardBody>
        </Card>
      </Page>
    )
  }

  const num = (key: keyof RulesResp) => (
    <NumField
      value={rules[key] as string | number | undefined}
      onCommit={(value) => patch({ [key]: value })}
    />
  )

  const flag = (key: keyof RulesResp) => (
    <Switch checked={Boolean(rules[key])} onChange={(v) => patch({ [key]: v })} />
  )

  return (
    <Page size="full">
      <PageHeader
        title="间距规则"
        description="类别矩阵 + 隔离带爬电/电气间隙 + 电压差增量 + 器件本体与板边间距；改动立即保存"
      />

      <div className="pg-col">
        <Card>
          <CardHeader
            title="类别间距矩阵（mm）"
            description="同类 0.20 / 原边恒定↔跳变 0.50 / 原边↔副边 = 隔离带（8.00）"
            actions={
              <Button size="sm" variant="ghost" onClick={load} loading={busy}>
                重新读取
              </Button>
            }
          />
          <CardBody>
            <div style={{ overflowX: 'auto' }}>
              <table className="cg-table" style={{ borderCollapse: 'collapse', width: '100%' }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: 'left', padding: '6px 10px' }}>类别</th>
                    {ORDER.map((c) => (
                      <th key={c} style={{ textAlign: 'left', padding: '6px 10px' }}>
                        {LABEL[c]}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {ORDER.map((row) => (
                    <tr key={row} style={{ borderTop: '1px solid var(--ok-line)' }}>
                      <td style={{ padding: '6px 10px', fontSize: 12.5, color: 'var(--ok-text-2)' }}>
                        {LABEL[row]}
                      </td>
                      {ORDER.map((col) => {
                        const key =
                          ORDER.indexOf(row) <= ORDER.indexOf(col) ? `${row}|${col}` : `${col}|${row}`
                        const value = rules.matrix?.[key]
                        return (
                          <td key={col} style={{ padding: '4px 8px', width: 140 }}>
                            <NumField value={value} onCommit={(next) => patchMatrix(row, col, next)} />
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardBody>
        </Card>

        <div className="pg-grid--2">
          <div className="pg-col">
            <Card>
              <CardHeader title="隔离带" />
              <CardBody>
                <Grid minItemWidth={150} gap={4}>
                  <Field label="爬电距离（沿面）mm" hint="两点之间没有避距槽时用这个">
                    {num('isolation_gap_mm')}
                  </Field>
                  <Field label="电气间隙（跨槽）mm" hint="被挖空隔开且绕槽沿面达标时用">
                    {num('isolation_clearance_mm')}
                  </Field>
                </Grid>
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="电压差增量" description="与矩阵取较大者（375 V 对 0 V 这类场景）" />
              <CardBody>
                <Grid minItemWidth={130} gap={4}>
                  <Field label="启用">{flag('voltage_step_enabled')}</Field>
                  <Field label="每档电压 V">{num('voltage_step_v')}</Field>
                  <Field label="每档增加 mm">{num('voltage_step_mm')}</Field>
                </Grid>
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="板边与器件本体" />
              <CardBody>
                <Grid minItemWidth={140} gap={4}>
                  <Field label="板边间距启用">{flag('board_edge_enabled')}</Field>
                  <Field label="板边间距 mm">{num('board_edge_gap_mm')}</Field>
                  <Field label="器件间间距启用">{flag('component_gap_enabled')}</Field>
                  <Field label="器件间间距 mm" hint="与网络无关，任何两个不同器件的焊盘">
                    {num('component_gap_mm')}
                  </Field>
                  <Field label="本体间距启用">{flag('body_gap_enabled')}</Field>
                  <Field label="本体间距 mm" hint="3D 本体投影，正反面不判干涉">
                    {num('body_gap_mm')}
                  </Field>
                </Grid>
              </CardBody>
            </Card>
          </div>

          <div className="pg-col">
            <Card>
              <CardHeader title="检查范围" />
              <CardBody>
                <Grid minItemWidth={160} gap={4}>
                  <Field label="检查“未定”网络">{flag('check_unknown')}</Field>
                  <Field label="纳入走线" hint="当前未拉取走线，开了也没数据">
                    {flag('include_tracks')}
                  </Field>
                  <Field label="只查跨类别">{flag('cross_class_only')}</Field>
                  <Field label="检查同一器件内部焊盘" hint="隔离器件内部始终检查">
                    {flag('check_same_component')}
                  </Field>
                </Grid>
                <div className="stack-gap" />
                {lastCheck && (
                  <>
                    <div className="metrics">
                      <div className="metric">
                        <span className="metric__label">器件级违规</span>
                        <span className="metric__value">{lastCheck.components.total}</span>
                      </div>
                      <div
                        className="metric"
                        data-tone={lastCheck.components.errors > 0 ? 'danger' : 'ok'}
                      >
                        <span className="metric__label">ERROR</span>
                        <span className="metric__value">{lastCheck.components.errors}</span>
                      </div>
                      <div className="metric" data-tone="warn">
                        <span className="metric__label">WARNING</span>
                        <span className="metric__value">{lastCheck.components.warnings}</span>
                      </div>
                      <div className="metric">
                        <span className="metric__label">最差缺口</span>
                        <span className="metric__value">{lastCheck.worst_deficit} mm</span>
                      </div>
                    </div>
                    <div className="stack-gap" />
                  </>
                )}
                <Text variant="caption" tone="subtle">
                  点「保存并校验」会先落盘当前规则，再重算避距（大板约 5~10 秒）；
                  明细在「违规结果」页。规则改动本身不会自动重算。
                </Text>
                <div style={{ marginTop: 12 }}>
                  <Button
                    variant="accent"
                    onClick={onSaveAndCheck}
                    loading={running}
                    disabled={busy}
                  >
                    保存并重新校验
                  </Button>
                </div>
              </CardBody>
            </Card>
          </div>
        </div>
      </div>
    </Page>
  )
}
