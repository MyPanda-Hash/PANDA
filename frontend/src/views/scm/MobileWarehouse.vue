<!-- MobileWarehouse.vue — 移动仓管（PDA）：流程说明 + 相关单据/报表快捷入口（对齐真实 T+ PDA 模块） -->
<template>
  <div class="pda-page">
    <div class="pda-head">
      <div class="pda-title">移动仓管（PDA）</div>
      <div class="pda-sub">及时、精准的仓库移动作业：入库 → 上下架 → 出库 → 货位调整（点击单据直达对应面板）</div>
    </div>
    <div class="pda-body">
      <div class="pda-flow">
        <div v-for="(s, i) in flow" :key="i" class="pda-step">
          <div class="pda-step-no">{{ i + 1 }}</div>
          <div class="pda-step-name">{{ s.name }}</div>
          <div class="pda-step-desc">{{ s.desc }}</div>
        </div>
      </div>
      <div class="pda-sec">
        <span class="pda-sec-title">相关单据</span>
        <span v-for="d in docs" :key="d.code" class="pda-btn" @click="go(d.code)">{{ d.label }}</span>
      </div>
      <div class="pda-sec">
        <span class="pda-sec-title">相关报表</span>
        <span v-for="d in reports" :key="d.code" class="pda-btn" @click="go(d.code)">{{ d.label }}</span>
      </div>
      <div class="pda-sec">
        <span class="pda-sec-title">基础档案</span>
        <span v-for="d in archives" :key="d.code" class="pda-btn" @click="go(d.code)">{{ d.label }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { useTabsStore } from '@/stores/tabs'

const router = useRouter()
const tabs = useTabsStore()

const flow = [
  { name: '入库', desc: '采购入库 / 生产入库（产成品入库）/ 其他入库，扫码或录入数量' },
  { name: '上下架', desc: '货位调整、货位管理，移动货物到指定货位' },
  { name: '出库', desc: '销售出库 / 材料出库 / 其他出库，扫描或选择货物出库' },
  { name: '货位调整', desc: '库存盘点（账面/实盘/盈亏）、货位调整、库存查询' },
]

const docs = [
  { code: 'PURCHASE_IN', label: '采购入库单' },
  { code: 'FINISH_IN', label: '产成品入库单' },
  { code: 'OTHER_IN', label: '其他入库单' },
  { code: 'SALE_OUT', label: '销售出库单' },
  { code: 'MATERIAL_OUT', label: '材料出库单' },
  { code: 'OTHER_OUT', label: '其他出库单' },
  { code: 'TRANSFER', label: '调拨单' },
  { code: 'STOCK_CHECK', label: '库存盘点单' },
  { code: 'LOCATION_ADJUST', label: '货位调整单' },
]

const reports = [
  { code: 'STOCK_STATUS', label: '库存状况表' },
  { code: 'STOCK_SUMMARY', label: '收发存汇总表' },
  { code: 'STOCK_LEDGER', label: '库存台账' },
]

const archives = [
  { code: 'INV', label: '存货' },
  { code: 'UOM', label: '计量单位' },
  { code: 'DEPT', label: '部门' },
  { code: 'EMP', label: '员工' },
  { code: 'WH', label: '仓库' },
]

function go(code) {
  const path = '/panelx/list/' + code
  router.push(path)
  tabs.open({ path, title: code })
}
</script>

<style scoped>
.pda-page {
  padding: 14px 18px;
  height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  background: #f9f9f9;
}
.pda-head {
  padding-bottom: 12px;
  border-bottom: 1px solid #e4e7ed;
}
.pda-title {
  font-size: 17px;
  font-weight: 700;
  color: #1f2d3d;
}
.pda-sub {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
.pda-body {
  flex: 1;
  margin-top: 14px;
  min-height: 0;
  overflow-y: auto;
}
.pda-flow {
  display: flex;
  gap: 12px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 14px;
}
.pda-step {
  flex: 1;
  text-align: center;
  border: 1px solid #cfe3ff;
  border-radius: 6px;
  padding: 12px 8px;
  background: #f0f7ff;
}
.pda-step-no {
  width: 26px;
  height: 26px;
  line-height: 26px;
  margin: 0 auto 8px;
  border-radius: 50%;
  background: #0d5bd3;
  color: #fff;
  font-size: 13px;
  font-weight: 600;
}
.pda-step-name {
  font-size: 14px;
  font-weight: 600;
  color: #1f2d3d;
}
.pda-step-desc {
  font-size: 11.5px;
  color: #606266;
  margin-top: 6px;
  line-height: 1.5;
}
.pda-sec {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  margin-top: 12px;
}
.pda-sec-title {
  width: 70px;
  font-size: 12.5px;
  font-weight: 600;
  color: #606266;
  flex-shrink: 0;
}
.pda-btn {
  padding: 4px 12px;
  font-size: 12.5px;
  color: #0d5bd3;
  background: #f0f7ff;
  border: 1px solid #cfe3ff;
  border-radius: 4px;
  cursor: pointer;
}
.pda-btn:hover {
  background: #d7e6ff;
}
</style>