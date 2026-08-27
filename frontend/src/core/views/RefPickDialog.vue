<template>
  <el-dialog
    :model-value="modelValue"
    :title="title"
    width="880px"
    append-to-body
    destroy-on-close
    @update:model-value="(v) => emit('update:modelValue', v)"
    @open="open"
  >
    <div class="rpd">
      <div class="rpd-toolbar">
        <el-input v-model="keyword" placeholder="输入关键字过滤…" clearable size="small" style="width: 240px" @keyup.enter="open" />
        <el-button size="small" type="primary" :icon="Search" @click="open">查询</el-button>
        <span class="rpd-tip">{{ tipText }} · 共 {{ total }} 条</span>
      </div>
      <el-table
        :data="rows"
        v-loading="loading"
        size="small"
        border
        height="380"
        highlight-current-row
        @selection-change="(r) => (selected = r)"
      >
        <el-table-column type="selection" width="45" />
        <el-table-column v-for="c in columns" :key="c" :prop="c" :label="c" min-width="110" show-overflow-tooltip />
      </el-table>
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :disabled="!selected.length" @click="confirm">确定导入（{{ selected.length }} 行）</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { usePanelRuntime } from '@core/panel-runtime'

const engine = usePanelRuntime()

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  // 兼容旧调用方（v-model:visible）
  // 参照字段定义（原始配置字段或 buildMeta 输出的 meta.ref 均可）
  field: { type: Object, default: null },
  // 'header' 表头单值字段（多选仅导入第一行）/ 'detail' 明细行（多选每行生成一条明细）
  mode: { type: String, default: 'header' },
})
const emit = defineEmits(['update:modelValue', 'update:visible', 'confirm'])

const keyword = ref('')
const rows = ref([])
const columns = ref([])
const selected = ref([])
const loading = ref(false)
const total = ref(0)

const title = ref('参照选择')
const multi = computed(() => !!props.field?.refMulti || !!props.field?.multi)
const tipText = computed(() => {
  if (props.mode === 'detail') return '可勾选多行，确定后每行生成一条明细'
  if (multi.value) return '可勾选多行，确定后一次导入（多值顿号连接）'
  return '单值字段，勾选多行时仅导入第一行'
})

async function open() {
  if (!props.field) return
  title.value = (await engine.refPanelName(props.field)) + ' · 参照选择'
  loading.value = true
  selected.value = []
  rows.value = []
  try {
    columns.value = await engine.refColumns(props.field)
    const list = await engine.queryRefRows(props.field, { keyword: keyword.value })
    rows.value = list
    total.value = list.length
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '参照数据加载失败')
  } finally {
    loading.value = false
  }
}

function confirm() {
  if (!selected.value.length) return
  emit('confirm', selected.value)
  emit('update:modelValue', false)
}
</script>

<style scoped>
.rpd-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.rpd-tip {
  font-size: 12px;
  color: var(--t-text-3);
  margin-left: 4px;
}
</style>
