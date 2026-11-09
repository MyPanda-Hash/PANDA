<!-- BusinessOverview.vue — 业务总览：模块导航 + 业务流程图（Vue Flow）
     设计基线 Ant Design v6（design-kits/antd）：colorPrimary #1677ff、borderRadius 6、
     controlHeight 32、正文 14/22、8pt 间距；卡片 0 层用 1px 边框不用阴影。
     流程图节点=单据面板，点击进入；箭头=生单/选单流转（smoothstep 正交连线）。 -->
<template>
  <div class="bo-page">
    <header class="bo-head">
      <div class="bo-head-main">
        <h1 class="bo-title">{{ tt('业务总览') }}</h1>
        <p class="bo-sub">{{ tt('全流程业务关系：设计 → 计划 → 销售 → 采购 → 生产 → 质量 → 库存 → 财务（点击节点或单据进入对应面板）') }}</p>
      </div>
      <div class="bo-head-actions">
        <el-input
          v-model="nodeFilter"
          class="bo-search"
          :placeholder="tt('筛选节点或单据')"
          clearable
        >
          <template #prefix><AntIcon name="file-search" :size="14" /></template>
        </el-input>
        <el-button :type="tracked ? 'primary' : 'default'" @click="pickVisible = true">
          <AntIcon name="node-index" :size="14" style="margin-right: 6px" />
          {{ tt('选择单据追踪') }}
        </el-button>
        <el-button v-if="tracked" @click="clearTrace">
          <AntIcon name="rollback" :size="14" style="margin-right: 6px" />
          {{ tt('清除追踪') }}
        </el-button>
      </div>
    </header>

    <!-- 追踪状态条：选中一张单据后，显示当前卡在哪个环节 -->
    <div v-if="tracked" class="bo-track-bar" :class="{ stalled: traceInfo['卡点单号'] }">
      <div class="bo-track-main">
        <AntIcon :name="traceInfo['卡点单号'] ? 'clock-circle' : 'check-circle'" :size="16" class="bo-track-icon" />
        <span class="bo-track-doc">{{ traceInfo['单号'] }}</span>
        <span class="bo-track-sep">·</span>
        <span class="bo-track-count">{{ traceInfo['环节数'] }} {{ tt('个环节') }}</span>
        <span class="bo-track-sep">·</span>
        <span v-if="traceInfo['卡点单号']" class="bo-track-msg">
          {{ tt('卡在') }} <b>{{ traceInfo['卡点单号'] }}</b> — {{ traceInfo['卡点原因'] }}
        </span>
        <span v-else class="bo-track-msg done">{{ tt('全流程已完成') }}</span>
      </div>
      <div class="bo-track-legend">
        <span class="lg"><i class="lg-dot done"></i>{{ tt('已完成') }}</span>
        <span class="lg"><i class="lg-dot doing"></i>{{ tt('进行中') }}</span>
        <span class="lg"><i class="lg-dot stalled"></i>{{ tt('卡点') }}</span>
        <span class="lg"><i class="lg-dot none"></i>{{ tt('无关环节') }}</span>
      </div>
    </div>

    <!-- 跨模块提示：一张单可能跨多个模块，这里给直达入口 -->
    <div v-if="tracked && hitModules.length > 1" class="bo-cross-mod">
      <AntIcon name="branches" :size="14" class="bo-cross-icon" />
      <span class="bo-cross-text">{{ tt('该单涉及') }}{{ hitModules.length }}{{ tt('个模块') }}：</span>
      <button
        v-for="m in hitModules"
        :key="m.code"
        class="bo-cross-btn"
        :class="{ on: active === m.code }"
        type="button"
        @click="active = m.code"
      >
        {{ tt(m.name) }}
        <span class="bo-cross-num">{{ moduleHitCount(m) }}</span>
      </button>
    </div>

    <!-- 经过的单据路线：按业务顺序列出全链路，当前卡点高亮 -->
    <div v-if="tracked && traceSteps.length" class="bo-route">
      <div class="bo-route-head">
        <AntIcon name="node-index" :size="14" class="bo-route-icon" />
        <span class="bo-route-title">{{ tt('经过的单据路线') }}</span>
        <span class="bo-route-count">{{ traceSteps.length }}{{ tt('张') }}</span>
      </div>
      <div class="bo-route-track">
        <template v-for="(s, i) in traceSteps" :key="s['单号']">
          <button
            class="bo-route-node"
            :class="[stepKind(s), { stalled: s['是否卡点'] }]"
            type="button"
            :title="s['环节'] + ' ' + s['单号']"
            @click="openDoc(s['面板编码'], s['单号'], s['环节'] + '-' + s['单号'])"
          >
            <AntIcon
              :name="s['阶段'] === '完成' ? 'check-circle' : (s['是否卡点'] ? 'clock-circle' : 'file-text')"
              :size="13"
              class="bo-route-ic"
            />
            <span class="bo-route-label">{{ tt(s['环节']) }}</span>
            <span class="bo-route-no">{{ s['单号'] }}</span>
            <span class="bo-route-status">{{ tt(s['单据状态']) }}</span>
          </button>
          <span v-if="i < traceSteps.length - 1" class="bo-route-arrow" :class="stepKind(traceSteps[i + 1])">
            <AntIcon name="arrow-right" :size="14" />
          </span>
        </template>
      </div>
    </div>

    <div class="bo-body">
      <!-- 左：模块导航 -->
      <nav class="bo-nav" :aria-label="tt('业务模块')">
        <button
          v-for="m in filteredModules"
          :key="m.code"
          class="bo-nav-item"
          :class="{ on: active === m.code, hit: moduleHitCount(m) > 0 }"
          type="button"
          @click="active = m.code"
        >
          <AntIcon :name="m.icon" :size="16" class="bo-nav-icon" />
          <span class="bo-nav-label">{{ tt(m.name) }}</span>
          <span v-if="tracked && moduleHitCount(m) > 0" class="bo-nav-hit" :title="tt('该模块有追踪命中的环节')">
            {{ moduleHitCount(m) }}
          </span>
          <span v-else class="bo-nav-count">{{ (m.nodes || []).length }}</span>
        </button>
      </nav>

      <!-- 中：流程图 -->
      <section class="bo-main" v-if="cur">
        <div class="bo-panel-head">
          <div class="bo-panel-title">
            <AntIcon :name="cur.icon" :size="16" class="bo-panel-icon" />
            <span>{{ tt(cur.name) }} · {{ tt('业务流程图') }}</span>
          </div>
          <div class="bo-panel-meta">
            <span class="bo-meta-item">{{ (cur.nodes || []).length }} {{ tt('环节') }}</span>
            <span class="bo-meta-sep">·</span>
            <span class="bo-meta-item">{{ (cur.edges || []).length }} {{ tt('条流转') }}</span>
            <span class="bo-meta-sep">·</span>
            <span class="bo-meta-item bo-meta-tip">{{ tt('节点点击进入 · 箭头为生单/选单流转') }}</span>
          </div>
        </div>
        <div class="bo-canvas" :style="{ height: canvasH + 'px' }">
          <VueFlow
            v-model:nodes="nodes"
            v-model:edges="edges"
            :min-zoom="0.3"
            :max-zoom="1.8"
            :fit-view-on-init="true"
            :default-viewport="{ zoom: 1 }"
            :nodes-draggable="false"
            :nodes-connectable="false"
            :elements-selectable="false"
            :delete-key-code="null"
            class="bo-vueflow"
            @node-click="onNodeClick"
          >
            <Background :gap="16" :size="1" variant="dots" pattern-color="#e8e8e8" />
            <template #node-flowNode="nodeProps">
              <div
                class="fn-card"
                :class="[
                  'state-' + (nodeProps.data.state || 'none'),
                  { dim: nodeProps.data.dim, stalled: nodeProps.data.stalled, 'no-perm': !canOpenPanel(nodeProps.data.code) },
                ]"
              >
                <!-- 连线锚点：Vue Flow 需要节点里有 Handle 才能把边接到卡片边缘；
                     基础锚点上下左右各一个，边不指定 handle id 时按相对位置自动取；
                     旁路（如采购订单直接入库）用「左边缘底部出 / 顶部靠左进」，
                     让折线从卡片下沿拐进卡片列左侧的空白带，不贴到货单左边框 -->
                <Handle id="in" type="target" :position="Position.Left" class="fn-handle" />
                <Handle id="out" type="source" :position="Position.Right" class="fn-handle" />
                <Handle id="in-t" type="target" :position="Position.Top" class="fn-handle" />
                <Handle id="out-b" type="source" :position="Position.Bottom" class="fn-handle" />
                <Handle id="out-t" type="source" :position="Position.Top" class="fn-handle" />
                <Handle id="in-b" type="target" :position="Position.Bottom" class="fn-handle" />
                <Handle id="out-l-b" type="source" :position="Position.Left" style="top: 100%" class="fn-handle" />
                <Handle id="in-t-q" type="target" :position="Position.Top" style="left: 25%" class="fn-handle" />
                <Handle id="out-b-r" type="source" :position="Position.Bottom" style="left: 75%" class="fn-handle" />
                <Handle id="in-b-l" type="target" :position="Position.Bottom" style="left: 25%" class="fn-handle" />
                <div class="fn-icon">
                  <AntIcon :name="nodeProps.data.icon" :size="18" />
                </div>
                <div class="fn-body">
                  <div class="fn-label">{{ tt(nodeProps.data.label) }}</div>
                  <div class="fn-code" :title="nodeProps.data.docNo || nodeProps.data.code">
                    <template v-if="nodeProps.data.docNo">{{ nodeProps.data.docNo }}</template>
                    <template v-else>{{ nodeProps.data.code }}</template>
                  </div>
                </div>
                <!-- 同一环节命中多张单据：显示 +N，避免误以为只有一张 -->
                <span
                  v-if="nodeProps.data.moreCount > 0"
                  class="fn-more"
                  :title="nodeProps.data.moreTitle"
                >+{{ nodeProps.data.moreCount }}</span>
                <span v-if="nodeProps.data.state === 'done'" class="fn-mark done">
                  <AntIcon name="check-circle" :size="13" />
                </span>
                <span v-else-if="nodeProps.data.stalled" class="fn-mark stalled">
                  <AntIcon name="clock-circle" :size="13" />
                </span>
              </div>
            </template>
          </VueFlow>
        </div>
      </section>

      <!-- 右：单据 / 档案 / 报表 -->
      <aside class="bo-side" v-if="cur">
        <div class="bo-sec" v-if="visibleDocs.length">
          <div class="bo-sec-head">
            <AntIcon name="file-text" :size="14" class="bo-sec-icon" />
            <span class="bo-sec-title">{{ tt('相关单据') }}</span>
            <span class="bo-sec-count">{{ visibleDocs.length }}</span>
          </div>
          <div class="bo-sec-list">
            <button
              v-for="d in visibleDocs"
              :key="d.code"
              class="bo-item"
              :class="{ 'no-perm': !canOpenPanel(d.code) }"
              :title="canOpenPanel(d.code) ? '' : tt('无权限访问')"
              type="button"
              @click="openPanel(d.code, d.label)"
            >
              <AntIcon name="file-text" :size="13" class="bo-item-icon" />
              <span class="bo-item-label">{{ tt(d.label) }}</span>
            </button>
          </div>
        </div>

        <div class="bo-sec" v-if="visibleArchives.length">
          <div class="bo-sec-head">
            <AntIcon name="database" :size="14" class="bo-sec-icon" />
            <span class="bo-sec-title">{{ tt('基础档案') }}</span>
            <span class="bo-sec-count">{{ visibleArchives.length }}</span>
          </div>
          <div class="bo-sec-list">
            <button
              v-for="d in visibleArchives"
              :key="d.code"
              class="bo-item"
              :class="{ 'no-perm': !canOpenPanel(d.code) }"
              :title="canOpenPanel(d.code) ? '' : tt('无权限访问')"
              type="button"
              @click="openPanel(d.code, d.label)"
            >
              <AntIcon name="database" :size="13" class="bo-item-icon" />
              <span class="bo-item-label">{{ tt(d.label) }}</span>
            </button>
          </div>
        </div>

        <div class="bo-sec" v-if="visibleReports.length">
          <div class="bo-sec-head">
            <AntIcon name="bar-chart" :size="14" class="bo-sec-icon" />
            <span class="bo-sec-title">{{ tt('相关报表') }}</span>
            <span class="bo-sec-count">{{ visibleReports.length }}</span>
          </div>
          <div class="bo-sec-list">
            <button
              v-for="d in visibleReports"
              :key="d.code"
              class="bo-item"
              :class="{ 'no-perm': !canOpenPanel(d.code) }"
              :title="canOpenPanel(d.code) ? '' : tt('无权限访问')"
              type="button"
              @click="openPanel(d.code, d.label)"
            >
              <AntIcon name="bar-chart" :size="13" class="bo-item-icon" />
              <span class="bo-item-label">{{ tt(d.label) }}</span>
            </button>
          </div>
        </div>

        <div class="bo-empty" v-if="!visibleDocs.length && !visibleArchives.length && !visibleReports.length">
          <AntIcon name="inbox" :size="28" class="bo-empty-icon" />
          <p class="bo-empty-text">{{ tt('没有匹配的结果') }}</p>
          <el-button size="small" @click="nodeFilter = ''">{{ tt('清空筛选') }}</el-button>
        </div>
      </aside>
    </div>

    <!-- 选择单据弹窗：选一张单据，按它追踪整条链路 -->
    <el-dialog v-model="pickVisible" :title="tt('选择单据追踪')" width="720px" class="bo-pick-dialog">
      <div class="bo-pick-bar">
        <el-select v-model="pickPanel" class="bo-pick-select" :placeholder="tt('选择单据类型')" filterable>
          <el-option
            v-for="p in pickPanels"
            :key="p.code"
            :label="p.label"
            :value="p.code"
          />
        </el-select>
        <el-input
          v-model="pickKeyword"
          class="bo-pick-kw"
          :placeholder="tt('按单号/客户/供应商筛选')"
          clearable
          @keyup.enter="loadPickRows"
        />
        <el-button type="primary" :loading="pickLoading" @click="loadPickRows">
          {{ tt('查询') }}
        </el-button>
      </div>

      <div class="bo-pick-list" v-if="pickRows.length">
        <button
          v-for="r in pickRows"
          :key="r.no"
          class="bo-pick-row"
          type="button"
          @click="applyTrace(r.no)"
        >
          <span class="bo-pick-no">{{ r.no }}</span>
          <span class="bo-pick-meta">{{ r.partner || '—' }}</span>
          <span class="bo-pick-date">{{ r.date || '—' }}</span>
          <span class="bo-pick-status" :class="'st-' + statusKind(r.status)">{{ r.status }}</span>
        </button>
      </div>
      <div class="bo-pick-empty" v-else>
        <AntIcon name="inbox" :size="28" class="bo-empty-icon" />
        <p class="bo-empty-text">{{ pickLoading ? tt('查询中…') : tt('没有符合条件的单据') }}</p>
        <el-button size="small" @click="loadPickRows">{{ tt('重新查询') }}</el-button>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { VueFlow, MarkerType, useVueFlow, Handle, Position } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useTabsStore } from '@/stores/tabs'
import { useUserStore } from '@/stores/user'
import { tt } from '@/i18n'
import { useLocaleStore } from '@/stores/locale'
import AntIcon from '@/business/components/AntIcon.vue'
import { traceProcess, queryFormDataList } from '@/business/engine'

const router = useRouter()
const route = useRoute()
const tabs = useTabsStore()
const user = useUserStore()
const localeStore = useLocaleStore()

/**
 * 单据面板 → 设计库图标。键是面板编码，值是 AntIcon 的 name。
 * 未列出的面板回退到 file-text，保证任何单据都有图标。
 */
const PANEL_ICONS = {
  // 销售
  QUOTE_ORDER: 'solution', SO_ORDER: 'shopping', SALE_INV: 'file-done',
  SALE_OUT: 'export', SALE_INVOICE: 'file-text', SALE_RETURN: 'rollback',
  EXPENSE: 'money-collect', SALE_COST_ALLOC: 'calculator',
  // 采购
  PU_REQ_ANALYSIS: 'pie-chart', PU_REQ: 'file-add', PU_ORDER: 'shopping-cart',
  ARRIVAL_IN: 'truck', PURCHASE_IN: 'inbox',
  PURCHASE_RETURN: 'rollback', PU_INVOICE: 'file-text',
  RECEIPT_PAYABLE: 'money-collect',
  // 生产
  MANU_ORDER: 'tool', PLAN: 'calendar', PROCESS_REPORT: 'profile', MATERIAL_REQ: 'form',
  MATERIAL_OUT: 'export', FINISH_IN: 'file-done', FINISH_INSPECT: 'file-search',
  TRANSFER: 'swap', PICK_ORDER: 'container', OUTSOURCE_ORDER: 'deployment-unit',
  OUTSOURCE_ISSUE: 'export', OUTSOURCE_IN: 'import', OUTSOURCE_FEE: 'money-collect',
  // 库存
  OTHER_IN: 'import', OTHER_OUT: 'export', INIT_BALANCE: 'database',
  STOCK_CHECK: 'audit', LOCATION_ADJUST: 'aim', STOCK_STATUS: 'database',
  STOCK_LEDGER: 'profile', PICK_WORKBENCH: 'container',
  // 质量
  INSPECTION: 'safety-certificate', PROCESS_INSPECTION: 'safety-certificate',
  FIRST_INSPECT: 'file-search', PROCESS_INSPECT_APPLY: 'file-add',
  DISPATCH: 'team', SPC_CONTROL: 'line-chart',
  // 计划
  MPS_PLAN: 'schedule', MRP_RESULT: 'branches', KIT_ANALYSIS: 'apartment',
  // 财务
  RECEIPT: 'money-collect', RECEIPT_PAYABLE: 'money-collect',
  SETTLE_CUSTOMER: 'account-book', SETTLE_SUPPLIER: 'account-book',
  AGING_ANALYSIS: 'clock-circle', ACCOUNTING_ENTRY: 'transaction',
  ACCOUNT_BALANCE: 'bank',
  // 档案
  INV: 'tags', UOM: 'calculator', DEPT: 'cluster', EMP: 'user',
  WH: 'shop', PROJ: 'flag', TEAM: 'team', OP: 'build',
  ROUTE: 'node-index', BOM: 'partition', PARTNER: 'idcard',
  BOM_FWD: 'node-index', BOM_REV: 'sisternode', OP_CONV: 'swap',
  WC: 'dashboard', EQUIP: 'control',
  QC_ITEM: 'audit', QC_PLAN: 'safety-certificate', REJECT: 'flag', SETTLE: 'pay-circle',
  ACCOUNT: 'account-book', ACCOUNT_DETAIL: 'profile',
}

function iconOf(code) {
  return PANEL_ICONS[code] || 'file-text'
}

const modules = [
  {
    code: 'design', name: '设计管理', icon: 'apartment', color: '#722ed1',
    /** PDM：存货与工序是主数据，向上游提供物料清单与工艺路线 */
    nodes: [
      { code: 'INV', label: '存货' },
      { code: 'OP', label: '工序' },
      { code: 'BOM', label: '物料清单' },
      { code: 'ROUTE', label: '工艺路线' },
    ],
    edges: [
      { from: 'INV', to: 'BOM', label: '构成' },
      { from: 'OP', to: 'ROUTE', label: '组成' },
    ],
    pos: { INV: [40, 0], OP: [320, 0], BOM: [40, 170], ROUTE: [320, 170] },
    docs: [
      { code: 'INV', label: '存货' }, { code: 'BOM', label: '物料清单' },
      { code: 'ROUTE', label: '工艺路线' }, { code: 'OP', label: '工序' },
      { code: 'BOM_FWD', label: '物料清单正向查询' }, { code: 'BOM_REV', label: '物料清单反向查询' },
      { code: 'OP_CONV', label: '工序辅单位换算率设置' },
    ],
    archives: [
      { code: 'INV', label: '存货' }, { code: 'UOM', label: '计量单位' }, { code: 'OP', label: '工序' },
      { code: 'OP_CONV', label: '工序换算率' }, { code: 'TEAM', label: '班组' },
      { code: 'WC', label: '工作中心' }, { code: 'EQUIP', label: '设备' },
    ],
    reports: [
      { code: 'BOM_FWD', label: '物料清单正向查询' }, { code: 'BOM_REV', label: '物料清单反向查询' },
    ],
  },
  {
    code: 'plan', name: '计划管理', icon: 'schedule', color: '#13a8a8',
    /** 计划：销售需求 → 主生产计划 → 物料需求运算 → 采购需求 / 齐套分析 */
    nodes: [
      { code: 'SO_ORDER', label: '销售订单' },
      { code: 'MPS_PLAN', label: '主生产计划' },
      { code: 'MRP_RESULT', label: '物料需求运算' },
      { code: 'PU_REQ_ANALYSIS', label: '采购需求分析' },
      { code: 'KIT_ANALYSIS', label: '齐套分析' },
    ],
    edges: [
      { from: 'SO_ORDER', to: 'MPS_PLAN', label: '需求' },
      { from: 'MPS_PLAN', to: 'MRP_RESULT', label: '运算' },
      { from: 'MRP_RESULT', to: 'PU_REQ_ANALYSIS', label: '生成采购需求' },
      { from: 'MRP_RESULT', to: 'KIT_ANALYSIS', label: '齐套检查' },
    ],
    pos: {
      SO_ORDER: [320, 0], MPS_PLAN: [320, 160], MRP_RESULT: [320, 320],
      PU_REQ_ANALYSIS: [80, 480], KIT_ANALYSIS: [560, 480],
    },
    docs: [
      { code: 'MPS_PLAN', label: '主生产计划' }, { code: 'MRP_RESULT', label: '物料需求运算结果' },
      { code: 'KIT_ANALYSIS', label: '齐套分析' }, { code: 'PU_REQ_ANALYSIS', label: '采购需求分析' },
    ],
    archives: [
      { code: 'INV', label: '存货' }, { code: 'BOM', label: '物料清单' }, { code: 'WH', label: '仓库' },
    ],
    reports: [],
  },
  {
    code: 'prod', name: '生产管理', icon: 'tool', color: '#1677ff',
    /** 生产主流程把质量链条画进来：领料 → 报工 → 报检 → 检验 → 首件/成品报检 → 入库 */
    nodes: [
      { code: 'SO_ORDER', label: '销售订单' },
      { code: 'MANU_ORDER', label: '生产加工单' },
      { code: 'PLAN', label: '工单排产' },
      { code: 'MATERIAL_REQ', label: '领料申请单' },
      { code: 'MATERIAL_OUT', label: '材料出库单' },
      { code: 'TRANSFER', label: '调拨单' },
      { code: 'PROCESS_REPORT', label: '工序汇报单' },
      { code: 'PROCESS_INSPECT_APPLY', label: '工序报检单' },
      { code: 'PROCESS_INSPECTION', label: '生产过程检验单' },
      { code: 'FIRST_INSPECT', label: '首件报检单' },
      { code: 'FINISH_INSPECT', label: '成品报检单' },
      { code: 'FINISH_IN', label: '产成品入库单' },
    ],
    edges: [
      { from: 'SO_ORDER', to: 'MANU_ORDER', label: '生单' },
      { from: 'MANU_ORDER', to: 'PLAN', label: '排产' },
      { from: 'PLAN', to: 'MATERIAL_REQ', label: '按批次领料' },
      { from: 'PLAN', to: 'PROCESS_REPORT', label: '按批次报工' },
      { from: 'MATERIAL_REQ', to: 'MATERIAL_OUT', label: '出库' },
      { from: 'MATERIAL_REQ', to: 'TRANSFER', label: '调拨' },
      { from: 'MANU_ORDER', to: 'PROCESS_REPORT', label: '报工' },
      { from: 'PROCESS_REPORT', to: 'PROCESS_INSPECT_APPLY', label: '报检' },
      { from: 'PROCESS_INSPECT_APPLY', to: 'PROCESS_INSPECTION', label: '检验' },
      { from: 'MANU_ORDER', to: 'FIRST_INSPECT', label: '首件' },
      { from: 'MANU_ORDER', to: 'FINISH_INSPECT', label: '完工报检' },
      { from: 'FIRST_INSPECT', to: 'FINISH_IN', label: '合格入库' },
      { from: 'FINISH_INSPECT', to: 'FINISH_IN', label: '合格入库' },
    ],
    /** 分层布局：每条边都是上一层 → 下一层，避免同层连线被画成 U 形 */
    pos: {
      SO_ORDER: [430, 0], MANU_ORDER: [430, 150], PLAN: [430, 300],
      MATERIAL_REQ: [40, 450], PROCESS_REPORT: [300, 450], FIRST_INSPECT: [560, 310], FINISH_INSPECT: [820, 310],
      MATERIAL_OUT: [40, 470], TRANSFER: [180, 470], PROCESS_INSPECT_APPLY: [300, 470], FINISH_IN: [690, 470],
      PROCESS_INSPECTION: [300, 630],
    },
    docs: [
      { code: 'MANU_ORDER', label: '生产加工单' }, { code: 'DISPATCH', label: '工序派工单' },
      { code: 'PROCESS_REPORT', label: '工序汇报单' }, { code: 'PROCESS_INSPECT_APPLY', label: '工序报检单' },
      { code: 'PROCESS_INSPECTION', label: '生产过程检验单' }, { code: 'FIRST_INSPECT', label: '首件报检单' },
      { code: 'FINISH_INSPECT', label: '成品报检单' },
      { code: 'MATERIAL_REQ', label: '领料申请单' }, { code: 'MATERIAL_OUT', label: '材料出库单' },
      { code: 'FINISH_IN', label: '产成品入库单' }, { code: 'TRANSFER', label: '调拨单' },
      { code: 'SO_ORDER', label: '销售订单' },
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
    code: 'outsource', name: '委外管理', icon: 'deployment-unit', color: '#13a8a8',
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
    code: 'sales', name: '销售管理', icon: 'shopping-cart', color: '#fa8c16',
    nodes: [
      { code: 'QUOTE_ORDER', label: '报价单' },
      { code: 'SO_ORDER', label: '销售订单' },
      { code: 'SALE_INV', label: '销货单' },
      { code: 'SALE_OUT', label: '销售出库单' },
      { code: 'SALE_RETURN', label: '销售退货单' },
      { code: 'SALE_INVOICE', label: '销售开票' },
      { code: 'SALE_COST_ALLOC', label: '销售费用分摊单' },
      { code: 'EXPENSE', label: '费用单' },
    ],
    /**
     * 主链右流、支链下挂：每个节点只在自己那一列往下走，避免长距离回绕。
     * 退货单挂在销售订单右侧下方（退货入库、冲减应收），审核只是动作不画成节点。
     */
    edges: [
      { from: 'QUOTE_ORDER', to: 'SO_ORDER', label: '生单' },
      { from: 'SO_ORDER', to: 'SALE_INV', label: '选单' },
      { from: 'SALE_INV', to: 'SALE_INVOICE', label: '选单' },
      { from: 'SO_ORDER', to: 'SALE_OUT', label: '生单' },
      { from: 'SO_ORDER', to: 'SALE_RETURN', label: '退货' },
      { from: 'SALE_INVOICE', to: 'SALE_COST_ALLOC', label: '分摊' },
      { from: 'EXPENSE', to: 'SALE_COST_ALLOC', label: '选单' },
    ],
    pos: {
      QUOTE_ORDER: [20, 40], SO_ORDER: [320, 40], SALE_INV: [620, 40], SALE_INVOICE: [920, 40],
      SALE_OUT: [320, 240], SALE_RETURN: [620, 240], SALE_COST_ALLOC: [920, 240],
      EXPENSE: [620, 440],
    },
    docs: [
      { code: 'QUOTE_ORDER', label: '报价单' }, { code: 'SO_ORDER', label: '销售订单' },
      { code: 'SALE_INV', label: '销货单' }, { code: 'SALE_OUT', label: '销售出库单' },
      { code: 'SALE_RETURN', label: '销售退货单' }, { code: 'SALE_INVOICE', label: '销售开票' },
      { code: 'SALE_COST_ALLOC', label: '销售费用分摊单' }, { code: 'EXPENSE', label: '费用单' },
    ],
    archives: [{ code: 'PARTNER', label: '往来单位(客户)' }, { code: 'INV', label: '存货' }, { code: 'DEPT', label: '部门' }, { code: 'EMP', label: '员工' }, { code: 'UOM', label: '计量单位' }, { code: 'WH', label: '仓库' }],
    reports: [
      { code: 'SALES_ORDER_DETAIL', label: '销售订单明细表' }, { code: 'SALES_ORDER_STATS', label: '销售订单统计表' },
      { code: 'SALES_ORDER_EXEC', label: '销售订单执行表' }, { code: 'SALES_ORDER_PROGRESS', label: '销售订单生产进度表' },
    ],
  },
  {
    code: 'purchase', name: '采购管理', icon: 'shopping', color: '#52c41a',
    /**
     * 采购主链：请购 → 采购订单 → 到货（收料通知）→ 检验（拣验货）→ 合格入库 / 不合格退回 → 发票 → 付款/退款。
     * 采购发票两条来源：采购订单先票后货（收货后单票勾稽）、采购入库单先货后票；退货单开红字。
     * 另有「直入」旁路：无收料/无质检时采购订单可直接生成采购入库单；同一订单行与到货链互斥。
     * 付款与退款共用付款单（负数退款）；2026-11-04 移除 T+ 进货单。
     */
    nodes: [
      { code: 'PU_REQ', label: '请购单（采购申请）' },
      { code: 'PU_ORDER', label: '采购订单' },
      { code: 'ARRIVAL_IN', label: '到货单（收料通知）' },
      { code: 'INSPECTION', label: '检验单（拣验货）' },
      { code: 'PURCHASE_IN', label: '采购入库单' },
      { code: 'PURCHASE_RETURN', label: '采购退货单' },
      { code: 'PU_INVOICE', label: '采购发票（入库/退货）' },
      { code: 'RECEIPT_PAYABLE', label: '付款单/退款单' },
    ],
    edges: [
      { from: 'PU_REQ', to: 'PU_ORDER', label: '确认申请' },
      { from: 'PU_ORDER', to: 'ARRIVAL_IN', label: '通知收料' },
      // 零收料/零质检的捷径：左边缘底部出、顶部靠左进，折线走卡片列左侧空白带，不贴到货单边框
      {
        from: 'PU_ORDER', to: 'PURCHASE_IN', label: '直接入库',
        sourceHandle: 'out-l-b', targetHandle: 'in-t-q',
        sourcePosition: Position.Left, targetPosition: Position.Top,
      },
      { from: 'ARRIVAL_IN', to: 'INSPECTION', label: '通知拣验' },
      { from: 'INSPECTION', to: 'PURCHASE_IN', label: '拣验入库' },
      { from: 'INSPECTION', to: 'PURCHASE_RETURN', label: '不合格退回' },
      { from: 'PURCHASE_IN', to: 'PURCHASE_RETURN', label: '如需退货' },
      // 先票后货：订单预开票，货到后从发票回到采购入库单完成单票勾稽。
      {
        from: 'PU_ORDER', to: 'PU_INVOICE', label: '先票后货', dashed: true,
        sourceHandle: 'out', targetHandle: 'in-t-q',
        sourcePosition: Position.Right, targetPosition: Position.Top,
      },
      // 反向下沉到独立通道，避免和入库→发票的「先货后票」实线重合。
      {
        from: 'PU_INVOICE', to: 'PURCHASE_IN', label: '单票勾稽', dashed: true, offset: 44,
        sourceHandle: 'out-b-r', targetHandle: 'in-b-l',
        sourcePosition: Position.Bottom, targetPosition: Position.Bottom,
      },
      // 先货后票：从入库单底部绕到发票底部，避免横穿中间的退货单卡片。
      {
        from: 'PURCHASE_IN', to: 'PU_INVOICE', label: '先货后票（确认应付）',
        sourceHandle: 'out-b-r', targetHandle: 'in-b-l',
        sourcePosition: Position.Bottom, targetPosition: Position.Bottom,
      },
      { from: 'PURCHASE_RETURN', to: 'PU_INVOICE', label: '红字发票（确认应退）' },
      // 当前系统付款与退款共用付款单：正数付款、负数退款。
      { from: 'PU_INVOICE', to: 'RECEIPT_PAYABLE', label: '支付货款/收到退款' },
    ],
    pos: {
      PU_REQ: [440, 160], PU_ORDER: [440, 320],
      ARRIVAL_IN: [240, 480], INSPECTION: [240, 640],
      PURCHASE_IN: [120, 800], PURCHASE_RETURN: [380, 800],
      PU_INVOICE: [720, 800], RECEIPT_PAYABLE: [960, 800],
    },
    docs: [
      { code: 'PU_REQ', label: '请购单' }, { code: 'PU_ORDER', label: '采购订单' },
      { code: 'ARRIVAL_IN', label: '到货单' }, { code: 'INSPECTION', label: '来料检验单' },
      { code: 'PURCHASE_IN', label: '采购入库单' }, { code: 'PURCHASE_RETURN', label: '采购退货单' },
      { code: 'PU_INVOICE', label: '采购发票' }, { code: 'RECEIPT_PAYABLE', label: '付款单/退款单' },
    ],
    archives: [{ code: 'PARTNER', label: '往来单位(供应商)' }, { code: 'INV', label: '存货' }, { code: 'WH', label: '仓库' }, { code: 'UOM', label: '计量单位' }],
    reports: [
      { code: 'PURCHASE_IN_DETAIL', label: '采购入库明细表' }, { code: 'PURCHASE_IN_STATS', label: '采购入库统计表' },
      { code: 'ARRIVAL_IN_EXEC', label: '到货单执行表' },
    ],
  },
  {
    code: 'distribution', name: '配货管理', icon: 'container', color: '#722ed1',
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
    code: 'inv', name: '库存核算', icon: 'database', color: '#2f54eb',
    nodes: [
      { code: 'PURCHASE_IN', label: '采购入库单' },
      { code: 'FINISH_IN', label: '产成品入库单' },
      { code: 'OTHER_IN', label: '其他入库单' },
      { code: 'STOCK_STATUS', label: '库存结存' },
      { code: 'SALE_OUT', label: '销售出库单' },
      { code: 'MATERIAL_OUT', label: '材料出库单' },
      { code: 'OTHER_OUT', label: '其他出库单' },
    ],
    /** 六张出入库单都汇到中间「库存结存」：入库加、出库减，结存由 STOCK_STATUS 实时聚合 */
    edges: [
      { from: 'PURCHASE_IN', to: 'STOCK_STATUS', label: '入库' },
      { from: 'FINISH_IN', to: 'STOCK_STATUS', label: '入库' },
      { from: 'OTHER_IN', to: 'STOCK_STATUS', label: '入库' },
      { from: 'STOCK_STATUS', to: 'SALE_OUT', label: '出库' },
      { from: 'STOCK_STATUS', to: 'MATERIAL_OUT', label: '出库' },
      { from: 'STOCK_STATUS', to: 'OTHER_OUT', label: '出库' },
    ],
    pos: {
      PURCHASE_IN: [20, 40], FINISH_IN: [20, 220], OTHER_IN: [20, 400],
      STOCK_STATUS: [420, 220],
      SALE_OUT: [820, 40], MATERIAL_OUT: [820, 220], OTHER_OUT: [820, 400],
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
    code: 'pda', name: '移动仓管', icon: 'idcard', color: '#13a8a8',
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
    code: 'sn', name: '序列号管理', icon: 'node-index', color: '#597ef7',
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
    code: 'qc', name: '质量管理', icon: 'safety-certificate', color: '#ff4d4f',
    /**
     * 质检有两条独立入口链，画成上下两段：
     * 上段 = 来料检验（采购到货 → 检验 → 入库）
     * 下段 = 生产检验（加工单 → 成品/首件/工序报检 → 入库）
     * 两段共用「来料/成品检验单」作为统一检验出口。
     */
    nodes: [
      // 来料检验链
      { code: 'PU_ORDER', label: '采购订单' },
      { code: 'ARRIVAL_IN', label: '到货单' },
      { code: 'INSPECTION', label: '来料/成品检验单' },
      { code: 'PURCHASE_IN', label: '采购入库单' },
      { code: 'PURCHASE_RETURN', label: '采购退货单' },
      // 生产检验链
      { code: 'MANU_ORDER', label: '生产加工单' },
      { code: 'FINISH_INSPECT', label: '成品报检单' },
      { code: 'FIRST_INSPECT', label: '首件报检单' },
      { code: 'PROCESS_REPORT', label: '工序汇报单' },
      { code: 'PROCESS_INSPECT_APPLY', label: '工序报检单' },
      { code: 'PROCESS_INSPECTION', label: '生产过程检验单' },
      { code: 'FINISH_IN', label: '产成品入库单' },
    ],
    edges: [
      // 采购来料链：到货 → 检验 → 入库 / 退货
      { from: 'PU_ORDER', to: 'ARRIVAL_IN', label: '到货' },
      { from: 'ARRIVAL_IN', to: 'INSPECTION', label: '报检' },
      { from: 'INSPECTION', to: 'PURCHASE_IN', label: '合格入库' },
      { from: 'INSPECTION', to: 'PURCHASE_RETURN', label: '不合格退回' },
      // 生产检验链：加工单派生成品报检与首件报检，检验合格入成品库
      { from: 'MANU_ORDER', to: 'FINISH_INSPECT', label: '报检' },
      { from: 'MANU_ORDER', to: 'FIRST_INSPECT', label: '首件' },
      { from: 'FINISH_INSPECT', to: 'FINISH_IN', label: '合格入库' },
      { from: 'FIRST_INSPECT', to: 'FINISH_IN', label: '合格入库' },
      // 工序链：报工 → 报检 → 过程检验
      { from: 'PROCESS_REPORT', to: 'PROCESS_INSPECT_APPLY', label: '报检' },
      { from: 'PROCESS_INSPECT_APPLY', to: 'PROCESS_INSPECTION', label: '检验' },
    ],
    /**
     * 布局按真实流向纵向分层，确保每条边都是"上一层 → 下一层"，
     * 避免同层相连被画成 U 形绕行。三条独立链并列：
     *   采购链  PU_ORDER → ARRIVAL_IN → INSPECTION → PURCHASE_IN / PURCHASE_RETURN
     *   生产链  MANU_ORDER → FINISH_INSPECT / FIRST_INSPECT → FINISH_IN
     *   工序链  PROCESS_REPORT → PROCESS_INSPECT_APPLY → PROCESS_INSPECTION
     */
    pos: {
      // 采购链（第1-3层）
      PU_ORDER: [0, 0], ARRIVAL_IN: [220, 0],
      INSPECTION: [220, 150],
      PURCHASE_IN: [120, 300], PURCHASE_RETURN: [320, 300],
      // 生产链（第1-3层，与采购链并列）
      MANU_ORDER: [480, 0], FINISH_INSPECT: [460, 150], FIRST_INSPECT: [620, 150],
      FINISH_IN: [540, 300],
      // 工序链（第4层）
      PROCESS_REPORT: [0, 460], PROCESS_INSPECT_APPLY: [220, 460], PROCESS_INSPECTION: [440, 460],
    },
    docs: [
      { code: 'INSPECTION', label: '来料/成品检验单' }, { code: 'ARRIVAL_IN', label: '到货单' },
      { code: 'FINISH_INSPECT', label: '成品报检单' }, { code: 'FIRST_INSPECT', label: '首件报检单' },
      { code: 'PROCESS_INSPECT_APPLY', label: '工序报检单' }, { code: 'PROCESS_INSPECTION', label: '生产过程检验单' },
      { code: 'PURCHASE_IN', label: '采购入库单' }, { code: 'FINISH_IN', label: '产成品入库单' },
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
  {
    code: 'finance', name: '应收应付', icon: 'account-book', color: '#faad14',
    /** 收付款来自销货单与采购入库：收款 / 付款 → 对账 → 账龄 → 凭证 */
    nodes: [
      { code: 'SALE_INV', label: '销货单' },
      { code: 'RECEIPT', label: '收款单' },
      { code: 'SETTLE_CUSTOMER', label: '客户对账单' },
      { code: 'PURCHASE_IN', label: '采购入库单' },
      { code: 'RECEIPT_PAYABLE', label: '付款单' },
      { code: 'SETTLE_SUPPLIER', label: '供应商对账单' },
      { code: 'AGING_ANALYSIS', label: '账龄分析表' },
      { code: 'ACCOUNTING_ENTRY', label: '记账凭证' },
    ],
    edges: [
      { from: 'SALE_INV', to: 'RECEIPT', label: '收款' },
      { from: 'RECEIPT', to: 'SETTLE_CUSTOMER', label: '对账' },
      { from: 'PURCHASE_IN', to: 'RECEIPT_PAYABLE', label: '付款' },
      { from: 'RECEIPT_PAYABLE', to: 'SETTLE_SUPPLIER', label: '对账' },
      { from: 'SETTLE_CUSTOMER', to: 'AGING_ANALYSIS', label: '账龄' },
      { from: 'SETTLE_SUPPLIER', to: 'AGING_ANALYSIS', label: '账龄' },
      { from: 'RECEIPT', to: 'ACCOUNTING_ENTRY', label: '凭证' },
      { from: 'RECEIPT_PAYABLE', to: 'ACCOUNTING_ENTRY', label: '凭证' },
    ],
    pos: {
      SALE_INV: [40, 0], RECEIPT: [40, 160], SETTLE_CUSTOMER: [40, 320],
      PURCHASE_IN: [420, 0], RECEIPT_PAYABLE: [420, 160], SETTLE_SUPPLIER: [420, 320],
      AGING_ANALYSIS: [230, 480], ACCOUNTING_ENTRY: [600, 480],
    },
    docs: [
      { code: 'RECEIPT', label: '收款单' }, { code: 'RECEIPT_PAYABLE', label: '付款单' },
      { code: 'SETTLE_CUSTOMER', label: '客户对账单' }, { code: 'SETTLE_SUPPLIER', label: '供应商对账单' },
      { code: 'AGING_ANALYSIS', label: '账龄分析表' }, { code: 'ACCOUNTING_ENTRY', label: '凭证查询' },
      { code: 'ACCOUNT', label: '会计科目' },
    ],
    archives: [
      { code: 'PARTNER', label: '往来单位' }, { code: 'PROJ', label: '项目' }, { code: 'DEPT', label: '部门' },
    ],
    reports: [
      { code: 'SETTLE_CUSTOMER', label: '客户对账单' }, { code: 'SETTLE_SUPPLIER', label: '供应商对账单' },
      { code: 'AGING_ANALYSIS', label: '账龄分析表' }, { code: 'ACCOUNT_BALANCE', label: '科目余额表' },
      { code: 'ACCOUNT_DETAIL', label: '明细账' },
    ],
  },
]

const active = ref('sales')
const cur = computed(() => modules.find((m) => m.code === active.value) || modules[0])

/** 左侧模块顺序 = 业务流顺序（设计 → 计划 → 销售 → 采购 → 生产 → 质量 → 库存 → 配货 → 移动仓管 → 序列号 → 委外 → 财务） */
const FLOW_ORDER = ['design', 'plan', 'sales', 'purchase', 'prod', 'qc', 'inv', 'distribution', 'pda', 'sn', 'outsource', 'finance']

// 非中文语言：预取本页可见词条（模块名/流程节点与单据标签，静态包缺失的走机翻补齐；tt() 渲染层切换即时生效）
watch(() => localeStore.current, async (locale) => {
  if (localeStore.isZh) return
  const keys = [
    '业务总览', '智能供应链 · 全流程业务关系（点击流程节点或单据进入对应面板）',
    '业务流程图', '节点点击进入 · 箭头为生单/选单流转', '相关单据', '基础档案', '相关报表',
  ]
  for (const m of modules) {
    keys.push(m.name)
    for (const list of [m.nodes, m.docs, m.archives, m.reports]) {
      for (const it of list || []) keys.push(it.label)
    }
  }
  await localeStore.ensureDict(locale, keys)
}, { immediate: true })

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

// ---------- 节点与侧栏筛选 ----------
const nodeFilter = ref('')
const filterText = computed(() => nodeFilter.value.trim().toLowerCase())

function hit(code, label) {
  if (!filterText.value) return true
  return String(code).toLowerCase().includes(filterText.value)
    || String(label || '').toLowerCase().includes(filterText.value)
}

const filteredModules = computed(() => {
  const list = filterText.value
    ? modules.filter((m) => !!(m.nodes || []).find((n) => hit(n.code, n.label)))
    : modules
  const rank = (code) => {
    const i = FLOW_ORDER.indexOf(code)
    return i < 0 ? FLOW_ORDER.length : i
  }
  return [...list].sort((a, b) => rank(a.code) - rank(b.code))
})

const visibleDocs = computed(() => (cur.value?.docs || []).filter((d) => hit(d.code, d.label)))
const visibleArchives = computed(() => (cur.value?.archives || []).filter((d) => hit(d.code, d.label)))
const visibleReports = computed(() => (cur.value?.reports || []).filter((d) => hit(d.code, d.label)))

// ---------- 追踪状态 ----------
/** 当前追踪的单号；空串表示未追踪。 */
const trackedNo = ref('')
const tracked = computed(() => !!trackedNo.value)
/** 追踪结果原文（来自 /api/px/trace）。 */
const traceInfo = ref({ 单号: '', 环节数: 0, 卡点单号: '', 卡点原因: '', steps: [] })
/** 变更触发器：结果变化后重建图。 */
const traceKey = ref(0)

/**
 * 面板编码 → 该面板在追踪结果里命中的单据列表。
 * 同一个面板可能命中多张（如多张出库单），保留数组以支持"多张单在同一环节"。
 */
const traceHits = computed(() => {
  const out = {}
  for (const s of traceInfo.value.steps || []) {
    const code = s['面板编码']
    if (!code) continue
    if (!out[code]) out[code] = []
    out[code].push({ docNo: s['单号'], state: s['阶段'] || '待开始', isStalled: s['是否卡点'] })
  }
  return out
})

function clearTrace() {
  trackedNo.value = ''
  traceInfo.value = { 单号: '', 环节数: 0, 卡点单号: '', 卡点原因: '', steps: [] }
  traceKey.value++
}

/** 某模块里有多少个环节命中追踪结果（用于在导航上打标记）。 */
function moduleHitCount(module) {
  if (!tracked.value) return 0
  return (module.nodes || []).filter((n) => (traceHits.value[n.code] || []).length).length
}

/** 追踪命中的模块列表：一个单据可能跨多个模块（如销售订单 → 委外加工单）。 */
const hitModules = computed(() => {
  if (!tracked.value) return []
  return modules.filter((m) => moduleHitCount(m) > 0)
})

/** 按业务顺序排列的追踪环节（后端已按业务序列排好），用于渲染"经过的单据路线"。 */
const traceSteps = computed(() => traceInfo.value.steps || [])

/** 环节状态 → CSS 类。 */
function stepKind(s) {
  const state = s['阶段']
  if (state === '完成') return 'kind-done'
  if (state === '已中止') return 'kind-aborted'
  if (state === '进行中') return 'kind-doing'
  return 'kind-pending'
}

async function applyTrace(docNo) {
  pickVisible.value = false
  if (!docNo) return
  try {
    const info = await traceProcess(docNo)
    trackedNo.value = docNo
    traceInfo.value = info || { 单号: docNo, 环节数: 0, 卡点单号: '', 卡点原因: '', steps: [] }
    traceKey.value++
    if (!info || !(info.steps || []).length) {
      ElMessage.warning(tt('该单号没有追踪到关联单据'))
      return
    }
    // 自动切到命中的模块页签，让用户第一眼看到相关流程
    const hitCodes = new Set((info.steps || []).map((s) => s['面板编码']))
    const target = modules.find((m) => (m.nodes || []).some((n) => hitCodes.has(n.code)))
    if (target && target.code !== active.value) active.value = target.code
  } catch (e) {
    ElMessage.error(tt('追踪失败') + '：' + (e?.message || ''))
    clearTrace()
  }
}

// ---------- 选单弹窗 ----------
const pickVisible = ref(false)
const pickPanel = ref('SO_ORDER')
const pickKeyword = ref('')
const pickLoading = ref(false)
const pickRows = ref([])

/** 可追踪的单据类型（与后端 TRACE_PANELS 对应，选主流程上的单据）。 */
const pickPanels = [
  { code: 'SO_ORDER', label: '销售订单' },
  { code: 'MANU_ORDER', label: '生产加工单' },
  { code: 'OUTSOURCE_ORDER', label: '委外加工单' },
  { code: 'PURCHASE_IN', label: '采购入库单' },
  { code: 'SALE_OUT', label: '销售出库单' },
  { code: 'FINISH_IN', label: '产成品入库单' },
  { code: 'MATERIAL_OUT', label: '材料出库单' },
  { code: 'PU_ORDER', label: '采购订单' },
  { code: 'INSPECTION', label: '检验单' },
  { code: 'SALE_INV', label: '销货单' },
  // 工序检验链
  { code: 'PROCESS_REPORT', label: '工序汇报单' },
  { code: 'PROCESS_INSPECT_APPLY', label: '工序报检单' },
  { code: 'PROCESS_INSPECTION', label: '生产过程检验单' },
  { code: 'FIRST_INSPECT', label: '首件报检单' },
  { code: 'FINISH_INSPECT', label: '成品报检单' },
]

function statusKind(status) {
  const s = String(status || '')
  if (s === '已审核' || s === '已完工') return 'done'
  if (s === '审批中' || s === '生产中') return 'doing'
  if (s === '已中止' || s === '已驳回') return 'aborted'
  return 'draft'
}

async function loadPickRows() {
  if (!pickPanel.value) return
  pickLoading.value = true
  try {
    const res = await queryFormDataList({
      panelCode: pickPanel.value,
      keyword: pickKeyword.value || undefined,
      condition: {},
      pageNo: 1,
      pageSize: 30,
    })
    const list = res?.list || []
    pickRows.value = list.map((r) => ({
      no: r['编号'] || r['单据编号'] || '',
      partner: r['客户'] || r['供应商'] || r['生产车间'] || '',
      date: r['单据日期'] || '',
      status: r['单据状态'] || '',
    })).filter((r) => r.no)
  } catch (e) {
    ElMessage.error(tt('查询失败') + '：' + (e?.message || ''))
    pickRows.value = []
  } finally {
    pickLoading.value = false
  }
}

watch(pickVisible, (v) => {
  if (v && !pickRows.value.length) loadPickRows()
})

// 切换单据类型后必须重新查询，否则列表仍停在上一个类型的结果上
watch(pickPanel, () => {
  if (pickVisible.value) {
    pickRows.value = []
    loadPickRows()
  }
})

// ---------- Vue Flow 图数据（按当前模块构建） ----------
const nodes = ref([])
const edges = ref([])
const { fitView } = useVueFlow()

/** 画布尺寸就绪后再 fitView：VueFlow 在容器高度为 0 时会算出错误的比例。 */
async function fitNow() {
  await nextTick()
  requestAnimationFrame(() => fitView({ padding: 0.12, maxZoom: 1, minZoom: 0.75 }))
}

const EDGE_COLOR = '#98a2b3'

/**
 * 流程图布局：原始 pos 是按 1400px 画布手摆的坐标，直接放进本页会超出可视区，
 * 硬缩放又会把卡片压到互相重叠。所以只用 pos 的纵向先后关系（Y 归入同一行），
 * 横向改为行内等距布点，保证任何模块都放得下且不重叠。
 */
const CARD_W = 176   // 与 .fn-card 宽度一致
const CARD_H = 56    // 与 .fn-card 高度一致
const GAP_X = 72
const GAP_Y = 56
/** 行吸附网格：原始 pos 的 Y 常以 120/170/250 这类手工值出现，按 60 取整才能把同层归到一行。 */
const ROW_SNAP = 60

/** 把一个模块的节点排成行列：返回 code → {x, y}。 */
function layoutNodes(module) {
  const raw = (module.nodes || []).map((n) => {
    const p = module.pos && module.pos[n.code] ? module.pos[n.code] : [0, 0]
    return { code: n.code, x: p[0], y: p[1] }
  })
  if (!raw.length) return {}

  // 1) 按原 Y 吸附到网格后分行：同层节点归入同一行
  const sorted = [...raw].sort((a, b) => a.y - b.y || a.x - b.x)
  const rows = []
  for (const item of sorted) {
    const band = Math.round(item.y / ROW_SNAP)
    const row = rows[rows.length - 1]
    if (row && row.band === band) {
      row.items.push(item)
    } else {
      rows.push({ band, items: [item] })
    }
  }

  // 2) 行内按原 X 排序，等距铺开
  const widest = Math.max(...rows.map((r) => r.items.length))
  const rowWidth = widest * CARD_W + (widest - 1) * GAP_X
  const out = {}
  rows.forEach((row, ri) => {
    row.items.sort((a, b) => a.x - b.x)
    const count = row.items.length
    const used = count * CARD_W + (count - 1) * GAP_X
    const offset = (rowWidth - used) / 2
    row.items.forEach((item, ci) => {
      out[item.code] = {
        x: Math.round(offset + ci * (CARD_W + GAP_X)),
        y: Math.round(ri * (CARD_H + GAP_Y)),
      }
    })
  })
  return out
}

/**
 * 连线方向：按上下游相对位置挑锚点，让直角折线尽量走直线。
 * - 主要往右：右出左进（水平直达或只拐一次）
 * - 主要往下/上：下出上进（竖直直达，不再绕成 S 形）
 * 边的显式 sourceHandle/targetHandle 优先，配置里可覆盖。
 */
function edgeHandles(e, layout) {
  // 旁路等特殊走向由模块配置显式指定锚点，绕开中间的节点列
  if (e.sourceHandle && e.targetHandle) {
    return {
      sourceHandle: e.sourceHandle,
      targetHandle: e.targetHandle,
      sourcePosition: e.sourcePosition || Position.Bottom,
      targetPosition: e.targetPosition || Position.Top,
    }
  }
  const a = layout[e.from] || { x: 0, y: 0 }
  const b = layout[e.to] || { x: 0, y: 0 }
  const dy = b.y - a.y
  // 跨行就是竖直支链：从上往下走「下出上进」，从下往上走「上出下进」，
  // 只有同一行才走「右出左进」。这样支链是直的，不再绕成 S 形长弯。
  if (dy > 0) {
    return {
      sourceHandle: 'out-b',
      targetHandle: 'in-t',
      sourcePosition: Position.Bottom,
      targetPosition: Position.Top,
    }
  }
  if (dy < 0) {
    return {
      sourceHandle: 'out-t',
      targetHandle: 'in-b',
      sourcePosition: Position.Top,
      targetPosition: Position.Bottom,
    }
  }
  return { sourceHandle: 'out', targetHandle: 'in', sourcePosition: Position.Right, targetPosition: Position.Left }
}

function buildFlow() {
  const m = cur.value
  const kw = filterText.value
  const layout = layoutNodes(m)
  // 追踪命中的面板 → 该面板下的单据（同面板可能有多张，例如多张出库单）
  const hits = traceHits.value
  const stalledSet = traceInfo.value['卡点单号'] ? new Set([traceInfo.value['卡点单号']]) : new Set()

  nodes.value = (m.nodes || []).map((n) => {
    const p = layout[n.code] || { x: 0, y: 0 }
    const hitList = hits[n.code] || []
    const firstHit = hitList[0]
    // 没有任何追踪结果时，所有节点都是中性态；有追踪时，未命中节点压暗
    const state = !tracked.value
      ? 'none'
      : hitList.length
        ? (firstHit.state === '完成' ? 'done' : 'doing')
        : 'none'
    const isStalled = hitList.some((h) => stalledSet.has(h.docNo))
    // 同环节多张单：第一张显示单号，其余用 +N 汇总，避免误以为只有一张
    const extra = hitList.slice(1)
    return {
      id: n.code,
      type: 'flowNode',
      position: p,
      data: {
        code: n.code,
        label: n.label,
        icon: iconOf(n.code),
        // 追踪态：未命中的环节压暗，命中的正常；无追踪时才用文字筛选压暗
        dim: tracked.value
          ? !hitList.length
          : (!!kw && !hit(n.code, n.label)),
        state: isStalled ? 'doing' : state,
        stalled: isStalled,
        docNo: firstHit ? firstHit.docNo : '',
        moreCount: extra.length,
        moreTitle: extra.length
          ? tt('同环节还有') + '：' + extra.map((h) => h.docNo).join('、')
          : '',
      },
    }
  })

  // 只有"上游节点 → 下游节点"两端都在追踪结果里，这条边才点亮并走流光
  edges.value = (m.edges || []).map((e, i) => {
    const fromHit = hits[e.from]
    const toHit = hits[e.to]
    const bothHit = tracked.value && !!fromHit && !!toHit
    // 两端都命中且下游仍未完成 → 正在流转，给流光动画
    const flowing = bothHit && toHit.some((h) => h.state !== '完成')
    return {
      id: 'e' + i + '-' + e.from + '-' + e.to,
      source: e.from,
      target: e.to,
      // 正交连线：制造业流程图用直角折线比贝塞尔曲线更易读
      type: 'smoothstep',
      pathOptions: { borderRadius: 6, offset: Number(e.offset) || 8 },
      ...edgeHandles(e, layout),
      // 流转标签：写在线上，让人不用悬停就知道这条箭头是生单还是选单
      label: e.label ? tt(e.label) : '',
      labelShowBg: true,
      labelBgPadding: [5, 3],
      labelBgBorderRadius: 4,
      labelBgStyle: { fill: '#ffffff', fillOpacity: 1, stroke: '#e6e9ef' },
      labelStyle: { fill: '#59616e', fontSize: '11px' },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 14,
        height: 14,
        color: flowing ? '#1677ff' : (bothHit ? '#52c41a' : EDGE_COLOR),
      },
      style: bothHit
        ? {
            stroke: flowing ? '#1677ff' : '#95de64',
            strokeWidth: flowing ? 2.2 : 1.8,
            ...(e.dashed ? { strokeDasharray: '5 4' } : {}),
          }
        : {
            stroke: EDGE_COLOR,
            strokeWidth: 1.6,
            opacity: tracked.value ? 0.35 : 1,
            ...(e.dashed ? { strokeDasharray: '5 4' } : {}),
          },
      animated: flowing,
      class: flowing ? 'bo-edge-flow' : (bothHit ? 'bo-edge-done' : ''),
      // 边标签说明流转条件（到货/报检/合格入库/不合格退回…），追踪态下命中边才显示
      ...(e.label && (!tracked.value || bothHit)
        ? {
            label: e.label,
            labelShowBg: true,
            labelBgPadding: [4, 2],
            labelBgBorderRadius: 3,
            labelBgStyle: { fill: '#ffffff', fillOpacity: 0.95 },
            labelStyle: { fill: bothHit ? '#1677ff' : '#8c8c8c', fontSize: 11 },
          }
        : {}),
    }
  })
}

watch([cur, filterText, traceKey], async () => {
  buildFlow()
  await fitNow()
  // 二次补救：首屏容器高度可能在首帧后变化，再适配一次
  setTimeout(fitNow, 400)
}, { immediate: true })

onMounted(() => {
  fitNow()
  window.addEventListener('resize', fitNow)
})

onUnmounted(() => window.removeEventListener('resize', fitNow))

/** 面板进入权限：与后端 requireVisible 同口径（admin 全通，其余按 sys_role_panel 可见面板判定）。只拦“进入”，不影响流程图与相关单据的展示。 */
function canOpenPanel(panelCode) {
  if (!panelCode) return false
  return !!user.isAdmin || (Array.isArray(user.visiblePanels) && user.visiblePanels.includes(panelCode))
}

/** 流程图节点 / 右侧相关单据统一入口：无权限时提示并留在当前页，不跳转、不开页签。 */
function openPanel(panelCode, title) {
  if (!canOpenPanel(panelCode)) {
    ElMessage.warning(tt('无权限访问该面板') + '：' + tt(title || panelCode))
    return
  }
  go('/panelx/list/' + panelCode, title)
}

/**
 * 有些面板已经有专属的作业界面（不再是通用单据表格），这里统一改道。
 * 新增专属页面时在这里登记即可，节点点击、单据列表、追踪跳转都会跟着走。
 */
const CUSTOM_PAGES = {
  SALE_INVOICE: '/scm/invoiceWorkbench',
  PLAN: '/mes/plan',
}

/** 面板默认落点：有专属页面就去专属页面，否则走通用列表页。 */
function pathOf(panelCode) {
  return CUSTOM_PAGES[panelCode] || '/panelx/list/' + panelCode
}

/**
 * 打开具体单据：统一走列表页（PanelxList），不再跳表单页。
 * 一个单据只有一个样子——列表页有单据栏、列头筛选、表头内联编辑，能力比表单页全。
 * 带 code 定位到该单（PanelxList.load 读 ?code=），带 from 让列表页出现「返回」回到业务总览。
 */
function openDoc(panelCode, docNo, title) {
  if (!panelCode) return
  // 专属页面目前不支持按单据号定位，带 docNo 时仍走通用列表页
  const useCustom = !docNo && !!CUSTOM_PAGES[panelCode]
  if (!useCustom && !canOpenPanel(panelCode)) {
    ElMessage.warning(tt('无权限访问该面板') + '：' + tt(title || panelCode))
    return
  }
  const path = useCustom ? CUSTOM_PAGES[panelCode] : '/panelx/list/' + panelCode
  const query = { from: route.path }
  if (docNo) query.code = docNo
  router.push({ path, query })
  tabs.open({ path, title: title || docNo || panelCode, query })
}

function onNodeClick({ node }) {
  // 追踪态下打开命中的具体单据；有多张时打开第一张
  const hit = (traceHits.value[node.data.code] || [])[0]
  if (tracked.value && hit && hit.docNo) {
    openDoc(node.data.code, hit.docNo, node.data.label + '-' + hit.docNo)
    return
  }
  openPanel(node.data.code, node.data.label)
}

function go(path, title) {
  router.push(path)
  tabs.open({ path, title })
}
</script>

<style scoped>
/* ---------- 设计基线（Ant Design v6 design-kits/antd）----------
   字号 14/22 正文（fontSize/lineHeight）、圆角 6（borderRadius）、
   间距 8/16/24（8pt 网格）、边框 #d9d9d9（colorBorder）、
   次要字 rgba(0,0,0,.65)（colorTextSecondary）、占位 rgba(0,0,0,.45)（colorTextTertiary）。
   卡片 0 层：白底 + 1px 边框，不加阴影；仅 hover 态给边框变色。 */
.bo-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  box-sizing: border-box;
  padding: 16px 24px 24px;
  gap: 16px;
  background: #f5f5f5;
}

/* ---------- 页头 ---------- */
.bo-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  flex: 0 0 auto;
}

.bo-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  line-height: 24px;
  color: rgba(0, 0, 0, 0.88);
}

.bo-sub {
  margin: 4px 0 0;
  font-size: 14px;
  line-height: 22px;
  color: rgba(0, 0, 0, 0.65);
}

.bo-head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.bo-search {
  width: 220px;
}

/* ---------- 主体三栏 ---------- */
.bo-body {
  flex: 1;
  display: flex;
  gap: 16px;
  min-height: 0;
}

/* 左：模块导航 */
.bo-nav {
  width: 168px;
  flex: 0 0 168px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px;
  background: #fff;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  overflow-y: auto;
}

.bo-nav-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 40px;
  padding: 8px 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: rgba(0, 0, 0, 0.88);
  font-size: 14px;
  line-height: 22px;
  text-align: left;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.15s, color 0.15s;
}

.bo-nav-item:hover {
  background: rgba(0, 0, 0, 0.04);
}

.bo-nav-item.on {
  background: #e6f4ff;
  color: #1677ff;
  font-weight: 600;
}

.bo-nav-icon {
  color: rgba(0, 0, 0, 0.45);
}

.bo-nav-item.on .bo-nav-icon {
  color: #1677ff;
}

.bo-nav-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bo-nav-count {
  flex: 0 0 auto;
  min-width: 20px;
  height: 18px;
  padding: 0 6px;
  border-radius: 9px;
  background: rgba(0, 0, 0, 0.06);
  color: rgba(0, 0, 0, 0.45);
  font-size: 12px;
  line-height: 18px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.bo-nav-item.on .bo-nav-count {
  background: #1677ff;
  color: #fff;
}

/* 中：流程图 */
.bo-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  overflow: hidden;
}

.bo-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
  flex: 0 0 auto;
}

.bo-panel-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  line-height: 22px;
  color: rgba(0, 0, 0, 0.88);
}

.bo-panel-icon {
  color: #1677ff;
}

.bo-panel-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  line-height: 20px;
  color: rgba(0, 0, 0, 0.45);
  font-variant-numeric: tabular-nums;
}

.bo-meta-sep {
  color: rgba(0, 0, 0, 0.25);
}

.bo-meta-tip {
  color: rgba(0, 0, 0, 0.45);
}

.bo-canvas {
  flex: 1;
  min-height: 420px;
  background: #fafafa;
  overflow: hidden;
}

.bo-vueflow {
  width: 100%;
  height: 100%;
}

/* 流程图节点卡片：0 层边框，hover 才提边框色 */
.fn-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  /* 与 JS 的 CARD_W 保持一致：布局按 176 计算列宽，卡片超出就会压到相邻节点 */
  width: 176px;
  padding: 8px 10px;
  box-sizing: border-box;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.fn-card:hover {
  border-color: #1677ff;
  background: #fafcff;
}

/* 连线锚点：保留 Vue Flow 默认尺寸（改尺寸会让边锚点算错），只做视觉弱化 */
.fn-handle {
  background: transparent;
  border-color: transparent;
}

/* 无进入权限：节点仍然显示（保证流程图完整可读），仅置灰并禁止点击进入 */
.fn-card.no-perm {
  opacity: 0.65;
  cursor: not-allowed;
}

.fn-card.no-perm:hover {
  border-color: #d9d9d9;
  background: #fff;
}

.fn-card.dim {
  opacity: 0.35;
}

.fn-card.done {
  border-color: #b7eb8f;
  background: #f6ffed;
}

.fn-icon {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 6px;
  background: #e6f4ff;
  color: #1677ff;
}

.fn-card.done .fn-icon {
  background: #f6ffed;
  color: #52c41a;
}

.fn-body {
  min-width: 0;
  flex: 1;
}

.fn-label {
  font-size: 14px;
  line-height: 22px;
  color: rgba(0, 0, 0, 0.88);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.fn-code {
  font-size: 12px;
  line-height: 18px;
  color: rgba(0, 0, 0, 0.45);
  font-family: Consolas, "Courier New", monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.fn-mark {
  position: absolute;
  top: 6px;
  right: 6px;
  color: #52c41a;
  display: flex;
}

.fn-mark.stalled {
  color: #faad14;
}

/* 同环节多张单的汇总标记 */
.fn-more {
  position: absolute;
  top: 6px;
  right: 22px;
  min-width: 20px;
  padding: 0 5px;
  border-radius: 9px;
  background: #e6f4ff;
  color: #1677ff;
  font-size: 11px;
  line-height: 16px;
  text-align: center;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.fn-card.stalled .fn-more {
  background: #fff7e6;
  color: #faad14;
}

/* ---------- 追踪态节点 ---------- */
/* 已完成：绿色边框 + 浅绿底 */
.fn-card.state-done {
  border-color: #95de64;
  background: #f6ffed;
}

.fn-card.state-done .fn-icon {
  background: #f6ffed;
  color: #52c41a;
}

/* 进行中（含卡点）：蓝色边框，卡点再加琥珀色脉冲 */
.fn-card.state-doing {
  border-color: #1677ff;
  background: #f0f7ff;
}

.fn-card.state-doing .fn-icon {
  background: #e6f4ff;
  color: #1677ff;
}

.fn-card.stalled {
  border-color: #faad14;
  background: #fffbe6;
  animation: bo-pulse 1.8s ease-in-out infinite;
}

.fn-card.stalled .fn-icon {
  background: #fff7e6;
  color: #faad14;
}

@keyframes bo-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(250, 173, 20, 0); }
  50% { box-shadow: 0 0 0 4px rgba(250, 173, 20, 0.18); }
}

/* ---------- 追踪状态条 ---------- */
.bo-track-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding: 10px 16px;
  border: 1px solid #b7eb8f;
  border-radius: 6px;
  background: #f6ffed;
  flex: 0 0 auto;
}

.bo-track-bar.stalled {
  border-color: #ffe58f;
  background: #fffbe6;
}

.bo-track-main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 14px;
  line-height: 22px;
  color: rgba(0, 0, 0, 0.88);
}

.bo-track-icon {
  color: #52c41a;
}

.bo-track-bar.stalled .bo-track-icon {
  color: #faad14;
}

.bo-track-doc {
  font-weight: 600;
  font-family: Consolas, "Courier New", monospace;
}

.bo-track-sep {
  color: rgba(0, 0, 0, 0.25);
}

.bo-track-count {
  color: rgba(0, 0, 0, 0.65);
  font-variant-numeric: tabular-nums;
}

.bo-track-msg {
  color: rgba(0, 0, 0, 0.65);
}

.bo-track-msg.done {
  color: #52c41a;
}

.bo-track-legend {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12px;
  line-height: 20px;
  color: rgba(0, 0, 0, 0.65);
}

.lg {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.lg-dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  display: inline-block;
}

.lg-dot.done { background: #52c41a; }
.lg-dot.doing { background: #1677ff; }
.lg-dot.stalled { background: #faad14; }
.lg-dot.none { background: #d9d9d9; }

/* ---------- 跨模块提示 ---------- */
.bo-cross-mod {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 16px;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  background: #fff;
  font-size: 13px;
  line-height: 20px;
  flex: 0 0 auto;
}

.bo-cross-icon {
  color: rgba(0, 0, 0, 0.45);
}

.bo-cross-text {
  color: rgba(0, 0, 0, 0.65);
}

.bo-cross-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 28px;
  padding: 2px 10px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  background: #fff;
  color: rgba(0, 0, 0, 0.88);
  font-size: 13px;
  cursor: pointer;
  font-family: inherit;
  transition: border-color 0.15s, color 0.15s;
}

.bo-cross-btn:hover {
  border-color: #1677ff;
  color: #1677ff;
}

.bo-cross-btn.on {
  border-color: #1677ff;
  background: #e6f4ff;
  color: #1677ff;
  font-weight: 600;
}

.bo-cross-num {
  min-width: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.06);
  font-size: 11px;
  line-height: 16px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.bo-cross-btn.on .bo-cross-num {
  background: #1677ff;
  color: #fff;
}

/* 导航上的追踪命中标记 */
.bo-nav-hit {
  flex: 0 0 auto;
  min-width: 20px;
  height: 18px;
  padding: 0 6px;
  border-radius: 9px;
  background: #faad14;
  color: #fff;
  font-size: 12px;
  line-height: 18px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.bo-nav-item.hit:not(.on) .bo-nav-icon {
  color: #faad14;
}

/* ---------- 经过的单据路线 ---------- */
.bo-route {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 16px;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  background: #fff;
  flex: 0 0 auto;
}

.bo-route-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  line-height: 20px;
  color: rgba(0, 0, 0, 0.88);
}

.bo-route-icon {
  color: rgba(0, 0, 0, 0.45);
}

.bo-route-count {
  font-weight: 400;
  color: rgba(0, 0, 0, 0.45);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.bo-route-track {
  display: flex;
  align-items: center;
  gap: 4px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.bo-route-node {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 152px;
  padding: 6px 10px;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  background: #fff;
  text-align: left;
  cursor: pointer;
  font-family: inherit;
  flex: 0 0 auto;
  transition: border-color 0.15s, background 0.15s;
}

.bo-route-node:hover {
  border-color: #1677ff;
  background: #fafcff;
}

.bo-route-ic {
  color: rgba(0, 0, 0, 0.45);
}

.bo-route-label {
  font-size: 12px;
  line-height: 18px;
  color: rgba(0, 0, 0, 0.88);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bo-route-no {
  font-size: 12px;
  line-height: 17px;
  font-family: Consolas, "Courier New", monospace;
  color: rgba(0, 0, 0, 0.65);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bo-route-status {
  font-size: 11px;
  line-height: 16px;
  color: rgba(0, 0, 0, 0.45);
}

/* 已完成 */
.bo-route-node.kind-done {
  border-color: #95de64;
  background: #f6ffed;
}

.bo-route-node.kind-done .bo-route-ic {
  color: #52c41a;
}

/* 进行中 / 卡点 */
.bo-route-node.kind-doing {
  border-color: #1677ff;
  background: #f0f7ff;
}

.bo-route-node.kind-doing .bo-route-ic {
  color: #1677ff;
}

.bo-route-node.stalled {
  border-color: #faad14;
  background: #fffbe6;
}

.bo-route-node.stalled .bo-route-ic {
  color: #faad14;
}

.bo-route-node.stalled .bo-route-status {
  color: #d48806;
}

.bo-route-node.kind-aborted {
  border-color: #ffa39e;
  background: #fff1f0;
}

.bo-route-node.kind-aborted .bo-route-ic {
  color: #ff4d4f;
}

.bo-route-arrow {
  flex: 0 0 auto;
  display: flex;
  color: rgba(0, 0, 0, 0.25);
}

.bo-route-arrow.kind-done {
  color: #95de64;
}

.bo-route-arrow.kind-doing {
  color: #1677ff;
}

/* ---------- 选单弹窗 ---------- */
.bo-pick-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.bo-pick-select {
  width: 180px;
}

.bo-pick-kw {
  flex: 1;
  min-width: 160px;
}

.bo-pick-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 360px;
  overflow-y: auto;
}

.bo-pick-row {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-height: 40px;
  padding: 8px 12px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  font-size: 13px;
  line-height: 20px;
  text-align: left;
  cursor: pointer;
  font-family: inherit;
  color: rgba(0, 0, 0, 0.88);
  transition: background 0.15s;
}

.bo-pick-row:hover {
  background: #e6f4ff;
}

.bo-pick-no {
  flex: 0 0 auto;
  width: 168px;
  font-family: Consolas, "Courier New", monospace;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bo-pick-meta {
  flex: 1;
  min-width: 0;
  color: rgba(0, 0, 0, 0.65);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bo-pick-date {
  flex: 0 0 auto;
  width: 88px;
  color: rgba(0, 0, 0, 0.45);
  font-variant-numeric: tabular-nums;
}

.bo-pick-status {
  flex: 0 0 auto;
  padding: 0 8px;
  border-radius: 10px;
  font-size: 12px;
  line-height: 20px;
  background: rgba(0, 0, 0, 0.06);
  color: rgba(0, 0, 0, 0.65);
}

.bo-pick-status.st-done { background: #f6ffed; color: #52c41a; }
.bo-pick-status.st-doing { background: #e6f4ff; color: #1677ff; }
.bo-pick-status.st-aborted { background: #fff1f0; color: #ff4d4f; }

.bo-pick-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 32px 8px;
  text-align: center;
}

/* ---------- 流光：沿连线流动的虚线 ---------- */
/* Vue Flow 默认 animated 只移动虚线，这里加一层更明显的流光效果 */
:deep(.bo-edge-flow .vue-flow__edge-path) {
  stroke-dasharray: 8 6;
  animation: bo-flow 0.9s linear infinite;
}

@keyframes bo-flow {
  to {
    stroke-dashoffset: -28;
  }
}

/* 右：单据/档案/报表 */
.bo-side {
  width: 248px;
  flex: 0 0 248px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
  background: #fff;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  overflow-y: auto;
}

.bo-sec {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.bo-sec + .bo-sec {
  padding-top: 12px;
  border-top: 1px solid #f0f0f0;
}

.bo-sec-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  line-height: 20px;
  color: rgba(0, 0, 0, 0.88);
}

.bo-sec-icon {
  color: rgba(0, 0, 0, 0.45);
}

.bo-sec-title {
  flex: 1;
  min-width: 0;
}

.bo-sec-count {
  flex: 0 0 auto;
  min-width: 18px;
  padding: 0 6px;
  border-radius: 9px;
  background: rgba(0, 0, 0, 0.06);
  color: rgba(0, 0, 0, 0.45);
  font-size: 11px;
  line-height: 18px;
  text-align: center;
  font-weight: 400;
  font-variant-numeric: tabular-nums;
}

.bo-sec-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.bo-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 32px;
  padding: 6px 8px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: rgba(0, 0, 0, 0.88);
  font-size: 13px;
  line-height: 20px;
  text-align: left;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.15s, color 0.15s;
}

.bo-item:hover {
  background: #e6f4ff;
  color: #1677ff;
}

.bo-item:hover .bo-item-icon {
  color: #1677ff;
}

/* 无进入权限：相关单据/档案/报表仍然列出，仅置灰并阻止跳转 */
.bo-item.no-perm {
  opacity: 0.65;
  cursor: not-allowed;
}

.bo-item.no-perm:hover {
  background: transparent;
  color: rgba(0, 0, 0, 0.88);
}

.bo-item.no-perm:hover .bo-item-icon {
  color: rgba(0, 0, 0, 0.45);
}

.bo-item-icon {
  flex: 0 0 auto;
  color: rgba(0, 0, 0, 0.45);
}

.bo-item-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 空态：给主行动（设计库规则：空态不能只写"暂无数据"） */
.bo-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 24px 8px;
  text-align: center;
}

.bo-empty-icon {
  color: rgba(0, 0, 0, 0.25);
}

.bo-empty-text {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: rgba(0, 0, 0, 0.45);
}

/* ---------- 响应式 ---------- */
/* 平板：侧栏下移，流程图占满宽度 */
@media (max-width: 1100px) {
  .bo-body {
    flex-wrap: wrap;
  }
  .bo-nav {
    width: 100%;
    flex: 1 0 100%;
    flex-direction: row;
    flex-wrap: wrap;
    gap: 4px;
  }
  .bo-nav-item {
    width: auto;
    flex: 0 0 auto;
    min-height: 36px;
    padding: 6px 10px;
  }
  .bo-nav-label {
    flex: 0 0 auto;
  }
  .bo-main {
    flex: 1 0 100%;
  }
  .bo-side {
    width: 100%;
    flex: 1 0 100%;
    flex-direction: row;
    flex-wrap: wrap;
    gap: 16px;
    overflow-y: visible;
  }
  .bo-sec {
    flex: 1 1 200px;
  }
  .bo-sec + .bo-sec {
    padding-top: 0;
    border-top: 0;
  }
}

/* 手机：单列，扇出节点卡片收窄 */
@media (max-width: 768px) {
  .bo-page {
    padding: 12px 12px 16px;
    gap: 12px;
  }
  .bo-head {
    flex-direction: column;
    align-items: stretch;
  }
  .bo-search {
    width: 100%;
  }
  .bo-head-actions {
    width: 100%;
  }
  .bo-canvas {
    min-height: 320px;
  }
  .fn-card {
    width: 176px;
    padding: 8px 10px;
  }
  .fn-icon {
    width: 28px;
    height: 28px;
  }
  .bo-side {
    flex-direction: column;
  }
  .bo-sec {
    flex: 1 1 auto;
  }
}
</style>
