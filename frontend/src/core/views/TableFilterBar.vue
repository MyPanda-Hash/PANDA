<!-- TableFilterBar.vue — 表格筛选栏（分类 + 模糊搜索 + 字段筛选 + 重置）
     设计依据：antd design-kit 列表页模板「筛选区在最上方，按总体→具体浏览」。
     数值：控件高 32 / 圆角 6 / 正文 14·22 / 次要字 rgba(0,0,0,.45) / 主色 #1677ff / 边框 #d9d9d9。
     用途：所有带表格的面板共用这一条，避免每个面板各写一套查询框。 -->
<template>
  <div class="tfb">
    <!-- 分类：按某一列的取值分段，带条数；「全部」常驻 -->
    <div v-if="categoryProp && categories.length" class="tfb-cats">
      <button
        type="button"
        class="tfb-chip"
        :class="{ on: !categoryValue }"
        @click="pickCategory('')"
      >
        全部<span v-if="showCounts" class="tfb-num">{{ rows.length }}</span>
      </button>
      <button
        v-for="c in categories"
        :key="c.value"
        type="button"
        class="tfb-chip"
        :class="{ on: categoryValue === c.value }"
        @click="pickCategory(c.value)"
      >
        {{ c.label }}<span v-if="showCounts" class="tfb-num">{{ c.count }}</span>
      </button>
    </div>

    <div class="tfb-right">
      <!-- 附加筛选：按类别分组的下拉（如「成品仓」下有 CK03 三个仓可选） -->
      <el-select
        v-for="sel in extraSelects"
        :key="sel.prop"
        :model-value="filters[sel.prop] || ''"
        class="tfb-select"
        size="small"
        clearable
        filterable
        :placeholder="sel.placeholder || sel.label"
        @update:model-value="(v) => pickExtra(sel.prop, v)"
      >
        <el-option-group v-for="g in sel.groups" :key="g.label" :label="g.label">
          <el-option v-for="o in g.options" :key="o.value" :label="o.label" :value="o.value" />
        </el-option-group>
        <template v-if="!sel.groups.length">
          <el-option v-for="o in sel.options || []" :key="o.value" :label="o.label" :value="o.value" />
        </template>
      </el-select>
      <el-input
        v-model="keywordModel"
        class="tfb-search"
        size="small"
        clearable
        :prefix-icon="Search"
        :placeholder="searchPlaceholder"
      />
      <el-button size="small" :icon="Filter" @click="emit('open-filters')">
        字段筛选
        <span v-if="filterCount" class="tfb-badge">{{ filterCount }}</span>
      </el-button>
      <el-button v-if="dirty" link type="primary" size="small" @click="reset">重置</el-button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Filter, Search } from '@element-plus/icons-vue'
import { tt } from '@/i18n'

const props = defineProps({
  /** 明细/列表行，用来统计分类条数 */
  rows: { type: Array, default: () => [] },
  /** 参与模糊搜索的列名（不给就搜所有标量字段） */
  searchProps: { type: Array, default: () => [] },
  /** 分类列名；为空则不显示分类 */
  categoryProp: { type: String, default: '' },
  /** 分类取值的显示名映射（如 所属类别 → 中文） */
  categoryLabels: { type: Object, default: () => ({}) },
  /** 分类字典（面板字段声明的选项）；给了就按它出全部分类按钮，没数据也显示 0 */
  categoryOptions: { type: Array, default: () => [] },
  keyword: { type: String, default: '' },
  /** 字段筛选条件：{ 列名: 筛选词 } */
  filters: { type: Object, default: () => ({}) },
  /** 搜索框提示里用的字段名，如「存货编码 / 存货名称」 */
  searchHint: { type: String, default: '' },
  /**
   * 附加下拉筛选，每项形如
   * { prop, label, placeholder, groups: [{ label, options: [{ value, label }] }] }
   * 值与 filters 共用一份状态（分类 chip 也一样），清空即取消该条件。
   */
  extraSelects: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:keyword', 'update:filters', 'open-filters'])

const keywordModel = computed({
  get: () => props.keyword,
  set: (v) => emit('update:keyword', String(v ?? '')),
})

const categoryValue = computed(() => (props.categoryProp ? props.filters[props.categoryProp] || '' : ''))

/**
 * 分类选项。
 *
 * 以**字典**（面板字段的选项）为准，再并上本页实际出现的值：
 * 后台新加一个「半成品」类别但暂时没有数据时，按钮也要在，否则没法按它筛。
 * 顺序按字典声明顺序，字典没有的（脏数据）排后面。
 */
const categories = computed(() => {
  const prop = props.categoryProp
  if (!prop) return []
  const counter = new Map()
  for (const row of props.rows || []) {
    const raw = row ? row[prop] : ''
    if (raw === null || raw === undefined || String(raw).trim() === '') continue
    const value = String(raw)
    counter.set(value, (counter.get(value) || 0) + 1)
  }
  const declared = (props.categoryOptions || [])
    .map((o) => String(o?.value ?? o ?? '').trim())
    .filter(Boolean)
  const ordered = []
  const seen = new Set()
  for (const value of declared) {
    if (seen.has(value)) continue
    seen.add(value)
    ordered.push({ value, count: counter.get(value) || 0, label: props.categoryLabels[value] || value })
  }
  for (const [value, count] of counter) {
    if (seen.has(value)) continue
    ordered.push({ value, count, label: props.categoryLabels[value] || value })
  }
  return ordered
})

const filterCount = computed(() =>
  Object.values(props.filters || {}).filter((v) => v !== null && v !== undefined && String(v).trim() !== '').length)

const dirty = computed(() => !!String(props.keyword || '').trim() || filterCount.value > 0)

/**
 * 计数只在「未筛选」时显示。
 * 服务端筛选后 rows 只剩结果集，其它类别的条数会变成 0 —— 那是假数字，不如不显示。
 */
const showCounts = computed(() => !dirty.value)

const searchPlaceholder = computed(() =>
  props.searchHint ? `${tt('搜索')} ${props.searchHint}` : `${tt('搜索关键字')}`)

/** 附加下拉写值：与分类/字段筛选共用 filters */
function pickExtra(prop, value) {
  const next = { ...props.filters }
  if (!value) delete next[prop]
  else next[prop] = value
  emit('update:filters', next)
}

/** 点分类 = 往 filters 里写该列条件（与字段筛选同一份状态，列头也能看到） */
function pickCategory(value) {
  const next = { ...props.filters }
  if (!value) delete next[props.categoryProp]
  else next[props.categoryProp] = value
  emit('update:filters', next)
}

function reset() {
  emit('update:keyword', '')
  emit('update:filters', {})
}
</script>

<style scoped>
.tfb {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 0;
}
.tfb-cats {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}
/* 分类 chip：未选中白底细边，选中主色；高度 28 与表格 size=small 的节奏一致 */
.tfb-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 12px;
  border: 1px solid var(--d-border-strong);
  border-radius: 6px;
  background: #fff;
  color: rgba(0, 0, 0, 0.88);
  font-size: 13px;
  line-height: 20px;
  cursor: pointer;
  transition: border-color 0.12s ease, color 0.12s ease;
}
.tfb-chip:hover {
  border-color: var(--t-primary);
  color: var(--t-primary);
}
.tfb-chip.on {
  border-color: var(--t-primary);
  background: var(--t-primary);
  color: #fff;
}
.tfb-num {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  opacity: 0.55;
}
.tfb-chip.on .tfb-num {
  opacity: 0.85;
}
.tfb-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}
.tfb-search {
  width: 240px;
}
.tfb-select {
  width: 168px;
}
.tfb-badge {
  margin-left: 4px;
  padding: 0 5px;
  border-radius: 8px;
  background: var(--t-primary);
  color: #fff;
  font-size: 11px;
  line-height: 16px;
  font-variant-numeric: tabular-nums;
}

@media (max-width: 768px) {
  .tfb-right {
    width: 100%;
  }
  .tfb-search {
    flex: 1;
    width: auto;
  }
}
</style>
