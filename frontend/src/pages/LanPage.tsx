import { useEffect, useState } from 'react'
import { Button, Input, Spinner } from '@fluentui/react-components'
import { api } from '../api'

const ACTION_GROUPS: { id: string; label: string; payload?: () => any }[] = [
  { id: 'checkup', label: '一键体检' },
  { id: 'repair', label: '一键修复' },
  { id: 'cleanup', label: '磁盘清理' },
]

function formatSize(bytes?: number): string {
  if (!bytes || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`
}

function levelClass(value: number | undefined): string {
  if (value === undefined || value === null) return ''
  if (value >= 88) return 'warn'
  if (value >= 70) return 'warn'
  return ''
}

function MiniBar({ label, percent }: { label: string; percent?: number }) {
  const value = typeof percent === 'number' ? Math.max(0, Math.min(100, percent)) : null
  return (
    <div style={{ minWidth: 92 }}>
      <div className="oc-list-sub" style={{ marginBottom: 3 }}>
        {label} {value === null ? '—' : `${value}%`}
      </div>
      <div className="oc-usage-track">
        <div
          className={`oc-usage-fill ${value !== null && value >= 88 ? 'crit' : levelClass(value ?? undefined)}`}
          style={{ width: `${value ?? 0}%` }}
        />
      </div>
    </div>
  )
}

function formatTime(ts?: number): string {
  if (!ts) return '—'
  try {
    return new Date(ts).toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    return '—'
  }
}

export default function LanPage() {
  const [status, setStatus] = useState<any>(null)
  const [nodes, setNodes] = useState<any[]>([])
  const [events, setEvents] = useState<any[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(true)

  const [scanResult, setScanResult] = useState<any[]>([])
  const [scanning, setScanning] = useState(false)
  const [serverUrl, setServerUrl] = useState('')
  const [code, setCode] = useState('')
  const [messageText, setMessageText] = useState('')
  const [inbox, setInbox] = useState<any[]>([])
  const [collectPath, setCollectPath] = useState('')
  const [receivePath, setReceivePath] = useState('')
  const [portInput, setPortInput] = useState('38900')
  const [proxyInput, setProxyInput] = useState('')
  const [groupFilter, setGroupFilter] = useState('')
  const [schedule, setSchedule] = useState<any>(null)
  const [scheduleTime, setScheduleTime] = useState('08:00')
  const [scheduleAction, setScheduleAction] = useState('checkup')

  const notify = (text: string) => {
    setMsg(text)
    window.setTimeout(() => setMsg(''), 4000)
  }

  const pushFile = async () => {
    if (!selected.length) {
      notify('请先勾选要接收文件的设备')
      return
    }
    const picked = await api.lan_pick_file()
    if (!picked?.ok) {
      notify(picked?.message ?? '未选择文件')
      return
    }
    notify('正在下发，请稍候…')
    const result = await api.lan_push_file(selected, picked.path)
    notify(result?.message ?? '')
    await load(true)
  }

  const saveNetwork = async () => {
    const port = Number(portInput.trim() || '38900')
    if (!Number.isFinite(port) || port < 1024 || port > 65535) {
      notify('端口需在 1024–65535 之间')
      return
    }
    const result = await api.lan_set_config(port, proxyInput.trim())
    notify(result?.message ?? '')
    await load(true)
  }

  const saveSchedule = async (enabled: boolean) => {
    const result = await api.lan_set_schedule(enabled, scheduleTime.trim(), scheduleAction.trim())
    notify(result?.message ?? '')
    await load(true)
  }

  const editMeta = async (node: any) => {
    const alias = window.prompt('设备备注名（留空则用原始名称）', node.alias || '')
    if (alias === null) return
    const group = window.prompt('分组 / 教室（留空则不分组）', node.group || '')
    if (group === null) return
    const result = await api.lan_set_node_meta(node.id, alias, group)
    notify(result?.message ?? '')
    await load(true)
  }

  const load = async (silent = false) => {
    if (!silent) setBusy(true)
    try {
      const [s, n, e, box, recv, cfg, sch] = await Promise.all([
        api.lan_status(),
        api.lan_nodes(),
        api.lan_events(60),
        api.lan_inbox(),
        api.lan_receive_dir(),
        api.lan_config(),
        api.lan_schedule(),
      ])
      setStatus(s)
      setNodes(n ?? [])
      setEvents(e ?? [])
      setInbox(box ?? [])
      setReceivePath(recv ?? '')
      setPortInput(String(cfg?.port ?? 38900))
      setProxyInput(cfg?.proxy ?? '')
      setSchedule(sch)
      setScheduleTime(String(sch?.time ?? '08:00'))
      setScheduleAction(String(sch?.action ?? 'checkup'))
    } catch {
      /* 首次读取失败保持空态 */
    } finally {
      if (!silent) setBusy(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  // 学生机心跳是 5 秒，这里 3 秒拉一次保证列表"活"
  useEffect(() => {
    const timer = window.setInterval(() => void load(true), 3000)
    return () => window.clearInterval(timer)
  }, [])

  const mode = status?.mode ?? 'single'
  const server = status?.server
  const client = status?.client
  const onlineNodes = nodes.filter((node) => node.online)

  // B 端（被管理端）：只展示"被谁管理 + 连接状态"。
  // B 端不提供主动连接别的机器，连接配置统一收在「设置 → 被链接（B 端）」，
  // 避免学生在一体机上乱点把机器接到错误的老师机。
  if (mode === 'student') {
    const stateText =
      client?.state === 'connected'
        ? '已连接老师机'
        : client?.state === 'connecting'
          ? '正在连接…'
          : client?.state === 'error'
            ? '连接异常'
            : '未连接'
    return (
      <div className="oc-page">
        <div className="oc-page-header">
          <div>
            <div className="oc-page-title">机房管理</div>
            <div className="oc-page-desc">
              本机是 B 端（被管理端）：由老师机统一管理，不主动连接其他机器
            </div>
          </div>
          <div className="oc-device">
            <div className="oc-device-model">B 端 · 被管理</div>
            <div className="oc-device-sub">{client?.message ?? ''}</div>
          </div>
        </div>

        <div className="oc-panel">
          <div className="oc-info-row">
            <span>连接状态</span>
            <span>{stateText}</span>
          </div>
          <div className="oc-info-row">
            <span>老师机</span>
            <span className="oc-mono">
              {client?.teacher || client?.server || '（未连接）'}
            </span>
          </div>
          <div className="oc-info-row">
            <span>本机标识</span>
            <span className="oc-mono">{client?.nodeId || '—'}</span>
          </div>
          <div className="oc-info-row">
            <span>配对状态</span>
            <span>{client?.paired ? '已配对' : '未配对'}</span>
          </div>
          <div className="oc-actions" style={{ marginTop: 12 }}>
            <Button
              size="small"
              appearance="secondary"
              onClick={() => void load(true)}
            >
              刷新状态
            </Button>
            <Button
              size="small"
              appearance="secondary"
              disabled={!client?.enabled}
              onClick={() =>
                void (async () => {
                  const result = await api.lan_leave()
                  notify(result?.message ?? '')
                  await load(true)
                })()
              }
            >
              断开与老师机的连接
            </Button>
          </div>
          <div className="oc-hint" style={{ marginTop: 10 }}>
            连接配置（老师机地址 / 配对码）在「设置 → 被链接（B 端）」里填写；
            老师机下达的体检、修复、清理、换壁纸等指令会在本机执行并回报结果。
          </div>
        </div>
      </div>
    )
  }

  const startServer = async () => {
    const result = await api.lan_start_server(38900)
    notify(result?.message ?? '')
    await load(true)
  }

  const stopServer = async () => {
    const result = await api.lan_stop_server()
    notify(result?.message ?? '')
    await load(true)
  }

  const resetCode = async () => {
    const value = await api.lan_reset_code()
    notify(`新的配对码：${value}`)
    await load(true)
  }

  // ── 远程电源控制（危险操作：默认关闭、延迟执行、二次确认）──
  const [powerStatus, setPowerStatus] = useState<any>(null)
  const [powerDelay, setPowerDelay] = useState(30)

  const loadPower = async () => {
    try {
      const data = await api.power_control_status()
      setPowerStatus(data)
      setPowerDelay(Number(data?.defaultDelay) || 30)
    } catch {
      /* 忽略 */
    }
  }

  useEffect(() => {
    void loadPower()
  }, [])

  const doPower = async (action: string, name: string, danger: boolean) => {
    if (!selected.length) {
      notify('请先勾选要操作的设备')
      return
    }
    const tip = danger
      ? `确定让 ${selected.length} 台设备「${name}」吗？\n\n` +
        `· ${powerDelay} 秒后执行，期间可以在同一位置点「取消关机」\n` +
        `· 请先确认这些机器上没有未保存的课件或作业`
      : `确定让 ${selected.length} 台设备「${name}」吗？`
    if (!window.confirm(tip)) return
    await sendAction('power', { mode: action, delay: powerDelay })
  }

  const sendAction = async (action: string, payload?: any) => {
    if (!selected.length) {
      notify('请先勾选要操作的设备')
      return
    }
    const result = await api.lan_send(selected, action, payload ?? {})
    notify(result?.message ?? '')
    await load(true)
  }

  const doScan = async () => {
    setScanning(true)
    try {
      const found = await api.lan_scan()
      setScanResult(found ?? [])
      notify(found?.length ? `发现 ${found.length} 台老师机` : '未发现老师机')
    } finally {
      setScanning(false)
    }
  }

  const join = async (url = '') => {
    const target = url || serverUrl
    if (!code.trim()) {
      notify('请填写老师机上显示的 6 位配对码')
      return
    }
    const result = await api.lan_join(target, code.trim())
    notify(result?.message ?? '')
    await load(true)
  }

  const leave = async () => {
    const result = await api.lan_leave()
    notify(result?.message ?? '')
    await load(true)
  }

  const toggleSelect = (id: string) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]))
  }

  const toggleAll = () => {
    setSelected(selected.length === nodes.length ? [] : nodes.map((node) => node.id))
  }

  const removeNode = async (id: string) => {
    const result = await api.lan_remove_node(id)
    notify(result?.message ?? '')
    setSelected((prev) => prev.filter((item) => item !== id))
    await load(true)
  }

  return (
    <div className="oc-page">
      <div className="oc-page-header">
        <div>
          <div className="oc-page-title">机房管理</div>
          <div className="oc-page-desc">
            A 端＝服务端（管理端）· B 端＝本体（被管理端）· 局域网直连，也可经代理跨网段
          </div>
        </div>
        <div className="oc-device">
          <div className="oc-device-model">
            {mode === 'teacher' ? 'A 端 · 服务端' : mode === 'student' ? 'B 端 · 本体' : '单机模式'}
          </div>
          <div className="oc-device-sub">
            {mode === 'teacher'
              ? `在线 ${server?.onlineCount ?? 0} / 共 ${server?.nodeCount ?? 0} 台`
              : mode === 'student'
                ? (client?.message ?? '')
                : '未启用机房协同'}
          </div>
        </div>
      </div>

      {/* ── 老师机面板 ── */}
      <div className="oc-panel">
        <div className="oc-panel-title">A 端 · 服务端（管理端）</div>
        {server?.running ? (
          <>
            <div className="oc-stats" style={{ marginTop: 8 }}>
              <div className="oc-stat">
                <div className="oc-stat-value">{server.pairingCode}</div>
                <div className="oc-stat-label">配对码（学生机输入）</div>
              </div>
              <div className="oc-stat">
                <div className="oc-stat-value">{server.onlineCount ?? 0}</div>
                <div className="oc-stat-label">在线设备</div>
              </div>
              <div className="oc-stat">
                <div className="oc-stat-value">{server.nodeCount ?? 0}</div>
                <div className="oc-stat-label">已配对数</div>
              </div>
              <div className="oc-stat">
                <div className="oc-stat-value">{server.port}</div>
                <div className="oc-stat-label">服务端口</div>
              </div>
              <div className={`oc-stat ${(server.offlineCount ?? 0) > 0 ? 'danger' : ''}`}>
                <div className="oc-stat-value">{server.offlineCount ?? 0}</div>
                <div className="oc-stat-label">离线设备</div>
              </div>
            </div>
            <div className="oc-searchbar" style={{ marginTop: 12, flexWrap: 'wrap' }}>
              <span className="oc-list-sub">服务端口</span>
              <Input
                value={portInput}
                onChange={(_e, data) => setPortInput(data.value)}
                style={{ width: 108 }}
              />
              <Button appearance="secondary" onClick={saveNetwork}>
                保存配置
              </Button>
              <span className="oc-list-sub">
                改端口后需重启服务；跨网段可用端口映射 / 内网穿透，B 端填映射后的地址
              </span>
            </div>
            <div className="oc-actions" style={{ marginTop: 10 }}>
              <Button appearance="secondary" onClick={resetCode}>
                重置配对码
              </Button>
              <Button appearance="secondary" onClick={stopServer}>
                停止服务
              </Button>
            </div>

            {/* 定时任务：每天按时对在线设备下发指令 */}
            <div
              style={{
                marginTop: 14,
                borderTop: '1px solid var(--oc-border)',
                paddingTop: 12,
              }}
            >
              <div className="oc-list-title">定时任务</div>
              <div className="oc-searchbar" style={{ marginTop: 8, flexWrap: 'wrap' }}>
                <span className="oc-list-sub">时间</span>
                <Input
                  value={scheduleTime}
                  onChange={(_e, data) => setScheduleTime(data.value)}
                  placeholder="HH:MM"
                  style={{ width: 92 }}
                />
                <span className="oc-list-sub">动作</span>
                <Input
                  value={scheduleAction}
                  onChange={(_e, data) => setScheduleAction(data.value)}
                  placeholder="checkup"
                  style={{ width: 112 }}
                />
                <Button appearance="primary" onClick={() => void saveSchedule(true)}>
                  启用
                </Button>
                <Button appearance="secondary" onClick={() => void saveSchedule(false)}>
                  停用
                </Button>
              </div>
              <div className="oc-list-sub" style={{ marginTop: 6 }}>
                {schedule?.enabled
                  ? `已启用：每天 ${schedule.time} 对${
                      (schedule.groups ?? []).length
                        ? '「' + (schedule.groups ?? []).join('、') + '」'
                        : '全部'
                    }设备执行 ${schedule.action}${
                      schedule.lastRun ? ` · 上次执行 ${schedule.lastRun}` : ''
                    }`
                  : '未启用 · 可设为每天自动体检（只对在线设备下发）'}
              </div>
            </div>
          </>
        ) : (
          <div style={{ marginTop: 8 }}>
            <div className="oc-hint">
              启动后本机成为老师机：学生机会自动发现你（或手动填写本机 IP），
              输入配对码即可加入管理。
            </div>
            <div className="oc-searchbar" style={{ marginTop: 10, flexWrap: 'wrap' }}>
              <span className="oc-list-sub">服务端口</span>
              <Input
                value={portInput}
                onChange={(_e, data) => setPortInput(data.value)}
                style={{ width: 108 }}
              />
              <Button
                appearance="primary"
                onClick={async () => {
                  await api.lan_set_config(Number(portInput.trim() || '38900'))
                  await startServer()
                }}
              >
                启动管理服务
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* ── 学生机面板 ── */}
      {mode !== 'teacher' && (
        <div className="oc-panel" style={{ marginTop: 12 }}>
          <div className="oc-panel-title">B 端 · 本体（加入服务端）</div>
          {client?.state === 'connected' ? (
            <>
              <div className="oc-list-sub" style={{ marginTop: 6 }}>
                已连接：{client.teacher}（{client.server}）· 节点 ID {client.nodeId}
              </div>
              <div className="oc-list-sub">最近上报：{formatTime(client.lastReport)}</div>
              <div className="oc-list-sub">接收目录：{receivePath || '—'}</div>
              <div className="oc-actions" style={{ marginTop: 10 }}>
                <Button appearance="secondary" onClick={() => void api.lan_open_receive_dir()}>
                  打开接收目录
                </Button>
                <Button appearance="secondary" onClick={leave}>
                  断开连接
                </Button>
              </div>
            </>
          ) : (
            <>
              <div className="oc-searchbar" style={{ marginTop: 8, marginBottom: 8 }}>
                <Input
                  value={proxyInput}
                  onChange={(_e, data) => setProxyInput(data.value)}
                  placeholder="代理（可选，跨网段时填 http://IP:端口）"
                  style={{ flex: 1 }}
                />
                <Button appearance="secondary" onClick={saveNetwork}>
                  保存代理
                </Button>
              </div>
              <div className="oc-searchbar" style={{ marginBottom: 8 }}>
                <Button appearance="secondary" onClick={doScan} disabled={scanning}>
                  {scanning ? '搜索中…' : '搜索服务端（A 端）'}
                </Button>
                {scanResult.length > 0 && (
                  <span className="oc-list-sub">发现 {scanResult.length} 台，点击即填入</span>
                )}
              </div>
              {scanResult.length > 0 && (
                <div className="oc-list" style={{ marginBottom: 8 }}>
                  {scanResult.map((item) => (
                    <div className="oc-list-row" key={item.ip}>
                      <div className="oc-list-main">
                        <div className="oc-list-title">{item.name || item.ip}</div>
                        <div className="oc-list-sub">
                          {item.ip}:{item.httpPort ?? 38900}
                        </div>
                      </div>
                      <Button
                        size="small"
                        onClick={() => {
                          setServerUrl(`http://${item.ip}:${item.httpPort ?? 38900}`)
                          void join(`http://${item.ip}:${item.httpPort ?? 38900}`)
                        }}
                      >
                        连接
                      </Button>
                    </div>
                  ))}
                </div>
              )}
              <div className="oc-searchbar">
                <Input
                  value={serverUrl}
                  onChange={(_e, data) => setServerUrl(data.value)}
                  placeholder="老师机地址（可留空：自动发现）"
                  style={{ flex: 1 }}
                />
                <Input
                  value={code}
                  onChange={(_e, data) => setCode(data.value)}
                  placeholder="6 位配对码"
                  style={{ width: 140 }}
                />
                <Button appearance="primary" onClick={() => void join()}>
                  加入
                </Button>
                {client?.enabled && (
                  <Button appearance="secondary" onClick={leave}>
                    停止
                  </Button>
                )}
              </div>
              {client?.message && (
                <div className="oc-list-sub" style={{ marginTop: 8 }}>
                  状态：{client.message}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {msg && (
        <div className="oc-panel" style={{ marginTop: 12 }}>
          <div className="oc-list-sub">{msg}</div>
        </div>
      )}

      {/* ── 在线设备 ── */}
      {server?.running && (
        <div className="oc-panel" style={{ marginTop: 12 }}>
          <div className="oc-panel-title">
            在线设备（{onlineNodes.length} / {nodes.length}）
            <Button
              size="small"
              appearance="secondary"
              style={{ marginLeft: 10 }}
              onClick={toggleAll}
              disabled={nodes.length === 0}
            >
              {selected.length === nodes.length && nodes.length > 0 ? '取消全选' : '全选'}
            </Button>
          </div>

          {(server?.groups ?? []).length > 0 && (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
              <Button
                size="small"
                appearance={groupFilter === '' ? 'primary' : 'subtle'}
                onClick={() => setGroupFilter('')}
              >
                全部分组
              </Button>
              {(server?.groups ?? []).map((group: string) => (
                <Button
                  key={group}
                  size="small"
                  appearance={groupFilter === group ? 'primary' : 'subtle'}
                  onClick={() => setGroupFilter(group)}
                >
                  {group}
                </Button>
              ))}
              {groupFilter && (
                <Button
                  size="small"
                  appearance="secondary"
                  onClick={() =>
                    setSelected(
                      nodes
                        .filter((item) => (item.group || '') === groupFilter)
                        .map((item) => item.id)
                    )
                  }
                >
                  选中本组
                </Button>
              )}
            </div>
          )}

          {nodes.length === 0 ? (
            <div className="oc-hint" style={{ marginTop: 6 }}>
              还没有学生机加入。请让学生机打开本程序 →「机房管理」→ 填入配对码{' '}
              {server?.pairingCode ?? ''} 加入。
            </div>
          ) : (
            <div className="oc-toolgrid" style={{ marginTop: 10 }}>
              {nodes
                .filter((item) => !groupFilter || (item.group || '') === groupFilter)
                .map((node) => (
                <div
                  className="oc-toolcard"
                  key={node.id}
                  style={{
                    cursor: 'pointer',
                    borderColor: selected.includes(node.id) ? 'var(--oc-accent)' : undefined,
                    opacity: node.online ? 1 : 0.55,
                  }}
                  onClick={() => toggleSelect(node.id)}
                >
                  <div className="oc-toolcard-top">
                    <input
                      type="checkbox"
                      checked={selected.includes(node.id)}
                      onChange={() => toggleSelect(node.id)}
                      onClick={(event) => event.stopPropagation()}
                    />
                    <div className="oc-toolcard-head">
                      <div className="oc-toolcard-name">{node.displayName || node.name}</div>
                      <div className="oc-toolcard-meta">
                        {node.group ? `[${node.group}] ` : ''}
                        {node.ip} · {node.online ? '在线' : `离线（${formatTime(node.lastSeen)}）`}
                      </div>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                    <MiniBar label="CPU" percent={node.info?.cpu} />
                    <MiniBar label="内存" percent={node.info?.memory} />
                    <MiniBar label="磁盘" percent={node.info?.disk} />
                  </div>
                  <div className="oc-list-sub">
                    {node.info?.user ? `${node.info.user} · ` : ''}
                    {node.info?.model || node.info?.os || ''}
                    {node.info?.foreground ? ` · 前台：${node.info.foreground}` : ''}
                  </div>
                  <div className="oc-actions">
                    <Button
                      size="small"
                      appearance="subtle"
                      onClick={(event) => {
                        event.stopPropagation()
                        void editMeta(node)
                      }}
                    >
                      备注/分组
                    </Button>
                    <Button
                      size="small"
                      appearance="subtle"
                      onClick={(event) => {
                        event.stopPropagation()
                        void removeNode(node.id)
                      }}
                    >
                      移除
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {nodes.length > 0 && (
            <div className="oc-actions" style={{ marginTop: 12 }}>
              {ACTION_GROUPS.map((item) => (
                <Button key={item.id} appearance="secondary" onClick={() => void sendAction(item.id)}>
                  {item.label}
                </Button>
              ))}
              <Button
                appearance="secondary"
                onClick={() => void sendAction('power', { mode: 'lock' })}
              >
                锁屏
              </Button>
            </div>
          )}

          {nodes.length > 0 && (
            <div className="oc-searchbar" style={{ marginTop: 10, flexWrap: 'wrap' }}>
              <Button appearance="primary" onClick={pushFile}>
                下发文件到选中设备…
              </Button>
              <Input
                value={collectPath}
                onChange={(_e, data) => setCollectPath(data.value)}
                placeholder="收作业目录（学生机上的路径，留空＝桌面）"
                style={{ flex: 1, minWidth: 220 }}
              />
              <Button
                appearance="secondary"
                onClick={() =>
                  void sendAction(
                    'pull_file',
                    collectPath.trim() ? { paths: [collectPath.trim()] } : {}
                  )
                }
              >
                收作业
              </Button>
            </div>
          )}

          {nodes.length > 0 && (
            <>
            <div className="oc-searchbar" style={{ marginTop: 10 }}>
              <Input
                value={messageText}
                onChange={(_e, data) => setMessageText(data.value)}
                placeholder="要在学生机弹出的消息内容"
                style={{ flex: 1 }}
              />
              <Button
                appearance="secondary"
                onClick={() => {
                  if (!messageText.trim()) {
                    notify('请先输入消息内容')
                    return
                  }
                  void sendAction('message', { text: messageText.trim() })
                }}
              >
                发送消息
              </Button>
            </div>

            {/* 电源控制：关机 / 重启 / 睡眠 / 休眠 / 取消关机 */}
            <div
              style={{
                display: 'flex',
                gap: 5,
                flexWrap: 'wrap',
                alignItems: 'center',
                marginTop: 10,
                paddingTop: 10,
                borderTop: '1px solid var(--oc-border)',
              }}
            >
              <span className="oc-list-sub">电源控制</span>
              {(powerStatus?.actions ?? []).map((item: any) => (
                <Button
                  key={item.key}
                  size="small"
                  appearance={item.danger ? 'primary' : 'secondary'}
                  onClick={() => void doPower(item.key, item.name, Boolean(item.danger))}
                >
                  {item.name}
                </Button>
              ))}
              <Input
                value={String(powerDelay)}
                onChange={(_e, data) => setPowerDelay(Number(data.value) || 30)}
                style={{ width: 76 }}
              />
              <span className="oc-list-sub">秒后执行</span>
            </div>
            <div className="oc-hint" style={{ marginTop: 6 }}>
              {powerStatus?.allowed
                ? '本机已允许被远程控制电源。'
                : '被控机器默认拒绝电源指令：需在那些机器的「设置 → 系统集成」里开启「允许远程电源控制」，否则关机 / 重启 / 睡眠会被拒绝（「取消关机」始终可用）。'}
            </div>
            </>
          )}
        </div>
      )}

      {/* ── 收件箱（收作业归档） ── */}
      {server?.running && (
        <div className="oc-panel" style={{ marginTop: 12 }}>
          <div className="oc-panel-title">
            收件箱（{inbox.length}）
            <Button
              size="small"
              appearance="secondary"
              style={{ marginLeft: 10 }}
              onClick={() => void api.lan_open_inbox()}
            >
              打开目录
            </Button>
          </div>
          {inbox.length === 0 ? (
            <div className="oc-hint">
              还没有收到文件。点「收作业」后，学生机打包的结果会归档到这里。
            </div>
          ) : (
            <div className="oc-list oc-scroll">
              {inbox.map((item, index) => (
                <div className="oc-list-row" key={index}>
                  <div className="oc-list-main">
                    <div className="oc-list-title">{item.name}</div>
                    <div className="oc-list-sub">
                      {item.node} · {formatSize(item.size)} · {formatTime(item.time)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── 事件日志 ── */}
      {server?.running && (
        <div className="oc-panel" style={{ marginTop: 12 }}>
          <div className="oc-panel-title">操作日志（{events.length}）</div>
          {events.length === 0 ? (
            <div className="oc-hint">还没有操作记录。</div>
          ) : (
            <div className="oc-list oc-scroll">
              {events.map((item, index) => (
                <div className="oc-list-row" key={index}>
                  <div className="oc-list-main">
                    <div className="oc-list-title">
                      <span className={`oc-level ${item.ok ? 'safe' : 'danger'}`}>
                        {item.ok ? '成功' : '失败'}
                      </span>
                      <span style={{ marginLeft: 8 }}>
                        {item.nodeName || '系统'} · {item.action}
                      </span>
                    </div>
                    <div className="oc-list-sub">
                      {formatTime(item.ts)} · {item.detail} {item.message ? `· ${item.message}` : ''}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {busy && !status && (
        <div className="oc-empty">
          <Spinner size="small" label="读取机房状态…" />
        </div>
      )}
    </div>
  )
}
