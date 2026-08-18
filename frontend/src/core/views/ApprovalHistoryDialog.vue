<!-- ApprovalHistoryDialog.vue — 审批情况：展示单据的审批流记录（提交/通过/驳回 全留痕） -->
<template>
  <el-dialog :model-value="modelValue" title="审批情况" width="760px" append-to-body @update:model-value="close">
    <el-table :data="records" size="small" border v-loading="loading">
      <el-table-column label="序号" width="55" align="center">
        <template #default="{ $index }">{{ $index + 1 }}</template>
      </el-table-column>
      <el-table-column label="节点" prop="nodeNo" width="60" align="center" />
      <el-table-column label="动作" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="tagType(row)">{{ actionLabel(row) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作人" prop="operator" width="110" />
      <el-table-column label="结果" width="90">
        <template #default="{ row }">{{ resultLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="意见" prop="opinion" show-overflow-tooltip />
      <el-table-column label="时间" prop="createTime" width="165" />
    </el-table>
    <div v-if="!loading && !records.length" class="empty">暂无审批记录</div>
    <template #footer>
      <el-button @click="close">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import request from '../request'

const props = defineProps({
  modelValue: Boolean,
  panelCode: String,
  formNo: String,
})
const emit = defineEmits(['update:modelValue'])

const records = ref([])
const loading = ref(false)

function close() {
  emit('update:modelValue', false)
}

function actionLabel(r) {
  return { SUBMIT: '提交审批', APPROVE: '审批通过', REJECT: '审批驳回', UNAUDIT: '弃审' }[r.action] || r.action || '-'
}
function resultLabel(r) {
  return { PENDING: '审批中', APPROVED: '已通过', REJECTED: '已驳回' }[r.result] || r.result || '-'
}
function tagType(r) {
  return { SUBMIT: 'info', APPROVE: 'success', REJECT: 'danger', UNAUDIT: 'warning' }[r.action] || 'info'
}

watch(
  () => props.modelValue,
  async (v) => {
    if (v && props.panelCode && props.formNo) {
      loading.value = true
      records.value = []
      try {
        const res = await request.get('/px/getApprovalHistory', { params: { panelCode: props.panelCode, code: props.formNo } })
        records.value = Array.isArray(res) ? res : res?.data || []
      } catch (e) {
        records.value = []
      } finally {
        loading.value = false
      }
    }
  }
)
</script>

<style scoped>
.empty {
  text-align: center;
  color: #999;
  padding: 20px 0;
  font-size: 13px;
}
</style>
