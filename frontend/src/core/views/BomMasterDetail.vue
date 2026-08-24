<!-- BomMasterDetail.vue — 物料清单「父件表格 + 子件表格」联动视图（BOM/BOM_FWD/BOM_REV 共用）
     数据：rows = 父件-子件对（BOM 面板 detail.children 或后端展平行）
     正向（默认）：上方父件表格（按父件编码去重），点击父件 → 下方子件表格联动
     反向（reverse）：上方子件表格（按子件编码去重），点击子件 → 下方显示其父件 -->
<template>
  <div class="bom-md">
    <!-- ══════════ 主表（父件/子件） ══════════ -->
    <div class="bom-md-sec">
      <div class="bom-md-head">
        <span class="bom-md-title">{{ reverse ? '子件（物料/原材料）' : '父件（产成品/物料）' }}</span>
        <span class="bom-md-count">共 {{ masters.length }} 项</span>
      </div>
      <el-table
        :data="masters"
        border
        size="small"
        height="230"
        highlight-current-row
        :row-class-name="masterRowCls"
        @row-click="onMasterClick"
        v-loading="loading"
      >
        <el-table-column type="index" label="序号" width="60" align="center" :index="(i) => i + 1" />
        <template v-if="!reverse">
          <el-table-column prop="父件编码" label="父件编码" min-width="110" show-overflow-tooltip />
          <el-table-column prop="父件名称" label="父件名称" min-width="150" show-overflow-tooltip />
          <el-table-column prop="版本号" label="版本号" width="90" show-overflow-tooltip />
          <el-table-column prop="默认BOM" label="默认BOM" width="90" align="center">
            <template #default="{ row }">{{ fmtBool(row['默认BOM']) }}</template>
          </el-table-column>
          <el-table-column prop="计量单位" label="计量单位" width="90" />
          <el-table-column prop="生产数量" label="生产数量" width="90" />
          <el-table-column prop="生产车间" label="生产车间" min-width="110" show-overflow-tooltip />
          <el-table-column prop="虚拟件" label="虚拟件" width="80" align="center">
            <template #default="{ row }">{{ fmtBool(row['虚拟件']) }}</template>
          </el-table-column>
        </template>
        <template v-else>
          <el-table-column prop="子件编码" label="子件编码" min-width="110" show-overflow-tooltip />
          <el-table-column prop="子件名称" label="子件名称" min-width="150" show-overflow-tooltip />
          <el-table-column prop="规格型号" label="规格型号" min-width="120" show-overflow-tooltip />
          <el-table-column prop="子件计量单位" label="单位" width="90" />
        </template>
      </el-table>
    </div>

    <!-- ══════════ 从表（选中父件的子件 / 选中子件的父件） ══════════ -->
    <div class="bom-md-sec">
      <div class="bom-md-head">
        <span class="bom-md-title">
          {{ reverse ? '父件（该子件被用于）' : '子件' }}：{{ curMasterLabel }}
        </span>
        <span class="bom-md-count">共 {{ curRows.length }} 项</span>
      </div>
      <el-table :data="curRows" border size="small" height="260">
        <el-table-column type="index" label="序号" width="60" align="center" :index="(i) => i + 1" />
        <template v-if="!reverse">
          <el-table-column prop="子件编码" label="子件编码" min-width="110" show-overflow-tooltip />
          <el-table-column prop="子件名称" label="子件名称" min-width="150" show-overflow-tooltip />
          <el-table-column prop="规格型号" label="规格型号" min-width="120" show-overflow-tooltip />
          <el-table-column prop="子件计量单位" label="单位" width="90" />
          <el-table-column prop="定额数量" label="定额数量" width="90" align="right" />
          <el-table-column prop="损耗率%" label="损耗率%" width="90" align="right" />
          <el-table-column prop="需用数量" label="需用数量" width="90" align="right" />
          <el-table-column prop="备注" label="备注" min-width="140" show-overflow-tooltip />
        </template>
        <template v-else>
          <el-table-column prop="父件编码" label="父件编码" min-width="110" show-overflow-tooltip />
          <el-table-column prop="父件名称" label="父件名称" min-width="150" show-overflow-tooltip />
          <el-table-column prop="版本号" label="版本号" width="100" />
          <el-table-column prop="默认BOM" label="默认BOM" width="90" align="center">
            <template #default="{ row }">{{ fmtBool(row['默认BOM']) }}</template>
          </el-table-column>
          <el-table-column prop="计量单位" label="计量单位" width="90" />
          <el-table-column prop="生产数量" label="生产数量" width="90" align="right" />
        </template>
      </el-table>
      <div v-if="!curRows.length" class="bom-md-empty">请选择上方 {{ reverse ? '子件' : '父件' }} 查看对应明细</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  reverse: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})

const masterKey = computed(() => (props.reverse ? '子件编码' : '父件编码'))

// 主表：按 父件编码/子件编码 去重（保留首行作为父件/子件信息）
const masters = computed(() => {
  const seen = new Map()
  for (const r of props.rows || []) {
    const k = r[masterKey.value]
    if (!k || seen.has(k)) continue
    seen.set(k, r)
  }
  return [...seen.values()]
})

const activeKey = ref('')
watch(
  masters,
  (list) => {
    if (!list.length) { activeKey.value = ''; return }
    if (!list.some((m) => m[masterKey.value] === activeKey.value)) {
      activeKey.value = list[0][masterKey.value]
    }
  },
  { immediate: true }
)

const curMaster = computed(() => masters.value.find((m) => m[masterKey.value] === activeKey.value) || null)
const curMasterLabel = computed(() => {
  const m = curMaster.value
  if (!m) return '-'
  return props.reverse
    ? `${m['子件编码'] || ''} ${m['子件名称'] || ''}`
    : `${m['父件编码'] || ''} ${m['父件名称'] || ''}`
})
const curRows = computed(() => {
  if (!activeKey.value) return []
  return (props.rows || []).filter((r) => r[masterKey.value] === activeKey.value)
})

function onMasterClick(row) {
  activeKey.value = row[masterKey.value]
}
function masterRowCls({ row }) {
  return row[masterKey.value] === activeKey.value ? 'row-cur' : ''
}
function fmtBool(v) {
  return v === true || v === 'true' || v === 1 || v === '1' ? '是' : ''
}
</script>

<style scoped>
.bom-md {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px 12px 12px;
  min-height: 100%;
  box-sizing: border-box;
}
.bom-md-sec {
  border: 1px solid var(--t-border, #e4e7ed);
  border-radius: 6px;
  overflow: hidden;
  background: #fff;
}
.bom-md-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: #f5f7fa;
  border-bottom: 1px solid var(--t-border, #e4e7ed);
}
.bom-md-title {
  font-size: 13px;
  font-weight: 600;
  color: #333;
}
.bom-md-count {
  font-size: 12px;
  color: #909399;
}
.bom-md-empty {
  padding: 14px;
  font-size: 12px;
  color: #909399;
  text-align: center;
}
:deep(.row-cur td) {
  background: #ecf5ff !important;
}
</style>