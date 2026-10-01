import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Button, Input, Switch, Spinner } from '@fluentui/react-components'
import RemotePanel from '../components/RemotePanel'
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
  const [storage, setStorage] = useState<any>(null)
  const [diag, setDiag] = useState<any>(null)

  // ── 配置备份（改乱了能回退）──
  const [backups, setBackups] = useState<any>(null)
  const [backupMsg, setBackupMsg] = useState('')

  const loadBackups = async () => {
    try {
      setBackups(await api.config_backup_status())
    } catch {
      /* 开发预览模式忽略 */
    }
  }

  const createBackup = async () => {
    setBusy(true)
    setBackupMsg('')
    try {
      const result = await api.config_backup_create()
      setBackupMsg(result?.message ?? '')
    } finally {
      setBusy(false)
      await loadBackups()
    }
  }

  const restoreBackup = async (name: string) => {
    if (!window.confirm(`确定恢复到 ${name} 吗？当前设置会先自动另存一份。`)) return
    setBusy(true)
    setBackupMsg('')
    try {
      const result = await api.config_backup_restore(name)
      setBackupMsg(result?.message ?? '')
      if (result?.ok) {
        // 恢复后让界面按新配置刷新一遍（主题等立即生效）
        const looks = await api.get_theme().catch(() => null)
        if (looks) window.location.reload()
      }
    } finally {
      setBusy(false)
      await loadBackups()
    }
  }
  const [startupMode, setStartupMode] = useState('window')
  const [startupMsg, setStartupMsg] = useState('')
  const [autoMsg, setAutoMsg] = useState('')

  // ── 渲染模式（GPU 合成：黑屏与性能之间的取舍）──
  const [renderMode, setRenderMode] = useState('safe')
  const [renderMsg, setRenderMsg] = useState('')

  const chooseRender = async (mode: string) => {
    setBusy(true)
    try {
      const result = await api.set_render_mode(mode)
      setRenderMode(result?.mode ?? mode)
      setRenderMsg(result?.message ?? '')
    } finally {
      setBusy(false)
    }
  }


  const [firewall, setFirewall] = useState<any>(null)
  const [allowPower, setAllowPower] = useState(false)

  // ── 远程管理（机房连通方式）──
  const [lan, setLan] = useState({ serverUrl: '', port: 38900, proxy: '' })
  const [lanMsg, setLanMsg] = useState('')
  const [lanBusy, setLanBusy] = useState(false)

  // ── 被链接（B 端加入老师机）──
  const [joinCode, setJoinCode] = useState('')
  const [joinMsg, setJoinMsg] = useState('')

  const joinTeacher = async () => {
    setBusy(true)
    setJoinMsg('')
    try {
      const result = await api.lan_join(lan.serverUrl.trim(), joinCode.trim())
      setJoinMsg(result?.message ?? '')
    } catch (error) {
      setJoinMsg(`连接失败：${error}`)
    } finally {
      setBusy(false)
    }
  }

  const leaveTeacher = async () => {
    setBusy(true)
    try {
      const result = await api.lan_leave()
      setJoinMsg(result?.message ?? '')
    } finally {
      setBusy(false)
    }
  }

  // ── 局域网放行 + 密码保护 ──
  const [fw, setFw] = useState<any>(null)
  const [pc, setPc] = useState<any>(null)
  const [pcPages, setPcPages] = useState<string[]>([])
  const [pcCur, setPcCur] = useState('')
  const [pcNew, setPcNew] = useState('')
  const [pcMsg, setPcMsg] = useState('')

  const loadFw = async () => {
    try {
      setFw(await api.firewall_status())
    } catch {
      /* 忽略 */
    }
  }

  const loadPc = async () => {
    try {
      const status = await api.passcode_status()
      setPc(status)
      setPcPages(status?.protected ?? [])
    } catch {
      /* 忽略 */
    }
  }

  const savePasscode = async () => {
    if (pcNew.trim().length < 4) {
      setPcMsg('密码至少 4 位')
      return
    }
    const result = await api.passcode_set(pcCur, pcNew, pcPages)
    setPcMsg(result?.message ?? '')
    if (result?.ok) {
      setPcNew('')
      setPcCur('')
      await loadPc()
    }
  }

  const clearPasscode = async () => {
    if (!window.confirm('确定关闭密码保护吗？关闭后任何人点开设置都不需要密码。')) return
    const result = await api.passcode_clear(pcCur)
    setPcMsg(result?.message ?? '')
    if (result?.ok) {
      setPcCur('')
      await loadPc()
    }
  }

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
        const [a, o, c, info, dir, mode, storage, power, render] = await Promise.all([
          api.get_autostart(),
          api.get_openwith_registered(),
          api.get_close_to_tray(),
          api.get_info(),
          api.get_data_dir(),
          api.get_startup_mode(),
          api.get_storage_info(),
          api.power_control_status(),
          api.get_render_mode(),
        ])
        setAllowPower(Boolean(power?.allowed))
        setRenderMode(String(render?.mode || 'safe'))
        try {
          const config = await api.lan_config()
          setLan({
            serverUrl: config?.serverUrl ?? '',
            port: Number(config?.port ?? 38900),
            proxy: config?.proxy ?? '',
          })
        } catch {
          /* 忽略 */
        }
        try {
          setFw(await api.firewall_status())
          const pcStatus = await api.passcode_status()
          setPc(pcStatus)
          setPcPages(pcStatus?.protected ?? [])
        } catch {
          /* 忽略 */
        }
        setAutostart(a)
        setOpenwith(o)
        setCloseToTray(c)
        setVersion(info.version)
        setDataDir(dir)
        setStartupMode(mode ?? 'window')
        setStorage(storage ?? null)
        try {
          setDiag(await api.config_diag())
        } catch {
          /* 开发预览模式忽略 */
        }
        void loadBackups()
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
    setAutoMsg('')
    try {
      const ok = await api.set_autostart(checked)
      if (ok) {
        setAutostart(checked)
        setAutoMsg(checked ? '已开启开机自启' : '已关闭开机自启')
      } else {
        setAutoMsg('设置失败：注册表写入被拒绝（常见原因是安全软件拦截，可稍后重试）')
      }
    } catch (error) {
      setAutoMsg(`设置失败：${error}`)
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

  const chooseStartup = async (mode: string) => {
    setBusy(true)
    try {
      const result = await api.set_startup_mode(mode)
      setStartupMode(result?.mode ?? mode)
      setStartupMsg(result?.message ?? '')
    } finally {
      setBusy(false)
    }
  }



  const loadFirewall = async () => {
    try {
      setFirewall(await api.webconsole_firewall())
    } catch {
      setFirewall(null)
    }
  }

  const allowFirewall = async () => {
    setConsoleBusy(true)
    try {
      const result = await api.webconsole_allow_firewall()
      setConsoleMsg(result?.message ?? '')
      window.setTimeout(() => void loadFirewall(), 3000)
    } finally {
      setConsoleBusy(false)
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

  const saveLan = async () => {
    setLanBusy(true)
    setLanMsg('')
    try {
      const result = await api.lan_set_config(lan.port, lan.proxy, lan.serverUrl)
      setLanMsg(result?.message ?? '')
    } catch (error) {
      setLanMsg(`保存失败：${error}`)
    } finally {
      setLanBusy(false)
    }
  }

  const testLan = async () => {
    setLanBusy(true)
    setLanMsg('正在扫描局域网内的教师机…')
    try {
      const list = await api.lan_scan()
      const online = (list ?? []).filter((item: any) => item?.online !== false)
      setLanMsg(
        online.length
          ? `在局域网内发现 ${online.length} 台设备：${online
              .slice(0, 3)
              .map((item: any) => `${item.name || item.host || ''}`)
              .join('、')}`
          : '没有发现教师机服务：请确认教师机已开启「机房管理 → 启动服务」，以及两台机器在同一网段'
      )
    } catch (error) {
      setLanMsg(`测试失败：${error}`)
    } finally {
      setLanBusy(false)
    }
  }

  const toggleAllowPower = async (checked: boolean) => {
    if (checked) {
      const ok = window.confirm(
        '开启后，已配对的教师机可以远程让这台机器关机 / 重启 / 睡眠。\n\n' +
          '· 关机 / 重启会延迟执行，期间可在教师机点「取消关机」\n' +
          '· 每次操作都会记录到本机运行日志\n\n确定开启吗？'
      )
      if (!ok) return
    }
    setBusy(true)
    try {
      const result = await api.set_allow_power(checked)
      setAllowPower(Boolean(result?.allowed))
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

  useEffect(() => {
    if (consoleState?.running) void loadFirewall()
  }, [consoleState?.running])

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
              desc="登录 Windows 后自动运行（仅当前用户）；显示界面还是缩到托盘，由下面的「启动时的窗口行为」决定"
              checked={autostart}
              disabled={busy}
              onChange={toggleAutostart}
            />
            {autoMsg && (
              <div className="oc-usage-sub" style={{ paddingBottom: 6 }}>
                {autoMsg}
              </div>
            )}
            <div style={{ borderTop: '1px solid var(--oc-border)', margin: '4px 0' }} />
            <SwitchRow
              label="关闭时最小化到托盘"
              desc="开启：点关闭按钮只隐藏窗口，程序继续驻留托盘；关闭：点关闭直接退出程序"
              checked={closeToTray}
              disabled={busy}
              onChange={toggleCloseToTray}
            />
            <div style={{ borderTop: '1px solid var(--oc-border)', margin: '4px 0' }} />
            <div style={{ padding: '8px 0' }}>
              <div style={{ fontWeight: 600 }}>启动时的窗口行为</div>
              <div className="oc-usage-sub">
                静默启动需要托盘支持：程序启动后直接缩到托盘，点托盘图标再打开（下次启动生效）
              </div>
              <div className="oc-actions" style={{ marginTop: 8 }}>
                <Button
                  size="small"
                  appearance={startupMode === 'window' ? 'primary' : 'secondary'}
                  disabled={busy}
                  onClick={() => void chooseStartup('window')}
                >
                  显示界面（默认）
                </Button>
                <Button
                  size="small"
                  appearance={startupMode === 'silent' ? 'primary' : 'secondary'}
                  disabled={busy}
                  onClick={() => void chooseStartup('silent')}
                >
                  静默启动到托盘
                </Button>
                {startupMsg && <span className="oc-usage-sub">{startupMsg}</span>}
              </div>
            </div>
            <div style={{ borderTop: '1px solid var(--oc-border)', margin: '4px 0' }} />
            <div style={{ padding: '8px 0' }}>
              <div style={{ fontWeight: 600 }}>渲染模式</div>
              <div className="oc-usage-sub">
                部分一体机（老显卡 / 驱动）在 WebView2 硬件合成下会出现「点某些界面整窗黑屏」。
                「兼容优先」会关闭 GPU 合成，优先保证画面正常；
                如果你这台机器一切正常，可以换成「性能优先」，界面动画会更顺滑。
              </div>
              <div className="oc-actions" style={{ marginTop: 8 }}>
                <Button
                  size="small"
                  appearance={renderMode === 'safe' ? 'primary' : 'secondary'}
                  disabled={busy}
                  onClick={() => void chooseRender('safe')}
                >
                  兼容优先（默认）
                </Button>
                <Button
                  size="small"
                  appearance={renderMode === 'gpu' ? 'primary' : 'secondary'}
                  disabled={busy}
                  onClick={() => void chooseRender('gpu')}
                >
                  性能优先（启用 GPU 合成）
                </Button>
                {renderMsg && <span className="oc-usage-sub">{renderMsg}</span>}
              </div>
            </div>
            <div style={{ borderTop: '1px solid var(--oc-border)', margin: '4px 0' }} />
            <SwitchRow
              label="允许远程电源控制"
              desc="开启后，已配对的教师机可以远程让这台机器关机 / 重启 / 睡眠。默认关闭；关机与重启会延迟执行，期间可在教师机取消（「取消关机」始终可用）"
              checked={allowPower}
              disabled={busy}
              onChange={toggleAllowPower}
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
                {firewall?.supported && !firewall?.allowed && (
                  <Button
                    size="small"
                    appearance="primary"
                    disabled={consoleBusy}
                    onClick={() => void allowFirewall()}
                  >
                    放行防火墙（手机连不上时点这里）
                  </Button>
                )}
              </div>
              {firewall?.supported && (
                <div className="oc-usage-sub" style={{ marginTop: 6 }}>
                  防火墙：{firewall.message}
                  {firewall.allowed
                    ? ''
                    : ' —— Windows 默认拦截入站连接，未放行时只有本机能打开这个地址'}
                </div>
              )}
              <div className="oc-usage-sub" style={{ marginTop: 8 }}>
                只允许局域网来源访问；连续输错 5 次会锁定 1 分钟；关闭开关后所有手机立即失效。
                若已放行仍连不上：确认手机与电脑在同一个 WiFi，且路由器没有开启「AP 隔离」。
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
        远程管理（机房）
      </div>
      <div className="oc-panel" style={{ marginBottom: 12 }}>
        <div className="oc-usage-sub" style={{ marginBottom: 10 }}>
          教师机与学生机之间的连通方式。同一网段留空即可自动发现；跨网段、走内网穿透或
          经过代理上网的环境，在下面填代理地址（学生机会经由该代理访问教师机）。
        </div>
        <div style={{ display: 'grid', gap: 8 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <span className="oc-usage-sub" style={{ width: 92 }}>
              教师机地址
            </span>
            <Input
              value={lan.serverUrl}
              onChange={(_e, data) => setLan({ ...lan, serverUrl: data.value })}
              placeholder="http://192.168.1.20:38900（留空＝局域网自动发现）"
              style={{ flex: 1 }}
            />
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <span className="oc-usage-sub" style={{ width: 92 }}>
              服务端口
            </span>
            <Input
              value={String(lan.port)}
              onChange={(_e, data) => setLan({ ...lan, port: Number(data.value) || 38900 })}
              style={{ width: 120 }}
            />
            <span className="oc-usage-sub">教师机监听端口（默认 38900，可在机房管理页改）</span>
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <span className="oc-usage-sub" style={{ width: 92 }}>
              代理地址
            </span>
            <Input
              value={lan.proxy}
              onChange={(_e, data) => setLan({ ...lan, proxy: data.value })}
              placeholder="http://127.0.0.1:7890　或　socks5://user:pass@127.0.0.1:1080（留空＝直连）"
              style={{ flex: 1 }}
            />
          </div>
          <div className="oc-actions">
            <Button appearance="primary" size="small" disabled={lanBusy} onClick={() => void saveLan()}>
              保存
            </Button>
            <Button appearance="secondary" size="small" disabled={lanBusy} onClick={() => void testLan()}>
              测试连通
            </Button>
            <span className="oc-usage-sub">{lanMsg}</span>
          </div>
          <div className="oc-usage-sub">
            代理支持带账号密码（形如 socks5://user:pass@主机:端口）。局域网的发现与配对
            始终直连，不受代理影响，避免"设了代理就连不上教室里的机器"。
          </div>
        </div>
      </div>

      <div className="oc-panel-title" style={{ fontSize: 12, opacity: 0.8, marginTop: 20 }}>
        局域网放行（手机与跨机功能的前提）
      </div>
      <div className="oc-panel" style={{ marginBottom: 12 }}>
        <div className="oc-usage-sub" style={{ marginBottom: 8 }}>
          Windows 防火墙默认拦截入站连接：本机能打开的服务，同网段的手机或其他电脑却连不上。
          手机控制台、临时传输、机房协同都要先放行，这里一次开好。
        </div>
        <div className="oc-actions">
          <Button
            appearance={fw?.allowed ? 'secondary' : 'primary'}
            size="small"
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              try {
                const result = await api.firewall_allow()
                setConsoleMsg(result?.message ?? '')
                window.setTimeout(() => void loadFw(), 3000)
              } finally {
                setBusy(false)
              }
            }}
          >
            {fw?.allowed ? '重新放行' : '一键放行'}
          </Button>
          <Button size="small" appearance="secondary" disabled={busy} onClick={() => void loadFw()}>
            刷新状态
          </Button>
          <Button
            size="small"
            appearance="transparent"
            disabled={busy}
            onClick={async () => {
              if (!window.confirm('撤销放行后，同网段的手机与其他电脑将无法访问本机服务，确定吗？')) return
              setBusy(true)
              try {
                const result = await api.firewall_revoke()
                setConsoleMsg(result?.message ?? '')
                window.setTimeout(() => void loadFw(), 3000)
              } finally {
                setBusy(false)
              }
            }}
          >
            撤销放行
          </Button>
          <span className="oc-usage-sub">
            {fw?.supported === false
              ? '非 Windows 系统无需放行'
              : fw?.allowed
                ? `已放行 TCP ${(fw?.ports ?? []).join('、')} 与 UDP ${fw?.discoveryPort}`
                : fw?.message || '读取中…'}
          </span>
        </div>
      </div>

      <div className="oc-panel-title" style={{ fontSize: 12, opacity: 0.8, marginTop: 20 }}>
        密码保护（防止学生改配置）
      </div>
      <div className="oc-panel" style={{ marginBottom: 12 }}>
        <div className="oc-usage-sub" style={{ marginBottom: 8 }}>
          开启后，点开受保护的页面会先要求输入密码；日常使用（体检、修复、工具箱、音乐、
          壁纸）不受影响。密码只保存散列值，不存明文。
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {pc?.enabled && (
            <Input
              type="password"
              value={pcCur}
              onChange={(_e, d) => setPcCur(d.value)}
              placeholder="当前密码"
              style={{ width: 150 }}
            />
          )}
          <Input
            type="password"
            value={pcNew}
            onChange={(_e, d) => setPcNew(d.value)}
            placeholder={pc?.enabled ? '新密码（至少 4 位）' : '设置密码（至少 4 位）'}
            style={{ width: 180 }}
          />
          <Button
            appearance="primary"
            size="small"
            disabled={busy}
            onClick={() => void savePasscode()}
          >
            {pc?.enabled ? '修改密码' : '启用密码保护'}
          </Button>
          {pc?.enabled && (
            <>
              <Button
                size="small"
                appearance="secondary"
                disabled={busy}
                onClick={async () => {
                  const result = await api.passcode_lock()
                  setPcMsg(result?.message ?? '')
                  await loadPc()
                  // 通知主界面：当前页若受保护，立刻切换到锁定界面（不用等切页）
                  window.dispatchEvent(new Event('oc-passcode-locked'))
                }}
              >
                立即重新上锁
              </Button>
              <Button size="small" appearance="transparent" disabled={busy} onClick={() => void clearPasscode()}>
                关闭保护
              </Button>
            </>
          )}
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 8, alignItems: 'center' }}>
          <span className="oc-usage-sub">保护范围：</span>
          {Object.entries(pc?.pages ?? { settings: '设置', security: '安全', tasks: '定时任务', lan: '机房管理' }).map(
            ([key, label]) => (
              <label key={key} style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: 13 }}>
                <input
                  type="checkbox"
                  checked={pcPages.includes(key)}
                  onChange={() =>
                    setPcPages((prev) =>
                      prev.includes(key) ? prev.filter((p) => p !== key) : [...prev, key]
                    )
                  }
                />
                {String(label)}
              </label>
            )
          )}
          {pc?.enabled && (
            <Button
              size="small"
              appearance="secondary"
              disabled={busy}
              onClick={async () => {
                const result = await api.passcode_set_protected(pcPages)
                setPcMsg(result?.message ?? '')
                await loadPc()
              }}
            >
              保存保护范围
            </Button>
          )}
          <span className="oc-usage-sub">
            {pc?.enabled
              ? pc?.unlocked
                ? '当前：已解锁（闲置 30 分钟自动重新上锁）'
                : '当前：已锁定'
              : '当前：未启用'}
          </span>
        </div>
        {pcMsg && (
          <div className="oc-usage-sub" style={{ marginTop: 8 }}>
            {pcMsg}
          </div>
        )}
        <div className="oc-usage-sub" style={{ marginTop: 8 }}>
          忘记密码：删除数据目录下的 passcode.json 即可复位（删除前请确认是本人操作）。
        </div>
      </div>

      <div className="oc-panel-title" style={{ fontSize: 12, opacity: 0.8, marginTop: 20 }}>
        被链接（B 端加入老师机）
      </div>
      <div className="oc-panel" style={{ marginBottom: 12 }}>
        <div className="oc-usage-sub" style={{ marginBottom: 8 }}>
          B 端只作为被管理端：填老师机地址与配对码即可接入（地址留空则在本局域网内自动发现）。
          接入后老师机可统一下发体检、修复、清理、消息、文件与壁纸；
          本机的「机房管理」页只显示连接状态，不再提供主动连接入口。
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Input
            value={lan.serverUrl}
            onChange={(_e, data) => setLan({ ...lan, serverUrl: data.value })}
            placeholder="老师机地址（留空自动发现，如 192.168.1.10:38900）"
            style={{ minWidth: 280, flex: 1 }}
          />
          <Input
            type="password"
            value={joinCode}
            onChange={(_e, data) => setJoinCode(data.value)}
            placeholder="配对码"
            style={{ width: 160 }}
          />
          <Button size="small" appearance="primary" disabled={busy} onClick={() => void joinTeacher()}>
            连接老师机
          </Button>
          <Button size="small" appearance="secondary" disabled={busy} onClick={() => void leaveTeacher()}>
            断开
          </Button>
        </div>
        {joinMsg && (
          <div className="oc-usage-sub" style={{ marginTop: 8 }}>
            {joinMsg}
          </div>
        )}
      </div>

      <RemotePanel />

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
            {!updateBusy && selfUpdate && selfUpdate.ok === false && (
              <Button
                size="small"
                appearance="secondary"
                onClick={() =>
                  void api.open_url('https://github.com/HMUG12/OpenClass-Box/releases')
                }
              >
                手动打开发布页
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
        {storage && (
          <>
            <div className="oc-info-row">
              <span>运行模式</span>
              <span>
                {storage.portable
                  ? '便携模式（数据跟随程序，插到哪台机器都是同一套设置）'
                  : storage.frozen
                    ? '安装模式（数据在用户目录，不受权限影响）'
                    : '开发模式（数据在项目目录）'}
                {storage.writable === false ? ' · 数据目录不可写' : ''}
              </span>
            </div>
            <div className="oc-info-row">
              <span>配置文件</span>
              <span className="oc-mono">{storage.configFile}</span>
            </div>
          </>
        )}
        {diag && (
          <div className="oc-info-row">
            <span>最近一次保存</span>
            <span>
              {diag.lastSaveAt
                ? `${new Date(diag.lastSaveAt * 1000).toLocaleString()} · ${
                    diag.savedOk ? '成功' : '失败'
                  }`
                : '本次启动后还没有保存过设置'}
              {diag.fallback ? ` · 已自动改用备用位置` : ''}
            </span>
          </div>
        )}
        {diag && diag.savedOk === false && diag.lastError && (
          <div className="oc-list-warn" style={{ marginTop: 6 }}>
            上一次保存失败：{diag.lastError}
          </div>
        )}

        {/* 配置备份：改乱了能回退 */}
        <div style={{ borderTop: '1px solid var(--oc-border)', margin: '12px 0 8px' }} />
        <div className="oc-info-row">
          <span>配置备份</span>
          <span>
            {backups
              ? `${backups.count} 份 · ${backups.totalKB} KB（保留最近 ${backups.keep} 份）`
              : '读取中…'}
          </span>
        </div>
        <div className="oc-usage-sub" style={{ marginBottom: 8 }}>
          每天启动时自动留一份快照。设置被改乱、被覆盖时，可回退到之前那份；
          恢复前会自动把「当前配置」另存一份，随时能反悔。
        </div>
        <div className="oc-actions">
          <Button size="small" appearance="primary" disabled={busy} onClick={() => void createBackup()}>
            立即备份
          </Button>
          <Button size="small" appearance="secondary" disabled={busy} onClick={() => void loadBackups()}>
            刷新列表
          </Button>
          {backupMsg && <span className="oc-usage-sub">{backupMsg}</span>}
        </div>
        {backups?.items?.length > 0 && (
          <div className="oc-list" style={{ marginTop: 8 }}>
            {backups.items.slice(0, 8).map((item: any) => (
              <div className="oc-list-row" key={item.name}>
                <div className="oc-list-main">
                  <div className="oc-list-title">
                    {item.time} · {item.reason}
                    {item.valid ? '' : '（内容已损坏）'}
                  </div>
                  <div className="oc-list-sub">
                    {item.keys} 项设置 · {(item.size / 1024).toFixed(1)} KB
                  </div>
                </div>
                <Button
                  size="small"
                  appearance="secondary"
                  disabled={busy || !item.valid}
                  onClick={() => void restoreBackup(item.name)}
                >
                  恢复
                </Button>
              </div>
            ))}
          </div>
        )}
        <div className="oc-hint" style={{ marginTop: 8 }}>
          所有设置会立即写入上面的目录。若程序安装在 Program Files 这类受保护位置，
          会自动改用用户目录（%LOCALAPPDATA%\OpenClass-Box）——
          Program Files 下管理员与普通用户看到的不是同一份文件，配置文件放那里会出现
          「这次保存成功、下次打开又变回去」。
        </div>
        {storage?.migratedFrom && (
          <div className="oc-hint" style={{ marginTop: 8 }}>
            已从旧位置迁移配置：{storage.migratedFrom}
            （原位置的文件不会被删除，确认无误后可自行清理）
          </div>
        )}
        <div className="oc-actions" style={{ marginTop: 10 }}>
          <Button appearance="secondary" onClick={() => void api.open_tool_dir()}>
            打开 tools 目录
          </Button>
        </div>
      </div>
    </div>
  )
}
