<!-- VoucherFormDialog.vue — 单据表单弹窗（2026-08-20）：列表页双击明细行打开当前单据的表单（弹窗内嵌 PanelxForm，不再跳转新页签）
  用法：<VoucherFormDialog v-model="visible" :panel-code="panelCode" :code="code" @saved="reload" /> -->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="title"
    width="1180px"
    append-to-body
    destroy-on-close
    :close-on-click-modal="false"
    @update:model-value="close"
    @open="open"
  >
    <div class="vfd-body">
      <PanelxForm v-if="ready" :panel-code-prop="panelCode" :code-prop="code" embedded @saved="onSaved" />
    </div>
  </el-dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import PanelxForm from './PanelxForm.vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  panelCode: { type: String, default: '' },
  code: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'saved'])

// 弹窗标题 = 面板名-单据号（表单组件内部会自绘单据标题区）
const title = computed(() => (props.code ? props.panelCode + '-' + props.code : props.panelCode))

// destroy-on-close 下每次打开重建表单组件，ready 先置 false 再置 true 强制刷新
const ready = ref(false)
function open() {
  ready.value = false
  setTimeout(() => {
    ready.value = true
  }, 0)
}

function onSaved() {
  // 表单内保存/删除/审核等操作完成：父组件刷新列表并关闭弹窗
  emit('saved')
  close()
}

function close() {
  emit('update:modelValue', false)
}
</script>

<style scoped>
.vfd-body {
  /* 弹窗内表单：限高滚动，避免超出视口 */
  max-height: 72vh;
  overflow-y: auto;
  margin: -12px -24px -8px;
  padding: 8px 24px 4px;
}
</style>