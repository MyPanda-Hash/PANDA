<template>
  <div class="login-page">
    <div class="login-box">
      <div class="brand">
        <div class="logo">轻<span>MES</span></div>
        <div class="sub">生产制造执行系统</div>
      </div>
      <el-form :model="form" size="large" @keyup.enter="doLogin">
        <el-form-item>
          <el-input v-model="form.userName" placeholder="用户名" :prefix-icon="User" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="form.password" type="password" placeholder="密码" show-password :prefix-icon="Lock" />
        </el-form-item>
        <el-form-item>
          <el-select v-model="form.factory" placeholder="登录工厂" style="width: 100%">
            <el-option v-for="f in user.factories" :key="f.code" :label="f.name" :value="f.code" />
          </el-select>
        </el-form-item>
        <el-button type="primary" size="large" style="width: 100%" :loading="loading" @click="doLogin">登 录</el-button>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { User, Lock } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const user = useUserStore()
const loading = ref(false)
const form = reactive({ userName: 'admin', password: '123456', factory: '' })

async function doLogin() {
  if (!form.userName || !form.password) return ElMessage.warning('请输入用户名和密码')
  loading.value = true
  try {
    await user.login({ userName: form.userName, password: form.password })
    ElMessage.success('登录成功')
    router.replace('/dashboard')
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '登录失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => user.fetchFactories())
</script>

<style scoped>
.login-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 50%, #60a5fa 100%);
}
.login-box {
  width: 380px;
  background: #fff;
  border-radius: 14px;
  padding: 40px 36px 28px;
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.25);
}
.brand {
  text-align: center;
  margin-bottom: 26px;
}
.logo {
  font-size: 32px;
  font-weight: 700;
  color: #2563eb;
}
.logo span {
  font-size: 18px;
  color: #111;
  font-weight: 400;
}
.sub {
  color: #9ca3af;
  font-size: 13px;
  margin-top: 4px;
  letter-spacing: 4px;
}
.tip {
  margin-top: 14px;
  text-align: center;
  color: #9ca3af;
  font-size: 12px;
}
</style>
