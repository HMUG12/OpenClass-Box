import { Button } from '@fluentui/react-components'

/** 统一诊断项（与 backend/core/diag_result.py 的输出同形）。 */
export interface UnifiedItem {
  id: string
  title: string
  status: string
  statusLabel: string
  summary: string
  advice?: string
  repairable?: boolean
  repairKey?: string
  manual?: boolean
  source?: string
  sourceLabel?: string
}

/**
 * 状态 → 颜色。
 *
 * 五档的文案由后端统一（与维护清单逐字一致），这里只负责上色。
 * 分两色而不是五色是有意的：老师要分辨的是"要不要处理"，
 * 而 replace 与 warn 都是"要处理"，watch 与 info 都是"看一眼"。
 */
function tone(status: string): string {
  if (status === 'replace' || status === 'warn') return 'var(--oc-danger)'
  if (status === 'watch') return 'var(--oc-warning)'
  if (status === 'ok') return 'var(--oc-positive)'
  return 'inherit'   // info：用正文色，靠"信息"这个文案本身表达中性
}

interface Props {
  items: UnifiedItem[] | null | undefined
  /** 传了才会渲染「一键修复」按钮 —— 点不动的按钮比没有更让人恼火。 */
  onRepair?: (repairKey: string) => void
  emptyText?: string
  showSource?: boolean
  busy?: boolean
}

/**
 * 统一诊断结果渲染。
 *
 * 体检、课堂检测、维护清单三处以前各写一套：字段名不同、颜色规则不同、
 * "该不该给修复按钮"的判断也不同 —— 同一个问题在三个页面长得不一样。
 * 现在都走这份，形状与后端 diag_result 一致；旧字段仍然保留，
 * 页面可以逐个切换而不必一次性大改。
 */
export default function UnifiedList({ items, onRepair, emptyText, showSource, busy }: Props) {
  if (!items || items.length === 0) {
    return emptyText ? (
      <div className="oc-hint" style={{ marginTop: 8 }}>
        {emptyText}
      </div>
    ) : null
  }
  return (
    <div className="oc-list" style={{ marginTop: 8 }}>
      {items.map((item) => (
        <div className="oc-list-row" key={item.id}>
          <div className="oc-list-main">
            <div className="oc-list-title">
              <span
                style={{
                  display: 'inline-block',
                  minWidth: 58,
                  marginRight: 8,
                  color: tone(item.status),
                  fontWeight: 600,
                }}
              >
                {item.statusLabel}
              </span>
              {item.title}
              {item.manual && (
                <span className="oc-chip external" style={{ marginLeft: 6 }}>
                  需人工
                </span>
              )}
            </div>
            <div className="oc-list-sub">{item.summary}</div>
            {item.advice && <div className="oc-list-warn">建议：{item.advice}</div>}
            {showSource && item.sourceLabel && (
              <div className="oc-list-sub">来自：{item.sourceLabel}</div>
            )}
          </div>
          {item.repairable && onRepair && item.repairKey && (
            <Button
              size="small"
              appearance="secondary"
              disabled={busy}
              onClick={() => onRepair(item.repairKey as string)}
            >
              一键修复
            </Button>
          )}
        </div>
      ))}
    </div>
  )
}