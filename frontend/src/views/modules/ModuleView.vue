<template>
  <div class="module-view">
    <div class="head">
      <h3>{{ title }}</h3>
      <span class="code">{{ code }}</span>
    </div>
    <el-empty :description="description" :image-size="90" />
    <div class="plan">
      <div class="plan-title">模块规划要点</div>
      <el-tag v-for="p in plan" :key="p" class="plan-tag" effect="plain">{{ p }}</el-tag>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const title = computed(() => route.meta.title || '')
const code = computed(() => route.meta.code || '')

const planMap = {
  salesOrder: ['客户/产品/数量/交期', '审批流', '单据状态跟踪'],
  salesOrderDetail: ['按单据展开明细', '多条件筛选'],
  salesOrderStats: ['按客户/产品汇总', '图表展示'],
  salesOrderExec: ['订单→生产→出库全链路', '执行进度百分比'],
  salesOrderProgress: ['关联生产加工单', '工序级进度'],
  purchaseIn: ['供应商到货入库', '关联采购订单'],
  finishIn: ['车间完工入库', '关联生产加工单'],
  otherIn: ['盘盈/调拨等入库'],
  saleOut: ['销售发货出库', '关联销售订单'],
  materialOut: ['车间领料出库', '关联生产加工单/物料清单'],
  otherOut: ['盘亏/调拨等出库'],
  costMaintain: ['存货成本手工调整', '月末加权平均'],
  stockStatus: ['实时库存查询', '按仓库/存货'],
  stockSummary: ['期初/收入/发出/结存', '期间汇总'],
  stockLedger: ['存货收发流水', '批次追溯'],
  manufactureOrder: ['加工单创建/审核', '领料/完工联动', '工序流转'],
  manufactureDetail: ['加工单明细查询'],
  manufactureStats: ['完工/在制统计'],
  manufactureBoard: ['车间大屏', '生产进度看板'],
  procReport: ['扫码报工', '合格/不良数量', '计件工资'],
  reworkReport: ['返修任务汇报'],
  reworkDesk: ['返修任务池', '处理流程'],
  procDetail: ['工序汇报明细'],
  procStats: ['工序产量汇总', '工时统计'],
  salaryDetail: ['计件工资明细'],
  salaryStats: ['工资汇总', '班组/个人对比'],
  dept: ['部门档案'],
  inventory: ['存货档案', '分类/单位/条码'],
  bom: ['物料清单树', '版本管理'],
  routing: ['工序定义', '标准工时'],
  warehouse: ['仓库档案'],
  process: ['工序档案'],
  invPrice: ['存货价格本', '价格策略'],
  options: ['系统选项配置'],
  boardAuth: ['看板数据授权'],
  docDesign: ['单据模板设计'],
  coding: ['单据编码规则'],
  print: ['打印模板管理'],
  alert: ['库存/交期预警规则'],
  task: ['定时任务管理'],
  screen: ['大屏设备与内容管理'],
  stockBalance: ['库存期初录入'],
  initTempIn: ['期初暂估入库'],
  initSaleOut: ['期初销售出库'],
  solutionCenter: ['行业方案', '应用市场'],
}

const description = computed(() => `【${title.value}】模块开发中，此处为功能占位页`)
const plan = computed(() => planMap[code.value] || ['待规划'])
</script>

<style scoped>
.module-view {
  background: #fff;
  border-radius: 10px;
  padding: 18px;
  min-height: 100%;
}
.dark .module-view {
  background: #26272e;
}
.head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  border-bottom: 1px solid #f0f1f3;
  padding-bottom: 10px;
}
.dark .head {
  border-color: #3a3b42;
}
.code {
  font-size: 12px;
  color: #9ca3af;
}
.plan {
  margin-top: 8px;
  padding: 12px;
  background: #f8fafc;
  border-radius: 8px;
}
.dark .plan {
  background: #2c2d35;
}
.plan-title {
  font-size: 13px;
  color: #6b7280;
  margin-bottom: 8px;
}
.plan-tag {
  margin: 0 8px 8px 0;
}
</style>
