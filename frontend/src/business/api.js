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
