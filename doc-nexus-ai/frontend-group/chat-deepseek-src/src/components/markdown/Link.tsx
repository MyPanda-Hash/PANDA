import type { Image, Link as LinkNode } from 'mdast'
import { memo, type ReactNode } from 'react'

import styles from './Link.module.css'

const sanitizeUrl = (url: string) => {
  // 如果URL没有协议，添加默认的https协议
  if (url && !url.match(/^[a-zA-Z]+:\/\//) && !url.startsWith('mailto:')) {
    url = `https://${url}`
  }

  try {
    const parsedUrl = new URL(url)
    if (['http:', 'https:', 'mailto:'].includes(parsedUrl.protocol)) {
      return parsedUrl.toString()
    }
    return undefined
  } catch {
    return undefined
  }
}

export const Link = memo(function Link({
  node,
  children,
}: {
  node: LinkNode
  children: ReactNode
}) {
  // 报表入口：nxs-report:<kind> 伪协议。
  // 报表正文存在后端会话里，不是独立页面，所以不能当普通链接打开；
  // 这里渲染成按钮，点一下 postMessage 给外层工作台（frontend-desk）去开查看器。
  // 注意：必须放在 sanitizeUrl 之前，否则会被协议白名单过滤掉。
  if (node.url.startsWith('nxs-report:')) {
    const kind = node.url.slice('nxs-report:'.length)
    return (
      <button
        type="button"
        className={styles.reportLink}
        onClick={() => {
          // 同源 iframe 里指向父窗口；跨源时 postMessage 仍会投递，只是不能读父文档
          window.parent?.postMessage({ type: 'nxs-report', kind }, '*')
        }}
      >
        {children}
      </button>
    )
  }

  // 页内锚点显示链接文字。
  if (node.url.startsWith('#')) {
    return children
  }

  // 邮件地址显示链接文字。
  if (node.url.startsWith('mailto:')) {
    return children
  }

  // URL 解析失败时 href 为空，链接文字仍可显示。
  const href = sanitizeUrl(node.url)

  return (
    <a href={href} title={node.title || undefined} target="_blank" rel="noreferrer">
      {children}
    </a>
  )
})

/** Markdown 图片显示为指向图片地址的链接 */
export function ImageAsLink({ node }: { node: Image }) {
  const url = sanitizeUrl(node.url)
  return (
    <a href={url} title={node.title ?? ''} target="_blank" rel="noreferrer">
      {url}
    </a>
  )
}
