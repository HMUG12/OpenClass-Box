import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { Button } from '@fluentui/react-components'
import { api } from '../api'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
  stack: string
}

/**
 * 页面级错误边界 —— 任何页面渲染异常都只显示可恢复的提示，不再整屏黑掉。
 *
 * 之前出现过「点某些界面后一片黑」，本质是某个组件抛错把整棵树带崩。
 * 这里兜住异常、把堆栈写进运行日志（诊断页可查），并给用户一个「重试」。
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, stack: '' }

  static getDerivedStateFromError(error: Error): State {
    return { error, stack: '' }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    const stack = info?.componentStack ?? ''
    this.setState({ stack })
    void (async () => {
      try {
        await api.report_frontend_error(
          `${error?.name ?? 'Error'}: ${error?.message ?? error}\n${stack}`.slice(0, 4000)
        )
      } catch {
        /* 上报失败不影响界面恢复 */
      }
    })()
  }

  render() {
    const { error, stack } = this.state
    if (!error) {
      return this.props.children
    }

    return (
      <div className="oc-page">
        <div className="oc-panel">
          <div className="oc-panel-title">这个页面出错了</div>
          <div className="oc-list-sub">
            程序没有崩，其他功能仍可正常使用。已把错误记录到「维护 → 诊断 → 运行日志」，
            反馈问题时可一并提供。
          </div>
          <pre
            style={{
              marginTop: 10,
              maxHeight: 220,
              overflow: 'auto',
              padding: 12,
              borderRadius: 'var(--oc-radius)',
              background: 'var(--oc-surface)',
              border: '1px solid var(--oc-border)',
              fontSize: 12,
              lineHeight: 1.6,
              whiteSpace: 'pre-wrap',
              userSelect: 'text',
            }}
          >
            {error.message}
            {stack ? `\n${stack}` : ''}
          </pre>
          <div className="oc-actions" style={{ marginTop: 10 }}>
            <Button appearance="primary" onClick={() => this.setState({ error: null, stack: '' })}>
              重试
            </Button>
            <Button
              appearance="secondary"
              onClick={() => {
                this.setState({ error: null, stack: '' })
                window.location.reload()
              }}
            >
              重新加载界面
            </Button>
          </div>
        </div>
      </div>
    )
  }
}
