<template>
  <el-dialog
    v-model="visible"
    :title="tt('表格调整') + ' - ' + tt(panelName || panelCode)"
    width="520px"
    append-to-body
    @open="initRows"
  >
    <div class="col-prefs-tip">{{ tt('拖动顺序用箭头调整；取消勾选隐藏列；栏名别名留空显示默认列名。保存后仅影响您本人的显示。') }}</div>
    <el-table :data="rows" size="small" max-height="420" class="col-prefs-table">
      <el-table-column :label="tt('顺序')" width="96" align="center">
        <template #default="{ $index }">
          <el-button size="small" text :disabled="$index === 0" @click="move($index, -1)">▲</el-button>
          <el-button size="small" text :disabled="$index === rows.length - 1" @click="move($index, 1)">▼</el-button>
        </template>
      </el-table-column>
      <el-table-column prop="name" :label="tt('列名')" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">{{ tt(row.name) }}</template>
      </el-table-column>
      <el-table-column :label="tt('显示')" width="64" align="center">
        <template #default="{ row }">
          <el-checkbox v-model="row.visible" />
        </template>
      </el-table-column>
      <el-table-column :label="tt('栏名别名')" min-width="140">
        <template #default="{ row }">
          <el-input v-model="row.alias" maxlength="100" :placeholder="tt('默认列名')" size="small" clearable />
        </template>
      </el-table-column>
    </el-table>
    <template #footer>
      <el-button @click="visible = false">{{ tt('取消') }}</el-button>
      <el-button @click="restore">{{ tt('恢复默认') }}</el-button>
      <el-button type="primary" @click="save">{{ tt('保存') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { tt } from '@/i18n'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  panelCode: { type: String, required: true },
  panelName: { type: String, default: '' },
  // 全量列清单（含隐藏列）：[{ name, alias, visible }]；无定制时传默认列名数组也行
  columns: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue', 'save'])

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const rows = ref([])

function initRows() {
  rows.value = (props.columns || []).map((c) => ({
    name: typeof c === 'string' ? c : String(c.name ?? ''),
    alias: typeof c === 'string' ? '' : String(c.alias ?? ''),
    visible: typeof c === 'string' ? true : c.visible !== false,
  }))
}

function move(index, delta) {
  const target = index + delta
  if (target < 0 || target >= rows.value.length) return
  const next = [...rows.value]
  const [item] = next.splice(index, 1)
  next.splice(target, 0, item)
  rows.value = next
}

async function restore() {
  try {
    await ElMessageBox.confirm(tt('恢复默认列序/显隐/栏名？该操作会清除您在本面板的表格调整。'), tt('恢复默认'), { type: 'warning' })
  } catch (e) {
    return
  }
  emit('save', [])
  visible.value = false
}

function save() {
  const aliases = rows.value.map((r) => (r.alias || '').trim()).filter(Boolean)
  if (new Set(aliases).size !== aliases.length) {
    ElMessage.warning(tt('栏名别名存在重复，请修改后保存'))
    return
  }
  if (!rows.value.some((r) => r.visible)) {
    ElMessage.warning(tt('至少保留一个显示列'))
    return
  }
  emit('save', rows.value.map((r) => ({ name: r.name, alias: (r.alias || '').trim(), visible: !!r.visible })))
  visible.value = false
}
</script>

<style scoped>
.col-prefs-tip {
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.col-prefs-table :deep(.el-table__row) {
  cursor: default;
}
</style>
