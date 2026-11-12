<!-- MpsBoard.vue — 主生产计划（MPS）+ 物料需求汇总（MRP）
     MPS：按计划期把已审核销售订单的「未计划量」收敛成计划行（幂等，已计划的不重复）。
     MRP：选一张计划单 → 逐来源订单走计划分析（BOM 展开 + 库存扣减）→ 按物料汇总缺口与建议。
     视觉：容器挂 .adx，取全局设计库语义层（--d-*）。 -->
<template>
  <div class="adx mps">
    <header class="mps-head">
      <div class="mps-title">
        <h2>主生产计划</h2>
        <span class="mps-sub">
          按计划期汇总销售订单需求：计划行 = 订单量 − 已计划量
          <span class="dot">·</span>物料需求按 BOM 展开并扣库存，给出请购 / 生产 / 委外建议
        </span>
      </div>
      <div class="mps-actions">
        <el-input v-model="period" class="mps-period" placeholder="计划期，如 2026-11" maxlength="7" />
        <el-button type="primary" :loading="generating" @click="generate">从销售订单生成</el-button>
        <el-button @click="loadPlans">刷新</el-button>
      </div>
    </header>

    <section class="mps-metrics">
      <div v-for="m in metrics" :key="m.label" class="mps-metric">
        <span class="mps-metric-label">{{ m.label }}</span>
        <span class="mps-metric-value" :class="m.tone">{{ m.value }}</span>
      </div>
    </section>

    <div class="mps-grid">
      <section class="mps-pane">
        <div class="mps-pane-head">
          <span>计划单</span>
          <span class="mps-hint">点击一行看物料需求</span>
        </div>
        <el-table :data="plans" v-loading="loadingPlans" height="calc(100vh - 420px)" highlight-current-row
                  @current-change="pickPlan">
          <el-table-column prop="单据编号" label="计划编号" width="164" />
          <el-table-column prop="计划期" label="计划期" width="96" />
          <el-table-column prop="单据状态" label="状态" width="90">
            <template #default="{ row }">
              <span class="mps-tag" :class="row['单据状态'] === '已确认' ? 'ok' : ''">{{ row['单据状态'] }}</span>
            </template>
          </el-table-column>
          <el-table-column label="计划行" width="80" align="right">
            <template #default="{ row }"><span class="num">{{ row.__lines ?? '—' }}</span></template>
          </el-table-column>
        </el-table>
      </section>

      <section class="mps-pane">
        <div class="mps-pane-head">
          <span>物料需求{{ current ? ' · ' + current['单据编号'] : '' }}</span>
          <span class="mps-hint" v-if="mrp">缺口合计 {{ qty(mrp['缺口合计']) }}</span>
        </div>
        <el-table :data="mrpRows" v-loading="loadingMrp" height="calc(100vh - 420px)" empty-text="先选一张计划单">
          <el-table-column prop="存货编码" label="编码" width="100" />
          <el-table-column prop="存货名称" label="存货" min-width="130" />
          <el-table-column prop="供应类型" label="属性" width="78" />
          <el-table-column prop="毛需求" label="毛需求" width="92" align="right">
            <template #default="{ row }"><span class="num">{{ qty(row['毛需求']) }}</span></template>
          </el-table-column>
          <el-table-column prop="现存量" label="现存" width="92" align="right">
            <template #default="{ row }"><span class="num">{{ qty(row['现存量']) }}</span></template>
          </el-table-column>
          <el-table-column prop="净需求" label="净需求" width="92" align="right">
            <template #default="{ row }"><span class="num strong">{{ qty(row['净需求']) }}</span></template>
          </el-table-column>
          <el-table-column prop="建议" label="建议" width="92">
            <template #default="{ row }">
              <span class="mps-tag" :class="row['建议'] === '请购' ? 'warn' : 'ok'">{{ row['建议'] }}</span>
            </template>
          </el-table-column>
          <el-table-column label="来源订单" min-width="150">
            <template #default="{ row }">{{ (row['来源订单'] || []).join('、') }}</template>
          </el-table-column>
        </el-table>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@core/request'

const now = new Date()
const period = ref(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`)
const plans = ref([])
const current = ref(null)
const mrp = ref(null)
const loadingPlans = ref(false)
const loadingMrp = ref(false)
const generating = ref(false)

const mrpRows = computed(() => mrp.value?.rows || [])

const metrics = computed(() => {
  const s = mrp.value || {}
  return [
    { label: '计划单', value: plans.value.length },
    { label: '物料行', value: s['物料行'] ?? 0 },
    { label: '需采购', value: s['需采购'] ?? 0, tone: 'warn' },
    { label: '需生产', value: s['需生产'] ?? 0 },
    { label: '需委外', value: s['需委外'] ?? 0 },
  ]
})

function qty(v) {
  const n = Number(v || 0)
  return Number.isInteger(n) ? String(n) : n.toFixed(2)
}

async function loadPlans() {
  loadingPlans.value = true
  try {
    const res = await request.post('/px/queryFormDataList', {
      panelCode: 'MPS_PLAN', pageNo: 1, pageSize: 50,
    })
    const list = res?.data?.list || []
    plans.value = list.map((row) => ({
      ...row,
      __lines: (row.detail?.items || []).length,
    }))
  } finally {
    loadingPlans.value = false
  }
}

async function generate() {
  generating.value = true
  try {
    const res = await request.post('/px/mps/generate', { 计划期: period.value })
    const data = res?.data || {}
    if (!data['新增计划行']) {
      ElMessage.info('没有未计划的需求：订单都已进过主生产计划')
    } else {
      ElMessage.success(`已生成 ${data['新增计划行']} 行计划（${data['编号']}），涉及 ${data['涉及销售订单']} 张订单`)
    }
    await loadPlans()
  } catch (e) {
    ElMessage.error(e?.message || '生成失败')
  } finally {
    generating.value = false
  }
}

async function pickPlan(row) {
  if (!row) return
  current.value = row
  loadingMrp.value = true
  mrp.value = null
  try {
    const res = await request.post('/px/mps/mrp', { 编号: row['单据编号'] })
    mrp.value = res?.data || null
  } catch (e) {
    ElMessage.error(e?.message || '物料需求计算失败')
  } finally {
    loadingMrp.value = false
  }
}

onMounted(loadPlans)
</script>

<style scoped>
.mps {
  padding: 14px 18px 0;
  /* 内容超过视口时撑开页面滚动，而不是把下半截裁掉 */
  min-height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  background: var(--d-bg, #f6f7f8);
}

.mps-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.mps-title h2 {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  color: var(--d-text, #1f2d3d);
}

.mps-sub {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3, #8b9893);
}

.dot { margin: 0 6px; color: var(--d-border-strong, #dce4e1); }

.mps-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex: 0 0 auto;
}

.mps-period { width: 150px; }

.mps-metrics {
  display: flex;
  gap: 10px;
  margin: 12px 0 8px;
}

.mps-metric {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid var(--d-border, #edf1ef);
  border-radius: 6px;
  background: #fff;
}

.mps-metric-label {
  display: block;
  font-size: 12px;
  line-height: 18px;
  color: var(--d-text-3, #8b9893);
}

.mps-metric-value {
  display: block;
  font-size: 18px;
  line-height: 26px;
  font-variant-numeric: tabular-nums;
  color: var(--d-text, #1f2d3d);
}

.mps-metric-value.warn { color: var(--d-warn, #faad14); }

.mps-grid {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(420px, 5fr) minmax(520px, 7fr);
  gap: 12px;
}

.mps-pane {
  border: 1px solid var(--d-border, #edf1ef);
  border-radius: 6px;
  background: #fff;
  padding: 8px 10px 10px;
  min-width: 0;
}

.mps-pane-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 2px 2px 8px;
  font-size: 13px;
  color: var(--d-text, #1f2d3d);
}

.mps-hint {
  font-size: 12px;
  color: var(--d-text-3, #8b9893);
}

.num { font-variant-numeric: tabular-nums; }
.num.strong { color: var(--d-text, #1f2d3d); font-weight: 600; }

.mps-tag {
  display: inline-block;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 20px;
  border: 1px solid var(--d-border-strong, #dce4e1);
  color: var(--d-text-2, #5b6b66);
}

.mps-tag.ok { border-color: #b7eb8f; background: #f6ffed; color: #389e0d; }
.mps-tag.warn { border-color: #ffe58f; background: #fffbe6; color: #d48806; }
</style>
