import { useEffect, useState } from 'react'
import { Button, Spinner } from '@fluentui/react-components'
import { api } from '../api'
import KbPanel from '../components/KbPanel'
import UnifiedList from '../components/UnifiedList'


export default function ClassroomPage() {
  const [report, setReport] = useState<any>(null)
  const [busy, setBusy] = useState(true)
  const [msg, setMsg] = useState('')

  // ── 课前准备：一次点击回答"这台机器现在能不能上课" ──
  const [pre, setPre] = useState<any>(null)
  const [preBusy, setPreBusy] = useState(false)
  const [preMsg, setPreMsg] = useState('')

  const runPreflight = async () => {
    setPreBusy(true)
    setPreMsg('')
    try {
      setPre(await api.preflight())
    } catch {
      setPreMsg('课前准备执行失败，请稍后重试')
    } finally {
      setPreBusy(false)
    }
  }

  const quickRepair = async (key: string) => {
    setPreMsg('')
    try {
      const outcome = await api.run_repair(key)
      setPreMsg(outcome?.message ?? '')
      // 修完立刻复查：结论要跟着变，否则老师不知道到底好了没有
      await runPreflight()
    } catch (error) {
      setPreMsg(`修复失败：${error}`)
    }
  }

  const load = async () => {
    setBusy(true)
    try {
      setReport(await api.classroom_report())
    } catch {
      setReport(null)
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const act = async (fn: () => Promise<any>) => {
    setMsg('')
    try {
      const result = await fn()
      setMsg(result?.message ?? result?.detail ?? '')
    } catch (error) {
      setMsg(`操作失败：${error}`)
    }
  }

  const rescanApps = async () => {
    setMsg('正在重新扫描教学软件…')
    try {
      const result = await api.refresh_teaching_apps()
      setMsg(result?.detail ?? '')
      await load()
    } catch (error) {
      setMsg(`扫描失败：${error}`)
    }
  }

  const items: any[] = report?.items ?? []
  const apps: any[] = report?.apps ?? []

  return (
    <div className="oc-page">
      <div className="oc-page-header">
        <div className="oc-page-title">课堂</div>
        <div className="oc-page-desc">
          一体机专属检测：投影与触摸是否正常、教学软件装了什么、有没有还原保护
        </div>
      </div>

      <div className="oc-panel" style={{ marginBottom: 12 }}>
        <div className="oc-panel-title">课前准备</div>
        <div className="oc-hint" style={{ marginBottom: 10 }}>
          上课前点一下：把体检、投影触摸、还原保护一起过一遍，直接告诉你能不能上课。
        </div>
        <div className="oc-actions">
          <Button
            appearance="primary"
            size="large"
            onClick={() => void runPreflight()}
            disabled={preBusy}
          >
            {preBusy ? '正在检查…' : '开始课前准备'}
          </Button>
          {preMsg && <span className="oc-list-sub">{preMsg}</span>}
        </div>

        {pre && (
          <div style={{ marginTop: 12 }}>
            <div className={`oc-verdict oc-verdict-${pre.verdict}`}>
              <div className="oc-verdict-label">{pre.verdictLabel}</div>
              <div className="oc-verdict-text">{pre.headline}</div>
              {pre.checkedAt && <div className="oc-verdict-time">检查时间：{pre.checkedAt}</div>}
            </div>

            {pre.blocking?.length > 0 && (
              <div className="oc-list" style={{ marginTop: 10 }}>
                {pre.blocking.map((item: any) => (
                  <div className="oc-list-row" key={item.id}>
                    <div className="oc-list-main">
                      <div className="oc-list-title">
                        {item.title}：{item.summary}
                      </div>
                      {item.advice && <div className="oc-list-warn">建议：{item.advice}</div>}
                    </div>
                    {item.repairable && (
                      <Button
                        size="small"
                        appearance="primary"
                        onClick={() => void quickRepair(item.repairKey)}
                      >
                        一键修复
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            )}

            {pre.attention?.length > 0 && (
              <>
                <div className="oc-usage-sub" style={{ marginTop: 10 }}>
                  可以上课，但这些建议看一眼：
                </div>
                <div className="oc-list">
                  {pre.attention.map((item: any) => (
                    <div className="oc-list-row" key={item.id}>
                      <div className="oc-list-main">
                        <div className="oc-list-title">
                          {item.title}：{item.summary}
                        </div>
                        {item.advice && <div className="oc-list-sub">建议：{item.advice}</div>}
                      </div>
                      {item.repairable && (
                        <Button
                          size="small"
                          appearance="secondary"
                          onClick={() => void quickRepair(item.repairKey)}
                        >
                          一键修复
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}

            {pre.manual?.length > 0 && (
              <div className="oc-usage-sub" style={{ marginTop: 8 }}>
                需要人工处理：
                {pre.manual.map((item: any) => item.title).join('、')}（软件无法代劳）
              </div>
            )}

            {pre.errors?.length > 0 && (
              <div className="oc-list-warn" style={{ marginTop: 8 }}>
                有项目没能完成检测：{pre.errors.join('；')}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="oc-toolbar" style={{ marginBottom: 12 }}>
        <div className="oc-actions">
          <Button appearance="primary" onClick={() => void load()} disabled={busy}>
            {busy ? '检测中…' : '重新检测'}
          </Button>
          <Button appearance="secondary" onClick={() => void act(api.open_display_switch)}>
            投影模式（Win+P）
          </Button>
          <Button appearance="secondary" onClick={() => void act(api.open_touch_calibration)}>
            触摸校准
          </Button>
          <Button appearance="secondary" onClick={() => void rescanApps}>
            重新扫描教学软件
          </Button>
        </div>
        {msg && <span className="oc-list-sub">{msg}</span>}
      </div>

      {busy && items.length === 0 ? (
        <div className="oc-panel">
          <Spinner size="tiny" /> 正在检测投影、触摸、无线投屏与教学软件…
        </div>
      ) : (
        <UnifiedList
          items={report?.unified}
          emptyText="还没有检测结果，点「重新检测」跑一遍。"
        />
      )}

      <div className="oc-panel" style={{ marginTop: 14 }}>
        <div className="oc-panel-title">
          教学软件清单
          {report?.apps?.length > 0 && (
            <span className="oc-list-sub" style={{ marginLeft: 8, fontWeight: 400 }}>
              共 {apps.length} 个
            </span>
          )}
        </div>
        {apps.length === 0 ? (
          <div className="oc-hint">
            未识别到常见教学软件。如果这台机器确实装了（比如希沃白板），点上面的
            「重新扫描教学软件」再试一次；仍识别不到说明软件不是标准安装方式装的。
          </div>
        ) : (
          <div className="oc-list">
            {apps.map((app) => (
              <div className="oc-list-row" key={`${app.kind}-${app.name}`}>
                <div className="oc-list-main">
                  <div className="oc-list-title">
                    {app.name}
                    <span className="oc-level safe" style={{ marginLeft: 8 }}>
                      {app.kind}
                    </span>
                  </div>
                  <div className="oc-list-sub">版本：{app.version || '未知'}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="oc-panel" style={{ marginTop: 14 }}>
        <div className="oc-panel-title">课堂场景速查</div>
        <div className="oc-hint">
          <b>投屏没画面</b>：按 Win+P 切到「复制」；仍无画面就检查 HDMI/VGA 线与投影仪输入源
          <br />
          <b>触摸点不准</b>：点「触摸校准」，按十字光标依次点完即可（校准数据按显示器保存）
          <br />
          <b>改了设置重启就复原</b>：说明这台机器开了还原保护（冰点/影子系统），先解除保护再改
          <br />
          <b>白板软件闪退</b>：先在「教学软件清单」确认版本，再对照下面的已知问题库
        </div>
      </div>

      <div style={{ marginTop: 14 }}>
        <KbPanel />
      </div>
    </div>
  )
}
