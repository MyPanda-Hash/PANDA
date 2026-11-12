<template>
  <!-- 左侧「单据列表」栏：镜像当前页单据列表，点行切当前单据。
       2026-11-04 起按「采购订单选择」的信息形式改成表头列式：
       单号 | 日期 | 面板主要字段 | 审核状态，一行一张单；列由 PanelxList 按面板传入
       （doc-rail-rows.js 的 railColumnsOf）。外观仍按 Ant Design 取值：
       白底 + 1px 分隔线、无阴影、控件高 32、圆角 6、8pt 间距、数字 tabular-nums。 -->
  <aside
    v-if="!collapsed"
    ref="railEl"
    class="doc-rail"
    :style="{ width: width + 'px' }"
    :aria-label="tt('单据列表')"
  >
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

    <!-- 表头筛选：跟着单据栏走，不占主区高度（成品报检单按「入库单」状态筛） -->
    <div v-if="filters.length" class="dr-filters">
      <div v-for="f in filters" :key="'df-' + f.dataName" class="dr-filter">
        <span class="dr-filter-label">{{ tt(f.label || f.dataName) }}</span>
        <el-select
          :model-value="filterValues[f.dataName] || ''"
          clearable
          size="small"
          :placeholder="tt(f.placeholder || '全部')"
          @update:model-value="(v) => $emit('update-filter', { dataName: f.dataName, value: v || '' })"
        >
          <el-option v-for="o in f.options" :key="o" :label="tt(o)" :value="o" />
        </el-select>
      </div>
    </div>

    <div class="dr-list" v-loading="loading">
      <table class="dr-table">
        <colgroup>
          <col v-for="c in columns" :key="'col-' + c.key" :class="'dr-col-' + c.kind" />
        </colgroup>
        <thead>
          <tr>
            <th v-for="c in columns" :key="'th-' + c.key" class="dr-th" :class="c.align">
              {{ tt(c.label) }}
            </th>
          </tr>
        </thead>
        <tbody v-if="viewRows.length">
          <tr
            v-for="row in viewRows"
            :key="row.index"
            class="dr-tr"
            :class="{ on: row.no && row.no === currentNo }"
            :title="row.cells.filter(Boolean).join(' · ')"
            @click="$emit('select', row.index)"
          >
            <td
              v-for="(cell, ci) in row.cells"
              :key="'td-' + columns[ci].key"
              class="dr-td"
              :class="[columns[ci].align, 'k-' + columns[ci].kind]"
            >
              <span v-if="columns[ci].kind === 'tag' && cell" class="dr-tag" :class="row.tone">{{ tt(cell) }}</span>
              <template v-else>{{ cell || '-' }}</template>
            </td>
          </tr>
        </tbody>
      </table>
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

    <!-- 右缘拖拽手柄：供应商名/中间列太长时把栏拖宽，表格列同步伸展 -->
    <div class="dr-resizer" :title="tt('拖动调整宽度')" @mousedown.prevent="startDrag"></div>
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
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ArrowLeft, ArrowRight, Search } from '@element-plus/icons-vue'
import { tt } from '@/i18n'
import { railColumnsOf, railPageCount, railRowsOf } from './doc-rail-rows.js'

const props = defineProps({
  /** 当前页单据行（与主列表同一份数据，不额外请求） */
  rows: { type: Array, default: () => [] },
  /** 表头筛选：[{dataName, label, placeholder, options}]，值走 emit('update-filter') */
  filters: { type: Array, default: () => [] },
  /** 当前筛选值：{ [dataName]: value } */
  filterValues: { type: Object, default: () => ({}) },
  /** 列定义：[{label, kind, align, keys}]，由挂载方按面板挑主要表头字段 */
  columns: { type: Array, default: () => [] },
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
const emit = defineEmits(['update-filter', 'select', 'page', 'search', 'toggle', 'create'])

/** 本页内的关键字（本地过滤，不影响后端条件） */
const kw = ref('')
watch(() => props.keyword, (value) => { kw.value = String(value || '') })
watch(() => props.pageNo, () => { kw.value = '' })

/** 挂载方没给列时（独立使用/旧入口）退回「单号/日期/往来单位/状态」 */
const columns = computed(() => (
  Array.isArray(props.columns) && props.columns.length ? props.columns : railColumnsOf('', props.rows)
))
const viewRows = computed(() => railRowsOf(props.rows, kw.value, columns.value))
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

// ---- 拖动调宽：只改本栏宽度，不写 localStorage（每次进页面按默认宽度） ----
// 展开时直接铺到最大宽度：单据栏默认收起，点开就是「要翻单据」的意图，
// 420 那种半宽仍会把单号/状态挤成截断；拖拽只在展开后用来收窄。
const MIN_WIDTH = 340
const MAX_WIDTH = 560
const DEFAULT_WIDTH = 420
const width = ref(DEFAULT_WIDTH)
const railEl = ref(null)
let dragging = false

// 从「收起」点开时直接给最大宽度（不用再拖一下）；展开后再拖拽收窄仍然有效
watch(() => props.collapsed, (collapsed) => {
  if (!collapsed) width.value = MAX_WIDTH
})

function onDragMove(e) {
  if (!dragging) return
  const left = railEl.value?.getBoundingClientRect().left ?? 0
  width.value = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(e.clientX - left)))
}

function stopDrag() {
  if (!dragging) return
  dragging = false
  document.removeEventListener('mousemove', onDragMove)
  document.removeEventListener('mouseup', stopDrag)
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
}

function startDrag() {
  dragging = true
  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'col-resize'
  document.addEventListener('mousemove', onDragMove)
  document.addEventListener('mouseup', stopDrag)
}

onBeforeUnmount(stopDrag)
</script>

<style scoped>
/* ── 外观取值（Ant Design v6 token → 本项目 CSS 变量）──────────────
   容器    白底 + 右侧 1px 分隔线，无阴影（0 层卡片规则）
   控件高  32（标题行 40 = 32 + 8，输入框 32，行高 40，翻页按钮 24 = controlHeightSM）
   圆角    6（容器/输入/按钮）、4（状态标签 borderRadiusSM）
   间距    8 / 16（8pt 网格）
   字号    标题 14/22，表头 12，单元格 12，次要信息 12（fontSizeSM）
   数字    等宽数字 tabular-nums（单号、日期、页码）
   选中    主色浅底（对应 colorPrimaryBg）+ 左侧 2px 主色条
   悬停    rgba(0,0,0,0.02)（colorFillQuaternary）
   分隔    #f0f0f0（colorSplit）
   ─────────────────────────────────────────────────────────── */
.doc-rail {
  position: relative;
  flex: none;
  display: flex;
  flex-direction: column;
  min-width: 0;
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

/* 表头列式：table-layout fixed 保证列宽稳定。
   列宽：单号 32% / 日期 18% / 状态 16% / 业务列分剩余 34%。
   业务列（客户、供应商、车间…）不截断：允许折行显示全称，
   单号/日期/状态仍单行省略——这三列值长度稳定，截断不会丢信息。 */
/* 单据栏里的筛选排：一行一个，栏窄也能读 */
.dr-filters {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0 8px 8px;
  border-bottom: 1px solid var(--t-border-light, #f0f0f0);
}
.dr-filter {
  display: flex;
  align-items: center;
  gap: 8px;
}
.dr-filter-label {
  flex: none;
  font-size: 12px;
  color: var(--t-text-2, #5d6c67);
  white-space: nowrap;
}
.dr-filter :deep(.el-select) {
  flex: 1;
  min-width: 0;
}

.dr-table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
}
.dr-col-no { width: 30%; }
.dr-col-date { width: 16%; }
.dr-col-tag { width: 20%; }

.dr-th {
  position: sticky;
  top: 0;
  z-index: 1;
  height: 30px;
  padding: 0 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  background: var(--t-content-bg, #fafafa);
  border-bottom: 1px solid var(--t-border-light, #f0f0f0);
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  color: var(--t-text-3, rgba(0, 0, 0, 0.45));
}
.dr-th.left { text-align: left; }
.dr-th.center { text-align: center; }

.dr-tr {
  height: 40px;
  border-bottom: 1px solid var(--t-border-light, #f0f0f0);
  cursor: pointer;
}
/* 业务列折行时行高跟着长，不裁字 */
.dr-tr:hover {
  background: var(--el-fill-color-lighter, rgba(0, 0, 0, 0.02));
}
.dr-tr.on {
  background: var(--t-primary-tint, #e8f1ef);
}
/* 选中行左侧 2px 主色条（画在首格内，避免 border 改列宽） */
.dr-tr.on .dr-td:first-child {
  box-shadow: inset 2px 0 0 var(--t-primary, #116a5b);
}

.dr-td {
  padding: 0 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  line-height: 20px;
  color: var(--t-text-1, rgba(0, 0, 0, 0.88));
}
.dr-td.left { text-align: left; }
.dr-td.center { text-align: center; }
.dr-td.k-no {
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
}
.dr-td.k-date {
  font-variant-numeric: tabular-nums;
  color: var(--t-text-2, rgba(0, 0, 0, 0.65));
}
.dr-td.k-text {
  color: var(--t-text-2, rgba(0, 0, 0, 0.65));
  /* 客户/供应商/车间这类名称要显示全：允许折行，不再省略号截断 */
  white-space: normal;
  word-break: break-word;
  line-height: 16px;
  padding-top: 4px;
  padding-bottom: 4px;
  vertical-align: middle;
}
.dr-tr.on .dr-td.k-no {
  font-weight: 500;
  color: var(--t-primary-dark, #0d584c);
}

/* 状态标签：12 号 / 圆角 4 / 1px 边框 / 行高 18（与 Tag 同密度） */
.dr-tag {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: middle;
  padding: 0 5px;
  border-radius: 4px;
  border: 1px solid transparent;
  font-size: 11px;
  line-height: 16px;
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

/* 右缘拖拽手柄：仅改变栏宽 */
.dr-resizer {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 5px;
  cursor: col-resize;
  z-index: 2;
}
.dr-resizer:hover {
  background: var(--t-primary-tint-border, #b8d3ce);
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
