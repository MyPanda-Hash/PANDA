import assert from 'node:assert/strict'
import test from 'node:test'

import { unwrapApiResponse } from '../src/core/request.js'
import { unwrap } from '../src/core/panel-engine.js'

test('returns the data payload for a successful business response', () => {
  const body = { code: 200, message: 'success', data: { id: 1 } }

  assert.deepEqual(unwrapApiResponse({ status: 200, data: body }), body)
  assert.deepEqual(unwrap(body), { id: 1 })
})

test('rejects an HTTP 200 response carrying a business error', () => {
  const body = { code: 403, message: '没有新增权限', data: null }

  assert.throws(
    () => unwrapApiResponse({ status: 200, data: body }),
    (error) => error.businessCode === 403 && error.message === '没有新增权限',
  )
  assert.throws(
    () => unwrap(body),
    (error) => error.businessCode === 403 && error.message === '没有新增权限',
  )
})
