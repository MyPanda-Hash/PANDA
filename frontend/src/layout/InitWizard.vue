<!-- InitWizard.vue — 系统初始化向导（AI 辅助开箱即用）
     设计基线 Ant Design v6：colorPrimary #1677ff、圆角 6、控件高 32、正文 14/22、8pt 间距。
     界面取向：工具界面、密而不挤；一屏看全 7 个模块的状态与下一步动作，不引导、不装饰。
     流程：下载模板 → 上传校验 → 预览确认 → 落库 → （期初）核对改数 → 完成初始化并锁定账套。 -->
<template>
  <teleport to="body">
    <div v-if="app.initWizardVisible" class="wz-mask" @click.self="close">
      <div class="wz">
        <header class="wz-head">
          <div>
            <h2 class="wz-title">{{ tt('系统初始化') }}</h2>
            <p class="wz-sub">
              {{ tt('按模块下载模板、填写后上传；期初库存需逐行核对再写入，全部完成后本账套锁定，不可再次导入') }}
            </p>
          </div>
          <div class="wz-head-right">
            <span v-if="state.batchNo" class="wz-batch">{{ tt('批次') }} {{ state.batchNo }}</span>
            <el-button text @click="close">{{ tt('关闭') }}</el-button>
          </div>
        </header>

        <div v-if="loading" class="wz-loading">
          <el-icon class="is-loading"><Loading /></el-icon>
          {{ tt('正在读取初始化状态…') }}
        </div>

        <!-- 已锁定：只读完成态 -->
        <div v-else-if="state.locked" class="wz-locked">
          <el-result icon="success" :title="tt('本账套已完成初始化')">
            <template #sub-title>
              <span>{{ tt('完成时间') }}：{{ state.lockedAt }} · {{ tt('操作人') }}：{{ state.lockedBy }}</span>
            </template>
          </el-result>
          <ul class="wz-done-list">
            <li v-for="m in state.modules" :key="m.code">
              <el-tag :type="m.done ? 'success' : 'info'" size="small" effect="plain">
                {{ m.done ? tt('已导入') : tt('未导入') }}
              </el-tag>
              <span class="wz-done-name">{{ tt(m.label) }}</span>
            </li>
          </ul>
          <p class="wz-hint">
            {{ tt('初始化不可重复执行；后续新增数据请走各面板的导入功能，库存差异请走盘点/调整单。') }}
          </p>
        </div>

        <!-- 未锁定：模块清单 -->
        <div v-else class="wz-body">
          <div class="wz-grid">
            <section
              v-for="m in state.modules"
              :key="m.code"
              class="wz-card"
              :class="{ done: m.done, blocked: m.blockedBy.length && !m.done }"
            >
              <header class="wz-card-head">
                <span class="wz-card-name">{{ tt(m.label) }}</span>
                <el-tag v-if="m.done" type="success" size="small" effect="plain">{{ tt('已导入') }}</el-tag>
                <el-tag v-else-if="m.blockedBy.length" type="info" size="small" effect="plain">
                  {{ tt('待') }}{{ m.blockedBy.map((x) => tt(x)).join('/') }}
                </el-tag>
                <el-tag v-else type="warning" size="small" effect="plain">{{ tt('待导入') }}</el-tag>
              </header>

              <div class="wz-card-meta">
                {{ tt('模板') }}：{{ m.fileName }}
                <span v-if="m.needsConfirmation" class="wz-card-note">· {{ tt('需核对后写入') }}</span>
              </div>

              <div class="wz-card-fields">
                <span v-for="h in m.headers" :key="h" class="wz-field">{{ h }}</span>
              </div>

              <footer class="wz-card-actions">
                <el-button size="small" @click="downloadTemplate(m)">
                  {{ tt('下载模板') }}
                </el-button>
                <el-button
                  v-if="m.code === 'dept'"
                  size="small"
                  :disabled="m.blockedBy.length > 0 || m.done"
                  :loading="uploading === 'org-chart'"
                  @click="pickOrgChart(m)"
                >
                  {{ tt('传架构图') }}
                </el-button>
                <el-button
                  size="small"
                  type="primary"
                  :disabled="m.blockedBy.length > 0 || m.done"
                  :loading="uploading === m.code"
                  @click="pickFile(m)"
                >
                  {{ m.done ? tt('已导入') : tt('上传') }}
                </el-button>
              </footer>
            </section>
          </div>

          <!-- 上传结果预览 -->
          <section v-if="preview" class="wz-preview">
            <header class="wz-preview-head">
              <span class="wz-preview-title">
                {{ tt(preview.moduleLabel) }} · {{ preview.fileName }} · {{ preview.rowCount }}{{ tt('行') }}
                <el-tag v-if="preview.fromImage" size="small" type="warning" effect="plain">
                  {{ tt('AI 识别，请逐行核对') }}
                </el-tag>
              </span>
              <div class="wz-preview-actions">
                <el-button size="small" @click="preview = null">{{ tt('取消') }}</el-button>
                <el-button
                  v-if="!preview.moduleNeedsConfirm"
                  size="small"
                  type="primary"
                  :disabled="preview.errors.length > 0"
                  :loading="applying"
                  @click="applyPreview"
                >
                  {{ tt('确认落库') }}
                </el-button>
                <el-button
                  v-else
                  size="small"
                  type="primary"
                  :disabled="preview.errors.length > 0"
                  @click="openCheck"
                >
                  {{ tt('核对并写入期初') }}
                </el-button>
              </div>
            </header>

            <el-alert
              v-if="preview.errors.length"
              type="error"
              :closable="false"
              show-icon
              :title="tt('存在') + preview.errors.length + tt('个问题，修正后重新上传')"
            >
              <ul class="wz-issues">
                <li v-for="(e, i) in preview.errors.slice(0, 8)" :key="i">{{ e }}</li>
              </ul>
            </el-alert>
            <el-alert
              v-else-if="preview.warnings.length"
              type="warning"
              :closable="false"
              show-icon
            >
              <ul class="wz-issues">
                <li v-for="(w, i) in preview.warnings.slice(0, 5)" :key="i">{{ w }}</li>
              </ul>
            </el-alert>

            <el-table :data="preview.rows.slice(0, 8)" size="small" border class="wz-table">
              <el-table-column
                v-for="h in previewHeaders"
                :key="h"
                :prop="h"
                :label="h"
                min-width="110"
                show-overflow-tooltip
              />
            </el-table>
            <p v-if="preview.rowCount > 8" class="wz-hint">
              {{ tt('仅预览前 8 行，共') }} {{ preview.rowCount }} {{ tt('行') }}
            </p>
          </section>

          <!-- 落库结果 -->
          <section v-if="result" class="wz-result">
            <header class="wz-result-head">
              <span class="wz-result-title">{{ tt(result.moduleLabel) }}{{ tt('导入结果') }}</span>
              <span class="wz-result-num">
                {{ tt('新增') }} {{ result.inserted }} · {{ tt('跳过') }} {{ result.skipped }}
                <template v-if="result.failed"> · {{ tt('失败') }} {{ result.failed }}</template>
              </span>
            </header>
            <ul class="wz-issues wz-result-list">
              <li v-for="(it, i) in result.items.slice(0, 10)" :key="i">
                <el-tag :type="it.action === 'insert' ? 'success' : 'info'" size="small" effect="plain">
                  {{ it.action === 'insert' ? tt('新增') : tt('跳过') }}
                </el-tag>
                <code class="wz-result-key">{{ it.key }}</code>
                <span class="wz-result-msg">{{ it.message }}</span>
              </li>
            </ul>
          </section>

          <footer class="wz-foot">
            <span class="wz-foot-tip">
              {{ tt('已完成') }} {{ doneCount }} / {{ state.modules.length }} {{ tt('个模块') }}
            </span>
            <el-button :disabled="doneCount === 0" :loading="finishing" type="primary" @click="finishInit">
              {{ tt('完成初始化并锁定') }}
            </el-button>
          </footer>
        </div>

        <!-- 期初核对弹窗：可逐行改数量/单价，改过的标黄并显示原值 -->
        <el-dialog v-model="checkVisible" :title="tt('期初库存核对')" width="920px" append-to-body>
          <p class="wz-hint wz-check-tip">
            {{ tt('核对数量与单价，可直接修改；确认后写入期初余额单并记入证据表（保留上传原值与修改记录）') }}
          </p>
          <el-table :data="checkRows" size="small" border max-height="380" class="wz-table">
            <el-table-column prop="仓库" :label="tt('仓库')" width="110" />
            <el-table-column prop="存货编码" :label="tt('存货编码')" width="120" />
            <el-table-column prop="存货名称" :label="tt('存货名称')" min-width="140" show-overflow-tooltip />
            <el-table-column prop="规格型号" :label="tt('规格型号')" min-width="110" show-overflow-tooltip />
            <el-table-column prop="计量单位" :label="tt('单位')" width="70" />
            <el-table-column :label="tt('数量')" width="150">
              <template #default="{ row }">
                <el-input-number v-model="row.数量" :min="0" :step="1" :controls="false" size="small"
                  style="width: 100%" @change="markChanged(row)" />
              </template>
            </el-table-column>
            <el-table-column :label="tt('主单价')" width="140">
              <template #default="{ row }">
                <el-input-number v-model="row.主单价" :min="0" :step="0.01" :precision="2" :controls="false"
                  size="small" style="width: 100%" @change="markChanged(row)" />
              </template>
            </el-table-column>
            <el-table-column :label="tt('金额')" width="120" align="right">
              <template #default="{ row }">
                <span class="wz-amount">{{ amountOf(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="tt('原值')" width="130">
              <template #default="{ row }">
                <span v-if="row.changed" class="wz-changed">
                  {{ row.qtyInput }} / {{ row.priceInput }}
                </span>
                <span v-else class="wz-muted">—</span>
              </template>
            </el-table-column>
          </el-table>
          <p class="wz-hint">
            {{ tt('合计') }}：{{ checkRows.length }} {{ tt('行') }}，{{ tt('数量') }} {{ totalQty }}，
            {{ tt('金额') }} {{ totalAmount }}
          </p>
          <template #footer>
            <el-button @click="checkVisible = false">{{ tt('取消') }}</el-button>
            <el-button type="primary" :loading="confirming" @click="confirmOpening">
              {{ tt('确认写入期初') }}
            </el-button>
          </template>
        </el-dialog>
      </div>
    </div>
  </teleport>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@core/request'
import { useAppStore } from '@/stores/app'
import { tt } from '@/i18n'

const app = useAppStore()

const loading = ref(false)
const uploading = ref('')
const applying = ref(false)
const finishing = ref(false)
const confirming = ref(false)
const state = reactive({ locked: false, batchNo: '', lockedAt: '', lockedBy: '', modules: [] })
const preview = ref(null)
const result = ref(null)
const checkVisible = ref(false)
const checkRows = ref([])
const pendingFile = ref(null)

const doneCount = computed(() => state.modules.filter((m) => m.done).length)
const previewHeaders = computed(() => {
  const rows = preview.value?.rows || []
  if (!rows.length) return []
  return Object.keys(rows[0]).filter((k) => !k.startsWith('__'))
})
const totalQty = computed(() => sumOf('数量'))
const totalAmount = computed(() => {
  const v = checkRows.value.reduce((s, r) => s + Number(r.数量 || 0) * Number(r.主单价 || 0), 0)
  return v.toFixed(2)
})

function sumOf(key) {
  const v = checkRows.value.reduce((s, r) => s + Number(r[key] || 0), 0)
  return Number.isInteger(v) ? v : v.toFixed(2)
}

function amountOf(row) {
  return (Number(row.数量 || 0) * Number(row.主单价 || 0)).toFixed(2)
}

async function loadState() {
  loading.value = true
  try {
    const res = await request.get('/init/state')
    const data = res?.data || {}
    Object.assign(state, {
      locked: !!data.locked,
      batchNo: data.batchNo || '',
      lockedAt: data.lockedAt || '',
      lockedBy: data.lockedBy || '',
      modules: data.modules || [],
    })
  } catch (e) {
    ElMessage.error(tt('读取初始化状态失败') + '：' + (e?.message || ''))
  } finally {
    loading.value = false
  }
}

async function downloadTemplate(m) {
  try {
    const res = await request.get(`/init/template/${m.code}`, { responseType: 'blob' })
    const blob = res.data instanceof Blob ? res.data : new Blob([res.data])
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = m.fileName
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 2000)
  } catch (e) {
    ElMessage.error(tt('下载模板失败') + '：' + (e?.message || ''))
  }
}

const fileInput = ref(null)

function pickFile(m) {
  if (!fileInput.value) {
    fileInput.value = document.createElement('input')
    fileInput.value.type = 'file'
    fileInput.value.accept = '.xlsx,.xls'
    fileInput.value.style.display = 'none'
    document.body.appendChild(fileInput.value)
  }
  const input = fileInput.value
  input.value = ''
  input.onchange = async () => {
    const file = input.files?.[0]
    if (file) await doUpload(m, file)
  }
  input.click()
}

function pickOrgChart(m) {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = 'image/png,image/jpeg,image/webp'
  input.style.display = 'none'
  document.body.appendChild(input)
  input.onchange = async () => {
    const file = input.files?.[0]
    if (file) await uploadOrgChart(m, file)
    input.remove()
  }
  input.click()
}

async function uploadOrgChart(m, file) {
  uploading.value = 'org-chart'
  result.value = null
  try {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('hint', '')
    const res = await request.post('/init/org-chart', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 150000,
    })
    const data = res?.data || {}
    preview.value = {
      module: m.code,
      moduleLabel: data.moduleLabel || m.label,
      moduleNeedsConfirm: false,
      fileName: data.fileName || file.name,
      rowCount: data.rowCount || 0,
      rows: data.rows || [],
      errors: data.errors || [],
      warnings: [...(data.warnings || []), data.notes ? `${tt('模型判断')}：${data.notes}` : ''].filter(Boolean),
      fromImage: true,
    }
    if (preview.value.errors.length) ElMessage.warning(tt('识别结果有问题，请按提示调整或改用 Excel 模板'))
    else ElMessage.success(`${tt('已从图里识别出')} ${preview.value.rowCount} ${tt('个部门，请核对后落库')}`)
  } catch (e) {
    ElMessage.error(tt('架构图解析失败') + '：' + (e?.message || ''))
  } finally {
    uploading.value = ''
  }
}

async function doUpload(m, file) {
  uploading.value = m.code
  result.value = null
  try {
    const fd = new FormData()
    fd.append('module', m.code)
    fd.append('file', file)
    const res = await request.post('/init/upload', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    const data = res?.data || {}
    pendingFile.value = file.name
    preview.value = {
      module: m.code,
      moduleLabel: data.moduleLabel || m.label,
      moduleNeedsConfirm: !!m.needsConfirmation,
      fileName: data.fileName || file.name,
      rowCount: data.rowCount || 0,
      rows: data.rows || [],
      errors: data.errors || [],
      warnings: data.warnings || [],
    }
    if (preview.value.errors.length) ElMessage.warning(tt('上传有问题，请按提示修正'))
    else loadState()
  } catch (e) {
    ElMessage.error(tt('上传失败') + '：' + (e?.message || ''))
  } finally {
    uploading.value = ''
  }
}

async function applyPreview() {
  applying.value = true
  try {
    const res = await request.post('/init/apply', {
      module: preview.value.module,
      rows: preview.value.rows,
    })
    result.value = res.data?.data || null
    preview.value = null
    ElMessage.success(tt('已导入'))
    await loadState()
  } catch (e) {
    ElMessage.error(tt('导入失败') + '：' + (e?.message || ''))
  } finally {
    applying.value = false
  }
}

function openCheck() {
  checkRows.value = (preview.value?.rows || []).map((row) => ({
    ...row,
    qtyInput: row.数量,
    priceInput: row.主单价,
    changed: false,
  }))
  checkVisible.value = true
}

function markChanged(row) {
  row.changed = Number(row.数量) !== Number(row.qtyInput) || Number(row.主单价) !== Number(row.priceInput)
}

async function confirmOpening() {
  confirming.value = true
  try {
    const res = await request.post('/init/opening/confirm', {
      fileName: preview.value?.fileName || '',
      rows: checkRows.value,
    })
    const data = res?.data || {}
    checkVisible.value = false
    preview.value = null
    result.value = {
      moduleLabel: tt('期初库存'),
      inserted: data.rowCount || 0,
      skipped: 0,
      failed: 0,
      items: [{ action: 'insert', key: data.initBalanceNo, message: data.message || '' }],
    }
    ElMessage.success(`${tt('期初已写入')}：${data.initBalanceNo}`)
    await loadState()
  } catch (e) {
    ElMessage.error(tt('写入期初失败') + '：' + (e?.message || ''))
  } finally {
    confirming.value = false
  }
}

async function finishInit() {
  try {
    await ElMessageBox.confirm(
      tt('完成后本账套将锁定，不可再次执行初始化导入。确认完成？'),
      tt('完成初始化'),
      { type: 'warning', confirmButtonText: tt('确认完成'), cancelButtonText: tt('再检查一下') },
    )
  } catch {
    return
  }
  finishing.value = true
  try {
    await request.post('/init/finish')
    ElMessage.success(tt('初始化完成，账套已锁定'))
    await loadState()
  } catch (e) {
    ElMessage.error(tt('完成失败') + '：' + (e?.message || ''))
  } finally {
    finishing.value = false
  }
}

function close() {
  // 已锁定的账套没有可做的事，关掉就记住「初始化已完成」，避免每次登录都弹
  if (state.locked) app.finishInitWizard()
  app.closeInitWizard(false)
}

watch(
  () => app.initWizardVisible,
  (visible) => {
    if (visible) {
      preview.value = null
      result.value = null
      loadState()
    }
  },
  { immediate: true },
)
</script>

<style scoped>
.wz-mask {
  position: fixed;
  inset: 0;
  z-index: 5000;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.wz {
  width: 1120px;
  max-width: 100%;
  max-height: 92vh;
  overflow: auto;
  background: var(--t-bg, #fff);
  border-radius: 8px;
  border: 1px solid var(--t-border-light, #e6e9ef);
  display: flex;
  flex-direction: column;
}
.wz-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 24px 12px;
  border-bottom: 1px solid var(--t-border-light, #e6e9ef);
  flex-wrap: wrap;
}
.wz-head > div:first-child {
  flex: 1 1 260px;
  min-width: 0;
}
.wz-title {
  margin: 0 0 4px;
  font-size: 18px;
  line-height: 26px;
  font-weight: 600;
  color: var(--t-text-1, #1f2329);
}
.wz-sub {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--t-text-3, #8a919f);
}
.wz-head-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.wz-batch {
  font-size: 12px;
  color: var(--t-text-3, #8a919f);
  font-variant-numeric: tabular-nums;
}
.wz-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 40px 24px;
  color: var(--t-text-3, #8a919f);
  font-size: 13px;
}
.wz-body {
  padding: 16px 24px 20px;
}
.wz-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
  gap: 12px;
}
.wz-card {
  border: 1px solid var(--t-border-light, #e6e9ef);
  border-radius: 6px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--t-bg, #fff);
}
.wz-card.done {
  border-color: #b7eb8f;
  background: #f6ffed;
}
.wz-card.blocked {
  background: var(--t-bg-hover, #fafbfc);
}
.wz-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.wz-card-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--t-text-1, #1f2329);
}
.wz-card-meta {
  font-size: 12px;
  color: var(--t-text-3, #8a919f);
}
.wz-card-note {
  color: var(--t-primary, #1677ff);
}
.wz-card-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.wz-field {
  font-size: 11px;
  line-height: 18px;
  padding: 0 6px;
  border-radius: 4px;
  border: 1px solid var(--t-border-light, #e6e9ef);
  color: var(--t-text-2, #59616e);
  background: var(--t-bg-hover, #fafbfc);
}
.wz-card-actions {
  display: flex;
  gap: 8px;
  margin-top: 2px;
}
.wz-preview,
.wz-result {
  margin-top: 16px;
  border: 1px solid var(--t-border-light, #e6e9ef);
  border-radius: 6px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.wz-preview-head,
.wz-result-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.wz-preview-title,
.wz-result-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--t-text-1, #1f2329);
}
.wz-preview-actions {
  display: flex;
  gap: 8px;
}
.wz-result-num {
  font-size: 12px;
  color: var(--t-text-3, #8a919f);
  font-variant-numeric: tabular-nums;
}
.wz-issues {
  margin: 0;
  padding-left: 16px;
  font-size: 12px;
  line-height: 20px;
}
.wz-result-list {
  list-style: none;
  padding-left: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.wz-result-list li {
  display: flex;
  align-items: center;
  gap: 8px;
}
.wz-result-key {
  font-size: 12px;
  color: var(--t-text-1, #1f2329);
}
.wz-result-msg {
  font-size: 12px;
  color: var(--t-text-3, #8a919f);
}
.wz-table {
  margin-top: 4px;
}
.wz-hint {
  margin: 0;
  font-size: 12px;
  line-height: 20px;
  color: var(--t-text-3, #8a919f);
}
.wz-check-tip {
  margin-bottom: 8px;
}
.wz-amount {
  font-variant-numeric: tabular-nums;
}
.wz-changed {
  color: #d46b08;
  font-variant-numeric: tabular-nums;
}
.wz-muted {
  color: var(--t-text-4, #b0b6c0);
}
.wz-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--t-border-light, #e6e9ef);
}
.wz-foot-tip {
  font-size: 12px;
  color: var(--t-text-3, #8a919f);
  font-variant-numeric: tabular-nums;
}
.wz-locked {
  padding: 16px 24px 24px;
}
.wz-done-list {
  list-style: none;
  margin: 0 0 12px;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 8px;
}
.wz-done-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--t-text-2, #59616e);
}
.wz-done-name {
  color: var(--t-text-1, #1f2329);
}

/* 窄屏：标题与关闭按钮分两行，模块卡片单列，避免标题被挤成竖排 */
@media (max-width: 640px) {
  .wz-mask {
    padding: 12px;
  }
  .wz-head {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
    padding: 16px 16px 10px;
  }
  /* 纵向布局下 flex-basis 会变成高度（曾把标题区撑成 260px 空白），这里显式复位 */
  .wz-head > div:first-child {
    flex: 0 0 auto;
  }
  .wz-head-right {
    width: 100%;
    justify-content: space-between;
  }
  .wz-batch {
    display: none;
  }
  .wz-body {
    padding: 12px 16px 16px;
  }
  .wz-grid {
    grid-template-columns: 1fr;
  }
  .wz-title {
    font-size: 16px;
    line-height: 24px;
  }
}
</style>