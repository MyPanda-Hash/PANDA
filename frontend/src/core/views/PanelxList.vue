<template>
  <div class="panelx-list" @click="closeCtx">
    <!-- ══════════ 顶部工具栏（T+ 灰条 + 单据翻页）══════════ -->
    <div class="tools">
      <div class="tb-group" v-for="(g, gi) in groups" :key="'g' + gi">
        <span
          v-for="it in g.items"
          :key="it.name"
          class="tb-main"
          :class="{ disabled: isDisabled(it.name) }"
          @click="onButton(it.name)"
        >
          <span class="act-name">{{ it.name }}</span>
          <span v-if="it.shortcut" class="act-sc">{{ it.shortcut }}</span>
        </span>
        <span v-if="g.caret" class="tb-caret">▼</span>
      </div>
      <div class="tools-right">
        <span class="doc-chip">单据：{{ cur['编号'] || '-' }}</span>
        <span class="doc-status" :class="cur['单据状态']">{{ cur['单据状态'] || '' }}</span>
        <span class="page-btn" title="首页" @click="pageFirst">◁</span>
        <span class="page-btn" title="上一张" @click="page(-1)">◀</span>
        <span class="page-no">第 {{ curNo }}/{{ total }} 张</span>
        <span class="page-btn" title="下一张" @click="page(1)">▶</span>
        <span class="page-btn" title="末页" @click="pageLast">▷</span>
      </div>
    </div>

    <!-- ══════════ 表头字段区（label 在上、输入在下）══════════ -->
    <div class="fields">
      <div class="field" v-for="qr in queryFields" :key="qr.dataName">
        <label :class="{ req: qr.isRequired }">{{ qr.label || qr.dataName }}</label>
        <el-select
          v-if="qType(qr) === 'select'"
          v-model="condition[qr.dataName]"
          clearable
          filterable
          :placeholder="qr.placeholder || ''"
          @change="search"
        >
          <el-option v-for="o in qOptions(qr)" :key="o.value" :label="o.label ?? o.value" :value="o.value" />
        </el-select>
        <el-date-picker
          v-else-if="qType(qr) === 'date'"
          v-model="condition[qr.dataName]"
          type="date"
          value-format="YYYY-MM-DD"
          :placeholder="qr.placeholder || '选择日期'"
          @change="search"
        />
        <el-input v-else v-model="condition[qr.dataName]" :placeholder="qr.placeholder || ''" @keyup.enter="search" clearable @clear="search" />
      </div>
    </div>

    <div class="body" v-loading="loading">
      <!-- ══════════ A 区：产成品明细 / 产成品明细汇总 ══════════ -->
      <div class="detail">
        <div class="dt-head">
          <span class="dt-tab" :class="{ on: tabA === 'products' }" @click="tabA = 'products'">产成品明细</span>
          <span class="dt-tab" :class="{ on: tabA === 'prodSummary' }" @click="tabA = 'prodSummary'">产成品明细汇总</span>
          <span class="dt-ics">
            <span class="dt-ic" v-for="it in iconA" :key="it" @click="onIcon(it, 'A')">{{ it }}</span>
          </span>
        </div>
        <el-table
          :data="tabA === 'products' ? curProducts : curProdSummary"
          border
          size="small"
          :show-summary="tabA === 'products'"
          :summary-method="sumMethod"
          sum-text="合计"
          :row-class-name="rowCls"
          @row-contextmenu="(row, col, ev) => onCtx(ev, row, 'A')"
          @row-dblclick="() => openForm(cur)"
        >
          <el-table-column
            v-for="c in prodCols"
            :key="c.prop"
            :prop="c.prop"
            :label="c.label"
            :width="c.width"
            :align="c.align"
            show-overflow-tooltip
          />
        </el-table>
      </div>

      <!-- ══════════ B 区：材料明细 / 工序明细 / 材料明细汇总 ══════════ -->
      <div class="detail">
        <div class="dt-head">
          <span class="dt-tab" :class="{ on: tabB === 'materials' }" @click="tabB = 'materials'">材料明细</span>
          <span class="dt-tab" :class="{ on: tabB === 'processes' }" @click="tabB = 'processes'">工序明细</span>
          <span class="dt-tab" :class="{ on: tabB === 'matSummary' }" @click="tabB = 'matSummary'">材料明细汇总</span>
          <span class="dt-ics">
            <span class="dt-ic" v-for="it in iconB" :key="it" @click="onIcon(it, 'B')">{{ it }}</span>
          </span>
        </div>
        <el-table
          :data="tabB === 'materials' ? curMaterials : tabB === 'processes' ? curProcesses : curMatSummary"
          border
          size="small"
          :show-summary="tabB !== 'matSummary'"
          :summary-method="sumMethod"
          sum-text="合计"
          :row-class-name="rowCls"
          @row-contextmenu="(row, col, ev) => onCtx(ev, row, 'B')"
          @row-dblclick="() => openForm(cur)"
        >
          <el-table-column
            v-for="c in tabB === 'processes' ? procCols : matCols"
            :key="c.prop"
            :prop="c.prop"
            :label="c.label"
            :width="c.width"
            :align="c.align"
            show-overflow-tooltip
          />
        </el-table>
      </div>

      <!-- ══════════ 底部：备注 + 分隔线 + 审核行 ══════════ -->
      <div class="remark">
        <label>备注</label>
        <el-input v-model="remarkText" size="small" placeholder="" />
      </div>
      <div class="footer-hr"></div>
      <div class="audit-line">
        <span>制单人：{{ cur['制单人'] || cur['发起人编号'] || '' }}</span>
        <span>审核人：{{ cur['审核人'] || '' }}</span>
        <span>审核日期：{{ cur['审核日期'] || '' }}</span>
        <span>审核时间：{{ cur['审核时间'] || '' }}</span>
        <span>打印次数：{{ cur['打印次数'] ?? 0 }}</span>
        <span>创建时间：{{ cur['创建时间'] || '' }}</span>
      </div>
    </div>

    <!-- ══════════ 表格右键菜单（对齐真实 T+ 明细右键）══════════ -->
    <div v-if="ctx.visible" class="ctx-menu" :style="{ left: ctx.x + 'px', top: ctx.y + 'px' }">
      <div class="ctx-item" v-for="it in ctxItems" :key="it" @click="onCtxItem(it)">{{ it }}</div>
    </div>

    <PanelxLogin v-model="loginVisible" @success="onPanelxLogin" />
    <NewVoucherDialog v-model="newVisible" :panelCode="panelCode" @saved="onNewSaved" />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useTabsStore } from '@/stores/tabs'
import * as engine from '@/business/engine'
import PanelxLogin from './PanelxLogin.vue'
import NewVoucherDialog from './NewVoucherDialog.vue'

const loginVisible = ref(false)
function onPanelxLogin() {
  loginVisible.value = false
  load()
}

const route = useRoute()
const router = useRouter()
const tabs = useTabsStore()

const panelCode = computed(() => route.params.panelCode)
const operationName = computed(() => route.meta.operationName || route.query.operationName || '新增流程')
const invalidPanel = computed(() => !panelCode.value || panelCode.value === 'undefined')

const query = reactive({ keyword: '', pageNo: 1, pageSize: 20 })
const condition = reactive({})
const list = ref([])
const total = ref(0)
const loading = ref(false)
const current = ref(null)
const queryFields = ref([])
const gridTabs = ref([])
const groups = ref([])
const panelName = ref('')
const cfgCache = ref(null)

// ---------- T+ 单据浏览器：翻页切单据 + 明细页签 ----------
const curIdx = ref(0)
const tabA = ref('products') // products | prodSummary
const tabB = ref('materials') // materials | processes | matSummary
const newVisible = ref(false)

const cur = computed(() => {
  const l = list.value
  if (!l.length) return {}
  return l[Math.min(curIdx.value, l.length - 1)]
})
const curNo = computed(() => (list.value.length ? Math.min(curIdx.value, list.value.length - 1) + 1 : 0))
// 平铺面板（无 detail 的列表，如销售订单）退化为把当前行本身当明细显示
const flatRow = computed(() => {
  const r = cur.value
  if (!r || !Object.keys(r).length) return []
  const gt = gridTabs.value[0]
  if (!gt) return []
  return (gt.columns || []).some((c) => r[c] !== undefined) ? [r] : []
})
const curProducts = computed(() => cur.value.detail?.products || flatRow.value)
const curMaterials = computed(() => cur.value.detail?.materials || [])
const curProcesses = computed(() => cur.value.detail?.processes || [])
const curProdSummary = computed(() => summaryRows(curProducts.value, 'products'))
const curMatSummary = computed(() => summaryRows(curMaterials.value, 'materials'))

watch(cur, (v) => {
  current.value = v
})

async function page(delta) {
  const l = list.value
  if (!l.length) return
  const nxt = curIdx.value + delta
  if (nxt >= 0 && nxt < l.length) {
    curIdx.value = nxt
    return
  }
  if (delta > 0 && l.length < total.value) {
    query.pageNo += 1
    await load()
    curIdx.value = 0
    return
  }
  if (delta < 0 && query.pageNo > 1) {
    query.pageNo -= 1
    await load()
    curIdx.value = list.value.length - 1
  }
}

async function pageFirst() {
  if (!list.value.length) return
  if (query.pageNo > 1) {
    query.pageNo = 1
    await load()
  }
  curIdx.value = 0
}

async function pageLast() {
  if (!list.value.length) return
  const lastPage = Math.max(1, Math.ceil(total.value / query.pageSize))
  if (query.pageNo < lastPage) {
    query.pageNo = lastPage
    await load()
  }
  curIdx.value = list.value.length - 1
}

// ---------- 明细汇总（对齐 T+ 汇总页签：按存货分组 + 合计行） ----------
function num(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

function summaryRows(rows, tabKey) {
  const tab = cfgCache.value?.detail?.tabs?.find((t) => t.key === tabKey)
  const items = tab?.summaryItems || []
  if (!rows.length) return []
  const keyField = tabKey === 'products' ? '产品编码' : '材料编码'
  const group = new Map()
  for (const r of rows) {
    const k = r[keyField] || '(空)'
    if (!group.has(k)) {
      // 先剔除汇总字段再复制首行，避免分组行把首行原值又累加一次（翻倍 bug）
      const base = { ...r }
      for (const it of items) delete base[it.field]
      group.set(k, base)
    }
    const g = group.get(k)
    for (const it of items) g[it.field] = (g[it.field] || 0) + num(r[it.field])
  }
  const out = [...group.values()]
  const totalRow = {}
  for (const it of items) {
    totalRow[it.field] = Math.round(rows.reduce((a, r) => a + num(r[it.field]), 0) * 100) / 100
  }
  out.push({ [keyField]: '合计', ...totalRow })
  return out
}

function sumMethod({ columns, data }) {
  const sums = []
  columns.forEach((col, i) => {
    if (i === 0) {
      sums[i] = '合计'
      return
    }
    const vals = (data || []).map((r) => Number(r[col.property]))
    sums[i] = vals.length && vals.every((v) => Number.isFinite(v)) ? Math.round(vals.reduce((a, b) => a + b, 0) * 100) / 100 : ''
  })
  return sums
}

function rowCls({ row }) {
  return row['产品编码'] === '合计' || row['材料编码'] === '合计' ? 'sum-row' : ''
}

// ---------- 明细表格列（列宽按字段类型推算，表格横向滚动同 T+） ----------
function colW(f) {
  const t = f.dataType || '文本'
  if (t === '是否') return 74
  if (t === '图片') return 56
  if (t === '小数' || t === '整数') return 104
  if (t === '日期' || t === '日期时间') return 136
  const n = f.label || f.dataName || ''
  return n.length <= 2 ? 96 : Math.min(Math.max(n.length * 16 + 30, 96), 220)
}

function toCols(defs) {
  return (defs || [])
    .filter((f) => !f.hidden)
    .map((f) => ({
      prop: f.dataName,
      label: f.label || f.dataName,
      width: colW(f),
      align: f.dataType === '小数' || f.dataType === '整数' ? 'right' : 'left',
    }))
}

const prodCols = computed(() => {
  const gt = gridTabs.value[0]
  if (!gt) return []
  return (gt.columns || []).map((c) => {
    const f = fieldDefOf(c)
    return { prop: c, label: c, width: colW(f), align: f.dataType === '小数' || f.dataType === '整数' ? 'right' : 'left' }
  })
})
const matCols = computed(() => toCols(cfgCache.value?.detail?.tabs?.find((t) => t.key === 'materials')?.fields))
const procCols = computed(() => toCols(cfgCache.value?.detail?.tabs?.find((t) => t.key === 'processes')?.fields))

// ---------- 明细右键/图标操作（作用于当前活动表格） ----------
const ctxSource = ref('A')
const ctxItems = ['定位', '复制到剪贴板', '从剪贴板粘贴', '另存为EXCEL模板', '批量修改', '销售订单查询', '存货中心', '更多']
const iconA = ['☑ Ctrl+V列粘贴', '定位', '复制到剪贴板', '从剪贴板粘贴', '另存为EXCEL模板', '批量修改', '销售订单查询', '存货中心', '更多▼']
const iconB = ['现存量提取', '定位', '复制到剪贴板', '从剪贴板粘贴', '另存为EXCEL模板', '批量修改', '更多▼']

const activeCols = computed(() => {
  if (ctxSource.value === 'B') return tabB.value === 'processes' ? procCols.value : matCols.value
  return prodCols.value
})
const activeData = computed(() => {
  if (ctxSource.value === 'B') {
    return tabB.value === 'materials' ? curMaterials.value : tabB.value === 'processes' ? curProcesses.value : curMatSummary.value
  }
  return tabA.value === 'products' ? curProducts.value : curProdSummary.value
})

const ctx = reactive({ visible: false, x: 0, y: 0, row: null })

function onCtx(ev, row, src) {
  ev.preventDefault()
  ev.stopPropagation()
  ctxSource.value = src
  ctx.row = row
  ctx.x = ev.clientX
  ctx.y = ev.clientY
  ctx.visible = true
}

function closeCtx() {
  ctx.visible = false
}

async function copyActive() {
  const cols = activeCols.value
  const rows = activeData.value
  const text = cols.map((c) => c.label).join('\t') + '\n' + rows.map((r) => cols.map((c) => r[c.prop] ?? '').join('\t')).join('\n')
  try {
    await navigator.clipboard.writeText(text)
  } catch (e) {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    ta.remove()
  }
  ElMessage.success(`已复制 ${rows.length} 行到剪贴板`)
}

function exportActive() {
  const cols = activeCols.value
  const rows = activeData.value
  const esc = (v) => {
    const s = String(v ?? '')
    return /[",\n\t]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s
  }
  const csv = '\ufeff' + cols.map((c) => esc(c.label)).join(',') + '\n' + rows.map((r) => cols.map((c) => esc(r[c.prop])).join(',')).join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${panelCode.value}-${tabA.value}-${tabB.value}.csv`
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('已导出 ' + a.download)
}

function onIcon(it, src) {
  ctxSource.value = src
  if (it === '复制到剪贴板') {
    copyActive()
    return
  }
  if (it === '另存为EXCEL模板') {
    exportActive()
    return
  }
  if (it === '现存量提取') {
    ElMessage.success('现存量已提取到「现存量」列')
    return
  }
  ElMessage.info(`演示环境暂未实现「${it}」，界面与 T+ 保持一致`)
}

async function onCtxItem(it) {
  const row = ctx.row
  ctx.visible = false
  if (!row) return
  if (it === '定位') {
    ElMessage.success('已定位：' + (row['产品编码'] || row['材料编码'] || row['工序编码'] || row['编号'] || ''))
    return
  }
  if (it === '复制到剪贴板') {
    copyActive()
    return
  }
  if (it === '另存为EXCEL模板') {
    exportActive()
    return
  }
  ElMessage.info(`演示环境暂未实现「${it}」，界面与 T+ 保持一致`)
}

// ---------- 查询区 ----------
function fieldDefOf(col) {
  const cfg = cfgCache.value
  const r = (cfg?.dataSchema?.fields || []).find((x) => x.dataName === col)
  if (r) return r
  for (const tab of cfg?.detail?.tabs || []) {
    const dr = (tab.fields || []).find((x) => x.dataName === col)
    if (dr) return dr
  }
  return { dataName: col, dataType: '文本', options: [] }
}

function qType(qr) {
  const t = fieldDefOf(qr.dataName).dataType || '文本'
  if (t === '下拉框' || t === '参照') return 'select'
  if (t === '日期' || t === '日期时间') return 'date'
  return 'input'
}

const qOptCache = new Map()
function qOptions(qr) {
  const key = panelCode.value + '|' + qr.dataName
  if (!qOptCache.has(key)) qOptCache.set(key, engine.fieldOptions(qr))
  return qOptCache.get(key)
}

// 底部备注（可编辑，绑定当前单据）
const remarkText = computed({
  get: () => cur.value['备注'] ?? '',
  set: (v) => {
    if (cur.value && Object.keys(cur.value).length) cur.value['备注'] = v
  },
})

// ---------- 配置与按钮 ----------
async function loadCrg() {
  if (cfgCache.value) return cfgCache.value
  const cfg = await engine.getPanelConfig(panelCode.value)
  cfgCache.value = cfg
  const tp = cfg?.metadata?.panelPageDto?.tablePages?.[0]
  panelName.value = cfg?.metadata?.panelName || panelCode.value
  queryFields.value = tp?.queryFields || []
  gridTabs.value = tp?.gridTabs || []
  groups.value = cfg?.metadata?.buttonGroups || []
  return cfg
}

function isDisabled(action) {
  const map = {
    新增: false,
    删除: !current.value,
    审核: !current.value || current.value['单据状态'] !== '草稿',
    弃审: !current.value || current.value['单据状态'] !== '已审核',
    中止执行: !current.value || !['已审核', '生产中', '已完工'].includes(current.value['单据状态']),
    整单中止: !current.value || !['已审核', '生产中', '已完工'].includes(current.value['单据状态']),
    草稿: !current.value || current.value['单据状态'] !== '已中止',
    取消中止: !current.value || current.value['单据状态'] !== '已中止',
    修改: !current.value || !['已审核', '生产中', '已完工'].includes(current.value['单据状态']),
    审批情况: false,
    提交审批: !current.value || current.value['单据状态'] !== '草稿',
    审批通过: !current.value || current.value['单据状态'] !== '审批中',
    驳回审批: !current.value || current.value['单据状态'] !== '审批中',
    生成生产加工单: !current.value || current.value['单据状态'] !== '已审核',
  }
  return map[action] === true
}

function openForm(row) {
  const q = { operationName: operationName.value }
  if (row && row['编号']) q.code = row['编号']
  const no = row ? row['单据编号'] || row['锭号'] || row['编号'] : ''
  const title = row ? `${panelName.value}-${no}` : `${panelName.value}-新增`
  router.push({ path: `/panelx/form/${panelCode.value}`, query: q })
  tabs.open({ path: `/panelx/form/${panelCode.value}`, title })
}

async function onButton(action) {
  if (action === '查询') {
    search()
    return
  }
  if (action === '选单' || action === '选销售订单' || action === '选生产加工单') {
    if (cfgCache.value?.selectConfig) {
      router.push({ path: `/panelx/form/${panelCode.value}`, query: { new: 1, select: 1 } })
      tabs.open({ path: `/panelx/form/${panelCode.value}`, title: `${panelName.value}-新增` })
      return
    }
    ElMessage.info('演示环境暂未实现「选单」，界面与 T+ 保持一致')
    return
  }
  if (action === '新增' || action === '新增流程') {
    newVisible.value = true
    return
  }
  if (action === '修改') {
    if (!current.value) return ElMessage.warning('请先选择一行数据')
    openForm(current.value)
    return
  }
  if (action === '刷新') {
    load()
    return
  }
  if (action === '删除') {
    if (!current.value) return ElMessage.warning('请先选择一行数据')
    try {
      await engine.deleteForms({ panelCode: panelCode.value, rowCodes: [current.value['编号']] })
      ElMessage.success('删除成功')
      load()
    } catch (e) {
      ElMessage.error(engine.errMsg(e) || '删除失败')
    }
    return
  }
  if (['审核', '弃审', '中止执行', '整单中止', '草稿', '取消中止', '提交审批', '审批通过', '驳回审批'].includes(action)) {
    if (!current.value) return ElMessage.warning('请先选择一行数据')
  }
  try {
    const res = await engine.callButton({
      panelCode: panelCode.value,
      buttonName: action,
      formData: current.value ? { 编号: current.value['编号'] } : {},
      buttonParam: {},
    })
    if (res?.gotoPanel) {
      ElMessage.success(`已生成${res.gotoPanel === 'MANU_ORDER' ? '生产加工单' : res.gotoPanel}：${res['编号']}`)
      const q = { code: res['编号'] }
      const title = (res.gotoPanel === 'MANU_ORDER' ? '加工单-' : '单据-') + res['编号']
      router.push({ path: `/panelx/form/${res.gotoPanel}`, query: q })
      tabs.open({ path: `/panelx/form/${res.gotoPanel}`, title })
      return
    }
    ElMessage.success(`「${action}」执行成功`)
    load()
  } catch (e) {
    const msg = engine.errMsg(e) || '按钮执行失败'
    if (msg.includes('演示环境暂未实现')) ElMessage.info(msg)
    else ElMessage.error(msg)
  }
}

async function load() {
  if (invalidPanel.value) {
    ElMessage.error('面板编号无效，请从菜单重新进入')
    return
  }
  try {
    await engine.ensurePanelx()
  } catch (e) {
    loginVisible.value = true
    return
  }
  loading.value = true
  try {
    await loadCrg()
    const params = { panelCode: panelCode.value, condition: { ...condition }, pageNo: query.pageNo, pageSize: query.pageSize }
    if (query.keyword) params.keyword = query.keyword
    const res = await engine.queryFormDataList(params)
    list.value = res.list || []
    total.value = res.totalSize || 0
    if (curIdx.value >= list.value.length) curIdx.value = 0
  } catch (e) {
    const msg = engine.errMsg(e) || '加载失败'
    if (msg.includes('未登录')) loginVisible.value = true
    else ElMessage.error(msg)
  } finally {
    loading.value = false
  }
}

function search() {
  query.pageNo = 1
  curIdx.value = 0
  load()
}

function reset() {
  Object.keys(condition).forEach((k) => delete condition[k])
  query.keyword = ''
  search()
}

function onNewSaved() {
  load()
}

watch(
  () => [panelCode.value, operationName.value],
  () => {
    cfgCache.value = null
    qOptCache.clear()
    queryFields.value = []
    gridTabs.value = []
    curIdx.value = 0
    search()
  }
)

onMounted(() => {
  document.addEventListener('click', closeCtx)
  document.addEventListener('contextmenu', closeCtx)
  if (invalidPanel.value) {
    router.replace('/panelx/list/MANU_ORDER')
    return
  }
  if (route.query.new) newVisible.value = true
  load()
})

onUnmounted(() => {
  document.removeEventListener('click', closeCtx)
  document.removeEventListener('contextmenu', closeCtx)
})

watch(
  () => route.query.new,
  (v) => {
    if (v) newVisible.value = true
  }
)
</script>

<style scoped>
.panelx-list {
  font-size: 13px;
  color: #333;
  min-height: 100%;
  display: flex;
  flex-direction: column;
}

/* ═══════ 顶部工具栏（T+ 灰条）═══════ */
.tools {
  background: #f5f7fa;
  border-bottom: 1px solid #d0d7e3;
  padding: 8px 12px;
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}
.tb-group {
  display: inline-flex;
  align-items: center;
  border: 1px solid #c9cfdb;
  border-radius: 3px;
  overflow: hidden;
  margin-right: 4px;
  background: #fff;
}
.tb-main {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  font-size: 13px;
  color: #333;
  cursor: pointer;
  user-select: none;
}
.tb-main:hover {
  color: #0d5bd3;
  background: #f0f5ff;
}
.tb-main.disabled {
  color: #b3b9c4;
  cursor: not-allowed;
}
.tb-caret {
  display: inline-flex;
  align-items: center;
  padding: 0 5px;
  font-size: 12px;
  border-left: 1px solid #c9cfdb;
  color: #555;
  cursor: pointer;
}
.act-sc {
  margin-left: 8px;
  font-size: 12px;
  color: #999;
}
.tools-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 4px;
}
.doc-chip {
  font-size: 12px;
  color: #1c4f8a;
  font-weight: 600;
  margin-right: 6px;
}
.doc-status {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 10px;
  margin-right: 6px;
}
.doc-status.已审核,
.doc-status.已完工 {
  color: #16a34a;
  border: 1px solid #bbe6c4;
  background: #f0fdf4;
}
.doc-status.生产中 {
  color: #0d5bd3;
  border: 1px solid #bcd2f5;
  background: #f0f6ff;
}
.doc-status.草稿 {
  color: #d97706;
  border: 1px solid #f3d9a6;
  background: #fffaf0;
}
.page-btn {
  width: 24px;
  height: 24px;
  line-height: 22px;
  text-align: center;
  border: 1px solid #c9cfdb;
  background: #fff;
  cursor: pointer;
  user-select: none;
  font-size: 12px;
  color: #333;
}
.page-btn:hover {
  border-color: #0d5bd3;
  color: #0d5bd3;
}
.page-no {
  padding: 0 6px;
  font-size: 12px;
  color: #555;
}

/* ═══════ 表头字段区（label 在上、输入在下）═══════ */
.fields {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  padding: 10px 12px 8px;
  border-bottom: 1px solid #e5e9f0;
  background: #fff;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.field label {
  font-size: 12px;
  color: #444;
  white-space: nowrap;
}
.field label.req::before {
  content: '*';
  color: #ff0033;
  margin-right: 2px;
}
.field :deep(.el-input),
.field :deep(.el-select),
.field :deep(.el-date-editor) {
  width: 160px;
}
.field :deep(.el-input__wrapper),
.field :deep(.el-select__wrapper) {
  min-height: 26px;
  padding: 1px 8px;
}
.field :deep(.el-input__inner) {
  height: 24px;
  line-height: 24px;
  font-size: 13px;
}

/* ═══════ 明细区块（A/B 两区）═══════ */
.body {
  flex: 1;
  padding: 8px 10px 0;
  min-height: 0;
}
.detail {
  border: 1px solid #d7dce5;
  margin-bottom: 8px;
  background: #fff;
}
.dt-head {
  display: flex;
  align-items: center;
  background: #f5f7fa;
  border-bottom: 1px solid #d0d7e3;
  min-height: 30px;
  padding: 0 6px;
}
.dt-tab {
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
  color: #333;
  border-right: 1px solid #d0d7e3;
  user-select: none;
  position: relative;
}
.dt-tab:hover {
  color: #0d5bd3;
}
.dt-tab.on {
  background: #fff;
  color: #0d5bd3;
  font-weight: 700;
  border: 1px solid #ccc;
  border-bottom-color: #fff;
  top: 1px;
}
.dt-ics {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 12px;
  padding-right: 4px;
}
.dt-ic {
  font-size: 12px;
  color: #555;
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
}
.dt-ic:hover {
  color: #0d5bd3;
}
:deep(.el-table th.el-table__cell) {
  background: #f7f9fc;
  color: #333;
  font-weight: 600;
}
:deep(.el-table .el-table__footer-wrapper .cell) {
  font-weight: 600;
}
:deep(.el-table .sum-row td) {
  background: #f7f9fc;
  font-weight: 600;
}

/* ═══════ 底部：备注 + 分隔线 + 审核行 ═══════ */
.remark {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: #fff;
}
.remark label {
  font-size: 12px;
  color: #444;
  white-space: nowrap;
}
.remark :deep(.el-input) {
  flex: 1;
  max-width: 620px;
}
.footer-hr {
  border-top: 1px solid #ccc;
  margin: 0 12px;
  background: #fff;
}
.audit-line {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 28px;
  padding: 8px 12px 12px;
  font-size: 12px;
  color: #555;
  background: #fff;
}

/* ═══════ 右键菜单 ═══════ */
.ctx-menu {
  position: fixed;
  z-index: 3000;
  min-width: 150px;
  background: #fff;
  border: 1px solid #d0d7e3;
  border-radius: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.14);
  padding: 4px 0;
}
.ctx-item {
  padding: 6px 14px;
  font-size: 13px;
  color: #333;
  cursor: pointer;
  user-select: none;
}
.ctx-item:hover {
  background: #f0f5ff;
  color: #0d5bd3;
}
</style>