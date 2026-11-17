<!-- ManufactureBoard.vue — 车间大屏：站着十几米外看的一屏。
     与「我的桌面」的分工：桌面是坐着逐项点开的，大屏是「少、大、能一眼判断谁掉队」。
     自适应范围：1280–3840 一屏铺满；1024 以下改为纵向堆叠（笔记本/平板看也成立）。 -->
<template>
  <div class="bd" :class="{ 'bd--empty': !data }">
    <header class="bd-top">
      <div class="bd-top__title">
        <span class="bd-dot" aria-hidden="true"></span>
        <h1>{{ tt('生产看板') }}</h1>
        <span class="bd-top__sub">{{ tt('车间当前生产要素') }}</span>
      </div>
      <div class="bd-top__meta">
        <span v-if="data">{{ data.date }} · {{ data.updatedAt }}</span>
        <span v-if="error" class="bd-error">{{ error }}</span>
        <el-button size="small" :loading="loading" @click="load">{{ tt('刷新') }}</el-button>
        <el-button size="small" @click="toggleFull">{{ full ? tt('退出全屏') : tt('全屏') }}</el-button>
      </div>
    </header>

    <template v-if="data">
      <section class="bd-kpis">
        <div v-for="k in kpis" :key="k.label" class="bd-kpi" :data-tone="k.tone">
          <div class="bd-kpi__num">{{ k.value }}<span v-if="k.unit" class="bd-kpi__unit">{{ k.unit }}</span></div>
          <div class="bd-kpi__label">{{ k.label }}</div>
          <div v-if="k.hint" class="bd-kpi__hint">{{ k.hint }}</div>
        </div>
      </section>

      <section class="bd-main">
        <div class="bd-panel bd-orders">
          <div class="bd-panel__head">
            <h2>{{ tt('在制工单进度') }}</h2>
            <span class="bd-panel__hint">{{ tt('进度低的排在前面') }}</span>
          </div>
          <div class="bd-orders__list">
            <div v-for="o in data.orders" :key="o.docNo" class="bd-order" :class="{ 'is-late': o.overdue_days > 0 }">
              <div class="bd-order__head">
                <span class="bd-order__product">{{ o.product || o.docNo }}</span>
                <span v-if="o.overdue_days > 0" class="bd-tag bd-tag--late">{{ tt('逾期') }} {{ o.overdue_days }} {{ tt('天') }}</span>
                <span class="bd-order__no">{{ o.docNo }}</span>
              </div>
              <div class="bd-order__bar">
                <div class="bd-order__fill" :style="{ width: Math.min(100, o.progress) + '%' }"></div>
              </div>
              <div class="bd-order__foot">
                <span>{{ o.workshop }}</span>
                <span class="bd-order__qty">{{ o.doneQty }} / {{ o.planQty }}</span>
                <span class="bd-order__pct">{{ o.progress }}%</span>
              </div>
            </div>
            <div v-if="!data.orders.length" class="bd-none">{{ tt('当前没有在制工单') }}</div>
          </div>
        </div>

        <aside class="bd-side">
          <div class="bd-panel">
            <div class="bd-panel__head"><h2>{{ tt('车间分布') }}</h2></div>
            <div class="bd-ws" v-for="w in data.workshops" :key="w.workshop">
              <span class="bd-ws__name">{{ w.workshop }}</span>
              <span class="bd-ws__num">{{ w.orders }}{{ tt('单') }}</span>
              <span class="bd-ws__qty">{{ w.done }}/{{ w.plan }}</span>
            </div>
            <div v-if="!data.workshops.length" class="bd-none">{{ tt('暂无车间数据') }}</div>
          </div>

          <div class="bd-panel bd-alert">
            <div class="bd-panel__head">
              <h2>{{ tt('需要关注') }}</h2>
              <span class="bd-panel__hint">{{ tt('逾期与停滞') }}</span>
            </div>
            <div v-for="o in data.overdue" :key="'o' + o.docNo" class="bd-alert__row">
              <span class="bd-tag bd-tag--late">{{ tt('逾期') }}</span>
              <span class="bd-alert__t">{{ o.product || o.docNo }}</span>
              <span class="bd-alert__d">{{ o.overdue_days }}{{ tt('天') }}</span>
            </div>
            <div v-for="o in data.stalled" :key="'s' + o.docNo" class="bd-alert__row">
              <span class="bd-tag bd-tag--warn">{{ tt('停滞') }}</span>
              <span class="bd-alert__t">{{ o.product || o.docNo }}</span>
              <span class="bd-alert__d">{{ o.idle_days }}{{ tt('天未报工') }}</span>
            </div>
            <div v-if="!data.overdue.length && !data.stalled.length" class="bd-none">{{ tt('没有逾期或停滞工单') }}</div>
          </div>
        </aside>
      </section>

      <footer class="bd-ticker">
        <span class="bd-ticker__label">{{ tt('最新报工') }}</span>
        <div class="bd-ticker__track">
          <span v-for="(r, i) in data.reports" :key="i" class="bd-ticker__item">
            {{ r.day?.slice(5) }} · {{ r.process }} · {{ r.qty }}{{ tt('件') }}<template v-if="r.worker"> · {{ r.worker }}</template>
          </span>
          <span v-if="!data.reports.length" class="bd-ticker__item">{{ tt('暂无报工记录') }}</span>
        </div>
      </footer>
    </template>

    <div v-else class="bd-loading">
      <span v-if="loading">{{ tt('正在读取生产数据…') }}</span>
      <template v-else>
        <p>{{ tt('生产看板暂时没有数据') }}</p>
        <el-button size="small" @click="load">{{ tt('重试') }}</el-button>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { apiGetProdBoard } from '@/business/api'
import { tt } from '@/i18n'

const data = ref(null)
const loading = ref(false)
const error = ref('')
const full = ref(false)
let timer = null

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await apiGetProdBoard()
  } catch (e) {
    error.value = (e && (e.message || e.msg)) || tt('读取失败')
  } finally {
    loading.value = false
  }
}

const kpis = computed(() => {
  const k = (data.value && data.value.kpis) || {}
  return [
    { label: tt('在制工单'), value: k['在制工单'] ?? 0, unit: tt('单') },
    { label: tt('计划数量'), value: fmt(k['计划数量']) },
    { label: tt('已汇报数量'), value: fmt(k['已汇报数量']) },
    { label: tt('平均进度'), value: fmt(k['平均进度']), unit: '%', tone: (k['平均进度'] || 0) < 30 ? 'warn' : 'ok' },
    { label: tt('逾期工单'), value: k['逾期工单'] ?? 0, unit: tt('单'), tone: k['逾期工单'] ? 'warn' : 'ok' },
    { label: tt('今日报工'), value: k['今日报工'] ?? 0, unit: tt('单'), hint: fmt(k['今日报工数量']) + ' ' + tt('件') },
  ]
})

function fmt(v) {
  const n = Number(v || 0)
  return Number.isInteger(n) ? String(n) : n.toFixed(1)
}

async function toggleFull() {
  try {
    if (!document.fullscreenElement) {
      await document.documentElement.requestFullscreen()
      full.value = true
    } else {
      await document.exitFullscreen()
      full.value = false
    }
  } catch (_) {
    // 浏览器拒绝（如 iframe 未授权）时保持原状，不弹错打扰现场
  }
}

onMounted(() => {
  load()
  // 大屏是常驻展示：60 秒自动刷新一次，不用人管
  timer = setInterval(load, 60000)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
/* 车间大屏：深底高对比，字号用 clamp 随屏幕宽度伸缩。
   一屏铺满不滚动：grid 三段（顶栏 / 主体 / 底部滚动条），主体再分左右。 */
.bd {
  --bd-bg: #0e1513;
  --bd-panel: #16201d;
  --bd-line: #26332e;
  --bd-text: #e6efec;
  --bd-dim: #8fa39c;
  --bd-accent: #58b09e;
  --bd-warn: #e0644f;
  display: grid;
  grid-template-rows: auto auto 1fr auto;
  gap: clamp(10px, 1vw, 18px);
  /* 外壳占掉的高度：顶栏(56) + 页签栏(40) + 内容边距，留 132 避免底部报工条被裁 */
  height: calc(100vh - 132px);
  min-height: calc(100vh - 132px);
  padding: clamp(12px, 1.2vw, 24px);
  background: var(--bd-bg);
  color: var(--bd-text);
  border-radius: 6px;
  overflow: hidden;
}

/* 顶栏 */
.bd-top { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.bd-top__title { display: flex; align-items: baseline; gap: 12px; }
.bd-top__title h1 { font-size: clamp(20px, 2vw, 34px); line-height: 1.2; font-weight: 700; letter-spacing: .02em; }
.bd-top__sub { font-size: clamp(12px, 1vw, 15px); color: var(--bd-dim); }
.bd-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--bd-accent); box-shadow: 0 0 12px var(--bd-accent); align-self: center; }
.bd-top__meta { display: flex; align-items: center; gap: 12px; font-size: clamp(12px, 1vw, 15px); color: var(--bd-dim); }
.bd-error { color: var(--bd-warn); }

/* KPI 行：等宽列，数量多也自动换行 */
.bd-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: clamp(8px, .8vw, 14px); }
.bd-kpi {
  background: var(--bd-panel); border: 1px solid var(--bd-line); border-radius: 6px;
  padding: clamp(10px, 1vw, 18px);
}
.bd-kpi__num { font-size: clamp(28px, 3.2vw, 56px); line-height: 1.1; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--bd-accent); }
.bd-kpi[data-tone="warn"] .bd-kpi__num { color: var(--bd-warn); }
.bd-kpi__unit { font-size: .45em; margin-left: 4px; color: var(--bd-dim); font-weight: 500; }
.bd-kpi__label { margin-top: 4px; font-size: clamp(12px, 1vw, 16px); color: var(--bd-dim); }
.bd-kpi__hint { margin-top: 2px; font-size: clamp(11px, .85vw, 13px); color: var(--bd-dim); opacity: .8; }

/* 主体：宽屏左右分栏，窄屏上下堆叠 */
.bd-main { display: grid; grid-template-columns: 2fr 1fr; gap: clamp(10px, 1vw, 18px); min-height: 0; }
.bd-side { display: grid; grid-template-rows: auto 1fr; gap: clamp(10px, 1vw, 18px); min-height: 0; }
.bd-panel { background: var(--bd-panel); border: 1px solid var(--bd-line); border-radius: 6px; padding: clamp(10px, 1vw, 16px); display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.bd-panel__head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-bottom: 8px; flex: none; }
.bd-panel__head h2 { font-size: clamp(14px, 1.1vw, 18px); font-weight: 600; }
.bd-panel__hint { font-size: clamp(11px, .85vw, 13px); color: var(--bd-dim); }

.bd-orders__list { display: flex; flex-direction: column; gap: clamp(8px, .7vw, 12px); overflow: auto; }
.bd-order { border-left: 3px solid var(--bd-line); padding-left: 10px; }
.bd-order.is-late { border-left-color: var(--bd-warn); }
.bd-order__head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.bd-order__product { font-size: clamp(14px, 1.2vw, 20px); font-weight: 600; }
.bd-order__no { font-size: clamp(11px, .85vw, 13px); color: var(--bd-dim); font-variant-numeric: tabular-nums; }
.bd-order__bar { margin: 6px 0 4px; height: clamp(8px, .7vw, 12px); background: #1f2b27; border-radius: 999px; overflow: hidden; }
.bd-order__fill { height: 100%; background: var(--bd-accent); border-radius: 999px; transition: width .4s ease; }
.bd-order__foot { display: flex; gap: 14px; font-size: clamp(11px, .9vw, 14px); color: var(--bd-dim); font-variant-numeric: tabular-nums; }
.bd-order__qty { margin-left: auto; }

.bd-ws { display: flex; align-items: baseline; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--bd-line); font-size: clamp(12px, 1vw, 16px); }
.bd-ws:last-child { border-bottom: 0; }
.bd-ws__name { flex: 1; }
.bd-ws__num { color: var(--bd-accent); font-variant-numeric: tabular-nums; }
.bd-ws__qty { color: var(--bd-dim); font-variant-numeric: tabular-nums; }

.bd-alert { overflow: auto; }
.bd-alert__row { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--bd-line); font-size: clamp(12px, 1vw, 16px); }
.bd-alert__row:last-child { border-bottom: 0; }
.bd-alert__t { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bd-alert__d { color: var(--bd-dim); font-variant-numeric: tabular-nums; }
.bd-tag { font-size: clamp(10px, .8vw, 12px); line-height: 1.6; padding: 0 6px; border-radius: 4px; flex: none; }
.bd-tag--late { background: rgba(224, 100, 79, .16); color: #f08a78; }
.bd-tag--warn { background: rgba(250, 173, 20, .14); color: #f5c04a; }

/* 底部：最新报工横向滚动条 */
.bd-ticker { display: flex; align-items: center; gap: 12px; background: var(--bd-panel); border: 1px solid var(--bd-line); border-radius: 6px; padding: 8px 12px; overflow: hidden; min-width: 0; }
.bd-ticker__label { flex: none; color: var(--bd-accent); font-size: clamp(11px, .9vw, 14px); font-weight: 600; }
/* min-width:0 是必须的：flex 子项默认 min-width:auto，nowrap 内容会把整条撑到屏幕外 */
.bd-ticker__track { flex: 1 1 auto; min-width: 0; display: flex; gap: 18px; overflow: hidden; white-space: nowrap; font-size: clamp(11px, .9vw, 14px); color: var(--bd-dim); }
.bd-ticker__item { flex: none; }

.bd-none { color: var(--bd-dim); font-size: clamp(12px, 1vw, 15px); padding: 8px 0; }
.bd-loading { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; color: var(--bd-dim); font-size: clamp(13px, 1.1vw, 17px); }

/* 工单少时（真实场景里大屏通常是满的，这里兜住空数据期）：
   面板按内容收缩，不让一块大黑区显得像加载失败 */
.bd-main { align-content: start; }
.bd-orders, .bd-side .bd-panel { max-height: 100%; }
.bd-side { align-content: start; }

/* 中屏：主体改上下 */
@media (max-width: 1400px) {
  .bd-main { grid-template-columns: 1fr; grid-template-rows: 1fr auto; }
  .bd-side { grid-template-rows: auto auto; }
}
/* 小屏/笔记本：允许整体纵向滚动，字号收紧 */
@media (max-width: 900px) {
  .bd { height: auto; min-height: calc(100vh - 92px); overflow: visible; grid-template-rows: auto; }
  .bd-main { grid-template-columns: 1fr; }
  .bd-kpis { grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); }
  .bd-ticker__track { overflow-x: auto; }
}
</style>
