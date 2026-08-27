<template>
  <div class="stree">
    <ul v-if="data.length" class="tree-root">
      <li v-for="(n, i) in data" :key="i" class="tn">
        <div class="tl" :class="{ 'is-root': n.children && n.children.length }">
          <el-icon v-if="n.children && n.children.length" class="tl-ic"><Box /></el-icon>
          <span class="tl-name" :title="n.label">{{ n.label }}</span>
          <span v-if="n.meta" class="tl-meta">{{ n.meta }}</span>
        </div>
        <ul v-if="n.children && n.children.length" class="tree-child">
          <li v-for="(c, j) in n.children" :key="j" class="tn">
            <div class="tl">
              <el-icon class="tl-ic leaf"><Goods /></el-icon>
              <span class="tl-name" :title="c.label">{{ c.label }}</span>
              <span v-if="c.meta" class="tl-meta">{{ c.meta }}</span>
            </div>
          </li>
        </ul>
      </li>
    </ul>
    <div v-else class="chart-empty">暂无数据</div>
  </div>
</template>

<script setup>
defineProps({
  data: { type: Array, default: () => [] },
})
</script>

<style scoped>
.tree-root {
  list-style: none;
  margin: 0;
  padding: 2px 0;
}
.tree-child {
  list-style: none;
  margin: 0;
  padding-left: 26px;
  border-left: 1px dashed var(--t-border);
  margin-left: 13px;
}
.tn {
  margin: 3px 0;
}
.tl {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 8px;
  font-size: 13px;
  background: var(--t-card-bg);
  border: 1px solid var(--t-border-light);
}
.tl.is-root {
  background: var(--t-hover-bg);
  border-color: transparent;
  font-weight: 600;
}
.tl-ic {
  font-size: 15px;
  color: #b87816;
  flex-shrink: 0;
}
.tl-ic.leaf {
  color: #116a5b;
  font-size: 13px;
}
.tl-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--t-text-1);
}
.tl-meta {
  font-size: 12px;
  color: var(--t-text-3);
  flex-shrink: 0;
}
.chart-empty {
  color: var(--t-text-3);
  font-size: 12px;
  text-align: center;
  padding: 30px 0;
}
</style>
