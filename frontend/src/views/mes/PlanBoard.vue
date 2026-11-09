<!-- PlanBoard.vue — 工单排产
     入口：生产加工单（已审核）→ 排产。按工序看「可排产量」，分批建排产批次。
     规则：第 1 道可排到加工单数量；第 k 道只能排「上道合格数量」的剩余部分（不合格不流入下道）。 -->
<template>
  <div class="plb-page">
    <header class="plb-head">
      <div>
        <h2>{{ moNo ? '工单排产-工序排产 · ' + moNo : '工单排产' }}</h2>
        <span class="plb-sub">{{ moNo ? (productName + '　加工单数量 ' + qty(orderQty) + '　按工序看可排产量并分批排产') : '选择一张已审核的生产加工单开始排产' }}</span>
      </div>
      <el-button size="small" @click="moNo ? back() : $router.back()">{{ moNo ? '换单' : '返回' }}</el-button>
    </header>

    <!-- 未指定加工单：先选单 -->
    <template v-if="!moNo">
      <h3 class="plb-sec">待排产 · 生产中</h3>
      <el-table :data="activeOrders" v-loading="loading" size="small" border height="calc((100vh - 300px) / 2)">
        <el-table-column label="加工单号" prop="编号" width="170" />
        <el-table-column label="单据日期" prop="单据日期" width="110" />
        <el-table-column label="客户" prop="客户" min-width="150" />
        <el-table-column label="产品" min-width="200">
          <template #default="{ row }">{{ productOf(row) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <el-tag size="small" :type="row['单据状态'] === '已完工' ? 'info' : 'success'" effect="light">{{ row['单据状态'] }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="数量" width="100" align="right">
          <template #default="{ row }">{{ qty(qtyOf(row)) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openPlanDialog(row)">排产</el-button>
          </template>
        </el-table-column>
      </el-table>

      <h3 class="plb-sec">已完工 · 追溯</h3>
      <el-table :data="finishedOrders" size="small" border height="calc((100vh - 300px) / 2)">
        <el-table-column label="加工单号" prop="编号" width="170" />
        <el-table-column label="单据日期" prop="单据日期" width="110" />
        <el-table-column label="客户" prop="客户" min-width="150" />
        <el-table-column label="产品" min-width="200">
          <template #default="{ row }">{{ productOf(row) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <el-tag size="small" :type="row['单据状态'] === '已完工' ? 'info' : 'success'" effect="light">{{ row['单据状态'] }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="数量" width="100" align="right">
          <template #default="{ row }">{{ qty(qtyOf(row)) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="pickOrder(row)">查看批次</el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <el-empty v-if="moNo && !loading && !rows.length" description="没有可排产的工序（加工单需先审批通过）" />

    <el-table v-else-if="moNo" :data="rows" v-loading="loading" size="small" border height="calc(100vh - 260px)">
      <el-table-column label="序" prop="工序顺序" width="55" align="center" />
      <el-table-column label="工序" width="120">
        <template #default="{ row }">{{ row['工序名称'] || row['工序编码'] }}</template>
      </el-table-column>
      <el-table-column label="车间" prop="生产车间" width="110" />
      <el-table-column label="计划" width="90" align="right">
        <template #default="{ row }">{{ qty(row['计划数量']) }}</template>
      </el-table-column>
      <el-table-column label="上道合格" width="100" align="right">
        <template #default="{ row }">{{ qty(row['上道合格量']) }}</template>
      </el-table-column>
      <el-table-column label="已排产" width="95" align="right">
        <template #default="{ row }">{{ qty(row['已排产量']) }}</template>
      </el-table-column>
      <el-table-column label="可排产量" width="105" align="right">
        <template #default="{ row }">
          <b :class="Number(row['可排产量']) > 0 ? 'ok' : 'zero'">{{ qty(row['可排产量']) }}</b>
        </template>
      </el-table-column>
      <el-table-column label="已建批次" min-width="260">
        <template #default="{ row }">
          <div v-if="row['批次'] && row['批次'].length" class="batches">
            <el-tag v-for="(b, i) in row['批次']" :key="i" size="small" type="info" class="batch is-clickable" effect="plain" @click="openBatch(b)">
              {{ b['排产单号'] }} · {{ qty(b['数量']) }}
              <span v-if="b['班组']"> · {{ b['班组'] }}</span>
              <span v-if="b['设备']"> · {{ b['设备'] }}</span>
            </el-tag>
          </div>
          <span v-else class="dim">尚未排产</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="Number(row['可排产量']) <= 0 ? 'info' : (Number(row['已排产量']) > 0 ? 'warning' : 'success')" effect="light">
            {{ Number(row['可排产量']) <= 0 ? '已排满' : (Number(row['已排产量']) > 0 ? '部分排产' : '未排产') }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="110" align="center" fixed="right">
        <template #default="{ row }">
          <el-button v-if="Number(row['可排产量']) > 0" link type="primary" size="small" @click="openPlan(row)">排产</el-button>
          <span v-else class="dim">—</span>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="visible" :title="`排产 · ${form.工序顺序 || ''}. ${form.工序名称 || ''}`" width="480px">
      <el-form label-width="96px" size="small">
        <el-form-item label="可排产量">
          <b class="ok">{{ qty(form['可排产量']) }}</b>
          <span class="hint">（上道合格 {{ qty(form['上道合格量']) }} − 已排 {{ qty(form['已排产量']) }}）</span>
        </el-form-item>
        <el-form-item label="本批数量" required>
          <el-input-number v-model="form.数量" :min="0" :max="Number(form['可排产量'])" :step="1" controls-position="right" style="width: 170px" />
        </el-form-item>
        <el-form-item label="班组">
          <el-select v-model="form.班组" clearable filterable placeholder="留空取工序设定" style="width: 220px">
            <el-option v-for="t in teams" :key="t['编号']" :label="t['班组名称']" :value="t['班组名称']" />
          </el-select>
        </el-form-item>
        <el-form-item label="设备">
          <el-select v-model="form.设备" clearable filterable placeholder="留空取工序设定" style="width: 220px">
            <el-option v-for="e in equips" :key="e['编号']" :label="e['设备名称']" :value="e['设备名称']" />
          </el-select>
        </el-form-item>
        <el-form-item label="计划起止">
          <el-date-picker v-model="form.日期" type="daterange" size="small" value-format="YYYY-MM-DD"
                          start-placeholder="预开工" end-placeholder="预完工" style="width: 260px" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="visible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="submitting" @click="submit">生成排产批次</el-button>
      </template>
    </el-dialog>

    <!-- 工序排产弹窗：列表点「排产」直接在这里排，不再跳页 -->
    <el-dialog v-model="planVisible" :title="`工单排产-工序排产 · ${dialogMo || ''}`" width="min(980px, 96vw)" append-to-body>
      <div class="plb-sub" style="margin-bottom: 8px">{{ dialogProduct }}</div>
      <el-table :data="rows" v-loading="loading" size="small" border max-height="420">
        <el-table-column label="序" prop="工序顺序" width="55" align="center" />
        <el-table-column label="工序" width="110">
          <template #default="{ row }">{{ row['工序名称'] || row['工序编码'] }}</template>
        </el-table-column>
        <el-table-column label="车间" prop="生产车间" width="110" />
        <el-table-column label="计划" width="80" align="right">
          <template #default="{ row }">{{ qty(row['计划数量']) }}</template>
        </el-table-column>
        <el-table-column label="上道合格" width="90" align="right">
          <template #default="{ row }">{{ qty(row['上道合格量']) }}</template>
        </el-table-column>
        <el-table-column label="已排产" width="90" align="right">
          <template #default="{ row }">{{ qty(row['已排产量']) }}</template>
        </el-table-column>
        <el-table-column label="可排产量" width="100" align="right">
          <template #default="{ row }"><b class="plb-avail">{{ qty(row['可排产量']) }}</b></template>
        </el-table-column>
        <el-table-column label="已建批次" min-width="240">
          <template #default="{ row }">
            <div v-if="row['批次'] && row['批次'].length" class="batches">
              <el-tag v-for="(b, i) in row['批次']" :key="i" size="small" type="info" class="batch is-clickable" effect="plain" @click="openBatch(b)">{{ b['排产单号'] }} · {{ qty(b['数量']) }}</el-tag>
            </div>
            <span v-else class="dim">尚未排产</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="center">
          <template #default="{ row }">
            <el-button v-if="Number(row['可排产量']) > 0" link type="primary" size="small" @click="openPlan(row)">排产</el-button>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- 批次报工信息：点批次标签查看 -->
    <el-dialog v-model="batchVisible" :title="`批次报工 · ${batchInfo['排产单号'] || ''}`" width="min(720px, 94vw)">
      <div class="b-sum">
        <div class="b-kv"><span>工序</span><b>{{ batchInfo['工序名称'] || batchReports[0]?.['工序名称'] || batchInfo['工序编码'] || '-' }}</b></div>
        <div class="b-kv"><span>批次数量</span><b>{{ qty(batchInfo['数量']) }}</b></div>
        <div class="b-kv"><span>已报工</span><b>{{ qty(batchReported) }}</b></div>
        <div class="b-kv"><span>剩余</span><b>{{ qty(Number(batchInfo['数量'] || 0) - batchReported) }}</b></div>
        <div class="b-kv"><span>班组</span><b>{{ batchInfo['班组'] || '-' }}</b></div>
        <div class="b-kv"><span>设备</span><b>{{ batchInfo['设备'] || '-' }}</b></div>
      </div>
      <el-table :data="batchReports" size="small" border max-height="320" empty-text="该批次还没有报工记录">
        <el-table-column label="报工单号" prop="编号" min-width="150" />
        <el-table-column label="单据日期" prop="单据日期" width="110" />
        <el-table-column label="合格" width="90" align="right">
          <template #default="{ row }">{{ qty(row['合格数量']) }}</template>
        </el-table-column>
        <el-table-column label="不合格" width="90" align="right">
          <template #default="{ row }">{{ qty(row['不合格数量']) }}</template>
        </el-table-column>
        <el-table-column label="报工数量" width="100" align="right">
          <template #default="{ row }">{{ qty(row['报工数量']) }}</template>
        </el-table-column>
        <el-table-column label="状态" prop="单据状态" width="90" />
      </el-table>
      <template #footer>
        <el-button size="small" @click="batchVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { usePanelRuntime } from '@core/panel-runtime'
import { useTabsStore } from '@/stores/tabs'

const engine = usePanelRuntime()
const route = useRoute()
const router = useRouter()
const tabs = useTabsStore()

const moNo = ref(String(route.query.code || route.query.moNo || ''))
const rows = ref([])
const orders = ref([])
const loading = ref(false)
const submitting = ref(false)
const teams = ref([])
const equips = ref([])
async function loadArchives() {
  try {
    const [t, e] = await Promise.all([
      engine.queryFormDataList({ panelCode: 'TEAM', pageNo: 1, pageSize: 200 }),
      engine.queryFormDataList({ panelCode: 'EQUIP', pageNo: 1, pageSize: 200 }),
    ])
    teams.value = (t?.list || []).filter((r) => !r['停用'] && !r['是否停用'])
    equips.value = (e?.list || []).filter((r) => !r['停用'])
  } catch { /* 档案加载失败时仍可手填默认值 */ }
}
const visible = ref(false)
const form = ref({ 数量: 0, 班组: '', 设备: '', 日期: [] })

const batchVisible = ref(false)
const batchInfo = ref({})
const batchReports = ref([])
const batchReported = computed(() => batchReports.value.filter((r) => r['单据状态'] === '已审核').reduce((n, r) => n + Number(r['合格数量'] || 0) + Number(r['不合格数量'] || 0), 0))
async function openBatch(b) {
  batchInfo.value = b || {}
  batchReports.value = []
  batchVisible.value = true
  try {
    batchReports.value = (await engine.batchReports(String(b?.['排产单号'] || ''))) || []
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载报工记录失败')
  }
}
const activeOrders = computed(() => orders.value.filter((r) => ['已审核', '生产中'].includes(r['单据状态'] || '')))
const finishedOrders = computed(() => orders.value.filter((r) => r['单据状态'] === '已完工'))
const orderQty = computed(() => Number(rows.value[0]?.['加工单数量'] || 0))
const productName = computed(() => {
  const r = rows.value[0]
  return r ? `${r['产品名称'] || ''} ${r['规格型号'] || ''}`.trim() : ''
})

function qty(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '0'
  return n === Math.round(n) ? String(n) : n.toFixed(2)
}

function qtyOf(row) {
  const p = (row.detail?.products || [])[0] || {}
  return p['数量'] || 0
}
function productOf(row) {
  const p = (row.detail?.products || [])[0] || {}
  return [p['产品名称'], p['规格型号']].filter(Boolean).join(' ') || '—'
}
const planVisible = ref(false)
const dialogMo = ref('')
const dialogProduct = ref('')
async function openPlanDialog(row) {
  dialogMo.value = row['编号'] || ''
  dialogProduct.value = productOf(row) + '　加工单数量 ' + qty(qtyOf(row))
  planVisible.value = true
  rows.value = (await engine.planBoard(dialogMo.value)) || []
}
function pickOrder(row) {
  const no = row['编号']
  moNo.value = no
  const path = '/mes/plan'
  const query = { code: no }
  router.replace({ path, query })
  tabs.open({ path, title: `工单排产-${no}`, query })
  load()
}
function back() {
  moNo.value = ''
  router.replace({ path: '/mes/plan' })
  load()
}

async function load() {
  loading.value = true
  try {
    if (!moNo.value) {
      // 审批通过后状态会推进到「生产中」，这类单仍要能继续排后续工序
      const res = await engine.queryFormDataList({ panelCode: 'MANU_ORDER', pageNo: 1, pageSize: 200 })
      // 已完工的单也要能看到（追溯已排批次），排产按钮打开后只读展示已有批次
      orders.value = (res?.list || []).filter((r) => ['已审核', '生产中', '已完工'].includes(r['单据状态'] || ''))
      rows.value = []
    } else {
      rows.value = (await engine.planBoard(moNo.value)) || []
    }
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载排产数据失败')
  } finally {
    loading.value = false
  }
}

function openPlan(row) {
  form.value = { ...row, 数量: Number(row['可排产量']), 班组: '', 设备: '', 日期: [] }
  visible.value = true
}

async function submit() {
  const q = Number(form.value.数量)
  if (!(q > 0)) return ElMessage.warning('排产数量必须大于 0')
  submitting.value = true
  try {
    const res = await engine.planBatch({
      manuOrderNo: moNo.value,
      工序编码: form.value['工序编码'],
      数量: q,
      班组: form.value.班组,
      设备: form.value.设备,
      预开工日: form.value.日期?.[0] || '',
      预完工日: form.value.日期?.[1] || '',
    })
    const no = res?.['编号'] || ''
    ElMessage.success(`已生成排产批次 ${no}（草稿），审批通过后占用排产额度`)
    visible.value = false
    await load()
    if (no) {
      const path = '/panelx/list/DISPATCH'
      const query = { code: no }
      router.push({ path, query })
      tabs.open({ path, title: `工序派工单-${no}`, query })
    }
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '排产失败')
  } finally {
    submitting.value = false
  }
}

onMounted(() => { load(); loadArchives() })
</script>

<style scoped>
.plb-page { padding: 16px 24px; height: 100%; display: flex; flex-direction: column; gap: 8px; background: #fff; color: #1f1f1f; font-size: 14px; line-height: 22px; }
.plb-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding-bottom: 12px; border-bottom: 1px solid #f0f0f0; }
.plb-head h2 { margin: 0; font-size: 16px; line-height: 24px; font-weight: 600; }
.plb-sub { display: block; margin-top: 2px; font-size: 12px; line-height: 20px; color: #8c8c8c; }
.plb-sec { margin: 16px 0 8px; padding-left: 8px; border-left: 2px solid #1677ff; font-size: 13px; line-height: 20px; font-weight: 600; color: #1f1f1f; }
.batches { display: flex; flex-wrap: wrap; gap: 4px; }
.batch { font-variant-numeric: tabular-nums; }
.batch.is-clickable { cursor: pointer; }
.batch.is-clickable:hover { color: #1677ff; border-color: #91caff; background: #f0f7ff; }
.b-sum { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 16px; margin-bottom: 16px; }
.b-kv { display: flex; justify-content: space-between; gap: 8px; font-size: 13px; line-height: 20px; color: #595959; padding: 6px 10px; border: 1px solid #f0f0f0; border-radius: 6px; }
.b-kv b { color: #1f1f1f; font-weight: 600; font-variant-numeric: tabular-nums; }
.ok { color: #1677ff; font-variant-numeric: tabular-nums; }
.plb-avail { color: #1677ff; font-weight: 600; font-variant-numeric: tabular-nums; }
.dim { color: #bfbfbf; }
:deep(.el-table) { font-size: 13px; }
:deep(.el-table th.el-table__cell) { background: #fafafa; color: #595959; font-weight: 500; }
:deep(.el-table .cell) { font-variant-numeric: tabular-nums; }
</style>
