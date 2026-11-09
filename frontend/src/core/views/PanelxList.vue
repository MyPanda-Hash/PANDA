<template>
  <div class="panelx-list" @click="closeCtx">
    <!-- ══════════ ① 顶部工具栏（T+ 灰条 + 单据翻页）══════════ -->
    <div class="tools">
      <button type="button" class="toolbar-query-btn" :title="tt('按表头字段查询单据')" @click.stop="openQueryDialog">
        <el-icon><Search /></el-icon>
        <span>{{ tt('查询') }}</span>
      </button>
      <template v-for="(g, gi) in toolbarGroups" :key="'g' + gi">
        <div class="tb-group">
          <span class="tb-main" :class="{ disabled: !isCatchAllGroup(g) && isDisabled(btnName(g)) }" @click.stop="onGroupMain(gi, g)">
            <span class="act-name">{{ groupLabel(g) }}</span>
          </span>
          <span v-if="dropItems(g).length" class="tb-caret" @click.stop="toggleGroup(gi)">▼</span>
          <div v-if="openGroup === gi" class="tb-menu">
            <!-- 下拉排除主按钮；显示名走 tt()，动作值保持原文 -->
            <div class="ctx-item" :class="{ disabled: isDisabled(a) }" v-for="a in dropItems(g)" :key="a" @click="onGroupAction(a)">{{ tt(a) }}</div>
          </div>
        </div>
        <!-- 审批中：通过做主按钮，驳回并列露出（两个决策都不藏进下拉） -->
        <span v-if="secondaryOf(g).length" class="tb-group">
          <span class="tb-main danger" @click="onButton(secondaryOf(g)[0])">
            <span class="act-name">{{ tt(secondaryOf(g)[0]) }}</span>
          </span>
        </span>
      </template>
      <div class="tools-right">
        <!-- 外部入口（业务总览流程图/生单跳转等）带 ?from= 时给「返回」，
             让用户能从单据页回到来处；菜单直接进入的应用内导航不带，不显示返回 -->
        <button v-if="fromPath" type="button" class="toolbar-back-btn" :title="tt('返回到来源页面')" @click="goBack">
          <el-icon><ArrowLeft /></el-icon>
          <span>{{ tt('返回') }}</span>
        </button>
        <template v-if="documentListMode">
          <span class="doc-chip">{{ tt(panelName) }}</span>
          <span class="report-count">{{ tt('共') }} {{ total }} {{ tt('条') }}</span>
          <span class="page-btn" :title="tt('首页')" @click="docPage(1)">◁</span>
          <span class="page-btn" :title="tt('上一页')" @click="docPage(query.pageNo - 1)">◀</span>
          <span class="page-no">{{ tt('第') }} {{ query.pageNo }}/{{ reportPageCount }} {{ tt('页') }}</span>
          <span class="page-btn" :title="tt('下一页')" @click="docPage(query.pageNo + 1)">▶</span>
          <span class="page-btn" :title="tt('末页')" @click="docPage(reportPageCount)">▷</span>
          <span class="doc-list-tip">{{ tt('双击行打开单据详情') }}</span>
        </template>
        <template v-else-if="reportMode">
          <span class="doc-chip">{{ tt(panelName) }}</span>
          <span class="report-count">{{ tt('共') }} {{ total }} {{ tt('条') }}</span>
          <span class="page-btn" :title="tt('首页')" @click="reportPage(1)">◁</span>
          <span class="page-btn" :title="tt('上一页')" @click="reportPage(query.pageNo - 1)">◀</span>
          <span class="page-no">{{ tt('第') }} {{ query.pageNo }}/{{ reportPageCount }} {{ tt('页') }}</span>
          <span class="page-btn" :title="tt('下一页')" @click="reportPage(query.pageNo + 1)">▶</span>
          <span class="page-btn" :title="tt('末页')" @click="reportPage(reportPageCount)">▷</span>
        </template>
        <template v-else>
          <span class="doc-chip">{{ tt('单据') }}：{{ cur['编号'] || cur['单据编号'] || '-' }}</span>
          <span v-if="cur['类别']" class="doc-cat">{{ tt(cur['类别']) }}</span>
          <span v-if="cur['单据状态']" class="doc-status" :class="cur['单据状态']">{{ tt(cur['单据状态']) }}</span>
          <!-- BOM 只读浏览：翻页在父件表里做（10/20/50），这里不再保留逐张单据箭头 -->
          <template v-if="!bomBrowseAll">
            <span class="page-btn" :title="tt('首页')" @click="pageFirst">◁</span>
            <span class="page-btn" :title="tt('上一张')" @click="page(-1)">◀</span>
            <span class="page-no">{{ tt('第') }} {{ curNo }}/{{ total }} {{ tt('张') }}</span>
            <span class="page-btn" :title="tt('下一张')" @click="page(1)">▶</span>
            <span class="page-btn" :title="tt('末页')" @click="pageLast">▷</span>
          </template>
        </template>
      </div>
    </div>

    <!-- 报表沿用配置查询字段；单据页显示当前单据表头，草稿态原地编辑。 -->
    <div v-if="reportMode" class="fields udl-fields">
      <div class="field" v-for="qr in queryFields" :key="qr.dataName">
        <label :class="{ req: qr.isRequired }">{{ tt(qr.label || qr.dataName) }}</label>
        <div v-if="qType(qr) === 'ref' && isRefSelect(qr)" class="query-ref-select">
          <el-select
            v-model="condition[qr.dataName]"
            clearable
            filterable
            remote
            allow-create
            default-first-option
            :remote-method="(kw) => loadRefSelectOptions(qr, refFieldKey(qr), kw)"
            :loading="refSelectData[refFieldKey(qr)]?.loading"
            :placeholder="qr.placeholder || tt('输入搜索')"
            style="width: 100%"
            @change="search"
            @clear="search"
            @focus="checkRefMode(qr, refFieldKey(qr))"
          >
            <el-option v-for="o in refSelectData[refFieldKey(qr)]?.options || []" :key="o.value" :label="tt(o.label)" :value="o.value" />
          </el-select>
        </div>
        <div v-else-if="qType(qr) === 'ref'" class="query-ref">
          <el-input
            :model-value="condition[qr.dataName] || ''"
            readonly
            clearable
            :placeholder="qr.placeholder || tt('请选择')"
            @click="openQueryRef(qr, 'page')"
            @clear="clearQueryRef(qr, 'page')"
          />
          <el-button :icon="Search" :title="tt('打开参照')" @click="openQueryRef(qr, 'page')" />
        </div>
        <el-select
          v-else-if="qType(qr) === 'select'"
          v-model="condition[qr.dataName]"
          clearable
          filterable
          :placeholder="qr.placeholder || ''"
          @change="search"
        >
          <el-option v-for="o in qOptions(qr)" :key="o.value" :label="tt(o.label ?? o.value)" :value="o.value" />
        </el-select>
        <el-date-picker
          v-else-if="qType(qr) === 'date'"
          v-model="condition[qr.dataName]"
          type="date"
          value-format="YYYY-MM-DD"
          :placeholder="qr.placeholder || tt('选择日期')"
          @change="search"
        />
        <el-input v-else v-model="condition[qr.dataName]" :placeholder="qr.placeholder || ''" @keyup.enter="search" clearable @clear="search" />
      </div>
    </div>
    <div v-else-if="!documentListMode" class="fields header-fields udl-fields" :class="{ 'is-draft': draftEditable }">
      <div class="field" v-for="field in headerFields" :key="headerFieldKey(field)">
        <label :class="{ req: field.isRequired }">{{ tt(headerFieldLabel(field)) }}</label>
        <!-- 表头参照带出列（部门.负责人）：档案属性镜像，只读显示 -->
        <div
          v-if="isCarriedCol(headerFieldKey(field))"
          class="field-readonly carried-cell"
          :title="tt('取自参照档案，修改请到档案里改')"
        >{{ cellText(cur, headerFieldKey(field)) }}</div>
        <template v-else-if="draftEditable">
          <div v-if="isRefSelect(field) && !isRouteFlowField(field)" class="query-ref-select">
            <el-select
              v-model="cur[headerFieldKey(field)]"
              clearable
              filterable
              remote
              :disabled="headerFieldLocked(field)"
              :remote-method="(kw) => loadRefSelectOptions(field, refFieldKey(field), kw)"
              :loading="refSelectData[refFieldKey(field)]?.loading"
              :placeholder="tt('输入搜索')"
              style="width: 100%"
              @change="(v) => onHeaderRefSelect(field, v)"
              @focus="checkRefMode(field, refFieldKey(field))"
            >
              <el-option v-for="o in refSelectData[refFieldKey(field)]?.options || []" :key="o.value" :label="tt(o.label)" :value="o.value" />
            </el-select>
          </div>
          <div v-else-if="isReferenceField(field)" class="query-ref">
            <el-input
              :model-value="headerRefText(field)"
              readonly
              :disabled="headerFieldLocked(field)"
              :placeholder="tt('请选择')"
              @click="openHeaderRef(field)"
            />
            <el-button
              :icon="Search"
              :title="tt('打开参照')"
              :disabled="headerFieldLocked(field)"
              @click="openHeaderRef(field)"
            />
          </div>
          <el-select
            v-else-if="isSelectField(field)"
            v-model="cur[headerFieldKey(field)]"
            :disabled="headerFieldLocked(field)"
            clearable
            filterable
            allow-create
          >
            <el-option v-for="option in fieldOptions(field)" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
          <el-date-picker
            v-else-if="isDateField(field)"
            v-model="cur[headerFieldKey(field)]"
            :disabled="headerFieldLocked(field)"
            type="date"
            value-format="YYYY-MM-DD"
          />
          <el-input-number
            v-else-if="isNumberField(field)"
            v-model="cur[headerFieldKey(field)]"
            :disabled="headerFieldLocked(field)"
            :controls="false"
          />
          <el-switch
            v-else-if="isBooleanField(field)"
            v-model="cur[headerFieldKey(field)]"
            :disabled="headerFieldLocked(field)"
          />
          <el-input
            v-else
            v-model="cur[headerFieldKey(field)]"
            :disabled="headerFieldLocked(field)"
          />
        </template>
        <div v-else class="field-readonly" :title="String(cur[headerFieldKey(field)] ?? '')">
          {{ formatFieldValue(field, cur[headerFieldKey(field)]) }}
        </div>
      </div>
    </div>

    <div v-if="reportMode" class="report-body" v-loading="loading">
      <div class="report-heading">
        <strong>{{ tt(panelName) }}</strong>
        <span>{{ reportPeriod }}</span>
      </div>
      <el-table
        class="report-table"
        :data="list"
        border
        stripe
        size="small"
        height="100%"
        show-summary
        :summary-method="sumMethod"
        :empty-text="tt('暂无符合条件的数据')"
        @row-click="(row) => (current = row)"
      >
        <el-table-column type="index" :label="tt('序号')" width="58" fixed="left" :index="(i) => (query.pageNo - 1) * query.pageSize + i + 1" />
        <template v-for="column in reportColumnTree" :key="column.label">
          <el-table-column v-if="column.children" :label="column.label" align="center">
            <el-table-column
              v-for="child in column.children"
              :key="child.prop"
              :prop="child.prop"
              :label="child.label"
              :min-width="child.width"
              :align="child.align"
              show-overflow-tooltip
            />
          </el-table-column>
          <el-table-column
            v-else
            :prop="column.prop"
            :label="column.label"
            :min-width="column.width"
            :align="column.align"
            show-overflow-tooltip
          />
        </template>
      </el-table>
    </div>

    <div v-else-if="documentListMode" class="doc-list-body" v-loading="loading">
      <el-table
        :data="list"
        border
        stripe
        size="small"
        height="100%"
        :row-class-name="docRowCls"
        :empty-text="tt('暂无符合条件的数据')"
        @row-click="onDocRowClick"
        @row-dblclick="openDocForm"
      >
        <el-table-column type="index" :label="tt('序号')" width="58" fixed="left" :index="(i) => (query.pageNo - 1) * query.pageSize + i + 1" />
        <el-table-column
          v-for="c in documentCols"
          :key="c"
          :prop="c"
          :label="tt(columnAliases[c] || c)"
          :min-width="c === '单据编号' || c === '发票号码' ? 150 : 110"
          :align="['不含税金额', '税额', '价税合计'].includes(c) ? 'right' : 'left'"
          show-overflow-tooltip
        >
          <template #default="{ row }">{{ cellText(row, c) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <div v-else class="body-outer">
      <!-- 左侧单据选择栏（镜像当前页单据，点行切单据；窄屏自动隐藏） -->
      <DocSelectRail
        v-if="showDocRail"
        :rows="list"
        :columns="railColumns"
        :current-no="String(cur['编号'] || cur['单据编号'] || cur['单号'] || '')"
        :total="total"
        :page-no="query.pageNo"
        :page-size="query.pageSize"
        :keyword="query.keyword"
        :collapsed="railCollapsed"
        :loading="loading"
        @select="onRailSelect"
        @page="onRailPage"
        @search="onRailSearch"
        @toggle="toggleRail"
        @create="directAdd"
      />
      <div class="body" :class="{ 'draft-body': draftEditable }" v-loading="loading && !isBomMasterPanel">
      <!-- ══════════ 物料清单专用：父件表格 + 子件表格联动（BOM/BOM_FWD/BOM_REV） ══════════ -->
      <BomMasterDetail
        v-if="isBomMasterPanel"
        ref="bomMasterRef"
        :rows="bomMasterRows"
        :fields="bomMasterFields"
        :document-no="cur['编号'] || ''"
        :reverse="panelCode === 'BOM_REV'"
        :editable="panelCode === 'BOM' && draftEditable"
        :loading="loading"
        :pager="bomBrowseAll"
        :total="bomPagerTotal"
        :page-no="query.pageNo"
        :page-size="query.pageSize"
        @update:rows="onBomRowsUpdate"
        @page-change="onBomPageChange"
        @page-size-change="onBomPageSizeChange"
        @select-master="onBomSelectMaster"
      />
      <template v-else>
      <!-- ══════════ ③b 主表预览表格（配置 mainTable 时显示：主表字段列，点行切换当前单据，明细联动） -->
      <div v-if="mainGrid" class="main-grid">
        <div class="dt-head">
          <span class="dt-tab on">{{ mainGrid.label }}</span>
        </div>
        <el-table :data="mainFilteredRows()" border size="small" :row-class-name="mainRowCls" @row-click="onMainRowClick" @row-dblclick="openMaintain">
          <el-table-column type="index" :label="tt('序号')" width="60" align="center" :index="(i) => i + 1" />
          <el-table-column v-for="c in mainCols" :key="c" :prop="c" :label="tt(c)" min-width="110" show-overflow-tooltip>
            <template #header>
              <ColumnFilterHeader
                :label="tt(columnAliases[c] || c)"
                :model-value="mainFilterOf(c)"
                :active="!!parseColumnFilter(mainFilterOf(c))"
                @update:model-value="(v) => setMainFilter(c, v)"
                @clear="setMainFilter(c, '')"
              />
            </template>
            <template #default="{ row }">{{ cellText(row, c) }}</template>
          </el-table-column>
        </el-table>
      </div>
      <!-- ══════════ ③ 表中 · 明细区块（配置驱动：区块内多页签，同 T+）══════════ -->
      <div class="detail" v-for="(b, bi) in blocks" :key="b.id">
        <div class="dt-head">
          <span v-if="isApproved && bi === 0" class="approved-stamp">{{ tt('已审批') }}</span>
          <span v-for="it in headItems(b)" :key="it.kind + it.key" class="dt-tab" :class="{ on: isOn(b, it) }" @click="switchTab(b, it)">{{ tt(it.label) }}</span>
          <span v-if="b.id === 'B' && activeTab(b).key === 'materials' && selectedProduct" class="filter-hint">{{ tt('当前产品') }}：{{ selectedProduct }} {{ tt('的 BOM 子件') }}</span>
          <span class="dt-ics">
            <el-button v-if="detailEditable(b)" size="small" type="primary" :icon="Plus" @click="addInlineDetailRow(b)">{{ tt('新增数据') }}</el-button>
            <span class="dt-ic" v-for="ic in blockIcons(b)" :key="ic" @click="onIcon(ic, b)">{{ tt(ic) }}</span>
          </span>
        </div>
        <el-table
          :data="blockRows(b)"
          :max-height="tableH(b)"
          border
          size="small"
          :show-summary="tabView(b, activeTab(b)) !== 'summary'"
          :summary-method="(payload) => sumMethodOf(b, payload)"
          :sum-text="tt('合计')"
          :row-class-name="(o) => rowCls(o, b)"
          @selection-change="(r) => (delSel = r)"
          @row-contextmenu="(row, col, ev) => onCtx(ev, row, b)"
          @cell-dblclick="(row, col, cell, ev) => onDetailCellDblclick(row, col, ev, b)"
          @row-click="(row) => onRowClick(row, b)"
          @click.capture="(e) => onTableClick(b, e)"
        >
          <el-table-column v-if="delMode && b.isMain" type="selection" width="45" fixed="left" />
          <el-table-column
            v-for="c in blockCols(b)"
            :key="c.prop"
            :prop="c.prop"
            :label="c.label"
            :min-width="c.width"
            :align="c.align"
            :show-overflow-tooltip="!detailEditable(b)"
          >
            <!-- 列头筛选：点表头右侧漏斗出输入框，生效后列头显示「筛选词 ×」 -->
            <template #header>
              <ColumnFilterHeader
                :label="c.label"
                :model-value="filterOf(b, c.prop)"
                :active="hasFilter(b, c.prop)"
                @update:model-value="(v) => setFilter(b, c.prop, v)"
                @clear="setFilter(b, c.prop, '')"
              />
            </template>
            <template #default="{ row }">
              <!-- 参照带出列（存货名称.品牌）：档案属性镜像，只显示不编辑 -->
              <span v-if="c.carried" class="carried-cell" :title="tt('取自参照档案，修改请到档案里改')">{{ cellText(row, c.prop) }}</span>
              <template v-else-if="detailEditable(b) && !row._placeholder">
                <span v-if="c.field.computed" class="inline-computed-value">{{ formatFieldValue(c.field, row[c.prop]) }}</span>
                <el-select
                  v-else-if="isRefSelect(c.field)"
                  v-model="row[c.prop]"
                  class="inline-ref-select"
                  clearable
                  filterable
                  remote
                  :remote-method="(kw) => loadRefSelectOptions(c.field, refFieldKey(c.field), kw)"
                  :loading="refSelectData[refFieldKey(c.field)]?.loading"
                  :placeholder="tt('输入搜索')"
                  @change="(v) => onDetailRefSelect(c.field, row, b, v)"
                  @visible-change="(vis) => vis && checkRefMode(c.field, refFieldKey(c.field))"
                >
                  <el-option v-for="o in refSelectData[refFieldKey(c.field)]?.options || []" :key="o.value" :label="tt(o.label)" :value="o.value" />
                </el-select>
                <div v-else-if="isReferenceField(c.field)" class="inline-ref-editor" :class="{ active: isActiveDetailRefRow(row, b, c.prop) }">
                  <el-input
                    :model-value="formatFieldValue(c.field, row[c.prop])"
                    readonly
                    :title="detailRefTrigger(c.field) === 'dblclick' ? tt('双击选择存货') : tt('点击选择')"
                    @click="openClickDetailRef(c.field, row, b)"
                  />
                  <el-icon v-if="detailRefTrigger(c.field) === 'dblclick' && isActiveDetailRefRow(row, b, c.prop)" class="list-ref-icon"><Search /></el-icon>
                </div>
                <el-select
                  v-else-if="isSelectField(c.field)"
                  v-model="row[c.prop]"
                  :disabled="c.field.computed || priceLocked(c.prop)"
                  filterable
                  clearable
                  allow-create
                  @change="onInlineDetailChange(activeTab(b).key, row, c.field)"
                >
                  <el-option v-for="option in fieldOptions(c.field)" :key="option.value" :label="option.label" :value="option.value" />
                </el-select>
                <el-date-picker
                  v-else-if="isDateField(c.field)"
                  v-model="row[c.prop]"
                  :disabled="c.field.computed || priceLocked(c.prop)"
                  type="date"
                  value-format="YYYY-MM-DD"
                  @change="onInlineDetailChange(activeTab(b).key, row, c.field)"
                />
                <el-input-number
                  v-else-if="isNumberField(c.field)"
                  v-model="row[c.prop]"
                  :disabled="c.field.computed || priceLocked(c.prop)"
                  :controls="false"
                  @change="onInlineDetailChange(activeTab(b).key, row, c.field)"
                />
                <el-switch
                  v-else-if="isBooleanField(c.field)"
                  v-model="row[c.prop]"
                  :disabled="c.field.computed || priceLocked(c.prop)"
                  @change="onInlineDetailChange(activeTab(b).key, row, c.field)"
                />
                <el-input
                  v-else
                  v-model="row[c.prop]"
                  :disabled="c.field.computed || priceLocked(c.prop)"
                  @change="onInlineDetailChange(activeTab(b).key, row, c.field)"
                />
              </template>
              <span v-else-if="c.prop === '材料编码' && activeTab(b).key === 'materials'" class="mat-cell">
                <span>{{ row[c.prop] }}</span>
                <span v-if="hasSubBom(row[c.prop])" class="mat-star" :title="tt('该材料有下级子件 BOM，点击行查看')">*</span>
              </span>
              <span v-else>{{ cellText(row, c.prop) }}</span>
            </template>
          </el-table-column>
        </el-table>
        <!-- 明细区翻页条：每页 10/20/50 行可选，超过一页才出现翻页箭头；合计按整块全部行计算 -->
        <div v-if="blockAllRows(b).length || filterCountOf(b)" class="dg-pager">
          <span class="dg-count">{{ tt('共') }} {{ blockAllRows(b).length }} {{ tt('行') }}</span>
          <span v-if="filterCountOf(b)" class="dg-filter-hint">
            {{ tt('已筛选') }} {{ filterCountOf(b) }} {{ tt('列') }}
            <el-button link type="primary" size="small" @click="clearBlockFilters(b)">{{ tt('清除筛选') }}</el-button>
          </span>
          <span class="dg-per">
            <span class="dg-per-label">{{ tt('每页') }}</span>
            <el-select
              :model-value="blockSize(b)"
              size="small"
              class="dg-size"
              @change="(v) => setBlockSize(b, v)"
            >
              <el-option v-for="n in ROWS_PER_PAGE_OPTIONS" :key="n" :label="String(n)" :value="n" />
            </el-select>
            <span class="dg-per-label">{{ tt('行') }}</span>
          </span>
          <template v-if="blockPageCount(b) > 1">
            <span class="page-btn" :title="tt('上一页')" @click="stepBlockPage(b, -1)">◀</span>
            <span class="page-no">{{ tt('第') }} {{ blockPage(b) }}/{{ blockPageCount(b) }} {{ tt('页') }}</span>
            <span class="page-btn" :title="tt('下一页')" @click="stepBlockPage(b, 1)">▶</span>
          </template>
        </div>
      </div>
      </template>

      </div>
    </div>

    <!-- ══════════ ④ 表尾（固定在页面底部，滚动明细时始终可见；备注 + 审核行）══════════ -->
    <div v-if="showFooter" class="footer">
      <div class="remark">
        <label>{{ tt('备注') }}</label>
        <el-input v-model="remarkText" size="small" placeholder="" :disabled="!draftEditable" />
      </div>
      <div class="footer-hr"></div>
      <div class="audit-line">
        <span>{{ tt('制单人') }}：{{ cur['制单人'] || cur['发起人编号'] || '' }}</span>
        <span>{{ tt('审核人') }}：{{ cur['审核人'] || '' }}</span>
        <span>{{ tt('审核日期') }}：{{ cur['审核日期'] || '' }}</span>
        <span>{{ tt('审核时间') }}：{{ cur['审核时间'] || '' }}</span>
        <span>{{ tt('打印次数') }}：{{ cur['打印次数'] ?? 0 }}</span>
        <span>{{ tt('创建时间') }}：{{ cur['创建时间'] || '' }}</span>
        <span>{{ tt('审核意见') }}：{{ cur['审核意见'] || '-' }}</span>
      </div>
    </div>

    <!-- ══════════ 表格右键菜单（对齐真实 T+ 明细右键）══════════ -->
    <div v-if="ctx.visible" class="ctx-menu" :style="{ left: ctx.x + 'px', top: ctx.y + 'px' }">
      <div class="ctx-item" v-for="it in ctxItems" :key="it" @click="onCtxItem(it)">{{ tt(it) }}</div>
    </div>

    <RefPickDialog v-model="queryRefVisible" :field="queryRefField" mode="query" @confirm="onQueryRefConfirm" />
    <RefPickDialog v-model="headerRefVisible" :field="headerRefField" mode="header" @confirm="onHeaderRefConfirm" />
    <RouteFlowPickDialog v-model="routeFlowVisible" :field="headerRefField" @confirm="onHeaderRefConfirm" />
    <RefPickDialog v-model="detailRefVisible" :field="detailRefPick?.field" mode="detail" @confirm="onDetailRefConfirm" />
    <el-dialog v-model="queryDialogVisible" :title="tt('查询')" width="760px" append-to-body destroy-on-close class="header-query-dialog">
      <div class="query-dialog-fields">
        <div v-for="field in queryDialogFields" :key="headerFieldKey(field)" class="query-dialog-field">
          <label>{{ tt(headerFieldLabel(field)) }}</label>
          <el-select
            v-if="isRefSelect(field)"
            v-model="queryDraft[headerFieldKey(field)]"
            clearable
            filterable
            remote
            allow-create
            default-first-option
            :remote-method="(kw) => loadRefSelectOptions(field, refFieldKey(field), kw)"
            :loading="refSelectData[refFieldKey(field)]?.loading"
            :placeholder="tt('输入搜索')"
            style="width: 100%"
            @focus="checkRefMode(field, refFieldKey(field))"
          >
            <el-option v-for="o in refSelectData[refFieldKey(field)]?.options || []" :key="o.value" :label="tt(o.label)" :value="o.value" />
          </el-select>
          <div v-else-if="isReferenceField(field)" class="query-ref">
            <el-input
              :model-value="queryDraft[headerFieldKey(field)] ?? ''"
              readonly
              clearable
              :placeholder="tt('请选择')"
              @click="openQueryRef(field, 'dialog')"
              @clear="clearQueryRef(field, 'dialog')"
            />
            <el-button :icon="Search" :title="tt('打开参照')" @click="openQueryRef(field, 'dialog')" />
          </div>
          <el-select v-else-if="isSelectField(field)" v-model="queryDraft[headerFieldKey(field)]" clearable filterable allow-create>
            <el-option v-for="option in fieldOptions(field)" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
          <el-date-picker v-else-if="isDateField(field)" v-model="queryDraft[headerFieldKey(field)]" type="date" value-format="YYYY-MM-DD" />
          <el-input-number v-else-if="isNumberField(field)" v-model="queryDraft[headerFieldKey(field)]" :controls="false" />
          <el-select v-else-if="isBooleanField(field)" v-model="queryDraft[headerFieldKey(field)]" clearable>
            <el-option :label="tt('是')" :value="true" />
            <el-option :label="tt('否')" :value="false" />
          </el-select>
          <el-input v-else v-model="queryDraft[headerFieldKey(field)]" clearable @keyup.enter="applyHeaderQuery" />
        </div>
      </div>
      <template #footer>
        <el-button @click="resetHeaderQuery">{{ tt('重置') }}</el-button>
        <el-button @click="queryDialogVisible = false">{{ tt('取消') }}</el-button>
        <el-button type="primary" :icon="Search" @click="applyHeaderQuery">{{ tt('查询') }}</el-button>
      </template>
    </el-dialog>
    <!-- 核销：按单勾选（来源单/未结金额/本次核销） -->
    <el-dialog v-model="writeOffVisible" :title="tt('核销') + ' · ' + writeOffNo" width="min(680px, 94vw)" append-to-body>
      <el-table :data="writeOffRows" size="small" border max-height="360">
        <el-table-column prop="往来单号" :label="tt('来源单号')" min-width="150" />
        <el-table-column prop="业务日期" :label="tt('业务日期')" width="110" />
        <el-table-column prop="未结金额" :label="tt('未结金额')" width="110" align="right" />
        <el-table-column :label="tt('本次核销')" width="150">
          <template #default="{ row }">
            <el-input-number v-model="row.核销金额" :min="0" :max="Number(row['未结金额'])" size="small" controls-position="right" style="width: 120px" />
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button size="small" @click="writeOffVisible = false">{{ tt('取消') }}</el-button>
        <el-button size="small" type="primary" @click="submitWriteOff">{{ tt('确认核销') }}</el-button>
      </template>
    </el-dialog>
    <!-- 选工艺路线：按 PDM 路线带出工序明细 -->
    <el-dialog v-model="routeVisible" :title="tt('选工艺路线')" width="min(520px, 94vw)" append-to-body>
      <el-form label-width="96px" size="small">
        <el-form-item :label="tt('加工单')"><span>{{ routeForm['编号'] }}</span></el-form-item>
        <el-form-item :label="tt('工艺路线')" required>
          <el-select v-model="routeForm['工艺路线编码']" filterable style="width: 260px">
            <el-option v-for="r in routeOptions" :key="r.code" :label="r.name + '（' + r.code + '）'" :value="r.code" />
          </el-select>
        </el-form-item>
        <el-form-item :label="tt('工序数量')">
          <el-input-number v-model="routeForm['数量']" :min="0" style="width: 160px" controls-position="right" />
          <span class="dim" style="margin-left: 8px">{{ tt('留空取产成品数量') }}</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="routeVisible = false">{{ tt('取消') }}</el-button>
        <el-button size="small" type="primary" @click="submitRoute">{{ tt('带入工序') }}</el-button>
      </template>
    </el-dialog>
    <NewVoucherDialog v-model:visible="newVisible" :panelCode="panelCode" :panel-name="panelName" @saved="onNewSaved" />
    <SubBomDialog v-model="subBomVisible" :material="subBomMaterial" :bom="subBomBom" />
    <ImportDialog v-model="impVisible" :fields="impFields" :target-label="impLabel" @imported="onImported" />
    <ApprovalHistoryDialog v-model="approvalVisible" :panelCode="panelCode" :formNo="approvalNo" />
    <SelectVoucherDialog v-model="selVisible" :panelCode="panelCode" :config="selCfg" @generated="onSelGenerated" />
    <ProducePreviewDialog v-model="produceVisible" :docNo="produceNo" @generated="onProduceGenerated" />
    <SaleOutPreviewDialog v-model="saleOutVisible" :docNo="saleOutNo" @generated="onSaleOutGenerated" />
    <FinishInPreviewDialog v-model="finishInVisible" :docNo="finishInNo" @generated="onFinishInGenerated" />
    <InvoiceLinePickDialog
      v-model="invoicePickVisible"
      :mode="invoicePickMode"
      :supplier="invoicePickSupplier"
      :panel-code="panelCode"
      @confirm="onInvoiceSourceConfirm"
    />
    <InvoiceImportDialog v-model="invoiceImportVisible" :panel-code="panelCode" @imported="onInvoiceImported" />
    <DetailMaintainDialog v-model="maintainVisible" :panel-code="panelCode" :row="maintainRow" @saved="onMaintainSaved" />
    <ScanFillDialog
      v-model="scanVisible"
      :panel-code="panelCode"
      :panel-name="panelName"
      :header-fields="headerFields"
      :detail-tabs="cfgCache?.detail?.tabs || []"
      @apply="onScanApply"
    />
    <ColumnPrefsDialog
      v-model="colPrefsVisible"
      :panel-code="panelCode"
      :panel-name="panelName"
      :columns="colPrefsColumns"
      @save="onColumnPrefsSaved"
    />
    <!-- 字段管理：面板自助加字段（明细/表头），保存后重取配置即出现在列表与表单里 -->
    <FieldManagerDialog
      v-model="fieldMgrVisible"
      :panel-code="panelCode"
      :panel-name="panelName"
      :config="cfgCache"
      @done="onFieldManageDone"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onActivated, onUnmounted, onDeactivated, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Plus, Search } from '@element-plus/icons-vue'
import { useTabsStore } from '@/stores/tabs'
import { useUserStore } from '@/stores/user'
import { usePanelRuntime } from '@core/panel-runtime'
import { INVOICE_IMPORT_ACTION, INVOICE_LINE_ACTION, filterSupportedActions } from '@core/action-support'
import { ensureAuditAction, ensureFieldManageAction, ensureScanFillAction, ensureColumnPrefsAction, ensureSelectAction, hasSelectConfig, isApprovalGroup, isCatchAllGroup, primaryActionOf, approvalSecondaryOf, supportsAudit } from '@core/button-groups'
import { isDocApproved } from '@core/doc-status'
import { computeSums, summaryColumnsOf } from './report-sums.js'
import { hasUnqualifiedTab, syncUnqualifiedRow } from '@core/inspection-unqualified.js'
import { tt } from '@/i18n'
import { useLocaleStore } from '@/stores/locale'
import RefPickDialog from './RefPickDialog.vue'
import RouteFlowPickDialog from './RouteFlowPickDialog.vue'
import NewVoucherDialog from './NewVoucherDialog.vue'
import ApprovalHistoryDialog from './ApprovalHistoryDialog.vue'
import SelectVoucherDialog from './SelectVoucherDialog.vue'
import ProducePreviewDialog from './ProducePreviewDialog.vue'
import SaleOutPreviewDialog from './SaleOutPreviewDialog.vue'
import FinishInPreviewDialog from './FinishInPreviewDialog.vue'
import InvoiceLinePickDialog from './InvoiceLinePickDialog.vue'
import InvoiceImportDialog from './InvoiceImportDialog.vue'
import SubBomDialog from './SubBomDialog.vue'
import BomMasterDetail from './BomMasterDetail.vue'
import { flattenDocDetailRows } from './master-detail-rows.js'
import ImportDialog from './ImportDialog.vue'
import DetailMaintainDialog from './DetailMaintainDialog.vue'
import ScanFillDialog from './ScanFillDialog.vue'
import ColumnPrefsDialog from './ColumnPrefsDialog.vue'
import FieldManagerDialog from './FieldManagerDialog.vue'
import ColumnFilterHeader from './ColumnFilterHeader.vue'
import DocSelectRail from './DocSelectRail.vue'
import { railColumnsOf } from './doc-rail-rows.js'
import { activeFilterCount, applyColumnFilters, parseColumnFilter } from './column-filter.js'
import { buildRefIndex, carriedValue, splitDotted } from './ref-carry.js'
import {
  DEFAULT_PAGE_SIZE,
  ROWS_PER_PAGE_OPTIONS,
  clampPage,
  gridHeight,
  normalizePageSize,
  padRows,
  pageCount,
  pageSlice,
} from './list-grid-paging.js'

const engine = usePanelRuntime()
// 权限过滤经引擎通道取用（core 层不直接依赖 business 层）
const { filterButtonGroups, priceFieldLocked } = engine
const route = useRoute()
const router = useRouter()
const tabs = useTabsStore()
const user = useUserStore()
const localeStore = useLocaleStore()

const panelCode = computed(() => route.params.panelCode)
const operationName = computed(() => route.meta.operationName || route.query.operationName || '新增流程')
const invalidPanel = computed(() => !panelCode.value || panelCode.value === 'undefined')

// 物料清单维护和正反向查询统一使用父件/子件主从视图；仅 BOM 草稿开放编辑。
const isBomMasterPanel = computed(() => ['BOM', 'BOM_FWD', 'BOM_REV'].includes(String(panelCode.value)))
// 只读浏览时父件表列出「所有单据的父件」，点哪行看哪行的子件；编辑草稿时仍只维护当前单据
/** 主从视图里点选中的父件所属单据号（「修改」跳转用） */
const boMasterPickedDoc = ref('')
const bomBrowseAll = computed(() => isBomMasterPanel.value && !(panelCode.value === 'BOM' && draftEditable.value))
const bomMasterRows = computed(() => {
  if (panelCode.value === 'BOM') {
    if (draftEditable.value) return cur.value?.detail?.['children'] || []
    return flattenDocDetailRows(list.value, 'children')
  }
  return list.value || [] // BOM_FWD/BOM_REV：后端返回的展平行（父件-子件对）
})
const bomMasterFields = computed(() => (
  (cfgCache.value?.detail?.tabs || []).find((tab) => tab.key === 'children')?.fields || []
))
const bomMasterRef = ref(null)

function onBomRowsUpdate(rows) {
  if (panelCode.value !== 'BOM' || !draftEditable.value) return
  if (!cur.value.detail) cur.value.detail = {}
  cur.value.detail.children = rows
}

// ---------- 父件表分页（只读浏览时替代「第 N 张单据」逐张翻） ----------
const bomPagerTotal = computed(() => total.value || 0)

async function onBomPageChange(next) {
  const last = Math.max(1, Math.ceil(bomPagerTotal.value / Math.max(1, query.pageSize)))
  const target = Math.max(1, Math.min(Number(next) || 1, last))
  if (target === query.pageNo) return
  query.pageNo = target
  await load()
}

async function onBomPageSizeChange(size) {
  const next = normalizePageSize(size)
  if (next === query.pageSize) return
  query.pageSize = next
  query.pageNo = 1
  await load()
}

// 点父件行 → 当前单据跟着切过去，表头「单据：xxx」与子件都对得上
/** 当前选中的父件所属 BOM 单据号：优先主从视图里选中的父件行，其次当前单据。 */
function bomTargetDocNo() {
  const picked = boMasterPickedDoc.value
  if (picked) return picked
  return cur.value?.['编号'] || cur.value?.['单据编号'] || ''
}

/**
 * 物料清单「修改」：直接打开选中父件所属的那张 BOM 单据。
 *
 * <p>以前用户得自己在单据列表里翻到对应单据，父件一多就找不到；现在点父件行再点「修改」
 * 就落在那张单上（表单页按 code 定位）。BOM_FWD/BOM_REV 是只读查询视图，同样跳过去看/改。
 */
async function openBomDocumentForEdit() {
  const docNo = bomTargetDocNo()
  if (!docNo) return ElMessage.warning(tt('请先选择一行父件'))
  await router.push({ path: '/panelx/form/BOM', query: { code: docNo } })
}

function onBomSelectMaster(row) {
  const docNo = row && row._docNo
  if (!docNo) return
  boMasterPickedDoc.value = docNo
  const index = list.value.findIndex((doc) => (doc['编号'] || doc['单据编号'] || '') === docNo)
  if (index >= 0) curIdx.value = index
}

const query = reactive({ keyword: '', pageNo: 1, pageSize: 20 })
const condition = reactive({})
const list = ref([])
const total = ref(0)
const loading = ref(false)
const current = ref(null)
const queryFields = ref([])
const gridTabs = ref([])
const groups = ref([])
const panelName = ref('')
const cfgCache = ref(null)
const queryRefVisible = ref(false)
const queryRefField = ref(null)
const queryRefContext = ref('page')
const queryDialogVisible = ref(false)
const queryDraft = reactive({})
const headerRefVisible = ref(false)
const headerRefField = ref(null)
const routeFlowVisible = ref(false)
const detailRefVisible = ref(false)
const detailRefPick = ref(null)
const detailRefSaving = ref(false)
const inlineSaving = ref(false)
const reportMode = computed(() => cfgCache.value?.metadata?.report === true || cfgCache.value?.metadata?.panelCategory === '报表')
/** 单据列表模式：tablePages[0].listMode='document' 时只显示表头主信息，新增/双击进入表单页。 */
const documentListMode = computed(() => cfgCache.value?.metadata?.panelPageDto?.tablePages?.[0]?.listMode === 'document')
const documentCols = computed(() => (gridTabs.value[0]?.columns || []).filter(Boolean))
/**
 * 外部入口带来的来源页（如业务总览流程图 ?from=/scm/businessOverview）：
 * 只有这种「从别处跳进来」的访问才显示「返回」，菜单直接打开的面板不显示。
 */
const fromPath = computed(() => {
  const from = route.query.from
  return typeof from === 'string' && from.startsWith('/') ? from : ''
})

function goBack() {
  const from = fromPath.value
  if (!from) return
  tabs.close(route.path)
  router.push(from)
}
const reportPageCount = computed(() => Math.max(1, Math.ceil(total.value / query.pageSize)))
const reportPeriod = computed(() => {
  const start = condition['开始日期']
  const end = condition['结束日期']
  if (start && end) return `${start} - ${end}`
  if (start) return `${start} ${tt('起')}`
  if (end) return `${tt('截至')} ${end}`
  return tt('当前业务数据')
})
const reportColumns = computed(() => gridTabs.value[0]?.columns || [])
// 表格列定制（阶段 C）：表头别名（数据键/取值仍是中文列名，仅显示替换）
const columnAliases = ref({})
const colPrefsVisible = ref(false)
const colPrefsColumns = ref([])
const supportsColPrefs = computed(() => typeof engine.saveColumnPrefs === 'function')
// 字段管理（面板自助加字段）：引擎提供能力 + 当前用户有本面板「修改」权限才显示入口
// （服务端 addCustomField/retireCustomField 会再判一次 edit 权限，前端显隐只是少一个点不开的菜单）
const fieldMgrVisible = ref(false)
const supportsFieldManage = computed(() => (
  typeof engine.addCustomField === 'function' && (user.isAdmin || user.hasPerm(panelCode.value, 'edit'))
))
const reportColumnTree = computed(() => {
  const groups = gridTabs.value[0]?.columnGroups || []
  const owner = new Map()
  for (const group of groups) for (const column of group.columns || []) owner.set(column, group)
  const emitted = new Set()
  const out = []
  for (const column of reportColumns.value) {
    const group = owner.get(column)
    if (group) {
      if (emitted.has(group.label)) continue
      emitted.add(group.label)
      out.push({
        label: group.label,
        children: (group.columns || []).filter((name) => reportColumns.value.includes(name)).map(reportLeaf),
      })
    } else {
      out.push(reportLeaf(column))
    }
  }
  return out
})
/** refDialog: route-flow 的字段强制弹窗（不受「≤20 行走下拉」影响，下拉看不到工序流程） */
function isRouteFlowField(field) {
  return (field?.refDialog || field?.ref?.dialog) === 'route-flow'
}

const toolbarGroups = computed(() => (groups.value || []).map((group) => {
  // 2026-11-04（对齐 yinjia）：列表页不再提供能打开整单卡片（VoucherFormDialog）的入口 ——
  //   「修改」动作唯一的作用就是弹那张卡片，卡片删除后它成了死按钮，故与 查询/查找 一样从工具栏摘掉。
  //   单据数据统一在列表页原地内联编辑（草稿态，见 draftEditable）；已审核单据先「弃审」回到草稿。
  // 单据列表模式只保留对整张发票有意义的动作；保存/选单/行级关联/删行在详情页做。
  const detailOnly = new Set(['保存', '保存新增', '保存为草稿', '保存打印', '选单', '选采购入库单', '选采购订单', '选入库单行', '导入发票', '删除'])
  // 物料清单（BOM/BOM_FWD/BOM_REV）：保留「修改」，作用=跳到选中父件所属的那张 BOM 单据，
  // 不用用户自己在单据列表里翻找。其余面板维持原规则（修改已无对应交互，不显示）。
  const keepModify = isBomMasterPanel.value;
  const blocked = keepModify ? ['查询', '查找'] : ['查询', '查找', '修改'];
  const actions = actsOf(group).filter((action) => !blocked.includes(action)
    && !(documentListMode.value && detailOnly.has(action)))
  const name = ['查询', '查找'].includes(group.name) ? (actions[0] || group.name) : group.name
  return { ...group, name, actions }
}).filter((group) => actsOf(group).length))
const headerFields = computed(() => {
  const fields = (cfgCache.value?.dataSchema?.fields || []).filter((field) => !field.hidden)
  const names = cfgCache.value?.metadata?.panelPageDto?.formPages?.[0]?.fieldNames
  if (!names) return fields
  const ordered = String(names).split(',').map((name) => name.trim()).filter(Boolean)
  const byName = new Map(fields.map((field) => [headerFieldKey(field), field]))
  return ordered.map((name) => byName.get(name)).filter(Boolean)
})
const queryDialogFields = computed(() => {
  const fields = reportMode.value ? queryFields.value : headerFields.value
  return fields.filter((field) => headerFieldKey(field) !== '备注')
})
const draftEditable = computed(() => {
  if (reportMode.value || documentListMode.value || ['BOM_FWD', 'BOM_REV'].includes(String(panelCode.value))) return false
  const st = cur.value?.['单据状态']
  if (st === '草稿') return true
  // 档案/单单据面板（存货档案、员工、部门、工艺路线等）：启用/停用状态列表页同样内联可编辑（2026-08-24）
  if ((cfgCache.value?.metadata?.singleDoc || cfgCache.value?.metadata?.panelCategory === '设置') && (st === '启用' || st === '停用')) return true
  return false
})
const newVisible = ref(false)
const approvalVisible = ref(false)
const approvalNo = ref('')
const selVisible = ref(false)
const impVisible = ref(false)
const impFields = ref([])
const impLabel = ref('明细')
const scanVisible = ref(false)
const selCfg = ref(null)

// 采购发票行级选入库单/退货单行：列表页草稿内联编辑态同样支持
const invoicePickVisible = ref(false)
const invoicePickMode = ref('purchase')
const invoicePickSupplier = ref('')
const invoiceImportVisible = ref(false)

function openInvoiceLinePick() {
  if (!draftEditable.value) return ElMessage.warning(tt('当前单据不可编辑'))
  const businessType = String(cur.value['业务类型'] || '')
  invoicePickMode.value = businessType.includes('退货') ? 'return' : 'purchase'
  invoicePickSupplier.value = String(cur.value['供应商'] || '')
  invoicePickVisible.value = true
}

/** 弹窗确认：追加到主明细，来源行按「来源面板+来源单号+来源行号」去重。 */
function onInvoiceSourceConfirm(selected) {
  if (!draftEditable.value) return ElMessage.warning(tt('当前单据不可编辑'))
  const picked = Array.isArray(selected) ? selected : []
  if (!picked.length) return
  const tabKey = cfgCache.value?.detail?.tabs?.[0]?.key || 'items'
  if (!cur.value.detail) cur.value.detail = {}
  const target = cur.value.detail[tabKey] || (cur.value.detail[tabKey] = [])
  const { rows, duplicates } = engine.buildInvoiceLineRows(picked, invoicePickMode.value, target)
  for (const row of rows) {
    target.push(row)
    calculateDetailRow(tabKey, row)
  }
  if (rows.length) {
    // 追加行在末尾：翻到最后一页，否则当前分页看不到新增行（与「新增数据」同口径）
    const mainBlock = blocks.value.find((b) => b.isMain)
    if (mainBlock) view[mainBlock.id + ':page'] = pageCount(blockAllRows(mainBlock).length, blockSize(mainBlock))
  }

  if (rows.length) {
    const supplierRow = picked.find((row) => row['供应商'])
    if (!String(cur.value['供应商'] || '').trim() && supplierRow?.['供应商']) {
      cur.value['供应商'] = supplierRow['供应商']
      if (!String(cur.value['供应商编码'] || '').trim()) cur.value['供应商编码'] = supplierRow['供应商编码'] || ''
    }
    if (!String(cur.value['采购订单号'] || '').trim()) {
      const orderNo = picked.find((row) => row['采购订单号'])?.['采购订单号']
      if (orderNo) cur.value['采购订单号'] = orderNo
    }
    // 表头金额由明细汇总（与后端 deriveInvoiceHeader 同口径）
    const totals = engine.invoiceHeaderTotals(target)
    for (const [key, value] of Object.entries(totals)) {
      if (Object.prototype.hasOwnProperty.call(cur.value, key)) cur.value[key] = value
    }
  }

  if (!rows.length) {
    ElMessage.warning(tt('所选来源行已在明细中，未重复追加'))
  } else if (duplicates) {
    ElMessage.success(tt('已追加') + ` ${rows.length} ` + tt('行') + '，' + tt('跳过') + ` ${duplicates} ` + tt('行重复来源'))
  } else {
    ElMessage.success(tt('已追加') + ` ${rows.length} ` + tt('行明细'))
  }
}

function selectConfigFor(action = '选单') {
  const cfg = cfgCache.value || {}
  const configs = cfg.selectConfigs || {}
  if (configs[action]) return configs[action]
  // 单来源面板的组动作常写成「选销售订单」「选报价单」，只有与配置标题一致时才复用，
  // 否则「选销售出库单」会悄悄弹出「选销售订单」（来源与用户点的不一致）
  if (cfg.selectConfig) {
    const title = String(cfg.selectConfig.title || '选单')
    if (action === '选单' || action === title) return cfg.selectConfig
  }
  return action === '选单' ? Object.values(configs)[0] || null : null
}
const routeVisible = ref(false)
const routeOptions = ref([])
const routeForm = ref({ 编号: '', 工艺路线编码: '', 数量: 0 })
async function submitRoute() {
  if (!routeForm.value['工艺路线编码']) return ElMessage.warning(tt('请选择工艺路线'))
  try {
    const res = await engine.callButton({
      panelCode: 'MANU_ORDER', buttonName: '选工艺路线',
      formData: routeForm.value, buttonParam: { code: routeForm.value['编号'] },
    })
    ElMessage.success(`已按路线带入 ${res?.['工序数'] || 0} 道工序，保存后生效`)
    routeVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('选工艺路线失败'))
  }
}
const writeOffVisible = ref(false)
const writeOffNo = ref('')
const writeOffRows = ref([])
async function submitWriteOff() {
  const lines = writeOffRows.value
    .filter((r) => Number(r.核销金额) > 0)
    .map((r) => ({ 往来单号: r['往来单号'], 核销金额: Number(r.核销金额) }))
  if (!lines.length) return ElMessage.warning(tt('请填写核销金额'))
  try {
    await engine.callButton({
      panelCode: panelCode.value, buttonName: '核销',
      formData: { 编号: writeOffNo.value, 核销明细: lines }, buttonParam: { code: writeOffNo.value },
    })
    ElMessage.success(tt('核销成功'))
    writeOffVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('核销失败'))
  }
}
const delMode = ref(false)
const delSel = ref([])

// 产成品→材料联动：当前选中产成品（列表页单据流览内点击产成品明细行）
const selectedProduct = ref(null)
const selectedBomCodes = ref([])

// 材料下级 BOM（红 * + 弹窗）：存货编码 → 物料清单 BOM 面板 children 子件行
const subBomMap = ref({})
const subBomVisible = ref(false)
const subBomMaterial = ref(null)
const subBomBom = ref([])

// ---------- 明细维护弹窗（主表双击行打开：在弹窗内维护该单明细，新增/删除/保存） ----------
const maintainVisible = ref(false)
const maintainRow = ref(null)
function openMaintain(row) {
  if (!row || row._placeholder) return
  maintainRow.value = row
  maintainVisible.value = true
}
function onMaintainSaved() {
  load()
}

// ---------- 单据浏览器：翻页切单据 ----------
const curIdx = ref(0)
/** 上一次 load 完成时对应的面板编码：keep-alive 复用时据此判断是否需要重载（见 onActivated） */
const loadedPanelCode = ref('')
const cur = computed(() => {
  const l = list.value
  if (!l.length) return {}
  return l[Math.min(curIdx.value, l.length - 1)]
})
const curNo = computed(() => (list.value.length ? Math.min(curIdx.value, list.value.length - 1) + 1 : 0))

watch(cur, (v) => {
  current.value = v
  detailRefVisible.value = false
  detailRefPick.value = null
  // 切换单据时关闭行选弹窗，避免把上一张单据的来源行追加到当前单据
  invoicePickVisible.value = false
  // 产成品→材料联动：默认不选中（点击产成品明细行才过滤材料明细），切换单据时重置
  if (selectedProduct.value) {
    selectedProduct.value = null
    selectedBomCodes.value = []
  }
})

async function page(delta) {
  const l = list.value
  if (!l.length) return
  const nxt = curIdx.value + delta
  if (nxt >= 0 && nxt < l.length) {
    curIdx.value = nxt
    return
  }
  if (delta > 0 && l.length < total.value) {
    query.pageNo += 1
    await load()
    curIdx.value = 0
    return
  }
  if (delta < 0 && query.pageNo > 1) {
    query.pageNo -= 1
    await load()
    curIdx.value = list.value.length - 1
  }
}

async function pageFirst() {
  if (!list.value.length) return
  if (query.pageNo > 1) {
    query.pageNo = 1
    await load()
  }
  curIdx.value = 0
}

async function pageLast() {
  if (!list.value.length) return
  const lastPage = Math.max(1, Math.ceil(total.value / query.pageSize))
  if (query.pageNo < lastPage) {
    query.pageNo = lastPage
    await load()
  }
  curIdx.value = list.value.length - 1
}

// ══════════ 左侧「单据选择」栏（镜像当前页单据，点行切单据）══════════
// 只在多张单据的单据类面板出现：一张单据没什么可选的，报表面板没有"当前单据"概念，
// BOM 面板已有父件表自己那套分页，都不叠这个栏。
const RAIL_COLLAPSED_KEY = 'mes.doc-rail.collapsed'
const railCollapsed = ref(readRailCollapsed())
function readRailCollapsed() {
  try { return localStorage.getItem(RAIL_COLLAPSED_KEY) === '1' } catch (e) { return false }
}
const showDocRail = computed(() => (
  !reportMode.value
  && !isBomMasterPanel.value
  && cfgCache.value?.metadata?.panelCategory === '单据'
  // 2026-11-05：有单据就显示（原来 total>1 导致只有一张单时侧栏被隐藏，采购单据表现不一致）
  && total.value > 0
))
// 左侧单据栏的列：单号 | 日期 | 面板主要表头字段 | 审核状态（2026-11-04 对齐「采购订单选择」）
// 中间列按面板挑（请购人/供应商/客户/加工单号…），未登记的面板自动取往来单位，见 doc-rail-rows.js
const railColumns = computed(() => railColumnsOf(panelCode.value, list.value))
function toggleRail() {
  railCollapsed.value = !railCollapsed.value
  try { localStorage.setItem(RAIL_COLLAPSED_KEY, railCollapsed.value ? '1' : '0') } catch (e) { /* 隐私模式写不了就算了 */ }
}
/** 点侧栏行：只切当前单据下标，不重新请求（行数据就是主列表那一份） */
function onRailSelect(index) {
  if (index >= 0 && index < list.value.length) curIdx.value = index
}
async function onRailPage(page) {
  const lastPage = Math.max(1, Math.ceil(total.value / query.pageSize))
  const next = Math.min(Math.max(1, Number(page) || 1), lastPage)
  if (next === query.pageNo) return
  query.pageNo = next
  await load()
  curIdx.value = 0
}
async function onRailSearch(keyword) {
  query.keyword = String(keyword || '').trim()
  query.pageNo = 1
  await load()
  curIdx.value = 0
}

// ══════════ 明细区块模型（配置驱动，见 docs/frontend/前端面板设计.md）══════════
// 视图状态：view[blockId + ':tab'] = 当前页签 key；view[blockId + ':' + tabKey + ':view'] = 'detail' | 'summary'
const view = reactive({})
const blocks = computed(() => buildBlocks(cfgCache.value))

// ══════════ 主表预览表格（mainTable 配置，如工艺路线主表；点行切换当前单据，下方明细联动）══════════
const mainGrid = computed(() => {
  const tp = cfgCache.value?.metadata?.panelPageDto?.tablePages?.[0]
  return tp?.mainTable || null
})
const mainCols = computed(() => (mainGrid.value?.columns || []).filter((c) => c !== '序号'))
// 主表固定 5 行（不足补占位，与明细区一致）
const mainRows = computed(() => {
  const l = list.value
  if (!l.length) return []
  const rows = l.slice(0, MIN_ROWS).map((r) => r)
  while (rows.length < MIN_ROWS) rows.push({ _placeholder: true })
  return rows
})
function onMainRowClick(row) {
  const i = list.value.indexOf(row)
  if (i >= 0) curIdx.value = i
}
function mainRowCls({ row }) {
  if (row._placeholder) return 'ph-row'
  return row === cur.value ? 'row-cur' : ''
}

// ══════════ 单据列表模式（配置 listMode=document：列表只显示表头主信息）══════════
function docRowCls({ row }) {
  return row === cur.value ? 'row-cur' : ''
}
function onDocRowClick(row) {
  const i = list.value.indexOf(row)
  if (i >= 0) curIdx.value = i
}
/** 新增（无单号）或双击行（有单号）都进入 PanelxForm 单据详情页。 */
function openDocForm(row) {
  const no = row && (row['编号'] || row['单据编号'] || row['锭号'])
  const path = `/panelx/form/${panelCode.value}`
  const query = no ? { code: String(no) } : {}
  tabs.open({ path, title: `${panelName.value}-${no || '新增'}`, query })
  router.push({ path, query })
}
async function docPage(pageNo) {
  const target = Math.max(1, Math.min(Number(pageNo) || 1, reportPageCount.value))
  if (target === query.pageNo) return
  query.pageNo = target
  await load()
  curIdx.value = 0
}

function buildBlocks(cfg) {
  if (!cfg) return []
  const tp = cfg.metadata?.panelPageDto?.tablePages?.[0]
  const gt = tp?.gridTabs || []
  const tabs = cfg.detail?.tabs || []
  const out = []
  const mkTab = (key, label, cols, summaryItems, sumLabel, hasSummary) => ({ key, label, cols, summaryItems, sumLabel, hasSummary })
  // A 区：优先 gridTabs[0]，其次 detail.tabs[0]（页签 = 明细 + 汇总）
  const first = tabs[0]
  const mainCols = gt[0]?.columns || (first ? (first.fields || []).filter((f) => !f.hidden).map((f) => f.dataName) : [])
  if (mainCols.length) {
    const sumItems = first?.summaryItems || []
    const label = gt[0]?.label || first?.label || '明细'
    const hasSummary = !!(gt.length > 1 && gt[1]?.summary) || sumItems.length > 0
    out.push({
      id: 'A', isMain: true,
      tabs: [mkTab(first?.key || 'items', label, mainCols, sumItems, gt[1]?.label || (sumItems.length ? label + '汇总' : ''), hasSummary)],
    })
  }
  // B 区：detail.tabs[1..n] 合并为一个区块、页签内切换（同 T+：材料明细/工序明细共区块）
  // 带 block:"B" 的页签即使排在第一位也放进 B 区：表头单据没有明细行、只额外多一个网格时
  //（如收款单的「核销明细」），detail.tabs[0] 会被 A 区当成字段 schema 吃掉，页签就永远不出现。
  const rest = tabs.filter((t, i) => i > 0 || t.block === 'B').map((t) => {
    const cols = (t.fields || []).filter((f) => !f.hidden).map((f) => f.dataName)
    return cols.length ? mkTab(t.key, t.label, cols, t.summaryItems || [], t.summaryItems?.length ? t.label + '汇总' : '', !!(t.summaryItems?.length)) : null
  }).filter(Boolean)
  if (rest.length) out.push({ id: 'B', isMain: false, tabs: rest })
  return out
}

function activeTab(b) {
  const k = view[b.id + ':tab']
  return b.tabs.find((t) => t.key === k) || b.tabs[0]
}

function tabView(b, t) {
  return view[b.id + ':' + t.key + ':view'] === 'summary' ? 'summary' : 'detail'
}

// 页签头条目：明细页签 + 汇总页签 依次展开
function headItems(b) {
  const out = []
  for (const t of b.tabs) {
    out.push({ kind: 'tab', key: t.key, label: t.label })
    if (t.hasSummary) out.push({ kind: 'sum', key: t.key, label: t.sumLabel })
  }
  return out
}

function isOn(b, item) {
  const cur = activeTab(b)
  if (cur.key !== item.key) return false
  return item.kind === 'sum' ? tabView(b, cur) === 'summary' : tabView(b, cur) !== 'summary'
}

function switchTab(b, item) {
  view[b.id + ':tab'] = item.key
  view[b.id + ':' + item.key + ':view'] = item.kind === 'sum' ? 'summary' : 'detail'
  // 切换明细/汇总页签后回到第 1 页，避免停在上一页签的页码上看到空表
  view[b.id + ':page'] = 1
}

// 明细数据：单据类取 cur.detail[block.key]；平铺类（档案/报表）把当前行当明细
function detailRows(b) {
  const d = cur.value.detail
  if (d && Array.isArray(d[b.key])) return d[b.key]
  if ((b.cols || []).some((c) => cur.value[c] !== undefined)) return [cur.value]
  return []
}

const KNOWN_NUM = ['数量', '实收数量', '报工数量', '合格数量', '不合格数量', '工价', '计时/计件金额', '金额', '含税金额', '含税单价', '单价', '税额', '现存量', '需用数量', '损耗数量', '计划数量', '累计领用数量', '齐套数量(主)', '累计汇报套数(工序单位)', '总重', '单重', '委外金额', '委外税额', '委外含税金额', '换算率', '可报工数量', '累计汇报数量']

function numericCols(rows, b) {
  const fromItems = (b.summaryItems || []).map((it) => it.field)
  const known = (b.cols || []).filter((c) => KNOWN_NUM.includes(c) && rows.every((r) => Number.isFinite(Number(r[c]))))
  return [...new Set([...fromItems, ...known])].filter((c) => (b.cols || []).includes(c))
}

function groupKeyOf(b) {
  return b.keyField || ['存货编码', '产品编码', '材料编码', '存货名称', '产品名称', '材料名称'].find((k) => (b.cols || []).includes(k)) || (b.cols || [])[0] || '编号'
}

// 汇总：按 编码/名称 分组 + 合计行（对齐 T+ 汇总页签）
function summaryRows(rows, b) {
  if (!rows.length) return []
  const keyField = groupKeyOf(b)
  const numeric = numericCols(rows, b)
  const group = new Map()
  for (const r of rows) {
    const k = r[keyField] || '(空)'
    if (!group.has(k)) {
      // 先剔除汇总字段再复制首行，避免分组行把首行原值又累加一次（翻倍 bug）
      const base = { ...r }
      for (const c of numeric) delete base[c]
      group.set(k, base)
    }
    const g = group.get(k)
    for (const c of numeric) g[c] = (g[c] || 0) + num(r[c])
  }
  const out = [...group.values()]
  const total = {}
  for (const c of numeric) total[c] = Math.round(rows.reduce((a, r) => a + num(r[c]), 0) * 100) / 100
  out.push({ [keyField]: '合计', ...total })
  return out
}

// 每页行数（10/20/50，用户可选）：表格高度跟每页行数走，超出一页翻页；不足补空占位行保持高度稳定
// 计算逻辑在 core/views/list-grid-paging.js（纯函数，有单测），见 docs/frontend/前端面板设计.md
const MIN_ROWS = DEFAULT_PAGE_SIZE

function blockData(b) {
  const t = activeTab(b)
  let rows = detailRows(t)
  // 产成品→材料联动过滤：点产成品行后，材料明细只显示该产品的 BOM 子件（子件BOM 优先，材料编码兜底）
  if (t.key === 'materials' && selectedProduct.value) {
    const byBom = rows.filter((m) => m['子件BOM'] === selectedProduct.value)
    if (byBom.length) rows = byBom
    else {
      const byCode = rows.filter((m) => selectedBomCodes.value.includes(m['材料编码']))
      if (byCode.length) rows = byCode
    }
  }
  return tabView(b, t) === 'summary' ? summaryRows(rows, t) : rows
}

// ---------- 列头筛选（明细表格 / 主表预览共用，纯内存过滤，不写库） ----------
// 语法与匹配规则在 core/views/column-filter.js（有单测）：普通文本=包含，= != > >= < <= 走比较。
// 合计按**筛选后**的行算 —— 筛出「铝棒」时合计就应该是铝棒那一批，否则数字对不上眼。
const colFilters = reactive({})
function filterKeyOf(b, prop) {
  return `${b.id}:${activeTab(b).key}:${prop}`
}
function filterOf(b, prop) {
  return colFilters[filterKeyOf(b, prop)] || ''
}
function setFilter(b, prop, value) {
  const key = filterKeyOf(b, prop)
  if (parseColumnFilter(value)) colFilters[key] = value
  else delete colFilters[key]
  view[b.id + ':page'] = 1
}
function filtersOfBlock(b) {
  const out = {}
  for (const prop of activeTab(b).cols || []) {
    const value = filterOf(b, prop)
    if (parseColumnFilter(value)) out[prop] = value
  }
  return out
}
function filterCountOf(b) {
  return activeFilterCount(filtersOfBlock(b))
}
function hasFilter(b, prop) {
  return !!parseColumnFilter(filterOf(b, prop))
}
function clearBlockFilters(b) {
  for (const prop of activeTab(b).cols || []) delete colFilters[filterKeyOf(b, prop)]
  view[b.id + ':page'] = 1
}
const mainFilters = reactive({})
function setMainFilter(prop, value) {
  if (parseColumnFilter(value)) mainFilters[prop] = value
  else delete mainFilters[prop]
}
function mainFilteredRows() {
  return applyColumnFilters(mainRows.value, mainFilters, mainCols.value)
}

// ---------- 参照带出列（存货名称.品牌 / 部门.负责人） ----------
// 列名写成「参照字段.属性」时，行里没这个键（选单/导入/生单/历史数据都没有）就按参照值去
// 档案里查属性显示，不再要求必须经过参照选择。索引由引擎取一次并缓存，规则见 ref-carry.js。
const carryPlans = ref({})
const carryPending = ref(false)

/** 收集本面板所有「X.Y」列（明细各页签字段/列表列 + 主表 + 表头），逐个建带出方案 */
async function loadCarryPlans() {
  if (typeof engine.archiveIndex !== 'function') return
  const props = new Set()
  const push = (name) => { if (splitDotted(name)) props.add(String(name)) }
  for (const tab of cfgCache.value?.detail?.tabs || []) {
    for (const field of tab.fields || []) push(field?.dataName)
  }
  for (const tab of gridTabs.value || []) {
    for (const col of tab.columns || []) push(columnNameOf(col))
  }
  for (const col of mainCols.value) push(col)
  for (const field of headerFields.value) push(headerFieldKey(field))
  if (!props.size) {
    carryPlans.value = {}
    return
  }
  carryPending.value = true
  try {
    const plans = {}
    for (const prop of props) {
      const { source, field } = splitDotted(prop)
      const def = fieldDefOf(source)
      if (!def || def.dataType !== '参照') continue
      const refPanel = def.refPanel || def.ref?.panel
      const refField = def.refField || def.ref?.field || def.ref?.refField
      if (!refPanel || !refField) continue
      const index = await engine.archiveIndex(refPanel, refField)
      if (!index || !index.size) continue
      plans[prop] = { sourceFields: [...new Set([source, refField])], field, index }
    }
    carryPlans.value = plans
  } catch (e) {
    carryPlans.value = {}
  } finally {
    carryPending.value = false
  }
}

/** 单元格显示值：带出列按档案解析，普通列取行内值 */
function cellText(row, prop) {
  const plan = carryPlans.value[prop]
  if (plan) return carriedValue(row, prop, plan)
  const value = row?.[prop]
  return value === undefined || value === null ? '' : value
}

/** 带出列是「档案属性的镜像」，不提供行内编辑（要改请到档案里改） */
function isCarriedCol(prop) {
  return !!carryPlans.value[prop]
}

// ---------- 明细区翻页：每页行数可选（10/20/50），表格高度跟行数走，合计按整块全部行算 ----------
function blockAllRows(b) {
  return applyColumnFilters(blockData(b), filtersOfBlock(b), activeTab(b).cols || [])
}

function blockSize(b) {
  return normalizePageSize(view[b.id + ':size'])
}

function blockPage(b) {
  return clampPage(view[b.id + ':page'] || 1, blockAllRows(b).length, blockSize(b))
}

function blockPageCount(b) {
  return pageCount(blockAllRows(b).length, blockSize(b))
}

function setBlockSize(b, size) {
  view[b.id + ':size'] = normalizePageSize(size)
  view[b.id + ':page'] = 1
}

function stepBlockPage(b, delta) {
  const next = clampPage(blockPage(b) + delta, blockAllRows(b).length, blockSize(b))
  view[b.id + ':page'] = next
}

function blockRows(b) {
  const size = blockSize(b)
  const rows = pageSlice(blockAllRows(b), blockPage(b), size)
  // 占位只补到 10 行（每页 20/50 而数据不足时不补空白行），表格不会被撑到把翻页条挤出屏幕
  return padRows(rows, Math.min(size, MIN_ROWS))
}

function tableH(b) {
  const hasFooter = tabView(b, activeTab(b)) !== 'summary'
  return gridHeight(blockSize(b), hasFooter)
}

function blockCols(b) {
  const t = activeTab(b)
  const rows = blockAllRows(b)
  return (t.cols || []).map((c) => {
    const f = fieldDefOf(c)
    return {
      prop: c,
      label: tt(columnAliases.value[c] || columnLabel(c)),
      field: f,
      width: colW(f, c, rows),
      align: f.dataType === '小数' || f.dataType === '整数' ? 'right' : 'left',
      carried: isCarriedCol(c),
    }
  })
}

/** 表头显示名：参照带出列 `存货名称.品牌` 显示为「品牌」（点号只是取值路径，不是列名） */
function columnLabel(prop) {
  const dotted = splitDotted(prop)
  return dotted ? dotted.field : prop
}

const showFooter = computed(() => {
  if (documentListMode.value) return false
  const cfg = cfgCache.value
  return cfg?.metadata?.panelCategory === '单据' || (cfg?.detail?.tabs || []).length > 0
})

function num(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

function sumMethod({ columns, data }) {
  // 库存台账这类混单位的报表只合计金额，列由 gridTabs[0].summaryColumns 声明
  return computeSums(columns, data, fieldDefOf, summaryColumnsOf(gridTabs.value[0]))
}

// 明细区块的合计：按「整块全部行」算，翻页后合计不跟着变成单页小计
function sumMethodOf(b, { columns }) {
  return computeSums(columns, blockAllRows(b), fieldDefOf, null)
}

// 审批流：当前单据已审批（直接审核/审批流通用，见 core/doc-status）→ 明细块左上角「已审批」印章；已审批行浅绿底色
const isApproved = computed(() => isDocApproved(cur.value))

function rowCls({ row }, b) {
  if (row._placeholder) return 'ph-row'
  if (b && b.id === 'A' && row['产品编码'] && row['产品编码'] === selectedProduct.value) return 'prod-selected'
  if (isDocApproved(row)) return 'row-approved'
  return ['产品编码', '材料编码', '存货编码', '存货名称', '产品名称', '材料名称'].some((k) => row[k] === '合计') ? 'sum-row' : ''
}

// ---------- 明细表格列宽（按字段类型推算，横向滚动同 T+） ----------
/** 列名提取：列配置兼容字符串与对象（dataName/name/label）两种形态 */
function columnNameOf(col) {
  if (typeof col === 'string') return col
  const name = col?.dataName ?? col?.name ?? col?.label
  return name === undefined || name === null ? '' : String(name)
}

function colW(f, prop, rows) {
  const t = f.dataType || '文本'
  if (t === '是否') return 74
  if (t === '图片') return 56
  if (t === '小数' || t === '整数') return 104
  if (t === '日期' || t === '日期时间') return 136
  const n = f.label || f.dataName || ''
  // 编号/单号类列要能看全单号（INIT-20261106-001 这种），不能按标签长度算成 96px 截断
  if (/编号|单号/.test(n)) return Math.min(Math.max(n.length * 16 + 30, 150), 220)
  return n.length <= 2 ? 96 : Math.min(Math.max(n.length * 16 + 30, 96), 220)
  if (!prop) {
    const n = f.label || f.dataName || ''
    return n.length <= 2 ? 96 : Math.min(Math.max(n.length * 16 + 30, 96), 220)
  }
  let max = (f.label || f.dataName || '').length
  for (const row of rows || []) {
    const value = row && row[prop]
    if (value !== undefined && value !== null) max = Math.max(max, String(value).length)
  }
  return Math.min(Math.max(max * 17 + 40, 110), 260)
}

// ---------- 明细右键/工具条操作（2026-11-03：只保留已实现的动作，未实现的不占位） ----------
// 工具条只放真能执行的：复制当前区块到剪贴板、导出当前区块 CSV；
// 「现存量提取」只在区块确实有「现存量」列时出现（否则按库存状况表回填 0 行，等于空动作），主表/明细同口径。
const TOOL_ICONS = ['复制到剪贴板', '另存为EXCEL模板']
function blockHasStockColumn(b) {
  return blockCols(b).some((c) => c.prop === '现存量')
}
function blockIcons(b) {
  return blockHasStockColumn(b) ? ['现存量提取', ...TOOL_ICONS] : TOOL_ICONS
}
const ctxItems = ['定位', '复制到剪贴板', '另存为EXCEL模板']

const ctxBlock = ref(null)
const ctx = reactive({ visible: false, x: 0, y: 0, row: null })

const activeCols = computed(() => (ctxBlock.value ? blockCols(ctxBlock.value) : []))
const activeData = computed(() => (ctxBlock.value ? blockData(ctxBlock.value) : []))

function onCtx(ev, row, b) {
  ev.preventDefault()
  ev.stopPropagation()
  ctxBlock.value = b
  ctx.row = row
  ctx.x = ev.clientX
  ctx.y = ev.clientY
  ctx.visible = true
}

function closeCtx() {
  ctx.visible = false
  openGroup.value = -1
}

// ---------- 审批按钮权限（提交审批/审批情况公开；审批通过/驳回需角色审批权限） ----------
const APPROVE_ACTIONS = ['审批通过', '审批驳回']
function filterGroups(raw) {
  const canApprove = user.isAdmin || user.approvePanels.includes(panelCode.value)
  if (canApprove) return raw
  return (raw || [])
    .map((g) => ({ ...g, actions: (g.actions || g.items || []).filter((a) => !APPROVE_ACTIONS.includes(a)) }))
    .filter((g) => (g.actions || []).length > 0)
}

// ---------- 工具栏分组（配置 {name, actions}：主按钮=第一个 action，actions>1 显示 ▼ 下拉） ----------
const openGroup = ref(-1)
function actsOf(g) {
  return g.actions || g.items || []
}
// 主按钮：审批组跟着单据状态走（草稿→提交审批 / 审批中→审批通过 / 已审核→弃审），
// 其它组仍是第一个动作（禁用即灰、点击不执行）。
function btnName(g) {
  return primaryActionOf(g, isDisabled) || g.name
}
/** 按钮文字：审批组直接写当前能做的动作，不再永远显示组名「审批」 */
function groupLabel(g) {
  if (isApprovalGroup(g)) return tt(btnName(g))
  const actions = actsOf(g)
  // 行级选来源单单独成组时直接显示动作名（如「关联」组只有「选入库单行」）
  if (actions.length === 1 && actions[0] === INVOICE_LINE_ACTION) return tt(INVOICE_LINE_ACTION)
  return tt(g.name)
}
// 下拉项 = 除主按钮外的其余动作；审批组只列当前真能点的（不摆灰项）
function dropItems(g) {
  const primary = btnName(g)
  const rest = actsOf(g).filter((a) => a !== primary)
  if (!isApprovalGroup(g)) return rest
  const secondary = approvalSecondaryOf(g, primary, isDisabled)
  return rest.filter((a) => !isDisabled(a) && !secondary.includes(a))
}
/** 与主按钮并列露出的动作（审批中的「审批驳回」） */
function secondaryOf(g) {
  return approvalSecondaryOf(g, btnName(g), isDisabled)
}
function toggleGroup(gi) {
  openGroup.value = openGroup.value === gi ? -1 : gi
}
/**
 * 多来源组（生单/选单）主按钮只展开菜单：
 * 组内没有与组名同名的动作，说明主按钮没有无歧义的默认动作（否则点「生单」会直接执行组内第一个来源）。
 */
function menuOnlyGroup(g) {
  if (isApprovalGroup(g)) return false
  if ((g.actions || []).includes(g.name)) return false
  return dropItems(g).length > 0
}
/** 主按钮点击：审批组等按状态取动作执行；收纳组/多来源组只展开菜单 */
function onGroupMain(gi, g) {
  if (isCatchAllGroup(g) || menuOnlyGroup(g)) {
    toggleGroup(gi)
    return
  }
  openGroup.value = -1
  onButton(btnName(g))
}
function onGroupAction(a) {
  openGroup.value = -1
  // 2026-08-25：灰按钮（如草稿态「生成XX」）点击不执行、不弹提示
  if (isDisabled(a)) return
  onButton(a)
}

async function copyActive() {
  const cols = activeCols.value
  const rows = (activeData.value || []).filter((r) => !r._placeholder)
  const text = cols.map((c) => c.label).join('\t') + '\n' + rows.map((r) => cols.map((c) => r[c.prop] ?? '').join('\t')).join('\n')
  try {
    await navigator.clipboard.writeText(text)
  } catch (e) {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    ta.remove()
  }
  ElMessage.success(tt('已复制') + ` ${rows.length} ` + tt('行到剪贴板'))
}

/**
 * 「另存为EXCEL模板」：把当前区块导出成真正的 .xlsx（SheetJS 按需加载，不进首屏 chunk）。
 * 数值列写成数字单元格（Excel 里天然右对齐、可继续求和），日期保持 yyyy-MM-dd 文本。
 */
async function exportActive() {
  const cols = activeCols.value
  const rows = (activeData.value || []).filter((r) => !r._placeholder)
  const cellOf = (c, raw) => {
    if (raw === undefined || raw === null || raw === '') return ''
    if (c.align === 'right') {
      const n = Number(String(raw).replace(/,/g, ''))
      return Number.isFinite(n) ? n : raw
    }
    return raw
  }
  const aoa = [cols.map((c) => c.label), ...rows.map((r) => cols.map((c) => cellOf(c, r[c.prop])))]
  const fileName = `${panelCode.value}-${ctxBlock.value?.id || 'list'}.xlsx`
  try {
    const XLSX = await import('xlsx')
    const ws = XLSX.utils.aoa_to_sheet(aoa)
    ws['!cols'] = cols.map((c) => ({ wch: Math.min(Math.max(String(c.label).length * 2 + 6, 10), 42) }))
    const rawName = String(activeTab(ctxBlock.value)?.label || '明细')
    const sheetName = rawName.replace(/[[\]*?/\\:]/g, ' ').trim().slice(0, 31) || 'Sheet1'
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, sheetName)
    const buf = XLSX.write(wb, { bookType: 'xlsx', type: 'array', compression: true })
    const blob = new Blob([buf], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = fileName
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success(tt('已导出') + ' ' + fileName)
  } catch (error) {
    ElMessage.error(tt('导出失败') + '：' + (error?.message || tt('未知错误')))
  }
}

async function onIcon(it, b) {
  ctxBlock.value = b
  if (it === '复制到剪贴板') {
    copyActive()
    return
  }
  if (it === '另存为EXCEL模板') {
    exportActive()
    return
  }
  if (it === '现存量提取') {
    try {
      const count = await engine.fillCurrentStock(blockData(b))
      ElMessage.success(tt('已按库存状况表刷新') + ` ${count} ` + tt('行现存量'))
    } catch (error) {
      ElMessage.error(engine.errMsg(error) || tt('现存量提取失败'))
    }
  }
}

async function onCtxItem(it) {
  const row = ctx.row
  ctx.visible = false
  if (!row) return
  if (it === '定位') {
    ElMessage.success(tt('已定位') + '：' + (row['产品编码'] || row['材料编码'] || row['存货编码'] || row['工序编码'] || row['编号'] || ''))
    return
  }
  if (it === '复制到剪贴板') {
    copyActive()
    return
  }
  if (it === '另存为EXCEL模板') {
    exportActive()
  }
}

// ---------- 查询区 ----------
function fieldDefOf(col) {
  const cfg = cfgCache.value
  const r = (cfg?.dataSchema?.fields || []).find((x) => x.dataName === col)
  if (r) return r
  for (const tab of cfg?.detail?.tabs || []) {
    const dr = (tab.fields || []).find((x) => x.dataName === col)
    if (dr) return dr
  }
  return { dataName: col, dataType: '文本', options: [] }
}

function headerFieldKey(field) {
  return field.code || field.dataName
}

function headerFieldLabel(field) {
  return field.name || field.label || field.displayName || field.dataName || field.code
}

function fieldType(field) {
  return field?.dataType || '文本'
}

function isReferenceField(field) {
  return fieldType(field) === '参照' && !!(field?.refPanel || field?.ref?.panel)
}

function isSelectField(field) {
  return fieldType(field) === '下拉框'
}

function isDateField(field) {
  return ['日期', '日期时间', '时间', 'DATE', 'DateTime', 'Date'].includes(fieldType(field))
}

function isNumberField(field) {
  return ['小数', '整数', 'Decimal', 'Long', 'Integer', 'Double'].includes(fieldType(field))
}

function isBooleanField(field) {
  return ['是否', 'Boolean', 'BOOL'].includes(fieldType(field))
}

function fieldOptions(field) {
  return (field?.options || engine.fieldOptions(field || {}) || []).map((option) => (
    typeof option === 'object'
      ? { value: option.value ?? option.label, label: option.label ?? option.value }
      : { value: option, label: option }
  ))
}

function formatFieldValue(field, value) {
  if (value === undefined || value === null || value === '') return ''
  if (isBooleanField(field)) return value ? '是' : '否'
  return String(value)
}

function headerFieldLocked(field) {
  const key = headerFieldKey(field)
  // readonly：派生展示字段（如 BOM 的「工艺路线名称」由编码决定），不允许手改
  return !!field.computed || !!field.readonly || !!field.autoCode
    || ['编号', '单据状态', '创建时间', '更新时间', '发起人编号'].includes(key)
    || priceLocked(key)
}

/** price 权限未授权时价格/金额类字段只读（表头与明细同口径） */
function priceLocked(fieldName) {
  return priceFieldLocked(fieldName, panelCode.value, user)
}

function headerRefText(field) {
  return formatFieldValue(field, cur.value[headerFieldKey(field)])
}

function openHeaderRef(field) {
  if (!draftEditable.value || headerFieldLocked(field)) return
  headerRefField.value = field
  // 工艺路线这类「要看工序流程」的字段走专用弹窗（panel_config 里声明 refDialog: route-flow）
  if ((field.refDialog || field.ref?.dialog) === 'route-flow') {
    routeFlowVisible.value = true
    return
  }
  headerRefVisible.value = true
}

function onHeaderRefConfirm(rows) {
  const field = headerRefField.value
  const source = rows?.[0]
  if (!field || !source || !draftEditable.value) return
  const key = headerFieldKey(field)
  const ref = field.ref && typeof field.ref === 'object' ? field.ref : field
  const refField = ref.field || ref.refField || key
  cur.value[key] = source[refField] ?? ''
  for (const map of ref.map || ref.refMap || []) {
    if (map && source[map.from] !== undefined) cur.value[map.to || map.from] = source[map.from]
  }
  headerRefVisible.value = false
  headerRefField.value = null
}

function detailTabDefOf(key) {
  return (cfgCache.value?.detail?.tabs || []).find((tab) => tab.key === key) || null
}

function detailEditable(b) {
  return draftEditable.value && !!b && tabView(b, activeTab(b)) !== 'summary'
}

function detailRefTrigger(field) {
  return field?.refTrigger || field?.trigger || 'click'
}

function isActiveDetailRefRow(row, b, prop) {
  const pick = detailRefPick.value
  return detailRefVisible.value && !!pick && pick.row === row && pick.tabKey === activeTab(b).key && pick.field?.dataName === prop
}

function openDetailReference(field, row, b) {
  if (!detailEditable(b) || !isReferenceField(field) || field.computed || row?._placeholder) return
  detailRefPick.value = {
    field,
    row,
    tabKey: activeTab(b).key,
    documentNo: cur.value['编号'],
    created: false,
  }
  detailRefVisible.value = true
}

function openClickDetailRef(field, row, b) {
  if (detailRefTrigger(field) === 'click') openDetailReference(field, row, b)
}

function onDetailCellDblclick(row, column, event, b) {
  const field = fieldDefOf(column?.property)
  // 明细行双击**只**服务「参照触发=双击」的字段（如存货：双击弹参照选择器）；其余情况什么都不做。
  // 2026-11-04（对齐 yinjia 2026-10-15 用户口径）：原实现在非草稿态还会 openForm(cur.value) ——
  //   即双击已审核单据的明细行会弹出整单卡片（VoucherFormDialog），所有单据面板都一样；
  //   用户明确要求双击不要再弹，卡片与工具栏「修改」入口一并删除（见 toolbarGroups 过滤）。
  if (detailEditable(b) && isReferenceField(field) && detailRefTrigger(field) === 'dblclick') {
    event?.stopPropagation?.()
    if (!row?._placeholder) openDetailReference(field, row, b)
  }
}

function newDetailRow(tabKey) {
  const row = {}
  for (const field of detailTabDefOf(tabKey)?.fields || []) {
    if (field.dataType === '小数' || field.dataType === '整数') row[field.dataName] = field.defaultValue ?? 0
    else if (field.dataType === '是否') row[field.dataName] = field.defaultValue ?? false
    else row[field.dataName] = field.defaultValue ?? ''
  }
  return row
}

function addInlineDetailRow(b) {
  if (!detailEditable(b)) return
  const tabKey = activeTab(b).key
  if (!cur.value.detail) cur.value.detail = {}
  const rows = cur.value.detail[tabKey] || (cur.value.detail[tabKey] = [])
  rows.push(newDetailRow(tabKey))
  // 新行追加在末尾：翻到最后一页，否则用户看不到刚加的行
  view[b.id + ':page'] = pageCount(blockAllRows(b).length, blockSize(b))
}

function primaryDetailRefField(b) {
  if (!detailEditable(b)) return null
  const columns = new Set(activeTab(b).cols || [])
  const fields = (detailTabDefOf(activeTab(b).key)?.fields || []).filter((field) => (
    columns.has(field.dataName) && isReferenceField(field) && !field.computed
  ))
  return fields.find((field) => ['产品编码', '存货编码', '材料编码'].includes(field.dataName)) || fields[0] || null
}

function openBlankDetailRow(b) {
  const field = primaryDetailRefField(b)
  if (!field) return
  const tabKey = activeTab(b).key
  if (!cur.value.detail) cur.value.detail = {}
  const rows = cur.value.detail[tabKey] || (cur.value.detail[tabKey] = [])
  const row = newDetailRow(tabKey)
  rows.push(row)
  detailRefPick.value = {
    field,
    row,
    tabKey,
    documentNo: cur.value['编号'],
    created: true,
  }
  detailRefVisible.value = true
}

function discardCreatedDetailRefRow(pick) {
  if (!pick?.created) return
  const rows = cur.value.detail?.[pick.tabKey]
  if (!Array.isArray(rows)) return
  const index = rows.indexOf(pick.row)
  if (index >= 0) rows.splice(index, 1)
}

async function onInlineDetailChange(tabKey, row, field) {
  calculateDetailRow(tabKey, row)
  syncInspectionUnqualified(tabKey, row)
  if (['存货编码', '存货名称', '产品编码', '产品名称', '材料编码', '材料名称', '仓库', '预出仓库', '出库仓库'].includes(field?.dataName)) {
    try { await engine.fillCurrentStock(row) } catch (error) { ElMessage.error(engine.errMsg(error) || tt('现存量刷新失败')) }
  }
}

/**
 * 检验单联动：上方检验行标记不合格后，自动在「不合格处理」页签维护关联行
 * （关联明细行号/存货/不合格原因/处理数量），恢复合格时移除该行处置。
 */
function syncInspectionUnqualified(tabKey, row) {
  const detailTabs = cfgCache.value?.detail?.tabs || []
  if (!hasUnqualifiedTab(detailTabs)) return
  const mainTabKey = detailTabs[0]?.key || 'items'
  if (tabKey !== mainTabKey) return
  const detail = cur.value?.detail
  const rows = detail?.[mainTabKey]
  if (!Array.isArray(rows)) return
  const lineNo = rows.indexOf(row) + 1
  if (lineNo <= 0) return
  syncUnqualifiedRow(detail, lineNo, mainTabKey)
}

function applyDetailReference(target, field, source) {
  const refField = field.refField || field.field
  target[field.dataName] = source[refField]
  for (const map of field.refMap || field.map || []) {
    if (map && source[map.from] !== undefined) target[map.to || map.from] = source[map.from]
  }
}

function calculateDetailRow(tabKey, row) {
  const tab = detailTabDefOf(tabKey)
  if (!tab?.calc?.length) return
  const numeric = (value) => Number.isFinite(Number(value)) ? Number(value) : 0
  for (const rule of tab.calc) {
    let expression = String(rule.formula || '')
    const names = [...new Set(expression.match(/[^\s+\-*/()]+/g) || [])]
      .filter((name) => !/^\d+(?:\.\d+)?$/.test(name))
      .sort((a, b) => b.length - a.length)
    for (const name of names) expression = expression.split(name).join(String(numeric(row[name])))
    if (!/^[\d.\s+\-*/()]+$/.test(expression)) continue
    let value
    try { value = Function(`"use strict"; return (${expression})`)() } catch (error) { value = 0 }
    if (!Number.isFinite(value)) value = 0
    if (rule.round != null) value = engine.roundDecimal(value, rule.round)
    row[rule.target] = value
  }
}

function currentFormData(detail) {
  const head = { ...cur.value }
  delete head.detail
  delete head['编号']
  delete head['单据状态']
  delete head['创建时间']
  delete head['更新时间']
  delete head['发起人编号']
  return { ...head, 编号: cur.value['编号'], detail }
}

function emptyFieldValue(value) {
  return value === undefined || value === null || String(value).trim() === ''
}

function validateInlineDraft() {
  if (panelCode.value === 'BOM' && bomMasterRef.value) {
    const validation = bomMasterRef.value.validate()
    if (validation) return validation
  }
  for (const field of headerFields.value) {
    if (field.isRequired && emptyFieldValue(cur.value[headerFieldKey(field)])) {
      return `${headerFieldLabel(field)}不能为空`
    }
  }
  // 只校验列表上真正渲染出来的列：字段可能在 panel_config 里标了必填，但没进 gridTabs.columns（或 hidden），
  // 用户看不到也就无从填写，不能因此卡住保存（如到货单「单价」只在配置里有、列表列里没有）。
  const visibleFields = new Set((blocks.value || []).flatMap((b) => b.tabs.flatMap((t) => t.cols || [])))
  for (const tab of cfgCache.value?.detail?.tabs || []) {
    const rows = cur.value.detail?.[tab.key] || []
    if (tab.isRequired && !rows.length) return `请至少添加一行${tab.label || '明细'}`
    for (let index = 0; index < rows.length; index++) {
      for (const field of tab.fields || []) {
        if (!field.isRequired || field.hidden || !visibleFields.has(field.dataName)) continue
        if (emptyFieldValue(rows[index][field.dataName])) {
          return `${tab.label || '明细'}第 ${index + 1} 行${field.dataName}不能为空`
        }
      }
    }
  }
  return ''
}

async function saveInlineDraft(buttonName = '保存', { silent = false } = {}) {
  if (!draftEditable.value || inlineSaving.value) return false
  const validation = validateInlineDraft()
  if (validation) {
    ElMessage.warning(validation)
    return false
  }
  for (const tab of cfgCache.value?.detail?.tabs || []) {
    for (const row of cur.value.detail?.[tab.key] || []) calculateDetailRow(tab.key, row)
  }
  inlineSaving.value = true
  const documentNo = cur.value['编号']
  try {
    await engine.callButton({
      panelCode: panelCode.value,
      buttonName,
      formData: currentFormData({ ...(cur.value.detail || {}) }),
      buttonParam: {},
    })
    await load()
    const index = list.value.findIndex((item) => item['编号'] === documentNo)
    if (index >= 0) curIdx.value = index
    if (!silent) ElMessage.success('「' + tt(buttonName) + '」' + tt('成功'))
    return true
  } catch (error) {
    ElMessage.error(engine.errMsg(error) || tt('保存失败'))
    return false
  } finally {
    inlineSaving.value = false
  }
}

/** BOM 展开：产品明细行带出材料明细（从 BOM 面板 children 按父件编码取子件，对齐表单页 loadBomFor） */
async function expandBomMaterials(detail, productRows) {
  const matTab = (cfgCache.value?.detail?.tabs || []).find((t) => t.key === 'materials')
  if (!matTab) return
  try {
    const res = await engine.queryFormDataList({ panelCode: 'BOM', condition: {}, pageNo: 1, pageSize: 100 })
    const bom = []
    for (const d of res.list || []) {
      for (const it of (d.detail && d.detail.children) || []) {
        const parent = String(it['父件编码'] || '')
        if (!parent || !productRows.some((r) => String(r['产品编码'] || '') === parent)) continue
        bom.push({
          材料编码: it['子件编码'],
          材料名称: it['子件名称'],
          规格型号: it['规格型号'] || '',
          计量单位: it['子件计量单位'] || '件',
          定额需用数量: it['定额数量'] ?? 0,
          '损耗率%': it['损耗率%'] ?? 0,
          parent,
        })
      }
    }
    if (!bom.length) return
    const mats = detail.materials || (detail.materials = [])
    const existing = new Set(mats.map((m) => m['材料编码'] + ':' + m['子件BOM']))
    for (const b of bom) {
      const key = b['材料编码'] + ':' + b.parent
      if (existing.has(key)) continue
      const row = newDetailRow('materials')
      row['材料编码'] = b['材料编码']
      row['材料名称'] = b['材料名称']
      row['规格型号'] = b['规格型号']
      row['计量单位'] = b['计量单位']
      row['定额需用数量'] = b['定额需用数量']
      row['损耗率%'] = b['损耗率%']
      row['子件BOM'] = b.parent
      mats.push(row)
      existing.add(key)
    }
  } catch (e) {
    // BOM 查询失败不阻塞参照导入
  }
}

async function onDetailRefConfirm(selectedRows) {
  const pick = detailRefPick.value
  if (!pick || !selectedRows?.length || detailRefSaving.value) return
  if (cur.value['编号'] !== pick.documentNo || cur.value['单据状态'] !== '草稿') {
    detailRefVisible.value = false
    ElMessage.warning(tt('当前单据已切换或不再是草稿，请重新选择'))
    return
  }

  const detail = {}
  for (const [key, value] of Object.entries(cur.value.detail || {})) {
    detail[key] = Array.isArray(value) ? value.map((row) => ({ ...row })) : value
  }
  const sourceRows = cur.value.detail?.[pick.tabKey] || []
  const targetRows = detail[pick.tabKey] || (detail[pick.tabKey] = [])
  const targetIndex = pick.row ? sourceRows.indexOf(pick.row) : -1
  const changedRows = []
  let offset = 0
  if (targetIndex >= 0) {
    applyDetailReference(targetRows[targetIndex], pick.field, selectedRows[0])
    calculateDetailRow(pick.tabKey, targetRows[targetIndex])
    changedRows.push(targetRows[targetIndex])
    offset = 1
  }
  for (let index = offset; index < selectedRows.length; index++) {
    const row = newDetailRow(pick.tabKey)
    applyDetailReference(row, pick.field, selectedRows[index])
    calculateDetailRow(pick.tabKey, row)
    targetRows.push(row)
    changedRows.push(row)
  }

  // 检验单：参照带出的字段（如不合格原因/存货）也要同步到不合格处理页签
  const detailTabs = cfgCache.value?.detail?.tabs || []
  const mainTabKey = detailTabs[0]?.key || 'items'
  if (hasUnqualifiedTab(detailTabs) && pick.tabKey === mainTabKey) {
    for (let index = 1; index <= targetRows.length; index += 1) {
      syncUnqualifiedRow(detail, index, mainTabKey)
    }
  }

  await engine.fillCurrentStock(changedRows)

  // BOM 展开：产品明细选产品 → 从 BOM 面板 children 带出材料明细（与表单页 loadBomFor 一致）
  if (pick.tabKey === 'products' && pick.field.dataName === '产品编码') {
    await expandBomMaterials(detail, targetRows)
  }

  detailRefSaving.value = true
  try {
    await engine.callButton({
      panelCode: panelCode.value,
      buttonName: '保存',
      formData: currentFormData(detail),
      buttonParam: {},
    })
    detailRefVisible.value = false
    const documentNo = pick.documentNo
    await load()
    const currentIndex = list.value.findIndex((item) => item['编号'] === documentNo)
    if (currentIndex >= 0) curIdx.value = currentIndex
    ElMessage.success(tt('已导入') + ` ${selectedRows.length} ` + tt('条存货并保存'))
  } catch (error) {
    discardCreatedDetailRefRow(pick)
    ElMessage.error(engine.errMsg(error) || tt('存货导入保存失败'))
  } finally {
    detailRefSaving.value = false
    detailRefPick.value = null
  }
}

function qType(qr) {
  const t = qr.dataType || fieldDefOf(qr.dataName).dataType || '文本'
  if (t === '参照') return 'ref'
  if (t === '下拉框') return 'select'
  if (t === '日期' || t === '日期时间') return 'date'
  return 'input'
}

function openQueryDialog() {
  Object.keys(queryDraft).forEach((key) => delete queryDraft[key])
  Object.assign(queryDraft, condition)
  queryDialogVisible.value = true
}

function openQueryRef(qr, context = 'page') {
  queryRefField.value = qr
  queryRefContext.value = context
  queryRefVisible.value = true
}

function clearQueryRef(qr, context = 'page') {
  const key = headerFieldKey(qr)
  if (context === 'dialog') {
    delete queryDraft[key]
    return
  }
  delete condition[key]
  search()
}

function onQueryRefConfirm(rows) {
  const field = queryRefField.value
  const row = rows?.[0]
  if (!field || !row) return
  const ref = field.ref && typeof field.ref === 'object' ? field.ref : field
  const valueField = ref.field || ref.refField || ref.display || ref.displayField || headerFieldKey(field)
  const target = queryRefContext.value === 'dialog' ? queryDraft : condition
  target[headerFieldKey(field)] = row[valueField] ?? ''
  queryRefVisible.value = false
  queryRefField.value = null
  if (queryRefContext.value === 'page') search()
}

// ==================== 参照字段动态形态：≤20 行下拉直选 / >20 行弹窗搜索定位 ====================
// 移植自 YINJIA 优化：按参照面板数据行数自动切换交互形态，数据量跨越阈值时
// （增删档案后）由 refreshRefModes 重新判定，形态随之切换。
const REF_DROPDOWN_THRESHOLD = 20
const refModeMap = reactive({})    // fieldKey -> 'dialog' | 'select'
const refSelectData = reactive({}) // fieldKey -> { options: [{label, value, row}], loading }
const refSelectTimers = {}

function refFieldKey(field) {
  const refPanel = field?.refPanel || field?.ref?.panel || ''
  return `${refPanel}|${headerFieldKey(field)}`
}

async function checkRefMode(field, key) {
  if (refModeMap[key]) return refModeMap[key]
  refModeMap[key] = 'dialog' // 默认弹窗，异步判定后可能切下拉
  try {
    const count = await engine.refRowCount(field)
    // 少量数据（≤20）用下拉轻快；大量数据（>20）用弹窗带搜索定位
    refModeMap[key] = count > REF_DROPDOWN_THRESHOLD ? 'dialog' : 'select'
    if (refModeMap[key] === 'select') loadRefSelectOptions(field, key, '')
  } catch (e) {
    /* 计数失败保持弹窗 */
  }
  return refModeMap[key]
}

// 远程搜索选项加载（300ms 防抖，避免每个按键打一次后端）
function loadRefSelectOptions(field, key, keyword) {
  if (!refSelectData[key]) refSelectData[key] = reactive({ options: [], loading: false })
  clearTimeout(refSelectTimers[key])
  refSelectTimers[key] = setTimeout(async () => {
    refSelectData[key].loading = true
    try {
      refSelectData[key].options = await engine.refSelectOptions(field, keyword)
    } catch (e) {
      refSelectData[key].options = []
    } finally {
      refSelectData[key].loading = false
    }
  }, 300)
}

/** 数据变化后重新判定全部参照字段形态（清空缓存计数，按最新数据量切换下拉/弹窗）。 */
async function refreshRefModes() {
  const all = [
    ...(queryFields.value || []),
    ...(headerFields.value || []),
    ...(cfgCache.value?.detail?.tabs || []).flatMap((tab) => tab.fields || []),
  ].filter(isReferenceField)
  const validKeys = new Set(all.map((f) => refFieldKey(f)))
  Object.keys(refModeMap).forEach((k) => {
    if (!validKeys.has(k)) delete refModeMap[k]
  })
  await Promise.all(all.map((f) => {
    const key = refFieldKey(f)
    delete refModeMap[key]
    return checkRefMode(f, key)
  }))
}

function isRefSelect(field) {
  return isReferenceField(field) && refModeMap[refFieldKey(field)] === 'select'
}

// 下拉选中后走与弹窗完全相同的确认回填链路（含 refMap 带出），避免两路径行为漂移
function onHeaderRefSelect(field, value) {
  const opt = (refSelectData[refFieldKey(field)]?.options || []).find((o) => o.value === value)
  if (!opt?.row) return
  headerRefField.value = field
  onHeaderRefConfirm([opt.row])
}

function onDetailRefSelect(field, row, b, value) {
  const opt = (refSelectData[refFieldKey(field)]?.options || []).find((o) => o.value === value)
  if (!opt?.row) return
  // 与 openDetailReference 相同的前置校验，但不打开弹窗（下拉即选即填）
  if (!detailEditable(b) || field.computed || row?._placeholder) return
  detailRefPick.value = {
    field,
    row,
    tabKey: activeTab(b).key,
    documentNo: cur.value['编号'],
    created: false,
  }
  onDetailRefConfirm([opt.row])
}

function applyHeaderQuery() {
  Object.keys(condition).forEach((key) => delete condition[key])
  for (const [key, value] of Object.entries(queryDraft)) {
    if (value !== undefined && value !== null && String(value) !== '') condition[key] = value
  }
  queryDialogVisible.value = false
  search()
}

function resetHeaderQuery() {
  Object.keys(queryDraft).forEach((key) => delete queryDraft[key])
  Object.keys(condition).forEach((key) => delete condition[key])
  query.keyword = ''
  queryDialogVisible.value = false
  search()
}

const qOptCache = new Map()
function qOptions(qr) {
  const key = panelCode.value + '|' + qr.dataName
  if (!qOptCache.has(key)) qOptCache.set(key, (qr.options || engine.fieldOptions(qr)).map((o) => typeof o === 'object' ? o : ({ value: o, label: o })))
  return qOptCache.get(key)
}

function reportLeaf(column) {
  const field = fieldDefOf(column)
  const numeric = field.dataType === '小数' || field.dataType === '整数'
  return { prop: column, label: tt(columnAliases.value[column] || column), width: colW(field), align: numeric ? 'right' : 'left' }
}

async function reportPage(pageNo) {
  const target = Math.max(1, Math.min(pageNo, reportPageCount.value))
  if (target === query.pageNo) return
  query.pageNo = target
  await load()
}

function exportReport() {
  const columns = reportColumns.value
  const esc = (value) => {
    const text = String(value ?? '')
    return /[",\n\t]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text
  }
  const csv = '\ufeff' + columns.map((c) => esc(columnAliases.value[c] || c)).join(',') + '\n' + list.value.map((row) => columns.map((column) => esc(row[column])).join(',')).join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${panelName.value}-${new Date().toISOString().slice(0, 10)}.csv`
  link.click()
  URL.revokeObjectURL(url)
  ElMessage.success(tt('已导出当前页') + ' ' + list.value.length + ' ' + tt('条数据'))
}

// 底部备注（可编辑，绑定当前单据）
const remarkText = computed({
  get: () => cur.value['备注'] ?? '',
  set: (v) => {
    if (cur.value && Object.keys(cur.value).length) cur.value['备注'] = v
  },
})

// ---------- 配置与按钮 ----------
async function loadCrg() {
  if (cfgCache.value) return cfgCache.value
  const cfg = await engine.getPanelConfig(panelCode.value)
  cfgCache.value = cfg
  const tp = cfg?.metadata?.panelPageDto?.tablePages?.[0]
  panelName.value = cfg?.metadata?.panelName || panelCode.value
  // 页面标题 = 真实面板名（路由 meta.title 是通用占位，配置加载后覆盖）
  document.title = panelName.value + ' · 轻MES'
  // 页签标题同步（生单直接跳转/页签替换后显示真实面板名）
  const curTab = tabs.tabs.find((x) => x.path === route.path)
  if (curTab) curTab.title = panelName.value
  queryFields.value = tp?.queryFields || []
  // 面板可配置每页条数（如档案类大列表 pageSize=100），未配置时保持默认 20；
  // BOM 只读浏览默认 10 项（父件表自己带 10/20/50 切换）
  const defaultPageSize = isBomMasterPanel.value && !(panelCode.value === 'BOM' && draftEditable.value) ? 10 : 20
  if (tp?.pageSize && query.pageSize !== tp.pageSize) {
    query.pageSize = tp.pageSize
    query.pageNo = 1
  } else if (!tp?.pageSize && query.pageSize !== defaultPageSize) {
    query.pageSize = defaultPageSize
    query.pageNo = 1
  }
  gridTabs.value = tp?.gridTabs || []
  columnAliases.value = cfg?.metadata?.columnAliases || {}
  const hasSelect = hasSelectConfig(cfg) || hasSelectConfig(cfg?.metadata)
  const configured = ensureAuditAction(
    ensureSelectAction(
      ensureFieldManageAction(
        ensureColumnPrefsAction(
          ensureScanFillAction(cfg?.metadata?.buttonGroups, cfg?.metadata),
          supportsColPrefs.value && (gridTabs.value[0]?.columns || []).length > 0,
        ),
        supportsFieldManage.value,
      ),
      hasSelect,
    ),
    supportsAudit(cfg?.metadata),
  )
  // 只留真有实现的动作：后端 callButton 未实现的动作点了只会抛「未定义按钮规则」，不如不显示
  groups.value = filterGroups(filterButtonGroups(
    filterSupportedActions(configured, { reportMode: reportMode.value, hasSelect }),
    panelCode.value,
    user,
  ))
  ensurePanelDict()
  // 参照带出列（X.Y）需要按参照面板取档案索引：配置到位后再后台加载，不阻塞首屏
  loadCarryPlans()
  return cfg
}

// 非中文语言：预取本面板可见词条（面板名/查询与表头字段/列名/按钮组与动作，静态包缺失的走机翻）
function ensurePanelDict() {
  if (localeStore.isZh) return
  localeStore.ensureDict(localeStore.current, [
    panelName.value,
    ...headerFields.value.map((f) => headerFieldLabel(f)),
    ...queryFields.value.map((f) => f.label || f.dataName),
    ...(gridTabs.value[0]?.columns || []).map((c) => (typeof c === 'string' ? c : String(c?.dataName ?? c?.name ?? c?.label ?? ''))),
    ...(groups.value || []).flatMap((g) => [g.name, ...actsOf(g)]),
  ])
}
watch(() => localeStore.current, ensurePanelDict)

// ---------- 表格调整（阶段 C：按用户列顺序/显隐/别名） ----------
function columnPrefList() {
  const saved = cfgCache.value?.metadata?.columnPrefs
  if (Array.isArray(saved) && saved.length) return saved
  // 无定制：默认全显、无别名（列对象兼容 dataName/name/label，与后端 columnNameOf 同口径）
  return (gridTabs.value[0]?.columns || []).map((c) => ({ name: columnNameOf(c), alias: '', visible: true }))
    .filter((x) => x.name)
}

async function onColumnPrefsSaved(columns) {
  try {
    await engine.saveColumnPrefs({ panelCode: panelCode.value, columns })
    cfgCache.value = null // 强制重取配置（列序/别名由后端读取出口叠加返回）
    await loadCrg()
    ElMessage.success(columns.length ? tt('表格调整已保存') : tt('已恢复默认列设置'))
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('保存失败'))
  }
}

/** 字段管理保存后：重取面板配置（新字段由读取出口合并进来），并清掉旧列筛选状态 */
async function onFieldManageDone() {
  try {
    cfgCache.value = null
    await loadCrg()
    for (const key of Object.keys(colFilters)) delete colFilters[key]
    ElMessage.success(tt('字段已更新'))
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('刷新失败'))
  }
}

function isDisabled(action) {
  const st = current.value?.['单据状态']
  const map = {
    新增: false, // 单单据面板新增按钮不置灰
    删除: !current.value,
    审核: !current.value || st !== '草稿',
    弃审: !current.value || st !== '已审核',
    中止执行: !current.value || !['已审核', '生产中', '已完工'].includes(st),
    整单中止: !current.value || !['已审核', '生产中', '已完工'].includes(st),
    中止: !current.value || !['已审核', '生产中', '已完工'].includes(st),
    草稿: !current.value || st !== '已中止',
    取消中止: !current.value || st !== '已中止',
    关闭: !current.value || !['已审核', '生产中'].includes(st),
    审批情况: false,
    提交审批: !current.value || st !== '草稿',
    审批通过: !current.value || st !== '审批中',
    审批驳回: !current.value || st !== '审批中',
   驳回审批: !current.value || st !== '审批中',
    保存: !draftEditable.value || inlineSaving.value,
    保存为草稿: !draftEditable.value || inlineSaving.value,
    保存新增: !draftEditable.value || inlineSaving.value,
    扫描填单: reportMode.value,
    [INVOICE_LINE_ACTION]: !draftEditable.value,
  }
  // 2026-08-25：所有「生成XX」生单按钮统一仅已审核/生产中可用（对齐 T+：已审核才能选择生单）
  if (map[action] === undefined && action.startsWith('生成')) {
    return !current.value || !['已审核', '生产中'].includes(st)
  }
  return map[action] === true
}

// 2026-11-04（对齐 yinjia 2026-10-15 用户口径）：列表页**不再有**任何打开「整单卡片」的入口 ——
//   双击明细行（见 onDetailCellDblclick）与工具栏「修改」都曾走 openForm() 弹 VoucherFormDialog，
//   两者连同 VoucherFormDialog 组件一并删除；单据数据一律在列表页原地内联编辑（草稿态）。

// 直接新增：调后端保存（空表头）创建最新草稿单（autoCode 编号 + 单据日期=当天自动填入），
// 刷新列表并定位到新单，在列表页直接内联填写（不跳转表单页/不弹新增弹窗）。
async function directAdd() {
  // 单据列表模式：新增打开空白单据详情页，保存时才建档（列表页不再内联编辑）。
  if (documentListMode.value) {
    openDocForm(null)
    return
  }
  try {
    const res = await engine.callButton({ panelCode: panelCode.value, buttonName: '保存', formData: {}, buttonParam: {} })
    const no = res && (res['编号'] || res.formNo)
    if (!no) return ElMessage.error(tt('新增失败') + '：' + tt('未返回单据编号'))
    await load() // 刷新列表（新单按创建时间倒序置顶）
    curIdx.value = 0 // 定位到最新单据，草稿状态列表页可直接填写
    ElMessage.success(tt('已新增') + ` ${panelName.value}-${no}，` + tt('请在列表页填写并保存'))
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('新增失败'))
  }
}

/** 置灰按钮的原因文案：此前静默忽略，用户点「生单」「审批通过」看不到任何反馈。 */
function disabledReason(action) {
  const st = current.value?.['单据状态']
  if (!current.value) return '请先选择一张单据'
  if (action.startsWith('生成')) {
    // 审批面板上「审核」只把单据推进到「审批中」，用户很容易以为已经审完；
    // 这里按真实状态给出下一步动作，而不是笼统让他「先审核」。
    if (st === '审批中') return '已提交审批，需「审批通过」后才可生单'
    return '需单据已审核后才可生单，请先审核'
  }
  if (action === '审核') return `仅草稿可审核，当前为「${st || '-'}」`
  if (action === '弃审') return `仅已审核可弃审，当前为「${st || '-'}」`
  if (action === '提交审批') return `仅草稿可提交审批，当前为「${st || '-'}」`
  if (action === '审批通过' || action === '审批驳回' || action === '驳回审批') {
    return `仅审批中可审批，当前为「${st || '-'}」`
  }
  if (['保存', '保存新增', '保存为草稿'].includes(action)) return '当前单据不可编辑，无法保存'
  if (['中止执行', '整单中止'].includes(action)) return '仅已审核/生产中/已完工可中止'
  return `当前状态不可执行「${action}」`
}

// 销售订单 → 生产加工单：先弹生单预览，让用户看到库存并勾选要投产的行
const produceVisible = ref(false)
const produceNo = ref('')

const saleOutVisible = ref(false)
const saleOutNo = ref('')

const finishInVisible = ref(false)
const finishInNo = ref('')

async function onFinishInGenerated(res) {
  if (res?.gotoPanel) {
    const no = res['编号'] || res.formNo || ''
    ElMessage.success(tt('已生成') + tt('产成品入库单') + '：' + no + '，' + tt('请在列表页继续填写'))
    const targetPath = `/panelx/list/${res.gotoPanel}`
    tabs.close(route.path)
    await router.push({ path: targetPath, query: no ? { code: no } : {} })
    tabs.open({ path: targetPath, title: res.gotoPanel, query: no ? { code: no } : {} })
    return
  }
  await load()
}

async function onSaleOutGenerated(res) {
  if (res?.gotoPanel) {
    const no = res['编号'] || res.formNo || ''
    ElMessage.success(tt('已生成') + tt('销售出库单') + '：' + no + '，' + tt('请在列表页继续填写'))
    const targetPath = `/panelx/list/${res.gotoPanel}`
    tabs.close(route.path)
    await router.push({ path: targetPath, query: no ? { code: no } : {} })
    tabs.open({ path: targetPath, title: res.gotoPanel, query: no ? { code: no } : {} })
    return
  }
  await load()
}

async function onProduceGenerated(res) {
  // 生单完成后直接跳到生产加工单，并定位到刚生成的那张，省掉用户手找
  if (res?.gotoPanel) {
    const no = res['编号'] || res.formNo || ''
    ElMessage.success(tt('已生成') + tt('生产加工单') + '：' + no + '，' + tt('请在列表页继续填写'))
    const targetPath = `/panelx/list/${res.gotoPanel}`
    tabs.close(route.path)
    await router.push({ path: targetPath, query: no ? { code: no } : {} })
    tabs.open({ path: targetPath, title: res.gotoPanel, query: no ? { code: no } : {} })
    return
  }
  await load()
}

async function onButton(action) {
  // 采购发票 Excel 导入：前端弹窗按「发票号码 + 供应商」分组逐张保存，不走下面的 callButton 分发
  if (action === INVOICE_IMPORT_ACTION) {
    invoiceImportVisible.value = true
    return
  }
  if (isDisabled(action)) return ElMessage.warning(tt(disabledReason(action)))
  if (APPROVE_ACTIONS.includes(action) && !user.isAdmin && !user.approvePanels.includes(panelCode.value)) {
    return ElMessage.warning(tt('当前角色无审批权限'))
  }
  // 选工艺路线：手工建加工单时按路线带出工序
  if (action === '选工艺路线' && panelCode.value === 'MANU_ORDER') {
    const no = current.value?.['编号'] || current.value?.['单据编号'] || ''
    if (!no) return ElMessage.warning(tt('请先保存加工单再选路线'))
    try {
      const res = await engine.queryFormDataList({ panelCode: 'ROUTE', pageNo: 1, pageSize: 50 })
      const seen = new Map()
      for (const doc of res?.list || []) {
        for (const p of doc?.detail?.processes || []) {
          const code = String(p['工艺路线编码'] || '')
          if (code && !seen.has(code)) seen.set(code, String(p['工艺路线名称'] || code))
        }
      }
      routeOptions.value = [...seen.entries()].map(([code, name]) => ({ code, name }))
      if (!routeOptions.value.length) return ElMessage.warning(tt('PDM 里还没有工艺路线'))
      routeForm.value = { 编号: no, 工艺路线编码: routeOptions.value[0].code, 数量: Number((current.value?.detail?.products || [])[0]?.['数量'] || 0) }
      routeVisible.value = true
    } catch (e) {
      ElMessage.error(engine.errMsg(e) || tt('加载工艺路线失败'))
    }
    return
  }
  // 核销：先列出本客户未结往来单，勾选后再核销（不再无提示地按 FIFO 冲最早一笔）
  if (action === '核销') {
    const no = current.value?.['编号'] || current.value?.['单据编号'] || ''
    if (!no) return ElMessage.warning(tt('请先选择一张单据'))
    try {
      const rows = (await engine.openWriteOffLines(panelCode.value, no)) || []
      if (!rows.length) return ElMessage.warning(tt('该往来单位没有未结单据'))
      writeOffNo.value = no
      // 默认按收款金额先进先出预填，用户可改成只核某几张单
      let remain = Number(current.value['金额'] || current.value['未核销金额'] || 0)
      writeOffRows.value = rows.map((r) => {
        const open = Number(r['未结金额']) || 0
        const take = remain <= 0 ? 0 : Math.min(open, remain)
        remain = Math.round((remain - take) * 100) / 100
        return { ...r, 核销金额: take }
      })
      writeOffVisible.value = true
    } catch (e) {
      ElMessage.error(engine.errMsg(e) || tt('加载未结单据失败'))
    }
    return
  }
  if (action === '生成生产加工单' && panelCode.value === 'SO_ORDER') {
    if (!current.value) return ElMessage.warning(tt('请先选择一行数据'))
    produceNo.value = current.value['编号'] || current.value['单据编号'] || ''
    produceVisible.value = true
    return
  }
  if (action === '生成产成品入库单' && panelCode.value === 'MANU_ORDER') {
    if (!current.value) return ElMessage.warning(tt('请先选择一行数据'))
    finishInNo.value = current.value['编号'] || current.value['单据编号'] || ''
    finishInVisible.value = true
    return
  }
  // 生产加工单：进入工单排产工作台（按工序分批排产）
  if (action === '排产') {
    const no = current.value?.['编号'] || current.value?.['单据编号'] || ''
    if (!no) return ElMessage.warning(tt('请先选择一张加工单'))
    const st = String(current.value?.['单据状态'] || '')
    if (st !== '已审核' && st !== '生产中') return ElMessage.warning(tt('仅已审核/生产中的加工单可以排产'))
    const path = '/mes/plan'
    const query = { code: no }
    router.push({ path, query })
    tabs.open({ path, title: `工单排产-${no}`, query })
    return
  }

  // 销售发票打印：审批通过后可用，独立页面渲染便于直接打印
  if (action === '打印发票') {
    const no = current.value?.['编号'] || current.value?.['单据编号'] || ''
    if (!no) return ElMessage.warning(tt('请先选择一张发票'))
    if (current.value?.['单据状态'] !== '已审核') return ElMessage.warning(tt('发票审批通过后才能打印'))
    window.open(`${location.origin}${location.pathname}#/print/invoice?code=${encodeURIComponent(no)}`, '_blank')
    return
  }
  if (['生成销售出库单', '生成销售出库单(普通销售)'].includes(action) && panelCode.value === 'SO_ORDER') {
    if (!current.value) return ElMessage.warning(tt('请先选择一行数据'))
    saleOutNo.value = current.value['编号'] || current.value['单据编号'] || ''
    saleOutVisible.value = true
    return
  }
  if (action === '扫描填单') {
    scanVisible.value = true
    return
  }
  if (action === '表格调整') {
    colPrefsColumns.value = columnPrefList()
    colPrefsVisible.value = true
    return
  }
  if (action === '字段管理') {
    fieldMgrVisible.value = true
    return
  }
  if (action === '查询' || action === '查找') {
    search()
    return
  }
  if (reportMode.value && action === '导出') {
    exportReport()
    return
  }
  if (action === '预览' || (reportMode.value && action === '打印')) {
    window.print()
    return
  }
  if (action === '恢复') {
    await load()
    ElMessage.success(tt('已恢复为最近一次保存的数据'))
    return
  }
  if (reportMode.value && action === '发送邮件') {
    ElMessage.info(tt('报表邮件发送需先配置企业邮箱服务'))
    return
  }
  if (reportMode.value && action === '退出') {
    router.push('/dashboard')
    return
  }
  if (action === '导入') {
    // Excel 导入：识别 A 区主明细字段，导入后追加行并自动保存
    const blk = blocks.value.find((x) => x.id === 'A')
    const tab = blk ? activeTab(blk) : null
    if (!tab) return ElMessage.warning(tt('该面板无明细可导入'))
    // 字段定义取自面板配置 detail.tabs（blocks 的 tab 只有列名 cols）；档案面板无明细 tab → 用 dataSchema.fields
    const tabDef = (cfgCache.value?.detail?.tabs || []).find((t) => t.key === tab.key)
    const fields = (tabDef && tabDef.fields && tabDef.fields.length)
      ? tabDef.fields
      : (cfgCache.value?.dataSchema?.fields || [])
    impFields.value = (fields || []).filter((f) => !f.hidden)
    impLabel.value = tab.label || '明细'
    impVisible.value = true
    return
  }
  // 采购发票「选入库单行」：前端弹窗按行追加，不走后端 callButton，也不能落到下面的选单分支
  if (action === INVOICE_LINE_ACTION) {
    openInvoiceLinePick()
    return
  }
  // 选单通用化：任意 选X 动作且配置有 selectConfig 即走选单（对齐 PanelxForm 的通用分支）
  if (action === '选单' || action.startsWith('选')) {
    const sc = selectConfigFor(action)
    if (sc) {
      // 选单通用化：列表页内嵌小弹窗勾选已审核源单据，确定后生成目标单据并打开表单（对齐 T+ 选单生单语义，不再跳转「新增」页面）
      selCfg.value = sc
      selVisible.value = true
      return
    }
    ElMessage.warning(tt('该面板未配置选单来源'))
    return
  }
  if (action === '新增' || action === '新建' || action === '新增流程') {
    if (cfgCache.value?.metadata?.singleDoc && current.value && current.value['编号']) {
      // 档案/单单据面板（存货档案、员工、部门等）：直接在当前单据页填写（列表页已内联可编辑），不弹新增弹窗
      ElMessage.info(tt('请在下方列表页直接填写并保存'))
      return
    }
    // 统一直接新增（2026-08-24 全量生效）：后端创建一张最新草稿单（autoCode 编号 + 单据日期=当天填入表头），
    // 刷新列表并定位到新单，在列表页内联填写（不再弹新增弹窗、不跳转表单页）
    return await directAdd()
  }
  // 「修改」：物料清单主从视图下 = 跳到选中父件所属的那张 BOM 单据（表头 + 子件明细一起改）。
  // 其余面板仍不处理（该动作已从工具栏摘掉，见 toolbarGroups）。
  if (action === '修改' && isBomMasterPanel.value) {
    await openBomDocumentForEdit()
    return
  }
  if (['保存', '保存为草稿', '保存新增'].includes(action) && draftEditable.value) {
    await saveInlineDraft(action)
    return
  }
  if (action === '刷新') {
    load()
    return
  }
  if (action === '删除单据') {
    if (!current.value) return ElMessage.warning(tt('请先选择一行数据'))
    const no = current.value['编号'] || current.value['单据编号'] || ''
    try {
      await ElMessageBox.confirm(tt('确认删除整张单据') + ' ' + no + '？' + tt('该操作不可恢复。'), tt('删除单据确认'), { type: 'warning' })
    } catch (e) {
      return
    }
    try {
      await engine.deleteForms({ panelCode: panelCode.value, rowCodes: [no] })
      ElMessage.success(tt('单据已删除') + '：' + no)
      delMode.value = false
      delSel.value = []
      load()
    } catch (e) {
      ElMessage.error(engine.errMsg(e) || tt('删除失败'))
    }
    return
  }
  if (action === '删除') {
    if (!current.value) return ElMessage.warning(tt('请先选择一行数据'))
    if (!delMode.value) {
      delMode.value = true
      ElMessage.info(tt('已进入删除模式：勾选要删除的行，再点「删除」确认；点「刷新」或翻页取消'))
      return
    }
    if (!delSel.value.length) return ElMessage.warning(tt('请先勾选要删除的行'))
    try {
      await ElMessageBox.confirm(tt('确认删除勾选的') + ' ' + delSel.value.length + ' ' + tt('行明细？'), tt('删除确认'), { type: 'warning' })
    } catch (e) {
      return
    }
    try {
      // 勾选删除：从当前单据对应明细中移除所选行（按对象引用匹配）
      const blk = blocks.value.find((x) => x.isMain)
      const tab = blk ? activeTab(blk) : null
      const key = tab ? tab.key : 'items'
      const items = cur.value.detail && Array.isArray(cur.value.detail[key]) ? cur.value.detail[key] : []
      const remain = items.filter((it) => !delSel.value.includes(it))
      const head = { ...cur.value }
      delete head.detail
      delete head['编号']
      delete head['单据状态']
      delete head['创建时间']
      delete head['更新时间']
      delete head['发起人编号']
      await engine.callButton({
        panelCode: panelCode.value,
        buttonName: '保存',
        formData: { ...head, 编号: cur.value['编号'], detail: { ...(cur.value.detail || {}), [key]: remain } },
        buttonParam: {},
      })
      ElMessage.success(tt('已删除') + ' ' + delSel.value.length + ' ' + tt('行'))
      delMode.value = false
      delSel.value = []
      load()
    } catch (e) {
      ElMessage.error(engine.errMsg(e) || tt('删除失败'))
    }
    return
  }
  if (['中止执行', '整单中止', '草稿', '取消中止', '提交审批', '审批通过', '驳回审批'].includes(action)) {
    if (!current.value) return ElMessage.warning(tt('请先选择一行数据'))
  }
  // 人工审核：确认弹窗 + 审核意见（选填）；审核人取当前登录人（后端从 JWT 取）
  let auditOpinion = ''
  if (action === '审核') {
    if (!current.value) return ElMessage.warning(tt('请先选择一行数据'))
    // 已审核过的单据不允许再次审核，也不允许补填审批意见
    if (current.value['单据状态'] !== '草稿') return ElMessage.warning(tt('仅草稿状态可审核，已审核单据不允许再次审核'))
    const no = current.value['编号'] || current.value['单据编号'] || ''
    try {
      const { value } = await ElMessageBox.prompt(
        '单据：' + no + '（当前状态：' + (current.value['单据状态'] || '') + '）',
        '人工审核确认',
        { confirmButtonText: '确认审核', cancelButtonText: '取消', inputType: 'textarea', inputPlaceholder: '审核意见（选填）' }
      )
      auditOpinion = value || ''
    } catch (e) {
      return
    }
  } else if (action === '弃审') {
    if (!current.value) return ElMessage.warning(tt('请先选择一行数据'))
    if (current.value['单据状态'] !== '已审核') return ElMessage.warning('仅已审核状态可弃审')
    try {
      await ElMessageBox.confirm(tt('确认弃审该单据？弃审后需重新审核。'), tt('弃审确认'), { type: 'warning' })
    } catch (e) {
      return
    }
  }
  try {
    // 审批流：提交审批/审批通过（确认+意见）、审批驳回（意见必填）、审批情况（历史弹窗）
    let approvalOpinion = ''
    if (action === '提交审批' || action === '审批通过') {
      if (!current.value) return ElMessage.warning(tt('请先选择一行数据'))
      const need = action === '提交审批' ? '草稿' : '审批中'
      if (current.value['单据状态'] !== need) return ElMessage.warning(action === '提交审批' ? '仅草稿状态可提交审批' : '仅审批中状态可审批通过')
      const no = current.value['编号'] || current.value['单据编号'] || ''
      try {
        // 同表单页：确认框要说明这一步之后还差什么，否则用户以为提交即完成。
        const nextStep = action === '提交审批'
          ? '提交后单据进入「审批中」，还需完成「审批通过」才算审核完成，之后才能生单。'
          : '审批通过后单据归档为「已审核」，可继续生单。'
        const { value } = await ElMessageBox.prompt(
          '单据：' + no + '（当前状态：' + (current.value['单据状态'] || '') + '）\n' + nextStep,
          action + '确认',
          { confirmButtonText: '确认' + action, cancelButtonText: '取消', inputType: 'textarea', inputPlaceholder: action === '审批通过' ? '审批意见（选填）' : '提交说明（选填）' }
        )
        approvalOpinion = value || ''
      } catch (e) {
        return
      }
    } else if (action === '审批驳回') {
      if (!current.value) return ElMessage.warning(tt('请先选择一行数据'))
      if (current.value['单据状态'] !== '审批中') return ElMessage.warning('仅审批中状态可审批驳回')
      const no = current.value['编号'] || current.value['单据编号'] || ''
      try {
        const { value } = await ElMessageBox.prompt(
          '单据：' + no + '（当前状态：审批中）\n驳回必须填写审批意见',
          '审批驳回确认',
          { confirmButtonText: '确认驳回', cancelButtonText: '取消', inputType: 'textarea', inputPlaceholder: '驳回原因（必填）', inputValidator: (v) => (v && v.trim() ? true : '驳回必须填写审批意见') }
        )
        approvalOpinion = value || ''
      } catch (e) {
        return
      }
    } else if (action === '审批情况') {
      if (!current.value) return ElMessage.warning(tt('请先选择一行数据'))
      approvalNo.value = current.value['编号'] || current.value['单据编号'] || ''
      approvalVisible.value = true
      return
    }
    const actionDocumentNo = current.value?.['编号'] || current.value?.['单据编号'] || ''
    // 列表页草稿是前端内联编辑态；审核/提交审批前必须先落库，否则状态刷新后会显示数据库中的旧空明细。
    if (['审核', '提交审批'].includes(action) && draftEditable.value) {
      const saved = await saveInlineDraft('保存', { silent: true })
      if (!saved) return
    }
    const res = await engine.callButton({
      panelCode: panelCode.value,
      buttonName: action,
      formData: current.value ? { 编号: current.value['编号'], ...(auditOpinion !== '' ? { 审核意见: auditOpinion } : {}), ...(approvalOpinion !== '' ? { 审批意见: approvalOpinion } : {}) } : {},
      buttonParam: {},
    })
    if (res?.gotoPanel) {
      // 推式生单：跳转到目标面板列表页并定位到新生成的单据（草稿内联可编辑），保留来源面板页签
      ElMessage.success(tt('已生成') + (res.gotoPanel === 'MANU_ORDER' ? tt('生产加工单') : res.gotoPanel) + '：' + res['编号'] + '，' + tt('请在列表页继续填写'))
      const targetPath = `/panelx/list/${res.gotoPanel}`
      const targetQuery = { code: res['编号'] }
      router.push({ path: targetPath, query: targetQuery })
      tabs.open({ path: targetPath, title: res.gotoPanel, query: targetQuery })
      return
    }
    // 「审核」在审批面板上等于「提交审批」（后端 submitApproval），不会直接到已审核。
    // 不提示清楚的话，用户会以为审完了，接着点「生单」被 409 拦下。
    // 「审核」和「提交审批」都会把单据推进到「审批中」，两者都要给出下一步提示。
    // 齐套检查：结果不是「成功/失败」，而是可开工数量 + 缺料清单，直接播报提示语
    if (action === '齐套检查' && res && res['提示']) {
      const enough = res['单据状态'] === '齐套'
      ElMessage({
        type: enough ? 'success' : 'warning',
        message: res['提示'],
        duration: enough ? 3000 : 6000,
        showClose: true,
      })
      await load()
      return
    }
    if (['审核', '提交审批'].includes(action) && res && res['单据状态'] === '审批中') {
      ElMessage.success(tt('已提交审批') + '，' + tt('需「审批通过」后才算审核完成，之后才能生单'))
    } else {
      ElMessage.success(`「${action}」执行成功`)
    }
    await load()
    const actionIndex = list.value.findIndex((item) => (
      (item['编号'] || item['单据编号'] || item['锭号']) === actionDocumentNo
    ))
    if (actionIndex >= 0) curIdx.value = actionIndex
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '按钮执行失败')
  }
}

async function onScanApply(payload) {
  if (!draftEditable.value) {
    try {
      const created = await engine.callButton({ panelCode: panelCode.value, buttonName: '保存', formData: {}, buttonParam: {} })
      const documentNo = created?.['编号'] || created?.formNo
      if (!documentNo) throw new Error('未返回单据编号')
      await load()
      const index = list.value.findIndex((row) => row['编号'] === documentNo)
      curIdx.value = index >= 0 ? index : 0
    } catch (error) {
      ElMessage.error(engine.errMsg(error) || '新建草稿失败')
      return
    }
  }

  const fields = new Map(headerFields.value.map((field) => [headerFieldKey(field), field]))
  for (const [key, value] of Object.entries(payload?.header || {})) {
    const field = fields.get(key)
    if (!field || field.hidden || field.computed || field.autoCode || headerFieldLocked(field)) continue
    cur.value[key] = value
  }

  if (!cur.value.detail) cur.value.detail = {}
  const tabMap = new Map((cfgCache.value?.detail?.tabs || []).map((tab) => [tab.key, tab]))
  for (const [tabKey, rows] of Object.entries(payload?.detail || {})) {
    const tab = tabMap.get(tabKey)
    if (!tab || !Array.isArray(rows)) continue
    const writable = new Set((tab.fields || []).filter((field) => !field.hidden && !field.computed && !priceLocked(field.dataName)).map((field) => field.dataName))
    const recognizedRows = rows.map((row) => Object.fromEntries(
      Object.entries(row || {}).filter(([key]) => writable.has(key)),
    ))
    cur.value.detail[tabKey] = payload?.detailMode === 'append'
      ? [...(cur.value.detail[tabKey] || []), ...recognizedRows]
      : recognizedRows
    for (const row of cur.value.detail[tabKey]) calculateDetailRow(tabKey, row)
  }
  ElMessage.success('识别数据已填入草稿，请核对后保存')
}

async function load() {
  delMode.value = false
  delSel.value = []
  if (invalidPanel.value) {
    ElMessage.error('面板编号无效，请从菜单重新进入')
    return
  }
  loading.value = true
  try {
    await loadCrg()
    const params = { panelCode: panelCode.value, condition: { ...condition }, pageNo: query.pageNo, pageSize: query.pageSize }
    if (query.keyword) params.keyword = query.keyword
    const res = await engine.queryFormDataList(params)
    list.value = res.list || []
    total.value = res.totalSize || 0
    loadedPanelCode.value = panelCode.value
    if (curIdx.value >= list.value.length) curIdx.value = 0
    // ?focus= 或 ?code= 定位到指定单据：focus 是旧参数（选单/生单跳转），code 是业务总览等外部入口用的
    const target = route.query.focus ?? route.query.code
    if (target) {
      const wanted = String(target)
      const fi = list.value.findIndex((r) => (r['编号'] || r['单据编号'] || r['锭号']) === wanted)
      if (fi >= 0) curIdx.value = fi
      // focus 用完即清（旧的即时定位语义）；code 保留在地址栏，刷新仍定位同一张单
      if (route.query.focus) router.replace({ path: route.path, query: { ...route.query, focus: undefined } })
    }
  } catch (e) {
    const msg = engine.errMsg(e) || '加载失败'
    ElMessage.error(msg)
  } finally {
    loading.value = false
    // 数据变化后重新判定参照字段形态（增删档案跨 20 条阈值时下拉/弹窗自动切换）
    refreshRefModes()
  }
}

function search() {
  query.pageNo = 1
  curIdx.value = 0
  load()
}

function reset() {
  Object.keys(condition).forEach((k) => delete condition[k])
  query.keyword = ''
  search()
}

function onNewSaved() {
  load()
}

function onSelGenerated(generated) {
  const first = generated && generated[0]
  const finish = () => {
    if (first) {
      // 2026-08-25：选单生成后不再弹窗，直接定位到列表页新选入单据（一屏一单流览）
      const idx = list.value.findIndex((r) => (r['编号'] || r['单据编号'] || r['锭号']) === first.no)
      curIdx.value = idx >= 0 ? idx : 0
    }
  }
  load().then(finish)
}

// Excel 导入发票完成：成功保存的草稿已在后端建档，刷新列表并定位到最新一张
function onInvoiceImported(result) {
  const count = result?.count || 0
  if (!count) return
  const lastNo = result.invoices?.[result.invoices.length - 1]?.['编号']
  load().then(() => {
    if (!lastNo) return
    const idx = list.value.findIndex((row) => (row['编号'] || row['单据编号'] || row['锭号']) === lastNo)
    if (idx >= 0) curIdx.value = idx
  })
}

// Excel 导入完成：单据面板追加到当前单明细并保存；档案面板（无明细 tab）逐行新建档案
async function onImported(rows) {
  const hasDetailTabs = (cfgCache.value?.detail?.tabs || []).length > 0
  if (!hasDetailTabs) {
    // 档案类（EMP/DEPT/WH…）：Excel 每行 = 一条新档案
    ElMessage.success('已解析 ' + rows.length + ' 行，正在逐条建档…')
    let ok = 0
    try {
      for (const r of rows) {
        await engine.callButton({ panelCode: panelCode.value, buttonName: '保存', formData: { ...r }, buttonParam: {} })
        ok++
      }
      ElMessage.success('已导入 ' + ok + ' 条档案')
    } catch (e) {
      ElMessage.error(engine.errMsg(e) || '第 ' + (ok + 1) + ' 条导入失败')
    }
    load()
    return
  }
  const blk = blocks.value.find((x) => x.id === 'A')
  const tab = blk ? activeTab(blk) : null
  const key = tab ? tab.key : 'items'
  if (!cur.value.detail || !Array.isArray(cur.value.detail[key])) {
    if (!cur.value.detail) cur.value.detail = {}
    cur.value.detail[key] = []
  }
  for (const r of rows) cur.value.detail[key].push(r)
  ElMessage.success('已导入 ' + rows.length + ' 行，正在保存…')
  try {
    const head = { ...cur.value }
    delete head.detail
    delete head['编号']
    delete head['单据状态']
    delete head['创建时间']
    delete head['更新时间']
    delete head['发起人编号']
    await engine.callButton({
      panelCode: panelCode.value,
      buttonName: '保存',
      formData: { ...head, 编号: cur.value['编号'], detail: { ...cur.value.detail } },
      buttonParam: {},
    })
    ElMessage.success('导入并保存成功')
    load()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('保存失败'))
  }
}

// 存货（INV）面板：单击行 → 打开 BOM 管理弹窗（勾选存货添加子件、可多级下钻）
function onRowClick(row, b) {
  if (row?._placeholder && detailEditable(b)) {
    openBlankDetailRow(b)
    return
  }
  // 材料明细：点材料行 → 该材料有下级 BOM 则弹窗展示其子件
  if (b && b.id === 'B' && activeTab(b).key === 'materials' && row && row['材料编码'] && hasSubBom(row['材料编码'])) {
    openSubBom(row)
    return
  }
  // 产成品→材料联动：MANU_ORDER 等单据点产成品明细行 → 材料明细只显示其 BOM 子件
  if (b && b.id === 'A' && row && row['产品编码'] && row['产品编码'] !== selectedProduct.value) {
    selectProduct(row['产品编码'])
    return
  }
  // 存货（INV）面板为纯存货管理（2026-08-25：BOM 关系维护已迁移至物料清单面板，存货行点击不再弹 BOM 管理）
  if (panelCode.value === 'INV') return
}

// 捕获阶段监听：点产成品明细行任意单元格（含固定列/控件）都触发联动
async function onTableClick(b, e) {
  if (!b || !e || !e.target || !e.target.closest) return
  const t = activeTab(b)
  // 材料明细：点材料行 → 该材料有下级 BOM 则弹窗展示其子件
  if (b.id === 'B' && t.key === 'materials') {
    const tr = e.target.closest('tr')
    if (!tr) return
    const body = tr.closest('.el-table__body-wrapper') || tr.closest('.el-table__fixed-body-wrapper')
    const rows = body ? [...body.querySelectorAll('tbody tr')] : []
    const idx = rows.indexOf(tr)
    const row = detailRows(t)[idx]
    if (row && row['材料编码'] && hasSubBom(row['材料编码'])) openSubBom(row)
    return
  }
  if (b.id !== 'A') return
  if (t.key !== 'products') return
  const tr = e.target.closest('tr')
  if (!tr) return
  const body = tr.closest('.el-table__body-wrapper') || tr.closest('.el-table__fixed-body-wrapper')
  const rows = body ? [...body.querySelectorAll('tbody tr')] : []
  const idx = rows.indexOf(tr)
  const row = detailRows(t)[idx]
  if (!row || !row['产品编码']) return
  selectProduct(row['产品编码'])
}

// 材料下级 BOM 映射（BOM 面板 children：父件编码 → 子件行）；材料编码行右上角显示红 *，点击行弹窗查看
async function loadSubBomMap() {
  try {
    const res = await engine.queryFormDataList({ panelCode: 'BOM', condition: {}, pageNo: 1, pageSize: 100 })
    const map = {}
    for (const d of res.list || []) {
      for (const it of (d.detail && d.detail.children) || []) {
        const parent = it['父件编码']
        if (!parent) continue
        if (!map[parent]) map[parent] = []
        map[parent].push({
          材料编码: it['子件编码'],
          材料名称: it['子件名称'],
          规格型号: it['规格型号'] || '',
          计量单位: it['子件计量单位'] || '件',
          定额需用数量: it['定额数量'] ?? 0,
          '损耗率%': it['损耗率%'] ?? 0,
        })
      }
    }
    subBomMap.value = map
  } catch (err) {}
}

function hasSubBom(code) {
  const b = subBomMap.value[code]
  return Array.isArray(b) && b.length > 0
}

function openSubBom(row) {
  const code = row['材料编码']
  subBomMaterial.value = row
  subBomBom.value = (subBomMap.value[code] || []).map((r) => ({ ...r }))
  subBomVisible.value = true
}

// 选中产成品：行高亮 + 材料明细联动（物料清单 BOM 面板 children → 子件编码集合；2026-08-25 原 INV _bom 已迁移）
async function selectProduct(code) {
  selectedProduct.value = code
  selectedBomCodes.value = []
  try {
    const res = await engine.queryFormDataList({ panelCode: 'BOM', condition: {}, pageNo: 1, pageSize: 200 })
    const codes = []
    for (const d of res.list || []) {
      for (const it of (d.detail && d.detail.children) || []) {
        if (String(it['父件编码']) !== code) continue
        if (it['子件编码']) codes.push(String(it['子件编码']))
      }
    }
    selectedBomCodes.value = codes
  } catch (err) {
    // 查询失败按 子件BOM 标记兜底
  }
}

watch(
  () => [panelCode.value, operationName.value],
  () => {
    scanVisible.value = false
    // 2026-08-20：关闭页签/切走时 panelCode 变 undefined——不触发加载（避免「面板编号无效」误报）
    if (!panelCode.value || panelCode.value === 'undefined') return
    cfgCache.value = null
    qOptCache.clear()
    Object.keys(refModeMap).forEach((k) => delete refModeMap[k])
    Object.keys(refSelectData).forEach((k) => delete refSelectData[k])
    Object.keys(refSelectTimers).forEach((k) => clearTimeout(refSelectTimers[k]))
    Object.keys(condition).forEach((key) => delete condition[key])
    Object.keys(queryDraft).forEach((key) => delete queryDraft[key])
    query.keyword = ''
    queryFields.value = []
    gridTabs.value = []
    queryRefVisible.value = false
    queryRefField.value = null
    queryDialogVisible.value = false
    headerRefVisible.value = false
    headerRefField.value = null
    detailRefVisible.value = false
    detailRefPick.value = null
    curIdx.value = 0
    search()
  }
)

watch(detailRefVisible, (visible) => {
  if (!visible && !detailRefSaving.value) {
    discardCreatedDetailRefRow(detailRefPick.value)
    detailRefPick.value = null
  }
})

watch(headerRefVisible, (visible) => {
  if (!visible) headerRefField.value = null
})

watch(queryRefVisible, (visible) => {
  if (!visible) queryRefField.value = null
})

// 快捷入口「新增单据」（?new=1）统一走直接新增（不弹窗）；singleDoc 无单据时兜底弹窗新建
let newQueryHandled = false
async function handleNewQuery() {
  if (newQueryHandled) return
  if (cfgCache.value?.metadata?.singleDoc && current.value && current.value['编号']) {
    newQueryHandled = true
    ElMessage.info(tt('请在下方列表页直接填写并保存'))
    return
  }
  if (cfgCache.value?.metadata?.singleDoc) {
    newQueryHandled = true
    newVisible.value = true
    return
  }
  newQueryHandled = true
  await directAdd()
}
watch(
  () => route.query.new,
  (v) => {
    if (v) handleNewQuery()
  }
)
watch(cfgCache, (cfg) => {
  // 配置加载完成后处理初始 ?new=1（此时才能判断 singleDoc）
  if (cfg && route.query.new) handleNewQuery()
})

onMounted(() => {
  document.addEventListener('click', closeCtx)
  document.addEventListener('contextmenu', closeCtx)
  loadSubBomMap() // 材料下级 BOM 映射（红 * 标记 + 点击行弹窗）
  if (invalidPanel.value) {
    router.replace('/panelx/list/MANU_ORDER')
    return
  }
  load()
})

/**
 * keep-alive 复用：PortalLayout 用 <keep-alive :max="20"> 缓存面板页，同一面板第二次进入时
 * onMounted 不再执行。从业务总览再点一张别的单据时，若不在这里重载，会停在上次那张上。
 * 只在「带 ?code=/?focus= 定位」或「面板编码变了」时重载，普通页签来回切换保持原状。
 */
onActivated(() => {
  if (invalidPanel.value) return
  const wanted = route.query.code ?? route.query.focus
  if (route.query.new) return // 新增走 ?new=1 自己的 watch
  if (wanted || panelCode.value !== loadedPanelCode.value) load()
})

onDeactivated(() => {
  // keep-alive 切离时关闭弹窗（防止 append-to-body 弹窗残留）
  newVisible.value = false
  queryDialogVisible.value = false
  queryRefVisible.value = false
  queryRefField.value = null
  headerRefVisible.value = false
  headerRefField.value = null
  detailRefVisible.value = false
  detailRefPick.value = null
  impVisible.value = false
  scanVisible.value = false
  maintainVisible.value = false
  selVisible.value = false
  invoicePickVisible.value = false
  invoiceImportVisible.value = false
})

onUnmounted(() => {
  document.removeEventListener('click', closeCtx)
  document.removeEventListener('contextmenu', closeCtx)
})
</script>

<style scoped>
.panelx-list {
  font-size: 13px;
  color: #333;
  /* 面板占满内容区（portal-content 有确定高度）：这样左侧单据栏才有确定高度、
     明细区自己滚、表尾「备注」永远贴在底部，不会被单据栏撑出屏幕。 */
  height: 100%;
  min-height: 100%;
  display: flex;
  flex-direction: column;
}

/* ═══════ ① 顶部工具栏（T+ 灰条）═══════ */
.tools {
  background: #f5f7fa;
  border-bottom: 1px solid #d0d7e3;
  padding: 8px 12px;
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}
.toolbar-query-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  align-self: stretch;
  min-width: 64px;
  padding: 0 12px;
  border: 0;
  border-right: 1px solid #c8ced8;
  background: transparent;
  color: #263548;
  font: inherit;
  cursor: pointer;
}
.toolbar-query-btn:hover {
  background: #e7eef8;
  color: #0d5bd3;
}
/* 返回：只在从外部入口（业务总览等）带 ?from= 进来时出现，放右侧与其他单据信息同区 */
.toolbar-back-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 28px;
  margin-right: 12px;
  padding: 0 10px;
  border: 1px solid #c9cfdb;
  border-radius: 3px;
  background: #fff;
  color: #263548;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.toolbar-back-btn:hover {
  border-color: #0d5bd3;
  background: #e7eef8;
  color: #0d5bd3;
}
.tb-group {
  display: inline-flex;
  align-items: center;
  position: relative;
  border: 1px solid #c9cfdb;
  border-radius: 3px;
  overflow: visible;
  margin-right: 4px;
  background: #fff;
}
.tb-menu {
  position: absolute;
  top: 100%;
  left: 0;
  z-index: 3000;
  min-width: 160px;
  background: #fff;
  border: 1px solid #d0d7e3;
  border-radius: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.14);
  padding: 4px 0;
  max-height: 360px;
  overflow: auto;
}
.tb-main {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  font-size: 13px;
  color: #333;
  cursor: pointer;
  user-select: none;
}
.tb-main:hover {
  color: #0d5bd3;
  background: #f0f5ff;
}
.tb-main.disabled {
  color: #b3b9c4;
  cursor: not-allowed;
}
/* 审批驳回：与「审批通过」并列的第二个决策，用克制的危险色区分，但不抢主按钮 */
.tb-main.danger {
  color: #c84d3f;
}
.tb-main.danger:hover {
  color: #a93a2e;
  background: #fdf1ef;
}
.tb-caret {
  display: inline-flex;
  align-items: center;
  padding: 0 5px;
  font-size: 12px;
  border-left: 1px solid #c9cfdb;
  color: #555;
  cursor: pointer;
}
.act-sc {
  margin-left: 8px;
  font-size: 12px;
  color: #999;
}
.tools-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 4px;
}
.doc-chip {
  font-size: 12px;
  color: #1c4f8a;
  font-weight: 600;
  margin-right: 6px;
}
.doc-status {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 10px;
  margin-right: 6px;
}
.doc-cat {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 10px;
  margin-right: 6px;
  color: #7c3aed;
  border: 1px solid #ddd6fe;
  background: #f5f3ff;
}
.doc-status.已审核,
.doc-status.已完工 {
  color: #16a34a;
  border: 1px solid #bbe6c4;
  background: #f0fdf4;
}
.doc-status.生产中,
.doc-status.审批中 {
  color: #0d5bd3;
  border: 1px solid #bcd2f5;
  background: #f0f6ff;
}
.doc-status.草稿 {
  color: #d97706;
  border: 1px solid #f3d9a6;
  background: #fffaf0;
}
.page-btn {
  width: 24px;
  height: 24px;
  line-height: 22px;
  text-align: center;
  border: 1px solid #c9cfdb;
  background: #fff;
  cursor: pointer;
  user-select: none;
  font-size: 12px;
  color: #333;
}
.page-btn:hover {
  border-color: #0d5bd3;
  color: #0d5bd3;
}
.page-no {
  padding: 0 6px;
  font-size: 12px;
  color: #555;
}
/* 明细区翻页条：贴表格下方，行高与页码按钮同为 24px，不抢表格的视觉重量 */
.dg-pager {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 3px 8px;
  border-top: 1px solid #e3e8f0;
  background: #fafbfd;
  font-size: 12px;
  color: #555;
}
.dg-count,
.dg-per-label {
  color: #64748b;
}
.dg-per {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.dg-size {
  width: 74px;
}
.dg-pager .page-btn {
  border-radius: 4px;
}
.dg-pager .page-no {
  padding: 0 2px;
}
/* 列头筛选生效提示：与「共 N 行」同行，不额外占一行高度 */
.dg-filter-hint {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  color: var(--t-primary-dark, #0d584c);
}
/* 参照带出列（存货名称.品牌）：跟普通文本同样的观感，但不可编辑 */
.carried-cell {
  color: #475569;
}
.report-count {
  color: #64748b;
  font-size: 12px;
  padding-right: 6px;
}

/* ═══════ ② 表头字段区（label 在上、输入在下）═══════ */
.fields {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  padding: 10px 12px 8px;
  border-bottom: 1px solid #e5e9f0;
  background: #fff;
}
/* 内联表头栅格（2026-11-03）：与表单页同口径，等宽列铺满，宽屏下不再挤在左边 */
.fields.header-fields {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px 16px;
  align-items: start;
}
.fields.header-fields .field {
  min-width: 0;
}
.fields.header-fields .field :deep(.el-input),
.fields.header-fields .field :deep(.el-select),
.fields.header-fields .field :deep(.el-date-editor),
.fields.header-fields .field :deep(.el-input-number),
.fields.header-fields .field :deep(.ref-ctl),
.fields.header-fields .field :deep(.query-ref),
.fields.header-fields .field :deep(.query-ref-select) {
  width: 100% !important;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.field label {
  font-size: 12px;
  color: #444;
  white-space: nowrap;
}
.field label.req::before {
  content: '*';
  color: #ff0033;
  margin-right: 2px;
}
.field :deep(.el-input),
.field :deep(.el-select),
.field :deep(.el-date-editor),
.field :deep(.el-input-number) {
  width: 160px;
}
.field :deep(.el-input__wrapper),
.field :deep(.el-select__wrapper) {
  min-height: 26px;
  padding: 1px 8px;
}
.field :deep(.el-input__inner) {
  height: 24px;
  line-height: 24px;
  font-size: 13px;
}
.query-ref {
  display: flex;
  width: 192px;
  gap: 4px;
}
/* 参照字段下拉形态（≤20 行）：与 query-ref 同宽，省去弹窗按钮 */
.query-ref-select {
  display: flex;
  width: 192px;
}
.query-ref-select :deep(.el-select) {
  width: 100%;
}
.inline-ref-select {
  width: 100%;
}
.query-ref :deep(.el-input) {
  width: 160px;
}
.query-ref :deep(.el-button) {
  width: 28px;
  min-height: 26px;
  padding: 0;
}
.field-readonly {
  width: 160px;
  min-height: 26px;
  padding: 4px 8px;
  border: 1px solid #d8dde6;
  background: #f7f8fa;
  color: #3f4b5c;
  font-size: 13px;
  line-height: 16px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.header-fields.is-draft {
  background: #fbfdff;
}
.query-dialog-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 20px;
  max-height: 520px;
  overflow-y: auto;
  padding: 2px 4px 4px;
}
.query-dialog-field {
  display: grid;
  grid-template-columns: 100px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.query-dialog-field > label {
  overflow: hidden;
  color: #4b5563;
  font-size: 13px;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.query-dialog-field :deep(.el-input),
.query-dialog-field :deep(.el-select),
.query-dialog-field :deep(.el-date-editor),
.query-dialog-field :deep(.el-input-number),
.query-dialog-field .query-ref {
  width: 100%;
}
.query-dialog-field .query-ref :deep(.el-input) {
  width: auto;
  flex: 1;
}

/* ═══════ ③ 明细区块 ═══════ */
/* 正文区：左侧单据栏 + 右主区横向并排；主区 flex:1 且 min-width:0，明细表的横向滚动留在表内 */
.body-outer {
  flex: 1;
  display: flex;
  align-items: stretch;
  min-height: 0;
  overflow: hidden;
}
.body {
  flex: 1;
  min-width: 0;
  padding: 8px 10px 0;
  min-height: 0;
  overflow: auto;
}
.report-body {
  flex: 1;
  min-height: 420px;
  padding: 0 10px 10px;
  display: flex;
  flex-direction: column;
  background: #f7f8fa;
}
/* 单据列表模式：整页只放表头主信息表，明细在 PanelxForm 详情页维护 */
.doc-list-body {
  flex: 1;
  min-height: 0;
  padding: 8px 10px 10px;
  background: #f7f8fa;
}
.doc-list-tip {
  margin-left: 6px;
  color: #8c8c8c;
  font-size: 12px;
}
.report-heading {
  min-height: 44px;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 0 4px;
  color: #1f2937;
}
.report-heading strong {
  font-size: 16px;
  font-weight: 600;
}
.report-heading span {
  color: #64748b;
  font-size: 12px;
}
.report-table {
  flex: 1;
  min-height: 360px;
  background: #fff;
}
:deep(.report-table th.el-table__cell) {
  background: #f3f6fa;
  color: #27364a;
  font-weight: 600;
  padding: 7px 0;
}
:deep(.report-table td.el-table__cell) {
  padding: 5px 0;
}
:deep(.report-table .el-table__footer-wrapper td) {
  background: #f8fafc;
  color: #1f2937;
}
.detail {
  border: 1px solid #d7dce5;
  margin-bottom: 8px;
  background: #fff;
  position: relative;
}
/* 已审批印章：跟着页签行内排（2026-11-03 修正）——此前是绝对定位盖在左上角，
   正好压在「明细/汇总」页签上，把可点的页签挡掉了；改成页签行里的一个行内标签，既不遮控件也不占新行 */
.approved-stamp {
  flex: none;
  transform: rotate(-8deg);
  color: #16a34a;
  border: 2px solid #16a34a;
  border-radius: 4px;
  padding: 0 6px;
  margin-right: 4px;
  font-size: 12px;
  font-weight: 700;
  line-height: 18px;
  background: rgba(240, 253, 244, 0.92);
  pointer-events: none;
  letter-spacing: 2px;
  box-shadow: 0 1px 3px rgba(22, 163, 74, 0.25);
}
.dt-head {
  display: flex;
  align-items: center;
  background: #f5f7fa;
  border-bottom: 1px solid #d0d7e3;
  min-height: 30px;
  padding: 0 6px;
}
.dt-tab {
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
  color: #333;
  border-right: 1px solid #d0d7e3;
  user-select: none;
  position: relative;
  /* 页签名不换行：窄屏（768）下曾被「已审批」印章挤成两行「明/细」 */
  white-space: nowrap;
  flex: none;
}
.dt-tab:hover {
  color: #0d5bd3;
}
.dt-tab.on {
  background: #fff;
  color: #0d5bd3;
  font-weight: 700;
  border: 1px solid #ccc;
  border-bottom-color: #fff;
  top: 1px;
}
.dt-ics {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 12px;
  padding-right: 4px;
}
.dt-ic {
  font-size: 12px;
  color: #555;
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
}
.dt-ic:hover {
  color: #0d5bd3;
}
.mat-cell {
  position: relative;
  display: inline-block;
  width: 100%;
}
.inline-ref-editor {
  position: relative;
  display: flex;
  align-items: center;
  width: 100%;
}
.inline-ref-editor :deep(.el-input) {
  width: 100%;
}
.inline-computed-value {
  display: block;
  min-height: 30px;
  padding: 6px 8px;
  overflow: hidden;
  color: #556171;
  line-height: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.inline-ref-editor.active :deep(.el-input__wrapper) {
  padding-right: 24px;
  box-shadow: 0 0 0 1px #4b74a6 inset;
}
.list-ref-icon {
  position: absolute;
  right: 7px;
  top: 50%;
  z-index: 2;
  width: 12px;
  height: 12px;
  transform: translateY(-50%);
  font-size: 12px;
  color: #4b74a6;
  pointer-events: none;
}
.detail :deep(.el-table td .el-input),
.detail :deep(.el-table td .el-select),
.detail :deep(.el-table td .el-date-editor),
.detail :deep(.el-table td .el-input-number) {
  width: 100%;
}
.detail :deep(.el-table td .el-input__wrapper),
.detail :deep(.el-table td .el-select__wrapper) {
  min-height: 30px;
  border-radius: 0;
  box-shadow: none;
  background: transparent;
}
.detail :deep(.el-table td .el-input.is-disabled .el-input__wrapper),
.detail :deep(.el-table td .el-input-number.is-disabled .el-input__wrapper),
.detail :deep(.el-table td .el-select__wrapper.is-disabled),
.detail :deep(.el-table td .el-textarea.is-disabled .el-textarea__inner) {
  --el-disabled-bg-color: transparent;
  background: transparent !important;
  background-color: transparent !important;
  box-shadow: none !important;
}
.detail :deep(.el-table td .el-input.is-disabled .el-input__inner),
.detail :deep(.el-table td .el-input-number.is-disabled .el-input__inner) {
  color: #556171;
  -webkit-text-fill-color: #556171;
}
:global(.panelx-list .draft-body .detail .el-input.is-disabled .el-input__wrapper),
:global(.panelx-list .draft-body .detail .el-input-number.is-disabled .el-input__wrapper),
:global(.panelx-list .draft-body .detail .el-select__wrapper.is-disabled),
:global(.panelx-list .draft-body .detail .el-textarea.is-disabled .el-textarea__inner) {
  --el-disabled-bg-color: transparent;
  background: transparent !important;
  background-color: transparent !important;
  box-shadow: none !important;
}
.detail :deep(.el-table td .el-input__wrapper:hover),
.detail :deep(.el-table td .el-select__wrapper:hover) {
  box-shadow: 0 0 0 1px #aab8ca inset;
}
.detail :deep(.el-table td .el-input-number .el-input__wrapper) {
  padding: 1px 8px;
}
.detail :deep(.el-table td .el-switch) {
  margin-left: 8px;
}
.mat-star {
  position: absolute;
  top: 2px;
  right: 2px;
  color: #e60000;
  font-weight: 700;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  user-select: none;
}
.filter-hint {
  font-size: 12px;
  color: #0d5bd3;
  margin-right: 8px;
}
:deep(.prod-selected > td.el-table__cell) {
  background: #fff !important;
}
:deep(.prod-selected > td.el-table__cell:first-child) {
  box-shadow: inset 3px 0 #7a9abe;
}
:deep(.el-table th.el-table__cell) {
  background: #f7f9fc;
  color: #333;
  font-weight: 600;
}
:deep(.el-table th .cell) {
  white-space: nowrap;
}
/* 固定 5 行：所有数据行统一 31px 高（含空占位行，占位行不渲染成矮行） */
:deep(.el-table .el-table__body td) {
  height: 31px;
  padding: 0;
  vertical-align: middle;
}
:deep(.el-table .el-table__footer-wrapper .cell) {
  font-weight: 600;
}
:deep(.el-table .sum-row td) {
  background: #f7f9fc;
  font-weight: 600;
}

/* ═══════ ④ 表尾固定条（sticky 底部：滚动明细时始终可见）═══════ */
.footer {
  position: sticky;
  bottom: 0;
  z-index: 20;
  background: #fff;
  border-top: 1px solid #d0d7e3;
  box-shadow: 0 -2px 6px rgba(0, 0, 0, 0.06);
  flex-shrink: 0;
}

/* ═══════ 表尾：备注 + 分隔线 + 审核行 ═══════ */
.remark {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: #fff;
}
.remark label {
  font-size: 12px;
  color: #444;
  white-space: nowrap;
}
.remark :deep(.el-input) {
  flex: 1;
  max-width: 620px;
}
.footer-hr {
  border-top: 1px solid #ccc;
  margin: 0 12px;
  background: #fff;
}
.audit-line {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 28px;
  padding: 8px 12px 12px;
  font-size: 12px;
  color: #555;
  background: #fff;
}

/* ═══════ 右键菜单 ═══════ */
.ctx-menu {
  position: fixed;
  z-index: 3000;
  min-width: 150px;
  background: #fff;
  border: 1px solid #d0d7e3;
  border-radius: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.14);
  padding: 4px 0;
}
.ctx-item {
  padding: 6px 14px;
  font-size: 13px;
  color: #333;
  cursor: pointer;
  user-select: none;
}
.ctx-item:hover {
  background: #f0f5ff;
  color: #0d5bd3;
}
/* 2026-08-25：灰按钮下拉项（如草稿态「生成XX」）视觉置灰 */
.ctx-item.disabled {
  color: #c0c4cc;
  cursor: not-allowed;
}
.ctx-item.disabled:hover {
  background: transparent;
  color: #c0c4cc;
}
:deep(.el-table .row-approved td) { background: #f0fdf4 !important; }
.main-grid { margin-bottom: 10px; }
.main-grid .dt-head { margin-bottom: 4px; }
.main-grid .dt-head .dt-tab.on { cursor: default; }
:deep(.main-grid .el-table .row-cur td) { background: #eaf4fe !important; }
:deep(.doc-list-body .el-table .row-cur td) { background: #eaf4fe !important; }
:deep(.main-grid .el-table .ph-row td) { height: 31px; }

@media print {
  .tools,
  .fields,
  .footer,
  .ctx-menu {
    display: none !important;
  }
  .panelx-list,
  .report-body {
    display: block;
    min-height: 0;
    padding: 0;
    background: #fff;
  }
  .report-table {
    height: auto !important;
  }
}

@media (max-width: 780px) {
  .query-dialog-fields {
    grid-template-columns: 1fr;
  }
}

/* ═══════ 移动端适配（≤768px）：触控尺寸 / 单列查询 / 表格横向滚动 ═══════ */
@media (max-width: 768px) {
  /* ① 顶部工具栏：允许换行、触控高度 ≥32px、按钮文字不溢出 */
  .tools {
    row-gap: 6px;
    padding: 6px 8px;
  }
  .toolbar-query-btn {
    min-height: 32px;
    padding: 0 10px;
  }
  .tb-group {
    min-height: 32px;
  }
  .tb-main {
    min-height: 32px;
    padding: 6px 10px;
    max-width: 132px;
  }
  .tb-main .act-name {
    display: block;
    min-width: 0;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tb-caret {
    min-height: 32px;
  }
  .tb-menu {
    min-width: 140px;
    max-height: 300px;
  }

  /* ② 右上分页区：独占一行、可换行、字号 12px 防挤压 */
  .tools-right {
    flex: 1 1 100%;
    margin-left: 0;
    flex-wrap: wrap;
    justify-content: flex-end;
    row-gap: 4px;
    font-size: 12px;
  }
  .page-btn {
    width: 30px;
    height: 30px;
    line-height: 28px;
  }

  /* ③ 查询弹窗字段：多列变单列、label 在上控件在下、间距 10px */
  .query-dialog-fields {
    grid-template-columns: 1fr;
    gap: 10px;
  }
  .query-dialog-field {
    grid-template-columns: 1fr;
    align-items: stretch;
    gap: 10px;
  }
  .query-dialog-field > label {
    text-align: left;
  }

  /* ④ 表格容器：不裁剪、不压缩，列宽溢出交给 el-table 内部横向滚动 */
  .report-body,
  .main-grid,
  .detail {
    min-width: 0;
    max-width: 100%;
  }
  .dt-head {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
  :deep(.el-table .el-table__body td) {
    height: 38px;
  }
  .detail :deep(.el-table td .el-input__wrapper),
  .detail :deep(.el-table td .el-select__wrapper) {
    min-height: 34px;
  }

  /* ⑤ 表尾审计信息区：字号 12px、允许换行 */
  .footer {
    font-size: 12px;
  }
  .remark {
    flex-wrap: wrap;
  }
  .remark :deep(.el-input) {
    min-width: 160px;
  }
  .audit-line {
    gap: 6px 12px;
    padding: 8px 10px 10px;
  }

  /* ⑥ 右键菜单：最小宽度与字号适配触屏 */
  .ctx-menu {
    min-width: 130px;
    max-width: 80vw;
    font-size: 13px;
  }
  .ctx-item {
    padding: 8px 12px;
    font-size: 13px;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}
</style>
