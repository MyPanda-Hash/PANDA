// ==================== PanelX 后端代理 SDK（项目适配层） ====================
// 可复用 SDK 位于 ./sdk/；本文件只负责注入项目 HTTP 客户端和运行模式。
import request from './request'
import { USE_PANELX_PROXY } from './env'
import {
  createPanelxProxySdk,
  getPanelxErrorDetail,
  PanelxProxyError,
} from './sdk/index.js'

export { createPanelxProxySdk, getPanelxErrorDetail, PanelxProxyError }

// 惰性单例：仅在 initSdk() 首次调用且代理模式启用时构建一次
let sdk = null

/**
 * 初始化（惰性）并返回 SDK 实例。
 * @returns {object|null} 代理模式启用（VITE_PANELX_PROXY=true）时返回 sdk 实例；否则返回 null
 */
export function initSdk() {
  if (sdk) return sdk
  if (!USE_PANELX_PROXY) return null
  sdk = createPanelxProxySdk({ httpClient: request })
  return sdk
}

/**
 * 平台登录。代理模式下为幂等占位：登录态由后端网关持有，前端恒为已登录。
 * @param {string} userName 用户名（保留参数，与真实 SDK 兼容）
 * @param {string} password 密码（保留参数，与真实 SDK 兼容）
 * @returns {Promise<boolean>} 恒为 true
 * @throws {Error} 代理模式未启用（VITE_PANELX_PROXY 非 true）时抛出
 */
export async function sdkLogin(userName, password) {
  const inst = initSdk()
  if (!inst) throw new Error('PanelX 后端代理未启用（VITE_PANELX_PROXY=true）')
  if (inst.user.isAuthenticated()) return true
  await inst.user.login({ userName, password })
  return true
}

/**
 * 确保平台模式已就绪并返回 sdk 实例。
 * @returns {Promise<object>} 就绪的 sdk 实例
 * @throws {Error} 代理模式未启用 / 平台未登录时抛出
 */
export async function requireAuthed() {
  const inst = initSdk()
  if (!inst) throw new Error('PanelX 后端代理未启用（VITE_PANELX_PROXY=true）')
  if (!inst.user.isAuthenticated()) throw new Error('PanelX 平台未登录')
  return inst
}
