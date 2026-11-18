<!-- RouteFlowPickDialog.vue - 工艺路线选择弹窗（配置驱动）
     用途：BOM 表头「工艺路线编码」这类字段，点击后按「路线 + 横向工序流程」挑选，
     而不是通用参照表格（通用表只能看到编码/名称，看不出这道产品要经过哪些工序、哪几道是委外）。
     用法：<RouteFlowPickDialog v-model="visible" :field="field" @confirm="onConfirm" />
     字段在 panel_config 里声明 refDialog: "route-flow" 即走本弹窗。 -->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="tt('选择工艺路线')"
    width="1040px"
    append-to-body
    destroy-on-close
    @update:model-value="(v) => emit('update:modelValue', v)"
    @open="open"
  >
    <div class="rfp">
      <div class="rfp-toolbar">
        <el-input
          v-model="keyword"
          :placeholder="tt('输入编码/名称过滤…')"
          clearable
          size="small"
          style="width: 260px"
          @keyup.enter="open"
        />
        <el-button size="small" type="primary" :icon="Search" @click="open">{{ tt('查询') }}</el-button>
        <span class="rfp-tip">{{ tt('共') }} {{ routes.length }} {{ tt('条路线') }}</span>
      </div>

      <el-table
        :data="routes"
        v-loading="loading"
        size="small"
        border
        height="240"
        highlight-current-row
        :current-row-key="current?.工艺路线编码"
        row-key="工艺路线编码"
        @row-click="onPick"
      >
        <el-table-column type="index" :label="tt('序号')" width="56" align="center" :index="(i) => i + 1" />
        <el-table-column prop="工艺路线编码" :label="tt('工艺路线编码')" min-width="130" show-overflow-tooltip />
        <el-table-column prop="工艺路线名称" :label="tt('工艺路线名称')" min-width="200" show-overflow-tooltip />
        <el-table-column :label="tt('工序数')" width="80" align="right">
          <template #default="{ row }">{{ processesOf(row).length }}</template>
        </el-table-column>
        <el-table-column :label="tt('含委外')" width="90" align="center">
          <template #default="{ row }">
            <span v-if="outsourceCount(row)" class="rfp-out-tag">{{ outsourceCount(row) }}</span>
            <span v-else class="rfp-none">—</span>
          </template>
        </el-table-column>
        <el-table-column :label="tt('停用')" width="70" align="center">
          <template #default="{ row }">{{ row['停用'] ? tt('是') : '' }}</template>
        </el-table-column>
      </el-table>

      <div class="rfp-flow-head">
        <span class="rfp-flow-title">
          {{ tt('工序流程') }}
          <template v-if="current">：{{ current['工艺路线编码'] }} {{ current['工艺路线名称'] || '' }}</template>
        </span>
        <span v-if="current && processesOf(current).length" class="rfp-flow-legend">
          <span class="rfp-legend-chip">{{ tt('自制') }}</span>
          <span class="rfp-legend-chip is-out">{{ tt('委外') }}</span>
          <span class="rfp-legend-note">{{ tt('数字为工序先后，从左到右依次加工') }}</span>
        </span>
      </div>
      <div class="rfp-flow" v-if="current && processesOf(current).length">
        <template v-for="(p, i) in processesOf(current)" :key="p['工序编码'] + '-' + i">
          <div class="rfp-step" :class="{ 'is-out': isOutsource(p) }">
            <span class="rfp-seq">{{ p['工序顺序'] ?? p['加工顺序'] ?? i + 1 }}</span>
            <span class="rfp-name">{{ p['工序名称'] || p['工序编码'] }}</span>
            <span v-if="isOutsource(p)" class="rfp-out">{{ tt('委外') }}</span>
            <span v-if="p['生产车间']" class="rfp-ws">{{ p['生产车间'] }}</span>
          </div>
          <span v-if="i < processesOf(current).length - 1" class="rfp-arrow">›</span>
        </template>
      </div>
      <div v-else class="rfp-empty">{{ current ? tt('该路线没有工序明细') : tt('请选择一条路线查看工序流程') }}</div>
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">{{ tt('取消') }}</el-button>
      <el-button type="primary" :disabled="!current" @click="confirm">
        {{ current ? tt('确定') + '（' + current['工艺路线编码'] + '）' : tt('请选择路线') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { usePanelRuntime } from '@core/panel-runtime'
import { tt } from '@/i18n'

const engine = usePanelRuntime()

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  field: { type: Object, default: null },       // 原始字段配置（含 refPanel/refField/refMap）
  routePanel: { type: String, default: 'ROUTE' },
})
const emit = defineEmits(['update:modelValue', 'confirm'])

const keyword = ref('')
const routes = ref([])
const current = ref(null)
const loading = ref(false)

/** 路线行里的工序明细：后端已按加工顺序排好并写 工序顺序 */
function processesOf(route) {
  const procs = route?.detail?.processes
  if (!Array.isArray(procs)) return []
  return [...procs].sort((a, b) => {
    const sa = Number(a['工序顺序'] ?? a['加工顺序'] ?? 0)
    const sb = Number(b['工序顺序'] ?? b['加工顺序'] ?? 0)
    return sa - sb
  })
}

function isOutsource(p) {
  return String(p?.['加工方式'] || '').includes('委外')
}

function outsourceCount(route) {
  return processesOf(route).filter(isOutsource).length
}

async function open() {
  loading.value = true
  try {
    const field = { ...(props.field || {}), refPanel: props.field?.refPanel || props.routePanel }
    const rows = await engine.queryRefRows(field, { keyword: keyword.value })
    // 只保留能拿到编码的路线；停用的按业务惯例仍可看，但排在后面
    routes.value = (Array.isArray(rows) ? rows : []).filter((r) => r && r['工艺路线编码'])
    current.value = routes.value[0] || null
    if (!routes.value.length) ElMessage.warning(tt('没有可选的工艺路线'))
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('工艺路线加载失败'))
    routes.value = []
    current.value = null
  } finally {
    loading.value = false
  }
}

function onPick(row) {
  current.value = row
}

function confirm() {
  if (!current.value) return
  // 与通用参照保持同一出口：回传选中行数组，由调用方按 refField/refMap 落值
  emit('confirm', [current.value])
  emit('update:modelValue', false)
}
</script>

<style scoped>
.rfp-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.rfp-tip {
  margin-left: 4px;
  font-size: 12px;
  color: var(--t-text-3);
}
.rfp-out-tag {
  display: inline-block;
  min-width: 18px;
  padding: 0 4px;
  border-radius: 4px;
  border: 1px solid var(--d-warn);
  color: var(--d-warn);
  font-size: 12px;
  line-height: 18px;
}
.rfp-none {
  color: var(--t-text-3);
}
.rfp-flow-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin: 12px 0 8px;
}
.rfp-flow-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--t-text-1);
}
.rfp-flow-legend {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--t-text-3);
}
.rfp-legend-chip {
  padding: 0 6px;
  border: 1px solid var(--d-border-strong);
  border-radius: 4px;
  background: #fff;
  color: var(--t-text-2);
  line-height: 18px;
}
.rfp-legend-chip.is-out {
  border-color: var(--d-warn);
  color: var(--d-warn);
  background: var(--d-warn-bg);
}
.rfp-legend-note {
  margin-left: 4px;
}
/* 横向工序流程：一行流式排布，超出可横向滚动，不换行打断先后阅读 */
.rfp-flow {
  display: flex;
  align-items: center;
  gap: 6px;
  overflow-x: auto;
  padding: 12px;
  border: 1px solid var(--d-border);
  border-radius: 6px;
  background: var(--d-fill-soft);
  min-height: 68px;
}
.rfp-step {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
  padding: 6px 10px;
  border: 1px solid var(--d-border-strong);
  border-radius: 6px;
  background: #fff;
}
.rfp-step.is-out {
  border-color: var(--d-warn);
  border-style: dashed;
  background: var(--d-warn-bg);
}
.rfp-seq {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--t-primary);
  color: #fff;
  font-size: 12px;
  line-height: 1;
}
.rfp-step.is-out .rfp-seq {
  background: var(--d-warn);
}
.rfp-name {
  font-size: 13px;
  color: var(--t-text-1);
  white-space: nowrap;
}
.rfp-out {
  padding: 0 4px;
  border-radius: 4px;
  border: 1px solid var(--d-warn);
  color: var(--d-warn);
  font-size: 11px;
  line-height: 16px;
}
.rfp-ws {
  font-size: 11px;
  color: var(--t-text-3);
  white-space: nowrap;
}
.rfp-arrow {
  flex: 0 0 auto;
  color: var(--t-text-3);
  font-size: 16px;
  line-height: 1;
}
.rfp-empty {
  padding: 24px 12px;
  border: 1px dashed var(--d-border);
  border-radius: 6px;
  color: var(--t-text-3);
  font-size: 13px;
  text-align: center;
}
</style>
