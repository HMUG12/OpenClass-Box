import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  FluentProvider,
  Input,
  webLightTheme,
  webDarkTheme,
  MessageBar,
  MessageBarBody,
  Spinner,
} from '@fluentui/react-components'
import {
  DesktopRegular,
  GaugeRegular,
  HardDriveRegular,
  InfoRegular,
  MusicNote1Regular,
  ChatRegular,
  TimerRegular,
  SettingsRegular,
  ShieldRegular,
  ToolboxRegular,
} from '@fluentui/react-icons'
import { api } from './api'
import type { ThemeMode, ToolSpec } from './types'
import TitleBar from './components/TitleBar'
import SideNav, { type NavItem } from './components/SideNav'
import DashboardPage from './pages/DashboardPage'
import ToolsPage from './pages/ToolsPage'
import SettingsPage from './pages/SettingsPage'
import AboutPage from './pages/AboutPage'
import SecurityPage from './pages/SecurityPage'
import MusicPage from './pages/MusicPage'
import WallpaperPage from './pages/WallpaperPage'
import MaintenancePage from './pages/MaintenancePage'
import HardwarePage from './pages/HardwarePage'
import LanPage from './pages/LanPage'
import ClassroomPage from './pages/ClassroomPage'
import ChatPage from './pages/ChatPage'
import TasksPage from './pages/TasksPage'
import ErrorBoundary from './components/ErrorBoundary'

/**
 * 导航结构（已按使用习惯合并）：
 *   一键体检 / 诊断  →  并入「维护」（页内分栏：体检 / 修复与清理 / 诊断）
 *   插件            →  并入「工具箱」（按来源筛选）
 *   更新            →  并入「设置」（含本软件版本检测与关闭行为）
 */
type PageId =
  | 'maintenance'
  | 'dashboard'
  | 'hardware'
  | 'lan'
  | 'classroom'
  | 'chat'
  | 'tasks'
  | 'tools'
  | 'music'
  | 'wallpaper'
  | 'security'
  | 'settings'
  | 'about'

/** 受密码保护的页面名称（与后端 passcode.PAGES 对应） */
const PAGE_LABEL: Record<string, string> = {
  settings: '设置',
  security: '安全',
  tasks: '定时任务',
  lan: '机房管理',
}

interface Toast {
  ok: boolean
  message: string
}

export default function App() {
  const [themeMode, setThemeMode] = useState<ThemeMode>('system')
  const [systemDark, setSystemDark] = useState(true)
  const [page, setPage] = useState<PageId>('dashboard')
  const [tools, setTools] = useState<ToolSpec[]>([])
  const [category, setCategory] = useState('all')
  const [loading, setLoading] = useState(true)
  const [toast, setToast] = useState<Toast | null>(null)
  const [version, setVersion] = useState('')
  const [hasUpdate, setHasUpdate] = useState(false)

  // ── 外观自定义（配色 / 圆角 / 字号 / 毛玻璃）──
  const [appearance, setAppearance] = useState<any>({
    accent: 'default',
    radius: 'standard',
    font: 'standard',
    glass: false,
  })

  // 把外观偏好写到 <html> 的 data-* 上：CSS 变量从这里往下继承，
  // 组件不用关心，也避免每个页面各自读一遍设置
  useEffect(() => {
    const root = document.documentElement
    if (appearance.accent && appearance.accent !== 'default') {
      root.setAttribute('data-accent', appearance.accent)
    } else {
      root.removeAttribute('data-accent')
    }
    root.setAttribute('data-radius', appearance.radius || 'standard')
    root.setAttribute('data-font', appearance.font || 'standard')
    root.setAttribute('data-glass', appearance.glass ? 'on' : 'off')
  }, [appearance])

  // ── 密码保护：受保护页面进入前需要解锁 ──
  const [unlockTarget, setUnlockTarget] = useState('')
  const [unlockCode, setUnlockCode] = useState('')
  const [unlockMsg, setUnlockMsg] = useState('')

  // 「立即上锁」（设置页触发）后立刻重新校验当前页：当场显示锁定界面，
  // 不用等用户切页才发现被锁
  useEffect(() => {
    const handler = () => {
      void (async () => {
        try {
          const check = await api.passcode_check(page)
          if (check?.need) {
            setUnlockTarget(page)
            setUnlockCode('')
            setUnlockMsg('')
          }
        } catch {
          /* 查询失败时按未锁处理，避免误挡正常使用 */
        }
      })()
    }
    window.addEventListener('oc-passcode-locked', handler)
    return () => window.removeEventListener('oc-passcode-locked', handler)
  }, [page])

  // ── 初始加载 ──
  useEffect(() => {
    void (async () => {
      try {
        const [saved, list, info, looks] = await Promise.all([
          api.get_theme(),
          api.list_tools(),
          api.get_info(),
          api.get_appearance(),
        ])
        if (saved) setThemeMode(saved)
        setTools(list)
        setVersion(info.version)
        if (looks) setAppearance(looks)
      } finally {
        setLoading(false)
        // 首屏数据就绪后再让窗口露面（窗口在 main 里是隐藏创建的）：
        // 用户第一眼看到的就是渲染好的界面，而不是白屏和转圈
        window.setTimeout(() => void api.frontend_ready(), 80)
      }
    })()
  }, [])

  // ── 本软件更新检测（有新版则在「设置」上显示角标） ──
  useEffect(() => {
    void (async () => {
      try {
        const result = await api.check_self_update()
        setHasUpdate(Boolean(result?.hasUpdate))
      } catch {
        /* 未联网时忽略，不误报 */
      }
    })()
  }, [])

  // ── 跟随系统 ──
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const sync = () => setSystemDark(mq.matches)
    sync()
    mq.addEventListener('change', sync)
    return () => mq.removeEventListener('change', sync)
  }, [])

  const isDark = themeMode === 'system' ? systemDark : themeMode === 'dark'

  // 切换页面：受保护的页面先要密码
  const selectPage = useCallback(async (id: string) => {
    try {
      const check = await api.passcode_check(id)
      if (check?.need) {
        setUnlockTarget(id)
        setUnlockCode('')
        setUnlockMsg('')
        return
      }
    } catch {
      /* 查不到保护状态时按未保护处理，不挡正常使用 */
    }
    setPage(id as PageId)
  }, [])

  const tryUnlock = useCallback(async () => {
    try {
      const result = await api.passcode_verify(unlockCode)
      if (result?.ok) {
        const target = unlockTarget
        setUnlockTarget('')
        setUnlockCode('')
        setUnlockMsg('')
        if (target) setPage(target as PageId)
      } else {
        setUnlockMsg(result?.message ?? '密码不正确')
      }
    } catch {
      setUnlockMsg('校验失败，请重试')
    }
  }, [unlockCode, unlockTarget])

  const refresh = useCallback(async () => {
    await api.refresh_tools()
    const list = await api.list_tools()
    setTools(list)
    setToast({ ok: true, message: `扫描完成，共 ${list.length} 个工具` })
    window.setTimeout(() => setToast(null), 2200)
  }, [])

  // 静默刷新：不弹提示；仅在列表确有变化时才更新 state，避免无谓重渲染
  const silentRefresh = useCallback(async () => {
    try {
      await api.refresh_tools()
      const list = await api.list_tools()
      setTools((prev) => (JSON.stringify(prev) === JSON.stringify(list) ? prev : list))
    } catch {
      /* 后台扫描失败时静默忽略，不影响使用 */
    }
  }, [])

  // 进入「工具箱」页时自动刷新一次
  useEffect(() => {
    if (page === 'tools') {
      void silentRefresh()
    }
  }, [page, silentRefresh])

  // 运行期间定期自动扫描，增删工具无需手动点「重新扫描」
  useEffect(() => {
    const timer = window.setInterval(() => void silentRefresh(), 30000)
    return () => window.clearInterval(timer)
  }, [silentRefresh])

  // 网址风险告警：复制到的网址被判定可疑/高危时顶部提示（不拦截任何操作）
  useEffect(() => {
    const timer = window.setInterval(async () => {
      try {
        const list = await api.url_alerts()
        if (list?.length) {
          const top = list[0]
          setToast({
            ok: false,
            message: `⚠️ 检测到可疑网址（${top.score} 分）：${top.url} — ${top.reasons?.[0] ?? ''}`,
          })
          window.setTimeout(() => setToast(null), 8000)
        }
      } catch {
        /* 拉取告警失败时忽略 */
      }
    }, 5000)
    return () => window.clearInterval(timer)
  }, [])

  const launch = useCallback(async (tool: ToolSpec) => {
    const result = await api.launch_tool(tool.id)
    setToast(result)
    window.setTimeout(() => setToast(null), result.ok ? 2600 : 5000)
  }, [])

  // 顺序约定：系统状态 / 硬件信息 固定在前两位；安全 / 设置 / 关于 固定在最后三位
  const navItems: NavItem[] = [
    { id: 'dashboard', label: '系统状态', icon: <GaugeRegular fontSize={16} /> },
    { id: 'hardware', label: '硬件信息', icon: <HardDriveRegular fontSize={16} /> },
    { id: 'lan', label: '机房管理', icon: <DesktopRegular fontSize={16} /> },
    // 「课堂」板块已并入「维护」（维护 → 课堂），此处不再单列
    { id: 'maintenance', label: '维护', icon: <ToolboxRegular fontSize={16} /> },
    { id: 'chat', label: '临时传输', icon: <ChatRegular fontSize={16} /> },
    { id: 'tasks', label: '定时任务', icon: <TimerRegular fontSize={16} /> },
    { id: 'tools', label: '工具箱', icon: <ToolboxRegular fontSize={16} />, badge: tools.length },
    { id: 'music', label: '音乐', icon: <MusicNote1Regular fontSize={16} /> },
    { id: 'wallpaper', label: '壁纸', icon: <SettingsRegular fontSize={16} /> },
    { id: 'security', label: '安全', icon: <ShieldRegular fontSize={16} /> },
    {
      id: 'settings',
      label: '设置',
      icon: <SettingsRegular fontSize={16} />,
      badge: hasUpdate ? 1 : undefined,
    },
    { id: 'about', label: '关于', icon: <InfoRegular fontSize={16} /> },
  ]

  const renderPage = () => {
    switch (page) {
      case 'lan':
        return <LanPage />
      case 'maintenance':
        return <MaintenancePage />
      case 'classroom':
        return <ClassroomPage />
      case 'chat':
        return <ChatPage />
      case 'tasks':
        return <TasksPage />
      case 'dashboard':
        return <DashboardPage />
      case 'hardware':
        return <HardwarePage />
      case 'tools':
        return (
          <ToolsPage
            tools={tools}
            category={category}
            setCategory={setCategory}
            onLaunch={launch}
            onRefresh={refresh}
          />
        )
      case 'music':
        return <MusicPage />
      case 'wallpaper':
        return <WallpaperPage />
      case 'security':
        return <SecurityPage />
      case 'settings':
        return <SettingsPage themeMode={themeMode} setThemeMode={setThemeMode} />
      case 'about':
        return <AboutPage toolCount={tools.length} />
      default:
        return null
    }
  }

  return (
    <FluentProvider theme={isDark ? webDarkTheme : webLightTheme} className="oc-root">
      <TitleBar />
      <div className="oc-body">
        <SideNav
          items={navItems}
          activeId={page}
          onSelect={(id) => void selectPage(id)}
          version={version}
        />

        <div className="oc-content">
          {toast && (
            <div style={{ padding: '8px 24px 0', flexShrink: 0 }}>
              <MessageBar intent={toast.ok ? 'success' : 'error'}>
                <MessageBarBody>{toast.message}</MessageBarBody>
              </MessageBar>
            </div>
          )}

          {unlockTarget ? (
            /* 锁定界面就显示在内容区里（不是盖住整个窗口的浮层）：
               侧栏仍可切换，切到未保护页面照常使用 */
            <div className="oc-unlock-wrap">
              <div className="oc-panel oc-unlock-card">
                <div className="oc-panel-title">需要密码</div>
                <div className="oc-hint" style={{ marginBottom: 10 }}>
                  「{PAGE_LABEL[unlockTarget] ?? unlockTarget}」受密码保护，输入密码后进入。
                </div>
                <Input
                  type="password"
                  value={unlockCode}
                  onChange={(_e, data) => setUnlockCode(data.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') void tryUnlock()
                  }}
                  placeholder="请输入密码"
                  autoFocus
                  style={{ width: '100%' }}
                />
                {unlockMsg && (
                  <div className="oc-list-warn" style={{ marginTop: 8 }}>
                    {unlockMsg}
                  </div>
                )}
                <div className="oc-actions" style={{ marginTop: 12 }}>
                  <Button appearance="primary" onClick={() => void tryUnlock()}>
                    解锁
                  </Button>
                  <Button
                    appearance="secondary"
                    onClick={() => {
                      setUnlockTarget('')
                      setUnlockMsg('')
                    }}
                  >
                    返回
                  </Button>
                </div>
                <div className="oc-hint" style={{ marginTop: 8 }}>
                  忘记密码：删除数据目录下的 passcode.json 即可复位（设置 → 存储位置可看到路径）。
                </div>
              </div>
            </div>
          ) : loading ? (
            <div className="oc-empty">
              <Spinner size="medium" label="正在加载…" />
            </div>
          ) : (
            <ErrorBoundary key={page}>{renderPage()}</ErrorBoundary>
          )}

        </div>
      </div>

    </FluentProvider>
  )
}
