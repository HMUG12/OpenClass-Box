import { useState } from 'react'
import { Button, Slider, Tooltip } from '@fluentui/react-components'
import { usePlayer } from './PlayerProvider'

function clock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

const MODE_LABEL: Record<string, string> = {
  list: '顺序播放',
  one: '单曲循环',
  shuffle: '随机播放',
}

const MODE_NEXT: Record<string, string> = { list: 'one', one: 'shuffle', shuffle: 'list' }

/**
 * 底部播放条 —— 常驻在窗口底部，不随页面切换出现或消失。
 *
 * 这正是"切到设置音乐还在放"的实现：播放器活在这里（PlayerProvider 里的
 * <audio>），页面只是控制它。
 */
export default function PlayerBar() {
  const p = usePlayer()
  const [showQueue, setShowQueue] = useState(false)

  if (!p.current) return null

  const at = p.index + 1

  return (
    <div className="oc-playerbar">
      {showQueue && (
        <div className="oc-player-queue">
          <div className="oc-list-title" style={{ fontSize: 13 }}>
            播放列表（{p.queue.length}）
            <Button
              size="small"
              appearance="subtle"
              style={{ marginLeft: 8 }}
              onClick={() => {
                p.clearQueue()
                setShowQueue(false)
              }}
            >
              清空
            </Button>
          </div>
          {p.queue.length === 0 ? (
            <div className="oc-hint">列表是空的。到「音乐」页搜索后点「加入列表」。</div>
          ) : (
            <div className="oc-player-queue-list">
              {p.queue.map((one, i) => (
                <div
                  key={`${one.path}-${i}`}
                  className={`oc-player-queue-row${i === p.index ? ' on' : ''}`}
                  onClick={() => void p.play(one, p.queue)}
                >
                  <span className="oc-player-queue-name">
                    {one.name}
                    {one.artist ? ` · ${one.artist}` : ''}
                  </span>
                  <Button
                    size="small"
                    appearance="subtle"
                    onClick={(e) => {
                      e.stopPropagation()
                      p.removeFromQueue(i)
                    }}
                  >
                    移除
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="oc-playerbar-row">
        <div className="oc-playerbar-info">
          <div className="oc-playerbar-name">{p.current.name}</div>
          <div className="oc-playerbar-sub">
            {p.current.artist || (p.current.platform ? `来自${p.current.platform}` : '')}
          </div>
        </div>

        <div className="oc-playerbar-controls">
          <Tooltip content={MODE_LABEL[p.mode]} relationship="label">
            <Button
              size="small"
              appearance="subtle"
              onClick={() => p.setMode(MODE_NEXT[p.mode] as never)}
            >
              {p.mode === 'list' ? '顺序' : p.mode === 'one' ? '单曲' : '随机'}
            </Button>
          </Tooltip>
          <Button size="small" appearance="subtle" onClick={() => p.prev()} title="上一首">
            ⏮
          </Button>
          <Button
            size="small"
            appearance="primary"
            onClick={() => p.toggle()}
            title={p.playing ? '暂停' : '播放'}
          >
            {p.playing ? '⏸' : '▶'}
          </Button>
          <Button size="small" appearance="subtle" onClick={() => p.next()} title="下一首">
            ⏭
          </Button>
        </div>

        <div className="oc-playerbar-seek">
          <span className="oc-playerbar-time">{clock(p.progress)}</span>
          <Slider
            min={0}
            max={Math.max(1, p.duration)}
            value={Math.min(p.progress, Math.max(1, p.duration))}
            onChange={(_e, data) => p.seek(data.value)}
            style={{ flex: 1 }}
          />
          <span className="oc-playerbar-time">{clock(p.duration)}</span>
        </div>

        <div className="oc-playerbar-right">
          <span className="oc-playerbar-time">音量 {Math.round(p.volume * 100)}%</span>
          <Slider
            min={0}
            max={100}
            value={Math.round(p.volume * 100)}
            onChange={(_e, data) => p.setVolume(data.value / 100)}
            style={{ width: 84 }}
          />
          <span className="oc-playerbar-time">
            {at}/{p.queue.length}
          </span>
          <Button
            size="small"
            appearance={showQueue ? 'primary' : 'subtle'}
            onClick={() => setShowQueue(!showQueue)}
          >
            列表
          </Button>
        </div>
      </div>

      {p.message && <div className="oc-playerbar-msg">{p.message}</div>}
    </div>
  )
}