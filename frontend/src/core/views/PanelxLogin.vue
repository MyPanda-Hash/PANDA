<template>
  <el-dialog :model-value="modelValue" title="登录 PanelX 平台" width="400px" append-to-body @update:model-value="(v) => $emit('update:modelValue', v)">
    <el-form label-width="70px" @submit.prevent>
      <el-form-item label="用户名">
        <el-input v-model="form.userName" placeholder="admin" />
      </el-form-item>
      <el-form-item label="密码">
        <el-input v-model="form.password" type="password" show-password placeholder="123456" @keyup.enter="doLogin" />
      </el-form-item>
      <div class="hint">演示域 SdkTest：admin / 123456</div>
    </el-form>
    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="loading" @click="doLogin">登录</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { sdkLogin } from '@core/sdk'

defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue', 'success'])

const loading = ref(false)
const form = reactive({ userName: 'admin', password: '123456' })

async function doLogin() {
  if (!form.userName || !form.password) return ElMessage.warning('请输入用户名和密码')
  loading.value = true
  try {
    await sdkLogin(form.userName, form.password)
    ElMessage.success('PanelX 登录成功')
    emit('update:modelValue', false)
    emit('success')
  } catch (e) {
    ElMessage.error('PanelX 登录失败：' + (e?.errorDescription || e?.response?.data?.errorDescription || e?.message || String(e)))
  } finally {
    loading.value = false
  }
}

</script>

<style scoped>
.hint {
  font-size: 12px;
  color: #9ca3af;
  margin-left: 70px;
}
</style>