<template>
  <div class="dashboard">
    <div class="welcome card">
      <div>
        <div class="hello">{{ greeting }}，{{ user.realName }}！</div>
        <div class="meta">工厂：{{ user.factoryName }} · {{ today }} · 班次：白班</div>
      </div>
      <div class="quick" v-if="quickEntries.length">
        <el-button
          v-for="q in quickEntries"
          :key="q.key"
          :type="q.key === 'newOrder' ? 'primary' : 'default'"
          @click="go(q.path, q.title)"
        >{{ q.title }}</el-button>
      </div>
    </div>

    <el-row :gutter="12" v-if="desk.showKpi">
      <el-col :span="6" v-for="k in kpis" :key="k.title">
        <div class="card kpi">
          <div class="kpi-icon" :style="{ background: k.color }"><el-icon :size="22"><component :is="k.icon" /></el-icon></div>
          <div>
            <div class="kpi-value">{{ k.value }}</div>
            <div class="kpi-title">{{ k.title }}</div>
          </div>
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="12" class="mt" v-if="desk.showProgress || desk.showTodo">
      <el-col :span="desk.showTodo ? 14 : 24" v-if="desk.showProgress">
        <div class="card">
          <div class="card-title">生产进度</div>
          <el-table :data="orders" size="small">
            <el-table-column prop="orderNo" label="工单号" width="150" />
            <el-table-column prop="product" label="产品" />
            <el-table-column prop="qty" label="数量" width="80" />
            <el-table-column prop="done" label="已完成" width="80" />
            <el-table-column label="进度" width="160">
              <template #default="{ row }">
                <el-progress :percentage="row.percent" :stroke-width="10" />
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="90" />
          </el-table>
        </div>
      </el-col>
      <el-col :span="desk.showProgress ? 10 : 24" v-if="desk.showTodo">
        <div class="card">
          <div class="card-title">我的待办</div>
          <el-timeline style="padding: 8px 4px">
            <el-timeline-item v-for="t in todos" :key="t.id" :timestamp="t.time" :type="t.type">{{ t.text }}</el-timeline-item>
          </el-timeline>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useTabsStore } from '@/stores/tabs'
import { useAppStore } from '@/stores/app'

const user = useUserStore()
const tabs = useTabsStore()
const app = useAppStore()
const router = useRouter()

const desk = computed(() => app.deskSettings)

const QUICK_DEFS = [
  { key: 'newOrder', title: '新建加工单', path: '/panelx/form/MANU_ORDER' },
  { key: 'quickReport', title: '快速报工', path: '/prod/shop/procReport' },
  { key: 'board', title: '生产看板', path: '/prod/manufacture/board' },
]

const quickEntries = computed(() => QUICK_DEFS.filter((q) => desk.value.quick.includes(q.key)))

const today = new Date().toLocaleDateString('zh-CN')
const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return '凌晨好'
  if (h < 12) return '上午好'
  if (h < 18) return '下午好'
  return '晚上好'
})

const kpis = [
  { title: '进行中工单', value: 12, icon: 'Odometer', color: '#e8f1ff' },
  { title: '今日报工数', value: 328, icon: 'EditPen', color: '#e7f9ef' },
  { title: '设备稼动率', value: '86%', icon: 'Cpu', color: '#fff6e8' },
  { title: '一次合格率', value: '98.2%', icon: 'Aim', color: '#fdeef0' },
]

const orders = [
  { orderNo: 'MO20260813-001', product: '减速箱体 A', qty: 200, done: 140, percent: 70, status: '生产中' },
  { orderNo: 'MO20260813-002', product: '法兰盘 B', qty: 500, done: 500, percent: 100, status: '已完工' },
  { orderNo: 'MO20260812-015', product: '轴套 C', qty: 300, done: 60, percent: 20, status: '生产中' },
]

const todos = [
  { id: 1, text: '工单 MO20260813-003 待审核', time: '10:30', type: 'primary' },
  { id: 2, text: '领料单 LL20260813-007 待审批', time: '09:45', type: 'warning' },
  { id: 3, text: '设备 EQ-03 点检到期提醒', time: '昨天', type: 'danger' },
]

function go(path, title) {
  router.push(path)
  tabs.open({ path, title })
}
</script>

<style scoped>
.card {
  background: #fff;
  border-radius: 10px;
  padding: 16px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.05);
}
.dark .card {
  background: #26272e;
}
.welcome {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  background: linear-gradient(120deg, #2563eb, #3b82f6);
  color: #fff;
}
.hello {
  font-size: 18px;
  font-weight: 600;
}
.meta {
  font-size: 12px;
  opacity: 0.85;
  margin-top: 4px;
}
.quick .el-button {
  margin-left: 8px;
}
.kpi {
  display: flex;
  align-items: center;
  gap: 12px;
}
.kpi-icon {
  width: 46px;
  height: 46px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #2563eb;
}
.kpi-value {
  font-size: 22px;
  font-weight: 700;
}
.kpi-title {
  font-size: 12px;
  color: #9ca3af;
}
.mt {
  margin-top: 12px;
}
.card-title {
  font-weight: 600;
  margin-bottom: 10px;
}
</style>
