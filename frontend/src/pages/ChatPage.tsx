import { useEffect, useState } from 'react'
import { Button, Input, Spinner } from '@fluentui/react-components'
import { api } from '../api'

function formatSize(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1048576).toFixed(1)} MB`
}

/**
 * 局域网临时文件传输 —— 手机、电脑在同一 WiFi 下互传文件。
 *
 * 2026-10：从「临时聊天传输」改来。原先要记两个码（访问码 + 房间码）、
 * 开两个页面；现在并入手机控制台 —— 一个访问码、一个地方。
 * 文字聊天已从界面移除（后端接口保留，想恢复只要把界面加回来）。
 */
export default function ChatPage() {
  const [state, setState] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [form, setForm] = useState({ host: '', code: '', nickname: '' })
  const role = state?.role ?? ''

  const load = async () => {
    try {
      setState(await api.transfer_state())
    } catch {
      /* 忽略 */
    }
  }

  useEffect(() => {
    void load()
  }, [])

  // 连着对方时持续刷新：对方的开关状态、对方发来的文件
  useEffect(() => {
    if (role !== 'member') return
    const timer = window.setInterval(() => void load(), 3000)
    return () => window.clearInterval(timer)
  }, [role])

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

  const pickAndSend = async () => {
    const picked = await api.chat_pick_file()
    if (!picked?.ok) {
      setMsg(picked?.message ?? '未选择文件')
      return
    }
    await act(() => api.chat_send_file(picked.path))
  }

  const copyAddress = async () => {
    const text = state?.hostUrl ?? ''
    try {
      await navigator.clipboard.writeText(text)
      setMsg('已复制本机地址')
    } catch {
      setMsg('本机地址：' + text)
    }
  }

  const files: any[] = state?.files ?? []
  const incoming: any[] = state?.incoming ?? []
  // 接收方看"我收到的"，发送方看"对方发来的" —— 同一份列表模板，两种数据源
  const shown = role === 'member' ? incoming : files
  const maxFile = Math.round((state?.maxFile ?? 0) / 1048576)

  // ── 空闲：开启接收，或连接另一台机器 ──
  if (role !== 'host' && role !== 'member') {
    return (
      <div className="oc-page">
        <div className="oc-page-header">
          <div className="oc-page-title">局域网临时文件传输</div>
          <div className="oc-page-desc">
            同一 WiFi 下互传文件。手机打开「手机控制台」输入访问码即可传；电脑之间也用同一个访问码
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 12 }}>
          <div className="oc-panel">
            <div className="oc-panel-title">让这台电脑接收</div>
            <div className="oc-usage-sub" style={{ marginBottom: 10 }}>
              开启后，手机或其他电脑用「手机控制台」的访问码就能把文件发进来。
              {maxFile > 0 ? ` 单个文件不超过 ${maxFile} MB。` : ''}
            </div>
            <Button
              appearance="primary"
              disabled={busy}
              onClick={() => void act(() => api.transfer_set_accepting(true))}
            >
              开启接收
            </Button>
            <div className="oc-hint" style={{ marginTop: 8 }}>
              不用记房间码 —— 访问码就在「设置 → 手机控制台」，和看状态用的是同一个。
            </div>
          </div>

          <div className="oc-panel">
            <div className="oc-panel-title">往另一台电脑发</div>
            <div style={{ display: 'grid', gap: 8 }}>
              <Input
                placeholder="对方地址（如 192.168.0.105 或 192.168.0.105:38610）"
                value={form.host}
                onChange={(_e, d) => setForm({ ...form, host: d.value })}
              />
              <Input
                placeholder="对方的访问码（6 位数字，在对方「设置 → 手机控制台」）"
                value={form.code}
                onChange={(_e, d) => setForm({ ...form, code: d.value })}
              />
              <Input
                placeholder="我的昵称（对方能看到，便于认出来）"
                value={form.nickname}
                onChange={(_e, d) => setForm({ ...form, nickname: d.value })}
              />
              <Button
                appearance="primary"
                disabled={busy}
                onClick={() =>
                  void act(() => api.chat_join(form.host, form.code, form.nickname || '同事'))
                }
              >
                连接
              </Button>
            </div>
            <div className="oc-hint" style={{ marginTop: 8 }}>
              对方要先点「开启接收」，不然传过去也没人收。
            </div>
          </div>
        </div>

        {msg && (
          <div className="oc-list-sub" style={{ marginTop: 10 }}>
            {msg}
          </div>
        )}
      </div>
    )
  }

  // ── 已在传输中 ──
  const isHost = role === 'host'
  return (
    <div className="oc-page">
      <div className="oc-page-header">
        <div className="oc-page-title">
          局域网临时文件传输
          <span className="oc-list-sub" style={{ marginLeft: 10, fontWeight: 400 }}>
            {isHost ? '本机正在接收' : '已连接对方'}
            {state.error ? ' · ' + state.error : ''}
          </span>
        </div>
        <div className="oc-page-desc">
          {isHost
            ? '手机或其他电脑用「手机控制台」的访问码就能把文件发进来'
            : '对方地址 ' + (state.hostUrl || '')}
        </div>
      </div>

      <div className="oc-panel" style={{ marginBottom: 12 }}>
        <div className="oc-actions">
          {isHost ? (
            <Button size="small" appearance="secondary" onClick={() => void copyAddress()}>
              复制本机地址
            </Button>
          ) : (
            <Button
              size="small"
              appearance="secondary"
              onClick={() => void pickAndSend()}
              disabled={busy || !state.remoteAccepting}
            >
              发送文件…
            </Button>
          )}
          <Button
            size="small"
            appearance="secondary"
            onClick={() => void act(() => api.chat_clear_received())}
            disabled={busy}
          >
            清空已收文件
          </Button>
          <Button
            size="small"
            onClick={() => {
              const ask = isHost ? '关闭接收？别人就传不进来了。' : '断开连接？'
              if (window.confirm(ask)) void act(() => api.chat_leave(false))
            }}
            disabled={busy}
          >
            {isHost ? '关闭接收' : '断开连接'}
          </Button>
          {!isHost && (
            <span className="oc-list-sub">
              {state.remoteAccepting ? '对方正在接收' : '对方还没有开启接收'}
            </span>
          )}
        </div>
      </div>

      <div className="oc-panel">
        <div className="oc-panel-title">{isHost ? '收到的文件' : '对方发来的文件'}</div>
        <div className="oc-list" style={{ marginTop: 6 }}>
          {shown.length === 0 && (
            <div className="oc-hint">
              {isHost ? '还没有人传文件过来。' : '对方还没有发文件过来。'}
            </div>
          )}
          {shown.map((one: any) => (
            <div className="oc-list-row" key={one.id}>
              <div className="oc-list-main">
                <div className="oc-list-title">{one.name}</div>
                <div className="oc-list-sub">
                  {formatSize(one.size)}
                  {one.from ? ` · 来自 ${one.from}` : ''}
                  {one.at ? ` · ${one.at}` : ''}
                </div>
              </div>
              {!isHost && (
                <Button
                  size="small"
                  appearance="secondary"
                  disabled={busy}
                  onClick={() => void act(() => api.chat_save_file(one.id, one.name))}
                >
                  下载到本机
                </Button>
              )}
            </div>
          ))}
        </div>
        {state.dir && (
          <div className="oc-hint" style={{ marginTop: 8 }}>
            收到的文件放在：{state.dir}
          </div>
        )}
        {msg && (
          <div className="oc-list-sub" style={{ marginTop: 6 }}>
            {msg}
          </div>
        )}
        {busy && (
          <div className="oc-list-sub" style={{ marginTop: 6 }}>
            <Spinner size="tiny" /> 处理中…
          </div>
        )}
      </div>
    </div>
  )
}
