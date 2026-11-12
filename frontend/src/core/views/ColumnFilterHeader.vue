<template>
  <div class="cfh" :class="{ on: active }">
    <div class="cfh-main">
      <span class="cfh-label" :title="labelText">{{ labelText }}</span>
      <span v-if="active" class="cfh-chip" :title="tt('点击清除筛选')" @click.stop="clear">{{ shortText }} ×</span>
      <el-icon v-else class="cfh-ic" :title="tt('筛选本列（含 = != &gt; &gt;= &lt; &lt;=）')" @click.stop="toggle"><Filter /></el-icon>
    </div>
    <el-input
      v-if="opened"
      ref="inputRef"
      :model-value="modelValue"
      size="small"
      clearable
      class="cfh-input"
      :placeholder="tt('筛选… 例：铝棒 / =CP001 / >=10')"
      @update:model-value="(v) => emit('update:modelValue', v)"
      @click.stop
      @keyup.esc="close"
      @clear="clear"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { Filter } from '@element-plus/icons-vue'
import { tt } from '@/i18n'

const props = defineProps({
  /** 列名（已翻译好的显示文本） */
  label: { type: String, default: '' },
  /** 本列的筛选词原文 */
  modelValue: { type: String, default: '' },
  /** 是否处于生效状态（有筛选词） */
  active: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'clear'])

const labelText = computed(() => props.label)
/** 生效时列头显示的摘要：过长截断，避免把表头撑宽 */
const shortText = computed(() => {
  const text = String(props.modelValue || '')
  return text.length > 10 ? text.slice(0, 10) + '…' : text
})

const opened = ref(false)
const inputRef = ref(null)

function toggle() {
  opened.value = !opened.value
}
function close() {
  opened.value = false
}
function clear() {
  emit('update:modelValue', '')
  emit('clear')
}

watch(opened, async (open) => {
  if (!open) return
  await nextTick()
  inputRef.value?.focus?.()
})
// 筛选被外部清掉（例如切换页签/查新单据）时收起输入框，避免留下空输入框
watch(() => props.active, (on) => { if (!on) opened.value = false })
</script>

<style scoped>
.cfh {
  line-height: 1.2;
}
.cfh-main {
  display: flex;
  align-items: center;
  justify-content: center;   /* 列头统一居中：全局规则管到 .cell，flex 容器里的内容要自己居中 */
  gap: 4px;
  min-width: 0;
}
.cfh-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cfh-ic {
  flex: none;
  font-size: 12px;
  color: var(--t-text-3, #8b9893);
  cursor: pointer;
  opacity: 0.5;
  transition: opacity 0.12s ease;
}
/* 图标常显（半透明）：悬停列头才变实，避免"能筛选"这件事只有鼠标扫过才被发现 */
.cfh:hover .cfh-ic,
.cfh.on .cfh-ic {
  opacity: 1;
}
.cfh-ic:hover {
  color: var(--t-primary, #116a5b);
}
.cfh-chip {
  flex: none;
  max-width: 96px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 0 4px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 400;
  color: var(--t-primary-dark, #0d584c);
  background: var(--el-color-primary-light-9, #e8f1ef);
  border: 1px solid var(--el-color-primary-light-7, #b8d3ce);
  cursor: pointer;
}
.cfh-chip:hover {
  border-color: var(--t-primary, #116a5b);
}
.cfh-input {
  margin-top: 2px;
}
.cfh-input :deep(.el-input__inner) {
  height: 24px;
  font-size: 12px;
  padding: 0 6px;
}
</style>
