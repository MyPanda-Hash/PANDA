<template>
  <el-dialog
    :model-value="visible"
    :title="'新增' + panelName"
    width="960px"
    append-to-body
    destroy-on-close
    @update:model-value="(v) => emit('update:visible', v)"
    @open="onOpen"
  >
    <div v-loading="loading" class="nvd">
      <!-- 表头字段（fieldCols 列 × N 行，默认 2 列） -->
      <div class="fields udl-fields" :style="{ gridTemplateColumns: 'repeat(' + fieldCols + ', 1fr)' }">
        <div v-for="r in visibleMeta" :key="r.code" class="field">
          <label :title="r.name">{{ r.name }}<span v-if="r.isNotNull" class="req">*</span></label>
          <el-input v-if="isText(r)" v-model="form[r.code]" :disabled="fieldLocked(r)" :placeholder="r.name" />
          <el-input-number v-else-if="isNumber(r)" v-model="form[r.code]" :disabled="fieldLocked(r)" :controls="false" style="width: 100%" />
          <!-- 参照字段：点击弹窗拉取基础档案面板数据，勾选导入（开发约束十一-1） -->
          <div v-else-if="isRef(r)" class="ref-ctl">
            <el-input :model-value="refText(r, form[r.code])" readonly :disabled="fieldLocked(r)" placeholder="点击选择" @click="openRefPick(r)" />
            <el-button v-if="!fieldLocked(r)" size="small" :icon="Search" class="ref-btn" @click="openRefPick(r)" />
          </div>
          <el-select v-else-if="isSelect(r)" v-model="form[r.code]" :disabled="fieldLocked(r)" filterable clearable allow-create style="width: 100%">
            <el-option v-for="o in r.options || []" :key="o" :label="o.label ?? o" :value="o.value ?? o" />
          </el-select>
          <el-date-picker
            v-else-if="isDate(r)"
            v-model="form[r.code]"
            :disabled="fieldLocked(r)"
            type="date"
            value-format="YYYY-MM-DD"
            style="width: 100%"
          />
          <el-switch v-else-if="isBool(r)" v-model="form[r.code]" :disabled="fieldLocked(r)" />
          <el-input v-else v-model="form[r.code]" :disabled="fieldLocked(r)" :placeholder="r.name" />
        </div>
      </div>

      <!-- 明细（若有） -->
      <div v-if="tabs.length" class="detail">
        <el-tabs v-model="activeTab">
          <el-tab-pane v-for="tab in tabs" :key="tab.key" :name="tab.key">
            <template #label>
              <span>{{ tab.label }}<span v-if="tab.isRequired" class="req">*</span></span>
            </template>
            <div class="tab-toolbar">
              <el-button size="small" type="primary" :icon="Plus" @click="addDetailRow(tab)">新增数据</el-button>
            </div>
            <el-table :data="detailData[tab.key] || []" size="small" border height="260">
              <el-table-column label="序号" width="50" align="center">
                <template #default="{ $index }">{{ $index + 1 }}</template>
              </el-table-column>
              <el-table-column
                v-for="dr in visibleFields(tab)"
                :key="dr.dataName"
                :label="dr.dataName"
                min-width="110"
                :class-name="[dr.computed ? 'computed-col' : '', dr.dataType === '参照' ? 'detail-ref-col' : ''].filter(Boolean).join(' ')"
              >
                <template #default="{ row }">
                  <button
                    v-if="dr.dataType === '参照'"
                    type="button"
                    class="detail-ref-cell"
                    :disabled="dr.computed"
                    :title="drRefText(dr, row) || undefined"
                    @click.stop="openDetailRef(dr, row, tab)"
                  >{{ drRefText(dr, row) || refPlaceholder(dr) }}</button>
                  <el-select v-else-if="dr.dataType === '下拉框'" v-model="row[dr.dataName]" :disabled="dr.computed" filterable allow-create style="width: 100%">
                    <el-option v-for="o in dr.options || []" :key="o" :label="o.label ?? o" :value="o.value ?? o" />
                  </el-select>
                  <el-switch v-else-if="dr.dataType === '是否'" v-model="row[dr.dataName]" :disabled="dr.computed" />
                  <el-image v-else-if="dr.dataType === '图片'" :src="row[dr.dataName] || ''" fit="contain" style="width: 34px; height: 34px">
                    <template #error><span class="img-ph">图</span></template>
                  </el-image>
                  <el-input-number
                    v-else-if="dr.dataType === '小数' || dr.dataType === '整数'"
                    v-model="row[dr.dataName]"
                    :disabled="dr.computed"
                    :controls="false"
                    style="width: 100%"
                  />
                  <el-date-picker
                    v-else-if="dr.dataType === '日期'"
                    v-model="row[dr.dataName]"
                    :disabled="dr.computed"
                    type="date"
                    value-format="YYYY-MM-DD"
                    style="width: 100%"
                  />
                  <el-input v-else v-model="row[dr.dataName]" :disabled="dr.computed" />
                </template>
              </el-table-column>
              <el-table-column label="操作" width="50" align="center">
                <template #default="{ $index }">
                  <el-icon class="del" @click="detailData[tab.key].splice($index, 1)"><Delete /></el-icon>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </div>

      <div class="hint">提示：也可在列表页底部空白行双击单元格直接填写（内联新增）</div>
    </div>

    <template #footer>
      <el-button @click="emit('update:visible', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
    </template>
  </el-dialog>
  <RefPickDialog v-model="refVisible" :field="refPick?.field" :mode="refPick?.kind || 'header'" @confirm="onRefConfirm" />
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Delete, Search } from '@element-plus/icons-vue'
import * as engine from '@/business/engine'
import RefPickDialog from './RefPickDialog.vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  panelCode: { type: String, default: '' },
  panelName: { type: String, default: '单据' },
  // 表头字段列数（2列×N行；FINISH_IN 等单据按需传 2）
  fieldCols: { type: Number, default: 2 },
})
const emit = defineEmits(['update:visible', 'saved'])

const form = reactive({})
const meta = ref([])
const detailDef = ref(null)
const detailData = reactive({})
const activeTab = ref('')
const loading = ref(false)
const saving = ref(false)

const visibleMeta = computed(() => (meta.value || []).filter((r) => !r.hidden))
const tabs = computed(() => {
  const d = detailDef.value
  if (!d) return []
  if (Array.isArray(d.tabs)) return d.tabs
  return []
})

function isText(r) { return !r.dataType || r.dataType === '文本' || r.dataType === 'STRING' }
function isNumber(r) { return ['小数', '整数', 'Decimal', 'Long', 'Integer', 'Double'].includes(r.dataType) }
function isDate(r) { return ['日期', '时间', 'DATE', 'DateTime', 'Date'].includes(r.dataType) }
function isBool(r) { return ['是否', 'Boolean', 'BOOL'].includes(r.dataType) }
function isSelect(r) { return r.dataType === '下拉框' || r.dataType === '参照' }

// ---------- 参照字段弹窗选择（开发约束十一-1：能对应基础档案的字段弹窗拉取勾选导入） ----------
const refVisible = ref(false)
const refPick = ref(null)

function isRef(r) {
  return r.dataType === '参照' && (r.ref || r.refPanel)
}

function fieldLocked(r) {
  return !!(r.autoCode || r.computed)
}

function refText(r, v) {
  if (v === undefined || v === null || v === '') return ''
  const text = engine.refLabelOf(r, v)
  return text === null || text === undefined ? String(v) : text
}

function refPlaceholder(r) {
  const ref = r.ref || r
  return `选择${ref.display || ref.displayField || r.dataName || ''}`
}

function openRefPick(r) {
  if (fieldLocked(r)) return
  refPick.value = { field: r, kind: 'header', code: r.code }
  refVisible.value = true
}

function drRefText(dr, row) {
  return refText(dr, row[dr.dataName])
}

function openDetailRef(dr, row, tab) {
  if (dr.computed) return
  refPick.value = { field: dr, kind: 'detail', row, tab }
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
  const maps = rp.map || rp.refMap || []
  const applyMap = (target, srcRow) => {
    for (const m of maps) {
      if (!m || srcRow[m.from] === undefined) continue
      const to = m.to || m.from
      if (to !== p.code && to !== r.dataName) target[to] = srcRow[m.from]
    }
  }
  if (p.kind === 'detail') {
    const row = p.row
    const tab = p.tab
    row[r.dataName] = vals[0]
    applyMap(row, rows[0] || {})
    // 多选批量：其余选中行直接新增明细行
    if (multi && rows.length > 1 && tab) {
      for (let i = 1; i < rows.length; i++) {
        const nr = addDetailRow(tab)
        nr[r.dataName] = rows[i][refField]
        applyMap(nr, rows[i])
      }
    }
  } else {
    form[p.code] = multi ? vals.join('、') : vals[0]
    applyMap(form, rows[0] || {})
  }
  applyCalc()
  ElMessage.success(`已导入 ${rows.length} 行${await engine.refPanelName(r)}数据`)
}
function visibleFields(tab) { return (tab.fields || []).filter((r) => !r.hidden) }

function num(v) { const n = Number(v); return Number.isFinite(n) ? n : 0 }

// 表达式计算链（与 PanelxForm 一致）
function evaluateExpr(expr, vars) {
  const tokens = String(expr).match(/\d+(?:\.\d+)?|[+\-*/()]|[^\s+\-*/()]+/g) || []
  const output = []
  const ops = []
  const prec = { '+': 1, '-': 1, '*': 2, '/': 2 }
  for (const tk of tokens) {
    if (/^[\d.]+$/.test(tk)) output.push(parseFloat(tk))
    else if (tk in prec) {
      while (ops.length && ops[ops.length - 1] !== '(' && prec[ops[ops.length - 1]] >= prec[tk]) output.push(ops.pop())
      ops.push(tk)
    } else if (tk === '(') ops.push(tk)
    else if (tk === ')') {
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
      const b = stack.pop(); const a = stack.pop()
      if (a === undefined || b === undefined) return 0
      stack.push(t === '+' ? a + b : t === '-' ? a - b : t === '*' ? a * b : b === 0 ? 0 : a / b)
    }
  }
  return stack[0] ?? 0
}

function applyCalc() {
  for (const tab of tabs.value) {
    const rows = detailData[tab.key] || []
    const productQty = (detailData.products || []).reduce((s, r) => s + num(r['数量']), 0)
    for (const row of rows) {
      const vars = { ...row, 产品数量: productQty }
      for (const rule of tab.calc || []) {
        let v
        try { v = evaluateExpr(rule.formula, vars) } catch (e) { v = 0 }
        if (rule.round != null) v = Math.round(v * 10 ** rule.round) / 10 ** rule.round
        if (row[rule.target] !== v) row[rule.target] = v
      }
    }
  }
}

watch(detailData, applyCalc, { deep: true })

function addDetailRow(tab) {
  const rows = detailData[tab.key] || (detailData[tab.key] = [])
  const row = {}
  for (const dr of tab.fields || []) {
    if (dr.dataType === '小数' || dr.dataType === '整数') row[dr.dataName] = dr.defaultValue ?? 0
    else if (dr.dataType === '是否') row[dr.dataName] = dr.defaultValue ?? false
    else if (dr.dataType === '日期') row[dr.dataName] = dr.defaultValue ?? ''
    else row[dr.dataName] = dr.defaultValue ?? ''
  }
  if (tab.subTable) row['子表材料'] = []
  rows.push(row)
  return row
}

async function onOpen() {
  if (!props.panelCode) return
  loading.value = true
  try {
    const p = await engine.getNewFormPermMatrix({ panelCode: props.panelCode, operationName: '新增流程' })
    Object.keys(form).forEach((k) => delete form[k])
    Object.assign(form, p.data || {})
    meta.value = p.meta || []
    detailDef.value = p.detail || null
    Object.keys(detailData).forEach((k) => delete detailData[k])
    if (p.detailData && typeof p.detailData === 'object') Object.assign(detailData, p.detailData)
    const firstTab = tabs.value[0]
    if (firstTab) activeTab.value = firstTab.key
    applyCalc()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '初始化失败')
  } finally {
    loading.value = false
  }
}

function emptyValue(v) {
  return v === undefined || v === null || String(v).trim() === ''
}

function validate() {
  for (const r of visibleMeta.value) {
    if (r.isNotNull && emptyValue(form[r.code])) return `${r.name}不能为空`
  }
  for (const tab of tabs.value) {
    const rows = detailData[tab.key] || []
    if (tab.isRequired && !rows.length) return `请至少添加一行${tab.label}`
    for (let i = 0; i < rows.length; i++) {
      for (const f of tab.fields || []) {
        if (f.isRequired && emptyValue(rows[i][f.dataName])) return `${tab.label}第 ${i + 1} 行${f.dataName}不能为空`
      }
    }
  }
  return ''
}

async function onSave() {
  const msg = validate()
  if (msg) return ElMessage.warning(msg)
  saving.value = true
  try {
    const rd = { ...form }
    if (tabs.value.length) rd.detail = { ...detailData }
    const res = await engine.callButton({ panelCode: props.panelCode, buttonName: '保存', formData: rd, buttonParam: {} })
    ElMessage.success(`保存成功：${res?.['编号'] || ''}`)
    emit('update:visible', false)
    emit('saved', res)
  } catch (e) {
    const m = engine.errMsg(e) || '保存失败'
    if (m.includes('演示环境暂未实现')) ElMessage.info(m)
    else ElMessage.error(m)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.nvd .fields {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px 18px;
  padding: 4px 0 10px;
}
.field {
  display: flex;
  align-items: center;
  gap: 8px;
}
.field label {
  width: 100px;
  text-align: right;
  font-size: 13px;
  color: var(--t-text-2);
  flex-shrink: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.field .req {
  color: #dc2626;
}
.detail {
  margin-top: 8px;
  border-top: 1px solid var(--t-border-light);
  padding-top: 8px;
}
.tab-toolbar {
  margin-bottom: 6px;
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
.del {
  cursor: pointer;
  color: #dc2626;
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
.hint {
  margin-top: 8px;
  font-size: 12px;
  color: var(--t-text-3);
}
:deep(.computed-col) {
  background: var(--t-content-bg);
}
</style>
