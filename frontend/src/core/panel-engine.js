export function unwrap(res) {
  if (!res) return res
  if (Number.isFinite(Number(res.code)) && Number(res.code) !== 200) {
    const error = new Error(res.message || '请求失败')
    error.businessCode = Number(res.code)
    error.response = { data: res }
    throw error
  }
  return res.data ?? res
}

export function errMsg(e) {
  return e?.response?.data?.message || e?.message || String(e)
}
