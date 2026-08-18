<!-- BomDialog.vue — 存货 BOM 管理弹窗（新结构：物品为类别单据的明细行，BOM 存物品行 _bom） -->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="item ? 'BOM 管理：' + item['存货编码'] + ' ' + item['存货名称'] : 'BOM 管理'"
    width="900px"
    append-to-body
    @update:model-value="close"
    @open="onOpen"
  >
    <div class="sec-title">当前子件（父件：{{ item ? item['存货编码'] : '-' }} {{ item ? item['存货名称'] : '' }}，类别单据：{{ parentDoc ? parentDoc['编号'] : '-' }}）</div>
    <el-table :data="bomRows" size="small" border height="190" v-loading="loading">
      <el-table-column label="材料编码" prop="材料编码" width="100" />
      <el-table-column label="材料名称" prop="材料名称" min-width="140" />
      <el-table-column label="规格型号" prop="规格型号" min-width="110" />
      <el-table-column label="单位" prop="计量单位" width="70" />
      <el-table-column label="定额需用数量" width="115">
        <template #default="{ row }"><el-input-number v-model="row['定额需用数量']" :controls="false" size="small" style="width:100%" /></template>
      </el-table-column>
      <el-table-column label="损耗率%" width="95">
        <template #default="{ row }"><el-input-number v-model="row['损耗率%']" :controls="false" size="small" style="width:100%" /></template>
      </el-table-column>
      <el-table-column label="操作" width="100" align="center">
        <template #default="{ row }">
          <el-button size="small" link type="danger" @click="removeRow(row)">移除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="sec-title">添加子件（勾选存货加入 BOM；所有物品均作为存货存放，BOM 关系自由关联，可多级）</div>
    <el-table :data="candRows" size="small" border height="210" @selection-change="onCandSel">
      <el-table-column type="selection" width="45" />
      <el-table-column label="存货编码" prop="存货编码" width="100" />
      <el-table-column label="存货名称" prop="存货名称" min-width="150" />
      <el-table-column label="规格型号" prop="规格型号" min-width="110" />
      <el-table-column label="类别" prop="所属类别" width="90" />
      <el-table-column label="属性" prop="属性" width="80" />
      <el-table-column label="单位" prop="计量单位" width="70" />
    </el-table>
    <div class="cand-actions">
      <el-button size="small" type="primary" :disabled="!candSel.length" @click="addSelected">添加勾选（{{ candSel.length }}）</el-button>
      <el-input v-model="kw" size="small" placeholder="按编码/名称过滤" style="width: 220px" clearable @input="loadCand" />
    </div>

    <template #footer>
      <el-button @click="close">关闭</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存 BOM</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import * as engine from '@/business/engine'

const props = defineProps({
  modelValue: Boolean,
  item: Object,     // 物品行（类别单据明细行：存货编码/存货名称/_bom）
  parentDoc: Object, // 父类别单据（编号/类别/detail.items）
})
const emit = defineEmits(['update:modelValue', 'saved'])

const bomRows = ref([])
const candRows = ref([])
const candSel = ref([])
const kw = ref('')
const loading = ref(false)
const saving = ref(false)

function close() {
  emit('update:modelValue', false)
}

async function onOpen() {
  bomRows.value = []
  parseBom()
  await loadCand()
}

// 物品 BOM 存于行 _bom（JSON 字符串）
function parseBom() {
  const raw = props.item && props.item['_bom']
  try {
    const arr = raw ? (typeof raw === 'string' ? JSON.parse(raw) : raw) : []
    bomRows.value = (Array.isArray(arr) ? arr : []).map((r) => ({ ...r }))
  } catch (e) {
    bomRows.value = []
  }
}

// 候选：全部类别单据的明细物品（排除自身）
async function loadCand() {
  loading.value = true
  try {
    const res = await engine.queryFormDataList({ panelCode: 'INV', condition: {}, pageNo: 1, pageSize: 100 })
    const docs = res.list || []
    let rows = []
    for (const d of docs) {
      for (const it of (d.detail && d.detail.items) || []) {
        rows.push({ ...it, 所属类别: d['类别'] || '' })
      }
    }
    const self = props.item && props.item['存货编码']
    if (self) rows = rows.filter((r) => r['存货编码'] !== self)
    if (kw.value) {
      const k = kw.value.trim()
      rows = rows.filter((r) => String(r['存货编码'] || '').includes(k) || String(r['存货名称'] || '').includes(k))
    }
    candRows.value = rows
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '存货列表加载失败')
  } finally {
    loading.value = false
  }
}

function onCandSel(r) {
  candSel.value = r
}

function addSelected() {
  const before = candSel.value.length
  for (const r of candSel.value) {
    if (bomRows.value.some((b) => b['材料编码'] === r['存货编码'])) continue
    bomRows.value.push({
      材料编码: r['存货编码'],
      材料名称: r['存货名称'],
      规格型号: r['规格型号'] || '',
      计量单位: r['计量单位'] || '件',
      定额需用数量: 1,
      '损耗率%': 0,
    })
  }
  candSel.value = []
  ElMessage.success('已添加 ' + before + ' 行')
}

function removeRow(row) {
  const idx = bomRows.value.indexOf(row)
  if (idx >= 0) bomRows.value.splice(idx, 1)
}

// 保存：更新父类别单据中该物品行的 _bom，再保存整个类别单据
async function save() {
  const item = props.item
  const doc = props.parentDoc
  if (!item || !doc || !doc['编号']) return ElMessage.warning('缺少存货/单据信息')
  saving.value = true
  try {
    const items = ((doc.detail && doc.detail.items) || []).map((r) => {
      if (r['存货编码'] === item['存货编码']) return { ...r, _bom: JSON.stringify(bomRows.value) }
      return r
    })
    const head = { ...doc }
    delete head.detail
    delete head['编号']
    delete head['单据状态']
    delete head['创建时间']
    delete head['更新时间']
    delete head['发起人编号']
    const rd = { ...head, 编号: doc['编号'], detail: { items } }
    const res = await engine.callButton({ panelCode: 'INV', buttonName: '保存', formData: rd, buttonParam: {} })
    ElMessage.success('BOM 已保存：' + item['存货编码'])
    emit('saved', res)
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.sec-title {
  font-size: 13px;
  font-weight: 600;
  color: #1c4f8a;
  margin: 10px 0 6px;
}
.cand-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
}
</style>
