<template>
  <div class="sdonut">
    <div class="donut-wrap">
      <svg viewBox="0 0 120 120" class="donut-svg">
        <circle cx="60" cy="60" r="48" fill="none" stroke="var(--t-border-light)" stroke-width="16" />
        <circle
          v-for="(seg, i) in segments"
          :key="i"
          cx="60" cy="60" r="48" fill="none"
          :stroke="colorOf(i)" stroke-width="16"
          :stroke-dasharray="`${seg.len} ${C - seg.len}`"
          :stroke-dashoffset="-seg.offset"
          transform="rotate(-90 60 60)"
        />
      </svg>
      <div class="donut-center">
        <div class="donut-total">{{ total }}</div>
        <div class="donut-sub">{{ sub }}</div>
      </div>
    </div>
    <div class="donut-legend">
      <div v-for="(seg, i) in segments" :key="i" class="lg-item">
        <span class="lg-dot" :style="{ background: colorOf(i) }"></span>
        <span class="lg-name" :title="seg.name">{{ seg.name }}</span>
        <span class="lg-val">{{ seg.value }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  data: { type: Array, default: () => [] },
  sub: { type: String, default: '合计' },
  colors: { type: Array, default: () => ['#116a5b', '#d79a2b', '#537786', '#8a9a92', '#3b8978', '#b94d3f', '#9c7650', '#708575'] },
})

const C = 2 * Math.PI * 48
const total = computed(() => props.data.reduce((s, d) => s + (d.value || 0), 0))
const segments = computed(() => {
  let acc = 0
  return props.data.map((d) => {
    const len = total.value ? (d.value / total.value) * C : 0
    const seg = { name: d.name, value: d.value, len, offset: acc }
    acc += len
    return seg
  })
})
function colorOf(i) {
  return props.colors[i % props.colors.length]
}
</script>

<style scoped>
.sdonut {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 4px 2px;
}
.donut-wrap {
  position: relative;
  width: 132px;
  height: 132px;
  flex-shrink: 0;
}
.donut-svg {
  width: 100%;
  height: 100%;
}
.donut-center {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.donut-total {
  font-size: 22px;
  font-weight: 700;
  color: var(--t-text-1);
}
.donut-sub {
  font-size: 11px;
  color: var(--t-text-3);
}
.donut-legend {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 7px;
  min-width: 0;
}
.lg-item {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  color: var(--t-text-2);
}
.lg-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
}
.lg-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.lg-val {
  font-weight: 600;
  color: var(--t-text-1);
}
</style>
