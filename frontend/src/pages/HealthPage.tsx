import { useEffect, useState } from 'react'
import { Button, Spinner } from '@fluentui/react-components'
import { api } from '../api'
import UnifiedList from '../components/UnifiedList'

export default function HealthPage() {
  const [result, setResult] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [repairs, setRepairs] = useState<any[]>([])
  const [running, setRunning] = useState('')
  const [msg, setMsg] = useState('')
  const [exporting, setExporting] = useState(false)
  const [reportPath, setReportPath] = useState('')

  // 完整报告：体检 + 课堂检测 + 维护清单汇总成一段可粘贴的文字
  const [fullReport, setFullReport] = useState('')
  const [reportBusy, setReportBusy] = useState(false)
  const [reportNote, setReportNote] = useState('')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    void api
      .list_repairs()
      .then(setRepairs)
      .catch(() => setRepairs([]))
  }, [])

  const run = async () => {
    setBusy(true)
    try {
      setResult(await api.run_health_checks())
    } catch {
      setResult(null)
    } finally {
      setBusy(false)
    }
  }

  const repair = async (key: string) => {
    setRunning(key)
    setMsg('')
    try {
      const outcome = await api.run_repair(key)
      setMsg(`${outcome?.message ?? ''}${outcome?.restart ? '（建议重启电脑后复查）' : ''}`)
    } catch {
      setMsg('执行失败，请稍后重试')
    } finally {
      setRunning('')
    }
  }

  const makeFullReport = async () => {
    setReportBusy(true)
    setCopied(false)
    setReportNote('')
    try {
      const outcome = await api.diagnostic_report()
      setFullReport(outcome?.report ?? '')
      setReportNote(outcome?.summary?.headline ?? '')
    } catch {
      setFullReport('')
      setReportNote('生成失败，请稍后重试')
    } finally {
      setReportBusy(false)
    }
  }

  const copyReport = async () => {
    try {
      await navigator.clipboard.writeText(fullReport)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      setReportNote('复制失败：请手动选中上面这段文字复制')
    }
  }

  const exportReport = async () => {
    setExporting(true)
    setReportPath('')
    try {
      const outcome = await api.export_report()
      setReportPath(outcome?.ok ? outcome.path : (outcome?.content ?? '导出失败'))
    } catch {
      setReportPath('导出失败，请稍后重试')
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="oc-page">
      <div className="oc-page-header">
        <div className="oc-page-title">一键体检</div>
        <div className="oc-page-desc">
          上课前点一下，看看这台一体机有没有问题；发现问题可在下方直接修复
        </div>
      </div>

      <div className="oc-panel">
        <div className="oc-hero">
          <Button
            appearance="primary"
            size="large"
            onClick={run}
            disabled={busy}
            style={{ fontSize: 17, padding: '12px 40px' }}
          >
            {busy ? '正在体检…' : '开始体检'}
          </Button>
          {result && !busy && (
            <div className="oc-hero-result">
              {result.healthy
                ? '✅ 一切正常，可以上课'
                : `⚠️ 发现 ${result.total - result.okCount} 项需要注意`}
            </div>
          )}
          {busy && (
            <div style={{ marginTop: 14 }}>
              <Spinner size="tiny" label="检查中，请稍候…" />
            </div>
          )}
        </div>

        {result && !busy && (
          <UnifiedList
            items={result.unified}
            emptyText="还没有检测结果，点「开始体检」跑一遍。"
          />
        )}

        <div className="oc-actions" style={{ marginTop: 14, justifyContent: 'center' }}>
          <Button
            size="small"
            appearance="secondary"
            onClick={exportReport}
            disabled={exporting}
          >
            {exporting ? '正在生成…' : '导出报修报告'}
          </Button>
          <Button
            size="small"
            appearance="secondary"
            onClick={() => void makeFullReport()}
            disabled={reportBusy}
          >
            {reportBusy ? '正在汇总…' : '生成完整报告'}
          </Button>
        </div>
        {reportPath && (
          <div className="oc-list-sub" style={{ marginTop: 8, textAlign: 'center' }}>
            已保存到：{reportPath}
          </div>
        )}

        {fullReport && (
          <div style={{ marginTop: 12 }}>
            <div
              className="oc-actions"
              style={{ justifyContent: 'space-between', alignItems: 'center' }}
            >
              <span className="oc-usage-sub">
                {reportNote || '把下面这段直接发给维修人员即可'}
              </span>
              <Button size="small" appearance="primary" onClick={() => void copyReport()}>
                {copied ? '已复制' : '复制全文'}
              </Button>
            </div>
            <pre className="oc-report-preview">{fullReport}</pre>
          </div>
        )}
      </div>

      {repairs.length > 0 && (
        <div className="oc-panel" style={{ marginTop: 12 }}>
          <div className="oc-panel-title">一键修复</div>
          <div className="oc-list">
            {repairs.map((item) => (
              <div className="oc-list-row" key={item.key}>
                <div className="oc-list-main">
                  <div className="oc-list-title">{item.name}</div>
                  <div className="oc-list-sub">
                    {item.desc}
                    {item.admin ? '（需要管理员确认）' : ''}
                  </div>
                </div>
                <Button
                  size="small"
                  onClick={() => repair(item.key)}
                  disabled={running === item.key}
                >
                  {running === item.key ? '执行中…' : '执行'}
                </Button>
              </div>
            ))}
          </div>
          {msg && (
            <div className="oc-list-sub" style={{ marginTop: 10 }}>
              {msg}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
