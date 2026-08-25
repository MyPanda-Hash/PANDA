<!-- SerialNumber.vue — 序列号管理：启用/入库/出库/追溯说明 + 快捷入口（对齐真实 T+ 序列号管理 SN 模块） -->
<template>
  <div class="sn-page">
    <div class="sn-head">
      <div class="sn-title">序列号管理</div>
      <div class="sn-sub">序列号启用 → 入库登记（扫码/录入/导入）→ 出库扫描 → 序列号状况表/跟踪表追溯</div>
    </div>
    <div class="sn-body">
      <div class="sn-flow">
        <div v-for="(s, i) in flow" :key="i" class="sn-step">
          <div class="sn-step-no">{{ i + 1 }}</div>
          <div class="sn-step-name">{{ s.name }}</div>
          <div class="sn-step-desc">{{ s.desc }}</div>
        </div>
      </div>
      <div class="sn-sec">
        <span class="sn-sec-title">快捷入口</span>
        <span v-for="d in entries" :key="d.code" class="sn-btn" @click="go(d.code)">{{ d.label }}</span>
      </div>
      <div class="sn-note">
        入库时：可以扫码、录入、导入序列号；出库时：直接扫描序列号或选择序列号，简单更高效。
        序列号查询：通过序列号状况表查询序列号状态，通过序列号跟踪表对序列号进行追溯查询。
      </div>
    </div>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { useTabsStore } from '@/stores/tabs'

const router = useRouter()
const tabs = useTabsStore()

const flow = [
  { name: '启用序列号', desc: '设置存货，启用序列号管理（存货档案中启用序列号属性）' },
  { name: '入库登记', desc: '入库时扫码、录入或导入序列号，登记到序列号登记单' },
  { name: '出库扫描', desc: '出库时直接扫描序列号或选择序列号，标记已出库' },
  { name: '查询追溯', desc: '序列号状况表查状态，序列号跟踪表做追溯查询' },
]

const entries = [
  { code: 'SERIAL_NO', label: '序列号登记单' },
  { code: 'SERIAL_STATUS', label: '序列号状况表' },
  { code: 'SERIAL_TRACE', label: '序列号跟踪表' },
  { code: 'INV', label: '存货（启用序列号）' },
]

function go(code) {
  const path = '/panelx/list/' + code
  router.push(path)
  tabs.open({ path, title: code })
}
</script>

<style scoped>
.sn-page {
  padding: 14px 18px;
  height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  background: #f9f9f9;
}
.sn-head {
  padding-bottom: 12px;
  border-bottom: 1px solid #e4e7ed;
}
.sn-title {
  font-size: 17px;
  font-weight: 700;
  color: #1f2d3d;
}
.sn-sub {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
.sn-body {
  flex: 1;
  margin-top: 14px;
  min-height: 0;
  overflow-y: auto;
}
.sn-flow {
  display: flex;
  gap: 12px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 14px;
}
.sn-step {
  flex: 1;
  text-align: center;
  border: 1px solid #cfe3ff;
  border-radius: 6px;
  padding: 12px 8px;
  background: #f0f7ff;
}
.sn-step-no {
  width: 26px;
  height: 26px;
  line-height: 26px;
  margin: 0 auto 8px;
  border-radius: 50%;
  background: #0d5bd3;
  color: #fff;
  font-size: 13px;
  font-weight: 600;
}
.sn-step-name {
  font-size: 14px;
  font-weight: 600;
  color: #1f2d3d;
}
.sn-step-desc {
  font-size: 11.5px;
  color: #606266;
  margin-top: 6px;
  line-height: 1.5;
}
.sn-sec {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  margin-top: 12px;
}
.sn-sec-title {
  width: 70px;
  font-size: 12.5px;
  font-weight: 600;
  color: #606266;
  flex-shrink: 0;
}
.sn-btn {
  padding: 4px 12px;
  font-size: 12.5px;
  color: #0d5bd3;
  background: #f0f7ff;
  border: 1px solid #cfe3ff;
  border-radius: 4px;
  cursor: pointer;
}
.sn-btn:hover {
  background: #d7e6ff;
}
.sn-note {
  margin-top: 12px;
  padding: 10px 14px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  font-size: 12px;
  color: #606266;
  line-height: 1.7;
}
</style>