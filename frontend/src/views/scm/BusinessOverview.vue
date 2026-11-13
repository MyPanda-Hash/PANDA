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

    <!-- 追踪状态条：选中一张单据后，显示标准路线走到哪一步、还缺哪些环节 -->
    <div v-if="tracked" class="bo-track-bar" :class="{ stalled: traceInfo['卡点单号'] }">
      <div class="bo-track-main">
        <AntIcon :name="traceInfo['卡点单号'] ? 'clock-circle' : 'check-circle'" :size="16" class="bo-track-icon" />
        <span class="bo-track-doc">{{ traceInfo['单号'] }}</span>
        <span class="bo-track-sep">·</span>
        <span class="bo-track-route" :title="traceInfo['是否生产路线'] ? tt('该单没有足够库存，需要生产加工') : tt('该单库存够发货，直接出库')">
          {{ traceInfo['是否生产路线'] ? tt('生产路线') : tt('直接出库') }}
        </span>
        <span class="bo-track-sep">·</span>
        <span class="bo-track-count">{{ traceInfo['环节数'] }} {{ tt('个环节') }}</span>
        <span class="bo-track-sep">·</span>
        <span class="bo-track-count">{{ tt('已完成') }} {{ traceInfo['已完成数'] ?? 0 }}</span>
        <template v-if="traceInfo['待生成数']">
          <span class="bo-track-sep">·</span>
          <span class="bo-track-count">{{ tt('待生成') }} {{ traceInfo['待生成数'] }}</span>
        </template>
        <span class="bo-track-sep">·</span>
        <span v-if="traceInfo['卡点单号']" class="bo-track-msg">
          {{ tt('卡在') }} <b>{{ traceInfo['卡点单号'] }}</b> — {{ traceInfo['卡点原因'] }}
        </span>
        <span v-else-if="!traceInfo['待生成数']" class="bo-track-msg done">{{ tt('全流程已完成') }}</span>
      </div>
      <div class="bo-track-legend">
        <span class="lg"><i class="lg-dot done"></i>{{ tt('已完成') }}</span>
        <span class="lg"><i class="lg-dot doing"></i>{{ tt('进行中') }}</span>
        <span class="lg"><i class="lg-dot stalled"></i>{{ tt('卡点') }}</span>
        <span class="lg"><i class="lg-dot none"></i>{{ tt('待生成') }}</span>
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

    <!-- 单据流程路线：标准路线固定，已发生的带单号，未发生的显示「待生成」 -->
    <div v-if="tracked && traceSteps.length" class="bo-route">
      <div class="bo-route-head">
        <AntIcon name="node-index" :size="14" class="bo-route-icon" />
        <span class="bo-route-title">{{ tt('单据流程路线') }}</span>
        <span class="bo-route-count">
          {{ skeletonSteps }} {{ tt('步标准流程') }} · {{ traceInfo['环节数'] ?? traceSteps.length }} {{ tt('个环节') }}
          · {{ tt('已完成') }} {{ traceInfo['已完成数'] ?? 0 }}
          <template v-if="branchCount"> · {{ branchCount }} {{ tt('个检验分支') }}</template>
        </span>
      </div>
      <!-- 并行排产线：一个销售订单行一条线，各走各的排产与加工单 -->
      <div v-if="traceLanes.length" class="bo-lanes">
        <div class="bo-lane-head">
          <RouteNodeCard :node="routeHead" @open="openRouteDoc" @open-branch="openBranch" />
          <span class="bo-lane-head-hint">{{ traceLanes.length }} {{ tt('条排产线（按销售订单行并行）') }}</span>
        </div>
        <div class="bo-lane-list">
          <div v-for="lane in traceLanes" :key="lane['线号']" class="bo-lane">
            <span class="bo-lane-tag">
              {{ tt('线') }} {{ lane['线号'].replace('L', '') }}
              <span class="bo-lane-no">{{ lane['加工单号'] }}</span>
            </span>
            <div class="bo-lane-track">
              <template v-for="(n, i) in lane.nodes" :key="lane['线号'] + '-' + n['面板编码'] + '-' + i">
                <RouteNodeCard :node="n" @open="(node) => openRouteDoc(node, lane['加工单号'])" @open-branch="openBranch" />
                <span v-if="i < lane.nodes.length - 1" class="bo-route-arrow" :class="stepKind(lane.nodes[i + 1])">
                  <i v-if="edgeProcessLabel(n, lane.nodes[i + 1])" class="bo-arrow-label">
                    {{ tt(edgeProcessLabel(n, lane.nodes[i + 1])) }}
                  </i>
                  <AntIcon name="arrow-right" :size="14" />
                </span>
              </template>
            </div>
          </div>
        </div>
        <div v-if="routeTail.length" class="bo-lane-track bo-lane-tail">
          <template v-for="(n, i) in routeTail" :key="'tail-' + n['面板编码'] + '-' + i">
            <RouteNodeCard :node="n" @open="openRouteDoc" @open-branch="openBranch" />
            <span v-if="i < routeTail.length - 1" class="bo-route-arrow" :class="stepKind(routeTail[i + 1])">
              <AntIcon name="arrow-right" :size="14" />
            </span>
          </template>
        </div>
      </div>

      <!-- 没有排产线（直接出库 / 还没排产）：沿用单行路线 -->
      <div v-else class="bo-route-track">
        <template v-for="(s, i) in traceSteps" :key="s['面板编码'] + '-' + (s['单号'] || s['工序名称'] || i)">
          <RouteNodeCard :node="s" @open="openRouteDoc" @open-branch="openBranch" />
          <span v-if="i < traceSteps.length - 1" class="bo-route-arrow" :class="stepKind(traceSteps[i + 1])">
            <i v-if="edgeProcessLabel(s, traceSteps[i + 1])" class="bo-arrow-label">
              {{ tt(edgeProcessLabel(s, traceSteps[i + 1])) }}
            </i>
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
            <span class="bo-meta-item">{{ (cur.nodes || []).filter((n) => !n.monitor).length }} {{ tt('环节') }}</span>
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
                  {
                    dim: nodeProps.data.dim,
                    stalled: nodeProps.data.stalled,
                    'no-perm': !canOpenPanel(nodeProps.data.code),
                    'is-monitor': nodeProps.data.monitor,
                    // 走审批流的面板给标识；追踪到「卡在未审批」时加流光
                    'is-approval': nodeProps.data.approval,
                    'stalled-approval': nodeProps.data.approvalStall,
                  },
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
                <!-- 左侧中位出：左侧旁路（检验单 → 退货单）从行左侧留白绕下去 -->
                <Handle id="out-l" type="source" :position="Position.Left" class="fn-handle" />
                <Handle id="in-t-q" type="target" :position="Position.Top" style="left: 25%" class="fn-handle" />
                <Handle id="out-b-r" type="source" :position="Position.Bottom" style="left: 75%" class="fn-handle" />
                <Handle id="in-b-l" type="target" :position="Position.Bottom" style="left: 25%" class="fn-handle" />
                <!-- 右边缘进：右侧旁路（采购订单预开票 → 采购发票）从空白带拐进来，不和同行的横向边抢锚点 -->
                <Handle id="in-r" type="target" :position="Position.Right" class="fn-handle" />
                <div class="fn-icon">
                  <AntIcon :name="nodeProps.data.icon" :size="18" />
                </div>
                <div class="fn-body">
                  <div class="fn-label">{{ tt(nodeProps.data.label) }}</div>
                  <div class="fn-meta">
                    <span class="fn-code" :title="nodeProps.data.docNo || nodeProps.data.code">
                      <template v-if="nodeProps.data.docNo">{{ nodeProps.data.docNo }}</template>
                      <template v-else>{{ nodeProps.data.code }}</template>
                    </span>
                    <!-- 审批标识：走审批流的面板才有；追踪到「草稿/审批中卡点」时变成未审批并流光 -->
                    <span
                      v-if="nodeProps.data.approval"
                      class="fn-approval"
                      :class="{ pending: nodeProps.data.approvalStall }"
                      :title="nodeProps.data.approvalStall
                        ? tt('卡在这里：单据未审批') + '（' + (nodeProps.data.docNo || '') + '）'
                        : tt('该面板需要审批')"
                    >
                      {{ nodeProps.data.approvalStall ? tt('未审批') : tt('审批') }}
                    </span>
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
        <!-- 读法：画不出来的口径（勾稽、共用付款单）用一行说明补，不靠长连线去表达 -->
        <div v-if="cur.note" class="bo-flow-note">
          <span class="bo-flow-note-tag">{{ tt('读法') }}</span>
          <span>{{ tt(cur.note) }}</span>
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
              @click="openPanel(d.code, d.label, d.permCode)"
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
              @click="openPanel(d.code, d.label, d.permCode)"
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
              @click="openPanel(d.code, d.label, d.permCode)"
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
import RouteNodeCard from './components/RouteNodeCard.vue'
import { canvasHeightOf, defaultHandles, layoutNodes } from './flow-layout.js'
import { isApprovalStall, requiresApproval } from '@/business/approval-panels'
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
  MANU_ORDER: 'tool', PLAN: 'calendar', PROCESS_REPORT: 'profile',
  MATERIAL_ISSUE: 'form', MATERIAL_REQ: 'form',
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
  PLAN_ANALYSIS: 'dashboard',
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
    /** 计划：销售需求 → 计划分析（排产前全链路）→ 采购需求分析（排产后请购） */
    nodes: [
      { code: 'SO_ORDER', label: '销售订单' },
      // 计划分析不是面板，落点是专属页面；权限按销售订单判
      { code: 'PLAN_ANALYSIS', label: '计划分析', permCode: 'SO_ORDER' },
      { code: 'PU_REQ_ANALYSIS', label: '采购需求分析' },
    ],
    edges: [
      { from: 'SO_ORDER', to: 'PLAN_ANALYSIS', label: '排产前分析' },
      { from: 'PLAN_ANALYSIS', to: 'PU_REQ_ANALYSIS', label: '排产后采购需求' },
    ],
    pos: {
      SO_ORDER: [320, 0], PLAN_ANALYSIS: [320, 160], PU_REQ_ANALYSIS: [320, 320],
    },
    docs: [
      { code: 'PLAN_ANALYSIS', label: '计划分析', permCode: 'SO_ORDER' },
      { code: 'PU_REQ_ANALYSIS', label: '采购需求分析' },
    ],
    archives: [
      { code: 'INV', label: '存货' }, { code: 'BOM', label: '物料清单' }, { code: 'WH', label: '仓库' },
    ],
    reports: [],
  },
  {
    code: 'prod', name: '生产管理', icon: 'tool', color: '#1677ff',
    /** 生产主流程把质量链条画进来：领料 → 报工 → 报检 → 检验 → 首件/成品报检 → 入库 */
    /**
     * 生产主流程（2026-11-12 按批次报工定稿）：
     * 销售订单 → 工单排产 → 生产加工单 →（按工序排批次）排产批次 → 按批次报工 → 工序汇报
     * → 首件报检（首件合格才能报满）→ 全部工序报满 → 成品报检 → 检验合格入库。
     * 领料同样按批次：加工单 →（生产领料：按批次直接生成材料出库单并提交审批）→ 材料出库单。
     * 现场领料不走领料申请单；领料申请单仍保留，用于先申请后出库的审批场景。
     * 排产在加工单之前：按销售订单行排产生成加工单（单号 = 订单号-行号），生成即已审核；
     * 派工单是排产的批次输出，不再单独审批。检验只有首件检验（每道工序一次，分支）与完工检验（成品报检，入库前置）。
     */
    nodes: [
      { code: 'SO_ORDER', label: '销售订单' },
      { code: 'PLAN', label: '工单排产' },
      { code: 'MANU_ORDER', label: '生产加工单' },
      // 数据监控节点：派工单是排产批次的数据载体，不需要人在这上面操作，所以不进环节计数
      { code: 'DISPATCH', label: '排产批次', monitor: true },
      { code: 'MATERIAL_ISSUE', label: '生产领料', permCode: 'MATERIAL_OUT' },
      { code: 'PROCESS_REPORT', label: '工序汇报单' },
      { code: 'MATERIAL_OUT', label: '材料出库单' },
      { code: 'FIRST_INSPECT', label: '首件报检单' },
      { code: 'FINISH_INSPECT', label: '成品报检单' },
      { code: 'FINISH_IN', label: '产成品入库单' },
    ],
    edges: [
      { from: 'SO_ORDER', to: 'PLAN', label: '按订单行排产' },
      { from: 'PLAN', to: 'MANU_ORDER', label: '生成加工单' },
      { from: 'MANU_ORDER', to: 'DISPATCH', label: '按工序排批次', monitor: true },
      { from: 'MANU_ORDER', to: 'MATERIAL_ISSUE', label: '按批次领料' },
      { from: 'DISPATCH', to: 'PROCESS_REPORT', label: '按批次报工', monitor: true },
      { from: 'MATERIAL_ISSUE', to: 'MATERIAL_OUT', label: '生成出库单·审批通过' },
      { from: 'PROCESS_REPORT', to: 'FIRST_INSPECT', label: '首件检验' },
      { from: 'PROCESS_REPORT', to: 'FINISH_INSPECT', label: '全部工序报满' },
      { from: 'FINISH_INSPECT', to: 'FINISH_IN', label: '检验合格入库' },
    ],
    /** 分层布局：每条边都是上一层 → 下一层，节点之间留足间距，避免重叠和出画布 */
    pos: {
      SO_ORDER: [430, 0], PLAN: [430, 130], MANU_ORDER: [430, 260],
      DISPATCH: [150, 390], MATERIAL_ISSUE: [720, 390],
      PROCESS_REPORT: [150, 520], MATERIAL_OUT: [720, 520],
      FIRST_INSPECT: [60, 650], FINISH_INSPECT: [400, 650],
      FINISH_IN: [230, 780],
    },
    docs: [
      { code: 'MANU_ORDER', label: '生产加工单' }, { code: 'PLAN', label: '工单排产' },
      { code: 'DISPATCH', label: '工序派工单' },
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
    /**
     * 委外三段（列模式，pos 写 [列, 行]）：
     *   接单：销售订单 ——生成委外加工单——→ 委外加工单（推式：销售订单点「生成委外加工单」落草稿；
     *        委外加工单也仍可「选销售订单」拉式带入，两条路都保留）
     *   发料/结算：委外加工单 ——发料——→ 委外发料单；——结算——→ 委外加工费用单（按产成品明细委外单价×数量）
     *   回厂：委外加工单 ——回厂检验——→ 委外检验单 ——检验合格——→ 委外入库单
     * 入库那道门是真的：`PxService#requireOutsourceInspectionPassed` 在点「生成委外入库单」时检查
     * 该委外加工单的委外检验单——必须已审核、逐行判定合格/让步接收，否则拦下；入库量以合格量为上限。
     */
    layout: 'columns',
    nodes: [
      { code: 'SO_ORDER', label: '销售订单' },
      { code: 'OUTSOURCE_ORDER', label: '委外加工单' },
      { code: 'OUTSOURCE_ISSUE', label: '委外发料单' },
      { code: 'OUTSOURCE_INSPECTION', label: '委外检验单' },
      { code: 'OUTSOURCE_FEE', label: '委外加工费用单' },
      { code: 'OUTSOURCE_IN', label: '委外入库单' },
    ],
    edges: [
      { from: 'SO_ORDER', to: 'OUTSOURCE_ORDER', label: '生成委外加工单' },
      { from: 'OUTSOURCE_ORDER', to: 'OUTSOURCE_ISSUE', label: '发料', offset: 28 },
      { from: 'OUTSOURCE_ORDER', to: 'OUTSOURCE_INSPECTION', label: '回厂检验', offset: 28 },
      { from: 'OUTSOURCE_ORDER', to: 'OUTSOURCE_FEE', label: '结算', offset: 28 },
      { from: 'OUTSOURCE_INSPECTION', to: 'OUTSOURCE_IN', label: '检验合格' },
    ],
    pos: {
      SO_ORDER: [1, 0], OUTSOURCE_ORDER: [1, 1],
      OUTSOURCE_ISSUE: [0, 2], OUTSOURCE_INSPECTION: [1, 2], OUTSOURCE_FEE: [2, 2],
      OUTSOURCE_IN: [1, 3],
    },
    note: '销售订单可「生成委外加工单」（也可在委外单里选销售订单带入）；一旦走了委外，该订单就不能再生成生产加工单或销售出库单。回厂必须「生成委外检验单」并检验审核通过（判定合格或让步接收）才能生成委外入库单，入库数量以合格量为上限；加工费用单按产成品明细的委外单价×数量汇总。',
    docs: [
      { code: 'OUTSOURCE_ORDER', label: '委外加工单' }, { code: 'OUTSOURCE_ISSUE', label: '委外发料单' },
      { code: 'OUTSOURCE_INSPECTION', label: '委外检验单' },
      { code: 'OUTSOURCE_IN', label: '委外入库单' }, { code: 'OUTSOURCE_FEE', label: '委外加工费用单' },
      { code: 'SO_ORDER', label: '销售订单' },
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
    /**
     * 只画「直接销售出库」这一条闭环（库存现货直接发，不经过生产/委外）：
     *   报价单 → 销售订单 → 销售出库单（扣库存）→ 销货单（确认应收）→ 销售发票 → 收款单（按客户核销应收）
     * 三列分工：左列费用来源、中列销售主链、右列库存与退货，箭头一律从左往右 / 从上往下：
     *   库存：销售出库单 出库扣减；销售退货单 退货回增（审核还冲减应收 AR_RETURN）
     *   费用：费用单 ─↓生成分摊单→ 销售费用分摊单 ─→分摊到票→ 销售开票（费用由分摊单摊进发票/毛利）
     * 退货单是手工建单（来源单号填原单），没有「生单」入口，所以不画从出库单到退货单的箭头。
     * 直接销售不走产成品入库：FINISH_IN → 销货单 那条属于生产发货，不在这张图里。
     */
    layout: 'columns',
    /* 左侧留白 104：与质检/移动仓管保持同一套列网格，旁路标签不会压到卡片 */
    gutter: 104,
    nodes: [
      // 主链（直接销售出库）
      { code: 'QUOTE_ORDER', label: '报价单' },
      { code: 'SO_ORDER', label: '销售订单' },
      { code: 'SALE_OUT', label: '销售出库单' },
      { code: 'SALE_INV', label: '销货单' },
      { code: 'SALE_INVOICE', label: '销售开票' },
      { code: 'RECEIPT', label: '收款单' },
      // 库存与退货
      { code: 'STOCK_STATUS', label: '库存结存' },
      { code: 'SALE_RETURN', label: '销售退货单' },
      // 费用
      { code: 'SALE_COST_ALLOC', label: '销售费用分摊单' },
      { code: 'EXPENSE', label: '费用单' },
    ],
    edges: [
      { from: 'QUOTE_ORDER', to: 'SO_ORDER', label: '生单' },
      { from: 'SO_ORDER', to: 'SALE_OUT', label: '生单' },
      { from: 'SALE_OUT', to: 'SALE_INV', label: '选单' },
      { from: 'SALE_INV', to: 'SALE_INVOICE', label: '生单' },
      // 收款：应收挂在销货单，收款单按客户核销（核对的是销货单产生的应收）
      { from: 'SALE_INVOICE', to: 'RECEIPT', label: '收款核销' },
      // 库存联动：出库扣减、退货回增
      { from: 'SALE_OUT', to: 'STOCK_STATUS', label: '出库' },
      { from: 'SALE_RETURN', to: 'STOCK_STATUS', label: '退货入库' },
      // 费用闭环：费用单在左列，往下生分摊单，再向右分摊到发票（整条费用链从左往右读）
      { from: 'EXPENSE', to: 'SALE_COST_ALLOC', label: '生成分摊单' },
      { from: 'SALE_COST_ALLOC', to: 'SALE_INVOICE', label: '分摊到票' },
    ],
    pos: {
      // 左列：费用来源；中列：销售主链；右列：库存与退货
      EXPENSE: [0, 3], SALE_COST_ALLOC: [0, 4],
      QUOTE_ORDER: [1, 0], SO_ORDER: [1, 1], SALE_OUT: [1, 2], SALE_INV: [1, 3],
      SALE_INVOICE: [1, 4], RECEIPT: [1, 5],
      STOCK_STATUS: [2, 2], SALE_RETURN: [2, 3],
    },
    note: '闭环：报价 → 订单 → 出库（扣库存）→ 销货（确认应收）→ 开票 → 收款（按客户核销应收）；退货单手工建单，审核后库存回增并冲减应收；费用单生成销售费用分摊单，分摊到发票/客户后进销售毛利。',
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
     * 采购主链一列到底：请购 → 采购订单 → 到货（收料通知）→ 检验（拣验货）→ 采购入库 → 采购发票 → 付款。
     * 唯一的分知行是「采购入库单 | 采购退货单」：入库后库存退料，两单各自下推到发票。
     * 左旁路：到货单免检行不走检验，直接入库（折线走卡片列左侧空白带）。
     * 右旁路：采购订单预开票（先票后货），折线走右侧空白带从发票右边缘进入，货到后按入库行勾稽。
     * 发票口径：入库后开蓝票（先货后票）、退货开红字冲减应付；勾稽是发票状态而非单据流转，
     * 不画「发票 → 入库单」反向连线（会与开票箭头重叠），改在 note 里一行说明。
     * 付款与退款共用付款单（正数付款、负数退款）；2026-11-04 移除 T+ 进货单。
     */
    nodes: [
      { code: 'PU_REQ', label: '请购单' },
      { code: 'PU_ORDER', label: '采购订单' },
      { code: 'ARRIVAL_IN', label: '到货单' },
      { code: 'INSPECTION', label: '检验单' },
      { code: 'PURCHASE_IN', label: '采购入库单' },
      { code: 'PURCHASE_RETURN', label: '采购退货单' },
      { code: 'PU_INVOICE', label: '采购发票' },
      { code: 'RECEIPT_PAYABLE', label: '付款单/退款单' },
    ],
    edges: [
      { from: 'PU_REQ', to: 'PU_ORDER', label: '确认申请' },
      { from: 'PU_ORDER', to: 'ARRIVAL_IN', label: '通知收料' },
      { from: 'ARRIVAL_IN', to: 'INSPECTION', label: '送检' },
      // 免检直入：到货行免检/不送检时从到货单直接入库。左边缘出、顶部靠左进，
      // offset 40 让折线停在卡片列左侧 40px 的空白带里，不压检验单边框。
      {
        from: 'ARRIVAL_IN', to: 'PURCHASE_IN', label: '免检直入', dashed: true, offset: 40,
        sourceHandle: 'out-l-b', targetHandle: 'in-t-q',
        sourcePosition: Position.Left, targetPosition: Position.Top,
      },
      // offset 28：56px 行距里折线的横向段正好走中间，两条分支不贴卡片边
      { from: 'INSPECTION', to: 'PURCHASE_IN', label: '合格入库', offset: 28 },
      { from: 'INSPECTION', to: 'PURCHASE_RETURN', label: '不合格退回', offset: 28 },
      { from: 'PURCHASE_IN', to: 'PURCHASE_RETURN', label: '退货' },
      { from: 'PURCHASE_IN', to: 'PU_INVOICE', label: '先货后票', offset: 28 },
      { from: 'PURCHASE_RETURN', to: 'PU_INVOICE', label: '红字发票', offset: 28 },
      // 先票后货：订单预开票，右侧空白带下到发票右边缘（退货单挡住 424 以内，offset 140 才够宽）
      {
        from: 'PU_ORDER', to: 'PU_INVOICE', label: '先票后货', dashed: true, offset: 140,
        sourceHandle: 'out', targetHandle: 'in-r',
        sourcePosition: Position.Right, targetPosition: Position.Right,
      },
      { from: 'PU_INVOICE', to: 'RECEIPT_PAYABLE', label: '付款/退款' },
    ],
    pos: {
      PU_REQ: [200, 40], PU_ORDER: [200, 160], ARRIVAL_IN: [200, 280], INSPECTION: [200, 400],
      PURCHASE_IN: [120, 520], PURCHASE_RETURN: [360, 520],
      PU_INVOICE: [200, 640], RECEIPT_PAYABLE: [200, 760],
    },
    note: '免检行从到货单直入，无收料时可由采购订单直接入库；先票后货的票先到，货到后按入库行勾稽；付款与退款共用付款单。',
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
    /**
     * 移动仓管 = 仓库现场的移动作业，所有单据最后都动同一本账（库存结存）：
     *   入库：采购入库 / 产成品入库 / 其他入库（扫码收料，加库存）
     *   出库：销售出库 / 材料出库 / 其他出库（扫码拣货，减库存）
     *   库内：调拨单（仓间移动，一出一进）、库存盘点单（按盈亏调账）
     * 旧图把「采购入库 → 库存盘点 → 货位调整」连成一条线，语义不通：盘点不是入库的下游。
     * 货位调整单只换货位（原货位出、新货位进），数量不变，和盘点挤一行会互相压标签，图上不画，
     * 单据栏仍可进入。
     */
    nodes: [
      // 入库作业
      { code: 'PURCHASE_IN', label: '采购入库单' },
      { code: 'FINISH_IN', label: '产成品入库单' },
      { code: 'OTHER_IN', label: '其他入库单' },
      // 库内作业 + 结存
      { code: 'TRANSFER', label: '调拨单' },
      { code: 'STOCK_STATUS', label: '库存结存' },
      { code: 'STOCK_CHECK', label: '库存盘点单' },
      // 出库作业
      { code: 'SALE_OUT', label: '销售出库单' },
      { code: 'MATERIAL_OUT', label: '材料出库单' },
      { code: 'OTHER_OUT', label: '其他出库单' },
    ],
    edges: [
      { from: 'PURCHASE_IN', to: 'STOCK_STATUS', label: '入库' },
      { from: 'FINISH_IN', to: 'STOCK_STATUS', label: '入库' },
      { from: 'OTHER_IN', to: 'STOCK_STATUS', label: '入库' },
      { from: 'STOCK_STATUS', to: 'SALE_OUT', label: '出库' },
      { from: 'STOCK_STATUS', to: 'MATERIAL_OUT', label: '出库' },
      { from: 'STOCK_STATUS', to: 'OTHER_OUT', label: '出库' },
      // 仓间移动：一张单同时出调出仓、入调入仓，结存总量不变
      { from: 'TRANSFER', to: 'STOCK_STATUS', label: '仓间移动' },
      // 盘点取账面数，审核即按盈亏调账
      { from: 'STOCK_STATUS', to: 'STOCK_CHECK', label: '账面实盘' },
    ],
    pos: {
      PURCHASE_IN: [40, 40], FINISH_IN: [420, 40], OTHER_IN: [800, 40],
      TRANSFER: [40, 220], STOCK_STATUS: [420, 220], STOCK_CHECK: [800, 220],
      SALE_OUT: [40, 400], MATERIAL_OUT: [420, 400], OTHER_OUT: [800, 400],
    },
    note: '入库加、出库减，调拨只在仓库之间搬（一出一进、总量不变），盘点按盈亏调账；货位调整单只换货位不改数量，单据栏可进。',
    docs: [
      { code: 'PURCHASE_IN', label: '采购入库单' }, { code: 'FINISH_IN', label: '产成品入库单' },
      { code: 'OTHER_IN', label: '其他入库单' },
      { code: 'SALE_OUT', label: '销售出库单' }, { code: 'MATERIAL_OUT', label: '材料出库单' },
      { code: 'OTHER_OUT', label: '其他出库单' },
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
    /**
     * 序列号跟着单据走：入库单据登记、出库单据核销，两张报表实时聚合（弃审自动回滚）。
     * 序列号登记单只做历史补录/导入，不再走审批，也不再是唯一入口。
     */
    nodes: [
      { code: 'INV', label: '存货(启用序列号)' },
      { code: 'PURCHASE_IN', label: '采购入库单' },
      { code: 'SERIAL_NO', label: '序列号登记单' },
      { code: 'SALE_OUT', label: '销售出库单' },
      { code: 'SERIAL_STATUS', label: '序列号状况表' },
      { code: 'SERIAL_TRACE', label: '序列号跟踪表' },
    ],
    edges: [
      { from: 'INV', to: 'PURCHASE_IN', label: '启用' },
      { from: 'INV', to: 'SERIAL_NO', label: '补录' },
      { from: 'PURCHASE_IN', to: 'SALE_OUT', label: '核销' },
      { from: 'SALE_OUT', to: 'SERIAL_STATUS', label: '在库' },
      { from: 'SALE_OUT', to: 'SERIAL_TRACE', label: '跟踪' },
    ],
    pos: {
      INV: [0, 0],
      PURCHASE_IN: [0, 120], SERIAL_NO: [300, 120],
      SALE_OUT: [0, 240],
      SERIAL_STATUS: [0, 360], SERIAL_TRACE: [300, 360],
    },
    note: '入库单（采购/产成品/委外/其他）登记序列号、出库单（销售/材料/其他）按序列号核销，数量必须一一对应且同一存货全局唯一；状况表只看在库，跟踪表带出入库单号。',
    archives: [{ code: 'INV', label: '存货' }, { code: 'WH', label: '仓库' }],
    reports: [],
  },
  {
    code: 'qc', name: '质量管理', icon: 'safety-certificate', color: '#ff4d4f',
    /**
     * 质量单据按三条链排（列模式，pos 写 [列, 行]）：
     *   来料（左）到货单 → 来料/成品检验单 → 采购入库单；检验不合格从左侧留白绕到退货单
     *   生产（中）生产加工单 → 首件报检单 →（放行）工序汇报单
     *   完工（右）生产加工单 → 成品报检单 → 产成品入库单
     * 关键顺序：**首检是报工前置**——首件报检单没审核通过，该工序任何批次都报不了工
     * （`PxService#firstInspectBlockReason`：未建单/待审批/不合格都拒绝），
     * 所以箭头是「首件报检单 → 工序汇报单」，不是报工生成首检。
     * 只画在用的链：工序报检单 / 生产过程检验单 至今 0 张单（生单入口也没有人用），
     * 首件报检单送检验单同样没用过，三条都从流程图里去掉（面板和菜单仍然保留）。
     */
    layout: 'columns',
    /* 左侧留白 104：旁路折线跑道在卡片左 76px，标签（居中在跑道与卡片之间）才不会压到卡片边框 */
    gutter: 104,
    nodes: [
      // 来料链
      { code: 'ARRIVAL_IN', label: '到货单' },
      { code: 'INSPECTION', label: '来料/成品检验单' },
      { code: 'PURCHASE_IN', label: '采购入库单' },
      { code: 'PURCHASE_RETURN', label: '采购退货单' },
      // 生产链
      { code: 'MANU_ORDER', label: '生产加工单' },
      { code: 'FIRST_INSPECT', label: '首件报检单' },
      { code: 'PROCESS_REPORT', label: '工序汇报单' },
      // 完工链
      { code: 'FINISH_INSPECT', label: '成品报检单' },
      { code: 'FINISH_IN', label: '产成品入库单' },
    ],
    edges: [
      { from: 'ARRIVAL_IN', to: 'INSPECTION', label: '报检' },
      { from: 'INSPECTION', to: 'PURCHASE_IN', label: '合格入库' },
      // 检验不合格：未入库直接退回，从左侧留白绕下去，不穿过采购入库单
      {
        from: 'INSPECTION', to: 'PURCHASE_RETURN', label: '不合格退回', offset: 76,
        sourceHandle: 'out-l', targetHandle: 'in',
        sourcePosition: Position.Left, targetPosition: Position.Left,
      },
      { from: 'MANU_ORDER', to: 'FIRST_INSPECT', label: '首件' },
      // 首检通过才放行报工（报工前置门，不是报工的副作用）
      { from: 'FIRST_INSPECT', to: 'PROCESS_REPORT', label: '放行报工' },
      // 全部工序报满才能报检成品；报检审核通过才产成品入库
      {
        from: 'MANU_ORDER', to: 'FINISH_INSPECT', label: '报检', offset: 28,
        sourceHandle: 'out-b-r', targetHandle: 'in-t',
        sourcePosition: Position.Bottom, targetPosition: Position.Top,
      },
      { from: 'FINISH_INSPECT', to: 'FINISH_IN', label: '合格入库' },
    ],
    pos: {
      ARRIVAL_IN: [0, 0], INSPECTION: [0, 1], PURCHASE_IN: [0, 2], PURCHASE_RETURN: [0, 3],
      MANU_ORDER: [1, 0], FIRST_INSPECT: [1, 1], PROCESS_REPORT: [1, 2],
      FINISH_INSPECT: [2, 1], FINISH_IN: [2, 2],
    },
    note: '先首检后报工：首件报检单审核通过，该工序才允许报工；成品报检单审核通过才能产成品入库；检验单按来源分流到采购入库或产成品入库。',
    // 只列在用的单据：工序报检单、生产过程检验单 至今 0 张单，不再出现在本模块入口
    // （面板与质量管理菜单 QM09 / QM16 保留，从菜单仍可进入）
    docs: [
      { code: 'INSPECTION', label: '来料/成品检验单' }, { code: 'ARRIVAL_IN', label: '到货单' },
      { code: 'FINISH_INSPECT', label: '成品报检单' }, { code: 'FIRST_INSPECT', label: '首件报检单' },
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

// 画布高度按「排完版」的最后一行算，不用原始 pos（手摆坐标会多出几百像素空白）
const canvasH = computed(() => canvasHeightOf(layoutNodes(cur.value)))

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
const traceInfo = ref({ 单号: '', 环节数: 0, 已完成数: 0, 待生成数: 0, 卡点单号: '', 卡点原因: '', steps: [], route: [] })
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
    out[code].push({
      docNo: s['单号'],
      state: s['阶段'] || '待开始',
      isStalled: s['是否卡点'],
      // 卡点的成因：单据停在草稿/审批中 → 卡在未审批（节点要流光提示）
      unapprovedStall: isApprovalStall(s),
    })
  }
  return out
})

function clearTrace() {
  trackedNo.value = ''
  traceInfo.value = { 单号: '', 环节数: 0, 已完成数: 0, 待生成数: 0, 卡点单号: '', 卡点原因: '', steps: [], route: [] }
  traceKey.value++
}

/**
 * 流程图节点 ↔ 追踪面板：排产环节在工作台（PLAN）完成，落地单据是工序派工单（DISPATCH）。
 * 节点上没有 PLAN 的单据，命中要顺着别名找，否则「工单排产」永远亮不起来。
 */
const NODE_PANEL_ALIAS = { PLAN: ['DISPATCH'], MATERIAL_ISSUE: ['MATERIAL_REQ'] }

/** 某节点在追踪结果里命中的单据（含别名面板；带回真实面板编码，点开时按真实面板走）。 */
function hitsOf(code) {
  const out = (traceHits.value[code] || []).map((hit) => ({ ...hit, panelCode: code }))
  for (const alias of NODE_PANEL_ALIAS[code] || []) {
    out.push(...(traceHits.value[alias] || []).map((hit) => ({ ...hit, panelCode: alias })))
  }
  return out
}

/** 某模块里有多少个环节命中追踪结果（用于在导航上打标记）。 */
function moduleHitCount(module) {
  if (!tracked.value) return 0
  return (module.nodes || []).filter((n) => hitsOf(n.code).length).length
}

/** 追踪命中的模块列表：一个单据可能跨多个模块（如销售订单 → 委外加工单）。 */
const hitModules = computed(() => {
  if (!tracked.value) return []
  return modules.filter((m) => moduleHitCount(m) > 0)
})

/**
 * 单据流程路线：后端返回的标准路线（固定环节序列，含还没发生的环节）。
 * 老版本接口没有 route 时回退到实际单据步骤，保证页面不空。
 */
const traceLanes = computed(() => traceInfo.value.lanes || [])
/** 并行线的共同头：销售订单 */
const routeHead = computed(() => traceSteps.value.find((s) => s['面板编码'] === 'SO_ORDER')
  || { 面板编码: 'SO_ORDER', 环节: '销售订单', 单号: '', 阶段: '待开始', 是否已发生: false, branches: [] })
/** 并行线的共同尾：出库、销货、发票 */
const routeTail = computed(() => traceSteps.value.filter((s) =>
  ['SALE_OUT', 'SALE_INV', 'SALE_INVOICE'].includes(s['面板编码'])))

const traceSteps = computed(() => {
  const route = traceInfo.value.route
  return (route && route.length ? route : traceInfo.value.steps) || []
})

/**
 * 标准路线的「步数」：按面板去重后的环节数（工序派工/工序汇报按工序展开，
 * 面板数仍是 1），用来对照 11 步骨架与实际展开的节点数。
 */
const skeletonSteps = computed(() => {
  const seen = new Set()
  let n = 0
  for (const s of traceSteps.value) {
    const code = s['面板编码']
    if (!code || seen.has(code)) continue
    seen.add(code)
    n += 1
  }
  return n
})

/** 分支总数（首件检验单）：只统计展开后的分支，不进主干步数。 */
const branchCount = computed(() => {
  let n = 0
  for (const s of traceSteps.value) n += (s['branches'] || []).length
  return n
})

/**
 * 箭头上的过程名：有些环节是「动作」不是「单据」，不给它画节点框，只标在流转箭头上。
 * 目前只有一段——生产加工单 --生产领料--> 材料出库单（现场领料直接出库，不再有领料申请单节点）。
 */
const EDGE_PROCESS_LABELS = { 'MANU_ORDER>MATERIAL_OUT': '生产领料' }

function edgeProcessLabel(from, to) {
  if (!from || !to) return ''
  return EDGE_PROCESS_LABELS[(from['面板编码'] || '') + '>' + (to['面板编码'] || '')] || ''
}

/** 环节名 + 工序名：工序汇报单要显示是哪道工序，追踪才看得懂。 */
function routeLabel(s) {
  const base = tt(s['环节'])
  return s['工序名称'] ? base + ' · ' + s['工序名称'] : base
}

/** 分支单据不在主干上，但同样可以点开。 */
function openBranch(b) {
  if (b['是否已发生'] === false) return
  openDoc(b['面板编码'], b['单号'], tt(b['环节']) + '-' + b['单号'])
}

/** 工单排产工作台：排产不是单据页，按加工单号进 /mes/plan。 */
function openPlan(moNo) {
  const path = CUSTOM_PAGES.PLAN
  const query = { from: route.path }
  if (moNo) query.code = moNo
  router.push({ path, query })
  tabs.open({ path, title: moNo ? tt('工单排产') + '-' + moNo : tt('工单排产'), query })
}

/** 待生成的环节没有单据可打开；排产环节进工作台，其余进单据页。 */
function openRouteDoc(s, moNo) {
  if (s['是否已发生'] === false) return
  if (s['面板编码'] === 'DISPATCH') {
    openPlan(moNo || traceInfo.value['加工单号'] || '')
    return
  }
  openDoc(s['面板编码'], s['单号'], routeLabel(s) + '-' + s['单号'])
}

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
    traceInfo.value = info || { 单号: docNo, 环节数: 0, 已完成数: 0, 待生成数: 0, 卡点单号: '', 卡点原因: '', steps: [], route: [] }
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
  requestAnimationFrame(() => fitView({ padding: 0.03, maxZoom: 1, minZoom: 0.75 }))
}

const EDGE_COLOR = '#98a2b3'

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
  // 方向规则集中在 flow-layout：跨行竖直、同行按左右选边（目标在左时左出右进）
  const h = defaultHandles(a, b)
  const position = {
    bottom: Position.Bottom, top: Position.Top, left: Position.Left, right: Position.Right,
  }
  return {
    sourceHandle: h.sourceHandle,
    targetHandle: h.targetHandle,
    sourcePosition: position[h.sourcePosition],
    targetPosition: position[h.targetPosition],
  }
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
    const hitList = hitsOf(n.code)
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
        monitor: !!n.monitor,
        permCode: n.permCode || '',
        // 审批面板标识 + 「卡在未审批」标记（只有追踪态才可能为真）
        approval: requiresApproval(n.code),
        approvalStall: hitList.some((h) => h.unapprovedStall),
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
      class: e.monitor ? 'bo-edge-monitor' : '',
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

/**
 * 流程图节点 / 右侧相关单据统一入口：无权限时提示并留在当前页，不跳转、不开页签。
 *
 * @param permCode 权限判定用的面板编码；计划分析这类专属页面没有自己的面板，借上游面板的权限
 */
function openPanel(panelCode, title, permCode) {
  const perm = permCode || panelCode
  if (!canOpenPanel(perm)) {
    ElMessage.warning(tt('无权限访问该面板') + '：' + tt(title || panelCode))
    return
  }
  go(pathOf(panelCode), title)
}

/**
 * 有些面板已经有专属的作业界面（不再是通用单据表格），这里统一改道。
 * 新增专属页面时在这里登记即可，节点点击、单据列表、追踪跳转都会跟着走。
 */
const CUSTOM_PAGES = {
  SALE_INVOICE: '/scm/invoiceWorkbench',
  PLAN: '/mes/plan',
  PLAN_ANALYSIS: '/mes/planAnalysis',
  MATERIAL_ISSUE: '/mes/materialIssue',
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
  // 工单排产是工作台不是单据面板：任何入口都进 /mes/plan，并按加工单号定位
  if (panelCode === 'PLAN') {
    openPlan(traceInfo.value['加工单号'] || '')
    return
  }
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
  const hit = hitsOf(node.data.code)[0]
  if (tracked.value && hit && hit.docNo) {
    // 别名节点（生产领料 ← 领料申请单）要用命中单据的真实面板打开，否则会落到不存在的面板
    openDoc(hit.panelCode || node.data.code, hit.docNo, node.data.label + '-' + hit.docNo)
    return
  }
  openPanel(node.data.code, node.data.label, node.data.permCode)
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
  /* 只能写 min-height：写 height:100% 时内容一超过视口高度，本级不撑开、
     外层 .portal-content 的滚动高度就等于视口，于是整页被裁掉且滚不动（追踪 39 环节时必然触发） */
  min-height: 100%;
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
  /* 不再用 flex:1 + min-height:0 硬压：追踪路线长的时候图表会被压没。
     内容高就撑开（页面滚动），内容矮就填满剩余高度 */
  flex: 1 0 auto;
  display: flex;
  gap: 16px;
  min-height: 520px;
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
  /* 追踪路线出现后画布可用高度变小，内容比卡片高时让卡片自己滚，不裁掉下面的节点 */
  overflow-y: auto;
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
  /* 高度由 canvasH 内联给出（按节点最大纵坐标算），父级滚动，避免底部节点被裁 */
  flex: 0 0 auto;
  min-height: 420px;
  background: #fafafa;
  overflow: hidden;
}

.bo-vueflow {
  width: 100%;
  height: 100%;
}

/* 读法说明：流程图下方一行，解释画不出成节点的口径（勾稽、共用单据） */
.bo-flow-note {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 8px 2px 10px;
  font-size: 12px;
  line-height: 20px;
  color: rgba(0, 0, 0, 0.45);
}

.bo-flow-note-tag {
  flex: 0 0 auto;
  padding: 0 6px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  color: rgba(0, 0, 0, 0.65);
  font-size: 11px;
  line-height: 18px;
}

/* 流程图节点卡片：0 层边框，hover 才提边框色 */
.fn-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  /* 与 flow-layout.js 的 CARD_W 保持一致：布局按 176 计算列宽，卡片超出就会压到相邻节点 */
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

/* 编号 + 审批标识同一行：编号占满剩余宽度，标识固定宽度不被挤掉 */
.fn-meta {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* 审批标识：默认灰底小字，不抢注意力；追踪到卡在未审批时转琥珀色 */
.fn-approval {
  flex: 0 0 auto;
  padding: 0 4px;
  border: 1px solid var(--d-border-strong, #dce4e1);
  border-radius: 3px;
  font-size: 10px;
  line-height: 14px;
  color: var(--d-text-3, #8b9893);
  white-space: nowrap;
}

.fn-approval.pending {
  border-color: #ffd591;
  background: #fffbe6;
  color: #d46b08;
}

/* 卡在「未审批」的节点：琥珀色边框上走一道流光，动画只澄清状态，不做装饰 */
.fn-card.stalled-approval {
  border-color: #faad14;
  background: #fffbe6;
  overflow: hidden;
}

.fn-card.stalled-approval::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1px;
  background: conic-gradient(
    from var(--fn-angle, 0deg),
    rgba(250, 173, 20, 0) 0deg,
    rgba(250, 173, 20, 0) 240deg,
    rgba(250, 173, 20, 0.9) 310deg,
    rgba(250, 173, 20, 0) 360deg
  );
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  mask-composite: exclude;
  animation: fn-approval-flow 2.4s linear infinite;
  pointer-events: none;
}

@property --fn-angle {
  syntax: '<angle>';
  initial-value: 0deg;
  inherits: false;
}

@keyframes fn-approval-flow {
  to { --fn-angle: 360deg; }
}

/* 降低动效偏好：不转流光，只留静态琥珀边框 */
@media (prefers-reduced-motion: reduce) {
  .fn-card.stalled-approval::before {
    animation: none;
    background: none;
  }
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
  /* 与审批角标同一行：编号自己收缩出省略号，角标不被挤掉 */
  flex: 1;
  min-width: 0;
  /* 11px：PROCESS_INSPECT_APPLY / PROCESS_INSPECTION 这类 18-19 字符的编码要能整串显示，
     卡片正文宽 128px，12px 等宽字只能放 17 位，会被截成同样的 PROCESS_INSPEC… */
  font-size: 11px;
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

/* 路线类型：生产路线 / 直接出库 */
.bo-track-route {
  padding: 1px 8px;
  border: 1px solid #d9d9d9;
  border-radius: 10px;
  background: #fff;
  font-size: 12px;
  line-height: 18px;
  color: rgba(0, 0, 0, 0.65);
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
  align-items: flex-start;
  gap: 4px;
  overflow-x: auto;
  padding-bottom: 6px;
}

/* 并行排产线：一个订单行一行，各自横向展开 */
.bo-lanes {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-bottom: 6px;
}

.bo-lane-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.bo-lane-head-hint {
  font-size: 12px;
  line-height: 20px;
  color: rgba(0, 0, 0, 0.45);
}

.bo-lane-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-left: 12px;
  border-left: 1px dashed #d9d9d9;
}

.bo-lane {
  display: flex;
  align-items: center;
  gap: 8px;
}

.bo-lane-tag {
  flex: 0 0 auto;
  min-width: 96px;
  padding: 2px 6px;
  border: 1px solid #e6f4ff;
  border-radius: 4px;
  background: #f7fbff;
  font-size: 11px;
  line-height: 16px;
  color: #1677ff;
  font-variant-numeric: tabular-nums;
}

.bo-lane-no {
  display: block;
  font-family: Consolas, "Courier New", monospace;
  color: rgba(0, 0, 0, 0.55);
}

.bo-lane-track {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  overflow-x: auto;
  padding-bottom: 2px;
}

/* 出货段：所有排产线汇合后共用 */
.bo-lane-tail {
  padding-top: 6px;
  border-top: 1px dashed #f0f0f0;
}

/* 箭头上的过程名：灰底小字，压在箭头正上方，不抢节点的注意力 */
.bo-arrow-label {
  position: absolute;
  top: -14px;
  left: 50%;
  transform: translateX(-50%);
  padding: 0 4px;
  border-radius: 3px;
  background: var(--t-card-bg, #fff);
  font-size: 10px;
  font-style: normal;
  line-height: 14px;
  color: var(--t-text-3, #8b9893);
  white-space: nowrap;
}

.bo-route-arrow {
  flex: 0 0 auto;
  position: relative;
  display: flex;
  /* 与主干卡片垂直居中，分支在下面不影响箭头位置 */
  margin-top: 34px;
  color: rgba(0, 0, 0, 0.25);
}

.bo-route-arrow.kind-done {
  color: #95de64;
}

.bo-route-arrow.kind-doing {
  color: #1677ff;
}

.bo-route-arrow.kind-pending {
  color: rgba(0, 0, 0, 0.15);
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

<style scoped>
.fn-card.is-monitor {
  border-style: dashed;
  border-color: #d9d9d9;
  background: #fafafa;
  min-width: 110px;
  padding: 6px 10px;
  opacity: 0.9;
}
.fn-card.is-monitor::after {
  content: '监控';
  display: block;
  font-size: 10px;
  line-height: 14px;
  color: #a6a6a6;
}
.bo-edge-monitor :deep(.vue-flow__edge-path) {
  stroke-dasharray: 4 3;
  stroke: #d9d9d9 !important;
}
</style>
