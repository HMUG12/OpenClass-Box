/**
 * 外观偏好的应用 —— 把配置映射成 <html> 上的 data-* 属性，CSS 里按这些属性切换变量。
 *
 * 为什么挂在根元素而不是 React 树上：主题、配色、圆角、字号全由 CSS 变量驱动，
 * 挂在根元素上页面切换不必重算，也不会因为某个页面没包在 Provider 里而失效。
 *
 * 设置页改完会**先应用再落盘**：界面立刻有反馈，落盘失败时再由提示纠正 ——
 * 反过来（先落盘再应用）会让人以为点击没生效。
 */

export interface Appearance {
  accent: string
  radius: string
  font: string
  glass: boolean
}

export const DEFAULT_APPEARANCE: Appearance = {
  accent: 'default',
  radius: 'standard',
  font: 'standard',
  glass: false,
}

/** 配色方案（id 与 styles.css 里的 [data-accent='...'] 对应） */
export const ACCENTS: { id: string; label: string; color: string }[] = [
  { id: 'default', label: 'Fluent 蓝（默认）', color: '#0f6cbd' },
  { id: 'peach', label: '蜜桃橙', color: '#ff7a45' },
  { id: 'mint', label: '薄荷绿', color: '#12b886' },
  { id: 'lemon', label: '柠檬黄', color: '#f5a524' },
  { id: 'sakura', label: '樱花粉', color: '#e64980' },
  { id: 'grape', label: '葡萄紫', color: '#7950f2' },
  { id: 'sky', label: '晴空蓝', color: '#1c9cd6' },
  { id: 'coral', label: '珊瑚红', color: '#f06595' },
]

export const RADII: { id: string; label: string; desc: string }[] = [
  { id: 'compact', label: '紧凑', desc: '信息密度更高，适合小屏' },
  { id: 'standard', label: '标准', desc: '默认' },
  { id: 'round', label: '圆润', desc: '更柔和的观感' },
]

export const FONTS: { id: string; label: string; desc: string }[] = [
  { id: 'compact', label: '紧凑', desc: '一屏看更多' },
  { id: 'standard', label: '标准', desc: '默认' },
  { id: 'large', label: '大', desc: '讲台距离远时更易读' },
]

export function applyAppearance(look: Partial<Appearance> | null | undefined): void {
  const merged = { ...DEFAULT_APPEARANCE, ...(look ?? {}) }
  const root = document.documentElement
  root.setAttribute('data-accent', merged.accent)
  root.setAttribute('data-radius', merged.radius)
  root.setAttribute('data-font', merged.font)
  root.setAttribute('data-glass', merged.glass ? 'on' : 'off')
}
