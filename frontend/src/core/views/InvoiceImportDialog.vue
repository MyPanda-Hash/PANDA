<!-- InvoiceImportDialog.vue — 采购发票 Excel 导入：按「发票号码 + 供应商」分组生成发票草稿 -->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="tt('导入发票')"
    width="min(1080px, 96vw)"
    append-to-body
    :close-on-click-modal="!saving"
    :close-on-press-escape="!saving"
    @update:model-value="close"
    @open="reset"
  >
    <div class="inv-tip">
      {{ tt('选择 Excel 文件（.xlsx / .xls），第一行为表头；同一「发票号码 + 供应商」的多行合并为一张发票。') }}
    </div>
    <div class="inv-tip inv-tip-sub">
      {{ tt('模板列：发票代码、发票号码、开票日期、供应商、供应商编码、业务类型、开票类型、来源单据号、入库单号、存货编码、存货名称、规格型号、计量单位、数量、无税单价、税率%、税额、价税合计、来源面板、来源单号、来源行号。') }}
    </div>

    <div class="inv-actions">
      <input ref="fileRef" type="file" accept=".xlsx,.xls" style="display: none" @change="onFile" />
      <el-button type="primary" :icon="Upload" :disabled="saving" @click="pick">{{ tt('选择 Excel 文件') }}</el-button>
      <span v-if="fileName" class="inv-file">{{ fileName }}</span>
      <span v-if="error" class="inv-err">{{ error }}</span>
    </div>

    <div v-if="groups.length" class="inv-preview">
      <div class="inv-match">
        {{ tt('已解析') }} {{ totalRows }} {{ tt('行') }} → {{ groups.length }} {{ tt('张发票草稿') }}，
        {{ tt('可导入') }} {{ importableGroups.length }} {{ tt('张') }}
        <span v-if="invalidCount" class="inv-sub">（{{ invalidCount }} {{ tt('张存在校验错误') }}）</span>
      </div>

      <el-table :data="groups" size="small" border max-height="320" row-key="key">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="inv-lines">
              <el-table :data="row.rows" size="small" border max-height="220">
                <el-table-column prop="存货编码" :label="tt('存货编码')" width="110" show-overflow-tooltip />
                <el-table-column prop="存货名称" :label="tt('存货名称')" min-width="150" show-overflow-tooltip />
                <el-table-column prop="规格型号" :label="tt('规格型号')" width="100" show-overflow-tooltip />
                <el-table-column prop="计量单位" :label="tt('计量单位')" width="80" align="center" />
                <el-table-column :label="tt('数量')" width="90" align="right">
                  <template #default="{ row: line }"><span class="inv-num">{{ numberText(line['数量']) }}</span></template>
                </el-table-column>
                <el-table-column :label="tt('无税单价')" width="96" align="right">
                  <template #default="{ row: line }"><span class="inv-num">{{ numberText(line['无税单价']) }}</span></template>
                </el-table-column>
                <el-table-column :label="tt('税率%')" width="76" align="right">
                  <template #default="{ row: line }"><span class="inv-num">{{ numberText(line['税率%']) }}</span></template>
                </el-table-column>
                <el-table-column :label="tt('税额')" width="96" align="right">
                  <template #default="{ row: line }"><span class="inv-num">{{ numberText(line['税额']) }}</span></template>
                </el-table-column>
                <el-table-column :label="tt('价税合计')" width="108" align="right">
                  <template #default="{ row: line }"><span class="inv-num">{{ numberText(line['价税合计']) }}</span></template>
                </el-table-column>
                <el-table-column prop="来源单号" :label="tt('来源单号')" width="120" show-overflow-tooltip />
                <el-table-column prop="来源行号" :label="tt('来源行号')" width="86" align="right" />
                <el-table-column :label="tt('校验')" min-width="170">
                  <template #default="{ row: line }">
                    <span v-if="line._errors.length" class="inv-err">{{ line._errors.join('；') }}</span>
                    <span v-else class="inv-ok">{{ tt('通过') }}</span>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="invoiceNo" :label="tt('发票号码')" width="130" show-overflow-tooltip />
        <el-table-column prop="supplier" :label="tt('供应商')" min-width="150" show-overflow-tooltip />
        <el-table-column prop="businessType" :label="tt('业务类型')" width="112" show-overflow-tooltip />
        <el-table-column prop="date" :label="tt('开票日期')" width="108" />
        <el-table-column :label="tt('明细行数')" width="90" align="right">
          <template #default="{ row }">{{ row.rows.length }}</template>
        </el-table-column>
        <el-table-column :label="tt('价税合计')" width="118" align="right">
          <template #default="{ row }"><span class="inv-num">{{ numberText(row.totalGross) }}</span></template>
        </el-table-column>
        <el-table-column :label="tt('状态')" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.savedNo" class="inv-ok">{{ tt('已导入') }} {{ row.savedNo }}</span>
            <span v-else-if="row.errors.length" class="inv-err">{{ tt('校验未通过') }}</span>
            <span v-else class="inv-ok">{{ tt('可导入') }}</span>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="issueRows.length" class="inv-block">
        <div class="inv-block-title">{{ tt('校验未通过明细') }}</div>
        <el-table :data="issueRows" size="small" border max-height="180">
          <el-table-column prop="invoiceNo" :label="tt('发票号码')" width="130" show-overflow-tooltip />
          <el-table-column prop="supplier" :label="tt('供应商')" min-width="140" show-overflow-tooltip />
          <el-table-column prop="excelRow" :label="tt('Excel 行')" width="90" align="right" />
          <el-table-column prop="message" :label="tt('错误信息')" min-width="240" />
        </el-table>
      </div>

      <div v-if="failureRows.length" class="inv-block">
        <div class="inv-block-title">{{ tt('保存失败') }}</div>
        <el-table :data="failureRows" size="small" border max-height="180">
          <el-table-column prop="invoiceNo" :label="tt('发票号码')" width="130" show-overflow-tooltip />
          <el-table-column prop="supplier" :label="tt('供应商')" min-width="140" show-overflow-tooltip />
          <el-table-column prop="message" :label="tt('错误信息')" min-width="280" />
        </el-table>
      </div>
    </div>

    <template #footer>
      <div class="inv-foot">
        <span v-if="groups.length" class="inv-foot-tip">
          {{ tt('共') }} {{ groups.length }} {{ tt('张发票，待导入') }} {{ importableGroups.length }} {{ tt('张') }}
        </span>
        <el-button :disabled="saving" @click="close">{{ tt('取消') }}</el-button>
        <el-button type="primary" :loading="saving" :disabled="!importableGroups.length" @click="doImport">
          {{ tt('导入') }} {{ importableGroups.length }} {{ tt('张发票') }}
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Upload } from '@element-plus/icons-vue'
import * as XLSX from 'xlsx'
import { usePanelRuntime } from '@core/panel-runtime'
import { tt } from '@/i18n'

const engine = usePanelRuntime()

const props = defineProps({
  modelValue: Boolean,
  panelCode: { type: String, default: 'PU_INVOICE' },
})
const emit = defineEmits(['update:modelValue', 'imported'])

const fileRef = ref(null)
const fileName = ref('')
const error = ref('')
const groups = ref([])
const totalRows = ref(0)
const saving = ref(false)
const failureRows = ref([])

/** 模板列（首行表头）→ 发票字段；别名用于容忍列名空格/大小写/全角括号差异 */
const COLUMN_ALIASES = {
  发票代码: ['发票代码'],
  发票号码: ['发票号码', '发票号', '票号'],
  开票日期: ['开票日期', '开票时间', '日期'],
  供应商: ['供应商', '供应商名称', '往来单位', '往来单位名称'],
  供应商编码: ['供应商编码', '供应商代码', '往来单位编码'],
  业务类型: ['业务类型', '业务类别'],
  开票类型: ['开票类型', '发票类型'],
  来源单据号: ['来源单据号', '来源单据'],
  入库单号: ['入库单号', '入库单据号'],
  存货编码: ['存货编码', '物料编码', '产品编码'],
  存货名称: ['存货名称', '物料名称', '产品名称', '存货', '物料'],
  规格型号: ['规格型号', '规格', '型号'],
  计量单位: ['计量单位', '单位'],
  数量: ['数量', '开票数量'],
  无税单价: ['无税单价', '不含税单价', '单价'],
  '税率%': ['税率%', '税率'],
  税额: ['税额', '税金'],
  价税合计: ['价税合计', '含税金额'],
  来源面板: ['来源面板', '来源单据类型', '源单类型'],
  来源单号: ['来源单号', '源单编号', '来源单据编号'],
  来源行号: ['来源行号', '源单行号'],
}

/** 提交给后端的明细字段（与行级选来源单 buildInvoiceLineRows 同口径） */
const LINE_FIELDS = [
  '存货编码', '存货名称', '规格型号', '计量单位', '数量', '无税单价', '税率%', '税额', '金额', '价税合计',
  '来源面板', '来源单号', '来源行号',
]

const ALIAS_INDEX = new Map()
for (const [field, aliases] of Object.entries(COLUMN_ALIASES)) {
  for (const alias of aliases) ALIAS_INDEX.set(normalizeHeader(alias), field)
}

const importableGroups = computed(() => groups.value.filter((group) => !group.savedNo && !group.errors.length))
const invalidCount = computed(() => groups.value.filter((group) => group.errors.length).length)

const issueRows = computed(() => {
  const rows = []
  for (const group of groups.value) {
    for (const issue of group.errors) {
      rows.push({
        key: `${group.key}|${issue.excelRow || 0}|${issue.message}`,
        invoiceNo: group.invoiceNo || tt('(空)'),
        supplier: group.supplier || tt('(空)'),
        excelRow: issue.excelRow || '',
        message: issue.message,
      })
    }
  }
  return rows
})

function normalizeHeader(value) {
  return String(value ?? '')
    .replace(/\uFEFF/g, '')
    .replace(/[\s\u3000]/g, '')
    .replace(/[（(]/g, '')
    .replace(/[）)]/g, '')
    .replace(/％/g, '%')
    .toLowerCase()
}

function pad2(value) {
  return String(value).padStart(2, '0')
}

/** Excel 日期单元格（cellDates）或常见日期文本 → YYYY-MM-DD；无法识别时保留原文 */
function dateText(value) {
  if (value instanceof Date && !Number.isNaN(value.getTime())) {
    return `${value.getFullYear()}-${pad2(value.getMonth() + 1)}-${pad2(value.getDate())}`
  }
  if (typeof value === 'number' && Number.isFinite(value) && value > 20000 && value < 80000) {
    const parsed = XLSX.SSF.parse_date_code(value)
    if (parsed) return `${parsed.y}-${pad2(parsed.m)}-${pad2(parsed.d)}`
  }
  const text = String(value ?? '').trim()
  const matched = text.match(/^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?/)
  if (matched) return `${matched[1]}-${pad2(matched[2])}-${pad2(matched[3])}`
  return text
}

function textOf(value) {
  if (value === null || value === undefined) return ''
  if (value instanceof Date) return dateText(value)
  return String(value).trim()
}

/** 数字单元格：空值与非法值分开返回，便于校验提示区分「必填」与「必须为数字」 */
function numberCell(value) {
  if (value === null || value === undefined || value === '') return { ok: false, blank: true, value: 0 }
  if (typeof value === 'number') {
    return Number.isFinite(value) ? { ok: true, blank: false, value } : { ok: false, blank: false, value: 0 }
  }
  const text = String(value).replace(/[\s\u3000,，￥¥%]/g, '')
  if (!text) return { ok: false, blank: true, value: 0 }
  const number = Number(text)
  return Number.isFinite(number) ? { ok: true, blank: false, value: number } : { ok: false, blank: false, value: 0 }
}

function round(value, digits) {
  const number = Number(value)
  if (!Number.isFinite(number)) return 0
  const factor = 10 ** digits
  return Math.round(number * factor) / factor
}

function numberText(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return value === undefined || value === null ? '' : String(value)
  return number.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
}

function reset() {
  fileName.value = ''
  error.value = ''
  groups.value = []
  totalRows.value = 0
  failureRows.value = []
  saving.value = false
}

function close() {
  if (saving.value) return
  emit('update:modelValue', false)
}

function pick() {
  if (saving.value) return
  fileRef.value && fileRef.value.click()
}

function onFile(event) {
  const file = event.target.files && event.target.files[0]
  event.target.value = ''
  if (!file || saving.value) return
  fileName.value = file.name
  error.value = ''
  groups.value = []
  totalRows.value = 0
  failureRows.value = []
  const reader = new FileReader()
  reader.onload = (ev) => {
    try {
      const workbook = XLSX.read(ev.target.result, { type: 'array', cellDates: true })
      const sheet = workbook.Sheets[workbook.SheetNames[0]]
      if (!sheet) throw new Error(tt('Excel 无工作表'))
      // blankrows: true 保留空行占位，行号才能与 Excel 行号一一对应（空行在 parseRows 跳过）
      const aoa = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: '', blankrows: true })
      if (!aoa.length) throw new Error(tt('Excel 为空'))
      const rows = parseRows(aoa)
      groups.value = buildGroups(rows)
      totalRows.value = rows.length
      ElMessage.success(tt('已解析') + ' ' + rows.length + ' ' + tt('行') + tt('，生成') + ' ' + groups.value.length + ' ' + tt('张发票草稿'))
    } catch (err) {
      error.value = err.message || tt('文件解析失败')
      groups.value = []
      totalRows.value = 0
    }
  }
  reader.onerror = () => {
    error.value = tt('文件读取失败')
  }
  reader.readAsArrayBuffer(file)
}

/** 第一行为表头；返回只含已识别列的数据行，并记录 Excel 行号用于定位错误 */
function parseRows(aoa) {
  const header = (aoa[0] || []).map((cell) => normalizeHeader(cell))
  const columnOf = {}
  header.forEach((name, index) => {
    const field = ALIAS_INDEX.get(name)
    if (field && columnOf[field] === undefined) columnOf[field] = index
  })
  const fields = Object.keys(columnOf)
  if (!fields.length) {
    throw new Error(tt('未识别任何列：第一行需为表头（如 发票号码、供应商、存货名称、数量）'))
  }
  const rows = []
  for (let index = 1; index < aoa.length; index += 1) {
    const raw = aoa[index] || []
    const row = { _excelRow: index + 1 }
    let filled = false
    for (const field of fields) {
      const value = raw[columnOf[field]]
      row[field] = value === undefined || value === null ? '' : value
      if (!filled && textOf(value) !== '') filled = true
    }
    if (!filled) continue
    rows.push(row)
  }
  if (!rows.length) throw new Error(tt('没有可导入的数据行'))
  return rows
}

function buildLine(raw) {
  const quantity = numberCell(raw['数量'])
  const price = numberCell(raw['无税单价'])
  const rate = numberCell(raw['税率%'])
  const tax = numberCell(raw['税额'])
  const gross = numberCell(raw['价税合计'])
  const sourceLine = numberCell(raw['来源行号'])
  return {
    _excelRow: raw._excelRow,
    _quantity: quantity,
    _price: price,
    _rate: rate,
    _tax: tax,
    _gross: gross,
    _sourceLine: sourceLine,
    _errors: [],
    存货编码: textOf(raw['存货编码']),
    存货名称: textOf(raw['存货名称']),
    规格型号: textOf(raw['规格型号']),
    计量单位: textOf(raw['计量单位']),
    数量: quantity.ok ? round(quantity.value, 4) : 0,
    无税单价: price.ok ? round(price.value, 4) : 0,
    '税率%': rate.ok ? round(rate.value, 2) : 0,
    税额: tax.ok ? round(tax.value, 2) : 0,
    价税合计: gross.ok ? round(gross.value, 2) : 0,
    来源面板: textOf(raw['来源面板']),
    来源单号: textOf(raw['来源单号']),
    来源行号: sourceLine.ok ? Math.round(sourceLine.value) : 0,
  }
}

function buildGroups(rows) {
  const map = new Map()
  for (const raw of rows) {
    const invoiceNo = textOf(raw['发票号码'])
    const supplier = textOf(raw['供应商'])
    const key = `${invoiceNo}\u0000${supplier}`
    let group = map.get(key)
    if (!group) {
      group = {
        key,
        invoiceNo,
        supplier,
        supplierCode: '',
        invoiceCode: '',
        date: '',
        sourceDocNo: '',
        inboundNo: '',
        invoiceType: '',
        businessTypes: [],
        businessType: '采购发票',
        rows: [],
        totalGross: 0,
        errors: [],
        savedNo: '',
      }
      map.set(key, group)
    }
    // 表头取组内第一个非空值；业务类型冲突在 validateGroup 单独报错
    if (!group.supplierCode) group.supplierCode = textOf(raw['供应商编码'])
    if (!group.invoiceCode) group.invoiceCode = textOf(raw['发票代码'])
    if (!group.date) group.date = dateText(raw['开票日期'])
    if (!group.sourceDocNo) group.sourceDocNo = textOf(raw['来源单据号'])
    if (!group.inboundNo) group.inboundNo = textOf(raw['入库单号'])
    if (!group.invoiceType) group.invoiceType = textOf(raw['开票类型'])
    const businessType = textOf(raw['业务类型'])
    if (businessType && !group.businessTypes.includes(businessType)) group.businessTypes.push(businessType)
    group.rows.push(buildLine(raw))
  }
  for (const group of map.values()) {
    if (group.businessTypes.length) group.businessType = group.businessTypes[0]
    validateGroup(group)
  }
  return [...map.values()]
}

/** 蓝票缺价税合计/税额时按数量、无税单价、税率补算；红票不补算，按负数校验 */
function resolveAmounts(line, red) {
  const quantity = line._quantity
  const price = line._price
  const rate = line._rate
  let gross = line._gross.ok ? round(line._gross.value, 2) : null
  let tax = line._tax.ok ? round(line._tax.value, 2) : null
  if (!red) {
    if (gross === null && quantity.ok && quantity.value !== 0) {
      const unitPrice = price.ok ? price.value : 0
      if (unitPrice) {
        const taxPrice = unitPrice * (1 + (rate.ok ? rate.value : 0) / 100)
        gross = round(quantity.value * taxPrice, 2)
      }
    }
    if (tax === null && gross !== null) {
      const rateValue = rate.ok ? rate.value : 0
      tax = rateValue > 0 ? round(gross * rateValue / (100 + rateValue), 2) : 0
    }
  }
  const grossValue = gross === null ? 0 : gross
  const taxValue = tax === null ? 0 : tax
  line.价税合计 = grossValue
  line.税额 = taxValue
  line.金额 = round(grossValue - taxValue, 2)
}

function validateGroup(group) {
  const red = group.businessType.includes('退货')
  if (!group.invoiceNo) group.errors.push({ excelRow: null, message: tt('发票号码不能为空') })
  if (!group.supplier) group.errors.push({ excelRow: null, message: tt('供应商不能为空') })
  if (group.businessTypes.length > 1) {
    group.errors.push({ excelRow: null, message: tt('同一发票的业务类型不一致：') + group.businessTypes.join(' / ') })
  }
  let totalGross = 0
  for (const line of group.rows) {
    resolveAmounts(line, red)
    totalGross += Number(line.价税合计) || 0
    const errors = []
    if (!line.存货名称) errors.push(tt('存货名称不能为空'))
    if (line._quantity.blank) errors.push(tt('数量不能为空'))
    else if (!line._quantity.ok) errors.push(tt('数量必须为数字'))
    for (const [field, cell] of [['无税单价', line._price], ['税率%', line._rate], ['税额', line._tax], ['价税合计', line._gross]]) {
      if (!cell.blank && !cell.ok) errors.push(field + tt('必须为数字'))
    }
    if (red) {
      if (line._quantity.ok && !(line._quantity.value < 0)) errors.push(tt('红字发票数量必须为负数'))
      if (!line._tax.ok || !(line._tax.value < 0)) errors.push(tt('红字发票税额必须为负数'))
      if (!line._gross.ok || !(line._gross.value < 0)) errors.push(tt('红字发票价税合计必须为负数'))
    }
    const hasSource = line.来源面板 || line.来源单号 || !line._sourceLine.blank
    if (hasSource) {
      if (!line.来源面板) errors.push(tt('来源面板不能为空'))
      if (!line.来源单号) errors.push(tt('来源单号不能为空'))
      if (!line._sourceLine.ok || !Number.isInteger(line._sourceLine.value) || line._sourceLine.value <= 0) {
        errors.push(tt('来源行号必须为正整数'))
      }
    }
    line._errors = errors
    for (const message of errors) group.errors.push({ excelRow: line._excelRow, message })
  }
  group.totalGross = round(totalGross, 2)
}

function lineRow(line) {
  const row = {}
  for (const field of LINE_FIELDS) row[field] = line[field]
  row.源单类型 = line.来源面板
  row.源单编号 = line.来源单号
  row.源单行号 = line.来源行号
  return row
}

function invoiceFormData(group) {
  return {
    业务类型: group.businessType,
    供应商: group.supplier,
    供应商编码: group.supplierCode,
    发票代码: group.invoiceCode,
    发票号码: group.invoiceNo,
    开票类型: group.invoiceType,
    开票日期: group.date,
    来源单据号: group.sourceDocNo,
    入库单号: group.inboundNo,
    detail: { items: group.rows.map(lineRow) },
  }
}

/** 后端「第 N 行」指发票明细行序，换算成 Excel 行号，便于用户直接定位 */
function describeSaveError(group, message) {
  const text = String(message || '')
  const matched = text.match(/第\s*(\d+)\s*行/)
  if (!matched) return text
  const line = group.rows[Number(matched[1]) - 1]
  if (!line) return text
  return text.replace(
    matched[0],
    tt('第') + ' ' + matched[1] + ' ' + tt('行') + '（Excel ' + tt('第') + ' ' + line._excelRow + ' ' + tt('行') + '）',
  )
}

async function doImport() {
  const pending = importableGroups.value
  if (!pending.length || saving.value) return
  saving.value = true
  error.value = ''
  failureRows.value = []
  const invoices = []
  const failed = []
  for (const group of pending) {
    try {
      const res = await engine.callButton({
        panelCode: props.panelCode || 'PU_INVOICE',
        buttonName: '保存',
        formData: invoiceFormData(group),
        buttonParam: {},
      })
      group.savedNo = String(res?.['编号'] || res?.formNo || '')
      invoices.push({ 发票号码: group.invoiceNo, 供应商: group.supplier, 编号: group.savedNo })
    } catch (e) {
      const message = describeSaveError(group, engine.errMsg(e) || tt('保存失败'))
      failed.push({ invoiceNo: group.invoiceNo, supplier: group.supplier, message })
      failureRows.value.push({
        key: group.key,
        invoiceNo: group.invoiceNo || tt('(空)'),
        supplier: group.supplier || tt('(空)'),
        message,
      })
    }
  }
  saving.value = false
  const count = invoices.length
  if (count) emit('imported', { count, invoices, failed })
  if (!failed.length) {
    ElMessage.success(tt('已导入') + ' ' + count + ' ' + tt('张发票'))
    close()
    return
  }
  if (count) ElMessage.warning(tt('已导入') + ' ' + count + ' ' + tt('张发票') + tt('，') + failed.length + ' ' + tt('张保存失败'))
  else ElMessage.error(tt('导入失败') + tt('，') + failed.length + ' ' + tt('张发票未保存'))
}
</script>

<style scoped>
.inv-tip {
  font-size: 12px;
  color: #666;
  margin-bottom: 6px;
  line-height: 1.6;
}
.inv-tip-sub {
  color: #999;
  margin-bottom: 10px;
}
.inv-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
}
.inv-file {
  font-size: 13px;
  color: var(--d-primary);
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.inv-err {
  color: var(--d-bad);
  font-size: 12px;
}
.inv-ok {
  color: var(--d-ok);
  font-size: 12px;
}
.inv-match {
  font-size: 13px;
  margin-bottom: 8px;
  color: #333;
}
.inv-sub {
  color: #999;
  font-size: 12px;
}
.inv-num {
  font-variant-numeric: tabular-nums;
}
.inv-lines {
  padding: 6px 8px;
  background: var(--d-fill-soft);
}
.inv-block {
  margin-top: 12px;
}
.inv-block-title {
  font-size: 12px;
  color: #666;
  margin-bottom: 6px;
}
.inv-foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}
.inv-foot-tip {
  margin-right: auto;
  font-size: 12px;
  color: #666;
  font-variant-numeric: tabular-nums;
}
</style>
