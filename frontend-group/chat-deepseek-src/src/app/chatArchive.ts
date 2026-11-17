/**
 * 会话归档上报：把「谁、什么时候、聊了什么」同步给后端，供管理员审计。
 *
 * 边界（重要）：
 * - 只**上报**，不影响 chat-demo 自己的本地会话存储；
 * - 归档 id 由后端按「用户 + 会话 id」生成，跨账号不会互相覆盖；
 * - 上报失败静默忽略：审计是旁路，不能因为它打断对话。
 */
import { useEffect } from 'react'
import { useChatStore } from '../services/chat/store.js'

const API_BASE = location.pathname.startsWith('/ai/') ? '/ai-api' : '/api'

function mesToken(): string {
  try {
    const value = localStorage.getItem('chat-demo:api-key')
    return value && value !== 'undefined' ? value : ''
  } catch {
    return ''
  }
}

/** 上报当前会话（后端 upsert）。 */
async function syncSession(sessionId: string) {
  const state = useChatStore.getState()
  const session = state.sessionCache[sessionId]
  if (!session) return
  const summary = state.sessions.find((item) => item.id === sessionId)
  const token = mesToken()
  if (!token) return

  const payload = {
    sessionId,
    title: summary?.title || '新对话',
    startedAt: new Date(session.createdAt || Date.now()).toISOString(),
    updatedAt: new Date(session.updatedAt || Date.now()).toISOString(),
    messageCount: (session.messages || []).length,
    messages: (session.messages || []).map((m) => ({
      role: m.role,
      content: typeof m.content === 'string' ? m.content : '',
    })),
  }

  try {
    await fetch(API_BASE + '/chat-archive/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
      body: JSON.stringify(payload),
    })
  } catch {
    /* 审计失败不打断对话 */
  }
}

/** 挂在 App 里：活跃会话变化时上报一次（节流 5 秒）。 */
export const useChatArchive = () => {
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null
    let last = ''
    const unsubscribe = useChatStore.subscribe((state) => {
      const id = state.activeId
      if (!id || id === last) return
      last = id
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => {
        void syncSession(id)
      }, 5000)
    })
    return () => {
      if (timer) clearTimeout(timer)
      unsubscribe()
    }
  }, [])
}