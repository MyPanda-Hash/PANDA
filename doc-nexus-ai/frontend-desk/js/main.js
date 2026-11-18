// 应用外壳：登录门、左侧导航、视图路由、公共头部动作。
// 各功能区是独立模块，统一契约：mount(root, ctx) / unmount()。
//   ctx = { api, util, navigate, emit, refreshCounts, user, state }
// 视图路由用 hash：#/todos（默认）、#/chat、#/voice

import * as api from './api.js';
import * as util from './util.js';
import { initViewAsSwitch, viewIdentity } from './view-as.js'

// 视图按角色分层：同一个 AI，管理者看「要你定什么」，执行层看「你要做什么」。
//   manager     → 简报 / 决策项（最多 3 条）
//   supervisor  → 待办清单（按执行视角铺开）
//   staff       → 只看自己手上的单
const VIEWS = [
  { key: 'brief', hash: '#/brief', label: '简报', icon: 'file-text', title: '今日简报', subtitle: '一屏看完今天要你决定的事', roles: ['manager', 'supervisor'] },
  // 车间主任的落点是「现场」看进度；一线员工的落点是待办清单。
  // 「现场」是主任按需查看的视图，不做登录落点：进来先看待办更符合岗位日常
  { key: 'floor', hash: '#/floor', label: '现场', icon: 'calendar', title: '车间现场', subtitle: '在制工单进度、今日报工、卡点', roles: ['supervisor', 'staff'] },
  { key: 'my', hash: '#/my', label: '我的活儿', icon: 'check-square', title: '我的活儿', subtitle: '还没报工的工序与我的产出', roles: ['staff'] },
  { key: 'todos', hash: '#/todos', label: '待办', icon: 'inbox', title: '待办工作台', subtitle: 'AI 整理出来的待办与发现，都收在这里' },
  // 工作台的默认落点：进来就是对话界面（侧边栏里再进简报/待办/群聊等）。
  { key: 'chat', hash: '#/chat', label: '对话', icon: 'message', title: '对话', subtitle: '和 {assistant} 聊，也可以直接说', landing: ['manager', 'supervisor', 'staff'] },
  { key: 'voice', hash: '#/voice', label: '语音', icon: 'audio', title: '语音助手', subtitle: '{voice} 随时可以通话，也能朗读生产概况' },
  { key: 'groups', hash: '#/groups', label: '群聊', icon: 'team', title: '项目群聊', subtitle: '按项目建群、群内 AI 整理待办' },
  // 员工画像只对管理员有意义：后端对非 admin 直接 403，所以按角色隐藏入口
  { key: 'staff', hash: '#/staff', label: '员工画像', icon: 'user', title: '员工画像', subtitle: 'AI 按周期蒸馏的团队职能卡片', adminOnly: true },
  // 会话归档：管理员审计用，普通账号看不到入口
  { key: 'library', hash: '#/library', label: '资料库', icon: 'folder', title: '资料库', subtitle: '报表模板、制度文件、工艺文件' },
  { key: 'archive', hash: '#/archive', label: '会话归档', icon: 'inbox', title: '会话归档', subtitle: '谁在什么时候用过 AI、聊了什么', adminOnly: true },
  { key: 'ai-usage', hash: '#/ai-usage', label: 'AI 用量', icon: 'file-text', title: 'AI 用量', subtitle: '每个账号每月花了多少、还剩多少额度', adminOnly: true },
  { key: 'models', hash: '#/models', label: '模型配置', icon: 'thunderbolt', title: '模型与 API', subtitle: 'AI 助理实际调用的模型端点与密钥', adminOnly: true },
];

const state = {
  user: null,
  counts: { todos: 0, events: 0, unread: 0 },
  active: null,
  module: null,
};

const ctx = {
  api,
  util,
  user: null,
  state,
  navigate,
  emit,
  refreshCounts,
};

// ---------------------------------------------------------------- 角色

const ROLES = [
  { key: 'manager', label: '管理者视角' },
  { key: 'supervisor', label: '车间主任视角' },
  { key: 'staff', label: '一线员工视角' },
];

/** 当前展示视角：切换到某账号时跟那个账号走，否则看自己的角色。 */
function currentRole() {
  const viewing = viewIdentity();
  if (viewing && viewing.role) return viewing.role;
  return ownRole();
}

/** 我自己的角色：由 MES 账号等级决定，SSO 时写进 localStorage。 */
function ownRole() {
  const allowed = ROLES.map((item) => item.key);
  let saved = '';
  try {
    saved = localStorage.getItem('dnx-role') || '';
  } catch (_) {
    saved = '';
  }
  if (allowed.includes(saved)) return saved;
  return 'manager';
}

/** 该视图对当前角色是否可见。未标注 roles 的对所有人可见。 */
function viewVisible(view, role = currentRole()) {
  if (view.adminOnly && !isAdmin()) return false;
  if (view.roles && !view.roles.includes(role)) return false;
  return true;
}

/** 落点：角色优先取标了 landing 的视图，否则第一个可见视图。 */
function landingHash(role = currentRole()) {
  const preferred = VIEWS.find((v) => (v.landing || []).includes(role) && viewVisible(v, role));
  const first = preferred || VIEWS.find((v) => viewVisible(v, role));
  return first ? first.hash : '#/todos';
}

// ---------------------------------------------------------------- 路由

function currentView() {
  const role = currentRole();
  const hash = location.hash || landingHash(role);
  const found = VIEWS.find((view) => hash.startsWith(view.hash));
  // 落到当前角色看不到的视图（手输地址 / 切角色前留在旧页）时回落到该角色落点
  if (!found || !viewVisible(found, role)) {
    const fallbackHash = landingHash(role);
    const fallback = VIEWS.find((v) => v.hash === fallbackHash);
    return fallback || VIEWS[0];
  }
  return found;
}

async function navigate(hash, { replace = false } = {}) {
  if (location.hash === hash) {
    await render();
    return;
  }
  if (replace) history.replaceState(null, '', hash);
  else location.hash = hash;
}

/**
 * 非对话视图的返回入口：侧边栏已经搬到对话界面里，这些页面只保留一个返回按钮。
 */
function renderBackToChat(view) {
  const slot = document.getElementById('backToChat')
  if (!slot) return
  slot.replaceChildren()
  if (view.key === 'chat') return
  const btn = util.el('button', {
    cls: 'back-chat',
    attrs: { type: 'button', title: '返回对话' },
    on: { click: () => { location.hash = '#/chat' } },
  }, [
    util.el('span', { cls: 'back-chat__arrow', text: '←' }),
    util.el('span', { text: '返回对话' }),
  ])
  slot.append(btn)
}

async function render() {
  const view = currentView();
  const viewRoot = document.getElementById('view');
  const titleEl = document.getElementById('viewTitle');
  const subEl = document.getElementById('viewSubtitle');

  const brand = state.brand || { assistant_name: '小智', voice_name: '小智' };
  titleEl.textContent = view.title;
  subEl.textContent = String(view.subtitle || '')
    .replace('{assistant}', brand.assistant_name || '小智')
    .replace('{voice}', brand.voice_name || brand.assistant_name || '小智');
  const brandName = (state.brand && state.brand.assistant_name) || '小智';
  document.title = `${view.title} · ${brandName}`;
  markNav(view.key);
  viewRoot.scrollTop = 0;

  if (!state.user) {
    renderGate(viewRoot);
    return;
  }

  try {
    if (state.module && typeof state.module.unmount === 'function') {
      await state.module.unmount();
    }
  } catch (err) {
    console.warn('unmount failed', err);
  }
  state.module = null;
  state.active = view.key
  // 对话页是全屏会话界面，外侧导航由它自己的侧边栏替代
  document.body.dataset.view = view.key
  // 非对话视图：左上角给一个「返回对话」，替代原来的外侧导航
  renderBackToChat(view);
  viewRoot.replaceChildren(util.el('div', { cls: 'view__inner' }));

  const mountPoint = viewRoot.firstChild;
  try {
    const mod = await import(`./${view.key}.js`);
    state.module = mod;
    await mod.mount(mountPoint, ctx);
  } catch (err) {
    renderModuleError(mountPoint, view, err);
  }
}

function renderModuleError(root, view, err) {
  console.error(`[desk] ${view.key} 加载失败`, err);
  root.replaceChildren();
  root.append(
    util.el('div', { cls: 'empty' }, [
      util.el('div', { cls: 'empty__icon', attrs: { style: '--empty-icon: url(/icons/alert.svg)' } }),
      util.el('h3', { text: `${view.label}暂时打不开` }),
      util.el('p', { text: err && err.message ? err.message : '模块加载失败' }),
      util.el('button', { cls: 'btn btn--primary', text: '重试', attrs: { type: 'button' }, on: { click: () => render() } }),
    ])
  );
}

/**
 * 顶栏视角切换：只给管理员显示。
 *
 * 选一个账号 = 按那个账号的身份取数（后端 view_as），不只是换文案。
 * 以前只有三个固定角色，切换后数字不变、视图可见性却变了，看着很乱。
 */
async function initRoleSwitch() {
  await initViewAsSwitch({
    isAdmin: isAdmin(),
    onSwitch: async () => {
      buildRail();
      await navigate(landingHash(), { replace: true });
      await render();
    },
  });
}
function isAdmin() {
  return Boolean(state.user && state.user.role === 'admin');
}

function markNav(key) {
  for (const link of document.querySelectorAll('.rail__link')) {
    if (link.dataset.view === key) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
}

function buildRail() {
  const nav = document.getElementById('railNav');
  nav.replaceChildren();
  for (const view of VIEWS) {
    if (!viewVisible(view)) continue;
    const count = view.key === 'todos' ? state.counts.todos
      : view.key === 'brief' ? (state.counts.brief || 0)
      : 0;
    const badge = util.el('span', {
      cls: 'rail__count',
      text: count > 99 ? '99+' : String(count),
      attrs: { hidden: count > 0 ? null : true },
    });
    if (view.key === 'todos') badge.dataset.tone = 'muted';
    const link = util.el(
      'button',
      { cls: 'rail__link', attrs: { type: 'button', 'data-view': view.key } },
      [util.icon(view.icon), util.el('span', { text: view.label }), badge]
    );
    link.addEventListener('click', () => navigate(view.hash));
    nav.append(util.el('li', null, link));
  }
  markNav(currentView().key);
}

function updateCounts(patch) {
  Object.assign(state.counts, patch || {});
  const nav = document.getElementById('railNav');
  const todoBtn = nav.querySelector('[data-view="todos"] .rail__count');
  if (todoBtn) {
    const value = state.counts.todos;
    todoBtn.textContent = value > 99 ? '99+' : String(value);
    todoBtn.hidden = !(value > 0);
  }
  // 简报角标：buildRail 在拿到通知之前就画完了，这里必须单独回填，
  // 否则「今日简报已推送」这个提示永远显示不出来（只有手动刷页面才出现）。
  const briefBtn = nav.querySelector('[data-view="brief"] .rail__count');
  if (briefBtn) {
    const hasBrief = (state.counts.brief || 0) > 0;
    briefBtn.textContent = hasBrief ? '1' : '0';
    briefBtn.hidden = !hasBrief;
  }
  const dot = document.getElementById('notifyDot');
  if (dot) dot.hidden = !(state.counts.unread > 0 || state.counts.events > 0);
}

async function refreshCounts() {
  if (!state.user) return;
  const [todos, events, notices] = await Promise.all([
    api.api.get('/todos?limit=200').catch(() => null),
    api.WATCH.events('open', 50).catch(() => null),
    api.NOTIFICATIONS.list(true, 20).catch(() => null),
  ]);
  const openTodos = Array.isArray(todos)
    ? todos.filter((item) => item.status !== 'done' && item.status !== 'dismissed').length
    : 0;
  const openEvents = Array.isArray(events) ? events.length : 0;
  const unread = notices && notices.data ? Number(notices.data.unread || 0) : 0;
  // 今日简报是「推给自己」的一条站内通知：单独记一个标记，让「简报」入口带角标
  const items = notices && notices.data && Array.isArray(notices.data.items) ? notices.data.items : [];
  const hasBrief = items.some((item) => item && item.refType === 'briefing');
  updateCounts({ todos: openTodos, events: openEvents, unread, brief: hasBrief ? 1 : 0 });
}

// ---------------------------------------------------------------- 登录门

function renderGate(viewRoot) {
  viewRoot.replaceChildren();
  const inner = util.el('div', { cls: 'view__inner' });
  const card = util.el('div', { cls: 'card', attrs: { style: 'max-width:560px;margin:48px auto' } });
  const body = util.el('div', { cls: 'card__body' }, [
    util.el('h2', { text: '先确认一下是你本人', attrs: { style: 'font-size:18px;line-height:26px;margin-bottom:8px' } }),
    util.el('p', {
      cls: 'secondary',
      text: '这个工作台读的是你自己的待办和 AI 发现，需要登录后才能看。从 MES 点「AI 助理」进来会自动登录，也可以在这里直接输账号密码。',
      attrs: { style: 'margin-bottom:20px' },
    }),
  ]);
  // 账号密码直接在这里登，不依赖任何外部站点。
  const userInput = util.el('input', {
    cls: 'input',
    attrs: { type: 'text', placeholder: '账号', autocomplete: 'username', style: 'width:100%' },
  });
  const passInput = util.el('input', {
    cls: 'input',
    attrs: { type: 'password', placeholder: '密码', autocomplete: 'current-password', style: 'width:100%;margin-top:8px' },
  });
  const hint = util.el('p', { cls: 'secondary', attrs: { style: 'margin-top:8px;min-height:20px;font-size:13px' } });
  const submit = util.el('button', {
    cls: 'btn btn--primary',
    text: '登录',
    attrs: { type: 'submit', style: 'margin-top:16px;width:100%' },
  });

  const form = util.el('form', { attrs: { style: 'margin-bottom:4px' } }, [
    userInput,
    passInput,
    submit,
    hint,
  ]);

  const doLogin = async (event) => {
    event.preventDefault();
    const username = userInput.value.trim();
    const password = passInput.value;
    if (!username || !password) {
      hint.textContent = '账号和密码都要填。';
      return;
    }
    submit.disabled = true;
    submit.textContent = '登录中…';
    hint.textContent = '';
    try {
      await api.loginWithPassword(username, password);
      await bootSession(true);
    } catch (err) {
      hint.textContent = err && err.status === 401
        ? '账号或密码不对。'
        : (err && err.message) || '登录失败，请重试。';
    } finally {
      submit.disabled = false;
      submit.textContent = '登录';
    }
  };
  form.addEventListener('submit', doLogin);

  const actions = util.el('div', { cls: 'row wrap', attrs: { style: 'margin-top:8px' } }, [
    util.el('button', {
      cls: 'btn btn--text', text: '用 MES 账号登录', attrs: { type: 'button' },
      on: { click: () => bootSession(true) },
    }),
    util.el('button', {
      cls: 'btn btn--text', text: '重试', attrs: { type: 'button' },
      on: { click: () => bootSession(false) },
    }),
  ]);
  body.append(form, actions);
  card.append(body);
  inner.append(card);
  viewRoot.append(inner);
}

async function bootSession(interactive) {
  const result = await api.ensureSession();
  if (result.ok) {
    state.user = result.user;
    ctx.user = result.user;
    paintUser();
    buildRail(); // 角色相关的入口（如员工画像）要等拿到 user 才能画
    await loadBranding(); // 品牌名要登录后才能读（接口要求登录态）
    await initFactory(); // 账套：SSO 带进来的那套，或用户上次选的
    const settingsBtn = document.getElementById('btnSettings');
    if (settingsBtn) settingsBtn.hidden = !isAdmin();
    initRoleSwitch();
    await refreshCounts();
    // 登录后按角色落点走：管理者直接进简报，执行层进待办
    if (!location.hash || location.hash === '#/todos') {
      const target = landingHash();
      if (target !== location.hash) {
        history.replaceState(null, '', target);
      }
    }
    await render();
    return;
  }
  if (result.reason === 'offline') {
    util.toast('连不上 AI 后端，请先启动服务', 'error');
  } else if (interactive) {
    util.toast('还没登录成功，请在上面输入账号密码', 'error');
  }
  state.user = null;
  renderGate(document.getElementById('view'));
}

// ---------------------------------------------------------------- 用户区

function paintUser() {
  const user = state.user || {};
  const name = user.display_name || user.username || '未登录';
  const nameEl = document.getElementById('meName');
  if (nameEl) nameEl.textContent = name;
  const avatar = document.getElementById('meAvatar');
  if (avatar) {
    avatar.replaceChildren();
    if (user.avatar_url) {
      const img = util.el('img', { attrs: { src: user.avatar_url, alt: '' } });
      // 头像地址失效时不要留个裂图标：退回姓名首字
      img.addEventListener('error', () => {
        avatar.replaceChildren();
        avatar.textContent = util.initials(name);
      });
      avatar.append(img);
    } else {
      avatar.textContent = util.initials(name);
    }
  }
}

function bindUserMenu() {
  const button = document.getElementById('btnUserMenu');
  button.addEventListener('click', () => {
    const existing = document.getElementById('userPop');
    if (existing) { existing.remove(); return; }
    const rect = button.getBoundingClientRect();
    const pop = util.el('div', {
      cls: 'card',
      attrs: { id: 'userPop', style: `position:fixed;z-index:900;left:${rect.left}px;bottom:${window.innerHeight - rect.top + 8}px;min-width:200px;padding:8px;box-shadow:var(--ant-box-shadow-secondary)` },
    });
    const user = state.user || {};
    pop.append(util.el('div', { cls: 'secondary', attrs: { style: 'padding:6px 10px;font-size:12px' }, text: `${user.username || ''}${user.role ? ` · ${user.role}` : ''}` }));
    pop.append(util.el('button', {
      cls: 'btn btn--text btn--block', text: '退出登录', attrs: { type: 'button' },
      on: { click: async () => { await api.api.post('/auth/logout').catch(() => null); location.reload(); } },
    }));
    document.getElementById('overlayRoot').append(pop);
    setTimeout(() => {
      document.addEventListener('click', function close(event) {
        if (!pop.contains(event.target) && event.target !== button) { pop.remove(); document.removeEventListener('click', close); }
      });
    }, 0);
  });
}


// ---------------------------------------------------------------- 品牌 / 命名

const BRAND_DEFAULTS = {
  system_name: 'MES',
  assistant_name: '小智',
  voice_name: '小智',
  company_name: '',
};

/*** 把品牌写进外壳：导航品牌、页面标题、助手称呼。 */
function applyBranding(brand) {
  state.brand = { ...BRAND_DEFAULTS, ...(brand || {}) };
  const assistant = state.brand.assistant_name || BRAND_DEFAULTS.assistant_name;
  const textEl = document.getElementById('brandText');
  const logoEl = document.getElementById('brandLogo');
  const subEl = document.getElementById('brandSub');
  // 品牌分两行：主名（助手名）+ 副标题（系统名）。
  // 只改主名的文本节点，别把 <i> 副标题整个覆盖掉。
  if (textEl) {
    const first = textEl.firstChild;
    if (first && first.nodeType === Node.TEXT_NODE) first.nodeValue = assistant;
    else textEl.insertBefore(document.createTextNode(assistant), textEl.firstChild);
  }
  if (logoEl) logoEl.textContent = assistant.slice(0, 1);
  if (subEl) subEl.textContent = state.brand.system_name || BRAND_DEFAULTS.system_name;
  document.title = `${currentView().title} · ${assistant}`;
}

async function loadBranding() {
  try {
    applyBranding(await api.BRANDING.get());
  } catch (_) {
    // 取不到就用默认，不影响其它功能
    applyBranding(null);
  }
}

/*** 管理员设置面板：品牌命名 + 外观。非管理员看不到入口。 */
function openSettings() {
  if (!isAdmin()) return;
  const existing = document.getElementById('settingsPanel');
  if (existing) { existing.remove(); return; }

  const brand = state.brand || BRAND_DEFAULTS;
  const fields = [
    { key: 'assistant_name', label: '助理名称', hint: '对话页与左侧品牌显示的名字', max: 16 },
    { key: 'voice_name', label: '语音助手名称', hint: '语音页里的助手称呼', max: 16 },
    { key: 'system_name', label: '系统名称', hint: '左侧品牌副标题与页面标题后缀', max: 32 },
    { key: 'company_name', label: '公司名称', hint: '可选，显示在设置里便于区分部署', max: 48 },
  ];

  const inputs = {};
  const rows = fields.map((field) => {
    const input = util.el('input', {
      cls: 'input',
      attrs: { type: 'text', maxlength: field.max, value: brand[field.key] || '', placeholder: BRAND_DEFAULTS[field.key] },
    });
    inputs[field.key] = input;
    return util.el('label', { cls: 'settings-field' }, [
      util.el('span', { cls: 'settings-field__label', text: field.label }),
      input,
      util.el('span', { cls: 'settings-field__hint secondary', text: field.hint }),
    ]);
  });

  const themeRow = util.el('div', { cls: 'settings-theme' }, [
    util.el('span', { cls: 'settings-field__label', text: '外观' }),
    (() => {
      const seg = util.el('div', { cls: 'seg', attrs: { role: 'group', 'aria-label': '外观模式' } });
      for (const [value, label] of [['light', '浅色'], ['dark', '深色'], ['auto', '跟随系统']]) {
        const btn = util.el('button', {
          cls: 'seg__btn',
          attrs: { type: 'button', 'data-theme-choice': value },
          text: label,
          on: {
            click: () => {
              setTheme(value);
              for (const b of seg.querySelectorAll('[data-theme-choice]')) {
                b.setAttribute('aria-selected', String(b.dataset.themeChoice === value));
              }
            },
          },
        });
        if (themeChoice() === value) btn.setAttribute('aria-selected', 'true');
        seg.append(btn);
      }
      return seg;
    })(),
  ]);

  const hint = util.el('p', { cls: 'secondary settings-hint' });
  const save = util.el('button', {
    cls: 'btn btn--primary',
    text: '保存',
    attrs: { type: 'submit' },
  });

  const form = util.el('form', { cls: 'settings-form' }, [...rows, themeRow, hint, save]);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    save.disabled = true;
    save.textContent = '保存中…';
    hint.textContent = '';
    try {
      const patch = {};
      for (const field of fields) patch[field.key] = inputs[field.key].value.trim();
      const updated = await api.BRANDING.update(patch);
      applyBranding(updated);
      util.toast('已保存');
      closePanel();
      await render();
    } catch (err) {
      hint.textContent = (err && err.message) || '保存失败';
      save.disabled = false;
      save.textContent = '保存';
    }
  });

  const panel = util.el('div', {
    cls: 'settings-panel',
    attrs: { id: 'settingsPanel', role: 'dialog', 'aria-modal': 'true', 'aria-label': '品牌与外观设置' },
  }, [
    util.el('div', { cls: 'settings-panel__mask', on: { click: () => closePanel() } }),
    util.el('div', { cls: 'settings-panel__body' }, [
      util.el('header', { cls: 'settings-panel__head' }, [
        util.el('h2', { cls: 'settings-panel__title', text: '品牌与外观' }),
        util.el('button', {
          cls: 'icon-btn',
          attrs: { type: 'button', 'aria-label': '关闭' },
          text: '×',
          on: { click: () => closePanel() },
        }),
      ]),
      form,
    ]),
  ]);
  (document.getElementById('overlayRoot') || document.body).append(panel);
  inputs[fields[0].key].focus();
}

function closePanel() {
  const panel = document.getElementById('settingsPanel');
  if (panel) panel.remove();
}


// ---------------------------------------------------------------- 账套

/** 读当前账套并渲染到顶栏；切换后整页重载，与 MES 侧行为一致。 */
async function initFactory() {
  try {
    const state = await api.FACTORY.get();
    state_factory = state;
    renderFactory(state);
  } catch (_) {
    // 取不到就不显示，不影响其它功能
  }
}

let state_factory = null;

function renderFactory(state) {
  const host = document.getElementById('factorySlot');
  if (!host) return;
  host.replaceChildren();
  const items = (state && state.items) || [];
  const current = (state && state.current) || '';
  const found = items.find((item) => item.code === current);
  const label = found ? found.name : current;
  if (!label) return;

  const btn = util.el('button', {
    cls: 'pill-btn',
    attrs: { type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': 'false', title: '当前账套' },
    on: { click: () => toggleFactoryMenu(host, items, current) },
  }, [
    util.icon('home'),
    util.el('span', { cls: 'pill-btn__label', text: label }),
  ]);
  const tag = util.el('span', { cls: 'factory-tag', text: '账套' });
  host.append(btn, tag);
}

function toggleFactoryMenu(host, items, current) {
  const existing = document.getElementById('factoryMenu');
  if (existing) { existing.remove(); return; }
  const rect = host.getBoundingClientRect();
  const menu = util.el('div', {
    cls: 'card factory-menu',
    attrs: {
      id: 'factoryMenu',
      role: 'menu',
      style: `position:fixed;z-index:950;right:${Math.max(8, window.innerWidth - rect.right)}px;top:${rect.bottom + 6}px;min-width:180px;padding:6px;box-shadow:var(--ant-box-shadow-secondary)`,
    },
  });
  for (const item of items) {
    const active = item.code === current;
    menu.append(
      util.el('button', {
        cls: 'factory-menu__item',
        attrs: { type: 'button', role: 'menuitem', 'aria-current': active ? 'true' : null },
        on: {
          click: async () => {
            if (active) { menu.remove(); return; }
            await api.FACTORY.switch(item.code);
            try { localStorage.setItem('mes_factory_code', item.code); } catch (_) {}
            menu.remove();
            location.reload();
          },
        },
      }, [
        util.el('span', { text: item.name }),
        item.code === current ? util.icon('check-square') : null,
      ])
    );
  }
  (document.getElementById('overlayRoot') || document.body).append(menu);
  setTimeout(() => {
    document.addEventListener('click', function close(event) {
      if (!menu.contains(event.target)) { menu.remove(); document.removeEventListener('click', close); }
    });
  }, 0);
}

// ---------------------------------------------------------------- 外观

/** 当前生效的外观：light | dark */
function currentTheme() {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

/**
 * 外观只认一个来源：chat 会话（chat-demo）的选择。
 *
 * 为什么不再给工作台自己的开关：用户在对话页和工作台之间来回切，
 * 两边各自记主题就会出现「聊天是深的、待办是浅的」，看着像两个产品。
 * chat 把偏好存在同源的 `chat-demo:theme-preference`（light | dark | system），
 * 工作台直接读它，并监听 storage 事件做实时跟随。
 */
const CHAT_THEME_KEY = 'chat-demo:theme-preference';

function themeChoice() {
  try {
    const saved = localStorage.getItem(CHAT_THEME_KEY);
    if (saved === 'light' || saved === 'dark' || saved === 'system') return saved;
    // 没有就跟着系统走，和 chat 的默认行为一致
    return 'system';
  } catch (_) {
    return 'system';
  }
}

function setTheme(choice) {
  const mode = choice || 'system';
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const resolved = mode === 'system' ? (prefersDark ? 'dark' : 'light') : mode;
  document.documentElement.dataset.theme = resolved;
  // 群聊页读的是 dnx-theme，写进去让它跟外层同色
  try { localStorage.setItem('dnx-theme', resolved); } catch (_) {}
}

/** 跟随 chat 的主题：同源 storage 事件 + 系统配色变化都要重新解析。 */
function followChatTheme() {
  setTheme(themeChoice());
  window.addEventListener('storage', (event) => {
    if (event.key === CHAT_THEME_KEY) setTheme(themeChoice());
  });
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (themeChoice() === 'system') setTheme('system');
  });
}


// ---------------------------------------------------------------- 头部动作

function bindChrome() {
  const settingsBtn = document.getElementById('btnSettings');
  if (settingsBtn) settingsBtn.addEventListener('click', () => openSettings());
  document.getElementById('btnNotify').addEventListener('click', () => {
    emit('desk:notify');
    // 有未读的「今日简报」时，铃铛直接把人送到简报页——这正是早晚最想看的那一页
    const role = currentRole();
    const toBrief = (state.counts.brief || 0) > 0 && viewVisible({ roles: ['manager', 'supervisor'] }, role);
    navigate(toBrief ? '#/brief' : '#/todos');
  });
  const search = document.getElementById('globalSearch');
  search.addEventListener('input', util.debounce((event) => emit('desk:search', event.target.value.trim()), 200));
  search.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { search.value = ''; emit('desk:search', ''); search.blur(); }
  });
}

function emit(name, detail) {
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

// 服务端可能是「假数据模式」（serve-ai-desk.mjs --mock），必须让人一眼看出来，
// 否则会把演示待办当成真任务去处理。
async function markMockMode() {
  try {
    const res = await fetch('/healthz', { cache: 'no-store' });
    const info = await res.json();
    if (!info || info.mode !== 'mock') return;
    const title = document.querySelector('.topbar__title h1');
    if (!title) return;
    title.append(
      util.el('span', {
        cls: 'tag',
        text: '演示数据',
        attrs: { 'data-tone': 'warn', title: '当前连的是 mock 数据（doc-nexus-ai/mock），不是真实待办', style: 'margin-left:8px;vertical-align:3px' },
      })
    );
  } catch (_) {
    /* 拿不到就不提示，不影响正常使用 */
  }
}

// ---------------------------------------------------------------- 启动

async function boot() {
  // 分步执行并记录进度：工作台是纯前端，任何一步抛错都会让整页停在静态 HTML 上，
  // 而浏览器控制台里只留一条没人看的堆栈。2026-11-17 就踩过：改了模块之后
  // 页面只剩 index.html 的静态标题，排查很久才想到是初始化中断。
  // 这里把错误的步骤名和消息直接写进页面，至少让人知道「坏在哪一步」。
  let step = 'start';
  try {
    step = 'theme';
    followChatTheme();
    step = 'rail';
    buildRail();
    step = 'chrome';
    bindChrome();
    step = 'userMenu';
    bindUserMenu();
    step = 'routes';
    window.addEventListener('hashchange', render);
    document.getElementById('viewSubtitle').textContent = currentView().subtitle;
    step = 'session';
    await bootSession(false);
    step = 'mock';
    markMockMode();
    setInterval(() => { if (state.user) refreshCounts(); }, 60000);
  } catch (err) {
    window.__bootError = { step, message: String(err && err.message || err), stack: String(err && err.stack || '') };
    console.error('[desk] boot failed at ' + step, err);
    const root = document.getElementById('view');
    if (root) {
      root.replaceChildren(
        util.el('div', { cls: 'empty' }, [
          util.el('h3', { text: '工作台初始化失败（' + step + '）' }),
          util.el('p', { text: String(err && err.message || err) }),
          util.el('button', {
            cls: 'btn btn--primary',
            text: '重试',
            attrs: { type: 'button' },
            on: { click: () => location.reload() },
          }),
        ])
      );
    }
  }
}

boot();
