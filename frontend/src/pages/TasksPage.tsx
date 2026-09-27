import { useEffect, useState } from 'react'
import { Button, Input, Switch, Spinner } from '@fluentui/react-components'
import { api } from '../api'

function timeText(ts: number): string {
  try {
    const date = new Date(ts)
    return `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(
      date.getHours()
    ).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  } catch {
    return ''
  }
}

/**
 * 定时任务 —— 让一体机按点自己做事（体检 / 清理 / 开软件 / 跑批处理）。
 * 总开关默认关闭；有启用中的任务时，退出程序会先提示。
 */
export default function TasksPage() {
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    name: '',
    kind: 'program',
    target: '',
    args: '',
    workdir: '',
    triggerType: 'daily',
    runTime: '08:00',
    weekdays: [0, 1, 2, 3, 4] as number[],
    onceAt: '',
    bootDelay: 60,
  })

  const load = async () => {
    try {
      setData(await api.tasks_state())
    } catch {
      /* 忽略 */
    }
  }

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 5000)
    return () => window.clearInterval(timer)
  }, [])

  const act = async (fn: () => Promise<any>) => {
    setBusy(true)
    setMsg('')
    try {
      const result = await fn()
      setMsg(result?.message ?? '')
      await load()
    } catch (error) {
      setMsg(`操作失败：${error}`)
    } finally {
      setBusy(false)
    }
  }

  const pickTarget = async () => {
    const picked = await api.tasks_pick_target()
    if (picked?.ok) setForm({ ...form, target: picked.path })
    else setMsg(picked?.message ?? '未选择文件')
  }

  const submit = async () => {
    if (!form.target.trim()) {
      setMsg('请填写要执行的内容（命令或文件路径）')
      return
    }
    await act(() =>
      api.tasks_add(
        form.name || form.target,
        form.kind,
        form.target,
        form.args,
        form.workdir,
        form.triggerType,
        form.runTime,
        form.weekdays.join(','),
        form.onceAt,
        form.bootDelay
      )
    )
    setShowForm(false)
    setForm({ ...form, name: '', target: '', args: '' })
  }

  const items: any[] = data?.items ?? []
  const overview: any[] = data?.overview ?? []
  const logs: any[] = data?.logs ?? []
  const kinds: Record<string, string> = data?.kinds ?? {}
  const triggers: Record<string, string> = data?.triggers ?? {}
  const weekdays: string[] = data?.weekdays ?? []

  const byId = (id: string) => overview.find((o) => o.id === id)

  return (
    <div className="oc-page">
      <div className="oc-page-header">
        <div className="oc-page-title">定时任务</div>
        <div className="oc-page-desc">
          让这台机器按点自己做事：跑体检、清临时文件、打开软件、执行批处理
        </div>
      </div>

      <div className="oc-panel">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <div>
            <div style={{ fontWeight: 600 }}>总开关</div>
            <div className="oc-list-sub">
              {data?.enabled
                ? `已开启 · ${data?.enabledCount ?? 0} 个任务在启用状态`
                : '已关闭 · 任何任务都不会执行（默认关闭，需要时再开）'}
            </div>
          </div>
          <Switch
            checked={Boolean(data?.enabled)}
            disabled={busy}
            onChange={(_e, d) => void act(() => api.tasks_set_enabled(d.checked))}
          />
        </div>
        {data?.enabled && (data?.enabledCount ?? 0) > 0 && (
          <div className="oc-list-warn" style={{ marginTop: 8 }}>
            退出程序时会提示：还有 {data?.enabledCount} 个任务在启用状态，退出后不会再执行。
          </div>
        )}
      </div>

      <div className="oc-panel" style={{ marginTop: 12 }}>
        <div className="oc-panel-title">
          任务列表（{items.length}）
          <Button
            size="small"
            appearance={showForm ? 'secondary' : 'primary'}
            style={{ marginLeft: 10 }}
            onClick={() => setShowForm(!showForm)}
          >
            {showForm ? '收起' : '添加任务'}
          </Button>
        </div>

        {showForm && (
          <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              {(Object.keys(kinds).length ? Object.keys(kinds) : ['program']).map((key) => (
                <Button
                  key={key}
                  size="small"
                  appearance={form.kind === key ? 'primary' : 'secondary'}
                  onClick={() => setForm({ ...form, kind: key })}
                >
                  {kinds[key] ?? key}
                </Button>
              ))}
              <Input
                placeholder="任务名称（可选）"
                value={form.name}
                onChange={(_e, d) => setForm({ ...form, name: d.value })}
                style={{ minWidth: 200 }}
              />
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <Input
                placeholder={
                  form.kind === 'command'
                    ? '要执行的命令，例如：ipconfig /flushdns'
                    : '脚本或程序的完整路径'
                }
                value={form.target}
                onChange={(_e, d) => setForm({ ...form, target: d.value })}
                style={{ flex: 1 }}
              />
              {form.kind !== 'command' && (
                <Button size="small" appearance="secondary" onClick={() => void pickTarget()}>
                  选择文件…
                </Button>
              )}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <Input
                placeholder="参数（空格分隔，可选）"
                value={form.args}
                onChange={(_e, d) => setForm({ ...form, args: d.value })}
                style={{ minWidth: 220 }}
              />
              <Input
                placeholder="工作目录（可选）"
                value={form.workdir}
                onChange={(_e, d) => setForm({ ...form, workdir: d.value })}
                style={{ minWidth: 220 }}
              />
            </div>

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <span className="oc-list-sub">触发方式</span>
              {(Object.keys(triggers).length
                ? Object.keys(triggers)
                : ['daily', 'weekly', 'once', 'boot']
              ).map((key) => (
                <Button
                  key={key}
                  size="small"
                  appearance={form.triggerType === key ? 'primary' : 'secondary'}
                  onClick={() => setForm({ ...form, triggerType: key })}
                >
                  {triggers[key] ?? key}
                </Button>
              ))}
            </div>

            {(form.triggerType === 'daily' || form.triggerType === 'weekly') && (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <span className="oc-list-sub">执行时间</span>
                <Input
                  value={form.runTime}
                  onChange={(_e, d) => setForm({ ...form, runTime: d.value })}
                  placeholder="08:00"
                  style={{ width: 100 }}
                />
                {form.triggerType === 'weekly' && (
                  <>
                    <span className="oc-list-sub">星期</span>
                    {(weekdays.length ? weekdays : ['周一', '周二', '周三', '周四', '周五']).map(
                      (label, index) => (
                        <Button
                          key={label}
                          size="small"
                          appearance={
                            form.weekdays.includes(index) ? 'primary' : 'secondary'
                          }
                          onClick={() =>
                            setForm({
                              ...form,
                              weekdays: form.weekdays.includes(index)
                                ? form.weekdays.filter((d) => d !== index)
                                : [...form.weekdays, index],
                            })
                          }
                        >
                          {label.replace('周', '')}
                        </Button>
                      )
                    )}
                  </>
                )}
              </div>
            )}

            {form.triggerType === 'once' && (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span className="oc-list-sub">执行时间</span>
                <Input
                  value={form.onceAt}
                  onChange={(_e, d) => setForm({ ...form, onceAt: d.value })}
                  placeholder="2026-10-01 08:00"
                  style={{ width: 200 }}
                />
              </div>
            )}

            {form.triggerType === 'boot' && (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span className="oc-list-sub">开机后延迟（秒）</span>
                <Input
                  value={String(form.bootDelay)}
                  onChange={(_e, d) => setForm({ ...form, bootDelay: Number(d.value) || 60 })}
                  style={{ width: 100 }}
                />
              </div>
            )}

            <div>
              <Button appearance="primary" onClick={() => void submit()} disabled={busy}>
                保存任务
              </Button>
            </div>
          </div>
        )}

        {items.length === 0 ? (
          <div className="oc-hint" style={{ marginTop: 8 }}>
            还没有任务。常见用法：每天 07:50 跑一次体检；每周五 17:00 清理临时文件；
            开机 60 秒后打开白板软件。
          </div>
        ) : (
          <div className="oc-list" style={{ marginTop: 8 }}>
            {items.map((item) => {
              const info = byId(item.id)
              return (
                <div className="oc-list-row" key={item.id}>
                  <div className="oc-list-main">
                    <div className="oc-list-title">
                      {item.name}
                      <span className="oc-level safe" style={{ marginLeft: 8 }}>
                        {kinds[item.kind] ?? item.kind}
                      </span>
                      {!item.enabled && (
                        <span className="oc-level warn" style={{ marginLeft: 6 }}>
                          已停用
                        </span>
                      )}
                    </div>
                    <div className="oc-list-sub">
                      {info?.when ?? ''} · 执行 {item.runCount ?? 0} 次
                      {item.lastRun ? ` · 上次 ${item.lastRun}` : ''}
                    </div>
                    <div className="oc-list-sub" style={{ userSelect: 'text' }}>
                      {item.target}
                    </div>
                    {item.lastResult && <div className="oc-list-sub">结果：{item.lastResult}</div>}
                  </div>
                  <div className="oc-actions">
                    <Button
                      size="small"
                      appearance="secondary"
                      disabled={busy}
                      onClick={() => void act(() => api.tasks_run_now(item.id))}
                    >
                      立即执行
                    </Button>
                    <Button
                      size="small"
                      appearance="secondary"
                      disabled={busy}
                      onClick={() =>
                        void act(() =>
                          api.tasks_update(item.id, { enabled: !item.enabled })
                        )
                      }
                    >
                      {item.enabled ? '停用' : '启用'}
                    </Button>
                    <Button
                      size="small"
                      appearance="transparent"
                      disabled={busy}
                      onClick={() => {
                        if (window.confirm(`删除任务「${item.name}」？`)) {
                          void act(() => api.tasks_remove(item.id))
                        }
                      }}
                    >
                      删除
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {msg && (
          <div className="oc-list-sub" style={{ marginTop: 8 }}>
            {msg}
          </div>
        )}
        {busy && (
          <div className="oc-list-sub" style={{ marginTop: 6 }}>
            <Spinner size="tiny" /> 处理中…
          </div>
        )}
      </div>

      <div className="oc-panel" style={{ marginTop: 12 }}>
        <div className="oc-panel-title">执行记录（最近 {logs.length} 条）</div>
        {logs.length === 0 ? (
          <div className="oc-hint">还没有执行记录。</div>
        ) : (
          <div className="oc-list oc-scroll">
            {logs.map((log, index) => (
              <div className="oc-list-row" key={`${log.ts}-${index}`}>
                <div className="oc-list-main">
                  <div className="oc-list-title">
                    {log.name}
                    <span
                      className={`oc-level ${log.ok ? 'safe' : 'warn'}`}
                      style={{ marginLeft: 8 }}
                    >
                      {log.ok ? '成功' : '失败'}
                    </span>
                  </div>
                  <div className="oc-list-sub">
                    {timeText(log.ts)} · 耗时 {log.elapsed}s · {log.detail}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
