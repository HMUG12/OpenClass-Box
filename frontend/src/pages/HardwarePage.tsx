import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Button, Spinner } from '@fluentui/react-components'
import { api } from '../api'
import RingChart from '../components/RingChart'

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="oc-info-row">
      <span>{label}</span>
      <span>{value === null || value === undefined || value === '' ? '不可用' : value}</span>
    </div>
  )
}

function formatSize(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`
}

function formatUptime(seconds: number): string {
  if (!seconds || seconds <= 0) return '—'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days} 天 ${hours} 小时 ${minutes} 分`
  if (hours > 0) return `${hours} 小时 ${minutes} 分`
  return `${minutes} 分钟`
}

function ringColor(percent: number, base: string): string {
  if (percent >= 88) return '#c50f1f'
  if (percent >= 70) return '#9a6700'
  return base
}

export default function HardwarePage() {
  const [detail, setDetail] = useState<any>(null)
  const [hardware, setHardware] = useState<any>(null)
  const [metrics, setMetrics] = useState<any>(null)
  const [busy, setBusy] = useState(true)

  const load = async () => {
    setBusy(true)
    try {
      // 先取「秒级快照」，保证界面立刻有内容
      const [d, h, m] = await Promise.all([
        api.get_hardware_detail(true),
        api.get_hardware(),
        api.get_metrics(),
      ])
      setDetail(d)
      setHardware(h)
      setMetrics(m)
    } catch {
      setDetail(null)
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  // 实时使用率（2 秒）
  useEffect(() => {
    const timer = window.setInterval(async () => {
      try {
        setMetrics(await api.get_metrics())
      } catch {
        /* 忽略单次失败 */
      }
    }, 2000)
    return () => window.clearInterval(timer)
  }, [])

  // 后台完整采集（WMI）就绪后自动替换展示
  useEffect(() => {
    if (detail?.fullReady) return
    let tries = 0
    const timer = window.setInterval(async () => {
      tries += 1
      if (tries > 20) {
        window.clearInterval(timer)
        return
      }
      try {
        const full = await api.get_hardware_detail(false)
        setDetail(full)
        if (full?.fullReady) window.clearInterval(timer)
      } catch {
        /* 忽略 */
      }
    }, 1500)
    return () => window.clearInterval(timer)
  }, [detail?.fullReady])

  const cpu = hardware?.cpu
  const memory = hardware?.memory
  const cpuTemp = detail?.cpuTemp
  const tempSource = detail?.tempSource
  const fullReady = Boolean(detail?.fullReady)

  return (
    <div className="oc-page">
      <div className="oc-page-header">
        <div className="oc-page-title">硬件信息</div>
        <div className="oc-page-desc">
          本机真实硬件状态 · 数据全部实测（读不到的项显示「不可用」，不估算）
          <Button
            size="small"
            appearance="secondary"
            style={{ marginLeft: 10 }}
            onClick={load}
            disabled={busy}
          >
            重新采集
          </Button>
        </div>
      </div>

      {busy && !detail ? (
        <div className="oc-empty">
          <Spinner size="small" label="正在读取硬件信息…" />
        </div>
      ) : (
        <>
          {/* ── CPU / 内存（实时圆环） ── */}
          <div className="oc-grid oc-grid-2" style={{ marginBottom: 12 }}>
            <div className="oc-panel">
              <div className="oc-panel-title">处理器（CPU）</div>
              <div className="oc-ring-row">
                <RingChart
                  percent={metrics?.cpu?.percent ?? 0}
                  color={ringColor(metrics?.cpu?.percent ?? 0, '#0f6cbd')}
                  label="使用率"
                  sub={metrics?.cpu?.freqCurrent ? `${Math.round(metrics.cpu.freqCurrent)} MHz` : ''}
                />
                <div className="oc-ring-meta">
                  <Row label="型号" value={cpu?.name} />
                  <Row
                    label="核心 / 线程"
                    value={cpu ? `${cpu.cores ?? '—'} / ${cpu.threads ?? '—'}` : null}
                  />
                  <Row
                    label="温度"
                    value={
                      cpuTemp === null || cpuTemp === undefined
                        ? '不可用（需硬件监控驱动）'
                        : `${cpuTemp} ℃${tempSource ? ` · ${tempSource}` : ''}`
                    }
                  />
                </div>
              </div>
            </div>

            <div className="oc-panel">
              <div className="oc-panel-title">内存（RAM）</div>
              <div className="oc-ring-row">
                <RingChart
                  percent={metrics?.memory?.percent ?? 0}
                  color={ringColor(metrics?.memory?.percent ?? 0, '#8764b8')}
                  label="已用"
                  sub={memory ? formatSize(memory.total) : ''}
                />
                <div className="oc-ring-meta">
                  <Row
                    label="已使用"
                    value={
                      metrics?.memory
                        ? `${formatSize(metrics.memory.used)}（${metrics.memory.percent}%）`
                        : null
                    }
                  />
                  <Row
                    label="可用"
                    value={metrics?.memory ? formatSize(metrics.memory.available) : null}
                  />
                  {(detail?.memModules ?? []).slice(0, 2).map((mod: any, index: number) => (
                    <Row
                      key={index}
                      label={`插槽 ${mod.slot || index + 1}`}
                      value={`${mod.capacityGB ?? '—'} GB · ${
                        mod.speed ? mod.speed + ' MHz' : '频率未知'
                      }${mod.manufacturer ? ' · ' + mod.manufacturer : ''}`}
                    />
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* ── 显卡 ── */}
          <div className="oc-panel" style={{ marginBottom: 12 }}>
            <div className="oc-panel-title">显卡（GPU）</div>
            <div style={{ marginTop: 8 }}>
              {(detail?.gpus ?? []).length === 0 ? (
                <div className="oc-hint">
                  {fullReady ? '未读取到显卡信息' : '正在补齐显卡信息…'}
                </div>
              ) : (
                (detail.gpus ?? []).map((gpu: any, index: number) => (
                  <div
                    className="oc-toolcard"
                    style={{ cursor: 'default', marginBottom: 8 }}
                    key={index}
                  >
                    <Row label="型号" value={gpu.name} />
                    <Row
                      label="显存"
                      value={
                        gpu.memoryGB
                          ? `${gpu.memoryGB} GB`
                          : gpu.shared
                            ? '共享系统内存（核显，无独立显存）'
                            : '不可用'
                      }
                    />
                    <Row label="驱动版本" value={gpu.driverVersion} />
                    <Row label="驱动日期" value={gpu.driverDate} />
                    <Row label="分辨率" value={gpu.resolution} />
                  </div>
                ))
              )}
            </div>
          </div>

          {/* ── 硬盘 ── */}
          <div className="oc-panel" style={{ marginBottom: 12 }}>
            <div className="oc-panel-title">硬盘（存储）</div>
            {!fullReady && (detail?.physicalDisks ?? []).length === 0 && (
              <div className="oc-hint">正在读取物理磁盘信息…</div>
            )}
            <div style={{ marginTop: 8 }}>
              {(detail?.physicalDisks ?? []).length > 0 && (
                <div className="oc-list" style={{ marginBottom: 10 }}>
                  {(detail.physicalDisks ?? []).map((disk: any, index: number) => (
                    <div className="oc-list-row" key={index}>
                      <div className="oc-list-main">
                        <div className="oc-list-title">{disk.model || '未知型号'}</div>
                        <div className="oc-list-sub">
                          {disk.sizeGB ? `${disk.sizeGB} GB` : '容量未知'}
                          {disk.media && disk.media !== '' ? ` · ${disk.media}` : ''}
                          {disk.bus ? ` · ${disk.bus}` : ''}
                          {disk.health ? ` · 健康：${disk.health}` : ''}
                          {` · 温度：${
                            disk.temperature === null || disk.temperature === undefined
                              ? '不可用'
                              : disk.temperature + ' ℃'
                          }`}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              <div className="oc-list">
                {(hardware?.disks ?? []).map((disk: any) => (
                  <div className="oc-list-row" key={disk.mountpoint}>
                    <div className="oc-list-main">
                      <div className="oc-list-title">{disk.mountpoint}</div>
                      <div className="oc-list-sub">
                        {disk.fstype} · {formatSize(disk.used)} / {formatSize(disk.total)}（
                        {disk.percent}%）
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* ── 主板 / BIOS / 系统 ── */}
          <div className="oc-panel">
            <div className="oc-panel-title">主板与系统</div>
            <div className="oc-info-grid" style={{ marginTop: 8 }}>
              <div className="oc-toolcard" style={{ cursor: 'default' }}>
                {(detail?.boards ?? []).map((board: any, index: number) => (
                  <Row
                    key={`b${index}`}
                    label={index === 0 ? '主板' : `主板 ${index + 1}`}
                    value={`${board.manufacturer ?? ''} ${board.product ?? ''}`.trim() || null}
                  />
                ))}
                {(detail?.bios ?? []).map((bios: any, index: number) => (
                  <Row
                    key={`s${index}`}
                    label="BIOS"
                    value={
                      `${bios.vendor ?? ''} ${bios.version ?? ''}${
                        bios.date ? ' · ' + bios.date : ''
                      }`.trim() || null
                    }
                  />
                ))}
                <Row
                  label="操作系统"
                  value={
                    hardware?.os
                      ? `${hardware.os.system} ${hardware.os.release}（${hardware.os.build}）`
                      : null
                  }
                />
                <Row label="主机名" value={hardware?.os?.hostname} />
                <Row
                  label="已运行"
                  value={
                    hardware?.bootTime
                      ? formatUptime(Date.now() / 1000 - hardware.bootTime)
                      : null
                  }
                />
              </div>
            </div>
          </div>

          <div className="oc-hint" style={{ marginTop: 14 }}>
            说明：CPU / 主板温度依赖硬件监控驱动，Windows 通常只暴露 ACPI 热区，部分机型读不到属正常现象；
            显存取自驱动在注册表登记的真实容量（避免系统接口对 4GB 以上显存的截断）。
          </div>
        </>
      )}
    </div>
  )
}
