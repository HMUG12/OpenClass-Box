import { useEffect, useState } from 'react'
import { Button, Input, Spinner } from '@fluentui/react-components'
import { api } from '../api'

const STYLES = [
  { id: 'fill', label: '填充' },
  { id: 'fit', label: '适应' },
  { id: 'stretch', label: '拉伸' },
  { id: 'tile', label: '平铺' },
  { id: 'center', label: '居中' },
  { id: 'span', label: '跨屏' },
]

const SCALES = [50, 75, 100, 125, 150]

export default function WallpaperPage() {
  const [dir, setDir] = useState('')
  const [list, setList] = useState<any[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const [style, setStyle] = useState('fill')
  const [scale, setScale] = useState(100)
  const [dyn, setDyn] = useState<any>({ running: false, path: '', hasMpv: false })

  const refreshDyn = async () => {
    try {
      setDyn(await api.dynamic_wallpaper_status())
    } catch {
      /* 忽略 */
    }
  }

  const load = async () => {
    setBusy(true)
    try {
      const data = await api.list_wallpapers(dir)
      setList(data?.items ?? [])
    } catch {
      setList([])
    } finally {
      setBusy(false)
    }
    void refreshDyn()
  }

  useEffect(() => {
    void load()
  }, [])

  const [msgError, setMsgError] = useState(false)

  /**
   * 提示消息。
   * sticky=true 时不自动消失 —— 动态壁纸失败的原因（缺 mpv / 桌面承载窗口
   * 定位失败 / 显卡组合不支持）必须留在页面上，否则用户没看清就以为"点了没反应"。
   */
  const notify = (text: string, sticky = false, error = false) => {
    setMsg(text)
    setMsgError(error)
    if (!sticky) {
      window.setTimeout(() => {
        setMsg('')
        setMsgError(false)
      }, 3600)
    }
  }

  const apply = async (path: string, kind: string) => {
    if (kind === 'image') {
      const result = await api.set_wallpaper(path, style, scale)
      notify(result?.message ?? '')
      return
    }
    const result = await api.set_dynamic_wallpaper(path)
    // 失败原因必须留在页面上（见 notify 的说明）
    notify(result?.message ?? '', !result?.ok, !result?.ok)
    void refreshDyn()
  }

  const importFile = async () => {
    const picked = await api.pick_wallpaper_file()
    if (!picked?.ok) {
      notify(picked?.message ?? '未选择文件')
      return
    }
    const result = await api.import_wallpaper(picked.path)
    notify(result?.message ?? '')
    await load()
  }

  const random = async () => {
    const result = await api.random_wallpaper(dir)
    notify(result?.message ?? '')
  }

  const stopDynamic = async () => {
    const result = await api.stop_dynamic_wallpaper()
    notify(result?.message ?? '')
    void refreshDyn()
  }

  const images = list.filter((i) => i.kind === 'image')
  const dynamic = list.filter((i) => i.kind !== 'image')

  return (
    <div className="oc-page">
      <div className="oc-page-header">
        <div className="oc-page-title">壁纸</div>
        <div className="oc-page-desc">
          支持图片 / 动图 / 视频，可调位置与大小，也可直接导入文件
        </div>
      </div>

      {/* ── 位置与大小 ── */}
      <div className="oc-panel">
        <div className="oc-panel-title">位置与大小</div>
        <div className="oc-list-sub" style={{ margin: '6px 0 10px' }}>
          位置：图片在桌面上的摆放方式；大小：按屏幕尺寸的百分比缩放（100% 为原始铺满）
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
          {STYLES.map((item) => (
            <Button
              key={item.id}
              size="small"
              appearance={style === item.id ? 'primary' : 'subtle'}
              onClick={() => setStyle(item.id)}
            >
              {item.label}
            </Button>
          ))}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {SCALES.map((value) => (
            <Button
              key={value}
              size="small"
              appearance={scale === value ? 'primary' : 'subtle'}
              onClick={() => setScale(value)}
            >
              {value}%
            </Button>
          ))}
        </div>
      </div>

      {/* ── 导入与扫描 ── */}
      <div className="oc-panel" style={{ marginTop: 12 }}>
        <div className="oc-panel-title">导入与来源</div>
        <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
          <Button appearance="primary" onClick={importFile}>
            导入壁纸文件…
          </Button>
          <Button appearance="secondary" onClick={random} disabled={busy}>
            随机换一张
          </Button>
        </div>
        <div className="oc-searchbar" style={{ marginTop: 10 }}>
          <Input
            value={dir}
            onChange={(_, data) => setDir(data.value)}
            placeholder="附加图片目录（留空使用「图片」文件夹）"
            style={{ flex: 1 }}
          />
          <Button appearance="secondary" onClick={load} disabled={busy}>
            扫描
          </Button>
        </div>
        {msg && (
          <div className={msgError ? 'oc-list-warn' : 'oc-list-sub'} style={{ marginTop: 8 }}>
            {msg}
          </div>
        )}
      </div>

      {/* ── 动态壁纸状态 ── */}
      <div className="oc-panel" style={{ marginTop: 12 }}>
        <div className="oc-panel-title">动态壁纸（动图 / 视频）</div>
        <div className="oc-list-sub" style={{ margin: '6px 0 8px' }}>
          {dyn?.running
            ? `运行中：${dyn.path}`
            : dyn?.hasMpv
              ? '未运行（选择下方动图或视频即可启动）'
              : '不可用：未检测到 mpv 播放器，把 mpv.exe 放到 tools/mpv/ 后即可使用'}
        </div>
        {dyn?.running && (
          <Button appearance="secondary" onClick={stopDynamic}>
            停止动态壁纸
          </Button>
        )}
      </div>

      {busy && (
        <div className="oc-empty">
          <Spinner size="small" label="扫描中…" />
        </div>
      )}

      {!busy && list.length === 0 && <div className="oc-empty">没有找到可用壁纸</div>}

      {!busy && images.length > 0 && (
        <div className="oc-panel" style={{ marginTop: 12 }}>
          <div className="oc-panel-title">图片（{images.length}）</div>
          <div className="oc-list oc-scroll">
            {images.map((item, index) => (
              <div className="oc-list-row" key={index}>
                <div className="oc-list-main">
                  <div className="oc-list-title">{item.name}</div>
                  <div className="oc-list-sub">{item.path}</div>
                </div>
                <Button size="small" onClick={() => apply(item.path, item.kind)}>
                  设为壁纸
                </Button>
              </div>
            ))}
          </div>
        </div>
      )}

      {!busy && dynamic.length > 0 && (
        <div className="oc-panel" style={{ marginTop: 12 }}>
          <div className="oc-panel-title">动图与视频（{dynamic.length}）</div>
          <div className="oc-list oc-scroll">
            {dynamic.map((item, index) => (
              <div className="oc-list-row" key={index}>
                <div className="oc-list-main">
                  <div className="oc-list-title">
                    {item.name}
                    <span className="oc-list-sub"> · {item.kind === 'gif' ? '动图' : '视频'}</span>
                  </div>
                  <div className="oc-list-sub">{item.path}</div>
                </div>
                <Button size="small" onClick={() => apply(item.path, item.kind)}>
                  设为动态壁纸
                </Button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
