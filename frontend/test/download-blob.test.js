import assert from 'node:assert/strict'
import test from 'node:test'

import { toBlob, assertFileBlob } from '../src/core/download.js'

/*
 * 背景（2026-11-16 实测）：响应拦截器 unwrapApiResponse 已把 res.data 解出，
 * 所以 blob 请求拿到的就是 Blob 本身。旧写法 `res.data instanceof Blob ? ... : new Blob([res.data])`
 * 会走 else 分支，把 undefined 变成 9 字节的字符串文件，Excel 打开报「文件格式无效」。
 */

test('拦截器解包后的 Blob 直接透传，不包成 "[object ...]"', async () => {
  const src = new Blob(['PK\u0003\u0004fake-xlsx'], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
  const out = toBlob(src)
  assert.equal(out, src)
  assert.equal(await out.text(), 'PK\u0003\u0004fake-xlsx')
})

test('兼容传 axios response 的写法', async () => {
  const src = new Blob(['x'], { type: 'application/pdf' })
  const out = toBlob({ data: src, status: 200 })
  assert.equal(out, src)
})

test('拿不到响应体时报错，而不是生成一个 9 字节的 undefined 文件', () => {
  assert.throws(() => toBlob(undefined), /下载内容为空/)
  assert.throws(() => toBlob(null), /下载内容为空/)
  assert.throws(() => toBlob({ status: 200 }), /下载内容为空/)
})

test('后端把 JSON 错误当文件流返回时抛出服务端文案', async () => {
  const errBlob = new Blob([JSON.stringify({ code: 400, message: '未知初始化模块：ORG' })], { type: 'application/json' })
  await assert.rejects(() => assertFileBlob(errBlob), /未知初始化模块/)
})

test('正常文件流原样返回', async () => {
  const src = new Blob(['PK'], { type: 'application/vnd.ms-excel' })
  const out = await assertFileBlob(src)
  assert.equal(out, src)
})
