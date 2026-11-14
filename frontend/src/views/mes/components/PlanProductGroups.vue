<!-- PlanProductGroups.vue — 排产看板的产品分区表
     独立页 /mes/plan 与「待排产」列表里的排产弹窗共用这一份渲染，
     否则同一张加工单在两个入口里长得不一样（一处分区、一处平铺）。
     0 层用 1px 边框不用阴影，圆角 6，块间距 16（8pt 网格）。 -->
<template>
  <div class="plb-groups">
    <section v-for="g in groups" :key="g.key" class="plb-group">
      <header class="plb-group-head">
        <span class="plb-prod-code">{{ g.productCode || '—' }}</span>
        <span class="plb-prod-name">{{ g.productName }}</span>
        <span v-if="g.spec" class="plb-prod-spec">{{ g.spec }}</span>
        <span class="plb-stats">
          <span class="plb-stat"><i>计划</i><b>{{ qty(g.planQty) }}</b></span>
          <span class="plb-stat"><i>已排</i><b>{{ qty(g.plannedQty) }}</b></span>
          <span class="plb-stat"><i>可排</i>
            <b :class="g.availableQty > 0 ? 'ok' : 'zero'">{{ qty(g.availableQty) }}</b>
          </span>
        </span>
        <el-tag size="small" :type="g.tagType" effect="light">{{ g.stateLabel }}</el-tag>
      </header>
      <el-table :data="g.rows" size="small" border class="plb-group-table" :max-height="maxHeight">
        <el-table-column label="序" prop="工序顺序" width="52" align="center" />
        <el-table-column label="工序" width="120">
          <template #default="{ row }">{{ row['工序名称'] || row['工序编码'] }}</template>
        </el-table-column>
        <el-table-column label="车间" prop="生产车间" width="106" />
        <el-table-column label="计划" width="84" align="right">
          <template #default="{ row }">{{ qty(row['计划数量']) }}</template>
        </el-table-column>
        <el-table-column label="上道合格" width="96" align="right">
          <template #default="{ row }">{{ qty(row['上道合格量']) }}</template>
        </el-table-column>
        <el-table-column label="已排产" width="90" align="right">
          <template #default="{ row }">{{ qty(row['已排产量']) }}</template>
        </el-table-column>
        <el-table-column label="可排产量" width="100" align="right">
          <template #default="{ row }">
            <b :class="Number(row['可排产量']) > 0 ? 'ok' : 'zero'">{{ qty(row['可排产量']) }}</b>
          </template>
        </el-table-column>
        <el-table-column label="已建批次" min-width="220">
          <template #default="{ row }">
            <div v-if="row['批次'] && row['批次'].length" class="batches">
              <el-tag
                v-for="(b, i) in row['批次']"
                :key="i"
                size="small"
                type="info"
                class="batch is-clickable"
                effect="plain"
                @click="$emit('batch', b)"
              >
                {{ b['排产单号'] }} · {{ qty(b['数量']) }}
                <span v-if="b['班组']"> · {{ b['班组'] }}</span>
                <span v-if="b['设备']"> · {{ b['设备'] }}</span>
              </el-tag>
            </div>
            <span v-else class="dim">尚未排产</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="102" align="center">
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="Number(row['可排产量']) <= 0 ? 'info' : (Number(row['已排产量']) > 0 ? 'warning' : 'success')"
              effect="light"
            >
              {{ Number(row['可排产量']) <= 0 ? '已排满' : (Number(row['已排产量']) > 0 ? '部分排产' : '未排产') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" align="center">
          <template #default="{ row }">
            <el-button v-if="Number(row['可排产量']) > 0" link type="primary" size="small" @click="$emit('plan', row)">排产</el-button>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </div>
</template>

<script setup>
defineProps({
  /** groupByProduct() 的结果：每块含产品头信息与自己的工序行 */
  groups: { type: Array, default: () => [] },
  /** 弹窗里给表体限高，页面里交给整页滚动 */
  maxHeight: { type: [Number, String], default: undefined },
})
defineEmits(['plan', 'batch'])

function qty(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '0'
  return n === Math.round(n) ? String(n) : n.toFixed(2)
}
</script>

<style scoped>
.plb-groups {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-bottom: 16px;
}

.plb-group {
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  overflow: hidden;
  background: #fff;
}

.plb-group-head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 10px 12px;
  background: #fafafa;
  border-bottom: 1px solid #d9d9d9;
}

.plb-prod-code {
  font-family: Consolas, "Courier New", monospace;
  font-size: 13px;
  font-weight: 600;
  color: rgba(0, 0, 0, 0.88);
}

.plb-prod-name {
  font-size: 14px;
  line-height: 22px;
  color: rgba(0, 0, 0, 0.88);
}

.plb-prod-spec {
  font-size: 12px;
  line-height: 20px;
  color: rgba(0, 0, 0, 0.45);
}

.plb-stats {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-left: auto;
}

.plb-stat {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  font-size: 12px;
  line-height: 20px;
}

.plb-stat i {
  font-style: normal;
  color: rgba(0, 0, 0, 0.45);
}

.plb-stat b {
  font-size: 14px;
  font-variant-numeric: tabular-nums;
  color: rgba(0, 0, 0, 0.88);
}

.plb-stat b.ok {
  color: var(--t-primary);
}

.plb-stat b.zero {
  color: rgba(0, 0, 0, 0.25);
}

/* 分组表的表头与卡片边对齐，不再套第二层边框 */
.plb-group-table {
  border: none;
}

.batches { display: flex; flex-wrap: wrap; gap: 4px; }
.batch { font-variant-numeric: tabular-nums; }
.batch.is-clickable { cursor: pointer; }
.batch.is-clickable:hover { color: var(--t-primary); border-color: var(--t-primary-tint-border); background: var(--t-primary-tint); }
.ok { color: var(--t-primary); font-variant-numeric: tabular-nums; }
.zero { color: rgba(0, 0, 0, 0.45); }
.dim { color: #bfbfbf; }
:deep(.el-table) { font-size: 13px; }
:deep(.el-table th.el-table__cell) { background: #fafafa; color: #595959; font-weight: 500; }
:deep(.el-table .cell) { font-variant-numeric: tabular-nums; }
</style>
