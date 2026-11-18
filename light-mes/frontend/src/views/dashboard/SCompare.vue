<template>
  <!--
    周期对比图：柱（本期）vs 柱（上期）+ 折线（环比%）。
    用 SVG 手绘，和本项目其它看板图（SLine/SBars/SDonut）保持同一实现风格，
    不引入图表库——这几个图只需要柱与折线，库的体积与主题适配成本都不划算。
  -->
  <div class="scmp">
    <div class="scmp-head">
      <div class="scmp-title">{{ title }}</div>
      <div class="scmp-legend">
        <span class="lg"><i class="dot cur"></i>{{ curName }}</span>
        <span class="lg"><i class="dot prev"></i>{{ prevName }}</span>
        <span class="lg"><i class="dot line"></i>{{ tt("环比") }}</span>
      </div>
    </div>

    <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" class="scmp-svg">
      <!-- 网格 -->
      <line v-for="(g, i) in gridYs" :key="'g' + i" x1="30" :y1="g" :x2="W - 8" :y2="g" class="g-line" />
      <text v-for="(g, i) in gridYs" :key="'gt' + i" x="4" :y="g + 3" class="g-txt">{{ gridLabels[i] }}</text>

      <!-- 柱：同一天两根，本期深色、上期浅色 -->
      <template v-for="(row, i) in rows" :key="'b' + i">
        <rect
          :x="barX(i, 0)"
          :y="barY(row.current)"
          :width="barW"
          :height="barH(row.current)"
          class="bar-cur"
        />
        <rect
          :x="barX(i, 1)"
          :y="barY(row.previous)"
          :width="barW"
          :height="barH(row.previous)"
          class="bar-prev"
        />
      </template>

      <!-- 环比折线 -->
      <polyline :points="linePoints" fill="none" class="line-cur" />
      <circle v-for="(p, i) in linePts" :key="'c' + i" :cx="p.x" :cy="p.y" r="2.5" class="line-dot" />

      <!-- 零线：环比 0% 的位置，一眼看出涨跌 -->
      <line x1="30" :y1="zeroY" :x2="W - 8" :y2="zeroY" class="zero-line" />
    </svg>

    <div class="scmp-x">
      <span v-for="(row, i) in rows" :key="'x' + i" class="lx">{{ row.label }}</span>
    </div>

    <div v-if="!rows.length" class="scmp-empty">{{ tt("暂无对比数据") }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { tt } from '@/i18n'

const props = defineProps({
  title: { type: String, default: '' },
  rows: { type: Array, default: () => [] },
  curName: { type: String, default: '本期' },
  prevName: { type: String, default: '上期' },
})

const W = 560
const H = 150
const PAD_TOP = 10
const PAD_BOTTOM = 18
const AXIS_L = 30

const maxBar = computed(() => {
  const v = Math.max(...props.rows.flatMap((r) => [r.current || 0, r.previous || 0]), 1)
  return v
})

const gridYs = computed(() => {
  const steps = 3
  const usable = H - PAD_TOP - PAD_BOTTOM
  return Array.from({ length: steps + 1 }, (_, i) => PAD_TOP + (usable * i) / steps)
})

const gridLabels = computed(() => {
  const steps = 3
  return gridYs.value.map((_, i) => Math.round((maxBar.value * (steps - i)) / steps))
})

const slot = computed(() => {
  const n = props.rows.length || 1
  return (W - AXIS_L - 12) / n
})

const barW = computed(() => Math.max(3, Math.min(14, slot.value / 3)))

const barX = (i, which) => AXIS_L + slot.value * i + slot.value / 2 - barW.value + which * (barW.value + 1)

const barH = (v) => {
  const usable = H - PAD_TOP - PAD_BOTTOM
  return Math.max(0, ((v || 0) / maxBar.value) * usable)
}

const barY = (v) => H - PAD_BOTTOM - barH(v)

// 环比折线：用「本期 vs 上期」的百分比，独立于柱的数值轴
const deltas = computed(() => props.rows.map((r) => {
  if (!r.previous) return r.current ? 100 : 0
  return ((r.current - r.previous) * 100) / r.previous
}))

const maxAbsDelta = computed(() => Math.max(...deltas.value.map((d) => Math.abs(d)), 20))

const zeroY = computed(() => {
  const usable = H - PAD_TOP - PAD_BOTTOM
  const maxUp = maxAbsDelta.value
  const maxDown = maxAbsDelta.value
  return PAD_TOP + (usable * maxUp) / (maxUp + maxDown)
})

const linePts = computed(() => props.rows.map((_, i) => {
  const usable = H - PAD_TOP - PAD_BOTTOM
  const total = maxAbsDelta.value * 2
  const y = zeroY.value - (deltas.value[i] / total) * usable
  return { x: AXIS_L + slot.value * i + slot.value / 2, y }
}))

const linePoints = computed(() => linePts.value.map((p) => `${p.x},${p.y}`).join(' '))
</script>

<style scoped>
.scmp { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.scmp-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.scmp-title { font-size: 12px; font-weight: 600; color: var(--t-text-2); }
.scmp-legend { display: flex; gap: 12px; align-items: center; }
.lg { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--t-text-3); }
.dot { width: 7px; height: 7px; border-radius: 2px; display: inline-block; }
.dot.cur { background: #116a5b; }
.dot.prev { background: #b9c9c6; }
.dot.line { background: #f0a020; border-radius: 50%; }
.scmp-svg { width: 100%; height: 150px; display: block; }
.g-line { stroke: var(--t-border); stroke-width: 1; stroke-dasharray: 3 3; }
.g-txt { font-size: 9px; fill: var(--t-text-3); }
.bar-cur { fill: #116a5b; }
.bar-prev { fill: #b9c9c6; }
.line-cur { stroke: #f0a020; stroke-width: 1.6; }
.line-dot { fill: #f0a020; }
.zero-line { stroke: var(--t-text-3); stroke-width: 1; stroke-dasharray: 4 3; opacity: .5; }
.scmp-x { display: flex; padding-left: 30px; }
.lx { flex: 1; text-align: center; font-size: 9px; color: var(--t-text-3); overflow: hidden; white-space: nowrap; }
.scmp-empty { padding: 24px; text-align: center; font-size: 12px; color: var(--t-text-3); }
</style>