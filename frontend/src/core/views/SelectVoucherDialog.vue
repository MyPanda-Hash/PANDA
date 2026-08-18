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
import * as engine from '@/business/engine'

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
const generateLabel = computed(() => props.config?.generateLabel || '生单')

function close() {
  emit('update:modelValue', false)
}

// 列值兼容：加工单等单据编号存于「锭号」，来源行统一回退到 编号/锭号
function cellText(c, row) {
  if (row[c] !== undefined && row[c] !== null && row[c] !== '') return row[c]
  if (c === '单据编号') return row['锭号'] || row['编号'] || ''
  return row[c] ?? ''
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
      condition: { 单据状态: '已审核' },
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
  if (!btn) {
    ElMessage.warning('该面板未配置生单按钮（selectConfig.generateButton）')
    return
  }
  if (!selRows.value.length) return
  generating.value = true
  const generated = []
  try {
    for (const r of selRows.value) {
      const no = r['编号'] || r['单据编号'] || ''
      if (!no) continue
      const res = await engine.callButton({
        panelCode: cfg.source,
        buttonName: btn,
        formData: { 编号: no },
        buttonParam: {},
      })
      if (res?.gotoPanel) generated.push({ panel: res.gotoPanel, no: res['编号'], sourceNo: no })
    }
    if (generated.length) {
      const panelNames = { MANU_ORDER: '生产加工单', PROCESS_REPORT: '工序汇报单', FINISH_IN: '产成品入库单' }
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
