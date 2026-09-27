import { useEffect, useState } from 'react'
import { Button, Input, Spinner, Switch } from '@fluentui/react-components'
import { api } from '../api'
import SysGuardPanel from '../components/SysGuardPanel'

const LEVEL_TEXT: Record<string, string> = {
  safe: '安全',
  warn: '可疑',
  danger: '高危',
}

const SOURCE_TEXT: Record<string, string> = {
  browser: '浏览器访问',
  clipboard: '剪贴板',
}

function formatTime(ts: number): string {
  try {
    return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return ''
  }
}

function SwitchRow({
  label,
  desc,
  checked,
  onChange,
}: {
  label: string
  desc: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 12,
        padding: '8px 0',
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 600 }}>{label}</div>
        <div className="oc-hint">{desc}</div>
      </div>
      <Switch checked={checked} onChange={(_e, data) => onChange(data.checked)} />
    </div>
  )
}

export default function SecurityPage() {
  const [stats, setStats] = useState<any>(null)
  const [events, setEvents] = useState<any[]>([])
  const [settings, setSettings] = useState<any>({ clipboard: true, browser: true })
  const [whitelist, setWhitelist] = useState<string[]>([])
  const [busy, setBusy] = useState(true)
  const [msg, setMsg] = useState('')

  const [manual, setManual] = useState('')
  const [manualResult, setManualResult] = useState<any>(null)
  const [manualBusy, setManualBusy] = useState(false)

  const [whitelistInput, setWhitelistInput] = useState('')

  const notify = (text: string) => {
    setMsg(text)
    window.setTimeout(() => setMsg(''), 3200)
  }

  const load = async (silent = false) => {
    if (!silent) setBusy(true)
    try {
      const [s, e, st, wl] = await Promise.all([
        api.security_stats(),
        api.security_events(120),
        api.security_settings(),
        api.security_whitelist(),
      ])
      setStats(s)
      setEvents(e ?? [])
      setSettings(st ?? { clipboard: true, browser: true })
      setWhitelist(wl ?? [])
    } catch {
      /* 首次读取失败保持空态 */
    } finally {
      if (!silent) setBusy(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  // 自动检测在后台持续进行，这里每 5 秒刷新让它"活"起来
  useEffect(() => {
    const timer = window.setInterval(() => void load(true), 5000)
    return () => window.clearInterval(timer)
  }, [])

  const toggle = async (key: 'clipboard' | 'browser', value: boolean) => {
    const result = await api.set_security_settings(
      key === 'clipboard' ? value : undefined,
      key === 'browser' ? value : undefined
    )
    if (result?.settings) setSettings(result.settings)
    notify(value ? '已开启该来源的自动检测' : '已关闭该来源的自动检测')
  }

  const markSafe = async (host: string) => {
    const result = await api.security_add_whitelist(host)
    notify(result?.message ?? '')
    await load(true)
  }

  const clearAll = async () => {
    if (!window.confirm('确定清空所有检测记录吗？白名单不受影响。')) return
    const result = await api.security_clear()
    notify(result?.message ?? '')
    await load(true)
  }

  const runManual = async () => {
    const target = manual.trim()
    if (!target) return
    setManualBusy(true)
    try {
      setManualResult(await api.check_url(target))
    } catch {
      setManualResult({ score: 0, level: 'safe', reasons: ['检测失败，请稍后重试'] })
    } finally {
      setManualBusy(false)
    }
  }

  return (
    <div className="oc-page">
      <div className="oc-page-header">
        <div className="oc-page-title">安全防护</div>
        <div className="oc-page-desc">
          打开网页时自动检测风险并在顶部提示；只做提醒，不拦截你的任何操作
        </div>
      </div>

      {/* ── 统计 ── */}
      <div className="oc-stats">
        <div className="oc-stat">
          <div className="oc-stat-value">{stats?.today ?? 0}</div>
          <div className="oc-stat-label">今日检测</div>
        </div>
        <div className={`oc-stat ${(stats?.riskToday ?? 0) > 0 ? 'danger' : 'safe'}`}>
          <div className="oc-stat-value">{stats?.riskToday ?? 0}</div>
          <div className="oc-stat-label">今日风险</div>
        </div>
        <div className="oc-stat">
          <div className="oc-stat-value">{stats?.total ?? 0}</div>
          <div className="oc-stat-label">累计检测</div>
        </div>
        <div className="oc-stat">
          <div className="oc-stat-value">{whitelist.length}</div>
          <div className="oc-stat-label">白名单</div>
        </div>
      </div>

      {/* ── 自动检测开关 ── */}
      <div className="oc-panel" style={{ marginTop: 12 }}>
        <div className="oc-panel-title">自动检测</div>
        <SwitchRow
          label="浏览器访问检测"
          desc="读取 Chrome / Edge 的本地访问记录，打开网页即自动评分（只读本机历史，不外传任何数据）"
          checked={settings.browser}
          onChange={(v) => void toggle('browser', v)}
        />
        <div style={{ borderTop: '1px solid var(--oc-border)', margin: '4px 0' }} />
        <SwitchRow
          label="剪贴板网址检测"
          desc="复制到网址时自动评分，风险网址会在顶部提示"
          checked={settings.clipboard}
          onChange={(v) => void toggle('clipboard', v)}
        />
      </div>

      {/* ── 最近检测记录 ── */}
      <div className="oc-panel" style={{ marginTop: 12 }}>
        <div className="oc-panel-title">
          最近检测记录（{events.length}）
          <Button
            size="small"
            appearance="secondary"
            style={{ marginLeft: 10 }}
            onClick={() => void load()}
          >
            刷新
          </Button>
          <Button size="small" appearance="secondary" style={{ marginLeft: 6 }} onClick={clearAll}>
            清空
          </Button>
        </div>

        {busy && events.length === 0 ? (
          <Spinner size="tiny" label="读取中…" />
        ) : events.length === 0 ? (
          <div className="oc-hint">
            还没有检测记录：用浏览器打开任意网页，或复制一个网址，这里就会自动出现记录。
          </div>
        ) : (
          <div className="oc-list oc-scroll">
            {events.map((item, index) => (
              <div className="oc-list-row" key={`${item.time}-${index}`}>
                <div className="oc-list-main">
                  <div className="oc-list-title">
                    <span className={`oc-level ${item.level}`}>
                      {LEVEL_TEXT[item.level] ?? item.level}
                    </span>
                    <span style={{ marginLeft: 8 }}>{item.host || item.url}</span>
                  </div>
                  <div className="oc-list-sub">
                    {formatTime(item.time)} · {SOURCE_TEXT[item.source] ?? item.source} · 评分{' '}
                    {item.score}
                    {item.reasons?.length ? ` · ${item.reasons[0]}` : ''}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                  <Button size="small" onClick={() => void api.open_url(item.url)}>
                    打开
                  </Button>
                  <Button size="small" onClick={() => void markSafe(item.host)}>
                    标为安全
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}

        {msg && (
          <div className="oc-list-sub" style={{ marginTop: 8 }}>
            {msg}
          </div>
        )}
      </div>

      {/* ── 系统防护（篡改修复 / USB / 自启动 / 弹窗 / 高占用）── */}
      <SysGuardPanel />

      {/* ── 白名单 ── */}
      <div className="oc-panel" style={{ marginTop: 12 }}>
        <div className="oc-panel-title">白名单（{whitelist.length}）</div>
        <div className="oc-searchbar" style={{ marginBottom: 8 }}>
          <Input
            value={whitelistInput}
            onChange={(_e, data) => setWhitelistInput(data.value)}
            placeholder="输入域名，如 example.com"
            style={{ flex: 1 }}
          />
          <Button
            appearance="secondary"
            onClick={async () => {
              const result = await api.security_add_whitelist(whitelistInput)
              notify(result?.message ?? '')
              setWhitelistInput('')
              await load(true)
            }}
          >
            添加
          </Button>
        </div>
        {whitelist.length === 0 ? (
          <div className="oc-hint">白名单中的域名不会被记录，也不会弹提示。</div>
        ) : (
          <div className="oc-list oc-scroll">
            {whitelist.map((domain) => (
              <div className="oc-list-row" key={domain}>
                <div className="oc-list-main">
                  <div className="oc-list-title">{domain}</div>
                </div>
                <Button
                  size="small"
                  onClick={async () => {
                    await api.security_remove_whitelist(domain)
                    await load(true)
                  }}
                >
                  移除
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── 手动检测（备用） ── */}
      <div className="oc-panel" style={{ marginTop: 12 }}>
        <div className="oc-panel-title">手动检测（备用）</div>
        <div className="oc-searchbar">
          <Input
            value={manual}
            onChange={(_e, data) => setManual(data.value)}
            placeholder="粘贴一个网址立即检测"
            style={{ flex: 1 }}
          />
          <Button appearance="primary" disabled={manualBusy || !manual.trim()} onClick={runManual}>
            {manualBusy ? '检测中…' : '检测'}
          </Button>
        </div>
        {manualResult && (
          <div style={{ marginTop: 10 }}>
            <div className="oc-list-title">
              <span className={`oc-level ${manualResult.level}`}>
                {LEVEL_TEXT[manualResult.level] ?? manualResult.level}
              </span>
              <span style={{ marginLeft: 8 }}>{manualResult.score} 分</span>
            </div>
            {manualResult.reasons?.length ? (
              <ul className="oc-hint" style={{ margin: '6px 0 0', paddingLeft: 20 }}>
                {manualResult.reasons.map((reason: string, index: number) => (
                  <li key={index}>{reason}</li>
                ))}
              </ul>
            ) : (
              <div className="oc-hint" style={{ marginTop: 6 }}>
                未发现风险特征
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
