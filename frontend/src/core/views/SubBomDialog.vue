<!-- SubBomDialog.vue — 材料明细子件 BOM 展示弹窗（只读）：材料编码 → 该材料存货的下级 BOM -->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="dialogTitle"
    width="min(880px, 96vw)"
    append-to-body
    @update:model-value="close"
  >
    <div class="sub-tip">{{ tt('上级件：') }}{{ material ? (material['产品编码'] || '') + ' ' + (material['材料名称'] || '') + '（' + (material['材料编码'] || '') + '）' : '-' }}{{ tt(' 的下级子件（只读）') }}</div>
    <el-table :data="bomRows" size="small" border height="300">
      <el-table-column :label="tt('子件编码')" prop="材料编码" width="120" />
      <el-table-column :label="tt('子件名称')" prop="材料名称" min-width="180" />
      <el-table-column :label="tt('规格型号')" prop="规格型号" min-width="130" />
      <el-table-column :label="tt('单位')" prop="计量单位" width="80" />
      <el-table-column :label="tt('定额需用数量')" prop="定额需用数量" width="120" align="right" />
      <el-table-column :label="tt('损耗率%')" prop="损耗率%" width="100" align="right" />
    </el-table>
    <div v-if="!bomRows.length" class="empty-tip">{{ tt('该物料清单暂无子件') }}</div>

    <!-- 工艺路线：同一张 BOM 上挂的路线，按加工顺序列出，派工/报工都按这个顺序 -->
    <div class="sub-route-head">
      <span class="sub-route-title">{{ tt('工艺路线') }}</span>
      <span v-if="routeCode" class="sub-route-code">{{ routeCode }}</span>
      <span v-else class="sub-route-empty">{{ tt('这张物料清单还没挂工艺路线') }}</span>
    </div>
    <el-table v-if="routeRows.length" :data="routeRows" size="small" border height="220" class="sub-route-table">
      <el-table-column :label="tt('顺序')" width="70" align="center">
        <template #default="{ $index }">{{ $index + 1 }}</template>
      </el-table-column>
      <el-table-column :label="tt('工序编码')" prop="工序编码" width="110" />
      <el-table-column :label="tt('工序名称')" prop="工序名称" min-width="140" />
      <el-table-column :label="tt('加工方式')" prop="加工方式" width="100" />
      <el-table-column :label="tt('生产车间')" prop="生产车间" width="120" />
      <el-table-column :label="tt('关键工序')" width="96" align="center">
        <template #default="{ row }">
          <span v-if="row['关键工序']" class="sub-key">{{ tt('关键') }}</span>
          <span v-else class="sub-dim">—</span>
        </template>
      </el-table-column>
    </el-table>
    <div v-else-if="routeCode" class="empty-tip">{{ tt('该工艺路线还没有工序明细') }}</div>

    <template #footer>
      <el-button type="primary" @click="close">{{ tt('关闭') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { tt } from '@/i18n'

const props = defineProps({
  modelValue: Boolean,
  material: Object, // 材料明细行（材料编码/材料名称）
  bom: Array,       // 该材料的下级 BOM（父组件已从 INV 解析好）
  route: Array,     // 该产品的工艺路线工序行（加工顺序）
  routeCode: String,
})
const emit = defineEmits(['update:modelValue'])

/**
 * 标题：`BOM：BOM-005 CP005 轴套 C`。
 * 中间那个是产成品编号——只看 BOM 号和名称分不清是哪件产品。
 */
const dialogTitle = computed(() => {
  const m = props.material
  if (!m || !m['材料编码']) return tt('BOM')
  const code = String(m['产品编码'] || '').trim()
  const rest = [m['材料编码']]
  if (code) rest.push(code)
  if (m['材料名称']) rest.push(String(m['材料名称']))
  return 'BOM：' + rest.join(' ')
})

const bomRows = ref([])
watch(
  () => [props.modelValue, props.bom],
  () => {
    bomRows.value = (Array.isArray(props.bom) ? props.bom : []).map((r) => ({ ...r }))
  },
  { immediate: true }
)

const routeRows = computed(() => {
  const list = Array.isArray(props.route) ? props.route : []
  return [...list].sort((a, b) => Number(a['加工顺序'] || 0) - Number(b['加工顺序'] || 0))
})

function close() {
  emit('update:modelValue', false)
}
</script>

<style scoped>
.sub-route-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 14px 0 8px;
}
.sub-route-title {
  font-size: 13px;
  font-weight: 600;
  color: rgba(0, 0, 0, 0.88);
}
.sub-route-code {
  padding: 0 6px;
  border: 1px solid var(--d-border-strong);
  border-radius: 4px;
  font-size: 12px;
  color: rgba(0, 0, 0, 0.65);
}
.sub-route-empty {
  font-size: 12px;
  color: rgba(0, 0, 0, 0.45);
}
.sub-key {
  color: var(--d-warn);
  font-size: 12px;
}
.sub-dim {
  color: rgba(0, 0, 0, 0.25);
}
</style>

<style scoped>
.sub-tip {
  font-size: 13px;
  color: var(--d-primary);
  margin-bottom: 8px;
}
.empty-tip {
  text-align: center;
  color: #999;
  padding: 10px 0;
}
</style>
