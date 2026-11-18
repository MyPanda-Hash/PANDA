/**
 * 侧边栏页头：显示「时间 · 账号名」，替代原来的产品名。
 *
 * 账号名来自 MES 的登录态（localStorage.mes_user），iframe 同源直接可读；
 * 时间每分钟刷新一次，跨天时带上日期。
 */
import { useEffect, useState } from 'react'

function readUserName(): string {
  try {
    const raw = localStorage.getItem('mes_user')
    if (!raw || raw === 'undefined') return ''
    const user = JSON.parse(raw) as { realName?: string; userName?: string; displayName?: string }
    return (user.realName || user.displayName || user.userName || '').trim()
  } catch {
    return ''
  }
}

function formatNow(date: Date): { time: string; day: string } {
  const pad = (n: number) => String(n).padStart(2, '0')
  return {
    time: pad(date.getHours()) + ':' + pad(date.getMinutes()),
    day: pad(date.getMonth() + 1) + '-' + pad(date.getDate()),
  }
}

export const WorkbenchHeader = ({ className }: { className: string }) => {
  const [now, setNow] = useState(() => new Date())
  // 登录态可能在对话页打开后才写入（先开页后登录），这里跟着刷新一次
  const [name, setName] = useState(() => readUserName())

  useEffect(() => {
    const timer = setInterval(() => {
      setNow(new Date())
      setName(readUserName())
    }, 30000)
    return () => clearInterval(timer)
  }, [])

  const { time, day } = formatNow(now)

  return (
    <div className={className} title={name || '未登录'}>
      <span>{time}</span>
      <span className='wbHeadSep' aria-hidden='true'>
        ·
      </span>
      <span>{name || '未登录'}</span>
      <span className='wbHeadDay'>{day}</span>
    </div>
  )
}