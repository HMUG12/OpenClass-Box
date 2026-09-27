import { useEffect, useRef, useState } from 'react'
import { Button, Input, Spinner } from '@fluentui/react-components'
import { api } from '../api'

/** 课堂常用搜索词：点一下就去对应平台找歌，比手打方便 */
const QUICK_WORDS = ['儿歌', '轻音乐', '古诗', '纯音乐', '白噪音', '英文儿歌', '钢琴曲', '课间音乐']

const MODE_TEXT: Record<string, string> = {
  list: '列表循环',
  one: '单曲循环',
  shuffle: '随机播放',
}

function fmtTime(seconds: number): string {
  if (!seconds || !isFinite(seconds)) return '00:00'
  const total = Math.max(0, Math.floor(seconds))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

/**
 * 音乐播放器 —— 搜索、推荐、队列、进度、音量、循环模式。
 * 在线歌曲会先拉取到本地缓存再播放（离线环境用本地曲库）。
 */
export default function MusicPage() {
  const [platform, setPlatform] = useState<'local' | 'netease'>('local')
  const [keyword, setKeyword] = useState('')
  const [list, setList] = useState<any[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const [queue, setQueue] = useState<any[]>([])
  const [index, setIndex] = useState(-1)
  const [current, setCurrent] = useState<any>(null)
  const [url, setUrl] = useState('')
  const [playing, setPlaying] = useState(false)
  const [progress, setProgress] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(0.8)
  const [mode, setMode] = useState<'list' | 'one' | 'shuffle'>('list')

  const audioRef = useRef<HTMLAudioElement | null>(null)

  const search = async (kw: string, target = platform) => {
    setBusy(true)
    setMsg('')
    setPlatform(target)
    try {
      const timeout = new Promise<never>((_, reject) =>
        window.setTimeout(() => reject(new Error('timeout')), 15000)
      )
      const result = await Promise.race([
        target === 'local' ? api.search_music(kw) : api.search_music_online(kw, target),
        timeout,
      ])
      setList((result as any[]) ?? [])
      if (!(result as any[])?.length) setMsg('没有找到结果，换个关键词或换个来源试试')
    } catch {
      setList([])
      setMsg('搜索超时或失败（在线搜索需要联网）')
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    void search('', 'local')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 音量实时同步到播放器
  useEffect(() => {
    if (audioRef.current) audioRef.current.volume = volume
  }, [volume, url])

  const loadAndPlay = async (item: any, listRef?: any[], position?: number) => {
    if (listRef) setQueue(listRef)
    if (typeof position === 'number') setIndex(position)
    setBusy(true)
    setMsg('')
    try {
      const path = item.path || (await api.fetch_music(item.id, item.platform || platform))
      if (!path) {
        setMsg('这首歌暂时拿不到播放地址（可能受版权限制或未联网）')
        return
      }
      setCurrent({ ...item, path })
      const streamUrl = await api.music_url(path)
      setUrl(streamUrl)
      setProgress(0)
      setDuration(0)
      window.setTimeout(() => {
        void audioRef.current?.play()
      }, 150)
    } catch (error) {
      setMsg(`播放失败：${error}`)
    } finally {
      setBusy(false)
    }
  }

  const step = (delta: number) => {
    if (!queue.length) return
    let nextIndex = index
    if (mode === 'shuffle') {
      nextIndex = Math.floor(Math.random() * queue.length)
    } else {
      nextIndex = (index + delta + queue.length) % queue.length
    }
    void loadAndPlay(queue[nextIndex], queue, nextIndex)
  }

  const handleEnded = () => {
    if (mode === 'one') {
      audioRef.current?.play()
      return
    }
    step(1)
  }

  const togglePlay = () => {
    if (!current) {
      if (list.length) void loadAndPlay(list[0], list, 0)
      return
    }
    if (audioRef.current?.paused) void audioRef.current?.play()
    else audioRef.current?.pause()
  }

  const seek = (value: number) => {
    if (!audioRef.current || !duration) return
    audioRef.current.currentTime = value
    setProgress(value)
  }

  const cycleMode = () => {
    const order: Array<'list' | 'one' | 'shuffle'> = ['list', 'one', 'shuffle']
    setMode(order[(order.indexOf(mode) + 1) % order.length])
  }

  const randomFromLocal = async () => {
    const result = await api.search_music('')
    const items = (result as any[]) ?? []
    if (!items.length) {
      setMsg('本地曲库是空的：可以在「工具目录」里放一个 music 文件夹，或改用在线搜索')
      return
    }
    const pick = items[Math.floor(Math.random() * items.length)]
    setList(items)
    await loadAndPlay(pick, items, items.indexOf(pick))
  }

  return (
    <div className="oc-page" style={{ paddingBottom: current ? 96 : undefined }}>
      <div className="oc-page-header">
        <div className="oc-page-title">音乐</div>
        <div className="oc-page-desc">
          搜本地曲库或在线拉歌（在线曲目会先缓存到本机再播放），支持队列、循环与音量
        </div>
      </div>

      <div className="oc-panel">
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
          <Button
            size="small"
            appearance={platform === 'local' ? 'primary' : 'secondary'}
            onClick={() => void search(keyword, 'local')}
          >
            本地曲库
          </Button>
          <Button
            size="small"
            appearance={platform === 'netease' ? 'primary' : 'secondary'}
            onClick={() => void search(keyword, 'netease')}
          >
            在线搜索
          </Button>
          <Button size="small" appearance="secondary" onClick={() => void randomFromLocal()}>
            随机来一首
          </Button>
          <span className="oc-usage-sub">
            {platform === 'local' ? '扫描本机音乐库' : '联网搜索并缓存到本机'}
          </span>
        </div>

        <div style={{ display: 'flex', gap: 8 }}>
          <Input
            value={keyword}
            onChange={(_e, data) => setKeyword(data.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void search(keyword)
            }}
            placeholder={platform === 'local' ? '搜索本地音乐（留空列出全部）' : '搜索在线音乐'}
            style={{ flex: 1 }}
          />
          <Button appearance="primary" onClick={() => void search(keyword)} disabled={busy}>
            搜索
          </Button>
        </div>

        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10, alignItems: 'center' }}>
          <span className="oc-usage-sub">课堂常用：</span>
          {QUICK_WORDS.map((word) => (
            <Button
              key={word}
              size="small"
              appearance="subtle"
              onClick={() => void search(word, 'netease')}
            >
              {word}
            </Button>
          ))}
        </div>

        {msg && (
          <div className="oc-list-sub" style={{ marginTop: 8 }}>
            {msg}
          </div>
        )}
      </div>

      {busy && (
        <div className="oc-empty">
          <Spinner size="small" label="加载中…" />
        </div>
      )}

      {!busy && list.length === 0 && (
        <div className="oc-empty">没有曲目：换个关键词，或点「随机来一首」</div>
      )}

      {!busy && list.length > 0 && (
        <div className="oc-panel" style={{ marginTop: 12 }}>
          <div className="oc-panel-title">共 {list.length} 首</div>
          <div className="oc-list oc-scroll" style={{ marginTop: 8 }}>
            {list.map((item, i) => {
              const active = current && (current.id === item.id || current.path === item.path)
              return (
                <div className="oc-list-row" key={`${item.id ?? item.path ?? i}`}>
                  <div className="oc-list-main">
                    <div className="oc-list-title">
                      {active && <span style={{ marginRight: 6 }}>▶</span>}
                      {item.name || item.title || '未命名曲目'}
                    </div>
                    <div className="oc-list-sub">
                      {item.artist || item.singer || '未知歌手'}
                      {item.album ? ` · ${item.album}` : ''}
                    </div>
                  </div>
                  <Button
                    size="small"
                    appearance={active ? 'primary' : 'secondary'}
                    disabled={busy}
                    onClick={() => void loadAndPlay(item, list, i)}
                  >
                    {active ? '正在播放' : '播放'}
                  </Button>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* ── 底部播放条 ── */}
      {current && (
        <div
          style={{
            position: 'fixed',
            left: 0,
            right: 0,
            bottom: 0,
            padding: '10px 18px',
            background: 'var(--oc-surface)',
            borderTop: '1px solid var(--oc-border)',
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            zIndex: 5,
          }}
        >
          <div style={{ minWidth: 0, width: 220 }}>
            <div className="oc-list-title" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {current.name || current.title}
            </div>
            <div className="oc-list-sub">{current.artist || current.singer || '未知歌手'}</div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Button size="small" appearance="secondary" onClick={() => step(-1)}>
              ⏮
            </Button>
            <Button size="small" appearance="primary" onClick={togglePlay} disabled={busy}>
              {playing ? '暂停' : '播放'}
            </Button>
            <Button size="small" appearance="secondary" onClick={() => step(1)}>
              ⏭
            </Button>
          </div>

          <span className="oc-usage-sub" style={{ width: 44, textAlign: 'right' }}>
            {fmtTime(progress)}
          </span>
          <input
            type="range"
            min={0}
            max={Math.max(1, duration)}
            value={progress}
            onChange={(event) => seek(Number(event.target.value))}
            style={{ flex: 1, accentColor: 'var(--colorBrandBackground, #4c8dff)' }}
          />
          <span className="oc-usage-sub" style={{ width: 44 }}>
            {fmtTime(duration)}
          </span>

          <Button size="small" appearance="secondary" onClick={cycleMode} title="切换循环模式">
            {MODE_TEXT[mode]}
          </Button>

          <span className="oc-usage-sub">音量</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={volume}
            onChange={(event) => setVolume(Number(event.target.value))}
            style={{ width: 90, accentColor: 'var(--colorBrandBackground, #4c8dff)' }}
          />

          <audio
            ref={audioRef}
            src={url}
            onTimeUpdate={(event) => setProgress((event.target as HTMLAudioElement).currentTime)}
            onLoadedMetadata={(event) => setDuration((event.target as HTMLAudioElement).duration || 0)}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={handleEnded}
          />
        </div>
      )}
    </div>
  )
}
