import request from '@core/request'

export async function apiLogin(payload) {
  const res = await request.post('/auth/login', payload)
  if (res?.code && res.code !== 200) throw new Error(res.message || '登录失败')
  return res?.data ?? res
}

export async function apiGetPerms() {
  const res = await request.get('/auth/perms')
  return res?.data ?? res
}

export async function apiGetUserInfo() {
  const res = await request.get('/auth/userinfo')
  return res?.data ?? res
}

export async function apiGetMenus() {
  const res = await request.get('/sys/menu/tree')
  return res?.data ?? res
}

export async function apiGetFactories() {
  const res = await request.get('/base/factory/list')
  return res?.data ?? res
}

export async function apiGetBadge() {
  const res = await request.get('/portal/badge')
  return res?.data ?? res
}

export async function apiGetNotices(type) {
  const res = await request.get('/portal/notice/list', { params: { type } })
  return res?.data ?? res
}

/** 清空消息未读（群聊消息只显示未读，清空后列表为空）。 */
export async function apiMarkNoticeRead(type, groupId) {
  const res = await request.post('/portal/notice/read', null, { params: { type, groupId } })
  return res?.data ?? res
}

/** AI 助理免密入口：后端签 60 秒票据，返回 { ticket, url } */
export async function apiGetAiSsoTicket() {
  const res = await request.get('/ai/sso-ticket')
  return res?.data ?? res
}

/** 我的桌面「AI 助理」卡片：今日简报摘要（AI 侧不可用时 available=false） */
/** 车间大屏：当前生产要素（在制工单进度/车间分布/异常/最新报工） */
export async function apiGetProdBoard() {
  const res = await request.get('/dashboard/prod-board')
  return res?.data ?? res
}

export async function apiGetAiBrief() {
  const res = await request.get('/ai/brief')
  return res?.data ?? res
}

/** 数据看板「AI 数据分析」：把看板指标交给 AI，换回增长点与建议 */
export async function apiRunAiAnalysis(metrics) {
  const res = await request.post('/ai/analysis', { metrics }, { timeout: 90000 })
  return res?.data ?? res
}

export async function apiPageManuOrders(params) {
  const res = await request.get('/manu/order/page', { params })
  return res?.data ?? res
}

export async function apiGetManuOrder(id) {
  const res = await request.get(`/manu/order/${id}`)
  return res?.data ?? res
}

export async function apiSaveManuOrder(data) {
  const res = data.id
    ? await request.put(`/manu/order/${data.id}`, data)
    : await request.post('/manu/order', data)
  return res?.data ?? res
}

export async function apiDeleteManuOrder(id) {
  const res = await request.delete(`/manu/order/${id}`)
  return res?.data ?? res
}

export async function apiManuOrderAction(id, action) {
  const res = await request.post(`/manu/order/${id}/${action}`)
  return res?.data ?? res
}
