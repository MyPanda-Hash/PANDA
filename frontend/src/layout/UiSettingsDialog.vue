<template>
  <el-dialog :model-value="modelValue" :title="tt('界面设置')" width="460px" append-to-body @update:model-value="(v) => $emit('update:modelValue', v)">
    <div class="ui-setting">
      <div class="set-row">
        <div class="set-label">{{ tt('菜单模式') }}</div>
        <el-radio-group v-model="mode" @change="(v) => app.setMenuMode(v)">
          <el-radio-button value="accordion">{{ tt('手风琴') }}</el-radio-button>
          <el-radio-button value="flat">{{ tt('平铺') }}</el-radio-button>
        </el-radio-group>
      </div>
      <div class="set-row">
        <div class="set-label">{{ tt('侧边栏默认折叠') }}</div>
        <el-switch :model-value="app.collapsed" @change="app.toggleCollapse()" />
      </div>
      <div class="set-row">
        <div class="set-label">{{ tt('暗色模式') }}</div>
        <el-switch :model-value="app.dark" @change="app.toggleDark()" />
      </div>
      <div class="set-hint">{{ tt('设置实时生效并保存到本机浏览器。') }}</div>
    </div>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useAppStore } from '@/stores/app'
import { tt } from '@/i18n'

defineProps({ modelValue: Boolean })
defineEmits(['update:modelValue'])

const app = useAppStore()
const mode = ref(app.menuMode)

watch(
  () => app.menuMode,
  (v) => { mode.value = v }
)
</script>

<style scoped>
.set-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid var(--t-border-light);
}
.dark .set-row {
  border-color: #3a3b42;
}
.set-label {
  font-size: 13px;
  color: var(--t-text-2);
}
.dark .set-label {
  color: #bbb;
}
.set-hint {
  margin-top: 10px;
  font-size: 12px;
  color: var(--t-text-3);
}
</style>
