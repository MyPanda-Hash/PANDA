<!-- PurchaseReqAnalysisBoard.vue — 采购需求分析
     只读汇总 + 缺口闭环跟踪：缺口（计划分析快照）→ 请购 → 采购 → 到货。
     不是业务单据：不新建/编辑/审批单据，只做跨单汇总、进度回写与请购生单。 -->
<template>
  <div class="adx pra-page">
    <header class="pra-head">
      <div class="pra-title">
        <h2>采购需求分析</h2>
        <span class="pra-sub">缺的料买齐了没有：缺口 → 请购 → 采购 → 到货 一眼看清</span>
      </div>
      <div class="pra-actions">
        <el-button size="small" @click="openManual">补录需求</el-button>
        <el-button size="small" @click="openManualList">手工需求</el-button>
        <el-button size="small" :loading="exporting" @click="exportList">导出</el-button>
        <el-button type="primary" size="small" :disabled="!pickable.length" @click="openGenerate(pickable)">
          批量生成请购单
        </el-button>
      </div>
    </header>

    <section class="pra-filters">
      <div class="pra-field">
        <span>需求日期</span>
        <el-date-picker v-model="filters.dueFrom" type="date" value-format="YYYY-MM-DD" size="small"
          placeholder="起" style="width: 130px" />
        <span class="pra-dash">~</span>
        <el-date-picker v-model="filters.dueTo" type="date" value-format="YYYY-MM-DD" size="small"
          placeholder="止" style="width: 130px" />
      </div>
      <div class="pra-field">
        <span>销售订单号</span>
        <el-input v-model="filters.soNo" size="small" clearable style="width: 150px" placeholder="按来源订单筛选" />
      </div>
      <div class="pra-field">
        <span>物料</span>
        <el-input v-model="filters.keyword" size="small" clearable style="width: 160px" placeholder="编码 / 名称" />
      </div>
      <div class="pra-field">
        <span>供应商</span>
        <el-input v-model="filters.supplier" size="small" clearable style="width: 140px" placeholder="建议供应商" />
      </div>
      <div class="pra-field">
        <span>状态</span>
        <el-select v-model="filters.status" size="small" clearable style="width: 140px" placeholder="全部">
          <el-option v-for="s in STATUSES" :key="s" :label="s" :value="s" />
        </el-select>
      </div>
      <div class="pra-field">
        <span>请购状态</span>
        <el-select v-model="filters.requestStatus" size="small" clearable style="width: 120px" placeholder="全部">
          <el-option v-for="s in REQUEST_STATUSES" :key="s" :label="s" :value="s" />
        </el-select>
      </div>
      <div class="pra-field">
        <span>来源</span>
        <el-select v-model="filters.sourceType" size="small" clearable style="width: 110px" placeholder="全部">
          <el-option label="计划" value="计划" />
          <el-option label="手工" value="手工" />
        </el-select>
      </div>
      <div class="pra-field">
        <el-button type="primary" size="small" :loading="loading" @click="load">查询</el-button>
        <el-button size="small" @click="reset">重置</el-button>
      </div>
    </section>

    <section class="pra-cards">
      <div class="pra-card">
        <span class="pra-c-label">缺口物料</span>
        <b>{{ summary.materials }}</b><span class="pra-c-unit">项</span>
      </div>
      <div class="pra-card">
        <span class="pra-c-label">缺口合计</span>
        <b>{{ fmt(summary.gapTotal) }}</b>
      </div>
      <div class="pra-card is-warn">
        <span class="pra-c-label">未请购</span>
        <b>{{ summary.notRequested }}</b><span class="pra-c-unit">项</span>
      </div>
      <div class="pra-card is-warn">
        <span class="pra-c-label">未请购量</span>
        <b>{{ fmt(summary.notRequestedQty) }}</b>
      </div>
      <div class="pra-card is-risk">
        <span class="pra-c-label">超请购</span>
        <b>{{ summary.overRequested }}</b><span class="pra-c-unit">项</span>
      </div>
      <div class="pra-card is-info">
        <span class="pra-c-label">在途进行中</span>
        <b>{{ summary.inProgress }}</b><span class="pra-c-unit">项</span>
      </div>
      <div class="pra-card is-ok">
        <span class="pra-c-label">已完成</span>
        <b>{{ summary.completed }}</b><span class="pra-c-unit">项</span>
      </div>
      <div class="pra-card is-risk">
        <span class="pra-c-label">交期风险</span>
        <b>{{ summary.risk }}</b><span class="pra-c-unit">项</span>
      </div>
    </section>

    <el-table ref="tableRef" :data="rows" v-loading="loading" size="small" border class="pra-table"
      :row-key="keyOf" @selection-change="onSelect" @expand-change="onExpand">
      <el-table-column type="selection" width="42" :selectable="canPick" />
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="pra-expand">
            <div class="pra-expand-head">
              <b>来源明细</b>
              <span class="pra-dim">计划分析快照 / 手工补录（只读）</span>
            </div>
            <el-table :data="sourcesOf(row)" size="small" border empty-text="没有来源行">
              <el-table-column label="来源类型" prop="来源类型" width="80" />
              <el-table-column label="计划分析单号" prop="计划分析单号" width="180" />
              <el-table-column label="销售订单号" prop="销售订单号" width="140" />
              <el-table-column label="客户" prop="客户" min-width="120" show-overflow-tooltip />
              <el-table-column label="交期" prop="交期" width="100" />
              <el-table-column label="层级" prop="层级" width="60" align="right" />
              <el-table-column label="来源链" prop="来源链" min-width="160" show-overflow-tooltip />
              <el-table-column label="毛需求" prop="毛需求" width="90" align="right" />
              <el-table-column label="现存量" prop="现存量" width="90" align="right" />
              <el-table-column label="净需求" prop="净需求" width="90" align="right" />
              <el-table-column label="备注" prop="备注" min-width="120" show-overflow-tooltip />
            </el-table>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="物料编码" prop="物料编码" width="120" />
      <el-table-column label="物料名称" prop="物料名称" min-width="150" show-overflow-tooltip />
      <el-table-column label="规格型号" prop="规格型号" min-width="120" show-overflow-tooltip />
      <el-table-column label="单位" prop="单位" width="60" />
      <el-table-column label="缺口合计" width="96" align="right">
        <template #default="{ row }">{{ fmt(row['缺口合计']) }}</template>
      </el-table-column>
      <el-table-column label="已请购量" width="96" align="right">
        <template #default="{ row }">{{ fmt(row['已请购量']) }}</template>
      </el-table-column>
      <el-table-column label="未请购量" width="96" align="right">
        <template #default="{ row }">{{ fmt(row['未请购量']) }}</template>
      </el-table-column>
      <el-table-column label="请购差异" width="96" align="right">
        <template #default="{ row }">
          <span :class="{ 'pra-bad': Number(row['请购差异']) > 0 }">{{ fmt(row['请购差异']) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="请购状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="requestStatusType(row['请购状态'])">{{ row['请购状态'] }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="已采购量" width="96" align="right">
        <template #default="{ row }">{{ fmt(row['已采购量']) }}</template>
      </el-table-column>
      <el-table-column label="已到货量" width="96" align="right">
        <template #default="{ row }">{{ fmt(row['已到货量']) }}</template>
      </el-table-column>
      <el-table-column label="未到货量" width="96" align="right">
        <template #default="{ row }">{{ fmt(row['未到货量'] ?? row['未完成量']) }}</template>
      </el-table-column>
      <el-table-column label="最早需求日期" prop="最早需求日期" width="120" />
      <el-table-column label="建议供应商" prop="建议供应商" min-width="120" show-overflow-tooltip />
      <el-table-column label="来源" prop="来源" width="90" />
      <el-table-column label="状态" width="110" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="statusType(row['状态'])">{{ row['状态'] }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" :disabled="!canGenerate(row)"
            @click="openGenerate([row])">生成请购单</el-button>
          <el-button link type="primary" size="small" :disabled="!Number(row['已请购量'])"
            @click="openLatestRequisition(row)">查看请购单</el-button>
          <el-button link type="primary" size="small" @click="openChain(row)">查看链路</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pra-pager">
      <el-pagination background layout="total, prev, pager, next, sizes" :total="total"
        :page-size="pageSize" :current-page="pageNo" :page-sizes="[20, 50, 100, 200]"
        @current-change="onPage" @size-change="onSize" />
    </div>

    <!-- 生成请购单 -->
    <el-dialog v-model="generateVisible" title="生成请购单" width="720px" append-to-body>
      <p class="pra-dim">同物料多来源合并为一行；数量默认取未完成量，可修改。超过缺口数量会再次确认。</p>
      <el-table :data="generateRows" size="small" border>
        <el-table-column label="物料编码" prop="物料编码" width="110" />
        <el-table-column label="物料名称" prop="物料名称" min-width="130" show-overflow-tooltip />
        <el-table-column label="缺口合计" width="90" align="right">
          <template #default="{ row }">{{ fmt(row['缺口合计']) }}</template>
        </el-table-column>
        <el-table-column label="未完成量" width="90" align="right">
          <template #default="{ row }">{{ fmt(row['未完成量']) }}</template>
        </el-table-column>
        <el-table-column label="请购数量" width="140">
          <template #default="{ row }">
            <el-input-number v-model="row._qty" :min="0" :precision="2" size="small" controls-position="right" />
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="generateVisible = false">取消</el-button>
        <el-button type="primary" :loading="generating" @click="doGenerate">生成</el-button>
      </template>
    </el-dialog>

    <!-- 手工补录 -->
    <el-dialog v-model="manualVisible" title="补录需求（备货 / 安全库存）" width="480px" append-to-body>
      <el-form :model="manualForm" label-width="90px" size="small">
        <el-form-item label="物料编码" required>
          <el-input v-model="manualForm.materialCode" placeholder="必填" />
        </el-form-item>
        <el-form-item label="物料名称"><el-input v-model="manualForm.materialName" /></el-form-item>
        <el-form-item label="规格型号"><el-input v-model="manualForm.spec" /></el-form-item>
        <el-form-item label="单位"><el-input v-model="manualForm.unit" /></el-form-item>
        <el-form-item label="数量" required>
          <el-input-number v-model="manualForm.qty" :min="0" :precision="2" controls-position="right" />
        </el-form-item>
        <el-form-item label="需求日期">
          <el-date-picker v-model="manualForm.dueDate" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="建议供应商"><el-input v-model="manualForm.supplier" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="manualForm.remark" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="manualVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingManual" @click="saveManual">保存</el-button>
      </template>
    </el-dialog>

    <!-- 手工需求列表 / 撤销 -->
    <el-dialog v-model="manualListVisible" title="手工需求" width="760px" append-to-body>
      <el-table :data="manualRows" size="small" border empty-text="没有手工需求">
        <el-table-column label="物料编码" prop="物料编码" width="110" />
        <el-table-column label="物料名称" prop="物料名称" min-width="120" show-overflow-tooltip />
        <el-table-column label="规格型号" prop="规格型号" min-width="100" show-overflow-tooltip />
        <el-table-column label="单位" prop="单位" width="60" />
        <el-table-column label="数量" width="90" align="right">
          <template #default="{ row }">{{ fmt(row['净需求']) }}</template>
        </el-table-column>
        <el-table-column label="需求日期" prop="交期" width="110" />
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row['状态'] === '有效' ? 'success' : 'info'">{{ row['状态'] }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="center">
          <template #default="{ row }">
            <el-button v-if="row['状态'] === '有效'" link type="danger" size="small"
              @click="revokeManual(row)">撤销</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- 单据链路 -->
    <el-drawer v-model="chainVisible" :title="`单据链路：${chainMaterial}`" size="560px">
      <el-table :data="chainRows" size="small" border empty-text="还没有请购/采购/入库单据">
        <el-table-column label="类型" prop="类型" width="110" />
        <el-table-column label="单号" prop="单号" min-width="150" />
        <el-table-column label="状态" prop="状态" width="90" align="center" />
        <el-table-column label="单据日期" prop="单据日期" width="110" />
        <el-table-column label="数量" width="90" align="right">
          <template #default="{ row }">{{ fmt(row['数量']) }}</template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePanelRuntime } from '@core/panel-runtime'
import { useTabsStore } from '@/stores/tabs'

const engine = usePanelRuntime()
const router = useRouter()
const tabs = useTabsStore()
const STATUSES = ['未请购', '部分请购', '已请购未采购', '已采购未到货', '已完成', '超量']
const REQUEST_STATUSES = ['未请购', '部分请购', '已请购', '超请购']
const filters = reactive({ dueFrom: '', dueTo: '', soNo: '', keyword: '', supplier: '', status: '', requestStatus: '', sourceType: '' })
const rows = ref([])
const total = ref(0)
const pageNo = ref(1)
const pageSize = ref(50)
const loading = ref(false)
const exporting = ref(false)
const summary = ref({ materials: 0, gapTotal: 0, notRequested: 0, notRequestedQty: 0, overRequested: 0, inProgress: 0, completed: 0, risk: 0 })
const selected = ref([])
const tableRef = ref(null)
const sourceCache = ref({})
const generateVisible = ref(false)
const generateRows = ref([])
const generating = ref(false)
const manualVisible = ref(false)
const manualForm = reactive({ materialCode: '', materialName: '', spec: '', unit: '', qty: 0, dueDate: '', supplier: '', remark: '' })
const savingManual = ref(false)
const manualListVisible = ref(false)
const manualRows = ref([])
const chainVisible = ref(false)
const chainRows = ref([])
const chainMaterial = ref('')

const pickable = computed(() => selected.value.filter(canGenerate))

function keyOf(row) {
  return `${row['物料编码']}||${row['规格型号'] || ''}||${row['单位'] || ''}`
}
function fmt(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return ''
  return Math.abs(n - Math.round(n)) < 1e-6 ? String(Math.round(n)) : String(Math.round(n * 100) / 100)
}
function statusType(s) {
  return {
    未请购: 'danger',
    部分请购: 'warning',
    已请购未采购: 'info',
    已采购未到货: 'primary',
    已完成: 'success',
    超量: 'warning',
  }[s] || 'info'
}
function requestStatusType(s) {
  return { 未请购: 'danger', 部分请购: 'warning', 已请购: 'success', 超请购: 'warning' }[s] || 'info'
}
function canGenerate(row) {
  return !['已完成', '超量', '已请购未采购'].includes(row['状态']) && Number(row['未完成量'] || 0) > 0
}
function canPick(row) {
  return row['状态'] !== '已完成' && row['状态'] !== '超量'
}
function onSelect(sel) {
  selected.value = sel
}
function sourcesOf(row) {
  return sourceCache.value[keyOf(row)] || []
}
async function onExpand(row, expanded) {
  const isExpanded = Array.isArray(expanded)
    ? expanded.some((item) => keyOf(item) === keyOf(row))
    : !!expanded
  if (!isExpanded || sourceCache.value[keyOf(row)]) return
  try {
    const list = await engine.purchaseReqAnalysisSources({
      materialCode: row['物料编码'], spec: row['规格型号'] || '', unit: row['单位'] || '',
    })
    sourceCache.value = { ...sourceCache.value, [keyOf(row)]: list || [] }
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载来源失败')
  }
}
function payload() {
  return {
    filters: { ...filters, dueFrom: filters.dueFrom || '', dueTo: filters.dueTo || '' },
    pageNo: pageNo.value,
    pageSize: pageSize.value,
  }
}
async function load() {
  loading.value = true
  try {
    const res = await engine.purchaseReqAnalysisQuery(payload())
    rows.value = res?.list || []
    total.value = res?.total || 0
    if (res?.summary) summary.value = res.summary
    sourceCache.value = {}
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载失败')
  } finally {
    loading.value = false
  }
}
function reset() {
  Object.assign(filters, { dueFrom: '', dueTo: '', soNo: '', keyword: '', supplier: '', status: '', requestStatus: '', sourceType: '' })
  pageNo.value = 1
  load()
}
function onPage(p) {
  pageNo.value = p
  load()
}
function onSize(s) {
  pageSize.value = s
  pageNo.value = 1
  load()
}
function openGenerate(list) {
  generateRows.value = (list || [])
    .filter(canGenerate)
    .map((row) => ({ ...row, _qty: Number(row['未完成量'] || 0) }))
  if (!generateRows.value.length) {
    ElMessage.warning('没有可生成请购单的物料')
    return
  }
  generateVisible.value = true
}
async function doGenerate() {
  const items = generateRows.value
    .filter((row) => Number(row._qty) > 0)
    .map((row) => ({
      materialCode: row['物料编码'], spec: row['规格型号'] || '', unit: row['单位'] || '', qty: Number(row._qty),
    }))
  if (!items.length) {
    ElMessage.warning('请购数量必须大于 0')
    return
  }
  const over = generateRows.value.filter((row) => Number(row._qty) > Number(row['缺口合计']))
  if (over.length) {
    try {
      await ElMessageBox.confirm(`有 ${over.length} 行请购数量超过缺口，确定继续？`, '数量确认', { type: 'warning' })
    } catch (e) {
      return
    }
  }
  generating.value = true
  try {
    const res = await engine.purchaseReqAnalysisGenerate(items)
    const docNo = res?.['编号'] || ''
    ElMessage.success(`已生成请购单：${docNo}`)
    generateVisible.value = false
    await load()
    if (docNo) openRequisition(docNo)
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '生成失败')
  } finally {
    generating.value = false
  }
}
/**
 * 打开一张请购单（列表页 + 页签），生成后直接跳过去。
 *
 * <p>单据统一走列表页：列表页有单据栏、列头筛选和表头内联编辑，是系统里
 * 请购单的正常形态；表单页只用于特殊场景，不作为默认入口。
 */
function openRequisition(docNo) {
  const path = '/panelx/list/PU_REQ'
  const query = { code: docNo, from: '/mes/purchaseReqAnalysis' }
  router.push({ path, query })
  tabs.open({ path, title: '请购单-' + docNo, query })
}

/** 行内查看请购单：取该物料最新一张请购单。 */
async function openLatestRequisition(row) {
  try {
    const chain = (await engine.purchaseReqAnalysisChain(row['物料编码'])) || []
    const reqs = chain
      .filter((item) => item['类型'] === '请购单' && item['单号'])
      .sort((a, b) => String(a['单号']).localeCompare(String(b['单号'])))
    if (!reqs.length) {
      ElMessage.warning('该物料还没有请购单')
      return
    }
    openRequisition(reqs[reqs.length - 1]['单号'])
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '打开请购单失败')
  }
}

function openManual() {
  Object.assign(manualForm, { materialCode: '', materialName: '', spec: '', unit: '', qty: 0, dueDate: '', supplier: '', remark: '' })
  manualVisible.value = true
}
async function saveManual() {
  if (!manualForm.materialCode.trim()) {
    ElMessage.warning('物料编码不能为空')
    return
  }
  if (!(Number(manualForm.qty) > 0)) {
    ElMessage.warning('数量必须大于 0')
    return
  }
  savingManual.value = true
  try {
    await engine.purchaseReqAnalysisManual({ ...manualForm })
    ElMessage.success('已补录需求')
    manualVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '保存失败')
  } finally {
    savingManual.value = false
  }
}
async function openManualList() {
  try {
    manualRows.value = (await engine.purchaseReqAnalysisManualList()) || []
    manualListVisible.value = true
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载失败')
  }
}
async function revokeManual(row) {
  try {
    await ElMessageBox.confirm(`撤销 ${row['物料编码']} 的手工需求？`, '撤销确认', { type: 'warning' })
  } catch (e) {
    return
  }
  try {
    await engine.purchaseReqAnalysisManualRevoke(row.id)
    ElMessage.success('已撤销')
    manualRows.value = (await engine.purchaseReqAnalysisManualList()) || []
    await load()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '撤销失败')
  }
}
async function openChain(row) {
  chainMaterial.value = row['物料编码']
  chainRows.value = []
  chainVisible.value = true
  try {
    chainRows.value = (await engine.purchaseReqAnalysisChain(row['物料编码'])) || []
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载链路失败')
  }
}
async function exportList() {
  exporting.value = true
  try {
    const blob = await engine.purchaseReqAnalysisExport({ ...filters })
    engine.saveBlob(blob, `采购需求分析-${new Date().toISOString().slice(0, 10)}.xlsx`)
    ElMessage.success('已导出')
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '导出失败')
  } finally {
    exporting.value = false
  }
}
onMounted(async () => {
  await load()
  if (!rows.value.length) {
    await new Promise((resolve) => setTimeout(resolve, 600))
    await load()
  }
})
</script>

<style scoped>
.pra-page {
  padding: 12px 16px;
  background: #fff;
  min-height: 100%;
}
.pra-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 10px;
  border-bottom: 1px solid #f0f0f0;
  margin-bottom: 10px;
}
.pra-title h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #1f1f1f;
}
.pra-sub {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  color: #8c8c8c;
}
.pra-actions {
  display: flex;
  gap: 8px;
}
.pra-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 16px;
  padding: 10px 12px;
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 4px;
  margin-bottom: 10px;
}
.pra-field {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #595959;
}
.pra-dash {
  color: #bfbfbf;
}
.pra-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 10px;
  margin-bottom: 10px;
}
.pra-card {
  padding: 10px 12px;
  border: 1px solid #f0f0f0;
  border-radius: 4px;
  background: #fff;
}
.pra-card b {
  display: block;
  margin-top: 4px;
  font-size: 20px;
  color: #1f1f1f;
}
.pra-card.is-warn b {
  color: #d46b08;
}
.pra-card.is-info b {
  color: #1677ff;
}
.pra-card.is-ok b {
  color: #389e0d;
}
.pra-card.is-risk b {
  color: #cf1322;
}
.pra-c-label {
  font-size: 12px;
  color: #8c8c8c;
}
.pra-c-unit {
  margin-left: 2px;
  font-size: 12px;
  color: #8c8c8c;
}
.pra-table {
  width: 100%;
}
.pra-expand {
  padding: 8px 12px 12px 48px;
  background: #fafafa;
}
.pra-expand-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 12px;
}
.pra-dim {
  color: #8c8c8c;
  font-size: 12px;
}
.pra-bad {
  color: #cf1322;
}
.pra-pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 10px;
}
</style>
