import { useEffect, useState } from 'react'
import { Button, Spinner } from '@fluentui/react-components'
import { api } from '../api'
import UnifiedList from './UnifiedList'

/**
 * 设备衰退监测与维护清单。
 *
 * 立场：**只依据可解释的数值给结论，不做寿命预测**。
 * 学校几十台机器的样本量不足以支撑模型，宣称"还剩 45 天"一旦不准，
 * 信任是一次性的。所以这里给的是"该换谁的硬盘、该给谁清灰"这样的清单。
 */
export default function HealthWatchPanel() {
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [expanded, setExpanded] = useState(false)

  const load = async () => {
    try {
      setData(await api.health_watch_status())
    } catch {
      setData(null)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const scan = async () => {
    setBusy(true)
    setMsg('正在读取磁盘可靠性计数与系统事件…')
    try {
      const result = await api.health_watch_scan()
      setData(result)
      setMsg(
        result?.actions?.length
          ? `扫描完成：${result.actions.length} 项需要关注`
          : '扫描完成：没有发现需要处理的项'
      )
    } catch (error) {
      setMsg(`扫描失败：${error}`)
    } finally {
      setBusy(false)
    }
  }

  // 清单正文已改用 UnifiedList（与体检 / 课堂检测同一形状）。
  // data.actions 仍保留在返回值里，供 A 端远程批量检查与报告使用。
  return (
    <div className="oc-panel" style={{ marginBottom: 12 }}>
      <div className="oc-panel-title">
        维护清单（衰退监测）
        <Button
          size="small"
          appearance="primary"
          style={{ marginLeft: 10 }}
          disabled={busy}
          onClick={() => void scan()}
        >
          {busy ? '扫描中…' : '重新扫描'}
        </Button>
        {data?.scannedAt && (
          <span className="oc-list-sub" style={{ marginLeft: 10, fontWeight: 400 }}>
            上次扫描：{data.scannedAt} · 总体 {data.levelLabel || '正常'}
          </span>
        )}
      </div>

      <div className="oc-usage-sub" style={{ marginBottom: 10 }}>
        依据是<b>可解释的数值</b>（读写错误计数、温度、通电时间、异常关机次数、磁盘空间），
        不做寿命预测 —— 每条结论都写明依据，方便你判断该换件还是该清灰。
      </div>

      {data?.smartHint && (
        <div className="oc-list-warn" style={{ marginBottom: 8 }}>
          {data.smartHint}
        </div>
      )}

      {busy && (
        <div className="oc-usage-sub" style={{ marginBottom: 8 }}>
          <Spinner size="tiny" /> {msg}
        </div>
      )}
      {!busy && msg && <div className="oc-usage-sub">{msg}</div>}

      <UnifiedList
        items={data?.unified}
        emptyText={
          data?.scannedAt
            ? '没有需要处理的项：磁盘读写无错误、温度正常、近期没有异常关机。'
            : '还没有扫描过，点「重新扫描」生成维护清单。'
        }
      />

      {(data?.disks ?? []).length > 0 && (
        <div style={{ marginTop: 10 }}>
          <button className="oc-linklike" onClick={() => setExpanded(!expanded)}>
            {expanded ? '收起硬盘明细' : `查看硬盘明细（${data.disks.length} 块）`}
          </button>
          {expanded && (
            <div className="oc-list" style={{ marginTop: 8 }}>
              {data.disks.map((disk: any, index: number) => (
                <div className="oc-list-row" key={index}>
                  <div className="oc-list-main">
                    <div className="oc-list-title">
                      {disk.name} · {disk.typeLabel}
                      {disk.sizeGB ? ` · ${disk.sizeGB} GB` : ''}
                    </div>
                    <div className="oc-list-sub">
                      温度 {disk.temperature ?? '不可用'}
                      {disk.temperature ? '℃' : ''} · 通电{' '}
                      {disk.powerOnHours ? `${disk.powerOnHours} 小时` : '不可用'} · 读写错误{' '}
                      {disk.readErrors ?? '—'}/{disk.writeErrors ?? '—'}
                      {disk.wear !== null && disk.wear !== undefined ? ` · 磨损 ${disk.wear}%` : ''}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {data?.restore?.installed && (
        <div className="oc-usage-sub" style={{ marginTop: 8 }}>
          还原保护：{data.restore.detail}
          {data.restore.level !== 'ok' && (
            <b style={{ color: 'var(--oc-warning)' }}>
              {' '}
              —— 学生改动会真正写入系统盘，请确认保护状态
            </b>
          )}
        </div>
      )}

      {data?.crashes?.count > 0 && (
        <div className="oc-usage-sub" style={{ marginTop: 8 }}>
          近 {data.crashes.windowDays} 天异常关机 / 蓝屏 {data.crashes.count} 次
          （事件 41 / 6008 / 1001）
        </div>
      )}
    </div>
  )
}
