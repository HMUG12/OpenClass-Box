import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Button } from '@fluentui/react-components'
import UsageBar from '../components/UsageBar'
import SparkLine from '../components/SparkLine'
import RingChart from '../components/RingChart'
import { api } from '../api'
import { formatBytes, formatDateTime, formatFrequency, formatRate, formatUptime } from '../format'
import type { HardwareInfo, IpInfo, Metrics, NetworkInfo, PublicIpResult } from '../types'

const DOWN_COLOR = '#0f6cbd'
const UP_COLOR = '#0e700e'

/** 占用率高时圆环转为橙/红，异常一眼可见 */
function ringColor(percent: number, base: string): string {
  if (percent >= 88) return '#c50f1f'
  if (percent >= 70) return '#9a6700'
  return base
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </>
  )
}

export default function DashboardPage() {
  const [hardware, setHardware] = useState<HardwareInfo | null>(null)
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [network, setNetwork] = useState<NetworkInfo | null>(null)
  const [ip, setIp] = useState<IpInfo | null>(null)
  const [publicIp, setPublicIp] = useState<PublicIpResult | null>(null)
  const [querying, setQuerying] = useState(false)

  // ── 课前准备：打开软件第一眼要知道"这台机器能不能上课" ──
  // 不在加载时自动跑（完整检查含课堂检测要十几秒，会让人以为软件卡住），
  // 先显示上次的结论与时间，要新的再点一下
  const [pre, setPre] = useState<any>(null)
  const [preBusy, setPreBusy] = useState(false)
  const [preMsg, setPreMsg] = useState('')

  useEffect(() => {
    void (async () => {
      try {
        setPre(await api.preflight_last())
      } catch {
        /* 忽略：开发预览模式 */
      }
    })()
  }, [])

  const runPreflight = async () => {
    setPreBusy(true)
    setPreMsg('')
    try {
      const result = await api.preflight()
      setPre(result)
      if (result?.errors?.length) setPreMsg(`部分项目未完成：${result.errors.join('；')}`)
    } catch {
      setPreMsg('检查失败，请稍后重试')
    } finally {
      setPreBusy(false)
    }
  }

  // 静态信息与网络拓扑：加载一次即可
  useEffect(() => {
    void (async () => {
      const [hw, net, ipInfo] = await Promise.all([
        api.get_hardware(),
        api.get_network(),
        api.get_ip(),
      ])
      setHardware(hw)
      setNetwork(net)
      setIp(ipInfo)
    })()
  }, [])

  // 硬件信息「先快后全」：显卡名 / 主板由后台 WMI 补齐，这里轮询直到就绪
  useEffect(() => {
    if (hardware?.hardwareReady) return
    let tries = 0
    const timer = window.setInterval(async () => {
      tries += 1
      if (tries > 15) {
        window.clearInterval(timer)
        return
      }
      try {
        const hw = await api.get_hardware()
        setHardware(hw)
        if (hw?.hardwareReady) window.clearInterval(timer)
      } catch {
        /* 忽略单次失败 */
      }
    }, 1500)
    return () => window.clearInterval(timer)
  }, [hardware?.hardwareReady])

  // 实时指标：1 秒轮询读取后端采样快照
  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const snapshot = await api.get_metrics()
        if (alive) setMetrics(snapshot)
      } catch {
        // 采样失败保持上一次数据，不影响展示
      }
    }
    void tick()
    const timer = window.setInterval(tick, 1000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  const queryPublicIp = useCallback(async () => {
    setQuerying(true)
    try {
      setPublicIp(await api.query_public_ip())
    } finally {
      setQuerying(false)
    }
  }, [])

  const down = formatRate(metrics?.network.download ?? 0)
  const up = formatRate(metrics?.network.upload ?? 0)
  const history = metrics?.history ?? []
  const axisMax = Math.max(...history.map((p) => Math.max(p.download, p.upload)), 1)

  const primaryDisk = hardware?.disks.find((d) => d.percent !== undefined) ?? hardware?.disks[0]
  const uptime = hardware ? Date.now() / 1000 - hardware.bootTime : 0

  if (!hardware || !metrics) {
    return (
      <div className="oc-page">
        <div className="oc-empty">正在采集系统状态…</div>
      </div>
    )
  }

  return (
    <div className="oc-page">
      <div className="oc-page-header">
        <div>
          <div className="oc-page-title">系统状态</div>
          <div className="oc-page-desc">
            {hardware.os.system} {hardware.os.release}
            {hardware.os.build ? ` · 内部版本 ${hardware.os.build}` : ''} · 已运行 {formatUptime(uptime)}
          </div>
        </div>
        {/* 右上角：自动识别当前设备型号与登录用户，多机环境一眼分辨 */}
        <div className="oc-device">
          <div className="oc-device-model">
            {[hardware.device?.manufacturer, hardware.device?.model]
              .filter(Boolean)
              .join(' ') || '设备型号未知'}
          </div>
          <div className="oc-device-sub">
            {hardware.device?.hostname || hardware.os.hostname}
            {hardware.device?.user ? ` · ${hardware.device.user}` : ''}
          </div>
        </div>
      </div>

      {/* ── 课前准备：打开第一眼就知道能不能上课 ── */}
      <div className="oc-panel" style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 220 }}>
            <div className="oc-panel-title" style={{ marginBottom: 4 }}>
              课前准备
            </div>
            {pre?.ok ? (
              <>
                <div className="oc-verdict-line">
                  <span className={`oc-verdict-badge oc-${pre.verdict}`}>{pre.verdictLabel}</span>
                  <span className="oc-usage-sub">{pre.headline}</span>
                </div>
                <div className="oc-usage-sub">上次检查：{pre.checkedAt}</div>
              </>
            ) : (
              <div className="oc-usage-sub">
                还没检查过。点一下，把体检、投影触摸、还原保护一起过一遍（约十几秒），
                之后这里会一直显示最近一次的结论。
              </div>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Button appearance="primary" onClick={() => void runPreflight()} disabled={preBusy}>
              {preBusy ? '检查中…' : pre?.ok ? '重新检查' : '检查一下'}
            </Button>
            {preMsg && <span className="oc-usage-sub">{preMsg}</span>}
          </div>
        </div>
        {pre?.ok && (pre.blocking?.length > 0 || pre.attention?.length > 0) && (
          <div className="oc-usage-sub" style={{ marginTop: 6 }}>
            需要关注：
            {[...(pre.blocking ?? []), ...(pre.attention ?? [])].join('、')}
          </div>
        )}
      </div>

      {/* ── 核心资源（圆环实时可视化） ── */}
      <div className="oc-grid oc-grid-3" style={{ marginBottom: 12 }}>
        <div className="oc-panel">
          <div className="oc-panel-title">处理器</div>
          <div className="oc-ring-row">
            <RingChart
              percent={metrics.cpu.percent}
              color={ringColor(metrics.cpu.percent, '#0f6cbd')}
              label="CPU"
              sub={formatFrequency(metrics.cpu.freqCurrent)}
            />
            <div className="oc-ring-meta">
              <div className="oc-usage-sub">
                {hardware.cpu.cores ?? '-'} 核 / {hardware.cpu.threads ?? '-'} 线程
              </div>
              <div className="oc-usage-sub">实时负载 {metrics.cpu.percent.toFixed(1)}%</div>
            </div>
          </div>
        </div>

        <div className="oc-panel">
          <div className="oc-panel-title">内存</div>
          <div className="oc-ring-row">
            <RingChart
              percent={metrics.memory.percent}
              color={ringColor(metrics.memory.percent, '#8764b8')}
              label="已用"
              sub={formatBytes(metrics.memory.total)}
            />
            <div className="oc-ring-meta">
              <div className="oc-usage-sub">已用 {formatBytes(metrics.memory.used)}</div>
              <div className="oc-usage-sub">可用 {formatBytes(metrics.memory.available)}</div>
            </div>
          </div>
        </div>

        <div className="oc-panel">
          <div className="oc-panel-title">存储</div>
          {primaryDisk ? (
            <div className="oc-ring-row">
              <RingChart
                percent={primaryDisk.percent}
                color={ringColor(primaryDisk.percent, '#0e700e')}
                label={primaryDisk.device}
                sub={formatBytes(primaryDisk.total)}
              />
              <div className="oc-ring-meta">
                <div className="oc-usage-sub">已用 {formatBytes(primaryDisk.used)}</div>
                <div className="oc-usage-sub">可用 {formatBytes(primaryDisk.free)}</div>
                <div className="oc-usage-sub">{primaryDisk.fstype}</div>
              </div>
            </div>
          ) : (
            <div className="oc-usage-sub">未检测到磁盘分区</div>
          )}
        </div>
      </div>

      {/* ── 网络实时 + IP ── */}
      <div className="oc-grid oc-grid-2" style={{ marginBottom: 12 }}>
        <div className="oc-panel">
          <div className="oc-panel-title">网络实时速率</div>
          <div className="oc-rate">
            <div className="oc-rate-item">
              <span className="oc-rate-label">
                <span className="oc-dot" style={{ background: DOWN_COLOR }} /> 下行
              </span>
              <span className="oc-rate-value">
                {down.value}
                <span className="oc-rate-unit">{down.unit}</span>
              </span>
            </div>
            <div className="oc-rate-item">
              <span className="oc-rate-label">
                <span className="oc-dot" style={{ background: UP_COLOR }} /> 上行
              </span>
              <span className="oc-rate-value">
                {up.value}
                <span className="oc-rate-unit">{up.unit}</span>
              </span>
            </div>
          </div>

          <div style={{ position: 'relative', height: 60 }}>
            <div style={{ position: 'absolute', inset: 0 }}>
              <SparkLine data={history.map((p) => p.download)} color={DOWN_COLOR} max={axisMax} />
            </div>
            <div style={{ position: 'absolute', inset: 0 }}>
              <SparkLine data={history.map((p) => p.upload)} color={UP_COLOR} max={axisMax} />
            </div>
          </div>

          <div className="oc-usage-sub" style={{ marginTop: 6 }}>
            累计 接收 {formatBytes(metrics.network.totalRecv)} / 发送 {formatBytes(metrics.network.totalSent)}
          </div>
        </div>

        <div className="oc-panel">
          <div className="oc-panel-title">IP 地址</div>
          <dl className="oc-kv">
            <Row label="主机名">{ip?.hostname ?? hardware.os.hostname}</Row>
            <Row label="局域网 IP">
              <span className="oc-mono">{ip?.local ?? '-'}</span>
            </Row>
            <Row label="默认网关">
              <span className="oc-mono">{network?.topology.gateways[0] ?? '-'}</span>
            </Row>
            <Row label="DNS">
              <span className="oc-mono">{network?.topology.dnsServers.join(' / ') || '-'}</span>
            </Row>
            <Row label="公网 IP">
              {publicIp ? (
                publicIp.reachable ? (
                  <span className="oc-mono">{publicIp.ip}</span>
                ) : (
                  <span style={{ opacity: 0.6 }}>未获取到（离线或全部源超时）</span>
                )
              ) : (
                <span style={{ opacity: 0.6 }}>未查询</span>
              )}
            </Row>
          </dl>

          <Button
            size="small"
            appearance="secondary"
            disabled={querying}
            onClick={() => void queryPublicIp()}
            style={{ marginTop: 10 }}
          >
            {querying ? '查询中…' : publicIp?.queried === false || publicIp === null ? '查询公网 IP' : '重新查询'}
          </Button>

          {publicIp?.reachable && publicIp.source && (
            <div className="oc-usage-sub" style={{ marginTop: 6 }}>
              数据来源：{publicIp.source}
            </div>
          )}
        </div>
      </div>

      {/* ── 硬件明细 ── */}
      <div className="oc-grid oc-grid-2" style={{ marginBottom: 12 }}>
        <div className="oc-panel">
          <div className="oc-panel-title">处理器与主板</div>
          <dl className="oc-kv">
            <Row label="型号">{hardware.cpu.name}</Row>
            <Row label="核心 / 线程">
              {hardware.cpu.cores ?? '-'}C / {hardware.cpu.threads ?? '-'}T
            </Row>
            <Row label="基准频率">{formatFrequency(hardware.cpu.freqMax)}</Row>
            <Row label="当前频率">{formatFrequency(metrics.cpu.freqCurrent)}</Row>
            <Row label="架构">{hardware.cpu.arch}</Row>
            <Row label="主板">
              {hardware.boards[0]
                ? `${hardware.boards[0].manufacturer ?? ''} ${hardware.boards[0].product ?? ''}`.trim() || '-'
                : '-'}
            </Row>
            <Row label="启动时间">{formatDateTime(hardware.bootTime)}</Row>
          </dl>
        </div>

        <div className="oc-panel">
          <div className="oc-panel-title">显示适配器</div>
          {hardware.gpus.length === 0 ? (
            <div className="oc-usage-sub">未检测到</div>
          ) : (
            <dl className="oc-kv">
              {hardware.gpus.map((gpu, index) => (
                <Row key={`${gpu.name}-${index}`} label={index === 0 ? '设备' : ''}>
                  <div>{gpu.name || '未知'}</div>
                  <div className="oc-usage-sub">
                    {gpu.memoryGB ? `显存 ${gpu.memoryGB} GB` : ''}
                    {gpu.driverVersion ? ` · 驱动 ${gpu.driverVersion}` : ''}
                  </div>
                </Row>
              ))}
            </dl>
          )}
        </div>
      </div>

      {/* ── 磁盘分区 ── */}
      {hardware.disks.length > 0 && (
        <div className="oc-panel" style={{ marginBottom: 12 }}>
          <div className="oc-panel-title">磁盘分区</div>
          <div className="oc-grid oc-grid-2">
            {hardware.disks.map((disk) => (
              <UsageBar
                key={disk.mountpoint}
                label={disk.mountpoint}
                percent={disk.percent}
                value={`${disk.percent.toFixed(1)}%`}
                sub={`${formatBytes(disk.used)} / ${formatBytes(disk.total)} · ${disk.fstype}`}
              />
            ))}
          </div>
        </div>
      )}

      {/* ── 网卡 ── */}
      {network && (
        <div className="oc-panel">
          <div className="oc-panel-title">网络接口</div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ textAlign: 'left', opacity: 0.6 }}>
                  <th style={{ padding: '4px 8px 6px 0', fontWeight: 500 }}>名称</th>
                  <th style={{ padding: '4px 8px 6px', fontWeight: 500 }}>IPv4</th>
                  <th style={{ padding: '4px 8px 6px', fontWeight: 500 }}>MAC</th>
                  <th style={{ padding: '4px 8px 6px', fontWeight: 500 }}>速率</th>
                  <th style={{ padding: '4px 8px 6px', fontWeight: 500 }}>接收 / 发送</th>
                  <th style={{ padding: '4px 8px 6px', fontWeight: 500 }}>状态</th>
                </tr>
              </thead>
              <tbody>
                {network.interfaces.map((iface) => (
                  <tr key={iface.name} style={{ borderTop: '1px solid var(--oc-border)' }}>
                    <td style={{ padding: '5px 8px 5px 0' }}>{iface.name}</td>
                    <td style={{ padding: '5px 8px' }} className="oc-mono">
                      {iface.ipv4.map((a) => a.address).join(', ') || '-'}
                    </td>
                    <td style={{ padding: '5px 8px' }} className="oc-mono">
                      {iface.mac || '-'}
                    </td>
                    <td style={{ padding: '5px 8px' }} className="oc-mono">
                      {iface.speedMbps ? `${iface.speedMbps} Mbps` : '-'}
                    </td>
                    <td style={{ padding: '5px 8px' }} className="oc-mono">
                      {formatBytes(iface.bytesRecv)} / {formatBytes(iface.bytesSent)}
                    </td>
                    <td style={{ padding: '5px 8px' }}>
                      <span className={`oc-chip ${iface.up ? 'external' : ''}`}>
                        {iface.up ? '已连接' : '断开'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
