<!-- PlanAnalysisBoard.vue — 计划分析
     入口：计划管理 → 计划分析。
     一张销售订单一次性回答四件事：成品库存够不够、缺哪些料、缺的料又缺什么、工艺路线通不通。
     替代主生产计划/物料需求运算/齐套分析/采购需求分析四个横向汇总面板的单据级分析入口。
     规则：数字全部由后端算好，本页只渲染，不二次计算。 -->
<template>
  <div class="adx pla-page">
    <header class="pla-head">
      <div class="pla-title">
        <h2>计划分析</h2>
        <span class="pla-sub">选一张销售订单，分析库存、物料清单与工艺路线的齐备情况</span>
      </div>
      <div class="pla-actions">
        <el-select
          v-model="selected"
          class="pla-select"
          filterable
          clearable
          placeholder="选择销售订单"
          :loading="loadingOrders"
          @change="onSelect"
        >
          <el-option
            v-for="o in orders"
            :key="o.salesOrderNo"
            :label="optionLabel(o)"
            :value="o.salesOrderNo"
          />
        </el-select>
        <el-button type="primary" :loading="analyzing" :disabled="!selected" @click="runAnalyze">
          分析
        </el-button>
        <el-button
          v-if="result && purchasableRows.length"
          :loading="requisitioning"
          @click="createRequisition(purchasableRows.map((r) => r.code))"
        >
          缺料一键请购
        </el-button>
      </div>
    </header>

    <!-- 空态：给主行动 -->
    <div v-if="!result" class="pla-empty">
      <p class="pla-empty-title">还没有分析结果</p>
      <p class="pla-empty-sub">在上方选择一张销售订单，点击「分析」开始</p>
      <el-button v-if="!orders.length && !loadingOrders" link type="primary" @click="loadOrders">
        重新加载订单列表
      </el-button>
    </div>

    <template v-else>
      <!-- ① 订单概览 -->
      <section class="pla-summary">
        <div class="pla-kv">
          <span class="pla-k">销售订单</span>
          <b>{{ order.no || '—' }}</b>
        </div>
        <div class="pla-kv">
          <span class="pla-k">客户</span>
          <b>{{ order.customer || '—' }}</b>
        </div>
        <div class="pla-kv">
          <span class="pla-k">交期</span>
          <b>{{ order.dueDate || '—' }}</b>
        </div>
        <div class="pla-kv">
          <span class="pla-k">优先级</span>
          <b :class="priorityClass">{{ order.priority || '—' }}</b>
        </div>
        <div class="pla-kv">
          <span class="pla-k">齐套率</span>
          <b>{{ rate(summary.kitRate) }}%</b>
        </div>
        <div class="pla-kv">
          <span class="pla-k">缺料项</span>
          <b :class="{ 'is-bad': summary.shortageCount > 0 }">{{ summary.shortageCount }}</b>
        </div>
      </section>

      <p v-if="order.linkedMo && order.linkedMo.length" class="pla-warn">
        该订单已生成加工单：{{ order.linkedMo.join('、') }}，本次分析仅供核对。
      </p>

      <!-- ② 成品层 -->
      <h3 class="pla-sec">成品层</h3>
      <div class="pla-table-scroll">
      <el-table :data="products" size="small" border class="pla-t-products" empty-text="没有可分析的产成品明细">
        <el-table-column label="存货编码" prop="code" width="120" />
        <el-table-column label="存货名称" prop="name" min-width="160" />
        <el-table-column label="规格型号" prop="spec" min-width="140" />
        <el-table-column label="需求数量" width="100" align="right">
          <template #default="{ row }">{{ qty(row.demandQty) }}</template>
        </el-table-column>
        <el-table-column label="现存量" width="96" align="right">
          <template #default="{ row }">
            <span :class="{ 'is-muted': !row.stockRecorded }">{{ qty(row.stockQty) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="待生产" width="110" align="right">
          <template #default="{ row }">{{ qty(row.shortQty) }}</template>
        </el-table-column>
        <el-table-column label="物料清单" width="130">
          <template #default="{ row }">
            <span :class="row.bomCode ? '' : 'is-bad'">{{ row.bomCode || '未维护' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="工艺路线" width="160">
          <template #default="{ row }">
            <template v-if="row.routingConfigured">{{ row.routeCode }}</template>
            <!-- 已建但没审核：直接跳到那张路线去审核，不是再新建一张 -->
            <el-button
              v-else-if="isDraftRoute(row)"
              link
              type="warning"
              size="small"
              title="工艺路线已建但还没审核，审核后计划分析才会认它"
              @click="openPanelForm('ROUTE', row.routeCode)"
            >
              {{ row.routeStatus }} · 去审核
            </el-button>
            <el-button
              v-else
              link
              type="primary"
              size="small"
              title="新增工艺路线并自动挂到本产品的物料清单；保存后需审核"
              @click="goConfigureRoute(row)"
            >
              未配置 · 去设置
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      </div>

      <!-- ③ 物料展开 -->
      <h3 class="pla-sec">
        物料需求与缺口
        <span class="pla-sec-hint">按成品 → 子件逐层展开；缺料且自制的件继续往下拆</span>
      </h3>
      <div class="pla-table-scroll">
      <el-table :data="materials" size="small" border class="pla-t-materials" empty-text="物料清单为空，无法展开物料需求">
        <el-table-column label="层级" prop="level" width="56" align="right" />
        <el-table-column label="来源链" prop="path" min-width="150" show-overflow-tooltip />
        <el-table-column label="物料编码" prop="code" width="100" />
        <el-table-column label="物料名称" prop="name" min-width="130" show-overflow-tooltip />
        <el-table-column label="单位" prop="unit" width="56" />
        <el-table-column label="毛需求" width="96" align="right">
          <template #default="{ row }">{{ qty(row.grossQty) }}</template>
        </el-table-column>
        <el-table-column label="现存量" width="110" align="right">
          <template #default="{ row }">
            <span :class="{ 'is-muted': !row.stockRecordExists }">{{ qty(row.stockQty) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="净需求" width="96" align="right">
          <template #default="{ row }">{{ qty(row.netQty) }}</template>
        </el-table-column>
        <el-table-column label="供应方式" prop="supplyType" width="80" />
        <el-table-column label="操作" width="96" align="center">
          <template #default="{ row }">
            <el-button
              v-if="isPurchasable(row)"
              link
              type="primary"
              size="small"
              :loading="requisitioning"
              @click="createRequisition([row.code])"
            >
              请购
            </el-button>
            <span v-else class="is-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <!-- 成品层库存不足是「待生产」，不是缺料：排产就是把它做出来 -->
            <el-tag v-if="row.level === 0 && row.shortage" size="small" type="info" effect="light">
              待生产
            </el-tag>
            <el-tag v-else-if="row.shortage" size="small" type="danger" effect="light">
              缺料{{ row.expandable ? '·待拆' : '' }}
            </el-tag>
            <el-tag v-else size="small" type="success" effect="light">充足</el-tag>
          </template>
        </el-table-column>
      </el-table>
      </div>

      <!-- ④ 工艺路线：订单里有几个成品，就逐条列出各自对应的路线 -->
      <h3 class="pla-sec">工艺路线</h3>
      <template v-if="routings.length">
        <div v-for="rt in routings" :key="rt.productCode + '|' + rt.routeCode" class="pla-route-block">
          <div class="pla-route-head">
            <span class="pla-route-product">{{ rt.productCode }} {{ rt.productName }}</span>
            <template v-if="rt.routeCode">
              <span class="pla-route-code">路线编码 <b>{{ rt.routeCode }}</b></span>
              <span v-if="!rt.configured" class="pla-route-warn">
                路线{{ rt.routeStatus || '未审核' }}，审核后才能排产
              </span>
            </template>
            <span v-else class="is-muted">未配置工艺路线</span>
          </div>
          <template v-if="(rt.steps || []).length">
            <div class="pla-route-meta">
              <span>关键工序 <b>{{ (rt.keySteps || []).join('、') || '无' }}</b></span>
              <span>委外工序 <b>{{ (rt.outsourced || []).join('、') || '无' }}</b></span>
            </div>
            <div class="pla-table-scroll">
            <el-table :data="rt.steps" size="small" border class="pla-t-routing">
              <el-table-column label="顺序" prop="seq" width="72" align="right" />
              <el-table-column label="工序编码" prop="opCode" width="110" />
              <el-table-column label="工序名称" prop="opName" min-width="120" />
              <el-table-column label="加工方式" prop="workType" width="96" />
              <el-table-column label="生产车间" prop="workshop" width="120" />
              <el-table-column label="标准合格率%" width="120" align="right">
                <template #default="{ row }">{{ qty(row.stdPassRate) }}</template>
              </el-table-column>
              <el-table-column label="关键工序" width="96" align="center">
                <template #default="{ row }">
                  <el-tag v-if="row.keyStep" size="small" type="warning" effect="light">关键</el-tag>
                  <span v-else class="is-muted">—</span>
                </template>
              </el-table-column>
            </el-table>
            </div>
          </template>
          <p v-else class="pla-empty-inline">该产品还没有工艺路线明细。</p>
        </div>
      </template>
      <p v-else class="pla-empty-inline">未配置工艺路线，排产后无法按工序派工。请先维护工艺路线。</p>

      <!-- ⑤ 分析结论 -->
      <h3 class="pla-sec">分析结论</h3>
      <div class="pla-narrative">
        <div class="pla-col">
          <h4>阻塞项</h4>
          <ul>
            <li v-for="(t, i) in narrative.blocked" :key="'b' + i">{{ t }}</li>
          </ul>
        </div>
        <div class="pla-col">
          <h4>原因提示</h4>
          <ul>
            <li v-for="(t, i) in narrative.causes" :key="'c' + i">{{ t }}</li>
          </ul>
          <p v-if="!narrative.causes.length" class="is-muted">无</p>
        </div>
        <div class="pla-col">
          <h4>建议动作</h4>
          <ul>
            <li v-for="(t, i) in narrative.actions" :key="'a' + i">{{ t }}</li>
          </ul>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { usePanelRuntime } from '@core/panel-runtime'
import { useTabsStore } from '@/stores/tabs'

const engine = usePanelRuntime()
const router = useRouter()
const tabs = useTabsStore()
const requisitioning = ref(false)

const orders = ref([])
const selected = ref('')
const loadingOrders = ref(false)
const analyzing = ref(false)
const result = ref(null)

const order = computed(() => result.value?.order || {})
const products = computed(() => result.value?.products || [])
const materials = computed(() => result.value?.materials || [])
/**
 * 每个成品各自的工艺路线。后端按订单产品逐条返回 routings；
 * 老接口只有单条 routing 时退回单条展示，保证页面不空。
 */
const routings = computed(() => {
  const list = result.value?.routings
  if (Array.isArray(list) && list.length) return list
  const single = result.value?.routing
  return single ? [single] : []
})
const summary = computed(() => result.value?.summary || {})
const narrative = computed(() => result.value?.narrative || { blocked: [], causes: [], actions: [] })
const priorityClass = computed(() => {
  const p = order.value.priority
  return { 'is-bad': p === '特急', 'is-warn': p === '紧急' }
})

function qty(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '0'
  return n === Math.round(n) ? String(n) : n.toFixed(2)
}

function rate(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(1) : '0.0'
}

function optionLabel(o) {
  const parts = [o.salesOrderNo]
  if (o.customer) parts.push(o.customer)
  if (o.dueDate) parts.push('交期 ' + o.dueDate)
  if (o.hasManuOrder) parts.push('已排产')
  return parts.join(' · ')
}

async function loadOrders() {
  loadingOrders.value = true
  try {
    orders.value = (await engine.planAnalyzeCandidates()) || []
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载销售订单失败')
  } finally {
    loadingOrders.value = false
  }
}

function onSelect() {
  // 换单时先清掉旧结果，避免看新单号配旧结论
  result.value = null
}

/** 可请购行：外购 + 缺料 + 非成品层，与后端 purchasableLines 口径一致 */
const purchasableRows = computed(() =>
  materials.value.filter((r) => r.level > 0 && r.shortage && Number(r.netQty) > 0 && r.supplyType === '外购'))

/** 工艺路线存在但还是草稿/审批中：动作是「去审核」而不是「去新建」 */
function isDraftRoute(row) {
  return !row.routingConfigured && ['草稿', '审批中'].includes(row.routeStatus)
}

function isPurchasable(row) {
  return row.level > 0 && row.shortage && Number(row.netQty) > 0 && row.supplyType === '外购'
}

/**
 * 生成请购单：成功后直接打开那张请购单，让用户看到带过来的缺料数据。
 *
 * 数量由服务端按缺口算好（缺多少请多少），前端不传数量，避免被改。
 */
async function createRequisition(codes) {
  if (!selected.value || requisitioning.value) return
  requisitioning.value = true
  try {
    const res = await engine.planAnalyzePurchaseReq(selected.value, codes)
    const no = res?.['编号'] || ''
    ElMessage.success(`已生成请购单 ${no}（草稿），待采购入库后回到本页重新分析`)
    if (no) openPanelForm('PU_REQ', no)
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '生成请购单失败')
  } finally {
    requisitioning.value = false
  }
}

/**
 * 去新增工艺路线：带上「编码 + 名称」直接打开新增页，用户只补工序明细。
 *
 * 编码按产品编码生成（GY-<产品编码>），名称取「<产品名称> 加工路线」，
 * 与现有工艺路线的命名习惯一致（GY-001 / 铝棒 Φ80 加工路线）。
 *
 * 注意：工艺路线编码还挂在物料清单表头，新增完要回到 BOM 的「工艺路线编码」里选中它，
 * 计划分析才认这条路线 —— 按钮 tooltip 里写明了这一步。
 */
function goConfigureRoute(product) {
  const code = product?.code || ''
  const routeCode = 'GY-' + code
  const routeName = (product?.name || code) + ' 加工路线'
  const prefill = { 工艺路线编码: routeCode, 工艺路线名称: routeName }
  const path = '/panelx/form/ROUTE'
  const query = {
    operationName: '新增流程',
    prefill: JSON.stringify(prefill),
    from: '/mes/planAnalysis',
  }
  router.push({ path, query })
  tabs.open({ path, title: '工艺路线-新增', query })
}

/** 打开某张已存在的单据表单。 */
function openPanelForm(panelCode, docNo) {
  const path = '/panelx/form/' + panelCode
  const query = { code: docNo, from: '/mes/planAnalysis' }
  router.push({ path, query })
  tabs.open({ path, title: panelCode + '-' + docNo, query })
}

async function runAnalyze() {
  if (!selected.value) return
  analyzing.value = true
  try {
    result.value = await engine.planAnalyze(selected.value)
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '分析失败')
  } finally {
    analyzing.value = false
  }
}

onMounted(loadOrders)
</script>

<style scoped>
.pla-page {
  padding: 16px 24px 32px;
  height: 100%;
  overflow: auto;
  background: #fff;
  color: var(--d-text);
  font-size: 14px;
  line-height: 22px;
}
.pla-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--d-fill);
}
.pla-title h2 { margin: 0; font-size: 16px; line-height: 24px; font-weight: 600; }
.pla-sub { display: block; margin-top: 2px; font-size: 12px; line-height: 20px; color: var(--d-text-3); }
.pla-actions { display: flex; align-items: center; gap: 8px; }
.pla-select { width: 360px; }

.pla-empty {
  margin-top: 48px;
  text-align: center;
}
.pla-empty-title { margin: 0; font-size: 14px; line-height: 22px; color: var(--d-text); }
.pla-empty-sub { margin: 4px 0 12px; font-size: 13px; line-height: 20px; color: var(--d-text-3); }
.pla-empty-inline {
  margin: 0;
  padding: 12px 16px;
  border: 1px solid var(--d-fill);
  border-radius: 6px;
  font-size: 13px;
  line-height: 20px;
  color: var(--d-text-3);
}

.pla-summary {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
  margin-top: 16px;
}
.pla-kv {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 12px;
  border: 1px solid var(--d-fill);
  border-radius: 6px;
}
.pla-k { font-size: 12px; line-height: 20px; color: var(--d-text-3); }
.pla-kv b { font-size: 14px; line-height: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }

.pla-warn {
  margin: 8px 0 0;
  padding: 8px 12px;
  border: 1px solid var(--d-warn-bg);
  border-radius: 6px;
  background: var(--d-warn-bg);
  font-size: 13px;
  line-height: 20px;
  color: var(--d-warn);
}

.pla-sec {
  margin: 24px 0 8px;
  padding-left: 8px;
  border-left: 2px solid var(--t-primary);
  font-size: 13px;
  line-height: 20px;
  font-weight: 600;
  color: var(--d-text);
}
.pla-sec-hint { margin-left: 8px; font-weight: 400; font-size: 12px; color: var(--d-text-3); }

/* 窄屏时表格保持自然列宽，由容器横向滚动，避免列被压扁裁切 */
.pla-table-scroll { width: 100%; overflow-x: auto; }
.pla-t-products { min-width: 1000px; }
.pla-t-materials { min-width: 940px; }
/* 工艺路线：一个成品一块，产品名做小标题，避免多成品时看串 */
.pla-route-block {
  margin-bottom: 12px;
}

.pla-route-head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 6px;
  font-size: 13px;
  line-height: 20px;
  color: rgba(0, 0, 0, 0.65);
}

.pla-route-product {
  padding: 1px 8px;
  border: 1px solid #91caff;
  border-radius: 4px;
  background: var(--t-primary-tint);
  color: rgba(0, 0, 0, 0.85);
  font-weight: 600;
}

.pla-route-warn {
  color: #d48806;
}

.pla-t-routing { min-width: 780px; }

.pla-route-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 24px;
  margin-bottom: 8px;
  font-size: 13px;
  line-height: 20px;
  color: var(--d-text-2);
}
.pla-route-meta b { color: var(--d-text); font-weight: 600; }

.pla-narrative {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.pla-col {
  padding: 12px 16px;
  border: 1px solid var(--d-fill);
  border-radius: 6px;
}
.pla-col h4 { margin: 0 0 8px; font-size: 13px; line-height: 20px; font-weight: 600; color: var(--d-text); }
.pla-col ul { margin: 0; padding-left: 18px; }
.pla-col li { font-size: 13px; line-height: 22px; color: var(--d-text-2); }

.is-bad { color: #cf1322; }
.is-warn { color: var(--d-warn); }
.is-muted { color: #bfbfbf; }

:deep(.el-table) { font-size: 13px; }
:deep(.el-table th.el-table__cell) { background: var(--d-fill-soft); color: var(--d-text-2); font-weight: 500; }
:deep(.el-table .cell) { font-variant-numeric: tabular-nums; }

@media (max-width: 1200px) {
  .pla-summary { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 768px) {
  .pla-page { padding: 12px 16px 24px; }
  .pla-head { flex-direction: column; }
  .pla-actions { width: 100%; }
  .pla-select { flex: 1; width: auto; }
  .pla-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .pla-narrative { grid-template-columns: 1fr; }
}
</style>
