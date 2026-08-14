<template>
  <div class="solution-center">
    <div class="head">
      <h3>方案中心 <span class="code">（行业方案 / 应用市场 · 对齐 T+ 方案中心形态）</span></h3>
    </div>

    <el-card shadow="never" class="sec">
      <template #header>行业方案</template>
      <el-row :gutter="12">
        <el-col :span="8" v-for="s in solutions" :key="s.name">
          <div class="sol">
            <div class="sol-name">{{ s.name }}</div>
            <div class="sol-desc">{{ s.desc }}</div>
            <div class="sol-tags">
              <el-tag v-for="t in s.tags" :key="t" size="small" effect="plain">{{ t }}</el-tag>
            </div>
            <el-button size="small" type="primary" @click="apply(s)">应用方案</el-button>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <el-card shadow="never" class="sec">
      <template #header>应用市场 <el-tag size="small" type="success" v-if="installed.length">已安装 {{ installed.length }} 个</el-tag></template>
      <el-row :gutter="12">
        <el-col :span="6" v-for="a in apps" :key="a.name">
          <div class="app">
            <div class="app-icon">{{ a.icon }}</div>
            <div class="app-name">{{ a.name }}</div>
            <div class="app-desc">{{ a.desc }}</div>
            <el-button v-if="!installed.includes(a.name)" size="small" type="primary" @click="install(a)">安装</el-button>
            <el-tag v-else size="small" type="success">已安装</el-tag>
          </div>
        </el-col>
      </el-row>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'

const solutions = [
  { name: '轻MES 智能制造方案', desc: '面向中小制造企业的生产执行一体化方案：工单驱动、工序报工、计件工资、库存联动。', tags: ['生产管理', '智慧车间', '库存核算'] },
  { name: '离散制造行业方案', desc: '按单生产、多品种小批量：销售订单 → 生产加工单 → 工序汇报 → 成品入库全链路。', tags: ['销售', '加工单', '汇报'] },
  { name: '流程制造行业方案', desc: '熔铸/轧制/精整连续作业场景：批号追溯、班组报工、产量看板。', tags: ['批号', '班组', '看板'] },
]

const apps = [
  { icon: '📊', name: '生产看板', desc: '车间大屏：在制进度、工序状态、产量汇总。' },
  { icon: '🔧', name: '设备点检', desc: '设备台账、点检计划、OEE 分析。' },
  { icon: '🧾', name: '计件工资', desc: '按工序汇报自动核算计件工资。' },
  { icon: '📦', name: '物料追溯', desc: '批号 + 工序 + 库存全链路追溯。' },
  { icon: '🚨', name: '交期预警', desc: '加工单/销售订单交期风险预警。' },
  { icon: '🛠️', name: '智能排产', desc: '按工序产能自动排产。' },
  { icon: '📈', name: '质量追溯', desc: '不合格品登记、返修任务跟踪。' },
  { icon: '👥', name: '班组绩效', desc: '班组产量与工资对比分析。' },
]

const installed = ref(['生产看板', '计件工资'])

function apply(s) {
  ElMessage.success(`已应用方案：${s.name}（演示环境为静态展示）`)
}

function install(a) {
  installed.value.push(a.name)
  ElMessage.success(`已安装应用：${a.name}`)
}
</script>

<style scoped>
.solution-center { background: #fff; border-radius: 10px; padding: 18px; min-height: 100%; }
.dark .solution-center { background: #26272e; }
.head { display: flex; align-items: baseline; gap: 10px; border-bottom: 1px solid #f0f1f3; padding-bottom: 10px; margin-bottom: 14px; }
.code { font-size: 12px; color: #9ca3af; }
.sec { margin-bottom: 14px; }
.sol, .app { border: 1px solid #eef0f3; border-radius: 10px; padding: 14px; margin-bottom: 12px; text-align: center; }
.sol:hover, .app:hover { border-color: #289be5; }
.sol-name { font-weight: 700; margin-bottom: 6px; }
.sol-desc { font-size: 12px; color: #6b7280; margin-bottom: 8px; min-height: 52px; }
.sol-tags { margin-bottom: 10px; }
.sol-tags .el-tag { margin: 0 4px 4px 0; }
.app-icon { font-size: 30px; margin-bottom: 6px; }
.app-name { font-weight: 700; margin-bottom: 4px; }
.app-desc { font-size: 12px; color: #6b7280; margin-bottom: 8px; min-height: 34px; }
</style>
