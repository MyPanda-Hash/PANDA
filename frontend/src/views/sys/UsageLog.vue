<!-- UsageLog.vue — 系统管理 · 使用日志：登录与关键操作留痕（仅管理员） -->
<template>
  <div class="usage-wrap">
    <div class="head">
      <span class="title">{{ tt('使用日志') }}</span>
      <span class="ops">
        <el-input v-model="q.userName" :placeholder="tt('按账号过滤')" size="small" clearable style="width: 150px" @keyup.enter="load" />
        <el-select v-model="q.eventType" :placeholder="tt('事件类型')" size="small" clearable style="width: 130px">
          <el-option label="登录" value="LOGIN" />
          <el-option label="登录失败" value="LOGIN_FAIL" />
          <el-option label="操作" value="ACTION" />
        </el-select>
        <el-button type="primary" size="small" @click="load">{{ tt('查询') }}</el-button>
      </span>
    </div>
    <div class="tip">{{ tt('记录登录成功/失败与关键操作；查询、翻页等浏览动作不记录。') }}</div>

    <el-table :data="rows" size="small" border height="580">
      <el-table-column prop="createTime" :label="tt('时间')" width="170" />
      <el-table-column prop="userName" :label="tt('账号')" width="110" />
      <el-table-column :label="tt('事件')" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="row.eventType === 'LOGIN' ? 'success' : row.eventType === 'LOGIN_FAIL' ? 'danger' : 'info'" size="small">
            {{ eventLabel(row.eventType) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="detail" :label="tt('详情')" min-width="200" show-overflow-tooltip />
      <el-table-column prop="ip" :label="tt('IP')" width="140" />
    </el-table>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@core/request'
import { tt } from '@/i18n'

const rows = ref([])
const q = reactive({ userName: '', eventType: '' })

const EVENT_LABEL = { LOGIN: '登录', LOGIN_FAIL: '登录失败', ACTION: '操作' }

function eventLabel(code) {
  return tt(EVENT_LABEL[code] || code || '-')
}

async function load() {
  try {
    const res = await request.get('/sys/usage/logs', {
      params: { limit: 200, userName: q.userName || undefined, eventType: q.eventType || undefined },
    })
    rows.value = res.data || []
  } catch (e) {
    ElMessage.error(tt('读取使用日志失败'))
  }
}

onMounted(load)
</script>

<style scoped>
.usage-wrap { padding: 12px 16px; }
.head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; gap: 8px; }
.title { font-size: 15px; font-weight: 600; }
.ops { display: flex; gap: 8px; align-items: center; }
.tip { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 10px; line-height: 1.6; }
</style>
