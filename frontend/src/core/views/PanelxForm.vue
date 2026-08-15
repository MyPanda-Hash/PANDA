<template>
  <div class="panelx-form">
    <div class="card">
      <!-- 工具栏（T+ 分组形态） -->
      <div class="tools">
        <span v-for="g in groups" :key="g.name" class="tb-group">
          <span class="tb-main" :class="{ disabled: isDisabled(g.actions[0]) }" @click="onButton(g.actions[0])">{{ g.name }}</span>
          <el-dropdown v-if="g.actions.length > 1" @command="(a) => onButton(a)" trigger="click">
            <span class="tb-caret"><el-icon><ArrowDown /></el-icon></span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-for="a in g.actions" :key="a" :command="a" :disabled="isDisabled(a)">
                  <span class="act-name">{{ a }}</span><span v-if="SHORTCUTS[a]" class="act-sc">{{ SHORTCUTS[a] }}</span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </span>
        <span class="tb-group">
          <span class="tb-main back" @click="back">返回</span>
        </span>
        <div class="tools-right">
          <span class="pg">◁</span><span class="pg">◀</span><span class="pg">▶</span><span class="pg">▷</span>
        </div>
      </div>

      <!-- 单据标题 -->
      <div class="head">
        <div class="title">
          <span class="no">{{ isEdit ? (form['单据编号'] || form['锭号'] || form['编号'] || '') : '（新增）' }}</span>
          <el-tag v-if="form['单据状态']" size="small" :type="statusTag(form['单据状态'])">{{ form['单据状态'] }}</el-tag>
        </div>
      </div>

      <!-- 表头（3 列） -->
      <div v-loading="loading" class="fields">
        <div v-for="r in visibleMeta" :key="r.code" class="field">
          <label :title="r.name">{{ r.name }}<span v-if="r.isNotNull" class="req">*</span></label>
          <el-input v-if="isText(r)" v-model="form[r.code]" :disabled="!editable || fieldLocked(r)" :placeholder="r.name" />
          <el-input-number
            v-else-if="isNumber(r)"
            v-model="form[r.code]"
            :disabled="!editable || fieldLocked(r)"
            :controls="false"
            style="width: 100%"
          />
          <!-- 参照字段：点击弹窗拉取基础档案面板数据，勾选导入（开发约束十一-1） -->
          <div v-else-if="isRef(r)" class="ref-ctl">
            <el-input
              :model-value="refText(r, form[r.code])"
              readonly
              :disabled="!editable || fieldLocked(r)"
              :placeholder="isEdit ? '' : '点击选择'"
              @click="openRefPick(r)"
            />
            <el-button v-if="editable && !fieldLocked(r)" class="ref-btn" size="small" :icon="Search" @click="openRefPick(r)" />
          </div>
          <el-select v-else-if="isSelect(r)" v-model="form[r.code]" :disabled="!editable || fieldLocked(r)" filterable clearable allow-create style="width: 100%">
            <el-option v-for="o in r.options || []" :key="o" :label="o.label ?? o" :value="o.value ?? o" />
          </el-select>
          <el-date-picker
            v-else-if="isDate(r)"
            v-model="form[r.code]"
            :disabled="!editable || fieldLocked(r)"
            type="date"
            value-format="YYYY-MM-DD"
            style="width: 100%"
          />
          <el-switch v-else-if="isBool(r)" v-model="form[r.code]" :disabled="!editable || fieldLocked(r)" />
          <el-input v-else v-model="form[r.code]" :disabled="!editable || fieldLocked(r)" :placeholder="r.name" />
        </div>
      </div>

      <!-- 表体：三明细上下堆叠（对齐真实 T+：每区自带 明细/汇总 页签行 + 图标行 + 网格 + 分隔条） -->
      <div v-if="tabs.length" class="detail">
        <div v-for="(tab, ti) in tabs" :key="tab.key" class="detail-block">
          <div class="dt-head">
            <div class="dt-tabs">
              <span class="dt-tab" :class="{ on: (subActive[tab.key] || 'detail') === 'detail' }" @click="subActive[tab.key] = 'detail'">
                {{ tab.label }}<span v-if="tab.isRequired" class="req">*</span>
              </span>
              <span
                v-if="tab.summaryItems && tab.summaryItems.length"
                class="dt-tab"
                :class="{ on: subActive[tab.key] === 'summary' }"
                @click="subActive[tab.key] = 'summary'"
              >{{ tab.label }}汇总</span>
            </div>
            <div class="dt-actions">
              <el-button v-if="editable" size="small" :icon="Plus" @click="addDetailRow(tab)">增行</el-button>
              <span class="dt-ic" v-if="ti === 0">Ctrl+V列粘贴</span>
              <span class="dt-ic">定位</span>
              <span class="dt-ic">复制到剪贴板</span>
              <span class="dt-ic">从剪贴板粘贴</span>
              <span class="dt-ic">另存为EXCEL模板</span>
              <span class="dt-ic">批量修改</span>
              <span class="dt-ic" v-if="ti === 0">销售订单查询</span>
              <span class="dt-ic">存货中心</span>
              <span class="dt-ic" v-if="ti === 1">现存量提取</span>
              <span class="dt-ic">更多</span>
              <span class="tab-hint">{{ tabHint(tab) }}</span>
            </div>
          </div>

          <!-- 明细视图 -->
          <template v-if="(subActive[tab.key] || 'detail') === 'detail'">
            <el-table
              :data="detailData[tab.key] || []"
              size="small"
              border
              :show-summary="true"
              :summary-method="(p) => summarize(p, tab)"
              height="380"
            >
              <el-table-column v-if="tab.subTable" type="expand" width="40">
                <template #default="{ row }">
                  <div class="sub-wrap">
                    <div class="sub-head">
                      <span class="sub-title">{{ tab.subTable.label }}</span>
                      <el-button v-if="editable" size="small" :icon="Plus" @click="addSubRow(row, tab)">增行</el-button>
                    </div>
                    <el-table :data="row['子表材料'] || []" size="small" border>
                      <el-table-column label="序号" width="50" align="center">
                        <template #default="{ $index }">{{ $index + 1 }}</template>
                      </el-table-column>
                      <el-table-column v-for="sr in tab.subTable.fields" :key="sr.dataName" :label="sr.dataName" min-width="100">
                        <template #default="{ row: sr }">
                          <el-select v-if="sr.dataType === '下拉框'" v-model="sr[sr.dataName]" :disabled="!editable" filterable allow-create style="width: 100%">
                            <el-option v-for="o in sr.options || []" :key="o" :label="o" :value="o" />
                          </el-select>
                          <el-input-number v-else-if="sr.dataType === '小数' || sr.dataType === '整数'" v-model="sr[sr.dataName]" :controls="false" :disabled="!editable" style="width: 100%" />
                          <el-input v-else v-model="sr[sr.dataName]" :disabled="!editable" />
                        </template>
                      </el-table-column>
                      <el-table-column v-if="editable" label="操作" width="50" align="center">
                        <template #default="{ $index }">
                          <el-icon class="del" @click="row['子表材料'].splice($index, 1)"><Delete /></el-icon>
                        </template>
                      </el-table-column>
                    </el-table>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="序号" width="50" align="center" fixed="left">
                <template #default="{ $index }">{{ $index + 1 }}</template>
              </el-table-column>
              <el-table-column
                v-for="dr in visibleFields(tab)"
                :key="dr.dataName"
                :label="dr.dataName"
                min-width="110"
                :class-name="dr.computed ? 'computed-col' : ''"
              >
                <template #default="{ row }">
                  <template v-if="dr.dataType === '参照'">
                    <span class="ref-cell" :class="{ disabled: !editable || dr.computed }" @click="openDetailRef(dr, row, tab)">{{ drRefText(dr, row) }}</span>
                  </template>
                  <el-select v-else-if="dr.dataType === '下拉框'" v-model="row[dr.dataName]" :disabled="!editable || dr.computed" filterable allow-create style="width: 100%">
                    <el-option v-for="o in dr.options || []" :key="o" :label="o.label ?? o" :value="o.value ?? o" />
                  </el-select>
                  <el-switch v-else-if="dr.dataType === '是否'" v-model="row[dr.dataName]" :disabled="!editable || dr.computed" />
                  <el-image
                    v-else-if="dr.dataType === '图片'"
                    :src="row[dr.dataName] || ''"
                    fit="contain"
                    style="width: 34px; height: 34px"
                  >
                    <template #error>
                      <span class="img-ph">图</span>
                    </template>
                  </el-image>
                  <el-input-number
                    v-else-if="dr.dataType === '小数' || dr.dataType === '整数'"
                    v-model="row[dr.dataName]"
                    :controls="false"
                    :disabled="!editable || dr.computed"
                    style="width: 100%"
                  />
                  <el-input v-else v-model="row[dr.dataName]" :disabled="!editable || dr.computed" />
                </template>
              </el-table-column>
              <el-table-column v-if="editable" label="操作" width="50" align="center" fixed="right">
                <template #default="{ $index }">
                  <el-icon class="del" @click="detailData[tab.key].splice($index, 1)"><Delete /></el-icon>
                </template>
              </el-table-column>
            </el-table>
          </template>

          <!-- 汇总视图（真实 T+ 仅产成品明细/材料明细有汇总页签） -->
          <el-table v-else :data="summaryRows(tab)" size="small" border>
            <el-table-column prop="label" label="汇总项目" min-width="220" />
            <el-table-column prop="value" label="数值" min-width="160" align="right" />
          </el-table>

          <div v-if="ti < tabs.length - 1" class="dt-splitter"></div>
        </div>
      </div>
      <!-- 表尾备注区（对齐真实 T+：备注 + 分隔线 + 审核信息行） -->
      <div class="remark">
        <label>备注</label>
        <el-input v-model="form['备注']" :disabled="!editable" style="width: 100%" />
      </div>
      <div class="footer-hr"></div>

      <!-- 表尾（审核信息栏，对齐真实 T+ 底栏） -->
      <div class="audit-line">
        <span>制单人：{{ form['发起人编号'] || '-' }}</span>
        <span>审核人：{{ form['审核人'] || '-' }}</span>
        <span>审核日期：{{ rmtTime(form['审核日期']) }}</span>
        <span>审核时间：{{ rmtTime(form['审核时间']) }}</span>
        <span>打印次数：{{ form['打印次数'] ?? 0 }}</span>
        <span>创建时间：{{ rmtTime(form['创建时间']) }}</span>
        <span>修改时间：{{ rmtTime(form['更新时间']) }}</span>
        <span>变更人：{{ form['变更人'] || '-' }}</span>
        <span>变更日期：{{ rmtTime(form['变更日期']) }}</span>
        <span>审核机器人：{{ form['审核机器人'] || '-' }}</span>
      </div>

      <!-- 拉式选单（配置驱动：selectConfig 定义来源面板/列/字段映射，对齐 T+ 选单前提：已生效且未中止） -->
      <el-dialog v-model="selectVisible" :title="selectCrg?.title || '选单'" width="920px" append-to-body>
        <div class="sel-tip">{{ selectCrg?.tip || '选择来源单据，明细将带入当前单据' }}</div>
        <el-table
          :data="selectList"
          v-loading="selectLoading"
          size="small"
          border
          height="360"
          @selection-change="(r) => (selectRows = r)"
        >
          <el-table-column type="selection" width="45" />
          <el-table-column
            v-for="c in selectCols"
            :key="c"
            :prop="c"
            :label="c"
            :width="['单据编号', '单据日期', '客户', '预完工日', '预计交货日期'].includes(c) ? 120 : undefined"
            :min-width="['存货名称', '产品名称'].includes(c) ? 200 : undefined"
          />
          <el-table-column label="明细行" min-width="240">
            <template #default="{ row }">
              <span class="sel-item">{{ row['存货名称'] || row['产品名称'] || '' }} × {{ row['数量'] }}{{ row['销售单位'] || row['生产单位'] || '' }}（{{ row['存货编码'] || row['产品编码'] || '' }}）</span>
            </template>
          </el-table-column>
        </el-table>
        <template #footer>
          <el-button @click="selectVisible = false">取消</el-button>
          <el-button type="primary" :disabled="!selectRows.length" @click="confirmSelect">
            确定生单（{{ selectRows.length }} 行）
          </el-button>
        </template>
      </el-dialog>
    </div>
    <PanelxLogin v-model="loginVisible" @success="onPanelxLogin" />
    <RefPickDialog v-model="refVisible" :field="refPick?.field" :mode="refPick?.mode" @confirm="onRefConfirm" />
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Back, Plus, Delete, ArrowDown, Search } from '@element-plus/icons-vue'
import * as engine from '@/business/engine'
import PanelxLogin from './PanelxLogin.vue'
import RefPickDialog from './RefPickDialog.vue'
const { SHORTCUTS } = engine

const loginVisible = ref(false)

function onPanelxLogin() {
  loginVisible.value = false
  load()
}

const route = useRoute()
const router = useRouter()

const panelCode = computed(() => route.params.panelCode)
const operationName = computed(() => route.query.operationName || '新增流程')
const code = computed(() => route.query.code)
const isEdit = computed(() => !!code.value)

const form = reactive({})
const meta = ref([])
const detailDef = ref(null)
const detailData = reactive({})
const activeTab = ref('')
const subActive = reactive({})
const groups = ref([])
const loading = ref(false)
const saving = ref(false)
const payloadCache = ref(null)

// ---------- 拉式选单（配置驱动：selectConfig 定义来源面板/列/字段映射） ----------
const selectVisible = ref(false)
const selectList = ref([])
const selectLoading = ref(false)
const selectRows = ref([])
const selectCrg = ref(null)
const selectCols = computed(() => selectCrg.value?.columns || [])

async function openSelectDialog() {
  const cfg = payloadCache.value?.selectConfig
  if (!cfg) {
    ElMessage.info('演示环境暂未实现「选单」，界面与 T+ 保持一致')
    return
  }
  selectCrg.value = cfg
  selectVisible.value = true
  selectLoading.value = true
  selectRows.value = []
  try {
    // 对齐 T+ 选单前提：已生效（已审核）且未中止的来源单据
    const res = await engine.queryFormDataList({ panelCode: cfg.source, condition: { 单据状态: '已审核' }, pageNo: 1, pageSize: 100 })
    let rows = res.list || []
    // detailRows 配置时选单粒度=单据（如工序汇报单选生产加工单）：按单据编号去重，避免一张多产品单显示多行
    if (cfg.detailRows) {
      const seen = new Set()
      rows = rows.filter((r) => {
        const k = r['单据编号'] || r['编号'] || ''
        if (!k || seen.has(k)) return false
        seen.add(k)
        return true
      })
    }
    selectList.value = rows
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '来源单据加载失败')
  } finally {
    selectLoading.value = false
  }
}

function confirmSelect() {
  const cfg = selectCrg.value
  if (!cfg) return
  // 表头字段映射（headerMap：from 来源字段 → to 当前表头字段；fixed 为固定值）
  const first = selectRows.value[0] || {}
  for (const m of cfg.headerMap || []) {
    if (m.fixed !== undefined) form[m.to] = m.fixed
    else if (m.from) form[m.to] = first[m.from] ?? form[m.to]
  }
  // 来源单号回填（对齐 T+：来源单据 + 来源单号）
  const sourceNos = [...new Set(selectRows.value.map((r) => r['单据编号'] || r['编号'] || '').filter(Boolean))]
  if (sourceNos.length) {
    if (cfg.sourceNoField) form[cfg.sourceNoField] = sourceNos.join('、')
    if (form['来源单据'] === undefined && form['匹配来源单号'] === undefined) form['来源单据'] = cfg.title || '选单'
  }
  // 明细行来源：默认选中行；配置 detailRows 时（如工序汇报单选生产加工单）对每个选中单据取工序明细合并（对齐 T+ 选单带出工序行）
  let srcRows = selectRows.value
  if (cfg.detailRows) {
    const extra = []
    for (const r of selectRows.value) {
      const part = cfg.detailRows(r)
      if (Array.isArray(part)) extra.push(...part)
    }
    if (extra.length) srcRows = extra
  }
  // 明细行映射（detailMap：from → to）
  const rows = srcRows.map((r) => {
    const out = {}
    for (const m of cfg.detailMap || []) {
      out[m.to] = r[m.from] ?? ''
    }
    return out
  })
  if (rows.length && detailDef.value?.tabs?.[0]) {
    detailData[detailDef.value.tabs[0].key] = rows
  }
  selectVisible.value = false
  ElMessage.success('已带入 ' + rows.length + ' 行明细')
  applyCalc()
}
const status = computed(() => form['单据状态'] || '草稿')
const editable = computed(() => !isEdit.value || status.value === '草稿')

const STATUS_TAG = { 草稿: 'info', 已审核: 'primary', 生产中: 'warning', 已完工: 'success', 已中止: 'danger', 已关闭: 'info' }
function statusTag(s) {
  return STATUS_TAG[s] || 'info'
}

const tabs = computed(() => {
  const d = detailDef.value
  if (!d) return []
  if (Array.isArray(d.tabs)) return d.tabs
  return [{ key: 'detail', label: d.label || '明细', fields: d.fields || [], isRequired: true }]
})

function visibleFields(tab) {
  return (tab.fields || []).filter((r) => !r.hidden)
}

const visibleMeta = computed(() => (meta.value || []).filter((r) => !r.hidden))

function fieldLocked(r) {
  // 锭号：自动编码；仅勾选「是否手工修改单据编码」时草稿可改
  if (r.autoCode) return !(status.value === '草稿' && form['是否手工修改单据编码'])
  return false
}

function isDisabled(action) {
  const s = status.value
  const map = {
    保存: !editable.value,
    保存新增: !editable.value,
    保存为草稿: !editable.value,
    删除: s !== '草稿' || !isEdit.value,
    审核: s !== '草稿' || !isEdit.value,
    弃审: s !== '已审核',
    中止执行: !['已审核', '生产中', '已完工'].includes(s) || !isEdit.value,
    整单中止: !['已审核', '生产中', '已完工'].includes(s) || !isEdit.value,
    草稿: s !== '已中止',
    取消中止: s !== '已中止',
    修改: !['已审核', '生产中', '已完工'].includes(s) || !isEdit.value,
    审批情况: false,
    生成生产加工单: s !== '已审核' || !isEdit.value,
  }
  return map[action] === true
}

function isText(r) {
  return !r.dataType || r.dataType === '文本' || r.dataType === 'STRING'
}
function isNumber(r) {
  return ['小数', '整数', 'Decimal', 'Long', 'Integer', 'Double'].includes(r.dataType)
}
function isDate(r) {
  return ['日期', '时间', 'DATE', 'DateTime', 'Date'].includes(r.dataType)
}
function isBool(r) {
  return ['是否', 'Boolean', 'BOOL'].includes(r.dataType)
}
function isSelect(r) {
  return r.dataType === '下拉框' || r.dataType === '参照'
}

// ---------- 参照字段弹窗选择（开发约束十一-1：能对应基础档案的字段弹窗拉取勾选导入） ----------
const refVisible = ref(false)
const refPick = ref(null)

function isRef(r) {
  return r.dataType === '参照' && (r.ref || r.refPanel)
}

function refText(r, v) {
  if (v === undefined || v === null || v === '') return ''
  const t = engine.refLabelOf(r, v)
  return t === null || t === undefined ? String(v) : t
}

function drRefText(dr, row) {
  return refText(dr, row[dr.dataName])
}

function openRefPick(r) {
  if (!editable.value || fieldLocked(r)) return
  refPick.value = { field: r, kind: 'header', code: r.code, mode: 'header' }
  refVisible.value = true
}

function openDetailRef(dr, row, tab) {
  if (!editable.value || dr.computed) return
  refPick.value = { field: dr, kind: 'detail', row, tab, mode: 'detail' }
  refVisible.value = true
}

function onRefConfirm(rows) {
  const p = refPick.value
  if (!p || !rows.length) return
  const r = p.field
  const rp = r.ref || r
  const refField = rp.field || rp.refField
  const multi = !!(rp.multi || rp.refMulti)
  const vals = rows.map((x) => x[refField])
  if (p.kind === 'header') {
    form[p.code] = multi ? vals.join('、') : vals[0]
    const first = rows[0] || {}
    for (const m of rp.map || rp.refMap || []) {
      if (!m || first[m.from] === undefined) continue
      const to = m.to || m.from
      if (to !== p.code) form[to] = first[m.from]
    }
  } else {
    const row = p.row
    const tab = p.tab
    const maps = rp.map || rp.refMap || []
    const applyMap = (target, srcRow) => {
      for (const m of maps) {
        if (!m || srcRow[m.from] === undefined) continue
        const to = m.to || m.from
        if (to !== r.dataName) target[to] = srcRow[m.from]
      }
    }
    const rillRow = (target, srcRow) => {
      target[r.dataName] = srcRow[refField]
      applyMap(target, srcRow)
    }
    // 明细行参照：勾选 N 行一次导入 → 当前行填第一行，其余每行生成一条明细（含 refMap 带出）
    rillRow(row, rows[0] || {})
    if (rows.length > 1 && tab) {
      for (let i = 1; i < rows.length; i++) {
        const nr = addDetailRow(tab)
        rillRow(nr, rows[i])
      }
    }
  }
  refVisible.value = false
  applyCalc()
  ElMessage.success(`已导入 ${rows.length} 行${engine.refPanelName(r)}数据`)
}

function rmtTime(t) {
  if (!t) return '-'
  if (typeof t === 'number') return new Date(t).toLocaleString('zh-CN', { hour12: false })
  const s = String(t)
  return s.length > 10 ? s.slice(0, 19).replace('T', ' ') : s
}

function tabHint(tab) {
  if (tab.key === 'materials') return '需用数量 = 定额需用数量 × 产品数量 ÷ 定额生产数量；计划数量 = 需用数量 + 损耗数量（损耗数量手工录入）'
  if (tab.key === 'processes') return '金额 = 计划数量 × 工价；行「手工完工」用于工序级完工；展开行维护本工序材料'
  if (tab.key === 'products') return '产品数量为工单生产数量，材料明细按此数量展开定额'
  return ''
}

function addDetailRow(tab) {
  const rows = detailData[tab.key] || (detailData[tab.key] = [])
  const row = {}
  for (const dr of tab.fields || []) {
    if (dr.dataType === '小数' || dr.dataType === '整数') row[dr.dataName] = dr.defaultValue ?? 0
    else if (dr.dataType === '是否') row[dr.dataName] = dr.defaultValue ?? false
    else row[dr.dataName] = dr.defaultValue ?? ''
  }
  if (tab.subTable) row['子表材料'] = []
  rows.push(row)
  return row
}

function addSubRow(row, tab) {
  if (!row['子表材料']) row['子表材料'] = []
  const sr = {}
  for (const sr of tab.subTable.fields || []) {
    if (sr.dataType === '小数' || sr.dataType === '整数') sr[sr.dataName] = sr.defaultValue ?? 0
    else sr[sr.dataName] = sr.defaultValue ?? ''
  }
  row['子表材料'].push(sr)
}

// ---------- 表达式计算链 ----------

function num(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

function evaluateExpr(expr, vars) {
  const tokens = String(expr).match(/\d+(?:\.\d+)?|[+\-*/()]|[^\s+\-*/()]+/g) || []
  const output = []
  const ops = []
  const prec = { '+': 1, '-': 1, '*': 2, '/': 2 }
  for (const tk of tokens) {
    if (/^[\d.]+$/.test(tk)) {
      output.push(parseFloat(tk))
    } else if (tk in prec) {
      while (ops.length && ops[ops.length - 1] !== '(' && prec[ops[ops.length - 1]] >= prec[tk]) output.push(ops.pop())
      ops.push(tk)
    } else if (tk === '(') {
      ops.push(tk)
    } else if (tk === ')') {
      while (ops.length && ops[ops.length - 1] !== '(') output.push(ops.pop())
      if (ops[ops.length - 1] === '(') ops.pop()
    } else {
      const v = vars[tk]
      if (v === undefined) throw new Error('未知变量: ' + tk)
      output.push(num(v))
    }
  }
  while (ops.length) output.push(ops.pop())
  const stack = []
  for (const t of output) {
    if (typeof t === 'number') stack.push(t)
    else {
      const b = stack.pop()
      const a = stack.pop()
      if (a === undefined || b === undefined) return 0
      stack.push(t === '+' ? a + b : t === '-' ? a - b : t === '*' ? a * b : b === 0 ? 0 : a / b)
    }
  }
  return stack[0] ?? 0
}

function productQty() {
  const rows = detailData.products || []
  return rows.reduce((s, r) => s + num(r['数量']), 0)
}

function applyCalc() {
  for (const tab of tabs.value) {
    const rows = detailData[tab.key] || []
    for (const row of rows) {
      if (tab.key === 'processes') {
        row['工序行码'] = `GX${String(num(row['加工顺序']) || rows.indexOf(row) + 1).padStart(3, '0')}`
      }
      const vars = { ...row, 产品数量: productQty() }
      for (const rule of tab.calc || []) {
        let v
        try {
          v = evaluateExpr(rule.formula, vars)
        } catch (e) {
          v = 0
        }
        if (rule.round != null) v = Math.round(v * 10 ** rule.round) / 10 ** rule.round
        if (row[rule.target] !== v) row[rule.target] = v
      }
    }
  }
}

watch(detailData, applyCalc, { deep: true })

// ---------- 汇总 ----------

function summaryRows(tab) {
  const items = tab.summaryItems || []
  const rows = detailData[tab.key] || []
  return items.map((it) => {
    const v = rows.reduce((s, r) => s + num(r[it.field]), 0)
    return { label: it.label, value: Math.round(v * 100) / 100 }
  })
}

// 明细网格合计行（对齐 T+ 网格底部合计）
function summarize({ columns }, tab) {
  const rows = detailData[tab.key] || []
  const sums = ['合计']
  for (let i = 1; i < columns.length; i++) {
    const col = columns[i]
    if (!col) continue
    const label = String(col.label || '')
    if (i === columns.length - 1) {
      sums.push('')
      continue
    }
    const field = (tab.fields || []).find((r) => r.dataName === label)
    if (field && (field.dataType === '小数' || field.dataType === '整数')) {
      sums.push(Math.round(rows.reduce((s, r) => s + num(r[label]), 0) * 100) / 100)
    } else {
      sums.push('')
    }
  }
  return sums
}

// ---------- 校验 / 加载 / 按钮 ----------

function validate() {
  for (const r of visibleMeta.value) {
    if (r.isNotNull && (form[r.code] === undefined || form[r.code] === null || String(form[r.code]).trim() === '')) {
      return `${r.name}不能为空`
    }
  }
  for (const tab of tabs.value) {
    if (tab.isRequired && !(detailData[tab.key] || []).length) return `请至少添加一行${tab.label}`
  }
  return ''
}

async function load() {
  try {
    await engine.ensurePanelx()
  } catch (e) {
    loginVisible.value = true
    return
  }
  loading.value = true
  try {
    let payload
    if (isEdit.value) {
      payload = await engine.getFormDescriptor({ panelCode: panelCode.value, code: code.value })
    } else {
      payload = await engine.getNewFormPermMatrix({ panelCode: panelCode.value, operationName: operationName.value })
    }
    Object.keys(detailData).forEach((k) => delete detailData[k])
    const dd = payload.detailData
    if (Array.isArray(dd)) detailData.detail = dd
    else if (dd && typeof dd === 'object') Object.assign(detailData, dd)
    Object.keys(form).forEach((k) => delete form[k])
    Object.assign(form, payload.data || {})
    meta.value = payload.meta || []
    detailDef.value = payload.detail || null
    groups.value = payload.buttonGroups || []
    payloadCache.value = payload
    const firstTab = tabs.value.find((t) => t.type !== 'summary')
    if (firstTab && !activeTab.value) activeTab.value = firstTab.key
    for (const t of tabs.value) {
      if (!subActive[t.key]) subActive[t.key] = 'detail'
    }
    applyCalc()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载失败')
    back()
  } finally {
    loading.value = false
  }
}

async function onButton(action) {
  if (action === '修改') {
    ElMessage.info(isEdit.value ? '当前单据已处于编辑状态' : '请先打开单据后再修改')
    return
  }
  if (action === '放弃' || action === '取消') {
    back()
    return
  }
  // 拉式选单（配置驱动：面板配置 selectConfig 存在则弹窗，否则提示）
  if (action === '选单' || action === '选销售订单' || action === '选生产加工单') {
    if (payloadCache.value?.selectConfig) {
      openSelectDialog()
      return
    }
    ElMessage.info('演示环境暂未实现「选单」，界面与 T+ 保持一致')
    return
  }
  if (action === '新增') {
    if (isEdit.value && status.value === '草稿') {
      try {
        await ElMessageBox.confirm('当前单据尚未保存，切换新增将丢弃修改，是否继续？', '提示', { type: 'warning' })
      } catch (e) {
        return
      }
    }
    router.replace({ path: `/panelx/form/${panelCode.value}`, query: { operationName: operationName.value } })
    return
  }
  if (action === '删除') {
    try {
      await ElMessageBox.confirm(`确认删除单据 ${form['单据编号'] || form['锭号'] || form['编号']}？`, '提示', { type: 'warning' })
    } catch (e) {
      return
    }
  }
  if (['保存', '保存为草稿', '保存新增'].includes(action)) {
    const msg = validate()
    if (msg) return ElMessage.warning(msg)
  }
  if (action === '刷新') {
    await load()
    return
  }
  saving.value = true
  try {
    const rd = { ...form }
    if (tabs.value.length) rd.detail = { ...detailData }
    const res = await engine.callButton({
      panelCode: panelCode.value,
      buttonName: action,
      formData: rd,
      buttonParam: isEdit.value ? { code: form['编号'] } : {},
    })
    // 推式生单结果：跳转到生成的新单据（如 销售订单→生产加工单）
    if (res?.gotoPanel) {
      ElMessage.success(`已生成${res.gotoPanel === 'MANU_ORDER' ? '生产加工单' : res.gotoPanel}：${res['编号']}`)
      router.push({ path: `/panelx/form/${res.gotoPanel}`, query: { code: res['编号'] } })
      return
    }
    ElMessage.success(`「${action}」成功`)
    if (action === '新增流程' && !isEdit.value) {
      back()
      return
    }
    if (action === '保存新增') {
      router.replace({ path: `/panelx/form/${panelCode.value}`, query: { operationName: operationName.value } })
    } else if (['保存', '保存为草稿', '删除', '审核', '弃审', '中止执行', '整单中止', '草稿', '取消中止'].includes(action)) {
      if (action === '删除') {
        back()
      } else if (isEdit.value && res?.['编号']) {
        await load()
      } else {
        back()
      }
    }
  } catch (e) {
    const msg = engine.errMsg(e) || '按钮执行失败'
    if (msg.includes('演示环境暂未实现')) ElMessage.info(msg)
    else ElMessage.error(msg)
  } finally {
    saving.value = false
  }
}

function back() {
  router.push({ path: `/panelx/list/${panelCode.value}` })
}

onMounted(() => {
  load()
  // 从列表页「选单」入口跳转而来：加载后自动弹出选单对话框
  if (route.query.select && payloadCache.value?.selectConfig) openSelectDialog()
})

watch(() => [panelCode.value, code.value], load)
</script>

<style scoped>
.card {
  background: var(--t-card-bg);
  border-radius: 6px;
  padding: 12px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.05);
}
.tools {
  display: flex;
  align-items: center;
  gap: 0;
  flex-wrap: wrap;
  background: #f5f7fa;
  border-bottom: 1px solid #d0d7e3;
  padding: 8px 12px;
}
.tb-group {
  display: inline-flex;
  align-items: center;
  margin-right: 12px;
}
.tb-main {
  display: inline-flex;
  align-items: center;
  padding: 0;
  font-size: 13px;
  color: #222;
  background: transparent;
  cursor: pointer;
  user-select: none;
  text-decoration: none;
}
.tb-main:hover {
  color: #3788FF;
}
.tb-main.disabled {
  color: #999;
  cursor: not-allowed;
}
.tb-main.back {
  margin-left: 0;
}
.tb-caret {
  display: inline-flex;
  align-items: center;
  padding: 0 2px;
  font-size: 12px;
  color: #222;
  cursor: pointer;
  outline: none;
}
.tb-caret:hover {
  color: #3788FF;
}
.tools-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 2px;
}
.tools-right .pg {
  width: 22px;
  height: 20px;
  border: 1px solid #d0d7e3;
  background: #fff;
  color: #222;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 12px;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin: 8px 0 4px;
}
.title {
  display: flex;
  align-items: center;
  gap: 10px;
}
.title .no {
  font-size: 16px;
  font-weight: 700;
  color: #222;
}
.fields {
  display: block;
  padding: 12px;
  background: #fff;
  min-height: 0;
}
.field {
  display: inline-block;
  vertical-align: top;
  margin: 0 24px 8px 0;
}
.field label {
  display: block;
  margin-bottom: 4px;
  font-size: 13px;
  color: #222;
  text-align: left;
  white-space: nowrap;
}
.field .req {
  color: #dc2626;
}
.field .el-input,
.field .el-select,
.field .el-date-editor,
.field .ref-ctl {
  width: 160px !important;
}
.field .ref-ctl {
  display: flex;
  align-items: center;
  gap: 2px;
}.field .req {
  color: #dc2626;
}
.detail {
  margin-top: 12px;
}
.tab-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.tab-hint {
  font-size: 12px;
  color: var(--t-text-3);
}
.sub-tabs {
  margin-top: 2px;
}
.sub-wrap {
  padding: 6px 8px;
}
.sub-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.sub-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--t-text-1);
}
.del {
  cursor: pointer;
  color: #dc2626;
}
.img-ph {
  display: inline-flex;
  align-items: center;
  justiry-content: center;
  width: 30px;
  height: 30px;
  font-size: 11px;
  color: var(--t-text-3);
  border: 1px dashed var(--t-border);
  border-radius: 3px;
}
.act-name {
  display: inline-flex;
}
.act-sc {
  margin-left: 12px;
  font-size: 12px;
  color: var(--t-text-3);
}
.audit-line {
  margin-top: 12px;
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--t-text-3);
}
.ref-ctl {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: 1;
}
.ref-ctl .el-input {
  flex: 1;
}
.ref-btn {
  flex-shrink: 0;
}
.ref-cell {
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 0 6px;
  color: var(--t-primary);
  cursor: pointer;
}
.ref-cell.disabled {
  color: inherit;
  cursor: default;
}
.sel-tip {
  font-size: 12px;
  color: var(--t-text-3);
  margin-bottom: 8px;
}
.sel-item {
  margin-right: 10px;
  white-space: nowrap;
}
:deep(.computed-col) {
  background: var(--t-content-bg);
}
:deep(.el-table__footer-wrapper .cell) {
  font-weight: 600;
}
.detail-block {
  margin-bottom: 4px;
}
.dt-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 0 2px;
}
.dt-tabs {
  display: flex;
  align-items: center;
  gap: 14px;
}
.dt-tab {
  font-size: 14px;
  color: #666;
  cursor: pointer;
  padding-bottom: 3px;
  user-select: none;
}
.dt-tab.on {
  color: #3788FF;
  font-weight: 700;
  border-bottom: 2px solid #3788FF;
}
.dt-tab .req {
  color: #dc2626;
}
.dt-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: #333;
}
.dt-ic {
  color: #333;
  cursor: pointer;
}
.dt-ic:hover {
  color: #3788FF;
}
.dt-splitter {
  height: 8px;
  border-top: 1px solid #ddd;
  margin: 6px 0;
}.remark {
  margin-top: 14px;
}
.remark label {
  display: block;
  margin-bottom: 4px;
  font-size: 13px;
  color: #222;
}
.footer-hr {
  border-top: 1px solid #ccc;
  margin: 10px 0;
}
.audit-line {
  margin-top: 10px;
  display: flex;
  gap: 40px;
  flex-wrap: wrap;
  font-size: 13px;
  color: #222;
}
.dt-head {
  border-bottom: 1px solid #ccc;
  padding: 8px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: #fff;
}
.dt-tabs {
  display: flex;
  align-items: flex-end;
  gap: 2px;
}
.dt-tab {
  display: inline-block;
  padding: 4px 12px;
  cursor: pointer;
  font-size: 13px;
  color: #222;
  border: 1px solid transparent;
  border-bottom: none;
  margin-bottom: -1px;
  user-select: none;
  background: transparent;
}
.dt-tab.on {
  border: 1px solid #ccc;
  border-bottom: 1px solid #fff;
  background: #fff;
  font-weight: 700;
}
.dt-tab .req {
  color: #dc2626;
}
.dt-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: #222;
}
.dt-ic {
  color: #222;
  cursor: pointer;
  text-decoration: none;
}
.dt-ic:hover {
  color: #3788FF;
}
.dt-splitter {
  height: 10px;
  border-bottom: 1px solid #d0d7e3;
  background: #f5f7fa;
}
.detail :deep(.el-table th.el-table__cell) {
  background: #f7f9fc;
  color: #222;
  font-weight: 700;
}</style>
