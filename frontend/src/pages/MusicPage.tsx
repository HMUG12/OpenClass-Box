import { useCallback, useEffect, useState } from 'react'
import { Button, Input, Spinner } from '@fluentui/react-components'
import { api } from '../api'
import { usePlayer, type Track } from '../components/PlayerProvider'

function formatSize(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1048576).toFixed(1)} MB`
}

function dateText(ts: number): string {
  try {
    const d = new Date(ts * 1000)
    return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch {
    return ''
  }
}

/**
 * 音乐 —— 搜索、下载缓存、播放列表。
 *
 * 三处与旧版不同，都是为了修"用不下去"的地方：
 * 1. 播放器搬到了窗口底栏（PlayerBar），切页面音乐不会停；
 * 2. 搜索结果**不自动进列表**，每首旁边有「加入列表」，由你决定听什么；
 * 3. 下载缓存有独立入口（可播放、加入列表、删除、清空）—— 以前它混在
 *    本地曲库里，文件名是一串歌曲 ID，既认不出也删不掉。
 */
export default function MusicPage() {
  const player = usePlayer()
  const [tab, setTab] = useState<'local' | 'online' | 'cache'>('local')
  const [keyword, setKeyword] = useState('')
  const [list, setList] = useState<any[]>([])
  const [cache, setCache] = useState<any[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const loadCache = useCallback(async () => {
    try {
      setCache((await api.music_cache_list()) ?? [])
    } catch {
      setCache([])
    }
  }, [])

  useEffect(() => {
    void loadCache()
  }, [loadCache, tab])
  // ── 搜索 ──
  const doSearch = async (kw = keyword, which: 'local' | 'online' | 'cache' = tab) => {
    const source: 'online' | 'local' = which === 'online' ? 'online' : 'local'
    setBusy(true)
    setMsg('')
    try {
      const result =
        source === 'online'
          ? await api.search_music_online(kw, 'netease')
          : await api.search_music(kw)
      setList((result as any[]) ?? [])
      if (!(result as any[])?.length) {
        setMsg(
          source === 'online'
            ? '没搜到。可能没联网，或这首歌不对外开放。'
            : '本地曲库是空的：把音频放进「音乐」文件夹后再搜。'
        )
      }
    } catch (e) {
      setMsg(`搜索失败：${e}`)
    } finally {
      setBusy(false)
    }
  }

  // ── 播放 / 加入列表 ──
  const resolve = async (item: any): Promise<Track | null> => {
    if (item.path) return item as Track
    // 在线歌曲：先拉到自己能播放的本地缓存
    const path = await api.fetch_music(
      item.id,
      item.platform || 'netease',
      item.name,
      item.artist
    )
    if (!path) {
      setMsg('这首歌拿不到播放地址（可能受版权限制）')
      return null
    }
    await loadCache()
    return { ...item, path } as Track
  }

  const playOne = async (item: any, list_?: any[]) => {
    const track = await resolve(item)
    if (!track) return
    // 传整份列表 → 点哪首就从哪首开始按顺序放
    const queue = (list_ ?? list).filter((one) => one?.id || one?.path)
    const resolved = await Promise.all(queue.map((one) => resolve(one).catch(() => null)))
    await player.play(track, resolved.filter(Boolean) as Track[])
  }

  const addOne = async (item: any) => {
    const track = await resolve(item)
    if (!track) return
    player.addToQueue([track])
  }

  const addAll = async () => {
    if (!list.length) return
    setBusy(true)
    try {
      const resolved = await Promise.all(list.map((one) => resolve(one).catch(() => null)))
      player.addToQueue(resolved.filter(Boolean) as Track[])
    } finally {
      setBusy(false)
    }
  }

  const removeCache = async (name: string) => {
    if (!window.confirm(`删除缓存的「${name}」？`)) return
    const r = await api.music_cache_delete(name)
    setMsg(r?.message ?? '')
    await loadCache()
  }

  const clearCache = async () => {
    if (!window.confirm('清空全部下载缓存？本地曲库里的文件不受影响。')) return
    const r = await api.music_cache_clear()
    setMsg(r?.message ?? '')
    await loadCache()
  }
  const renderTrackRow = (item: any, extra?: any) => (
    <div className="oc-list-row" key={item.path || item.id}>
      <div className="oc-list-main">
        <div className="oc-list-title">{item.name}</div>
        <div className="oc-list-sub">
          {item.artist ? item.artist + ' · ' : ''}
          {extra}
        </div>
      </div>
      <div className="oc-actions">
        <Button size="small" appearance="subtle" onClick={() => void addOne(item)}>
          加入列表
        </Button>
        <Button
          size="small"
          appearance="primary"
          onClick={() => void playOne(item, list)}
          disabled={busy}
        >
          播放
        </Button>
      </div>
    </div>
  )

  return (
    <div className="oc-page">
      <div className="oc-page-header">
        <div className="oc-page-title">音乐</div>
        <div className="oc-page-desc">
          播放条常驻窗口底部，切到别的页面也不会停
          {player.queue.length ? `（列表 ${player.queue.length} 首）` : ''}
        </div>
      </div>

      <div className="oc-panel" style={{ marginBottom: 12 }}>
        <div className="oc-toolbar" style={{ marginBottom: 8 }}>
          <div className="oc-actions">
            <Button
              size="small"
              appearance={tab === 'local' ? 'primary' : 'secondary'}
              onClick={() => {
                setTab('local')
                setList([])
                setMsg('')
              }}
            >
              本地曲库
            </Button>
            <Button
              size="small"
              appearance={tab === 'online' ? 'primary' : 'secondary'}
              onClick={() => {
                setTab('online')
                setList([])
                setMsg('')
              }}
            >
              在线搜索
            </Button>
            <Button
              size="small"
              appearance={tab === 'cache' ? 'primary' : 'secondary'}
              onClick={() => {
                setTab('cache')
                setList([])
                setMsg('')
              }}
            >
              下载缓存{cache.length ? `（${cache.length}）` : ''}
            </Button>
          </div>
        </div>
        {tab !== 'cache' && (
          <>
            <div className="oc-searchbar">
              <Input
                placeholder={
                  tab === 'online' ? '歌名或歌手（需要联网）' : '搜索本地曲库'
                }
                value={keyword}
                onChange={(_e, d) => setKeyword(d.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void doSearch()
                }}
              />
              <Button appearance="primary" onClick={() => void doSearch()} disabled={busy}>
                {busy ? '搜索中…' : '搜索'}
              </Button>
              {list.length > 0 && (
                <Button appearance="secondary" onClick={() => void addAll()} disabled={busy}>
                  全部加入列表
                </Button>
              )}
            </div>
            <div className="oc-hint" style={{ marginTop: 6 }}>
              搜索结果不会自动播放，也不会自动进列表 —— 点「加入列表」后由你决定听什么。
            </div>
          </>
        )}

        {tab === 'cache' && (
          <>
            <div className="oc-usage-sub" style={{ marginBottom: 8 }}>
              在线搜索里下载过的歌会存在这里，可以直接播放、加入列表或删除。
              不占用本地曲库，删掉不影响你自己的音乐文件。
            </div>
            {cache.length > 0 && (
              <div className="oc-actions" style={{ marginBottom: 8 }}>
                <Button size="small" appearance="secondary" onClick={() => void addAll()}>
                  全部加入列表
                </Button>
                <Button size="small" appearance="secondary" onClick={() => void clearCache()}>
                  清空缓存
                </Button>
              </div>
            )}
          </>
        )}

        {msg && <div className="oc-list-sub" style={{ marginTop: 8 }}>{msg}</div>}

        {tab === 'cache' ? (
          <div className="oc-list" style={{ marginTop: 8 }}>
            {cache.length === 0 ? (
              <div className="oc-hint">还没有下载过歌曲。到「在线搜索」找一首，播放时会自动缓存。</div>
            ) : (
              cache.map((one) => (
                <div className="oc-list-row" key={one.path}>
                  <div className="oc-list-main">
                    <div className="oc-list-title">{one.name}</div>
                    <div className="oc-list-sub">
                      {one.artist ? one.artist + ' · ' : ''}
                      {formatSize(one.size)} · {dateText(one.at)}
                    </div>
                  </div>
                  <div className="oc-actions">
                    <Button
                      size="small"
                      appearance="subtle"
                      onClick={() => {
                        player.addToQueue([one as Track])
                        setMsg(`已加入 ${one.name}`)
                      }}
                    >
                      加入列表
                    </Button>
                    <Button
                      size="small"
                      appearance="primary"
                      onClick={() => void playOne(one, cache)}
                    >
                      播放
                    </Button>
                    <Button
                      size="small"
                      appearance="subtle"
                      onClick={() => void removeCache(one.path.split(/[\\/]/).pop() || '')}
                    >
                      删除
                    </Button>
                  </div>
                </div>
              ))
            )}
          </div>
        ) : (
          <div className="oc-list" style={{ marginTop: 8 }}>
            {busy && list.length === 0 ? (
              <div className="oc-hint">搜索中…</div>
            ) : list.length === 0 ? (
              <div className="oc-hint">
                {tab === 'online' ? '输入歌名开始搜索。' : '本地曲库为空时先去「在线搜索」找几首。'}
              </div>
            ) : (
              list.map((item) => renderTrackRow(item, item.size ? formatSize(item.size) : ''))
            )}
          </div>
        )}

        {busy && list.length > 0 && <Spinner size="tiny" />}
      </div>
    </div>
  )
}