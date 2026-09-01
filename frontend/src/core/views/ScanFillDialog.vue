<template>
  <el-dialog
    v-model="visible"
    :title="tt('扫描填单')"
    width="min(920px, 94vw)"
    append-to-body
    destroy-on-close
    class="scan-fill-dialog"
    :close-on-click-modal="!recognizing"
    :close-on-press-escape="!recognizing"
    :show-close="!recognizing"
    @closed="reset"
  >
    <input
      ref="cameraInput"
      class="scan-file-input"
      type="file"
      accept="image/*"
      capture="environment"
      @change="onFileSelected"
    />
    <input
      ref="uploadInput"
      class="scan-file-input"
      type="file"
      accept="image/jpeg,image/png,image/bmp,.jpg,.jpeg,.png,.bmp"
      @change="onFileSelected"
    />

    <div class="scan-source-bar">
      <el-button type="primary" :icon="Camera" :disabled="recognizing" @click="cameraInput?.click()">{{ tt('拍照') }}</el-button>
      <el-button :icon="Upload" :disabled="recognizing" @click="uploadInput?.click()">{{ tt('上传图片') }}</el-button>
      <span v-if="file" class="scan-file-name" :title="file.name">{{ file.name }}</span>
    </div>

    <div v-if="file" class="scan-preview-row">
      <div class="scan-image-wrap">
        <img v-if="previewUrl" :src="previewUrl" :alt="tt('待识别单据')" />
        <div v-else class="scan-image-placeholder">{{ file.name }}</div>
      </div>
      <div class="scan-file-meta">
        <strong>{{ tt(panelName || panelCode) }}</strong>
        <span>{{ formatBytes(file.size) }}</span>
        <span v-if="result?.requestId">{{ tt('请求编号：') }}{{ result.requestId }}</span>
      </div>
    </div>

    <el-alert
      v-if="errorMessage"
      class="scan-alert"
      type="error"
      :title="tt(errorMessage)"
      :closable="false"
      show-icon
    />

    <template v-if="result">
      <el-alert
        v-if="warnings.length"
        class="scan-alert"
        type="warning"
        :title="warnings.join('；')"
        :closable="false"
        show-icon
      />

      <section v-if="headerEntries.length" class="scan-section">
        <div class="scan-section-title">{{ tt('表头字段') }}</div>
        <div class="scan-header-grid">
          <label v-for="entry in headerEntries" :key="entry.key" class="scan-header-field">
            <span>{{ tt(entry.key) }}</span>
            <el-switch v-if="fieldType(entry.key) === 'boolean'" v-model="header[entry.key]" />
            <el-input-number
              v-else-if="fieldType(entry.key) === 'number'"
              v-model="header[entry.key]"
              :controls="false"
            />
            <el-date-picker
              v-else-if="fieldType(entry.key) === 'date'"
              v-model="header[entry.key]"
              type="date"
              value-format="YYYY-MM-DD"
            />
            <el-input v-else v-model="header[entry.key]" />
          </label>
        </div>
      </section>

      <section v-for="tab in detailSections" :key="tab.key" class="scan-section">
        <div class="scan-section-heading">
          <div class="scan-section-title">{{ tt(tab.label) }}</div>
          <el-radio-group v-model="detailMode" size="small">
            <el-radio-button value="replace">{{ tt('替换明细') }}</el-radio-button>
            <el-radio-button value="append">{{ tt('追加明细') }}</el-radio-button>
          </el-radio-group>
        </div>
        <el-table :data="tab.rows" border size="small" max-height="300">
          <el-table-column type="index" :label="tt('序号')" width="58" fixed="left" />
          <el-table-column v-for="column in tab.columns" :key="column" :label="tt(column)" min-width="130">
            <template #default="{ row }">
              <el-switch v-if="detailFieldType(tab.key, column) === 'boolean'" v-model="row[column]" />
              <el-input-number
                v-else-if="detailFieldType(tab.key, column) === 'number'"
                v-model="row[column]"
                :controls="false"
              />
              <el-date-picker
                v-else-if="detailFieldType(tab.key, column) === 'date'"
                v-model="row[column]"
                type="date"
                value-format="YYYY-MM-DD"
              />
              <el-input v-else v-model="row[column]" />
            </template>
          </el-table-column>
          <el-table-column :label="tt('操作')" width="54" fixed="right" align="center">
            <template #default="{ $index }">
              <el-button
                link
                type="danger"
                :icon="Delete"
                :title="tt('删除本行')"
                @click="tab.rows.splice($index, 1)"
              />
            </template>
          </el-table-column>
        </el-table>
      </section>

      <el-empty v-if="!headerEntries.length && !detailSections.length" :description="tt('未匹配到当前单据字段')" :image-size="64" />

    </template>

    <template #footer>
      <el-button :disabled="recognizing" @click="visible = false">{{ tt('取消') }}</el-button>
      <el-button
        v-if="file"
        type="primary"
        :icon="DocumentChecked"
        :loading="recognizing"
        @click="recognize"
      >{{ result ? tt('重新识别') : tt('开始识别') }}</el-button>
      <el-button v-if="result" type="success" :disabled="!hasMatchedData" @click="applyResult">{{ tt('应用到单据') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { Camera, Delete, DocumentChecked, Upload } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { usePanelRuntime } from '@core/panel-runtime'
import { tt } from '@/i18n'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  panelCode: { type: String, required: true },
  panelName: { type: String, default: '' },
  headerFields: { type: Array, default: () => [] },
  detailTabs: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue', 'apply'])
const engine = usePanelRuntime()

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})
const cameraInput = ref(null)
const uploadInput = ref(null)
const file = ref(null)
const previewUrl = ref('')
const recognizing = ref(false)
const result = ref(null)
const errorMessage = ref('')
const header = reactive({})
const detail = reactive({})
const detailMode = ref('replace')
let requestVersion = 0

const warnings = computed(() => {
  const value = result.value?.warnings || result.value?.unmatched || []
  return (Array.isArray(value) ? value : [value])
    .map((item) => typeof item === 'string' ? item : (item?.message || item?.text || ''))
    .filter(Boolean)
})
const headerEntries = computed(() => Object.keys(header).map((key) => ({ key, value: header[key] })))
const detailSections = computed(() => Object.entries(detail)
  .filter(([, rows]) => Array.isArray(rows) && rows.length)
  .map(([key, rows]) => ({
    key,
    label: props.detailTabs.find((tab) => tab.key === key)?.label || key,
    rows,
    columns: [...new Set(rows.flatMap((row) => Object.keys(row || {})))],
  })))
const hasMatchedData = computed(() => headerEntries.value.length > 0 || detailSections.value.length > 0)

function normalizeType(type) {
  if (['小数', '整数', 'Decimal', 'Long', 'Integer', 'Double'].includes(type)) return 'number'
  if (['日期', '日期时间', '时间', 'DATE', 'DateTime', 'Date'].includes(type)) return 'date'
  if (['是否', 'Boolean', 'BOOL'].includes(type)) return 'boolean'
  return 'text'
}

function fieldType(name) {
  const field = props.headerFields.find((item) => (item.dataName || item.code || item.name) === name)
  return normalizeType(field?.dataType)
}

function detailFieldType(tabKey, name) {
  const tab = props.detailTabs.find((item) => item.key === tabKey)
  const field = (tab?.fields || []).find((item) => (item.dataName || item.code || item.name) === name)
  return normalizeType(field?.dataType)
}

function clearPreview() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
}

function clearResult() {
  result.value = null
  errorMessage.value = ''
  Object.keys(header).forEach((key) => delete header[key])
  Object.keys(detail).forEach((key) => delete detail[key])
}

function onFileSelected(event) {
  const selected = event.target.files?.[0]
  event.target.value = ''
  if (!selected) return
  if (selected.size > 10 * 1024 * 1024) {
    ElMessage.warning(tt('图片不能超过 10MB'))
    return
  }
  if (!selected.type.startsWith('image/')) {
    ElMessage.warning(tt('请选择图片文件'))
    return
  }
  clearPreview()
  clearResult()
  file.value = selected
  previewUrl.value = URL.createObjectURL(selected)
}

async function recognize() {
  if (!file.value || recognizing.value) return
  const version = ++requestVersion
  recognizing.value = true
  errorMessage.value = ''
  try {
    const response = await engine.recognizeFormImage({ panelCode: props.panelCode, image: file.value })
    if (version !== requestVersion) return
    result.value = response || {}
    const recognizedHeader = response?.header || response?.formData || {}
    const recognizedDetail = response?.detail || recognizedHeader.detail || {}
    Object.keys(header).forEach((key) => delete header[key])
    Object.keys(detail).forEach((key) => delete detail[key])
    for (const [key, value] of Object.entries(recognizedHeader)) {
      if (key !== 'detail') header[key] = value
    }
    for (const [key, rows] of Object.entries(recognizedDetail || {})) {
      if (Array.isArray(rows) && rows.length) detail[key] = rows.map((row) => ({ ...row }))
    }
  } catch (error) {
    if (version !== requestVersion) return
    result.value = null
    errorMessage.value = engine.errMsg(error) || tt('OCR 识别失败')
  } finally {
    if (version === requestVersion) recognizing.value = false
  }
}

function applyResult() {
  if (!hasMatchedData.value) return
  emit('apply', {
    header: { ...header },
    detail: Object.fromEntries(Object.entries(detail).map(([key, rows]) => [key, rows.map((row) => ({ ...row }))])),
    requestId: result.value?.requestId || '',
    warnings: [...warnings.value],
    detailMode: detailMode.value,
  })
  visible.value = false
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function reset() {
  requestVersion++
  recognizing.value = false
  clearPreview()
  clearResult()
  file.value = null
  detailMode.value = 'replace'
}

watch(() => props.modelValue, (open) => {
  if (!open && !recognizing.value) reset()
})

onBeforeUnmount(reset)
</script>

<style scoped>
.scan-file-input {
  display: none;
}

.scan-source-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 36px;
}

.scan-file-name {
  min-width: 0;
  overflow: hidden;
  color: #4b5563;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scan-preview-row {
  display: grid;
  grid-template-columns: minmax(180px, 280px) 1fr;
  gap: 18px;
  align-items: center;
  margin-top: 14px;
  padding: 12px 0;
  border-top: 1px solid #e5e7eb;
  border-bottom: 1px solid #e5e7eb;
}

.scan-image-wrap {
  display: grid;
  width: 100%;
  height: 180px;
  place-items: center;
  overflow: hidden;
  background: #f5f7fa;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
}

.scan-image-wrap img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.scan-image-placeholder {
  padding: 12px;
  color: #6b7280;
  text-align: center;
  overflow-wrap: anywhere;
}

.scan-file-meta {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 8px;
  color: #6b7280;
}

.scan-file-meta strong {
  color: #1f2937;
}

.scan-alert,
.scan-section {
  margin-top: 14px;
}

.scan-section-title {
  color: #303133;
  font-size: 14px;
  font-weight: 600;
}

.scan-section-heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  margin-bottom: 8px;
}

.scan-header-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 18px;
}

.scan-header-field {
  display: grid;
  grid-template-columns: minmax(86px, 120px) minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  min-width: 0;
}

.scan-header-field > span {
  overflow: hidden;
  color: #606266;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scan-header-field :deep(.el-date-editor),
.scan-header-field :deep(.el-input-number) {
  width: 100%;
}

@media (max-width: 700px) {
  .scan-source-bar {
    flex-wrap: wrap;
  }

  .scan-file-name {
    width: 100%;
  }

  .scan-preview-row {
    grid-template-columns: 1fr;
  }

  .scan-image-wrap {
    height: min(46vh, 300px);
  }

  .scan-header-grid {
    grid-template-columns: 1fr;
  }

  .scan-section-heading {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
