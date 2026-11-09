<!-- InvoiceWorkbench.vue — 销售开票工作台
     以「销货单」为行：勾选可开票的销货单开具发票；已开票的直接提交审批 / 审批通过 / 打印。
     替代原先的通用单据表格界面，围绕「选销货单 → 开票 → 审批 → 打印」组织。 -->
<template>
  <div class="iw-page">
    <header class="iw-head">
      <div class="iw-title">
        <h2>销售开票</h2>
        <span class="iw-sub">{{ tt('勾选已审核销货单开具发票；审批通过后可打印票面') }}</span>
      </div>
      <div class="iw-company">
        <span class="iw-c-label">{{ tt('开票方') }}</span>
        <b>{{ company.name || '—' }}</b>
        <span class="iw-c-tax">{{ company.taxNo || '—' }}</span>
      </div>
    </header>

    <div class="iw-bar">
      <el-radio-group v-model="filter" size="small" @change="load">
        <el-radio-button label="all">{{ tt('全部') }}</el-radio-button>
        <el-radio-button label="todo">{{ tt('待开票') }}</el-radio-button>
        <el-radio-button label="done">{{ tt('已开票') }}</el-radio-button>
      </el-radio-group>
      <el-input
        v-model="keyword"
        size="small"
        clearable
        style="width: 240px"
        :placeholder="tt('搜索销货单号 / 客户')"
      />
      <el-button size="small" @click="load">{{ tt('刷新') }}</el-button>
      <div class="iw-spacer"></div>
      <span class="iw-picked">{{ tt('已选') }} {{ picked.length }} {{ tt('张') }}</span>
      <el-button type="primary" size="small" :loading="issuing" :disabled="!picked.length" @click="issue">
        {{ tt('开具发票') }}
      </el-button>
      <el-button size="small" :disabled="!exportable.length" @click="exportList">
        {{ tt('导出开票清单') }}
      </el-button>
    </div>

    <el-table
      ref="tableRef"
      :data="rows"
      v-loading="loading"
      size="small"
      border
      height="calc(100vh - 260px)"
      @selection-change="onSel"
    >
      <!-- 可开票的行用于开具发票；已开票的行用于导出开票清单 / 回传票面 -->
      <el-table-column type="selection" width="45" :selectable="(row) => row['可否开票'] || !!row['发票号']" />
      <el-table-column :label="tt('销货单号')" prop="销货单号" width="150" />
      <el-table-column :label="tt('单据日期')" prop="单据日期" width="110" />
      <el-table-column :label="tt('客户')" prop="客户" min-width="160" show-overflow-tooltip />
      <el-table-column :label="tt('数量合计')" width="100" align="right">
        <template #default="{ row }">{{ fmt(row['数量合计']) }}</template>
      </el-table-column>
      <el-table-column :label="tt('价税合计')" width="120" align="right">
        <template #default="{ row }">¥{{ money(row['价税合计']) }}</template>
      </el-table-column>
      <el-table-column :label="tt('销货单状态')" width="110" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="row['销货单状态'] === '已审核' ? 'success' : 'info'">
            {{ tt(row['销货单状态'] || '-') }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="tt('发票号')" width="150">
        <template #default="{ row }">{{ row['发票号'] || '—' }}</template>
      </el-table-column>
      <el-table-column :label="tt('发票状态')" width="110" align="center">
        <template #default="{ row }">
          <el-tag v-if="row['发票状态']" size="small" :type="row['发票状态'] === '已审核' ? 'success' : 'warning'">
            {{ tt(row['发票状态']) }}
          </el-tag>
          <span v-else class="iw-dim">{{ tt('未开票') }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="tt('官方票面')" width="150" align="center">
        <template #default="{ row }">
          <template v-if="official[row['发票号']]">
            <el-link
              v-if="isPdf(official[row['发票号']])"
              type="primary"
              :underline="false"
              class="iw-file-act"
              @click="previewOfficial(row)"
            >
              {{ tt('预览') }}
            </el-link>
            <el-link type="primary" :underline="false" class="iw-file-act" @click="downloadOfficial(row)">
              {{ official[row['发票号']]['文件类型'] }} · {{ tt('下载') }}
            </el-link>
          </template>
          <el-upload
            v-else-if="row['发票状态'] === '已审核'"
            :show-file-list="false"
            :auto-upload="false"
            accept=".ofd,.pdf"
            :on-change="(f) => uploadOfficial(row, f)"
          >
            <el-button link type="primary" size="small">{{ tt('回传票面') }}</el-button>
          </el-upload>
          <span v-else class="iw-dim">—</span>
        </template>
      </el-table-column>
      <el-table-column :label="tt('操作')" width="230" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row['可否开票']" link type="primary" size="small" @click="issueOne(row)">
            {{ tt('开具发票') }}
          </el-button>
          <template v-else-if="row['发票状态']">
            <el-button v-if="row['发票状态'] === '草稿'" link type="primary" size="small" @click="approveStep(row, '提交审批')">
              {{ tt('提交审批') }}
            </el-button>
            <el-button v-if="['草稿', '审批中'].includes(row['发票状态'])" link type="primary" size="small" @click="approveStep(row, '审批通过')">
              {{ tt('审批通过') }}
            </el-button>
            <el-button v-if="row['发票状态'] === '已审核'" link type="primary" size="small" @click="printInvoice(row)">
              {{ tt('打印发票') }}
            </el-button>
          </template>
          <span v-else class="iw-dim">{{ tt(row['不可开票原因'] || '-') }}</span>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { usePanelRuntime } from '@core/panel-runtime'
import { tt } from '@/i18n'

const engine = usePanelRuntime()

const rows = ref([])
const loading = ref(false)
const issuing = ref(false)
const filter = ref('all')
const keyword = ref('')
const picked = ref([])
const tableRef = ref(null)
const company = ref({ name: '', taxNo: '' })
/** 发票号 → 官方票面文件（OFD/PDF），已回传的显示下载入口 */
const official = ref({})

const exportable = computed(() => picked.value.filter((r) => r['发票号']))

const filtered = computed(() => {
  let list = rows.value
  if (filter.value === 'todo') list = list.filter((r) => r['可否开票'])
  if (filter.value === 'done') list = list.filter((r) => r['发票号'])
  const kw = keyword.value.trim()
  if (kw) {
    list = list.filter((r) =>
      String(r['销货单号'] || '').includes(kw) || String(r['客户'] || '').includes(kw)
    )
  }
  return list
})

function money(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : '0.00'
}
function fmt(v) {
  const n = Number(v)
  return Number.isFinite(n) ? String(Math.round(n * 100) / 100) : ''
}
function onSel(sel) {
  picked.value = sel
}

async function load() {
  loading.value = true
  picked.value = []
  try {
    const list = await engine.invoiceWorkbench()
    rows.value = list || []
    await loadOfficial()
    if (tableRef.value) tableRef.value.clearSelection()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('加载开票数据失败'))
  } finally {
    loading.value = false
  }
}

async function loadCompany() {
  try {
    const info = await engine.companyInfo()
    company.value = { name: info?.['名称'] || '', taxNo: info?.['纳税人识别号'] || '' }
  } catch (e) {
    // 开票方信息取不到时不阻断
  }
}

async function issueOne(row) {
  await issueList([row])
}

async function issue() {
  const list = picked.value.filter((r) => r['可否开票'])
  if (!list.length) return ElMessage.warning(tt('请先勾选可开票的销货单'))
  await issueList(list)
}

async function issueList(list) {
  issuing.value = true
  let ok = 0
  const failed = []
  for (const row of list) {
    try {
      await engine.callButton({
        panelCode: 'SALE_INV',
        buttonName: '生成销售发票',
        formData: { 编号: row['销货单号'] },
        buttonParam: { code: row['销货单号'] },
      })
      ok++
    } catch (e) {
      failed.push(`${row['销货单号']}：${engine.errMsg(e)}`)
    }
  }
  issuing.value = false
  if (ok) ElMessage.success(tt('已开具') + ` ${ok} ` + tt('张发票（草稿），审批通过后可打印'))
  if (failed.length) ElMessage.warning(failed.slice(0, 3).join('；'))
  await load()
}

async function approveStep(row, action) {
  try {
    const res = await engine.callButton({
      panelCode: 'SALE_INVOICE',
      buttonName: action,
      formData: { 编号: row['发票号'] },
      buttonParam: { code: row['发票号'] },
    })
    const st = res?.['单据状态'] || ''
    ElMessage.success(`${tt(action)} ${tt('成功')}${st ? '（' + st + '）' : ''}`)
    await load()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('操作失败'))
  }
}

async function loadOfficial() {
  const codes = [...new Set(rows.value.map((r) => r['发票号']).filter(Boolean))]
  const map = {}
  await Promise.all(
    codes.map(async (code) => {
      try {
        const files = await engine.invoiceFileList(code)
        if (files && files.length) map[code] = files[0]
      } catch (e) {
        // 单个发票取不到不影响列表
      }
    })
  )
  official.value = map
}

function isPdf(f) {
  return String(f?.['文件类型'] || '').toUpperCase() === 'PDF'
}

async function previewOfficial(row) {
  const f = official.value[row['发票号']]
  if (!f) return
  try {
    await engine.invoiceFilePreview(f.id, f['文件名'])
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('预览失败'))
  }
}

async function downloadOfficial(row) {
  const f = official.value[row['发票号']]
  if (!f) return
  try {
    await engine.invoiceFileDownload(f.id, f['文件名'])
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('下载失败'))
  }
}

async function uploadOfficial(row, file) {
  const raw = file?.raw
  if (!raw) return
  try {
    await engine.invoiceFileUpload(row['发票号'], raw)
    ElMessage.success(tt('票面已归档') + '：' + raw.name)
    await loadOfficial()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('上传失败'))
  }
}

async function exportList() {
  const codes = exportable.value.map((r) => r['发票号'])
  if (!codes.length) return ElMessage.warning(tt('请先勾选已开票的单据'))
  try {
    const blob = await engine.invoiceExport(codes)
    const d = new Date()
    const stamp = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`
    engine.saveBlob(blob, `开票清单-${stamp}.xlsx`)
    ElMessage.success(tt('已导出') + ' ' + codes.length + ' ' + tt('张发票的开票清单'))
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('导出失败'))
  }
}

function printInvoice(row) {
  if (row['发票状态'] !== '已审核') return ElMessage.warning(tt('发票审批通过后才能打印'))
  window.open(
    `${location.origin}${location.pathname}#/print/invoice?code=${encodeURIComponent(row['发票号'])}`,
    '_blank'
  )
}

onMounted(async () => {
  await loadCompany()
  await load()
})
</script>

<style scoped>
.iw-page {
  padding: 12px 16px;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #fff;
}
.iw-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 10px;
  border-bottom: 1px solid #f0f0f0;
  margin-bottom: 10px;
}
.iw-title h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #1f1f1f;
}
.iw-sub {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  color: #8c8c8c;
}
.iw-company {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
}
.iw-c-label {
  color: #8c8c8c;
}
.iw-c-tax {
  color: #8c8c8c;
  font-family: ui-monospace, Menlo, monospace;
}
.iw-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.iw-spacer {
  flex: 1;
}
.iw-picked {
  font-size: 12px;
  color: #595959;
}
.iw-dim {
  color: #bfbfbf;
  font-size: 12px;
}
.iw-file-act + .iw-file-act {
  margin-left: 8px;
}
</style>