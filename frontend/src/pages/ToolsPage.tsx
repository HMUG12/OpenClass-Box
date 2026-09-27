import { useMemo, useState } from 'react'
import { Input, Button } from '@fluentui/react-components'
import ToolCard from '../components/ToolCard'
import type { ToolSpec } from '../types'

// 与后端 registry.CATEGORY_NAMES 保持一致
const CATEGORY_NAMES: Record<string, string> = {
  all: '全部',
  classroom: '课堂',
  document: '文档',
  file: '文件',
  media: '媒体',
  system: '系统',
  network: '网络',
  security: '安全',
  external: '外部',
  other: '其他',
}

import MarketPanel from '../components/MarketPanel'

// 来源（kind）：原先独立的「插件」页合并到这里
const KIND_NAMES: Record<string, string> = {
  all: '全部来源',
  builtin: '自带',
  plugin: '插件',
  external: '外部程序',
}

interface Category {
  id: string
  name: string
  count: number
}

interface Props {
  tools: ToolSpec[]
  category: string
  setCategory: (c: string) => void
  onLaunch: (t: ToolSpec) => void
  onRefresh?: () => void
}

export default function ToolsPage({ tools, category, setCategory, onLaunch, onRefresh }: Props) {
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState('all')

  const categories = useMemo<Category[]>(() => {
    const counter = new Map<string, number>()
    for (const t of tools) counter.set(t.category, (counter.get(t.category) ?? 0) + 1)
    return [
      { id: 'all', name: CATEGORY_NAMES.all, count: tools.length },
      ...[...counter.entries()].map(([id, count]) => ({
        id,
        name: CATEGORY_NAMES[id] ?? id,
        count,
      })),
    ]
  }, [tools])

  const kinds = useMemo<Category[]>(() => {
    const counter = new Map<string, number>()
    for (const t of tools) {
      const key = (t as any).kind ?? 'external'
      counter.set(key, (counter.get(key) ?? 0) + 1)
    }
    return [
      { id: 'all', name: KIND_NAMES.all, count: tools.length },
      ...[...counter.entries()].map(([id, count]) => ({
        id,
        name: KIND_NAMES[id] ?? id,
        count,
      })),
    ]
  }, [tools])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return tools.filter((t) => {
      if (kind !== 'all' && ((t as any).kind ?? 'external') !== kind) return false
      if (category !== 'all' && t.category !== category) return false
      if (!q) return true
      return (
        t.name.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q) ||
        t.tags.some((tag) => tag.toLowerCase().includes(q))
      )
    })
  }, [tools, category, kind, query])

  return (
    <div className="oc-page">
      <div className="oc-page-header">
        <div className="oc-page-title">工具箱</div>
        <div className="oc-page-desc">
          已收录 {tools.length} 个工具（含插件与外部程序） · 全部以独立进程运行，互不影响
          {onRefresh && (
            <Button size="small" appearance="secondary" style={{ marginLeft: 10 }} onClick={onRefresh}>
              重新扫描
            </Button>
          )}
        </div>
      </div>

      <div className="oc-toolbar" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 8 }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <Input
            placeholder="搜索工具名称、描述或标签…"
            value={query}
            onChange={(_e, data) => setQuery(data.value)}
            style={{ maxWidth: 300 }}
          />
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
            {kinds.map((k) => (
              <Button
                key={k.id}
                size="small"
                appearance={kind === k.id ? 'primary' : 'subtle'}
                onClick={() => setKind(k.id)}
              >
                {k.name} ({k.count})
              </Button>
            ))}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
          {categories.map((c) => (
            <Button
              key={c.id}
              size="small"
              appearance={category === c.id ? 'primary' : 'subtle'}
              onClick={() => setCategory(c.id)}
            >
              {c.name} ({c.count})
            </Button>
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="oc-empty">没有匹配的工具</div>
      ) : (
        <div className="oc-toolgrid">
          {filtered.map((t) => (
            <ToolCard key={t.id} tool={t} onLaunch={onLaunch} />
          ))}
        </div>
      )}

      <MarketPanel onChanged={onRefresh} />
    </div>
  )
}
