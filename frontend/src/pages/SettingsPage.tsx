import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Button, Input, Switch, Spinner } from '@fluentui/react-components'
import { DesktopRegular, WeatherMoonRegular, WeatherSunnyRegular } from '@fluentui/react-icons'
import { api } from '../api'
import type { ThemeMode } from '../types'

interface Props {
  themeMode: ThemeMode
  setThemeMode: (m: ThemeMode) => void
}

const OPTIONS: { id: ThemeMode; label: string; icon: ReactNode; desc: string }[] = [
  { id: 'light', label: '浅色', icon: <WeatherSunnyRegular fontSize={16} />, desc: '明亮环境下的默认外观' },
  { id: 'dark', label: '深色', icon: <WeatherMoonRegular fontSize={16} />, desc: '低光环境，减轻眼部疲劳' },
  { id: 'system', label: '跟随系统', icon: <DesktopRegular fontSize={16} />, desc: '自动同步 Windows 的外观设置' },
]

function SwitchRow({
  label,
  desc,
  checked,
  disabled,
  onChange,
}: {
  label: string
  desc: string
  checked: boolean
  disabled?: boolean
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
        <div className="oc-usage-sub">{desc}</div>
      </div>
      <Switch
        checked={checked}
        disabled={disabled}
        onChange={(_e, data) => onChange(data.checked)}
      />
    </div>
  )
}

export default function SettingsPage({ themeMode, setThemeMode }: Props) {
  const [autostart, setAutostart] = useState(false)
  const [openwith, setOpenwith] = useState(false)
  const [closeToTray, setCloseToTray] = useState(true)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const [dataDir, setDataDir] = useState('')

  // ── 手机控制台 ──
  const [consoleState, setConsoleState] = useState<any>(null)
  const [consoleBusy, setConsoleBusy] = useState(false)
  const [consoleMsg, setConsoleMsg] = useState('')

  // ── 版本与更新 ──
  const [version, setVersion] = useState('')
  const [selfUpdate, setSelfUpdate] = useState<any>(null)
  const [components, setComponents] = useState<any[]>([])
  const [updateBusy, setUpdateBusy] = useState(true)

  useEffect(() => {
    void (async () => {
      try {
        const [a, o, c, info, dir] = await Promise.all([
          api.get_autostart(),
          api.get_openwith_registered(),
          api.get_close_to_tray(),
          api.get_info(),
          api.get_data_dir(),
        ])
        setAutostart(a)
        setOpenwith(o)
        setCloseToTray(c)
        setVersion(info.version)
        setDataDir(dir)
      } catch {
        // 忽略：开发模式下拿不到真实值
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  const checkUpdate = async () => {
    setUpdateBusy(true)
    try {
      const [self, comps] = await Promise.all([api.check_self_update(), api.check_updates(true)])
      setSelfUpdate(self)
      setComponents(comps ?? [])
    } catch {
      setSelfUpdate({ ok: false, message: '检查失败（可能未联网）' })
      setComponents([])
    } finally {
      setUpdateBusy(false)
    }
  }

  useEffect(() => {
    void checkUpdate()
  }, [])

  const select = async (m: ThemeMode) => {
    setThemeMode(m)
    await api.set_theme(m)
  }

  const toggleAutostart = async (checked: boolean) => {
    setBusy(true)
    try {
      const ok = await api.set_autostart(checked)
      setAutostart(ok ? checked : autostart)
    } finally {
      setBusy(false)
    }
  }

  const toggleOpenwith = async (checked: boolean) => {
    setBusy(true)
    try {
      const ok = await api.set_openwith_registered(checked)
      setOpenwith(ok ? checked : openwith)
    } finally {
      setBusy(false)
    }
  }

  const toggleCloseToTray = async (checked: boolean) => {
    setBusy(true)
    try {
      const ok = await api.set_close_to_tray(checked)
      setCloseToTray(ok ? checked : closeToTray)
    } finally {
      setBusy(false)
    }
  }

  const loadConsole = async () => {
    try {
      setConsoleState(await api.webconsole_status())
    } catch {
      setConsoleState(null)
    }
  }

  useEffect(() => {
    void loadConsole()
  }, [])

  const toggleConsole = async (checked: boolean) => {
    setConsoleBusy(true)
    setConsoleMsg('')
    try {
      const result = checked ? await api.webconsole_start() : await api.webconsole_stop()
      if (result && result.ok === false) setConsoleMsg(result.message ?? '')
      await loadConsole()
    } catch (error) {
      setConsoleMsg(`操作失败：${error}`)
    } finally {
      setConsoleBusy(false)
    }
  }

  const regenerateCode = async () => {
    setConsoleBusy(true)
    try {
      const result = await api.webconsole_regenerate()
      if (result && result.ok === false) setConsoleMsg(result.message ?? '')
      else setConsoleMsg('访问码已更换，已登录的手机会自动退出')
      await loadConsole()
    } finally {
      setConsoleBusy(false)
    }
  }

  const copyConsoleUrl = async () => {
    const url = consoleState?.url ?? ''
    if (!url) return
    try {
      await navigator.clipboard.writeText(url)
      setConsoleMsg('地址已复制，可以直接发到微信/QQ 再在手机上打开')
    } catch {
      setConsoleMsg(`复制失败，请手动输入：${url}`)
    }
  }

  // ── 插件索引地址 ──
  const [marketUrl, setMarketUrl] = useState('')
  const [marketDefault, setMarketDefault] = useState('')
  const [marketMsg, setMarketMsg] = useState('')

  const loadMarket = async () => {
    try {
      const data = await api.market_index_url()
      setMarketUrl(data?.url ?? '')
      setMarketDefault(data?.default ?? '')
    } catch {
      /* 开发模式忽略 */
    }
  }

  useEffect(() => {
    void loadMarket()
  }, [])

  const saveMarket = async () => {
    const result = await api.market_set_index_url(marketUrl)
    setMarketMsg(result?.message ?? '')
    await loadMarket()
  }

  const componentUpdates = components.filter((item) => item.hasUpdate)

  return (
    <div className="oc-page">
      <div className="oc-page-header">
        <div className="oc-page-title">设置</div>
        <div className="oc-page-desc">偏好保存在程序目录下的 data/app_config.json</div>
      </div>

      <div className="oc-panel-title" style={{ fontSize: 12, opacity: 0.8 }}>
        外观
      </div>

      <div className="oc-grid oc-grid-3">
        {OPTIONS.map((o) => (
          <div
            key={o.id}
            className="oc-toolcard"
            style={{
              borderColor: themeMode === o.id ? 'var(--oc-accent)' : undefined,
              background: themeMode === o.id ? 'var(--oc-accent-soft)' : undefined,
            }}
            onClick={() => void select(o.id)}
          >
            <div className="oc-toolcard-top">
              <div className="oc-toolcard-icon">{o.icon}</div>
              <div className="oc-toolcard-head">
                <div className="oc-toolcard-name">{o.label}</div>
              </div>
            </div>
            <div className="oc-toolcard-desc">{o.desc}</div>
          </div>
        ))}
      </div>

      <div className="oc-panel-title" style={{ fontSize: 12, opacity: 0.8, marginTop: 20 }}>
        系统集成
      </div>
      <div className="oc-panel" style={{ marginBottom: 12 }}>
        {loading ? (
          <div className="oc-usage-sub">
            <Spinner size="tiny" /> 读取系统设置…
          </div>
        ) : (
          <>
            <SwitchRow
              label="开机自启"
              desc="登录 Windows 后自动在后台运行（仅当前用户）"
              checked={autostart}
              disabled={busy}
              onChange={toggleAutostart}
            />
            <div style={{ borderTop: '1px solid var(--oc-border)', margin: '4px 0' }} />
            <SwitchRow
              label="关闭时最小化到托盘"
              desc="开启：点关闭按钮只隐藏窗口，程序继续驻留托盘；关闭：点关闭直接退出程序"
              checked={closeToTray}
              disabled={busy}
              onChange={toggleCloseToTray}
            />
            <div style={{ borderTop: '1px solid var(--oc-border)', margin: '4px 0' }} />
            <SwitchRow
              label="右键「打开方式」集成"
              desc="把 OpenClass-Box 收编进文件的右键打开方式菜单（需打包为 exe 后生效）"
              checked={openwith}
              disabled={busy}
              onChange={toggleOpenwith}
            />
          </>
        )}
      </div>

      <div className="oc-panel-title" style={{ fontSize: 12, opacity: 0.8, marginTop: 20 }}>
        手机控制台
      </div>
      <div className="oc-panel" style={{ marginBottom: 12 }}>
        <SwitchRow
          label="开启手机控制台"
          desc="同一 WiFi 下用手机浏览器打开下面的地址，即可查看状态、跑体检、远程修复（无需装 App、不连外网）"
          checked={!!consoleState?.running}
          disabled={consoleBusy || loading}
          onChange={toggleConsole}
        />
        {consoleState?.running && (
          <>
            <div style={{ borderTop: '1px solid var(--oc-border)', margin: '4px 0' }} />
            <div style={{ padding: '10px 0' }}>
              <div style={{ fontWeight: 600 }}>手机访问地址</div>
              <div
                style={{
                  fontFamily: 'var(--oc-mono)',
                  fontSize: 17,
                  marginTop: 4,
                  userSelect: 'text',
                }}
              >
                {consoleState.url || '未检测到局域网 IP（请检查网络连接）'}
              </div>
              <div className="oc-usage-sub" style={{ marginTop: 10 }}>
                访问码：
                <b
                  style={{
                    fontSize: 17,
                    letterSpacing: 3,
                    fontFamily: 'var(--oc-mono)',
                    marginLeft: 4,
                  }}
                >
                  {consoleState.code}
                </b>
                <span style={{ marginLeft: 8 }}>（手机首次访问时输入）</span>
              </div>
              <div className="oc-actions" style={{ marginTop: 10 }}>
                <Button size="small" appearance="secondary" onClick={() => void copyConsoleUrl()}>
                  复制地址
                </Button>
                <Button
                  size="small"
                  appearance="secondary"
                  disabled={consoleBusy}
                  onClick={() => void regenerateCode()}
                >
                  更换访问码
                </Button>
                <span className="oc-usage-sub">已登录手机：{consoleState.clients ?? 0} 台</span>
              </div>
              <div className="oc-usage-sub" style={{ marginTop: 8 }}>
                只允许局域网来源访问；连续输错 5 次会锁定 1 分钟；关闭开关后所有手机立即失效。
              </div>
            </div>
          </>
        )}
        {consoleMsg && (
          <div className="oc-usage-sub" style={{ marginTop: 8 }}>
            {consoleMsg}
          </div>
        )}
      </div>

      <div className="oc-panel-title" style={{ fontSize: 12, opacity: 0.8, marginTop: 20 }}>
        插件索引
      </div>
      <div className="oc-panel" style={{ marginBottom: 12 }}>
        <div className="oc-usage-sub" style={{ marginBottom: 8 }}>
          工具箱 → 插件市场从这里读取索引。教室没有外网时，可以换成内网镜像地址，
          或用「离线导入 zip」手动安装插件包。
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <Input
            value={marketUrl}
            onChange={(_e, data) => setMarketUrl(data.value)}
            style={{ flex: 1, minWidth: 320 }}
          />
          <Button appearance="primary" size="small" onClick={() => void saveMarket()}>
            保存
          </Button>
          {marketDefault && marketUrl !== marketDefault && (
            <Button
              appearance="secondary"
              size="small"
              onClick={() => setMarketUrl(marketDefault)}
            >
              恢复默认
            </Button>
          )}
        </div>
        {marketMsg && (
          <div className="oc-usage-sub" style={{ marginTop: 8 }}>
            {marketMsg}
          </div>
        )}
      </div>

      <div className="oc-panel-title" style={{ fontSize: 12, opacity: 0.8 }}>
        更新
      </div>
      <div className="oc-panel" style={{ marginBottom: 12 }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: 12,
            padding: '4px 0',
          }}
        >
          <div>
            <div style={{ fontWeight: 600 }}>
              OpenClass-Box {version ? `v${version}` : ''}
            </div>
            <div className="oc-usage-sub">
              {updateBusy
                ? '正在检查新版本…'
                : selfUpdate?.hasUpdate
                  ? `发现新版本：${selfUpdate.latest}${selfUpdate.publishedAt ? `（${selfUpdate.publishedAt.slice(0, 10)} 发布）` : ''}`
                  : selfUpdate?.ok
                    ? '已是最新版本'
                    : (selfUpdate?.message ?? '未能获取版本信息')}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <Button size="small" appearance="secondary" onClick={checkUpdate} disabled={updateBusy}>
              重新检查
            </Button>
            {selfUpdate?.hasUpdate && (
              <Button
                size="small"
                appearance="primary"
                onClick={() => void api.open_url(selfUpdate.url)}
              >
                前往下载
              </Button>
            )}
          </div>
        </div>

        {componentUpdates.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <div className="oc-list-sub" style={{ marginBottom: 6 }}>
              已集成组件有 {componentUpdates.length} 项可更新：
            </div>
            {componentUpdates.map((item) => (
              <div
                key={item.id}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '4px 0',
                }}
              >
                <span className="oc-usage-sub">
                  {item.name}：{item.current} → {item.latest}
                </span>
                <Button size="small" onClick={() => void api.open_url(item.url)}>
                  前往
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="oc-panel-title" style={{ fontSize: 12, opacity: 0.8 }}>
        存储位置
      </div>
      <div className="oc-panel" style={{ marginBottom: 12 }}>
        <div className="oc-info-row">
          <span>配置与数据</span>
          <span className="oc-mono">{dataDir || '读取中…'}</span>
        </div>
        <div className="oc-hint" style={{ marginTop: 8 }}>
          所有设置会立即写入上面的目录。若程序安装在 Program Files 这类受保护位置，
          会自动改用用户目录（%LOCALAPPDATA%\OpenClass-Box），保证设置一定保存成功。
        </div>
        <div className="oc-actions" style={{ marginTop: 10 }}>
          <Button appearance="secondary" onClick={() => void api.open_tool_dir()}>
            打开 tools 目录
          </Button>
        </div>
      </div>
    </div>
  )
}
