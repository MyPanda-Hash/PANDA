<template>
  <div class="panelx-list" @click="closeCtx">
    <!-- ══════════ ① 顶部工具栏（T+ 灰条 + 单据翻页）══════════ -->
    <div class="tools">
      <div class="tb-group" v-for="(g, gi) in groups" :key="'g' + gi">
        <span class="tb-main" :class="{ disabled: isDisabled(btnName(g)) }" @click="onButton(btnName(g))">
          <span class="act-name">{{ g.name }}</span>
        </span>
        <span v-if="actsOf(g).length > 1" class="tb-caret" @click.stop="toggleGroup(gi)">▼</span>
        <div v-if="openGroup === gi" class="tb-menu">
          <div class="ctx-item" v-for="a in actsOf(g)" :key="a" @click="onGroupAction(a)">{{ a }}</div>
        </div>
      </div>
      <div class="tools-right">
        <span class="doc-chip">单据：{{ cur['编号'] || cur['单据编号'] || '-' }}</span>
        <span v-if="cur['类别']" class="doc-cat">{{ cur['类别'] }}</span>
        <span v-if="cur['单据状态']" class="doc-status" :class="cur['单据状态']">{{ cur['单据状态'] }}</span>
        <span class="page-btn" title="首页" @click="pageFirst">◁</span>
        <span class="page-btn" title="上一张" @click="page(-1)">◀</span>
        <span class="page-no">第 {{ curNo }}/{{ pageTotal }} 张</span>
        <span class="page-btn" title="下一张" @click="page(1)">▶</span>
        <span class="page-btn" title="末页" @click="pageLast">▷</span>
      </div>
    </div>

    <!-- ══════════ ② 表头字段区（label 在上、输入在下）══════════ -->
    <div class="fields udl-fields">
      <!-- 单据态：所有状态都显示当前单据表头；仅未保存新页 / 草稿可编辑 -->
      <template v-if="isDocumentPanel">
        <div class="field" v-for="f in headFields" :key="f.dataName">
          <label :class="{ req: f.isRequired }">{{ fieldLabel(f) }}</label>
          <el-input v-if="isText(f)" v-model="cur[f.dataName]" :disabled="headerFieldDisabled(f)" :placeholder="fieldLabel(f)" />
          <el-input-number v-else-if="isNumber(f)" v-model="cur[f.dataName]" :disabled="headerFieldDisabled(f)" :controls="false" />
          <div v-else-if="isRef(f)" class="ref-ctl">
            <el-input :model-value="refText(f, cur[f.dataName])" readonly :disabled="headerFieldDisabled(f)" :placeholder="editing ? '点击选择' : ''" @click="openRefPick(f)" />
            <el-button v-if="editing && !fieldLocked(f)" class="ref-btn" size="small" :icon="Search" @click="openRefPick(f)" />
          </div>
          <el-select v-else-if="isSelect(f)" v-model="cur[f.dataName]" :disabled="headerFieldDisabled(f)" filterable clearable allow-create>
            <el-option v-for="o in (f.options || [])" :key="o.value ?? o" :label="o.label ?? o" :value="o.value ?? o" />
          </el-select>
          <el-date-picker v-else-if="isDate(f)" v-model="cur[f.dataName]" :disabled="headerFieldDisabled(f)" type="date" value-format="YYYY-MM-DD" />
          <el-switch v-else-if="isBool(f)" v-model="cur[f.dataName]" :disabled="headerFieldDisabled(f)" />
          <el-input v-else v-model="cur[f.dataName]" :disabled="headerFieldDisabled(f)" :placeholder="fieldLabel(f)" />
        </div>
      </template>
      <!-- 查询态：查询字段（search 用） -->
      <template v-else>
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
      </template>
    </div>

    <div class="body" v-loading="loading">
      <!-- ══════════ ③b 主表预览表格（配置 mainTable 时显示：主表字段列，点行切换当前单据，明细联动） -->
      <div v-if="mainGrid" class="main-grid">
        <div class="dt-head">
          <span class="dt-tab on">{{ mainGrid.label }}</span>
          <span class="dt-ics">
            <span class="dt-ic" title="点行切换当前单据">定位</span>
          </span>
        </div>
        <el-table :data="mainRows" border size="small" :row-class-name="mainRowCls" @row-click="onMainRowClick" @row-dblclick="openMaintain">
          <el-table-column type="index" label="序号" width="60" align="center" :index="(i) => i + 1" />
          <el-table-column v-for="c in mainCols" :key="c" :prop="c" :label="c" min-width="110" show-overflow-tooltip />
        </el-table>
      </div>
      <!-- ══════════ ③ 表中 · 明细区块（配置驱动：区块内多页签，同 T+）══════════ -->
      <div class="detail" v-for="b in blocks" :key="b.id">
        <div v-if="isApproved" class="approved-stamp">已审批</div>
        <div class="dt-head">
          <span v-for="it in headItems(b)" :key="it.kind + it.key" class="dt-tab" :class="{ on: isOn(b, it) }" @click="switchTab(b, it)">{{ it.label }}</span>
          <span v-if="b.id === 'B' && activeTab(b).key === 'materials' && selectedProduct" class="filter-hint">当前产品：{{ selectedProduct }} 的 BOM 子件</span>
          <span class="dt-ics">
            <el-button v-if="editingDetail(b)" class="add-data-btn" size="small" type="primary" :icon="Plus" @click="addDetailRow(b)">新增数据</el-button>
            <span class="dt-ic" v-for="ic in b.isMain ? iconA : iconB" :key="ic" @click="onIcon(ic, b)">{{ ic }}</span>
          </span>
        </div>
        <el-table
          :data="blockRows(b)"
          :height="tableH(b)"
          border
          size="small"
          :show-summary="tabView(b, activeTab(b)) !== 'summary'"
          :summary-method="sumMethod"
          sum-text="合计"
          :row-key="(row) => row._placeholder ? row._placeholderKey : row"
          :row-class-name="(o) => rowCls(o, b)"
          @selection-change="(r) => (delSel = r)"
          @row-contextmenu="(row, col, ev) => onCtx(ev, row, b)"
          @row-dblclick="() => onRowDblclick(cur)"
          @row-click="(row) => onRowClick(row, b)"
          @click.capture="(e) => onTableClick(b, e)"
        >
          <el-table-column v-if="delMode && b.isMain" type="selection" width="45" fixed="left" />
          <el-table-column
            v-for="(c, ci) in blockCols(b)"
            :key="c.prop"
            :prop="c.prop"
            :label="c.label"
            :min-width="c.width"
            :align="c.align"
            :class-name="editingDetail(b) && c.f.dataType === '参照' && !c.f.computed ? 'detail-ref-col' : ''"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              <!-- 材料编码（读态）：下级 BOM 红 * -->
              <span v-if="c.prop === '材料编码' && activeTab(b).key === 'materials' && !editingDetail(b)" class="mat-cell">
                <span>{{ row[c.prop] }}</span>
                <span v-if="hasSubBom(row[c.prop])" class="mat-star" title="该材料有下级子件 BOM，点击行查看">*</span>
              </span>
              <!-- 编辑态：按 dataType 渲染单元格控件（未保存新页 / 草稿单据） -->
              <template v-else-if="editingDetail(b) && !row._placeholder && !c.f.computed">
                <button
                  v-if="c.f.dataType === '参照'"
                  type="button"
                  class="detail-ref-cell"
                  :title="refText(c.f, row[c.prop]) || undefined"
                  @click.stop="openDetailRef(c.f, row, b)"
                >{{ refText(c.f, row[c.prop]) || `选择${engine.refPanelName(c.f)}` }}</button>
                <el-select v-else-if="c.f.dataType === '下拉框'" v-model="row[c.prop]" filterable allow-create style="width: 100%" @change="applyCalc">
                  <el-option v-for="o in (c.f.options || [])" :key="o" :label="o.label ?? o" :value="o.value ?? o" />
                </el-select>
                <el-switch v-else-if="c.f.dataType === '是否'" v-model="row[c.prop]" />
                <el-image v-else-if="c.f.dataType === '图片'" :src="row[c.prop] || ''" fit="contain" style="width: 34px; height: 34px">
                  <template #error><span class="img-ph">图</span></template>
                </el-image>
                <el-input-number v-else-if="c.f.dataType === '小数' || c.f.dataType === '整数'" v-model="row[c.prop]" :controls="false" style="width: 100%" @change="applyCalc" />
                <el-date-picker v-else-if="c.f.dataType === '日期'" v-model="row[c.prop]" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
                <el-input v-else v-model="row[c.prop]" />
              </template>
              <!-- 编辑态占位行：点击物化一行（首列显示 + 提示） -->
              <span v-else-if="editingDetail(b) && row._placeholder" class="cell-add" @click.stop="materializeDetailRow(b)">{{ ci === 0 ? '+' : '' }}</span>
              <!-- 只读 / 占位行：纯文本 -->
              <span v-else>{{ row[c.prop] }}</span>
            </template>
          </el-table-column>
          <el-table-column v-if="editingDetail(b)" label="操作" width="50" align="center" fixed="right">
            <template #default="{ row }">
              <el-icon v-if="!row._placeholder" class="del" @click="removeDetailRow(b, row)"><Delete /></el-icon>
            </template>
          </el-table-column>
        </el-table>
      </div>

    </div>

    <!-- ══════════ ④ 表尾（固定在页面底部，滚动明细时始终可见；备注 + 审核行）══════════ -->
    <div v-if="showFooter" class="footer">
      <div class="remark">
        <label>备注</label>
        <el-input v-model="remarkText" size="small" placeholder="" :disabled="isDocumentPanel && !editing" />
      </div>
      <div class="footer-hr"></div>
      <div class="audit-line">
        <span>制单人：{{ cur['制单人'] || cur['发起人编号'] || '' }}</span>
        <span>审核人：{{ cur['审核人'] || '' }}</span>
        <span>审核日期：{{ cur['审核日期'] || '' }}</span>
        <span>审核时间：{{ cur['审核时间'] || '' }}</span>
        <span>打印次数：{{ cur['打印次数'] ?? 0 }}</span>
        <span>创建时间：{{ cur['创建时间'] || '' }}</span>
        <span>审核意见：{{ cur['审核意见'] || '-' }}</span>
      </div>
    </div>

    <!-- ══════════ 表格右键菜单（对齐真实 T+ 明细右键）══════════ -->
    <div v-if="ctx.visible" class="ctx-menu" :style="{ left: ctx.x + 'px', top: ctx.y + 'px' }">
      <div class="ctx-item" v-for="it in ctxItems" :key="it" @click="onCtxItem(it)">{{ it }}</div>
    </div>

    <PanelxLogin v-model="loginVisible" @success="onPanelxLogin" />
    <RefPickDialog v-model="refVisible" :field="refPick?.field" :mode="refPick?.mode" @confirm="onRefConfirm" />
    <NewVoucherDialog v-model:visible="newVisible" :panelCode="panelCode" :panel-name="panelName" @saved="onNewSaved" />
    <BomDialog v-model="bomVisible" :item="bomItem" :parentDoc="bomParent" @saved="onBomSaved" />
    <SubBomDialog v-model="subBomVisible" :material="subBomMaterial" :bom="subBomBom" />
    <ImportDialog v-model="impVisible" :fields="impFields" :target-label="impLabel" @imported="onImported" />
    <ApprovalHistoryDialog v-model="approvalVisible" :panelCode="panelCode" :formNo="approvalNo" />
    <el-dialog v-model="catPickVisible" title="选择类别添加存货" width="360px" append-to-body>
      <el-select v-model="catPick" style="width: 100%" placeholder="选择存货类别">
        <el-option v-for="c in ['产成品', '原材料', '辅助材料', '包装物', '半成品']" :key="c" :label="c" :value="c" />
      </el-select>
      <div style="margin-top:8px;font-size:12px;color:#999">存货固定 5 张类别单据，新增物品进入对应类别单据的明细中</div>
      <template #footer>
        <el-button @click="catPickVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!catPick" @click="gotoCategory">打开该类别单据</el-button>
      </template>
    </el-dialog>
    <SelectVoucherDialog v-model="selVisible" :panelCode="panelCode" :config="selCfg" @generated="onSelGenerated" />
    <DetailMaintainDialog v-model="maintainVisible" :panel-code="panelCode" :row="maintainRow" @saved="onMaintainSaved" />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, Delete } from '@element-plus/icons-vue'
import { useTabsStore } from '@/stores/tabs'
import { useUserStore } from '@/stores/user'
import * as engine from '@/business/engine'
import PanelxLogin from './PanelxLogin.vue'
import RefPickDialog from './RefPickDialog.vue'
import NewVoucherDialog from './NewVoucherDialog.vue'
import ApprovalHistoryDialog from './ApprovalHistoryDialog.vue'
import SelectVoucherDialog from './SelectVoucherDialog.vue'
import BomDialog from './BomDialog.vue'
import SubBomDialog from './SubBomDialog.vue'
import ImportDialog from './ImportDialog.vue'
import DetailMaintainDialog from './DetailMaintainDialog.vue'

const loginVisible = ref(false)
function onPanelxLogin() {
  loginVisible.value = false
  load()
}

const route = useRoute()
const router = useRouter()
const tabs = useTabsStore()
const user = useUserStore()

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
const newVisible = ref(false)
const approvalVisible = ref(false)
const approvalNo = ref('')
const selVisible = ref(false)
const impVisible = ref(false)
const impFields = ref([])
const impLabel = ref('明细')
const selCfg = ref(null)
const bomVisible = ref(false)
const bomItem = ref(null)
const bomParent = ref(null)
const delMode = ref(false)
const delSel = ref([])
const catPickVisible = ref(false)
const catPick = ref('')

// 就地新增：未保存新页在 list 中的下标（无新页为 -1）；参照弹窗状态
const draftIdx = ref(-1)
const refVisible = ref(false)
const refPick = ref(null)

// 产成品→材料联动：当前选中产成品（列表页单据流览内点击产成品明细行）
const selectedProduct = ref(null)
const selectedBomCodes = ref([])

// 材料下级 BOM（红 * + 弹窗）：存货编码 → _bom 数组
const subBomMap = ref({})
const subBomVisible = ref(false)
const subBomMaterial = ref(null)
const subBomBom = ref([])

// ---------- 明细维护弹窗（主表双击行打开：在弹窗内维护该单明细，新增/删除/保存） ----------
const maintainVisible = ref(false)
const maintainRow = ref(null)
function openMaintain(row) {
  if (!row || row._placeholder) return
  maintainRow.value = row
  maintainVisible.value = true
}
function onMaintainSaved() {
  load()
}

// ---------- 单据浏览器：翻页切单据 ----------
const curIdx = ref(0)
const cur = computed(() => {
  const l = list.value
  if (!l.length) return {}
  return l[Math.min(curIdx.value, l.length - 1)]
})
const curNo = computed(() => (list.value.length ? Math.min(curIdx.value, list.value.length - 1) + 1 : 0))
const pageTotal = computed(() => total.value + (hasDraft.value ? 1 : 0))

// 单据类面板始终显示当前单据表头；未保存新页 _draft 或草稿才允许编辑。
const DOCUMENT_CATEGORIES = new Set(['单据', '期初单据'])
const isDocumentPanel = computed(() => DOCUMENT_CATEGORIES.has(cfgCache.value?.metadata?.panelCategory))
const inlineEditCapable = computed(() => isDocumentPanel.value && !cfgCache.value?.metadata?.singleDoc && panelCode.value !== 'INV')
const editing = computed(() => inlineEditCapable.value && !!cur.value && (cur.value._draft === true || cur.value['单据状态'] === '草稿'))
const hasDraft = computed(() => draftIdx.value >= 0 && list.value[draftIdx.value]?._draft === true)
const headFields = computed(() => (cfgCache.value?.dataSchema?.fields || []).filter((f) => !f.hidden))
// 明细区块仅在「明细」视图下就地编辑（汇总视图为计算聚合，只读）
function editingDetail(b) {
  return editing.value && tabView(b, activeTab(b)) === 'detail'
}

watch(cur, (v) => {
  current.value = v
  // 产成品→材料联动：默认选中第一个产成品（材料明细只显示其 BOM 子件，不整单全显示）
  const blk = blocks.value.find((x) => x.id === 'A')
  const tab = blk ? activeTab(blk) : null
  if (tab && tab.key === 'products') {
    const first = detailRows(tab)[0]
    if (first && first['产品编码'] !== selectedProduct.value) selectProduct(first['产品编码'])
  } else if (selectedProduct.value) {
    selectedProduct.value = null
    selectedBomCodes.value = []
  }
})

async function page(delta) {
  const l = list.value
  if (!l.length) return
  const leave = await confirmLeaveDraft()
  if (leave === 'abort' || leave === 'saved') return
  const base = curIdx.value // 草稿被移除后可能越界一位（= 原草稿位置），供下方按 delta 落点
  const nxt = base + delta
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
    return
  }
  // 无更多页可走：收拢越界的 curIdx（草稿丢弃后停在原位置时）
  if (curIdx.value >= l.length) curIdx.value = l.length - 1
}

async function pageFirst() {
  if (!list.value.length) return
  const leave = await confirmLeaveDraft()
  if (leave === 'abort' || leave === 'saved') return
  if (query.pageNo > 1) {
    query.pageNo = 1
    await load()
  }
  curIdx.value = 0
}

async function pageLast() {
  if (!list.value.length) return
  const leave = await confirmLeaveDraft()
  if (leave === 'abort' || leave === 'saved') return
  const lastPage = Math.max(1, Math.ceil(total.value / query.pageSize))
  if (query.pageNo < lastPage) {
    query.pageNo = lastPage
    await load()
  }
  curIdx.value = list.value.length - 1
}

// ══════════ 明细区块模型（配置驱动，见 docs/页面开发规范.md）══════════
// 视图状态：view[blockId + ':tab'] = 当前页签 key；view[blockId + ':' + tabKey + ':view'] = 'detail' | 'summary'
const view = reactive({})
const blocks = computed(() => buildBlocks(cfgCache.value))

// ══════════ 主表预览表格（mainTable 配置，如工艺路线主表；点行切换当前单据，下方明细联动）══════════
const mainGrid = computed(() => {
  const tp = cfgCache.value?.metadata?.panelPageDto?.tablePages?.[0]
  return tp?.mainTable || null
})
const mainCols = computed(() => (mainGrid.value?.columns || []).filter((c) => c !== '序号'))
// 主表固定 5 行（不足补占位，与明细区一致）
const mainRows = computed(() => {
  const l = list.value
  if (!l.length) return []
  const rows = l.slice(0, 5).map((r) => r)
  while (rows.length < 5) rows.push({ _placeholder: true })
  return rows
})
async function onMainRowClick(row) {
  const i = list.value.indexOf(row)
  if (i < 0 || i === curIdx.value) return
  const leave = await confirmLeaveDraft()
  if (leave === 'abort' || leave === 'saved') return
  if (i < list.value.length) curIdx.value = i
}
function mainRowCls({ row }) {
  if (row._placeholder) return 'ph-row'
  return row === cur.value ? 'row-cur' : ''
}

function buildBlocks(cfg) {
  if (!cfg) return []
  const tp = cfg.metadata?.panelPageDto?.tablePages?.[0]
  const gt = tp?.gridTabs || []
  const tabs = cfg.detail?.tabs || []
  const out = []
  const mkTab = (key, label, cols, summaryItems, sumLabel, hasSummary) => ({ key, label, cols, summaryItems, sumLabel, hasSummary })
  // A 区：优先 gridTabs[0]，其次 detail.tabs[0]（页签 = 明细 + 汇总）
  const first = tabs[0]
  const mainCols = gt[0]?.columns || (first ? (first.fields || []).filter((f) => !f.hidden).map((f) => f.dataName) : [])
  if (mainCols.length) {
    const sumItems = first?.summaryItems || []
    const label = gt[0]?.label || first?.label || '明细'
    const hasSummary = !!(gt.length > 1 && gt[1]?.summary) || sumItems.length > 0
    out.push({
      id: 'A', isMain: true,
      tabs: [mkTab(first?.key || 'items', label, mainCols, sumItems, gt[1]?.label || (sumItems.length ? label + '汇总' : ''), hasSummary)],
    })
  }
  // B 区：detail.tabs[1..n] 合并为一个区块、页签内切换（同 T+：材料明细/工序明细共区块）
  const rest = tabs.slice(1).map((t) => {
    const cols = (t.fields || []).filter((f) => !f.hidden).map((f) => f.dataName)
    return cols.length ? mkTab(t.key, t.label, cols, t.summaryItems || [], t.summaryItems?.length ? t.label + '汇总' : '', !!(t.summaryItems?.length)) : null
  }).filter(Boolean)
  if (rest.length) out.push({ id: 'B', isMain: false, tabs: rest })
  return out
}

function activeTab(b) {
  const k = view[b.id + ':tab']
  return b.tabs.find((t) => t.key === k) || b.tabs[0]
}

function tabView(b, t) {
  return view[b.id + ':' + t.key + ':view'] === 'summary' ? 'summary' : 'detail'
}

// 页签头条目：明细页签 + 汇总页签 依次展开
function headItems(b) {
  const out = []
  for (const t of b.tabs) {
    out.push({ kind: 'tab', key: t.key, label: t.label })
    if (t.hasSummary) out.push({ kind: 'sum', key: t.key, label: t.sumLabel })
  }
  return out
}

function isOn(b, item) {
  const cur = activeTab(b)
  if (cur.key !== item.key) return false
  return item.kind === 'sum' ? tabView(b, cur) === 'summary' : tabView(b, cur) !== 'summary'
}

function switchTab(b, item) {
  view[b.id + ':tab'] = item.key
  view[b.id + ':' + item.key + ':view'] = item.kind === 'sum' ? 'summary' : 'detail'
}

// 明细数据：单据类取 cur.detail[block.key]；平铺类（档案/报表）把当前行当明细
function detailRows(b) {
  const d = cur.value.detail
  if (d && Array.isArray(d[b.key])) return d[b.key]
  if ((b.cols || []).some((c) => cur.value[c] !== undefined)) return [cur.value]
  return []
}

const KNOWN_NUM = ['数量', '实收数量', '报工数量', '合格数量', '不合格数量', '工价', '计时/计件金额', '金额', '含税金额', '含税单价', '单价', '税额', '现存量', '需用数量', '损耗数量', '计划数量', '累计领用数量', '齐套数量(主)', '累计汇报套数(工序单位)', '总重', '单重', '委外金额', '委外税额', '委外含税金额', '换算率', '可报工数量', '累计汇报数量']

function numericCols(rows, b) {
  const fromItems = (b.summaryItems || []).map((it) => it.field)
  const known = (b.cols || []).filter((c) => KNOWN_NUM.includes(c) && rows.every((r) => Number.isFinite(Number(r[c]))))
  return [...new Set([...fromItems, ...known])].filter((c) => (b.cols || []).includes(c))
}

function groupKeyOf(b) {
  return b.keyField || ['存货编码', '产品编码', '材料编码', '存货名称', '产品名称', '材料名称'].find((k) => (b.cols || []).includes(k)) || (b.cols || [])[0] || '编号'
}

// 汇总：按 编码/名称 分组 + 合计行（对齐 T+ 汇总页签）
function summaryRows(rows, b) {
  if (!rows.length) return []
  const keyField = groupKeyOf(b)
  const numeric = numericCols(rows, b)
  const group = new Map()
  for (const r of rows) {
    const k = r[keyField] || '(空)'
    if (!group.has(k)) {
      // 先剔除汇总字段再复制首行，避免分组行把首行原值又累加一次（翻倍 bug）
      const base = { ...r }
      for (const c of numeric) delete base[c]
      group.set(k, base)
    }
    const g = group.get(k)
    for (const c of numeric) g[c] = (g[c] || 0) + num(r[c])
  }
  const out = [...group.values()]
  const total = {}
  for (const c of numeric) total[c] = Math.round(rows.reduce((a, r) => a + num(r[c]), 0) * 100) / 100
  out.push({ [keyField]: '合计', ...total })
  return out
}

// 所有表格固定展示 5 行：不足补空占位行（{_placeholder:true}），超出 5 行鼠标滚动（见 docs/页面开发规范.md）
const MIN_ROWS = 5
const ROW_H = 31
const HEAD_H = 32
const FOOT_H = 32

function blockData(b) {
  const t = activeTab(b)
  let rows = detailRows(t)
  // 产成品→材料联动过滤：点产成品行后，材料明细只显示该产品的 BOM 子件（子件BOM 优先，材料编码兜底）
  if (t.key === 'materials' && selectedProduct.value) {
    const byBom = rows.filter((m) => m['子件BOM'] === selectedProduct.value)
    if (byBom.length) rows = byBom
    else {
      const byCode = rows.filter((m) => selectedBomCodes.value.includes(m['材料编码']))
      if (byCode.length) rows = byCode
    }
  }
  return tabView(b, t) === 'summary' ? summaryRows(rows, t) : rows
}

function blockRows(b) {
  const out = blockData(b).map((r) => r)
  const missing = MIN_ROWS - out.length
  for (let i = 0; i < missing; i++) out.push({ _placeholder: true, _placeholderKey: `${activeTab(b).key}-${out.length + i}` })
  return out
}

function tableH(b) {
  const hasFooter = tabView(b, activeTab(b)) !== 'summary'
  return HEAD_H + MIN_ROWS * ROW_H + (hasFooter ? FOOT_H : 0)
}

function blockCols(b) {
  const t = activeTab(b)
  return (t.cols || []).map((c) => {
    const f = fieldDefOf(c)
    return { prop: c, label: c, width: colW(f), align: f.dataType === '小数' || f.dataType === '整数' ? 'right' : 'left', f }
  })
}

const showFooter = computed(() => {
  const cfg = cfgCache.value
  return cfg?.metadata?.panelCategory === '单据' || (cfg?.detail?.tabs || []).length > 0
})

function num(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

function sumMethod({ columns, data }) {
  const sums = []
  // 占位行不参与合计
  const real = (data || []).filter((r) => !r._placeholder)
  columns.forEach((col, i) => {
    if (i === 0) {
      sums[i] = '合计'
      return
    }
    // 只对「小数/整数」类型的字段求和：纯数字文本（身份证号/手机号/编码）不参与合计
    const f = fieldDefOf(col.property)
    const isNumeric = f && (f.dataType === '小数' || f.dataType === '整数')
    const vals = real.map((r) => Number(r[col.property]))
    sums[i] = isNumeric && vals.length && vals.every((v) => Number.isFinite(v)) ? Math.round(vals.reduce((a, b) => a + b, 0) * 100) / 100 : ''
  })
  return sums
}

// 审批流：当前单据已审批 → 表格左上角「已审批」角标；已审批明细行浅绿底色
const isApproved = computed(() => cur.value && cur.value['审批状态'] === '已审批')

function rowCls({ row }, b) {
  if (row._placeholder) return 'ph-row'
  if (b && b.id === 'A' && row['产品编码'] && row['产品编码'] === selectedProduct.value) return 'prod-selected'
  if (row['审批状态'] === '已审批') return 'row-approved'
  return ['产品编码', '材料编码', '存货编码', '存货名称', '产品名称', '材料名称'].some((k) => row[k] === '合计') ? 'sum-row' : ''
}

// ---------- 明细表格列宽（按字段类型推算，横向滚动同 T+） ----------
function colW(f) {
  const t = f.dataType || '文本'
  if (t === '是否') return 74
  if (t === '图片') return 56
  if (t === '小数' || t === '整数') return 104
  if (t === '日期' || t === '日期时间') return 136
  const n = f.label || f.dataName || ''
  return n.length <= 2 ? 96 : Math.min(Math.max(n.length * 16 + 30, 96), 220)
}

// ---------- 明细右键/图标操作（作用于当前活动区块） ----------
const ctxItems = ['定位', '复制到剪贴板', '从剪贴板粘贴', '另存为EXCEL模板', '批量修改', '销售订单查询', '存货中心', '更多']
const iconA = ['☑ Ctrl+V列粘贴', '定位', '复制到剪贴板', '从剪贴板粘贴', '另存为EXCEL模板', '批量修改', '销售订单查询', '存货中心', '更多▼']
const iconB = ['现存量提取', '定位', '复制到剪贴板', '从剪贴板粘贴', '另存为EXCEL模板', '批量修改', '更多▼']

const ctxBlock = ref(null)
const ctx = reactive({ visible: false, x: 0, y: 0, row: null })

const activeCols = computed(() => (ctxBlock.value ? blockCols(ctxBlock.value) : []))
const activeData = computed(() => (ctxBlock.value ? blockData(ctxBlock.value) : []))

function onCtx(ev, row, b) {
  ev.preventDefault()
  ev.stopPropagation()
  ctxBlock.value = b
  ctx.row = row
  ctx.x = ev.clientX
  ctx.y = ev.clientY
  ctx.visible = true
}

function closeCtx() {
  ctx.visible = false
  openGroup.value = -1
}

// ---------- 审批按钮权限（提交审批/审批情况公开；审批通过/驳回需角色审批权限） ----------
const APPROVE_ACTIONS = ['审批通过', '审批驳回']
function filterGroups(raw) {
  const canApprove = user.isAdmin || user.approvePanels.includes(panelCode.value)
  if (canApprove) return raw
  return (raw || [])
    .map((g) => ({ ...g, actions: (g.actions || g.items || []).filter((a) => !APPROVE_ACTIONS.includes(a)) }))
    .filter((g) => (g.actions || []).length > 0)
}

// ---------- 工具栏分组（配置 {name, actions}：主按钮=第一个 action，actions>1 显示 ▼ 下拉） ----------
const openGroup = ref(-1)
function actsOf(g) {
  return g.actions || g.items || []
}
function btnName(g) {
  return actsOf(g)[0] || g.name
}
function toggleGroup(gi) {
  openGroup.value = openGroup.value === gi ? -1 : gi
}
function onGroupAction(a) {
  openGroup.value = -1
  onButton(a)
}

async function copyActive() {
  const cols = activeCols.value
  const rows = (activeData.value || []).filter((r) => !r._placeholder)
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
  const rows = (activeData.value || []).filter((r) => !r._placeholder)
  const esc = (v) => {
    const s = String(v ?? '')
    return /[",\n\t]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s
  }
  const csv = '\ufeff' + cols.map((c) => esc(c.label)).join(',') + '\n' + rows.map((r) => cols.map((c) => esc(r[c.prop])).join(',')).join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${panelCode.value}-${ctxBlock.value?.id || 'list'}.csv`
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('已导出 ' + a.download)
}

function onIcon(it, b) {
  ctxBlock.value = b
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
    ElMessage.success('已定位：' + (row['产品编码'] || row['材料编码'] || row['存货编码'] || row['工序编码'] || row['编号'] || ''))
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

// ---------- 就地编辑：字段类型判断 / 参照 / 明细重算（移植 PanelxForm） ----------
function isText(r) {
  return !r.dataType || r.dataType === '文本' || r.dataType === 'STRING'
}
function isNumber(r) {
  return ['小数', '整数', 'Decimal', 'Long', 'Integer', 'Double'].includes(r.dataType)
}
function isDate(r) {
  return ['日期', '时间', 'DATE', 'DateTime', 'Date'].includes(r.dataType)
}
function isBool(r) {
  return ['是否', 'Boolean', 'BOOL'].includes(r.dataType)
}
function isSelect(r) {
  return r.dataType === '下拉框'
}
function isRef(r) {
  return r.dataType === '参照' && (r.refPanel || r.ref)
}
function fieldLocked(r) {
  return !!(r.autoCode || r.computed)
}
function headerFieldDisabled(r) {
  return !editing.value || fieldLocked(r)
}
function fieldLabel(r) {
  return r.label || r.displayName || r.dataName
}
function refText(r, v) {
  return engine.refTextOf(r, v)
}

// 明细表达式计算链（对齐 PanelxForm evaluateExpr / applyCalc）
function evaluateExpr(expr, vars) {
  const tokens = String(expr).match(/\d+(?:\.\d+)?|[+\-*/()]|[^\s+\-*/()]+/g) || []
  const output = []
  const ops = []
  const prec = { '+': 1, '-': 1, '*': 2, '/': 2 }
  for (const tk of tokens) {
    if (/^[\d.]+$/.test(tk)) {
      output.push(parseFloat(tk))
    } else if (tk in prec) {
      while (ops.length && ops[ops.length - 1] !== '(' && prec[ops[ops.length - 1]] >= prec[tk]) output.push(ops.pop())
      ops.push(tk)
    } else if (tk === '(') {
      ops.push(tk)
    } else if (tk === ')') {
      while (ops.length && ops[ops.length - 1] !== '(') output.push(ops.pop())
      if (ops[ops.length - 1] === '(') ops.pop()
    } else {
      const v = vars[tk]
      if (v === undefined) throw new Error('未知变量: ' + tk)
      output.push(num(v))
    }
  }
  while (ops.length) output.push(ops.pop())
  const stack = []
  for (const t of output) {
    if (typeof t === 'number') stack.push(t)
    else {
      const b = stack.pop()
      const a = stack.pop()
      if (a === undefined || b === undefined) return 0
      stack.push(t === '+' ? a + b : t === '-' ? a - b : t === '*' ? a * b : b === 0 ? 0 : a / b)
    }
  }
  return stack[0] ?? 0
}

function productQty() {
  const rows = (cur.value.detail && cur.value.detail.products) || []
  return rows.reduce((s, r) => s + num(r['数量']), 0)
}

function applyCalc() {
  const tabs = cfgCache.value?.detail?.tabs || []
  const detail = cur.value.detail || {}
  for (const tab of tabs) {
    const rows = detail[tab.key] || []
    for (const row of rows) {
      if (tab.key === 'processes') {
        row['工序行码'] = `GX${String(num(row['加工顺序']) || rows.indexOf(row) + 1).padStart(3, '0')}`
      }
      const vars = { ...row, 产品数量: productQty() }
      for (const rule of tab.calc || []) {
        let v
        try {
          v = evaluateExpr(rule.formula, vars)
        } catch (e) {
          v = 0
        }
        if (rule.round != null) v = Math.round(v * 10 ** rule.round) / 10 ** rule.round
        if (row[rule.target] !== v) row[rule.target] = v
      }
    }
  }
}

// 新增明细行（按配置 tab.fields 填默认值；数组不存在则初始化）
function addDetailRow(b) {
  const key = activeTab(b).key
  const tabDef = (cfgCache.value?.detail?.tabs || []).find((t) => t.key === key)
  const detail = cur.value.detail || (cur.value.detail = {})
  const rows = detail[key] || (detail[key] = [])
  const row = {}
  for (const dr of tabDef?.fields || []) {
    if (dr.dataType === '小数' || dr.dataType === '整数') row[dr.dataName] = dr.defaultValue ?? 0
    else if (dr.dataType === '是否') row[dr.dataName] = dr.defaultValue ?? false
    else row[dr.dataName] = dr.defaultValue ?? ''
  }
  if (tabDef?.key === 'materials' && selectedProduct.value) row['子件BOM'] = selectedProduct.value
  rows.push(row)
  return row
}

// 占位行来自 blockRows() 的临时数组；只调用 addDetailRow 不会让该 DOM 行立刻切换为编辑态。
// 把新行内容写回当前占位对象，让整行原地变为真实明细行，同时仍保存到 cur.detail。
function materializeDetailRow(b) {
  addDetailRow(b)
}

function removeDetailRow(b, row) {
  const key = activeTab(b).key
  const arr = cur.value.detail && Array.isArray(cur.value.detail[key]) ? cur.value.detail[key] : []
  const i = arr.indexOf(row)
  if (i >= 0) arr.splice(i, 1)
}

// ---------- 参照字段弹窗（开发约束十一-1：能对应基础档案的字段弹窗拉取勾选导入） ----------
function openRefPick(f) {
  if (!editing.value || fieldLocked(f)) return
  refPick.value = { field: f, kind: 'header', code: f.dataName, mode: 'header' }
  refVisible.value = true
}

function openDetailRef(dr, row, b) {
  if (!editing.value || dr.computed) return
  refPick.value = {
    field: dr,
    kind: 'detail',
    row,
    block: b,
    tabDef: (cfgCache.value?.detail?.tabs || []).find((t) => t.key === activeTab(b).key),
    mode: 'detail',
  }
  refVisible.value = true
}

function onRefConfirm(rows) {
  const p = refPick.value
  if (!p || !rows.length) return
  const r = p.field
  const rp = r.ref && typeof r.ref === 'object' ? r.ref : r
  const refField = rp.field || rp.refField
  const multi = !!(rp.multi || rp.refMulti)
  const vals = rows.map((x) => x[refField])
  if (p.kind === 'header') {
    cur.value[p.code] = multi ? vals.join('、') : vals[0]
    const first = rows[0] || {}
    for (const m of rp.map || rp.refMap || []) {
      if (!m || first[m.from] === undefined) continue
      const to = m.to || m.from
      if (to !== p.code) cur.value[to] = first[m.from]
    }
  } else {
    const row = p.row
    const maps = rp.map || rp.refMap || []
    const applyMap = (target, srcRow) => {
      for (const m of maps) {
        if (!m || srcRow[m.from] === undefined) continue
        const to = m.to || m.from
        if (to !== r.dataName) target[to] = srcRow[m.from]
      }
    }
    const rillRow = (target, srcRow) => {
      target[r.dataName] = srcRow[refField]
      applyMap(target, srcRow)
    }
    rillRow(row, rows[0] || {})
    if (rows.length > 1 && p.block) {
      for (let i = 1; i < rows.length; i++) {
        const nr = addDetailRow(p.block)
        rillRow(nr, rows[i])
      }
    }
  }
  refVisible.value = false
  applyCalc()
  ElMessage.success(`已导入 ${rows.length} 行${engine.refPanelName(r)}数据`)
}

function emptyValue(v) {
  return v === undefined || v === null || String(v).trim() === ''
}

// 保存前校验：必填表头、必填明细页签，以及每行配置为必填的单元格。
function validate() {
  for (const f of headFields.value) {
    if (f.isRequired && emptyValue(cur.value[f.dataName])) return `${f.dataName}不能为空`
  }
  for (const tab of cfgCache.value?.detail?.tabs || []) {
    const rows = cur.value.detail?.[tab.key] || []
    if (tab.isRequired && !rows.length) return `请至少添加一行${tab.label}`
    for (let i = 0; i < rows.length; i++) {
      for (const f of tab.fields || []) {
        if (f.isRequired && emptyValue(rows[i][f.dataName])) return `${tab.label}第 ${i + 1} 行${f.dataName}不能为空`
      }
    }
  }
  return ''
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
  // 页面标题 = 真实面板名（路由 meta.title 是通用占位，配置加载后覆盖）
  document.title = panelName.value + ' · 轻MES'
  queryFields.value = tp?.queryFields || []
  // 面板可配置每页条数（如档案类大列表 pageSize=100），未配置时保持默认 20
  if (tp?.pageSize && query.pageSize !== tp.pageSize) {
    query.pageSize = tp.pageSize
    query.pageNo = 1
  }
  gridTabs.value = tp?.gridTabs || []
  groups.value = filterGroups(cfg?.metadata?.buttonGroups || [])
  return cfg
}

function isDisabled(action) {
  const st = current.value?.['单据状态']
  const map = {
    新增: catPickVisible.value, // 存货类别选择弹窗打开时禁用，防止重复触发
    删除: !current.value,
    审核: !current.value || st !== '草稿',
    弃审: !current.value || st !== '已审核',
    中止执行: !current.value || !['已审核', '生产中', '已完工'].includes(st),
    整单中止: !current.value || !['已审核', '生产中', '已完工'].includes(st),
    草稿: !current.value || st !== '已中止',
    取消中止: !current.value || st !== '已中止',
    修改: !current.value || !['已审核', '生产中', '已完工'].includes(st),
    审批情况: false,
    提交审批: !current.value || st !== '草稿',
    审批通过: !current.value || st !== '审批中',
    审批驳回: !current.value || st !== '审批中',
   驳回审批: !current.value || st !== '审批中',
    生成生产加工单: !current.value || st !== '已审核',
  }
  return map[action] === true
}

function openForm(row) {
  const q = { operationName: operationName.value }
  if (row && row['编号']) q.code = row['编号']
  const no = row ? row['单据编号'] || row['锭号'] || row['编号'] : ''
  const title = row ? `${panelName.value}-${no}` : `${panelName.value}-新增`
  router.push({ path: `/panelx/form/${panelCode.value}`, query: q })
  tabs.open({ path: `/panelx/form/${panelCode.value}`, title, query: q })
}

// 就地编辑：双击当前单据时，草稿/新页就地编辑（不跳表单）；其余仍打开表单页
function onRowDblclick(row) {
  if (editing.value) return
  openForm(row)
}

// ---------- 就地新增 / 放弃 / 保存 ----------
async function startNew() {
  if (!inlineEditCapable.value) {
    newVisible.value = true
    return
  }
  if (hasDraft.value) {
    const r = await confirmLeaveDraft()
    if (r === 'abort') { curIdx.value = draftIdx.value; return }
    // 'saved' / 'discarded'：草稿已解决，继续新建一页
  }
  try {
    const payload = await engine.getNewFormPermMatrix({ panelCode: panelCode.value, operationName: operationName.value })
    const draft = { ...payload.data, detail: {}, _draft: true }
    for (const t of payload.detail?.tabs || []) draft.detail[t.key] = []
    list.value.push(draft)
    draftIdx.value = list.value.length - 1
    curIdx.value = draftIdx.value
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '新增失败')
  }
}

// 移除未保存新页（不弹窗）。keepIdx=true 时保持 curIdx 不动（调用方随后自行导航/新建）
function removeDraft(keepIdx = false) {
  if (!hasDraft.value) return
  list.value.splice(draftIdx.value, 1)
  draftIdx.value = -1
  if (!keepIdx && curIdx.value >= list.value.length) curIdx.value = Math.max(0, list.value.length - 1)
}

// 丢弃草稿（带确认，供搜索/刷新/切面板等使用）；返回是否已丢弃
async function discardDraft() {
  if (!hasDraft.value) return true
  try {
    await ElMessageBox.confirm('存在未保存的新单据，确定丢弃？', '提示', { type: 'warning' })
  } catch (e) {
    return false
  }
  removeDraft()
  return true
}

// 保存当前未保存新页 / 草稿单据（校验 + 组装 + callButton；成功后重载并定位）
async function saveDraft(buttonName = '保存') {
  if (hasDraft.value) curIdx.value = draftIdx.value // 确保 cur 指向未保存新页
  const msg = validate()
  if (msg) { ElMessage.warning(msg); return false }
  const head = { ...cur.value }
  delete head.detail
  delete head._draft
  delete head['编号']
  delete head['单据状态']
  delete head['创建时间']
  delete head['更新时间']
  delete head['发起人编号']
  const formData = { ...head, detail: { ...(cur.value.detail || {}) } }
  if (cur.value['编号']) formData['编号'] = cur.value['编号']
  try {
    const res = await engine.callButton({ panelCode: panelCode.value, buttonName, formData, buttonParam: {} })
    const savedNo = res?.['编号'] || cur.value['编号']
    draftIdx.value = -1
    await load()
    if (savedNo) {
      const i = list.value.findIndex((r) => r['编号'] === savedNo)
      curIdx.value = i >= 0 ? i : 0
    }
    ElMessage.success(`「${buttonName}」成功`)
    return true
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '保存失败')
    return false
  }
}

// 离开未保存新页前的确认：返回 'saved' | 'discarded' | 'abort' | 'none'
async function confirmLeaveDraft() {
  if (!hasDraft.value) return 'none'
  let choice
  try {
    await ElMessageBox({
      title: '提示',
      message: '当前新单据有未保存的数据，是否保存？',
      type: 'warning',
      confirmButtonText: '保存',
      cancelButtonText: '不保存',
      showCancelButton: true,
      distinguishCancelAndClose: true,
      closeOnClickModal: false,
    })
    choice = 'save'
  } catch (e) {
    choice = e === 'cancel' ? 'discard' : 'abort'
  }
  if (choice === 'save') return (await saveDraft()) ? 'saved' : 'abort'
  if (choice === 'discard') { removeDraft(true); return 'discarded' }
  return 'abort'
}

async function onButton(action) {
  if (APPROVE_ACTIONS.includes(action) && !user.isAdmin && !user.approvePanels.includes(panelCode.value)) {
    return ElMessage.warning('当前角色无审批权限')
  }
  if (action === '查询' || action === '查找') {
    search()
    return
  }
  if (action === '导入') {
    // Excel 导入：识别 A 区主明细字段，导入后追加行并自动保存
    const blk = blocks.value.find((x) => x.id === 'A')
    const tab = blk ? activeTab(blk) : null
    if (!tab) return ElMessage.warning('该面板无明细可导入')
    // 字段定义取自面板配置 detail.tabs（blocks 的 tab 只有列名 cols）；档案面板无明细 tab → 用 dataSchema.fields
    const tabDef = (cfgCache.value?.detail?.tabs || []).find((t) => t.key === tab.key)
    const fields = (tabDef && tabDef.fields && tabDef.fields.length)
      ? tabDef.fields
      : (cfgCache.value?.dataSchema?.fields || [])
    impFields.value = (fields || []).filter((f) => !f.hidden)
    impLabel.value = tab.label || '明细'
    impVisible.value = true
    return
  }
  if (action === '选单' || action === '选销售订单' || action === '选生产加工单') {
    const sc = cfgCache.value?.selectConfig
    if (sc) {
      if (sc.generateButton) {
        // 新版选单：内嵌来源面板弹窗（勾选 + 翻页 + 生单直接生成）
        selCfg.value = sc
        selVisible.value = true
      } else {
        router.push({ path: `/panelx/form/${panelCode.value}`, query: { new: 1, select: 1 } })
        tabs.open({ path: `/panelx/form/${panelCode.value}`, title: `${panelName.value}-新增`, query: { new: 1, select: 1 } })
      }
      return
    }
    ElMessage.info('演示环境暂未实现「选单」，界面与 T+ 保持一致')
    return
  }
  if (action === '新增' || action === '新增流程') {
    if (cfgCache.value?.metadata?.singleDoc) {
      // 单单据面板（如员工档案）：不新建第二张单据，直接打开已有单据（无单据时新建一张）
      if (current.value && current.value['编号']) { openForm(current.value); return }
      newVisible.value = true
      return
    }
    if (panelCode.value === 'INV') {
      // 存货固定 5 张类别单据：新增物品进入对应类别单据（不新建第 6 张）
      // 防重复触发：类别选择弹窗已打开时忽略再次点击（避免刷新单据）
      if (catPickVisible.value) return
      catPickVisible.value = true
      return
    }
    await startNew()
    return
  }
  if (action === '修改') {
    if (!current.value) return ElMessage.warning('请先选择一行数据')
    openForm(current.value)
    return
  }
  // 就地保存：编辑态（未保存新页 / 草稿单据）组装表头 + 明细落库
  if ((action === '保存' || action === '保存为草稿') && editing.value) {
    if (!current.value) return ElMessage.warning('请先选择一行数据')
    await saveDraft(action)
    return
  }
  if (action === '放弃' || action === '取消') {
    if (hasDraft.value) await discardDraft()
    return
  }
  if (action === '刷新') {
    if (!(await discardDraft())) return
    load()
    return
  }
  if (action === '删除单据') {
    if (!current.value) return ElMessage.warning('请先选择一行数据')
    const no = current.value['编号'] || current.value['单据编号'] || ''
    try {
      await ElMessageBox.confirm('确认删除整张单据 ' + no + '？该操作不可恢复。', '删除单据确认', { type: 'warning' })
    } catch (e) {
      return
    }
    try {
      await engine.deleteForms({ panelCode: panelCode.value, rowCodes: [no] })
      ElMessage.success('单据已删除：' + no)
      delMode.value = false
      delSel.value = []
      load()
    } catch (e) {
      ElMessage.error(engine.errMsg(e) || '删除失败')
    }
    return
  }
  if (action === '删除') {
    if (!current.value) return ElMessage.warning('请先选择一行数据')
    if (!delMode.value) {
      delMode.value = true
      ElMessage.info('已进入删除模式：勾选要删除的行，再点「删除」确认；点「刷新」或翻页取消')
      return
    }
    if (!delSel.value.length) return ElMessage.warning('请先勾选要删除的行')
    try {
      await ElMessageBox.confirm('确认删除勾选的 ' + delSel.value.length + ' 行明细？', '删除确认', { type: 'warning' })
    } catch (e) {
      return
    }
    try {
      // 勾选删除：从当前单据对应明细中移除所选行（按对象引用匹配）
      const blk = blocks.value.find((x) => x.isMain)
      const tab = blk ? activeTab(blk) : null
      const key = tab ? tab.key : 'items'
      const items = cur.value.detail && Array.isArray(cur.value.detail[key]) ? cur.value.detail[key] : []
      const remain = items.filter((it) => !delSel.value.includes(it))
      const head = { ...cur.value }
      delete head.detail
      delete head['编号']
      delete head['单据状态']
      delete head['创建时间']
      delete head['更新时间']
      delete head['发起人编号']
      await engine.callButton({
        panelCode: panelCode.value,
        buttonName: '保存',
        formData: { ...head, 编号: cur.value['编号'], detail: { ...(cur.value.detail || {}), [key]: remain } },
        buttonParam: {},
      })
      ElMessage.success('已删除 ' + delSel.value.length + ' 行')
      delMode.value = false
      delSel.value = []
      load()
    } catch (e) {
      ElMessage.error(engine.errMsg(e) || '删除失败')
    }
    return
  }
  if (['中止执行', '整单中止', '草稿', '取消中止', '提交审批', '审批通过', '驳回审批'].includes(action)) {
    if (!current.value) return ElMessage.warning('请先选择一行数据')
  }
  // 人工审核：确认弹窗 + 审核意见（选填）；审核人取当前登录人（后端从 JWT 取）
  let auditOpinion = ''
  if (action === '审核') {
    if (!current.value) return ElMessage.warning('请先选择一行数据')
    // 已审核过的单据不允许再次审核，也不允许补填审批意见
    if (current.value['单据状态'] !== '草稿') return ElMessage.warning('仅草稿状态可审核，已审核单据不允许再次审核')
    const no = current.value['编号'] || current.value['单据编号'] || ''
    try {
      const { value } = await ElMessageBox.prompt(
        '单据：' + no + '（当前状态：' + (current.value['单据状态'] || '') + '）',
        '人工审核确认',
        { confirmButtonText: '确认审核', cancelButtonText: '取消', inputType: 'textarea', inputPlaceholder: '审核意见（选填）' }
      )
      auditOpinion = value || ''
    } catch (e) {
      return
    }
  } else if (action === '弃审') {
    if (!current.value) return ElMessage.warning('请先选择一行数据')
    if (current.value['单据状态'] !== '已审核') return ElMessage.warning('仅已审核状态可弃审')
    try {
      await ElMessageBox.confirm('确认弃审该单据？弃审后需重新审核。', '弃审确认', { type: 'warning' })
    } catch (e) {
      return
    }
  }
  try {
    // 审批流：提交审批/审批通过（确认+意见）、审批驳回（意见必填）、审批情况（历史弹窗）
    let approvalOpinion = ''
    if (action === '提交审批' || action === '审批通过') {
      if (!current.value) return ElMessage.warning('请先选择一行数据')
      const need = action === '提交审批' ? '草稿' : '审批中'
      if (current.value['单据状态'] !== need) return ElMessage.warning(action === '提交审批' ? '仅草稿状态可提交审批' : '仅审批中状态可审批通过')
      const no = current.value['编号'] || current.value['单据编号'] || ''
      try {
        const { value } = await ElMessageBox.prompt(
          '单据：' + no + '（当前状态：' + (current.value['单据状态'] || '') + '）',
          action + '确认',
          { confirmButtonText: '确认' + action, cancelButtonText: '取消', inputType: 'textarea', inputPlaceholder: action === '审批通过' ? '审批意见（选填）' : '提交说明（选填）' }
        )
        approvalOpinion = value || ''
      } catch (e) {
        return
      }
    } else if (action === '审批驳回') {
      if (!current.value) return ElMessage.warning('请先选择一行数据')
      if (current.value['单据状态'] !== '审批中') return ElMessage.warning('仅审批中状态可审批驳回')
      const no = current.value['编号'] || current.value['单据编号'] || ''
      try {
        const { value } = await ElMessageBox.prompt(
          '单据：' + no + '（当前状态：审批中）\n驳回必须填写审批意见',
          '审批驳回确认',
          { confirmButtonText: '确认驳回', cancelButtonText: '取消', inputType: 'textarea', inputPlaceholder: '驳回原因（必填）', inputValidator: (v) => (v && v.trim() ? true : '驳回必须填写审批意见') }
        )
        approvalOpinion = value || ''
      } catch (e) {
        return
      }
    } else if (action === '审批情况') {
      if (!current.value) return ElMessage.warning('请先选择一行数据')
      approvalNo.value = current.value['编号'] || current.value['单据编号'] || ''
      approvalVisible.value = true
      return
    }
    const res = await engine.callButton({
      panelCode: panelCode.value,
      buttonName: action,
      formData: current.value ? { 编号: current.value['编号'], ...(auditOpinion !== '' ? { 审核意见: auditOpinion } : {}), ...(approvalOpinion !== '' ? { 审批意见: approvalOpinion } : {}) } : {},
      buttonParam: {},
    })
    if (res?.gotoPanel) {
      ElMessage.success(`已生成${res.gotoPanel === 'MANU_ORDER' ? '生产加工单' : res.gotoPanel}：${res['编号']}`)
      const q = { code: res['编号'] }
      const title = (res.gotoPanel === 'MANU_ORDER' ? '加工单-' : '单据-') + res['编号']
      router.push({ path: `/panelx/form/${res.gotoPanel}`, query: q })
      tabs.open({ path: `/panelx/form/${res.gotoPanel}`, title, query: q })
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
  delMode.value = false
  delSel.value = []
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

async function search() {
  if (!(await discardDraft())) return
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

function onSelGenerated() {
  load()
}

// Excel 导入完成：单据面板追加到当前单明细并保存；档案面板（无明细 tab）逐行新建档案
async function onImported(rows) {
  const hasDetailTabs = (cfgCache.value?.detail?.tabs || []).length > 0
  if (!hasDetailTabs) {
    // 档案类（EMP/DEPT/WH…）：Excel 每行 = 一条新档案
    ElMessage.success('已解析 ' + rows.length + ' 行，正在逐条建档…')
    let ok = 0
    try {
      for (const r of rows) {
        await engine.callButton({ panelCode: panelCode.value, buttonName: '保存', formData: { ...r }, buttonParam: {} })
        ok++
      }
      ElMessage.success('已导入 ' + ok + ' 条档案')
    } catch (e) {
      ElMessage.error(engine.errMsg(e) || '第 ' + (ok + 1) + ' 条导入失败')
    }
    load()
    return
  }
  const blk = blocks.value.find((x) => x.id === 'A')
  const tab = blk ? activeTab(blk) : null
  const key = tab ? tab.key : 'items'
  if (!cur.value.detail || !Array.isArray(cur.value.detail[key])) {
    if (!cur.value.detail) cur.value.detail = {}
    cur.value.detail[key] = []
  }
  for (const r of rows) cur.value.detail[key].push(r)
  ElMessage.success('已导入 ' + rows.length + ' 行，正在保存…')
  try {
    const head = { ...cur.value }
    delete head.detail
    delete head['编号']
    delete head['单据状态']
    delete head['创建时间']
    delete head['更新时间']
    delete head['发起人编号']
    await engine.callButton({
      panelCode: panelCode.value,
      buttonName: '保存',
      formData: { ...head, 编号: cur.value['编号'], detail: { ...cur.value.detail } },
      buttonParam: {},
    })
    ElMessage.success('导入并保存成功')
    load()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '保存失败')
  }
}

// 存货（INV）面板：单击行 → 打开 BOM 管理弹窗（勾选存货添加子件、可多级下钻）
function onRowClick(row, b) {
  if (editing.value) return
  // 材料明细：点材料行 → 该材料有下级 BOM 则弹窗展示其子件
  if (b && b.id === 'B' && activeTab(b).key === 'materials' && row && row['材料编码'] && hasSubBom(row['材料编码'])) {
    openSubBom(row)
    return
  }
  // 产成品→材料联动：MANU_ORDER 等单据点产成品明细行 → 材料明细只显示其 BOM 子件
  if (b && b.id === 'A' && row && row['产品编码'] && row['产品编码'] !== selectedProduct.value) {
    selectProduct(row['产品编码'])
    return
  }
  if (panelCode.value !== 'INV') return
  if (!row || !row['存货编码']) return
  bomItem.value = row
  bomParent.value = cur.value // 父类别单据（当前行）
  bomVisible.value = true
}

// 捕获阶段监听：点产成品明细行任意单元格（含固定列/控件）都触发联动
async function onTableClick(b, e) {
  if (!b || !e || !e.target || !e.target.closest) return
  if (editing.value) return
  const t = activeTab(b)
  // 材料明细：点材料行 → 该材料有下级 BOM 则弹窗展示其子件
  if (b.id === 'B' && t.key === 'materials') {
    const tr = e.target.closest('tr')
    if (!tr) return
    const body = tr.closest('.el-table__body-wrapper') || tr.closest('.el-table__fixed-body-wrapper')
    const rows = body ? [...body.querySelectorAll('tbody tr')] : []
    const idx = rows.indexOf(tr)
    const row = detailRows(t)[idx]
    if (row && row['材料编码'] && hasSubBom(row['材料编码'])) openSubBom(row)
    return
  }
  if (b.id !== 'A') return
  if (t.key !== 'products') return
  const tr = e.target.closest('tr')
  if (!tr) return
  const body = tr.closest('.el-table__body-wrapper') || tr.closest('.el-table__fixed-body-wrapper')
  const rows = body ? [...body.querySelectorAll('tbody tr')] : []
  const idx = rows.indexOf(tr)
  const row = detailRows(t)[idx]
  if (!row || !row['产品编码']) return
  selectProduct(row['产品编码'])
}

// 材料下级 BOM 映射（INV 全量 _bom → 编码索引）；材料编码行右上角显示红 *，点击行弹窗查看
async function loadSubBomMap() {
  try {
    const res = await engine.queryFormDataList({ panelCode: 'INV', condition: {}, pageNo: 1, pageSize: 100 })
    const map = {}
    for (const d of res.list || []) {
      for (const it of (d.detail && d.detail.items) || []) {
        let bom = it['_bom']
        if (typeof bom === 'string') { try { bom = JSON.parse(bom) } catch (err) { bom = [] } }
        map[it['存货编码']] = Array.isArray(bom) ? bom : []
      }
    }
    subBomMap.value = map
  } catch (err) {}
}

function hasSubBom(code) {
  const b = subBomMap.value[code]
  return Array.isArray(b) && b.length > 0
}

function openSubBom(row) {
  const code = row['材料编码']
  subBomMaterial.value = row
  subBomBom.value = (subBomMap.value[code] || []).map((r) => ({ ...r }))
  subBomVisible.value = true
}

// 选中产成品：行高亮 + 材料明细联动（异步读该产品存货 BOM → 材料编码集合）
async function selectProduct(code) {
  selectedProduct.value = code
  selectedBomCodes.value = []
  try {
    const res = await engine.queryFormDataList({ panelCode: 'INV', condition: {}, pageNo: 1, pageSize: 100 })
    for (const d of res.list || []) {
      const it = ((d.detail && d.detail.items) || []).find((i) => i['存货编码'] === code)
      if (it && it['_bom']) {
        let bom = []
        try { bom = typeof it['_bom'] === 'string' ? JSON.parse(it['_bom']) : it['_bom'] } catch (err) {}
        selectedBomCodes.value = (Array.isArray(bom) ? bom : []).map((b) => b['材料编码']).filter(Boolean)
        break
      }
    }
  } catch (err) {
    // 查询失败按 子件BOM 标记兜底
  }
}

function onBomSaved() {
  load()
}

// 打开所选类别对应的存货单据（5 类固定）；单据被删后自动补建
async function gotoCategory() {
  const docMap = { 产成品: 'CP-001', 原材料: 'YL-001', 辅助材料: 'FZ-001', 包装物: 'BZ-001', 半成品: 'BC-001' }
  const doc = docMap[catPick.value]
  if (!doc) return
  catPickVisible.value = false
  catPick.value = ''
  // 检查该类别单据是否存在，缺失则自动补建（保持 5 张）
  try {
    const res = await engine.queryFormDataList({ panelCode: 'INV', condition: { 类别: catPick.value }, pageNo: 1, pageSize: 10 })
    const exists = (res.list || []).some((r) => r['编号'] === doc)
    if (!exists) {
      await engine.callButton({ panelCode: 'INV', buttonName: '保存', formData: { 类别: catPick.value, detail: { items: [] } }, buttonParam: {} })
      ElMessage.success('已自动补建类别单据 ' + doc)
    }
  } catch (e) {
    // 补建失败不阻塞跳转
  }
  const q = { code: doc }
  router.push({ path: `/panelx/form/${panelCode.value}`, query: q })
  tabs.open({ path: `/panelx/form/${panelCode.value}`, title: '存货-' + doc, query: q })
}

watch(
  () => [panelCode.value, operationName.value],
  async () => {
    if (!(await discardDraft())) return
    cfgCache.value = null
    qOptCache.clear()
    queryFields.value = []
    gridTabs.value = []
    curIdx.value = 0
    search()
  }
)

onMounted(async () => {
  document.addEventListener('click', closeCtx)
  document.addEventListener('contextmenu', closeCtx)
  loadSubBomMap() // 材料下级 BOM 映射（红 * 标记 + 点击行弹窗）
  if (invalidPanel.value) {
    router.replace('/panelx/list/MANU_ORDER')
    return
  }
  // 先加载列表与配置，再就地新增（startNew 依赖 cfgCache 判断是否可编辑、并把草稿追加到末尾）
  await load()
  if (route.query.new) startNew()
})

onUnmounted(() => {
  document.removeEventListener('click', closeCtx)
  document.removeEventListener('contextmenu', closeCtx)
})

watch(
  () => route.query.new,
  (v) => {
    if (v) startNew()
  }
)

// 就地编辑：明细变化时重算（含税单价/金额/含税金额 等 calc 规则）
watch(
  () => cur.value?.detail,
  () => {
    if (editing.value) applyCalc()
  },
  { deep: true }
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

/* ═══════ ① 顶部工具栏（T+ 灰条）═══════ */
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
  position: relative;
  border: 1px solid #c9cfdb;
  border-radius: 3px;
  overflow: visible;
  margin-right: 4px;
  background: #fff;
}
.tb-menu {
  position: absolute;
  top: 100%;
  left: 0;
  z-index: 3000;
  min-width: 160px;
  background: #fff;
  border: 1px solid #d0d7e3;
  border-radius: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.14);
  padding: 4px 0;
  max-height: 360px;
  overflow: auto;
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
.doc-cat {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 10px;
  margin-right: 6px;
  color: #7c3aed;
  border: 1px solid #ddd6fe;
  background: #f5f3ff;
}
.doc-status.已审核,
.doc-status.已完工 {
  color: #16a34a;
  border: 1px solid #bbe6c4;
  background: #f0fdf4;
}
.doc-status.生产中,
.doc-status.审批中 {
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

/* ═══════ ② 表头字段区（label 在上、输入在下）═══════ */
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

/* ═══════ ③ 明细区块 ═══════ */
.body {
  flex: 1;
  padding: 8px 10px 0;
  min-height: 0;
}
.detail {
  border: 1px solid #d7dce5;
  margin-bottom: 8px;
  background: #fff;
  position: relative;
}
.approved-stamp {
  position: absolute;
  top: 3px;
  left: 6px;
  z-index: 9;
  transform: rotate(-12deg);
  color: #16a34a;
  border: 2px solid #16a34a;
  border-radius: 4px;
  padding: 0 10px;
  font-size: 14px;
  font-weight: 700;
  background: rgba(240, 253, 244, 0.92);
  pointer-events: none;
  letter-spacing: 3px;
  box-shadow: 0 1px 3px rgba(22, 163, 74, 0.25);
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
.mat-cell {
  position: relative;
  display: inline-block;
  width: 100%;
}
.mat-star {
  position: absolute;
  top: 2px;
  right: 2px;
  color: #e60000;
  font-weight: 700;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  user-select: none;
}
.filter-hint {
  font-size: 12px;
  color: #0d5bd3;
  margin-right: 8px;
}
:deep(.prod-selected > td.el-table__cell) {
  background: #e8f1ff !important;
}
:deep(.el-table th.el-table__cell) {
  background: #f7f9fc;
  color: #333;
  font-weight: 600;
}
:deep(.el-table th .cell) {
  white-space: nowrap;
}
/* 固定 5 行：所有数据行统一 31px 高（含空占位行，占位行不渲染成矮行） */
:deep(.el-table .el-table__body td) {
  height: 31px;
  padding: 0;
  vertical-align: middle;
}
:deep(.el-table .el-table__footer-wrapper .cell) {
  font-weight: 600;
}
:deep(.el-table .sum-row td) {
  background: #f7f9fc;
  font-weight: 600;
}

/* ═══════ ④ 表尾固定条（sticky 底部：滚动明细时始终可见）═══════ */
.footer {
  position: sticky;
  bottom: 0;
  z-index: 20;
  background: #fff;
  border-top: 1px solid #d0d7e3;
  box-shadow: 0 -2px 6px rgba(0, 0, 0, 0.06);
  flex-shrink: 0;
}

/* ═══════ 表尾：备注 + 分隔线 + 审核行 ═══════ */
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
:deep(.el-table .row-approved td) { background: #f0fdf4 !important; }
.main-grid { margin-bottom: 10px; }
.main-grid .dt-head { margin-bottom: 4px; }
.main-grid .dt-head .dt-tab.on { cursor: default; }
:deep(.main-grid .el-table .row-cur td) { background: #eaf4fe !important; }
:deep(.main-grid .el-table .ph-row td) { height: 31px; }

/* ═══════ 就地编辑：参照控件 / 单元格 / 新增数据按钮 ═══════ */
.field .ref-ctl { width: 160px; }
.ref-ctl { display: flex; align-items: center; gap: 2px; }
.ref-ctl :deep(.el-input) { flex: 1; width: auto; }
.ref-btn { flex-shrink: 0; }
.cell-add { display: inline-block; width: 100%; min-height: 24px; color: #b3b9c4; cursor: pointer; user-select: none; text-align: center; }
.cell-add:hover { color: #0d5bd3; background: #f0f6ff; }
.add-data-btn { margin-left: 6px; }
.del { cursor: pointer; color: #dc2626; }
.img-ph { display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; font-size: 11px; color: #999; border: 1px dashed #d0d7e3; border-radius: 3px; }
.field :deep(.el-input-number) { width: 160px; }
.field :deep(.el-switch) { margin-top: 5px; }
</style>
