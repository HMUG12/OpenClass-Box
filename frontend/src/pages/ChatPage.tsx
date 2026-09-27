import { useEffect, useRef, useState } from 'react'
import { Button, Input, Spinner } from '@fluentui/react-components'
import { api } from '../api'

function formatSize(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1048576).toFixed(1)} MB`
}

function timeText(ts: number): string {
  try {
    const date = new Date(ts)
    return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  } catch {
    return ''
  }
}

/**
 * 临时聊天传输 —— 局域网里用完即走的聊天与文件互传。
 * 谁开房间谁当主机，关掉程序房间就消失，不依赖任何服务器。
 */
export default function ChatPage() {
  const [state, setState] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [text, setText] = useState('')
  const [form, setForm] = useState({
    nickname: '',
    roomName: '',
    password: '',
    host: '',
    code: '',
  })
  const listRef = useRef<HTMLDivElement | null>(null)

  const load = async () => {
    try {
      setState(await api.chat_state())
    } catch {
      /* 忽略 */
    }
  }

  useEffect(() => {
    void load()
  }, [])

  // 在房间里时持续刷新（成员端消息由主机中转过来）
  useEffect(() => {
    if (!state?.active) return
    const timer = window.setInterval(() => void load(), 2000)
    return () => window.clearInterval(timer)
  }, [state?.active])

  // 有新消息时自动滚到底部
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [state?.messages?.length])

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

  const sendText = async () => {
    const value = text.trim()
    if (!value) return
    setText('')
    await act(() => api.chat_send_text(value))
  }

  const pickAndSend = async () => {
    const picked = await api.chat_pick_file()
    if (!picked?.ok) {
      setMsg(picked?.message ?? '未选择文件')
      return
    }
    await act(() => api.chat_send_file(picked.path))
  }

  const copyRoom = async () => {
    try {
      await navigator.clipboard.writeText(`${state?.hostUrl ?? ''} 房间码 ${state?.roomCode ?? ''}`)
      setMsg('已复制「主机地址 + 房间码」，发给同事即可')
    } catch {
      setMsg(`请手动告知同事：${state?.hostUrl} 房间码 ${state?.roomCode}`)
    }
  }

  const messages: any[] = state?.messages ?? []
  const members: any[] = state?.members ?? []

  // ── 未在房间：创建 / 加入 ──
  if (!state?.active) {
    return (
      <div className="oc-page">
        <div className="oc-page-header">
          <div className="oc-page-title">临时传输</div>
          <div className="oc-page-desc">
            同一 WiFi 下临时聊天与互传课件，输入同一个房间码即可；关掉程序房间就消失，不依赖服务器
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 12 }}>
          <div className="oc-panel">
            <div className="oc-panel-title">开一个房间（我当主机）</div>
            <div style={{ display: 'grid', gap: 8 }}>
              <Input
                placeholder="我的昵称（如：电教小王）"
                value={form.nickname}
                onChange={(_e, d) => setForm({ ...form, nickname: d.value })}
              />
              <Input
                placeholder="房间名（可选，如：三楼办公室）"
                value={form.roomName}
                onChange={(_e, d) => setForm({ ...form, roomName: d.value })}
              />
              <Input
                placeholder="房间密码（可选，留空则凭房间码加入）"
                value={form.password}
                onChange={(_e, d) => setForm({ ...form, password: d.value })}
              />
              <Button
                appearance="primary"
                disabled={busy}
                onClick={() =>
                  void act(() => api.chat_host(form.nickname || '我', form.roomName, form.password))
                }
              >
                创建房间
              </Button>
            </div>
            <div className="oc-hint" style={{ marginTop: 8 }}>
              本机会成为消息中转站：同事发的内容经过你转给他们。文件也会先存到你这台机器，
              可在房间里一键清空。
            </div>
          </div>

          <div className="oc-panel">
            <div className="oc-panel-title">加入同事的房间</div>
            <div style={{ display: 'grid', gap: 8 }}>
              <Input
                placeholder="主机地址（如 192.168.1.20 或 192.168.1.20:38620）"
                value={form.host}
                onChange={(_e, d) => setForm({ ...form, host: d.value })}
              />
              <Input
                placeholder="房间码（6 位数字）"
                value={form.code}
                onChange={(_e, d) => setForm({ ...form, code: d.value })}
              />
              <Input
                placeholder="我的昵称"
                value={form.nickname}
                onChange={(_e, d) => setForm({ ...form, nickname: d.value })}
              />
              <Input
                placeholder="房间密码（没有就留空）"
                value={form.password}
                onChange={(_e, d) => setForm({ ...form, password: d.value })}
              />
              <Button
                appearance="primary"
                disabled={busy}
                onClick={() =>
                  void act(() =>
                    api.chat_join(form.host, form.code, form.nickname || '同事', form.password)
                  )
                }
              >
                加入房间
              </Button>
            </div>
            <div className="oc-hint" style={{ marginTop: 8 }}>
              房间码与主机地址由开房间的人告诉你（他点「复制邀请」就能一次发过来）。
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

  // ── 在房间里：聊天界面 ──
  return (
    <div className="oc-page">
      <div className="oc-page-header">
        <div className="oc-page-title">
          临时传输
          <span className="oc-list-sub" style={{ marginLeft: 10, fontWeight: 400 }}>
            {state.role === 'host' ? '我是主机' : '已加入房间'}
            {state.error ? ` · ${state.error}` : ''}
          </span>
        </div>
        <div className="oc-page-desc">
          房间 {state.roomCode}
          {state.roomName ? `（${state.roomName}）` : ''} · {members.length} 人在线 · 聊天记录只存在内存里，
          关掉程序即消失
        </div>
      </div>

      <div className="oc-panel" style={{ marginBottom: 12 }}>
        <div className="oc-actions">
          <Button size="small" appearance="secondary" onClick={() => void copyRoom()}>
            复制邀请（地址 + 房间码）
          </Button>
          <Button size="small" appearance="secondary" onClick={() => void pickAndSend()} disabled={busy}>
            发送文件…
          </Button>
          <Button
            size="small"
            appearance="secondary"
            onClick={() => void act(() => api.chat_clear_received())}
            disabled={busy}
          >
            清空临时文件
          </Button>
          <Button
            size="small"
            onClick={() => {
              if (window.confirm('退出房间？聊天记录会立即清空。')) {
                void act(() => api.chat_leave(true))
              }
            }}
            disabled={busy}
          >
            退出房间
          </Button>
          <span className="oc-list-sub">
            在线：{members.map((m) => `${m.nickname}${m.host ? '（主机）' : ''}`).join('、')}
          </span>
        </div>
      </div>

      <div className="oc-panel">
        <div
          ref={listRef}
          style={{
            maxHeight: 420,
            minHeight: 220,
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            paddingRight: 4,
          }}
        >
          {messages.length === 0 && <div className="oc-hint">还没有消息，打个招呼吧。</div>}
          {messages.map((item: any) => {
            if (item.kind === 'system') {
              return (
                <div
                  key={item.id}
                  className="oc-hint"
                  style={{ textAlign: 'center', fontSize: 12 }}
                >
                  {item.text}
                </div>
              )
            }
            const mine = item.sender === state.nickname
            return (
              <div
                key={item.id}
                style={{
                  alignSelf: mine ? 'flex-end' : 'flex-start',
                  maxWidth: '78%',
                  background: mine ? 'var(--colorBrandBackground2, rgba(76,141,255,.16))' : 'var(--oc-surface)',
                  border: '1px solid var(--oc-border)',
                  borderRadius: 'var(--oc-radius)',
                  padding: '8px 12px',
                }}
              >
                <div className="oc-list-sub" style={{ fontSize: 12 }}>
                  {mine ? '我' : item.sender} · {timeText(item.ts)}
                </div>
                {item.kind === 'file' && item.file ? (
                  <div style={{ marginTop: 4 }}>
                    <div className="oc-list-title" style={{ fontSize: 13.5 }}>
                      📎 {item.file.name}
                    </div>
                    <div className="oc-list-sub">
                      {formatSize(item.file.size)}
                      {state.role === 'member' && (
                        <Button
                          size="small"
                          appearance="secondary"
                          style={{ marginLeft: 8 }}
                          onClick={() =>
                            void act(() => api.chat_save_file(item.file.id, item.file.name))
                          }
                        >
                          下载到本机
                        </Button>
                      )}
                    </div>
                  </div>
                ) : (
                  <div style={{ marginTop: 2, userSelect: 'text', wordBreak: 'break-word' }}>
                    {item.text}
                  </div>
                )}
              </div>
            )
          })}
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <Input
            value={text}
            placeholder="输入消息，回车发送"
            onChange={(_e, data) => setText(data.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void sendText()
            }}
            style={{ flex: 1 }}
          />
          <Button appearance="primary" onClick={() => void sendText()} disabled={busy}>
            发送
          </Button>
        </div>

        {state.receivedDir && (
          <div className="oc-hint" style={{ marginTop: 8 }}>
            收到的文件放在：{state.receivedDir}
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
