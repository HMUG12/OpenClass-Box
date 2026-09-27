import { useEffect, useState } from 'react'
import { Button, Input, Switch, Spinner } from '@fluentui/react-components'
import { api } from '../api'

/** 安全 · 系统防护：篡改修复 / USB 防护 / 自启动管理 / 弹窗管理 / 高占用监测 */
export default function SysGuardPanel() {
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [open, setOpen] = useState('')

  const load = async () => {
    setBusy('load')
    try {
      setData(await api.guard_overview())
    } catch {
      setData(null)
    } finally {
      setBusy('')
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const act = async (key: string, fn: () => Promise<any>) => {
    setBusy(key)
    setMsg('')
    try {
      const result = await fn()
      setMsg(result?.message ?? '')
      await load()
    } catch (error) {
      setMsg(`操作失败：${error}`)
    } finally {
      setBusy('')
    }
  }

  const section = (id: string, title: string, extra: React.ReactNode, body: React.ReactNode) => (
    <div className="oc-panel" style={{ marginTop: 12 }}>
      <div className="oc-panel-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Button
          size="small"
          appearance="transparent"
          onClick={() => setOpen(open === id ? '' : id)}
          style={{ paddingLeft: 0 }}
        >
          {open === id ? '▾' : '▸'} {title}
        </Button>
        <span style={{ flex: 1 }} />
        {extra}
      </div>
      {open === id && <div style={{ marginTop: 8 }}>{body}</div>}
    </div>
  )

  const hijack = data?.hijack
  const usb = data?.usb
  const startup: any[] = data?.startup?.items ?? []
  const popup = data?.popup
  const guardSettings = data?.guard ?? {}

  return (
    <>
      <div className="oc-panel" style={{ marginTop: 12 }}>
        <div className="oc-panel-title">
          系统防护
          <Button
            size="small"
            appearance="secondary"
            style={{ marginLeft: 10 }}
            onClick={() => void load()}
            disabled={busy === 'load'}
          >
            {busy === 'load' ? '检测中…' : '重新检测'}
          </Button>
          <span className="oc-list-sub" style={{ marginLeft: 8, fontWeight: 400 }}>
            共 5 项 · 所有修改都会先备份，可还原
          </span>
        </div>
        <div className="oc-hint">
          这五项是教室机器最常被动手脚的地方：装软件被顺带改掉的浏览器、被塞满的
          开机自启、突然弹广告、以及莫名卡顿。展开任意一项即可查看与处理。
        </div>
        {msg && (
          <div className="oc-list-sub" style={{ marginTop: 8 }}>
            {msg}
          </div>
        )}
        {busy === 'load' && !data && (
          <div className="oc-hint" style={{ marginTop: 8 }}>
            <Spinner size="tiny" /> 正在检查系统防护状态…
          </div>
        )}
      </div>

      {/* ① 浏览器篡改 */}
      {section(
        'hijack',
        `浏览器篡改修复（发现 ${hijack?.total ?? 0} 处）`,
        <Button
          size="small"
          appearance="secondary"
          disabled={busy === 'hijack'}
          onClick={() =>
            void act('hijack', async () => {
              const result = await api.guard_scan_hijack()
              return { ok: true, message: `扫描完成：发现 ${result?.total ?? 0} 处可疑项` }
            })
          }
        >
          重新扫描
        </Button>,
        <>
          {(hijack?.issues ?? []).length === 0 ? (
            <div className="oc-hint">
              没有发现篡改痕迹：快捷方式没有推广尾巴、hosts 干净、浏览器主页正常。
            </div>
          ) : (
            <div className="oc-list">
              {(hijack?.issues ?? []).map((item: any, index: number) => (
                <div className="oc-list-row" key={`${item.kind}-${index}`}>
                  <div className="oc-list-main">
                    <div className="oc-list-title">
                      <span className="oc-level warn" style={{ marginRight: 8 }}>
                        {item.kind === 'hosts' ? 'hosts' : item.kind === 'shortcut' ? '快捷方式' : '主页'}
                      </span>
                      {item.path || item.content || item.value}
                    </div>
                    <div className="oc-list-sub">{item.reason}</div>
                  </div>
                  <div className="oc-actions">
                    {item.kind === 'hosts' && (
                      <Button
                        size="small"
                        appearance="primary"
                        disabled={busy === 'fix'}
                        onClick={() => void act('fix', () => api.guard_fix_hosts([item.line]))}
                      >
                        注释掉这行
                      </Button>
                    )}
                    {item.kind === 'shortcut' && (
                      <Button
                        size="small"
                        appearance="primary"
                        disabled={busy === 'fix'}
                        onClick={() => void act('fix', () => api.guard_delete_shortcut(item.path))}
                      >
                        删除快捷方式
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="oc-hint" style={{ marginTop: 8 }}>
            {hijack?.homepages?.length
              ? `当前浏览器主页：${hijack.homepages.map((h: any) => `${h.browser} = ${h.value}`).join('；')}`
              : '未读取到浏览器主页设置。'}
          </div>
        </>
      )}

      {/* ② USB 防护 */}
      {section(
        'usb',
        `USB 设备防护（存储${usb?.storageEnabled === false ? '已禁用' : usb?.storageEnabled === true ? '可用' : '状态未知'}${usb?.writeProtected ? ' · 只读' : ''}）`,
        null,
        <>
          <div className="oc-list-sub" style={{ marginBottom: 8 }}>
            {usb?.message ||
              '用于防止课间被随意拷贝：可把 U 盘设为只读，或完全禁用 USB 存储设备。改动需要管理员权限，重新插拔或重启后生效。'}
          </div>
          <div className="oc-actions">
            <Button
              size="small"
              appearance="secondary"
              disabled={busy === 'usb'}
              onClick={() => void act('usb', () => api.guard_set_usb(null, true))}
            >
              设为只读
            </Button>
            <Button
              size="small"
              appearance="secondary"
              disabled={busy === 'usb'}
              onClick={() => void act('usb', () => api.guard_set_usb(null, false))}
            >
              解除只读
            </Button>
            <Button
              size="small"
              appearance="secondary"
              disabled={busy === 'usb'}
              onClick={() => void act('usb', () => api.guard_set_usb(false, null))}
            >
              禁用 USB 存储
            </Button>
            <Button
              size="small"
              appearance="primary"
              disabled={busy === 'usb'}
              onClick={() => void act('usb', () => api.guard_set_usb(true, null))}
            >
              恢复 USB 存储
            </Button>
          </div>
        </>
      )}

      {/* ③ 自启动管理 */}
      {section(
        'startup',
        `开机自启动管理（${startup.length} 项）`,
        null,
        <div className="oc-list oc-scroll">
          {startup.length === 0 && <div className="oc-hint">没有读到自启动项。</div>}
          {startup.map((item) => (
            <div className="oc-list-row" key={item.id}>
              <div className="oc-list-main">
                <div className="oc-list-title">
                  {item.name}
                  <span className="oc-level safe" style={{ marginLeft: 8 }}>
                    {item.source}
                  </span>
                  {item.disabled && (
                    <span className="oc-level warn" style={{ marginLeft: 6 }}>
                      已停用
                    </span>
                  )}
                </div>
                <div className="oc-list-sub" style={{ userSelect: 'text' }}>
                  {String(item.command).slice(0, 120)}
                </div>
              </div>
              <div className="oc-actions">
                {!item.restorable && !item.disabled && (
                  <span className="oc-list-sub">需管理员</span>
                )}
                {item.restorable &&
                  (item.disabled ? (
                    <Button
                      size="small"
                      appearance="primary"
                      disabled={busy === 'startup'}
                      onClick={() => void act('startup', () => api.guard_enable_startup(item.id))}
                    >
                      恢复
                    </Button>
                  ) : (
                    <Button
                      size="small"
                      appearance="secondary"
                      disabled={busy === 'startup'}
                      onClick={() => void act('startup', () => api.guard_disable_startup(item.id))}
                    >
                      停用
                    </Button>
                  ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ④ 弹窗管理 */}
      {section(
        'popup',
        `弹窗管理（命中 ${popup?.total ?? 0} 个）`,
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="oc-list-sub">后台自动拦截</span>
          <Switch
            checked={Boolean(guardSettings.popup_guard)}
            onChange={(_e, d) =>
              void act('popup', () => api.set_guard_settings(d.checked, undefined, undefined, undefined))
            }
          />
        </div>,
        <>
          {(popup?.items ?? []).length === 0 ? (
            <div className="oc-hint">
              没有发现规则命中的推广/弹窗程序。当前规则库：{(popup?.rules ?? []).slice(0, 6).join('、')}…
            </div>
          ) : (
            <div className="oc-list">
              {(popup?.items ?? []).map((item: any) => (
                <div className="oc-list-row" key={item.pid}>
                  <div className="oc-list-main">
                    <div className="oc-list-title">
                      {item.name}
                      <span className="oc-level warn" style={{ marginLeft: 8 }}>
                        {item.rule}
                      </span>
                    </div>
                    <div className="oc-list-sub">
                      PID {item.pid} · 内存 {item.memoryMB} MB
                      {item.exe ? ` · ${item.exe}` : ''}
                    </div>
                  </div>
                  <Button
                    size="small"
                    appearance="primary"
                    disabled={busy === `popup-${item.pid}`}
                    onClick={() =>
                      void act(`popup-${item.pid}`, () => api.guard_popup_kill(item.pid))
                    }
                  >
                    结束进程
                  </Button>
                </div>
              ))}
            </div>
          )}
          <div className="oc-hint" style={{ marginTop: 8 }}>
            开启「后台自动拦截」后，命中规则的进程会被自动结束并弹通知；默认关闭，
            只在你确认后才动手。
          </div>
        </>
      )}

      {/* ⑤ 高占用监测 */}
      {section(
        'high',
        '异常高占用程序监测',
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="oc-list-sub">超阈值时提醒我</span>
          <Switch
            checked={Boolean(guardSettings.high_usage_guard)}
            onChange={(_e, d) =>
              void act('high', () => api.set_guard_settings(undefined, d.checked, undefined, undefined))
            }
          />
        </div>,
        <>
          <div className="oc-actions" style={{ marginBottom: 8 }}>
            <span className="oc-list-sub">CPU 阈值</span>
            <Input
              value={String(guardSettings.cpuThreshold ?? 80)}
              onChange={(_e, d) =>
                void api.set_guard_settings(undefined, undefined, Number(d.value) || 80, undefined)
              }
              style={{ width: 70 }}
            />
            <span className="oc-list-sub">% · 内存阈值</span>
            <Input
              value={String(guardSettings.memThresholdMB ?? 1500)}
              onChange={(_e, d) =>
                void api.set_guard_settings(undefined, undefined, undefined, Number(d.value) || 1500)
              }
              style={{ width: 90 }}
            />
            <span className="oc-list-sub">MB</span>
            <Button
              size="small"
              appearance="secondary"
              disabled={busy === 'high'}
              onClick={() =>
                void act('high', async () => {
                  const result = await api.guard_high_usage()
                  return { ok: true, message: `当前有 ${result?.total ?? 0} 个程序超过阈值` }
                })
              }
            >
              立即检查
            </Button>
          </div>
          <div className="oc-hint">
            守护开启后：连续约 1 分钟都超阈值才会弹一次提醒（避免瞬时波动误报），
            只提醒不自动结束 —— 避免误杀正在播放的课件或考试程序。
          </div>
        </>
      )}
    </>
  )
}
