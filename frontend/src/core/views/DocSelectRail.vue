<template>
  <!-- 左侧「单据选择」栏：镜像当前页单据列表，点行切当前单据。
       外观按 Ant Design 的取值重做（见下方 style 注释）：白底 + 1px 分隔线、无阴影、
       控件高 32、圆角 6、8pt 间距、数字 tabular-nums、状态标签 12 号。 -->
  <aside v-if="!collapsed" class="doc-rail" :aria-label="tt('单据列表')">
    <header class="dr-head">
      <span class="dr-title">{{ tt('单据列表') }}</span>
      <span class="dr-count">{{ total }}</span>
      <button type="button" class="dr-icon-btn" :title="tt('收起单据栏')" @click="$emit('toggle')">
        <el-icon><ArrowLeft /></el-icon>
      </button>
    </header>

    <div class="dr-search">
      <el-input
        v-model="kw"
        :prefix-icon="Search"
        clearable
        :placeholder="tt('搜索单号 / 客户')"
        @keyup.enter="searchAll"
        @clear="clearSearch"
      />
      <button type="button" class="dr-text-btn" :title="tt('按关键字重查整个列表')" @click="searchAll">
        {{ tt('全库') }}
      </button>
    </div>

    <div class="dr-list" v-loading="loading">
      <button
        v-for="row in viewRows"
        :key="row.index"
        type="button"
        class="dr-row"
        :class="{ on: row.no && row.no === currentNo }"
        :title="[row.no, row.partner, row.date, row.status].filter(Boolean).join(' · ')"
        @click="$emit('select', row.index)"
      >
        <span class="dr-row-1">
          <span class="dr-no">{{ row.no || '-' }}</span>
          <span v-if="row.status" class="dr-tag" :class="row.tone">{{ tt(row.status) }}</span>
        </span>
        <span class="dr-row-2">
          <span class="dr-date">{{ row.date }}</span>
          <span v-if="row.partner" class="dr-partner">{{ row.partner }}</span>
        </span>
      </button>
      <div v-if="!viewRows.length" class="dr-empty">
        <span class="dr-empty-text">{{ keyword ? tt('本页没有匹配的单据') : tt('还没有单据') }}</span>
        <button v-if="keyword" type="button" class="dr-text-btn" @click="clearSearch">{{ tt('清除搜索') }}</button>
        <button v-else type="button" class="dr-text-btn primary" @click="$emit('create')">{{ tt('新建单据') }}</button>
      </div>
    </div>

    <footer class="dr-foot">
      <button type="button" class="dr-icon-btn" :disabled="pageNo <= 1" :title="tt('上一页')" @click="$emit('page', pageNo - 1)">
        <el-icon><ArrowLeft /></el-icon>
      </button>
      <span class="dr-page">{{ tt('第') }} {{ pageNo }}/{{ pages }} {{ tt('页') }}</span>
      <button type="button" class="dr-icon-btn" :disabled="pageNo >= pages" :title="tt('下一页')" @click="$emit('page', pageNo + 1)">
        <el-icon><ArrowRight /></el-icon>
      </button>
      <span class="dr-per">{{ tt('每页') }} {{ pageSize }} {{ tt('张') }}</span>
    </footer>
  </aside>

  <button
    v-else
    type="button"
    class="doc-rail-collapsed"
    :title="tt('展开单据列表')"
    @click="$emit('toggle')"
  >
    <el-icon><ArrowRight /></el-icon>
    <span class="dr-coll-text">{{ total }}</span>
  </button>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ArrowLeft, ArrowRight, Search } from '@element-plus/icons-vue'
import { tt } from '@/i18n'
import { railPageCount, railRowsOf } from './doc-rail-rows.js'

const props = defineProps({
  /** 当前页单据行（与主列表同一份数据，不额外请求） */
  rows: { type: Array, default: () => [] },
  /** 当前单据编号（高亮行） */
  currentNo: { type: String, default: '' },
  total: { type: Number, default: 0 },
  pageNo: { type: Number, default: 1 },
  pageSize: { type: Number, default: 20 },
  /** 已生效的服务端关键字（父组件是唯一真源） */
  keyword: { type: String, default: '' },
  collapsed: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'page', 'search', 'toggle', 'create'])

/** 本页内的关键字（本地过滤，不影响后端条件） */
const kw = ref('')
watch(() => props.keyword, (value) => { kw.value = String(value || '') })
watch(() => props.pageNo, () => { kw.value = '' })

const viewRows = computed(() => railRowsOf(props.rows, kw.value))
const pages = computed(() => railPageCount(props.total, props.pageSize))

/**
 * 两种搜索，各有各的用途，界面上只占一行：
 * - 打字：只过滤**本页**（瞬时、不发请求），够用于"这一页里找那张单"；
 * - 回车 / 点「全库」：关键字交给后端重查整个列表（父组件回到第 1 页）。
 */
function searchAll() {
  emit('search', String(kw.value || '').trim())
}

function clearSearch() {
  kw.value = ''
  emit('search', '')
}
</script>

<style scoped>
/* ── 外观取值（Ant Design v6 token → 本项目 CSS 变量）──────────────
   容器    白底 + 右侧 1px 分隔线，无阴影（0 层卡片规则）
   控件高  32（标题行 40 = 32 + 8，输入框 32，行高 32，翻页按钮 24 = controlHeightSM）
   圆角    6（容器/输入/按钮）、4（状态标签 borderRadiusSM）
   间距    8 / 16（8pt 网格）
   字号    标题 14/22，单号 13，次要信息 12（fontSizeSM）
   数字    等宽数字 tabular-nums（单号、页码）
   选中    主色浅底（对应 colorPrimaryBg）+ 左侧 2px 主色条
   悬停    rgba(0,0,0,0.02)（colorFillQuaternary）
   分隔    #f0f0f0（colorSplit）
   ─────────────────────────────────────────────────────────── */
.doc-rail {
  width: 264px;
  flex: none;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--t-card-bg, #fff);
  border-right: 1px solid var(--t-border-light, #f0f0f0);
}

.dr-head {
  height: 40px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 8px 0 16px;
  border-bottom: 1px solid var(--t-border-light, #f0f0f0);
}
.dr-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--t-text-1, rgba(0, 0, 0, 0.88));
}
.dr-count {
  padding: 0 6px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 20px;
  font-variant-numeric: tabular-nums;
  color: var(--t-text-2, rgba(0, 0, 0, 0.65));
  background: var(--t-content-bg, rgba(0, 0, 0, 0.04));
}
.dr-head .dr-icon-btn {
  margin-left: auto;
}

.dr-icon-btn {
  width: 24px;
  height: 24px;
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: var(--t-text-2, rgba(0, 0, 0, 0.65));
  cursor: pointer;
}
.dr-icon-btn:hover:not(:disabled) {
  border-color: var(--t-border, #d9d9d9);
  color: var(--t-primary, #116a5b);
}
.dr-icon-btn:disabled {
  color: var(--t-text-3, rgba(0, 0, 0, 0.25));
  cursor: not-allowed;
}

.dr-search {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--t-border-light, #f0f0f0);
}
.dr-text-btn {
  flex: none;
  padding: 0;
  border: 0;
  background: transparent;
  font-size: 12px;
  line-height: 20px;
  color: var(--t-primary, #116a5b);
  cursor: pointer;
}
.dr-text-btn:disabled {
  color: var(--t-text-3, rgba(0, 0, 0, 0.25));
  cursor: not-allowed;
}
.dr-text-btn.primary {
  font-weight: 500;
}

.dr-search :deep(.el-input) {
  flex: 1;
  min-width: 0;
}
/* 输入框按 32 高 / 圆角 6 / 边框 #d9d9d9（controlHeight / borderRadius / colorBorder） */
.dr-search :deep(.el-input__wrapper) {
  border-radius: 6px;
  box-shadow: 0 0 0 1px var(--t-border, #d9d9d9) inset;
}
.dr-search :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px var(--t-primary-tint-border, #b8d3ce) inset;
}

.dr-list {
  flex: 1 1 0;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}
.dr-row {
  width: 100%;
  height: 44px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
  padding: 4px 16px;
  border: 0;
  border-bottom: 1px solid var(--t-border-light, #f0f0f0);
  border-left: 2px solid transparent;
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.dr-row:hover {
  background: var(--el-fill-color-lighter, rgba(0, 0, 0, 0.02));
}
.dr-row.on {
  background: var(--t-primary-tint, #e8f1ef);
  border-left-color: var(--t-primary, #116a5b);
}
/* 第一行：单号 + 状态（单号优先占满，永远不被日期/标签挤到省略号） */
.dr-row-1,
.dr-row-2 {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.dr-no {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  line-height: 20px;
  font-variant-numeric: tabular-nums;
  color: var(--t-text-1, rgba(0, 0, 0, 0.88));
}
.dr-row.on .dr-no {
  font-weight: 500;
  color: var(--t-primary-dark, #0d584c);
}
/* 第二行：日期 + 往来单位（12 号次要信息，单位过长省略） */
.dr-row-2 {
  font-size: 12px;
  line-height: 16px;
  color: var(--t-text-3, rgba(0, 0, 0, 0.45));
}
.dr-date {
  flex: none;
  font-variant-numeric: tabular-nums;
}
.dr-partner {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 状态标签：12 号 / 圆角 4 / 1px 边框 / 行高 20（与 Tag 同密度） */
.dr-tag {
  flex: none;
  padding: 0 6px;
  border-radius: 4px;
  border: 1px solid transparent;
  font-size: 12px;
  line-height: 18px;
  white-space: nowrap;
}
.dr-tag.ok {
  color: var(--t-primary-dark, #0d584c);
  border-color: var(--t-primary-tint-border, #b8d3ce);
  background: var(--t-primary-tint, #e8f1ef);
}
.dr-tag.done {
  color: #4338ca;
  border-color: #c7d2fe;
  background: #eef2ff;
}
.dr-tag.pending {
  color: #8a6d1f;
  border-color: #e6d3a3;
  background: #fbf5e6;
}
.dr-tag.danger {
  color: #a83b2f;
  border-color: #eec3bc;
  background: #fdf1ef;
}
.dr-tag.draft {
  color: var(--t-text-2, rgba(0, 0, 0, 0.65));
  border-color: var(--t-border, #d9d9d9);
  background: var(--t-content-bg, rgba(0, 0, 0, 0.02));
}

.dr-empty {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  padding: 16px;
}
.dr-empty-text {
  font-size: 12px;
  color: var(--t-text-3, rgba(0, 0, 0, 0.45));
}

.dr-foot {
  height: 40px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 16px;
  border-top: 1px solid var(--t-border-light, #f0f0f0);
}
.dr-page {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--t-text-2, rgba(0, 0, 0, 0.65));
}
.dr-per {
  margin-left: auto;
  font-size: 12px;
  color: var(--t-text-3, rgba(0, 0, 0, 0.45));
}

/* 收起态：32px 竖条，只留一个展开按钮与张数 */
.doc-rail-collapsed {
  width: 32px;
  flex: none;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
  border: 0;
  border-right: 1px solid var(--t-border-light, #f0f0f0);
  background: var(--t-card-bg, #fff);
  color: var(--t-text-3, rgba(0, 0, 0, 0.45));
  cursor: pointer;
}
.doc-rail-collapsed:hover {
  color: var(--t-primary, #116a5b);
}
.dr-coll-text {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  writing-mode: vertical-rl;
}

/* 窄屏（<1024）不显示单据栏：明细区宽度优先，翻页仍在顶栏 */
@media (max-width: 1023px) {
  .doc-rail,
  .doc-rail-collapsed {
    display: none;
  }
}
</style>
