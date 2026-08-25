<!-- BusinessOverview.vue — 智能供应链「业务总览」（对齐 T+：模块 + 业务流程图 + 相关单据/档案/报表）
     流程图节点=单据面板，点击进入；箭头=生单/选单流转（对齐业务流程图 + 已实现的推式生单/选单联动） -->
<template>
  <div class="bo-page">
    <div class="bo-head">
      <div class="bo-title">业务总览</div>
      <div class="bo-sub">智能供应链 · 全流程业务关系（点击流程节点或单据进入对应面板）</div>
    </div>
    <div class="bo-body">
      <div class="bo-modules">
        <div
          v-for="m in modules"
          :key="m.code"
          class="bo-mod"
          :class="{ on: active === m.code }"
          @click="active = m.code"
        >
          <el-icon class="bo-mod-icon"><component :is="m.icon" /></el-icon>
          <span>{{ m.name }}</span>
        </div>
      </div>
      <div class="bo-main">
        <div class="bo-flow" v-if="cur">
          <div class="bo-flow-title">
            <span>{{ cur.name }} · 业务流程图</span>
            <span class="bo-flow-tip">节点点击进入 · 箭头为生单/选单流转</span>
          </div>
          <svg :viewBox="'0 0 ' + VW + ' ' + VH" class="bo-svg" :key="cur.code">
            <defs>
              <marker id="bo-arrow" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
                <polygon points="0 0, 10 3.5, 0 7" fill="#0d5bd3" />
              </marker>
            </defs>
            <g v-for="(e, i) in cur.edges" :key="'e' + i">
              <line
                :x1="nodeX(e.from)" :y1="nodeY(e.from) + NODE_H / 2"
                :x2="nodeX(e.to)" :y2="nodeY(e.to) + NODE_H / 2"
                stroke="#0d5bd3" stroke-width="1.6" marker-end="url(#bo-arrow)"
              />
            </g>
            <g
              v-for="n in cur.nodes"
              :key="n.code"
              class="bo-node"
              @click="go(n.code)"
            >
              <rect :x="nodeX(n.code)" :y="nodeY(n.code)" :width="NODE_W" :height="NODE_H" rx="5" />
              <text :x="nodeX(n.code) + NODE_W / 2" :y="nodeY(n.code) + NODE_H / 2 + 5" text-anchor="middle">{{ n.label }}</text>
            </g>
          </svg>
        </div>
        <div class="bo-sections" v-if="cur">
          <div class="bo-sec" v-if="cur.docs && cur.docs.length">
            <span class="bo-sec-title">相关单据</span>
            <span v-for="d in cur.docs" :key="d.code" class="bo-btn" @click="go(d.code)">{{ d.label }}</span>
          </div>
          <div class="bo-sec" v-if="cur.archives && cur.archives.length">
            <span class="bo-sec-title">基础档案</span>
            <span v-for="d in cur.archives" :key="d.code" class="bo-btn" @click="go(d.code)">{{ d.label }}</span>
          </div>
          <div class="bo-sec" v-if="cur.reports && cur.reports.length">
            <span class="bo-sec-title">相关报表</span>
            <span v-for="d in cur.reports" :key="d.code" class="bo-btn" @click="go(d.code)">{{ d.label }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useTabsStore } from '@/stores/tabs'

const router = useRouter()
const tabs = useTabsStore()

const NODE_W = 150
const NODE_H = 40
const VW = 980
const VH = 420

const modules = [
  {
    code: 'prod', name: '生产管理', icon: 'SetUp',
    nodes: [
      { code: 'SO_ORDER', label: '销售订单' },
      { code: 'MANU_ORDER', label: '生产加工单' },
      { code: 'PROCESS_REPORT', label: '工序汇报单' },
      { code: 'TRANSFER', label: '调拨单' },
      { code: 'MATERIAL_REQ', label: '领料申请单' },
      { code: 'MATERIAL_OUT', label: '材料出库单' },
      { code: 'FINISH_IN', label: '产成品入库单' },
      { code: 'PU_REQ_ANALYSIS', label: '采购需求分析' },
      { code: 'PU_REQ', label: '请购单' },
    ],
    edges: [
      { from: 'SO_ORDER', to: 'MANU_ORDER' },
      { from: 'MANU_ORDER', to: 'PROCESS_REPORT' },
      { from: 'MANU_ORDER', to: 'MATERIAL_REQ' },
      { from: 'MANU_ORDER', to: 'TRANSFER' },
      { from: 'MANU_ORDER', to: 'MATERIAL_OUT' },
      { from: 'MANU_ORDER', to: 'FINISH_IN' },
      { from: 'MANU_ORDER', to: 'PU_REQ_ANALYSIS' },
      { from: 'MATERIAL_REQ', to: 'TRANSFER' },
      { from: 'MATERIAL_REQ', to: 'MATERIAL_OUT' },
      { from: 'TRANSFER', to: 'MATERIAL_OUT' },
      { from: 'PU_REQ_ANALYSIS', to: 'PU_REQ' },
    ],
    pos: {
      SO_ORDER: [20, 40], MANU_ORDER: [230, 40], PROCESS_REPORT: [440, 40], TRANSFER: [650, 40],
      MATERIAL_REQ: [440, 130], MATERIAL_OUT: [650, 130],
      FINISH_IN: [440, 220], PU_REQ_ANALYSIS: [230, 220], PU_REQ: [440, 310],
    },
    docs: [
      { code: 'MANU_ORDER', label: '生产加工单' }, { code: 'PROCESS_REPORT', label: '工序汇报单' },
      { code: 'TRANSFER', label: '调拨单' }, { code: 'MATERIAL_REQ', label: '领料申请单' },
      { code: 'MATERIAL_OUT', label: '材料出库单' }, { code: 'FINISH_IN', label: '产成品入库单' },
      { code: 'SO_ORDER', label: '销售订单' }, { code: 'PU_REQ_ANALYSIS', label: '采购需求分析' },
      { code: 'PU_REQ', label: '请购单' }, { code: 'PU_ORDER', label: '采购订单' },
    ],
    archives: [
      { code: 'INV', label: '存货' }, { code: 'UOM', label: '计量单位' }, { code: 'DEPT', label: '部门' },
      { code: 'EMP', label: '员工' }, { code: 'WH', label: '仓库' }, { code: 'PROJ', label: '项目' },
      { code: 'TEAM', label: '班组' }, { code: 'OP', label: '工序' }, { code: 'ROUTE', label: '工艺路线' },
      { code: 'BOM', label: '物料清单' },
    ],
    reports: [
      { code: 'MANU_ORDER_STATS', label: '生产加工单统计表' }, { code: 'MANU_PROC_STATS', label: '工序统计表' },
      { code: 'SALARY_STATS', label: '工资统计表' }, { code: 'MANU_ORDER_DETAIL', label: '生产加工单明细表' },
      { code: 'PROC_DETAIL', label: '工序明细表' }, { code: 'PROC_STATS', label: '工序统计表(车间)' },
    ],
  },
  {
    code: 'purchase', name: '采购管理', icon: 'ShoppingCart',
    nodes: [
      { code: 'PU_REQ_ANALYSIS', label: '采购需求分析' },
      { code: 'PU_REQ', label: '请购单' },
      { code: 'PU_ORDER', label: '采购订单' },
      { code: 'PURCHASE_IN', label: '采购入库单' },
      { code: 'PU_IN', label: '进货单' },
    ],
    edges: [
      { from: 'PU_REQ_ANALYSIS', to: 'PU_REQ' },
      { from: 'PU_REQ', to: 'PU_ORDER' },
      { from: 'PU_ORDER', to: 'PURCHASE_IN' },
      { from: 'PU_ORDER', to: 'PU_IN' },
    ],
    pos: {
      PU_REQ_ANALYSIS: [30, 90], PU_REQ: [30, 200], PU_ORDER: [260, 170],
      PURCHASE_IN: [490, 140], PU_IN: [490, 260],
    },
    docs: [
      { code: 'PU_REQ_ANALYSIS', label: '采购需求分析' }, { code: 'PU_REQ', label: '请购单' },
      { code: 'PU_ORDER', label: '采购订单' },
      { code: 'PU_IN', label: '进货单' }, { code: 'PURCHASE_IN', label: '采购入库单' },
    ],
    archives: [{ code: 'PARTNER', label: '往来单位(供应商)' }, { code: 'INV', label: '存货' }, { code: 'WH', label: '仓库' }],
    reports: [
      { code: 'PURCHASE_IN_DETAIL', label: '采购入库明细表' }, { code: 'PURCHASE_IN_STATS', label: '采购入库统计表' },
    ],
  },
  {
    code: 'sales', name: '销售管理', icon: 'ShoppingCart',
    nodes: [
      { code: 'SO_ORDER', label: '销售订单' },
      { code: 'SALE_INV', label: '销货单' },
      { code: 'SALE_OUT', label: '销售出库单' },
    ],
    edges: [
      { from: 'SO_ORDER', to: 'SALE_INV' },
      { from: 'SO_ORDER', to: 'SALE_OUT' },
    ],
    pos: { SO_ORDER: [40, 170], SALE_INV: [300, 140], SALE_OUT: [300, 260] },
    docs: [{ code: 'SO_ORDER', label: '销售订单' }, { code: 'SALE_INV', label: '销货单' }, { code: 'SALE_OUT', label: '销售出库单' }],
    archives: [{ code: 'PARTNER', label: '往来单位(客户)' }, { code: 'INV', label: '存货' }],
    reports: [
      { code: 'SALES_ORDER_DETAIL', label: '销售订单明细表' }, { code: 'SALES_ORDER_STATS', label: '销售订单统计表' },
      { code: 'SALES_ORDER_EXEC', label: '销售订单执行表' }, { code: 'SALES_ORDER_PROGRESS', label: '销售订单生产进度表' },
    ],
  },
  {
    code: 'distribution', name: '配货管理', icon: 'Box',
    nodes: [
      { code: 'SO_ORDER', label: '销售订单' },
      { code: 'PICK_ORDER', label: '配货单' },
      { code: 'SALE_OUT', label: '销售出库单' },
    ],
    edges: [
      { from: 'SO_ORDER', to: 'PICK_ORDER' },
      { from: 'PICK_ORDER', to: 'SALE_OUT' },
    ],
    pos: { SO_ORDER: [40, 170], PICK_ORDER: [300, 170], SALE_OUT: [560, 170] },
    docs: [{ code: 'PICK_ORDER', label: '配货单' }, { code: 'SO_ORDER', label: '销售订单' }, { code: 'SALE_OUT', label: '销售出库单' }],
    archives: [{ code: 'INV', label: '存货' }, { code: 'WH', label: '仓库' }],
    reports: [],
  },
  {
    code: 'inv', name: '库存核算', icon: 'Box',
    nodes: [
      { code: 'PURCHASE_IN', label: '采购入库单' },
      { code: 'FINISH_IN', label: '产成品入库单' },
      { code: 'OTHER_IN', label: '其他入库单' },
      { code: 'SALE_OUT', label: '销售出库单' },
      { code: 'MATERIAL_OUT', label: '材料出库单' },
      { code: 'OTHER_OUT', label: '其他出库单' },
    ],
    edges: [],
    pos: {
      PURCHASE_IN: [30, 60], FINISH_IN: [30, 170], OTHER_IN: [30, 280],
      SALE_OUT: [500, 60], MATERIAL_OUT: [500, 170], OTHER_OUT: [500, 280],
    },
    docs: [
      { code: 'PURCHASE_IN', label: '采购入库单' }, { code: 'FINISH_IN', label: '产成品入库单' },
      { code: 'OTHER_IN', label: '其他入库单' }, { code: 'SALE_OUT', label: '销售出库单' },
      { code: 'MATERIAL_OUT', label: '材料出库单' }, { code: 'OTHER_OUT', label: '其他出库单' },
      { code: 'MATERIAL_REQ', label: '领料申请单' }, { code: 'TRANSFER', label: '调拨单' },
    ],
    archives: [{ code: 'INV', label: '存货' }, { code: 'WH', label: '仓库' }, { code: 'REGION', label: '地区' }],
    reports: [
      { code: 'STOCK_STATUS', label: '库存状况表' }, { code: 'STOCK_SUMMARY', label: '收发存汇总表' },
      { code: 'STOCK_LEDGER', label: '库存台账' },
    ],
  },
  {
    code: 'qc', name: '质量管理', icon: 'View',
    nodes: [
      { code: 'ARRIVAL_IN', label: '到货单' },
      { code: 'INSPECTION', label: '来料/成品检验单' },
      { code: 'FINISH_INSPECT', label: '成品报检单' },
    ],
    edges: [
      { from: 'ARRIVAL_IN', to: 'INSPECTION' },
    ],
    pos: { ARRIVAL_IN: [40, 170], INSPECTION: [300, 140], FINISH_INSPECT: [300, 260] },
    docs: [
      { code: 'ARRIVAL_IN', label: '到货单' }, { code: 'INSPECTION', label: '来料/成品检验单' },
      { code: 'FINISH_INSPECT', label: '成品报检单' }, { code: 'DISPATCH', label: '工序派工单' },
    ],
    archives: [{ code: 'REJECT', label: '不合格原因' }, { code: 'INV', label: '存货' }],
    reports: [],
  },
]

const active = ref('prod')
const cur = computed(() => modules.find((m) => m.code === active.value) || modules[0])

function nodePos(code) {
  return (cur.value && cur.value.pos && cur.value.pos[code]) || [0, 0]
}
function nodeX(code) {
  return nodePos(code)[0]
}
function nodeY(code) {
  return nodePos(code)[1]
}
function go(code) {
  const path = '/panelx/list/' + code
  router.push(path)
  tabs.open({ path, title: code })
}
</script>

<style scoped>
.bo-page {
  padding: 14px 18px;
  height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  background: #f9f9f9;
}
.bo-head {
  padding-bottom: 12px;
  border-bottom: 1px solid #e4e7ed;
}
.bo-title {
  font-size: 17px;
  font-weight: 700;
  color: #1f2d3d;
}
.bo-sub {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
.bo-body {
  flex: 1;
  display: flex;
  gap: 14px;
  margin-top: 14px;
  min-height: 0;
}
.bo-modules {
  width: 170px;
  flex-shrink: 0;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 8px;
  overflow-y: auto;
}
.bo-mod {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 5px;
  cursor: pointer;
  font-size: 13px;
  color: #333;
}
.bo-mod:hover {
  background: #f0f7ff;
}
.bo-mod.on {
  background: #0d5bd3;
  color: #fff;
}
.bo-mod-icon {
  font-size: 15px;
}
.bo-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}
.bo-flow {
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 10px 12px;
}
.bo-flow-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
  color: #333;
  padding-bottom: 8px;
}
.bo-flow-tip {
  font-size: 11px;
  font-weight: 400;
  color: #909399;
}
.bo-svg {
  width: 100%;
  height: auto;
  display: block;
}
.bo-node {
  cursor: pointer;
}
.bo-node rect {
  fill: #eef4ff;
  stroke: #0d5bd3;
  stroke-width: 1.3;
}
.bo-node:hover rect {
  fill: #d7e6ff;
}
.bo-node text {
  font-size: 12.5px;
  fill: #1f2d3d;
}
.bo-sections {
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 10px 14px;
  flex: 1;
  overflow-y: auto;
}
.bo-sec {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 7px 0;
  border-bottom: 1px dashed #eef0f3;
}
.bo-sec:last-child {
  border-bottom: none;
}
.bo-sec-title {
  width: 70px;
  font-size: 12.5px;
  font-weight: 600;
  color: #606266;
  flex-shrink: 0;
}
.bo-btn {
  padding: 4px 12px;
  font-size: 12.5px;
  color: #0d5bd3;
  background: #f0f7ff;
  border: 1px solid #cfe3ff;
  border-radius: 4px;
  cursor: pointer;
}
.bo-btn:hover {
  background: #d7e6ff;
}
</style>