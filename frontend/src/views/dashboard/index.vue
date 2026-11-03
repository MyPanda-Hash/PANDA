<template>
  <div class="dash adx">
    <!-- 页头：安静标题 + 一行元信息 + 主操作（不做大色块） -->
    <header class="d-head">
      <div class="d-head__text">
        <h1 class="d-head__title">{{ tt(greeting) }}，{{ user.realName }}</h1>
        <p class="d-head__meta">
          <span class="d-shift">
            <el-icon><component :is="shift.key === 'day' ? 'Sunny' : 'Moon'" /></el-icon>
            {{ tt(shift.name) }}<em>{{ shift.range }}</em>
          </span>
          <span class="d-sep">·</span>
          <span>{{ user.factoryName }}</span>
          <span class="d-sep">·</span>
          <span class="d-num">{{ today }} {{ nowTime }}</span>
          <span class="d-sep">·</span>
          <span class="d-sync" :class="{ err: loadError }">
            <i></i>{{ loadError ? tt('数据同步异常') : `${tt('数据更新于')} ${tt(lastUpdatedText)}` }}
          </span>
        </p>
      </div>
      <div class="d-head__actions">
        <el-button
          v-for="(q, index) in quickEntries"
          :key="q.key"
          :type="index === 0 ? 'primary' : 'default'"
          @click="go(q.path, q.title)"
        >{{ tt(q.title) }}</el-button>
        <el-tooltip :content="tt('立即刷新')" placement="top">
          <el-button class="d-refresh" :loading="loadingStats" aria-label="立即刷新" @click="load">
            <el-icon><Refresh /></el-icon>
          </el-button>
        </el-tooltip>
      </div>
    </header>

    <!-- 分段控制器：概览 / 生产 / 库存 / 销售 / 质量 -->
    <div class="d-seg" role="tablist">
      <button
        v-for="m in MODULES"
        :key="m.key"
        class="d-seg__item"
        :class="{ on: mod === m.key }"
        role="tab"
        :aria-selected="mod === m.key ? 'true' : 'false'"
        @click="mod = m.key"
      >
        <el-icon><component :is="m.icon" /></el-icon><span>{{ tt(m.title) }}</span>
      </button>
    </div>

    <!-- ===== 概览 ===== -->
    <template v-if="mod === 'overview'">
      <div class="d-grid">
        <!-- 四个数字先看 -->
        <template v-if="desk.showKpi">
          <article v-for="metric in kpis" :key="metric.title" class="d-card d-card--tight d-span-3 d-stat">
            <div class="d-stat__label">
              <el-icon><component :is="metric.icon" /></el-icon>
              <span>{{ metric.title }}</span>
            </div>
            <div class="d-stat__value">
              <strong>{{ metric.value }}</strong>
              <span v-if="metric.unit">{{ metric.unit }}</span>
            </div>
            <div class="d-stat__foot">
              <span>{{ metric.meta }}</span>
              <span class="d-tag" :class="`is-${metric.tone}`">{{ metric.state }}</span>
            </div>
          </article>
        </template>

        <!-- 生产执行核心 -->
        <section v-if="desk.showProgress" class="d-card d-span-8">
          <div class="d-card__head">
            <div>
              <h2 class="d-card__title">{{ tt('生产执行核心') }}</h2>
              <p class="d-card__desc">{{ tt('工单生命周期与近 7 天执行脉冲') }}</p>
            </div>
            <span class="d-live"><i></i>{{ tt('实时') }}</span>
          </div>

          <div class="d-core">
            <div class="d-core__gauge">
              <div class="d-ring">
                <svg viewBox="0 0 120 120" aria-hidden="true">
                  <circle class="d-ring__track" cx="60" cy="60" r="48" />
                  <circle
                    class="d-ring__value"
                    cx="60" cy="60" r="48"
                    :style="{ strokeDasharray: RING_C, strokeDashoffset: gaugeOffset }"
                  />
                </svg>
                <div class="d-ring__text">
                  <strong>{{ completionRate }}<em>%</em></strong>
                  <span>{{ tt('工单闭环率') }}</span>
                </div>
              </div>
              <dl class="d-facts">
                <div><dt>{{ tt('总工单') }}</dt><dd class="d-num">{{ productionTotal }}</dd></div>
                <div><dt>{{ tt('执行中') }}</dt><dd class="d-num">{{ productionRunning }}</dd></div>
                <div><dt>{{ tt('已完工') }}</dt><dd class="d-num">{{ productionDone }}</dd></div>
              </dl>
            </div>

            <div class="d-core__main">
              <div class="d-subhead">
                <span>{{ tt('新增 / 完工趋势') }}</span>
                <span>{{ tt('近 7 天') }}</span>
              </div>
              <SLine :data="prod.trend7 || []" />

              <ul class="d-orders">
                <li v-for="order in progress.slice(0, 3)" :key="order['编号']" class="d-orders__row">
                  <div class="d-orders__name">
                    <strong>{{ order['产品'] || tt('未指定产品') }}</strong>
                    <button class="d-link d-num" @click="openDoc('MANU_ORDER', order['编号'])">
                      {{ order['编号'] }}
                    </button>
                  </div>
                  <div class="d-track" :aria-label="`${tt('当前状态：')}${order['状态']}`">
                    <i v-for="stageIndex in 4" :key="stageIndex" :class="{ on: stageIndex <= orderStage(order['状态']) }"></i>
                  </div>
                  <span class="d-tag" :class="`is-${statusTone(order['状态'])}`">{{ tt(order['状态']) }}</span>
                </li>
                <li v-if="!progress.length" class="d-empty">
                  <el-icon><Odometer /></el-icon>
                  <p>{{ tt('还没有加工单') }}</p>
                  <el-button type="primary" size="small" @click="go('/panelx/form/MANU_ORDER', '新建加工单')">
                    {{ tt('新建加工单') }}
                  </el-button>
                </li>
              </ul>
            </div>
          </div>
        </section>

        <!-- 质量与待办 -->
        <section v-if="desk.showTodo" class="d-card d-span-4">
          <div class="d-card__head">
            <div>
              <h2 class="d-card__title">{{ tt('质量与待办监测') }}</h2>
              <p class="d-card__desc">{{ tt('检验结果和流程阻塞') }}</p>
            </div>
            <span class="d-tag" :class="`is-${qualityRisk.tone}`">{{ tt(qualityRisk.label) }}</span>
          </div>

          <div class="d-quality">
            <div class="d-ring d-ring--sm">
              <svg viewBox="0 0 80 80" aria-hidden="true">
                <circle class="d-ring__track" cx="40" cy="40" r="32" />
                <circle
                  class="d-ring__value"
                  :class="`is-${qualityRisk.tone}`"
                  cx="40" cy="40" r="32"
                  :style="{ strokeDasharray: RING_C_SM, strokeDashoffset: qualityGaugeOffset }"
                />
              </svg>
              <div class="d-ring__text">
                <strong>{{ quality.total ? `${qualityRate}` : '--' }}<em v-if="quality.total">%</em></strong>
                <span>{{ tt('检验合格率') }}</span>
              </div>
            </div>
            <dl class="d-facts d-facts--rows">
              <div><dt>{{ tt('检验明细') }}</dt><dd class="d-num">{{ quality.total || 0 }}</dd></div>
              <div><dt>{{ tt('非合格 / 待判') }}</dt><dd class="d-num is-bad">{{ qualityExceptions }}</dd></div>
              <div><dt>{{ tt('流程待办') }}</dt><dd class="d-num">{{ todos.length }}</dd></div>
            </dl>
          </div>

          <div class="d-results">
            <div v-for="result in qualityResults" :key="result.name" class="d-results__row">
              <span><i :class="`is-${result.tone}`"></i>{{ tt(result.name) }}</span>
              <strong class="d-num">{{ result.value }}</strong>
            </div>
            <div v-if="!qualityResults.length" class="d-results__empty">{{ tt('暂无检验记录') }}</div>
          </div>

          <div class="d-alert" :class="todos.length ? 'is-warning' : 'is-success'">
            <el-icon><component :is="todos.length ? 'Warning' : 'CircleCheck'" /></el-icon>
            <span>{{ todos.length ? `${todos.length} ${tt('项流程等待处理')}` : tt('当前没有审批流程阻塞') }}</span>
          </div>
        </section>

        <!-- 实时业务事件流 -->
        <section class="d-card d-span-7">
          <div class="d-card__head">
            <div>
              <h2 class="d-card__title">{{ tt('实时业务事件流') }}</h2>
              <p class="d-card__desc">{{ tt('来自业务单据的最新活动') }}</p>
            </div>
            <span class="d-count">{{ latest.length }} {{ tt('条事件') }}</span>
          </div>

          <ol class="d-timeline">
            <li
              v-for="(event, index) in latest.slice(0, 8)"
              :key="`${event['编号']}-${index}`"
              class="d-timeline__row"
              :class="{ 'is-first': index === 0 }"
            >
              <span class="d-timeline__dot"></span>
              <button class="d-timeline__doc" @click="openDoc(event.panelCode || event.panel, event['编号'])">
                <el-icon><component :is="eventIcon(event.panel)" /></el-icon>
                <span>{{ tt(panelTitle(event.panel)) }}</span>
                <em class="d-num">{{ event['编号'] }}</em>
              </button>
              <span class="d-tag" :class="`is-${statusTone(event['状态'])}`">{{ tt(event['状态']) }}</span>
              <time class="d-num">{{ compactTime(event['时间']) }}</time>
            </li>
            <li v-if="!latest.length" class="d-empty">
              <el-icon><Document /></el-icon>
              <p>{{ tt('暂无业务事件') }}</p>
              <el-button type="primary" size="small" @click="go('/prod/manufacture/board', '生产看板')">
                {{ tt('去生产看板') }}
              </el-button>
            </li>
          </ol>
        </section>

        <!-- 业务数据内核 -->
        <section class="d-card d-span-5">
          <div class="d-card__head">
            <div>
              <h2 class="d-card__title">{{ tt('业务数据内核') }}</h2>
              <p class="d-card__desc">{{ tt('单据流量与基础资源覆盖') }}</p>
            </div>
            <span class="d-count">{{ docStats.length }} {{ tt('类单据') }}</span>
          </div>

          <div class="d-bars">
            <div v-for="doc in docStats.slice(0, 8)" :key="doc.panelCode" class="d-bars__row">
              <span class="d-bars__label" :title="tt(doc.panelName)">{{ tt(doc.panelName) }}</span>
              <div class="d-bars__track"><i :style="{ width: `${docPercent(doc.count)}%` }"></i></div>
              <strong class="d-num">{{ doc.count }}</strong>
            </div>
            <div v-if="!docStats.length" class="d-results__empty">{{ tt('暂无单据数据') }}</div>
          </div>

          <dl class="d-resources">
            <div v-for="resource in resourceStats" :key="resource.label">
              <dt><el-icon><component :is="resource.icon" /></el-icon>{{ tt(resource.label) }}</dt>
              <dd class="d-num">{{ resource.value }}</dd>
            </div>
          </dl>

          <div class="d-foot">
            <span class="d-sync" :class="{ err: loadError }">
              <i></i>{{ loadError ? tt('数据同步异常') : tt('SQL 数据链路在线') }}
            </span>
            <span class="d-num">{{ loadingStats ? tt('同步中') : `${refreshLeft}s ${tt('后刷新')}` }}</span>
          </div>
        </section>
      </div>
    </template>

    <!-- ===== 生产 ===== -->
    <template v-else-if="mod === 'prod'">
      <div class="d-grid">
        <section class="d-card d-span-4">
          <div class="d-card__head"><h2 class="d-card__title">{{ tt('工单状态分布') }}</h2></div>
          <div class="d-chart"><SBars :data="prod.statusDist" /></div>
        </section>
        <section class="d-card d-span-4">
          <div class="d-card__head"><h2 class="d-card__title">{{ tt('车间生产分布') }}</h2></div>
          <div class="d-chart"><SDonut :data="prod.workshopDist" :sub="tt('加工单')" :colors="CHART_COLORS" /></div>
        </section>
        <section class="d-card d-span-4">
          <div class="d-card__head"><h2 class="d-card__title">{{ tt('近 7 天新增 / 完工') }}</h2></div>
          <div class="d-chart"><SLine :data="prod.trend7" :name-a="tt('新增')" :name-b="tt('完工')" /></div>
        </section>

        <article class="d-card d-card--tight d-span-4">
          <div class="d-stat__label">{{ tt('工序报工汇总') }}</div>
          <dl class="d-facts d-facts--rows">
            <div><dt>{{ tt('报工数量') }}</dt><dd class="d-num">{{ reportQty }}</dd></div>
            <div><dt>{{ tt('合格数量') }}</dt><dd class="d-num">{{ reportPassQty }}</dd></div>
            <div><dt>{{ tt('不合格数量') }}</dt><dd class="d-num is-bad">{{ reportFailQty }}</dd></div>
            <div><dt>{{ tt('汇报单数') }}</dt><dd class="d-num">{{ prod.processReport?.docCount || 0 }}</dd></div>
          </dl>
        </article>
        <section class="d-card d-span-8">
          <div class="d-card__head">
            <div>
              <h2 class="d-card__title">{{ tt('产品工单分布') }}</h2>
              <p class="d-card__desc">{{ tt('按加工单产品统计，取前 6 项') }}</p>
            </div>
          </div>
          <div class="d-chart"><SBars :data="prod.topProducts || []" /></div>
        </section>

        <section class="d-card d-span-12">
          <div class="d-card__head">
            <div>
              <h2 class="d-card__title">{{ tt('BOM 物料树') }}</h2>
              <p class="d-card__desc">{{ tt('产品 → 材料，来自生产加工单真实数据') }}</p>
            </div>
          </div>
          <div class="d-chart d-chart--auto"><STree :data="bomTree" /></div>
        </section>
      </div>
    </template>

    <!-- ===== 库存 ===== -->
    <template v-else-if="mod === 'stock'">
      <div class="d-grid">
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('入库单量') }}</div>
          <div class="d-stat__value"><strong>{{ stock.totalIn }}</strong><span>{{ tt('单') }}</span></div>
        </article>
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('出库单量') }}</div>
          <div class="d-stat__value"><strong>{{ stock.totalOut }}</strong><span>{{ tt('单') }}</span></div>
        </article>
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('结存数量') }}</div>
          <div class="d-stat__value"><strong>{{ stock.balanceQty ?? 0 }}</strong></div>
        </article>
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('结存金额') }}</div>
          <div class="d-stat__value"><strong>{{ formatMoney(stock.balanceAmount || 0) }}</strong><span>{{ tt('元') }}</span></div>
        </article>

        <section class="d-card d-span-4">
          <div class="d-card__head"><h2 class="d-card__title">{{ tt('各单据数量') }}</h2></div>
          <div class="d-chart"><SBars :data="stockPanels" /></div>
        </section>
        <section class="d-card d-span-4">
          <div class="d-card__head"><h2 class="d-card__title">{{ tt('各单据明细行数') }}</h2></div>
          <div class="d-chart"><SBars :data="stockLines" :colors="CHART_COLORS" /></div>
        </section>
        <section class="d-card d-span-4">
          <div class="d-card__head"><h2 class="d-card__title">{{ tt('仓库结存数量') }}</h2></div>
          <div class="d-chart"><SDonut :data="stock.warehouseQty || []" :sub="tt('结存')" :colors="CHART_COLORS" /></div>
        </section>

        <section class="d-card d-span-8">
          <div class="d-card__head">
            <div>
              <h2 class="d-card__title">{{ tt('近 7 天出入库流水') }}</h2>
              <p class="d-card__desc">{{ tt('来自库存事务表，按业务日期归集数量') }}</p>
            </div>
          </div>
          <div class="d-chart">
            <SLine v-if="hasTxn7" :data="stock.txnTrend || []" :name-a="tt('入库')" :name-b="tt('出库')" />
            <div v-else class="d-empty">
              <el-icon><Box /></el-icon>
              <p>{{ tt('近 7 天没有出入库流水') }}</p>
              <el-button type="primary" size="small" @click="go('/panelx/list/STOCK_LEDGER', '库存台账')">
                {{ tt('去查库存台账') }}
              </el-button>
            </div>
          </div>
        </section>
        <article class="d-card d-card--tight d-span-4">
          <div class="d-stat__label">{{ tt('库存流水汇总') }}</div>
          <dl class="d-facts d-facts--rows">
            <div><dt>{{ tt('累计入库数量') }}</dt><dd class="d-num">{{ stock.totalInQty ?? 0 }}</dd></div>
            <div><dt>{{ tt('累计出库数量') }}</dt><dd class="d-num">{{ stock.totalOutQty ?? 0 }}</dd></div>
            <div><dt>{{ tt('结存行数') }}</dt><dd class="d-num">{{ stock.balanceRows ?? 0 }}</dd></div>
            <div><dt>{{ tt('明细行数合计') }}</dt><dd class="d-num">{{ stock.totalLines }}</dd></div>
          </dl>
        </article>
      </div>
    </template>

    <!-- ===== 销售 ===== -->
    <template v-else-if="mod === 'sales'">
      <div class="d-grid">
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('销售订单数') }}</div>
          <div class="d-stat__value"><strong>{{ sales.total }}</strong><span>{{ tt('张') }}</span></div>
        </article>
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('明细金额合计') }}</div>
          <div class="d-stat__value"><strong>{{ formatMoney(sales.amount || 0) }}</strong><span>{{ tt('元') }}</span></div>
        </article>
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('已审核订单') }}</div>
          <div class="d-stat__value"><strong>{{ salesDone }}</strong><span>{{ tt('张') }}</span></div>
        </article>
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('平均单额') }}</div>
          <div class="d-stat__value"><strong>{{ formatMoney(sales.avgAmount || 0) }}</strong><span>{{ tt('元') }}</span></div>
        </article>

        <section class="d-card d-span-4">
          <div class="d-card__head"><h2 class="d-card__title">{{ tt('客户订单分布') }}</h2></div>
          <div class="d-chart"><SDonut :data="sales.byCustomer" :sub="tt('订单')" :colors="CHART_COLORS" /></div>
        </section>
        <section class="d-card d-span-4">
          <div class="d-card__head"><h2 class="d-card__title">{{ tt('订单状态分布') }}</h2></div>
          <div class="d-chart"><SBars :data="sales.byStatus" /></div>
        </section>
        <section class="d-card d-span-4">
          <div class="d-card__head"><h2 class="d-card__title">{{ tt('客户金额排行') }}</h2></div>
          <div class="d-chart"><SBars :data="sales.topCustomers || []" :colors="CHART_COLORS" /></div>
        </section>

        <section class="d-card d-span-12">
          <div class="d-card__head">
            <div>
              <h2 class="d-card__title">{{ tt('近 7 天订单 / 审核') }}</h2>
              <p class="d-card__desc">{{ tt('按单据创建日期归集，已审核为当日审核完成的订单') }}</p>
            </div>
          </div>
          <div class="d-chart"><SLine :data="sales.trend7 || []" :name-a="tt('新增订单')" :name-b="tt('已审核')" /></div>
        </section>
      </div>
    </template>

    <!-- ===== 采购 ===== -->
    <template v-else-if="mod === 'purchase'">
      <div class="d-grid">
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('采购订单数') }}</div>
          <div class="d-stat__value"><strong>{{ purchase.total || 0 }}</strong><span>{{ tt('张') }}</span></div>
        </article>
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('含税金额合计') }}</div>
          <div class="d-stat__value"><strong>{{ formatMoney(purchase.amount || 0) }}</strong><span>{{ tt('元') }}</span></div>
        </article>
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('到货报检单') }}</div>
          <div class="d-stat__value"><strong>{{ purchase.arrivalTotal || 0 }}</strong><span>{{ tt('张') }}</span></div>
        </article>
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('采购入库单') }}</div>
          <div class="d-stat__value"><strong>{{ purchase.inTotal || 0 }}</strong><span>{{ tt('张') }}</span></div>
        </article>

        <section class="d-card d-span-4">
          <div class="d-card__head"><h2 class="d-card__title">{{ tt('供应商订单分布') }}</h2></div>
          <div class="d-chart"><SDonut :data="purchase.bySupplier || []" :sub="tt('采购单')" :colors="CHART_COLORS" /></div>
        </section>
        <section class="d-card d-span-4">
          <div class="d-card__head"><h2 class="d-card__title">{{ tt('采购单状态分布') }}</h2></div>
          <div class="d-chart"><SBars :data="purchase.byStatus || []" /></div>
        </section>
        <section class="d-card d-span-4">
          <div class="d-card__head"><h2 class="d-card__title">{{ tt('供应商金额排行') }}</h2></div>
          <div class="d-chart"><SBars :data="purchase.supplierAmount || []" :colors="CHART_COLORS" /></div>
        </section>

        <section class="d-card d-span-6">
          <div class="d-card__head">
            <div>
              <h2 class="d-card__title">{{ tt('物料采购量') }}</h2>
              <p class="d-card__desc">{{ tt('按明细数量合计，取前 6 项') }}</p>
            </div>
          </div>
          <div class="d-chart"><SBars :data="purchase.topItems || []" :colors="CHART_COLORS" /></div>
        </section>
        <section class="d-card d-span-6">
          <div class="d-card__head">
            <div>
              <h2 class="d-card__title">{{ tt('近 7 天采购 / 到货') }}</h2>
              <p class="d-card__desc">{{ tt('采购单新建与到货报检按日期归集') }}</p>
            </div>
          </div>
          <div class="d-chart"><SLine :data="purchase.trend7 || []" :name-a="tt('新建采购单')" :name-b="tt('到货')" /></div>
        </section>
      </div>
    </template>

    <!-- ===== 质量 ===== -->
    <template v-else-if="mod === 'quality'">
      <div class="d-grid">
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('检验明细总数') }}</div>
          <div class="d-stat__value"><strong>{{ quality.total }}</strong><span>{{ tt('条') }}</span></div>
        </article>
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('合格数') }}</div>
          <div class="d-stat__value"><strong>{{ quality.pass }}</strong><span>{{ tt('条') }}</span></div>
        </article>
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('非合格数') }}</div>
          <div class="d-stat__value"><strong class="is-bad">{{ quality.total - quality.pass }}</strong><span>{{ tt('条') }}</span></div>
        </article>
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('合格率') }}</div>
          <div class="d-stat__value"><strong>{{ quality.passRate }}</strong><span>%</span></div>
        </article>

        <section class="d-card d-span-4">
          <div class="d-card__head"><h2 class="d-card__title">{{ tt('检验结果分布') }}</h2></div>
          <div class="d-chart"><SDonut :data="quality.byResult" :sub="tt('明细')" :colors="QUALITY_COLORS" /></div>
        </section>
        <section class="d-card d-span-4">
          <div class="d-card__head"><h2 class="d-card__title">{{ tt('检验单类型分布') }}</h2></div>
          <div class="d-chart"><SBars :data="quality.byType || []" :colors="CHART_COLORS" /></div>
        </section>
        <section class="d-card d-span-4">
          <div class="d-card__head">
            <div>
              <h2 class="d-card__title">{{ tt('不合格处理方式') }}</h2>
              <p class="d-card__desc">{{ tt('按处理数量合计') }}</p>
            </div>
          </div>
          <div class="d-chart">
            <SBars v-if="(quality.byMethod || []).length" :data="quality.byMethod" :colors="QUALITY_COLORS" />
            <div v-else class="d-empty">
              <el-icon><CircleCheck /></el-icon>
              <p>{{ tt('暂无不合格处理记录') }}</p>
            </div>
          </div>
        </section>
      </div>
    </template>

    <!-- ===== 设计（PDM） ===== -->
    <template v-else>
      <div class="d-grid">
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('物料清单 BOM') }}</div>
          <div class="d-stat__value"><strong>{{ design.bom || 0 }}</strong><span>{{ tt('张') }}</span></div>
        </article>
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('工艺路线') }}</div>
          <div class="d-stat__value"><strong>{{ design.route || 0 }}</strong><span>{{ tt('条') }}</span></div>
        </article>
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('工序档案') }}</div>
          <div class="d-stat__value"><strong>{{ design.op || 0 }}</strong><span>{{ tt('个') }}</span></div>
        </article>
        <article class="d-card d-card--tight d-span-3 d-stat">
          <div class="d-stat__label">{{ tt('存货档案') }}</div>
          <div class="d-stat__value"><strong>{{ design.inv || 0 }}</strong><span>{{ tt('个') }}</span></div>
        </article>

        <section class="d-card d-span-8">
          <div class="d-card__head">
            <div>
              <h2 class="d-card__title">{{ tt('BOM 子件数排行') }}</h2>
              <p class="d-card__desc">{{ tt('按每张物料清单的子件条数统计') }}</p>
            </div>
          </div>
          <div class="d-chart">
            <SBars v-if="(design.bomTop || []).length" :data="design.bomTop" :colors="CHART_COLORS" />
            <div v-else class="d-empty">
              <el-icon><Files /></el-icon>
              <p>{{ tt('还没有维护 BOM') }}</p>
              <el-button type="primary" size="small" @click="go('/panelx/list/BOM', '物料清单')">{{ tt('去维护 BOM') }}</el-button>
            </div>
          </div>
        </section>
        <article class="d-card d-card--tight d-span-4">
          <div class="d-stat__label">{{ tt('设计档案覆盖') }}</div>
          <dl class="d-facts d-facts--rows">
            <div><dt>{{ tt('物料清单 BOM') }}</dt><dd class="d-num">{{ design.bom || 0 }}</dd></div>
            <div><dt>{{ tt('工艺路线') }}</dt><dd class="d-num">{{ design.route || 0 }}</dd></div>
            <div><dt>{{ tt('工序档案') }}</dt><dd class="d-num">{{ design.op || 0 }}</dd></div>
            <div><dt>{{ tt('存货档案') }}</dt><dd class="d-num">{{ design.inv || 0 }}</dd></div>
            <div><dt>{{ tt('档案合计') }}</dt><dd class="d-num">{{ design.total || 0 }}</dd></div>
          </dl>
        </article>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useTabsStore } from '@/stores/tabs'
import { useAppStore } from '@/stores/app'
import { tt } from '@/i18n'
import { useLocaleStore } from '@/stores/locale'
import request from '@core/request'
import { ElNotification } from 'element-plus'
import SBars from './SBars.vue'
import SDonut from './SDonut.vue'
import SLine from './SLine.vue'
import STree from './STree.vue'
import { CircleCheck, Files } from '@element-plus/icons-vue'

const user = useUserStore()
const tabs = useTabsStore()
const app = useAppStore()
const localeStore = useLocaleStore()
const router = useRouter()

const desk = computed(() => app.deskSettings)

const QUICK_DEFS = [
  { key: 'newOrder', title: '新建加工单', path: '/panelx/form/MANU_ORDER' },
  { key: 'quickReport', title: '快速报工', path: '/prod/shop/procReport' },
  { key: 'board', title: '生产看板', path: '/prod/manufacture/board' },
]
const quickEntries = computed(() => QUICK_DEFS.filter((q) => desk.value.quick.includes(q.key)))

// ---------- 看板模块 ----------
const MODULES = [
  { key: 'overview', title: '概览', icon: 'DataBoard' },
  { key: 'prod', title: '生产', icon: 'Odometer' },
  { key: 'stock', title: '库存', icon: 'Box' },
  { key: 'sales', title: '销售', icon: 'ShoppingCart' },
  { key: 'purchase', title: '采购', icon: 'ShoppingBag' },
  { key: 'quality', title: '质量', icon: 'Aim' },
  { key: 'design', title: '设计', icon: 'Collection' },
]
const mod = ref('overview')

// 图表色板：语义优先（合格/让步/不合格），其余取设计库 category10 里的可搭配子集
const CHART_COLORS = ['#1E9493', '#5D7092', '#F6BD16', '#5AD8A6', '#FF9845', '#945FB9']
const QUALITY_COLORS = ['#52c41a', '#faad14', '#ff4d4f', '#8b9893']

// ---------- 班次：8:00-21:00 白班，其余夜班；每分钟自动检查，到点自动切换 ----------
const now = ref(new Date())
const shift = computed(() => {
  const h = now.value.getHours()
  return h >= 8 && h < 21
    ? { key: 'day', name: '白班', range: '08:00 - 21:00' }
    : { key: 'night', name: '夜班', range: '21:00 - 次日 08:00' }
})
const nowTime = computed(() => now.value.toTimeString().slice(0, 5))
const today = computed(() => now.value.toLocaleDateString('zh-CN'))
// 页头用问候语而不是「我的桌面」——页签本身已经写着页面名，重复一遍只是噪音
const greeting = computed(() => {
  const h = now.value.getHours()
  if (h < 6) return '凌晨好'
  if (h < 12) return '上午好'
  if (h < 18) return '下午好'
  return '晚上好'
})
let shiftTimer = null
let lastShift = ''
function startShiftTimer() {
  lastShift = shift.value.key
  shiftTimer = setInterval(() => {
    now.value = new Date()
    if (shift.value.key !== lastShift) {
      lastShift = shift.value.key
      ElNotification({
        title: `${tt('已切换至')}${tt(shift.value.name)}`,
        message: `${tt('当前班次时段：')}${tt(shift.value.range)}`,
        type: shift.value.key === 'day' ? 'success' : 'info',
        duration: 4000,
      })
    }
  }, 60000)
}

// ---------- 数据加载（真实接口 /dashboard/stats，每 5 分钟刷新） ----------
const stats = ref({
  kpis: {}, docStats: [], progress: [], todos: [], archives: {}, latest: [],
  production: {}, stock: {}, sales: {}, quality: {},
})
const loadingStats = ref(false)
const loadError = ref(false)
const lastUpdated = ref(null)
const refreshLeft = ref(300)
async function load() {
  if (loadingStats.value) return
  loadingStats.value = true
  try {
    const r = await request.get('/dashboard/stats')
    if (r?.data) {
      stats.value = { ...stats.value, ...r.data }
      lastUpdated.value = new Date()
      refreshLeft.value = 300
      loadError.value = false
    }
  } catch (e) {
    loadError.value = true
  } finally {
    loadingStats.value = false
  }
}
let refreshTimer = null
let countdownTimer = null

onMounted(() => {
  load()
  refreshTimer = setInterval(load, 300000)
  countdownTimer = setInterval(() => {
    if (!loadingStats.value && refreshLeft.value > 0) refreshLeft.value -= 1
  }, 1000)
  startShiftTimer()
})
onBeforeUnmount(() => {
  clearInterval(refreshTimer)
  clearInterval(countdownTimer)
  clearInterval(shiftTimer)
})

// ---------- 概览数据 ----------
const todos = computed(() => stats.value.todos || [])
const docStats = computed(() => stats.value.docStats || [])
const archives = computed(() => stats.value.archives || {})
const latest = computed(() => stats.value.latest || [])
const progress = computed(() => stats.value.progress || [])

// ---------- 生产数据 ----------
const prod = computed(() => stats.value.production || {})
const bomTree = computed(() =>
  (prod.value.bomTree || []).map((p) => ({
    label: p['产品'],
    meta: p['规格型号'] ? `${tt('规格：')}${p['规格型号']}` : '',
    children: (p.materials || []).map((m) => ({
      label: m['名称'],
      meta: `${m['数量']} ${m['单位']}`.trim(),
    })),
  }))
)

// ---------- 库存数据 ----------
const stock = computed(() => stats.value.stock || {})
const stockCount = computed(() => (stock.value.panels || []).reduce((s, p) => s + (p.count || 0), 0))
const stockPanels = computed(() => (stock.value.panels || []).map((p) => ({ name: p.panelName, value: p.count })))
const stockLines = computed(() => (stock.value.panels || []).map((p) => ({ name: p.panelName, value: p.lines })))

// ---------- 销售数据 ----------
const sales = computed(() => stats.value.sales || {})
const salesDone = computed(() => {
  const s = (sales.value.byStatus || []).find((x) => x.name === '已审核')
  return s ? s.value : 0
})

// ---------- 质量数据 ----------
const quality = computed(() => stats.value.quality || { total: 0, pass: 0, passRate: 0, byResult: [] })

// ---------- 采购（ERP）----------
const purchase = computed(() => stats.value.purchase || {})

// ---------- 设计（PDM）----------
const design = computed(() => stats.value.design || {})

// ---------- 生产：工序报工汇总 ----------
const reportQty = computed(() => prod.value.processReport?.reportQty ?? 0)
const reportPassQty = computed(() => prod.value.processReport?.passQty ?? 0)
const reportFailQty = computed(() => prod.value.processReport?.failQty ?? 0)

// ---------- 库存：近 7 天是否有流水（全 0 时给空态，不画一条贴地的线）----------
const hasTxn7 = computed(() => (stock.value.txnTrend || []).some((d) => Number(d.in) > 0 || Number(d.out) > 0))

// ---------- 实时驾驶舱派生指标 ----------
const productionTotal = computed(() => Number(stats.value.kpis.moTotal || 0))
const productionDone = computed(() => {
  const item = (prod.value.statusDist || []).find((entry) => entry.name === '已完工')
  return Number(item?.value || 0)
})
const productionRunning = computed(() => {
  const item = (prod.value.statusDist || []).find((entry) => entry.name === '生产中')
  return Number(item?.value || 0)
})
const completionRate = computed(() => (
  productionTotal.value ? Math.round((productionDone.value / productionTotal.value) * 100) : 0
))
// 环形进度：半径 48 / 32 对应的周长，dasharray 必须显式给，否则 offset 不生效
const RING_C = 301.6
const RING_C_SM = 201.1
const gaugeOffset = computed(() => RING_C * (1 - completionRate.value / 100))
const qualityRate = computed(() => Number(quality.value.passRate || 0))
const qualityGaugeOffset = computed(() => RING_C_SM * (1 - Math.min(100, qualityRate.value) / 100))
const qualityExceptions = computed(() => Math.max(0, Number(quality.value.total || 0) - Number(quality.value.pass || 0)))
const qualityRisk = computed(() => {
  if (!quality.value.total) return { label: '暂无检验', tone: 'neutral' }
  if (qualityRate.value >= 95) return { label: '质量稳定', tone: 'stable' }
  if (qualityRate.value >= 80) return { label: '需要关注', tone: 'attention' }
  return { label: '质量风险', tone: 'risk' }
})
const qualityResults = computed(() => {
  const tones = { 合格: 'stable', 不合格: 'risk', 让步接收: 'attention', 待检: 'neutral' }
  return (quality.value.byResult || []).map((item) => ({ ...item, tone: tones[item.name] || 'neutral' }))
})
const resourceStats = computed(() => [
  { label: '存货', value: archives.value.invItems ?? 0, icon: 'Grid' },
  { label: '部门', value: archives.value.deptCount ?? 0, icon: 'OfficeBuilding' },
  { label: '仓库', value: archives.value.whCount ?? 0, icon: 'House' },
  { label: 'BOM 产品', value: bomTree.value.length, icon: 'Connection' },
])
const lastUpdatedText = computed(() => (
  lastUpdated.value ? lastUpdated.value.toLocaleTimeString('zh-CN', { hour12: false }).slice(0, 8) : '等待同步'
))
const kpis = computed(() => {
  const amount = Number(sales.value.amount || 0)
  return [
    {
      title: tt('生产任务负载'), value: Number(stats.value.kpis.moActive || 0), unit: tt('单'), icon: 'Odometer',
      tone: 'primary', state: tt('实时'), meta: `${tt('总计')} ${productionTotal.value} ${tt('单')}`,
    },
    {
      title: tt('工单闭环率'), value: completionRate.value, unit: '%', icon: 'CircleCheck',
      tone: 'stable', state: completionRate.value >= 80 ? tt('稳定') : tt('推进中'),
      meta: `${productionDone.value} / ${productionTotal.value} ${tt('已完工')}`,
    },
    {
      title: tt('检验合格率'), value: quality.value.total ? qualityRate.value : '--', unit: quality.value.total ? '%' : '', icon: 'Aim',
      tone: qualityRate.value < 80 && quality.value.total ? 'risk' : 'attention', state: tt(qualityRisk.value.label),
      meta: `${quality.value.pass || 0} / ${quality.value.total || 0} ${tt('合格')}`,
    },
    {
      title: tt('销售订单金额'), value: formatCompact(amount), unit: tt('元'), icon: 'TrendCharts',
      tone: 'neutral', state: `${sales.value.total || 0} ${tt('张订单')}`,
      meta: `${salesDone.value} ${tt('张已审核')}`,
    },
  ]
})

// ---------- 工具 ----------
function statusTone(status) {
  return {
    已完工: 'stable', 已审核: 'stable', 启用: 'stable', 生产中: 'primary',
    审批中: 'attention', 待审批: 'attention', 草稿: 'neutral', 不合格: 'risk',
    已中止: 'risk', 已关闭: 'risk',
  }[status] || 'neutral'
}
function orderStage(status) {
  return { 草稿: 1, 已审核: 2, 生产中: 3, 已完工: 4, 已关闭: 4 }[status] || 1
}
function eventIcon(panel) {
  const name = String(panel || '')
  if (name.includes('生产') || name.includes('加工')) return 'SetUp'
  if (name.includes('销售') || name.includes('订单')) return 'ShoppingCart'
  if (name.includes('检验') || name.includes('质量')) return 'Aim'
  if (name.includes('入库') || name.includes('出库')) return 'Box'
  return 'Document'
}
const PANEL_TITLES = {
  PURCHASE_IN: '采购入库单', QUOTE_ORDER: '报价单', CUSTOMER_TRACE_SETTINGS: '客户追溯设置',
  TRACE_PRINT_TEMPLATE: '追溯打印模板', COMPANY_TRACE_SETTINGS: '企业追溯设置', QC_ITEM: '质检项目',
  MANU_ORDER: '生产加工单', SO_ORDER: '销售订单', PROCESS_REPORT: '工序汇报单',
  FINISH_IN: '产成品入库单', SALE_OUT: '销售出库单', DEPT: '部门档案',
}
function panelTitle(panel) {
  return PANEL_TITLES[panel] || panel || '业务单据'
}
function compactTime(value) {
  const text = String(value || '')
  return text.length >= 16 ? text.slice(5, 16).replace('T', ' ') : text || '--'
}
function formatCompact(value) {
  if (!Number.isFinite(value)) return '0'
  if (Math.abs(value) >= 10000) return `${(value / 10000).toFixed(value >= 100000 ? 0 : 1)}${tt('万')}`
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 }).format(value)
}
/** 金额/数量：模块卡里给全值 + 千分位，不缩写成「万」，避免丢精度。 */
function formatMoney(value) {
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 }).format(Number(value) || 0)
}
function docPercent(value) {
  const max = Math.max(...docStats.value.map((item) => Number(item.count || 0)), 1)
  return value ? Math.max(8, Math.round((Number(value) / max) * 100)) : 0
}
function go(path, title) {
  router.push(path)
  tabs.open({ path, title })
}
/** 单据号直接跳对应单据页，不让用户自己在列表里找。 */
function openDoc(panel, docNo) {
  if (!panel || !docNo) return
  const path = `/panelx/form/${panel}?code=${encodeURIComponent(docNo)}`
  router.push(path)
  tabs.open({ path, title: panelTitle(panel) })
}

// ---------- 界面语言：切换后补齐本页词条（tt() 在渲染层读 locale ref，切换即时重算） ----------
const DICT_KEYS = [
  '凌晨好', '上午好', '下午好', '晚上好',
  '白班', '夜班', '21:00 - 次日 08:00',
  ...QUICK_DEFS.map((q) => q.title),
  ...MODULES.map((m) => m.title),
  ...Object.values(PANEL_TITLES), '业务单据',
  '我的桌面', '数据更新于', '立即刷新', '实时', '还没有加工单', '暂无检验记录',
  '暂无单据数据', '类单据', '去生产看板', '暂无业务事件', 'BOM 物料树',
  '产品 → 材料，来自生产加工单真实数据',
  '生产执行核心', '工单生命周期与近 7 天执行脉冲', '等待同步', '工单闭环率', '总工单', '执行中', '已完工',
  '新增 / 完工趋势', '近 7 天', '未指定产品', '当前状态：', '暂无工单执行数据',
  '质量与待办监测', '检验结果和流程阻塞', '暂无检验', '质量稳定', '需要关注', '质量风险',
  '检验合格率', '检验明细', '非合格 / 待判', '流程待办', '项流程等待处理', '当前没有审批流程阻塞',
  '实时业务事件流', '来自业务单据的最新活动', '条事件',
  '业务数据内核', '单据流量与基础资源覆盖', '数据同步异常', 'SQL 数据链路在线', '同步中', '后刷新',
  '工单状态分布', '车间生产分布', '加工单', '近 7 天新增 / 完工',
  '入库单量', '出库单量', '出入库单据总数', '明细行数合计', '各单据数量', '各单据明细行数',
  '销售订单数', '明细金额合计（元）', '已审核订单', '客户订单分布', '订单', '订单状态分布',
  '检验明细总数', '合格数', '非合格数', '合格率', '检验结果分布', '明细', '检验结果对比',
  '生产任务负载', '单', '总计', '稳定', '推进中', '合格', '张订单', '张已审核', '元', '万', '规格：',
  '已切换至', '当前班次时段：',
  // 分模块看板补齐（采购 / 设计 / 各模块补充卡）
  '采购', '设计', '采购订单数', '含税金额合计', '到货报检单', '采购入库单', '供应商订单分布',
  '采购单状态分布', '供应商金额排行', '物料采购量', '采购单', '近 7 天采购 / 到货', '新建采购单', '到货',
  '物料清单 BOM', '工艺路线', '工序档案', '存货档案', 'BOM 子件数排行', '设计档案覆盖', '档案合计',
  '还没有维护 BOM', '去维护 BOM', '按每张物料清单的子件条数统计', '按明细数量合计，取前 6 项',
  '采购单新建与到货报检按日期归集',
  '工序报工汇总', '报工数量', '合格数量', '不合格数量', '汇报单数', '产品工单分布', '按加工单产品统计，取前 6 项',
  '结存数量', '结存金额', '仓库结存数量', '结存', '近 7 天出入库流水', '来自库存事务表，按业务日期归集数量',
  '入库', '出库', '库存流水汇总', '累计入库数量', '累计出库数量', '结存行数',
  '明细金额合计', '平均单额', '客户金额排行', '近 7 天订单 / 审核', '新增订单',
  '按单据创建日期归集，已审核为当日审核完成的订单',
  '检验单类型分布', '不合格处理方式', '暂无不合格处理记录', '条', '张', '个',
  '近 7 天没有出入库流水', '去查库存台账',
]
watch([() => localeStore.current, docStats], async ([locale]) => {
  if (localeStore.isZh) return
  await localeStore.ensureDict(locale, [
    ...DICT_KEYS,
    ...resourceStats.value.map((r) => r.label),
    ...docStats.value.map((d) => d.panelName),
  ].flatMap((k) => String(k || '').split(' / ')))
}, { immediate: true })
</script>

<style scoped>
/* ===== 设计变量来自全局 `.adx`（styles/index.css，映射 antd v6 token）；本页只放布局 ===== */
.dash {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

/* ===== 页头 ===== */
.d-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.d-head__title {
  margin: 0;
  color: var(--d-text);
  font-size: 20px;
  font-weight: 600;
  line-height: 28px;
}
.d-head__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 4px 0 0;
  color: var(--d-text-3);
  font-size: 12px;
  line-height: 20px;
}
.d-shift {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  background: var(--d-primary-bg);
  color: var(--d-primary);
  font-weight: 600;
}
.d-shift em {
  font-style: normal;
  font-weight: 400;
  opacity: 0.72;
}
.d-sep {
  color: var(--d-text-3);
}
.d-sync {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.d-sync i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--d-ok);
}
.d-sync.err {
  color: var(--d-bad);
}
.d-sync.err i {
  background: var(--d-bad);
}
.d-head__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

/* ===== 分段控制器（32 控件高 + 4 内边距 = 40 命中区） ===== */
.d-seg {
  display: inline-flex;
  align-self: flex-start;
  align-items: center;
  gap: 2px;
  height: 40px;
  padding: 4px;
  border-radius: var(--d-radius-sm);
  background: var(--d-fill);
}
.d-seg__item {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: var(--d-text-2);
  font: inherit;
  font-size: 13px;
  white-space: nowrap;
  cursor: pointer;
  transition: color 0.15s, background 0.15s, border-color 0.15s;
}
.d-seg__item:hover {
  color: var(--d-text);
}
.d-seg__item.on {
  border-color: var(--d-border-strong);
  background: var(--d-card);
  color: var(--d-primary);
  font-weight: 600;
}
.d-seg__item:focus-visible {
  outline: 2px solid var(--d-primary);
  outline-offset: 1px;
}

/* ===== 栅格与卡片 ===== */
.d-grid {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 16px;
}
.d-span-3 { grid-column: span 3; }
.d-span-4 { grid-column: span 4; }
.d-span-5 { grid-column: span 5; }
.d-span-6 { grid-column: span 6; }
.d-span-7 { grid-column: span 7; }
.d-span-8 { grid-column: span 8; }
.d-span-12 { grid-column: span 12; }

.d-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 24px;
  border: 1px solid var(--d-border);
  border-radius: var(--d-radius);
  background: var(--d-card);
}
.d-card--tight {
  padding: 16px;
}
.d-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.d-card__title {
  margin: 0;
  color: var(--d-text);
  font-size: 16px;
  font-weight: 600;
  line-height: 24px;
}
.d-card__desc {
  margin: 2px 0 0;
  color: var(--d-text-3);
  font-size: 12px;
  line-height: 20px;
}
.d-live {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  background: var(--d-ok-bg);
  color: var(--d-ok);
  font-size: 12px;
  white-space: nowrap;
}
.d-live i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.d-count {
  color: var(--d-text-3);
  font-size: 12px;
  white-space: nowrap;
}

/* ===== Statistic ===== */
.d-stat__label {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--d-text-2);
  font-size: 14px;
  line-height: 22px;
}
.d-stat__value {
  display: flex;
  align-items: baseline;
  gap: 4px;
  margin-top: 8px;
}
.d-stat__value strong {
  color: var(--d-text);
  font-size: 28px;
  font-weight: 600;
  line-height: 36px;
  font-variant-numeric: tabular-nums;
}
.d-stat__value strong.is-bad {
  color: var(--d-bad);
}
.d-stat__value span {
  color: var(--d-text-3);
  font-size: 12px;
}
.d-stat__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 12px;
  color: var(--d-text-3);
  font-size: 12px;
  line-height: 20px;
}

/* ===== 标签（状态用语义色，不堆装饰） ===== */
.d-tag {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: var(--d-fill);
  color: var(--d-text-2);
  font-size: 12px;
  line-height: 20px;
  white-space: nowrap;
}
.d-tag.is-stable { background: var(--d-ok-bg); color: var(--d-ok); }
.d-tag.is-primary { background: var(--d-primary-bg); color: var(--d-primary); }
.d-tag.is-attention { background: var(--d-warn-bg); color: var(--d-warn); }
.d-tag.is-risk { background: var(--d-bad-bg); color: var(--d-bad); }
.d-tag.is-neutral { background: var(--d-fill); color: var(--d-text-2); }

/* ===== 数字统一等宽 ===== */
.d-num {
  font-variant-numeric: tabular-nums;
  text-align: right;
}

/* ===== 生产执行核心 ===== */
.d-core {
  display: grid;
  flex: 1;
  grid-template-columns: 168px minmax(0, 1fr);
  gap: 24px;
  margin-top: 16px;
}
.d-core__gauge {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding-right: 24px;
  border-right: 1px solid var(--d-border);
}
.d-ring {
  position: relative;
  width: 120px;
  height: 120px;
  flex: 0 0 auto;
}
.d-ring--sm {
  width: 112px;
  height: 112px;
}
.d-ring svg {
  width: 100%;
  height: 100%;
  transform: rotate(-90deg);
}
.d-ring circle {
  fill: none;
  stroke-width: 8;
}
.d-ring--sm circle {
  stroke-width: 6;
}
.d-ring__track {
  stroke: var(--d-fill);
}
.d-ring__value {
  stroke: var(--d-primary);
  stroke-linecap: round;
  transition: stroke-dashoffset 0.4s ease;
}
.d-ring__value.is-stable { stroke: var(--d-ok); }
.d-ring__value.is-attention { stroke: var(--d-warn); }
.d-ring__value.is-risk { stroke: var(--d-bad); }
.d-ring__value.is-neutral { stroke: var(--d-text-3); }
.d-ring__text {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
}
.d-ring__text strong {
  color: var(--d-text);
  font-size: 24px;
  font-weight: 600;
  line-height: 32px;
  font-variant-numeric: tabular-nums;
}
.d-ring--sm .d-ring__text strong {
  font-size: 20px;
  line-height: 26px;
}
.d-ring--sm .d-ring__text span {
  line-height: 16px;
  text-align: center;
}
.d-ring__text strong em {
  font-style: normal;
  font-size: 14px;
}
.d-ring__text span {
  color: var(--d-text-3);
  font-size: 12px;
}
.d-facts {
  display: grid;
  width: 100%;
  gap: 8px;
  margin: 0;
}
.d-facts > div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.d-facts dt {
  color: var(--d-text-3);
  font-size: 12px;
}
.d-facts dd {
  margin: 0;
  color: var(--d-text);
  font-size: 14px;
  font-weight: 600;
}
.d-facts dd.is-bad {
  color: var(--d-bad);
}
/* 紧凑卡里「标题 + 清单」直接相邻，补一点间距 */
.d-card--tight .d-facts--rows {
  margin-top: 12px;
}
.d-card--tight .d-facts--rows > div {
  min-height: 24px;
}
.d-core__main {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.d-subhead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--d-text-3);
  font-size: 12px;
}
.d-orders {
  display: flex;
  flex-direction: column;
  margin: auto 0 0;
  padding: 0;
  list-style: none;
}
.d-orders__row:hover {
  background: var(--d-fill-soft);
}
.d-orders__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 96px 64px;
  align-items: center;
  gap: 12px;
  min-height: 40px;
  border-top: 1px solid var(--d-border);
}
.d-orders__name {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.d-orders__name strong {
  overflow: hidden;
  color: var(--d-text);
  font-size: 14px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.d-link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--d-primary);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.d-link:hover {
  text-decoration: underline;
}
.d-track {
  display: flex;
  gap: 3px;
}
.d-track i {
  height: 4px;
  flex: 1;
  border-radius: 100px;
  background: var(--d-fill);
}
.d-track i.on {
  background: var(--d-primary);
}

/* ===== 质量与待办 ===== */
.d-quality {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 16px;
}
.d-quality .d-facts {
  flex: 1;
}
.d-results {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--d-border);
}
.d-results__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 24px;
  color: var(--d-text-2);
  font-size: 12px;
}
.d-results__row span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.d-results__row i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--d-text-3);
}
.d-results__row i.is-stable { background: var(--d-ok); }
.d-results__row i.is-attention { background: var(--d-warn); }
.d-results__row i.is-risk { background: var(--d-bad); }
.d-results__row strong {
  color: var(--d-text);
  font-size: 13px;
  font-weight: 600;
}
.d-results__empty {
  padding: 8px 0;
  color: var(--d-text-3);
  font-size: 12px;
}
.d-alert {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: auto;
  padding: 8px 12px;
  border: 1px solid transparent;
  border-radius: var(--d-radius-sm);
  font-size: 12px;
  line-height: 20px;
}
.d-results + .d-alert {
  margin-top: 16px;
}
.d-alert.is-warning {
  border-color: rgba(250, 173, 20, 0.35);
  background: var(--d-warn-bg);
  color: var(--d-warn);
}
.d-alert.is-success {
  border-color: rgba(82, 196, 26, 0.3);
  background: var(--d-ok-bg);
  color: var(--d-ok);
}

/* ===== 事件流（Timeline 结构） ===== */
.d-timeline {
  display: flex;
  flex-direction: column;
  margin: 16px 0 0;
  padding: 0;
  list-style: none;
}
.d-timeline__row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 40px;
  padding-left: 20px;
}
.d-timeline__row::before {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 3px;
  width: 2px;
  background: var(--d-border-strong);
  content: '';
  opacity: 0.6;
}
.d-timeline__row:hover {
  background: var(--d-fill-soft);
}
.d-timeline__row:first-child::before {
  top: 19px;
}
.d-timeline__row:last-child::before {
  bottom: calc(100% - 19px);
}
.d-timeline__dot {
  position: absolute;
  top: 50%;
  left: 0;
  width: 8px;
  height: 8px;
  margin-top: -4px;
  border: 2px solid var(--d-border-strong);
  border-radius: 50%;
  background: var(--d-card);
}
.d-timeline__row.is-first .d-timeline__dot {
  border-color: var(--d-primary);
  background: var(--d-primary);
}
.d-timeline__doc {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 0;
  border: 0;
  background: none;
  color: var(--d-text);
  font: inherit;
  font-size: 14px;
  text-align: left;
  cursor: pointer;
}
.d-timeline__doc span {
  flex: 0 0 auto;
}
.d-timeline__doc em {
  overflow: hidden;
  color: var(--d-text-3);
  font-size: 12px;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.d-timeline__doc:hover em,
.d-timeline__doc:hover span {
  color: var(--d-primary);
}
.d-timeline time {
  flex: 0 0 auto;
  color: var(--d-text-3);
  font-size: 12px;
}

/* ===== 业务数据内核 ===== */
.d-bars {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 16px;
}
.d-bars__row {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr) 48px;
  align-items: center;
  gap: 12px;
  min-height: 28px;
}
.d-bars__label {
  overflow: hidden;
  color: var(--d-text-2);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.d-bars__track {
  height: 6px;
  overflow: hidden;
  border-radius: 100px;
  background: var(--d-fill);
}
.d-bars__track i {
  display: block;
  height: 100%;
  border-radius: 100px;
  background: var(--d-primary);
  opacity: 0.85;
}
.d-bars__row strong {
  color: var(--d-text);
  font-size: 13px;
  font-weight: 600;
}
.d-resources {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 16px;
  margin: 16px 0 0;
  padding-top: 16px;
  border-top: 1px solid var(--d-border);
}
.d-resources > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.d-resources dt {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--d-text-3);
  font-size: 12px;
}
.d-resources dd {
  margin: 0;
  color: var(--d-text);
  font-size: 14px;
  font-weight: 600;
}
.d-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: auto;
  padding-top: 16px;
  color: var(--d-text-3);
  font-size: 12px;
}

/* ===== 空态：图标 + 说明 + 主行动 ===== */
.d-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 24px 0;
  color: var(--d-text-3);
  font-size: 12px;
  list-style: none;
}
.d-empty .el-icon {
  color: var(--d-text-3);
  font-size: 24px;
}
.d-empty p {
  margin: 0;
}

/* ===== 简单图表卡 ===== */
.d-chart {
  display: flex;
  flex: 1;
  flex-direction: column;
  justify-content: center;
  min-height: 220px;
  margin-top: 16px;
}
.d-chart--auto {
  min-height: 0;
  max-height: 560px;
  overflow-y: auto;
}

/* ===== 响应式：1100 收两列、768 收单列、480 以下全部单列 ===== */
@media (max-width: 1100px) {
  .d-span-3,
  .d-span-4 {
    grid-column: span 6;
  }
  .d-span-5,
  .d-span-7,
  .d-span-8 {
    grid-column: span 12;
  }
  .d-core {
    grid-template-columns: 1fr;
    gap: 16px;
  }
  .d-core__gauge {
    flex-direction: row;
    justify-content: flex-start;
    padding: 0 0 16px;
    border-right: 0;
    border-bottom: 1px solid var(--d-border);
  }
  .d-core__gauge .d-facts {
    width: auto;
    flex: 1;
  }
}

@media (max-width: 768px) {
  /* 指标卡保持两列，内容卡单列 */
  .d-span-3 {
    grid-column: span 6;
  }
  .d-span-4,
  .d-span-5,
  .d-span-6,
  .d-span-7,
  .d-span-8 {
    grid-column: span 12;
  }
  .d-head__actions {
    width: 100%;
  }
  .d-seg {
    max-width: 100%;
    overflow-x: auto;
  }
  .d-orders__row {
    grid-template-columns: minmax(0, 1fr) 64px;
    padding-bottom: 8px;
  }
  .d-orders__row .d-track {
    display: none;
  }
  .d-timeline__row {
    flex-wrap: wrap;
    padding-bottom: 8px;
  }
  .d-timeline time {
    width: 100%;
    padding-left: 28px;
    text-align: left;
  }
  .d-resources {
    grid-template-columns: 1fr;
  }
  .d-stat__value strong {
    font-size: 24px;
    line-height: 32px;
  }
}

@media (max-width: 480px) {
  .d-span-3,
  .d-span-4,
  .d-span-5,
  .d-span-6 {
    grid-column: span 12;
  }
  .d-card {
    padding: 16px;
  }
  .d-core__gauge {
    flex-direction: column;
    align-items: center;
  }
  /* 窄屏去掉分隔点，避免换行后行首出现「·」 */
  .d-sep {
    display: none;
  }
  /* 窄屏不放单独的刷新图标按钮：它会独占一行，且 5 分钟自动刷新仍在 */
  .d-refresh {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .d-ring__value,
  .d-seg__item {
    transition: none;
  }
}
</style>