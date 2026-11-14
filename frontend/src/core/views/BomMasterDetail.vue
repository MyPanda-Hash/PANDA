<!-- BOM 父件/子件主从视图：BOM 草稿可维护，已审核 BOM 与正反向查询只读。 -->
<template>
  <div class="bom-md">
    <div class="bom-md-sec">
      <div class="bom-md-head">
        <div>
          <span class="bom-md-title">{{ reverse ? tt('子件（物料/原材料）') : tt('父件（产成品/物料）') }}</span>
          <span class="bom-md-count">{{ tt('共') }} {{ masters.length }} {{ tt('项') }}</span>
        </div>
      </div>
      <el-table
        :data="masters"
        border
        size="small"
        :height="masterTableHeight"
        highlight-current-row
        :row-class-name="masterRowCls"
        v-loading="loading"
        @row-click="onMasterClick"
      >
        <el-table-column type="index" :label="tt('序号')" width="60" align="center" :index="(index) => index + 1" />
        <el-table-column v-if="pager" prop="_docNo" :label="tt('单据编号')" min-width="140" show-overflow-tooltip />
        <template v-if="!reverse">
          <el-table-column prop="父件编码" :label="tt('父件编码')" min-width="130">
            <template #default="{ row }">
              <button v-if="editable" type="button" class="bom-ref" @click.stop="openRef('父件编码', row)">
                {{ row['父件编码'] || tt('请选择') }}
                <el-icon><Search /></el-icon>
              </button>
              <span v-else>{{ row['父件编码'] || '' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="父件名称" :label="tt('父件名称')" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">
              <el-input v-if="editable" v-model="row['父件名称']" @change="syncParent" />
              <span v-else>{{ row['父件名称'] || '' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="版本号" :label="tt('版本号')" width="110">
            <template #default="{ row }">
              <el-input v-if="editable" v-model="row['版本号']" @change="syncParent" />
              <span v-else>{{ row['版本号'] || '' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="默认BOM" :label="tt('默认BOM')" width="100" align="center">
            <template #default="{ row }">
              <el-switch v-if="editable" v-model="row['默认BOM']" @change="syncParent" />
              <span v-else>{{ fmtBool(row['默认BOM']) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="计量单位" :label="tt('计量单位')" width="120">
            <template #default="{ row }">
              <el-select v-if="editable" v-model="row['计量单位']" filterable allow-create @change="syncParent">
                <el-option v-for="option in optionsOf('计量单位')" :key="option" :label="tt(option)" :value="option" />
              </el-select>
              <span v-else>{{ row['计量单位'] || '' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="生产数量" :label="tt('生产数量')" width="120" align="right">
            <template #default="{ row }">
              <el-input-number v-if="editable" v-model="row['生产数量']" :controls="false" @change="syncParent" />
              <span v-else>{{ row['生产数量'] ?? '' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="生产车间" :label="tt('生产车间')" min-width="140">
            <template #default="{ row }">
              <el-select v-if="editable" v-model="row['生产车间']" filterable allow-create @change="syncParent">
                <el-option v-for="option in optionsOf('生产车间')" :key="option" :label="tt(option)" :value="option" />
              </el-select>
              <span v-else>{{ row['生产车间'] || '' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="虚拟件" :label="tt('虚拟件')" width="90" align="center">
            <template #default="{ row }">
              <el-switch v-if="editable" v-model="row['虚拟件']" @change="syncParent" />
              <span v-else>{{ fmtBool(row['虚拟件']) }}</span>
            </template>
          </el-table-column>
        </template>
        <template v-else>
          <el-table-column prop="子件编码" :label="tt('子件编码')" min-width="110" show-overflow-tooltip />
          <el-table-column prop="子件名称" :label="tt('子件名称')" min-width="150" show-overflow-tooltip />
          <el-table-column prop="规格型号" :label="tt('规格型号')" min-width="120" show-overflow-tooltip />
          <el-table-column prop="子件计量单位" :label="tt('单位')" width="90" />
        </template>
      </el-table>
      <!-- 父件表分页：共 N 项 / 每页 10·20·50 / 第 a/b 页，替代「第 N 张单据」逐张翻 -->
      <div v-if="pager" class="bom-md-pager">
        <span class="bom-md-count">{{ tt('共') }} {{ total }} {{ tt('项') }}</span>
        <span class="bom-md-per">
          <span>{{ tt('每页') }}</span>
          <el-select
            :model-value="normalizedPageSize"
            size="small"
            class="bom-md-size"
            @change="(value) => emit('page-size-change', value)"
          >
            <el-option v-for="option in ROWS_PER_PAGE_OPTIONS" :key="option" :label="String(option)" :value="option" />
          </el-select>
          <span>{{ tt('项') }}</span>
        </span>
        <template v-if="masterPageCount > 1">
          <span class="page-btn" :title="tt('上一页')" @click="emit('page-change', pageNo - 1)">◀</span>
          <span class="page-no">{{ tt('第') }} {{ pageNo }}/{{ masterPageCount }} {{ tt('页') }}</span>
          <span class="page-btn" :title="tt('下一页')" @click="emit('page-change', pageNo + 1)">▶</span>
        </template>
      </div>
    </div>

    <div class="bom-md-sec">
      <div class="bom-md-head">
        <div>
          <span class="bom-md-title">{{ reverse ? tt('父件（该子件被用于）') : tt('子件') }}：{{ curMasterLabel }}</span>
          <span class="bom-md-count">{{ tt('共') }} {{ curRows.length }} {{ tt('项') }}</span>
        </div>
        <el-button v-if="editable" type="primary" size="small" :icon="Plus" @click="addChild">{{ tt('新增子件') }}</el-button>
      </div>
      <el-table :data="curRows" :row-key="childRowKey" border size="small" height="260">
        <el-table-column type="index" :label="tt('序号')" width="60" align="center" :index="(index) => index + 1" />
        <template v-if="!reverse">
          <el-table-column prop="子件编码" :label="tt('子件编码')" min-width="130">
            <template #default="{ row }">
              <button v-if="editable" type="button" class="bom-ref" @click.stop="openRef('子件编码', row)">
                {{ row['子件编码'] || tt('请选择') }}
                <el-icon><Search /></el-icon>
              </button>
              <span v-else>{{ row['子件编码'] || '' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="子件名称" :label="tt('子件名称')" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">
              <el-input v-if="editable" v-model="row['子件名称']" @change="emitRows" />
              <span v-else>{{ row['子件名称'] || '' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="规格型号" :label="tt('规格型号')" min-width="130">
            <template #default="{ row }">
              <el-input v-if="editable" v-model="row['规格型号']" @change="emitRows" />
              <span v-else>{{ row['规格型号'] || '' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="子件计量单位" :label="tt('单位')" width="110">
            <template #default="{ row }">
              <el-select v-if="editable" v-model="row['子件计量单位']" filterable allow-create @change="emitRows">
                <el-option v-for="option in optionsOf('子件计量单位')" :key="option" :label="tt(option)" :value="option" />
              </el-select>
              <span v-else>{{ row['子件计量单位'] || '' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="定额数量" :label="tt('定额数量')" width="120" align="right">
            <template #default="{ row }">
              <el-input-number v-if="editable" v-model="row['定额数量']" :controls="false" @change="emitRows" />
              <span v-else>{{ row['定额数量'] ?? '' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="损耗率%" :label="tt('损耗率%')" width="110" align="right">
            <template #default="{ row }">
              <el-input-number v-if="editable" v-model="row['损耗率%']" :controls="false" @change="emitRows" />
              <span v-else>{{ row['损耗率%'] ?? '' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="需用数量" :label="tt('需用数量')" width="120" align="right">
            <template #default="{ row }">
              <el-input-number v-if="editable" v-model="row['需用数量']" :controls="false" @change="emitRows" />
              <span v-else>{{ row['需用数量'] ?? '' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="备注" :label="tt('备注')" min-width="150">
            <template #default="{ row }">
              <el-input v-if="editable" v-model="row['备注']" @change="emitRows" />
              <span v-else>{{ row['备注'] || '' }}</span>
            </template>
          </el-table-column>
          <el-table-column v-if="editable" :label="tt('操作')" width="64" fixed="right" align="center">
            <template #default="{ row }">
              <el-button link type="danger" :icon="Delete" :title="tt('删除子件')" @click="removeChild(row)" />
            </template>
          </el-table-column>
        </template>
        <template v-else>
          <el-table-column prop="父件编码" :label="tt('父件编码')" min-width="110" show-overflow-tooltip />
          <el-table-column prop="父件名称" :label="tt('父件名称')" min-width="150" show-overflow-tooltip />
          <el-table-column prop="版本号" :label="tt('版本号')" width="100" />
          <el-table-column prop="默认BOM" :label="tt('默认BOM')" width="90" align="center">
            <template #default="{ row }">{{ fmtBool(row['默认BOM']) }}</template>
          </el-table-column>
          <el-table-column prop="计量单位" :label="tt('计量单位')" width="90" />
          <el-table-column prop="生产数量" :label="tt('生产数量')" width="90" align="right" />
        </template>
      </el-table>
      <div v-if="!curRows.length && !editable" class="bom-md-empty">{{ tt('请选择上方') }} {{ reverse ? tt('子件') : tt('父件') }} {{ tt('查看对应明细') }}</div>
    </div>

    <RefPickDialog v-model="refVisible" :field="refPick?.field" mode="header" @confirm="onRefConfirm" />
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Delete, Plus, Search } from '@element-plus/icons-vue'
import { tt } from '@/i18n'
import { ROWS_PER_PAGE_OPTIONS, gridHeight, normalizePageSize, pageCount } from './list-grid-paging.js'
import RefPickDialog from './RefPickDialog.vue'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  fields: { type: Array, default: () => [] },
  documentNo: { type: String, default: '' },
  reverse: { type: Boolean, default: false },
  editable: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  // 父件表分页：只读浏览时把所有单据的父件放进同一张表，按 10/20/50 分页（服务端分页）
  pager: { type: Boolean, default: false },
  total: { type: Number, default: 0 },
  pageNo: { type: Number, default: 1 },
  pageSize: { type: Number, default: 10 },
})
const emit = defineEmits(['update:rows', 'page-change', 'page-size-change', 'select-master'])

const PARENT_KEYS = [
  '物料清单编码', '父件编码', '父件名称', '版本号', '默认BOM',
  '计量单位', '生产数量', '生产车间', '虚拟件', '预入仓库',
]
const masterKey = computed(() => (props.reverse ? '子件编码' : '父件编码'))
const localRows = ref([])
const parentDraft = reactive({})
const activeId = ref('')
const syncing = ref(false)
const rowIds = new WeakMap()
let rowSeq = 0

function fieldOf(name) {
  return props.fields.find((field) => field.dataName === name) || { dataName: name, dataType: '文本' }
}

function defaultValue(name) {
  const field = fieldOf(name)
  if (field.defaultValue !== undefined) return field.defaultValue
  if (field.dataType === '是否') return false
  if (field.dataType === '小数' || field.dataType === '整数') return 0
  return ''
}

function optionsOf(name) {
  return fieldOf(name).options || []
}

function resetFromProps() {
  syncing.value = true
  localRows.value = (props.rows || []).map((row) => ({ ...row }))
  for (const key of Object.keys(parentDraft)) delete parentDraft[key]
  const first = localRows.value[0] || {}
  for (const key of PARENT_KEYS) parentDraft[key] = first[key] ?? defaultValue(key)
  parentDraft['物料清单编码'] = first['物料清单编码'] || props.documentNo || ''
  if (!localRows.value.length && !props.reverse) activeId.value = parentDraft['父件编码'] || '__draft__'
  syncing.value = false
}

watch(
  () => [props.rows, props.documentNo, props.editable],
  resetFromProps,
  { immediate: true }
)

// 父件行的唯一标识：只读浏览时同一父件可能出现在多张 BOM 单里，用「单据#父件」区分
// （否则两张 BOM 都挂 CP001 时，子件会把两单的行混在一起显示）
function rowIdentity(row) {
  const key = row ? row[masterKey.value] : ''
  const docNo = (row && row._docNo) || ''
  return docNo ? `${docNo}#${key}` : key
}

const masters = computed(() => {
  if (props.editable && !props.reverse) return [parentDraft]
  const seen = new Map()
  for (const row of localRows.value) {
    const id = rowIdentity(row)
    if (!id || seen.has(id)) continue
    seen.set(id, row)
  }
  return [...seen.values()]
})

const normalizedPageSize = computed(() => normalizePageSize(props.pageSize))
const masterPageCount = computed(() => pageCount(props.total, normalizedPageSize.value))
// 高度按「本页实际项数」算：不足一页时不留一大片空白，最少给 3 行；编辑单张单据保持原来的 230px
const masterTableHeight = computed(() => {
  if (!props.pager) return 230
  const rows = Math.max(3, Math.min(normalizedPageSize.value, masters.value.length || normalizedPageSize.value))
  return gridHeight(rows)
})

watch(
  masters,
  (list) => {
    if (!list.length) {
      activeId.value = ''
      return
    }
    if (props.editable && !props.reverse) {
      activeId.value = parentDraft['父件编码'] || '__draft__'
      return
    }
    if (!list.some((master) => rowIdentity(master) === activeId.value)) {
      activeId.value = rowIdentity(list[0])
    }
  },
  { immediate: true }
)

const curMaster = computed(() => {
  if (props.editable && !props.reverse) return parentDraft
  return masters.value.find((master) => rowIdentity(master) === activeId.value) || null
})
const curMasterLabel = computed(() => {
  const master = curMaster.value
  if (!master) return '-'
  const label = props.reverse
    ? `${master['子件编码'] || ''} ${master['子件名称'] || ''}`
    : `${master['父件编码'] || ''} ${master['父件名称'] || ''}`
  return label.trim() || tt('待选择父件')
})
const curRows = computed(() => {
  if (props.editable && !props.reverse) return localRows.value
  if (!activeId.value) return []
  // 同一父件出现在多张单据时，只显示当前选中那一张的子件
  return localRows.value.filter((row) => rowIdentity(row) === activeId.value)
})

function emitRows() {
  if (!props.editable || syncing.value) return
  emit('update:rows', localRows.value.map((row) => ({ ...row })))
}

function syncParent() {
  if (!props.editable) return
  parentDraft['物料清单编码'] = parentDraft['物料清单编码'] || props.documentNo || ''
  activeId.value = parentDraft['父件编码'] || '__draft__'
  for (const row of localRows.value) {
    for (const key of PARENT_KEYS) row[key] = parentDraft[key]
  }
  // 尚无子件时父件只能暂存在主表；新增首个子件时再写入 children，避免空数组回灌清空父件。
  if (localRows.value.length) emitRows()
}

function newChildRow() {
  const row = {}
  for (const field of props.fields) row[field.dataName] = defaultValue(field.dataName)
  for (const key of PARENT_KEYS) row[key] = parentDraft[key]
  row['物料清单编码'] = parentDraft['物料清单编码'] || props.documentNo || ''
  return row
}

function addChild() {
  if (!props.editable) return
  localRows.value.push(newChildRow())
  emitRows()
}

function removeChild(row) {
  if (!props.editable) return
  const index = localRows.value.indexOf(row)
  if (index >= 0) localRows.value.splice(index, 1)
  emitRows()
}

function childRowKey(row) {
  if (!rowIds.has(row)) rowIds.set(row, `bom-child-${++rowSeq}`)
  return rowIds.get(row)
}

const refVisible = ref(false)
const refPick = ref(null)

function openRef(fieldName, target) {
  if (!props.editable) return
  refPick.value = { field: fieldOf(fieldName), target, parent: fieldName === '父件编码' }
  refVisible.value = true
}

function applyReference(target, field, source) {
  target[field.dataName] = source[field.refField || field.field || field.dataName] ?? ''
  for (const map of field.refMap || field.map || []) {
    if (map && source[map.from] !== undefined) target[map.to || map.from] = source[map.from]
  }
}

function onRefConfirm(rows) {
  const source = rows?.[0]
  const pick = refPick.value
  if (!source || !pick || !props.editable) return
  applyReference(pick.target, pick.field, source)
  if (pick.parent) syncParent()
  else emitRows()
  refVisible.value = false
  refPick.value = null
}

function validate() {
  if (!props.editable || props.reverse) return ''
  if (!String(parentDraft['父件编码'] || '').trim()) return tt('父件编码不能为空')
  if (!String(parentDraft['父件名称'] || '').trim()) return tt('父件名称不能为空')
  if (!localRows.value.length) return tt('请至少添加一行子件')
  for (let index = 0; index < localRows.value.length; index++) {
    const row = localRows.value[index]
    if (!String(row['子件编码'] || '').trim()) return tt('子件第') + ' ' + (index + 1) + ' ' + tt('行子件编码不能为空')
    if (row['子件编码'] === parentDraft['父件编码']) return tt('子件第') + ' ' + (index + 1) + ' ' + tt('行不能与父件相同')
  }
  syncParent()
  return ''
}

function onMasterClick(row) {
  activeId.value = props.editable && !props.reverse ? (row['父件编码'] || '__draft__') : rowIdentity(row)
  // 只读浏览时点父件要同步当前单据，表头的「单据：xxx」才跟手上这行一致
  if (!props.editable && row._docNo) emit('select-master', row)
}

function masterRowCls({ row }) {
  const id = props.editable && !props.reverse ? (row['父件编码'] || '__draft__') : rowIdentity(row)
  return id === activeId.value ? 'row-cur' : ''
}

function fmtBool(value) {
  return value === true || value === 'true' || value === 1 || value === '1' ? tt('是') : ''
}

defineExpose({ validate })
</script>

<style scoped>
.bom-md {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px 12px 12px;
  min-height: 100%;
  box-sizing: border-box;
}
.bom-md-sec {
  border: 1px solid var(--t-border, var(--d-border));
  border-radius: 6px;
  overflow: hidden;
  background: #fff;
}
.bom-md-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 34px;
  padding: 5px 12px;
  background: var(--d-fill-soft);
  border-bottom: 1px solid var(--t-border, var(--d-border));
}
.bom-md-title {
  font-size: 13px;
  font-weight: 600;
  color: #333;
}
.bom-md-count {
  margin-left: 12px;
  font-size: 12px;
  color: var(--d-text-3);
}
/* 父件表分页条：贴表格下方，与明细区翻页条同一套视觉 */
.bom-md-pager {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 3px 12px;
  border-top: 1px solid var(--d-border);
  background: var(--d-fill-soft);
  font-size: 12px;
  color: #555;
  white-space: nowrap;
}
.bom-md-per {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--d-text-3);
  white-space: nowrap;
}
.bom-md-size {
  width: 74px;
  flex: 0 0 auto;
}
.bom-md-pager .page-btn {
  width: 24px;
  height: 24px;
  line-height: 22px;
  text-align: center;
  border: 1px solid var(--d-border-strong);
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
  user-select: none;
  font-size: 12px;
  color: #333;
}
.bom-md-pager .page-btn:hover {
  border-color: var(--t-primary, var(--d-primary));
  color: var(--t-primary, var(--d-primary));
}
.bom-md-pager .page-no {
  padding: 0 2px;
  color: #555;
}
.bom-md-empty {
  padding: 14px;
  font-size: 12px;
  color: var(--d-text-3);
  text-align: center;
}
.bom-ref {
  width: 100%;
  min-height: 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 0 8px;
  color: var(--d-text);
  background: #fff;
  border: 1px solid var(--d-border-strong);
  border-radius: 4px;
  cursor: pointer;
}
.bom-ref:hover {
  border-color: var(--el-color-primary);
}
:deep(.row-cur td) {
  background: var(--d-primary-bg) !important;
}
:deep(.el-input-number),
:deep(.el-select) {
  width: 100%;
}
</style>