<template>
  <div class="panelx-list">
    <!-- 查询区（配置驱动，3 列） -->
    <div class="query card">
      <el-form inline class="q-form" @submit.prevent>
        <div class="q-grid">
          <div v-for="qr in queryFields" :key="qr.dataName" class="q-field">
            <span class="q-label">{{ qr.dataName }}</span>
            <el-input v-if="!qr.dataType || qr.dataType === '文本'" v-model="condition[qr.dataName]" clearable placeholder="" size="small" @keyup.enter="search" />
            <el-select v-else-if="qr.dataType === '下拉框' || qr.dataType === '参照'" v-model="condition[qr.dataName]" clearable filterable size="small">
              <el-option v-for="o in qOptions(qr)" :key="o" :label="o.label ?? o" :value="o.value ?? o" />
            </el-select>
            <el-date-picker
              v-else-if="qr.dataType === '日期'"
              v-model="condition[qr.dataName]"
              type="date"
              value-format="YYYY-MM-DD"
              size="small"
              style="width: 100%"
            />
            <el-input v-else v-model="condition[qr.dataName]" clearable size="small" @keyup.enter="search" />
          </div>
        </div>
        <div class="q-btns">
          <el-button type="primary" :icon="Search" @click="search">查询</el-button>
          <el-button :icon="Refresh" @click="reset">重置</el-button>
        </div>
      </el-form>
    </div>

    <div class="card">
      <!-- 工具栏（T+ 分组形态） -->
      <div class="toolbar">
        <span v-for="g in groups" :key="g.name" class="tb-group">
          <span class="tb-main" :class="{ disabled: isDisabled(g.actions[0]) }" @click="onButton(g.actions[0])">{{ g.name }}</span>
          <el-dropdown v-if="g.actions.length > 1" @command="(a) => onButton(a)" trigger="click">
            <span class="tb-caret"><el-icon><ArrowDown /></el-icon></span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-for="a in g.actions" :key="a" :command="a" :disabled="isDisabled(a)">
                  <span class="act-name">{{ a }}</span><span v-if="SHORTCUTS[a]" class="act-sc">{{ SHORTCUTS[a] }}</span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </span>
        <div class="spacer" />
        <span class="panel-info">{{ panelName }} · {{ panelCode }}</span>
      </div>

      <!-- 网格双视图（明细 / 明细汇总） -->
      <el-tabs v-model="gridTab" class="grid-tabs">
        <el-tab-pane v-for="gt in gridTabs" :key="gt.label" :name="gt.label">
          <template #label>{{ gt.label }}</template>
          <div class="grid-wrap">
            <div v-if="isApproved" class="approved-stamp">已审批</div>
          <el-table
            :data="gt.summary ? summaryList(gt) : mergedList"
            v-loading="loading"
            size="small"
            border
            height="480"
            highlight-current-row
            :row-class-name="rowCls"
            :show-summary="true"
            :summary-method="(p) => summarize(p, gt, gt.summary ? summaryList(gt) : list)"
            @current-change="(row) => (current = row)"
            @row-dblclick="(row) => onRowDblClick(row)"
            @cell-dblclick="(row, column) => onCellDblClick(row, column)"
            @row-contextmenu="(row, col, ev) => onCtx(ev, row)"
          >
            <el-table-column label="序号" width="55" align="center" fixed="left">
              <template #default="{ $index }">{{ $index + 1 }}</template>
            </el-table-column>
            <el-table-column v-for="c in gt.columns" :key="c" :prop="c" :label="c" min-width="110" show-overflow-tooltip>
              <template #default="{ row }">
                <template v-if="row._inline && inlineCol === c">
                  <el-input v-if="inlineType(c) === '文本'" v-model="row[c]" size="small" autofocus @keyup.enter="inlineSave" @blur="inlineCol = ''" />
                  <div v-else-if="inlineType(c) === '参照'" class="ref-inline">
                    <span class="ref-inline-txt" @click="openInlineRef(c)">{{ inlineRefText(c) }}</span>
                    <el-button size="small" :icon="Search" class="ref-btn" @click="openInlineRef(c)" />
                  </div>
                  <el-select v-else-if="inlineType(c) === '下拉框'" v-model="row[c]" size="small" filterable allow-create style="width: 100%">
                    <el-option v-for="o in inlineOptions(c)" :key="o" :label="o.label ?? o" :value="o.value ?? o" />
                  </el-select>
                  <el-date-picker v-else-if="inlineType(c) === '日期'" v-model="row[c]" type="date" value-format="YYYY-MM-DD" size="small" style="width: 100%" @change="inlineCol = ''" />
                  <el-input-number v-else-if="inlineType(c) === '小数' || inlineType(c) === '整数'" v-model="row[c]" :controls="false" size="small" style="width: 100%" @keyup.enter="inlineSave" />
                  <el-switch v-else-if="inlineType(c) === '是否'" v-model="row[c]" size="small" @change="inlineCol = ''" />
                  <el-input v-else v-model="row[c]" size="small" autofocus @keyup.enter="inlineSave" @blur="inlineCol = ''" />
                </template>
                <template v-else>
                  <el-image v-if="c === '存货图片' && row[c]" :src="row[c]" fit="contain" style="width: 30px; height: 30px" />
                  <span v-else-if="c === '存货图片'" class="img-ph">图</span>
                  <span v-else>{{ row[c] }}</span>
                </template>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <template v-if="row._inline">
                  <el-button link type="primary" size="small" @click="inlineSave">保存</el-button>
                  <el-button link size="small" @click="cancelInline">取消</el-button>
                </template>
                <el-button v-else link type="primary" size="small" @click="openForm(row)">查看</el-button>
              </template>
            </el-table-column>
          </el-table>
          </div>
        </el-tab-pane>
      </el-tabs>

      <!-- 网格右键菜单（对齐真实 T+） -->
      <div v-show="ctx.visible" class="ctx-menu" :style="{ left: ctx.x + 'px', top: ctx.y + 'px' }" @click.stop @contextmenu.stop>
        <div v-for="it in ctxItems" :key="it" class="ctx-item" @click="onCtxItem(it)">{{ it }}</div>
      </div>

      <div class="pager">
        <el-pagination
          v-model:current-page="query.pageNo"
          v-model:page-size="query.pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="load"
          @size-change="search"
        />
      </div>
    </div>
    <PanelxLogin v-model="loginVisible" @success="onPanelxLogin" />
    <NewVoucherDialog v-model:visible="newVisible" :panel-code="panelCode" :panel-name="panelName" @saved="onNewSaved" />
    <RefPickDialog v-model="inlineRefVisible" :field="inlineRefPick?.field" @confirm="onInlineRefConfirm" />
  </div>
</template>

<script setup>
import { ref, reactive, computed, nextTick, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search, Refresh, ArrowDown } from '@element-plus/icons-vue'
import { useTabsStore } from '@/stores/tabs'
import * as engine from '@/business/engine'
import PanelxLogin from './PanelxLogin.vue'
import NewVoucherDialog from './NewVoucherDialog.vue'
import RefPickDialog from './RefPickDialog.vue'
const { SHORTCUTS } = engine

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
const gridTab = ref('')
const cfgCache = ref(null)

// ---------- 内联新增 + 弹窗新增 ----------
const inlineRow = ref(null)
const inlineCol = ref('')
const newVisible = ref(false)

// ---------- 网格右键菜单（对齐真实 T+ 产成品明细右键） ----------
const ctxItems = ['定位', '复制到剪贴板', '从剪贴板粘贴', '另存为EXCEL模板', '批量修改', '销售订单查询', '存货中心', '更多']
const ctx = reactive({ visible: false, x: 0, y: 0, row: null })

function onCtx(ev, row) {
  ev.preventDefault()
  ev.stopPropagation()
  current.value = row
  ctx.row = row
  ctx.x = ev.clientX
  ctx.y = ev.clientY
  ctx.visible = true
}

function closeCtx() {
  ctx.visible = false
}

async function onCtxItem(it) {
  const row = ctx.row
  ctx.visible = false
  if (!row) return
  if (it === '定位') {
    ElMessage.success(`已定位：${row['锭号'] || row['编号'] || ''}`)
    return
  }
  if (it === '复制到剪贴板') {
    const cols = gridTabs.value.find((g) => g.label === gridTab.value)?.columns || []
    const line = cols.map((c) => row[c] ?? '').join('\t')
    try {
      await navigator.clipboard.writeText(line)
    } catch (e) {
      // 兜底：execCommand 复制（部分受限环境剪贴板 API 不可用）
      const ta = document.createElement('textarea')
      ta.value = line
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      ta.remove()
      if (!ok) throw new Error('execCommand copy failed')
    }
    ElMessage.success('已复制到剪贴板（Tab 分隔）')
    return
  }
  if (it === '另存为EXCEL模板') {
    const gt = gridTabs.value.find((g) => g.label === gridTab.value)
    const cols = gt?.columns || []
    const rows = gt?.summary ? summaryList(gt) : list.value
    const esc = (v) => {
      const s = String(v ?? '')
      return /[",\n\t]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s
    }
    const csv = '\ufeff' + cols.map(esc).join(',') + '\n' + rows.map((r) => cols.map((c) => esc(r[c])).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.hrer = url
    a.download = `${panelCode.value}-${gridTab.value}.csv`
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('已导出 ' + a.download)
    return
  }
  if (it === '从剪贴板粘贴' || it === '批量修改' || it === '销售订单查询' || it === '存货中心' || it === '更多') {
    ElMessage.info(`演示环境暂未实现「${it}」，界面与 T+ 保持一致`)
    return
  }
}

function num(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

// 查询区选项缓存：fieldOptions 对参照字段每次新建数组会导致 el-select 无限递归渲染
const qOptCache = new Map()
function qOptions(qr) {
  const key = panelCode.value + '|' + qr.dataName
  if (!qOptCache.has(key)) qOptCache.set(key, engine.fieldOptions(qr))
  return qOptCache.get(key)
}

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

function inlineType(col) {
  return fieldDefOf(col).dataType || '文本'
}

// 选项缓存：fieldOptions 对参照字段每次新建数组会导致 el-select 无限递归渲染，必须按列缓存稳定引用
const inlineOptCache = new Map()
function inlineOptions(col) {
  const key = panelCode.value + '|' + col
  if (!inlineOptCache.has(key)) inlineOptCache.set(key, engine.fieldOptions(fieldDefOf(col)))
  return inlineOptCache.get(key)
}

// ---------- 参照字段弹窗选择（开发约束十一-1：能对应基础档案的字段弹窗拉取勾选导入） ----------
const refVisible = ref(false)
const refPick = ref(null)

function inlineRefText(c) {
  const row = inlineRow.value
  if (!row) return ''
  const v = row[c]
  if (v === undefined || v === null || v === '') return ''
  const t = engine.refLabelOf(fieldDefOf(c), v)
  return t === null || t === undefined ? String(v) : t
}

function openInlineRef(c) {
  if (!inlineRow.value) return
  refPick.value = { field: fieldDefOf(c), col: c }
  refVisible.value = true
}

function onRefConfirm(rows) {
  const p = refPick.value
  if (!p || !rows.length || !inlineRow.value) return
  const r = p.field
  const rp = r.ref || r
  const refField = rp.field || rp.refField
  const multi = !!(rp.multi || rp.refMulti)
  const vals = rows.map((x) => x[refField])
  inlineRow.value[p.col] = multi ? vals.join('、') : vals[0]
  const first = rows[0] || {}
  for (const m of rp.map || rp.refMap || []) {
    if (!m || first[m.from] === undefined) continue
    const to = m.to || m.from
    if (to !== p.col) inlineRow.value[to] = first[m.from]
  }
  ElMessage.success(`已导入 ${rows.length} 行${engine.refPanelName(r)}数据`)
}

const mergedList = computed(() => (inlineRow.value ? [...list.value, inlineRow.value] : list.value))

// ---------- 内联行参照选择（双击参照列弹出档案选择，对齐表单体验） ----------
const inlineRefVisible = ref(false)
const inlineRefPick = ref(null)

function onCellDblClick(row, column) {
  if (!row._inline) return
  const col = column?.property || column?.label || ''
  if (!col) return
  const dr = fieldDefOf(col)
  // 参照字段：弹窗选择
  if (dr.dataType === '参照' && (dr.refPanel || dr.ref)) {
    inlineRefPick.value = { field: dr, col }
    inlineRefVisible.value = true
    return
  }
  inlineCol.value = col
  nextTick(() => {
    const i = document.querySelector('.el-table__body-wrapper tbody tr:last-child input')
    if (i) i.focus()
  })
}

function onInlineRefConfirm(rows) {
  const p = inlineRefPick.value
  if (!p || !rows.length) return
  const row = inlineRow.value
  if (!row) return
  const r = p.field
  const rp = r.ref || r
  const refField = rp.field || rp.refField
  const src = rows[0]
  if (refField && p.col) row[p.col] = src[refField]
  if (r.displayField && r.displayField !== p.col && row[r.displayField] !== undefined) row[r.displayField] = src[r.displayField]
  for (const m of rp.map || rp.refMap || []) {
    if (!m || src[m.from] === undefined) continue
    const to = m.to || m.from
    if (to !== p.col && row[to] !== undefined) row[to] = src[m.from]
  }
  inlineRefVisible.value = false
  ElMessage.success('已导入 1 行' + engine.refPanelName(r) + '数据')
}

async function startInline() {
  if (inlineRow.value) return
  try {
    const p = await engine.getNewFormPermMatrix({ panelCode: panelCode.value, operationName: '新增流程' })
    inlineRow.value = { ...p.data, 编号: '', _inline: true }
    for (const tab of cfgCache.value?.detail?.tabs || []) {
      for (const dr of tab.fields) {
        if (inlineRow.value[dr.dataName] === undefined) {
          inlineRow.value[dr.dataName] = dr.defaultValue ?? (dr.dataType === '小数' || dr.dataType === '整数' ? 0 : dr.dataType === '是否' ? false : '')
        }
      }
    }
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '初始化新增行失败')
  }
}

// 审批流：当前行已审批 → 表格左上角「已审批」角标；已审批行浅绿底色
const isApproved = computed(() => current.value && current.value['审批状态'] === '已审批')

function rowCls({ row }) {
  return row && row['审批状态'] === '已审批' ? 'row-approved' : ''
}

function cancelInline() {
  inlineRow.value = null
  inlineCol.value = ''
  load()
}

function onRowDblClick(row) {
  if (row._inline) return
  if (cfgCache.value?.metadata?.readonly) return
  openForm(row)
}

async function inlineSave() {
  const row = inlineRow.value
  if (!row) return
  const cfg = cfgCache.value
  for (const r of cfg?.dataSchema?.fields || []) {
    if (r.isRequired && (row[r.dataName] === undefined || row[r.dataName] === null || String(row[r.dataName]).trim() === '')) {
      return ElMessage.warning(r.dataName + '不能为空')
    }
  }
  const head = {}
  for (const r of cfg?.dataSchema?.fields || []) head[r.dataName] = row[r.dataName]
  const tabs = cfg?.detail?.tabs || []
  const rd = { ...head }
  if (tabs.length) {
    const tab = tabs[0]
    const item = {}
    for (const dr of tab.fields) {
      item[dr.dataName] = row[dr.dataName] ?? dr.defaultValue ?? (dr.dataType === '小数' || dr.dataType === '整数' ? 0 : dr.dataType === '是否' ? false : '')
    }
    rd.detail = { [tab.key]: [item] }
  }
  try {
    const res = await engine.callButton({ panelCode: panelCode.value, buttonName: '保存', formData: rd, buttonParam: {} })
    ElMessage.success('新增成功：' + (res?.['编号'] || ''))
    inlineRow.value = null
    inlineCol.value = ''
    load()
  } catch (e) {
    const m = engine.errMsg(e) || '保存失败'
    if (m.includes('演示环境暂未实现')) ElMessage.info(m)
    else ElMessage.error(m)
  }
}

function onNewSaved() {
  load()
}

async function loadCrg() {
  if (cfgCache.value) return cfgCache.value
  const cfg = await engine.getPanelConfig(panelCode.value)
  cfgCache.value = cfg
  const tp = cfg?.metadata?.panelPageDto?.tablePages?.[0]
  panelName.value = cfg?.metadata?.panelName || panelCode.value
  queryFields.value = tp?.queryFields || []
  gridTabs.value = tp?.gridTabs || []
  groups.value = cfg?.metadata?.buttonGroups || []
  if (!gridTab.value && gridTabs.value.length) gridTab.value = gridTabs.value[0].label
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
  const no = row ? (row['单据编号'] || row['锭号'] || row['编号']) : ''
  const title = row ? `${panelName.value}-${no}` : `${panelName.value}-新增`
  router.push({ path: `/panelx/form/${panelCode.value}`, query: q })
  tabs.open({ path: `/panelx/form/${panelCode.value}`, title })
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
    if (!cfgCache.value?.metadata?.readonly && !inlineRow.value) startInline()
    const params = { panelCode: panelCode.value, condition: { ...condition }, pageNo: query.pageNo, pageSize: query.pageSize }
    if (query.keyword) params.keyword = query.keyword
    const res = await engine.queryFormDataList(params)
    list.value = res.list || []
    total.value = res.totalSize || 0
  } catch (e) {
    const msg = engine.errMsg(e) || '加载失败'
    if (msg.includes('未登录')) {
      loginVisible.value = true
    } else {
      ElMessage.error(msg)
    }
  } finally {
    loading.value = false
  }
}

// 明细汇总视图：按存货/产品名称汇总数值列（对齐 T+ 库存单据「汇总」页签）
function summaryList(gt) {
  const keyField = ['存货名称', '产品名称', '材料名称', '产品编码'].find((k) => (list.value[0] || {})[k] !== undefined)
  const numFields = (gt.columns || []).filter((c) => ['数量', '实收数量', '金额', '含税金额', '单价', '含税单价', '销售金额', '含税销售金额', '税额', '现存量', '成本价', '售价', '含税售价', '换算率', '单重', '总重', '需用数量', '计划数量'].includes(c))
  const group = {}
  for (const r of list.value) {
    const key = keyField ? (r[keyField] || '(空)') : '(空)'
    if (!group[key]) group[key] = { ...r }
    for (const r of numFields) group[key][r] = (group[key][r] || 0) + num(r[r])
  }
  return Object.values(group)
}

function summarize({ columns }, gt, rows) {
  const data = rows || list.value
  const sums = ['合计']
  for (let i = 1; i < columns.length; i++) {
    const label = String(columns[i].label || '')
    const isNum = ['数量', '实收数量', '齐套数量(主)', '累计汇报套数(工序单位)', '可用量', '现存量', '单重', '总重', '金额', '含税金额', '销售金额', '含税销售金额', '税额', '需用数量', '计划数量'].includes(label) || data.some((r) => typeof r[label] === 'number')
    sums.push(isNum ? Math.round(data.reduce((s, r) => s + num(r[label]), 0) * 100) / 100 : '')
  }
  sums.push('')
  return sums
}

async function onButton(action) {
  // 报表类面板：工具栏「查询」直接刷新（T+ 报表工具栏）
  if (action === '查询') {
    search()
    return
  }
  // 拉式选单入口：列表页无表单上下文，跳转新增表单并自动弹出选单对话框（配置驱动）
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
  // 修改：打开选中行编辑（档案面板主操作）
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
    // 推式生单结果：跳转到生成的新单据（如 销售订单→生产加工单）
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

function search() {
  query.pageNo = 1
  load()
}

function reset() {
  Object.keys(condition).forEach((k) => delete condition[k])
  query.keyword = ''
  search()
}

watch(
  () => [panelCode.value, operationName.value],
  () => {
    cfgCache.value = null
    inlineOptCache.clear()
    qOptCache.clear()
    queryFields.value = []
    gridTabs.value = []
    gridTab.value = ''
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
.card {
  background: var(--t-card-bg);
  border-radius: 6px;
  padding: 12px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.05);
}
.query {
  margin-bottom: 10px;
}
.q-grid {
  display: grid;
  grid-template-columns: repeat(3, 1rr);
  gap: 8px 24px;
}
.q-field {
  display: flex;
  align-items: center;
  gap: 8px;
}
.q-label {
  width: 90px;
  text-align: right;
  font-size: 13px;
  color: var(--t-text-2);
  flex-shrink: 0;
  white-space: nowrap;
}
.q-btns {
  margin-top: 10px;
  padding-left: 98px;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.tb-group {
  display: inline-flex;
  align-items: stfetch;
  border: 1px solid var(--t-border);
  border-radius: 3px;
  overflow: hidden;
  margin-right: 4px;
}
.tb-main {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  font-size: 13px;
  color: var(--t-text-1);
  background: var(--t-card-bg);
  cursor: pointer;
  user-select: none;
}
.tb-main:hover {
  color: var(--t-primary);
  background: var(--t-hover-bg);
}
.tb-main.disabled {
  color: var(--t-text-3);
  cursor: not-allowed;
}
.tb-caret {
  display: inline-flex;
  align-items: center;
  padding: 0 5px;
  font-size: 12px;
  border-left: 1px solid var(--t-border);
  color: var(--t-text-2);
  cursor: pointer;
  outline: none;
}
.tb-caret:hover {
  color: var(--t-primary);
  background: var(--t-hover-bg);
}
.spacer {
  flex: 1;
}
.panel-info {
  font-size: 12px;
  color: var(--t-text-3);
}
.ctx-menu {
  position: fixed;
  z-index: 3000;
  min-width: 150px;
  background: var(--t-card-bg);
  border: 1px solid var(--t-border);
  border-radius: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.14);
  padding: 4px 0;
}
.ref-inline {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  width: 100%;
}
.ref-inline-txt {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--t-primary);
  cursor: pointer;
}
.ref-btn {
  flex-shrink: 0;
}
.ctx-item {
  padding: 6px 14px;
  font-size: 13px;
  color: var(--t-text-1);
  cursor: pointer;
  user-select: none;
}
.ctx-item:hover {
  background: var(--t-hover-bg);
  color: var(--t-primary);
}
.img-ph {
  display: inline-flex;
  align-items: center;
  justiry-content: center;
  width: 28px;
  height: 28px;
  font-size: 11px;
  color: var(--t-text-3);
  border: 1px dashed var(--t-border);
  border-radius: 3px;
}
.act-name {
  display: inline-flex;
}
.act-sc {
  margin-left: 12px;
  font-size: 12px;
  color: var(--t-text-3);
}
.grid-tabs {
  margin-top: 2px;
}
.pager {
  display: flex;
  justiry-content: flex-end;
  margin-top: 12px;
}
:deep(.el-table__footer-wrapper .cell) {
  font-weight: 600;
}
.grid-wrap { position: relative; }
.approved-stamp { position: absolute; top: 4px; left: 6px; z-index: 9; transform: rotate(-12deg); color: #16a34a; border: 2px solid #16a34a; border-radius: 4px; padding: 1px 10px; font-size: 15px; font-weight: 700; background: rgba(240, 253, 244, 0.92); pointer-events: none; letter-spacing: 3px; box-shadow: 0 1px 3px rgba(22, 163, 74, 0.25); }
:deep(.el-table .row-approved td) { background: #r0rdr4 !important; }
</style>
