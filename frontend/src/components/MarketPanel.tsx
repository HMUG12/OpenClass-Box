import { useEffect, useState } from 'react'
import { Button, Spinner } from '@fluentui/react-components'
import { api } from '../api'

function formatSize(bytes: number): string {
  if (!bytes || bytes <= 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1048576).toFixed(1)} MB`
}

/** 插件市场（轻量版）：JSON 索引 + 一键下载 + 离线导入 */
export default function MarketPanel({ onChanged }: { onChanged?: () => void }) {
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [open, setOpen] = useState(false)

  const load = async (refresh = false) => {
    setBusy('load')
    try {
      setData(await api.market_list(refresh))
    } catch {
      setData(null)
    } finally {
      setBusy('')
    }
  }

  useEffect(() => {
    void load(true)
  }, [])

  const install = async (item: any) => {
    const ok = window.confirm(
      `安装插件「${item.name}」？\n\n` +
        `· 来源：${item.url || '（无下载地址）'}\n` +
        `· 安装位置：程序目录 tools\\${item.id}\n` +
        `· 插件会以独立进程运行，请确认来源可信`
    )
    if (!ok) return
    setBusy(item.id)
    setMsg('正在下载并校验…')
    try {
      const result = await api.market_install(item.id)
      setMsg(result?.message ?? '')
      if (result?.ok) {
        await load()
        onChanged?.()
      }
    } catch (error) {
      setMsg(`安装失败：${error}`)
    } finally {
      setBusy('')
    }
  }

  const uninstall = async (item: any) => {
    if (!window.confirm(`卸载插件「${item.name}」？会删除程序目录 tools\\${item.id}`)) return
    setBusy(item.id)
    try {
      const result = await api.market_uninstall(item.id)
      setMsg(result?.message ?? '')
      if (result?.ok) {
        await load()
        onChanged?.()
      }
    } finally {
      setBusy('')
    }
  }

  const importLocal = async () => {
    setBusy('import')
    try {
      const result = await api.market_import_local()
      setMsg(result?.message ?? '')
      if (result?.ok) {
        await load()
        onChanged?.()
      }
    } finally {
      setBusy('')
    }
  }

  const items: any[] = data?.items ?? []
  const installedCount = data?.installedCount ?? 0
  const sourceText =
    data?.source === 'network'
      ? '索引来自网络'
      : data?.source === 'cache'
        ? '使用缓存的索引（离线可用）'
        : '未能获取索引'

  return (
    <div className="oc-panel" style={{ marginTop: 14 }}>
      <div className="oc-panel-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        插件市场
        <span className="oc-list-sub" style={{ fontWeight: 400 }}>
          {sourceText} · 索引版本 {data?.indexVersion || '—'} · 本机已装 {installedCount} 个
        </span>
        <span style={{ flex: 1 }} />
        <Button size="small" appearance="secondary" onClick={() => setOpen(!open)}>
          {open ? '收起' : '浏览插件'}
        </Button>
        <Button size="small" appearance="secondary" disabled={busy === 'load'} onClick={() => void load(true)}>
          刷新索引
        </Button>
        <Button size="small" appearance="secondary" disabled={busy === 'import'} onClick={() => void importLocal()}>
          离线导入 zip
        </Button>
      </div>

      {msg && (
        <div className="oc-list-sub" style={{ marginTop: 8 }}>
          {msg}
        </div>
      )}

      {open && (
        <>
          {busy === 'load' && items.length === 0 ? (
            <div className="oc-hint" style={{ marginTop: 10 }}>
              <Spinner size="tiny" /> 正在读取插件索引…
            </div>
          ) : items.length === 0 ? (
            <div className="oc-hint" style={{ marginTop: 10 }}>
              没有取到插件列表。可能是没有联网或索引地址不可达；也可以在「设置 → 插件索引地址」
              里把地址换成内网镜像，或用「离线导入 zip」手动安装插件包。
            </div>
          ) : (
            <div className="oc-list" style={{ marginTop: 10 }}>
              {items.map((item) => (
                <div className="oc-list-row" key={item.id}>
                  <div className="oc-list-main">
                    <div className="oc-list-title">
                      {item.name}
                      {(item.roles ?? []).map((role: string) => (
                        <span key={role} className="oc-level safe" style={{ marginLeft: 6 }}>
                          {role}
                        </span>
                      ))}
                      {item.installed && (
                        <span className="oc-level warn" style={{ marginLeft: 6 }}>
                          已安装{item.updatable ? ' · 可更新' : ''}
                        </span>
                      )}
                      {item.local && (
                        <span className="oc-level warn" style={{ marginLeft: 6 }}>
                          本机
                        </span>
                      )}
                    </div>
                    <div className="oc-list-sub">{item.desc}</div>
                    <div className="oc-list-sub">
                      {item.author ? `作者 ${item.author} · ` : ''}
                      {item.version ? `v${item.version} · ` : ''}
                      {formatSize(item.size)}
                      {item.url ? '' : ' · 无下载地址（仅本机）'}
                    </div>
                  </div>
                  <div className="oc-actions">
                    {item.url ? (
                      <Button
                        size="small"
                        appearance={item.installed ? 'secondary' : 'primary'}
                        disabled={busy === item.id}
                        onClick={() => void install(item)}
                      >
                        {busy === item.id ? '处理中…' : item.installed ? '重新安装' : '安装'}
                      </Button>
                    ) : null}
                    {item.installed && (
                      <Button
                        size="small"
                        appearance="transparent"
                        disabled={busy === item.id}
                        onClick={() => void uninstall(item)}
                      >
                        卸载
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="oc-hint" style={{ marginTop: 10 }}>
            插件安装前会校验 sha256（索引提供时），只从 https 地址下载；装好后以独立进程运行，
            崩溃不会影响主程序。想分享自己的插件：把带 tool.json 的文件夹打包成 zip 提 PR 即可。
          </div>
        </>
      )}
    </div>
  )
}
