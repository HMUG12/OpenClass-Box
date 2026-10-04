import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { api } from '../api'

export interface Track {
  path: string
  name: string
  artist?: string
  platform?: string
  size?: number
}

export type PlayMode = 'list' | 'one' | 'shuffle'

interface PlayerApi {
  queue: Track[]
  index: number
  current: Track | null
  playing: boolean
  progress: number
  duration: number
  volume: number
  mode: PlayMode
  /** 播放某一首；给了 queue 就把它作为新的播放队列（手动加入列表时用） */
  play: (track: Track, queue?: Track[]) => Promise<void>
  addToQueue: (tracks: Track[]) => void
  removeFromQueue: (at: number) => void
  clearQueue: () => void
  toggle: () => void
  next: () => void
  prev: () => void
  seek: (seconds: number) => void
  setVolume: (value: number) => void
  setMode: (mode: PlayMode) => void
  message: string
}

const Ctx = createContext<PlayerApi | null>(null)

/** 播放控制。任何组件里都能用，但播放器本体只存在于 Provider（不随页面销毁）。 */
export function usePlayer(): PlayerApi {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('usePlayer 必须在 PlayerProvider 内使用')
  return ctx
}
/**
 * 全局播放器。
 *
 * 为什么必须全局：播放状态原本存在 MusicPage 里，而页面切换会让该组件被
 * 卸载 —— <audio> 元素随之销毁，音乐当场停掉。用户听到的是"我切个设置
 * 歌就没了"，这是最典型的"功能存在但用不下去"。
 *
 * 现在 <audio> 只存在于这个 Provider 内（Provider 挂在 App 根部，永不
 * 卸载），页面只是它的观察者。
 */
export function PlayerProvider({ children }: { children: ReactNode }) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [queue, setQueue] = useState<Track[]>([])
  const [index, setIndex] = useState(-1)
  const [playing, setPlaying] = useState(false)
  const [progress, setProgress] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolumeState] = useState(0.8)
  const [mode, setMode] = useState<PlayMode>('list')
  const [message, setMessage] = useState('')

  const current = index >= 0 && index < queue.length ? queue[index] : null

  // ── 装载 <audio>：整个应用生命周期内只挂一次 ──
  useEffect(() => {
    const audio = new Audio()
    audio.preload = 'metadata'
    audio.volume = volume
    audioRef.current = audio

    const onTime = () => setProgress(audio.currentTime || 0)
    const onMeta = () => setDuration(Number.isFinite(audio.duration) ? audio.duration : 0)
    const onPlay = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    const onEnded = () => setPlaying(false)
    const onError = () => {
      setPlaying(false)
      setMessage('这首歌播不了（文件可能已被移动或删除）')
    }

    audio.addEventListener('timeupdate', onTime)
    audio.addEventListener('loadedmetadata', onMeta)
    audio.addEventListener('play', onPlay)
    audio.addEventListener('pause', onPause)
    audio.addEventListener('ended', onEnded)
    audio.addEventListener('error', onError)

    return () => {
      audio.pause()
      audio.removeEventListener('timeupdate', onTime)
      audio.removeEventListener('loadedmetadata', onMeta)
      audio.removeEventListener('play', onPlay)
      audio.removeEventListener('pause', onPause)
      audio.removeEventListener('ended', onEnded)
      audio.removeEventListener('error', onError)
      audioRef.current = null
    }
    // 故意只跑一次：volume 变化单独处理，不该重建 audio
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (audioRef.current) audioRef.current.volume = volume
  }, [volume])

  const load = useCallback(async (track: Track) => {
    const audio = audioRef.current
    if (!audio) return
    setMessage('')
    try {
      const url = await api.music_url(track.path)
      if (url) {
        audio.src = url
        await audio.play().catch(() => setMessage('浏览器拦住了自动播放，请再点一次播放'))
      } else {
        setMessage('拿不到播放地址')
      }
    } catch (e) {
      setMessage(`播放失败：${e}`)
    }
  }, [])
  // ── 控制 ──
  const play = useCallback(
    async (track: Track, list?: Track[]) => {
      if (list && list.length) {
        setQueue(list)
        setIndex(list.findIndex((one) => one.path === track.path))
      } else {
        setQueue((old) => {
          const found = old.findIndex((one) => one.path === track.path)
          if (found >= 0) setIndex(found)
          return found >= 0 ? old : [...old, track]
        })
      }
      await load(track)
    },
    [load]
  )

  const addToQueue = useCallback((tracks: Track[]) => {
    if (!tracks.length) return
    setQueue((old) => {
      const known = new Set(old.map((one) => one.path))
      // 去重：同一首歌重复点"加入列表"不该出现两次
      return [...old, ...tracks.filter((one) => !known.has(one.path))]
    })
    setMessage(`已加入 ${tracks.length} 首`)
  }, [])

  const removeFromQueue = useCallback((at: number) => {
    setQueue((old) => {
      const next = old.filter((_, i) => i !== at)
      setIndex((cur) => {
        if (at < cur) return cur - 1
        if (at === cur) return next.length ? Math.min(cur, next.length - 1) : -1
        return cur
      })
      return next
    })
  }, [])

  const clearQueue = useCallback(() => {
    audioRef.current?.pause()
    setQueue([])
    setIndex(-1)
    setProgress(0)
    setDuration(0)
  }, [])

  const step = useCallback(
    (delta: number) => {
      setQueue((old) => {
        if (!old.length) return old
        let next = index + delta
        if (mode === 'shuffle' && old.length > 1) {
          next = Math.floor(Math.random() * old.length)
        } else if (next < 0) {
          next = 0
        } else if (next >= old.length) {
          next = mode === 'one' ? index : 0
        }
        if (old[next]) {
          setIndex(next)
          void load(old[next])
        }
        return old
      })
    },
    [index, load, mode]
  )

  const next = useCallback(() => {
    if (mode === 'one' && current) {
      void load(current)
      return
    }
    step(1)
  }, [current, load, mode, step])

  const prev = useCallback(() => {
    // 播放超过 3 秒时，「上一首」先回到本曲开头 —— 这是播放器的通用行为
    if (audioRef.current && audioRef.current.currentTime > 3) {
      audioRef.current.currentTime = 0
      setProgress(0)
      return
    }
    step(-1)
  }, [step])

  const toggle = useCallback(() => {
    const audio = audioRef.current
    if (!audio || !current) return
    if (audio.paused) {
      if (!audio.src) void load(current)
      else void audio.play().catch(() => setMessage('播放被浏览器拦住了，请再点一次'))
    } else {
      audio.pause()
    }
  }, [current, load])

  const seek = useCallback((seconds: number) => {
    const audio = audioRef.current
    if (!audio) return
    audio.currentTime = seconds
    setProgress(seconds)
  }, [])

  const api_: PlayerApi = {
    queue, index, current, playing, progress, duration, volume, mode, message,
    play, addToQueue, removeFromQueue, clearQueue, toggle, next, prev, seek,
    setVolume: setVolumeState,
    setMode,
  }

  return <Ctx.Provider value={api_}>{children}</Ctx.Provider>
}