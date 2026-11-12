<!-- RouteNodeCard.vue — 单据流程路线上的一个环节卡片
     主干卡片 + 挂在下面的分支（首件检验单、排产的逐道工序）。
     独立页与并行排产线共用这一份渲染，避免两种布局各写一套卡片。 -->
<template>
  <div class="rn-col">
    <button
      class="rn-node"
      :class="[kind, { stalled, running, pending: node['是否已发生'] === false }]"
      type="button"
      :disabled="node['是否已发生'] === false"
      :title="title"
      @click="$emit('open', node)"
    >
      <AntIcon :name="icon" :size="13" class="rn-ic" />
      <span class="rn-label">{{ label }}</span>
      <!-- 没单据时：工序这类按生产进度写「生产中/待生产」，其余环节才是「待生成」 -->
      <span class="rn-no">{{ node['是否已发生'] === false ? (node['单据状态'] || tt('待生成')) : node['单号'] }}</span>
      <span v-if="node['是否已发生'] !== false" class="rn-status">{{ tt(node['单据状态']) }}</span>
    </button>
    <!-- 分支环节：树支样式，不占主干步数 -->
    <div v-if="(node['branches'] || []).length" class="rn-branches">
      <button
        v-for="b in node['branches']"
        :key="'br-' + b['面板编码'] + '-' + (b['单号'] || b['工序名称'] || '')"
        class="rn-branch"
        :class="{ pending: b['是否已发生'] === false }"
        type="button"
        :disabled="b['是否已发生'] === false"
        :title="branchTitle(b)"
        @click.stop="$emit('open-branch', b)"
      >
        <AntIcon name="branches" :size="11" />
        <span class="rn-branch-label">{{ branchLabel(b) }}</span>
        <span class="rn-branch-no">{{ b['是否已发生'] === false ? (b['单据状态'] || tt('待生成')) : b['单号'] }}</span>
        <!-- 二级分支（首件报检）：标签形式跟在同一行，不另起一层树 -->
        <span
          v-for="sub in (b['branches'] || [])"
          :key="'sub-' + sub['面板编码'] + '-' + (sub['单号'] || sub['工序名称'] || '')"
          class="rn-branch-sub"
          :class="{ pending: sub['是否已发生'] === false }"
        >
          {{ tt(sub['环节']) }} {{ sub['是否已发生'] === false ? (sub['单据状态'] || tt('待生成')) : sub['单号'] }}
        </span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { tt } from '@/i18n'
import AntIcon from '@/business/components/AntIcon.vue'

const props = defineProps({
  /** 路线节点行：面板编码/环节/单号/单据状态/阶段/是否已发生/是否卡点/branches */
  node: { type: Object, required: true },
})

defineEmits(['open', 'open-branch'])

const kind = computed(() => {
  const state = props.node['阶段']
  if (state === '完成') return 'kind-done'
  if (state === '已中止') return 'kind-aborted'
  if (state === '进行中') return 'kind-doing'
  return 'kind-pending'
})

const stalled = computed(() => props.node['是否卡点'] === true)

/** 正在生产的环节（工序「生产中」）：给一道流光，一眼看出卡在哪道工序上 */
const running = computed(() => (props.node['单据状态'] || '') === '生产中')

/** 排产节点下面挂的是工序（有工序名称），主干节点显示环节名 */
const label = computed(() => {
  const name = props.node['环节'] || ''
  const process = props.node['工序名称'] || ''
  if (process && props.node['面板编码'] === 'DISPATCH') return `${name} · ${process}`
  if (process) return `${name} · ${process}`
  return name
})

const icon = computed(() => {
  if (props.node['是否已发生'] === false) return 'clock-circle'
  if (props.node['阶段'] === '完成') return 'check-circle'
  if (props.node['是否卡点']) return 'clock-circle'
  return 'file-text'
})

const title = computed(() => {
  const parts = [label.value]
  if (props.node['是否已发生'] === false) parts.push(tt('待生成'))
  else parts.push(props.node['单号'] || '', props.node['单据状态'] || '')
  if (props.node['是否卡点']) parts.push(tt('卡在这里'))
  return parts.filter(Boolean).join(' · ')
})

/** 分支名：工序汇报的分支就是那道工序，直接显示工序名称，比「工序汇报单 · 车削」短 */
function branchLabel(b) {
  if (b['面板编码'] === 'PROCESS_REPORT' && b['工序名称']) return b['工序名称']
  return tt(b['环节'])
}

function branchTitle(b) {
  const parts = [b['环节']]
  if (b['工序名称']) parts.push(b['工序名称'])
  parts.push(b['是否已发生'] === false ? tt('待生成') : (b['单号'] || ''))
  return parts.filter(Boolean).join(' · ')
}
</script>

<style scoped>
/* 一列：上面是环节卡片，下面挂分支（树支） */
.rn-col {
  display: flex;
  flex-direction: column;
  flex: 0 0 auto;
}

.rn-node {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 152px;
  padding: 6px 10px;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  background: #fff;
  text-align: left;
  cursor: pointer;
  font-family: inherit;
  flex: 0 0 auto;
  transition: border-color 0.15s, background 0.15s;
}

.rn-node:hover {
  border-color: #1677ff;
  background: #fafcff;
}

.rn-ic {
  color: rgba(0, 0, 0, 0.45);
}

.rn-label {
  font-size: 12px;
  line-height: 18px;
  color: rgba(0, 0, 0, 0.88);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rn-no {
  font-size: 12px;
  line-height: 17px;
  font-family: Consolas, "Courier New", monospace;
  color: rgba(0, 0, 0, 0.65);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rn-status {
  font-size: 11px;
  line-height: 16px;
  color: rgba(0, 0, 0, 0.45);
}

/* 已完成 */
.rn-node.kind-done {
  border-color: #95de64;
  background: #f6ffed;
}

.rn-node.kind-done .rn-ic {
  color: #52c41a;
}

/* 进行中：生产中的工序加流光，扫过卡片表示"正在做"；只澄清状态，不做装饰
   底色用主色 #1677ff 的低透明度带，缓动取 antd motionEaseOut */
.rn-node.running {
  position: relative;
  overflow: hidden;
}

/* 边框流光：一圈主色光沿边框绕行，提示"这道工序正在做"
   用 conic-gradient + 环形遮罩只留下 1px 边线，@property 驱动角度旋转 */
@property --rn-border-angle {
  syntax: '<angle>';
  initial-value: 0deg;
  inherits: false;
}

.rn-node.running::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1px;
  background: conic-gradient(
    from var(--rn-border-angle),
    rgba(22, 119, 255, 0) 0deg,
    rgba(22, 119, 255, 0) 250deg,
    rgba(22, 119, 255, 0.85) 305deg,
    rgba(22, 119, 255, 1) 330deg,
    rgba(22, 119, 255, 0.85) 355deg,
    rgba(22, 119, 255, 0) 360deg
  );
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
          mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
          mask-composite: exclude;
  animation: rn-border-flow 2.6s linear infinite;
  pointer-events: none;
}

@keyframes rn-border-flow {
  to { --rn-border-angle: 360deg; }
}

.rn-node.running::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 55%;
  background: linear-gradient(
    100deg,
    rgba(22, 119, 255, 0) 0%,
    rgba(22, 119, 255, 0.1) 35%,
    rgba(22, 119, 255, 0.26) 50%,
    rgba(22, 119, 255, 0.1) 65%,
    rgba(22, 119, 255, 0) 100%
  );
  transform: translateX(-120%);
  animation: rn-flow 2.6s cubic-bezier(0.215, 0.61, 0.355, 1) infinite;
  pointer-events: none;
}

@keyframes rn-flow {
  0% { transform: translateX(-120%); }
  65%, 100% { transform: translateX(240%); }
}

/* 降低动效偏好：直接静态高亮，不扫光 */
@media (prefers-reduced-motion: reduce) {
  .rn-node.running::before {
    animation: none;
    background: none;
  }
  .rn-node.running::after {
    animation: none;
    transform: none;
    background: linear-gradient(180deg, rgba(22, 119, 255, 0.06), rgba(22, 119, 255, 0.14));
  }
}

/* 进行中 / 卡点 */
.rn-node.kind-doing {
  border-color: #1677ff;
  background: #f0f7ff;
}

.rn-node.kind-doing .rn-ic {
  color: #1677ff;
}

.rn-node.stalled {
  border-color: #faad14;
  background: #fffbe6;
}

.rn-node.stalled .rn-ic {
  color: #faad14;
}

.rn-node.stalled .rn-status {
  color: #d48806;
}

.rn-node.kind-aborted {
  border-color: #ffa39e;
  background: #fff1f0;
}

.rn-node.kind-aborted .rn-ic {
  color: #ff4d4f;
}

/* 待生成：标准路线里有、还没生成单据的环节 */
.rn-node.pending {
  border-style: dashed;
  border-color: #d9d9d9;
  background: #fafafa;
  cursor: default;
}

.rn-node.pending:hover {
  border-color: #d9d9d9;
  background: #fafafa;
}

.rn-node.pending .rn-label {
  color: rgba(0, 0, 0, 0.45);
}

.rn-node.pending .rn-no {
  color: rgba(0, 0, 0, 0.25);
  font-family: inherit;
}

.rn-node.pending .rn-ic {
  color: rgba(0, 0, 0, 0.25);
}

/* 分支环节（首件检验单、排产逐道工序）：树支样式，不占主干步数 */
.rn-branches {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0 0 0 16px;
  padding: 6px 0 0 10px;
  border-left: 1px dashed #b7eb8f;
}

/* 从主干卡片底部连到分支的那一小段竖线 */
.rn-branches::before {
  content: '';
  position: absolute;
  left: -1px;
  top: -6px;
  height: 12px;
  border-left: 1px dashed #b7eb8f;
}

.rn-branch {
  position: relative;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border: 1px dashed #b7eb8f;
  border-radius: 4px;
  background: #f6ffed;
  font-family: inherit;
  font-size: 11px;
  line-height: 16px;
  color: #389e0d;
  cursor: pointer;
  white-space: nowrap;
}

/* 分支连到主干竖线的小横段 */
.rn-branch::before {
  content: '';
  position: absolute;
  left: -11px;
  top: 50%;
  width: 10px;
  border-top: 1px dashed #b7eb8f;
}

.rn-branch:hover {
  border-color: #52c41a;
  background: #f0fff0;
}

.rn-branch-label {
  color: inherit;
}

.rn-branch-no {
  font-family: Consolas, "Courier New", monospace;
  color: rgba(0, 0, 0, 0.55);
}

.rn-branch.pending {
  border-color: #d9d9d9;
  background: #fafafa;
  color: rgba(0, 0, 0, 0.35);
}

.rn-branch.pending::before {
  border-color: #d9d9d9;
}

.rn-branch-sub {
  margin-left: 2px;
  padding: 0 4px;
  border: 1px dashed #b7eb8f;
  border-radius: 3px;
  background: #fff;
  color: #389e0d;
  font-size: 10px;
  line-height: 14px;
}

.rn-branch-sub.pending {
  border-color: #d9d9d9;
  color: rgba(0, 0, 0, 0.35);
}

.rn-branch.pending .rn-branch-no {
  color: rgba(0, 0, 0, 0.25);
  font-family: inherit;
}
</style>
