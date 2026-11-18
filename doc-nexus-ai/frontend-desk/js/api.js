// 与 AI 后端（FastAPI）的唯一数据通道。
// 前端所有请求走同源 /api/*，由 serve-ai-desk.mjs（或 serve-ai-web.mjs）反代到 :8000。
// 登录态是同源 Cookie，所以和旧的 18091 共用同一个会话（Cookie 不区分端口）。

// 合并账号体系后，工作台有两种运行形态：
//   ① 挂在 MES 同源路径 /ai/ 下 —— 接口走 /ai-api（由 MES 前端反代到 AI 后端）；
//   ② 独立站点 :18092 —— 接口仍走 /api（由 serve-ai-desk.mjs 反代）。
// 用路径判断而不是配置注入：同一个构建产物两种部署都能跑。
export const API_ROOT = window.location.pathname.startsWith('/ai/') ? '/ai-api' : '/api';

/** MES 的登录态：同源时直接读 localStorage（MES 存在 mes_token）。 */
export function mesToken() {
  try {
    return localStorage.getItem('mes_token') || '';
  } catch (_) {
    return '';
  }
}
export const IS_MOCK = new URLSearchParams(location.search).has('mock');

export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

function errorMessage(status, payload) {
  const detail = payload && (payload.detail || payload.message);
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((item) => item.msg || item.detail || '').filter(Boolean).join('；') || '请求被拒绝';
  }
  if (status === 401) return '登录已过期，请重新登录';
  if (status === 403) return '没有权限执行这个操作';
  if (status === 404) return '数据不存在或已被删除';
  if (status === 422) return '参数不合法';
  if (status >= 500) return '服务暂时不可用，请稍后重试';
  return `请求失败（${status}）`;
}

/** 当前账套：MES 点进来时由 SSO 写入，切换后随请求带上。 */
function currentFactory() {
  try {
    // 优先 localStorage（用户在工作台内切过账套），其次 SSO 写下的 Cookie。
    // AI 工作台与 MES 跨源，localStorage 不共享，所以 SSO 落 Cookie 是唯一的传递通道。
    const local = localStorage.getItem('mes_factory_code');
    if (local) return local;
    const match = document.cookie.match(/(?:^|; )doc_nexus_factory=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : '';
  } catch (_) {
    return '';
  }
}

function viewAsHeader() {
  try {
    const raw = localStorage.getItem('dnx-view-as')
    if (!raw) return ''
    const parsed = JSON.parse(raw)
    return parsed && parsed.username ? String(parsed.username) : ''
  } catch (_) {
    return ''
  }
}

async function request(path, { method = 'GET', body, headers, signal, raw } = {}) {
  const factory = currentFactory();
  const init = {
    method,
    credentials: 'include',
    signal,
    headers: {
      Accept: 'application/json',
      ...(factory ? { 'X-Factory-Code': factory } : {}),
      // 管理员切换查看账号时带上；普通账号后端会 403，不会静默生效
      ...(viewAsHeader() ? { 'X-View-As': viewAsHeader() } : {}),
      // 同源部署：直接把 MES 的登录态带给 AI，AI 验签即可，不需要再登录一次
      ...(mesToken() ? { Authorization: 'Bearer ' + mesToken() } : {}),
      ...(headers || {}),
    },
  };
  if (body !== undefined) {
    if (body instanceof FormData) {
      init.body = body; // 交给浏览器自己带 boundary
    } else {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
  }
  let response;
  try {
    response = await fetch(`${API_ROOT}${path}`, init);
  } catch (err) {
    if (err && err.name === 'AbortError') throw err;
    throw new ApiError('连不上服务，检查 AI 后端是否在运行', 0, null);
  }
  if (response.status === 204) return null;
  if (raw) return response;
  const text = await response.text();
  let payload = null;
  if (text) {
    try { payload = JSON.parse(text); } catch (_) { payload = { detail: text.slice(0, 300) }; }
  }
  if (!response.ok) throw new ApiError(errorMessage(response.status, payload), response.status, payload);
  return payload;
}

export const api = {
  get: (path, opts) => request(path, opts),
  post: (path, body, opts) => request(path, { ...opts, method: 'POST', body }),
  patch: (path, body, opts) => request(path, { ...opts, method: 'PATCH', body }),
  del: (path, opts) => request(path, { ...opts, method: 'DELETE' }),
  raw: (path, opts) => request(path, { ...opts, raw: true }),
};

// ---------------------------------------------------------------- 会话

export async function fetchMe() {
  try {
    return await api.get('/auth/me', { signal: AbortSignal.timeout(6000) });
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null;
    throw err;
  }
}

/** 老部署兜底：用 MES 票据换 AI 会话（未配置 MES_JWT_SECRET 时才会走到）。 */
async function tryMesSso() {
  let token = '';
  try {
    token = localStorage.getItem('mes_token') || '';
    if (token === 'undefined') token = '';
  } catch (_) {
    return false;
  }
  if (!token) return false;
  try {
    // 走 API_ROOT：同源部署时 MES 后端挂在 /ai-api 之外，票据接口只在独立站点下有效
    const res = await fetch(`${API_ROOT}/ai/sso-ticket`, { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) return false;
    const payload = await res.json();
    const ticket = payload && payload.data && payload.data.ticket;
    if (!ticket) return false;
    const exchange = await fetch(`${API_ROOT}/auth/mes-sso?ticket=${encodeURIComponent(ticket)}`, {
      credentials: 'include',
      redirect: 'follow',
    });
    return exchange.ok || exchange.type === 'opaqueredirect';
  } catch (_) {
    return false;
  }
}

/**
 * 确认登录态。返回 { ok, user, reason }。
 * reason: 'ok' | 'sso' | 'unauthenticated' | 'offline'
 */
export async function ensureSession() {
  let user = null;
  try {
    user = await fetchMe();
  } catch (err) {
    return { ok: false, user: null, reason: err.status === 0 ? 'offline' : 'unauthenticated' };
  }
  if (user) return { ok: true, user, reason: 'ok' };
  if (await tryMesSso()) {
    user = await fetchMe().catch(() => null);
    if (user) return { ok: true, user, reason: 'sso' };
  }
  return { ok: false, user: null, reason: 'unauthenticated' };
}

/** 账号密码登录：后端 POST /auth/login，成功后 Set-Cookie，本页直接可用。 */
export async function loginWithPassword(username, password) {
  return api.post('/auth/login', { username, password });
}

/** 旧门户的登录页（同一个后端，登录后 Cookie 对本页同样有效）。 */
export function legacyLoginUrl() {
  const { protocol, hostname, port } = location;
  const legacyPort = port === '18092' ? '18091' : '';
  return `${protocol}//${hostname}${legacyPort ? `:${legacyPort}` : ''}/`;
}

// ---------------------------------------------------------------- 待办

export const TODOS = {
  list: (params = '') => api.get(`/todos${params ? `?${params}` : ''}`),
  setStatus: (id, status) => api.patch(`/todos/${encodeURIComponent(id)}`, { status }),
};

// ---------------------------------------------------------------- AI 发现（主动监控）

// 后端 assistant_watch 的前缀是 /assistant（api/assistant_watch.py:26），
// 事件在 /assistant/events、状态在 /assistant/status，没有 watch 这一层。
export const WATCH = {
  events: (status = 'open', limit = 50) =>
    api.get(`/assistant/events?status=${encodeURIComponent(status)}&limit=${limit}`),
  /** 待认领 + 已认领（跟进中）：界面要同时看到的两种状态。 */
  active: async (limit = 50) => {
    const [open, handled] = await Promise.all([
      api.get(`/assistant/events?status=open&limit=${limit}`).catch(() => []),
      api.get(`/assistant/events?status=handled&limit=${limit}`).catch(() => []),
    ]);
    return [...(Array.isArray(open) ? open : []), ...(Array.isArray(handled) ? handled : [])];
  },
  status: () => api.get('/assistant/status'),
  ack: (id) => api.post(`/assistant/events/${encodeURIComponent(id)}:ack`),
  reopen: (id) => api.post(`/assistant/events/${encodeURIComponent(id)}:reopen`),
};

// ---------------------------------------------------------------- 品牌 / 命名

// 全员可读（外壳渲染品牌与助手名），写仅管理员。见 backend/app/api/branding.py。
export const BRANDING = {
  get: () => api.get('/branding'),
  update: (patch) => api.patch('/branding', patch),
};

// ---------------------------------------------------------------- 语音助手配置（仅管理员）

// 语音走 MOSS-Live + DashScope ASR，与 chat 端点不是一套，单独一组接口。
// 密钥只回「是否已配置」，永不回传明文。
export const VOICE_CONFIG = {
  get: () => api.get('/admin/voice-config'),
  update: (patch) => api.patch('/admin/voice-config', patch),
  test: () => api.post('/admin/voice-config/test'),
};

// ---------------------------------------------------------------- 账套（MES factory）

// 账套由 MES 权威维护，AI 只镜像当前选择。见 backend/app/api/factory.py。
export const FACTORY = {
  get: () => api.get('/factory'),
  switch: (code) => api.post('/factory/switch', { code, name: code }),
};

// ---------------------------------------------------------------- 模型端点（仅管理员）

// 后端：api/model_endpoints.py，前缀 /admin/model-endpoints。
// secret_ref 接受明文 Key 或 env://变量名，由后端解析（api/model_endpoints.py:_resolve_configured_secret）。
export const MODELS = {
  list: () => api.get('/admin/model-endpoints'),
  create: (payload) => api.post('/admin/model-endpoints', payload),
  update: (id, payload) => api.patch(`/admin/model-endpoints/${encodeURIComponent(id)}`, payload),
  remove: (id) => api.del(`/admin/model-endpoints/${encodeURIComponent(id)}`),
  test: (id) => api.post(`/admin/model-endpoints/${encodeURIComponent(id)}:test`),
};

// ---------------------------------------------------------------- 员工画像（仅管理员）

// 后端：app/api/employee_profiles.py，路由前缀 /admin/employees，非 admin 返回 403。
export const EMPLOYEES = {
  list: (userId = '') =>
    api.get(`/admin/employees${userId ? `?user_id=${encodeURIComponent(userId)}` : ''}`),
  experiences: (status = '') =>
    api.get(`/admin/employees/experiences${status ? `?status=${encodeURIComponent(status)}` : ''}`),
  /** 某人的 AI 会话记忆（按时间倒序）。 */
  memories: (userId, limit = 12) =>
    api.get(`/admin/employees/${encodeURIComponent(userId)}/memories?limit=${limit}`),
  /** 触发会话蒸馏：后端把会话正文交给模型提炼，同周期覆盖写。 */
  distillSessions: (userId, days = 30) =>
    api.post(`/admin/employees/${encodeURIComponent(userId)}:distill-sessions?days=${days}`),
};

// ---------------------------------------------------------------- 简报 / 通知

export const BRIEFING = {
  latest: () => api.get('/assistant/briefing'),
  /** 简报看板：结论 + 四个数字 + 三条待拍板；角色决定取数范围 */
  board: ({ role = 'manager', period = '' } = {}) =>
    api.get(`/assistant/briefing/board?role=${encodeURIComponent(role)}&period=${encodeURIComponent(period)}`),
  /** 车间现场看板：在制工单进度、今日报工、停滞与逾期 */
  floor: () => api.get('/assistant/briefing/floor'),
  /** 一线看板：待报工工序 + 我的产出 */
  mine: () => api.get('/assistant/briefing/mine'),
  /** 生成周期报表（daily / weekly / monthly），会投递到我的对话 */
  generateReport: (kind, anchor = '') =>
    api.post(`/assistant/briefing/reports/${encodeURIComponent(kind)}${anchor ? '?anchor=' + encodeURIComponent(anchor) : ''}`),
  notifications: (limit = 5) => api.get(`/assistant/briefing/notifications?limit=${limit}`),
};

export const NOTIFICATIONS = {
  list: (unreadOnly = true, limit = 20) => api.get(`/notifications?unread=${unreadOnly ? 'true' : 'false'}&limit=${limit}`),
  markRead: (ids) => api.post('/notifications/read', { ids: ids || [] }),
};

// ---------------------------------------------------------------- 对话

export const CHAT = {
  sessions: () => api.get('/chat/sessions'),
  session: (id, limit = 60) => api.get(`/chat/sessions/${encodeURIComponent(id)}?message_limit=${limit}`),
  create: (title = '') => api.post('/chat/sessions', { title }),
  rename: (id, title) => api.patch(`/chat/sessions/${encodeURIComponent(id)}`, { title }),
  remove: (id) => api.del(`/chat/sessions/${encodeURIComponent(id)}`),
  capabilities: () => api.get('/chat/capabilities'),
};

/**
 * 发一条消息并消费 SSE 流。
 * 后端：POST /api/chat/messages，响应 text/event-stream，每行 `data: {json}`。
 * 事件里关心 type=assistant_message（phase start/complete）、done、error、tool 等；
 * 这里统一抛给 onEvent，由 UI 决定怎么画。
 *
 * @returns {Promise<{sessionId: string|null}>}
 */
export async function streamChatMessage(body, { onEvent, signal } = {}) {
  const sseFactory = currentFactory();
  const response = await fetch(`${API_ROOT}/chat/messages`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(sseFactory ? { 'X-Factory-Code': sseFactory } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const text = await response.text();
    let payload = null;
    try { payload = JSON.parse(text); } catch (_) { payload = { detail: text.slice(0, 300) }; }
    throw new ApiError(errorMessage(response.status, payload), response.status, payload);
  }
  if (!response.body) throw new ApiError('当前浏览器不支持流式响应', 0, null);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let sessionId = null;

  const dispatch = (chunk) => {
    const line = chunk.trim();
    if (!line.startsWith('data:')) return;
    const raw = line.slice(5).trim();
    if (!raw || raw === '[DONE]') return;
    let event = null;
    try { event = JSON.parse(raw); } catch (_) { return; }
    if (event && event.type === 'session' && event.session && event.session.id) sessionId = event.session.id;
    if (event && event.session_id) sessionId = event.session_id;
    if (Array.isArray(event && event.messages)) {
      const last = event.messages[event.messages.length - 1];
      if (last && last.session_id) sessionId = last.session_id;
    }
    onEvent && onEvent(event);
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop() || '';
    for (const part of parts) {
      for (const line of part.split('\n')) dispatch(line);
    }
  }
  if (buffer.trim()) {
    for (const line of buffer.split('\n')) dispatch(line);
  }
  return { sessionId };
}

// ---------------------------------------------------------------- 语音

export const VOICE = {
  status: () => api.get('/assistant/voice/status'),
  /** 录音文件 → 文字（DashScope ASR，WAV 最稳） */
  transcribe: async (blob, filename = 'speech.wav') => {
    const form = new FormData();
    form.append('file', blob, filename);
    return request('/asr/transcribe', { method: 'POST', body: form });
  },
};


// ---------------------------------------------------------------- 会话归档（管理员）
export const CHAT_ARCHIVE = {
  list: (owner = '') =>
    api.get('/chat-archive' + (owner ? '?owner=' + encodeURIComponent(owner) : '')),
  detail: (id) => api.get('/chat-archive/' + encodeURIComponent(id)),
}


// ---------------------------------------------------------------- 报表导出
// 下载要带 Authorization，不能用 <a href>，只能 fetch 成 blob 再触发保存。
export async function downloadReportPptx(kind, sessionId = '') {
  const query = sessionId ? '?session_id=' + encodeURIComponent(sessionId) : ''
  const res = await fetch('/ai-api/assistant/briefing/reports/' + encodeURIComponent(kind) + '/pptx' + query, {
    headers: mesToken() ? { Authorization: 'Bearer ' + mesToken() } : {},
  })
  if (!res.ok) {
    let detail = '导出失败'
    try {
      const body = await res.json()
      detail = body.detail || body.message || detail
    } catch {
      /* 非 JSON 错误体，用默认文案 */
    }
    const err = new Error(detail)
    err.status = res.status
    throw err
  }
  const blob = await res.blob()
  // 文件名优先按服务端 Content-Disposition 的 filename* 解析
  const disposition = res.headers.get('Content-Disposition') || ''
  const match = /filename\*=UTF-8''(.+)$/.exec(disposition)
  const name = match ? decodeURIComponent(match[1]) : '报表.pptx'
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.append(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 4000)
  return name
}

// ---------------------------------------------------------------- 资料库
export const LIBRARY = {
  categories: () => api.get('/library/categories'),
  list: (category = '', keyword = '') => {
    const params = []
    if (category) params.push('category=' + encodeURIComponent(category))
    if (keyword) params.push('keyword=' + encodeURIComponent(keyword))
    return api.get('/library' + (params.length ? '?' + params.join('&') : ''))
  },
  upload: (file, category = 'template', note = '') => {
    const form = new FormData()
    form.append('file', file)
    form.append('category', category)
    form.append('note', note)
    return api.post('/library', form)
  },
  remove: (id) => api.del('/library/' + encodeURIComponent(id)),
  // 下载要带 Authorization，走 blob
  download: async (id, name) => {
    const res = await fetch('/ai-api/library/' + encodeURIComponent(id) + '/download', {
      headers: mesToken() ? { Authorization: 'Bearer ' + mesToken() } : {},
    })
    if (!res.ok) {
      const err = new Error('下载失败')
      err.status = res.status
      throw err
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = name || 'file'
    document.body.append(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 4000)
  },
}


// ---------------------------------------------------------------- 视角切换（管理员）
export const VIEW_AS = {
  accounts: () => api.get('/view-as/accounts'),
}

// ---------------------------------------------------------------- 联网检索配置（管理员）
export const SEARCH_CFG = {
  get: () => api.get('/admin/search'),
  save: (values) => api.patch('/admin/search', values),
  test: () => api.post('/admin/search:test', {}),
}

export const AI_USAGE = {
  report: (month) => api.get('/admin/ai-usage' + (month ? '?month=' + encodeURIComponent(month) : '')),
  saveQuota: (values) => api.patch('/admin/ai-usage/quota', values),
  prices: () => api.get('/admin/ai-usage/prices'),
  monthly: () => api.get('/admin/ai-usage/monthly'),
}

// ---------------------------------------------------------------- 角色权限自检（管理员）
// 配置完「AI 角色权限」后用它核对：每个岗位实际收到多少条、都落在哪些域。
// 只看自己那份是发现不了「销售员一条也收不到」这种断链的。
export const ROLE_AUDIT = {
  list: () => api.get('/assistant/role-audit'),
}
