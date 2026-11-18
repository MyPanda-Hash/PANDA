<!-- SystemConsole.vue — 系统管理平台（系统功能入口）
     从 yinjia 项目搬过来。三块：账号信息 / 修改密码 / 显示设置。
     改密以前是纯前端假实现（硬编码 123456），现在走后端 POST /api/auth/password。 -->
<template>
  <div class="sys-console adx">
    <div class="sys-col">
      <el-card shadow="never" class="sys-card">
        <template #header>
          <div class="card-head">
            <span class="card-title">{{ tt('账号信息') }}</span>
            <el-button link type="primary" size="small" @click="loadMe">{{ tt('刷新') }}</el-button>
          </div>
        </template>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item :label="tt('账号')">{{ info.userName || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="tt('姓名')">{{ info.realName || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="tt('当前账套')">{{ info.factoryCode || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="tt('角色')">{{ roleText }}</el-descriptions-item>
          <el-descriptions-item :label="tt('管理员')">{{ info.isAdmin ? tt('是') : tt('否') }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-card shadow="never" class="sys-card">
        <template #header>
          <div class="card-head"><span class="card-title">{{ tt('修改密码') }}</span></div>
        </template>
        <el-form :model="pwdForm" label-width="90px" @submit.prevent>
          <el-form-item :label="tt('原密码')">
            <el-input v-model="pwdForm.old" type="password" show-password :placeholder="tt('请输入原密码')" />
          </el-form-item>
          <el-form-item :label="tt('新密码')">
            <el-input v-model="pwdForm.next" type="password" show-password :placeholder="tt('至少 6 位')" />
          </el-form-item>
          <el-form-item :label="tt('确认密码')">
            <el-input v-model="pwdForm.confirm" type="password" show-password :placeholder="tt('再次输入新密码')" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="pwdLoading" @click="changePwd">{{ tt('确定') }}</el-button>
          </el-form-item>
        </el-form>
        <div class="card-tip">{{ tt('改密后请用新密码重新登录。') }}</div>
      </el-card>
    </div>

    <div class="sys-col">
      <el-card shadow="never" class="sys-card">
        <template #header>
          <div class="card-head"><span class="card-title">{{ tt('显示设置') }}</span></div>
        </template>
        <div class="set-row">
          <span class="set-label">{{ tt('暗色模式') }}</span>
          <el-switch :model-value="app.dark" @change="app.toggleDark()" />
        </div>
        <div class="set-row">
          <span class="set-label">{{ tt('界面设置') }}</span>
          <el-button size="small" @click="uiVisible = true">{{ tt('打开') }}</el-button>
        </div>
        <div class="set-row">
          <span class="set-label">{{ tt('工作台设置') }}</span>
          <el-button size="small" @click="deskVisible = true">{{ tt('打开') }}</el-button>
        </div>
        <div class="card-tip">{{ tt('设置实时生效并保存到本机浏览器。') }}</div>
      </el-card>

      <el-card v-if="info.isAdmin" shadow="never" class="sys-card">
        <template #header>
          <div class="card-head"><span class="card-title">{{ tt('管理员功能') }}</span></div>
        </template>
        <div class="entry-row">
          <el-button size="small" @click="go('/sys/org')">{{ tt('组织架构') }}</el-button>
          <el-button size="small" @click="go('/sys/usage')">{{ tt('使用权限查看') }}</el-button>
          <el-button size="small" @click="go('/sys/backup')">{{ tt('数据备份') }}</el-button>
        </div>
      </el-card>
    </div>

    <UiSettingsDialog v-model="uiVisible" />
    <DeskSettingsDialog v-model="deskVisible" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import request from '@/core/request'
import { tt } from '@/i18n'
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'
import UiSettingsDialog from '@/layout/UiSettingsDialog.vue'
import DeskSettingsDialog from '@/layout/DeskSettingsDialog.vue'

const router = useRouter()
const app = useAppStore()
const user = useUserStore()

const uiVisible = ref(false)
const deskVisible = ref(false)
const pwdForm = ref({ old: '', next: '', confirm: '' })
const pwdLoading = ref(false)

const info = computed(() => user.userInfo || {})
const roleText = computed(() => {
  const code = String(info.value.roleCode || '').trim()
  if (!code) return tt('未分配')
  return code
})

async function changePwd() {
  const f = pwdForm.value
  if (!f.old || !f.next || !f.confirm) return ElMessage.warning(tt('请填写完整'))
  if (f.next.length < 6) return ElMessage.warning(tt('新密码至少 6 位'))
  if (f.next !== f.confirm) return ElMessage.warning(tt('两次输入的新密码不一致'))
  pwdLoading.value = true
  try {
    // 真接口：后端会校验原密码、落库新哈希。以前这里是硬编码 123456 的假实现。
    await request.post('/auth/password', { old: f.old, next: f.next })
    pwdForm.value = { old: '', next: '', confirm: '' }
    ElMessage.success(tt('密码修改成功，请用新密码重新登录'))
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || e?.message || tt('修改密码失败'))
  } finally {
    pwdLoading.value = false
  }
}

async function loadMe() {
  try {
    await user.fetchUserInfo?.()
    await user.fetchPerms?.()
    ElMessage.success(tt('已刷新'))
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || e?.message || tt('刷新失败'))
  }
}

function go(path) {
  router.push(path)
}

onMounted(() => {
  // 本页只展示已登录账号自身信息，不额外请求；「刷新」按钮才重拉。
})
</script>

<style scoped>
.sys-console {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
  padding: 14px;
  box-sizing: border-box;
  align-items: start;
  max-width: 1180px;
}
.sys-col { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.card-head { display: flex; justify-content: space-between; align-items: center; }
.card-title { font-size: 14px; font-weight: 600; }
.card-tip { margin-top: 8px; font-size: 12px; line-height: 1.6; color: var(--el-text-color-secondary); }
.set-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--el-border-color-lighter);
}
.set-row:last-of-type { border-bottom: none; }
.set-label { font-size: 13px; }
.entry-row { display: flex; gap: 8px; flex-wrap: wrap; }

@media (max-width: 900px) {
  .sys-console { grid-template-columns: minmax(0, 1fr); }
}
</style>