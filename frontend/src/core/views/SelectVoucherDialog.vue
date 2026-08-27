<!-- SelectVoucherDialog.vue — 选单弹窗：内嵌来源面板列表（勾选 + 翻页切换单据 + 生单直接生成目标单据） -->
<template>
  <el-dialog :model-value="modelValue" :title="config?.title || '选单'" width="880px" append-to-body @update:model-value="close" @open="load(1)">
    <div class="sel-tip">{{ config?.tip || '' }}</div>
    <el-table :data="rows" v-loading="loading" size="small" border height="340" @selection-change="onSel">
      <el-table-column type="selection" width="45" />
      <el-table-column label="序号" width="50" align="center">
        <template #default="{ $index }">{{ (pageNo - 1) * pageSize + $index + 1 }}</template>
      </el-table-column>
      <el-table-column
        v-for="c in columns"
        :key="c"
        :label="c"
        :width="['单据编号', '单据日期', '预完工日', '预计交货日期'].includes(c) ? 120 : undefined"
        :min-width="['存货名称', '产品名称'].includes(c) ? 180 : undefined"
      >
        <template #default="{ row }">{{ cellText(c, row) }}</template>
      </el-table-column>
      <el-table-column label="明细行" min-width="220">
        <template #default="{ row }">
          <span class="sel-item">{{ itemsText(row) }}</span>
        </template>
      </el-table-column>
    </el-table>
    <div class="sel-foot">
      <el-pagination
        small
        layout="total, prev, pager, next"
        :total="total"
        :page-size="pageSize"
        :current-page="pageNo"
        @current-change="load"
      />
      <div class="sel-actions">
        <el-button @click="close">取消</el-button>
        <el-button type="primary" :disabled="!selRows.length" :loading="generating" @click="generate">
          {{ generateLabel }}
        </el-button>
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { usePanelRuntime } from '@core/panel-runtime'

const engine = usePanelRuntime()

const props = defineProps({
  modelValue: Boolean,
  panelCode: String,     // 目标面板（生成本单，如 MANU_ORDER）
  config: Object,        // selectConfig
})
const emit = defineEmits(['update:modelValue', 'generated'])

const rows = ref([])
const total = ref(0)
const pageNo = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const selRows = ref([])
const generating = ref(false)

const columns = computed(() => props.config?.columns || [])
const generateLabel = computed(() => props.config?.generateLabel || (props.config?.generateButton ? '生单' : '确定'))

function close() {
  emit('update:modelValue', false)
}

// 列值兼容：加工单等单据编号存于「锭号」，来源行统一回退到 编号/锭号
function cellText(c, row) {
  if (row[c] !== undefined && row[c] !== null && row[c] !== '') return row[c]
  if (c === '单据编号') return row['锭号'] || row['编号'] || ''
  return row[c] ?? ''
}

// 生单映射取值（2026-08-20 修复：headerMap from='单据编号' 在 MO 行取不到 → 表头全空）：
// 与 cellText 同规则回退 锭号/编号；空值不写入 head（避免 undefined 键被 JSON 丢弃后表头空白）
function selVal(row, f) {
  if (row[f] !== undefined && row[f] !== null && row[f] !== '') return row[f]
  if (f === '单据编号') return row['锭号'] || row['编号'] || ''
  return row[f] ?? ''
}

function itemsText(row) {
  const d = row.detail
  if (d && typeof d === 'object') {
    const key = Object.keys(d)[0]
    const arr = d[key]
    if (Array.isArray(arr) && arr.length) {
      const names = arr.slice(0, 2).map((i) => i['存货名称'] || i['产品名称'] || '').filter(Boolean)
      return names.join('、') + (arr.length > 2 ? ' 等 ' + arr.length + ' 行' : '')
    }
  }
  return ''
}

function onSel(r) {
  selRows.value = r
}

async function load(p) {
  if (!props.modelValue || !props.config) return
  loading.value = true
  pageNo.value = p || 1
  try {
    // 对齐 T+ 选单前提：仅已审核来源单据
    const res = await engine.queryFormDataList({
      panelCode: props.config.source,
      condition: { ...(props.config.condition || {}), 单据状态: '已审核' },
      pageNo: pageNo.value,
      pageSize: pageSize.value,
    })
    rows.value = res.list || []
    total.value = res.totalSize || 0
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '来源单据加载失败')
  } finally {
    loading.value = false
  }
}

// 生单：对勾选的来源单据逐个调用来源面板的生成按钮（如 销售订单 -> 生成生产加工单）
async function generate() {
  const cfg = props.config
  const btn = cfg.generateButton
  if (!selRows.value.length) return
  generating.value = true
  const generated = []
  try {
    for (const r of selRows.value) {
      const no = r['编号'] || r['单据编号'] || ''
      if (!no) continue
      if (btn) {
        // 推式生单：调用源面板的生成按钮（如 销售订单 -> 生成生产加工单）
        const res = await engine.callButton({
          panelCode: cfg.source,
          buttonName: btn,
          formData: { 编号: no },
          buttonParam: {},
        })
        if (res?.gotoPanel) generated.push({ panel: res.gotoPanel, no: res['编号'], sourceNo: no })
        continue
      }
      // 普通选单：表头/明细映射 -> 目标面板保存生成（对齐 T+ 选单生单语义）
      const head = {}
      for (const m of cfg.headerMap || []) head[m.to] = selVal(r, m.from)
      const srcDetail = r.detail || {}
      const srcKey = cfg.detailKey || Object.keys(srcDetail)[0] || 'items'
      const srcArr = Array.isArray(srcDetail[srcKey]) ? srcDetail[srcKey] : []
      const items = srcArr.map((row) => {
        const o = {}
        for (const m of cfg.detailMap || []) o[m.to] = row[m.from]
        const sourceNo = r['编号'] || r['单据编号'] || r['锭号'] || ''
        o['来源面板'] = cfg.source || ''
        o['来源单号'] = sourceNo
        o['来源行号'] = srcArr.indexOf(row) + 1
        const sourceQty = cfg.sourceQuantityField || '数量'
        const targetQty = cfg.targetQuantityField || ''
        o['来源数量'] = targetQty && o[targetQty] !== undefined ? o[targetQty] : (row[sourceQty] ?? 0)
        return o
      })
      // 目标明细键 = 目标面板 detail.tabs[0].key（2026-08-20 修复：FINISH_IN tabs=items 但 detailKey=products
      // → 写入键不匹配导致表格不显示；来源键只用于从源单据取明细）
      let targetKey = srcKey
      try {
        const tcfg = await engine.getPanelConfig(props.panelCode)
        targetKey = tcfg?.detail?.tabs?.[0]?.key || srcKey
      } catch (e) { /* 目标配置不可用时回退来源键 */ }
      const formData = { ...head, detail: { [targetKey]: items } }
      const res = await engine.callButton({
        panelCode: props.panelCode,
        buttonName: '保存',
        formData,
        buttonParam: {},
      })
      if (res && res['编号']) generated.push({ panel: props.panelCode, no: res['编号'], sourceNo: no })
    }
    if (generated.length) {
      const panelNames = { MANU_ORDER: '生产加工单', PROCESS_REPORT: '工序汇报单', FINISH_IN: '产成品入库单', PU_IN: '进货单', ARRIVAL_IN: '到货单', FINISH_INSPECT: '成品报检单', INSPECTION: '检验单', DISPATCH: '工序派工单', PURCHASE_IN: '采购入库单' }
      ElMessage.success('已生成 ' + generated.length + ' 张' + (panelNames[generated[0].panel] || generated[0].panel))
      emit('generated', generated)
      close()
    } else {
      ElMessage.warning('未生成任何单据')
    }
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '生单失败')
  } finally {
    generating.value = false
  }
}
</script>

<style scoped>
.sel-tip {
  color: #666;
  font-size: 12px;
  margin-bottom: 8px;
}
.sel-item {
  color: #1c4f8a;
  font-size: 12px;
}
.sel-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 10px;
}
</style>
