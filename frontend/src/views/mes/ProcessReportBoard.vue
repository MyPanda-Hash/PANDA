<!-- ProcessReportBoard.vue — 工序报工工作台
     一行一道工序（不是整张加工单倒进来）。放行规则 B：本道有剩余量，且前一道已有报工即可开工，
     允许工序重叠生产。报工只生成「只含该工序一行」的工序汇报单，避免整单被某道已报满的工序卡住。 -->
<template>
  <div class="rp-page">
    <header class="rp-head">
      <div>
        <h2>工序报工</h2>
        <span class="rp-sub">按排产批次报工：必须先排产并审批，累计报工不超过批次数量</span>
      </div>
      <div class="rp-actions">
        <el-radio-group v-model="scope" size="small">
          <el-radio-button label="todo">可报工</el-radio-button>
          <el-radio-button label="all">全部</el-radio-button>
        </el-radio-group>
        <el-button size="small" @click="load">刷新</el-button>
      </div>
    </header>

    <section class="rp-kpis">
      <div class="kpi"><span class="kpi-label">可报工序</span><b class="kpi-value">{{ kpi.todo }}</b><span class="kpi-hint">共 {{ kpi.total }} 个批次</span></div>
      <div class="kpi"><span class="kpi-label">涉及批次</span><b class="kpi-value">{{ kpi.orders }}</b><span class="kpi-hint">在制</span></div>
      <div class="kpi"><span class="kpi-label">已报满</span><b class="kpi-value">{{ kpi.done }}</b><span class="kpi-hint">剩余为 0</span></div>
      <div class="kpi"><span class="kpi-label">等待前置</span><b class="kpi-value">{{ kpi.blocked }}</b><span class="kpi-hint">前道尚未报工</span></div>
    </section>

    <div class="rp-bar">
      <el-select v-model="workshop" size="small" clearable placeholder="全部车间" style="width: 150px">
        <el-option v-for="w in workshops" :key="w" :label="w" :value="w" />
      </el-select>
      <el-input v-model="keyword" size="small" clearable style="width: 260px" placeholder="搜索加工单号 / 产品 / 工序" />
    </div>

    <el-table :data="filtered" v-loading="loading" size="small" border height="calc(100vh - 360px)">
      <el-table-column label="排产单号" prop="排产单号" width="150" />
      <el-table-column label="加工单号" prop="加工单号" width="150" />
      <el-table-column label="产品" width="170" show-overflow-tooltip>
        <template #default="{ row }">{{ row['产品名称'] || row['产品编码'] || '—' }}</template>
      </el-table-column>
      <el-table-column label="工序" width="110">
        <template #default="{ row }">{{ row['工序名称'] || row['工序编码'] }}</template>
      </el-table-column>
      <el-table-column label="班组/设备" width="150" show-overflow-tooltip>
        <template #default="{ row }">{{ [row['班组'], row['设备']].filter(Boolean).join(' / ') || '—' }}</template>
      </el-table-column>
      <el-table-column label="批次数量" width="100" align="right">
        <template #default="{ row }">{{ qty(row['批次数量']) }}</template>
      </el-table-column>
      <el-table-column label="已报" width="90" align="right">
        <template #default="{ row }">{{ qty(row['已报数量']) }}</template>
      </el-table-column>
      <el-table-column label="剩余" width="90" align="right">
        <template #default="{ row }">
          <b :class="Number(row['剩余数量']) > 0 ? 'rest' : 'rest-zero'">{{ qty(row['剩余数量']) }}</b>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="120" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="row['可否报工'] ? 'success' : 'info'">
            {{ row['可否报工'] ? '可报工' : (row['不可报工原因'] || '不可报工') }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="110" align="center">
        <template #default="{ row }">
          <el-button v-if="row['可否报工']" link type="primary" size="small" @click="openReport(row)">报工</el-button>
          <span v-else class="dim">—</span>
        </template>
      </el-table-column>
    </el-table>

    <!-- 报工弹窗：只填数量，其余由后端按该工序生成 -->
    <el-dialog v-model="dialogVisible" :title="`报工 · ${form.工序名称}（${form.排产单号}）`" width="460px">
      <el-form label-width="96px" size="small">
        <el-form-item label="工序">
          <span>{{ form.工序顺序 }}. {{ form.工序名称 }}</span>
        </el-form-item>
        <el-form-item label="批次 / 已报">
          <span>{{ qty(form.批次数量) }} / {{ qty(form.已报数量) }}</span>
        </el-form-item>
        <el-form-item label="剩余可报">
          <b class="rest">{{ qty(form.剩余数量) }}</b>
        </el-form-item>
        <el-form-item label="合格数量" required>
          <el-input-number v-model="form.合格数量" :min="0" :max="Number(form.剩余数量)" :step="1" controls-position="right" style="width: 170px" />
        </el-form-item>
        <el-form-item label="不合格数量">
          <el-input-number v-model="form.不合格数量" :min="0" :max="Number(form.剩余数量)" :step="1" controls-position="right" style="width: 170px" />
        </el-form-item>
        <el-form-item label="报工合计">
          <b>{{ qty(Number(form.合格数量 || 0) + Number(form.不合格数量 || 0)) }}</b>
          <span class="hint">= 合格 + 不合格，不得超过剩余 {{ qty(form.剩余数量) }}</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="dialogVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="submitting" @click="submit">确认报工</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { usePanelRuntime } from '@core/panel-runtime'
import { useTabsStore } from '@/stores/tabs'

const engine = usePanelRuntime()
const router = useRouter()
const tabs = useTabsStore()

const rows = ref([])
const loading = ref(false)
const submitting = ref(false)
const scope = ref('todo')
const workshop = ref('')
const keyword = ref('')
const dialogVisible = ref(false)
const form = ref({ 加工单号: '', 工序编码: '', 工序名称: '', 工序顺序: 1, 计划数量: 0, 已报数量: 0, 剩余数量: 0, 本次数量: 0 })

const workshops = computed(() => [...new Set(rows.value.map((r) => r['生产车间']).filter(Boolean))])

const filtered = computed(() => {
  let list = rows.value
  if (scope.value === 'todo') list = list.filter((r) => r['可否报工'])
  if (workshop.value) list = list.filter((r) => r['生产车间'] === workshop.value)
  const kw = keyword.value.trim()
  if (kw) {
    list = list.filter((r) =>
      [r['加工单号'], r['产品名称'], r['工序名称'], r['产品编码']].some((v) => String(v || '').includes(kw))
    )
  }
  return list
})

const kpi = computed(() => {
  const all = rows.value
  return {
    total: all.length,
    todo: all.filter((r) => r['可否报工']).length,
    orders: new Set(all.map((r) => r['加工单号'])).size,
    done: all.filter((r) => Number(r['剩余数量']) <= 0).length,
    blocked: all.filter((r) => !r['可否报工'] && Number(r['剩余数量']) > 0).length,
  }
})

function qty(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '0'
  return n === Math.round(n) ? String(n) : n.toFixed(2)
}

function openReport(row) {
  form.value = { ...row, 合格数量: Number(row['剩余数量']), 不合格数量: 0 }
  dialogVisible.value = true
}

async function submit() {
  const qualified = Number(form.value.合格数量 || 0)
  const rejected = Number(form.value.不合格数量 || 0)
  const q = qualified + rejected
  if (!(q > 0)) return ElMessage.warning('报工数量必须大于 0（合格 + 不合格）')
  if (q > Number(form.value['剩余数量']) + 1e-6) return ElMessage.warning('超过批次剩余可报数量')
  submitting.value = true
  try {
    const res = await engine.reportByBatch(form.value['排产单号'], qualified, rejected)
    const no = res?.['编号'] || ''
    ElMessage.success(`已生成工序汇报单 ${no}（草稿），请提交审批`)
    dialogVisible.value = false
    await load()
    if (no) {
      const path = '/panelx/list/PROCESS_REPORT'
      const query = { code: no }
      router.push({ path, query })
      tabs.open({ path, title: `工序汇报单-${no}`, query })
    }
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '报工失败')
  } finally {
    submitting.value = false
  }
}

async function load() {
  loading.value = true
  try {
    rows.value = (await engine.reportBatchBoard()) || []
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载报工数据失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.rp-page { padding: 12px 16px; height: 100%; display: flex; flex-direction: column; background: #fff; }
.rp-head { display: flex; align-items: flex-end; justify-content: space-between; padding-bottom: 10px; border-bottom: 1px solid #f0f0f0; }
.rp-head h2 { margin: 0; font-size: 16px; font-weight: 600; color: #1f1f1f; }
.rp-sub { display: block; margin-top: 2px; font-size: 12px; color: #8c8c8c; }
.rp-actions { display: flex; gap: 8px; align-items: center; }
.rp-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 12px 0; }
.kpi { border: 1px solid #e8e8e8; border-radius: 6px; padding: 10px 12px; display: flex; flex-direction: column; gap: 2px; }
.kpi-label { font-size: 12px; color: #8c8c8c; }
.kpi-value { font-size: 22px; line-height: 30px; font-weight: 600; font-variant-numeric: tabular-nums; }
.kpi-hint { font-size: 12px; color: #8c8c8c; }
.rp-bar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; }
.rest { color: #1677ff; }
.rest-zero { color: #bfbfbf; }
.dim { color: #bfbfbf; }
.hint { margin-left: 10px; font-size: 12px; color: #8c8c8c; }
</style>