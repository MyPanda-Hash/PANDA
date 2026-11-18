// 模型与 API 配置（#/models 之外的浮层，也可以从「设置」进）。
//
// 三个真实接口：
//   GET  /admin/model-endpoints        列端点（管理员）
//   POST /admin/model-endpoints        新建端点
//   POST /admin/model-endpoints/{id}:test  测连通
//
// 为什么不做「密钥库」：本部署关掉了 LinkMind，而 /admin/provider-keys/register
// 必须经 LinkMind 网关转发，直接建会 503。真实可用的路径是端点的 secret_ref 字段
// ——它接受明文 Key 或 env://变量名，由后端自己解析（见 api/model_endpoints.py）。

import { MODELS, ROLE_AUDIT, SEARCH_CFG, VOICE_CONFIG } from './api.js';
import { el, icon, clear, formatMoment, toast } from './util.js';

let host = null;
let parts = null;
let state = null;
let mounted = false;
let testing = new Set();

const CAPABILITIES = [
  { key: 'chat', label: '对话' },
  { key: 'vision', label: '图片识别' },
  { key: 'embedding', label: '向量' },
];

export async function mount(root, ctx) {
  host = root;
  mounted = true;
  state = {
    endpoints: [],
    loading: true,
    error: '',
    creating: false,
    voice: null,
    voiceDraft: {},
    voiceError: '',
  };
  build();
  await Promise.all([load(), loadVoice()]);
}

export async function unmount() {
  mounted = false;
  closeForm();
  if (host) clear(host);
  host = null;
  parts = null;
  state = null;
  testing.clear();
}

async function load() {
  if (!state) return;
  state.loading = true;
  state.error = '';
  render();
  try {
    const rows = await MODELS.list();
    if (!mounted) return;
    state.endpoints = Array.isArray(rows) ? rows.filter((r) => r && r.id) : [];
  } catch (err) {
    if (!mounted) return;
    state.endpoints = [];
    state.error = describe(err);
  }
  if (!mounted) return;
  state.loading = false;
  render();
}

function describe(err) {
  if (!err) return '请求失败';
  if (err.status === 401) return '登录已过期，请重新登录';
  if (err.status === 403) return '只有管理员可以配置模型';
  if (err.status === 0) return '连不上 AI 后端';
  return err.message || '请求失败';
}

/** 联网检索的配置渲染：服务地址、密钥、开关，加一个自检。 */
/** 角色权限自检：管理员核对「每个岗位实际收到什么」。 */
async function renderRoleAudit() {
  if (!parts || !parts.auditBody) return;
  const body = clear(parts.auditBody);
  body.append(el('div', { cls: 'role-audit__loading secondary', text: '读取中…' }));

  let rows = [];
  try {
    const payload = await ROLE_AUDIT.list();
    rows = Array.isArray(payload) ? payload : (payload && payload.data) || [];
  } catch (err) {
    clear(body);
    body.append(el('div', { cls: 'role-audit__err', text: describe(err) }));
    return;
  }

  clear(body);
  if (!rows.length) {
    body.append(el('div', { cls: 'role-audit__empty secondary', text: '还没有派发记录' }));
    return;
  }

  const table = el('table', { cls: 'role-audit__table' });
  table.append(
    el('thead', {}, [
      el('tr', {}, [
        el('th', { text: '角色' }),
        el('th', { cls: 'num', text: 'AI 发现' }),
        el('th', { cls: 'num', text: '待办' }),
        el('th', { text: '落在哪些域' }),
      ]),
    ]),
  );
  const tbody = el('tbody');
  for (const row of rows) {
    // 0 条是最值得看的一行：某个岗位什么都收不到，通常意味着派发断链
    const silent = !row.events && !row.todos;
    tbody.append(
      el('tr', { cls: silent ? 'is-silent' : '' }, [
        el('td', {}, [
          el('span', { cls: 'role-audit__role', text: row.role || '—' }),
          silent ? el('span', { cls: 'role-audit__warn', text: '收不到任何东西' }) : null,
        ]),
        el('td', { cls: 'num', text: String(row.events ?? 0) }),
        el('td', { cls: 'num', text: String(row.todos ?? 0) }),
        el('td', { cls: 'role-audit__domains', text: row.domainText || '—' }),
      ]),
    );
  }
  table.append(tbody);
  body.append(
    table,
    el('p', {
      cls: 'role-audit__note secondary',
      text: '按岗位汇总所有未关闭的发现与未完成的待办。某一行全是 0 说明这个岗位收不到任何提醒，检查派发规则。',
    }),
  );
}

async function renderSearch() {
  if (!parts || !parts.searchBody) return;
  const body = clear(parts.searchBody);
  body.append(el('div', { cls: 'search-cfg__loading secondary', text: '读取中…' }));

  let view = null;
  try {
    const payload = await SEARCH_CFG.get();
    view = (payload && payload.data) || payload || {};
  } catch (err) {
    clear(body);
    body.append(el('div', { cls: 'search-cfg__err', text: describe(err) }));
    return;
  }

  clear(body);

  const enabled = el('input', { attrs: { type: 'checkbox' } });
  enabled.checked = Boolean(view.search_enabled);
  const urlInput = el('input', {
    cls: 'input',
    attrs: { type: 'text', value: String(view.anysearch_api_url || ''), placeholder: 'https://api.anysearch.com/mcp' },
  });
  const keyInput = el('input', {
    cls: 'input',
    attrs: {
      type: 'password',
      placeholder: view.anysearch_api_key && view.anysearch_api_key.configured
        ? '已配置 ' + String(view.anysearch_api_key.hint || '') + '（留空表示不改）'
        : '粘贴 AnySearch 的 API Key',
      autocomplete: 'new-password',
    },
  });
  const modelInput = el('input', {
    cls: 'input',
    attrs: { type: 'text', value: String(view.search_api_model || ''), placeholder: 'qwen-plus（备用通道）' },
  });

  const saveBtn = el('button', { cls: 'btn btn--primary btn--sm', attrs: { type: 'button' }, text: '保存' });
  saveBtn.addEventListener('click', async () => {
    saveBtn.disabled = true;
    saveBtn.textContent = '保存中…';
    try {
      await SEARCH_CFG.save({
        search_enabled: enabled.checked,
        anysearch_api_url: urlInput.value.trim(),
        anysearch_api_key: keyInput.value.trim(),
        search_api_model: modelInput.value.trim(),
      });
      toast('检索配置已保存，立即生效');
      await renderSearch();
    } catch (err) {
      toast(describe(err), 'error');
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = '保存';
    }
  });

  const row = (label, control, hint) => el('label', { cls: 'search-cfg__row' }, [
    el('span', { cls: 'search-cfg__label', text: label }),
    control,
    hint ? el('span', { cls: 'search-cfg__hint', text: hint }) : null,
  ]);

  body.append(
    row('启用联网检索', enabled, '关闭后模型不会调用 web_search，只回本系统的数据'),
    row('服务地址', urlInput, 'AnySearch 是 MCP 协议，默认 https://api.anysearch.com/mcp'),
    row('API Key', keyInput, view.anysearch_api_key && view.anysearch_api_key.configured ? '已配置。留空表示不修改' : '去 anysearch.com/console/api-keys 申请'),
    row('备用模型', modelInput, 'AnySearch 不可用时退回的通义千问模型'),
    el('div', { cls: 'search-cfg__acts' }, [saveBtn]),
  );
}

async function testSearch() {
  toast('正在试检索…');
  try {
    const payload = await SEARCH_CFG.test();
    const data = (payload && payload.data) || payload || {};
    toast('检索可用：' + String(data.sample || '').replace(/\s+/g, ' ').slice(0, 60));
  } catch (err) {
    toast(describe(err), 'error');
  }
}
function build() {
  clear(host);
  const page = el('div', { cls: 'models-page' });
  const listBody = el('div', { cls: 'card__body card__body--flush models-list' });

  const card = el('section', { cls: 'card' }, [
    el('div', { cls: 'card__head' }, [
      el('h2', { cls: 'card__title', text: '模型端点' }),
      el('span', { cls: 'tag', attrs: { 'data-tone': 'muted' }, text: '管理员' }),
      el('div', { cls: 'card__head-actions' }, [
        el('button', {
          cls: 'btn btn--ghost btn--sm',
          attrs: { type: 'button' },
          text: '刷新',
          on: { click: () => load() },
        }),
        el('button', {
          cls: 'btn btn--primary btn--sm',
          attrs: { type: 'button' },
          text: '新增端点',
          on: { click: () => openForm() },
        }),
      ]),
    ]),
    listBody,
  ]);

  // 语音助手是独立一套（MOSS-Live 实时通话 + DashScope ASR），
  // 和上面的 chat 端点不共用配置，所以单独一张卡，别让人误以为改端点就能换语音模型。
  const voiceBody = el('div', { cls: 'card__body voice-cfg' });
  const voiceCard = el('section', { cls: 'card' }, [
    el('div', { cls: 'card__head' }, [
      el('h2', { cls: 'card__title', text: '语音助手' }),
      el('span', { cls: 'tag', attrs: { 'data-tone': 'muted' }, text: 'MOSS-Live + DashScope' }),
      el('div', { cls: 'card__head-actions' }, [
        el('button', {
          cls: 'btn btn--ghost btn--sm',
          attrs: { type: 'button' },
          text: '连通自检',
          on: { click: () => testVoice() },
        }),
      ]),
    ]),
    voiceBody,
  ]);

  // 联网检索：和模型端点分开一张卡——它换的是「去哪搜」，不是「用哪个模型」。
  const searchBody = el('div', { cls: 'card__body search-cfg' });
  const searchCard = el('section', { cls: 'card' }, [
    el('div', { cls: 'card__head' }, [
      el('h2', { cls: 'card__title', text: '联网检索' }),
      el('span', { cls: 'tag', attrs: { 'data-tone': 'muted' }, text: 'AnySearch · MCP' }),
      el('div', { cls: 'card__head-actions' }, [
        el('button', {
          cls: 'btn btn--ghost btn--sm',
          attrs: { type: 'button' },
          text: '连通自检',
          on: { click: () => testSearch() },
        }),
      ]),
    ]),
    searchBody,
  ]);
  // 角色权限自检：配置完「谁该收到什么」之后，用它核对每个岗位实际收到多少。
  const auditBody = el('div', { cls: 'card__body role-audit' });
  const auditCard = el('section', { cls: 'card' }, [
    el('div', { cls: 'card__head' }, [
      el('h2', { cls: 'card__title', text: '角色权限自检' }),
      el('span', { cls: 'tag', attrs: { 'data-tone': 'muted' }, text: '管理员' }),
      el('div', { cls: 'card__head-actions' }, [
        el('button', {
          cls: 'btn btn--ghost btn--sm',
          attrs: { type: 'button' },
          text: '刷新',
          on: { click: () => renderRoleAudit() },
        }),
      ]),
    ]),
    auditBody,
  ]);
  page.append(
    el('p', { cls: 'models-intro secondary' }, [
      '这里配置 AI 数字员工实际调用的模型。端点决定「用哪家、哪个模型、连哪个地址」，密钥填在这一行的 Key 里；模型不可用时对话会退回规则版。',
    ]),
    card,
    auditCard,
    searchCard,
    voiceCard
  );
  host.append(page);
  parts = { listBody, voiceBody, searchBody, auditBody };
  renderVoice();
  void renderSearch();
  void renderRoleAudit();
}

function render() {
  if (!parts) return;
  const body = clear(parts.listBody);

  if (state.loading) {
    for (let i = 0; i < 2; i += 1) {
      body.append(el('div', { cls: 'models-row' }, [el('div', { cls: 'skeleton models-skel' })]));
    }
    return;
  }
  if (state.error) {
    body.append(
      el('div', { cls: 'empty models-empty' }, [
        el('div', { cls: 'empty__icon', attrs: { style: '--empty-icon: url(/icons/alert.svg)' } }),
        el('h3', { text: '配置读不出来' }),
        el('p', { text: state.error }),
        el('button', { cls: 'btn btn--primary', attrs: { type: 'button' }, text: '重试', on: { click: () => load() } }),
      ])
    );
    return;
  }
  if (!state.endpoints.length) {
    body.append(
      el('div', { cls: 'empty models-empty' }, [
        el('div', { cls: 'empty__icon', attrs: { style: '--empty-icon: url(/icons/thunderbolt.svg)' } }),
        el('h3', { text: '还没有模型端点' }),
        el('p', { text: '没有端点时 AI 只能用规则版结论。先加一个，例如 DeepSeek 的 chat 端点。' }),
        el('button', { cls: 'btn btn--primary', attrs: { type: 'button' }, text: '新增端点', on: { click: () => openForm() } }),
      ])
    );
    return;
  }
  for (const endpoint of state.endpoints) body.append(rowEl(endpoint));
}

function rowEl(ep) {
  const statusOk = ep.status === 'active';
  const tested = ep.last_test_status;
  const testTone = tested === 'reachable' ? 'accent' : tested ? 'danger' : 'muted';

  const meta = [];
  if (ep.endpoint_url) meta.push(ep.endpoint_url);
  if (ep.extra_models) meta.push(`附：${ep.extra_models}`);

  const testLabel = testing.has(ep.id) ? '测试中…' : '测试';
  return el('div', { cls: 'models-row' }, [
    el('div', { cls: 'models-row__main' }, [
      el('div', { cls: 'models-row__head' }, [
        el('span', { cls: 'models-row__model', text: String(ep.model || '') }),
        el('span', { cls: 'tag', attrs: { 'data-tone': 'muted' }, text: String(ep.provider || '') }),
        el('span', { cls: 'tag', attrs: { 'data-tone': statusOk ? 'accent' : 'danger' }, text: statusOk ? '启用' : String(ep.status || '') }),
        ep.capability ? el('span', { cls: 'tag', attrs: { 'data-tone': 'muted' }, text: capabilityLabel(ep.capability) }) : null,
        ep.secret_configured
          ? el('span', { cls: 'tag', attrs: { 'data-tone': 'muted' }, text: '密钥已配置' })
          : el('span', { cls: 'tag', attrs: { 'data-tone': 'warn' }, text: '缺密钥' }),
      ]),
      meta.length ? el('p', { cls: 'models-row__meta mono', text: meta.join(' · ') }) : null,
      el('p', { cls: 'models-row__test' }, [
        el('span', { cls: 'tag', attrs: { 'data-tone': testTone }, text: tested === 'reachable' ? '连通' : tested || '未测试' }),
        ep.last_tested_at ? el('span', { cls: 'secondary', text: ` · ${formatMoment(ep.last_tested_at) || ''}` }) : null,
        ep.last_test_error ? el('span', { cls: 'models-row__err', text: ` · ${String(ep.last_test_error).slice(0, 80)}` }) : null,
      ]),
      editingSecretId === ep.id ? secretEditor(ep) : null,
    ]),
    el('div', { cls: 'models-row__actions' }, [
      // 改密钥：密钥删了/换了必须能在这里直接填，否则端点只能作废重建
      el('button', {
        cls: 'btn btn--sm',
        attrs: { type: 'button' },
        text: ep.secret_configured ? '改密钥' : '填密钥',
        on: { click: () => openSecret(ep) },
      }),
      el('button', {
        cls: 'btn btn--sm',
        attrs: { type: 'button', disabled: testing.has(ep.id) ? true : null },
        text: testLabel,
        on: { click: () => runTest(ep) },
      }),
    ]),
  ]);
}

function capabilityLabel(key) {
  const found = CAPABILITIES.find((c) => c.key === key);
  return found ? found.label : String(key || '');
}

async function runTest(ep) {
  if (testing.has(ep.id)) return;
  testing.add(ep.id);
  render();
  try {
    const result = await MODELS.test(ep.id);
    if (!mounted) return;
    if (result && result.reachable) toast(`连通，${result.latency_ms ?? '?'} ms`);
    else toast((result && (result.error || result.test_status)) || '测试未通过', 'error');
    await load();
  } catch (err) {
    if (!mounted) return;
    toast(describe(err), 'error');
  } finally {
    testing.delete(ep.id);
    if (mounted) render();
  }
}

// ---------------------------------------------------------------- 填 / 改 API Key

// 行内展开编辑，不弹遮罩层：
// 工作台没有通用 modal 样式，行内编辑既省一套组件，也让「改的是哪个端点」一目了然。
let editingSecretId = null

function openSecret(ep) {
  editingSecretId = ep.id === editingSecretId ? null : ep.id
  render()
}

function closeSecret() {
  editingSecretId = null
  render()
}

/** 行内编辑器：输入 → 保存 → 自动测一次（密钥对不对由端点说了算）。 */
function secretEditor(ep) {
  const input = el('input', {
    cls: 'input',
    attrs: { type: 'password', placeholder: 'sk-… 或 env://变量名', autocomplete: 'off' },
  })
  const hint = el('p', { cls: 'settings-hint', text: '只写进本机该端点的配置，页面不回显明文。' })
  const saveBtn = el('button', { cls: 'btn btn--sm btn--primary', attrs: { type: 'button' }, text: '保存并测试' })

  const submit = async () => {
    const value = (input.value || '').trim()
    if (!value) {
      hint.textContent = '请填写 API Key'
      hint.dataset.tone = 'warn'
      return
    }
    saveBtn.disabled = true
    saveBtn.textContent = '保存中…'
    try {
      await MODELS.update(ep.id, { secret_ref: value })
      editingSecretId = null
      toast('密钥已更新')
      await load()
      const fresh = (state.endpoints || []).find((x) => x.id === ep.id)
      if (fresh) await runTest(fresh)
    } catch (err) {
      hint.textContent = describe(err)
      hint.dataset.tone = 'danger'
      saveBtn.disabled = false
      saveBtn.textContent = '保存并测试'
    }
  }

  saveBtn.addEventListener('click', submit)
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') submit()
  })
  // 打开就聚焦，省一次点击
  setTimeout(() => input.focus(), 0)

  return el('div', { cls: 'models-secret' }, [
    el('label', { cls: 'settings-field' }, [
      el('span', { cls: 'settings-field__label', text: 'API Key' }),
      input,
    ]),
    el('div', { cls: 'models-secret__actions' }, [
      hint,
      el('div', { cls: 'models-secret__btns' }, [
        el('button', { cls: 'btn btn--sm', attrs: { type: 'button' }, text: '取消', on: { click: closeSecret } }),
        saveBtn,
      ]),
    ]),
  ])
}

// ---------------------------------------------------------------- 新增端点表单

const FIELD_DEFS = [
  { key: 'provider', label: '供应商', placeholder: 'deepseek / alibaba / moonshot…', required: true },
  { key: 'model', label: '模型名', placeholder: 'deepseek-chat', required: true },
  { key: 'endpoint_url', label: '接口地址', placeholder: 'https://api.deepseek.com/v1', required: true },
  { key: 'secret_ref', label: 'API Key', placeholder: 'sk-… 或 env://变量名', secret: true },
  { key: 'extra_models', label: '同端点其它模型', placeholder: '可选，逗号分隔', optional: true },
];

let formEl = null;

function openForm() {
  if (formEl) return;
  const inputs = {};
  const hint = el('p', { cls: 'settings-hint' });

  const rows = FIELD_DEFS.map((field) => {
    const input = el('input', {
      cls: 'input',
      attrs: {
        type: field.secret ? 'password' : 'text',
        placeholder: field.placeholder || '',
        autocomplete: 'off',
      },
    });
    inputs[field.key] = input;
    return el('label', { cls: 'settings-field' }, [
      el('span', { cls: 'settings-field__label' }, [
        field.label,
        field.required ? el('span', { cls: 'models-req', text: ' *' }) : null,
      ]),
      input,
    ]);
  });

  const capSelect = el('select', { cls: 'select' });
  for (const cap of CAPABILITIES) {
    const opt = el('option', { text: cap.label });
    opt.value = cap.key;
    capSelect.append(opt);
  }

  const vision = el('input', { attrs: { type: 'checkbox' } });
  const structured = el('input', { attrs: { type: 'checkbox' } });

  const form = el('form', { cls: 'settings-form' }, [
    ...rows,
    el('label', { cls: 'settings-field' }, [
      el('span', { cls: 'settings-field__label', text: '用途' }),
      capSelect,
    ]),
    el('div', { cls: 'models-checks' }, [
      el('label', { cls: 'models-check' }, [vision, el('span', { text: '支持图片' })]),
      el('label', { cls: 'models-check' }, [structured, el('span', { text: '支持结构化输出' })]),
    ]),
    hint,
    el('div', { cls: 'row' }, [
      el('button', { cls: 'btn', attrs: { type: 'button' }, text: '取消', on: { click: () => closeForm() } }),
      el('button', { cls: 'btn btn--primary', attrs: { type: 'submit' }, text: '保存' }),
    ]),
  ]);

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const payload = {
      provider: inputs.provider.value.trim(),
      model: inputs.model.value.trim(),
      endpoint_url: inputs.endpoint_url.value.trim(),
      capability: capSelect.value,
      secret_ref: inputs.secret_ref.value.trim() || null,
      extra_models: inputs.extra_models.value.trim(),
      supports_vision: vision.checked,
      supports_structured_output: structured.checked,
    };
    const missing = ['provider', 'model', 'endpoint_url'].filter((k) => !payload[k]);
    if (missing.length) {
      hint.textContent = `请填写：${missing.map((k) => FIELD_DEFS.find((f) => f.key === k).label).join('、')}`;
      return;
    }
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    submit.textContent = '保存中…';
    try {
      await MODELS.create(payload);
      toast('端点已添加');
      closeForm();
      await load();
    } catch (err) {
      hint.textContent = describe(err);
      submit.disabled = false;
      submit.textContent = '保存';
    }
  });

  const panel = el('div', { cls: 'models-panel', attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-label': '新增模型端点' } }, [
    el('div', { cls: 'models-panel__mask', on: { click: () => closeForm() } }),
    el('div', { cls: 'models-panel__body' }, [
      el('header', { cls: 'models-panel__head' }, [
        el('h2', { cls: 'models-panel__title', text: '新增模型端点' }),
        el('button', { cls: 'icon-btn', attrs: { type: 'button', 'aria-label': '关闭' }, text: '×', on: { click: () => closeForm() } }),
      ]),
      form,
    ]),
  ]);
  formEl = panel;
  (document.getElementById('overlayRoot') || document.body).append(panel);
  inputs.provider.focus();
}

function closeForm() {
  if (formEl && formEl.parentNode) formEl.parentNode.removeChild(formEl);
  formEl = null;
}
// ---------------------------------------------------------------- 语音助手配置

const VOICE_FIELDS = [
  { key: 'moss_realtime_model', label: '实时通话模型', hint: 'MOSS-Live 的模型名' },
  { key: 'moss_realtime_url', label: '实时通话地址', hint: '必须以 wss:// 开头' },
  { key: 'moss_voice_id', label: '音色 ID', hint: 'MOSS 控制台里的 voice id' },
  { key: 'moss_voice_persona', label: '语音助手人设名', hint: '通话里自称的名字' },
  { key: 'moss_voice_max_seconds', label: '单次通话上限（秒）', hint: '到点自动结束', type: 'number' },
  { key: 'moss_voice_write_limit', label: '单次通话写操作上限', hint: '防止语音误改太多单据', type: 'number' },
  { key: 'dashscope_asr_model', label: '语音转文字模型', hint: 'DashScope ASR' },
];

async function loadVoice() {
  try {
    state.voice = await VOICE_CONFIG.get();
  } catch (err) {
    state.voice = null;
    state.voiceError = describe(err);
  }
  if (mounted) renderVoice();
}

function renderVoice() {
  if (!parts || !parts.voiceBody) return;
  const body = clear(parts.voiceBody);

  if (!state.voice) {
    body.append(emptyBox('alert', '语音配置读不出来', state.voiceError || '', '重试', () => loadVoice()));
    return;
  }

  const v = state.voice;
  state.voiceDraft = state.voiceDraft || {};

  const grid = el('div', { cls: 'voice-cfg__grid' });
  // 语音转文字依赖 DashScope：没配 Key 时这一栏置灰，
  // 免得住手点进去改模型名，改完发现根本用不了。
  const dashReady = Boolean(v.dashscope_api_key && v.dashscope_api_key.configured);
  for (const field of VOICE_FIELDS) {
    const gated = field.key === 'dashscope_asr_model' && !dashReady;
    const current = state.voiceDraft[field.key] !== undefined ? state.voiceDraft[field.key] : v[field.key];
    const input = el('input', {
      cls: 'input',
      attrs: {
        type: field.type === 'number' ? 'number' : 'text',
        value: String(current ?? ''),
        // 置灰同时禁用输入：只灰不禁，用户还是会按进去改
        disabled: gated ? true : null,
        title: gated ? '需先配置 DashScope Key' : null,
      },
      on: gated ? {} : { input: (event) => { state.voiceDraft[field.key] = event.target.value; } },
    });
    grid.append(
      el('label', { cls: 'settings-field' + (gated ? ' is-disabled' : '') }, [
        el('span', { cls: 'settings-field__label' }, [
          field.label,
          gated ? el('span', { cls: 'tag', attrs: { 'data-tone': 'muted' }, text: '未启用' }) : null,
        ].filter(Boolean)),
        input,
        el('span', {
          cls: 'settings-field__hint secondary',
          text: gated ? '需先配置 DashScope Key 才能启用语音转文字' : field.hint,
        }),
      ])
    );
  }

  // 两个密钥：只显示是否已配置，输入框留空表示不改
  for (const [key, label] of [['moss_api_key', 'MOSS API Key'], ['dashscope_api_key', 'DashScope Key']]) {
    const configured = v[key] && v[key].configured;
    const input = el('input', {
      cls: 'input',
      attrs: { type: 'password', placeholder: configured ? '已配置，留空表示不修改' : '未配置，填入后保存', autocomplete: 'off' },
      on: { input: (event) => { state.voiceDraft[key] = event.target.value; } },
    });
    grid.append(
      el('label', { cls: 'settings-field' }, [
        el('span', { cls: 'settings-field__label' }, [
          label,
          configured
            ? el('span', { cls: 'tag voice-cfg__ok', attrs: { 'data-tone': 'accent' }, text: '已配置' })
            : el('span', { cls: 'tag voice-cfg__warn', attrs: { 'data-tone': 'warn' }, text: '未配置' }),
        ]),
        input,
      ])
    );
  }

  const toggles = el('div', { cls: 'voice-cfg__toggles' });
  for (const [key, label, hint] of [
    ['moss_voice_enabled', '启用实时语音', '关掉后语音页会提示不可用'],
    ['moss_voice_write_enabled', '允许语音写操作', '如语音派工、改状态；关掉只能查询'],
    ['moss_voice_briefing_enabled', '允许朗读生产概况', '语音页的朗读按钮'],
  ]) {
    const checked = state.voiceDraft[key] !== undefined ? Boolean(state.voiceDraft[key]) : Boolean(v[key]);
    const box = el('input', { attrs: { type: 'checkbox' } });
    box.checked = checked;
    box.addEventListener('change', () => { state.voiceDraft[key] = box.checked; });
    toggles.append(
      el('label', { cls: 'models-check' }, [box, el('span', { text: label }), el('span', { cls: 'secondary voice-cfg__hint', text: hint })])
    );
  }

  const hint = el('p', { cls: 'settings-hint' });
  const save = el('button', {
    cls: 'btn btn--primary btn--sm',
    attrs: { type: 'button' },
    text: '保存并生效',
    on: {
      click: async () => {
        save.disabled = true;
        save.textContent = '保存中…';
        hint.textContent = '';
        try {
          const patch = {};
          for (const [k, val] of Object.entries(state.voiceDraft || {})) {
            if (val === '' || val === undefined || val === null) continue;
            patch[k] = k.endsWith('_seconds') || k.endsWith('_limit') ? Number(val) : val;
          }
          state.voice = await VOICE_CONFIG.update(patch);
          state.voiceDraft = {};
          toast('语音配置已生效');
          renderVoice();
        } catch (err) {
          hint.textContent = describe(err);
          save.disabled = false;
          save.textContent = '保存并生效';
        }
      },
    },
  });

  body.append(
    el('p', { cls: 'voice-cfg__note secondary' }, [
      '语音助手用的是 MOSS-Live 实时通话 + DashScope 语音转文字，和上面的模型端点不是一套配置。改完点保存立即生效，不用重启后端。',
    ]),
    grid,
    toggles,
    hint,
    el('div', { cls: 'row voice-cfg__actions' }, [save])
  );
}

async function testVoice() {
  try {
    const result = await VOICE_CONFIG.test();
    if (result.ok && result.host_reachable) {
      toast('配置完整，主机可达');
    } else if (result.ok) {
      toast('配置完整，但主机暂时探测不到', 'error');
    } else {
      toast((result.problems || []).join('；') || '配置不完整', 'error');
    }
  } catch (err) {
    toast(describe(err), 'error');
  }
}

/** 空态盒子：图标 + 标题 + 说明 + 可选主行动。与 todos.js 的同名工具保持一致。 */
function emptyBox(iconName, title, text, actionLabel, onAction) {
  const box = el('div', { cls: 'empty models-empty' }, [
    el('div', { cls: 'empty__icon', attrs: { style: `--empty-icon: url(/icons/${iconName}.svg)` } }),
    el('h3', { text: title }),
    text ? el('p', { text }) : null,
  ]);
  if (actionLabel) {
    box.append(
      el('button', { cls: 'btn btn--primary', attrs: { type: 'button' }, text: actionLabel, on: { click: onAction } })
    );
  }
  return box;
}
