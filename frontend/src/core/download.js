/**
 * 文件下载的公共封装。
 *
 * <p>坑：`request.interceptors.response` 用的是 {@link unwrapApiResponse}，
 * 它返回的是 `res.data`，所以 `request.get(url, { responseType: 'blob' })`
 * resolve 出来的**就是 Blob 本身**，不是 axios response。
 * 写成 `res.data instanceof Blob ? res.data : new Blob([res.data])` 会永远走 else 分支，
 * 把 `undefined` 写成 9 字节的文本文件（内容就是字符串 undefined），
 * Excel / WPS 打开报「文件格式无效」。2026-11-16 实测踩过。
 */

/** 从拦截器产物里取出 Blob；拿不到就报错，绝不退化成字符串。 */
export function toBlob(raw) {
  if (raw instanceof Blob) return raw
  // 兼容直接把 axios response 传进来的调用方
  if (raw && raw.data instanceof Blob) return raw.data
  throw new Error('下载内容为空：响应不是文件流')
}

/**
 * 后端可能用 200 + JSON 返回业务错误（例如模块名传错），
 * 这时 Blob 里是错误 JSON；不拦下来就会存成一个打不开的 .xlsx。
 */
export async function assertFileBlob(blob) {
  const type = String(blob.type || '').toLowerCase()
  if (!type.includes('json')) return blob
  let message = '下载失败'
  try {
    const body = JSON.parse(await blob.text())
    if (body && (body.message || body.msg)) message = body.message || body.msg
  } catch (e) {
    // 内容不是合法 JSON，保留默认文案
  }
  throw new Error(message)
}

/** 触发浏览器保存 Blob。 */
export function saveBlob(blob, fileName) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = fileName || 'download'
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}
