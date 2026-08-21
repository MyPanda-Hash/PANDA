<template>
  <div class="sline">
    <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" class="line-svg">
      <polyline :points="ptsA" fill="none" stroke="#289be5" stroke-width="2" />
      <polyline :points="ptsB" fill="none" stroke="#22c55e" stroke-width="2" stroke-dasharray="5 4" />
      <circle v-for="(p, i) in xysA" :key="'a' + i" :cx="p.x" :cy="p.y" r="2.5" fill="#289be5" />
      <circle v-for="(p, i) in xysB" :key="'d' + i" :cx="p.x" :cy="p.y" r="2.5" fill="#22c55e" />
    </svg>
    <div class="line-x">
      <span v-for="d in data" :key="d.date" class="lx">{{ d.date }}</span>
    </div>
    <div class="line-legend">
      <span class="lg-k"><i class="lg-dot" style="background: #289be5"></i>新增</span>
      <span class="lg-k"><i class="lg-dot" style="background: #22c55e"></i>完工</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  data: { type: Array, default: () => [] },
})

const W = 320
const H = 120
const PAD = 12

const max = computed(() => Math.max(...props.data.flatMap((d) => [d.added || 0, d.done || 0]), 1))
const xy = (key) => {
  const n = props.data.length
  return props.data.map((d, i) => ({
    x: n <= 1 ? W / 2 : PAD + (i * (W - PAD * 2)) / (n - 1),
    y: H - PAD - ((d[key] || 0) / max.value) * (H - PAD * 2),
  }))
}
const xysA = computed(() => xy('added'))
const xysB = computed(() => xy('done'))
const ptsA = computed(() => xysA.value.map((p) => `${p.x},${p.y}`).join(' '))
const ptsB = computed(() => xysB.value.map((p) => `${p.x},${p.y}`).join(' '))
</script>

<style scoped>
.sline {
  display: flex;
  flex-direction: column;
}
.line-svg {
  width: 100%;
  height: 130px;
}
.line-x {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: var(--t-text-3);
  padding: 2px 6px 0;
}
.lx {
  flex-shrink: 0;
}
.line-legend {
  display: flex;
  gap: 16px;
  justify-content: center;
  font-size: 12px;
  color: var(--t-text-2);
  margin-top: 8px;
}
.lg-k {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.lg-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
}
</style>