<template>
  <div class="panelx-form">
    <div class="card">
      <!-- 工具栏（T+ 分组形态） -->
      <div class="tools">
        <span v-for="g in groups" :key="g.name" class="tb-group">
          <span class="tb-main" :class="{ disabled: isDisabled(g.actions[0]) }" @click="onButton(g.actions[0])">{{ g.name }}</span>
          <el-dropdown v-if="g.actions.length > 1" @command="(a) => onButton(a)" trigger="click">
            <span class="tb-caret"><el-icon><ArrowDown /></el-icon></span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-for="a in g.actions.slice(1)" :key="a" :command="a" :disabled="isDisabled(a)">
                  <span class="act-name">{{ a }}</span><span v-if="SHORTCUTS[a]" class="act-sc">{{ SHORTCUTS[a] }}</span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </span>
        <span class="tb-group">
          <span v-if="!embedded" class="tb-main back" @click="back">返回</span>
        </span>
        <div class="tools-right">
          <span class="pg">◁</span><span class="pg">◀</span><span class="pg">▶</span><span class="pg">▷</span>
        </div>
      </div>

      <!-- 单据标题 -->
      <div class="head">
        <div class="title">
          <span class="no">{{ isEdit ? (form['单据编号'] || form['锭号'] || form['编号'] || '') : '（新增）' }}</span>
          <el-tag v-if="form['单据状态']" size="small" :type="statusTag(form['单据状态'])">{{ form['单据状态'] }}</el-tag>
        </div>
      </div>

      <!-- 表头（3 列） -->
      <div v-loading="loading" class="fields udl-fields">
        <div v-for="r in visibleMeta" :key="r.code" class="field">
          <label :title="r.name">{{ r.name }}<span v-if="r.isNotNull" class="req">*</span></label>
          <el-input v-if="isText(r)" v-model="form[r.code]" :disabled="!editable || fieldLocked(r)" :placeholder="r.name" />
          <el-input-number
            v-else-if="isNumber(r)"
            v-model="form[r.code]"
            :disabled="!editable || fieldLocked(r)"
            :controls="false"
            style="width: 100%"
          />
          <!-- 参照字段：点击弹窗拉取基础档案面板数据，勾选导入（开发约束十一-1） -->
          <div v-else-if="isRef(r)" class="ref-ctl">
            <el-input
              :model-value="refText(r, form[r.code])"
              readonly
              :disabled="!editable || fieldLocked(r)"
              :placeholder="isEdit ? '' : '点击选择'"
              @click="openRefPick(r)"
            />
            <el-button v-if="editable && !fieldLocked(r)" class="ref-btn" size="small" :icon="Search" @click="openRefPick(r)" />
          </div>
          <el-select v-else-if="isSelect(r)" v-model="form[r.code]" :disabled="!editable || fieldLocked(r)" filterable clearable allow-create style="width: 100%">
            <el-option v-for="o in r.options || []" :key="o" :label="o.label ?? o" :value="o.value ?? o" />
          </el-select>
          <el-date-picker
            v-else-if="isDate(r)"
            v-model="form[r.code]"
            :disabled="!editable || fieldLocked(r)"
            type="date"
            value-format="YYYY-MM-DD"
            style="width: 100%"
          />
          <el-switch v-else-if="isBool(r)" v-model="form[r.code]" :disabled="!editable || fieldLocked(r)" />
          <el-input v-else v-model="form[r.code]" :disabled="!editable || fieldLocked(r)" :placeholder="r.name" />
        </div>
      </div>

      <!-- 表体：三明细上下堆叠（对齐真实 T+：每区自带 明细/汇总 页签行 + 图标行 + 网格 + 分隔条） -->
      <div v-if="tabs.length" class="detail">
        <div v-for="(tab, ti) in tabs" :key="tab.key" class="detail-block">
          <div class="dt-head">
            <div class="dt-tabs">
              <span class="dt-tab" :class="{ on: (subActive[tab.key] || 'detail') === 'detail' }" @click="subActive[tab.key] = 'detail'">
                {{ tab.label }}<span v-if="tab.isRequired" class="req">*</span>
              </span>
              <span
                v-if="tab.summaryItems && tab.summaryItems.length"
                class="dt-tab"
                :class="{ on: subActive[tab.key] === 'summary' }"
                @click="subActive[tab.key] = 'summary'"
              >{{ tab.label }}汇总</span>
            </div>
            <div class="dt-actions">
              <el-button v-if="editable" type="primary" :icon="Plus" @click="addDetailRow(tab)" class="add-data-btn">新增数据</el-button>
              <span class="dt-ic" v-if="ti === 0">Ctrl+V列粘贴</span>
              <span class="dt-ic">定位</span>
              <span class="dt-ic">复制到剪贴板</span>
              <span class="dt-ic">从剪贴板粘贴</span>
              <span class="dt-ic">另存为EXCEL模板</span>
              <span class="dt-ic">批量修改</span>
              <span class="dt-ic" v-if="ti === 0">销售订单查询</span>
              <span class="dt-ic">存货中心</span>
              <span class="dt-ic" v-if="ti === 1">现存量提取</span>
              <span class="dt-ic">更多</span>
              <span v-if="tab.key === 'materials' && selectedProduct" class="filter-hint">当前产品：{{ selectedProduct }} 的 BOM 子件</span>
              <span class="tab-hint">{{ tabHint(tab) }}</span>
            </div>
          </div>

          <!-- 明细视图 -->
          <template v-if="(subActive[tab.key] || 'detail') === 'detail'">
            <el-table
              :data="tabData(tab)"
              size="small"
              border
              :show-summary="true"
              :summary-method="(p) => summarize(p, tab)"
              height="380"
              :row-class-name="(o) => (tab.key === 'products' ? prodRowCls(o) : '')"
              @click.capture="(e) => onTableClickCapture(tab, e)"
              @row-click="(row) => onRowClickDetail(tab, row)"
            >
              <el-table-column v-if="tab.subTable" type="expand" width="40">
                <template #default="{ row }">
                  <div class="sub-wrap">
                    <div class="sub-head">
                      <span class="sub-title">{{ tab.subTable.label }}</span>
                      <el-button v-if="editable" size="small" :icon="Plus" @click="addSubRow(row, tab)">增行</el-button>
                    </div>
                    <el-table :data="row['子表材料'] || []" size="small" border>
                      <el-table-column label="序号" width="50" align="center">
                        <template #default="{ $index }">{{ $index + 1 }}</template>
                      </el-table-column>
                      <el-table-column v-for="sr in tab.subTable.fields" :key="sr.dataName" :label="sr.dataName" min-width="100">
                        <template #default="{ row: sr }">
                          <el-select v-if="sr.dataType === '下拉框'" v-model="sr[sr.dataName]" :disabled="!editable" filterable allow-create style="width: 100%">
                            <el-option v-for="o in sr.options || []" :key="o" :label="o" :value="o" />
                          </el-select>
                          <el-input-number v-else-if="sr.dataType === '小数' || sr.dataType === '整数'" v-model="sr[sr.dataName]" :controls="false" :disabled="!editable" style="width: 100%" />
                          <el-input v-else v-model="sr[sr.dataName]" :disabled="!editable" />
                        </template>
                      </el-table-column>
                      <el-table-column v-if="editable" label="操作" width="50" align="center">
                        <template #default="{ $index }">
                          <el-icon class="del" @click="row['子表材料'].splice($index, 1)"><Delete /></el-icon>
                        </template>
                      </el-table-column>
                    </el-table>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="序号" width="50" align="center" fixed="left">
                <template #default="{ $index }">{{ $index + 1 }}</template>
              </el-table-column>
              <el-table-column
                v-for="dr in visibleFields(tab)"
                :key="dr.dataName"
                :label="dr.dataName"
                min-width="110"
                :class-name="dr.computed ? 'computed-col' : ''"
              >
                <template #default="{ row }">
                  <template v-if="dr.dataType === '参照'">
                    <span v-if="dr.dataName === '材料编码' && tab.key === 'materials'" class="mat-cell">
                      <span class="ref-cell" :class="{ disabled: !detailRefEnabled(dr) }" @click="openDetailRef(dr, row, tab, 'click')">{{ drRefText(dr, row) }}</span>
                      <span v-if="hasSubBom(row[dr.dataName])" class="mat-star" title="该材料有下级子件 BOM，点击行查看">*</span>
                    </span>
                    <span
                      v-else
                      class="ref-cell"
                      :class="{ disabled: !detailRefEnabled(dr), 'dblclick-ref': detailRefTrigger(dr) === 'dblclick' }"
                      :title="detailRefTrigger(dr) === 'dblclick' && detailRefEnabled(dr) ? '双击选择存货' : ''"
                      @click="openDetailRef(dr, row, tab, 'click')"
                      @dblclick.stop="openDetailRef(dr, row, tab, 'dblclick')"
                    >{{ drRefText(dr, row) }}</span>
                  </template>
                  <el-select v-else-if="dr.dataType === '下拉框'" v-model="row[dr.dataName]" :disabled="!editable || dr.computed" filterable allow-create style="width: 100%" @change="onDetailChange(dr, row, tab)">
                    <el-option v-for="o in dr.options || []" :key="o" :label="o.label ?? o" :value="o.value ?? o" />
                  </el-select>
                  <el-switch v-else-if="dr.dataType === '是否'" v-model="row[dr.dataName]" :disabled="!editable || dr.computed" />
                  <el-image
                    v-else-if="dr.dataType === '图片'"
                    :src="row[dr.dataName] || ''"
                    fit="contain"
                    style="width: 34px; height: 34px"
                  >
                    <template #error>
                      <span class="img-ph">图</span>
                    </template>
                  </el-image>
                  <el-input-number
                    v-else-if="dr.dataType === '小数' || dr.dataType === '整数'"
                    v-model="row[dr.dataName]"
                    :controls="false"
                    :disabled="!editable || dr.computed"
                    style="width: 100%"
                  />
                  <el-date-picker
                    v-else-if="dr.dataType === '日期'"
                    v-model="row[dr.dataName]"
                    type="date"
                    value-format="YYYY-MM-DD"
                    :disabled="!editable || dr.computed"
                    style="width: 100%"
                  />
                  <el-input v-else v-model="row[dr.dataName]" :disabled="!editable || dr.computed" />
                </template>
              </el-table-column>
              <el-table-column v-if="editable" label="操作" width="50" align="center" fixed="right">
                <template #default="{ $index }">
                  <el-icon class="del" @click="detailData[tab.key].splice($index, 1)"><Delete /></el-icon>
                </template>
              </el-table-column>
            </el-table>
          </template>

          <!-- 汇总视图（真实 T+ 仅产成品明细/材料明细有汇总页签） -->
          <el-table v-else :data="summaryRows(tab)" size="small" border>
            <el-table-column prop="label" label="汇总项目" min-width="220" />
            <el-table-column prop="value" label="数值" min-width="160" align="right" />
          </el-table>

          <div v-if="ti < tabs.length - 1" class="dt-splitter"></div>
        </div>
      </div>
      <!-- 表尾备注区（对齐真实 T+：备注 + 分隔线 + 审核信息行） -->
      <div class="remark">
        <label>备注</label>
        <el-input v-model="form['备注']" :disabled="!editable" style="width: 100%" />
      </div>
      <div class="footer-hr"></div>

      <!-- 表尾（审核信息栏，对齐真实 T+ 底栏） -->
      <div class="audit-line">
        <span>制单人：{{ form['发起人编号'] || '-' }}</span>
        <span>审核人：{{ form['审核人'] || '-' }}</span>
        <span>审核日期：{{ rmtTime(form['审核日期']) }}</span>
        <span>审核时间：{{ rmtTime(form['审核时间']) }}</span>
        <span>打印次数：{{ form['打印次数'] ?? 0 }}</span>
        <span>创建时间：{{ rmtTime(form['创建时间']) }}</span>
        <span>修改时间：{{ rmtTime(form['更新时间']) }}</span>
        <span>变更人：{{ form['变更人'] || '-' }}</span>
        <span>变更日期：{{ rmtTime(form['变更日期']) }}</span>
        <span>审核机器人：{{ form['审核机器人'] || '-' }}</span>
        <span>审核意见：{{ form['审核意见'] || '-' }}</span>
      </div>

      <!-- 拉式选单（配置驱动：selectConfig 定义来源面板/列/字段映射，对齐 T+ 选单前提：已生效且未中止） -->
      <el-dialog v-model="selectVisible" :title="selectCrg?.title || '选单'" width="920px" append-to-body>
        <div class="sel-tip">{{ selectCrg?.tip || '选择来源单据，明细将带入当前单据' }}</div>
        <el-table
          :data="selectList"
          v-loading="selectLoading"
          size="small"
          border
          height="360"
          @selection-change="(r) => (selectRows = r)"
        >
          <el-table-column type="selection" width="45" />
          <el-table-column
            v-for="c in selectCols"
            :key="c"
            :prop="c"
            :label="c"
            :width="['单据编号', '单据日期', '客户', '预完工日', '预计交货日期'].includes(c) ? 120 : undefined"
            :min-width="['存货名称', '产品名称'].includes(c) ? 200 : undefined"
          />
          <el-table-column label="明细行" min-width="240">
            <template #default="{ row }">
              <span class="sel-item">{{ row['存货名称'] || row['产品名称'] || '' }} × {{ row['数量'] }}{{ row['销售单位'] || row['生产单位'] || '' }}（{{ row['存货编码'] || row['产品编码'] || '' }}）</span>
            </template>
          </el-table-column>
        </el-table>
        <template #footer>
          <el-button @click="selectVisible = false">取消</el-button>
          <el-button type="primary" :disabled="!selectRows.length" @click="confirmSelect">
            确定生单（{{ selectRows.length }} 行）
          </el-button>
        </template>
      </el-dialog>
    </div>
    <RefPickDialog v-model="refVisible" :field="refPick?.field" :mode="refPick?.mode" @confirm="onRefConfirm" />
    <ApprovalHistoryDialog v-model="approvalVisible" :panelCode="panelCode" :formNo="approvalNo" />
    <SelectVoucherDialog v-model="selVisible" :panelCode="panelCode" :config="selCfg" @generated="onSelGenerated" />
    <ImportDialog v-model="impVisible" :fields="impFields" :target-label="impLabel" @imported="onImported" />
    <SubBomDialog v-model="subBomVisible" :material="subBomMaterial" :bom="subBomBom" />
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onDeactivated } from 'vue'
import { useRoute, useRouter } from 'vue-router'

// 2026-08-20：支持弹窗嵌入（列表页双击打开表单弹窗）——panelCodeProp/codeProp 优先于路由；embedded 时保存/返回改为 emit
const props = defineProps({
  panelCodeProp: { type: String, default: '' },
  codeProp: { type: String, default: '' },
  embedded: { type: Boolean, default: false },
})
const emit = defineEmits(['saved'])
import { useTabsStore } from '@/stores/tabs'
import { useUserStore } from '@/stores/user'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Back, Plus, Delete, ArrowDown, Search } from '@element-plus/icons-vue'
import * as engine from '@/business/engine'
import RefPickDialog from './RefPickDialog.vue'
import ApprovalHistoryDialog from './ApprovalHistoryDialog.vue'
import SelectVoucherDialog from './SelectVoucherDialog.vue'
import ImportDialog from './ImportDialog.vue'
import SubBomDialog from './SubBomDialog.vue'
const { SHORTCUTS } = engine

const route = useRoute()
const router = useRouter()
const tabsStore = useTabsStore()
const user = useUserStore()

const panelCode = computed(() => props.panelCodeProp || route.params.panelCode)
const operationName = computed(() => route.query.operationName || '新增流程')
const code = computed(() => props.codeProp || route.query.code)
const isEdit = computed(() => !!code.value)

const form = reactive({})
const meta = ref([])
const detailDef = ref(null)
const detailData = reactive({})
const activeTab = ref('')
const subActive = reactive({})
// 产成品明细选中行 → 材料明细联动过滤（显示该产品的 BOM 子件）
const selectedProduct = ref(null)
const selectedBomCodes = ref([])

// 材料下级 BOM（红 * + 弹窗）：存货编码 → 物料清单 BOM 面板 children 子件行
const subBomMap = ref({})
const subBomVisible = ref(false)
const subBomMaterial = ref(null)
const subBomBom = ref([])
const groups = ref([])
// 审批按钮权限（提交审批/审批情况公开；审批通过/驳回需角色审批权限）
const APPROVE_ACTIONS = ['审批通过', '审批驳回']
function filterGroups(raw) {
  const canApprove = user.isAdmin || user.approvePanels.includes(panelCode.value)
  if (canApprove) return raw
  return (raw || [])
    .map((g) => ({ ...g, actions: (g.actions || g.items || []).filter((a) => !APPROVE_ACTIONS.includes(a)) }))
    .filter((g) => (g.actions || []).length > 0)
}

const loading = ref(false)
const saving = ref(false)
const payloadCache = ref(null)
const approvalVisible = ref(false)
const approvalNo = ref('')
const selVisible = ref(false)
const impVisible = ref(false)
const impFields = ref([])
const impLabel = ref('明细')
const selCfg = ref(null)

// ---------- 拉式选单（配置驱动：selectConfig 定义来源面板/列/字段映射） ----------
const selectVisible = ref(false)
const selectList = ref([])
const selectLoading = ref(false)
const selectRows = ref([])
const selectCrg = ref(null)
const selectCols = computed(() => selectCrg.value?.columns || [])

async function openSelectDialog() {
  const cfg = payloadCache.value?.selectConfig
  if (!cfg) {
    ElMessage.info('演示环境暂未实现「选单」，界面与 T+ 保持一致')
    return
  }
  selectCrg.value = cfg
  selectVisible.value = true
  selectLoading.value = true
  selectRows.value = []
  try {
    // 对齐 T+ 选单前提：已生效（已审核）且未中止的来源单据
    const res = await engine.queryFormDataList({ panelCode: cfg.source, condition: { 单据状态: '已审核' }, pageNo: 1, pageSize: 100 })
    let rows = res.list || []
    // 来源列表返回单据级行（带 detail）时展开为明细行（对齐 T+ 选单按明细行展示/带出；有 detailRows 配置则保持单据粒度）
    if (!cfg.detailRows && rows.some((r) => r.detail)) {
      const flat = []
      for (const r of rows) {
        const d = r.detail
        const key = cfg.detailKey || (d ? Object.keys(d)[0] : null)
        if (key && Array.isArray(d[key])) for (const it of d[key]) flat.push({ ...r, ...it })
      }
      if (flat.length) rows = flat
    }
    // detailRows 配置时选单粒度=单据（如工序汇报单选生产加工单）：按单据编号去重，避免一张多产品单显示多行
    if (cfg.detailRows) {
      const seen = new Set()
      rows = rows.filter((r) => {
        const k = r['单据编号'] || r['编号'] || ''
        if (!k || seen.has(k)) return false
        seen.add(k)
        return true
      })
    }
    selectList.value = rows
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '来源单据加载失败')
  } finally {
    selectLoading.value = false
  }
}

function confirmSelect() {
  const cfg = selectCrg.value
  if (!cfg) return
  // 表头字段映射（headerMap：from 来源字段 → to 当前表头字段；fixed 为固定值）
  const first = selectRows.value[0] || {}
  for (const m of cfg.headerMap || []) {
    if (m.fixed !== undefined) form[m.to] = m.fixed
    else if (m.from) form[m.to] = first[m.from] ?? form[m.to]
  }
  // 来源单号回填（对齐 T+：来源单据 + 来源单号）
  const sourceNos = [...new Set(selectRows.value.map((r) => r['单据编号'] || r['编号'] || '').filter(Boolean))]
  if (sourceNos.length) {
    if (cfg.sourceNoField) form[cfg.sourceNoField] = sourceNos.join('、')
    if (form['来源单据'] === undefined && form['匹配来源单号'] === undefined) form['来源单据'] = cfg.title || '选单'
  }
  // 明细行来源：默认选中行；配置 detailRows 时（如工序汇报单选生产加工单）对每个选中单据取工序明细合并（对齐 T+ 选单带出工序行）
  let srcRows = selectRows.value
  if (cfg.detailRows) {
    const extra = []
    for (const r of selectRows.value) {
      const part = cfg.detailRows(r)
      if (Array.isArray(part)) extra.push(...part)
    }
    if (extra.length) srcRows = extra
  }
  // 明细行映射（detailMap：from → to）
  const rows = srcRows.map((r) => {
    const out = {}
    for (const m of cfg.detailMap || []) {
      out[m.to] = r[m.from] ?? ''
    }
    return out
  })
  if (rows.length && detailDef.value?.tabs?.[0]) {
    const targetTab = detailDef.value.tabs[0]
    detailData[targetTab.key] = rows
    // BOM 展开：目标单含 materials 明细且带入的是产成品明细 → 逐产品带出 BOM 材料（对齐 T+ 选单/生单联动）
    if (targetTab.key === 'products' && detailDef.value.tabs.some((t) => t.key === 'materials')) {
      for (const r of rows) {
        const code = r['产品编码'] || r['存货编码'] || ''
        if (code) loadBomFor(code)
      }
    }
  }
  selectVisible.value = false
  ElMessage.success('已带入 ' + rows.length + ' 行明细')
  applyCalc()
}
const status = computed(() => form['单据状态'] || '草稿')
// 单据=草稿可编辑；基础档案（存货/部门等）状态为 启用/停用 同样可编辑（增行/改字段/保存）
const editable = computed(() => !isEdit.value || status.value === '草稿' || status.value === '启用' || status.value === '停用')

const STATUS_TAG = { 草稿: 'info', 已审核: 'primary', 生产中: 'warning', 已完工: 'success', 已中止: 'danger', 已关闭: 'info' }
function statusTag(s) {
  return STATUS_TAG[s] || 'info'
}

const tabs = computed(() => {
  const d = detailDef.value
  if (!d) return []
  if (Array.isArray(d.tabs)) return d.tabs
  return [{ key: 'detail', label: d.label || '明细', fields: d.fields || [], isRequired: true }]
})

function visibleFields(tab) {
  return (tab.fields || []).filter((r) => !r.hidden)
}

const visibleMeta = computed(() => (meta.value || []).filter((r) => !r.hidden))

function fieldLocked(r) {
  // 锭号：自动编码；仅勾选「是否手工修改单据编码」时草稿可改
  if (r.autoCode) return !(status.value === '草稿' && form['是否手工修改单据编码'])
  // 存货类别：创建后固定，不允许修改
  if (r.code === '类别' && isEdit.value) return true
  return false
}

function isDisabled(action) {
  const s = status.value
  const map = {
    保存: !editable.value,
    保存新增: !editable.value,
    保存为草稿: !editable.value,
    删除: s !== '草稿' || !isEdit.value,
    审核: s !== '草稿' || !isEdit.value,
    弃审: s !== '已审核',
    中止执行: !['已审核', '生产中', '已完工'].includes(s) || !isEdit.value,
    整单中止: !['已审核', '生产中', '已完工'].includes(s) || !isEdit.value,
    草稿: s !== '已中止',
    取消中止: s !== '已中止',
    修改: !['已审核', '生产中', '已完工'].includes(s) || !isEdit.value,
    审批情况: false,
    提交审批: s !== '草稿' || !isEdit.value,
    审批通过: s !== '审批中' || !isEdit.value,
    审批驳回: s !== '审批中' || !isEdit.value,
  }
  // 2026-08-25：所有「生成XX」生单按钮统一仅已审核/生产中可用（对齐 T+：已审核才能选择生单）
  if (map[action] === undefined && action.startsWith('生成')) {
    return !['已审核', '生产中'].includes(s) || !isEdit.value
  }
  return map[action] === true
}

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
  return r.dataType === '下拉框' || r.dataType === '参照'
}

// ---------- 参照字段弹窗选择（开发约束十一-1：能对应基础档案的字段弹窗拉取勾选导入） ----------
const refVisible = ref(false)
const refPick = ref(null)

function isRef(r) {
  return r.dataType === '参照' && (r.ref || r.refPanel)
}

function refText(r, v) {
  if (v === undefined || v === null || v === '') return ''
  const t = engine.refLabelOf(r, v)
  return t === null || t === undefined ? String(v) : t
}

function drRefText(dr, row) {
  return refText(dr, row[dr.dataName])
}

function openRefPick(r) {
  if (!editable.value || fieldLocked(r)) return
  refPick.value = { field: r, kind: 'header', code: r.code, mode: 'header' }
  refVisible.value = true
}

function detailRefConfig(dr) {
  return dr.ref || dr
}

function detailRefTrigger(dr) {
  const r = detailRefConfig(dr)
  return r.trigger || r.refTrigger || 'click'
}

function detailRefEnabled(dr) {
  if (!editable.value || dr.computed) return false
  const r = detailRefConfig(dr)
  const statuses = r.statuses || r.refStatuses
  return !Array.isArray(statuses) || statuses.length === 0 || statuses.includes(status.value)
}

function openDetailRef(dr, row, tab, trigger = 'click') {
  if (!detailRefEnabled(dr) || detailRefTrigger(dr) !== trigger) return
  refPick.value = { field: dr, kind: 'detail', row, tab, mode: 'detail' }
  refVisible.value = true
}

async function onRefConfirm(rows) {
  const p = refPick.value
  if (!p || !rows.length) return
  const r = p.field
  const rp = r.ref || r
  const refField = rp.field || rp.refField
  const multi = !!(rp.multi || rp.refMulti)
  const vals = rows.map((x) => x[refField])
  if (p.kind === 'header') {
    form[p.code] = multi ? vals.join('、') : vals[0]
    const first = rows[0] || {}
    for (const m of rp.map || rp.refMap || []) {
      if (!m || first[m.from] === undefined) continue
      const to = m.to || m.from
      if (to !== p.code) form[to] = first[m.from]
    }
  } else {
    const row = p.row
    const tab = p.tab
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
    // 明细行参照：勾选 N 行一次导入 → 当前行填第一行，其余每行生成一条明细（含 refMap 带出）
    rillRow(row, rows[0] || {})
    if (rows.length > 1 && tab) {
      for (let i = 1; i < rows.length; i++) {
        const nr = addDetailRow(tab)
        rillRow(nr, rows[i])
      }
    }
    if (tab?.key === 'products' && r.dataName === '产品编码') {
      for (const selected of rows) loadBomFor(selected[refField])
    }
  }
  refVisible.value = false
  applyCalc()
  ElMessage.success(`已导入 ${rows.length} 行${await engine.refPanelName(r)}数据`)
}

function rmtTime(t) {
  if (!t) return '-'
  if (typeof t === 'number') return new Date(t).toLocaleString('zh-CN', { hour12: false })
  const s = String(t)
  return s.length > 10 ? s.slice(0, 19).replace('T', ' ') : s
}

// 明细数据：材料明细在选中产成品时只显示其 BOM 子件（子件BOM=产品编码）
function tabData(tab) {
  const rows = detailData[tab.key] || []
  if (tab.key === 'materials' && selectedProduct.value) {
    // 优先按 子件BOM 标记精确过滤（回填/自动带出的材料行带 子件BOM=产品编码）；
    // 兜底按该产品存货 BOM 的材料编码过滤（旧手工行无标记时）
    const byBom = rows.filter((m) => m['子件BOM'] === selectedProduct.value)
    if (byBom.length) return byBom
    const byCode = rows.filter((m) => selectedBomCodes.value.includes(m['材料编码']))
    if (byCode.length) return byCode
    return rows
  }
  return rows
}

function prodRowCls({ row }) {
  return selectedProduct.value && row['产品编码'] === selectedProduct.value ? 'prod-selected' : ''
}

// 捕获阶段监听：点产成品明细行触发联动；点材料明细行（该材料有下级 BOM）弹出子件 BOM
async function onTableClickCapture(tab, e) {
  if (!e || !e.target || !e.target.closest) return
  const tr = e.target.closest('tr')
  if (!tr) return
  // 主表与固定列（序号列 fixed=left）都可能触发：两种 body 容器都支持
  const body = tr.closest('.el-table__body-wrapper') || tr.closest('.el-table__fixed-body-wrapper')
  const rows = body ? [...body.querySelectorAll('tbody tr')] : []
  const idx = rows.indexOf(tr)
  // 材料明细：点材料行 → 该材料有下级 BOM 则弹窗展示（拦截参照/其他点击），无下级放行
  if (tab.key === 'materials') {
    const row = (detailData['materials'] || [])[idx]
    if (row && row['材料编码'] && hasSubBom(row['材料编码'])) {
      openSubBom(row)
      if (e.stopPropagation) e.stopPropagation()
    }
    return
  }
  if (tab.key !== 'products') return
  const row = (detailData['products'] || [])[idx]
  if (!row || !row['产品编码']) return
  selectProduct(row['产品编码'])
}

// 材料下级 BOM 映射（BOM 面板 children：父件编码 → 子件行）；加载后材料编码行右上角显示红 *，点击行弹窗查看
async function loadSubBomMap() {
  try {
    const res = await engine.queryFormDataList({ panelCode: 'BOM', condition: {}, pageNo: 1, pageSize: 100 })
    const map = {}
    for (const d of res.list || []) {
      for (const it of (d.detail && d.detail.children) || []) {
        const parent = it['父件编码']
        if (!parent) continue
        if (!map[parent]) map[parent] = []
        map[parent].push({
          材料编码: it['子件编码'],
          材料名称: it['子件名称'],
          规格型号: it['规格型号'] || '',
          计量单位: it['子件计量单位'] || '件',
          定额需用数量: it['定额数量'] ?? 0,
          '损耗率%': it['损耗率%'] ?? 0,
        })
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

// row-click 兜底：普通单元格点击走此路径（控件内点击被 el-select 等吞掉时由捕获阶段补上）
function onRowClickDetail(tab, row) {
  if (tab.key === 'products' && row && row['产品编码'] && row['产品编码'] !== selectedProduct.value) {
    selectProduct(row['产品编码'])
  }
}

// 选中产成品：行高亮 + 材料明细只显示该产品的 BOM 子件（物料清单 BOM 面板 children → 子件编码集合；2026-08-25 原 INV _bom 已迁移）
async function selectProduct(code) {
  selectedProduct.value = code
  selectedBomCodes.value = []
  try {
    const res = await engine.queryFormDataList({ panelCode: 'BOM', condition: {}, pageNo: 1, pageSize: 200 })
    const codes = []
    for (const d of res.list || []) {
      for (const it of (d.detail && d.detail.children) || []) {
        if (String(it['父件编码']) !== code) continue
        if (it['子件编码']) codes.push(String(it['子件编码']))
      }
    }
    selectedBomCodes.value = codes
  } catch (err) {
    // 查询失败按 子件BOM 标记兜底
  }
}

function tabHint(tab) {
  if (tab.key === 'materials') return '需用数量 = 定额需用数量 × 产品数量 ÷ 定额生产数量；计划数量 = 需用数量 + 损耗数量（损耗数量手工录入）'
  if (tab.key === 'processes') return '金额 = 计划数量 × 工价；行「手工完工」用于工序级完工；展开行维护本工序材料'
  if (tab.key === 'products') return '产品数量为工单生产数量，材料明细按此数量展开定额'
  return ''
}

function addDetailRow(tab) {
  const rows = detailData[tab.key] || (detailData[tab.key] = [])
  const row = {}
  for (const dr of tab.fields || []) {
    if (dr.dataType === '小数' || dr.dataType === '整数') row[dr.dataName] = dr.defaultValue ?? 0
    else if (dr.dataType === '是否') row[dr.dataName] = dr.defaultValue ?? false
    else row[dr.dataName] = dr.defaultValue ?? ''
  }
  if (tab.subTable) row['子表材料'] = []
  // 材料明细：新增行自动归属当前选中的产成品（子件BOM=产品编码），保证联动过滤精确
  if (tab.key === 'materials' && selectedProduct.value) row['子件BOM'] = selectedProduct.value
  rows.push(row)
  return row
}

function addSubRow(row, tab) {
  if (!row['子表材料']) row['子表材料'] = []
  const sr = {}
  for (const sr of tab.subTable.fields || []) {
    if (sr.dataType === '小数' || sr.dataType === '整数') sr[sr.dataName] = sr.defaultValue ?? 0
    else sr[sr.dataName] = sr.defaultValue ?? ''
  }
  row['子表材料'].push(sr)
}

// ---------- 表达式计算链 ----------

function num(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

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
  const rows = detailData.products || []
  return rows.reduce((s, r) => s + num(r['数量']), 0)
}

function applyCalc() {
  for (const tab of tabs.value) {
    const rows = detailData[tab.key] || []
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

watch(detailData, applyCalc, { deep: true })

// ---------- BOM 联动：产成品明细选产品 → 从存货面板（INV）BOM 子表带出材料明细 ----------
const bomLoaded = new Set()

function onDetailChange(dr, row, tab) {
  if (tab && tab.key === 'products' && dr.dataName === '产品编码') {
    loadBomFor(row[dr.dataName])
  }
}

function bomRowFrom(b, code) {
  const matTab = tabs.value.find((t) => t.key === 'materials')
  const row = {}
  for (const dr of (matTab ? matTab.fields : [])) {
    if (dr.dataType === '小数' || dr.dataType === '整数') row[dr.dataName] = dr.defaultValue ?? 0
    else if (dr.dataType === '是否') row[dr.dataName] = dr.defaultValue ?? false
    else row[dr.dataName] = dr.defaultValue ?? ''
  }
  row['材料编码'] = b['材料编码'] || ''
  row['材料名称'] = b['材料名称'] || ''
  row['规格型号'] = b['规格型号'] || ''
  row['计量单位'] = b['计量单位'] || 'kg'
  row['定额需用数量'] = b['定额需用数量'] ?? 0
  row['损耗率%'] = b['损耗率%'] ?? 0
  row['子件BOM'] = code // BOM 关系：本材料属于哪个产成品
  return row
}

/** BOM 自动展开：面板含 products+materials 明细、产品明细有行而材料明细为空时，按产品 BOM 带出材料 */
async function autoExpandBom() {
  const tabsDef = tabs.value || []
  if (!tabsDef.some((t) => t.key === 'products') || !tabsDef.some((t) => t.key === 'materials')) return
  const products = detailData['products'] || []
  const mats = detailData['materials'] || []
  if (!products.length || mats.length) return
  for (const p of products) {
    const code = p['产品编码'] || p['存货编码'] || ''
    if (code) await loadBomFor(code)
  }
}

async function loadBomFor(code) {
  if (!code || bomLoaded.has(code)) return
  bomLoaded.add(code)
  try {
    // BOM 数据源：物料清单 BOM 面板 children（父件编码 → 子件行；2026-08-25 原 INV 物品行 _bom 已迁移废弃）
    const res = await engine.queryFormDataList({ panelCode: 'BOM', condition: {}, pageNo: 1, pageSize: 100 })
    const bom = []
    for (const d of res.list || []) {
      for (const it of (d.detail && d.detail.children) || []) {
        if (String(it['父件编码']) !== code) continue
        bom.push({
          材料编码: it['子件编码'],
          材料名称: it['子件名称'],
          规格型号: it['规格型号'] || '',
          计量单位: it['子件计量单位'] || '件',
          定额需用数量: it['定额数量'] ?? 0,
          '损耗率%': it['损耗率%'] ?? 0,
        })
      }
    }
    if (!bom.length) return
    const mats = detailData['materials'] || (detailData['materials'] = [])
    for (const b of bom) mats.push(bomRowFrom(b, code))
    ElMessage.success('已按 BOM 带入 ' + bom.length + ' 行材料（' + code + '）')
  } catch (e) {
    // 无 BOM 或查询失败不阻塞录入
  }
}

// ---------- 汇总 ----------

function summaryRows(tab) {
  const items = tab.summaryItems || []
  const rows = detailData[tab.key] || []
  return items.map((it) => {
    const v = rows.reduce((s, r) => s + num(r[it.field]), 0)
    return { label: it.label, value: Math.round(v * 100) / 100 }
  })
}

// 明细网格合计行（对齐 T+ 网格底部合计）
function summarize({ columns }, tab) {
  const rows = tabData(tab)
  const sums = ['合计']
  for (let i = 1; i < columns.length; i++) {
    const col = columns[i]
    if (!col) continue
    const label = String(col.label || '')
    if (i === columns.length - 1) {
      sums.push('')
      continue
    }
    const field = (tab.fields || []).find((r) => r.dataName === label)
    if (field && (field.dataType === '小数' || field.dataType === '整数')) {
      sums.push(Math.round(rows.reduce((s, r) => s + num(r[label]), 0) * 100) / 100)
    } else {
      sums.push('')
    }
  }
  return sums
}

// ---------- 校验 / 加载 / 按钮 ----------

function validate() {
  for (const r of visibleMeta.value) {
    if (r.isNotNull && (form[r.code] === undefined || form[r.code] === null || String(form[r.code]).trim() === '')) {
      return `${r.name}不能为空`
    }
  }
  for (const tab of tabs.value) {
    if (tab.isRequired && !(detailData[tab.key] || []).length) return `请至少添加一行${tab.label}`
  }
  return ''
}

async function load() {
  loading.value = true
  try {
    let payload
    if (isEdit.value) {
      payload = await engine.getFormDescriptor({ panelCode: panelCode.value, code: code.value })
    } else {
      payload = await engine.getNewFormPermMatrix({ panelCode: panelCode.value, operationName: operationName.value })
    }
    Object.keys(detailData).forEach((k) => delete detailData[k])
    const dd = payload.detailData
    if (Array.isArray(dd)) detailData.detail = dd
    else if (dd && typeof dd === 'object') Object.assign(detailData, dd)
    Object.keys(form).forEach((k) => delete form[k])
    Object.assign(form, payload.data || {})
    meta.value = payload.meta || []
    detailDef.value = payload.detail || null
    groups.value = filterGroups(payload.buttonGroups || [])
    payloadCache.value = payload
    const firstTab = tabs.value.find((t) => t.type !== 'summary')
    if (firstTab && !activeTab.value) activeTab.value = firstTab.key
    for (const t of tabs.value) {
      if (!subActive[t.key]) subActive[t.key] = 'detail'
    }
    applyCalc()
    // 产成品→材料联动：默认不选中（点击产成品明细行才过滤材料明细），加载后重置
    selectedProduct.value = null
    selectedBomCodes.value = []
    // BOM 自动展开：草稿单已有产品明细但材料明细为空时，按产品 BOM 带出材料
    // （覆盖列表页选单生成、推式生单、历史草稿等所有入口；材料非空不重复展开）
    autoExpandBom()
    // 页签标题 = 面板名-单据号（新单显示 面板名-新增），便于多单据区分（弹窗嵌入模式跳过）
    if (!props.embedded) {
      const no = isEdit.value ? (form['单据编号'] || form['锭号'] || form['编号'] || '') : '新增'
      // 复用顶部 tabsStore
      const cur = tabsStore.tabs.find((x) => x.path === route.path)
      if (cur) cur.title = (payload.panelName || '表单') + '-' + no
      // 页面标题 = 真实面板名（路由 meta.title 是通用占位，配置加载后覆盖）
      document.title = (payload.panelName || '表单') + ' · 轻MES'
    }
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载失败')
    back()
  } finally {
    loading.value = false
  }
}

async function onButton(action) {
  // 2026-08-25：灰按钮（disabled）点击直接忽略，不执行、不弹提示（如草稿态「生成XX」生单按钮）
  if (isDisabled(action)) return
  if (APPROVE_ACTIONS.includes(action) && !user.isAdmin && !user.approvePanels.includes(panelCode.value)) {
    return ElMessage.warning('当前角色无审批权限')
  }
  if (action === '修改') {
    ElMessage.info(isEdit.value ? '当前单据已处于编辑状态' : '请先打开单据后再修改')
    return
  }
  if (action === '放弃' || action === '取消') {
    back()
    return
  }
  // Excel 导入：识别当前主明细 tab 字段，导入后追加明细行
  if (action === '导入') {
    const tab = tabs.value[0]
    if (!tab) return ElMessage.warning('该面板无明细可导入')
    impFields.value = (tab.fields || []).filter((f) => !f.hidden)
    impLabel.value = tab.label || '明细'
    impVisible.value = true
    return
  }
  // 拉式选单（配置驱动：selectConfig.generateButton 存在 → 新弹窗直接生单；否则旧带入流程）
  if (action === '选单' || (action.startsWith('选') && payloadCache.value?.selectConfig)) {
    const sc = payloadCache.value?.selectConfig
    if (sc) {
      if (sc.generateButton) {
        selCfg.value = sc
        selVisible.value = true
      } else {
        openSelectDialog()
      }
      return
    }
    ElMessage.info('演示环境暂未实现「选单」，界面与 T+ 保持一致')
    return
  }
  if (action === '新增') {
    if (isEdit.value && status.value === '草稿') {
      try {
        await ElMessageBox.confirm('当前单据尚未保存，切换新增将丢弃修改，是否继续？', '提示', { type: 'warning' })
      } catch (e) {
        return
      }
    }
    router.replace({ path: `/panelx/form/${panelCode.value}`, query: { operationName: operationName.value } })
    return
  }
  if (action === '删除') {
    try {
      await ElMessageBox.confirm(`确认删除单据 ${form['单据编号'] || form['锭号'] || form['编号']}？`, '提示', { type: 'warning' })
    } catch (e) {
      return
    }
  }
  // 人工审核：确认弹窗 + 审核意见（选填）；审核人取当前登录人（后端从 JWT 取）
  let auditOpinion = ''
  if (action === '审核') {
    // 已审核过的单据不允许再次审核，也不允许补填审批意见
    if (status.value !== '草稿') return ElMessage.warning('仅草稿状态可审核，已审核单据不允许再次审核')
    const no = form['单据编号'] || form['锭号'] || form['编号'] || ''
    try {
      const { value } = await ElMessageBox.prompt(
        '单据：' + no + '（当前状态：' + status.value + '）',
        '人工审核确认',
        { confirmButtonText: '确认审核', cancelButtonText: '取消', inputType: 'textarea', inputPlaceholder: '审核意见（选填）' }
      )
      auditOpinion = value || ''
    } catch (e) {
      return
    }
  } else if (action === '弃审') {
    if (status.value !== '已审核') return ElMessage.warning('仅已审核状态可弃审')
    try {
      await ElMessageBox.confirm('确认弃审该单据？弃审后需重新审核。', '弃审确认', { type: 'warning' })
    } catch (e) {
      return
    }
  }
  // 审批流：提交审批/审批通过（确认+意见）、审批驳回（意见必填）、审批情况（历史弹窗）
  let approvalOpinion = ''
  if (action === '提交审批' || action === '审批通过') {
    const need = action === '提交审批' ? '草稿' : '审批中'
    if (status.value !== need) return ElMessage.warning(action === '提交审批' ? '仅草稿状态可提交审批' : '仅审批中状态可审批通过')
    const no = form['单据编号'] || form['锭号'] || form['编号'] || ''
    try {
      const { value } = await ElMessageBox.prompt(
        '单据：' + no + '（当前状态：' + status.value + '）',
        action + '确认',
        { confirmButtonText: '确认' + action, cancelButtonText: '取消', inputType: 'textarea', inputPlaceholder: action === '审批通过' ? '审批意见（选填）' : '提交说明（选填）' }
      )
      approvalOpinion = value || ''
    } catch (e) {
      return
    }
  } else if (action === '审批驳回') {
    if (status.value !== '审批中') return ElMessage.warning('仅审批中状态可审批驳回')
    const no = form['单据编号'] || form['锭号'] || form['编号'] || ''
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
    approvalNo.value = form['编号'] || form['单据编号'] || form['锭号'] || ''
    approvalVisible.value = true
    return
  }
  if (['保存', '保存为草稿', '保存新增'].includes(action)) {
    const msg = validate()
    if (msg) return ElMessage.warning(msg)
  }
  if (action === '刷新') {
    await load()
    return
  }
  saving.value = true
  try {
    const rd = { ...form }
    if (tabs.value.length) rd.detail = { ...detailData }
    if (auditOpinion !== '') rd['审核意见'] = auditOpinion
    if (approvalOpinion !== '') rd['审批意见'] = approvalOpinion
    const res = await engine.callButton({
      panelCode: panelCode.value,
      buttonName: action,
      formData: rd,
      buttonParam: isEdit.value ? { code: form['编号'] } : {},
    })
    // 推式生单结果：直接跳转目标面板列表页（不弹窗、不新开标签页），新单按创建时间倒序显示在第一张（草稿内联可编辑）
    if (res?.gotoPanel) {
      if (props.embedded) {
        emit('saved', res)
        return
      }
      ElMessage.success(`已生成${res.gotoPanel === 'MANU_ORDER' ? '生产加工单' : res.gotoPanel}：${res['编号']}，请在列表页继续填写`)
      const targetPath = `/panelx/list/${res.gotoPanel}`
      tabsStore.close(route.path) // 关闭当前源表单页签（页签被目标面板替换）
      router.push(targetPath)
      tabsStore.open({ path: targetPath, title: res.gotoPanel })
      return
    }
    ElMessage.success(`「${action}」成功`)
    if (action === '新增流程' && !isEdit.value) {
      back()
      return
    }
    if (action === '保存新增') {
      router.replace({ path: `/panelx/form/${panelCode.value}`, query: { operationName: operationName.value } })
    } else if (['保存', '保存为草稿', '删除', '审核', '弃审', '中止执行', '整单中止', '草稿', '取消中止'].includes(action)) {
      if (action === '删除') {
        back()
      } else if (isEdit.value && res?.['编号']) {
        await load()
      } else {
        back()
      }
    }
  } catch (e) {
    const msg = engine.errMsg(e) || '按钮执行失败'
    if (msg.includes('演示环境暂未实现')) ElMessage.info(msg)
    else ElMessage.error(msg)
  } finally {
    saving.value = false
  }
}

function back() {
  if (props.embedded) {
    emit('saved')
    return
  }
  router.push({ path: `/panelx/list/${panelCode.value}` })
}

// Excel 导入完成：行追加到主明细，提示保存落库
function onImported(rows) {
  const tab = tabs.value[0]
  if (!tab || !Array.isArray(rows)) return
  const target = detailData[tab.key] || (detailData[tab.key] = [])
  for (const r of rows) target.push(r)
  ElMessage.success('已导入 ' + rows.length + ' 行到「' + (tab.label || tab.key) + '」，请点击保存落库')
}

// 新选单弹窗生单完成：2026-08-25 不再弹窗展示，直接跳目标面板列表页并定位新选入单据
function onSelGenerated(generated) {
  const first = generated && generated[0]
  if (!first) return
  const path = '/panelx/list/' + first.panel
  router.push({ path, query: { focus: first.no } })
  tabsStore.open({ path, title: first.panel, query: { focus: first.no } })
}

onMounted(() => {
  load()
  // 材料下级 BOM 映射（红 * 标记 + 点击行弹窗）
  loadSubBomMap()
})

// keep-alive 切离时关闭所有弹窗（防止 append-to-body 弹窗在路由切换后残留）
onDeactivated(() => {
  selectVisible.value = false
  selVisible.value = false
  refVisible.value = false
  impVisible.value = false
})

// 从列表页「选单」入口（?select=1）跳转而来：load 完成后自动弹出选单对话框（payloadCache 异步赋值）
watch(payloadCache, (v) => {
  if (route.query.select && v && v.selectConfig && !selVisible.value) openSelectDialog()
})

watch(() => [panelCode.value, code.value], () => {
  // 2026-08-20：关闭页签/切走时 panelCode 变 undefined——不触发加载
  if (!panelCode.value || panelCode.value === 'undefined') return
  // 路由参数变化（同组件切换面板）时关闭所有弹窗，防止 append-to-body 弹窗残留
  selectVisible.value = false
  selVisible.value = false
  refVisible.value = false
  impVisible.value = false
  load()
})
</script>

<style scoped>
.card {
  background: var(--t-card-bg);
  border-radius: 6px;
  padding: 12px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.05);
}
.tools {
  display: flex;
  align-items: center;
  gap: 0;
  flex-wrap: wrap;
  background: #f5f7fa;
  border-bottom: 1px solid #d0d7e3;
  padding: 8px 12px;
}
.tb-group {
  display: inline-flex;
  align-items: center;
  margin-right: 12px;
}
.tb-main {
  display: inline-flex;
  align-items: center;
  padding: 0;
  font-size: 13px;
  color: #222;
  background: transparent;
  cursor: pointer;
  user-select: none;
  text-decoration: none;
}
.tb-main:hover {
  color: #3788FF;
}
.tb-main.disabled {
  color: #999;
  cursor: not-allowed;
}
.tb-main.back {
  margin-left: 0;
}
.tb-caret {
  display: inline-flex;
  align-items: center;
  padding: 0 2px;
  font-size: 12px;
  color: #222;
  cursor: pointer;
  outline: none;
}
.tb-caret:hover {
  color: #3788FF;
}
.tools-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 2px;
}
.tools-right .pg {
  width: 22px;
  height: 20px;
  border: 1px solid #d0d7e3;
  background: #fff;
  color: #222;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 12px;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin: 8px 0 4px;
}
.title {
  display: flex;
  align-items: center;
  gap: 10px;
}
.title .no {
  font-size: 16px;
  font-weight: 700;
  color: #222;
}
.fields {
  display: block;
  padding: 12px;
  background: #fff;
  min-height: 0;
}
.field {
  display: inline-block;
  vertical-align: top;
  margin: 0 24px 8px 0;
}
.field label {
  display: block;
  margin-bottom: 4px;
  font-size: 13px;
  color: #222;
  text-align: left;
  white-space: nowrap;
}
.field .req {
  color: #dc2626;
}
.field .el-input,
.field .el-select,
.field .el-date-editor,
.field .ref-ctl {
  width: 160px !important;
}
.field .ref-ctl {
  display: flex;
  align-items: center;
  gap: 2px;
}.field .req {
  color: #dc2626;
}
.detail {
  margin-top: 12px;
}
.tab-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.prod-selected {
  background: #eef4ff !important;
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
  font-weight: 600;
  margin-right: 8px;
}
.tab-hint {
  font-size: 12px;
  color: var(--t-text-3);
}
.sub-tabs {
  margin-top: 2px;
}
.sub-wrap {
  padding: 6px 8px;
}
.sub-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.sub-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--t-text-1);
}
.del {
  cursor: pointer;
  color: #dc2626;
}
.img-ph {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
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
.audit-line {
  margin-top: 12px;
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--t-text-3);
}
.ref-ctl {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: 1;
}
.ref-ctl .el-input {
  flex: 1;
}
.ref-btn {
  flex-shrink: 0;
}
.ref-cell {
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 0 6px;
  color: var(--t-primary);
  cursor: pointer;
}
.ref-cell.disabled {
  color: inherit;
  cursor: default;
}
.sel-tip {
  font-size: 12px;
  color: var(--t-text-3);
  margin-bottom: 8px;
}
.sel-item {
  margin-right: 10px;
  white-space: nowrap;
}
:deep(.computed-col) {
  background: var(--t-content-bg);
}
:deep(.el-table__footer-wrapper .cell) {
  font-weight: 600;
}
.detail-block {
  margin-bottom: 4px;
}
.dt-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 0 2px;
}
.dt-tabs {
  display: flex;
  align-items: center;
  gap: 14px;
}
.dt-tab {
  font-size: 14px;
  color: #666;
  cursor: pointer;
  padding-bottom: 3px;
  user-select: none;
}
.dt-tab.on {
  color: #3788FF;
  font-weight: 700;
  border-bottom: 2px solid #3788FF;
}
.dt-tab .req {
  color: #dc2626;
}
.dt-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: #333;
}
.dt-ic {
  color: #333;
  cursor: pointer;
}
.dt-ic:hover {
  color: #3788FF;
}
.dt-splitter {
  height: 8px;
  border-top: 1px solid #ddd;
  margin: 6px 0;
}.remark {
  margin-top: 14px;
}
.remark label {
  display: block;
  margin-bottom: 4px;
  font-size: 13px;
  color: #222;
}
.footer-hr {
  border-top: 1px solid #ccc;
  margin: 10px 0;
}
.audit-line {
  margin-top: 10px;
  display: flex;
  gap: 40px;
  flex-wrap: wrap;
  font-size: 13px;
  color: #222;
}
.dt-head {
  border-bottom: 1px solid #ccc;
  padding: 8px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: #fff;
}
.dt-tabs {
  display: flex;
  align-items: flex-end;
  gap: 2px;
}
.dt-tab {
  display: inline-block;
  padding: 4px 12px;
  cursor: pointer;
  font-size: 13px;
  color: #222;
  border: 1px solid transparent;
  border-bottom: none;
  margin-bottom: -1px;
  user-select: none;
  background: transparent;
}
.dt-tab.on {
  border: 1px solid #ccc;
  border-bottom: 1px solid #fff;
  background: #fff;
  font-weight: 700;
}
.dt-tab .req {
  color: #dc2626;
}
.dt-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: #222;
}
.dt-ic {
  color: #222;
  cursor: pointer;
  text-decoration: none;
}
.dt-ic:hover {
  color: #3788FF;
}
.dt-splitter {
  height: 10px;
  border-bottom: 1px solid #d0d7e3;
  background: #f5f7fa;
}
.detail :deep(.el-table th.el-table__cell) {
  background: #f7f9fc;
  color: #222;
  font-weight: 700;
}

/* ═══════ 移动端响应式（≤768px）：单列字段、触控尺寸、明细横向滚动 ═══════ */
@media (max-width: 768px) {
  .card { padding: 8px; border-radius: 0; }

  .tools { gap: 4px 8px; padding: 8px; }
  .tools .tb-group { margin-right: 8px; }
  .tools .tb-main { min-height: 32px; font-size: 13px; }
  .tools .tb-caret { min-height: 32px; }
  .tools-right { font-size: 12px; }
  .tools-right .pg { width: 26px; height: 26px; min-height: 26px; font-size: 11px; }

  .head { margin: 6px 0 2px; }
  .title .no { font-size: 16px; max-width: 60vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .head :deep(.el-tag) { height: 22px; line-height: 20px; font-size: 12px; max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .fields.udl-fields { padding: 10px 8px; }
  .fields.udl-fields .field { display: block; width: 100%; margin: 0 0 10px; }
  .fields.udl-fields .field label { margin-bottom: 6px; }
  .fields.udl-fields .field .el-input,
  .fields.udl-fields .field .el-select,
  .fields.udl-fields .field .el-date-editor,
  .fields.udl-fields .field .ref-ctl { width: 100% !important; }
  .fields.udl-fields .field :deep(.el-input__wrapper),
  .fields.udl-fields .field :deep(.el-select__wrapper) { min-height: 32px; }
  .fields.udl-fields :deep(.el-date-editor.el-input) { width: 100%; height: 32px; }
  .fields.udl-fields :deep(.el-date-editor .el-input__wrapper) { height: 32px; }
  .fields.udl-fields .ref-ctl .ref-btn { min-height: 32px; min-width: 32px; }

  .detail-block { min-width: 0; }
  .dt-head { overflow-x: auto; -webkit-overflow-scrolling: touch; gap: 8px; padding: 8px 4px; }
  .dt-head .dt-tabs, .dt-head .dt-actions { flex-shrink: 0; }
  .dt-head .dt-tab { padding: 6px 10px; font-size: 13px; }
  .dt-head .dt-actions { gap: 8px; font-size: 12px; }
  .dt-head .add-data-btn { min-height: 36px; }

  .remark { margin-top: 10px; }
  .audit-line { gap: 6px 12px; font-size: 12px; line-height: 1.6; }
}
</style>
