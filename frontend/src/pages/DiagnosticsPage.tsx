import { useEffect, useState } from 'react'
import { Button, Spinner } from '@fluentui/react-components'
import { api } from '../api'

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

type Tab = 'process' | 'service' | 'startup' | 'log'

export default function DiagnosticsPage() {
  const [packBusy, setPackBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const [processes, setProcesses] = useState<any[]>([])
  const [procTotal, setProcTotal] = useState(0)
  const [procBusy, setProcBusy] = useState(false)

  const [services, setServices] = useState<any[]>([])
  const [svcTotal, setSvcTotal] = useState(0)

  const [startup, setStartup] = useState<any[]>([])

  const [tab, setTab] = useState<Tab>('process')

  // ── 运行日志 ──
  const [logText, setLogText] = useState('')
  const [logInfo, setLogInfo] = useState<any>(null)

  const loadLog = async () => {
    try {
      const result = await api.app_log_tail(300)
      setLogText(result?.text ?? '')
      setLogInfo(result ?? null)
    } catch {
      setLogText('')
    }
  }

  const clearLog = async () => {
    try {
      const result = await api.app_log_clear()
      setMsg(result?.message ?? '')
      await loadLog()
    } catch {
      /* 忽略 */
    }
  }

  useEffect(() => {
    if (tab === 'log') void loadLog()
  }, [tab])

  const loadProcesses = async () => {
    setProcBusy(true)
    try {
      const result = await api.list_processes(40)
      setProcesses(result?.items ?? [])
      setProcTotal(result?.total ?? 0)
    } catch {
      setProcesses([])
    } finally {
      setProcBusy(false)
    }
  }

  const loadServices = async () => {
    try {
      const result = await api.list_services(150)
      setServices(result?.items ?? [])
      setSvcTotal(result?.total ?? 0)
    } catch {
      setServices([])
    }
  }

  const loadStartup = async () => {
    try {
      const result = await api.list_startup()
      setStartup(result?.items ?? [])
    } catch {
      setStartup([])
    }
  }

  useEffect(() => {
    void loadProcesses()
    void loadServices()
    void loadStartup()
  }, [])

  const exportPack = async () => {
    setPackBusy(true)
    setMsg('')
    try {
      const result = await api.export_diagnostics()
      setMsg(
        result?.ok
          ? `已生成：${result.path}（${formatSize(result.size)}，含 ${(result.parts ?? []).join('、')}）`
          : (result?.message ?? '生成失败')
      )
    } catch {
      setMsg('生成失败，请稍后重试')
    } finally {
      setPackBusy(false)
    }
  }

  const kill = async (pid: number, name: string) => {
    if (!window.confirm(`确定结束进程 ${name}（PID ${pid}）吗？未保存的数据可能丢失。`)) return
    const result = await api.kill_process(pid)
    setMsg(result?.message ?? '')
    void loadProcesses()
  }

  return (
    <div className="oc-page">
      <div className="oc-page-header">
        <div className="oc-page-title">诊断</div>
        <div className="oc-page-desc">排障用的系统视图与日志采集（数据均为本机实时查询）</div>
      </div>

      {/* ── 诊断包 ── */}
      <div className="oc-panel">
        <div className="oc-panel-title">诊断包</div>
        <div className="oc-list-sub" style={{ marginBottom: 10 }}>
          一键打包系统信息、体检结果与最近事件日志（zip），可直接发给维修人员
        </div>
        <div className="oc-actions">
          <Button appearance="primary" onClick={exportPack} disabled={packBusy}>
            {packBusy ? '正在采集…' : '生成诊断包'}
          </Button>
          <Button appearance="secondary" onClick={() => setTab('process')}>
            进程
          </Button>
          <Button appearance="secondary" onClick={() => setTab('service')}>
            服务
          </Button>
          <Button appearance="secondary" onClick={() => setTab('startup')}>
            启动项
          </Button>
          <Button appearance="secondary" onClick={() => setTab('log')}>
            运行日志
          </Button>
        </div>
        {msg && (
          <div className="oc-list-sub" style={{ marginTop: 8 }}>
            {msg}
          </div>
        )}
      </div>

      {/* ── 运行日志 ── */}
      {tab === 'log' && (
        <div className="oc-panel" style={{ marginTop: 12 }}>
          <div className="oc-panel-title">
            运行日志
            <Button
              size="small"
              appearance="secondary"
              style={{ marginLeft: 10 }}
              onClick={() => void loadLog()}
            >
              刷新
            </Button>
            <Button
              size="small"
              appearance="secondary"
              style={{ marginLeft: 6 }}
              onClick={() => void clearLog()}
            >
              清理
            </Button>
            <Button
              size="small"
              appearance="secondary"
              style={{ marginLeft: 6 }}
              onClick={() => void api.open_log_folder()}
            >
              打开日志目录
            </Button>
          </div>
          <div className="oc-list-sub" style={{ marginBottom: 8 }}>
            {logInfo?.exists
              ? `日志文件：${logInfo.path}（${formatSize(logInfo.size)}）`
              : '还没有日志文件：程序运行期间的异常与关键操作会记录在这里'}
          </div>
          <pre
            style={{
              maxHeight: 380,
              overflow: 'auto',
              margin: 0,
              padding: 12,
              borderRadius: 'var(--oc-radius)',
              background: 'var(--oc-surface)',
              border: '1px solid var(--oc-border)',
              fontSize: 12,
              lineHeight: 1.65,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all',
              userSelect: 'text',
            }}
          >
            {logText || '（暂无日志）'}
          </pre>
          <div className="oc-list-sub" style={{ marginTop: 8 }}>
            日志只记录程序自身的异常与关键操作（修复、导出、插件安装等），不含个人文件内容；
            文件超过 1 MB 会自动轮转，最多保留 3 份。
          </div>
        </div>
      )}

      {/* ── 进程 ── */}
      {tab === 'process' && (
        <div className="oc-panel" style={{ marginTop: 12 }}>
          <div className="oc-panel-title">
            进程（按内存占用，共 {procTotal} 个）
            <Button size="small" appearance="secondary" onClick={loadProcesses} style={{ marginLeft: 10 }}>
              刷新
            </Button>
          </div>
          {procBusy && processes.length === 0 ? (
            <Spinner size="tiny" label="读取中…" />
          ) : (
            <div className="oc-list oc-scroll">
              {processes.map((proc) => (
                <div className="oc-list-row" key={proc.pid}>
                  <div className="oc-list-main">
                    <div className="oc-list-title">{proc.name}</div>
                    <div className="oc-list-sub">
                      PID {proc.pid} · {proc.user || '—'} · 内存 {proc.memoryMB} MB
                    </div>
                  </div>
                  <Button size="small" onClick={() => kill(proc.pid, proc.name)}>
                    结束
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── 服务 ── */}
      {tab === 'service' && (
        <div className="oc-panel" style={{ marginTop: 12 }}>
          <div className="oc-panel-title">
            服务（共 {svcTotal} 个，显示前 {services.length} 个）
          </div>
          <div className="oc-list oc-scroll">
            {services.map((svc) => (
              <div className="oc-list-row" key={svc.name}>
                <div className="oc-list-main">
                  <div className="oc-list-title">{svc.display || svc.name}</div>
                  <div className="oc-list-sub">
                    {svc.name} · {svc.status === 'running' ? '运行中' : '已停止'} · {svc.startType}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── 启动项 ── */}
      {tab === 'startup' && (
        <div className="oc-panel" style={{ marginTop: 12 }}>
          <div className="oc-panel-title">开机启动项（共 {startup.length} 个）</div>
          <div className="oc-list oc-scroll">
            {startup.length === 0 ? (
              <div className="oc-list-sub" style={{ marginTop: 8 }}>
                未检测到启动项
              </div>
            ) : (
              startup.map((item, index) => (
                <div className="oc-list-row" key={index}>
                  <div className="oc-list-main">
                    <div className="oc-list-title">{item.name}</div>
                    <div className="oc-list-sub">
                      {item.source} · {item.command}
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}
