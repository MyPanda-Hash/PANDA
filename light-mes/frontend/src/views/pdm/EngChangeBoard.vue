<!-- EngChangeBoard.vue — 工程变更（ECR/ECO）
     变更单描述「改哪张单、改多大、为什么」；审核后点「执行变更」，把目标物料清单/工艺路的版本号 +1
     并盖上变更单号（小版本 V1.0→V1.1，大版本 V1.0→V2.0）。这样 BOM 是哪一版、谁改的都能查回去。
     视觉：容器挂 .adx，取全局设计库语义层（--d-*）。 -->
<template>
  <div class="adx ec">
    <header class="ec-head">
      <div class="ec-title">
        <h2>工程变更</h2>
        <span class="ec-sub">
          变更单审核后才能执行：执行时按明细把「物料清单 / 工艺路线」的版本号抬一版，并盖上变更单号
          <span class="dot">·</span>小版本 V1.0→V1.1，大版本 V1.0→V2.0
        </span>
      </div>
      <div class="ec-actions">
        <el-button @click="load">刷新</el-button>
        <el-button type="primary" :disabled="!selected || selected['单据状态'] !== '已审核'"
                   :loading="applying" @click="apply">执行变更</el-button>
      </div>
    </header>

    <div class="ec-grid">
      <section class="ec-pane">
        <div class="ec-pane-head">
          <span>变更单</span>
          <span class="ec-hint">选中一行看明细</span>
        </div>
        <el-table :data="changes" v-loading="loading" height="calc(100vh - 300px)" highlight-current-row
                  @current-change="pick">
          <el-table-column prop="单据编号" label="变更单号" width="164" />
          <el-table-column prop="申请人" label="申请人" width="100" />
          <el-table-column prop="单据状态" label="状态" width="92">
            <template #default="{ row }">
              <span class="ec-tag" :class="row['单据状态'] === '已审核' ? 'ok' : ''">{{ row['单据状态'] }}</span>
            </template>
          </el-table-column>
          <el-table-column label="对象" min-width="150">
            <template #default="{ row }">{{ (row.detail?.items || []).map((i) => i['对象编码']).join('、') }}</template>
          </el-table-column>
        </el-table>
      </section>

      <section class="ec-pane">
        <div class="ec-pane-head">
          <span>变更明细{{ selected ? ' · ' + selected['单据编号'] : '' }}</span>
          <span class="ec-hint" v-if="selected">{{ selected['变更原因'] || '—' }}</span>
        </div>
        <el-table :data="detailRows" height="calc(100vh - 300px)" empty-text="先选一张变更单">
          <el-table-column prop="对象类型" label="对象类型" width="110" />
          <el-table-column prop="对象编码" label="对象编码" width="160" />
          <el-table-column prop="变更级别" label="级别" width="92" />
          <el-table-column prop="变更说明" label="说明" min-width="160" />
        </el-table>

        <div v-if="applied" class="ec-result">
          <div class="ec-result-head">执行结果</div>
          <div v-for="row in applied.rows" :key="row['对象编码']" class="ec-result-row">
            <span>{{ row['对象类型'] }} {{ row['对象编码'] }}</span>
            <span class="ec-version">{{ row['原版本'] }} → {{ row['新版本'] }}</span>
            <span class="ec-hint">单据 {{ row['单据号'] }}</span>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@core/request'

const changes = ref([])
const selected = ref(null)
const applied = ref(null)
const loading = ref(false)
const applying = ref(false)

const detailRows = computed(() => selected.value?.detail?.items || [])

async function load() {
  loading.value = true
  try {
    const res = await request.post('/px/queryFormDataList', {
      panelCode: 'ENG_CHANGE', pageNo: 1, pageSize: 50,
    })
    changes.value = res?.data?.list || []
    if (selected.value) {
      selected.value = changes.value.find((c) => c['单据编号'] === selected.value['单据编号']) || null
    }
  } finally {
    loading.value = false
  }
}

function pick(row) {
  selected.value = row || null
  applied.value = null
}

async function apply() {
  applying.value = true
  try {
    const res = await request.post('/px/eng/apply', { 编号: selected.value['单据编号'] })
    applied.value = res?.data || null
    ElMessage.success(`已执行 ${res?.data?.['执行条数'] || 0} 条变更`)
    await load()
  } catch (e) {
    ElMessage.error(e?.message || '执行失败')
  } finally {
    applying.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.ec {
  padding: 14px 18px 0;
  /* 内容超过视口时撑开页面滚动，而不是把下半截裁掉 */
  min-height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  background: var(--d-bg, #f6f7f8);
}

.ec-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.ec-title h2 {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  color: var(--d-text, #1f2d3d);
}

.ec-sub {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3, #8b9893);
}

.dot { margin: 0 6px; color: var(--d-border-strong, #dce4e1); }

.ec-actions { display: flex; gap: 8px; flex: 0 0 auto; }

.ec-grid {
  flex: 1;
  min-height: 0;
  margin-top: 12px;
  display: grid;
  grid-template-columns: minmax(460px, 5fr) minmax(460px, 6fr);
  gap: 12px;
}

.ec-pane {
  border: 1px solid var(--d-border, #edf1ef);
  border-radius: 6px;
  background: #fff;
  padding: 8px 10px 10px;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.ec-pane-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 2px 2px 8px;
  font-size: 13px;
  color: var(--d-text, #1f2d3d);
}

.ec-hint { font-size: 12px; color: var(--d-text-3, #8b9893); }

.ec-tag {
  display: inline-block;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 20px;
  border: 1px solid var(--d-border-strong, #dce4e1);
  color: var(--d-text-2, #5b6b66);
}

.ec-tag.ok { border-color: #b7eb8f; background: #f6ffed; color: #389e0d; }

.ec-result {
  margin-top: 10px;
  padding: 8px 10px;
  border: 1px solid var(--d-border, #edf1ef);
  border-radius: 6px;
  background: #fafcfa;
}

.ec-result-head {
  font-size: 12px;
  color: var(--d-text-3, #8b9893);
  margin-bottom: 4px;
}

.ec-result-row {
  display: flex;
  gap: 12px;
  font-size: 13px;
  line-height: 22px;
  color: var(--d-text, #1f2d3d);
}

.ec-version {
  font-variant-numeric: tabular-nums;
  color: var(--d-primary, #116a5b);
}
</style>
