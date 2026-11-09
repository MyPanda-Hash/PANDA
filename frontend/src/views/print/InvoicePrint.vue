<!-- InvoicePrint.vue — 销售发票票样（按数电票「电子发票」版式）
     版式参考国家税务总局数电票样式：无发票代码、20 位发票号码、购买方/销售方双栏、
     项目名称/规格型号/单位/数量/单价/金额/税率·征收率/税额、合计、价税合计大小写、备注、开票人。
     注意：本页是业务系统票样，用于核对与归档；正式发票须以电子税务局开具的 OFD/PDF 为准，
     监制章与二维码属于税务机关开具时生成的内容，这里以占位标识，不伪造。 -->
<template>
  <div class="inv-page">
    <div class="inv-toolbar no-print">
      <button type="button" class="tb" @click="print">打印</button>
      <button type="button" class="tb ghost" @click="goBack">返回</button>
      <span class="tb-tip">
        系统票样：用于业务核对与归档；正式发票以电子税务局开具的 OFD/PDF 为准。
      </span>
    </div>

    <div v-if="error" class="inv-error">{{ error }}</div>

    <div v-if="officialFile && !error" class="inv-official no-print">
      <span>
        {{ tt('官方票面已归档') }}：{{ officialFile['文件名'] }}
        （{{ officialFile['文件类型'] }} · {{ kb(officialFile['大小']) }} · {{ officialFile['上传时间'] }}）
      </span>
      <span class="inv-official-acts">
        <el-button v-if="isPdf" size="small" type="primary" link @click="previewOfficial">
          {{ tt('预览') }}
        </el-button>
        <el-button size="small" type="primary" link @click="downloadOfficial">
          {{ tt('下载官方 OFD/PDF') }}
        </el-button>
      </span>
    </div>

    <div v-if="!error" class="invoice">
      <!-- 抬头：二维码 / 标题 / 号码与日期 -->
      <div class="inv-head">
        <div class="qr-box">
          <div class="qr" aria-label="二维码占位">
            <span>二维码</span>
          </div>
        </div>

        <div class="head-center">
          <div class="inv-title">{{ title }}</div>
          <div class="seal" aria-label="发票监制章占位">
            <span class="seal-main">发票监制章</span>
            <span class="seal-sub">税务机关开具时生成</span>
          </div>
        </div>

        <div class="head-right">
          <div class="meta-row"><span>发票号码：</span><b>{{ invoice.no }}</b></div>
          <div class="meta-row"><span>开票日期：</span><b>{{ invoice.dateCn }}</b></div>
        </div>
      </div>

      <!-- 主体表格 -->
      <table class="inv-table">
        <colgroup>
          <col style="width: 4%" /><col style="width: 27%" /><col style="width: 14%" />
          <col style="width: 12%" /><col style="width: 6%" /><col style="width: 7%" />
          <col style="width: 9%" /><col style="width: 9%" /><col style="width: 9%" />
          <col style="width: 3%" />
        </colgroup>

        <tbody>
          <!-- 购买方 / 销售方：左半用前 5 列，右半用后 5 列 -->
          <tr class="party-row">
            <td class="vlabel" rowspan="2">购买方信息</td>
            <td class="party-name" colspan="4"><span class="lb">名称：</span>{{ buyer.name }}</td>
            <td class="vlabel" rowspan="2">销售方信息</td>
            <td class="party-name" colspan="4"><span class="lb">名称：</span>{{ seller.name }}</td>
          </tr>
          <tr class="party-row">
            <td class="party-code" colspan="4">
              <span class="lb">统一社会信用代码/纳税人识别号：</span>{{ buyer.taxNo }}
            </td>
            <td class="party-code" colspan="4">
              <span class="lb">统一社会信用代码/纳税人识别号：</span>{{ seller.taxNo }}
            </td>
          </tr>

          <!-- 明细表头 -->
          <tr class="items-head">
            <td colspan="2" class="mid">项目名称</td>
            <td class="mid">规格型号</td>
            <td class="mid">单位</td>
            <td class="mid">数量</td>
            <td class="mid">单价</td>
            <td class="mid">金额</td>
            <td class="mid">税率/征收率</td>
            <td colspan="2" class="mid">税额</td>
          </tr>

          <!-- 明细行 -->
          <tr v-for="(it, i) in items" :key="i" class="item-row">
            <td colspan="2" class="pname">{{ it['存货名称'] }}</td>
            <td class="mid">{{ it['规格型号'] }}</td>
            <td class="mid">{{ it['计量单位'] }}</td>
            <td class="num">{{ fmt(it['数量']) }}</td>
            <td class="num">{{ money(it['无税单价']) }}</td>
            <td class="num">{{ money(net(it)) }}</td>
            <td class="num">{{ rate(it['税率%']) }}</td>
            <td colspan="2" class="num">{{ money(it['税额']) }}</td>
          </tr>
          <tr v-if="!items.length">
            <td colspan="10" class="mid empty">无明细</td>
          </tr>

          <!-- 合计 -->
          <tr class="sum-row">
            <td colspan="7" class="sum-label">合<span class="gap"></span>计</td>
            <td class="num">¥{{ money(totalNet) }}</td>
            <td colspan="2" class="num">¥{{ money(totalTax) }}</td>
          </tr>

          <!-- 价税合计 -->
          <tr class="gross-row">
            <td colspan="3" class="mid bold">价税合计(大写)</td>
            <td colspan="4" class="upper">
              <span class="cny">⊗</span>{{ upper }}
            </td>
            <td colspan="3" class="num bold">（小写）¥{{ money(totalGross) }}</td>
          </tr>

          <!-- 备注 -->
          <tr class="remark-row">
            <td class="vlabel">备<span class="vspace"></span>注</td>
            <td colspan="9" class="remark">{{ head['备注'] || '' }}</td>
          </tr>
        </tbody>
      </table>

      <div class="inv-foot">
        <span class="drawer">开票人：{{ drawer }}</span>
        <span v-if="payee" class="drawer">收款人：{{ payee }}</span>
        <span v-if="reviewer" class="drawer">复核：{{ reviewer }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePanelRuntime } from '@core/panel-runtime'
import { tt } from '@/i18n'

const engine = usePanelRuntime()
const route = useRoute()
const router = useRouter()

const head = ref({})
const items = ref([])
const error = ref('')
const buyerTaxNo = ref('')
const seller = ref({ name: '—', taxNo: '—' })
/** 该发票回传的官方票面（有则以它为准，票样仅作核对） */
const officialFile = ref(null)
const signers = ref({ drawer: '', payee: '', reviewer: '' })

const buyer = computed(() => ({
  name: head.value['客户'] || head.value['结算客户'] || '',
  taxNo: buyerTaxNo.value || '—',
}))

/** 数电票抬头：普通发票 / 增值税专用发票。 */
const title = computed(() => {
  const t = String(head.value['开票类型'] || '')
  return t.includes('专用') ? '电子发票（增值税专用发票）' : '电子发票（普通发票）'
})

const drawer = computed(() => signers.value.drawer || head.value['业务员'] || head.value['开票人'] || 'admin')
const payee = computed(() => signers.value.payee || '')
const reviewer = computed(() => signers.value.reviewer || '')

/**
 * 发票号码：数电票为 20 位、无发票代码。
 * 这里按单据号稳定派生（保证复打一致），正式号码由电子税务局赋码。
 */
const invoice = computed(() => {
  const seed = String(head.value['单据编号'] || route.query.code || 'INV')
  let h = 0
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) % 1e15
  const digits = String(h).padStart(15, '0')
  const year = String(head.value['单据日期'] || '').slice(2, 4) || '00'
  return {
    no: `${year}${digits}000`.slice(0, 20),
    dateCn: toCnDate(head.value['单据日期']),
  }
})

const totalNet = computed(() => items.value.reduce((s, it) => s + net(it), 0))
const totalTax = computed(() => items.value.reduce((s, it) => s + (Number(it['税额']) || 0), 0))
const totalGross = computed(() => items.value.reduce((s, it) => s + (Number(it['价税合计']) || 0), 0))
const upper = computed(() => toChineseAmount(totalGross.value))

function toCnDate(d) {
  const s = String(d || '')
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/)
  return m ? `${m[1]}年${m[2]}月${m[3]}日` : s
}
function net(it) {
  const gross = Number(it['价税合计']) || 0
  const tax = Number(it['税额']) || 0
  return Math.round((gross - tax) * 100) / 100
}
function money(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : '0.00'
}
function fmt(v) {
  const n = Number(v)
  return Number.isFinite(n) ? String(Math.round(n * 100) / 100) : ''
}
function rate(v) {
  const n = Number(v)
  return Number.isFinite(n) && n > 0 ? `${n}%` : '0%'
}

/** 人民币金额大写（数电票用「圆」）。 */
function toChineseAmount(value) {
  const num = Math.round((Number(value) || 0) * 100)
  if (num === 0) return '零圆整'
  const digits = '零壹贰叁肆伍陆柒捌玖'
  const units = ['', '拾', '佰', '仟']
  const bigUnits = ['', '万', '亿', '兆']
  let yuan = Math.floor(num / 100)
  const jiao = Math.floor((num % 100) / 10)
  const fen = num % 10
  let out = ''
  let group = 0
  if (yuan === 0) {
    out = '零'
  } else {
    while (yuan > 0) {
      const part = yuan % 10000
      let partStr = ''
      let zero = false
      let p = part
      let pos = 0
      while (p > 0) {
        const d = p % 10
        if (d === 0) {
          zero = true
        } else {
          partStr = digits[d] + units[pos] + (zero ? '零' : '') + partStr
          zero = false
        }
        p = Math.floor(p / 10)
        pos++
      }
      if (partStr) out = partStr + bigUnits[group] + out
      yuan = Math.floor(yuan / 10000)
      group++
    }
  }
  out += '圆'
  if (jiao === 0 && fen === 0) return out + '整'
  if (jiao > 0) out += digits[jiao] + '角'
  else if (fen > 0) out += '零'
  if (fen > 0) out += digits[fen] + '分'
  return out
}

async function loadCompany() {
  try {
    const info = await engine.companyInfo()
    seller.value = {
      name: info?.['名称'] || '—',
      taxNo: info?.['纳税人识别号'] || '—',
    }
    signers.value = {
      drawer: info?.['开票人'] || '',
      payee: info?.['收款人'] || '',
      reviewer: info?.['复核人'] || '',
    }
  } catch (e) {
    // 企业信息取不到时保留占位，不影响版式
  }
}

/** 购买方统一社会信用代码：从往来单位档案按客户编码取。 */
async function loadBuyerTaxNo(customerCode) {
  if (!customerCode) return
  try {
    const res = await engine.queryFormDataList({ panelCode: 'PARTNER', pageNo: 1, pageSize: 500 })
    const hit = (res?.list || []).find(
      (r) => String(r['往来单位编码'] || '') === String(customerCode)
    )
    if (hit && hit['统一社会信用代码']) buyerTaxNo.value = String(hit['统一社会信用代码'])
  } catch (e) {
    // 档案取不到就留空，票面上显示「—」
  }
}

function kb(v) {
  const n = Number(v) || 0
  return n > 1024 * 1024 ? (n / 1024 / 1024).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB'
}

const isPdf = computed(() => String(officialFile.value?.['文件类型'] || '').toUpperCase() === 'PDF')

async function previewOfficial() {
  if (!officialFile.value) return
  try {
    await engine.invoiceFilePreview(officialFile.value.id, officialFile.value['文件名'])
  } catch (e) {
    error.value = engine.errMsg(e) || tt('预览失败')
  }
}

async function downloadOfficial() {
  if (!officialFile.value) return
  try {
    await engine.invoiceFileDownload(officialFile.value.id, officialFile.value['文件名'])
  } catch (e) {
    error.value = engine.errMsg(e) || tt('下载失败')
  }
}

function print() {
  window.print()
}
/**
 * 返回开票工作台。
 *
 * <p>打印页是从工作台 window.open 打开的新标签页，新标签页没有可退的历史，
 * router.back() 会毫无反应——所以优先直接关掉本标签页（关掉即回到工作台），
 * 直接输网址打开时再跳到工作台。
 */
function goBack() {
  const openedByApp = window.opener && !window.opener.closed
  if (openedByApp) {
    window.close()
    // 浏览器不允许脚本关闭时（极少见），退回到工作台
    window.setTimeout(() => {
      if (!window.closed) router.push('/scm/invoiceWorkbench')
    }, 120)
    return
  }
  router.push('/scm/invoiceWorkbench')
}

async function loadInvoice(code) {
  error.value = ''
  if (!code) {
    error.value = tt('缺少发票单据号')
    return
  }
  try {
    const list = await engine.queryFormDataList({ panelCode: 'SALE_INVOICE', condition: { 编号: code }, pageNo: 1, pageSize: 1 })
    const row = (list?.list || [])[0]
    if (!row) {
      error.value = tt('未找到该发票单据') + '：' + code
      return
    }
    head.value = row
    items.value = row.detail?.items || []
    buyerTaxNo.value = ''
    await loadBuyerTaxNo(row['客户编码'])
    try {
      const files = await engine.invoiceFileList(code)
      officialFile.value = (files && files[0]) || null
    } catch (e) {
      officialFile.value = null
    }
    if (row['单据状态'] !== '已审核') {
      error.value = tt('发票尚未审核，审批通过后才能打印') + `（当前：${row['单据状态'] || '-'}）`
    }
  } catch (e) {
    error.value = engine.errMsg(e) || tt('加载发票失败')
  }
}

onMounted(async () => {
  await loadCompany()
  await loadInvoice(String(route.query.code || ''))
})

// 同一路由只变 ?code=（例如从列表连点两张票）时组件会被复用，
// 必须跟着 query 重新取数，否则会一直显示上一张票。
watch(
  () => route.query.code,
  (code) => {
    if (route.path.includes('print/invoice')) loadInvoice(String(code || ''))
  }
)
</script>

<style scoped>
.inv-page {
  min-height: 100vh;
  background: #f5f6f8;
  padding: 16px;
}
.inv-toolbar {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
}
.tb {
  height: 32px;
  padding: 0 14px;
  border: 1px solid #1677ff;
  background: #1677ff;
  color: #fff;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}
.tb.ghost {
  background: #fff;
  color: #333;
  border-color: #d9d9d9;
}
.tb-tip {
  color: #8c8c8c;
  font-size: 12px;
}
.inv-official {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  max-width: 1000px;
  margin: 0 auto 10px;
  padding: 8px 12px;
  background: #f6ffed;
  border: 1px solid #b7eb8f;
  border-radius: 6px;
  color: #237804;
  font-size: 12px;
}
.inv-official-acts {
  display: flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.inv-error {
  padding: 24px;
  background: #fff;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  color: #cf1322;
  font-size: 13px;
}

/* ===== 票面 ===== */
.invoice {
  width: 1000px;
  margin: 0 auto;
  background: #fff;
  padding: 14px 16px 10px;
  color: #9e2b25;
  font-family: "SimSun", "Songti SC", "STSong", serif;
  font-size: 13px;
}

.inv-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 6px;
}
.qr-box {
  width: 118px;
  flex: 0 0 118px;
}
.qr {
  width: 118px;
  height: 118px;
  border: 1px dashed #c9a3a0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #c9a3a0;
  font-size: 12px;
  background: repeating-linear-gradient(45deg, #fff, #fff 4px, #faf3f2 4px, #faf3f2 8px);
}
.head-center {
  flex: 1;
  position: relative;
  text-align: center;
  padding-top: 6px;
}
.inv-title {
  font-size: 27px;
  font-weight: 700;
  letter-spacing: 3px;
  color: #9e2b25;
}
.seal {
  position: absolute;
  left: 50%;
  top: 0;
  transform: translateX(-50%);
  width: 190px;
  height: 96px;
  border: 2px dashed #c0392b;
  border-radius: 50%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  opacity: 0.55;
  pointer-events: none;
}
.seal-main {
  font-size: 12px;
  letter-spacing: 2px;
}
.seal-sub {
  font-size: 10px;
  letter-spacing: 1px;
}
.head-right {
  flex: 0 0 300px;
  text-align: right;
  padding-top: 4px;
}
.meta-row {
  font-size: 13px;
  line-height: 1.9;
}
.meta-row b {
  font-family: ui-monospace, Menlo, monospace;
  letter-spacing: 0.5px;
}

.inv-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}
.inv-table td {
  border: 1px solid #b5655f;
  padding: 5px 6px;
  vertical-align: middle;
  word-break: break-all;
}
.vlabel {
  text-align: center;
  border-right: 2px solid #b5655f;
  font-size: 12px;
  /* 与真实票面一致：购买方信息 / 销售方信息 / 备 注 为竖排 */
  writing-mode: vertical-rl;
  text-orientation: upright;
  letter-spacing: 2px;
  line-height: 1.2;
  white-space: nowrap;
  padding: 6px 4px;
}
.party-row .party-name {
  border-bottom: 1px dashed #d9b3b0;
}
.lb {
  color: #9e2b25;
}
.items-head td {
  text-align: center;
  letter-spacing: 1px;
  border-top: 2px solid #b5655f;
  border-bottom: 2px solid #b5655f;
}
.item-row .pname {
  line-height: 1.5;
}
.mid {
  text-align: center;
}
.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-family: ui-monospace, Menlo, monospace;
}
.bold {
  font-weight: 700;
}
.sum-row .sum-label {
  text-align: center;
  letter-spacing: 6px;
  font-weight: 700;
}
.gap {
  display: inline-block;
  width: 12px;
}
.gross-row .upper {
  letter-spacing: 2px;
  font-weight: 700;
}
.cny {
  display: inline-block;
  width: 16px;
  height: 16px;
  line-height: 15px;
  text-align: center;
  border: 1px solid #b5655f;
  border-radius: 50%;
  font-size: 11px;
  margin-right: 8px;
}
.remark-row .remark {
  height: 62px;
  vertical-align: top;
}
.vspace {
  display: inline-block;
  width: 6px;
}
.empty {
  color: #b5655f;
}
.inv-foot {
  display: flex;
  gap: 28px;
  margin-top: 10px;
  padding-left: 8px;
  font-size: 13px;
}
.drawer {
  letter-spacing: 1px;
}

@media print {
  .no-print {
    display: none !important;
  }
  .inv-page {
    background: #fff;
    padding: 0;
  }
  .invoice {
    width: auto;
    margin: 0;
    padding: 0;
  }
  @page {
    size: A4 landscape;
    margin: 8mm;
  }
}
</style>