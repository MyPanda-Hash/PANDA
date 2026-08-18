<!-- SubBomDialog.vue — 材料明细子件 BOM 展示弹窗（只读）：材料编码 → 该材料存货的下级 BOM -->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="material && material['材料编码'] ? '子件 BOM：' + material['材料编码'] + ' ' + (material['材料名称'] || '') : '子件 BOM'"
    width="620px"
    append-to-body
    @update:model-value="close"
  >
    <div class="sub-tip">父件：{{ material && material['材料编码'] ? material['材料编码'] + ' ' + (material['材料名称'] || '') : '-' }} 的下级子件（只读）</div>
    <el-table :data="bomRows" size="small" border height="260">
      <el-table-column label="材料编码" prop="材料编码" width="100" />
      <el-table-column label="材料名称" prop="材料名称" min-width="150" />
      <el-table-column label="规格型号" prop="规格型号" min-width="110" />
      <el-table-column label="单位" prop="计量单位" width="70" />
      <el-table-column label="定额需用数量" prop="定额需用数量" width="110" align="right" />
      <el-table-column label="损耗率%" prop="损耗率%" width="90" align="right" />
    </el-table>
    <div v-if="!bomRows.length" class="empty-tip">该材料暂无下级子件 BOM</div>
    <template #footer>
      <el-button type="primary" @click="close">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  modelValue: Boolean,
  material: Object, // 材料明细行（材料编码/材料名称）
  bom: Array,       // 该材料的下级 BOM（父组件已从 INV 解析好）
})
const emit = defineEmits(['update:modelValue'])

const bomRows = ref([])
watch(
  () => [props.modelValue, props.bom],
  () => {
    bomRows.value = (Array.isArray(props.bom) ? props.bom : []).map((r) => ({ ...r }))
  },
  { immediate: true }
)

function close() {
  emit('update:modelValue', false)
}
</script>

<style scoped>
.sub-tip {
  font-size: 13px;
  color: #1c4f8a;
  margin-bottom: 8px;
}
.empty-tip {
  text-align: center;
  color: #999;
  padding: 10px 0;
}
</style>
