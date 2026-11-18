<template>
  <div class="sbars">
    <template v-if="data.length">
      <div v-for="(d, i) in data" :key="i" class="bar-row">
        <span class="bar-label" :title="d.name">{{ d.name }}</span>
        <div class="bar-track">
          <div class="bar-fill" :style="{ width: pct(d.value) + '%', background: colorOf(i) }"></div>
        </div>
        <span class="bar-val">{{ fmt(d.value) }}</span>
      </div>
    </template>
    <div v-else class="chart-empty">暂无数据</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  data: { type: Array, default: () => [] },
  colors: { type: Array, default: () => ['#116a5b', '#d79a2b', '#537786', '#8a9a92', '#3b8978', '#b94d3f', '#9c7650', '#708575'] },
})

const max = computed(() => {
  const m = Math.max(...props.data.map((d) => d.value || 0), 1)
  return m || 1
})

const numberFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 })

function pct(v) {
  return Math.round(((v || 0) / max.value) * 100)
}
function fmt(v) {
  return numberFormat.format(Number(v || 0))
}
function colorOf(i) {
  return props.colors[i % props.colors.length]
}
</script>

<style scoped>
.sbars {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px 2px;
}
/* 网格：标签定宽、条自适应、数值按内容取宽（大数字不会挤到条形上） */
.bar-row {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
}
.bar-label {
  font-size: 12px;
  color: var(--t-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: right;
}
.bar-track {
  height: 8px;
  background: var(--t-border-light);
  border-radius: 100px;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  border-radius: 100px;
  transition: width 0.4s ease;
  min-width: 2px;
}
.bar-val {
  min-width: 34px;
  font-size: 13px;
  font-weight: 600;
  color: var(--t-text-1);
  text-align: right;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.chart-empty {
  color: var(--t-text-3);
  font-size: 12px;
  text-align: center;
  padding: 30px 0;
}
</style>