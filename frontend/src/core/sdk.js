import { ref } from 'vue'
import request from './request'
import { USE_PANELX_PROXY } from './env'

// PanelX 后端代理模式伪 SDK（2026-08-20 起仅代理模式；PanelX 直连 SDK 与 preload 已移除）：
// 与真实 PanelXSdk 暴露相同的 user/api 子集，引擎里的 platformCall / adapters 无需改动，
// 请求改走本地网关 /api/panelx/*（后端 PanelxService 以服务端身份调平台）。
// 平台：业务域 GroupChat_Inst_17867095995605 @ http://203.132.49.57:6612/hscx

export const sdkState = ref('idle')

let sdk = null

/**
 * 后端代理模式伪 SDK：与真实 PanelXSdk 暴露相同的 user/api 子集，
 * 引擎里的 platformCall / adapters 无需改动，请求改走本地网关 /api/panelx/*。
 * 后端已解析 getPanelConfig 的 data 字符串，语义与 SDK 一致。
 */
function buildProxySdk() {
  const normalize = (e) => {
    const desc = e?.response?.data?.errorDescription || e?.response?.data?.message || e?.message || String(e)
    return Promise.reject({ response: { data: { message: desc } }, message: desc })
  }
  return {
    user: {
      isAuthenticated: () => true,
      login: async () => true,
      logout: () => {},
    },
    api: {
      getPanelConfig: (code) => request.get('/panelx/getPanelConfig', { params: { panelCode: code } }).catch(normalize),
      getPermMatrix: (p) => request.get('/panelx/getPermMatrix', { params: p }).catch(normalize),
      getNewFormPermMatrix: (p) => request.get('/panelx/getNewFormPermMatrix', { params: p }).catch(normalize),
      getFormDescriptor: (p) => request.get('/panelx/getFormDescriptor', { params: p }).catch(normalize),
      queryFormDataList: (p) => request.post('/panelx/queryFormDataList', p).catch(normalize),
      callButton: (p) => request.post('/panelx/callButton', p).catch(normalize),
      deleteForms: (p) => request.post('/panelx/deleteForms', p).catch(normalize),
    },
    getErrorDetail: () => ({}),
  }
}

export function getSdk() {
  return sdk
}

export function isSdkLoaded() {
  return !!sdk
}

export function initSdk() {
  if (sdk) return sdk
  if (!USE_PANELX_PROXY) return null
  sdk = buildProxySdk()
  sdkState.value = 'authed'
  return sdk
}

export async function sdkLogin(userName, password) {
  const inst = initSdk()
  if (!inst) throw new Error('PanelX 后端代理未启用（VITE_PANELX_PROXY=true）')
  if (inst.user.isAuthenticated()) {
    sdkState.value = 'authed'
    return true
  }
  await inst.user.login({ userName, password })
  sdkState.value = 'authed'
  return true
}

export function sdkLogout() {
  if (sdk) sdk.user.logout()
  sdkState.value = 'ready'
}

export function unwrap(res) {
  if (!res) return res
  return res.data ?? res
}

export function sdkError(e) {
  if (sdk) {
    try {
      const d = sdk.getErrorDetail(e)
      if (d && Object.keys(d).length) return d
    } catch (_) {}
  }
  return { errorDescription: e?.message || String(e) }
}

export async function requireAuthed() {
  const inst = initSdk()
  if (!inst) throw new Error('PanelX 后端代理未启用（VITE_PANELX_PROXY=true）')
  if (!inst.user.isAuthenticated()) throw new Error('PanelX 平台未登录')
  return inst
}
