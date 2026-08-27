<template>
  <div class="sbars">
    <template v-if="data.length">
      <div v-for="(d, i) in data" :key="i" class="bar-row">
        <span class="bar-label" :title="d.name">{{ d.name }}</span>
        <div class="bar-track">
          <div class="bar-fill" :style="{ width: pct(d.value) + '%', background: colorOf(i) }"></div>
        </div>
        <span class="bar-val">{{ d.value }}</span>
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

function pct(v) {
  return Math.round(((v || 0) / max.value) * 100)
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
.bar-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.bar-label {
  width: 88px;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--t-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: right;
}
.bar-track {
  flex: 1;
  height: 16px;
  background: var(--t-border-light);
  border-radius: 3px;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.4s ease;
  min-width: 2px;
}
.bar-val {
  width: 34px;
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--t-text-1);
  text-align: right;
}
.chart-empty {
  color: var(--t-text-3);
  font-size: 12px;
  text-align: center;
  padding: 30px 0;
}
</style>
