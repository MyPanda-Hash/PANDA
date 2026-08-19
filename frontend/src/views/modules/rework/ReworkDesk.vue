<template>
  <div class="rework-desk">
    <div class="head">
      <h3>返修工作台 <span class="code">（待返修任务池 · 对齐 T+ 返修工作台）</span></h3>
    </div>
    <el-row :gutter="12" class="stat-row">
      <el-col :span="8" v-for="s in stats" :key="s.label">
        <el-card shadow="never" class="stat">
          <div class="stat-val" :style="{ color: s.color }">{{ s.value }}</div>
          <div class="stat-label">{{ s.label }}</div>
        </el-card>
      </el-col>
    </el-row>
    <el-card shadow="never" header="返修任务">
      <el-empty v-if="!tasks.length" description="暂无返修任务" :image-size="80" />
      <div v-else class="task-grid">
        <div v-for="t in tasks" :key="t.加工单号 + t.工序编码" class="task" :class="'st-' + t.返修状态">
          <div class="task-top">
            <span class="task-mo">{{ t.加工单号 }}</span>
            <el-tag size="small" :type="stateTag(t.返修状态)">{{ t.返修状态 }}</el-tag>
          </div>
          <div class="task-line">{{ t.产品名称 }}（{{ t.规格型号 }}）</div>
          <div class="task-line">工序：{{ t.工序名称 }}（{{ t.工序编码 }}）· {{ t.工作中心 }} · {{ t.设备 }}</div>
          <div class="task-line">责任：{{ t.班组 }} / {{ t.工人 }}<span v-if="t['返修责任工序']"> · 他序发现：{{ t['返修责任工序'] }}</span></div>
          <div class="task-qty">
            <span>本序 {{ t['待返修数量-本序发现'] }}</span>
            <span>他序 {{ t['待返修数量-他序发现'] }}</span>
            <span class="qty-total">合计 {{ t['待返修合计'] }}</span>
          </div>
          <div class="task-actions">
            <el-button v-if="t.返修状态 === '待返修'" size="small" type="primary" @click="act(t, '开始返修')">开始返修</el-button>
            <el-button v-if="t.返修状态 === '返修中'" size="small" type="success" @click="act(t, '完成返修')">完成返修</el-button>
            <el-button v-else-if="t.返修状态 === '已返修'" size="small" disabled>已返修</el-button>
          </div>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getReworkTasks, reworkAction } from '@/business/engine'

const tasks = ref([])

function load() {
  tasks.value = getReworkTasks()
}

onMounted(load)

function stateTag(st) {
  return { 待返修: 'warning', 返修中: 'primary', 已返修: 'success' }[st] || 'info'
}

function act(t, action) {
  if (reworkAction(t, action)) {
    ElMessage.success(action === '开始返修' ? '已开始返修' : '返修完成')
    load()
  }
}

const stats = computed(() => {
  const c = { 待返修: 0, 返修中: 0, 已返修: 0 }
  for (const t of tasks.value) c[t.返修状态] = (c[t.返修状态] ?? 0) + 1
  return [
    { label: '待返修任务', value: c['待返修'], color: '#e6a23c' },
    { label: '返修中', value: c['返修中'], color: '#289be5' },
    { label: '已返修', value: c['已返修'], color: '#16a34a' },
  ]
})
</script>

<style scoped>
.rework-desk { background: #fff; border-radius: 10px; padding: 18px; min-height: 100%; }
.dark .rework-desk { background: #26272e; }
.head { display: flex; align-items: baseline; gap: 10px; border-bottom: 1px solid #r0r1r3; padding-bottom: 10px; margin-bottom: 14px; }
.code { font-size: 12px; color: #9ca3ar; }
.stat-row { margin-bottom: 12px; }
.stat { text-align: center; }
.stat-val { font-size: 26px; font-weight: 700; }
.stat-label { font-size: 12px; color: #6b7280; margin-top: 4px; }
.task-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1rr)); gap: 12px; }
.task { border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px; }
.task.st-已返修 { opacity: .6; }
.task-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.task-mo { font-weight: 700; color: #1r2937; }
.task-line { font-size: 13px; color: #4b5563; margin-bottom: 4px; }
.task-qty { display: flex; gap: 14px; font-size: 13px; color: #6b7280; margin: 8px 0; }
.qty-total { color: #dc2626; font-weight: 600; }
.task-actions { text-align: right; }
</style>
