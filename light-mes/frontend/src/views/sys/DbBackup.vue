<!-- DbBackup.vue — 系统管理 · 数据备份：一键 pg_dump 备份 + 留痕 + 下载 -->
<template>
  <div class="backup-wrap">
    <div class="head">
      <span class="title">{{ tt('数据库备份') }}</span>
      <span class="ops">
        <el-button type="primary" size="small" :loading="running" @click="runNow">{{ tt('立即备份') }}</el-button>
        <el-button size="small" @click="load">{{ tt('刷新') }}</el-button>
      </span>
    </div>
    <div class="tip">
      {{ tt('备份文件保存在服务器 /opt/light-mes/backup/，可直接下载留存；每次备份都会记录成功或失败。') }}
    </div>
    <div class="schedule" v-if="scheduleText">
      <span class="schedule__dot" :class="{ 'is-off': schedule && !schedule.auto }"></span>
      <span>{{ scheduleText }}</span>
    </div>

    <el-table :data="rows" size="small" border height="580" highlight-current-row>
      <el-table-column prop="fileName" :label="tt('文件')" min-width="250" />
      <el-table-column :label="tt('大小')" width="100">
        <template #default="{ row }">{{ row.fileSizeKb ? row.fileSizeKb + ' KB' : '-' }}</template>
      </el-table-column>
      <el-table-column :label="tt('状态')" width="86" align="center">
        <template #default="{ row }">
          <el-tag :type="row.status === 'SUCCESS' ? 'success' : 'danger'" size="small">
            {{ row.status === 'SUCCESS' ? tt('成功') : tt('失败') }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="elapsedMs" :label="tt('耗时(ms)')" width="100" align="right" />
      <el-table-column prop="operator" :label="tt('操作人')" width="90" />
      <el-table-column prop="createTime" :label="tt('时间')" width="170" />
      <el-table-column :label="tt('操作')" width="70" align="center">
        <template #default="{ row }">
          <el-button v-if="row.status === 'SUCCESS'" link type="primary" size="small" @click="download(row)">{{ tt('下载') }}</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@core/request'
import { tt } from '@/i18n'

const rows = ref([])
const running = ref(false)
const schedule = ref(null)
const scheduleText = ref('')

const WEEK = { MON: '周一', TUE: '周二', WED: '周三', THU: '周四', FRI: '周五', SAT: '周六', SUN: '周日' }

// 把 cron 翻成人话；认不出来的就原样显示，别猜
function describeCron(cron) {
  const parts = String(cron || '').trim().split(/\s+/)
  if (parts.length < 6) return String(cron || '')
  const [min, hour, dom, mon, dow] = [parts[1], parts[2], parts[3], parts[4], parts[5]]
  const hhmm = `${String(hour).padStart(2, '0')}:${String(min).padStart(2, '0')}`
  if (dom === '*' && mon === '*' && WEEK[dow]) return `每周${WEEK[dow].slice(1)} ${hhmm}`
  if (dom === '*' && mon === '*' && dow === '*') return `每天 ${hhmm}`
  if (dom !== '*' && mon === '*' && dow === '*') return `每月 ${dom} 日 ${hhmm}`
  return String(cron)
}

async function loadSchedule() {
  try {
    const res = await request.get('/sys/backup/schedule')
    schedule.value = res.data || null
    if (!schedule.value) return
    const keep = schedule.value.keep || 8
    scheduleText.value = schedule.value.auto
      ? tt('自动备份：') + describeCron(schedule.value.cron) + tt('，保留最近 ') + keep + tt(' 份')
      : tt('自动备份已关闭，可手动「立即备份」')
  } catch (e) {
    scheduleText.value = ''
  }
}

async function load() {
  try {
    const res = await request.get('/sys/backup/list', { params: { limit: 50 } })
    rows.value = res.data || []
  } catch (e) {
    ElMessage.error(tt('读取备份记录失败'))
  }
}

async function runNow() {
  running.value = true
  try {
    const res = await request.post('/sys/backup/run')
    const d = res.data || {}
    if (res.code === 200 && d.status === 'SUCCESS') {
      ElMessage.success(tt('备份成功') + ' ' + d.fileSizeKb + ' KB / ' + d.elapsedMs + ' ms')
    } else {
      ElMessage.error(d.message || res.message || tt('备份失败'))
    }
    await load()
  } catch (e) {
    ElMessage.error(tt('备份请求失败'))
  } finally {
    running.value = false
  }
}

async function download(row) {
  try {
    const blob = await request.get('/sys/backup/download/' + encodeURIComponent(row.fileName), { responseType: 'blob' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = row.fileName
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    ElMessage.error(tt('下载失败'))
  }
}

onMounted(() => {
  load()
  loadSchedule()
})
</script>

<style scoped>
.backup-wrap { padding: 12px 16px; }
.head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.title { font-size: 15px; font-weight: 600; }
.schedule { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; font-size: 12px; color: #5b6470; }
.schedule__dot { width: 6px; height: 6px; border-radius: 50%; background: #2f6b58; }
.schedule__dot.is-off { background: #b0b6bf; }
.tip { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 10px; line-height: 1.6; }
</style>
