export function unwrap(res) {
  if (!res) return res
  return res.data ?? res
}

export function errMsg(e) {
  return e?.response?.data?.message || e?.message || String(e)
}
