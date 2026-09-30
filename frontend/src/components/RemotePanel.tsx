import { useEffect, useState } from 'react'
import { Button, Input, Switch } from '@fluentui/react-components'
import { api } from '../api'

/**
 * 远程管理服务（A 端）+ 内网穿透。
 *
 * 用途：把 A 端的机房管理搬到浏览器里 —— 用内网穿透映射出去以后，
 * 在教室外（家里、办公室）打开一个网址就能管整个机房。
 *
 * 安全约束（界面上会一并提示）：
 *  · 访问码必须至少 8 位，不提供默认码；
 *  · 默认只允许内网来源，要用公网访问必须显式打开开关；
 *  · 电源操作只会下发给 B 端设备，A 端自身永不执行。
 */
export default function RemotePanel() {
  const [remote, setRemote] = useState<any>(null)
  const [tunnel, setTunnel] = useState<any>(null)
  const [code, setCode] = useState('')
  const [msg, setMsg] = useState('')
  const [tunnelMsg, setTunnelMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [fields, setFields] = useState<Record<string, string>>({})
  const [showAudit, setShowAudit] = useState(false)

  const load = async () => {
    try {
      const [r, t] = await Promise.all([api.remote_admin_status(), api.tunnel_status()])
      setRemote(r)
      setTunnel(t)
      const saved = t?.settings ?? {}
      setFields((prev) => {
        const merged: Record<string, string> = { ...saved }
        Object.keys(prev).forEach((key) => {
          if (prev[key]) merged[key] = prev[key]
        })
        return merged
      })
    } catch {
      /* 开发预览模式忽略 */
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const provider = tunnel?.provider || 'cloudflared'
  const providerSpec = (tunnel?.providers ?? []).find((item: any) => item.id === provider)

  const saveCode = async () => {
    setBusy(true)
    setMsg('')
    try {
      const result = await api.remote_admin_set_code(code)
      setMsg(result?.message ?? '')
      if (result?.ok) setCode('')
    } finally {
      setBusy(false)
      await load()
    }
  }

  const toggleRemote = async (checked: boolean) => {
    setBusy(true)
    setMsg('')
    try {
      const result = checked ? await api.remote_admin_start() : await api.remote_admin_stop()
      setMsg(result?.message ?? '')
    } catch (error) {
      setMsg(`操作失败：${error}`)
    } finally {
      setBusy(false)
      await load()
    }
  }

  const copyAddress = async () => {
    const url = remote?.url || ''
    if (!url) return
    try {
      await navigator.clipboard.writeText(url)
      setMsg('地址已复制，发到手机或浏览器即可打开')
    } catch {
      setMsg(`复制失败，请手动输入：${url}`)
    }
  }

  const startTunnel = async () => {
    setBusy(true)
    setTunnelMsg('')
    try {
      const saved = await api.tunnel_save(provider, fields)
      if (saved?.ok === false) {
        setTunnelMsg(saved.message ?? '')
        return
      }
      const result = await api.tunnel_start(remote?.port || 0, provider, fields)
      setTunnelMsg(result?.message ?? '')
    } catch (error) {
      setTunnelMsg(`启动失败：${error}`)
    } finally {
      setBusy(false)
      await load()
    }
  }

  const stopTunnel = async () => {
    setBusy(true)
    try {
      const result = await api.tunnel_stop()
      setTunnelMsg(result?.message ?? '')
    } finally {
      setBusy(false)
      await load()
    }
  }

  const publicUrl = tunnel?.url || remote?.url || ''

  return (
    <>
      <div className="oc-panel-title" style={{ fontSize: 12, opacity: 0.8, marginTop: 20 }}>
        远程管理服务（A 端）
      </div>

      <div className="oc-panel" style={{ marginBottom: 12 }}>
        <div className="oc-usage-sub" style={{ marginBottom: 10 }}>
          开启后，用下面的内网穿透把端口映射出去，就能在教室外的浏览器里管理整个机房：
          设备列表、下发指令（体检 / 修复 / 清理 / 消息 / 文件）、电源控制。
          <b>电源操作只会下发给 B 端设备，A 端自身永不执行。</b>
        </div>

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <Input
            type="password"
            value={code}
            onChange={(_e, data) => setCode(data.value)}
            placeholder={`设置访问码（至少 ${remote?.minCodeLen ?? 8} 位）`}
            style={{ width: 240 }}
          />
          <Button size="small" appearance="secondary" disabled={busy || !code} onClick={() => void saveCode()}>
            保存访问码
          </Button>
          <span className="oc-usage-sub">
            {remote?.hasCode ? '已设置（改码后所有已登录设备立即失效）' : '尚未设置，未设置前无法启动'}
          </span>
        </div>

        <div style={{ marginTop: 12 }}>
          <Switch
            checked={Boolean(remote?.running)}
            disabled={busy || !remote?.hasCode}
            onChange={(_e, data) => void toggleRemote(data.checked)}
            label={remote?.running ? '远程管理服务：运行中' : '远程管理服务：已停止'}
          />
        </div>

        {remote?.running && (
          <div style={{ marginTop: 10 }}>
            <div className="oc-info-row">
              <span>访问地址</span>
              <span className="oc-mono" style={{ userSelect: 'text' }}>
                {remote.localUrl || '—'}
              </span>
            </div>
            {publicUrl && publicUrl !== remote.localUrl && (
              <div className="oc-info-row">
                <span>公网地址（穿透）</span>
                <span className="oc-mono" style={{ userSelect: 'text' }}>
                  {publicUrl}
                </span>
              </div>
            )}
            <div className="oc-actions" style={{ marginTop: 8 }}>
              <Button size="small" appearance="secondary" onClick={() => void copyAddress()}>
                复制地址
              </Button>
              <span className="oc-usage-sub">已登录会话：{remote.sessions ?? 0}</span>
            </div>
          </div>
        )}

        <div style={{ marginTop: 12, borderTop: '1px solid var(--oc-border)', paddingTop: 10 }}>
          <Switch
            checked={Boolean(remote?.allowPublic)}
            disabled={busy}
            onChange={(_e, data) =>
              void (async () => {
                setBusy(true)
                try {
                  const result = await api.remote_admin_set_allow_public(data.checked)
                  setMsg(result?.message ?? '')
                } finally {
                  setBusy(false)
                  await load()
                }
              })()
            }
            label="允许公网来源访问"
          />
          <div className="oc-usage-sub" style={{ marginTop: 4 }}>
            默认只接受内网来源；要用内网穿透在教室外访问，必须打开这一项。
            打开后请务必确认访问码足够长（连续输错 5 次会锁定 5 分钟）。
          </div>
          {remote?.safeTest && (
            <div className="oc-list-warn" style={{ marginTop: 8 }}>
              安全测试模式已开启（环境变量 OPENCLASS_SAFE_TEST=1）：电源操作只会被记录，不会真正下发。
            </div>
          )}
          {remote?.powerAllowed === false && (
            <div className="oc-usage-sub" style={{ marginTop: 6 }}>
              提示：B 端尚未允许远程电源控制 —— 需要在被控机器的「设置 → 远程电源控制」里开启。
            </div>
          )}
        </div>

        {msg && (
          <div className="oc-usage-sub" style={{ marginTop: 8 }}>
            {msg}
          </div>
        )}

        <div style={{ marginTop: 10 }}>
          <button className="oc-linklike" onClick={() => setShowAudit(!showAudit)}>
            {showAudit ? '收起操作记录' : '查看最近操作记录'}
          </button>
          {showAudit && (
            <div className="oc-list" style={{ marginTop: 8 }}>
              {(remote?.audit ?? []).length === 0 ? (
                <div className="oc-hint">还没有操作记录</div>
              ) : (
                (remote.audit ?? []).map((item: any, index: number) => (
                  <div className="oc-list-row" key={index}>
                    <div className="oc-list-main">
                      <div className="oc-list-title">
                        {item.ok ? '✅' : '⚠️'} {item.action} · {item.detail}
                      </div>
                      <div className="oc-list-sub">
                        {item.time}
                        {item.source ? ` · 来自 ${item.source}` : ''}
                        {item.extra ? ` · ${item.extra}` : ''}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </div>

      <div className="oc-panel-title" style={{ fontSize: 12, opacity: 0.8 }}>
        内网穿透（把上面的端口映射出去）
      </div>
      <div className="oc-panel" style={{ marginBottom: 12 }}>
        <div className="oc-usage-sub" style={{ marginBottom: 10 }}>
          机房通常没有公网 IP，也不在同一网段。选一个穿透方案把本机端口映射出去，
          就能在教室外用浏览器打开上面那个地址。
        </div>

        <div className="oc-actions" style={{ flexWrap: 'wrap' }}>
          {(tunnel?.providers ?? []).map((item: any) => (
            <Button
              key={item.id}
              size="small"
              appearance={provider === item.id ? 'primary' : 'secondary'}
              disabled={busy || Boolean(tunnel?.running)}
              onClick={() => {
                setFields({})
                void (async () => {
                  await api.tunnel_save(item.id, {})
                  await load()
                })()
              }}
            >
              {item.name}
              {item.exeReady ? '' : '（未找到）'}
            </Button>
          ))}
        </div>

        {providerSpec && (
          <>
            <div className="oc-usage-sub" style={{ marginTop: 10 }}>
              {providerSpec.desc}
              {providerSpec.urlHint ? ` ${providerSpec.urlHint}` : ''}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
              {(providerSpec.fields ?? []).map((field: any) => (
                <Input
                  key={field.key}
                  type={field.secret ? 'password' : 'text'}
                  value={fields[field.key] ?? ''}
                  onChange={(_e, data) => setFields({ ...fields, [field.key]: data.value })}
                  placeholder={field.placeholder || field.label}
                  style={{ minWidth: 240, flex: 1 }}
                />
              ))}
            </div>
          </>
        )}

        <div className="oc-actions" style={{ marginTop: 10 }}>
          {tunnel?.running ? (
            <Button size="small" appearance="secondary" disabled={busy} onClick={() => void stopTunnel()}>
              停止穿透
            </Button>
          ) : (
            <Button size="small" appearance="primary" disabled={busy} onClick={() => void startTunnel()}>
              启动穿透
            </Button>
          )}
          <Button size="small" appearance="secondary" disabled={busy} onClick={() => void load()}>
            刷新状态
          </Button>
          <span className="oc-usage-sub">
            {tunnel?.running
              ? publicUrl
                ? `公网地址：${publicUrl}`
                : '已启动，正在等待公网地址…'
              : '未启动'}
          </span>
        </div>

        {tunnelMsg && (
          <div className="oc-usage-sub" style={{ marginTop: 8 }}>
            {tunnelMsg}
          </div>
        )}
        {tunnel?.message && (
          <div className="oc-list-warn" style={{ marginTop: 8 }}>
            {tunnel.message}
          </div>
        )}

        <div className="oc-usage-sub" style={{ marginTop: 8 }}>
          把工具放到 <span className="oc-mono">tools\tunnel\</span> 下即可：
          cloudflared.exe / frpc.exe / ngrok.exe；其他工具用「自定义命令」，命令里用{' '}
          <span className="oc-mono">{'{port}'}</span> 占位本地端口。
          穿透后管理端就在公网上了 —— 请确认访问码足够长，不需要时及时停止。
        </div>
      </div>
    </>
  )
}
