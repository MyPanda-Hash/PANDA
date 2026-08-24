import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createPanelxProxySdk,
  PanelxProxyError,
} from './index.js'

function createHttpClient() {
  const calls = []
  return {
    calls,
    async get(url, config) {
      calls.push({ method: 'GET', url, params: config.params })
      return { state: '200', data: { url } }
    },
    async post(url, body) {
      calls.push({ method: 'POST', url, body })
      return { state: '200', data: { url } }
    },
  }
}

test('maps the public API to PanelX gateway endpoints', async () => {
  const httpClient = createHttpClient()
  const sdk = createPanelxProxySdk({ httpClient, basePath: '/api/panelx/' })

  await sdk.api.getPanelConfig('MANU_ORDER')
  await sdk.api.getPermMatrix({ panelCode: 'MANU_ORDER' })
  await sdk.api.getNewFormPermMatrix({ panelCode: 'MANU_ORDER', operationName: '新增流程' })
  await sdk.api.getFormDescriptor({ panelCode: 'MANU_ORDER', code: 'MO-001' })
  await sdk.api.queryFormDataList({ panelCode: 'MANU_ORDER', pageNo: 1 })
  await sdk.api.callButton({ panelCode: 'MANU_ORDER', buttonName: '提交' })
  await sdk.api.deleteForms({ panelCode: 'MANU_ORDER', rowCodes: ['MO-001'] })

  assert.deepEqual(httpClient.calls.map(({ method, url }) => ({ method, url })), [
    { method: 'GET', url: '/api/panelx/getPanelConfig' },
    { method: 'GET', url: '/api/panelx/getPermMatrix' },
    { method: 'GET', url: '/api/panelx/getNewFormPermMatrix' },
    { method: 'GET', url: '/api/panelx/getFormDescriptor' },
    { method: 'POST', url: '/api/panelx/queryFormDataList' },
    { method: 'POST', url: '/api/panelx/callButton' },
    { method: 'POST', url: '/api/panelx/deleteForms' },
  ])
  assert.deepEqual(httpClient.calls[0].params, { panelCode: 'MANU_ORDER' })
  assert.deepEqual(httpClient.calls[6].body.rowCodes, ['MO-001'])
})

test('keeps the PanelXSdk authentication-compatible user facade', async () => {
  const sdk = createPanelxProxySdk({ httpClient: createHttpClient() })

  assert.equal(sdk.user.isAuthenticated(), true)
  assert.equal(await sdk.user.login({ userName: 'ignored', password: 'ignored' }), true)
  assert.equal(sdk.user.logout(), undefined)
})

test('normalizes gateway errors without losing platform details', async () => {
  const sourceError = {
    response: {
      status: 502,
      data: {
        error: 'server_error',
        errorCode: 'PX_DOWN',
        errorDescription: 'PanelX unavailable',
      },
    },
  }
  const sdk = createPanelxProxySdk({
    httpClient: {
      async get() { throw sourceError },
      async post() { throw sourceError },
    },
  })

  await assert.rejects(
    sdk.api.getPanelConfig('MANU_ORDER'),
    (error) => {
      assert.ok(error instanceof PanelxProxyError)
      assert.equal(error.errorCode, 'PX_DOWN')
      assert.equal(error.errorDescription, 'PanelX unavailable')
      assert.equal(error.response.data.message, 'PanelX unavailable')
      assert.deepEqual(sdk.getErrorDetail(error), {
        errorCode: 'PX_DOWN',
        errorDescription: 'PanelX unavailable',
        error: 'server_error',
        status: 502,
      })
      return true
    },
  )
})

test('requires an injected GET/POST HTTP client', () => {
  assert.throws(() => createPanelxProxySdk(), /requires an HTTP client/)
})