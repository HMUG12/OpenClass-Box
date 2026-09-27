import type { ToolSpec } from '../types'
import { api } from '../api'

interface Props {
  tool: ToolSpec
  onLaunch: (tool: ToolSpec) => void
}

const KIND_LABEL: Record<string, { text: string; cls: string }> = {
  builtin: { text: '内置', cls: '' },
  plugin: { text: '插件', cls: 'plugin' },
  external: { text: '外部', cls: 'external' },
  web: { text: '网页', cls: 'plugin' },
}

/** 兜底：清单里出现未知 kind 时也不能让整页崩掉（这里曾经崩过整屏） */
function kindOf(kind: string | undefined): { text: string; cls: string } {
  if (kind && KIND_LABEL[kind]) return KIND_LABEL[kind]
  return { text: kind ? String(kind) : '工具', cls: '' }
}

export default function ToolCard({ tool, onLaunch }: Props) {
  const kind = kindOf(tool.kind)
  const disabled = !tool.available
  const canDownload = disabled && !!tool.download

  const handleClick = () => {
    if (canDownload) {
      void api.open_url(tool.download as string)
      return
    }
    if (disabled) return
    onLaunch(tool)
  }

  return (
    <div
      className={`oc-toolcard${disabled && !canDownload ? ' disabled' : ''}`}
      onClick={handleClick}
      title={canDownload ? `点击前往下载 ${tool.name}` : (disabled ? tool.reason : `启动 ${tool.name}`)}
    >
      <div className="oc-toolcard-top">
        <div className="oc-toolcard-icon">{tool.icon}</div>
        <div className="oc-toolcard-head">
          <div className="oc-toolcard-name">{tool.name}</div>
          <div className="oc-toolcard-meta">v{tool.version} · {tool.author}</div>
        </div>
      </div>

      <div className="oc-toolcard-desc">{tool.description}</div>

      <div className="oc-toolcard-foot">
        <span className={`oc-chip ${kind.cls}`}>{kind.text}</span>
        {tool.admin && <span className="oc-chip admin">需管理员</span>}
        {canDownload && <span className="oc-chip admin">去下载</span>}
        {disabled && !canDownload && <span className="oc-chip admin">不可用</span>}
        {tool.tags.slice(0, 2).map((t) => (
          <span key={t} className="oc-chip">
            {t}
          </span>
        ))}
      </div>
    </div>
  )
}
