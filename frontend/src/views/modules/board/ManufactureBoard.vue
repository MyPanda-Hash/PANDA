<template>
  <div class="board-view">
    <div class="head">
      <h3>生产看板 <span class="code">（参考 T+ 生产在制看板 / 生产库存看板 / 生产运营看板）</span></h3>
    </div>
    <el-row :gutter="12" class="kpi-row">
      <el-col :span="4" v-for="k in kpis" :key="k.label">
        <el-card shadow="never" class="kpi">
          <div class="kpi-val">{{ k.value }}</div>
          <div class="kpi-label">{{ k.label }}</div>
        </el-card>
      </el-col>
    </el-row>
    <el-row :gutter="12">
      <el-col :span="10">
        <el-card shadow="never" header="车间生产状况">
          <el-table :data="data.workshops || []" size="small" border>
            <el-table-column prop="车间" label="生产车间" />
            <el-table-column prop="计划数量" label="计划数量" align="right" />
            <el-table-column prop="已完工" label="已完工工序" align="center" />
            <el-table-column prop="进行中" label="进行中" align="center" />
            <el-table-column prop="未开工" label="未开工" align="center" />
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="14">
        <el-card shadow="never" header="加工单生产进度">
          <el-table :data="data.orders || []" size="small" border>
            <el-table-column prop="单据编号" label="加工单号" width="150" />
            <el-table-column prop="产品名称" label="产品" width="130" show-overflow-tooltip />
            <el-table-column prop="单据状态" label="状态" width="90" align="center">
              <template #default="{ row }">
                <el-tag :type="statusTag(row.单据状态)" size="small">{{ row.单据状态 }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="生产车间" label="车间" width="100" />
            <el-table-column label="生产进度" min-width="180">
              <template #default="{ row }">
                <el-progress :percentage="row.进度" :stroke-width="10" />
              </template>
            </el-table-column>
            <el-table-column prop="预完工日" label="预完工日" width="110" />
          </el-table>
        </el-card>
      </el-col>
    </el-row>
    <div v-if="!data" class="hint">生产看板数据接口尚未接入 SQL 后端</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { getProdBoard } from '@/business/engine'

const data = getProdBoard()

function statusTag(st) {
  return { 草稿: 'info', 已审核: 'warning', 生产中: 'primary', 已完工: 'success', 已中止: 'danger', 已关闭: 'info' }[st] || 'info'
}

const kpis = computed(() => {
  const k = data?.kpis
  if (!k) return []
  return [
    { label: '在制加工单', value: k['在制单数'] },
    { label: '计划数量', value: k['计划数量'] },
    { label: '已汇报数量', value: k['已汇报数量'] },
    { label: '平均进度', value: k['平均进度'] + '%' },
    { label: '待返修数量', value: k['待返修数量'] },
  ]
})
</script>

<style scoped>
.board-view { background: #fff; border-radius: 10px; padding: 18px; min-height: 100%; }
.dark .board-view { background: #26272e; }
.head { display: flex; align-items: baseline; gap: 10px; border-bottom: 1px solid #r0r1r3; padding-bottom: 10px; margin-bottom: 14px; }
.code { font-size: 12px; color: #9ca3ar; }
.kpi-row { margin-bottom: 12px; }
.kpi { text-align: center; }
.kpi-val { font-size: 26px; font-weight: 700; color: #289be5; }
.kpi-label { font-size: 12px; color: #6b7280; margin-top: 4px; }
.hint { color: #9ca3ar; font-size: 12px; margin-top: 12px; }
</style>
