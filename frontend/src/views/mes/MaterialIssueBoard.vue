<!-- MaterialIssueBoard.vue — 生产领料（按排产批次）
     一行 = 一个排产批次上的一种材料；批次需用量 = 整单需用量 × 批次占比。
     领料按批次整批生成一张材料出库单（多行），多种材料不用分好几次点。 -->
<template>
  <div class="mi-page">
    <header class="mi-head">
      <div>
        <h2>生产领料{{ moNo ? ' · ' + moNo : '' }}</h2>
        <span class="mi-sub">按排产批次领料：一个批次一张出库单，批次需用量 = 整单需用量 × 批次占比</span>
      </div>
      <el-button size="small" @click="moNo ? switchOrder() : $router.back()">{{ moNo ? '换单' : '返回' }}</el-button>
    </header>

    <!-- 未选加工单：先选 -->
    <el-table v-if="!moNo" :data="orders" v-loading="loading" size="small" border height="calc(100vh - 260px)">
      <el-table-column label="加工单号" prop="编号" width="170" />
      <el-table-column label="单据日期" prop="单据日期" width="110" />
      <el-table-column label="客户" prop="客户" min-width="150" />
      <el-table-column label="产品" min-width="200">
        <template #default="{ row }">{{ productOf(row) }}</template>
      </el-table-column>
      <el-table-column label="数量" width="100" align="right">
        <template #default="{ row }">{{ qty(qtyOf(row)) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100" align="center">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="pickOrder(row)">领料</el-button>
        </template>
      </el-table-column>
    </el-table>

    <template v-else>
      <section class="mi-kpis">
        <div class="kpi"><span class="kpi-label">可领批次</span><b class="kpi-value">{{ kpi.todo }}</b><span class="kpi-hint">共 {{ kpi.total }} 行</span></div>
        <div class="kpi"><span class="kpi-label">材料种数</span><b class="kpi-value">{{ kpi.materials }}</b><span class="kpi-hint">去重</span></div>
        <div class="kpi"><span class="kpi-label">已领满</span><b class="kpi-value">{{ kpi.done }}</b><span class="kpi-hint">剩余为 0</span></div>
      </section>

      <el-table :data="rows" v-loading="loading" size="small" border height="calc(100vh - 360px)">
        <el-table-column label="排产单号" prop="排产单号" width="150" />
        <el-table-column label="工序" width="100">
          <template #default="{ row }">{{ row['工序名称'] || row['工序编码'] }}</template>
        </el-table-column>
        <el-table-column label="批次数量" width="95" align="right">
          <template #default="{ row }">{{ qty(row['批次数量']) }}</template>
        </el-table-column>
        <el-table-column label="材料" width="150">
          <template #default="{ row }">{{ row['材料名称'] || row['材料编码'] }}</template>
        </el-table-column>
        <el-table-column label="整单需用" width="100" align="right">
          <template #default="{ row }">{{ qty(row['整单需用量']) }}</template>
        </el-table-column>
        <el-table-column label="批次需用" width="100" align="right">
          <template #default="{ row }">{{ qty(row['批次需用量']) }}</template>
        </el-table-column>
        <el-table-column label="已领" width="90" align="right">
          <template #default="{ row }">{{ qty(row['已领数量']) }}</template>
        </el-table-column>
        <el-table-column label="剩余可领" width="100" align="right">
          <template #default="{ row }">
            <b :class="Number(row['剩余可领']) > 0 ? 'ok' : 'zero'">{{ qty(row['剩余可领']) }}</b>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" align="center" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row['可否领料'] && isBatchHead(row)" link type="primary" size="small" @click="openIssue(row)">领料</el-button>
            <span v-else-if="row['可否领料']" class="dim">同批领</span>
            <span v-else class="dim">已领满</span>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <el-dialog v-model="visible" :title="`领料 · ${form.排产单号 || ''}`" width="min(600px, 94vw)">
      <el-form label-width="96px" size="small">
        <el-form-item label="排产批次">
          <span>{{ form.排产单号 }}（{{ form.工序名称 }}）· 批次 {{ qty(form.批次数量) }} 件</span>
        </el-form-item>
        <el-form-item label="本批材料">
          <el-table :data="form.lines" size="small" border style="width: 100%">
            <el-table-column label="材料" min-width="150">
              <template #default="{ row }">{{ row['材料名称'] || row['材料编码'] }}</template>
            </el-table-column>
            <el-table-column label="剩余可领" width="100" align="right">
              <template #default="{ row }">{{ qty(row['剩余可领']) }}</template>
            </el-table-column>
            <el-table-column label="本次数量" width="150">
              <template #default="{ row }">
                <el-input-number v-model="row.数量" :min="0" :max="Number(row['剩余可领'])" :step="1" size="small" controls-position="right" style="width: 120px" />
              </template>
            </el-table-column>
          </el-table>
        </el-form-item>
        <el-form-item label="领料人" required>
          <el-input v-model="form.领料人" placeholder="实际领料人" style="width: 200px" />
        </el-form-item>
        <el-form-item label="出库仓库">
          <el-input v-model="form.仓库" placeholder="留空按材料预出仓库" style="width: 200px" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="visible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="submitting" @click="submit">生成领料单</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { usePanelRuntime } from '@core/panel-runtime'
import { useTabsStore } from '@/stores/tabs'

const engine = usePanelRuntime()
const route = useRoute()
const router = useRouter()
const tabs = useTabsStore()

const moNo = ref(String(route.query.code || ''))
const rows = ref([])
const orders = ref([])
const loading = ref(false)
const submitting = ref(false)
const visible = ref(false)
const form = ref({ 排产单号: '', 工序名称: '', 批次数量: 0, lines: [], 领料人: '', 仓库: '' })

const kpi = computed(() => {
  const all = rows.value
  return {
    total: all.length,
    todo: all.filter((r) => r['可否领料']).length,
    materials: new Set(all.map((r) => r['材料编码'])).size,
    done: all.filter((r) => Number(r['剩余可领']) <= 0).length,
  }
})

function qty(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '0'
  return n === Math.round(n) ? String(n) : n.toFixed(2)
}
function qtyOf(row) {
  const p = (row.detail?.products || [])[0] || {}
  return p['数量'] || 0
}
function productOf(row) {
  const p = (row.detail?.products || [])[0] || {}
  return [p['产品名称'], p['规格型号']].filter(Boolean).join(' ') || '—'
}

/** 同一排产批次只在第一行挂「领料」按钮，其余行提示同批领。 */
function isBatchHead(row) {
  const first = rows.value.find((r) => r['排产单号'] === row['排产单号'] && r['可否领料'])
  return first === row
}

function pickOrder(row) {
  moNo.value = row['编号']
  router.replace({ path: '/mes/materialIssue', query: { code: moNo.value } })
  tabs.open({ path: '/mes/materialIssue', title: `生产领料-${moNo.value}`, query: { code: moNo.value } })
  load()
}
function switchOrder() {
  moNo.value = ''
  router.replace({ path: '/mes/materialIssue' })
  load()
}

function openIssue(row) {
  const lines = rows.value
    .filter((r) => r['排产单号'] === row['排产单号'] && Number(r['剩余可领']) > 0)
    .map((r) => ({ ...r, 数量: Number(r['剩余可领']) }))
  form.value = {
    排产单号: row['排产单号'],
    工序名称: row['工序名称'] || row['工序编码'],
    批次数量: row['批次数量'],
    lines,
    领料人: '',
    仓库: '',
  }
  visible.value = true
}

async function submit() {
  const items = form.value.lines
    .filter((l) => Number(l.数量) > 0)
    .map((l) => ({ 材料编码: l['材料编码'], 数量: Number(l.数量) }))
  if (!items.length) return ElMessage.warning('领料数量必须大于 0')
  if (!String(form.value.领料人 || '').trim()) return ElMessage.warning('请填写领料人')
  submitting.value = true
  try {
    const res = await engine.issueBatch({
      排产单号: form.value.排产单号,
      items,
      领料人: form.value.领料人,
      仓库: form.value.仓库,
    })
    const no = res?.['编号'] || ''
    ElMessage.success(`已生成材料出库单 ${no}（草稿，${items.length} 行），请提交审批`)
    visible.value = false
    await load()
    if (no) {
      const path = '/panelx/list/MATERIAL_OUT'
      const query = { code: no }
      router.push({ path, query })
      tabs.open({ path, title: `材料出库单-${no}`, query })
    }
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '领料失败')
  } finally {
    submitting.value = false
  }
}

async function load() {
  loading.value = true
  try {
    if (!moNo.value) {
      const res = await engine.queryFormDataList({
        panelCode: 'MANU_ORDER', pageNo: 1, pageSize: 200, condition: { 单据状态: '已审核' },
      })
      orders.value = (res?.list || []).filter((r) => (r['单据状态'] || '') === '已审核')
      rows.value = []
    } else {
      rows.value = (await engine.materialIssueBoard(moNo.value)) || []
    }
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载领料数据失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.mi-page { padding: 12px 16px; height: 100%; display: flex; flex-direction: column; background: #fff; }
.mi-head { display: flex; align-items: flex-end; justify-content: space-between; padding-bottom: 10px; border-bottom: 1px solid #f0f0f0; }
.mi-head h2 { margin: 0; font-size: 16px; font-weight: 600; color: #1f1f1f; }
.mi-sub { display: block; margin-top: 2px; font-size: 12px; color: #8c8c8c; }
.mi-kpis { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 12px 0; }
.kpi { border: 1px solid #e8e8e8; border-radius: 6px; padding: 10px 12px; display: flex; flex-direction: column; gap: 2px; }
.kpi-label { font-size: 12px; color: #8c8c8c; }
.kpi-value { font-size: 22px; line-height: 30px; font-weight: 600; font-variant-numeric: tabular-nums; }
.kpi-hint { font-size: 12px; color: #8c8c8c; }
.ok { color: #1677ff; }
.zero { color: #bfbfbf; }
.dim { color: #bfbfbf; font-size: 12px; }
</style>