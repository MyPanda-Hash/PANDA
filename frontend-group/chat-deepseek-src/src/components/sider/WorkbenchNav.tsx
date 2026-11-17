/**
 * 工作台入口：把「简报 / 待办 / 群聊 / 员工画像 / 模型配置」放进对话侧边栏顶部，
 * 布局参考 DeepSeek Harness 的侧边栏（图标 + 文字的纵向列表，位于新会话按钮之上）。
 *
 * 这里只做导航，不复制任何业务逻辑：
 * - 嵌在工作台 iframe 里时，改**父窗口**的 hash，让它切视图；
 * - 单独打开对话页时，直接跳工作台的对应路由。
 */
import { useTranslate } from '../../i18n/index.js'

type NavItem = {
  key: string
  hash: string
  label: string
  icon: JSX.Element
}

const stroke = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}


/** 是否管理员：审计类入口只给管理员。 */
function isMesAdmin(): boolean {
  try {
    const raw = localStorage.getItem('mes_user')
    if (!raw || raw === 'undefined') return false
    const user = JSON.parse(raw) as { isAdmin?: boolean }
    return Boolean(user && user.isAdmin)
  } catch {
    return false
  }
}

const ICONS: Record<string, JSX.Element> = {
  brief: (
    <svg width='16' height='16' viewBox='0 0 16 16' aria-hidden='true' {...stroke}>
      <path d='M3 2.5h10v11H3z' />
      <path d='M5.5 5.5h5M5.5 8h5M5.5 10.5h3' />
    </svg>
  ),
  todos: (
    <svg width='16' height='16' viewBox='0 0 16 16' aria-hidden='true' {...stroke}>
      <path d='M2.5 4.5l1.5 1.5 2.5-3' />
      <path d='M9 5h4.5M2.5 11l1.5 1.5 2.5-3M9 11.5h4.5' />
    </svg>
  ),
  groups: (
    <svg width='16' height='16' viewBox='0 0 16 16' aria-hidden='true' {...stroke}>
      <path d='M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z' />
    </svg>
  ),
  staff: (
    <svg width='16' height='16' viewBox='0 0 16 16' aria-hidden='true' {...stroke}>
      <circle cx='8' cy='5.5' r='2.6' />
      <path d='M3 13.2c0-2.4 2.2-3.9 5-3.9s5 1.5 5 3.9' />
    </svg>
  ),

  archive: (
    <svg width='16' height='16' viewBox='0 0 16 16' aria-hidden='true' {...stroke}>
      <path d='M2 3.5h12v3H2z' />
      <path d='M3 6.5v6h10v-6' />
      <path d='M6.5 9h3' />
    </svg>
  ),
  usage: (
    <svg width='16' height='16' viewBox='0 0 16 16' aria-hidden='true' {...stroke}>
      <path d='M2 12.5V9M6 12.5V5.5M10 12.5V7.5M14 12.5V3.5' />
    </svg>
  ),
  models: (
    <svg width='16' height='16' viewBox='0 0 16 16' aria-hidden='true' {...stroke}>
      <circle cx='8' cy='8' r='2.2' />
      <path d='M8 1.8v2M8 12.2v2M1.8 8h2M12.2 8h2M3.6 3.6l1.4 1.4M11 11l1.4 1.4M12.4 3.6L11 5M5 11l-1.4 1.4' />
    </svg>
  ),
}

export const WorkbenchNav = ({ styles }: { styles: Record<string, string> }) => {
  const t = useTranslate()
  const items: NavItem[] = [
    { key: 'brief', hash: '#/brief', label: t('wbBrief'), icon: ICONS.brief },
    { key: 'todos', hash: '#/todos', label: t('wbTodos'), icon: ICONS.todos },
    { key: 'groups', hash: '#/groups', label: t('wbGroups'), icon: ICONS.groups },
    { key: 'staff', hash: '#/staff', label: t('wbStaff'), icon: ICONS.staff },
    { key: 'models', hash: '#/models', label: t('wbModels'), icon: ICONS.models },
    ...(isMesAdmin() ? [{ key: 'aiUsage', hash: '#/ai-usage', label: t('wbAiUsage'), icon: ICONS.usage }] : []),
    ...(isMesAdmin() ? [{ key: 'archive', hash: '#/archive', label: t('wbArchive'), icon: ICONS.archive }] : []),
  ]

  const go = (hash: string) => {
    try {
      if (window.parent && window.parent !== window) {
        // 同源嵌入：让外层工作台切视图（它会卸载本 iframe）
        window.parent.location.hash = hash
        return
      }
    } catch {
      /* 跨源或访问受限时退回整页跳转 */
    }
    window.location.href = '/ai/' + hash
  }

  return (
    <nav className={styles.wbNav} aria-label={t('wbNavLabel')}>
      {items.map((item) => (
        <button
          key={item.key}
          type='button'
          className={styles.wbNavItem}
          onClick={() => go(item.hash)}
        >
          <span className={styles.wbNavIcon}>{item.icon}</span>
          <span className={styles.wbNavLabel}>{item.label}</span>
        </button>
      ))}
    </nav>
  )
}