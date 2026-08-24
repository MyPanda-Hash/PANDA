import { createRouter, createWebHashHistory } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { flatMenus } from '@/business/menus'

const ModuleView = () => import('@/views/modules/ModuleView.vue')
const PanelxList = () => import('@core/views/PanelxList.vue')
const PanelxForm = () => import('@core/views/PanelxForm.vue')

const routes = [
  { path: '/login', component: () => import('@/views/login/index.vue'), meta: { title: '登录' } },
  {
    path: '/',
    component: () => import('@/layout/PortalLayout.vue'),
    redirect: '/dashboard',
    children: [
      { path: 'dashboard', component: () => import('@/views/dashboard/index.vue'), meta: { title: '我的桌面' } },
      { path: 'prod/manufacture/order', redirect: '/panelx/list/MANU_ORDER' },
      { path: 'prod/manufacture/orderForm', redirect: '/panelx/form/MANU_ORDER' },
      { path: 'prod/manufacture/board', component: () => import('@/views/modules/board/ManufactureBoard.vue'), meta: { title: '生产看板', code: 'manufactureBoard' } },
      { path: 'prod/shop/reworkDesk', component: () => import('@/views/modules/rework/ReworkDesk.vue'), meta: { title: '返修工作台', code: 'reworkDesk' } },
      { path: 'top/solution', component: () => import('@/views/modules/solution/SolutionCenter.vue'), meta: { title: '方案中心', code: 'solutionCenter' } },
      { path: 'sys/org', component: () => import('@/views/sys/OrgAdmin.vue'), meta: { title: '组织架构', requireAdmin: true } },
      { path: 'scm/businessOverview', component: () => import('@/views/scm/BusinessOverview.vue'), meta: { title: '业务总览', code: 'businessOverview' } },
      { path: 'panelx/list/:panelCode', component: PanelxList, meta: { title: '单据', operationName: '新增流程' } },
      { path: 'panelx/form/:panelCode', component: PanelxForm, meta: { title: '表单' } },
      ...flatMenus()
        .filter((m) => m.path && m.path !== '/dashboard' && m.code !== 'manufactureOrder' && m.code !== 'manufactureBoard' && m.code !== 'reworkDesk' && m.code !== 'solutionCenter' && !m.panelCode)
        .map((m) => ({
          path: m.path.slice(1),
          component: ModuleView,
          meta: { title: m.title, code: m.code },
        })),
    ],
  },
  { path: '/:pathMatch(.*)*', component: () => import('@/views/error/404.vue'), meta: { title: '404' } },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach((to) => {
  document.title = `${to.meta.title || ''} · 轻MES`
  const user = useUserStore()
  if (to.path !== '/login' && !user.isLogin) return '/login'
  if (to.meta.requireAdmin && !user.isAdmin) return '/dashboard'
  if (to.path === '/login' && user.isLogin) return '/dashboard'
  return true
})

export default router
