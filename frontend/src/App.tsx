import { useCallback, useEffect, useState } from 'react'
import {
  FluentProvider,
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
  BookRegular,
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

  // ── 初始加载 ──
  useEffect(() => {
    void (async () => {
      try {
        const [saved, list, info] = await Promise.all([
          api.get_theme(),
          api.list_tools(),
          api.get_info(),
        ])
        if (saved) setThemeMode(saved)
        setTools(list)
        setVersion(info.version)
      } finally {
        setLoading(false)
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
    { id: 'maintenance', label: '维护', icon: <ToolboxRegular fontSize={16} /> },
    { id: 'classroom', label: '课堂', icon: <BookRegular fontSize={16} /> },
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
          onSelect={(id) => setPage(id as PageId)}
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

          {loading ? (
            <div className="oc-empty">
              <Spinner size="medium" label="正在加载…" />
            </div>
          ) : (
            <ErrorBoundary key={page}>{renderPage()}</ErrorBoundary>
          )}

          {/* 右下角版本标识 */}
          <div className="oc-version-badge">v{version || '0.1.4 Beta'}</div>
        </div>
      </div>
    </FluentProvider>
  )
}
