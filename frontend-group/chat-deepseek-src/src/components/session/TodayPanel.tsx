/**
 * 新会话顶部的「今天」面板：第一眼就能看到要拍板的事、逾期、待办。
 *
 * 设计取向（对齐 DSH 的 welcome 统计卡）：
 * - 一块面板说清楚，不再往下堆卡片；
 * - 只用一条分隔线分区，不用图标堆砌、不用彩色块；
 * - 数字克数靠字号与字重，标签退到背景里；
 * - 取不到数据就整块不渲染，宁可空着也不显示占位垃圾。
 *
 * 每个指标都能点：直接切到工作台对应的视图看明细，而不是只给一个数字。
 */
import { useEffect, useState } from 'react'
import styles from './TodayPanel.module.css'

const API_BASE = location.pathname.startsWith('/ai/') ? '/ai-api' : '/api'

type Stat = {
  label: string
  value: string
  hint?: string
  tone?: string
  /** 点这张卡去哪个工作台视图 */
  hash: string
}

function token(): string {
  try {
    const value = localStorage.getItem('chat-demo:api-key')
    return value && value !== 'undefined' ? value : ''
  } catch {
    return ''
  }
}

/** 账号等级 → 简报取数视角，与工作台保持同一套映射。 */
function roleOf(): string {
  try {
    const raw = localStorage.getItem('mes_user')
    if (!raw || raw === 'undefined') return 'staff'
    const user = JSON.parse(raw) as { isAdmin?: boolean; roleCode?: string }
    if (user.isAdmin) return 'manager'
    if (['FOREMAN', 'WORKER', 'PLANNER'].includes(String(user.roleCode))) return 'supervisor'
    return 'staff'
  } catch {
    return 'staff'
  }
}

/**
 * 切工作台视图。
 * 嵌在工作台 iframe 里时改**父窗口** hash（外层会重新渲染并把本 iframe 换掉）；
 * 单独打开对话页时整页跳转。
 */
/** 管理员切换了查看账号时带上；普通账号后端会 403，不静默生效。 */
function viewAsHeader(): Record<string, string> {
  try {
    const raw = localStorage.getItem('dnx-view-as')
    if (!raw) return {}
    const parsed = JSON.parse(raw) as { username?: string }
    return parsed && parsed.username ? { 'X-View-As': parsed.username } : {}
  } catch {
    return {}
  }
}


function goView(hash: string) {
  try {
    if (window.parent && window.parent !== window) {
      window.parent.location.hash = hash
      return
    }
  } catch {
    /* 跨源时退回整页跳转 */
  }
  window.location.href = '/ai/' + hash
}

export const TodayPanel = () => {
  const [headline, setHeadline] = useState('')
  const [stats, setStats] = useState<Stat[]>([])
  const [next, setNext] = useState('')
  const [ready, setReady] = useState(false)

  useEffect(() => {
    const bearer = token()
    if (!bearer) return
    let alive = true

    const load = async () => {
      try {
        const [boardRes, todoRes] = await Promise.all([
          fetch(API_BASE + '/assistant/briefing/board?role=' + roleOf(), {
            headers: { Authorization: 'Bearer ' + bearer, ...viewAsHeader() },
          }),
          fetch(API_BASE + '/todos?limit=200', {
            headers: { Authorization: 'Bearer ' + bearer },
          }),
        ])
        if (!alive || !boardRes.ok) return

        const board = ((await boardRes.json()) || {}).data || {}
        const rows = todoRes.ok ? await todoRes.json() : []
        const all = Array.isArray(rows) ? rows : rows.data || []
        const open = all.filter(
          (item: { status?: string }) => item.status !== 'done' && item.status !== 'dismissed',
        )
        const urgent = open.filter(
          (item: { priority?: string }) => item.priority === 'urgent',
        ).length

        const fromBoard: Stat[] = (board.stats || [])
          .map((item: { label: string; value: string; hint?: string; tone?: string }) => ({
            label: item.label,
            value: String(item.value),
            hint: item.hint,
            tone: item.tone,
            // 一律去简报：#/floor 只对 supervisor/staff 开放，
            // 管理者点了会被路由挡回来，停在对话页（2026-11-17 实测）。
            hash: '#/brief',
          }))
          // 「暂无样本」不是数字，换成待办数更有用
          .filter((item: Stat) => item.value !== '暂无样本')
          .slice(0, 2)

        const merged: Stat[] = [
          ...fromBoard,
          {
            label: '待处理待办',
            value: String(open.length),
            hint: urgent ? urgent + ' 条紧急' : '无紧急',
            hash: '#/todos',
          },
        ]

        const first = (board.decisions || [])[0]
        setHeadline(String(board.headline || ''))
        setStats(merged)
        setNext(first ? String(first.title || '') : '')
        setReady(true)
      } catch {
        /* 取不到就整块不显示 */
      }
    }

    void load()
    return () => {
      alive = false
    }
  }, [])

  if (!ready || !stats.length) return null

  return (
    <section className={styles.panel} aria-label="今天的情况">
      {headline ? (
        <button
          type='button'
          className={styles.headline}
          onClick={() => goView('#/brief')}
          title='打开今日简报'
        >
          {headline}
        </button>
      ) : null}
      <div className={styles.stats}>
        {stats.map((item) => (
          <button
            type='button'
            key={item.label}
            className={styles.stat}
            onClick={() => goView(item.hash)}
            title={'查看' + item.label}
          >
            <div className={styles.value} data-tone={item.tone || ''}>
              {item.value}
            </div>
            <div className={styles.label}>{item.label}</div>
            {item.hint ? <div className={styles.hint}>{item.hint}</div> : null}
          </button>
        ))}
      </div>
      {next ? (
        <button
          type='button'
          className={styles.next}
          onClick={() => goView('#/brief')}
          title='查看要你决定的事'
        >
          接下来：{next}
        </button>
      ) : null}
    </section>
  )
}
