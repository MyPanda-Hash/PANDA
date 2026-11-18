import axios from 'axios'

const request = axios.create({
  baseURL: '/api',
  timeout: 15000,
})

request.interceptors.request.use((config) => {
  const token = localStorage.getItem('mes_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  // 账套：后端从 X-Factory-Code 取当前账套，所有业务查询按它过滤。
  // 放在统一拦截器里而不是各个 api 调用里传，否则 12000 行面板代码迟早漏一个，
  // 漏掉的那个请求会静默读到别的账套数据。
  const factory = readFactoryCode()
  if (factory) config.headers['X-Factory-Code'] = factory
  return config
})

/** 当前账套编码；取 user store 存的当前工厂，没选过就用后端默认。 */
function readFactoryCode() {
  try {
    const raw = localStorage.getItem('mes_factory')
    if (!raw) return ''
    const parsed = JSON.parse(raw)
    return (parsed && parsed.code) || ''
  } catch (_) {
    return ''
  }
}

export function unwrapApiResponse(res) {
  const body = res.data
  if (body && Number.isFinite(Number(body.code)) && Number(body.code) !== 200) {
    const error = new Error(body.message || '请求失败')
    error.businessCode = Number(body.code)
    error.response = { status: res.status, data: body }
    throw error
  }
  return body
}

request.interceptors.response.use(
  unwrapApiResponse,
  async (err) => {
    const status = err.response?.status
    // 仅 HTTP 401/403 表示认证失效；HTTP 200 中的业务 403 是权限不足，不能退出登录。
    if (status === 401 || status === 403) {
      try {
        const { useUserStore } = await import('@/stores/user')
        useUserStore().logout()
      } catch (e) {
        localStorage.removeItem('mes_token')
      }
      if (!location.hash.includes('/login')) location.hash = '#/login'
    }
    return Promise.reject(err)
  }
)

export default request
