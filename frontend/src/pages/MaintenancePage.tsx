import { useEffect, useState } from 'react'
import { Button, Input, Spinner } from '@fluentui/react-components'
import { api } from '../api'
import HealthPage from './HealthPage'
import DiagnosticsPage from './DiagnosticsPage'
import TermPanel from '../components/TermPanel'

function formatSize(bytes: number): string {
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

/** 修复与清理（原独立「维护」页的全部内容） */
function RepairPanel() {
  const [cleanItems, setCleanItems] = useState<any[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [cleanBusy, setCleanBusy] = useState(false)
  const [cleanMsg, setCleanMsg] = useState('')

  const [diag, setDiag] = useState<any>(null)
  const [diagBusy, setDiagBusy] = useState(false)

  const [pkgDir, setPkgDir] = useState('')
  const [packages, setPackages] = useState<any[]>([])
  const [pkgMsg, setPkgMsg] = useState('')

  const [points, setPoints] = useState<any[]>([])
  const [pointMsg, setPointMsg] = useState('')
  const [pointBusy, setPointBusy] = useState(false)

  const loadCleanup = async () => {
    try {
      const result = await api.analyze_cleanup()
      const items = result?.items ?? []
      setCleanItems(items)
      setSelected(items.filter((i: any) => i.exists && i.size > 0).map((i: any) => i.key))
    } catch {
      setCleanItems([])
    }
  }

  const loadPackages = async (dir = '') => {
    try {
      const result = await api.list_packages(dir)
      setPackages(result?.items ?? [])
      setPkgMsg(result?.message ?? '')
    } catch {
      setPackages([])
    }
  }

  const loadPoints = async () => {
    try {
      const result = await api.list_restore_points()
      setPoints(result?.points ?? [])
      setPointMsg(result?.message ?? '')
    } catch {
      setPoints([])
    }
  }

  useEffect(() => {
    void loadCleanup()
    void loadPackages()
    void loadPoints()
  }, [])

  const toggle = (key: string) => {
    setSelected((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]))
  }

  const clean = async () => {
    if (!selected.length) return
    setCleanBusy(true)
    setCleanMsg('')
    try {
      const result = await api.run_cleanup(selected)
      setCleanMsg(`${(result?.details ?? []).join('；')}（共释放 ${formatSize(result?.freed ?? 0)}）`)
      await loadCleanup()
    } catch {
      setCleanMsg('清理失败，请稍后重试')
    } finally {
      setCleanBusy(false)
    }
  }

  const diagnose = async () => {
    setDiagBusy(true)
    try {
      setDiag(await api.run_netdiag())
    } catch {
      setDiag(null)
    } finally {
      setDiagBusy(false)
    }
  }

  const install = async (path: string) => {
    const result = await api.install_package(path)
    setPkgMsg(result?.message ?? '')
  }

  const checkLastResult = async () => {
    try {
      const result = await api.restore_last_result()
      setPointMsg(result?.message ?? '')
    } catch {
      setPointMsg('读取结果失败')
    }
  }

  const createPoint = async () => {
    setPointBusy(true)
    setPointMsg('')
    try {
      // 先看系统保护开没开：没开的话 Checkpoint-Computer 必然失败，先说清楚原因
      const protection = await api.restore_protection()
      if (protection?.enabled === false) {
        setPointMsg(protection.message || '系统保护未开启，无法创建还原点')
        return
      }
      const result = await api.create_restore_point()
      setPointMsg(result?.message ?? '')
      await loadPoints()
    } catch {
      setPointMsg('创建失败，请稍后重试')
    } finally {
      setPointBusy(false)
    }
  }

  return (
    <>
      {/* ── 磁盘清理 ── */}
      <div className="oc-panel">
        <div className="oc-panel-title">磁盘清理</div>
        {cleanItems.length === 0 ? (
          <div className="oc-list-sub" style={{ marginTop: 8 }}>
            正在统计…
          </div>
        ) : (
          <>
            <div className="oc-list">
              {cleanItems.map((item) => (
                <div className="oc-list-row" key={item.key}>
                  <div className="oc-list-main">
                    <div className="oc-list-title">
                      <input
                        type="checkbox"
                        checked={selected.includes(item.key)}
                        onChange={() => toggle(item.key)}
                        disabled={!item.exists}
                        style={{ marginRight: 8 }}
                      />
                      {item.name}
                    </div>
                    <div className="oc-list-sub">{item.desc}</div>
                  </div>
                  <div className="oc-list-sub" style={{ whiteSpace: 'nowrap' }}>
                    {item.exists ? formatSize(item.size) : '无'}
                  </div>
                </div>
              ))}
            </div>
            <div className="oc-actions" style={{ marginTop: 12 }}>
              <Button appearance="primary" onClick={clean} disabled={cleanBusy || !selected.length}>
                {cleanBusy ? '正在清理…' : `立即清理（已选 ${selected.length} 项）`}
              </Button>
              <Button appearance="secondary" onClick={loadCleanup} disabled={cleanBusy}>
                重新统计
              </Button>
            </div>
            {cleanMsg && (
              <div className="oc-list-sub" style={{ marginTop: 8 }}>
                {cleanMsg}
              </div>
            )}
          </>
        )}
      </div>

      {/* ── 网络诊断 ── */}
      <div className="oc-panel" style={{ marginTop: 12 }}>
        <div className="oc-panel-title">网络诊断</div>
        <div className="oc-list-sub" style={{ marginBottom: 10 }}>
          断网时依次检查：本机地址 → 局域网 → 域名解析 → 外网
        </div>
        <div className="oc-actions">
          <Button appearance="primary" onClick={diagnose} disabled={diagBusy}>
            {diagBusy ? '诊断中…' : '开始诊断'}
          </Button>
          {diag && !diagBusy && (
            <span className="oc-list-sub">
              {diag.healthy ? '✅ 网络一切正常' : `⚠️ ${diag.total - diag.okCount} 项未通过`}
            </span>
          )}
        </div>

        {diagBusy && (
          <div style={{ marginTop: 12 }}>
            <Spinner size="tiny" label="正在检查网络，请稍候…" />
          </div>
        )}

        {diag && !diagBusy && (
          <div className="oc-list" style={{ marginTop: 10 }}>
            {diag.items.map((item: any) => (
              <div className="oc-list-row" key={item.name}>
                <div className="oc-list-main">
                  <div className="oc-list-title">
                    {item.ok ? '✅' : '❌'} {item.name}
                  </div>
                  <div className="oc-list-sub">{item.detail}</div>
                  {!item.ok && item.suggest && <div className="oc-list-warn">建议：{item.suggest}</div>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── 离线软件目录 ── */}
      <div className="oc-panel" style={{ marginTop: 12 }}>
        <div className="oc-panel-title">离线软件目录</div>
        <div className="oc-list-sub" style={{ marginBottom: 10 }}>
          把常用软件的安装包放进程序目录下的 software 文件夹，这里会自动列出来，点一下就装
        </div>
        <div className="oc-searchbar">
          <Input
            value={pkgDir}
            onChange={(_, data) => setPkgDir(data.value)}
            placeholder="软件目录（留空使用默认 software 文件夹）"
            style={{ flex: 1 }}
          />
          <Button appearance="secondary" onClick={() => loadPackages(pkgDir)}>
            刷新
          </Button>
        </div>
        {packages.length === 0 ? (
          <div className="oc-list-sub" style={{ marginTop: 10 }}>
            {pkgMsg || '目录中暂无安装包'}
          </div>
        ) : (
          <div className="oc-list oc-scroll">
            {packages.map((pkg) => (
              <div className="oc-list-row" key={pkg.path}>
                <div className="oc-list-main">
                  <div className="oc-list-title">{pkg.name}</div>
                  <div className="oc-list-sub">{formatSize(pkg.size)}</div>
                </div>
                <Button size="small" onClick={() => install(pkg.path)}>
                  安装
                </Button>
              </div>
            ))}
          </div>
        )}
        {pkgMsg && packages.length > 0 && (
          <div className="oc-list-sub" style={{ marginTop: 8 }}>
            {pkgMsg}
          </div>
        )}
      </div>

      {/* ── 系统还原点 ── */}
      <div className="oc-panel" style={{ marginTop: 12 }}>
        <div className="oc-panel-title">系统还原点</div>
        <div className="oc-list-sub" style={{ marginBottom: 10 }}>
          装软件或改设置前先建一个还原点，出问题可以回滚。Windows 默认 24 小时内只允许创建一个，这是系统限制
        </div>
        <div className="oc-actions">
          <Button appearance="primary" onClick={createPoint} disabled={pointBusy}>
            {pointBusy ? '正在请求…' : '创建还原点'}
          </Button>
          <Button appearance="secondary" onClick={loadPoints} disabled={pointBusy}>
            刷新列表
          </Button>
          <Button appearance="secondary" onClick={() => void checkLastResult()} disabled={pointBusy}>
            查看上次结果
          </Button>
        </div>
        {pointMsg && (
          <div className="oc-list-sub" style={{ marginTop: 8 }}>
            {pointMsg}
          </div>
        )}
        {points.length > 0 && (
          <div className="oc-list oc-scroll">
            {points.map((pt: any, index: number) => (
              <div className="oc-list-row" key={index}>
                <div className="oc-list-main">
                  <div className="oc-list-title">{pt.Description || '（无描述）'}</div>
                  <div className="oc-list-sub">{String(pt.CreationTime ?? '')}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  )
}

/** 便携急救盘：U 盘随插随用 */
function PortablePanel() {
  const [status, setStatus] = useState<any>(null)
  const [drives, setDrives] = useState<any[]>([])
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const load = async () => {
    try {
      const [s, d] = await Promise.all([api.portable_status(), api.list_removable_drives()])
      setStatus(s)
      setDrives(d ?? [])
    } catch {
      setStatus(null)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const make = async (drive: string) => {
    const ok = window.confirm(
      `确定把精简版程序复制到 ${drive} 吗？\n\n` +
        `· 会在 U 盘上新建 OpenClass-Box 目录\n` +
        `· 不会格式化整盘，也不影响盘上其他文件\n` +
        `· 不含 OpenOffice / VLC / mpv 等大体积工具`
    )
    if (!ok) return
    setBusy(true)
    setMsg('正在制作，请稍候（需要复制数百 MB）…')
    try {
      const result = await api.make_rescue_usb(drive)
      setMsg(result?.message ?? '')
    } catch (error) {
      setMsg(`制作失败：${error}`)
    } finally {
      setBusy(false)
      await load()
    }
  }

  return (
    <>
      <div className="oc-panel">
        <div className="oc-panel-title">便携运行状态</div>
        <div className="oc-info-row">
          <span>运行位置</span>
          <span>
            {status?.portable ? '可移动介质（U 盘）· 配置随身携带' : '本机磁盘'}
          </span>
        </div>
        <div className="oc-info-row">
          <span>数据目录</span>
          <span style={{ fontFamily: 'var(--oc-mono)' }}>{status?.dataDir || '—'}</span>
        </div>
        <div className="oc-hint" style={{ marginTop: 10 }}>
          程序放在 U 盘上运行时，配置、安全记录、收发文件都写在 U 盘的 data 目录，
          换一台电脑插上就是同一套设置，拔走不在对方机器留痕。
        </div>
      </div>

      <div className="oc-panel" style={{ marginTop: 12 }}>
        <div className="oc-panel-title">
          制作便携急救盘
          <Button
            size="small"
            appearance="secondary"
            style={{ marginLeft: 10 }}
            onClick={() => void load()}
            disabled={busy}
          >
            刷新磁盘列表
          </Button>
        </div>
        <div className="oc-hint" style={{ marginBottom: 10 }}>
          把精简版程序复制到 U 盘：系统崩了、或被装乱了也能插上就用 ——
          一键体检、修复、磁盘清理、诊断包、硬件信息。
        </div>
        {drives.length === 0 ? (
          <div className="oc-hint">
            未检测到可移动磁盘（U 盘 / 移动硬盘）。插入后点「刷新磁盘列表」。
          </div>
        ) : (
          <div className="oc-list">
            {drives.map((item) => (
              <div className="oc-list-row" key={item.drive}>
                <div className="oc-list-main">
                  <div className="oc-list-title">{item.drive}</div>
                  <div className="oc-list-sub">
                    剩余 {formatSize(item.free)} / 共 {formatSize(item.total)}
                  </div>
                </div>
                <Button
                  size="small"
                  appearance="primary"
                  disabled={busy}
                  onClick={() => void make(item.drive)}
                >
                  {busy ? '制作中…' : '制作急救盘'}
                </Button>
              </div>
            ))}
          </div>
        )}
        {msg && (
          <div className="oc-list-sub" style={{ marginTop: 10 }}>
            {msg}
          </div>
        )}
      </div>

      <div className="oc-panel" style={{ marginTop: 12 }}>
        <div className="oc-panel-title">急救盘里的清单</div>
        <div className="oc-hint">
          1. 插上 U 盘 → 双击根目录「启动OpenClass-Box.bat」（需要时右键以管理员身份运行）
          <br />
          2. 维护 → 一键体检 / 修复（DNS、临时文件、音频服务、网络重置）/ 磁盘清理
          <br />
          3. 维护 → 诊断 → 生成诊断包，交给报修人员
          <br />
          4. 硬件信息 → 温度 / 显存 / 硬盘健康等实测数据
          <br />
          5. 机房管理 → 把出问题的机器临时设为 B 端，接入老师的 A 端统一处理
        </div>
      </div>
    </>
  )
}

type Tab = 'checkup' | 'repair' | 'diagnostics' | 'term' | 'portable'

export default function MaintenancePage() {
  const [tab, setTab] = useState<Tab>('checkup')

  return (
    <div className="oc-page">
      <div className="oc-page-header">
        <div className="oc-page-title">维护</div>
        <div className="oc-page-desc">一键体检 · 修复与清理 · 诊断，常用维护能力集中在这里</div>
      </div>

      <div className="oc-toolbar" style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', gap: 6 }}>
          <Button
            appearance={tab === 'checkup' ? 'primary' : 'secondary'}
            onClick={() => setTab('checkup')}
          >
            一键体检
          </Button>
          <Button
            appearance={tab === 'repair' ? 'primary' : 'secondary'}
            onClick={() => setTab('repair')}
          >
            修复与清理
          </Button>
          <Button
            appearance={tab === 'diagnostics' ? 'primary' : 'secondary'}
            onClick={() => setTab('diagnostics')}
          >
            诊断
          </Button>
          <Button
            appearance={tab === 'term' ? 'primary' : 'secondary'}
            onClick={() => setTab('term')}
          >
            学期模式
          </Button>
          <Button
            appearance={tab === 'portable' ? 'primary' : 'secondary'}
            onClick={() => setTab('portable')}
          >
            便携急救盘
          </Button>
        </div>
      </div>

      {tab === 'checkup' && <HealthPage />}
      {tab === 'repair' && <RepairPanel />}
      {tab === 'diagnostics' && <DiagnosticsPage />}
      {tab === 'term' && <TermPanel />}
      {tab === 'portable' && <PortablePanel />}
    </div>
  )
}
