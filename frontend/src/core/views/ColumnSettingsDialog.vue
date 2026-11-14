<template>
  <el-dialog
    :model-value="modelValue"
    title="列设置"
    width="560px"
    :close-on-click-modal="false"
    @update:model-value="$emit('update:modelValue', $event)"
    @open="onOpen"
  >
    <div class="col-set-tip">拖拽行调整列顺序，双击栏名可编辑，取消勾选则隐藏该列。</div>
    <div class="col-set-head">
      <span class="cs-order">顺序</span>
      <span class="cs-name">栏名（可修改）</span>
      <span class="cs-show">显示</span>
    </div>
    <div ref="listRef" class="col-set-list">
      <div
        v-for="(col, idx) in local"
        :key="col.prop"
        class="col-set-row"
        :class="{ dragging: dragIdx === idx, 'drag-over': dragOverIdx === idx && dragIdx !== idx }"
        draggable="true"
        @dragstart="onDragStart(idx, $event)"
        @dragover.prevent="onDragOver(idx)"
        @dragend="onDragEnd"
        @drop.prevent="onDrop(idx)"
      >
        <span class="cs-order">
          <span class="cs-drag-handle">⠿</span>
          <span class="cs-idx">{{ idx + 1 }}</span>
          <span class="cs-arrows">
            <span class="cs-arrow" title="上移" @click.stop="move(idx, -1)">▲</span>
            <span class="cs-arrow" title="下移" @click.stop="move(idx, 1)">▼</span>
          </span>
        </span>
        <span class="cs-name">
          <el-input v-model="col.label" size="small" :placeholder="col.prop" @dblclick="$event.target.select?.()" />
        </span>
        <span class="cs-show">
          <el-checkbox v-model="col.visible" size="small" />
        </span>
      </div>
    </div>
    <template #footer>
      <el-button size="small" @click="resetDefault">恢复默认</el-button>
      <el-button size="small" @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button size="small" type="primary" @click="confirm">确定</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** 全量列定义（默认态）：[{ prop, label }]，label 为原始栏名 */
  columns: { type: Array, default: () => [] },
  /** 已保存设置（可选）：[{ prop, label, visible }]，顺序/栏名/显隐覆盖默认 */
  saved: { type: Array, default: () => null },
})
const emit = defineEmits(['update:modelValue', 'save'])

const local = ref([])
const listRef = ref(null)
const dragIdx = ref(-1)
const dragOverIdx = ref(-1)

/** 用默认列 + 已保存设置 合成编辑列表 */
function compose(list) {
  const defaults = props.columns.map((c) => ({ prop: c.prop, label: c.label ?? c.prop }))
  if (!list) return defaults.map((d) => ({ ...d, visible: true }))
  const defMap = new Map(defaults.map((d) => [d.prop, d]))
  const ordered = list
    .filter((s) => defMap.has(s.prop))
    .map((s) => { const d = defMap.get(s.prop); return { prop: d.prop, label: s.label || d.label, visible: s.visible !== false } })
  const extras = defaults.filter((d) => !list.some((s) => s.prop === d.prop)).map((d) => ({ ...d, visible: true }))
  return [...ordered, ...extras]
}

function onOpen() {
  local.value = compose(props.saved)
}

function move(idx, dir) {
  const next = idx + dir
  if (next < 0 || next >= local.value.length) return
  const arr = [...local.value]
  ;[arr[idx], arr[next]] = [arr[next], arr[idx]]
  local.value = arr
}

function onDragStart(idx, ev) {
  dragIdx.value = idx
  ev.dataTransfer.effectAllowed = 'move'
}

function onDragOver(idx) {
  dragOverIdx.value = idx
}

function onDrop(idx) {
  const from = dragIdx.value
  if (from < 0 || from === idx) return onDragEnd()
  const arr = [...local.value]
  const [moved] = arr.splice(from, 1)
  arr.splice(idx, 0, moved)
  local.value = arr
  onDragEnd()
}

function onDragEnd() {
  dragIdx.value = -1
  dragOverIdx.value = -1
}

/** 恢复默认：原始顺序 + 原始栏名 + 全部显示 */
function resetDefault() {
  local.value = props.columns.map((c) => ({ prop: c.prop, label: c.label ?? c.prop, visible: true }))
}

function confirm() {
  emit('save', local.value.map((c) => ({ prop: c.prop, label: c.label, visible: c.visible })))
  emit('update:modelValue', false)
}
</script>

<style scoped>
.col-set-tip { font-size: 12px; color: var(--d-text-3); margin-bottom: 8px; }
.col-set-head, .col-set-row {
  display: flex; align-items: center; gap: 8px;
  padding: 4px 6px; border-bottom: 1px solid var(--d-border);
}
.col-set-head { color: var(--d-text-2); font-size: 13px; font-weight: 600; border-bottom: 2px solid var(--d-border); }
.col-set-row { cursor: grab; }
.col-set-row.dragging { opacity: .4; }
.col-set-row.drag-over { border-top: 2px solid var(--d-primary); }
.cs-order { width: 90px; display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
.cs-idx { width: 22px; text-align: center; font-size: 12px; color: var(--d-text-3); }
.cs-drag-handle { cursor: grab; color: var(--d-border-strong); font-size: 14px; }
.cs-arrows { display: flex; flex-direction: column; line-height: 1; }
.cs-arrow { font-size: 10px; cursor: pointer; color: var(--d-text-3); user-select: none; }
.cs-arrow:hover { color: var(--t-primary); }
.cs-name { flex: 1; }
.cs-show { width: 48px; text-align: center; flex-shrink: 0; }
.col-set-list { max-height: 380px; overflow-y: auto; }
</style>
