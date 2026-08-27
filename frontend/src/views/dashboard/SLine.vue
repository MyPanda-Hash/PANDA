<template>
  <div class="sline" :class="{ compact }">
    <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" class="line-svg">
      <line v-for="y in [30, 60, 90]" :key="y" x1="12" :y1="y" x2="308" :y2="y" class="grid-line" />
      <polygon :points="areaA" class="line-area" />
      <polyline :points="ptsA" fill="none" stroke="#537786" stroke-width="2" class="series-line" />
      <polyline :points="ptsB" fill="none" stroke="#116a5b" stroke-width="2" stroke-dasharray="5 4" class="series-line series-done" />
      <circle v-for="(p, i) in xysA" :key="'a' + i" :cx="p.x" :cy="p.y" r="2.5" fill="#537786" />
      <circle v-for="(p, i) in xysB" :key="'d' + i" :cx="p.x" :cy="p.y" r="2.5" fill="#116a5b" />
    </svg>
    <div class="line-x">
      <span v-for="d in data" :key="d.date" class="lx">{{ d.date }}</span>
    </div>
    <div class="line-legend">
      <span class="lg-k"><i class="lg-dot added"></i>新增</span>
      <span class="lg-k"><i class="lg-dot done"></i>完工</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  data: { type: Array, default: () => [] },
  compact: { type: Boolean, default: false },
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
const areaA = computed(() => {
  if (!xysA.value.length) return ''
  return `${PAD},${H - PAD} ${ptsA.value} ${W - PAD},${H - PAD}`
})
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
.grid-line {
  stroke: var(--t-border-light);
  stroke-width: 1;
}
.line-area {
  fill: #537786;
  opacity: 0.08;
}
.series-line {
  stroke-dasharray: 1000;
  stroke-dashoffset: 1000;
  animation: draw-line 0.9s ease forwards;
}
.series-line.series-done {
  stroke-dasharray: 5 4;
  stroke-dashoffset: 1000;
}
@keyframes draw-line {
  to { stroke-dashoffset: 0; }
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
.lg-dot.added { background: #537786; }
.lg-dot.done { background: #116a5b; }
.sline.compact .line-svg {
  height: 80px;
}
.sline.compact .line-x {
  padding-top: 0;
  font-size: 8px;
}
.sline.compact .line-legend {
  justify-content: flex-end;
  margin-top: 3px;
  font-size: 9px;
}

@media (prefers-reduced-motion: reduce) {
  .series-line {
    animation: none;
    stroke-dashoffset: 0;
  }
}
</style>
