<!-- BusinessOverview.vue — 智能供应链「业务总览」：模块 + 业务流程图（Vue Flow 升级版）
     流程图节点=单据面板，点击进入；箭头=生单/选单流转（对齐业务流程图 + 推式生单/选单联动） -->
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
          :style="{ '--mc': m.color }"
          @click="active = m.code"
        >
          <el-icon class="bo-mod-icon"><component :is="ICONS[m.icon]" /></el-icon>
          <span>{{ m.name }}</span>
        </div>
      </div>
      <div class="bo-main">
        <div class="bo-flow" v-if="cur">
          <div class="bo-flow-title">
            <span>
              <i class="bo-flow-dot" :style="{ background: cur.color }"></i>
              {{ cur.name }} · 业务流程图
            </span>
            <span class="bo-flow-tip">节点点击进入 · 箭头为生单/选单流转</span>
          </div>
          <div class="bo-canvas" :style="{ height: canvasH + 'px' }">
            <VueFlow
              v-model:nodes="nodes"
              v-model:edges="edges"
              :min-zoom="0.3"
              :max-zoom="1.8"
              :default-viewport="{ zoom: 0.9 }"
              :nodes-draggable="false"
              :nodes-connectable="false"
              :elements-selectable="false"
              :delete-key-code="null"
              class="bo-vueflow"
              @node-click="onNodeClick"
            >
              <Background :gap="20" :size="1.4" variant="dots" />
              <template #node-flowNode="nodeProps">
                <div class="fn-card" :style="{ '--c': nodeProps.data.color }">
                  <div class="fn-icon">
                    <el-icon><component :is="ICONS[nodeProps.data.icon]" /></el-icon>
                  </div>
                  <div class="fn-label">{{ nodeProps.data.label }}</div>
                </div>
              </template>
            </VueFlow>
          </div>
        </div>
      </div>
      <div class="bo-sections" v-if="cur">
        <div class="bo-sec" v-if="cur.docs && cur.docs.length">
          <span class="bo-sec-title" :style="{ '--sc': cur.color }">相关单据</span>
          <div class="bo-sec-btns">
            <span v-for="d in cur.docs" :key="d.code" class="bo-btn" @click="go(d.code)">{{ d.label }}</span>
          </div>
        </div>
        <div class="bo-sec" v-if="cur.archives && cur.archives.length">
          <span class="bo-sec-title" :style="{ '--sc': cur.color }">基础档案</span>
          <div class="bo-sec-btns">
            <span v-for="d in cur.archives" :key="d.code" class="bo-btn" @click="go(d.code)">{{ d.label }}</span>
          </div>
        </div>
        <div class="bo-sec" v-if="cur.reports && cur.reports.length">
          <span class="bo-sec-title" :style="{ '--sc': cur.color }">相关报表</span>
          <div class="bo-sec-btns">
            <span v-for="d in cur.reports" :key="d.code" class="bo-btn" @click="go(d.code)">{{ d.label }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { VueFlow, MarkerType, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import { SetUp, Van, ShoppingCart, Box, Iphone, Sort, View, Connection } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { useTabsStore } from '@/stores/tabs'

const ICONS = { SetUp, Van, ShoppingCart, Box, Iphone, Sort, View, Connection }

const router = useRouter()
const tabs = useTabsStore()

const modules = [
  {
    code: 'prod', name: '生产管理', icon: 'SetUp', color: '#2f6fed',
    nodes: [
      { code: 'SO_ORDER', label: '销售订单' },
      { code: 'MANU_ORDER', label: '生产加工单' },
      { code: 'PROCESS_REPORT', label: '工序汇报单' },
      { code: 'TRANSFER', label: '调拨单' },
      { code: 'MATERIAL_REQ', label: '领料申请单' },
      { code: 'MATERIAL_OUT', label: '材料出库单' },
      { code: 'FINISH_IN', label: '产成品入库单' },
      { code: 'PU_REQ_ANALYSIS', label: '采购需求分析' },
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
    ],
    pos: {
      SO_ORDER: [470, 60],
      MANU_ORDER: [470, 180],
      TRANSFER: [20, 330], PROCESS_REPORT: [920, 330], FINISH_IN: [1220, 330],
      MATERIAL_REQ: [470, 480], PU_REQ_ANALYSIS: [920, 480],
      MATERIAL_OUT: [20, 630],
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
      { code: 'MANU_ORDER_EXEC', label: '生产加工单执行表' }, { code: 'MANU_ORDER_TRACKER', label: '生产加工单跟踪工具' },
      { code: 'MANU_ORDER_PRODUCT_DETAIL', label: '生产加工单产成品明细表' }, { code: 'MANU_ORDER_MATERIAL_DETAIL', label: '生产加工单材料明细表' },
      { code: 'MANU_ORDER_DETAIL', label: '生产加工单明细表' }, { code: 'PROC_DETAIL', label: '工序明细表' },
      { code: 'MANU_ORDER_PRODUCT_STATS', label: '生产加工单产成品统计表' }, { code: 'MANU_ORDER_MATERIAL_STATS', label: '生产加工单材料统计表' },
      { code: 'MANU_ORDER_STATS', label: '生产加工单统计表' }, { code: 'MANU_PROC_STATS', label: '工序统计表' },
      { code: 'PROC_STATS', label: '工序统计表(车间)' }, { code: 'SALARY_STATS', label: '工资统计表' },
    ],
  },
  {
    code: 'outsource', name: '委外管理', icon: 'Van', color: '#0e9f9f',
    nodes: [
      { code: 'SO_ORDER', label: '销售订单' },
      { code: 'OUTSOURCE_ORDER', label: '委外加工单' },
      { code: 'OUTSOURCE_ISSUE', label: '委外发料单' },
      { code: 'OUTSOURCE_IN', label: '委外入库单' },
      { code: 'OUTSOURCE_FEE', label: '委外加工费用单' },
    ],
    edges: [
      { from: 'SO_ORDER', to: 'OUTSOURCE_ORDER' },
      { from: 'OUTSOURCE_ORDER', to: 'OUTSOURCE_ISSUE' },
      { from: 'OUTSOURCE_ORDER', to: 'OUTSOURCE_IN' },
      { from: 'OUTSOURCE_ORDER', to: 'OUTSOURCE_FEE' },
    ],
    pos: {
      SO_ORDER: [320, 60],
      OUTSOURCE_ORDER: [320, 230],
      OUTSOURCE_ISSUE: [20, 400], OUTSOURCE_IN: [320, 400], OUTSOURCE_FEE: [620, 400],
    },
    docs: [
      { code: 'OUTSOURCE_ORDER', label: '委外加工单' }, { code: 'OUTSOURCE_ISSUE', label: '委外发料单' },
      { code: 'OUTSOURCE_IN', label: '委外入库单' }, { code: 'OUTSOURCE_FEE', label: '委外加工费用单' },
      { code: 'SO_ORDER', label: '销售订单' }, { code: 'PU_REQ', label: '请购单' }, { code: 'PU_ORDER', label: '采购订单' },
    ],
    archives: [
      { code: 'INV', label: '存货' }, { code: 'UOM', label: '计量单位' }, { code: 'PARTNER', label: '往来单位' },
      { code: 'DEPT', label: '部门' }, { code: 'EMP', label: '员工' }, { code: 'WH', label: '仓库' },
      { code: 'PROJ', label: '项目' }, { code: 'TEAM', label: '班组' }, { code: 'OP', label: '工序' },
      { code: 'ROUTE', label: '工艺路线' },
    ],
    reports: [
      { code: 'OUTSOURCE_ISSUE_BALANCE', label: '委外发料耗用结存表' },
      { code: 'OUTSOURCE_ORDER_EXEC', label: '委外加工单执行表' },
      { code: 'OUTSOURCE_ORDER_PRODUCT_DETAIL', label: '委外加工单产成品明细表' },
      { code: 'OUTSOURCE_ORDER_MATERIAL_DETAIL', label: '委外加工单材料明细表' },
      { code: 'OUTSOURCE_FEE_DETAIL', label: '委外加工费用单明细表' },
      { code: 'OUTSOURCE_ORDER_PRODUCT_STATS', label: '委外加工单产成品统计表' },
      { code: 'OUTSOURCE_ORDER_MATERIAL_STATS', label: '委外加工单材料统计表' },
      { code: 'OUTSOURCE_FEE_STATS', label: '委外加工费用单统计表' },
    ],
  },
  {
    code: 'sales', name: '销售管理', icon: 'ShoppingCart', color: '#e8873a',
    nodes: [
      { code: 'QUOTE_ORDER', label: '报价单' },
      { code: 'SO_ORDER', label: '销售订单' },
      { code: 'SALE_INV', label: '销货单' },
      { code: 'SALE_OUT', label: '销售出库单' },
      { code: 'SALE_INVOICE', label: '销售发票' },
      { code: 'EXPENSE', label: '费用单' },
      { code: 'SALE_COST_ALLOC', label: '销售费用分摊单' },
    ],
    edges: [
      { from: 'QUOTE_ORDER', to: 'SO_ORDER' },
      { from: 'SO_ORDER', to: 'SALE_INV' },
      { from: 'SO_ORDER', to: 'SALE_OUT' },
      { from: 'SALE_INV', to: 'SALE_OUT' },
      { from: 'SALE_INV', to: 'SALE_INVOICE' },
      { from: 'EXPENSE', to: 'SALE_COST_ALLOC' },
      { from: 'SALE_INVOICE', to: 'SALE_COST_ALLOC' },
    ],
    pos: {
      QUOTE_ORDER: [20, 60], SO_ORDER: [320, 60], SALE_INV: [620, 60], SALE_INVOICE: [920, 60],
      SALE_OUT: [320, 250], SALE_COST_ALLOC: [620, 250], EXPENSE: [920, 250],
    },
    docs: [
      { code: 'QUOTE_ORDER', label: '报价单' }, { code: 'SO_ORDER', label: '销售订单' },
      { code: 'SALE_INV', label: '销货单' }, { code: 'SALE_OUT', label: '销售出库单' },
      { code: 'SALE_INVOICE', label: '销售发票' }, { code: 'EXPENSE', label: '费用单' },
      { code: 'SALE_COST_ALLOC', label: '销售费用分摊单' },
    ],
    archives: [{ code: 'PARTNER', label: '往来单位(客户)' }, { code: 'INV', label: '存货' }, { code: 'DEPT', label: '部门' }, { code: 'EMP', label: '员工' }, { code: 'UOM', label: '计量单位' }, { code: 'WH', label: '仓库' }, { code: 'SETTLE', label: '结算方式' }],
    reports: [
      { code: 'SALES_ORDER_DETAIL', label: '销售订单明细表' }, { code: 'SALES_ORDER_STATS', label: '销售订单统计表' },
      { code: 'SALES_ORDER_EXEC', label: '销售订单执行表' }, { code: 'SALES_ORDER_PROGRESS', label: '销售订单生产进度表' },
    ],
  },
  {
    code: 'purchase', name: '采购管理', icon: 'ShoppingCart', color: '#3aa76d',
    nodes: [
      { code: 'PU_REQ_ANALYSIS', label: '采购需求分析' },
      { code: 'PU_REQ', label: '请购单' },
      { code: 'PU_ORDER', label: '采购订单' },
      { code: 'PURCHASE_IN', label: '采购入库单' },
      { code: 'PU_IN', label: '进货单' },
      { code: 'PU_INVOICE', label: '采购发票' },
      { code: 'EXPENSE', label: '费用单' },
      { code: 'PU_COST_ALLOC', label: '采购费用分摊单' },
    ],
    edges: [
      { from: 'PU_REQ_ANALYSIS', to: 'PU_REQ' },
      { from: 'PU_REQ', to: 'PU_ORDER' },
      { from: 'PU_ORDER', to: 'PURCHASE_IN' },
      { from: 'PU_ORDER', to: 'PU_IN' },
      { from: 'PU_IN', to: 'PU_INVOICE' },
      { from: 'PU_ORDER', to: 'PU_INVOICE' },
      { from: 'EXPENSE', to: 'PU_COST_ALLOC' },
      { from: 'PU_INVOICE', to: 'PU_COST_ALLOC' },
    ],
    pos: {
      PU_REQ_ANALYSIS: [440, 20], PU_REQ: [440, 190], PU_ORDER: [440, 360],
      PURCHASE_IN: [120, 360], PU_IN: [760, 360],
      PU_INVOICE: [440, 530], PU_COST_ALLOC: [440, 700], EXPENSE: [760, 700],
    },
    docs: [
      { code: 'PU_REQ_ANALYSIS', label: '采购需求分析' }, { code: 'PU_REQ', label: '请购单' },
      { code: 'PU_ORDER', label: '采购订单' },
      { code: 'PU_IN', label: '进货单' }, { code: 'PURCHASE_IN', label: '采购入库单' },
      { code: 'PU_INVOICE', label: '采购发票' }, { code: 'EXPENSE', label: '费用单' },
      { code: 'PU_COST_ALLOC', label: '采购费用分摊单' },
    ],
    archives: [{ code: 'PARTNER', label: '往来单位(供应商)' }, { code: 'INV', label: '存货' }, { code: 'WH', label: '仓库' }],
    reports: [
      { code: 'PURCHASE_IN_DETAIL', label: '采购入库明细表' }, { code: 'PURCHASE_IN_STATS', label: '采购入库统计表' },
    ],
  },
  {
    code: 'distribution', name: '配货管理', icon: 'Box', color: '#7a5af8',
    nodes: [
      { code: 'SO_ORDER', label: '销售订单' },
      { code: 'PICK_ORDER', label: '配货单' },
      { code: 'SALE_OUT', label: '销售出库单' },
    ],
    edges: [
      { from: 'SO_ORDER', to: 'PICK_ORDER' },
      { from: 'PICK_ORDER', to: 'SALE_OUT' },
    ],
    pos: { SO_ORDER: [20, 170], PICK_ORDER: [320, 170], SALE_OUT: [620, 170] },
    docs: [
      { code: 'PICK_ORDER', label: '配货单' }, { code: 'OTHER_OUT', label: '其他出库单' },
      { code: 'OTHER_IN', label: '其他入库单' }, { code: 'SO_ORDER', label: '销售订单' },
      { code: 'SALE_OUT', label: '销售出库单' }, { code: 'SALE_INV', label: '销货单' },
    ],
    archives: [{ code: 'INV', label: '存货' }, { code: 'WH', label: '仓库' }],
    reports: [
      { code: 'PICK_ORDER_DETAIL', label: '配货单明细表' }, { code: 'PICK_ORDER_STATS', label: '配货单统计表' },
      { code: 'PICK_ORDER_SUMMARY', label: '配货综合统计表' },
    ],
  },
  {
    code: 'inv', name: '库存核算', icon: 'Box', color: '#4a7bd8',
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
      PURCHASE_IN: [40, 60], FINISH_IN: [40, 240], OTHER_IN: [40, 420],
      SALE_OUT: [640, 60], MATERIAL_OUT: [640, 240], OTHER_OUT: [640, 420],
    },
    docs: [
      { code: 'PURCHASE_IN', label: '采购入库单' }, { code: 'FINISH_IN', label: '产成品入库单' },
      { code: 'OUTSOURCE_IN', label: '委外入库单' }, { code: 'OTHER_IN', label: '其他入库单' },
      { code: 'SALE_OUT', label: '销售出库单' }, { code: 'MATERIAL_OUT', label: '材料出库单' },
      { code: 'OTHER_OUT', label: '其他出库单' }, { code: 'PU_COST_ALLOC', label: '采购费用分摊单' },
      { code: 'MATERIAL_REQ', label: '领料申请单' }, { code: 'TRANSFER', label: '调拨单' },
    ],
    archives: [{ code: 'INV', label: '存货' }, { code: 'WH', label: '仓库' }, { code: 'REGION', label: '地区' }],
    reports: [
      { code: 'STOCK_STATUS', label: '库存状况表' }, { code: 'STOCK_SUMMARY', label: '收发存汇总表' },
      { code: 'STOCK_LEDGER', label: '库存台账' },
    ],
  },
  {
    code: 'pda', name: '移动仓管', icon: 'Iphone', color: '#1c9fae',
    nodes: [
      { code: 'PURCHASE_IN', label: '采购入库单' },
      { code: 'FINISH_IN', label: '产成品入库单' },
      { code: 'SALE_OUT', label: '销售出库单' },
      { code: 'MATERIAL_OUT', label: '材料出库单' },
      { code: 'TRANSFER', label: '调拨单' },
      { code: 'STOCK_CHECK', label: '库存盘点单' },
      { code: 'LOCATION_ADJUST', label: '货位调整单' },
    ],
    edges: [
      { from: 'PURCHASE_IN', to: 'STOCK_CHECK' },
      { from: 'FINISH_IN', to: 'STOCK_CHECK' },
      { from: 'STOCK_CHECK', to: 'LOCATION_ADJUST' },
      { from: 'TRANSFER', to: 'LOCATION_ADJUST' },
    ],
    pos: {
      PURCHASE_IN: [40, 60], FINISH_IN: [420, 60],
      STOCK_CHECK: [420, 260], TRANSFER: [800, 260],
      LOCATION_ADJUST: [800, 460], MATERIAL_OUT: [420, 460], SALE_OUT: [40, 460],
    },
    docs: [
      { code: 'PURCHASE_IN', label: '采购入库单' }, { code: 'FINISH_IN', label: '产成品入库单' },
      { code: 'SALE_OUT', label: '销售出库单' }, { code: 'MATERIAL_OUT', label: '材料出库单' },
      { code: 'TRANSFER', label: '调拨单' }, { code: 'STOCK_CHECK', label: '库存盘点单' },
      { code: 'LOCATION_ADJUST', label: '货位调整单' },
    ],
    archives: [{ code: 'INV', label: '存货' }, { code: 'UOM', label: '计量单位' }, { code: 'DEPT', label: '部门' }, { code: 'EMP', label: '员工' }, { code: 'WH', label: '仓库' }],
    reports: [
      { code: 'STOCK_STATUS', label: '库存状况表' }, { code: 'STOCK_SUMMARY', label: '收发存汇总表' },
      { code: 'STOCK_LEDGER', label: '库存台账' },
    ],
  },
  {
    code: 'sn', name: '序列号管理', icon: 'Sort', color: '#5a7fa8',
    nodes: [
      { code: 'INV', label: '存货(启用序列号)' },
      { code: 'SERIAL_NO', label: '序列号登记单' },
      { code: 'SERIAL_STATUS', label: '序列号状况表' },
      { code: 'SERIAL_TRACE', label: '序列号跟踪表' },
    ],
    edges: [
      { from: 'INV', to: 'SERIAL_NO' },
      { from: 'SERIAL_NO', to: 'SERIAL_STATUS' },
      { from: 'SERIAL_NO', to: 'SERIAL_TRACE' },
    ],
    pos: {
      INV: [40, 150], SERIAL_NO: [420, 150], SERIAL_STATUS: [800, 150],
      SERIAL_TRACE: [420, 330],
    },
    docs: [
      { code: 'SERIAL_NO', label: '序列号登记单' }, { code: 'SERIAL_STATUS', label: '序列号状况表' },
      { code: 'SERIAL_TRACE', label: '序列号跟踪表' },
    ],
    archives: [{ code: 'INV', label: '存货' }, { code: 'WH', label: '仓库' }],
    reports: [],
  },
  {
    code: 'qc', name: '质量管理', icon: 'View', color: '#d95b5b',
    nodes: [
      { code: 'PU_ORDER', label: '采购订单' },
      { code: 'ARRIVAL_IN', label: '到货单' },
      { code: 'INSPECTION', label: '来料/成品检验单' },
      { code: 'PURCHASE_IN', label: '采购入库单' },
      { code: 'MANU_ORDER', label: '生产加工单' },
      { code: 'FINISH_INSPECT', label: '成品报检单' },
      { code: 'FINISH_IN', label: '产成品入库单' },
      { code: 'FIRST_INSPECT', label: '首件报检单' },
      { code: 'PROCESS_REPORT', label: '工序汇报单' },
      { code: 'PROCESS_INSPECT_APPLY', label: '工序报检单' },
      { code: 'PROCESS_INSPECTION', label: '生产过程检验单' },
    ],
    edges: [
      { from: 'PU_ORDER', to: 'ARRIVAL_IN' },
      { from: 'ARRIVAL_IN', to: 'INSPECTION' },
      { from: 'INSPECTION', to: 'PURCHASE_IN' },
      { from: 'MANU_ORDER', to: 'FINISH_INSPECT' },
      { from: 'FINISH_INSPECT', to: 'INSPECTION' },
      { from: 'INSPECTION', to: 'FINISH_IN' },
      { from: 'MANU_ORDER', to: 'FIRST_INSPECT' },
      { from: 'FIRST_INSPECT', to: 'INSPECTION' },
      { from: 'PROCESS_REPORT', to: 'PROCESS_INSPECT_APPLY' },
      { from: 'PROCESS_INSPECT_APPLY', to: 'PROCESS_INSPECTION' },
    ],
    pos: {
      PU_ORDER: [40, 80], ARRIVAL_IN: [360, 80], INSPECTION: [690, 190], PURCHASE_IN: [1030, 80],
      MANU_ORDER: [40, 260], FINISH_INSPECT: [360, 220], FINISH_IN: [1030, 260],
      FIRST_INSPECT: [360, 360], PROCESS_REPORT: [40, 500], PROCESS_INSPECT_APPLY: [360, 500], PROCESS_INSPECTION: [690, 500],
    },
    docs: [
      { code: 'ARRIVAL_IN', label: '到货单' }, { code: 'INSPECTION', label: '来料/成品检验单' },
      { code: 'FINISH_INSPECT', label: '成品报检单' }, { code: 'FIRST_INSPECT', label: '首件报检单' },
      { code: 'PROCESS_INSPECT_APPLY', label: '工序报检单' }, { code: 'PROCESS_INSPECTION', label: '生产过程检验单' },
    ],
    archives: [
      { code: 'QC_ITEM', label: '检验项目' }, { code: 'QC_PLAN', label: '检验方案' },
      { code: 'REJECT', label: '不合格原因' }, { code: 'INV', label: '存货' },
    ],
    reports: [
      { code: 'ARRIVAL_IN_EXEC', label: '到货单执行表' }, { code: 'FINISH_INSPECT_EXEC', label: '成品报检单执行表' },
      { code: 'FIRST_INSPECT_EXEC', label: '首件报检单执行表' }, { code: 'PROCESS_INSPECT_APPLY_EXEC', label: '工序报检单执行表' },
      { code: 'INSPECTION_DETAIL', label: '检验单综合明细表' }, { code: 'INSPECTION_STATS', label: '检验单综合统计表' },
      { code: 'QUALITY_STATS_ANALYSIS', label: '质量统计分析表' },
    ],
  },
]

const active = ref('prod')
const cur = computed(() => modules.find((m) => m.code === active.value) || modules[0])

// 画布高度按当前模块节点最大纵坐标自适应（卡片高 80 + 边距）
const canvasH = computed(() => {
  const m = cur.value
  let maxY = 300
  for (const n of m.nodes || []) {
    const y = m.pos && m.pos[n.code] ? m.pos[n.code][1] : 0
    if (y + 80 > maxY) maxY = y + 80
  }
  return maxY + 40
})

// ---------- Vue Flow 图数据（按当前模块构建） ----------
const nodes = ref([])
const edges = ref([])
const { fitView } = useVueFlow()

function buildFlow() {
  const m = cur.value
  nodes.value = (m.nodes || []).map((n) => ({
    id: n.code,
    type: 'flowNode',
    position: { x: (m.pos && m.pos[n.code] ? m.pos[n.code][0] : 0), y: (m.pos && m.pos[n.code] ? m.pos[n.code][1] : 0) },
    data: { code: n.code, label: n.label, icon: m.icon, color: m.color },
  }))
  edges.value = (m.edges || []).map((e, i) => ({
    id: 'e' + i + '-' + e.from + '-' + e.to,
    source: e.from,
    target: e.to,
    ...(e.sourceHandle ? { sourceHandle: e.sourceHandle } : {}),
    ...(e.targetHandle ? { targetHandle: e.targetHandle } : {}),
    markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18, color: m.color },
    style: { stroke: m.color, strokeWidth: 1.8 },
  }))
}

watch(cur, () => {
  buildFlow()
  setTimeout(() => fitView({ padding: 0.18, maxZoom: 1 }), 80)
}, { immediate: true })

function onNodeClick({ node }) {
  go(node.data.code)
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
  background: #f5f7fa;
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
  width: 176px;
  flex-shrink: 0;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 8px;
  overflow-y: auto;
}
.bo-mod {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  color: #333;
  margin-bottom: 2px;
  transition: background .15s, color .15s;
}
.bo-mod:hover {
  background: #f0f7ff;
}
.bo-mod.on {
  background: var(--mc, #0d5bd3);
  color: #fff;
  box-shadow: 0 3px 8px rgba(31, 45, 61, .18);
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
  border-radius: 8px;
  padding: 10px 12px 12px;
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
.bo-flow-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 3px;
  margin-right: 6px;
  vertical-align: middle;
}
.bo-flow-tip {
  font-size: 11px;
  font-weight: 400;
  color: #909399;
}
.bo-canvas {
  border: 1px solid #eef1f5;
  border-radius: 8px;
  background: #fbfcfe;
  overflow: hidden;
}
.bo-vueflow {
  width: 100%;
  height: 100%;
}
/* 自定义节点卡片（对齐真实 T+ 卡片尺寸 211×83 → 200×80，左侧图标条 + 名称） */
.fn-card {
  width: 200px;
  height: 80px;
  display: flex;
  align-items: stretch;
  border-radius: 10px;
  background: #fff;
  border: 1.5px solid var(--c, #2f6fed);
  box-shadow: 0 3px 10px rgba(31, 45, 61, .12);
  cursor: pointer;
  overflow: hidden;
  transition: transform .16s ease, box-shadow .16s ease;
}
.fn-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 10px 22px rgba(31, 45, 61, .20);
}
.fn-icon {
  width: 52px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 22px;
  background: linear-gradient(160deg, var(--c, #2f6fed), rgba(0, 0, 0, .16) 135%);
}
.fn-label {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
  color: #1f2d3d;
  white-space: nowrap;
}
.bo-sections {
  width: 248px;
  flex-shrink: 0;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 8px 12px 12px;
  overflow-y: auto;
}
.bo-sec {
  padding: 8px 0;
  border-bottom: 1px dashed #eef0f3;
}
.bo-sec:last-child {
  border-bottom: none;
}
.bo-sec-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  font-weight: 700;
  color: #303133;
  margin-bottom: 7px;
}
.bo-sec-title::before {
  content: '';
  width: 4px;
  height: 13px;
  border-radius: 2px;
  background: var(--sc, #0d5bd3);
}
.bo-sec-btns {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.bo-btn {
  padding: 3px 9px;
  font-size: 12px;
  color: #0d5bd3;
  background: #f0f7ff;
  border: 1px solid #cfe3ff;
  border-radius: 5px;
  cursor: pointer;
  transition: background .15s;
}
.bo-btn:hover {
  background: #d7e6ff;
}
</style>
