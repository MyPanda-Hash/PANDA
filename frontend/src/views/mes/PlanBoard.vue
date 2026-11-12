<!-- PlanBoard.vue — 工单排产
     两个入口：
     ① 销售订单 → 排产 → 生成生产加工单（单号 = 销售订单号-行号，一行一张单，生成即已审核）
     ② 生产加工单 → 按工序排批次（预生产/补排）。第 1 道可排到加工单数量，第 k 道只排「上道合格」的剩余部分。 -->
<template>
  <div class="plb-page">
    <header class="plb-head">
      <div>
        <h2>{{ pageTitle }}</h2>
        <span class="plb-sub">{{ pageSub }}</span>
      </div>
      <div class="plb-head-actions">
        <el-button
          v-if="soNo"
          type="primary"
          size="small"
          :loading="generating"
          :disabled="!selectedLines.length"
          @click="doPlanFromSo"
        >
          生成加工单{{ selectedLines.length ? '（' + selectedLines.length + ' 行）' : '' }}
        </el-button>
        <el-button
          v-else-if="moNo"
          type="primary"
          size="small"
          :loading="planningAll"
          :disabled="!canPlanAll"
          @click="doPlanAll"
        >
          一键排产{{ pendingSteps ? '（' + pendingSteps + ' 道）' : '' }}
        </el-button>
        <el-button size="small" @click="soNo || moNo ? back() : $router.back()">{{ soNo || moNo ? '换单' : '返回' }}</el-button>
      </div>
    </header>

    <!-- 订单排产：勾订单行 → 生成加工单 -->
    <template v-if="soNo">
      <el-table :data="soRows" v-loading="soLoading" size="small" border height="calc(100vh - 260px)">
        <el-table-column width="52" align="center">
          <template #header>选</template>
          <template #default="{ row }">
            <el-checkbox v-model="row.选中" :disabled="Number(row['可排数量']) <= 0" />
          </template>
        </el-table-column>
        <el-table-column label="行号" prop="行号" width="60" align="center" />
        <el-table-column label="产品编码" prop="存货编码" width="110" />
        <el-table-column label="产品名称" prop="存货名称" min-width="150" />
        <el-table-column label="规格型号" prop="规格型号" min-width="140" show-overflow-tooltip />
        <el-table-column label="订单数量" width="96" align="right">
          <template #default="{ row }">{{ qty(row['订单数量']) }}</template>
        </el-table-column>
        <el-table-column label="已排数量" width="96" align="right">
          <template #default="{ row }">{{ qty(row['已排数量']) }}</template>
        </el-table-column>
        <el-table-column label="可排数量" width="96" align="right">
          <template #default="{ row }">
            <b :class="Number(row['可排数量']) > 0 ? 'ok' : 'zero'">{{ qty(row['可排数量']) }}</b>
          </template>
        </el-table-column>
        <el-table-column label="工序数" width="80" align="center">
          <template #default="{ row }">{{ row['工序数'] || 0 }} 道</template>
        </el-table-column>
        <el-table-column label="本批数量" width="130" align="center">
          <template #default="{ row }">
            <el-input-number
              v-model="row.本批数量"
              :min="0"
              :max="Number(row['可排数量'])"
              :step="1"
              size="small"
              controls-position="right"
              :disabled="Number(row['可排数量']) <= 0"
              style="width: 104px"
            />
          </template>
        </el-table-column>
        <el-table-column label="加工单号" prop="加工单号" min-width="180">
          <template #default="{ row }"><span class="mono">{{ row['加工单号'] }}</span></template>
        </el-table-column>
      </el-table>
      <p class="plb-tip">勾选要生产的订单行，数量可小于订单数量（分批投产）。生成的是已审核加工单，可直接领料、报工；下道工序报工后再回来补排批次。</p>
    </template>

    <!-- 未指定单据：先选销售订单，或按加工单补排 -->
    <template v-else-if="!moNo">
      <h3 class="plb-sec">待排产销售订单</h3>
      <el-table :data="soOrders" v-loading="loading" size="small" border height="calc((100vh - 320px) / 2)">
        <el-table-column label="销售订单号" prop="编号" width="170" />
        <el-table-column label="单据日期" prop="单据日期" width="110" />
        <el-table-column label="客户" prop="客户" min-width="160" />
        <el-table-column label="产品" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">{{ saleProductsText(row) }}</template>
        </el-table-column>
        <el-table-column label="订单数量" width="100" align="right">
          <template #default="{ row }">{{ qty(saleQtyOf(row)) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openSaleOrder(row)">排产</el-button>
          </template>
        </el-table-column>
      </el-table>

      <h3 class="plb-sec">按加工单排产 · 预生产/补排</h3>
      <el-table :data="activeOrders" size="small" border height="calc((100vh - 320px) / 2)">
        <el-table-column label="加工单号" prop="编号" width="170" />
        <el-table-column label="单据日期" prop="单据日期" width="110" />
        <el-table-column label="客户" prop="客户" min-width="150" />
        <el-table-column label="产品" min-width="240" show-overflow-tooltip>
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
    </template>

    <el-empty v-if="moNo && !loading && !rows.length" description="没有可排产的工序（加工单需先审批通过）" />

    <!-- 按产品分区：一件产品一块，与排产弹窗共用同一份渲染 -->
    <div v-if="moNo && !soNo" v-loading="loading">
      <PlanProductGroups v-if="rows.length" :groups="productGroups" @plan="(r) => openPlan(r, moNo)" @batch="openBatch" />
    </div>

    <el-dialog v-model="visible" :title="`排产 · ${form['产品编码'] || ''} ${form.工序顺序 || ''}. ${form.工序名称 || ''}`" width="480px">
      <el-form label-width="96px" size="small">
        <el-form-item label="产品">
          <span>{{ form['产品编码'] }} {{ form['产品名称'] }}</span>
        </el-form-item>
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
            <el-option v-for="e in equips" :key="e['编号'] || e['设备编码']" :label="equipLabel(e)" :value="e['设备名称']" />
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

    <!-- 工序排产弹窗：列表点「排产」直接在这里排，不再跳页。
         排版与独立页一致（按产品分区 + 一键排产），否则两个入口看到的是两套东西。 -->
    <el-dialog v-model="planVisible" width="min(1180px, 96vw)" append-to-body>
      <template #header>
        <div class="plb-dlg-head">
          <div>
            <span class="plb-dlg-title">工单排产-工序排产 · {{ dialogMo }}</span>
            <span class="plb-sub">{{ dialogSummary }}</span>
          </div>
          <el-button
            type="primary"
            size="small"
            :loading="planningAll"
            :disabled="!dialogCanPlanAll"
            @click="doPlanAll(dialogMo)"
          >
            一键排产{{ dialogPending ? '（' + dialogPending + ' 道）' : '' }}
          </el-button>
        </div>
      </template>
      <el-empty v-if="!dialogLoading && !dialogRows.length" description="没有可排产的工序（加工单需先审批通过）" />
      <div v-else v-loading="dialogLoading" class="plb-dlg-body">
        <PlanProductGroups
          :groups="dialogGroups"
          @plan="(r) => openPlan(r, dialogMo)"
          @batch="openBatch"
        />
      </div>
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
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePanelRuntime } from '@core/panel-runtime'
import { useTabsStore } from '@/stores/tabs'
import { boardSummary as summaryOf, groupByProduct, pendingStepCount, saleOrderQty, saleProductSummary } from '@/business/plan-groups.js'
import PlanProductGroups from './components/PlanProductGroups.vue'

const engine = usePanelRuntime()
const route = useRoute()
const router = useRouter()
const tabs = useTabsStore()

const moNo = ref(String(route.query.code || route.query.moNo || ''))
const soNo = ref(String(route.query.so || ''))
const rows = ref([])
const orders = ref([])
const loading = ref(false)
const submitting = ref(false)
const teams = ref([])
const equips = ref([])
/**
 * 班组/设备选项。
 * 设备是「单单据档案」（全厂一张单、设备在 detail.equips 明细里），
 * 必须经 engine.loadArchiveRows 展平，按列表取只会拿到单据头，下拉永远是空的。
 */
async function loadArchives() {
  try {
    const [t, e] = await Promise.all([
      engine.loadArchiveRows('TEAM'),
      engine.loadArchiveRows('EQUIP'),
    ])
    teams.value = (t || []).filter((r) => !r['停用'] && !r['是否停用'])
    equips.value = (e || []).filter((r) => !r['停用'])
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

// ---------- 订单排产（销售订单 → 生成生产加工单） ----------
const soOrders = ref([])
const soRows = ref([])
const soLoading = ref(false)
const generating = ref(false)

/** 勾选的行（本批数量 > 0 才算数） */
const selectedLines = computed(() => soRows.value
  .filter((r) => r.选中 && Number(r['本批数量']) > 0)
  .map((r) => ({ 行号: r['行号'], 数量: Number(r['本批数量']), 存货编码: r['存货编码'] })))

const pageTitle = computed(() => {
  if (soNo.value) return `工单排产-订单排产 · ${soNo.value}`
  if (moNo.value) return `工单排产-工序排产 · ${moNo.value}`
  return '工单排产'
})

const pageSub = computed(() => {
  if (soNo.value) {
    const total = soRows.value.length
    const planned = soRows.value.reduce((n, r) => n + Number(r['已排数量'] || 0), 0)
    const plannable = soRows.value.reduce((n, r) => n + Number(r['可排数量'] || 0), 0)
    return `${total} 个订单行 · 已排 ${qty(planned)} · 可排 ${qty(plannable)} · 按订单行生成生产加工单（单号 = 订单号-行号）`
  }
  if (moNo.value) return boardSummary.value
  return '从已审核销售订单排产生成加工单；或按加工单补排工序（预生产）'
})

async function loadSaleOrder() {
  soLoading.value = true
  try {
    const list = (await engine.saleOrderPlanBoard(soNo.value)) || []
    soRows.value = list.map((r) => ({
      ...r,
      选中: Number(r['可排数量']) > 0,
      本批数量: Number(r['可排数量']) || 0,
    }))
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载订单排产数据失败')
  } finally {
    soLoading.value = false
  }
}

function openSaleOrder(row) {
  const no = row['编号'] || row['单据编号'] || ''
  if (!no) return
  soNo.value = no
  moNo.value = ''
  const path = '/mes/plan'
  const query = { so: no }
  router.replace({ path, query })
  tabs.open({ path, title: `工单排产-${no}`, query })
  loadSaleOrder()
}

/** 按勾选的订单行生成加工单（后端生成即已审核，并自动排首道工序批次） */
async function doPlanFromSo() {
  const lines = selectedLines.value
  if (!lines.length) return
  try {
    await ElMessageBox.confirm(
      `将按 ${lines.length} 个订单行生成生产加工单（单号 = 订单号-行号），生成后直接是已审核状态，并按首道工序自动排产批次。`,
      '生成生产加工单',
      { type: 'info', confirmButtonText: '生成', cancelButtonText: '取消' },
    )
  } catch (e) {
    return
  }
  generating.value = true
  try {
    const res = await engine.planFromSalesOrder({ 销售订单号: soNo.value, lines })
    const created = (res && res['加工单']) || []
    const failed = (res && res['失败']) || []
    if (created.length) {
      ElMessage.success(`已生成 ${created.length} 张生产加工单：${created.map((r) => r['加工单号']).join('、')}`)
    }
    if (failed.length) ElMessage.warning(`${failed.length} 行未生成：${failed.join('；')}`)
    if (!created.length && !failed.length) ElMessage.info('没有可排产的订单行')
    await loadSaleOrder()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '生成生产加工单失败')
  } finally {
    generating.value = false
  }
}

function qty(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '0'
  return n === Math.round(n) ? String(n) : n.toFixed(2)
}

/** 设备选项文案：编码在前便于和现场设备铭牌对上，存的是设备名称 */
function equipLabel(e) {
  return [e['设备编码'], e['设备名称']].filter(Boolean).join(' ')
}

/** 加工单里的全部产品（一张单可以有多件产品，此前只看第一件） */
function productsOf(row) {
  const list = row.detail?.products
  return Array.isArray(list) ? list.filter((p) => p && (p['产品编码'] || p['产品名称'])) : []
}
function qtyOf(row) {
  return productsOf(row).reduce((sum, p) => sum + Number(p['数量'] || 0), 0)
}
/** 产品列：多产品逐件列出（编码 名称 ×数量），单产品保持原样 */
function productOf(row) {
  const list = productsOf(row)
  if (!list.length) return '—'
  if (list.length === 1) {
    const p = list[0]
    return [p['产品名称'], p['规格型号']].filter(Boolean).join(' ') || p['产品编码'] || '—'
  }
  return list
    .map((p) => `${p['产品编码'] || ''} ${p['产品名称'] || ''} ×${qty(p['数量'])}`.trim())
    .join('、')
}

/**
 * 销售订单明细：明细键是 items（不是加工单的 products），字段是 存货名称/数量/销售单位。
 * 列表只给「名称 ×数量 单位」，够看清这批要做什么，不堆编码和规格。
 */
function saleItemsOf(row) {
  const list = row.detail?.items
  return Array.isArray(list) ? list : []
}
function saleProductsText(row) {
  return saleProductSummary(saleItemsOf(row))
}
function saleQtyOf(row) {
  return saleOrderQty(saleItemsOf(row))
}
/**
 * 排产看板按产品分区：一件产品一块，块头给计划/已排/可排汇总。
 * 分组规则在 @/business/plan-groups.js，独立页与排产弹窗共用同一套。
 */
const productGroups = computed(() => groupByProduct(rows.value))

/** 当前能一键排掉的工序数（可排产量 > 0） */
const pendingSteps = computed(() => pendingStepCount(rows.value))
const canPlanAll = computed(() => !loading.value && pendingSteps.value > 0)

const planningAll = ref(false)

/**
 * 一键排产：把当前每道可排工序按「可排产量」各建一个批次并审批通过。
 * 下道工序要等上道报工合格才解锁，所以报工完再点一次可以继续往后排。
 *
 * <p>独立页传页面单号，排产弹窗传弹窗自己的单号：两个入口共用同一个后端动作，
 * 但只刷新自己的那份数据（弹窗刷新不能顺手把页面的表覆盖掉）。
 */
async function doPlanAll(orderNo) {
  const inDialog = Boolean(orderNo)
  const target = orderNo || moNo.value
  const n = inDialog ? dialogPending.value : pendingSteps.value
  if (!target || !n) return
  try {
    await ElMessageBox.confirm(
      `将把当前 ${n} 道工序按可排产量各建一个排产批次，并直接提交审批通过。班组/设备取工序档案默认值。`,
      '一键排产',
      { type: 'info', confirmButtonText: '开始排产', cancelButtonText: '取消' },
    )
  } catch (e) {
    return
  }
  planningAll.value = true
  try {
    const res = await engine.planAll({ manuOrderNo: target, 产品编码: '' })
    const done = (res && res['已排产']) || []
    const failed = (res && res['失败']) || []
    if (done.length) ElMessage.success(`已生成并审批 ${done.length} 个排产批次：${done.join('、')}`)
    if (failed.length) ElMessage.warning(`${failed.length} 道工序未排成：${failed.join('；')}`)
    if (!done.length && !failed.length) ElMessage.info('当前没有可排产的工序')
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '一键排产失败')
  } finally {
    planningAll.value = false
  }
  await (inDialog ? loadDialog() : load())
}

/** 头部摘要：几个产品、几道工序、还有几道能排 */
const boardSummary = computed(() => summaryOf(rows.value))

const planVisible = ref(false)
const dialogMo = ref('')
const dialogRows = ref([])
const dialogLoading = ref(false)
const dialogGroups = computed(() => groupByProduct(dialogRows.value))
const dialogPending = computed(() => pendingStepCount(dialogRows.value))
const dialogCanPlanAll = computed(() => !dialogLoading.value && dialogPending.value > 0)
const dialogSummary = computed(() => summaryOf(dialogRows.value))

/** 只重载弹窗自己的工序行，不动页面状态 */
async function loadDialog() {
  if (!dialogMo.value) return
  dialogLoading.value = true
  try {
    dialogRows.value = (await engine.planBoard(dialogMo.value)) || []
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载排产数据失败')
  } finally {
    dialogLoading.value = false
  }
}

async function openPlanDialog(row) {
  dialogMo.value = row['编号'] || ''
  dialogRows.value = []
  planVisible.value = true
  await loadDialog()
}
function back() {
  soNo.value = ''
  moNo.value = ''
  soRows.value = []
  router.replace({ path: '/mes/plan' })
  load()
}

async function load() {
  loading.value = true
  try {
    if (soNo.value) {
      await loadSaleOrder()
      return
    }
    if (!moNo.value) {
      // 待排产销售订单：排产从这里进（生成加工单）
      const soRes = await engine.queryFormDataList({ panelCode: 'SO_ORDER', pageNo: 1, pageSize: 200 })
      soOrders.value = (soRes?.list || []).filter((r) => (r['单据状态'] || '') === '已审核')
      // 审批通过后状态会推进到「生产中」，这类加工单仍要能继续排后续工序（预生产/补排）
      const res = await engine.queryFormDataList({ panelCode: 'MANU_ORDER', pageNo: 1, pageSize: 200 })
      orders.value = (res?.list || []).filter((r) => ['已审核', '生产中'].includes(r['单据状态'] || ''))
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

/**
 * 打开排产弹窗。
 *
 * <p>工序行里没有加工单号，必须由调用方传进来：单订单页传页面上的 moNo，
 * 排产弹窗里的工序传弹窗自己的 dialogMo。以前一律读页面 moNo，
 * 从「待排产」列表点进来（地址栏没有 code）就会带上空单号，提交时报「缺少加工单号」。
 */
function openPlan(row, orderNo) {
  form.value = {
    ...row,
    加工单号: orderNo || moNo.value || dialogMo.value || '',
    数量: Number(row['可排产量']),
    班组: '',
    设备: '',
    日期: [],
  }
  visible.value = true
}

async function submit() {
  const q = Number(form.value.数量)
  if (!(q > 0)) return ElMessage.warning('排产数量必须大于 0')
  submitting.value = true
  try {
    const res = await engine.planBatch({
      manuOrderNo: form.value['加工单号'] || moNo.value,
      产品编码: form.value['产品编码'] || '',
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

/**
 * 页签按路径复用：从「待排产销售订单」列表切到「某张销售订单排产」时地址栏变了但组件不重建，
 * 只在 setup 里读一次 query 会一直停在上一次的形态（实测点「进入排产」停在订单列表）。
 * 所以监听地址栏参数，变了就切形态并重新取数。
 */
watch(
  () => [route.query.so, route.query.code, route.query.moNo],
  ([so, code, moNoQuery]) => {
    const nextSo = String(so || '')
    const nextMo = String(code || moNoQuery || '')
    if (nextSo === soNo.value && nextMo === moNo.value) return
    soNo.value = nextSo
    moNo.value = nextMo
    load()
  },
)

onMounted(() => { load(); loadArchives() })
</script>

<style scoped>
/* 页头动作区：一键排产与换单同一行 */
.plb-head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 弹窗页头：标题 + 摘要 + 一键排产按钮 */
.plb-dlg-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-right: 24px;
}

.plb-dlg-title {
  display: block;
  font-size: 15px;
  line-height: 24px;
  font-weight: 600;
  color: rgba(0, 0, 0, 0.88);
}

/* 弹窗内整体滚动，产品块之间不再各自出滚动条 */
.plb-dlg-body {
  max-height: 62vh;
  overflow-y: auto;
}

.plb-page { padding: 16px 24px; height: 100%; display: flex; flex-direction: column; gap: 8px; background: #fff; color: #1f1f1f; font-size: 14px; line-height: 22px; }
.plb-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding-bottom: 12px; border-bottom: 1px solid #f0f0f0; }
.plb-head h2 { margin: 0; font-size: 16px; line-height: 24px; font-weight: 600; }
.plb-sub { display: block; margin-top: 2px; font-size: 12px; line-height: 20px; color: #8c8c8c; }
.plb-sec { margin: 16px 0 8px; padding-left: 8px; border-left: 2px solid #1677ff; font-size: 13px; line-height: 20px; font-weight: 600; color: #1f1f1f; }
.b-sum { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 16px; margin-bottom: 16px; }
.b-kv { display: flex; justify-content: space-between; gap: 8px; font-size: 13px; line-height: 20px; color: #595959; padding: 6px 10px; border: 1px solid #f0f0f0; border-radius: 6px; }
.b-kv b { color: #1f1f1f; font-weight: 600; font-variant-numeric: tabular-nums; }
.ok { color: #1677ff; font-variant-numeric: tabular-nums; }
.zero { color: rgba(0, 0, 0, 0.45); font-variant-numeric: tabular-nums; }
.mono { font-family: Consolas, "Courier New", monospace; font-size: 13px; }
/* 订单排产提示：说明生成规则，不抢视觉 */
.plb-tip { margin: 8px 0 0; padding: 8px 12px; border: 1px solid #e6f4ff; border-radius: 6px; background: #f7fbff; font-size: 12px; line-height: 20px; color: #595959; }
:deep(.el-table) { font-size: 13px; }
:deep(.el-table th.el-table__cell) { background: #fafafa; color: #595959; font-weight: 500; }
:deep(.el-table .cell) { font-variant-numeric: tabular-nums; }
</style>
