<template>
  <teleport to="body">
    <div v-if="app.initWizardVisible" class="wizard-mask">
      <div class="wizard">
        <!-- 头部 -->
        <div class="wz-header">
          <div class="wz-close" @click="skip">
            <el-icon><Close /></el-icon>
          </div>
          <div class="wz-title">MES 初始化配置</div>
          <div class="wz-subtitle">三步即可轻松完成行业功能初始化流程</div>
        </div>

        <!-- 步骤指示 -->
        <div class="wz-steps">
          <div class="wz-step" :class="{ done: step > 1, active: step === 1 }">
            <span class="wz-step-no">1</span>
            <div class="wz-step-text">
              <div class="wz-step-name">第一步</div>
              <div class="wz-step-desc">选择行业细分 & 经营业态</div>
            </div>
          </div>
          <div class="wz-step-arrow"><el-icon><ArrowRight /></el-icon></div>
          <div class="wz-step" :class="{ done: step > 2, active: step === 2 }">
            <span class="wz-step-no">2</span>
            <div class="wz-step-text">
              <div class="wz-step-name">第二步</div>
              <div class="wz-step-desc">行业特性选择 & 设置</div>
            </div>
          </div>
          <div class="wz-step-arrow"><el-icon><ArrowRight /></el-icon></div>
          <div class="wz-step" :class="{ done: step > 3, active: step === 3 }">
            <span class="wz-step-no">3</span>
            <div class="wz-step-text">
              <div class="wz-step-name">第三步</div>
              <div class="wz-step-desc">完成初始化设置</div>
            </div>
          </div>
        </div>

        <!-- 第一步：行业 & 业态 -->
        <div v-if="step === 1" class="wz-body">
          <div class="wz-body-title">选择行业细分</div>
          <div class="wz-cards">
            <div
              v-for="i in industries"
              :key="i.code"
              class="wz-card"
              :class="{ active: form.industry === i.code }"
              @click="form.industry = i.code"
            >
              <el-icon class="wz-card-icon"><component :is="i.icon" /></el-icon>
              <span class="wz-card-name">{{ i.name }}</span>
              <span class="wz-card-desc">{{ i.desc }}</span>
            </div>
          </div>
          <div class="wz-body-title">选择经营业态</div>
          <div class="wz-cards">
            <div
              v-for="b in business"
              :key="b.code"
              class="wz-card small"
              :class="{ active: form.business === b.code }"
              @click="form.business = b.code"
            >
              <span class="wz-card-name">{{ b.name }}</span>
              <span class="wz-card-desc">{{ b.desc }}</span>
            </div>
          </div>
        </div>

        <!-- 第二步：特性选择 -->
        <div v-if="step === 2" class="wz-body">
          <div class="wz-body-title">选择启用模块</div>
          <div class="wz-cards">
            <div
              v-for="m in modules"
              :key="m.code"
              class="wz-card small checkable"
              :class="{ active: form.modules.includes(m.code) }"
              @click="toggleModule(m.code)"
            >
              <span class="wz-check"><el-icon v-if="form.modules.includes(m.code)"><Check /></el-icon></span>
              <span class="wz-card-name">{{ m.name }}</span>
              <span class="wz-card-desc">{{ m.desc }}</span>
            </div>
          </div>
          <div class="wz-body-title">报工方式</div>
          <el-radio-group v-model="form.reportMode" class="wz-radio-group">
            <el-radio-button value="scan">扫码报工（推荐）</el-radio-button>
            <el-radio-button value="manual">手工报工</el-radio-button>
            <el-radio-button value="batch">批量报工</el-radio-button>
          </el-radio-group>
        </div>

        <!-- 第三步：完成 -->
        <div v-if="step === 3" class="wz-body">
          <div class="wz-result">
            <div class="wz-result-icon">
              <el-icon><CircleCheckFilled /></el-icon>
            </div>
            <div class="wz-result-text">
              完成行业化设置，自动为您匹配<br />
              <span class="wz-result-hl">全新的专属桌面、菜单、选项功能、关注指标及相关报表</span>
            </div>
          </div>
          <div class="wz-summary">
            <div class="wz-summary-row">
              <span class="wz-summary-label">行业细分</span>
              <span>{{ industryName }}</span>
            </div>
            <div class="wz-summary-row">
              <span class="wz-summary-label">经营业态</span>
              <span>{{ businessName }}</span>
            </div>
            <div class="wz-summary-row">
              <span class="wz-summary-label">启用模块</span>
              <span>{{ moduleNames || '仅基础模块' }}</span>
            </div>
            <div class="wz-summary-row">
              <span class="wz-summary-label">报工方式</span>
              <span>{{ reportModeName }}</span>
            </div>
            <div class="wz-summary-row">
              <span class="wz-summary-label">当前工厂</span>
              <span>{{ user.factoryName }}</span>
            </div>
          </div>
        </div>

        <!-- 底部按钮 -->
        <div class="wz-footer">
          <div v-if="step === 1" class="wz-start" @click="next">开始配置</div>
          <template v-else>
            <div class="wz-back" @click="prev">上一步</div>
            <div v-if="step === 2" class="wz-start" @click="next">下一步</div>
            <div v-else class="wz-start" @click="finish">完成设置</div>
          </template>
          <div class="wz-skip" @click="skip">下次再说</div>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'
import { ElMessage } from 'element-plus'

const app = useAppStore()
const user = useUserStore()

const step = ref(1)

const industries = [
  { code: 'metal', name: '金属制品加工', desc: '铸造/锻压/热处理/机加工', icon: 'Coin' },
  { code: 'auto', name: '汽车零部件', desc: '车削件/冲压件/总成', icon: 'Van' },
  { code: 'elec', name: '电子制造', desc: 'SMT/组装/测试', icon: 'Cpu' },
  { code: 'mach', name: '机械加工', desc: 'CNC/模具/装配', icon: 'Setting' },
  { code: 'other', name: '其他行业', desc: '通用离散制造', icon: 'OfficeBuilding' },
]

const business = [
  { code: 'incoming', name: '来料加工', desc: '客户供料，赚取加工费' },
  { code: 'self', name: '自主生产', desc: '自购料生产销售' },
  { code: 'mixed', name: '混合经营', desc: '自产与代工并存' },
]

const modules = [
  { code: 'prod', name: '生产管理', desc: '加工单/工序/排产' },
  { code: 'inv', name: '库存核算', desc: '出入库/盘点/核算' },
  { code: 'qc', name: '质量管理', desc: '来料/过程/完工检验' },
  { code: 'eq', name: '设备管理', desc: '台账/点检/OEE' },
  { code: 'salary', name: '工资核算', desc: '计件工资/汇总' },
]

const form = reactive({
  industry: '',
  business: '',
  modules: ['prod', 'inv'],
  reportMode: 'scan',
})

function toggleModule(code) {
  const idx = form.modules.indexOf(code)
  if (idx >= 0) form.modules.splice(idx, 1)
  else form.modules.push(code)
}

const industryName = computed(() => industries.find((i) => i.code === form.industry)?.name || '未选择')
const businessName = computed(() => business.find((b) => b.code === form.business)?.name || '未选择')
const moduleNames = computed(() => form.modules.map((c) => modules.find((m) => m.code === c)?.name).filter(Boolean).join('、'))
const reportModeName = computed(() => ({ scan: '扫码报工', manual: '手工报工', batch: '批量报工' })[form.reportMode])

function next() {
  if (step.value === 1) {
    if (!form.industry || !form.business) return ElMessage.warning('请先选择行业细分与经营业态')
  }
  if (step.value < 3) step.value++
}

function prev() {
  if (step.value > 1) step.value--
}

function finish() {
  app.finishInitWizard()
  app.closeInitWizard(false)
  ElMessage.success('初始化完成，已为您匹配专属桌面与菜单')
}

function skip() {
  app.closeInitWizard(true)
}
</script>

<style scoped>
.wizard-mask {
  position: fixed;
  inset: 0;
  z-index: 5000;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
}
.wizard {
  width: 720px;
  max-width: 92vw;
  max-height: 90vh;
  overflow: auto;
  background: var(--t-card-bg);
  border-radius: 10px;
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
}
.wz-header {
  position: relative;
  padding: 26px 30px 18px;
  background: #174c42;
  color: #fff;
  border-radius: 10px 10px 0 0;
}
.wz-close {
  position: absolute;
  top: 14px;
  right: 16px;
  font-size: 16px;
  cursor: pointer;
  opacity: 0.85;
}
.wz-close:hover {
  opacity: 1;
}
.wz-title {
  font-size: 20px;
  font-weight: 600;
}
.wz-subtitle {
  font-size: 12px;
  margin-top: 6px;
  opacity: 0.9;
}
.wz-steps {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 30px;
  border-bottom: 1px solid var(--t-border-light);
}
.wz-step {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
}
.wz-step-no {
  width: 26px;
  height: 26px;
  line-height: 26px;
  text-align: center;
  border-radius: 50%;
  background: var(--t-border-light);
  color: var(--t-text-3);
  font-size: 13px;
  flex-shrink: 0;
}
.wz-step.active .wz-step-no {
  background: var(--t-primary);
  color: #fff;
}
.wz-step.done .wz-step-no {
  background: #67c23a;
  color: #fff;
}
.wz-step-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--t-text-1);
}
.wz-step-desc {
  font-size: 12px;
  color: var(--t-text-3);
  margin-top: 2px;
}
.wz-step-arrow {
  color: var(--t-text-3);
  font-size: 14px;
}
.wz-body {
  flex: 1;
  padding: 20px 30px 10px;
}
.wz-body-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--t-text-1);
  margin: 10px 0;
}
.wz-cards {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.wz-card {
  width: calc(20% - 10px);
  min-width: 118px;
  border: 1px solid var(--t-border);
  border-radius: 8px;
  padding: 14px 10px;
  text-align: center;
  cursor: pointer;
  transition: all 0.15s;
  position: relative;
}
.wz-card:hover {
  border-color: var(--t-primary);
}
.wz-card.active {
  border-color: var(--t-primary);
  background: var(--t-hover-bg);
  box-shadow: 0 0 0 1px var(--t-primary) inset;
}
.wz-card.small {
  width: calc(33.3% - 8px);
  min-width: 200px;
  text-align: left;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.wz-card-icon {
  font-size: 24px;
  color: var(--t-primary);
  margin-bottom: 6px;
}
.wz-card-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--t-text-1);
}
.wz-card-desc {
  font-size: 12px;
  color: var(--t-text-3);
}
.wz-card.checkable {
  flex-direction: row;
  align-items: center;
  gap: 8px;
}
.wz-card.checkable .wz-card-desc {
  margin-left: auto;
}
.wz-check {
  width: 16px;
  height: 16px;
  border: 1px solid var(--t-text-3);
  border-radius: 3px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: #fff;
  flex-shrink: 0;
}
.wz-card.active .wz-check {
  background: var(--t-primary);
  border-color: var(--t-primary);
}
.wz-radio-group {
  margin: 4px 0 8px;
}
.wz-result {
  text-align: center;
  padding: 18px 0 10px;
}
.wz-result-icon {
  font-size: 44px;
  color: #67c23a;
}
.wz-result-text {
  font-size: 13px;
  color: var(--t-text-2);
  line-height: 1.9;
  margin-top: 10px;
}
.wz-result-hl {
  color: var(--t-primary);
  font-weight: 600;
}
.wz-summary {
  margin: 10px auto 0;
  max-width: 460px;
  border: 1px solid var(--t-border-light);
  border-radius: 8px;
  overflow: hidden;
}
.wz-summary-row {
  display: flex;
  padding: 9px 14px;
  font-size: 13px;
  color: var(--t-text-1);
  border-bottom: 1px solid var(--t-border-light);
}
.wz-summary-row:last-child {
  border-bottom: none;
}
.wz-summary-label {
  width: 90px;
  color: var(--t-text-3);
  flex-shrink: 0;
}
.wz-footer {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 30px 20px;
  border-top: 1px solid var(--t-border-light);
}
.wz-start {
  background: var(--t-primary);
  color: #fff;
  font-size: 14px;
  padding: 8px 26px;
  border-radius: 4px;
  cursor: pointer;
}
.wz-start:hover {
  background: var(--t-primary-dark);
}
.wz-back {
  border: 1px solid var(--t-border);
  color: var(--t-text-2);
  font-size: 14px;
  padding: 8px 26px;
  border-radius: 4px;
  cursor: pointer;
}
.wz-back:hover {
  color: var(--t-primary);
  border-color: var(--t-primary);
}
.wz-skip {
  margin-left: auto;
  font-size: 13px;
  color: var(--t-text-3);
  cursor: pointer;
}
.wz-skip:hover {
  color: var(--t-primary);
}
</style>
