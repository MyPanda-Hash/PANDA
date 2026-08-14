<template>
  <el-drawer
    :model-value="app.helpVisible"
    direction="rtl"
    size="380px"
    :with-header="false"
    append-to-body
    @close="app.closeHelp()"
  >
    <div class="help-panel">
      <div class="hp-titlebar">
        <ul class="hp-tabs">
          <li
            v-for="t in TABS"
            :key="t.key"
            class="hp-tab"
            :class="{ active: app.helpTab === t.key }"
            @click="app.helpTab = t.key"
          >
            <span>{{ t.title }}</span>
            <span v-if="t.key === 'message' && unread" class="hp-badge">{{ unread }}</span>
          </li>
        </ul>
      </div>

      <div class="hp-body">
        <!-- 动态 -->
        <div v-if="app.helpTab === 'dynamic'" class="hp-list">
          <div v-for="d in dynamics" :key="d.id" class="hp-dyn">
            <div class="hp-dyn-title">{{ d.title }}</div>
            <div class="hp-dyn-desc">{{ d.desc }}</div>
            <div class="hp-dyn-time">{{ d.time }}</div>
          </div>
        </div>

        <!-- 消息 -->
        <div v-if="app.helpTab === 'message'" class="hp-list">
          <div v-for="m in messages" :key="m.id" class="hp-msg" :class="{ unread: !m.read }">
            <div class="hp-msg-top">
              <span class="hp-msg-title">{{ m.title }}</span>
              <span v-if="!m.read" class="hp-dot"></span>
            </div>
            <div class="hp-msg-time">{{ m.time }}</div>
          </div>
          <el-empty v-if="!messages.length" description="暂无消息" :image-size="50" />
        </div>

        <!-- 知识库 -->
        <div v-if="app.helpTab === 'knowledge'">
          <el-collapse v-model="openFaqs">
            <el-collapse-item v-for="f in faqs" :key="f.q" :name="f.q">
              <template #title>
                <span class="hp-faq-q">{{ f.q }}</span>
              </template>
              <div class="hp-faq-a">{{ f.a }}</div>
            </el-collapse-item>
          </el-collapse>
        </div>

        <!-- 帮助教程 -->
        <div v-if="app.helpTab === 'help'" class="hp-list">
          <div class="hp-section-title">新手引导</div>
          <div v-for="(s, i) in guide" :key="s.title" class="hp-guide">
            <span class="hp-guide-step">{{ i + 1 }}</span>
            <div class="hp-guide-body">
              <div class="hp-guide-title">{{ s.title }}</div>
              <div class="hp-guide-desc">{{ s.desc }}</div>
            </div>
          </div>
          <div class="hp-section-title mt16">常用操作</div>
          <div v-for="s in shortcuts" :key="s.title" class="hp-shortcut" @click="doShortcut(s)">
            <el-icon><component :is="s.icon" /></el-icon>
            <span>{{ s.title }}</span>
          </div>
        </div>
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useTabsStore } from '@/stores/tabs'

const app = useAppStore()
const tabs = useTabsStore()
const router = useRouter()

const TABS = [
  { key: 'dynamic', title: '动态' },
  { key: 'message', title: '消息' },
  { key: 'knowledge', title: '知识库' },
  { key: 'help', title: '帮助教程' },
]

const unread = ref(2)
const openFaqs = ref([])

const dynamics = [
  { id: 1, title: '生产加工单面板上线', desc: '首个 PanelX 配置驱动单据：支持新增/审核/弃审/关闭全流程。', time: '2026-08-13' },
  { id: 2, title: '门户壳升级为 T+ 形态', desc: '顶栏三段式、页签快捷按钮组、帮助面板与初始化向导。', time: '2026-08-13' },
  { id: 3, title: '消息通知中心上线', desc: '待办/消息/预警角标 + 详情弹窗（上一条/下一条/历史消息）。', time: '2026-08-12' },
  { id: 4, title: '工作台支持个性化设置', desc: '快捷入口与 KPI/进度/待办卡片可显隐配置。', time: '2026-08-12' },
]

const messages = [
  { id: 1, title: '管理员：本周五 18:00 系统例行维护', read: false, time: '2026-08-13 10:00' },
  { id: 2, title: '新功能：帮助面板与初始化向导已上线', read: false, time: '2026-08-13 09:00' },
  { id: 3, title: '您的角色已开通「生产管理」模块权限', read: true, time: '2026-08-12 15:00' },
]

const faqs = [
  { q: '如何新建一张生产加工单？', a: '点击左侧菜单「单据查询/新增单据」或在生产管理模块打开「生产加工单」，点击工具栏「新增流程」按钮，填写合同号、锭号、批号等必填项后提交。' },
  { q: '单据状态是如何流转的？', a: '草稿 → 提交后待审核 → 审核通过进入「已审核」→ 开工后「生产中」→ 完工后「已完工」→ 归档「已关闭」。草稿状态可编辑删除，审核后可弃审回退。' },
  { q: '如何切换工厂（账套）？', a: '点击顶栏左侧工厂名称下拉，选择目标工厂即可切换，业务数据按工厂隔离。' },
  { q: '暗色模式如何开启？', a: '点击顶栏右侧用户头像下拉菜单，选择「换肤（暗色）」，或在「界面设置」中切换。' },
  { q: '找不到某个功能菜单怎么办？', a: '使用顶栏「搜索-产品功能」输入菜单关键字全局搜索，点击结果直达对应页面。' },
]

const guide = [
  { title: '完成初始化向导', desc: '选择行业细分与经营业态，自动匹配专属桌面与菜单。' },
  { title: '维护基础资料', desc: '在「基础设置」录入存货、物料清单、工艺路线、仓库与工序。' },
  { title: '录入期初数据', desc: '在「初始化」录入库存期初余额与期初单据。' },
  { title: '开始日常业务', desc: '从生产加工单开始，按 报工→出入库→质检 的流程运转。' },
]

const shortcuts = [
  { title: '新建加工单', icon: 'DocumentAdd', path: '/panelx/form/MANU_ORDER?new=1', tabPath: '/panelx/form/MANU_ORDER', tabTitle: '新增加工单' },
  { title: '打开加工单列表', icon: 'List', path: '/panelx/list/MANU_ORDER', tabPath: '/panelx/list/MANU_ORDER', tabTitle: '生产加工单' },
  { title: '我的桌面', icon: 'HomeFilled', path: '/dashboard', tabPath: '/dashboard', tabTitle: '我的桌面' },
  { title: '初始化向导', icon: 'MagicStick', action: 'init' },
]

function doShortcut(s) {
  if (s.action === 'init') {
    app.closeHelp()
    app.openInitWizard()
    return
  }
  tabs.open({ title: s.tabTitle, path: s.tabPath })
  router.push(s.path)
  app.closeHelp()
}
</script>

<style scoped>
.help-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.hp-titlebar {
  flex-shrink: 0;
  border-bottom: 1px solid var(--t-border);
  background: var(--t-content-bg);
}
.hp-tabs {
  display: flex;
  margin: 0;
  padding: 0;
  list-style: none;
}
.hp-tab {
  flex: 1;
  text-align: center;
  padding: 12px 0 10px;
  font-size: 13px;
  color: var(--t-text-2);
  cursor: pointer;
  border-bottom: 3px solid transparent;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
}
.hp-tab.active {
  color: var(--t-primary);
  border-bottom-color: var(--t-primary);
  font-weight: 600;
}
.hp-badge {
  font-size: 11px;
  min-width: 16px;
  height: 16px;
  line-height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: var(--t-badge);
  color: #fff;
}
.hp-body {
  flex: 1;
  overflow: auto;
  padding: 12px 14px;
  background: var(--t-card-bg);
}
.hp-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.hp-dyn {
  padding: 10px 12px;
  border: 1px solid var(--t-border-light);
  border-radius: 8px;
}
.hp-dyn:hover {
  border-color: var(--t-primary);
}
.hp-dyn-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--t-text-1);
}
.hp-dyn-desc {
  font-size: 12px;
  color: var(--t-text-2);
  line-height: 1.7;
  margin-top: 4px;
}
.hp-dyn-time {
  font-size: 11px;
  color: var(--t-text-3);
  margin-top: 6px;
}
.hp-msg {
  padding: 9px 12px;
  border-radius: 6px;
  cursor: pointer;
}
.hp-msg:hover {
  background: var(--t-hover-bg);
}
.hp-msg-top {
  display: flex;
  align-items: center;
  gap: 6px;
}
.hp-msg-title {
  font-size: 13px;
  color: var(--t-text-1);
}
.hp-msg.unread .hp-msg-title {
  font-weight: 600;
}
.hp-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--t-badge);
  flex-shrink: 0;
}
.hp-msg-time {
  font-size: 12px;
  color: var(--t-text-3);
  margin-top: 2px;
}
.hp-faq-q {
  font-size: 13px;
  color: var(--t-text-1);
}
.hp-faq-a {
  font-size: 12px;
  color: var(--t-text-2);
  line-height: 1.8;
  padding: 0 4px 8px;
}
.hp-section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--t-text-1);
  padding: 6px 0;
  border-bottom: 1px solid var(--t-border-light);
  margin-bottom: 4px;
}
.mt16 {
  margin-top: 16px;
}
.hp-guide {
  display: flex;
  gap: 10px;
  padding: 8px 4px;
}
.hp-guide-step {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  line-height: 20px;
  text-align: center;
  border-radius: 50%;
  background: var(--t-primary);
  color: #fff;
  font-size: 12px;
  margin-top: 1px;
}
.hp-guide-title {
  font-size: 13px;
  color: var(--t-text-1);
  font-weight: 600;
}
.hp-guide-desc {
  font-size: 12px;
  color: var(--t-text-2);
  line-height: 1.7;
  margin-top: 2px;
}
.hp-shortcut {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--t-text-1);
  cursor: pointer;
}
.hp-shortcut:hover {
  background: var(--t-hover-bg);
  color: var(--t-primary);
}
</style>
