<!-- ImportDialog.vue — Excel 导入：选择 .xlsx/.xls，自动识别明细字段（表头匹配 dataName），预览后填入明细 -->
<template>
  <el-dialog :model-value="modelValue" title="Excel 导入" width="760px" append-to-body @update:model-value="close">
    <div class="imp-tip">
      选择 Excel 文件（.xlsx / .xls），自动识别「{{ targetLabel }}」字段并填入明细；
      Excel 第一行为字段名，需与明细字段名一致（如 产品名称、实收数量、材料编码）。
    </div>
    <div class="imp-actions">
      <input ref="fileRef" type="file" accept=".xlsx,.xls" style="display: none" @change="onFile" />
      <el-button type="primary" :icon="Upload" @click="pick">选择 Excel 文件</el-button>
      <span v-if="fileName" class="imp-file">{{ fileName }}</span>
      <span v-if="error" class="imp-err">{{ error }}</span>
    </div>
    <div v-if="rows.length" class="imp-preview">
      <div class="imp-match">
        已识别 <b>{{ matchedCols.length }}</b>/{{ totalCols }} 列 → 明细字段，共 {{ rows.length }} 行数据
        <span class="imp-sub">（未匹配列忽略；确认后追加到明细，请点击保存落库）</span>
      </div>
      <el-table :data="previewRows" size="small" border max-height="300">
        <el-table-column v-for="c in matchedCols" :key="c" :label="c" :prop="c" min-width="100" show-overflow-tooltip />
      </el-table>
    </div>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :disabled="!rows.length || !matchedCols.length" @click="doImport">导入 {{ rows.length }} 行</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Upload } from '@element-plus/icons-vue'
import * as XLSX from 'xlsx'

const props = defineProps({
  modelValue: Boolean,
  fields: { type: Array, default: () => [] }, // 明细字段定义 [{dataName,dataType}]
  targetLabel: { type: String, default: '明细' },
})
const emit = defineEmits(['update:modelValue', 'update:visible', 'imported'])

const fileRef = ref(null)
const fileName = ref('')
const error = ref('')
const matchedCols = ref([])
const totalCols = ref(0)
const rows = ref([])
const previewRows = ref([])

watch(() => props.modelValue, (v) => {
  if (v) {
    fileName.value = ''
    error.value = ''
    matchedCols.value = []
    rows.value = []
    previewRows.value = []
  }
})

function close() {
  emit('update:modelValue', false); emit('update:visible', false)
}

function pick() {
  fileRef.value && fileRef.value.click()
}

function onFile(e) {
  const file = e.target.files && e.target.files[0]
  e.target.value = ''
  if (!file) return
  fileName.value = file.name
  error.value = ''
  const reader = new FileReader()
  reader.onload = (ev) => {
    try {
      const wb = XLSX.read(ev.target.result, { type: 'array' })
      const ws = wb.Sheets[wb.SheetNames[0]]
      if (!ws) throw new Error('Excel 无工作表')
      const aoa = XLSX.utils.sheet_to_json(ws, { header: 1, defval: '' })
      if (!aoa.length) throw new Error('Excel 为空')
      // 第一行为表头：匹配明细字段 dataName
      const header = (aoa[0] || []).map((h) => String(h).trim())
      const fieldNames = (props.fields || []).map((f) => f.dataName)
      const matched = []
      for (let ci = 0; ci < header.length; ci++) {
        if (header[ci] && fieldNames.includes(header[ci])) matched.push(ci)
      }
      totalCols.value = header.length
      matchedCols.value = matched.map((i) => header[i])
      const ftype = {}
      for (const f of props.fields || []) ftype[f.dataName] = f.dataType
      const out = []
      for (let ri = 1; ri < aoa.length; ri++) {
        const r = aoa[ri]
        if (!r || r.every((v) => v === '' || v === null || v === undefined)) continue
        const obj = {}
        matched.forEach((ci) => {
          const name = header[ci]
          let v = r[ci]
          const dt = ftype[name]
          if (dt === '小数' || dt === '整数') { const n = Number(v); obj[name] = Number.isFinite(n) ? n : 0 }
          else if (dt === '是否') obj[name] = v === true || String(v).toLowerCase() === 'true' || String(v) === '1' || String(v) === '是'
          else obj[name] = v === undefined || v === null ? '' : String(v).trim()
        })
        out.push(obj)
      }
      if (!out.length) throw new Error('没有可导入的数据行')
      if (!matched.length) throw new Error('未识别任何列：Excel 表头需与明细/档案字段名一致（如 员工编码、员工名称）')
      rows.value = out
      previewRows.value = out.slice(0, 20)
      ElMessage.success('已解析 ' + out.length + ' 行，识别 ' + matchedCols.value.length + ' 列')
    } catch (err) {
      error.value = err.message || '文件解析失败'
      rows.value = []
      previewRows.value = []
    }
  }
  reader.readAsArrayBuffer(file)
}

function doImport() {
  emit('imported', rows.value)
  close()
}
</script>

<style scoped>
.imp-tip {
  font-size: 12px;
  color: #666;
  margin-bottom: 10px;
  line-height: 1.6;
}
.imp-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.imp-file {
  font-size: 13px;
  color: #1c4f8a;
}
.imp-err {
  color: #dc2626;
  font-size: 12px;
}
.imp-match {
  font-size: 13px;
  margin-bottom: 8px;
  color: #333;
}
.imp-sub {
  color: #999;
  font-size: 12px;
}
</style>