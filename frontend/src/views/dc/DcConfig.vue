<!-- DcConfig.vue — IOT设备配置（数采）
     设备接入五连：绑定网关 → 配置数采工程 → 绘制组态 → 配置数采规则 → 数据调试。
     配置维护仅管理员可写；后端 DcController 自己也做管理员校验，这里只控制按钮显示。
     视觉：容器挂 .adx，取全局设计库语义层（--d-*）。 -->
<template>
  <div class="adx dc dc-config">
    <header class="dc-head">
      <div class="dc-title">
        <h2>IOT设备配置</h2>
        <span class="dc-sub">
          设备接入五连：绑定网关 → 配置数采工程 → 绘制组态 → 配置数采规则 → 数据调试
          <span class="dot">·</span>五项齐备才算接入完成，未接入设备一律显示离线
        </span>
      </div>
      <div class="dc-actions">
        <el-button v-if="canWrite" type="primary" :icon="Plus" @click="openDevice(null)">新增设备</el-button>
        <el-button v-if="canWrite" :icon="Connection" @click="openGateway(null)">新增网关</el-button>
        <el-button @click="helpVisible = true">数据接入说明</el-button>
        <el-button :loading="loading" @click="load(true)">刷新</el-button>
      </div>
    </header>

    <section class="dc-metrics">
      <div v-for="m in kpiCards" :key="m.label" class="dc-metric">
        <span class="dc-metric-label">{{ m.label }}</span>
        <span class="dc-metric-value" :class="m.tone">{{ m.value }}</span>
      </div>
    </section>

    <div class="dc-filter">
      <el-input v-model="keyword" class="dc-search" placeholder="搜索设备编码 / 名称 / 网关" clearable>
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-checkbox v-model="onlyUnfinished">只看未完成</el-checkbox>
      <span class="dc-hint">共 {{ rows.length }} 台设备</span>
      <span v-if="!canWrite" class="dc-hint">配置维护仅管理员可操作，当前为只读查看</span>
    </div>

    <section class="dc-card">
      <div class="dc-card-head">
        <span>设备接入五连</span>
        <span class="dc-hint">✓ 已完成 / ✗ 未完成；缺哪一项看「缺失提示」列</span>
      </div>
      <el-table v-if="devices.length || loading" v-loading="loading" :data="rows" row-key="id" class="dc-table"
                empty-text="没有匹配的设备，换个关键词或取消「只看未完成」">
        <el-table-column label="设备编码" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="dc-dot" :class="dotTone(row)"></span>
            <span class="dc-code">{{ row.code }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="设备名称" min-width="140" show-overflow-tooltip />
        <el-table-column label="类型" width="96">
          <template #default="{ row }"><span class="dc-muted">{{ row.type || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="网关" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.gatewayName || row.gatewayId" class="dc-muted">{{ row.gatewayName || row.gatewayId }}</span>
            <span v-else class="dc-tag muted">未绑定</span>
          </template>
        </el-table-column>
        <el-table-column label="五连检查" width="164" align="center">
          <template #default="{ row }">
            <span class="dc-checks">
              <el-tooltip v-for="c in checks" :key="c.key" :content="c.label" placement="top">
                <span class="dc-check" :class="isChecked(row, c.key) ? 'ok' : 'no'">
                  <el-icon><Check v-if="isChecked(row, c.key)" /><Close v-else /></el-icon>
                </span>
              </el-tooltip>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="完成度" width="132">
          <template #default="{ row }">
            <el-progress :percentage="pct(row.progress)" :stroke-width="8" />
          </template>
        </el-table-column>
        <el-table-column label="缺失提示" min-width="150">
          <template #default="{ row }">
            <span v-if="tipOf(row)" class="dc-tag warn">{{ tipOf(row) }}</span>
            <span v-else-if="pct(row.progress) >= 100" class="dc-tag ok">配置齐备</span>
            <span v-else class="dc-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="采集点" width="84" align="right">
          <template #default="{ row }">
            <el-button v-if="canWrite" link type="primary" @click="openPoints(row)">{{ row.pointCount ?? 0 }}</el-button>
            <span v-else class="num">{{ row.pointCount ?? 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column label="规则" width="72" align="right">
          <template #default="{ row }">
            <el-button v-if="canWrite" link type="primary" @click="openRules(row)">{{ row.ruleCount ?? 0 }}</el-button>
            <span v-else class="num">{{ row.ruleCount ?? 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column label="报警" width="72" align="right">
          <template #default="{ row }">
            <span class="num" :class="{ warn: Number(row.alarmOpen) > 0 }">{{ row.alarmOpen ?? 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column v-if="canWrite" label="操作" width="252" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDevice(row)">编辑</el-button>
            <el-button link type="primary" :disabled="isChecked(row, 'debug')"
                       :loading="debuggingId === row.id" @click="markDebugged(row)">标记调试</el-button>
            <el-button link type="danger" @click="removeDevice(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-else class="dc-empty-block">
        <span>尚未登记采集设备</span>
        <span class="dc-hint">电子秤 / 看板 / 扫码枪等设备登记后，五连检查与在线状态才会出现在这里</span>
        <el-button v-if="canWrite" type="primary" :icon="Plus" @click="openDevice(null)">新增设备</el-button>
      </div>
    </section>

    <section class="dc-card">
      <div class="dc-card-head">
        <span>数采网关</span>
        <span class="dc-hint">设备需绑定网关才能上报；密钥用于硬件上报时的 X-DC-Key 校验</span>
      </div>
      <el-table :data="gateways" class="dc-table" empty-text="尚未登记网关；设备接入前请先新增网关">
        <el-table-column prop="code" label="网关编码" min-width="120" show-overflow-tooltip />
        <el-table-column prop="name" label="网关名称" min-width="140" show-overflow-tooltip />
        <el-table-column label="协议" width="118">
          <template #default="{ row }"><span class="dc-muted">{{ row.protocol || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="地址" min-width="150" show-overflow-tooltip>
          <template #default="{ row }"><span class="dc-muted">{{ addressOf(row) }}</span></template>
        </el-table-column>
        <el-table-column label="设备数" width="82" align="right">
          <template #default="{ row }"><span class="num">{{ row.deviceCount ?? 0 }}</span></template>
        </el-table-column>
        <el-table-column label="密钥" width="92">
          <template #default="{ row }">
            <span class="dc-tag" :class="row.keySet ? 'ok' : 'muted'">{{ row.keySet ? '已配' : '未配' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="88">
          <template #default="{ row }">
            <span class="dc-tag" :class="row.enabled === false ? 'muted' : row.status ? 'ok' : 'warn'">
              {{ gatewayStatusText(row) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="最近上报" width="152">
          <template #default="{ row }"><span class="dc-muted">{{ fmtAt(row.lastSeenAt) }}</span></template>
        </el-table-column>
        <el-table-column v-if="canWrite" label="启停" width="76" align="center">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled !== false" size="small" @change="toggleGateway(row, $event)" />
          </template>
        </el-table-column>
        <el-table-column v-if="canWrite" label="操作" width="112" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openGateway(row)">编辑</el-button>
            <el-button link type="danger" @click="removeGateway(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <!-- 设备 -->
    <el-dialog v-model="deviceVisible" :title="deviceForm.id ? '编辑设备' : '新增设备'" width="min(680px, 94vw)" append-to-body>
      <el-form :model="deviceForm" label-width="96px" @submit.prevent>
        <div class="dc-form-grid">
          <el-form-item label="设备编码" required>
            <el-input v-model="deviceForm.code" maxlength="32" placeholder="如 SCALE-01，唯一" />
          </el-form-item>
          <el-form-item label="设备名称" required>
            <el-input v-model="deviceForm.name" maxlength="64" placeholder="如 一号线电子秤" />
          </el-form-item>
          <el-form-item label="设备类型">
            <el-select v-model="deviceForm.type" filterable allow-create default-first-option placeholder="选择或输入类型">
              <el-option v-for="o in DEVICE_TYPES" :key="o" :label="o" :value="o" />
            </el-select>
          </el-form-item>
          <el-form-item label="规格型号">
            <el-input v-model="deviceForm.model" maxlength="64" placeholder="选填" />
          </el-form-item>
          <el-form-item label="出厂编号">
            <el-input v-model="deviceForm.serialNo" maxlength="64" placeholder="选填" />
          </el-form-item>
          <el-form-item label="所属网关">
            <el-select v-model="deviceForm.gatewayId" clearable placeholder="未绑定">
              <el-option v-for="g in gateways" :key="g.id" :label="gatewayLabel(g)" :value="g.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="IP 地址">
            <el-input v-model="deviceForm.ip" maxlength="64" placeholder="如 192.168.1.20" />
          </el-form-item>
          <el-form-item label="端口">
            <el-input v-model="deviceForm.port" maxlength="8" placeholder="如 502" />
          </el-form-item>
          <el-form-item label="所属车间">
            <el-input v-model="deviceForm.workshop" maxlength="64" placeholder="选填" />
          </el-form-item>
          <el-form-item label="数采工程">
            <el-input v-model="deviceForm.projectCode" maxlength="64" placeholder="数采工程编码，选填" />
          </el-form-item>
          <el-form-item label="心跳(秒)">
            <el-input v-model="deviceForm.heartbeatSeconds" maxlength="6" placeholder="如 60" />
          </el-form-item>
          <el-form-item label="启用">
            <el-switch v-model="deviceForm.enabled" />
          </el-form-item>
          <el-form-item label="已绘制组态">
            <el-switch v-model="deviceForm.scadaDrawn" />
          </el-form-item>
          <el-form-item label="备注" class="dc-span2">
            <el-input v-model="deviceForm.remark" type="textarea" :rows="2" maxlength="200" show-word-limit />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="deviceVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingDevice" @click="submitDevice">保存</el-button>
      </template>
    </el-dialog>

    <!-- 网关 -->
    <el-dialog v-model="gatewayVisible" :title="gatewayForm.id ? '编辑网关' : '新增网关'" width="min(560px, 94vw)" append-to-body>
      <el-form :model="gatewayForm" label-width="96px" @submit.prevent>
        <el-form-item label="网关编码" required>
          <el-input v-model="gatewayForm.code" maxlength="32" placeholder="如 DC-GW-01，唯一" />
        </el-form-item>
        <el-form-item label="网关名称" required>
          <el-input v-model="gatewayForm.name" maxlength="64" placeholder="如 一号车间网关" />
        </el-form-item>
        <el-form-item label="通信协议">
          <el-select v-model="gatewayForm.protocol" filterable allow-create default-first-option placeholder="选择或输入协议">
            <el-option v-for="o in GATEWAY_PROTOCOLS" :key="o" :label="o" :value="o" />
          </el-select>
        </el-form-item>
        <el-form-item label="地址">
          <el-input v-model="gatewayForm.host" maxlength="64" placeholder="IP 或域名" />
        </el-form-item>
        <el-form-item label="端口">
          <el-input v-model="gatewayForm.port" maxlength="8" placeholder="如 8899" />
        </el-form-item>
        <el-form-item label="上报密钥">
          <el-input v-model="gatewayForm.apiKey" maxlength="64" :placeholder="gatewayForm.keySet ? '已配置，留空表示不改' : '选填，硬件上报时用 X-DC-Key 校验'" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="gatewayForm.enabled" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="gatewayForm.remark" type="textarea" :rows="2" maxlength="200" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="gatewayVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingGateway" @click="submitGateway">保存</el-button>
      </template>
    </el-dialog>

    <!-- 采集点 -->
    <el-dialog v-model="pointVisible" :title="`采集点 · ${deviceLabel(pointDevice)}`" width="min(860px, 96vw)" append-to-body>
      <p class="dc-dialog-tip">采集点编码就是硬件上报报文里的 pointCode，须与设备侧一致；未登记的采集点上报会被拒绝</p>
      <el-form :model="pointForm" label-width="100px" @submit.prevent>
        <div class="dc-form-grid">
          <el-form-item label="采集点编码" required>
            <el-input v-model="pointForm.code" maxlength="32" placeholder="如 weight" />
          </el-form-item>
          <el-form-item label="采集点名称" required>
            <el-input v-model="pointForm.name" maxlength="64" placeholder="如 重量" />
          </el-form-item>
          <el-form-item label="数据类型">
            <el-select v-model="pointForm.dataType">
              <el-option v-for="o in DATA_TYPES" :key="o" :label="o" :value="o" />
            </el-select>
          </el-form-item>
          <el-form-item label="单位">
            <el-input v-model="pointForm.unit" maxlength="16" placeholder="如 kg" />
          </el-form-item>
          <el-form-item label="采集频率(秒)">
            <el-input v-model="pointForm.freqSeconds" maxlength="6" />
          </el-form-item>
          <el-form-item label="下限">
            <el-input v-model="pointForm.lowLimit" maxlength="12" placeholder="选填，越限报警用" />
          </el-form-item>
          <el-form-item label="上限">
            <el-input v-model="pointForm.highLimit" maxlength="12" placeholder="选填，越限报警用" />
          </el-form-item>
          <el-form-item label="地址">
            <el-input v-model="pointForm.addr" maxlength="32" placeholder="如 40001，选填" />
          </el-form-item>
        </div>
        <el-form-item label="启用">
          <el-switch v-model="pointForm.enabled" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="savingPoint" @click="submitPoint">
            {{ pointForm.id ? '保存' : '新增采集点' }}
          </el-button>
          <el-button v-if="pointForm.id" @click="pointForm = emptyPoint()">取消编辑</el-button>
        </el-form-item>
      </el-form>
      <el-table v-loading="pointLoading" :data="points" empty-text="该设备尚未登记采集点">
        <el-table-column prop="code" label="编码" min-width="110" show-overflow-tooltip />
        <el-table-column prop="name" label="名称" min-width="110" show-overflow-tooltip />
        <el-table-column label="类型" width="86">
          <template #default="{ row }"><span class="dc-muted">{{ row.dataType || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="单位" width="70">
          <template #default="{ row }"><span class="dc-muted">{{ row.unit || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="上下限" width="120">
          <template #default="{ row }">
            <span class="num dc-muted">{{ dash(row.lowLimit) }} ~ {{ dash(row.highLimit) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="频率(秒)" width="88" align="right">
          <template #default="{ row }"><span class="num">{{ row.freqSeconds ?? '—' }}</span></template>
        </el-table-column>
        <el-table-column v-if="canWrite" label="操作" width="112" align="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="editPoint(row)">编辑</el-button>
            <el-button link type="danger" @click="removePoint(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="pointVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 数采规则 -->
    <el-dialog v-model="ruleVisible" :title="`数采规则 · ${deviceLabel(ruleDevice)}`" width="min(760px, 96vw)" append-to-body>
      <p class="dc-dialog-tip">至少一条启用状态的规则，设备「配置数采规则」一项才算完成；报警级别由后端按规则判定</p>
      <el-form :model="ruleForm" label-width="100px" @submit.prevent>
        <el-form-item label="规则名称" required>
          <el-input v-model="ruleForm.name" maxlength="64" placeholder="如 重量越限报警" />
        </el-form-item>
        <el-form-item label="规则类型">
          <el-select v-model="ruleForm.ruleType">
            <el-option v-for="o in RULE_TYPES" :key="o" :label="o" :value="o" />
          </el-select>
        </el-form-item>
        <el-form-item label="规则表达式">
          <el-input v-model="ruleForm.expression" maxlength="200" placeholder="如 value > 50" />
        </el-form-item>
        <el-form-item label="参数">
          <el-input v-model="ruleForm.params" maxlength="200" placeholder="选填，如 采样次数=3" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="ruleForm.enabled" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="savingRule" @click="submitRule">
            {{ ruleForm.id ? '保存' : '新增规则' }}
          </el-button>
          <el-button v-if="ruleForm.id" @click="ruleForm = emptyRule()">取消编辑</el-button>
        </el-form-item>
      </el-form>
      <el-table v-loading="ruleLoading" :data="rules" empty-text="该设备尚未配置数采规则">
        <el-table-column prop="name" label="规则名称" min-width="140" show-overflow-tooltip />
        <el-table-column label="类型" width="104">
          <template #default="{ row }"><span class="dc-muted">{{ row.ruleType || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="表达式" min-width="160" show-overflow-tooltip>
          <template #default="{ row }"><span class="dc-muted">{{ row.expression || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="启用" width="78">
          <template #default="{ row }">
            <span class="dc-tag" :class="row.enabled ? 'ok' : 'muted'">{{ row.enabled ? '启用' : '停用' }}</span>
          </template>
        </el-table-column>
        <el-table-column v-if="canWrite" label="操作" width="112" align="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="editRule(row)">编辑</el-button>
            <el-button link type="danger" @click="removeRule(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="ruleVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 硬件接入说明 -->
    <el-dialog v-model="helpVisible" title="数据接入说明" width="min(680px, 94vw)" append-to-body>
      <div class="dc-help">
        <p>本模块只做配置与展示；实时数据由数采网关（或边缘程序）调用下列接口上报，未上报的设备一律显示离线。</p>
        <h4>① 首次上报前先登记</h4>
        <p>在「IOT设备配置」登记网关与设备，网关编码是上报时的唯一标识；网关里配的上报密钥用于 <code>X-DC-Key</code> 校验。</p>
        <h4>② JSON 上报</h4>
        <pre class="dc-code">POST /api/px/dc/ingest
X-DC-Key: &lt;网关密钥&gt;
{
  "gatewayCode": "DC-GW-01",
  "deviceCode": "SCALE-01",
  "values": [ { "pointCode": "weight", "value": 12.35 } ]
}</pre>
        <h4>③ 文本上报口（串口透传模块用）</h4>
        <pre class="dc-code">POST /api/px/dc/ingest/text?device=SCALE-01&amp;data=12.35kg
X-DC-Key: &lt;网关密钥&gt;</pre>
        <h4>④ 心跳</h4>
        <pre class="dc-code">POST /api/px/dc/heartbeat
{ "deviceCode": "SCALE-01" }</pre>
        <p>上报后「数据监控」会出现采集点与实时值；报警由后端按数采规则判定。</p>
      </div>
      <template #footer>
        <el-button type="primary" @click="helpVisible = false">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Close, Connection, Plus, Search } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import {
  debugDcDevice,
  deleteDcDevice,
  deleteDcGateway,
  deleteDcPoint,
  deleteDcRule,
  getDcConfig,
  getDcPoints,
  getDcRules,
  saveDcDevice,
  saveDcGateway,
  saveDcPoint,
  saveDcRule,
} from '@/business/dcApi'

const user = useUserStore()
const canWrite = computed(() => !!user.isAdmin)

const DEFAULT_CHECKS = [
  { key: 'gateway', label: '绑定网关' },
  { key: 'project', label: '配置数采工程' },
  { key: 'scada', label: '绘制组态' },
  { key: 'rule', label: '配置数采规则' },
  { key: 'debug', label: '数据调试' },
]
const DEVICE_TYPES = ['电子秤', '看板', '扫码枪', '温湿度计', 'PLC', '加工设备', '产线',
  '检测设备', '包装设备', '公用设备', '办公终端', '其他']
const GATEWAY_PROTOCOLS = ['MODBUS-TCP', 'MODBUS-RTU', 'MQTT', 'HTTP', 'OPC-UA', '串口/RS485']
const DATA_TYPES = ['数值', '布尔', '文本']
const RULE_TYPES = ['定时采集', '变化上报', '越限报警']

const loading = ref(false)
const kpis = ref({})
const gateways = ref([])
const devices = ref([])
const checkDefs = ref([])
const keyword = ref('')
const onlyUnfinished = ref(false)
const debuggingId = ref(null)
const helpVisible = ref(false)

const checks = computed(() => (checkDefs.value?.length ? checkDefs.value : DEFAULT_CHECKS))

const kpiCards = computed(() => {
  const k = kpis.value || {}
  const total = num(k.deviceTotal, devices.value.length)
  const bound = num(k.gatewayBound, devices.value.filter((d) => isChecked(d, 'gateway')).length)
  const fully = num(k.fullyReady, devices.value.filter((d) => pct(d.progress) >= 100).length)
  return [
    { label: '设备总数', value: total, tone: '' },
    { label: '已绑网关', value: bound, tone: bound > 0 ? 'ok' : '' },
    { label: '五连齐备', value: fully, tone: fully > 0 ? 'ok' : '' },
  ]
})

const rows = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return devices.value.filter((d) => {
    if (onlyUnfinished.value && pct(d.progress) >= 100) return false
    if (!kw) return true
    return [d.code, d.name, d.type, d.gatewayName, d.workshop, d.projectCode]
      .some((v) => String(v ?? '').toLowerCase().includes(kw))
  })
})

function num(v, fallback) {
  const n = Number(v)
  return Number.isFinite(n) ? n : fallback
}
function pct(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return 0
  return Math.min(100, Math.max(0, Math.round(n)))
}
function isChecked(row, key) {
  return !!(row && row.checks && row.checks[key])
}
function dotTone(row) {
  if (row.enabled === false || row.status === 'disabled') return 'off'
  return row.online ? 'on' : 'off'
}
function tipOf(row) {
  if (row?.missingTip) return row.missingTip
  if (pct(row?.progress) >= 100) return ''
  const missing = checks.value.filter((c) => !isChecked(row, c.key)).map((c) => c.label)
  return missing.length ? `缺：${missing.join('、')}` : ''
}
function fmtAt(v) {
  if (!v) return '—'
  return String(v).replace('T', ' ').slice(0, 19)
}
function addressOf(row) {
  const host = row?.host || ''
  const port = row?.port ?? ''
  if (!host && port === '') return '—'
  return port === '' ? host : `${host}:${port}`
}
function gatewayLabel(g) {
  if (!g) return ''
  return g.code ? `${g.code} · ${g.name || ''}` : (g.name || String(g.id))
}
function gatewayStatusText(row) {
  if (row.enabled === false) return '已停用'
  return row.status ? '在线' : '离线'
}
function deviceLabel(d) {
  if (!d) return ''
  return d.code ? `${d.code} · ${d.name || ''}` : (d.name || String(d.id))
}
function dash(v) {
  return v === null || v === undefined || v === '' ? '—' : v
}
function numOrNull(v) {
  if (v === '' || v === null || v === undefined) return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}
function errText(e, fallback) {
  return e?.message || fallback
}

async function load(showToast) {
  loading.value = true
  try {
    const data = await getDcConfig()
    kpis.value = data?.kpis || {}
    gateways.value = Array.isArray(data?.gateways) ? data.gateways : []
    devices.value = Array.isArray(data?.devices) ? data.devices : []
    checkDefs.value = Array.isArray(data?.checkDefs) ? data.checkDefs : []
    if (showToast) ElMessage.success('已刷新')
  } catch (e) {
    ElMessage.error(errText(e, '加载数采配置失败'))
  } finally {
    loading.value = false
  }
}

// ---------- 设备 ----------
const deviceVisible = ref(false)
const savingDevice = ref(false)
const deviceForm = ref(emptyDevice())

function emptyDevice() {
  return {
    id: null, code: '', name: '', type: '电子秤', model: '', serialNo: '',
    gatewayId: '', ip: '', port: '', workshop: '', projectCode: '',
    heartbeatSeconds: '60', scadaDrawn: false, enabled: true, remark: '',
  }
}

function openDevice(row) {
  if (row) {
    deviceForm.value = {
      id: row.id ?? null,
      code: row.code || '',
      name: row.name || '',
      type: row.type || '',
      model: row.model || '',
      serialNo: row.serialNo || '',
      gatewayId: row.gatewayId ?? '',
      ip: row.ip || '',
      port: row.port === null || row.port === undefined ? '' : String(row.port),
      workshop: row.workshop || '',
      projectCode: row.projectCode || '',
      heartbeatSeconds: row.heartbeatSeconds === null || row.heartbeatSeconds === undefined
        ? '' : String(row.heartbeatSeconds),
      scadaDrawn: !!row.scadaDrawn,
      enabled: row.enabled !== false,
      remark: row.remark || '',
    }
  } else {
    deviceForm.value = emptyDevice()
  }
  deviceVisible.value = true
}

async function submitDevice() {
  const f = deviceForm.value
  if (!f.code.trim()) return ElMessage.warning('请填写设备编码')
  if (!f.name.trim()) return ElMessage.warning('请填写设备名称')
  const body = {
    code: f.code.trim(),
    name: f.name.trim(),
    deviceType: f.type || '',
    model: f.model || '',
    serialNo: f.serialNo || '',
    gatewayId: f.gatewayId === '' ? null : f.gatewayId,
    ip: f.ip || '',
    port: numOrNull(f.port),
    workshop: f.workshop || '',
    projectCode: f.projectCode || '',
    scadaDrawn: !!f.scadaDrawn,
    heartbeatSeconds: numOrNull(f.heartbeatSeconds),
    enabled: f.enabled !== false,
    remark: f.remark || '',
  }
  if (f.id) body.id = f.id
  savingDevice.value = true
  try {
    await saveDcDevice(body)
    deviceVisible.value = false
    ElMessage.success('保存成功')
    await load()
  } catch (e) {
    ElMessage.error(errText(e, '保存设备失败'))
  } finally {
    savingDevice.value = false
  }
}

async function removeDevice(row) {
  try {
    await ElMessageBox.confirm('删除后该设备的采集点与报警记录不再展示，确认删除？', '删除设备',
      { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteDcDevice(row.id)
    ElMessage.success('删除成功')
    await load()
  } catch (e) {
    ElMessage.error(errText(e, '删除设备失败'))
  }
}

async function markDebugged(row) {
  debuggingId.value = row.id
  try {
    await debugDcDevice(row.id)
    ElMessage.success('已标记数据调试完成')
    await load()
  } catch (e) {
    ElMessage.error(errText(e, '标记调试失败'))
  } finally {
    debuggingId.value = null
  }
}

// ---------- 网关 ----------
const gatewayVisible = ref(false)
const savingGateway = ref(false)
const gatewayForm = ref(emptyGateway())

function emptyGateway() {
  return {
    id: null, code: '', name: '', protocol: 'MODBUS-TCP', host: '', port: '',
    apiKey: '', enabled: true, keySet: false, remark: '',
  }
}

function openGateway(row) {
  gatewayForm.value = row
    ? {
        id: row.id ?? null,
        code: row.code || '',
        name: row.name || '',
        protocol: row.protocol || '',
        host: row.host || '',
        port: row.port === null || row.port === undefined ? '' : String(row.port),
        apiKey: '',
        enabled: row.enabled !== false,
        keySet: !!row.keySet,
        remark: row.remark || '',
      }
    : emptyGateway()
  gatewayVisible.value = true
}

async function submitGateway() {
  const f = gatewayForm.value
  if (!f.code.trim()) return ElMessage.warning('请填写网关编码')
  if (!f.name.trim()) return ElMessage.warning('请填写网关名称')
  const body = {
    code: f.code.trim(),
    name: f.name.trim(),
    protocol: f.protocol || '',
    host: f.host || '',
    port: numOrNull(f.port),
    enabled: f.enabled !== false,
    remark: f.remark || '',
  }
  if (f.apiKey && f.apiKey.trim()) body.apiKey = f.apiKey.trim()
  if (f.id) body.id = f.id
  savingGateway.value = true
  try {
    await saveDcGateway(body)
    gatewayVisible.value = false
    ElMessage.success('保存成功')
    await load()
  } catch (e) {
    ElMessage.error(errText(e, '保存网关失败'))
  } finally {
    savingGateway.value = false
  }
}

async function toggleGateway(row, value) {
  try {
    await saveDcGateway({
      id: row.id,
      code: row.code,
      name: row.name,
      protocol: row.protocol || '',
      host: row.host || '',
      port: row.port ?? null,
      enabled: !!value,
      remark: row.remark || '',
    })
    ElMessage.success(value ? '网关已启用' : '网关已停用')
  } catch (e) {
    ElMessage.error(errText(e, '更新网关状态失败'))
  }
  await load()
}

async function removeGateway(row) {
  try {
    await ElMessageBox.confirm('删除后绑定该网关的设备将变为未绑定，确认删除？', '删除网关',
      { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteDcGateway(row.id)
    ElMessage.success('删除成功')
    await load()
  } catch (e) {
    ElMessage.error(errText(e, '删除网关失败'))
  }
}

// ---------- 采集点 ----------
const pointVisible = ref(false)
const pointDevice = ref(null)
const points = ref([])
const pointLoading = ref(false)
const savingPoint = ref(false)
const pointForm = ref(emptyPoint())

function emptyPoint() {
  return {
    id: null, code: '', name: '', dataType: '数值', unit: '', addr: '',
    freqSeconds: '5', lowLimit: '', highLimit: '', enabled: true, remark: '',
  }
}

async function openPoints(row) {
  pointDevice.value = row
  pointForm.value = emptyPoint()
  points.value = []
  pointVisible.value = true
  await loadPoints()
}

async function loadPoints() {
  const id = pointDevice.value?.id
  if (!id) return
  pointLoading.value = true
  try {
    const list = await getDcPoints(id)
    points.value = Array.isArray(list) ? list : []
  } catch (e) {
    ElMessage.error(errText(e, '加载采集点失败'))
  } finally {
    pointLoading.value = false
  }
}

function editPoint(row) {
  pointForm.value = {
    id: row.id ?? null,
    code: row.code || '',
    name: row.name || '',
    dataType: row.dataType || '数值',
    unit: row.unit || '',
    addr: row.addr || '',
    freqSeconds: row.freqSeconds ?? '5',
    lowLimit: row.lowLimit === null || row.lowLimit === undefined ? '' : String(row.lowLimit),
    highLimit: row.highLimit === null || row.highLimit === undefined ? '' : String(row.highLimit),
    enabled: row.enabled !== false,
    remark: row.remark || '',
  }
}

async function submitPoint() {
  const d = pointDevice.value
  if (!d?.id) return
  const f = pointForm.value
  if (!String(f.code || '').trim()) return ElMessage.warning('请填写采集点编码')
  if (!String(f.name || '').trim()) return ElMessage.warning('请填写采集点名称')
  const body = {
    deviceId: d.id,
    code: String(f.code).trim(),
    name: String(f.name).trim(),
    dataType: f.dataType,
    unit: f.unit || '',
    addr: f.addr || '',
    freqSeconds: numOrNull(f.freqSeconds) ?? 5,
    lowLimit: numOrNull(f.lowLimit),
    highLimit: numOrNull(f.highLimit),
    enabled: f.enabled !== false,
    remark: f.remark || '',
  }
  if (f.id) body.id = f.id
  savingPoint.value = true
  try {
    await saveDcPoint(body)
    ElMessage.success('保存成功')
    pointForm.value = emptyPoint()
    await loadPoints()
    await load()
  } catch (e) {
    ElMessage.error(errText(e, '保存采集点失败'))
  } finally {
    savingPoint.value = false
  }
}

async function removePoint(row) {
  try {
    await ElMessageBox.confirm('删除后该采集点的历史采样仍保留，确认删除？', '删除采集点',
      { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteDcPoint(row.id)
    ElMessage.success('删除成功')
    if (pointForm.value.id === row.id) pointForm.value = emptyPoint()
    await loadPoints()
    await load()
  } catch (e) {
    ElMessage.error(errText(e, '删除采集点失败'))
  }
}

// ---------- 数采规则 ----------
const ruleVisible = ref(false)
const ruleDevice = ref(null)
const rules = ref([])
const ruleLoading = ref(false)
const savingRule = ref(false)
const ruleForm = ref(emptyRule())

function emptyRule() {
  return { id: null, name: '', ruleType: '定时采集', expression: '', params: '', enabled: true, remark: '' }
}

async function openRules(row) {
  ruleDevice.value = row
  ruleForm.value = emptyRule()
  rules.value = []
  ruleVisible.value = true
  await loadRules()
}

async function loadRules() {
  const id = ruleDevice.value?.id
  if (!id) return
  ruleLoading.value = true
  try {
    const list = await getDcRules(id)
    rules.value = Array.isArray(list) ? list : []
  } catch (e) {
    ElMessage.error(errText(e, '加载数采规则失败'))
  } finally {
    ruleLoading.value = false
  }
}

function editRule(row) {
  ruleForm.value = {
    id: row.id ?? null,
    name: row.name || '',
    ruleType: row.ruleType || '定时采集',
    expression: row.expression || '',
    params: row.params || '',
    enabled: row.enabled !== false,
    remark: row.remark || '',
  }
}

async function submitRule() {
  const d = ruleDevice.value
  if (!d?.id) return
  const f = ruleForm.value
  if (!String(f.name || '').trim()) return ElMessage.warning('请填写规则名称')
  const body = {
    deviceId: d.id,
    name: String(f.name).trim(),
    ruleType: f.ruleType,
    expression: f.expression || '',
    params: f.params || '',
    enabled: f.enabled !== false,
    remark: f.remark || '',
  }
  if (f.id) body.id = f.id
  savingRule.value = true
  try {
    await saveDcRule(body)
    ElMessage.success('保存成功')
    ruleForm.value = emptyRule()
    await loadRules()
    await load()
  } catch (e) {
    ElMessage.error(errText(e, '保存数采规则失败'))
  } finally {
    savingRule.value = false
  }
}

async function removeRule(row) {
  try {
    await ElMessageBox.confirm('删除规则后该设备可能不再满足「配置数采规则」，确认删除？', '删除规则',
      { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteDcRule(row.id)
    ElMessage.success('删除成功')
    if (ruleForm.value.id === row.id) ruleForm.value = emptyRule()
    await loadRules()
    await load()
  } catch (e) {
    ElMessage.error(errText(e, '删除数采规则失败'))
  }
}

onMounted(() => load())
</script>

<style scoped>
.dc {
  padding: 14px 18px 18px;
  /* 内容超过视口时撑开页面滚动，而不是把下半截裁掉 */
  min-height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: var(--d-bg, #f6f7f8);
}

.dc-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.dc-title h2 {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  color: var(--d-text, #1f2d3d);
}

.dc-sub {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3, #8b9893);
}

.dot {
  margin: 0 6px;
  color: var(--d-border-strong, #dce4e1);
}

.dc-actions {
  display: flex;
  gap: 8px;
  flex: 0 0 auto;
}

.dc-metrics {
  display: flex;
  gap: 10px;
}

.dc-metric {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid var(--d-border, #edf1ef);
  border-radius: 6px;
  background: #fff;
}

.dc-metric-label {
  display: block;
  font-size: 12px;
  line-height: 18px;
  color: var(--d-text-3, #8b9893);
}

.dc-metric-value {
  display: block;
  font-size: 18px;
  line-height: 26px;
  font-variant-numeric: tabular-nums;
  color: var(--d-text, #1f2d3d);
}

.dc-metric-value.ok { color: var(--d-ok, #52c41a); }

.dc-filter {
  display: flex;
  align-items: center;
  gap: 12px;
}

.dc-search { width: 260px; }

.dc-hint {
  font-size: 12px;
  color: var(--d-text-3, #8b9893);
}

.dc-card {
  border: 1px solid var(--d-border, #edf1ef);
  border-radius: 6px;
  background: #fff;
  padding: 8px 12px 12px;
}

.dc-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 0 8px;
  font-size: 13px;
  color: var(--d-text, #1f2d3d);
}

.dc-table { background: #fff; }

.dc-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: 6px;
  border-radius: 50%;
  background: var(--d-border-strong, #dce4e1);
  vertical-align: middle;
}

.dc-dot.on { background: var(--d-ok, #52c41a); }
.dc-dot.off { background: #bfbfbf; }

.dc-code {
  font-variant-numeric: tabular-nums;
  color: var(--d-text, #1f2d3d);
}

.dc-muted { color: var(--d-text-3, #8b9893); }

.num { font-variant-numeric: tabular-nums; }
.num.warn { color: var(--d-warn, #faad14); }

.dc-checks {
  display: inline-flex;
  gap: 4px;
}

.dc-check {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 4px;
  font-size: 12px;
}

.dc-check.ok { color: #389e0d; background: #f6ffed; border: 1px solid #b7eb8f; }
.dc-check.no { color: #bfbfbf; background: #fafafa; border: 1px solid #e8e8e8; }

.dc-tag {
  display: inline-block;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 20px;
  border: 1px solid var(--d-border-strong, #dce4e1);
  color: var(--d-text-2, #5b6b66);
}

.dc-tag.ok { border-color: #b7eb8f; background: #f6ffed; color: #389e0d; }
.dc-tag.warn { border-color: #ffe58f; background: #fffbe6; color: #d48806; }
.dc-tag.muted { color: var(--d-text-3, #8b9893); }

.dc-empty-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 28px 0;
  font-size: 13px;
  color: var(--d-text-2, #5b6b66);
}

.dc-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 12px;
}

.dc-span2 { grid-column: 1 / -1; }

.dc-dialog-tip {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3, #8b9893);
}

.dc-help p {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 22px;
  color: var(--d-text-2, #5b6b66);
}

.dc-help h4 {
  margin: 12px 0 6px;
  font-size: 13px;
  color: var(--d-text, #1f2d3d);
}

.dc-code {
  margin: 0;
  padding: 8px 10px;
  border: 1px solid var(--d-border, #edf1ef);
  border-radius: 6px;
  background: #fafbfb;
  font-size: 12px;
  line-height: 20px;
  white-space: pre-wrap;
  word-break: break-all;
}

@media (max-width: 900px) {
  .dc-empty-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 28px 0;
  font-size: 13px;
  color: var(--d-text-2, #5b6b66);
}

.dc-form-grid { grid-template-columns: 1fr; }
  .dc-metrics { flex-wrap: wrap; }
}
</style>
