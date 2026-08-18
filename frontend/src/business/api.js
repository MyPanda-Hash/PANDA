import request from '@core/request'
import { menuTree } from './menus'
// 运行模式开关统一定义在通用层 core/env.js：本文件内部使用需显式 import（re-export 不创建本地绑定）
import { USE_MOCK, USE_PANELX, USE_PORTAL_MOCK } from '@core/env'

// 运行模式开关统一定义在通用层 core/env.js，这里转发保持既有调用方兼容
export {
  USE_PANELX_DIRECT, USE_PANELX_PROXY, USE_PANELX, USE_MOCK, USE_PORTAL_MOCK,
} from '@core/env'

export async function apiLogin(payload) {
  if (USE_PORTAL_MOCK) {
    await new Promise((r) => setTimeout(r, 400))
    if (payload.userName === 'admin' && payload.password === '123456') {
      return { token: 'mock-token-' + Date.now(), user: { userName: 'admin', realName: '管理员', factoryCode: 'F01', factoryName: '一号工厂', roles: ['admin'] } }
    }
    throw { response: { data: { message: '用户名或密码错误（演示账号 admin / 123456）' } } }
  }
  const res = await request.post('/auth/login', payload)
  return res?.data ?? res
}

export async function apiGetUserInfo() {
  if (USE_PORTAL_MOCK) {
    return { userName: 'admin', realName: '管理员', factoryCode: 'F01', factoryName: '一号工厂', roles: ['admin'] }
  }
  const res = await request.get('/auth/userinfo')
  return res?.data ?? res
}

export async function apiGetMenus() {
  if (USE_PORTAL_MOCK) return menuTree
  const res = await request.get('/sys/menu/tree')
  return res?.data ?? res
}

export async function apiGetFactories() {
  if (USE_PORTAL_MOCK) return [{ code: 'F01', name: '一号工厂' }, { code: 'F02', name: '二号工厂' }]
  const res = await request.get('/base/factory/list')
  return res?.data ?? res
}

export async function apiGetBadge() {
  if (USE_PORTAL_MOCK) return { todo: 5, message: 3, alarm: 2 }
  const res = await request.get('/portal/badge')
  return res?.data ?? res
}

const MOCK_NOTICES = {
  notice: [
    { id: 1, type: 'notice', title: '轻MES v0.2 更新公告', time: '2026-08-13 09:00', read: false, content: '本次更新：新增单据查询/新增单据快捷入口、界面设置与工作台设置、消息通知中心。' },
    { id: 2, type: 'notice', title: '生产加工单模块即将上线', time: '2026-08-12 15:30', read: true, content: '生产加工单（建表 → 后端 CRUD → 前端列表+表单页）将作为第一个真实业务模块开发。' },
  ],
  todo: [
    { id: 11, type: 'todo', title: '工单 MO20260813-003 待审核', time: '2026-08-13 10:30', read: false, content: '工单 MO20260813-003（减速箱体 A ×200）已提交，等待您审核。请及时处理以免影响排产。' },
    { id: 12, type: 'todo', title: '领料单 LL20260813-007 待审批', time: '2026-08-13 09:45', read: false, content: '车间提交领料单 LL20260813-007（轴套 C 原材料），请审批后发放物料。' },
    { id: 13, type: 'todo', title: '设备 EQ-03 点检到期提醒', time: '2026-08-12 18:00', read: false, content: '设备 EQ-03（数控车床）日点检即将到期，请安排点检并录入点检结果。' },
    { id: 14, type: 'todo', title: '工序汇报单待确认', time: '2026-08-12 16:20', read: true, content: '3 张工序汇报单等待确认，涉及 5 名工人计件工资核算。' },
    { id: 15, type: 'todo', title: '期初库存余额待录入', time: '2026-08-11 11:00', read: true, content: '初始化：库存期初余额尚未完成录入，请尽快完成以保证月末核算准确。' },
  ],
  message: [
    { id: 21, type: 'message', title: '领料单 LL20260813-007 审批通过', time: '2026-08-13 09:50', read: false, content: '您提交的领料单 LL20260813-007 已由管理员审批通过，可前往仓库领料。' },
    { id: 22, type: 'message', title: '系统将于周六 22:00 维护', time: '2026-08-13 08:00', read: false, content: '系统将于本周六 22:00 - 24:00 进行例行维护，期间服务暂停，请提前保存数据。' },
    { id: 23, type: 'message', title: '新的角色权限已生效', time: '2026-08-12 14:00', read: true, content: '管理员已为您开通「生产管理」模块权限，重新登录后生效。' },
  ],
  alarm: [
    { id: 31, type: 'alarm', title: '库存预警：法兰盘 B 原材料低于安全库存', time: '2026-08-13 08:30', read: false, content: '存货「45# 圆钢 Φ60」当前库存 120 件，低于安全库存 200 件，请及时采购补充。' },
    { id: 32, type: 'alarm', title: '设备稼动率异常：EQ-05 低于 60%', time: '2026-08-12 17:00', read: false, content: '设备 EQ-05 今日稼动率 54%，低于阈值 60%，请排查停机原因。' },
  ],
}

export async function apiGetNotices(type) {
  if (USE_PORTAL_MOCK) {
    await new Promise((r) => setTimeout(r, 200))
    return MOCK_NOTICES[type] || []
  }
  const res = await request.get('/portal/notice/list', { params: { type } })
  return res?.data ?? res
}

// ==================== 生产加工单 ====================

let MOCK_ORDER_SEQ = 100

function mockOrders() {
  if (!window.__mockManuOrders) {
    const today = new Date().toISOString().slice(0, 10)
    window.__mockManuOrders = [
      {
        id: 1001, orderNo: 'MO20260813-001', orderDate: today, factoryCode: 'F01', contractNo: 'HT2026-0712', ingotNo: 'D20260813-01', batchNo: '正常',
        workshop: '熔铸车间', planStart: today, planEnd: '2026-08-20', saleOrderNo: 'SO20260810-01', customerCode: 'KH001', customerName: '华东铝业',
        testProgram: '光谱分析', prodOrderCustomer: '华东铝业', status: 2, remark: '重点客户合同，按期交付',
        createBy: 'admin', createTime: today + ' 09:00', auditBy: 'admin', auditTime: today + ' 09:30',
        items: [
          { id: 2001, seq: 1, processCode: 'PX001', processName: '下料', equipment: '锯床-01', workerGroup: '下料班', planQty: 200, finishedQty: 200, qualifiedQty: 200, defectQty: 0, remark: '' },
          { id: 2002, seq: 2, processCode: 'PX002', processName: '车削', equipment: '数控车床-03', workerGroup: '车工班', planQty: 200, finishedQty: 140, qualifiedQty: 138, defectQty: 2, remark: '' },
          { id: 2003, seq: 3, processCode: 'PX005', processName: '热处理', equipment: '热处理炉-01', workerGroup: '热处理班', planQty: 200, finishedQty: 0, qualifiedQty: 0, defectQty: 0, remark: '' },
          { id: 2004, seq: 4, processCode: 'PX007', processName: '检验', equipment: '检测台-01', workerGroup: '质检班', planQty: 200, finishedQty: 0, qualifiedQty: 0, defectQty: 0, remark: '' },
        ],
      },
      {
        id: 1002, orderNo: 'MO20260813-002', orderDate: today, factoryCode: 'F01', contractNo: 'HT2026-0688', ingotNo: 'D20260813-02', batchNo: '加急',
        workshop: '轧制车间', planStart: '2026-08-12', planEnd: '2026-08-15', saleOrderNo: 'SO20260809-02', customerCode: 'KH002', customerName: '中天精工',
        testProgram: '硬度测试', prodOrderCustomer: '中天精工', status: 3, remark: '',
        createBy: 'admin', createTime: today + ' 08:30', auditBy: 'admin', auditTime: today + ' 08:40',
        items: [
          { id: 2005, seq: 1, processCode: 'PX001', processName: '下料', equipment: '锯床-02', workerGroup: '下料班', planQty: 500, finishedQty: 500, qualifiedQty: 500, defectQty: 0, remark: '' },
          { id: 2006, seq: 2, processCode: 'PX002', processName: '车削', equipment: '数控车床-01', workerGroup: '车工班', planQty: 500, finishedQty: 500, qualifiedQty: 498, defectQty: 2, remark: '' },
          { id: 2007, seq: 3, processCode: 'PX007', processName: '检验', equipment: '检测台-02', workerGroup: '质检班', planQty: 500, finishedQty: 498, qualifiedQty: 498, defectQty: 0, remark: '' },
        ],
      },
      {
        id: 1003, orderNo: 'MO20260813-003', orderDate: today, factoryCode: 'F02', contractNo: 'HT2026-0750', ingotNo: 'D20260813-03', batchNo: '正常',
        workshop: '精整车间', planStart: '2026-08-14', planEnd: '2026-08-22', saleOrderNo: 'SO20260811-03', customerCode: 'KH003', customerName: '西部材料',
        testProgram: '金相检验', prodOrderCustomer: '西部材料', status: 1, remark: '待开工',
        createBy: 'admin', createTime: today + ' 10:00', auditBy: 'admin', auditTime: today + ' 10:10',
        items: [
          { id: 2008, seq: 1, processCode: 'PX001', processName: '下料', equipment: '锯床-01', workerGroup: '下料班', planQty: 300, finishedQty: 60, qualifiedQty: 60, defectQty: 0, remark: '' },
          { id: 2009, seq: 2, processCode: 'PX002', processName: '车削', equipment: '数控车床-05', workerGroup: '车工班', planQty: 300, finishedQty: 0, qualifiedQty: 0, defectQty: 0, remark: '' },
          { id: 2010, seq: 3, processCode: 'PX007', processName: '检验', equipment: '检测台-01', workerGroup: '质检班', planQty: 300, finishedQty: 0, qualifiedQty: 0, defectQty: 0, remark: '' },
        ],
      },
    ]
  }
  return window.__mockManuOrders
}

function mockDelay(ms = 200) {
  return new Promise((r) => setTimeout(r, ms))
}

export async function apiPageManuOrders(params) {
  if (USE_PORTAL_MOCK) {
    await mockDelay()
    let list = mockOrders().map((o) => ({ ...o }))
    if (params.orderNo) list = list.filter((o) => o.orderNo.includes(params.orderNo))
    if (params.ingotNo) list = list.filter((o) => (o.ingotNo || '').includes(params.ingotNo))
    if (params.status !== undefined && params.status !== null && params.status !== '') list = list.filter((o) => o.status === Number(params.status))
    if (params.factoryCode) list = list.filter((o) => o.factoryCode === params.factoryCode)
    list.sort((a, b) => (a.id < b.id ? 1 : -1))
    const pageNo = params.pageNo || 1
    const pageSize = params.pageSize || 20
    return { total: list.length, records: list.slice((pageNo - 1) * pageSize, pageNo * pageSize).map((o) => ({ ...o, items: undefined })) }
  }
  const res = await request.get('/manu/order/page', { params })
  return res?.data ?? res
}

export async function apiGetManuOrder(id) {
  if (USE_PORTAL_MOCK) {
    await mockDelay()
    const o = mockOrders().find((x) => x.id === Number(id))
    if (!o) throw { response: { data: { message: '单据不存在' } } }
    return JSON.parse(JSON.stringify(o))
  }
  const res = await request.get(`/manu/order/${id}`)
  return res?.data ?? res
}

export async function apiSaveManuOrder(data) {
  if (USE_PORTAL_MOCK) {
    await mockDelay()
    const list = mockOrders()
    if (data.id) {
      const idx = list.findIndex((x) => x.id === Number(data.id))
      if (idx === -1) throw { response: { data: { message: '单据不存在' } } }
      const old = list[idx]
      list[idx] = { ...old, ...data, orderNo: old.orderNo, status: old.status, createBy: old.createBy, createTime: old.createTime }
      return list[idx]
    }
    const today = new Date().toISOString().slice(0, 10)
    const no = `MO${today.replaceAll('-', '')}-${String(MOCK_ORDER_SEQ++).padStart(3, '0')}`
    const now = new Date().toLocaleString('zh-CN', { hour12: false }).replaceAll('/', '-')
    const order = { ...data, id: Math.max(0, ...list.map((x) => x.id)) + 1, orderNo: no, status: 0, createBy: 'admin', createTime: now }
    list.unshift(order)
    return order
  }
  const res = data.id
    ? await request.put(`/manu/order/${data.id}`, data)
    : await request.post('/manu/order', data)
  return res?.data ?? res
}

export async function apiDeleteManuOrder(id) {
  if (USE_PORTAL_MOCK) {
    await mockDelay()
    const list = mockOrders()
    const o = list.find((x) => x.id === Number(id))
    if (o && o.status !== 0) throw { response: { data: { message: '仅草稿状态可删除' } } }
    window.__mockManuOrders = list.filter((x) => x.id !== Number(id))
    return true
  }
  const res = await request.delete(`/manu/order/${id}`)
  return res?.data ?? res
}

export async function apiManuOrderAction(id, action) {
  if (USE_PORTAL_MOCK) {
    await mockDelay()
    const o = mockOrders().find((x) => x.id === Number(id))
    if (!o) throw { response: { data: { message: '单据不存在' } } }
    const now = new Date().toLocaleString('zh-CN', { hour12: false }).replaceAll('/', '-')
    if (action === 'audit') {
      if (o.status !== 0) throw { response: { data: { message: '仅草稿状态可审核' } } }
      o.status = 1
      o.auditBy = 'admin'
      o.auditTime = now
    } else if (action === 'unaudit') {
      if (o.status !== 1) throw { response: { data: { message: '仅已审核状态可弃审' } } }
      o.status = 0
      o.auditBy = null
      o.auditTime = null
    } else if (action === 'close') {
      if (o.status !== 1 && o.status !== 2) throw { response: { data: { message: '仅已审核/生产中状态可关闭' } } }
      o.status = 4
    }
    return o
  }
  const res = await request.post(`/manu/order/${id}/${action}`)
  return res?.data ?? res
}
