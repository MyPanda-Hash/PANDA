<template>
  <div class="topbar">
    <!-- ===== 左：汉堡（移动端）+ Logo + 分隔线 + 账套（工厂）切换 ===== -->
    <div class="t-left">
      <el-icon class="hamburger" @click="app.toggleMobileNav()"><Menu /></el-icon>
      <div class="logo">轻<span>MES</span></div>
      <div class="t-split"></div>
      <el-dropdown @command="(f) => user.switchFactory(f)">
        <span
          class="factory"
          :aria-label="user.factoryName || tt('选择工厂')"
          :title="user.factoryName || tt('选择工厂')"
        >
          <el-icon><OfficeBuilding /></el-icon>
          <span class="factory-name">{{ user.factoryName || tt('选择工厂') }}</span>
          <el-icon class="caret"><ArrowDown /></el-icon>
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-for="f in user.factories" :key="f.code" :command="f">
              <span class="factory-item">{{ f.name }}</span>
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-tooltip :content="tt('刷新企业名字')" placement="bottom">
        <el-icon class="refresh-icon" @click="refreshFactories"><Refresh /></el-icon>
      </el-tooltip>
    </div>

    <!-- ===== 中：账号 / 认证状态·企业 / 登录日期 / 服务到期 ===== -->
    <div class="t-center">
      <span class="t-user-info" id="useraccount">{{ user.account || user.realName }}</span>
      <span class="attestation">
        <span class="att-box">
          <el-icon class="att-icon"><CircleCheckFilled /></el-icon>
          <span class="att-text">{{ tt('已认证') }}</span>
        </span>
        <span class="company">{{ user.factoryName }}</span>
      </span>
      <span class="t-user-info" id="logindate">{{ tt('登录日期') }} {{ user.loginDateText }}</span>
      <span class="t-user-info" id="serviceEndTime">{{ tt('服务到期') }} {{ user.serviceEnd }}</span>
    </div>

    <!-- ===== 右：搜索 / 更新公告 / 移动端 / 通知角标 / 全屏 / 帮助 / 用户 ===== -->
    <div class="t-right">
      <!-- 内嵌全局搜索（T+ 形态：顶栏内输入框 + 下拉结果） -->
      <div class="search-wrap" ref="searchWrapRef">
        <el-input
          v-model="keyword"
          class="search-input"
          :placeholder="tt('搜索-产品功能')"
          clearable
          @input="onSearchInput"
          @focus="onSearchInput"
          @keyup.enter="goFirst"
        >
          <template #suffix>
            <el-icon class="search-icon" @mousedown.prevent><Search /></el-icon>
          </template>
        </el-input>
        <div v-if="searchOpen" class="search-drop">
          <template v-if="matched.length">
            <div v-for="m in matched" :key="m.path" class="search-item" @mousedown.prevent @click="go(m)">
              <el-icon class="s-icon"><component :is="m.icon || 'Folder'" /></el-icon>
              <span class="s-content">
                <span class="s-title">{{ tt(m.fullTitle || m.title) }}</span>
                <span class="s-meta">
                  <span v-if="m.panelCode" class="s-code">{{ m.panelCode }}</span>
                  <span class="s-path" :title="m.path">{{ m.path }}</span>
                </span>
              </span>
            </div>
          </template>
          <div v-else class="search-empty">{{ tt('无匹配菜单') }}</div>
        </div>
      </div>

      <!-- 更新公告（new 角标） -->
      <el-popover v-model:visible="noticePop" placement="bottom-end" :width="340" trigger="click" @show="loadNotices">
        <template #reference>
          <span class="gonggao">
            {{ tt('更新公告') }}
            <span v-if="hasUnread" class="new-icon">new</span>
          </span>
        </template>
        <div class="nc-head">
          <span class="nc-title">{{ tt('更新公告') }}</span>
        </div>
        <div class="nc-list">
          <div v-for="n in notices" :key="n.id" class="nc-item" :class="{ unread: !n.read }" @click="openNotice(n)">
            <div class="nc-item-top">
              <span class="nc-item-title">{{ n.title }}</span>
              <span v-if="!n.read" class="nc-dot"></span>
            </div>
            <div class="nc-item-time">{{ n.time }}</div>
          </div>
          <el-empty v-if="!notices.length" :description="tt('暂无公告')" :image-size="50" />
        </div>
      </el-popover>

      <!-- 移动端 -->
      <el-tooltip :content="tt('移动端扫码报工（建设中）')" placement="bottom">
        <span class="bar-icon"><el-icon><Iphone /></el-icon></span>
      </el-tooltip>

      <!-- 待办 / 消息 / 预警 -->
      <NoticeCenter />

      <!-- 全屏 -->
      <el-tooltip :content="app.fullscreen ? tt('退出全屏') : tt('全屏')" placement="bottom">
        <span class="bar-icon" @click="app.toggleFullscreen"><el-icon><FullScreen /></el-icon></span>
      </el-tooltip>

      <!-- 界面语言（阶段 A：tt() 显示层翻译，纯前端热切换） -->
      <el-dropdown @command="(l) => locale.set(l)" popper-class="t-dropdown-popper">
        <span class="bar-icon lang-icon" tabindex="0" :title="tt('切换语言')">{{ locale.currentShort }}</span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item
              v-for="l in locale.available"
              :key="l.locale"
              :command="l.locale"
              :disabled="l.locale === locale.current"
            >
              {{ l.nameNative }}（{{ l.nameZh }}）
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>

      <!-- 帮助下拉 -->
      <el-dropdown @command="onHelpCommand" popper-class="t-dropdown-popper">
        <el-tooltip :content="tt('帮助')" placement="bottom">
          <span class="bar-icon"><el-icon><QuestionFilled /></el-icon></span>
        </el-tooltip>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="docs">{{ tt('帮助文档') }}</el-dropdown-item>
            <el-dropdown-item command="ai">{{ tt('AI 智能帮助') }}</el-dropdown-item>
            <el-dropdown-item command="guide">{{ tt('显示新手引导') }}</el-dropdown-item>
            <el-dropdown-item command="about" divided>{{ tt('关于') }}</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>

      <!-- 用户下拉 -->
      <el-dropdown @command="onUserCommand" popper-class="t-dropdown-popper">
        <span class="user">
          <el-icon class="user-img"><UserFilled /></el-icon>
          <span class="show-name">{{ user.realName }}</span>
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-if="user.isAdmin" command="org"><el-icon><OfficeBuilding /></el-icon>{{ tt('组织架构') }}</el-dropdown-item>
            <el-dropdown-item command="account"><el-icon><User /></el-icon>{{ tt('账号管理') }}</el-dropdown-item>
            <el-dropdown-item command="pwd"><el-icon><Key /></el-icon>{{ tt('修改密码') }}</el-dropdown-item>
            <el-dropdown-item command="ui"><el-icon><Setting /></el-icon>{{ tt('界面设置') }}</el-dropdown-item>
            <el-dropdown-item command="dark"><el-icon><Brush /></el-icon>{{ app.dark ? tt('切换亮色') : tt('换肤（暗色）') }}</el-dropdown-item>
            <el-dropdown-item command="desk"><el-icon><Monitor /></el-icon>{{ tt('工作台设置') }}</el-dropdown-item>
            <el-dropdown-item command="init"><el-icon><MagicStick /></el-icon>{{ tt('初始化向导') }}</el-dropdown-item>
            <el-dropdown-item command="logout" divided><el-icon><SwitchButton /></el-icon>{{ tt('退出') }}</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <!-- 公告详情 -->
    <el-dialog v-model="noticeDetailVisible" :title="tt('更新公告')" width="560px" append-to-body>
      <div v-if="currentNotice" class="nc-detail">
        <div class="nc-detail-head">
          <span class="nc-detail-title">{{ currentNotice.title }}</span>
          <el-tag v-if="!currentNotice.read" size="small" type="danger">new</el-tag>
        </div>
        <div class="nc-detail-time">{{ currentNotice.time }}</div>
        <div class="nc-detail-content">{{ currentNotice.content }}</div>
      </div>
    </el-dialog>

    <!-- 账号管理 -->
    <el-dialog v-model="accountVisible" :title="tt('账号管理')" width="440px" append-to-body>
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item :label="tt('账号')">{{ user.account }}</el-descriptions-item>
        <el-descriptions-item :label="tt('姓名')">{{ user.realName }}</el-descriptions-item>
        <el-descriptions-item :label="tt('当前工厂')">{{ user.factoryName }}</el-descriptions-item>
        <el-descriptions-item :label="tt('角色')">{{ (user.userInfo?.roles || []).join('、') || tt('未分配') }}</el-descriptions-item>
        <el-descriptions-item :label="tt('服务到期')">{{ user.serviceEnd }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <!-- 修改密码 -->
    <el-dialog v-model="pwdVisible" :title="tt('修改密码')" width="420px" append-to-body>
      <el-form :model="pwdForm" label-width="80px">
        <el-form-item :label="tt('原密码')">
          <el-input v-model="pwdForm.old" type="password" show-password :placeholder="tt('请输入原密码')" />
        </el-form-item>
        <el-form-item :label="tt('新密码')">
          <el-input v-model="pwdForm.next" type="password" show-password :placeholder="tt('至少 6 位')" />
        </el-form-item>
        <el-form-item :label="tt('确认密码')">
          <el-input v-model="pwdForm.confirm" type="password" show-password :placeholder="tt('再次输入新密码')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdVisible = false">{{ tt('取消') }}</el-button>
        <el-button type="primary" @click="changePwd">{{ tt('确定') }}</el-button>
      </template>
    </el-dialog>

    <!-- 关于 -->
    <el-dialog v-model="aboutVisible" :title="tt('关于轻MES')" width="420px" append-to-body>
      <div class="about">
        <div class="about-logo">轻<span>MES</span></div>
        <div class="about-row">{{ tt('版本：v0.2.0（门户壳 T+ 形态）') }}</div>
        <div class="about-row">{{ tt('参考：畅捷通 T+ 门户（h2t.chanjet.com）') }}</div>
        <div class="about-row">{{ tt('技术栈：Vue3 + Element Plus / Spring Boot 3 + MySQL') }}</div>
        <div class="about-row about-copy">{{ tt('© 2026 轻MES 项目组') }}</div>
      </div>
    </el-dialog>

    <UiSettingsDialog v-model="uiSettingVisible" />
    <DeskSettingsDialog v-model="deskSettingVisible" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'
import { useTabsStore } from '@/stores/tabs'
import { useLocaleStore } from '@/stores/locale'
import { tt } from '@/i18n'
import { flatMenus, menuTree as rawMenuTree, filterMenuTree } from '@/business/menus'
import { apiGetNotices } from '@/business/api'
import { ElMessage } from 'element-plus'
import NoticeCenter from './NoticeCenter.vue'
import UiSettingsDialog from './UiSettingsDialog.vue'
import DeskSettingsDialog from './DeskSettingsDialog.vue'

const app = useAppStore()
const user = useUserStore()
const tabs = useTabsStore()
const locale = useLocaleStore()
const router = useRouter()

// ---------- 内嵌搜索 ----------
const keyword = ref('')
const searchOpen = ref(false)
const searchWrapRef = ref(null)

const matched = computed(() => {
  const k = keyword.value.trim().toLowerCase()
  if (!k) return []
  return flatMenus(filterMenuTree(rawMenuTree, user.visiblePanels, user.isAdmin))
    .filter((m) => {
      if (!m.path) return false
      return [m.title, m.fullTitle, m.panelCode, m.path]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(k))
    })
    .slice(0, 10)
})

function onSearchInput() {
  searchOpen.value = !!keyword.value.trim()
}

function go(m) {
  router.push(m.path)
  tabs.open(m)
  keyword.value = ''
  searchOpen.value = false
}

function goFirst() {
  if (matched.value.length) go(matched.value[0])
}

function onDocClick(e) {
  if (searchWrapRef.value && !searchWrapRef.value.contains(e.target)) searchOpen.value = false
}

onMounted(() => {
  document.addEventListener('mousedown', onDocClick)
  // App 挂载时（登录页）尚无 token，/locale/list 会 401；顶栏登录后才挂载，此时补拉语言注册表
  locale.loadAvailable()
})
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocClick))

// ---------- 更新公告 ----------
const noticePop = ref(false)
const notices = ref([])
const noticeDetailVisible = ref(false)
const currentNotice = ref(null)

const hasUnread = computed(() => notices.value.some((n) => !n.read))

async function loadNotices() {
  if (notices.value.length) return
  try {
    notices.value = await apiGetNotices('notice')
  } catch (e) {}
}

function openNotice(n) {
  currentNotice.value = n
  noticePop.value = false
  noticeDetailVisible.value = true
}

// ---------- 工厂 ----------
async function refreshFactories() {
  await user.fetchFactories()
  ElMessage.success(tt('企业信息已刷新'))
}

// ---------- 帮助下拉 ----------
function onHelpCommand(cmd) {
  if (cmd === 'docs') app.openHelp('help')
  else if (cmd === 'ai') app.openHelp('knowledge')
  else if (cmd === 'guide') app.openInitWizard()
  else if (cmd === 'about') aboutVisible.value = true
}

// ---------- 用户下拉 ----------
const uiSettingVisible = ref(false)
const deskSettingVisible = ref(false)
const accountVisible = ref(false)
const pwdVisible = ref(false)
const aboutVisible = ref(false)
const pwdForm = ref({ old: '', next: '', confirm: '' })

function onUserCommand(cmd) {
  if (cmd === 'account') accountVisible.value = true
  else if (cmd === 'org') router.push('/sys/org')
  else if (cmd === 'pwd') pwdVisible.value = true
  else if (cmd === 'ui') uiSettingVisible.value = true
  else if (cmd === 'dark') app.toggleDark()
  else if (cmd === 'desk') deskSettingVisible.value = true
  else if (cmd === 'init') app.openInitWizard()
  else if (cmd === 'logout') {
    user.logout()
    router.replace('/login')
  }
}

function changePwd() {
  const f = pwdForm.value
  if (!f.old || !f.next || !f.confirm) return ElMessage.warning(tt('请填写完整'))
  if (f.old !== '123456') return ElMessage.error(tt('原密码不正确（演示账号原密码 123456）'))
  if (f.next.length < 6) return ElMessage.warning(tt('新密码至少 6 位'))
  if (f.next !== f.confirm) return ElMessage.warning(tt('两次输入的新密码不一致'))
  pwdVisible.value = false
  pwdForm.value = { old: '', next: '', confirm: '' }
  ElMessage.success(tt('密码修改成功（演示环境不落库）'))
}
</script>

<style scoped>
.topbar {
  height: 52px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  padding: 0 14px;
  background: var(--t-navbar-bg);
  color: var(--t-navbar-text);
  border-bottom: 1px solid var(--t-border);
}

/* ---------- 左 ---------- */
.t-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.logo {
  font-size: 19px;
  font-weight: 700;
  color: var(--t-primary);
  letter-spacing: 0;
  cursor: default;
}
.logo span {
  font-weight: 400;
  font-size: 13px;
  color: var(--t-text-1);
}
.t-split {
  width: 1px;
  height: 18px;
  background: #c8ccd2;
}
.factory {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
  color: var(--t-navbar-text);
  font-size: 13px;
  outline: none;
}
.factory-name {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.caret {
  font-size: 12px;
  color: var(--t-navbar-info);
}
.refresh-icon {
  font-size: 14px;
  color: var(--t-navbar-info);
  cursor: pointer;
}
.refresh-icon:hover {
  color: var(--t-primary);
}

/* ---------- 中 ---------- */
.t-center {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-left: 20px;
  overflow: hidden;
}
.t-user-info {
  font-size: 12px;
  color: var(--t-navbar-info);
  white-space: nowrap;
}
.attestation {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  white-space: nowrap;
}
.att-box {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: #67c23a;
}
.att-icon {
  font-size: 13px;
}
.att-text {
  color: #67c23a;
}
.company {
  color: var(--t-navbar-text);
}

/* ---------- 右 ---------- */
.t-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 14px;
  flex-shrink: 0;
}

.search-wrap {
  position: relative;
}
.search-input {
  width: 190px;
}
.search-input :deep(.el-input__wrapper) {
  border: 1px solid var(--t-border);
  border-radius: 5px;
  height: 32px;
  background: var(--t-sidebar-bg);
  box-shadow: none;
}
.search-icon {
  color: var(--t-text-1);
  cursor: pointer;
}
.search-drop {
  position: absolute;
  top: 34px;
  right: 0;
  width: min(420px, calc(100vw - 24px));
  background: var(--t-card-bg);
  border: 1px solid var(--t-border);
  border-radius: 6px;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.12);
  z-index: 4000;
  max-height: min(420px, calc(100vh - 72px));
  overflow: auto;
  padding: 6px;
}
.search-item {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr);
  align-items: start;
  gap: 10px;
  min-height: 54px;
  padding: 8px 10px;
  border-radius: 4px;
  font-size: 13px;
  color: var(--t-text-1);
  cursor: pointer;
}
.search-item:hover {
  background: var(--t-hover-bg);
  color: var(--t-primary);
}
.s-icon {
  margin-top: 2px;
  font-size: 16px;
  color: var(--t-primary);
}
.s-content {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.s-title {
  display: block;
  line-height: 20px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.s-meta {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 7px;
  line-height: 16px;
}
.s-code {
  flex-shrink: 0;
  padding: 0 4px;
  border: 1px solid var(--t-border-light);
  border-radius: 3px;
  color: var(--t-text-2);
  background: var(--t-content-bg);
  font-size: 10px;
}
.s-path {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: var(--t-text-3);
}
.search-empty {
  padding: 14px;
  text-align: center;
  font-size: 12px;
  color: var(--t-text-3);
}

.gonggao {
  font-size: 13px;
  color: var(--t-navbar-text);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
  outline: none;
}
.gonggao:hover {
  color: var(--t-primary);
}
.new-icon {
  font-size: 10px;
  line-height: 1;
  padding: 2px 4px;
  border-radius: 2px;
  background: var(--t-badge);
  color: #fff;
  font-style: italic;
}

.bar-icon {
  display: inline-flex;
  font-size: 17px;
  cursor: pointer;
  color: var(--t-navbar-text);
  outline: none;
}
.bar-icon:hover {
  color: var(--t-primary);
}
.lang-icon {
  font-size: 12px;
  font-weight: 600;
  min-width: 26px;
  justify-content: center;
  outline: none;
}

.user {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  font-size: 13px;
  color: var(--t-navbar-text);
  outline: none;
}
.user:hover {
  color: var(--t-primary);
}
.user-img {
  font-size: 15px;
  color: var(--t-navbar-info);
}
.show-name {
  max-width: 80px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---------- 弹层 ---------- */
.nc-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--t-border-light);
  padding-bottom: 8px;
  margin-bottom: 6px;
}
.nc-title {
  font-weight: 600;
  font-size: 14px;
}
.nc-list {
  max-height: 320px;
  overflow: auto;
}
.nc-item {
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
}
.nc-item:hover {
  background: var(--t-hover-bg);
}
.nc-item-top {
  display: flex;
  align-items: center;
  gap: 6px;
}
.nc-item-title {
  font-size: 13px;
  color: var(--t-text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nc-item.unread .nc-item-title {
  font-weight: 600;
}
.nc-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--t-badge);
  flex-shrink: 0;
}
.nc-item-time {
  font-size: 12px;
  color: var(--t-text-3);
  margin-top: 2px;
}
.nc-detail-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.nc-detail-title {
  font-size: 15px;
  font-weight: 600;
}
.nc-detail-time {
  font-size: 12px;
  color: var(--t-text-3);
  margin: 8px 0;
}
.nc-detail-content {
  font-size: 13px;
  line-height: 1.8;
  color: var(--t-text-2);
  background: var(--t-content-bg);
  border-radius: 8px;
  padding: 12px;
  min-height: 80px;
}

.about {
  text-align: center;
  padding: 8px 0;
}
.about-logo {
  font-size: 26px;
  font-weight: 700;
  color: var(--t-primary);
  margin-bottom: 14px;
}
.about-logo span {
  font-weight: 400;
  font-size: 15px;
  color: var(--t-text-1);
}
.about-row {
  font-size: 13px;
  color: var(--t-text-2);
  line-height: 2;
}
.about-copy {
  margin-top: 8px;
  color: var(--t-text-3);
}

/* ===== 移动端（≤768px）：汉堡按钮 + 顶栏精简 ===== */
.hamburger {
  display: none;
  font-size: 20px;
  color: var(--t-text-1);
  cursor: pointer;
  padding: 4px;
  border-radius: 6px;
}
.hamburger:hover {
  background: var(--t-hover-bg);
  color: var(--t-primary);
}

@media (min-width: 769px) and (max-width: 900px) {
  .search-drop {
    position: fixed;
    top: 44px;
    right: auto;
    left: 50%;
    transform: translateX(-50%);
  }
}

@media (max-width: 768px) {
  .topbar {
    height: 54px;
    padding: 0 8px;
  }

  .hamburger {
    width: 32px;
    height: 32px;
    display: inline-grid;
    place-items: center;
    padding: 0;
  }

  .t-left {
    min-width: 0;
    gap: 4px;
  }

  .logo {
    font-size: 17px;
    white-space: nowrap;
  }

  .logo span {
    font-size: 11px;
  }

  .t-split {
    display: none;
  }

  /* 中段信息（账号/认证/登录日期/到期）手机隐藏 */
  .t-center {
    display: none;
  }
  /* 搜索框：手机隐藏（菜单抽屉内含单据查询/新增入口） */
  .search-wrap {
    display: none;
  }
  /* 次要图标与文字入口隐藏，保留通知/用户 */
  .refresh-icon,
  .gonggao,
  .bar-icon {
    display: none;
  }

  .factory {
    width: 32px;
    height: 32px;
    display: grid;
    place-items: center;
    border: 1px solid var(--t-border);
    border-radius: 5px;
    background: var(--t-sidebar-bg);
    font-size: 16px;
  }

  .factory-name,
  .factory .caret {
    display: none;
  }

  .t-right {
    gap: 6px;
  }

  .user {
    width: 34px;
    height: 34px;
    display: grid;
    place-items: center;
    border: 1px solid var(--t-border);
    border-radius: 5px;
    background: var(--t-sidebar-bg);
  }

  .user-img {
    font-size: 17px;
  }

  .show-name {
    display: none;
  }
}

@media (max-width: 360px) {
  .topbar {
    padding-right: 6px;
    padding-left: 6px;
  }

  .t-left {
    gap: 2px;
  }

  .logo span {
    display: none;
  }

  .t-right {
    gap: 4px;
  }
}
</style>
