<template>
  <div class="panelx-list" @click="closeCtx">
    <!-- ══════════ ① 顶部工具栏（T+ 灰条 + 单据翻页）══════════ -->
    <div class="tools">
      <button type="button" class="toolbar-query-btn" title="按表头字段查询单据" @click.stop="openQueryDialog">
        <el-icon><Search /></el-icon>
        <span>查询</span>
      </button>
      <div class="tb-group" v-for="(g, gi) in toolbarGroups" :key="'g' + gi">
        <span class="tb-main" :class="{ disabled: isDisabled(btnName(g)) }" @click="onButton(btnName(g))">
          <span class="act-name">{{ g.name }}</span>
        </span>
        <span v-if="actsOf(g).length > 1" class="tb-caret" @click.stop="toggleGroup(gi)">▼</span>
        <div v-if="openGroup === gi" class="tb-menu">
          <!-- 下拉排除主按钮（组按钮=第一个 action，下拉只列其余动作，避免「审核」重复） -->
          <div class="ctx-item" v-for="a in dropItems(g)" :key="a" @click="onGroupAction(a)">{{ a }}</div>
        </div>
      </div>
      <div class="tools-right">
        <template v-if="reportMode">
          <span class="doc-chip">{{ panelName }}</span>
          <span class="report-count">共 {{ total }} 条</span>
          <span class="page-btn" title="首页" @click="reportPage(1)">◁</span>
          <span class="page-btn" title="上一页" @click="reportPage(query.pageNo - 1)">◀</span>
          <span class="page-no">第 {{ query.pageNo }}/{{ reportPageCount }} 页</span>
          <span class="page-btn" title="下一页" @click="reportPage(query.pageNo + 1)">▶</span>
          <span class="page-btn" title="末页" @click="reportPage(reportPageCount)">▷</span>
        </template>
        <template v-else>
          <span class="doc-chip">单据：{{ cur['编号'] || cur['单据编号'] || '-' }}</span>
          <span v-if="cur['类别']" class="doc-cat">{{ cur['类别'] }}</span>
          <span v-if="cur['单据状态']" class="doc-status" :class="cur['单据状态']">{{ cur['单据状态'] }}</span>
          <span class="page-btn" title="首页" @click="pageFirst">◁</span>
          <span class="page-btn" title="上一张" @click="page(-1)">◀</span>
          <span class="page-no">第 {{ curNo }}/{{ total }} 张</span>
          <span class="page-btn" title="下一张" @click="page(1)">▶</span>
          <span class="page-btn" title="末页" @click="pageLast">▷</span>
        </template>
      </div>
    </div>

    <!-- 报表沿用配置查询字段；单据页显示当前单据表头，草稿态原地编辑。 -->
    <div v-if="reportMode" class="fields udl-fields">
      <div class="field" v-for="qr in queryFields" :key="qr.dataName">
        <label :class="{ req: qr.isRequired }">{{ qr.label || qr.dataName }}</label>
        <div v-if="qType(qr) === 'ref'" class="query-ref">
          <el-input
            :model-value="condition[qr.dataName] || ''"
            readonly
            clearable
            :placeholder="qr.placeholder || '请选择'"
            @click="openQueryRef(qr, 'page')"
            @clear="clearQueryRef(qr, 'page')"
          />
          <el-button :icon="Search" title="打开参照" @click="openQueryRef(qr, 'page')" />
        </div>
        <el-select
          v-else-if="qType(qr) === 'select'"
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
    <div v-else class="fields header-fields udl-fields" :class="{ 'is-draft': draftEditable }">
      <div class="field" v-for="field in headerFields" :key="headerFieldKey(field)">
        <label :class="{ req: field.isRequired }">{{ headerFieldLabel(field) }}</label>
        <template v-if="draftEditable">
          <div v-if="isReferenceField(field)" class="query-ref">
            <el-input
              :model-value="headerRefText(field)"
              readonly
              :disabled="headerFieldLocked(field)"
              placeholder="请选择"
              @click="openHeaderRef(field)"
            />
            <el-button
              :icon="Search"
              title="打开参照"
              :disabled="headerFieldLocked(field)"
              @click="openHeaderRef(field)"
            />
          </div>
          <el-select
            v-else-if="isSelectField(field)"
            v-model="cur[headerFieldKey(field)]"
            :disabled="headerFieldLocked(field)"
            clearable
            filterable
            allow-create
          >
            <el-option v-for="option in fieldOptions(field)" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
          <el-date-picker
            v-else-if="isDateField(field)"
            v-model="cur[headerFieldKey(field)]"
            :disabled="headerFieldLocked(field)"
            type="date"
            value-format="YYYY-MM-DD"
          />
          <el-input-number
            v-else-if="isNumberField(field)"
            v-model="cur[headerFieldKey(field)]"
            :disabled="headerFieldLocked(field)"
            :controls="false"
          />
          <el-switch
            v-else-if="isBooleanField(field)"
            v-model="cur[headerFieldKey(field)]"
            :disabled="headerFieldLocked(field)"
          />
          <el-input
            v-else
            v-model="cur[headerFieldKey(field)]"
            :disabled="headerFieldLocked(field)"
          />
        </template>
        <div v-else class="field-readonly" :title="String(cur[headerFieldKey(field)] ?? '')">
          {{ formatFieldValue(field, cur[headerFieldKey(field)]) }}
        </div>
      </div>
    </div>

    <div v-if="reportMode" class="report-body" v-loading="loading">
      <div class="report-heading">
        <strong>{{ panelName }}</strong>
        <span>{{ reportPeriod }}</span>
      </div>
      <el-table
        class="report-table"
        :data="list"
        border
        stripe
        size="small"
        height="100%"
        show-summary
        :summary-method="sumMethod"
        empty-text="暂无符合条件的数据"
        @row-click="(row) => (current = row)"
      >
        <el-table-column type="index" label="序号" width="58" fixed="left" :index="(i) => (query.pageNo - 1) * query.pageSize + i + 1" />
        <template v-for="column in reportColumnTree" :key="column.label">
          <el-table-column v-if="column.children" :label="column.label" align="center">
            <el-table-column
              v-for="child in column.children"
              :key="child.prop"
              :prop="child.prop"
              :label="child.label"
              :min-width="child.width"
              :align="child.align"
              show-overflow-tooltip
            />
          </el-table-column>
          <el-table-column
            v-else
            :prop="column.prop"
            :label="column.label"
            :min-width="column.width"
            :align="column.align"
            show-overflow-tooltip
          />
        </template>
      </el-table>
    </div>

    <div v-else class="body" :class="{ 'draft-body': draftEditable }" v-loading="loading">
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
            <el-button v-if="detailEditable(b)" size="small" type="primary" :icon="Plus" @click="addInlineDetailRow(b)">新增数据</el-button>
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
          :row-class-name="(o) => rowCls(o, b)"
          @selection-change="(r) => (delSel = r)"
          @row-contextmenu="(row, col, ev) => onCtx(ev, row, b)"
          @cell-dblclick="(row, col, cell, ev) => onDetailCellDblclick(row, col, ev, b)"
          @row-click="(row) => onRowClick(row, b)"
          @click.capture="(e) => onTableClick(b, e)"
        >
          <el-table-column v-if="delMode && b.isMain" type="selection" width="45" fixed="left" />
          <el-table-column
            v-for="c in blockCols(b)"
            :key="c.prop"
            :prop="c.prop"
            :label="c.label"
            :min-width="c.width"
            :align="c.align"
            :show-overflow-tooltip="!detailEditable(b)"
          >
            <template #default="{ row }">
              <template v-if="detailEditable(b) && !row._placeholder">
                <span v-if="c.field.computed" class="inline-computed-value">{{ formatFieldValue(c.field, row[c.prop]) }}</span>
                <div v-else-if="isReferenceField(c.field)" class="inline-ref-editor" :class="{ active: isActiveDetailRefRow(row, b, c.prop) }">
                  <el-input
                    :model-value="formatFieldValue(c.field, row[c.prop])"
                    readonly
                    :title="detailRefTrigger(c.field) === 'dblclick' ? '双击选择存货' : '点击选择'"
                    @click="openClickDetailRef(c.field, row, b)"
                  />
                  <el-icon v-if="detailRefTrigger(c.field) === 'dblclick' && isActiveDetailRefRow(row, b, c.prop)" class="list-ref-icon"><Search /></el-icon>
                </div>
                <el-select
                  v-else-if="isSelectField(c.field)"
                  v-model="row[c.prop]"
                  :disabled="c.field.computed"
                  filterable
                  clearable
                  allow-create
                  @change="onInlineDetailChange(activeTab(b).key, row)"
                >
                  <el-option v-for="option in fieldOptions(c.field)" :key="option.value" :label="option.label" :value="option.value" />
                </el-select>
                <el-date-picker
                  v-else-if="isDateField(c.field)"
                  v-model="row[c.prop]"
                  :disabled="c.field.computed"
                  type="date"
                  value-format="YYYY-MM-DD"
                  @change="onInlineDetailChange(activeTab(b).key, row)"
                />
                <el-input-number
                  v-else-if="isNumberField(c.field)"
                  v-model="row[c.prop]"
                  :disabled="c.field.computed"
                  :controls="false"
                  @change="onInlineDetailChange(activeTab(b).key, row)"
                />
                <el-switch
                  v-else-if="isBooleanField(c.field)"
                  v-model="row[c.prop]"
                  :disabled="c.field.computed"
                  @change="onInlineDetailChange(activeTab(b).key, row)"
                />
                <el-input
                  v-else
                  v-model="row[c.prop]"
                  :disabled="c.field.computed"
                  @change="onInlineDetailChange(activeTab(b).key, row)"
                />
              </template>
              <span v-else-if="c.prop === '材料编码' && activeTab(b).key === 'materials'" class="mat-cell">
                <span>{{ row[c.prop] }}</span>
                <span v-if="hasSubBom(row[c.prop])" class="mat-star" title="该材料有下级子件 BOM，点击行查看">*</span>
              </span>
              <span v-else>{{ row[c.prop] ?? '' }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>

    </div>

    <!-- ══════════ ④ 表尾（固定在页面底部，滚动明细时始终可见；备注 + 审核行）══════════ -->
    <div v-if="showFooter" class="footer">
      <div class="remark">
        <label>备注</label>
        <el-input v-model="remarkText" size="small" placeholder="" :disabled="!draftEditable" />
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
    <RefPickDialog v-model="queryRefVisible" :field="queryRefField" mode="query" @confirm="onQueryRefConfirm" />
    <RefPickDialog v-model="headerRefVisible" :field="headerRefField" mode="header" @confirm="onHeaderRefConfirm" />
    <RefPickDialog v-model="detailRefVisible" :field="detailRefPick?.field" mode="detail" @confirm="onDetailRefConfirm" />
    <el-dialog v-model="queryDialogVisible" title="查询" width="760px" append-to-body destroy-on-close class="header-query-dialog">
      <div class="query-dialog-fields">
        <div v-for="field in queryDialogFields" :key="headerFieldKey(field)" class="query-dialog-field">
          <label>{{ headerFieldLabel(field) }}</label>
          <div v-if="isReferenceField(field)" class="query-ref">
            <el-input
              :model-value="queryDraft[headerFieldKey(field)] ?? ''"
              readonly
              clearable
              placeholder="请选择"
              @click="openQueryRef(field, 'dialog')"
              @clear="clearQueryRef(field, 'dialog')"
            />
            <el-button :icon="Search" title="打开参照" @click="openQueryRef(field, 'dialog')" />
          </div>
          <el-select v-else-if="isSelectField(field)" v-model="queryDraft[headerFieldKey(field)]" clearable filterable allow-create>
            <el-option v-for="option in fieldOptions(field)" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
          <el-date-picker v-else-if="isDateField(field)" v-model="queryDraft[headerFieldKey(field)]" type="date" value-format="YYYY-MM-DD" />
          <el-input-number v-else-if="isNumberField(field)" v-model="queryDraft[headerFieldKey(field)]" :controls="false" />
          <el-select v-else-if="isBooleanField(field)" v-model="queryDraft[headerFieldKey(field)]" clearable>
            <el-option label="是" :value="true" />
            <el-option label="否" :value="false" />
          </el-select>
          <el-input v-else v-model="queryDraft[headerFieldKey(field)]" clearable @keyup.enter="applyHeaderQuery" />
        </div>
      </div>
      <template #footer>
        <el-button @click="resetHeaderQuery">重置</el-button>
        <el-button @click="queryDialogVisible = false">取消</el-button>
        <el-button type="primary" :icon="Search" @click="applyHeaderQuery">查询</el-button>
      </template>
    </el-dialog>
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
    <VoucherFormDialog v-model="formVisible" :panel-code="formPanel || panelCode" :code="formCode" @saved="onFormSaved" />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, onDeactivated, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search } from '@element-plus/icons-vue'
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
import VoucherFormDialog from './VoucherFormDialog.vue'

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
const queryRefVisible = ref(false)
const queryRefField = ref(null)
const queryRefContext = ref('page')
const queryDialogVisible = ref(false)
const queryDraft = reactive({})
const headerRefVisible = ref(false)
const headerRefField = ref(null)
const detailRefVisible = ref(false)
const detailRefPick = ref(null)
const detailRefSaving = ref(false)
const inlineSaving = ref(false)
const reportMode = computed(() => cfgCache.value?.metadata?.report === true || cfgCache.value?.metadata?.panelCategory === '报表')
const reportPageCount = computed(() => Math.max(1, Math.ceil(total.value / query.pageSize)))
const reportPeriod = computed(() => {
  const start = condition['开始日期']
  const end = condition['结束日期']
  if (start && end) return `${start} - ${end}`
  if (start) return `${start} 起`
  if (end) return `截至 ${end}`
  return '当前业务数据'
})
const reportColumns = computed(() => gridTabs.value[0]?.columns || [])
const reportColumnTree = computed(() => {
  const groups = gridTabs.value[0]?.columnGroups || []
  const owner = new Map()
  for (const group of groups) for (const column of group.columns || []) owner.set(column, group)
  const emitted = new Set()
  const out = []
  for (const column of reportColumns.value) {
    const group = owner.get(column)
    if (group) {
      if (emitted.has(group.label)) continue
      emitted.add(group.label)
      out.push({
        label: group.label,
        children: (group.columns || []).filter((name) => reportColumns.value.includes(name)).map(reportLeaf),
      })
    } else {
      out.push(reportLeaf(column))
    }
  }
  return out
})
const toolbarGroups = computed(() => (groups.value || []).map((group) => {
  const actions = actsOf(group).filter((action) => action !== '查询' && action !== '查找')
  const name = ['查询', '查找'].includes(group.name) ? (actions[0] || group.name) : group.name
  return { ...group, name, actions }
}).filter((group) => actsOf(group).length))
const headerFields = computed(() => {
  const fields = (cfgCache.value?.dataSchema?.fields || []).filter((field) => !field.hidden)
  const names = cfgCache.value?.metadata?.panelPageDto?.formPages?.[0]?.fieldNames
  if (!names) return fields
  const ordered = String(names).split(',').map((name) => name.trim()).filter(Boolean)
  const byName = new Map(fields.map((field) => [headerFieldKey(field), field]))
  return ordered.map((name) => byName.get(name)).filter(Boolean)
})
const queryDialogFields = computed(() => {
  const fields = reportMode.value ? queryFields.value : headerFields.value
  return fields.filter((field) => headerFieldKey(field) !== '备注')
})
const draftEditable = computed(() => !reportMode.value && cur.value?.['单据状态'] === '草稿')
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

watch(cur, (v) => {
  current.value = v
  detailRefVisible.value = false
  detailRefPick.value = null
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
function onMainRowClick(row) {
  const i = list.value.indexOf(row)
  if (i >= 0) curIdx.value = i
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
  while (out.length < MIN_ROWS) out.push({ _placeholder: true })
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
    return { prop: c, label: c, field: f, width: colW(f), align: f.dataType === '小数' || f.dataType === '整数' ? 'right' : 'left' }
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
// 下拉项 = 除主按钮（第一个 action）外的其余动作（2026-08-20：避免下拉与组按钮重复）
function dropItems(g) {
  return actsOf(g).slice(1)
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

function headerFieldKey(field) {
  return field.code || field.dataName
}

function headerFieldLabel(field) {
  return field.name || field.label || field.displayName || field.dataName || field.code
}

function fieldType(field) {
  return field?.dataType || '文本'
}

function isReferenceField(field) {
  return fieldType(field) === '参照' && !!(field?.refPanel || field?.ref?.panel)
}

function isSelectField(field) {
  return fieldType(field) === '下拉框'
}

function isDateField(field) {
  return ['日期', '日期时间', '时间', 'DATE', 'DateTime', 'Date'].includes(fieldType(field))
}

function isNumberField(field) {
  return ['小数', '整数', 'Decimal', 'Long', 'Integer', 'Double'].includes(fieldType(field))
}

function isBooleanField(field) {
  return ['是否', 'Boolean', 'BOOL'].includes(fieldType(field))
}

function fieldOptions(field) {
  return (field?.options || engine.fieldOptions(field || {}) || []).map((option) => (
    typeof option === 'object'
      ? { value: option.value ?? option.label, label: option.label ?? option.value }
      : { value: option, label: option }
  ))
}

function formatFieldValue(field, value) {
  if (value === undefined || value === null || value === '') return ''
  if (isBooleanField(field)) return value ? '是' : '否'
  return String(value)
}

function headerFieldLocked(field) {
  const key = headerFieldKey(field)
  return !!field.computed || !!field.autoCode || ['编号', '单据状态', '创建时间', '更新时间', '发起人编号'].includes(key)
}

function headerRefText(field) {
  return formatFieldValue(field, cur.value[headerFieldKey(field)])
}

function openHeaderRef(field) {
  if (!draftEditable.value || headerFieldLocked(field)) return
  headerRefField.value = field
  headerRefVisible.value = true
}

function onHeaderRefConfirm(rows) {
  const field = headerRefField.value
  const source = rows?.[0]
  if (!field || !source || !draftEditable.value) return
  const key = headerFieldKey(field)
  const ref = field.ref && typeof field.ref === 'object' ? field.ref : field
  const refField = ref.field || ref.refField || key
  cur.value[key] = source[refField] ?? ''
  for (const map of ref.map || ref.refMap || []) {
    if (map && source[map.from] !== undefined) cur.value[map.to || map.from] = source[map.from]
  }
  headerRefVisible.value = false
  headerRefField.value = null
}

function detailTabDefOf(key) {
  return (cfgCache.value?.detail?.tabs || []).find((tab) => tab.key === key) || null
}

function detailEditable(b) {
  return draftEditable.value && !!b && tabView(b, activeTab(b)) !== 'summary'
}

function detailRefTrigger(field) {
  return field?.refTrigger || field?.trigger || 'click'
}

function isActiveDetailRefRow(row, b, prop) {
  const pick = detailRefPick.value
  return detailRefVisible.value && !!pick && pick.row === row && pick.tabKey === activeTab(b).key && pick.field?.dataName === prop
}

function openDetailReference(field, row, b) {
  if (!detailEditable(b) || !isReferenceField(field) || field.computed || row?._placeholder) return
  detailRefPick.value = {
    field,
    row,
    tabKey: activeTab(b).key,
    documentNo: cur.value['编号'],
    created: false,
  }
  detailRefVisible.value = true
}

function openClickDetailRef(field, row, b) {
  if (detailRefTrigger(field) === 'click') openDetailReference(field, row, b)
}

function onDetailCellDblclick(row, column, event, b) {
  const field = fieldDefOf(column?.property)
  if (detailEditable(b) && isReferenceField(field) && detailRefTrigger(field) === 'dblclick') {
    event?.stopPropagation?.()
    if (!row?._placeholder) openDetailReference(field, row, b)
    return
  }
  if (draftEditable.value) return
  if (!row?._placeholder) openForm(cur.value)
}

function newDetailRow(tabKey) {
  const row = {}
  for (const field of detailTabDefOf(tabKey)?.fields || []) {
    if (field.dataType === '小数' || field.dataType === '整数') row[field.dataName] = field.defaultValue ?? 0
    else if (field.dataType === '是否') row[field.dataName] = field.defaultValue ?? false
    else row[field.dataName] = field.defaultValue ?? ''
  }
  return row
}

function addInlineDetailRow(b) {
  if (!detailEditable(b)) return
  const tabKey = activeTab(b).key
  if (!cur.value.detail) cur.value.detail = {}
  const rows = cur.value.detail[tabKey] || (cur.value.detail[tabKey] = [])
  rows.push(newDetailRow(tabKey))
}

function primaryDetailRefField(b) {
  if (!detailEditable(b)) return null
  const columns = new Set(activeTab(b).cols || [])
  const fields = (detailTabDefOf(activeTab(b).key)?.fields || []).filter((field) => (
    columns.has(field.dataName) && isReferenceField(field) && !field.computed
  ))
  return fields.find((field) => ['产品编码', '存货编码', '材料编码'].includes(field.dataName)) || fields[0] || null
}

function openBlankDetailRow(b) {
  const field = primaryDetailRefField(b)
  if (!field) return
  const tabKey = activeTab(b).key
  if (!cur.value.detail) cur.value.detail = {}
  const rows = cur.value.detail[tabKey] || (cur.value.detail[tabKey] = [])
  const row = newDetailRow(tabKey)
  rows.push(row)
  detailRefPick.value = {
    field,
    row,
    tabKey,
    documentNo: cur.value['编号'],
    created: true,
  }
  detailRefVisible.value = true
}

function discardCreatedDetailRefRow(pick) {
  if (!pick?.created) return
  const rows = cur.value.detail?.[pick.tabKey]
  if (!Array.isArray(rows)) return
  const index = rows.indexOf(pick.row)
  if (index >= 0) rows.splice(index, 1)
}

function onInlineDetailChange(tabKey, row) {
  calculateDetailRow(tabKey, row)
}

function applyDetailReference(target, field, source) {
  const refField = field.refField || field.field
  target[field.dataName] = source[refField]
  for (const map of field.refMap || field.map || []) {
    if (map && source[map.from] !== undefined) target[map.to || map.from] = source[map.from]
  }
}

function calculateDetailRow(tabKey, row) {
  const tab = detailTabDefOf(tabKey)
  if (!tab?.calc?.length) return
  const numeric = (value) => Number.isFinite(Number(value)) ? Number(value) : 0
  for (const rule of tab.calc) {
    let expression = String(rule.formula || '')
    const names = [...new Set(expression.match(/[^\s+\-*/()]+/g) || [])]
      .filter((name) => !/^\d+(?:\.\d+)?$/.test(name))
      .sort((a, b) => b.length - a.length)
    for (const name of names) expression = expression.split(name).join(String(numeric(row[name])))
    if (!/^[\d.\s+\-*/()]+$/.test(expression)) continue
    let value
    try { value = Function(`"use strict"; return (${expression})`)() } catch (error) { value = 0 }
    if (!Number.isFinite(value)) value = 0
    if (rule.round != null) value = Math.round(value * 10 ** rule.round) / 10 ** rule.round
    row[rule.target] = value
  }
}

function currentFormData(detail) {
  const head = { ...cur.value }
  delete head.detail
  delete head['编号']
  delete head['单据状态']
  delete head['创建时间']
  delete head['更新时间']
  delete head['发起人编号']
  return { ...head, 编号: cur.value['编号'], detail }
}

function emptyFieldValue(value) {
  return value === undefined || value === null || String(value).trim() === ''
}

function validateInlineDraft() {
  for (const field of headerFields.value) {
    if (field.isRequired && emptyFieldValue(cur.value[headerFieldKey(field)])) {
      return `${headerFieldLabel(field)}不能为空`
    }
  }
  for (const tab of cfgCache.value?.detail?.tabs || []) {
    const rows = cur.value.detail?.[tab.key] || []
    if (tab.isRequired && !rows.length) return `请至少添加一行${tab.label || '明细'}`
    for (let index = 0; index < rows.length; index++) {
      for (const field of tab.fields || []) {
        if (field.isRequired && emptyFieldValue(rows[index][field.dataName])) {
          return `${tab.label || '明细'}第 ${index + 1} 行${field.dataName}不能为空`
        }
      }
    }
  }
  return ''
}

async function saveInlineDraft(buttonName = '保存') {
  if (!draftEditable.value || inlineSaving.value) return false
  const validation = validateInlineDraft()
  if (validation) {
    ElMessage.warning(validation)
    return false
  }
  for (const tab of cfgCache.value?.detail?.tabs || []) {
    for (const row of cur.value.detail?.[tab.key] || []) calculateDetailRow(tab.key, row)
  }
  inlineSaving.value = true
  const documentNo = cur.value['编号']
  try {
    await engine.callButton({
      panelCode: panelCode.value,
      buttonName,
      formData: currentFormData({ ...(cur.value.detail || {}) }),
      buttonParam: {},
    })
    await load()
    const index = list.value.findIndex((item) => item['编号'] === documentNo)
    if (index >= 0) curIdx.value = index
    ElMessage.success(`「${buttonName}」成功`)
    return true
  } catch (error) {
    ElMessage.error(engine.errMsg(error) || '保存失败')
    return false
  } finally {
    inlineSaving.value = false
  }
}

async function onDetailRefConfirm(selectedRows) {
  const pick = detailRefPick.value
  if (!pick || !selectedRows?.length || detailRefSaving.value) return
  if (cur.value['编号'] !== pick.documentNo || cur.value['单据状态'] !== '草稿') {
    detailRefVisible.value = false
    ElMessage.warning('当前单据已切换或不再是草稿，请重新选择')
    return
  }

  const detail = {}
  for (const [key, value] of Object.entries(cur.value.detail || {})) {
    detail[key] = Array.isArray(value) ? value.map((row) => ({ ...row })) : value
  }
  const sourceRows = cur.value.detail?.[pick.tabKey] || []
  const targetRows = detail[pick.tabKey] || (detail[pick.tabKey] = [])
  const targetIndex = pick.row ? sourceRows.indexOf(pick.row) : -1
  let offset = 0
  if (targetIndex >= 0) {
    applyDetailReference(targetRows[targetIndex], pick.field, selectedRows[0])
    calculateDetailRow(pick.tabKey, targetRows[targetIndex])
    offset = 1
  }
  for (let index = offset; index < selectedRows.length; index++) {
    const row = newDetailRow(pick.tabKey)
    applyDetailReference(row, pick.field, selectedRows[index])
    calculateDetailRow(pick.tabKey, row)
    targetRows.push(row)
  }

  detailRefSaving.value = true
  try {
    await engine.callButton({
      panelCode: panelCode.value,
      buttonName: '保存',
      formData: currentFormData(detail),
      buttonParam: {},
    })
    detailRefVisible.value = false
    const documentNo = pick.documentNo
    await load()
    const currentIndex = list.value.findIndex((item) => item['编号'] === documentNo)
    if (currentIndex >= 0) curIdx.value = currentIndex
    ElMessage.success(`已导入 ${selectedRows.length} 条存货并保存`)
  } catch (error) {
    discardCreatedDetailRefRow(pick)
    ElMessage.error(engine.errMsg(error) || '存货导入保存失败')
  } finally {
    detailRefSaving.value = false
    detailRefPick.value = null
  }
}

function qType(qr) {
  const t = qr.dataType || fieldDefOf(qr.dataName).dataType || '文本'
  if (t === '参照') return 'ref'
  if (t === '下拉框') return 'select'
  if (t === '日期' || t === '日期时间') return 'date'
  return 'input'
}

function openQueryDialog() {
  Object.keys(queryDraft).forEach((key) => delete queryDraft[key])
  Object.assign(queryDraft, condition)
  queryDialogVisible.value = true
}

function openQueryRef(qr, context = 'page') {
  queryRefField.value = qr
  queryRefContext.value = context
  queryRefVisible.value = true
}

function clearQueryRef(qr, context = 'page') {
  const key = headerFieldKey(qr)
  if (context === 'dialog') {
    delete queryDraft[key]
    return
  }
  delete condition[key]
  search()
}

function onQueryRefConfirm(rows) {
  const field = queryRefField.value
  const row = rows?.[0]
  if (!field || !row) return
  const ref = field.ref && typeof field.ref === 'object' ? field.ref : field
  const valueField = ref.field || ref.refField || ref.display || ref.displayField || headerFieldKey(field)
  const target = queryRefContext.value === 'dialog' ? queryDraft : condition
  target[headerFieldKey(field)] = row[valueField] ?? ''
  queryRefVisible.value = false
  queryRefField.value = null
  if (queryRefContext.value === 'page') search()
}

function applyHeaderQuery() {
  Object.keys(condition).forEach((key) => delete condition[key])
  for (const [key, value] of Object.entries(queryDraft)) {
    if (value !== undefined && value !== null && String(value) !== '') condition[key] = value
  }
  queryDialogVisible.value = false
  search()
}

function resetHeaderQuery() {
  Object.keys(queryDraft).forEach((key) => delete queryDraft[key])
  Object.keys(condition).forEach((key) => delete condition[key])
  query.keyword = ''
  queryDialogVisible.value = false
  search()
}

const qOptCache = new Map()
function qOptions(qr) {
  const key = panelCode.value + '|' + qr.dataName
  if (!qOptCache.has(key)) qOptCache.set(key, (qr.options || engine.fieldOptions(qr)).map((o) => typeof o === 'object' ? o : ({ value: o, label: o })))
  return qOptCache.get(key)
}

function reportLeaf(column) {
  const field = fieldDefOf(column)
  const numeric = field.dataType === '小数' || field.dataType === '整数'
  return { prop: column, label: column, width: colW(field), align: numeric ? 'right' : 'left' }
}

async function reportPage(pageNo) {
  const target = Math.max(1, Math.min(pageNo, reportPageCount.value))
  if (target === query.pageNo) return
  query.pageNo = target
  await load()
}

function exportReport() {
  const columns = reportColumns.value
  const esc = (value) => {
    const text = String(value ?? '')
    return /[",\n\t]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text
  }
  const csv = '\ufeff' + columns.map(esc).join(',') + '\n' + list.value.map((row) => columns.map((column) => esc(row[column])).join(',')).join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${panelName.value}-${new Date().toISOString().slice(0, 10)}.csv`
  link.click()
  URL.revokeObjectURL(url)
  ElMessage.success('已导出当前页 ' + list.value.length + ' 条数据')
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
    保存: !draftEditable.value || inlineSaving.value,
    保存为草稿: !draftEditable.value || inlineSaving.value,
    保存新增: !draftEditable.value || inlineSaving.value,
  }
  return map[action] === true
}

// 2026-08-20：双击明细行/修改按钮改为面板弹窗打开表单（不再跳新页签）；无编号（新增兜底）仍走页签
const formVisible = ref(false)
const formCode = ref('')
// 弹窗面板：双击=当前面板；选单生成=生成的目标面板（可跨面板）
const formPanel = ref('')
function openForm(row) {
  if (row && row['编号']) {
    formCode.value = row['编号']
    formVisible.value = true
    return
  }
  const q = { operationName: operationName.value }
  if (row && row['编号']) q.code = row['编号']
  const no = row ? row['单据编号'] || row['锭号'] || row['编号'] : ''
  const title = row ? `${panelName.value}-${no}` : `${panelName.value}-新增`
  router.push({ path: `/panelx/form/${panelCode.value}`, query: q })
  tabs.open({ path: `/panelx/form/${panelCode.value}`, title, query: q })
}
function onFormSaved() {
  formVisible.value = false
  formPanel.value = ''
  load()
}

async function onButton(action) {
  if (APPROVE_ACTIONS.includes(action) && !user.isAdmin && !user.approvePanels.includes(panelCode.value)) {
    return ElMessage.warning('当前角色无审批权限')
  }
  if (action === '查询' || action === '查找') {
    search()
    return
  }
  if (reportMode.value && action === '导出') {
    exportReport()
    return
  }
  if (reportMode.value && (action === '打印' || action === '预览')) {
    window.print()
    return
  }
  if (reportMode.value && action === '发送邮件') {
    ElMessage.info('报表邮件发送需先配置企业邮箱服务')
    return
  }
  if (reportMode.value && action === '退出') {
    router.push('/dashboard')
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
  // 选单通用化：任意 选X 动作且配置有 selectConfig 即走选单（对齐 PanelxForm 的通用分支）
  if (action === '选单' || (action.startsWith('选') && cfgCache.value?.selectConfig)) {
    const sc = cfgCache.value?.selectConfig
    if (sc) {
      // 选单通用化：列表页内嵌小弹窗勾选已审核源单据，确定后生成目标单据并打开表单（对齐 T+ 选单生单语义，不再跳转「新增」页面）
      selCfg.value = sc
      selVisible.value = true
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
    newVisible.value = true
    return
  }
  if (action === '修改') {
    if (!current.value) return ElMessage.warning('请先选择一行数据')
    openForm(current.value)
    return
  }
  if (['保存', '保存为草稿', '保存新增'].includes(action) && draftEditable.value) {
    await saveInlineDraft(action)
    return
  }
  if (action === '刷新') {
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

function onSelGenerated(generated) {
  const first = generated && generated[0]
  if (first) {
    // 2026-08-20：选单生成的新单用面板弹窗显示（不再跳新页签）
    formPanel.value = first.panel
    formCode.value = first.no
    formVisible.value = true
  }
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
  if (row?._placeholder && detailEditable(b)) {
    openBlankDetailRow(b)
    return
  }
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
  () => {
    // 2026-08-20：关闭页签/切走时 panelCode 变 undefined——不触发加载（避免「面板编号无效」误报）
    if (!panelCode.value || panelCode.value === 'undefined') return
    cfgCache.value = null
    qOptCache.clear()
    Object.keys(condition).forEach((key) => delete condition[key])
    Object.keys(queryDraft).forEach((key) => delete queryDraft[key])
    query.keyword = ''
    queryFields.value = []
    gridTabs.value = []
    queryRefVisible.value = false
    queryRefField.value = null
    queryDialogVisible.value = false
    headerRefVisible.value = false
    headerRefField.value = null
    detailRefVisible.value = false
    detailRefPick.value = null
    curIdx.value = 0
    search()
  }
)

watch(detailRefVisible, (visible) => {
  if (!visible && !detailRefSaving.value) {
    discardCreatedDetailRefRow(detailRefPick.value)
    detailRefPick.value = null
  }
})

watch(headerRefVisible, (visible) => {
  if (!visible) headerRefField.value = null
})

watch(queryRefVisible, (visible) => {
  if (!visible) queryRefField.value = null
})

onMounted(() => {
  document.addEventListener('click', closeCtx)
  document.addEventListener('contextmenu', closeCtx)
  loadSubBomMap() // 材料下级 BOM 映射（红 * 标记 + 点击行弹窗）
  if (invalidPanel.value) {
    router.replace('/panelx/list/MANU_ORDER')
    return
  }
  if (route.query.new) newVisible.value = true
  load()
})

onDeactivated(() => {
  // keep-alive 切离时关闭弹窗（防止 append-to-body 弹窗残留）
  newVisible.value = false
  queryDialogVisible.value = false
  queryRefVisible.value = false
  queryRefField.value = null
  headerRefVisible.value = false
  headerRefField.value = null
  detailRefVisible.value = false
  detailRefPick.value = null
  impVisible.value = false
  loginVisible.value = false
  maintainVisible.value = false
  selVisible.value = false
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
.toolbar-query-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  align-self: stretch;
  min-width: 64px;
  padding: 0 12px;
  border: 0;
  border-right: 1px solid #c8ced8;
  background: transparent;
  color: #263548;
  font: inherit;
  cursor: pointer;
}
.toolbar-query-btn:hover {
  background: #e7eef8;
  color: #0d5bd3;
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
.report-count {
  color: #64748b;
  font-size: 12px;
  padding-right: 6px;
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
.field :deep(.el-date-editor),
.field :deep(.el-input-number) {
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
.query-ref {
  display: flex;
  width: 192px;
  gap: 4px;
}
.query-ref :deep(.el-input) {
  width: 160px;
}
.query-ref :deep(.el-button) {
  width: 28px;
  min-height: 26px;
  padding: 0;
}
.field-readonly {
  width: 160px;
  min-height: 26px;
  padding: 4px 8px;
  border: 1px solid #d8dde6;
  background: #f7f8fa;
  color: #3f4b5c;
  font-size: 13px;
  line-height: 16px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.header-fields.is-draft {
  background: #fbfdff;
}
.query-dialog-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 20px;
  max-height: 520px;
  overflow-y: auto;
  padding: 2px 4px 4px;
}
.query-dialog-field {
  display: grid;
  grid-template-columns: 100px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.query-dialog-field > label {
  overflow: hidden;
  color: #4b5563;
  font-size: 13px;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.query-dialog-field :deep(.el-input),
.query-dialog-field :deep(.el-select),
.query-dialog-field :deep(.el-date-editor),
.query-dialog-field :deep(.el-input-number),
.query-dialog-field .query-ref {
  width: 100%;
}
.query-dialog-field .query-ref :deep(.el-input) {
  width: auto;
  flex: 1;
}

/* ═══════ ③ 明细区块 ═══════ */
.body {
  flex: 1;
  padding: 8px 10px 0;
  min-height: 0;
}
.report-body {
  flex: 1;
  min-height: 420px;
  padding: 0 10px 10px;
  display: flex;
  flex-direction: column;
  background: #f7f8fa;
}
.report-heading {
  min-height: 44px;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 0 4px;
  color: #1f2937;
}
.report-heading strong {
  font-size: 16px;
  font-weight: 600;
}
.report-heading span {
  color: #64748b;
  font-size: 12px;
}
.report-table {
  flex: 1;
  min-height: 360px;
  background: #fff;
}
:deep(.report-table th.el-table__cell) {
  background: #f3f6fa;
  color: #27364a;
  font-weight: 600;
  padding: 7px 0;
}
:deep(.report-table td.el-table__cell) {
  padding: 5px 0;
}
:deep(.report-table .el-table__footer-wrapper td) {
  background: #f8fafc;
  color: #1f2937;
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
.inline-ref-editor {
  position: relative;
  display: flex;
  align-items: center;
  width: 100%;
}
.inline-ref-editor :deep(.el-input) {
  width: 100%;
}
.inline-computed-value {
  display: block;
  min-height: 30px;
  padding: 6px 8px;
  overflow: hidden;
  color: #556171;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.inline-ref-editor.active :deep(.el-input__wrapper) {
  padding-right: 24px;
  box-shadow: 0 0 0 1px #4b74a6 inset;
}
.list-ref-icon {
  position: absolute;
  right: 7px;
  top: 50%;
  z-index: 2;
  width: 12px;
  height: 12px;
  transform: translateY(-50%);
  font-size: 12px;
  color: #4b74a6;
  pointer-events: none;
}
.detail :deep(.el-table td .el-input),
.detail :deep(.el-table td .el-select),
.detail :deep(.el-table td .el-date-editor),
.detail :deep(.el-table td .el-input-number) {
  width: 100%;
}
.detail :deep(.el-table td .el-input__wrapper),
.detail :deep(.el-table td .el-select__wrapper) {
  min-height: 30px;
  border-radius: 0;
  box-shadow: none;
  background: transparent;
}
.detail :deep(.el-table td .el-input.is-disabled .el-input__wrapper),
.detail :deep(.el-table td .el-input-number.is-disabled .el-input__wrapper),
.detail :deep(.el-table td .el-select__wrapper.is-disabled),
.detail :deep(.el-table td .el-textarea.is-disabled .el-textarea__inner) {
  --el-disabled-bg-color: transparent;
  background: transparent !important;
  background-color: transparent !important;
  box-shadow: none !important;
}
.detail :deep(.el-table td .el-input.is-disabled .el-input__inner),
.detail :deep(.el-table td .el-input-number.is-disabled .el-input__inner) {
  color: #556171;
  -webkit-text-fill-color: #556171;
}
:global(.panelx-list .draft-body .detail .el-input.is-disabled .el-input__wrapper),
:global(.panelx-list .draft-body .detail .el-input-number.is-disabled .el-input__wrapper),
:global(.panelx-list .draft-body .detail .el-select__wrapper.is-disabled),
:global(.panelx-list .draft-body .detail .el-textarea.is-disabled .el-textarea__inner) {
  --el-disabled-bg-color: transparent;
  background: transparent !important;
  background-color: transparent !important;
  box-shadow: none !important;
}
.detail :deep(.el-table td .el-input__wrapper:hover),
.detail :deep(.el-table td .el-select__wrapper:hover) {
  box-shadow: 0 0 0 1px #aab8ca inset;
}
.detail :deep(.el-table td .el-input-number .el-input__wrapper) {
  padding: 1px 8px;
}
.detail :deep(.el-table td .el-switch) {
  margin-left: 8px;
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
  background: #fff !important;
}
:deep(.prod-selected > td.el-table__cell:first-child) {
  box-shadow: inset 3px 0 #7a9abe;
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

@media print {
  .tools,
  .fields,
  .footer,
  .ctx-menu {
    display: none !important;
  }
  .panelx-list,
  .report-body {
    display: block;
    min-height: 0;
    padding: 0;
    background: #fff;
  }
  .report-table {
    height: auto !important;
  }
}

@media (max-width: 780px) {
  .query-dialog-fields {
    grid-template-columns: 1fr;
  }
}

/* ═══════ 移动端适配（≤768px）：触控尺寸 / 单列查询 / 表格横向滚动 ═══════ */
@media (max-width: 768px) {
  /* ① 顶部工具栏：允许换行、触控高度 ≥32px、按钮文字不溢出 */
  .tools {
    row-gap: 6px;
    padding: 6px 8px;
  }
  .toolbar-query-btn {
    min-height: 32px;
    padding: 0 10px;
  }
  .tb-group {
    min-height: 32px;
  }
  .tb-main {
    min-height: 32px;
    padding: 6px 10px;
    max-width: 132px;
  }
  .tb-main .act-name {
    display: block;
    min-width: 0;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tb-caret {
    min-height: 32px;
  }
  .tb-menu {
    min-width: 140px;
    max-height: 300px;
  }

  /* ② 右上分页区：独占一行、可换行、字号 12px 防挤压 */
  .tools-right {
    flex: 1 1 100%;
    margin-left: 0;
    flex-wrap: wrap;
    justify-content: flex-end;
    row-gap: 4px;
    font-size: 12px;
  }
  .page-btn {
    width: 30px;
    height: 30px;
    line-height: 28px;
  }

  /* ③ 查询弹窗字段：多列变单列、label 在上控件在下、间距 10px */
  .query-dialog-fields {
    grid-template-columns: 1fr;
    gap: 10px;
  }
  .query-dialog-field {
    grid-template-columns: 1fr;
    align-items: stretch;
    gap: 10px;
  }
  .query-dialog-field > label {
    text-align: left;
  }

  /* ④ 表格容器：不裁剪、不压缩，列宽溢出交给 el-table 内部横向滚动 */
  .report-body,
  .main-grid,
  .detail {
    min-width: 0;
    max-width: 100%;
  }
  .dt-head {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
  :deep(.el-table .el-table__body td) {
    height: 38px;
  }
  .detail :deep(.el-table td .el-input__wrapper),
  .detail :deep(.el-table td .el-select__wrapper) {
    min-height: 34px;
  }

  /* ⑤ 表尾审计信息区：字号 12px、允许换行 */
  .footer {
    font-size: 12px;
  }
  .remark {
    flex-wrap: wrap;
  }
  .remark :deep(.el-input) {
    min-width: 160px;
  }
  .audit-line {
    gap: 6px 12px;
    padding: 8px 10px 10px;
  }

  /* ⑥ 右键菜单：最小宽度与字号适配触屏 */
  .ctx-menu {
    min-width: 130px;
    max-width: 80vw;
    font-size: 13px;
  }
  .ctx-item {
    padding: 8px 12px;
    font-size: 13px;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}
</style>
