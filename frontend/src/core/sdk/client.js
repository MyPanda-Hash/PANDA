const DEFAULT_BASE_PATH = '/panelx'

function normalizeBasePath(basePath) {
  const path = String(basePath || DEFAULT_BASE_PATH).trim()
  return `/${path.replace(/^\/+|\/+$/g, '')}`
}

function assertHttpClient(httpClient) {
  if (!httpClient || typeof httpClient.get !== 'function' || typeof httpClient.post !== 'function') {
    throw new TypeError('PanelX Proxy SDK requires an HTTP client with get() and post() methods')
  }
}

/**
 * Extract a stable error detail object from Axios-style and native errors.
 *
 * @param {unknown} error
 * @returns {{ errorCode: string, errorDescription: string, error: string, status: number|undefined }}
 */
export function getPanelxErrorDetail(error) {
  const data = error?.response?.data || {}
  return {
    errorCode: data.errorCode || error?.errorCode || 'unknown',
    errorDescription: data.errorDescription || data.message || error?.errorDescription || error?.message || String(error),
    error: data.error || error?.error || 'panelx_proxy_error',
    status: error?.response?.status || error?.status,
  }
}

/** Error returned by the local PanelX gateway. */
export class PanelxProxyError extends Error {
  constructor(error) {
    const detail = getPanelxErrorDetail(error)
    super(detail.errorDescription, { cause: error })
    this.name = 'PanelxProxyError'
    this.errorCode = detail.errorCode
    this.errorDescription = detail.errorDescription
    this.status = detail.status
    this.response = {
      status: detail.status,
      data: {
        error: detail.error,
        errorCode: detail.errorCode,
        errorDescription: detail.errorDescription,
        message: detail.errorDescription,
      },
    }
  }
}

function normalizeError(error) {
  if (error instanceof PanelxProxyError) return error
  return new PanelxProxyError(error)
}

/**
 * Create a PanelXSdk-compatible client backed by the local Spring Boot gateway.
 * The HTTP client is injected so this module has no Vue, Axios, router, or MES dependency.
 *
 * @param {{ httpClient: { get: Function, post: Function }, basePath?: string }} options
 * @returns {{ user: object, api: object, getErrorDetail: Function }}
 */
export function createPanelxProxySdk({ httpClient, basePath = DEFAULT_BASE_PATH } = {}) {
  assertHttpClient(httpClient)
  const root = normalizeBasePath(basePath)

  const get = async (path, params) => {
    try {
      return await httpClient.get(`${root}/${path}`, { params })
    } catch (error) {
      throw normalizeError(error)
    }
  }

  const post = async (path, body) => {
    try {
      return await httpClient.post(`${root}/${path}`, body)
    } catch (error) {
      throw normalizeError(error)
    }
  }

  return {
    user: {
      isAuthenticated: () => true,
      login: async () => true,
      logout: () => {},
    },
    api: {
      getPanelConfig: (panelCode) => get('getPanelConfig', { panelCode }),
      getPermMatrix: (params = {}) => get('getPermMatrix', params),
      getNewFormPermMatrix: (params = {}) => get('getNewFormPermMatrix', params),
      getFormDescriptor: (params = {}) => get('getFormDescriptor', params),
      queryFormDataList: (params = {}) => post('queryFormDataList', params),
      callButton: (params = {}) => post('callButton', params),
      deleteForms: (params = {}) => post('deleteForms', params),
    },
    getErrorDetail: getPanelxErrorDetail,
  }
}