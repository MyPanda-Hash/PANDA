<template>
  <el-dialog
    v-model="visible"
    :title="tt('字段管理') + ' - ' + tt(panelName || panelCode)"
    width="720px"
    append-to-body
    :close-on-click-modal="false"
    @open="reset"
  >
    <div class="fm-tip">
      {{ tt('在这里给本面板增删字段，保存后列表、表单直接可用；字段值存在单据 JSON 里，不动表结构、不用改代码。') }}
      <span class="fm-tip-sub">{{ tt('停用字段只是不再显示，已录入的数据保留，重新加同名字段即恢复。') }}</span>
    </div>

    <div class="fm-bar">
      <span class="fm-scope-label">{{ tt('位置') }}</span>
      <el-select v-model="listScope" size="small" style="width: 180px" @change="reset">
        <el-option :label="tt('全部')" value="all" />
        <el-option :label="tt('表头')" value="header" />
        <el-option v-for="tab in tabs" :key="tab.key" :label="tt(tab.label || tab.key)" :value="tab.key" />
      </el-select>
      <span class="fm-count">
        {{ tt('共') }} {{ rows.length }} {{ tt('个字段') }} ·
        {{ tt('自定义') }} <b>{{ customCount }}</b>
      </span>
    </div>

    <el-table :data="rows" size="small" border max-height="260" class="fm-table">
      <el-table-column :label="tt('位置')" width="110">
        <template #default="{ row }">{{ row.scope === 'header' ? tt('表头') : tt(row.tabLabel || '明细') }}</template>
      </el-table-column>
      <el-table-column prop="dataName" :label="tt('字段名')" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">{{ tt(row.dataName) }}</template>
      </el-table-column>
      <el-table-column :label="tt('类型')" width="86">
        <template #default="{ row }">{{ tt(row.dataType || '文本') }}</template>
      </el-table-column>
      <el-table-column :label="tt('词表')" min-width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ optionText(row) }}</template>
      </el-table-column>
      <el-table-column :label="tt('必填')" width="62" align="center">
        <template #default="{ row }">{{ row.isRequired ? tt('是') : '' }}</template>
      </el-table-column>
      <el-table-column :label="tt('来源')" width="86" align="center">
        <template #default="{ row }">
          <span class="fm-src" :class="{ custom: row.custom }">{{ row.custom ? tt('自定义') : tt('内置') }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="tt('操作')" width="72" align="center">
        <template #default="{ row }">
          <el-button v-if="row.custom" link type="danger" size="small" @click="retire(row)">{{ tt('停用') }}</el-button>
        </template>
      </el-table-column>
      <template #empty>{{ tt('该位置还没有字段') }}</template>
    </el-table>

    <el-divider content-position="left">{{ tt('新增字段') }}</el-divider>
    <el-form :model="form" label-width="86px" size="small" @submit.prevent>
      <el-form-item :label="tt('位置')">
        <el-select v-model="form.scope" style="width: 200px">
          <el-option :label="tt('表头')" value="header" />
          <el-option v-for="tab in tabs" :key="tab.key" :label="tt(tab.label || tab.key)" :value="tab.key" />
        </el-select>
        <span class="fm-help">{{ tt('选明细页签=进明细表格；选表头=进单据表头') }}</span>
      </el-form-item>
      <el-form-item :label="tt('字段名')">
        <el-input v-model="form.dataName" maxlength="40" :placeholder="tt('中文，不能含空格与 . % ( ) /')" />
      </el-form-item>
      <el-form-item :label="tt('类型')">
        <el-select v-model="form.dataType" style="width: 160px">
          <el-option v-for="t in FIELD_TYPES" :key="t" :label="tt(t)" :value="t" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="form.dataType === '下拉框'" :label="tt('词表')">
        <el-input v-model="form.options" :placeholder="tt('逗号分隔，如：是,否,待定')" />
      </el-form-item>
      <el-form-item :label="tt('列宽')">
        <el-input-number v-model="form.fieldWidth" :min="60" :max="400" :controls="false" style="width: 120px" />
        <span class="fm-help">{{ tt('像素，留空按字段类型自动推算') }}</span>
      </el-form-item>
      <el-form-item :label="tt('必填')">
        <el-switch v-model="form.isRequired" />
        <span class="fm-help">{{ tt('必填只拦截保存，不改库结构') }}</span>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="visible = false">{{ tt('关闭') }}</el-button>
      <el-button type="primary" :loading="saving" :disabled="!canAdd" @click="add">{{ tt('添加字段') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePanelRuntime } from '@core/panel-runtime'
import { tt } from '@/i18n'

const engine = usePanelRuntime()

const FIELD_TYPES = ['文本', '下拉框', '日期', '是否', '小数', '整数']

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  panelCode: { type: String, required: true },
  panelName: { type: String, default: '' },
  /** 面板配置（含已合并的自定义字段，字段带 custom:true） */
  config: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'done'])

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const tabs = computed(() => (props.config?.detail?.tabs || []).map((tab) => ({ key: tab.key, label: tab.label || tab.key })))
const listScope = ref('all')
const saving = ref(false)
const form = reactive({ scope: '', dataName: '', dataType: '文本', options: '', fieldWidth: null, isRequired: false })

/** 字段清单：表头 + 各明细页签，位置/页签名一起带上（前端只展示，服务端是唯一裁判） */
const allRows = computed(() => {
  const out = []
  for (const field of props.config?.dataSchema?.fields || []) {
    if (!field || !field.dataName) continue
    out.push({ ...field, scope: 'header', tabKey: '', tabLabel: '' })
  }
  for (const tab of props.config?.detail?.tabs || []) {
    for (const field of tab.fields || []) {
      if (!field || !field.dataName) continue
      out.push({ ...field, scope: 'detail', tabKey: tab.key, tabLabel: tab.label || tab.key })
    }
  }
  return out
})

const rows = computed(() => {
  const scope = listScope.value
  if (!scope || scope === 'all') return allRows.value
  if (scope === 'header') return allRows.value.filter((row) => row.scope === 'header')
  return allRows.value.filter((row) => row.scope === 'detail' && row.tabKey === scope)
})

const customCount = computed(() => allRows.value.filter((row) => row.custom).length)

const canAdd = computed(() => !!String(form.dataName || '').trim() && !!form.scope)

/** 词表摘要：内置下拉可能几十项，只显示前 3 项 + 省略号，避免把表格撑吵 */
function optionText(row) {
  const options = row.options
  if (!options) return ''
  const list = Array.isArray(options)
    ? options.map((o) => (o && typeof o === 'object' ? o.label ?? o.value : o))
    : String(options).split(/[,，]/)
  const text = list.filter((o) => o !== null && o !== undefined && String(o) !== '').map(String)
  if (!text.length) return ''
  return text.length > 3 ? text.slice(0, 3).join('，') + '…' : text.join('，')
}

function reset() {
  // 默认位置：当前面板第一个明细页签（最常见诉求是给明细加一列）
  form.scope = tabs.value[0]?.key || 'header'
  form.dataName = ''
  form.dataType = '文本'
  form.options = ''
  form.fieldWidth = null
  form.isRequired = false
}

function payload() {
  const body = {
    panelCode: props.panelCode,
    scope: form.scope === 'header' ? 'header' : 'detail',
    tabKey: form.scope === 'header' ? '' : form.scope,
    dataName: String(form.dataName || '').trim(),
    dataType: form.dataType,
    isRequired: !!form.isRequired,
  }
  if (form.dataType === '下拉框') body.options = form.options
  if (form.fieldWidth != null) body.fieldWidth = form.fieldWidth
  return body
}

async function add() {
  if (!canAdd.value) return
  saving.value = true
  try {
    await engine.addCustomField(payload())
    ElMessage.success(tt('字段已添加') + '：' + payload().dataName)
    reset()
    emit('done')
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('添加失败'))
  } finally {
    saving.value = false
  }
}

async function retire(row) {
  try {
    await ElMessageBox.confirm(
      tt('停用字段「') + row.dataName + tt('」？已录入的数据会保留，重新加同名字段即可恢复显示。'),
      tt('停用字段'),
      { type: 'warning' },
    )
  } catch (e) {
    return
  }
  try {
    await engine.retireCustomField({ panelCode: props.panelCode, scope: row.scope, tabKey: row.tabKey || '', dataName: row.dataName })
    ElMessage.success(tt('已停用'))
    emit('done')
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('停用失败'))
  }
}
</script>

<style scoped>
.fm-tip {
  margin-bottom: 10px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--t-text-2, #5d6c67);
}
.fm-tip-sub {
  display: block;
  color: var(--t-text-3, #8b9893);
}
.fm-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--t-text-2, #5d6c67);
}
.fm-scope-label {
  color: var(--t-text-3, #8b9893);
}
.fm-count b {
  color: var(--t-primary, #116a5b);
}
.fm-table :deep(.el-table__row) {
  cursor: default;
}
.fm-src {
  padding: 0 5px;
  border-radius: 8px;
  font-size: 11px;
  color: var(--t-text-2, #5d6c67);
  border: 1px solid var(--t-border, #dce4e1);
}
.fm-src.custom {
  color: var(--t-primary-dark, #0d584c);
  border-color: var(--el-color-primary-light-5, #88b4ac);
  background: var(--el-color-primary-light-9, #e8f1ef);
}
.fm-help {
  margin-left: 8px;
  font-size: 12px;
  color: var(--t-text-3, #8b9893);
}
</style>
