<!-- DetailMaintainDialog.vue - 明细维护弹窗（通用，配置驱动）：双击主表行打开，维护该单据的明细（新增/删除/保存）。
  用法：<DetailMaintainDialog v-model="visible" :panel-code="panelCode" :row="row" @saved="reload" /> -->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="title"
    width="1180px"
    append-to-body
    @update:model-value="close"
  >
    <div v-if="info" class="mm-head">
      <span class="mm-tag">{{ info[headCodeField] || '' }}</span>
      <span class="mm-name">{{ info[headNameField] || '' }}</span>
      <span class="mm-status" :class="info['单据状态']">{{ info['单据状态'] || '' }}</span>
      <span v-if="!editable" class="mm-tip">已审核/生效单据需弃审后维护</span>
    </div>
    <div class="mm-toolbar">
      <el-button size="small" type="primary" :disabled="!editable" @click="addRow">新增数据</el-button>
      <el-button size="small" :disabled="!editable || !selRows.length" @click="delRows">删除选中</el-button>
      <span class="mm-count">共 {{ rows.length }} 行</span>
    </div>
    <el-table :data="rows" size="small" border height="380" @selection-change="(r) => (selRows = r)">
      <el-table-column type="selection" width="40" :selectable="() => editable" />
      <el-table-column type="index" label="序号" width="55" align="center" :index="(i) => i + 1" />
      <el-table-column v-for="f in fields" :key="f.dataName" :label="f.dataName" min-width="110">
        <template #default="{ row }">
          <el-select
            v-if="f.dataType === '参照'"
            v-model="row[f.dataName]"
            filterable
            size="small"
            :disabled="!editable"
            style="width: 100%"
            placeholder="选择"
            @change="onRefChange(f, row)"
          >
            <el-option v-for="o in refOptions(f)" :key="o[refValOf(f)]" :label="o[refValOf(f)] + ' ' + (o[refDispOf(f)] || '')" :value="o[refValOf(f)]" />
          </el-select>
          <el-select
            v-else-if="f.dataType === '下拉框'"
            v-model="row[f.dataName]"
            filterable
            allow-create
            size="small"
            :disabled="!editable"
            style="width: 100%"
          >
            <el-option v-for="o in f.options || []" :key="o.label ?? o" :label="o.label ?? o" :value="o.value ?? o" />
          </el-select>
          <el-switch v-else-if="f.dataType === '是否'" v-model="row[f.dataName]" :disabled="!editable" />
          <el-input-number
            v-else-if="f.dataType === '小数' || f.dataType === '整数'"
            v-model="row[f.dataName]"
            :controls="false"
            size="small"
            :disabled="!editable"
            style="width: 100%"
          />
          <el-date-picker
            v-else-if="f.dataType === '日期'"
            v-model="row[f.dataName]"
            type="date"
            value-format="YYYY-MM-DD"
            size="small"
            :disabled="!editable"
            style="width: 100%"
          />
          <el-input v-else v-model="row[f.dataName]" size="small" :disabled="!editable" />
        </template>
      </el-table-column>
    </el-table>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :disabled="!editable || saving" @click="save">{{ saving ? '保存中…' : '保存' }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { usePanelRuntime } from '@core/panel-runtime'

const engine = usePanelRuntime()

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  panelCode: { type: String, default: '' },
  row: { type: Object, default: null }, // 当前单据行（含 编号/单据状态 + detail）
})
const emit = defineEmits(['update:modelValue', 'saved'])

const info = ref(null) // 表头 data（getFormDescriptor.data）
const detailDef = ref(null) // detail 定义（tabs）
const rows = ref([])
const selRows = ref([])
const saving = ref(false)
const refCache = ref({}) // refPanel -> 选项行数组

const tab = computed(() => (detailDef.value?.tabs || [])[0] || null)
const fields = computed(() => (tab.value?.fields || []).filter((f) => !f.hidden))
const headCodeField = computed(() => (tab.value?.fields || []).some((f) => f.dataName === '工艺路线编码') ? '工艺路线编码' : '编号')
const headNameField = computed(() => (tab.value?.fields || []).some((f) => f.dataName === '工艺路线名称') ? '工艺路线名称' : '编号')
const editable = computed(() => !info.value || info.value['单据状态'] === '草稿' || info.value['单据状态'] === '启用' || info.value['单据状态'] === '停用')
const title = computed(() => {
  const no = props.row ? props.row['编号'] : ''
  return '明细维护' + (no ? ' - ' + no : '')
})

watch(
  () => [props.modelValue, props.row],
  () => {
    if (props.modelValue && props.row) load()
  },
  { immediate: true }
)

async function load() {
  const no = props.row?.['编号']
  if (!no) return
  info.value = null
  detailDef.value = null
  rows.value = []
  refCache.value = {}
  try {
    const res = await engine.getFormDescriptor({ panelCode: props.panelCode, code: no })
    info.value = res.data || {}
    detailDef.value = res.detail || null
    const dd = res.detailData || {}
    const key = tab.value?.key
    rows.value = (key && Array.isArray(dd[key]) ? dd[key] : []).map((r) => ({ ...r }))
    for (const f of fields.value) if (f.dataType === '参照') await ensureRef(f)
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '明细加载失败')
  }
}

// ---------- 参照 ----------
function refPanelOf(f) {
  return f.refPanel || (f.ref && f.ref.panel) || ''
}
function refValOf(f) {
  return f.refField || (f.ref && f.ref.field) || f.dataName
}
function refDispOf(f) {
  return f.displayField || (f.ref && f.ref.display) || f.dataName
}
async function ensureRef(f) {
  const panel = refPanelOf(f)
  if (!panel || refCache.value[panel]) return
  try {
    refCache.value[panel] = await engine.queryRefRows(f, { keyword: '' })
  } catch {
    refCache.value[panel] = []
  }
}
function refOptions(f) {
  return refCache.value[refPanelOf(f)] || []
}
function onRefChange(f, row) {
  const panel = refPanelOf(f)
  const val = row[f.dataName]
  if (!panel || val === undefined || val === null || val === '') return
  const target = (refCache.value[panel] || []).find((r) => r[refValOf(f)] === val)
  if (!target) return
  const maps = f.refMap || (f.ref && f.ref.map) || []
  for (const m of maps) {
    if (!m) continue
    if (target[m.from] !== undefined && m.to !== f.dataName) row[m.to] = target[m.from]
  }
}

// ---------- 行维护 ----------
function addRow() {
  const base = {}
  for (const f of fields.value) {
    if (f.dataType === '是否') base[f.dataName] = false
    else if (f.dataType === '小数') base[f.dataName] = 0
    else if (f.dataType === '整数') base[f.dataName] = rows.value.length + 1
    else if (f.dataType === '下拉框') base[f.dataName] = f.defaultValue ?? (f.options?.[0]?.value ?? f.options?.[0] ?? '')
    else base[f.dataName] = f.defaultValue ?? ''
  }
  if (fields.value.some((f) => f.dataName === '加工顺序')) base['加工顺序'] = rows.value.length + 1
  rows.value.push(base)
}
function delRows() {
  const keep = new Set(selRows.value)
  rows.value = rows.value.filter((r) => !keep.has(r))
  selRows.value = []
}

// ---------- 保存 ----------
async function save() {
  if (!tab.value) return
  saving.value = true
  try {
    const detail = { [tab.value.key]: rows.value.map((r) => ({ ...r })) }
    await engine.callButton({
      panelCode: props.panelCode,
      buttonName: '保存',
      formData: { ...info.value, detail },
      buttonParam: { code: info.value['编号'] },
    })
    ElMessage.success('保存成功')
    emit('saved')
    close()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '保存失败')
  } finally {
    saving.value = false
  }
}

function close() {
  emit('update:modelValue', false)
}
</script>

<style scoped>
.mm-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
  font-size: 13px;
}
.mm-tag {
  font-weight: 700;
  color: #1c4f8a;
}
.mm-name {
  color: #333;
}
.mm-status {
  padding: 1px 8px;
  border-radius: 3px;
  font-size: 12px;
}
.mm-status.草稿 { background: #fff3e0; color: #e65100; }
.mm-status.已审核 { background: #e8f5e9; color: #2e7d32; }
.mm-status.启用 { background: #e8f5e9; color: #2e7d32; }
.mm-status.生效 { background: #e8f5e9; color: #2e7d32; }
.mm-status.已中止 { background: #ffebee; color: #c62828; }
.mm-tip {
  color: #c62828;
  font-size: 12px;
}
.mm-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.mm-count {
  font-size: 12px;
  color: #888;
  margin-left: 4px;
}
</style>
