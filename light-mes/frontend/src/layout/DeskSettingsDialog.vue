<template>
  <el-dialog :model-value="modelValue" :title="tt('工作台设置')" width="480px" append-to-body @update:model-value="(v) => $emit('update:modelValue', v)">
    <div class="desk-setting">
      <div class="set-group">
        <div class="set-group-title">{{ tt('快捷入口') }}</div>
        <el-checkbox-group v-model="quick" @change="save">
          <el-checkbox value="newOrder">{{ tt('新建加工单') }}</el-checkbox>
          <el-checkbox value="quickReport">{{ tt('快速报工') }}</el-checkbox>
          <el-checkbox value="board">{{ tt('生产看板') }}</el-checkbox>
        </el-checkbox-group>
      </div>
      <div class="set-group">
        <div class="set-group-title">{{ tt('内容卡片') }}</div>
        <div class="set-row">
          <span>{{ tt('KPI 指标卡') }}</span>
          <el-switch v-model="showKpi" @change="save" />
        </div>
        <div class="set-row">
          <span>{{ tt('生产进度') }}</span>
          <el-switch v-model="showProgress" @change="save" />
        </div>
        <div class="set-row">
          <span>{{ tt('我的待办') }}</span>
          <el-switch v-model="showTodo" @change="save" />
        </div>
      </div>
      <div class="set-hint">{{ tt('保存后回到「我的桌面」查看效果。') }}</div>
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

const quick = ref([...app.deskSettings.quick])
const showKpi = ref(app.deskSettings.showKpi)
const showProgress = ref(app.deskSettings.showProgress)
const showTodo = ref(app.deskSettings.showTodo)

function save() {
  app.saveDeskSettings({
    quick: [...quick.value],
    showKpi: showKpi.value,
    showProgress: showProgress.value,
    showTodo: showTodo.value,
  })
}

watch(
  () => app.deskSettings,
  (s) => {
    quick.value = [...s.quick]
    showKpi.value = s.showKpi
    showProgress.value = s.showProgress
    showTodo.value = s.showTodo
  },
  { deep: true }
)
</script>

<style scoped>
.set-group {
  padding: 10px 0;
  border-bottom: 1px solid var(--t-border-light);
}
.dark .set-group {
  border-color: #3a3b42;
}
.set-group-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--t-text-1);
  margin-bottom: 10px;
}
.dark .set-group-title {
  color: #ddd;
}
.set-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0;
  font-size: 13px;
  color: var(--t-text-2);
}
.dark .set-row {
  color: #bbb;
}
.set-hint {
  margin-top: 10px;
  font-size: 12px;
  color: var(--t-text-3);
}
</style>
