<template>
  <main class="login-page">
    <section class="login-scene">
      <img :src="manufacturingImage" alt="" aria-hidden="true" />
      <div class="scene-tint" aria-hidden="true"></div>

      <div class="scene-content">
        <p class="scene-kicker">
          <span></span>
          MANUFACTURING OPERATIONS
        </p>
        <h1><strong>轻 MES</strong> 让生产现场<br />有序运转</h1>
        <p class="scene-subtitle">生产制造执行系统</p>
      </div>

      <div class="scene-meta" aria-hidden="true">
        <span>聚焦现场</span>
        <span>协同执行</span>
        <span>持续改善</span>
      </div>
    </section>

    <section class="login-workspace">
      <div class="login-shell">
        <header class="brand">
          <div class="brand-mark">
            <el-icon><DataLine /></el-icon>
          </div>
          <div>
            <div class="brand-name">轻 MES</div>
            <div class="brand-subtitle">制造协同工作台</div>
          </div>
        </header>

        <div class="login-heading">
          <p class="eyebrow">企业账号</p>
          <h2>登录工作台</h2>
          <p class="heading-note">进入制造协同工作台</p>
        </div>

        <div v-if="errorMessage" class="login-error" role="alert">
          <el-icon><WarningFilled /></el-icon>
          <span>{{ errorMessage }}</span>
        </div>

        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-position="top"
          size="large"
          class="login-form"
          @keyup.enter="doLogin"
        >
          <el-form-item label="账号" prop="userName">
            <el-input
              v-model.trim="form.userName"
              :prefix-icon="User"
              placeholder="请输入登录账号"
              autocomplete="username"
              clearable
              @input="clearError"
            />
          </el-form-item>

          <el-form-item label="密码" prop="password">
            <el-input
              v-model="form.password"
              :prefix-icon="Lock"
              type="password"
              placeholder="请输入登录密码"
              autocomplete="current-password"
              show-password
              @input="clearError"
            />
          </el-form-item>

          <el-form-item label="登录工厂" prop="factory">
            <el-select
              v-model="form.factory"
              :loading="factoryLoading"
              :prefix-icon="OfficeBuilding"
              :placeholder="factoryLoading ? '正在加载工厂' : '请选择登录工厂'"
              :disabled="factoryLoading || !user.factories.length"
              @change="clearError"
            >
              <el-option
                v-for="factory in user.factories"
                :key="factory.code"
                :label="factory.name"
                :value="factory.code"
              />
            </el-select>
          </el-form-item>

          <div class="form-options">
            <el-checkbox v-model="rememberAccount">记住账号</el-checkbox>
            <span class="secure-note">
              <el-icon><Lock /></el-icon>
              安全连接
            </span>
          </div>

          <el-button
            type="primary"
            class="login-submit"
            :loading="loading"
            :disabled="factoryLoading"
            @click="doLogin"
          >
            <span>进入系统</span>
            <el-icon v-if="!loading"><ArrowRight /></el-icon>
          </el-button>
        </el-form>

        <footer class="login-footer">
          <span>LIGHT MES · 2026</span>
          <span
            class="footer-status"
            :class="{ 'is-error': !factoryLoading && !user.factories.length }"
          >
            <span class="status-dot"></span>
            {{ factoryLoading ? '正在连接服务' : user.factories.length ? '服务连接正常' : '服务连接异常' }}
          </span>
        </footer>
      </div>
    </section>
  </main>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowRight,
  DataLine,
  Lock,
  OfficeBuilding,
  User,
  WarningFilled,
} from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import manufacturingImage from '@/assets/login-manufacturing.png'

const REMEMBERED_ACCOUNT_KEY = 'mes_login_account'
const router = useRouter()
const user = useUserStore()
const formRef = ref(null)
const loading = ref(false)
const factoryLoading = ref(false)
const errorMessage = ref('')
const rememberedAccount = localStorage.getItem(REMEMBERED_ACCOUNT_KEY) || ''
const rememberAccount = ref(Boolean(rememberedAccount))
const form = reactive({ userName: rememberedAccount, password: '', factory: '' })

const rules = {
  userName: [{ required: true, message: '请输入登录账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入登录密码', trigger: 'blur' }],
  factory: [{ required: true, message: '请选择登录工厂', trigger: 'change' }],
}

function clearError() {
  errorMessage.value = ''
}

async function doLogin() {
  if (loading.value || factoryLoading.value) return
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  errorMessage.value = ''
  try {
    await user.login({ userName: form.userName, password: form.password })
    const selectedFactory = user.factories.find((item) => item.code === form.factory)
    if (selectedFactory) user.switchFactory(selectedFactory)
    if (rememberAccount.value) localStorage.setItem(REMEMBERED_ACCOUNT_KEY, form.userName)
    else localStorage.removeItem(REMEMBERED_ACCOUNT_KEY)
    await router.replace('/dashboard')
  } catch (error) {
    errorMessage.value = error.response?.data?.message || error.message || '登录失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  factoryLoading.value = true
  try {
    await user.fetchFactories()
    form.factory = user.factory?.code || user.factories[0]?.code || ''
  } catch (error) {
    errorMessage.value = '工厂信息加载失败，请检查服务后重试'
  } finally {
    factoryLoading.value = false
  }
})
</script>

<style scoped>
.login-page {
  --login-accent: #116a5b;
  --login-accent-hover: #0d584c;
  position: relative;
  min-height: 100%;
  overflow: hidden;
  background: #15241f;
  color: #1d2926;
}

.login-scene {
  position: absolute;
  inset: 0;
  overflow: hidden;
  background: #273b35;
}

.login-scene > img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
  object-position: center;
}

.scene-tint {
  position: absolute;
  inset: 0;
  background: rgba(8, 28, 23, 0.25);
}

.scene-content {
  position: absolute;
  z-index: 1;
  top: 48%;
  left: clamp(52px, 7vw, 112px);
  width: min(48vw, 650px);
  color: #ffffff;
  transform: translateY(-50%);
  text-shadow: 0 2px 18px rgba(5, 19, 16, 0.3);
}

.scene-kicker {
  display: flex;
  align-items: center;
  gap: 11px;
  margin: 0 0 22px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0;
}

.scene-kicker span {
  width: 28px;
  height: 2px;
  background: #e6ad3d;
}

.scene-content h1 {
  margin: 0;
  color: #ffffff;
  font-size: 46px;
  font-weight: 560;
  line-height: 1.3;
  letter-spacing: 0;
}

.scene-content h1 strong {
  font-weight: 780;
}

.scene-subtitle {
  margin: 22px 0 0;
  color: rgba(255, 255, 255, 0.9);
  font-size: 17px;
  font-weight: 520;
}

.scene-meta {
  position: absolute;
  z-index: 1;
  left: clamp(52px, 7vw, 112px);
  bottom: 34px;
  display: flex;
  align-items: center;
  gap: 0;
  color: rgba(255, 255, 255, 0.78);
  font-size: 12px;
}

.scene-meta span + span::before {
  content: '';
  display: inline-block;
  width: 1px;
  height: 11px;
  margin: 0 14px;
  background: rgba(255, 255, 255, 0.44);
  vertical-align: -1px;
}

.login-workspace {
  position: relative;
  z-index: 2;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 40px clamp(36px, 5.2vw, 84px);
}

.login-shell {
  width: 100%;
  max-width: 400px;
  padding: 30px 32px 27px;
  border: 1px solid rgba(255, 255, 255, 0.7);
  border-radius: 8px;
  background: rgba(250, 252, 251, 0.98);
  box-shadow: 0 20px 54px rgba(8, 30, 24, 0.22);
  backdrop-filter: blur(10px);
}

.brand {
  display: flex;
  align-items: center;
  gap: 13px;
}

.brand-mark {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  background: var(--login-accent);
  color: #ffffff;
  font-size: 23px;
}

.brand-name {
  color: #18231f;
  font-size: 20px;
  font-weight: 750;
  line-height: 1.15;
}

.brand-subtitle {
  margin-top: 4px;
  color: #71807b;
  font-size: 12px;
}

.login-heading {
  margin: 29px 0 24px;
  padding-top: 24px;
  border-top: 1px solid #e2e8e5;
}

.eyebrow {
  margin: 0 0 7px;
  color: var(--login-accent);
  font-size: 12px;
  font-weight: 700;
}

.login-heading h2 {
  margin: 0;
  color: #17221f;
  font-size: 27px;
  font-weight: 720;
  line-height: 1.25;
  letter-spacing: 0;
}

.heading-note {
  margin: 7px 0 0;
  color: #7b8984;
  font-size: 12px;
}

.login-error {
  min-height: 42px;
  display: flex;
  align-items: flex-start;
  gap: 9px;
  margin-bottom: 18px;
  padding: 11px 12px;
  border: 1px solid #e4b9b0;
  border-radius: 6px;
  background: #fff4f1;
  color: #9d3325;
  font-size: 13px;
  line-height: 18px;
}

.login-error .el-icon {
  flex: 0 0 auto;
  margin-top: 1px;
  font-size: 16px;
}

.login-form :deep(.el-form-item) {
  margin-bottom: 18px;
}

.login-form :deep(.el-form-item__label) {
  height: auto;
  margin-bottom: 8px;
  padding: 0;
  color: #40504b;
  font-size: 13px;
  font-weight: 650;
  line-height: 18px;
}

.login-form :deep(.el-input__wrapper),
.login-form :deep(.el-select__wrapper) {
  min-height: 48px;
  border: 1px solid #cfd8d4;
  border-radius: 6px;
  background: #ffffff;
  box-shadow: none;
  transition: border-color 0.18s, box-shadow 0.18s;
}

.login-form :deep(.el-input__wrapper:hover),
.login-form :deep(.el-select__wrapper:hover) {
  border-color: #91a29c;
}

.login-form :deep(.el-input__wrapper.is-focus),
.login-form :deep(.el-select__wrapper.is-focused) {
  border-color: var(--login-accent);
  box-shadow: 0 0 0 3px rgba(17, 106, 91, 0.12);
}

.login-form :deep(.el-input__prefix),
.login-form :deep(.el-select__prefix) {
  color: #70817b;
  font-size: 17px;
}

.login-form :deep(.el-form-item.is-error .el-input__wrapper),
.login-form :deep(.el-form-item.is-error .el-select__wrapper) {
  border-color: #c64d3d;
  box-shadow: 0 0 0 3px rgba(198, 77, 61, 0.1);
}

.form-options {
  min-height: 26px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin: 0 0 18px;
}

.form-options :deep(.el-checkbox__label) {
  color: #52615c;
  font-size: 13px;
}

.form-options :deep(.el-checkbox__input.is-checked .el-checkbox__inner) {
  border-color: var(--login-accent);
  background: var(--login-accent);
}

.secure-note {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #82908b;
  font-size: 12px;
  white-space: nowrap;
}

.login-submit {
  width: 100%;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  border-color: var(--login-accent);
  border-radius: 6px;
  background: var(--login-accent);
  font-size: 15px;
  font-weight: 680;
}

.login-submit:hover,
.login-submit:focus {
  border-color: var(--login-accent-hover);
  background: var(--login-accent-hover);
}

.login-submit .el-icon {
  font-size: 17px;
}

.login-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 26px;
  padding-top: 19px;
  border-top: 1px solid #e5eae8;
  color: #929e9a;
  font-size: 10px;
  font-weight: 650;
}

.footer-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #3d9a6f;
}

.footer-status.is-error {
  color: #a34437;
}

.footer-status.is-error .status-dot {
  background: #c75a49;
}

@media (max-width: 1100px) {
  .scene-content {
    left: 46px;
    width: 42vw;
  }

  .scene-content h1 {
    font-size: 38px;
  }

  .scene-meta {
    left: 46px;
  }

  .login-workspace {
    padding-right: 36px;
    padding-left: 36px;
  }
}

@media (max-width: 860px) {
  .scene-content,
  .scene-meta {
    display: none;
  }
}

@media (max-width: 720px) {
  .login-page {
    min-height: 100%;
    display: block;
    overflow: visible;
    background: #f8faf9;
  }

  .login-scene {
    position: relative;
    inset: auto;
    height: 180px;
  }

  .login-scene > img {
    object-position: 38% 48%;
  }

  .scene-tint {
    background: rgba(8, 28, 23, 0.16);
  }

  .scene-content {
    top: auto;
    bottom: 20px;
    left: 24px;
    display: block;
    width: calc(100% - 48px);
    transform: none;
  }

  .scene-kicker {
    margin-bottom: 9px;
    font-size: 9px;
  }

  .scene-kicker span {
    width: 20px;
  }

  .scene-content h1 {
    font-size: 25px;
    line-height: 1.25;
  }

  .scene-content h1 br,
  .scene-subtitle {
    display: none;
  }

  .login-workspace {
    min-height: calc(100vh - 180px);
    align-items: flex-start;
    padding: 0;
    background: #f8faf9;
  }

  .login-shell {
    max-width: none;
    padding: 27px 24px 24px;
    border: 0;
    border-radius: 0;
    background: #f8faf9;
    box-shadow: none;
    backdrop-filter: none;
  }

  .brand-mark {
    width: 38px;
    height: 38px;
    font-size: 20px;
  }

  .brand-name {
    font-size: 18px;
  }

  .login-heading {
    margin: 25px 0 22px;
    padding-top: 21px;
  }

  .login-heading h2 {
    font-size: 25px;
  }

  .login-footer {
    margin-top: 24px;
  }
}

@media (max-width: 380px) {
  .login-workspace {
    padding-right: 18px;
    padding-left: 18px;
  }

  .form-options {
    gap: 10px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .login-form :deep(.el-input__wrapper),
  .login-form :deep(.el-select__wrapper) {
    transition: none;
  }
}
</style>
