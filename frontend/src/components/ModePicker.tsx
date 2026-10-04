import { useState } from 'react'
import { Button, Dialog, DialogBody, DialogSurface, DialogTitle } from '@fluentui/react-components'
import { api } from '../api'

interface Option {
  id: string
  label: string
  summary: string
  detail: string
}

interface Props {
  options: Option[]
  onDone: () => void
}

/**
 * 首次启动的模式选择。
 *
 * 刻意做成**必须选、不能跳过**：三种模式的界面差别很大（自动化会藏掉四个
 * 页面），让用户在没意识到差别的情况下随机落到一个模式，后面会一头雾水。
 *
 * 顺序按"需要动手的程度"排：自动化（什么都不用做）→ 正常 → 专业。
 * 默认选中「正常」—— 它是能力最全、最不容易出错的那个；把最激进的选项
 * 作为默认，是这类产品常见的坑。
 */
export default function ModePicker({ options, onDone }: Props) {
  const [pick, setPick] = useState('normal')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const apply = async () => {
    setBusy(true)
    setErr('')
    try {
      const result = await api.set_mode(pick)
      if (result && result.ok === false) setErr(result.message ?? '切换失败')
      else onDone()
    } catch (e) {
      setErr(`切换失败：${e}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={true}>
      <DialogSurface style={{ maxWidth: 560 }}>
        <DialogBody>
          <DialogTitle>这台机器主要用来做什么？</DialogTitle>
          <div className="oc-usage-sub" style={{ margin: '6px 0 14px' }}>
            之后随时能在「设置」里改，选错也不要紧。
          </div>

          <div style={{ display: 'grid', gap: 10 }}>
            {options.map((one) => (
              <div
                key={one.id}
                onClick={() => setPick(one.id)}
                style={{
                  cursor: 'pointer',
                  padding: '12px 14px',
                  borderRadius: 10,
                  border: pick === one.id ? '2px solid var(--oc-accent)' : '1px solid var(--oc-border)',
                  background: pick === one.id ? 'var(--oc-accent-soft)' : 'transparent',
                }}
              >
                <div className="oc-list-title" style={{ fontSize: 15 }}>
                  {one.label}
                </div>
                <div className="oc-list-sub" style={{ marginTop: 2 }}>
                  {one.summary}
                </div>
                {pick === one.id && (
                  <div className="oc-list-warn" style={{ marginTop: 6 }}>
                    {one.detail}
                  </div>
                )}
              </div>
            ))}
          </div>

          {err && (
            <div className="oc-list-warn" style={{ marginTop: 10 }}>
              {err}
            </div>
          )}

          <div className="oc-actions" style={{ marginTop: 18, justifyContent: 'flex-end' }}>
            <Button appearance="primary" onClick={() => void apply()} disabled={busy}>
              {busy ? '正在应用…' : '就用这个模式'}
            </Button>
          </div>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}