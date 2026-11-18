// 项目群聊页（无构建步骤，浏览器直接跑 ES 模块）
// 全部数据走 /api/*，由 serve-ai-web.mjs 反代到 FastAPI。

import { concatChunks, encodeWav, resampleTo } from './audio-wav.js';
import {
  ASSISTANT_LABEL,
  UNASSIGNED_LABEL,
  buildOrgTree,
  deptLine,
  groupByDeptPath,
} from './org-view.js';

// 合并到 MES 同源后，群聊页可能挂在 /ai/group/ 下：
// 此时接口要走 /ai-api（MES 反代到 AI 后端），直接写 /api 会打到 MES 自己身上。
// 用路径前缀判断，同构建产物两种部署都成立。
const API = window.location.pathname.startsWith('/ai/') ? '/ai-api' : '/api';
const POLL_MS = 5000;

const state = {
  me: null,
  groups: [],
  groupId: null,
  detail: null,
  messages: [],
  latestId: null,
  todos: [],
  showFinishedTodos: false,
  showDissolved: false,
  dissolved: [],
  recording: null,
  events: [],
  seenEventIds: new Set(),
  eventsPrimed: false,
  quoted: null,        // 正在引用的消息
  menuTarget: null,    // 右键菜单指向的消息
  unreadFromId: null,
  readUpToId: null,
  ackedFilter: 'any',     // any / none / me / others
  assigneeFilter: '',     // '' 或用户名
  orgMap: null,           // 人力地图：MES 部门树 + 成员归属（接口不可用时为 null）
  memberView: localStorage.getItem('group.memberView') === 'list' ? 'list' : 'map',
  // 组织架构树里被手动折叠的部门（记住这一个人的看法，不写服务端）
  orgCollapsed: new Set(JSON.parse(localStorage.getItem('group.orgCollapsed') || '[]')),
  facets: null,
  staffTab: 'profiles',
  staffSelected: null,
  staffProfiles: [],
  staffExperiences: [],
  staffPromotions: [],
};

const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
};

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

/** MES 是唯一身份源：同源时要显式带上它的 token，否则 /auth/me 一律 401。 */
function mesToken() {
  try {
    const value = localStorage.getItem('mes_token');
    return value && value !== 'undefined' ? value : '';
  } catch (_) {
    return '';
  }
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const token = mesToken();
  if (token) headers.Authorization = 'Bearer ' + token;
  let body = options.body;
  if (body && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(body);
  }
  const response = await fetch(API + path, {
    method: options.method || 'GET',
    headers,
    body,
    credentials: 'include',
  });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!response.ok) {
    const detail = (data && (data.detail || data.message)) || `请求失败（${response.status}）`;
    throw new ApiError(response.status, typeof detail === 'string' ? detail : '请求失败');
  }
  return data;
}

function toast(message) {
  const node = $('toast');
  node.textContent = message;
  node.classList.add('is-on');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove('is-on'), 2600);
}

function initials(name) {
  const text = (name || '?').trim();
  return text.slice(0, 1).toUpperCase();
}

/**
 * 头像节点：有图就画图，没图退回姓名首字。
 * 全部头像都走这一个函数，免得消息、成员、顶栏三处各写一套。
 */
function avatarNode(className, name, url, { ai = false } = {}) {
  const node = el('span', className);
  if (ai) {
    node.classList.add(`${String(className).split(' ')[0]}--ai`);
    node.textContent = 'AI';
    return node;
  }
  if (url) {
    const img = document.createElement('img');
    img.src = url;
    img.alt = name ? `${name}的头像` : '头像';
    img.loading = 'lazy';
    img.addEventListener('error', () => {
      img.remove();
      node.textContent = initials(name);
    });
    node.appendChild(img);
    return node;
  }
  node.textContent = initials(name);
  return node;
}

function formatDue(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const midnight = date.getHours() === 0 && date.getMinutes() === 0;
  const md = `${date.getMonth() + 1}-${date.getDate()}`;
  return midnight ? md : `${md} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function formatTime(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  return sameDay ? `${hh}:${mm}` : `${date.getMonth() + 1}-${date.getDate()} ${hh}:${mm}`;
}

// ---------------- 引导 ----------------

async function boot() {
  try {
    state.me = await api('/auth/me');
  } catch (error) {
    if (error.status === 401) {
      // 同源部署下 /chat 打到 MES 会被 SPA fallback 成整个 MES 页面（MES 套 MES）；
      // 必须指回工作台自己的对话页。
      const chatUrl = location.pathname.startsWith('/ai/') ? '/ai/#/chat' : '/chat';
      window.location.replace(chatUrl);
      return;
    }
    toast(error.message);
    return;
  }
  $('me').textContent = state.me.display_name || state.me.username || '';
  renderMyAvatar();
  applyTheme(savedTheme());
  if (isAdmin()) $('btnStaff').hidden = false;
  bindEvents();
  await loadGroups();
  loadTodos();
  await loadEvents();
  setInterval(pollMessages, POLL_MS);
  setInterval(() => loadEvents({ silent: false }), 20000);
}

async function loadGroups() {
  const all = await api('/groups?include_archived=true');
  state.dissolved = (all || []).filter((item) => item.status === 'archived');
  state.groups = (all || []).filter((item) => item.status !== 'archived');
  renderGroups();
  if (state.groups.length) {
    // 支持深链：/group?group=<id> 直接从 MES 的消息通知跳进对应群聊
    const wanted = new URLSearchParams(window.location.search).get('group') || '';
    const keep = state.groups.some((item) => item.id === state.groupId);
    const target = state.groups.some((item) => item.id === wanted)
      ? wanted
      : keep ? state.groupId : state.groups[0].id;
    await selectGroup(target);
  } else {
    $('groupsEmpty').hidden = false;
    $('stream').hidden = true;
    $('composer').hidden = true;
    $('roomHead').hidden = true;
  }
}

function renderGroups() {
  const list = $('groupList');
  const select = $('groupSelect');
  list.innerHTML = '';
  select.innerHTML = '';
  $('groupsEmpty').hidden = state.groups.length > 0 || (state.dissolved || []).length > 0;

  for (const group of state.groups) {
    const item = el('li');
    const button = el('button', 'group-item');
    button.type = 'button';
    button.dataset.id = group.id;
    if (group.id === state.groupId) button.classList.add('is-on');

    const avatar = el('span', 'group-item__avatar', initials(group.name));
    const body = el('span', 'group-item__body');
    const name = el('span', 'group-item__name', group.name);
    body.appendChild(name);
    body.appendChild(
      el('span', 'group-item__meta', `${group.member_count} 人 · ${group.my_role === 'owner' ? '组长' : group.my_role === 'manager' ? '管理员' : '成员'}`)
    );
    button.append(avatar, body);
    if (group.unread_count > 0) button.appendChild(el('span', 'badge', String(group.unread_count)));
    button.addEventListener('click', () => selectGroup(group.id));
    item.appendChild(button);
    list.appendChild(item);

    const option = el('option', null, group.name);
    option.value = group.id;
    select.appendChild(option);
  }

  renderDissolvedGroups(list);
  if (state.groupId) select.value = state.groupId;
}


/** 已解散的群：默认折叠，展开后可查看历史并手动补提炼。 */
function renderDissolvedGroups(list) {
  const rows = state.dissolved || [];
  if (!rows.length) return;
  const head = el('li', 'group-more');
  const toggle = el('button', 'group-more__toggle', state.showDissolved ? '收起已解散' : `已解散（${rows.length}）`);
  toggle.type = 'button';
  toggle.addEventListener('click', () => {
    state.showDissolved = !state.showDissolved;
    renderGroups();
  });
  head.appendChild(toggle);
  list.appendChild(head);
  if (!state.showDissolved) return;

  for (const group of rows) {
    const item = el('li');
    const button = el('button', 'group-item group-item--dissolved');
    button.type = 'button';
    button.dataset.id = group.id;
    if (group.id === state.groupId) button.classList.add('is-on');
    button.append(
      el('span', 'group-item__avatar', initials(group.name)),
    );
    const body = el('span', 'group-item__body');
    body.appendChild(el('span', 'group-item__name', group.name));
    body.appendChild(el('span', 'group-item__meta', `已解散 · ${group.member_count} 人`));
    button.append(body);
    button.addEventListener('click', () => selectGroup(group.id));
    item.appendChild(button);
    list.appendChild(item);
  }
}

async function selectGroup(groupId) {
  if (!groupId) return;
  state.groupId = groupId;
  renderGroups();
  try {
    state.detail = await api(`/groups/${groupId}`);
    // 人力地图是独立接口：组织数据取不到也要把群聊正常打开，只是没有部门分组
    try {
      state.orgMap = await api(`/groups/${groupId}/org-map`);
    } catch (error) {
      state.orgMap = null;
    }
    const listed = await api(`/groups/${groupId}/messages?limit=80`);
    state.messages = listed.messages || [];
    // 首屏拉了 80 条：满页说明上面还有，给「加载更早」入口
    state.hasMoreHistory = (listed.messages || []).length >= 80;
    state.loadingHistory = false;
    state.latestId = listed.latest_message_id || null;
    state.unreadFromId = listed.unread_from_id || null;
    state.readUpToId = listed.read_up_to_id || null;
  } catch (error) {
    toast(error.message);
    return;
  }
  renderRoom();
  renderMembers();
  renderMessages();
  reportRead();
  syncDisbandEntry();
}

function renderRoom() {
  const group = state.detail;
  if (!group) return;
  $('roomHead').hidden = false;
  $('stream').hidden = false;
  $('composer').hidden = group.status === 'archived';
  $('roomName').textContent = group.name;
  const roomAvatar = $('roomAvatar');
  if (roomAvatar) roomAvatar.textContent = initials(group.name);
  const roleText =
    group.my_role === 'owner' ? '组长' : group.my_role === 'manager' ? '管理员' : '成员';
  const memory =
    group.memory_message_count > 0 ? ` · 已记住前 ${group.memory_message_count} 条` : '';
  $('roomMeta').textContent = `${group.member_count} 人 · 我是${roleText}${memory}${
    group.status === 'archived' ? ' · 已解散（记录保留）' : ''
  }`;
  const archived = group.status === 'archived';
  const distill = $('btnDistill');
  if (distill) {
    distill.hidden = !(archived && (group.my_role === 'owner' || isAdmin()));
  }
  // 已解散的群不能再拉人、也没必要再整理待办
  const invite = $('btnInvite');
  if (invite) invite.hidden = archived;
  const analyze = $('btnAnalyze');
  if (analyze) analyze.hidden = archived;
}

/**
 * 成员行：头像 + 姓名 + 可选副标题（部门/岗位）+ 群角色。
 * 副标题由调用方决定：平铺看部门，人力地图里部门已在分组标题上、这里只补岗位。
 */
function memberRow(member, sub, { compact = false } = {}) {
  const row = el('li', compact ? 'member-row member-row--compact' : 'member-row');
  if (member.role === 'assistant') row.classList.add('member-row--ai');
  row.appendChild(
    avatarNode('member-avatar', member.display_name, member.avatar_url, {
      ai: member.role === 'assistant',
    }),
  );
  const main = el('span', 'member-row__main');
  main.appendChild(el('span', 'member-row__name', member.display_name));
  if (sub) {
    const line = el('span', 'member-row__dept', sub);
    if (member.dept_path) line.title = member.dept_path;
    main.appendChild(line);
  }
  row.appendChild(main);
  // 树里只在「不是普通成员」时给角色标：一排「成员」标签只是噪音
  if (!compact || member.role !== 'member') {
    const role = el('span', 'member-row__role', roleLabel(member.role));
    role.dataset.role = member.role;
    row.appendChild(role);
  }
  return row;
}

// 折叠箭头用设计库里的 CaretRightFilled / CaretDownFilled（assets/icons/filled/）
const CARET_PATH =
  'M715.8 493.5L335 165.1c-14.2-12.2-35-1.2-35 18.5v656.8c0 19.7 20.8 30.7 35 18.5l380.8-328.4c10.9-9.4 10.9-27.6 0-37z';

// 部门图标：设计库 ApartmentOutlined（assets/icons/outlined/apartment.svg）
const DEPT_ICON_PATH =
  'M908 640H804V488c0-4.4-3.6-8-8-8H548v-96h108c8.8 0 16-7.2 16-16V80c0-8.8-7.2-16-16-16H368c-8.8 0-16 7.2-16 16v288c0 8.8 7.2 16 16 16h108v96H228c-4.4 0-8 3.6-8 8v152H116c-8.8 0-16 7.2-16 16v288c0 8.8 7.2 16 16 16h288c8.8 0 16-7.2 16-16V656c0-8.8-7.2-16-16-16H292v-88h440v88H620c-8.8 0-16 7.2-16 16v288c0 8.8 7.2 16 16 16h288c8.8 0 16-7.2 16-16V656c0-8.8-7.2-16-16-16zm-564 76v168H176V716h168zm84-408V140h168v168H428zm420 576H680V716h168v168z';

function svgIcon(path, className, size = 12) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', path.viewBox);
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', className);
  const node = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  node.setAttribute('d', path.d);
  svg.appendChild(node);
  return svg;
}

function caretNode(open) {
  const span = el('span', open ? 'org-row__switcher is-open' : 'org-row__switcher');
  span.appendChild(svgIcon({ viewBox: '0 0 1024 1024', d: CARET_PATH }, 'org-row__caret', 8));
  return span;
}

/**
 * 组织架构树的一个部门节点，按设计库 Tree(showLine) 的语义画：
 * 左侧折叠箭头 + 缩进连线 + 名称，人数右对齐、tabular-nums。
 * 人数是含下级的合计，父部门才看得出「这个体系一共几个人」。
 */
function orgTreeNode(node) {
  const key = String(node.id);
  const collapsed = state.orgCollapsed.has(key);
  const expandable = node.children.length > 0 || node.members.length > 0;

  const wrap = el('div', 'org-tree__node');
  if (node.total === 0) wrap.classList.add('is-empty');
  const row = el('button', 'org-tree__row');
  row.type = 'button';
  row.setAttribute('role', 'treeitem');
  row.setAttribute('aria-level', String(node.depth + 1));
  row.setAttribute('aria-expanded', expandable ? String(!collapsed) : 'false');
  if (expandable) {
    row.classList.add('is-toggle');
    if (!collapsed) row.classList.add('is-open');
    row.appendChild(caretNode(!collapsed));
    row.addEventListener('click', () => {
      if (state.orgCollapsed.has(key)) state.orgCollapsed.delete(key);
      else state.orgCollapsed.add(key);
      localStorage.setItem('group.orgCollapsed', JSON.stringify([...state.orgCollapsed]));
      renderOrgMap();
    });
  } else {
    row.appendChild(el('span', 'org-row__switcher org-row__switcher--leaf'));
    row.disabled = true;
  }
  row.appendChild(svgIcon({ viewBox: '64 64 896 896', d: DEPT_ICON_PATH }, 'org-tree__icon', 12));
  const name = el('span', 'org-tree__name', node.name);
  if (node.path) name.title = node.path;
  row.appendChild(name);
  row.appendChild(el('span', 'org-tree__count', String(node.total)));
  wrap.appendChild(row);

  if (expandable && !collapsed) {
    const body = el('div', 'org-tree__children');
    // 先列本部门的人，再列下级单位：反过来的话，最后一个子部门会把人「认领」走
    if (node.members.length) {
      const leaves = el('ul', 'org-tree__leaves');
      for (const member of node.members) {
        leaves.appendChild(memberRow(member, member.position || '', { compact: true }));
      }
      body.appendChild(leaves);
    }
    for (const child of node.children) body.appendChild(orgTreeNode(child));
    wrap.appendChild(body);
  }
  return wrap;
}

/** 常驻助手 / 未分配部门：不是部门节点，用一行小标签 + 成员列表。 */
function orgFlatSection(name, rows) {
  const wrap = el('section', 'org-section');
  const head = el('div', 'org-section__head');
  head.appendChild(el('span', 'org-section__name', name));
  head.appendChild(el('span', 'org-section__count', String(rows.length)));
  wrap.appendChild(head);
  const list = el('ul', 'org-section__list');
  for (const member of rows) {
    list.appendChild(memberRow(member, member.position || '', { compact: true }));
  }
  wrap.appendChild(list);
  return wrap;
}

function renderOrgMap() {
  const members = (state.detail && state.detail.members) || [];
  const orgMap = $('orgMap');
  const tree = buildOrgTree(state.orgMap, members);
  orgMap.innerHTML = '';
  if (tree.assistants.length) orgMap.appendChild(orgFlatSection(ASSISTANT_LABEL, tree.assistants));
  if (tree.roots.length) {
    const wrap = el('div', 'org-tree');
    wrap.setAttribute('role', 'tree');
    for (const root of tree.roots) wrap.appendChild(orgTreeNode(root));
    orgMap.appendChild(wrap);
  }
  if (tree.unassigned.length) {
    orgMap.appendChild(orgFlatSection(UNASSIGNED_LABEL, tree.unassigned));
  }
}

function renderMembers() {
  const members = (state.detail && state.detail.members) || [];
  const list = $('memberList');
  list.innerHTML = '';
  for (const member of members) {
    list.appendChild(memberRow(member, member.role === 'assistant' ? '' : deptLine(member)));
  }
  renderOrgMap();
  const empty = $('orgEmpty');
  if (empty) {
    empty.textContent =
      members.length > 0 ? '' : '还没有成员。点右上角「邀请成员」把人拉进来。';
  }
  applyMemberView();
}

/** 人力地图 / 平铺 的显隐与按钮态；选择存在 state.memberView。 */
function applyMemberView() {
  const view = state.memberView === 'list' ? 'list' : 'map';
  const orgMap = $('orgMap');
  const list = $('memberList');
  const empty = $('orgEmpty');
  const members = (state.detail && state.detail.members) || [];
  if (orgMap) orgMap.hidden = view !== 'map';
  if (list) list.hidden = view !== 'list';
  if (empty) empty.hidden = view !== 'map' || members.length > 0;
  for (const button of document.querySelectorAll('.org-switch__btn')) {
    const on = button.dataset.view === view;
    button.classList.toggle('is-on', on);
    button.setAttribute('aria-pressed', on ? 'true' : 'false');
  }
}

function roleLabel(role) {
  if (role === 'owner') return '组长';
  if (role === 'manager') return '管理员';
  if (role === 'assistant') return '常驻助手';
  return '成员';
}

// ---------------- 消息 ----------------

// 微信式时间：今天/昨天/星期/日期，超过 5 分钟或换天才显示
const WEEKDAYS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];

function dayKey(date) {
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
}

function formatChatTime(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const now = new Date();
  const todayKey = dayKey(now);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const hm = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  if (dayKey(date) === todayKey) return hm;
  if (dayKey(date) === dayKey(yesterday)) return `昨天 ${hm}`;
  if (now - date < 6 * 24 * 3600 * 1000) return `${WEEKDAYS[date.getDay()]} ${hm}`;
  return `${date.getMonth() + 1}月${date.getDate()}日 ${hm}`;
}

// 相邻消息是否属于「同一个人连着说的」：同人、5 分钟内、中间没有别人
function groupedWith(prev, cur) {
  if (!prev || prev.sender_kind === 'system' || cur.sender_kind === 'system') return false;
  if ((prev.sender_user_id || prev.sender_name) !== (cur.sender_user_id || cur.sender_name)) return false;
  const a = new Date(prev.created_at).getTime();
  const b = new Date(cur.created_at).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return false;
  return b - a < 5 * 60 * 1000;
}

function needTimeSeparator(prev, cur) {
  if (!prev) return true;
  if (cur.sender_kind === 'system') return true;
  const a = new Date(prev.created_at).getTime();
  const b = new Date(cur.created_at).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return true;
  return b - a > 5 * 60 * 1000;
}

/**
 * 瞬时滚到底。
 *
 * 消息流有 scroll-behavior: smooth，直接赋值 scrollTop 会变成平滑动画：
 * 进群时先渲染 80 条再滚，用户看到的就是「从头一段段滑到最新」，很累。
 * 这里临时关掉平滑，赋值后立刻还原，只影响这一次跳转。
 */
function jumpToBottom(node) {
  if (!node) return;
  const previous = node.style.scrollBehavior;
  node.style.scrollBehavior = 'auto';
  node.scrollTop = node.scrollHeight;
  node.style.scrollBehavior = previous;
}



/**
 * 消息流的渲染策略（2026-11-17 性能改造）。
 *
 * 原来的做法是「每次全量重建」：每 5 秒轮询到一条新消息，就 innerHTML 清空、
 * 把全部历史重新构造一遍。实测 DOM 节点数到 1.3 万时单次重建已 60ms+，
 * 而且 state.messages 只增不减，几个月就会进入明显卡顿区间。
 *
 * 现在两条策略：
 * 1. **窗口化**：只渲染最近 WINDOW_SIZE 条，更早的用顶部「加载更早」按钮翻；
 * 2. **增量**：轮询到新消息只 append，不重建已有节点。
 */
const WINDOW_SIZE = 200;
const PAGE_SIZE = 50;

/** 渲染一行消息并打上 id，供「补历史后锚回原位」使用。 */
/** 群聊页自己的 window（app.js 就在这个文档里跑）。 */
function gd() {
  return typeof window === 'undefined' ? null : window;
}


function rowNode(message, prev) {
  const node = renderMessage(message, prev);
  if (message && message.id) node.dataset.msgId = message.id;
  return node;
}

/** 当前视口顶部那条消息的 id（补历史前记下来，补完锚回去）。 */
function firstVisibleId(stream) {
  const top = stream.getBoundingClientRect().top;
  for (const row of stream.querySelectorAll('[data-msg-id]')) {
    if (row.getBoundingClientRect().top >= top - 1) return row.dataset.msgId;
  }
  return '';
}
/** 只在整体重排（切群 / 加载更早 / 需要重算分组）时调用。 */
function renderMessages(options = {}) {
  const stream = $('stream');
  const atBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 48;
  stream.innerHTML = '';

  const visible = state.messages.slice(-WINDOW_SIZE);
  if (!visible.length) {
    stream.appendChild(el('p', 'empty', '还没有消息，说第一句吧。'));
    return;
  }

  // 上面还有没加载/没显示的历史：给一个入口，而不是一次全画出来
  if (state.hasMoreHistory) {
    stream.appendChild(historyButton());
  }

  let prev = null;
  let unreadMarked = false;
  for (const message of visible) {
    if (needTimeSeparator(prev, message)) {
      stream.appendChild(el('div', 'time-sep', formatChatTime(message.created_at)));
    }
    if (!unreadMarked && state.unreadFromId && message.id === state.unreadFromId) {
      stream.appendChild(el('div', 'unread-divider', '以下为新消息'));
      unreadMarked = true;
    }
    stream.appendChild(rowNode(message, prev));
    prev = message;
  }

  // keepScroll：往顶部补历史时由调用方自己补偿滚动位置，
  // 这里再跳底就会把用户视线拽到最新一条（2026-11-17 实测踩到）。
  if (!options.keepScroll && atBottom) jumpToBottom(stream);
}

/** 顶部「加载更早」按钮。 */
function historyButton() {
  const wrap = el('div', 'history-more');
  const button = el('button', 'history-more__btn', state.loadingHistory ? '加载中…' : '加载更早消息');
  button.type = 'button';
  button.disabled = Boolean(state.loadingHistory);
  button.addEventListener('click', () => {
    loadHistory().catch(() => {});
  });
  wrap.appendChild(button);
  return wrap;
}

/**
 * 往顶部补一页历史。
 *
 * 补完要**保住视口**：用户正在看的位置不能因为顶插而跳动，
 * 所以在插入前后补偿 scrollTop（新内容高度 = 插入后的 scrollHeight - 插入前的）。
 */
async function loadHistory() {
  if (state.loadingHistory || !state.groupId || !state.messages.length) return;
  const stream = $('stream');
  state.loadingHistory = true;
  try {
    const firstId = state.messages[0].id;
    const listed = await api(
      `/groups/${state.groupId}/messages?before=${encodeURIComponent(firstId)}&limit=${PAGE_SIZE}`
    );
    const older = listed.messages || [];
    if (!older.length) {
      state.hasMoreHistory = false;
      const btn = stream.querySelector('.history-more');
      if (btn) btn.remove();
      return;
    }

    state.messages = [...older, ...state.messages];
    state.hasMoreHistory = older.length >= PAGE_SIZE;

    // 用**前插**而不是整体重建：
    // 整体重建（innerHTML 清空再画）会让滚动位置失去锚点，
    // 而 insertBefore 前插时浏览器自己会保持视口（overflow-anchor），
    // 用户视线停在原处，不需要手动补偿（实测前插后 scrollTop 自动 +2425）。
    const frag = document.createDocumentFragment();
    let prev = null;
    for (const message of older) {
      if (needTimeSeparator(prev, message)) {
        frag.appendChild(el('div', 'time-sep', formatChatTime(message.created_at)));
      }
      frag.appendChild(rowNode(message, prev));
      prev = message;
    }
    const before = stream.querySelector('.history-more');
    if (before) stream.insertBefore(frag, before.nextSibling);
    else stream.insertBefore(frag, stream.firstChild);

    if (!state.hasMoreHistory && before) before.remove();

    // 补完把视口带到新加载的那一段（就是顶部）。
    // 必须同步关掉平滑再滚：CSS 的 scroll-behavior:smooth 会让赋值变动画，
    // 异步补偿又会被轮询打断，同步瞬时是唯一稳定的做法。
    const prevBehavior = stream.style.scrollBehavior;
    stream.style.scrollBehavior = 'auto';
    stream.scrollTop = 0;
    stream.style.scrollBehavior = prevBehavior;


  } catch (error) {
    // 失败要能看见原因，不要静默吞掉
    window.__historyError = {
      message: String((error && error.message) || error),
      stack: String((error && error.stack) || '').slice(0, 400),
    };
    toast(error.message);
  } finally {
    state.loadingHistory = false;
  }
}
/**
 * 增量追加：轮询到新消息时走这里，不碰已有节点。
 *
 * 前提：state.messages 的尾部就是「当前已渲染的最后一条」，所以可以直接续画；
 * 如果这次拿到的比窗口还多（很久没轮询），退回整体重排一次。
 */
function appendMessages(fresh) {
  const stream = $('stream');
  const atBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 48;

  // 空态占位要清掉，否则新消息会接在「还没有消息」下面
  const empty = stream.querySelector('.empty');
  if (empty) empty.remove();

  const before = state.messages.length - fresh.length;
  let prev = before > 0 ? state.messages[before - 1] : null;
  let unreadMarked = Boolean(stream.querySelector('.unread-divider'));

  for (const message of fresh) {
    if (needTimeSeparator(prev, message)) {
      stream.appendChild(el('div', 'time-sep', formatChatTime(message.created_at)));
    }
    if (!unreadMarked && state.unreadFromId && message.id === state.unreadFromId) {
      stream.appendChild(el('div', 'unread-divider', '以下为新消息'));
      unreadMarked = true;
    }
    stream.appendChild(rowNode(message, prev));
    prev = message;
  }

  // 超出窗口就整体重排一次（偶尔发生，成本可接受）
  if (state.messages.length > WINDOW_SIZE * 2) {
    renderMessages();
    return;
  }
  if (atBottom) jumpToBottom(stream);
}
/**
 * AI 消息的结构化渲染。
 *
 * 调度摘要长这样：
 *   我在 MES 里发现了这些待办：
 *   - @planner：销售订单 SO-2026-08-0004 已审核，尚未安排生产
 *   建议：按销售订单生成生产工单并排产
 * 直接当一段文字排出来是一坨，这里拆成「段落 / 条目（带 @ 标记）/ 建议」。
 */
function renderAiContent(bubble, content) {
  const lines = String(content).split(/\r?\n/);
  let list = null;
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      list = null;
      continue;
    }
    if (/^[-•*]\s+/.test(line)) {
      if (!list) {
        list = el('div', 'ai-list');
        bubble.appendChild(list);
      }
      const row = el('div', 'ai-item');
      const body = line.replace(/^[-•*]\s+/, '');
      const mention = body.match(/^@([^\s：:，,]+)[：:]?\s*(.*)$/);
      if (mention) {
        row.appendChild(el('span', 'ai-mention', `@${mention[1]}`));
        row.appendChild(el('span', 'ai-item__text', mention[2]));
      } else {
        row.appendChild(el('span', 'ai-dot'));
        row.appendChild(el('span', 'ai-item__text', body));
      }
      list.appendChild(row);
      continue;
    }
    list = null;
    if (/^建议[：:]/.test(line)) {
      bubble.appendChild(el('div', 'ai-suggest', line));
    } else {
      bubble.appendChild(el('p', 'ai-para', line));
    }
  }
}

function renderMessage(message, prev) {
  if (message.sender_kind === 'system') {
    return el('div', 'msg-row msg-row--system', message.content);
  }
  const grouped = groupedWith(prev, message);
  const row = el('div', 'msg-row');
  if (message.mine) row.classList.add('msg-row--mine');
  if (grouped) row.classList.add('msg-row--grouped');

  // 头像：连续消息占位不重复画，微信也是这样
  const avatar = avatarNode('msg-avatar', message.sender_name, message.sender_avatar_url,
    { ai: message.kind === 'ai' });
  if (grouped) avatar.classList.add('msg-avatar--blank');
  row.appendChild(avatar);

  const col = el('div', 'msg-col');
  if (!grouped && !message.mine) {
    const name = message.kind === 'ai' ? 'AI 助手' : message.sender_name;
    col.appendChild(el('div', 'msg-name', name));
  }

  const bubble = el('div', 'bubble');
  if (message.kind === 'ai') bubble.classList.add('bubble--ai');
  if (message.quoted) {
    const quote = el('div', 'bubble__quote',
      `${message.quoted.sender_name}：${message.quoted.content}`);
    bubble.appendChild(quote);
  }
  if (message.kind === 'ai' && /\n|^- /m.test(message.content || '')) {
    renderAiContent(bubble, message.content || '');
  } else {
    bubble.appendChild(document.createTextNode(message.content || ''));
  }

  for (const attachment of message.attachments || []) {
    bubble.appendChild(renderAttachment(attachment));
  }

  if (message.kind === 'ai' && message.parts_json) {
    try {
      const parts = JSON.parse(message.parts_json);
      for (const part of parts) {
        if (part.type !== 'group_analysis' && part.type !== 'assistant_watch') continue;
        if (part.type === 'assistant_watch') continue;   // 调度事件在铃铛里看，不在气泡里堆
        for (const todo of part.todos || []) {
          bubble.appendChild(el('span', 'todo-chip', `待办 · ${todo.title}`));
        }
      }
    } catch {
      /* 片段格式不认识就忽略 */
    }
  }

  if (message.mine && message.read_total > 0) {
    const all = message.read_count >= message.read_total;
    const receipt = el('div', 'bubble__receipt', all
      ? '全部已读'
      : (message.read_count > 0 ? `已读 ${message.read_count}/${message.read_total}` : '未读'));
    receipt.dataset.read = all ? '1' : '0';
    bubble.appendChild(receipt);
  }

  bubble.addEventListener('contextmenu', (event) => {
    event.preventDefault();
    openMessageMenu(event.clientX, event.clientY, message);
  });
  let pressTimer = null;
  bubble.addEventListener('touchstart', () => {
    pressTimer = setTimeout(() => {
      const rect = bubble.getBoundingClientRect();
      openMessageMenu(rect.left + 24, rect.top + 8, message);
    }, 500);
  }, { passive: true });
  bubble.addEventListener('touchend', () => clearTimeout(pressTimer));

  col.appendChild(bubble);
  row.appendChild(col);
  return row;
}

function renderAttachment(attachment) {
  const link = document.createElement('a');
  link.className = 'attach';
  link.href = API + attachment.url;
  link.target = '_blank';
  link.rel = 'noopener';

  if (attachment.kind === 'image') {
    link.classList.add('attach--image');
    const img = document.createElement('img');
    img.src = link.href;
    img.alt = attachment.filename;
    link.appendChild(img);
    link.addEventListener('click', (event) => {
      event.preventDefault();
      $('dialogImageSrc').src = link.href;
      $('dialogImage').showModal();
    });
    return link;
  }

  link.textContent = attachment.filename;

  if (attachment.kind === 'voice') {
    link.classList.add('attach--voice');
    link.textContent = '';
    const audio = document.createElement('audio');
    audio.controls = true;
    audio.preload = 'none';
    audio.src = link.href;
    link.appendChild(audio);
    if (attachment.transcript) {
      link.appendChild(el('span', 'attach__transcript', attachment.transcript));
    } else {
      const stateText = el(
        'span',
        'attach__transcript',
        attachment.extract_status === 'pending' ? '未转写（语音识别未配置）' : '未转写'
      );
      stateText.dataset.state = 'pending';
      link.appendChild(stateText);
    }
    return link;
  }

  if (attachment.extract_status === 'pending') {
    link.appendChild(el('span', 'attach__state', '未转写'));
  }
  return link;
}

function openMessageMenu(x, y, message) {
  state.menuTarget = message;
  const menu = $('msgMenu');
  menu.hidden = false;
  menu.style.left = `${Math.min(x, window.innerWidth - 120)}px`;
  menu.style.top = `${Math.min(y, window.innerHeight - 130)}px`;
  menu.querySelector('[data-action="recall"]').hidden = !message.mine;
  menu.querySelector('[data-action="quote"]').hidden = message.sender_kind === 'system';
}

// 发出消息后 AI 可能在 1-3 秒内接话，短时间内加密轮询，让回复自己冒出来
function scheduleFastPoll() {
  for (const delay of [1200, 2600, 4200, 6500, 9000]) {
    setTimeout(() => {
      pollMessages();
    }, delay);
  }
}

async function pollMessages() {
  if (!state.groupId || document.hidden || !state.detail) return;
  try {
    const query = state.latestId ? `?after=${encodeURIComponent(state.latestId)}` : '?limit=80';
    const listed = await api(`/groups/${state.groupId}/messages${query}`);
    if (!listed.messages || !listed.messages.length) return;
    if (state.latestId) {
      // 增量续画：只 append 新来的，不重建已渲染的节点
      state.messages = [...state.messages, ...listed.messages];
      appendMessages(listed.messages);
    } else {
      state.messages = listed.messages;
      renderMessages();
    }
    state.latestId = listed.latest_message_id || state.latestId;
    state.unreadFromId = listed.unread_from_id || state.unreadFromId;
    reportRead();
  } catch {
    /* 轮询失败不打扰用户 */
  }
}

async function sendMessage(event) {
  event.preventDefault();
  if (!state.groupId) return;
  const draft = $('draft');
  const content = draft.value.trim();
  if (!content) return;
  const button = $('btnSend');
  button.disabled = true;
  try {
    const created = await api(`/groups/${state.groupId}/messages`, {
      method: 'POST',
      body: {
        kind: 'text',
        content,
        reply_to_message_id: state.quoted ? state.quoted.id : null,
      },
    });
    clearQuote();
    draft.value = '';
    draft.style.height = 'auto';
    $('btnSend').disabled = true;
    state.messages.push(created);
    state.latestId = created.id;
    renderMessages();
    // 服务端会在命中「问项目情况 / @AI」时自己让助手接话，这里只负责把它拉回来
    jumpToBottom($('stream'));
    $('composerHint').textContent = 'AI 正在看…';
    scheduleFastPoll();
    setTimeout(() => {
      if ($('composerHint').textContent === 'AI 正在看…') $('composerHint').textContent = '';
    }, 12000);
  } catch (error) {
    toast(error.message);
  } finally {
    // 发完输入框是空的，发送键应保持禁用（不能无条件启用）
    button.disabled = $('draft').value.trim().length === 0;
  }
}

async function uploadFiles(files) {
  if (!state.groupId || !files.length) return;
  for (const file of files) {
    const form = new FormData();
    form.append('file', file, file.name);
    try {
      const attachment = await api(`/groups/${state.groupId}/attachments`, {
        method: 'POST',
        body: form,
      });
      const created = await api(`/groups/${state.groupId}/messages`, {
        method: 'POST',
        body: {
          kind: attachment.kind,
          content: '',
          attachment_ids: [attachment.id],
        },
      });
      state.messages.push(created);
      state.latestId = created.id;
      scheduleFastPoll();
      if (attachment.kind === 'voice') {
        toast(
          attachment.extract_status === 'done'
            ? `已转写成文字：${(attachment.transcript || '').slice(0, 20)}…`
            : '语音已存档，但语音识别未配置，AI 读不到内容（见文档）'
        );
      }
    } catch (error) {
      toast(`${file.name}：${error.message}`);
    }
  }
  renderMessages();
}

// ---------------- 语音 ----------------

// 不同失败原因给不同动作，别一律让人去地址栏点图标。
const MIC_ERRORS = {
  NotAllowedError: {
    msg: "浏览器拒绝了麦克风权限。",
    steps: ["点地址栏左侧的锁 / 麦克风图标", "把「麦克风」改成「允许」", "回到这个页面点「重试」"],
    retry: true,
  },
  PermissionDeniedError: {
    msg: "浏览器拒绝了麦克风权限。",
    steps: ["点地址栏左侧的锁 / 麦克风图标", "把「麦克风」改成「允许」", "回到这个页面点「重试」"],
    retry: true,
  },
  NotFoundError: {
    msg: "没有检测到麦克风设备。",
    steps: ["确认耳机或麦克风已插好", "在系统声音设置里确认有输入设备", "接好后点「重试」"],
    retry: true,
  },
  DevicesNotFoundError: {
    msg: "没有检测到麦克风设备。",
    steps: ["确认耳机或麦克风已插好", "在系统声音设置里确认有输入设备", "接好后点「重试」"],
    retry: true,
  },
  NotReadableError: {
    msg: "麦克风被其他程序占用了。",
    steps: ["关掉正在录音的软件（会议、录音机等）", "再回到这个页面点「重试」"],
    retry: true,
  },
  SecurityError: {
    msg: "当前页面不是安全上下文，无法使用麦克风。",
    steps: ["用 https 或 localhost 打开这个页面"],
    retry: false,
  },
};

function showMicDialog({ msg, steps = [], retry = true }) {
  $('micReason').textContent = msg;
  const list = $('micSteps');
  list.innerHTML = '';
  for (const step of steps) {
    const item = document.createElement('li');
    item.textContent = step;
    list.appendChild(item);
  }
  list.hidden = steps.length === 0;
  $('btnMicRetry').hidden = !retry;
  if (!$('dialogMic').open) $('dialogMic').showModal();
}

async function requestMicStream() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    showMicDialog({
      msg: '这个浏览器不支持录音（缺少 getUserMedia）。',
      steps: ['换用 Chrome / Edge / Safari 的较新版本'],
      retry: false,
    });
    return null;
  }
  try {
    return await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    const key = error && (error.name || error.code);
    const known = MIC_ERRORS[key];
    if (known) showMicDialog(known);
    else showMicDialog({ msg: `无法使用麦克风（${key || '未知原因'}）。`, steps: [], retry: true });
    return null;
  }
}

const MAX_RECORD_SECONDS = 270; // 百炼 ASR 单次上限 5 分钟，留 30 秒余量

function stopRecording() {
  const rec = state.recording;
  if (!rec) return;
  state.recording = null;
  clearInterval(rec.timer);
  try {
    rec.processor.disconnect();
    rec.source.disconnect();
  } catch {
    /* 已断开 */
  }
  rec.stream.getTracks().forEach((track) => track.stop());
  const inputRate = rec.ctx.sampleRate;
  rec.ctx.close().catch(() => {});
  $('btnVoice').textContent = '按住说话';

  const collected = concatChunks(rec.chunks);
  if (!collected.length) {
    toast('没有录到声音');
    return;
  }
  // 统一重采样到 16 kHz 单声道，ASR 最稳的输入
  const samples = resampleTo(collected, inputRate, 16000);
  const blob = encodeWav(samples, 16000);
  const file = new File([blob], `语音-${Date.now()}.wav`, { type: 'audio/wav' });
  uploadFiles([file]);
}

async function toggleVoice() {
  const button = $('btnVoice');
  if (state.recording) {
    stopRecording();
    return;
  }
  const stream = await requestMicStream();
  if (!stream) return;

  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) {
    stream.getTracks().forEach((track) => track.stop());
    showMicDialog({
      msg: '这个浏览器缺少 WebAudio，无法编码录音。',
      steps: ['换用 Chrome / Edge / Safari 的较新版本'],
      retry: false,
    });
    return;
  }

  const ctx = new Ctx();
  const source = ctx.createMediaStreamSource(stream);
  // ScriptProcessor 虽已废弃但各浏览器仍在，AudioWorklet 需要额外模块文件。
  const processor = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  processor.onaudioprocess = (event) => {
    chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
  };
  source.connect(processor);
  processor.connect(ctx.destination);

  state.recording = { ctx, stream, source, processor, chunks, startedAt: Date.now(), timer: null };
  const started = Date.now();
  const render = () => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    button.textContent = `停止 ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  };
  render();
  state.recording.timer = setInterval(render, 500);
  setTimeout(() => {
    if (state.recording && Date.now() - started >= MAX_RECORD_SECONDS * 1000 - 500) {
      toast('已到 4 分 30 秒上限，自动结束（语音识别单次最多 5 分钟）');
      stopRecording();
    }
  }, MAX_RECORD_SECONDS * 1000);
}

// ---------------- AI 整理 ----------------

async function runAnalysis(silent = false) {
  if (!state.groupId) return;
  const button = $('btnAnalyze');
  button.disabled = true;
  $('composerHint').textContent = 'AI 正在读聊天…';
  try {
    const result = await api(`/groups/${state.groupId}:analyze`, { method: 'POST' });
    await selectGroup(state.groupId);
    await loadTodos();
    $('composerHint').textContent = '';
    if (!silent) {
      toast(`整理完成：新增 ${result.todos_created} 条待办`);
    }
  } catch (error) {
    $('composerHint').textContent = '';
    if (error.status === 503) {
      toast('还没配置对话模型端点，AI 暂时无法分析');
    } else {
      toast(error.message);
    }
  } finally {
    button.disabled = false;
  }
}


// ---------------- 外观 ----------------

const THEME_KEY = 'dnx-theme';
const THEME_MEDIA = window.matchMedia('(prefers-color-scheme: dark)');

function savedTheme() {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === 'light' || value === 'dark' ? value : 'auto';
  } catch (_) {
    return 'auto';
  }
}

function effectiveTheme(choice) {
  if (choice === 'light' || choice === 'dark') return choice;
  return THEME_MEDIA.matches ? 'dark' : 'light';
}

function applyTheme(choice) {
  const mode = effectiveTheme(choice);
  document.documentElement.dataset.theme = mode;
  for (const button of document.querySelectorAll('[data-theme-choice]')) {
    button.setAttribute('aria-checked', String(button.dataset.themeChoice === choice));
  }
}

function setTheme(choice) {
  try {
    if (choice === 'auto') localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, choice);
  } catch (_) {
    /* 隐私模式写不了就只影响本次 */
  }
  applyTheme(choice);
}

// ---------------- 待办 ----------------

/** 窄屏放不下长占位语，换成短的，避免撑出滚动条。 */
function syncComposerPlaceholder() {
  const box = $('draft');
  if (!box) return;
  const full = '说点什么…（问进度直接说，或 @AI）';
  const short = '说点什么…';
  box.placeholder = window.innerWidth < 520 ? short : full;
}

async function loadTodos() {
  try {
    state.todos = await api('/todos');
  } catch {
    state.todos = [];
  }
  renderTodos();
}


/**
 * 把 MES 链接的主机名对齐到当前页面的主机名。
 *
 * 后端拼链接用的是配置里的 MES_WATCH_UI_URL（比如 http://localhost:4173），
 * 但人可能是在 127.0.0.1:18091 上用。MES 的登录态存在 localStorage 里，
 * 是按源隔离的——换一个回环别名就等于没登录，点过去会被踢到登录页。
 * 只有两边都是回环地址时才替换，真实域名不动。
 */
function alignMesHost(url) {
  if (!url) return url;
  try {
    const link = new URL(url, location.href);
    const loopback = ['localhost', '127.0.0.1', '::1', '[::1]'];
    if (
      loopback.includes(location.hostname) &&
      loopback.includes(link.hostname) &&
      link.hostname !== location.hostname
    ) {
      link.hostname = location.hostname;
    }
    return link.toString();
  } catch (_) {
    return url;
  }
}

function renderTodos() {
  const list = $('todoList');
  list.innerHTML = '';
  const open = state.todos.filter((item) => item.status !== 'done' && item.status !== 'dismissed');
  $('todoCount').textContent = String(open.length);
  $('todoCountMobile').textContent = String(open.length);
  $('todosEmpty').hidden = state.todos.length > 0;
  // 默认只列未完成的；已完成折叠在下面，避免刷屏
  const finished = state.todos.filter((item) => item.status === 'done');
  const visible = state.todos.filter((item) => !finished.includes(item));
  const shown = state.showFinishedTodos ? state.todos : visible;

  for (const todo of shown) {
    const parsed = parseTodoDetail(todo);
    const row = el('li');
    const button = el('button', 'todo-row');
    button.type = 'button';
    if (todo.status === 'done') button.classList.add('is-done');
    button.dataset.todoId = todo.id;
    button.setAttribute('aria-label', `查看待办：${todo.title}`);

    const top = el('div', 'todo-row__top');
    top.appendChild(el('span', 'todo-row__title', todo.title));
    button.appendChild(top);

    const tags = el('div', 'todo-row__tags');
    const prio = el('span', 'tag', priorityLabel(todo.priority));
    if (todo.priority === 'high' || todo.priority === 'urgent') prio.dataset.tone = 'warn';
    else prio.dataset.tone = 'muted';
    tags.appendChild(prio);
    const status = el('span', 'tag', todoStatusLabel(todo.status));
    if (todo.status === 'doing') status.dataset.tone = 'accent';
    if (todo.status === 'done') status.dataset.tone = 'muted';
    tags.appendChild(status);
    if (parsed.items.length > 1) tags.appendChild(el('span', 'tag', `${parsed.items.length} 项明细`));
    if (todo.nudge_count) tags.appendChild(el('span', 'tag', `催过 ${todo.nudge_count} 次`));
    tags.appendChild(el('span', 'todo-row__time', timeAgo(todo.created_at)));
    button.appendChild(tags);

    button.addEventListener('click', () => openTodoDrawer(todo));
    row.appendChild(button);
    list.appendChild(row);
  }

  if (finished.length) {
    const more = el(
      'button',
      'todo-more',
      state.showFinishedTodos ? '收起已完成' : `显示已完成（${finished.length}）`
    );
    more.type = 'button';
    more.addEventListener('click', () => {
      state.showFinishedTodos = !state.showFinishedTodos;
      renderTodos();
    });
    list.appendChild(more);
  }
}

const TODO_STATUS_LABEL = {
  pending: '待处理',
  accepted: '已接受',
  doing: '进行中',
  done: '已完成',
  dismissed: '已取消',
};

function todoStatusLabel(status) {
  return TODO_STATUS_LABEL[status] || status;
}

function timeAgo(iso) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return '';
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 1) return '刚刚';
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 31) return `${days} 天前`;
  return new Date(then).toLocaleDateString('zh-CN');
}

function formatMoment(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/**
 * 待办明细的结构化：AI 写下来的格式是
 *   「主说明 / （语音派发，来自「某某」）/ · 子项（补充说明）」
 * 直接在卡片里当一段文字渲染就成了一坨，抽屉里要拆成说明 + 有序子项。
 */
function parseTodoDetail(todo) {
  const raw = String(todo.detail || '');
  const lines = raw.split(/\r?\n/);
  const summary = [];
  const items = [];
  const notes = [];

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;
    if (/^（语音派发/.test(line)) {
      notes.push(line.replace(/^（|）$/g, ''));
      continue;
    }
    if (line.startsWith('·')) {
      const text = line.replace(/^·\s*/, '').trim();
      if (text) items.push(splitNote(text));
      continue;
    }
    summary.push(line);
  }

  // 没有子项时，如果正文和标题一模一样就不重复显示
  const summaryText = summary.join('\n').trim();
  const sameAsTitle = summaryText && summaryText === String(todo.title || '').trim();
  return {
    summary: sameAsTitle ? '' : summaryText,
    items: items.map((item) => (item.title === todo.title ? { ...item, title: '' } : item)),
    notes,
  };
}

/** 「确认参观路线（绕开折弯机）」→ 标题 + 补充说明 */
function splitNote(text) {
  const match = text.match(/^(.*?)[（(]([^（）()]{4,})[）)]$/);
  if (!match) return { title: text, note: '' };
  return { title: match[1].trim(), note: match[2].trim() };
}

function closeTodoDrawer() {
  const node = document.getElementById('todoDrawer');
  if (node) node.remove();
  document.removeEventListener('keydown', onTodoDrawerKey);
  for (const row of document.querySelectorAll('.todo-row[aria-current="true"]')) {
    row.removeAttribute('aria-current');
  }
}

function onTodoDrawerKey(event) {
  if (event.key === 'Escape') closeTodoDrawer();
}

function openTodoDrawer(todo) {
  closeTodoDrawer();
  const parsed = parseTodoDetail(todo);

  for (const row of document.querySelectorAll('.todo-row')) {
    if (row.dataset.todoId === todo.id) row.setAttribute('aria-current', 'true');
  }

  const root = el('div', 'todo-drawer');
  root.id = 'todoDrawer';

  const mask = el('div', 'todo-drawer__mask');
  mask.addEventListener('click', closeTodoDrawer);
  root.appendChild(mask);

  const panel = el('div', 'todo-drawer__panel');
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'true');
  panel.setAttribute('aria-label', todo.title);

  // 头部
  const head = el('div', 'todo-drawer__head');
  const heading = el('div', 'todo-drawer__heading');
  const title = el('h2', 'todo-drawer__title', todo.title);
  const subtags = el('div', 'todo-drawer__subtags');
  const prioTag = el('span', 'tag', priorityLabel(todo.priority));
  prioTag.dataset.tone = todo.priority === 'high' || todo.priority === 'urgent' ? 'warn' : 'muted';
  subtags.appendChild(prioTag);
  const statusTag = el('span', 'tag', todoStatusLabel(todo.status));
  if (todo.status === 'doing') statusTag.dataset.tone = 'accent';
  subtags.appendChild(statusTag);
  if (todo.origin === 'ai') subtags.appendChild(el('span', 'tag', 'AI 整理'));
  heading.append(title, subtags);
  const close = el('button', 'todo-drawer__close', '×');
  close.type = 'button';
  close.setAttribute('aria-label', '关闭');
  close.addEventListener('click', closeTodoDrawer);
  head.append(heading, close);

  // 内容
  const body = el('div', 'todo-drawer__body');

  const desc = el('div', 'desc');
  const rows = [
    ['指派给', todo.assignee_display_name || '本人'],
    ['来源', todo.group_name || 'AI 调度台'],
    ['创建时间', formatMoment(todo.created_at)],
    ['截止时间', todo.due_at ? formatMoment(todo.due_at) : '未设置'],
  ];
  if (todo.nudge_count) rows.push(['催办', `已催 ${todo.nudge_count} 次${todo.nudged_at ? `（${timeAgo(todo.nudged_at)}）` : ''}`]);
  if (todo.escalated_at) rows.push(['升级', `${timeAgo(todo.escalated_at)}已升级给派活人`]);
  if (todo.status === 'done') rows.push(['完成时间', formatMoment(todo.updated_at || todo.created_at)]);
  for (const note of parsed.notes) rows.push(['备注', note]);
  for (const [label, value] of rows) {
    const row = el('div', 'desc__row');
    row.append(el('div', 'desc__label', label), el('div', 'desc__value', value));
    desc.appendChild(row);
  }
  body.appendChild(desc);

  if (parsed.summary) {
    const section = el('div');
    section.appendChild(el('p', 'panel-label', '说明'));
    section.appendChild(el('p', 'todo-summary', parsed.summary));
    body.appendChild(section);
  }

  if (parsed.items.length) {
    const section = el('div');
    section.appendChild(el('p', 'panel-label', `要做的事（${parsed.items.length}）`));
    const list = el('ol', 'todo-items');
    parsed.items.forEach((item, index) => {
      const li = el('li');
      li.appendChild(el('span', 'no', String(index + 1)));
      const content = el('div');
      if (item.title) content.appendChild(el('div', null, item.title));
      if (item.note) content.appendChild(el('span', 'note', item.note));
      li.appendChild(content);
      list.appendChild(li);
    });
    section.appendChild(list);
    body.appendChild(section);
  }

  // 关联单据：直接跳去 MES 处理
  const refs = Array.isArray(todo.refs) ? todo.refs : [];
  if (refs.length) {
    const section = el('div');
    section.appendChild(el('p', 'panel-label', `关联单据（${refs.length}）`));
    const list = el('div', 'todo-refs');
    for (const ref of refs) {
      const link = el('a', 'todo-ref');
      link.href = alignMesHost(ref.url);
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.appendChild(el('span', 'todo-ref__label', ref.label || ref.panel || '单据'));
      link.appendChild(el('span', 'todo-ref__no', ref.docNo));
      link.appendChild(el('span', 'todo-ref__go', '去处理 ↗'));
      list.appendChild(link);
    }
    section.appendChild(list);
    body.appendChild(section);
  }

  // 底部操作：一个主按钮
  const foot = el('div', 'todo-drawer__foot');
  if (todo.status !== 'done' && todo.status !== 'dismissed') {
    const dismiss = el('button', 'btn--text-danger', '不处理');
    dismiss.type = 'button';
    dismiss.addEventListener('click', async () => {
      await setTodoStatus(todo.id, 'dismissed');
      closeTodoDrawer();
    });
    foot.appendChild(dismiss);
    if (todo.status === 'pending') {
      const start = el('button', 'btn btn--ghost', '开始处理');
      start.type = 'button';
      start.addEventListener('click', async () => {
        await setTodoStatus(todo.id, 'doing');
        closeTodoDrawer();
      });
      foot.appendChild(start);
    }
    const done = el('button', 'btn btn--primary', '完成');
    done.type = 'button';
    done.addEventListener('click', async () => {
      await setTodoStatus(todo.id, 'done');
      closeTodoDrawer();
    });
    foot.appendChild(done);
  }
  if (foot.children.length) panel.append(head, body, foot);
  else panel.append(head, body);

  root.appendChild(panel);
  document.body.appendChild(root);
  document.addEventListener('keydown', onTodoDrawerKey);
  close.focus();
}

function priorityLabel(priority) {
  return { low: '低', normal: '普通', high: '重要', urgent: '紧急' }[priority] || '普通';
}

async function setTodoStatus(todoId, status) {
  try {
    await api(`/todos/${todoId}`, { method: 'PATCH', body: { status } });
    await loadTodos();
  } catch (error) {
    toast(error.message);
  }
}

// ---------------- MES 主动事件 ----------------

const SEVERITY_LABEL = { info: '提示', warning: '需要处理', critical: '紧急' };
const KIND_LABEL = {
  so_unstarted: '销售订单未排产',
  so_draft: '销售订单草稿',
  so_shipping_overdue: '销售订单超期未出库',
  manu_due: '生产工单临期',
  manu_no_report: '生产工单无报工',
  stock_low: '库存低于下限',
  pu_draft: '采购订单草稿',
  pu_unshipped: '采购订单未发货',
  arrival_uninspected: '到货未检验',
  finish_inspect_overdue: '完工报检无结果',
  pending_approval: '待审批',
};

function ageText(hours) {
  if (!hours || hours < 1) return '刚刚';
  if (hours < 24) return `${Math.floor(hours)} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

async function loadEvents({ silent = true } = {}) {
  let events = [];
  const params = new URLSearchParams({ status: 'all', limit: '80' });
  if (state.ackedFilter && state.ackedFilter !== 'any') {
    params.set('acked', state.ackedFilter);
  }
  if (state.assigneeFilter) params.set('assignee', state.assigneeFilter);
  try {
    events = await api(`/assistant/events?${params.toString()}`);
    const facetQuery = new URLSearchParams({ status: 'all' });
    if (state.ackedFilter && state.ackedFilter !== 'any') {
      facetQuery.set('acked', state.ackedFilter);
    }
    state.facets = await api(`/assistant/facets?${facetQuery.toString()}`);
  } catch {
    return;
  }
  state.events = events;
  const openCount = events.filter((e) => e.status === 'open').length;
  $('bellCount').textContent = String(openCount);
  $('bellCount').hidden = openCount === 0;

  // 首次加载只记下已有事件，避免一进页面就弹一堆历史提醒
  if (!state.eventsPrimed) {
    events.forEach((item) => state.seenEventIds.add(item.id));
    state.eventsPrimed = true;
    return;
  }
  const fresh = events.filter(
    (item) => item.status === 'open' && !state.seenEventIds.has(item.id)
  );
  for (const item of fresh) state.seenEventIds.add(item.id);
  if (!fresh.length) return;

  toast(`AI 提示：${fresh[0].title}`);
  const urgent = fresh.some((item) => item.severity === 'warning' || item.severity === 'critical');
  // 只有「需要处理/紧急」才主动弹窗，普通提示只亮铃铛，避免打断输入
  if (urgent && !silent && !$('dialogEvents').open && !state.recording) {
    renderEvents();
    openSheet($('dialogEvents'));
  }
}

function renderFilters() {
  const facets = state.facets || {};
  const counts = {
    any: facets.total || 0,
    none: facets.unacked || 0,
    me: facets.acked_by_me || 0,
    others: facets.acked_by_others || 0,
  };
  document.querySelectorAll('#ackedFilters .chip').forEach((chip) => {
    const key = chip.dataset.acked;
    chip.classList.toggle('is-on', key === state.ackedFilter);
    const badge = chip.querySelector('.chip__n');
    if (badge) badge.textContent = counts[key] ? String(counts[key]) : '';
  });

  // 按人筛选：只在有数据时显示
  const row = $('assigneeFilters');
  row.innerHTML = '';
  const byAssignee = facets.by_assignee || {};
  const names = Object.keys(byAssignee);
  if (!names.length) return;
  const addChip = (label, value, count) => {
    const chip = el('button', 'chip', label);
    chip.type = 'button';
    chip.dataset.assignee = value;
    if (state.assigneeFilter === value) chip.classList.add('is-on');
    if (count) chip.appendChild(el('span', 'chip__n', String(count)));
    chip.addEventListener('click', async () => {
      state.assigneeFilter = state.assigneeFilter === value ? '' : value;
      await loadEvents({ silent: false });
      renderFilters();
      renderEvents();
    });
    row.appendChild(chip);
  };
  addChip('所有人', '', 0);
  if (byAssignee[((state.me && state.me.username) || '')] !== undefined) {
    addChip('派给我的', 'me', byAssignee[state.me.username]);
  }
  names.forEach((name) => addChip(`@${name}`, name, byAssignee[name]));
}

function renderEvents() {
  const list = $('eventList');
  list.innerHTML = '';
  $('eventsEmpty').hidden = state.events.length > 0;

  if (!state.events.length) {
    $('eventsEmpty').hidden = false;
    $('eventsEmpty').textContent = state.ackedFilter === 'any' && !state.assigneeFilter
      ? '暂时没有需要你处理的事。'
      : '当前筛选条件下没有事件。';
    return;
  }
  $('eventsEmpty').hidden = true;

  // 按严重度排序：紧急在前，避免重要的事被淹没
  const order = { critical: 0, warning: 1, info: 2 };
  const sorted = [...state.events].sort(
    (a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9)
  );

  for (const event of sorted) {
    const item = el('li', 'event-item');
    item.dataset.sev = event.severity;

    const head = el('div', 'event-item__head');
    head.appendChild(el('span', 'event-item__kind', KIND_LABEL[event.kind] || event.kind));
    if (event.escalated) head.appendChild(el('span', 'event-item__flag', '已升级'));
    if (event.reminders > 0) {
      head.appendChild(el('span', 'event-item__flag', `提醒 ${event.reminders} 次`));
    }
    head.appendChild(el('span', 'event-item__age', ageText(event.age_hours)));
    item.appendChild(head);

    item.appendChild(el('div', 'event-item__title', event.title));
    if (event.detail) item.appendChild(el('div', 'event-item__detail', event.detail));
    if (event.suggested_action) {
      item.appendChild(el('div', 'event-item__detail', `建议：${event.suggested_action}`));
    }

    if (event.note) {
      item.appendChild(el('div', 'event-item__note', `备注：${event.note}`));
    }
    if (event.acked_by) {
      item.appendChild(el('div', 'event-item__note', `${event.acked_by} 已认领`));
    }

    const actions = el('div', 'event-item__actions');
    if (event.monday_url) {
      const open = el('a', 'btn btn--ghost', '去 MES 处理');
      open.href = alignMesHost(event.monday_url);
      open.target = '_blank';
      open.rel = 'noopener';
      actions.appendChild(open);
    }
    if (event.status === 'handled') {
      const back = el('button', 'btn btn--ghost', '撤回认领');
      back.type = 'button';
      back.addEventListener('click', () => reopenEvent(event.id));
      actions.appendChild(back);
    } else {
      const ack = el('button', 'btn btn--ghost', '我认领了');
      ack.type = 'button';
      ack.addEventListener('click', () => ackEvent(event.id, item, event));
      actions.appendChild(ack);
    }
    item.appendChild(actions);
    list.appendChild(item);
  }
}

async function loadStats() {
  try {
    const stats = await api('/assistant/stats');
    const parts = Object.entries(stats.by_severity || {})
      .map(([k, v]) => `${SEVERITY_LABEL[k] || k} ${v}`);
    $('eventsSummary').textContent = stats.open
      ? `共 ${stats.open} 条待处理${parts.length ? '：' + parts.join(' · ') : ''}${
          stats.stale_over_24h ? ` · ${stats.stale_over_24h} 条超过 24 小时` : ''
        }`
      : '';
  } catch {
    $('eventsSummary').textContent = '';
  }
}

async function ackEvent(eventId, itemEl, event) {
  // 第一次点展开备注框，第二次提交；避免弹窗打断
  if (itemEl && !itemEl.querySelector('.event-item__note-input')) {
    const box = el('div', 'event-item__note-input');
    const input = document.createElement('input');
    input.type = 'text';
    input.maxLength = 200;
    input.placeholder = '处理说明（可留空），回车提交';
    const submit = el('button', 'btn btn--primary', '确认');
    submit.type = 'button';
    const send = async () => {
      try {
        await api(`/assistant/events/${eventId}:ack`, {
          method: 'POST',
          body: { note: input.value.trim() },
        });
        await loadEvents({ silent: false });
        renderFilters();
        renderEvents();
        await loadTodos();
        toast('已认领');
      } catch (error) {
        toast(error.message);
      }
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.isComposing) {
        e.preventDefault();
        send();
      }
      if (e.key === 'Escape') box.remove();
    });
    submit.addEventListener('click', send);
    box.appendChild(input);
    box.appendChild(submit);
    itemEl.appendChild(box);
    input.focus();
    return;
  }
}

async function reopenEvent(eventId) {
  try {
    await api(`/assistant/events/${eventId}:reopen`, { method: 'POST' });
    await loadEvents({ silent: false });
    renderFilters();
    renderEvents();
    await loadTodos();
    toast('已撤回认领');
  } catch (error) {
    toast(error.message);
  }
}

function relativeSeconds(seconds) {
  if (seconds === null || seconds === undefined) return '未知';
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

async function loadHealth() {
  let health = null;
  let sla = null;
  try {
    [health, sla] = await Promise.all([
      api('/assistant/health?hours=24'),
      api('/assistant/sla?days=7'),
    ]);
  } catch {
    $('watchHealth').hidden = true;
    return;
  }
  const box = $('watchHealth');
  box.hidden = false;

  const dot = $('healthDot');
  let state = 'ok';
  let text = '';
  if (!health.runs) {
    state = 'bad';
    text = '还没有巡查记录';
  } else if (health.stale) {
    state = 'bad';
    text = `巡查可能已停：上次 ${relativeSeconds(health.seconds_since_last)}（间隔 ${Math.round(health.interval_seconds)} 秒）`;
  } else if (health.failed > 0) {
    state = 'warn';
    text = `近 ${health.runs} 轮成功 ${health.ok} 次、失败 ${health.failed} 次`;
    if (health.last_error) text += `，最近错误：${health.last_error.slice(0, 40)}`;
  } else {
    text = `巡查正常：近 ${health.runs} 轮全部成功，上次 ${relativeSeconds(health.seconds_since_last)}`;
  }
  if (health.avg_duration_ms) text += `，平均耗时 ${health.avg_duration_ms} ms`;
  dot.dataset.state = state;
  $('healthText').textContent = text;

  const parts = [];
  if (sla.avg_ack_hours !== null && sla.avg_ack_hours !== undefined) {
    parts.push(`平均 ${sla.avg_ack_hours} 小时认领`);
  }
  if (sla.ack_within_4h !== null && sla.ack_within_4h !== undefined) {
    parts.push(`${Math.round(sla.ack_within_4h * 100)}% 在 4 小时内`);
  }
  if (sla.open_over_24h) parts.push(`${sla.open_over_24h} 条超过 24 小时未处理`);
  $('healthSla').textContent = parts.join(' · ');
}

/**
 * 待办弹层：类 macOS 窗口（可拖动 / 可缩放 / 可固定 / 双击标题栏最大化）。
 *
 * 为什么不是 showModal()：模态会吞掉页面交互、还带一层遮罩，和「像页面一样
 * 随手拖到旁边边看边操作」冲突。这里用非模态 show()，位置与尺寸自己管，
 * 关掉只走动画（原生 dialog 一 close() 就 display:none，看不到收起过程）。
 */
const SHEET_ANIM_MS = 360;
const SHEET_KEY = "group.eventsSheet";
const SHEET_MIN = { w: 420, h: 340 };

function sheetSaved() {
  try {
    return JSON.parse(localStorage.getItem(SHEET_KEY) || "null") || {};
  } catch {
    return {};
  }
}

function sheetSave(patch) {
  const next = { ...sheetSaved(), ...patch };
  localStorage.setItem(SHEET_KEY, JSON.stringify(next));
  return next;
}

/** 默认落在右上角（铃铛那一侧），留出顶栏高度。 */
function sheetDefaultRect() {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  // 手机上不做浮窗：铺满可视宽度，留出顶栏和底部安全边
  if (vw < 640) {
    return { left: 8, top: 56, width: vw - 16, height: vh - 72 };
  }
  const width = Math.min(560, vw - 32);
  const height = Math.min(760, vh - 120);
  return { left: Math.max(16, vw - width - 24), top: 64, width, height };
}

/**
 * 夹在视口内：整窗（含右下角的缩放手柄）必须可见。
 * 只夹左上角是不够的——拖动/缩放后右下角跑出屏幕，手柄就点不到了。
 */
function sheetClampRect(rect) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  // 视口比最小尺寸还窄时（手机），最小尺寸跟着视口让路，不能撑出屏幕
  const maxW = Math.max(240, vw - 16);
  const maxH = Math.max(240, vh - 16);
  const width = Math.max(Math.min(SHEET_MIN.w, maxW), Math.min(Math.round(rect.width), maxW));
  const height = Math.max(Math.min(SHEET_MIN.h, maxH), Math.min(Math.round(rect.height), maxH));
  const maxLeft = Math.max(8, vw - width - 8);
  const maxTop = Math.max(8, vh - height - 8);
  const left = Math.min(Math.max(rect.left, 8), maxLeft);
  const top = Math.min(Math.max(rect.top, 8), maxTop);
  return { left: Math.round(left), top: Math.round(top), width, height };
}

function sheetRectOf(dialog = $("dialogEvents")) {
  const r = dialog.getBoundingClientRect();
  return { left: r.left, top: r.top, width: r.width, height: r.height };
}

function sheetApplyRect(rect) {
  const dialog = $("dialogEvents");
  const safe = sheetClampRect(rect);
  dialog.style.left = `${safe.left}px`;
  dialog.style.top = `${safe.top}px`;
  dialog.style.width = `${safe.width}px`;
  dialog.style.height = `${safe.height}px`;
  return safe;
}

function sheetPinned() {
  return sheetSaved().pinned === true;
}

function sheetSetPinned(on) {
  sheetSave({ pinned: on });
  const button = $("btnEventsPin");
  if (!button) return;
  button.setAttribute("aria-pressed", String(on));
  button.title = on ? "已固定：点页面别处不收起" : "固定窗口（点页面别处不收起）";
}

function openSheet(dialog) {
  if (!dialog || dialog.open) return;
  const saved = sheetSaved();
  // 手机直接铺满，桌面才沿用上次拖好的位置
  const geom = saved.width && window.innerWidth >= 640 ? saved : sheetDefaultRect();
  dialog.classList.remove("is-closing");
  sheetApplyRect(geom);           // 先定位再显示，避免从屏幕中间跳过来
  dialog.show();                   // 非模态：页面还能点
  dialog.classList.add("is-opening");
  sheetSetPinned(saved.pinned === true);
  const settle = () => {
    dialog.classList.remove("is-opening");
    dialog.removeEventListener("animationend", settle);
  };
  dialog.addEventListener("animationend", settle);
  setTimeout(settle, SHEET_ANIM_MS + 120);
  // 侧栏「待办」按钮和角落的关闭按钮共用同一套关闭逻辑
}

function closeSheet(dialog) {
  if (!dialog || !dialog.open || dialog.classList.contains("is-closing")) return;
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    dialog.removeEventListener("animationend", finish);
    dialog.classList.remove("is-closing");
    if (dialog.open) dialog.close();
  };
  dialog.classList.add("is-closing");
  dialog.addEventListener("animationend", finish);
  setTimeout(finish, SHEET_ANIM_MS);
}

/** 拖动标题栏移动窗口。 */
function sheetBeginDrag(event) {
  if (event.button !== 0) return;
  const dialog = $("dialogEvents");
  const start = sheetRectOf(dialog);
  const origin = { x: event.clientX, y: event.clientY };
  document.body.classList.add("sheet-dragging");
  const move = (ev) => {
    sheetApplyRect({
      ...start,
      left: start.left + ev.clientX - origin.x,
      top: start.top + ev.clientY - origin.y,
    });
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    document.body.classList.remove("sheet-dragging");
    const rect = sheetRectOf(dialog);
    sheetSave({ ...rect, maximized: false });
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

/** 八向缩放（macOS 窗口的四边四角）。 */
function sheetBeginResize(event, dir) {
  if (event.button !== 0) return;
  event.preventDefault();
  event.stopPropagation();
  const dialog = $("dialogEvents");
  const start = sheetRectOf(dialog);
  const origin = { x: event.clientX, y: event.clientY };
  document.body.classList.add("sheet-resizing");
  const move = (ev) => {
    const dx = ev.clientX - origin.x;
    const dy = ev.clientY - origin.y;
    let left = start.left;
    let top = start.top;
    let width = start.width;
    let height = start.height;
    if (dir.includes("e")) width = start.width + dx;
    if (dir.includes("s")) height = start.height + dy;
    if (dir.includes("w")) width = start.width - dx;
    if (dir.includes("n")) height = start.height - dy;
    const applied = sheetApplyRect({ left, top, width, height });
    // 往左/上拉时，被夹住的那一段要回推左上角，否则窗口会自己漂移
    if (dir.includes("w")) left = start.left + start.width - applied.width;
    if (dir.includes("n")) top = start.top + start.height - applied.height;
    if (dir.includes("w") || dir.includes("n")) sheetApplyRect({ left, top, width: applied.width, height: applied.height });
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    document.body.classList.remove("sheet-resizing");
    sheetSave({ ...sheetRectOf(dialog), maximized: false });
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

/** 双击标题栏：最大化 / 还原（macOS 的 zoom）。 */
function sheetToggleMaximize() {
  const dialog = $("dialogEvents");
  const saved = sheetSaved();
  if (saved.maximized) {
    sheetApplyRect(saved.restore || sheetDefaultRect());
    sheetSave({ maximized: false });
    return;
  }
  const restore = sheetRectOf(dialog);
  sheetSave({ maximized: true, restore });
  sheetApplyRect({ left: 8, top: 8, width: window.innerWidth - 16, height: window.innerHeight - 16 });
}

/** 弹层的窗口行为：拖标题栏、八向缩放、固定、双击最大化、点外部收起。 */
function bindSheetWindow() {
  const dialog = $('dialogEvents');
  const bar = $('eventsBar');
  const pin = $('btnEventsPin');
  const closeTop = $('btnEventsClose');

  closeTop.addEventListener('click', () => closeSheet(dialog));

  pin.addEventListener('click', () => sheetSetPinned(!sheetPinned()));

  bar.addEventListener('pointerdown', (event) => {
    // 标题栏上的按钮不触发拖动
    if (event.target.closest('button')) return;
    sheetBeginDrag(event);
  });
  bar.addEventListener('dblclick', (event) => {
    if (event.target.closest('button')) return;
    sheetToggleMaximize();
  });

  for (const handle of dialog.querySelectorAll('.sheet__resize')) {
    handle.addEventListener('pointerdown', (event) => sheetBeginResize(event, handle.dataset.resize || 'se'));
  }

  // 点页面别处收起；固定住（图钉按下）时不动
  document.addEventListener('pointerdown', (event) => {
    if (!dialog.open || sheetPinned()) return;
    if (dialog.contains(event.target)) return;
    if (event.target.closest('#btnBell')) return;
    closeSheet(dialog);
  });

  // 非模态 dialog 不触发 cancel，Esc 自己接
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !dialog.open) return;
    event.preventDefault();
    closeSheet(dialog);
  });

  // 视口变小（旋转 / 分屏）时把窗口夹回可见区
  window.addEventListener('resize', () => {
    if (dialog.open) sheetApplyRect(sheetRectOf(dialog));
  });
}

async function openEvents() {
  await loadEvents({ silent: false });
  await loadHealth();
  renderFilters();
  renderEvents();
  loadStats();
  openSheet($('dialogEvents'));
}

// ---------------- 表情 / @ / 右键菜单 / 已读 ----------------

const EMOJIS = ['😀','😄','😁','😆','😅','😂','🙂','🙃','😉','😊','😍','😘','😜','🤔','😐','😑',
  '😴','😷','🤒','😎','😢','😭','😡','👍','👎','👌','🙏','💪','👏','✅','❌','⚠️',
  '📌','📎','📄','📊','📈','🗂','📝','🔧','⚙️','🔍','⏰','📅','🚚','🏭','💰','🔔'];

function buildEmojiPanel() {
  const panel = $('emojiPanel');
  panel.innerHTML = '';
  for (const emoji of EMOJIS) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = emoji;
    button.addEventListener('click', () => {
      const draft = $('draft');
      const start = draft.selectionStart ?? draft.value.length;
      draft.value = draft.value.slice(0, start) + emoji + draft.value.slice(draft.selectionEnd ?? start);
      draft.focus();
      draft.selectionStart = draft.selectionEnd = start + emoji.length;
      draft.dispatchEvent(new Event('input'));
    });
    panel.appendChild(button);
  }
}

function toggleEmoji() {
  const panel = $('emojiPanel');
  panel.hidden = !panel.hidden;
  if (!panel.hidden) $('mentionPanel').hidden = true;
}

function bindMentionPicker() {
  const draft = $('draft');
  const panel = $('mentionPanel');
  const render = (keyword) => {
    const members = (state.detail && state.detail.members) || [];
    const matched = members.filter((m) => !keyword || m.display_name.includes(keyword));
    panel.innerHTML = '';
    for (const member of matched.slice(0, 8)) {
      const button = document.createElement('button');
      button.type = 'button';
      button.innerHTML = '';
      button.appendChild(avatarNode('msg-avatar', member.display_name, member.avatar_url));
      button.appendChild(document.createTextNode(member.display_name));
      button.addEventListener('click', () => {
        const value = draft.value;
        const at = value.lastIndexOf('@');
        draft.value = `${value.slice(0, at)}@${member.display_name} ${value.slice(draft.selectionStart ?? value.length)}`;
        panel.hidden = true;
        draft.focus();
        draft.dispatchEvent(new Event('input'));
      });
      panel.appendChild(button);
    }
    if (!matched.length) {
      const none = document.createElement('button');
      none.type = 'button';
      none.textContent = '没有匹配的成员';
      none.disabled = true;
      panel.appendChild(none);
    }
  };

  draft.addEventListener('input', () => {
    const before = draft.value.slice(0, draft.selectionStart ?? 0);
    const at = before.lastIndexOf('@');
    if (at >= 0 && !/\s/.test(before.slice(at + 1))) {
      panel.hidden = false;
      $('emojiPanel').hidden = true;
      render(before.slice(at + 1));
    } else {
      panel.hidden = true;
    }
  });
  draft.addEventListener('blur', () => setTimeout(() => { panel.hidden = true; }, 150));
}

function openQuote(message) {
  state.quoted = message;
  const bar = $('quoteBar');
  bar.hidden = false;
  bar.querySelector('.quote-bar__text').textContent =
    `引用 ${message.sender_name}：${(message.content || '').slice(0, 48)}`;
  $('draft').focus();
}

function clearQuote() {
  state.quoted = null;
  $('quoteBar').hidden = true;
}

async function recallMessage(message) {
  try {
    await api(`/groups/${state.groupId}/messages/${message.id}`, { method: 'DELETE' });
    await selectGroup(state.groupId);
  } catch (error) {
    toast(error.message);
  }
}

function bindComposerExtras() {
  // 表情/@/引用条挂在输入区里，absolute 定位才会相对输入区，不会飘到别的栏上
  const composer = $('composer');
  for (const node of [$('emojiPanel'), $('mentionPanel'), $('quoteBar')]) {
    if (node.parentElement !== composer) composer.appendChild(node);
  }
  buildEmojiPanel();
  bindMentionPicker();
  $('btnEmoji').addEventListener('click', toggleEmoji);
  $('btnQuoteCancel').addEventListener('click', clearQuote);
  $('btnImageClose').addEventListener('click', () => $('dialogImage').close());

  $('msgMenu').addEventListener('click', async (event) => {
    const action = event.target.dataset.action;
    const message = state.menuTarget;
    $('msgMenu').hidden = true;
    if (!action || !message) return;
    if (action === 'copy') {
      try {
        await navigator.clipboard.writeText(message.content || '');
        toast('已复制');
      } catch {
        toast('复制失败');
      }
    } else if (action === 'quote') {
      openQuote(message);
    } else if (action === 'recall') {
      await recallMessage(message);
    }
  });

  document.addEventListener('click', (event) => {
    if (!$('msgMenu').contains(event.target)) $('msgMenu').hidden = true;
    // 点的是按钮本身或其内部图标时不要立刻关掉（图标是 SVG，target 不是按钮）
    if (!$('emojiPanel').contains(event.target) && !event.target.closest('#btnEmoji')) {
      $('emojiPanel').hidden = true;
    }
  });
  document.addEventListener('scroll', () => { $('msgMenu').hidden = true; }, true);
}

// 看到最后就上报已读
let lastReportedReadId = null;
async function reportRead() {
  if (!state.groupId || !state.latestId) return;
  if (lastReportedReadId === state.latestId) return;
  const stream = $('stream');
  const atBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 64;
  if (!atBottom) return;
  lastReportedReadId = state.latestId;
  try {
    await api(`/groups/${state.groupId}:read`, {
      method: 'POST',
      body: { message_id: state.latestId },
    });
    state.unreadFromId = null;
  } catch {
    lastReportedReadId = null;
  }
}

// ---------------- 员工知识库（仅管理员） ----------------

const STAFF_STATS = [
  ['active_days', '活跃天数', '天'],
  ['chat_sessions', '个人会话', '个'],
  ['chat_messages', '会话消息', '条'],
  ['group_messages', '群聊发言', '条'],
  ['todo_assigned', '接收待办', '条'],
  ['todo_completed', '完成待办', '条'],
  ['events_acked', '认领派单', '次'],
  ['actions_total', '操作次数', '次'],
];


// ---------------- 账号菜单 ----------------

function setUserMenu(open) {
  const panel = $('userMenuPanel');
  const trigger = $('btnUserMenu');
  if (!panel || !trigger) return;
  panel.hidden = !open;
  trigger.setAttribute('aria-expanded', String(open));
  if (open) syncDisbandEntry();
}

function syncDisbandEntry() {
  const button = $('btnDisband');
  const sep = $('userMenuSep');
  if (!button) return;
  const group = state.detail;
  const canDisband = !!(group && (group.my_role === 'owner' || isAdmin()));
  button.hidden = !canDisband;
  if (sep) sep.hidden = !canDisband;
}

function openDisbandDialog() {
  const group = state.detail;
  if (!group) return;
  const dialog = $('dialogDisband');
  $('disbandName').textContent = group.name;
  setUserMenu(false);
  if (dialog.showModal) dialog.showModal();
  else dialog.setAttribute('open', '');
}


/** 对当前群（通常是已解散的）手动跑一次经验提炼。 */
async function distillCurrentGroup() {
  const group = state.detail;
  if (!group) return;
  const button = $('btnDistill');
  if (button) {
    button.disabled = true;
    button.textContent = '提炼中…';
  }
  try {
    const result = await api(`/groups/${group.id}:distill`, { method: 'POST' });
    const created = result && result.created ? result.created : 0;
    toast(created ? `提炼出 ${created} 条经验，已进员工知识库` : '这次没有提炼出新的经验');
  } catch (error) {
    toast(error.message);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = '提炼经验';
    }
  }
}

async function disbandGroup() {
  const group = state.detail;
  if (!group) return;
  const button = $('btnDisbandConfirm');
  button.disabled = true;
  try {
    await api(`/groups/${group.id}:dissolve`, { method: 'POST' });
    $('dialogDisband').close();
    state.groupId = '';
    state.detail = null;
    await loadGroups();
    const first = state.groups[0];
    if (first) await selectGroup(first.id);
    toast('群聊已解散，记录已保留，AI 会从中提炼经验');
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

function isAdmin() {
  return !!(state.me && state.me.role === 'admin');
}

function renderStaffList() {
  const list = $('staffList');
  list.innerHTML = '';
  const items = state.staffTab === 'profiles'
    ? state.staffProfiles
    : state.staffTab === 'experiences' ? state.staffExperiences : state.staffPromotions;
  $('staffEmpty').hidden = items.length > 0;
  $('staffEmpty').textContent = items.length
    ? ''
    : state.staffTab === 'profiles'
      ? '还没有画像，点右上角「生成本周画像」。'
      : state.staffTab === 'experiences'
        ? '还没有沉淀的项目经验。'
        : '还没有可晋升的主题，先积累同类经验。';

  $('staffCountProfiles').textContent = String(
    state.staffTab === 'profiles' ? state.staffProfiles.length : state.staffProfiles.length
  );
  $('staffCountExperiences').textContent = String(state.staffExperiences.length);
  $('staffCountPromotions').textContent = String(
    state.staffPromotions.filter((p) => p.ready).length
  );

  if (state.staffTab === 'profiles') {
    for (const profile of state.staffProfiles) {
      const li = el('li');
      const btn = el('button', 'staff-row');
      btn.type = 'button';
      if (state.staffSelected === profile.user_id) btn.classList.add('is-on');
      btn.appendChild(el('div', 'staff-row__name', profile.display_name || profile.username || ''));
      btn.appendChild(el('div', 'staff-row__sub',
        `活跃 ${profile.active_days} 天 · 待办 ${profile.todo_completed}/${profile.todo_assigned}`
        + ` · ${profile.period_start.slice(5, 10)}~${profile.period_end.slice(5, 10)}`));
      btn.addEventListener('click', () => {
        state.staffSelected = profile.user_id;
        renderStaffList();
        renderStaffProfileDetail(profile);
      });
      li.appendChild(btn);
      list.appendChild(li);
    }
  } else if (state.staffTab === 'promotions') {
    for (const promo of state.staffPromotions) {
      const li = el('li');
      const btn = el('button', 'staff-row');
      btn.type = 'button';
      if (state.staffSelected === promo.topic) btn.classList.add('is-on');
      btn.appendChild(el('div', 'staff-row__name',
        (promo.ready ? '✓ ' : '') + promo.topic));
      const passed = Object.values(promo.checks).filter((c) => c.passed).length;
      btn.appendChild(el('div', 'staff-row__sub',
        `${promo.cards} 条经验 · ${passed}/4 项达标`));
      btn.addEventListener('click', () => {
        state.staffSelected = promo.topic;
        renderStaffList();
        renderPromotionDetail(promo);
      });
      li.appendChild(btn);
      list.appendChild(li);
    }
  } else {
    for (const exp of state.staffExperiences) {
      const li = el('li');
      const btn = el('button', 'staff-row');
      btn.type = 'button';
      if (state.staffSelected === exp.id) btn.classList.add('is-on');
      btn.appendChild(el('div', 'staff-row__name', exp.title));
      const marks = [exp.status === 'verified' ? '已验证' : exp.status === 'stale' ? '已过期' : '未验证'];
      if (exp.reusable) marks.push('可复用');
      btn.appendChild(el('div', 'staff-row__sub',
        (exp.source_ref || '') + ' · ' + marks.join(' · ')));
      btn.addEventListener('click', () => {
        state.staffSelected = exp.id;
        renderStaffList();
        renderStaffExperienceDetail(exp);
      });
      li.appendChild(btn);
      list.appendChild(li);
    }
  }
}

function staffCard(title, hint) {
  const card = el('section', 'staff-card');
  const head = el('h3', 'staff-card__title', title);
  if (hint) head.appendChild(el('span', 'staff-card__hint', hint));
  card.appendChild(head);
  return card;
}

function renderStaffProfileDetail(profile) {
  const detail = $('staffDetail');
  detail.innerHTML = '';

  // 概览：数值卡
  const overview = staffCard('关键指标', `${profile.period_start.slice(5, 10)} ~ ${profile.period_end.slice(5, 10)}`);
  overview.classList.add('staff-card--wide');
  const stats = el('div', 'staff-stats');
  for (const [key, label, unit] of STAFF_STATS) {
    const box = el('div', 'staff-stat');
    box.appendChild(el('div', 'staff-stat__label', label));
    const value = el('div', 'staff-stat__value',
      String(profile[key] ?? 0) + (unit ? '' : ''));
    if (unit) value.appendChild(el('span', 'staff-stat__unit', unit));
    box.appendChild(value);
    stats.appendChild(box);
  }
  if (profile.avg_response_hours !== null && profile.avg_response_hours !== undefined) {
    const box = el('div', 'staff-stat');
    box.appendChild(el('div', 'staff-stat__label', '响应中位'));
    const value = el('div', 'staff-stat__value', String(profile.avg_response_hours));
    value.appendChild(el('span', 'staff-stat__unit', '小时'));
    box.appendChild(value);
    stats.appendChild(box);
  }
  overview.appendChild(stats);
  detail.appendChild(overview);

  if (profile.summary) {
    const card = staffCard('画像摘要', profile.generated_by === 'model' ? 'AI 蒸馏' : '规则生成');
    card.classList.add('staff-card--wide');
    card.appendChild(el('div', 'staff-kv__v', profile.summary));
    if (profile.activity_digest) {
      const kv = el('div', 'staff-kv');
      kv.appendChild(el('div', 'staff-kv__k', '行为轨迹'));
      kv.appendChild(el('div', 'staff-kv__v', profile.activity_digest));
      card.appendChild(kv);
    }
    detail.appendChild(card);
  }

  if (profile.capability_tags.length) {
    const card = staffCard('职能标签');
    const tags = el('div', 'staff-tags');
    for (const tag of profile.capability_tags) tags.appendChild(el('span', 'staff-tag', tag));
    card.appendChild(tags);
    detail.appendChild(card);
  }

  if (profile.strengths.length) {
    const card = staffCard('优势');
    const ul = el('ul', 'staff-bullets');
    for (const item of profile.strengths) ul.appendChild(el('li', null, item));
    card.appendChild(ul);
    detail.appendChild(card);
  }

  if (profile.risks.length) {
    const card = staffCard('风险');
    const ul = el('ul', 'staff-bullets staff-bullets--risk');
    for (const item of profile.risks) ul.appendChild(el('li', null, item));
    card.appendChild(ul);
    detail.appendChild(card);
  }

  if (profile.focus_areas.length) {
    const card = staffCard('下周期关注');
    const ul = el('ul', 'staff-bullets');
    for (const item of profile.focus_areas) ul.appendChild(el('li', null, item));
    card.appendChild(ul);
    detail.appendChild(card);
  }

  const meta = staffCard('生成信息');
  const kv = el('div', 'staff-kv');
  kv.appendChild(el('div', 'staff-kv__k', '生成方式'));
  kv.appendChild(el('div', 'staff-kv__v', profile.generated_by === 'model' ? 'AI 蒸馏' : '规则统计'));
  kv.appendChild(el('div', 'staff-kv__k', '生成时间'));
  kv.appendChild(el('div', 'staff-kv__v', formatChatTime(profile.generated_at)));
  meta.appendChild(kv);
  detail.appendChild(meta);

  $('staffBody').classList.add('is-detail');
}

function renderStaffExperienceDetail(exp) {
  const detail = $('staffDetail');
  detail.innerHTML = '';

  const head = staffCard(exp.title, exp.source_ref || '');
  if (exp.tags.length) {
    const tags = el('div', 'staff-tags');
    for (const tag of exp.tags) tags.appendChild(el('span', 'staff-tag', tag));
    head.appendChild(tags);
  }
  detail.appendChild(head);

  for (const [label, value] of [
    ['问题', exp.problem],
    ['处理', exp.resolution],
    ['经验', exp.lesson],
  ]) {
    if (!value) continue;
    const card = staffCard(label);
    card.appendChild(el('div', 'staff-kv__v', value));
    detail.appendChild(card);
  }

  const evidence = staffCard('证据状态', '借鉴 skill-cairn 的证据门禁');
  const statusRow = el('div', 'staff-tags');
  const badge = el('span', 'exp-status', exp.status === 'verified'
    ? '已验证' : exp.status === 'stale' ? '已过期' : '未验证');
  badge.dataset.s = exp.status;
  statusRow.appendChild(badge);
  if (exp.reusable) {
    const reuse = el('span', 'exp-status exp-status--reuse', '可复用');
    statusRow.appendChild(reuse);
  }
  evidence.appendChild(statusRow);
  if (exp.not_applicable) {
    const kvNA = el('div', 'staff-kv');
    kvNA.appendChild(el('div', 'staff-kv__k', '不适用'));
    kvNA.appendChild(el('div', 'staff-kv__v', exp.not_applicable));
    evidence.appendChild(kvNA);
  }
  const actions = el('div', 'exp-actions');
  for (const [label, next, extra] of [
    ['标为已验证', 'verified', {}],
    ['标为未验证', 'unverified', {}],
    ['标为已过期', 'stale', {}],
    ['切换可复用', exp.status, { reusable: !exp.reusable }],
  ]) {
    const btn = el('button', 'btn btn--ghost', label);
    btn.type = 'button';
    if (label === '标为已验证' && exp.status === 'verified') btn.classList.add('is-on');
    btn.addEventListener('click', async () => {
      try {
        const body = { status: next, ...extra };
        const updated = await api(`/admin/employees/experiences/${exp.id}`, {
          method: 'PATCH', body,
        });
        Object.assign(exp, updated);
        renderStaffList();
        renderStaffExperienceDetail(exp);
        toast('已更新证据状态');
      } catch (error) {
        toast(error.message);
      }
    });
    actions.appendChild(btn);
  }
  evidence.appendChild(actions);
  detail.appendChild(evidence);

  const meta = staffCard('来源信息');
  const kv = el('div', 'staff-kv');
  kv.appendChild(el('div', 'staff-kv__k', '项目群'));
  kv.appendChild(el('div', 'staff-kv__v', exp.source_ref || '—'));
  kv.appendChild(el('div', 'staff-kv__k', '贡献人'));
  kv.appendChild(el('div', 'staff-kv__v', String(exp.contributors.length) + ' 人'));
  kv.appendChild(el('div', 'staff-kv__k', '沉淀时间'));
  kv.appendChild(el('div', 'staff-kv__v', formatChatTime(exp.created_at)));
  meta.appendChild(kv);
  detail.appendChild(meta);

  $('staffBody').classList.add('is-detail');
}

function renderPromotionDetail(promo) {
  const detail = $('staffDetail');
  detail.innerHTML = '';

  const head = staffCard(promo.topic, `共 ${promo.cards} 条经验`);
  head.classList.add('staff-card--wide');
  const stateEl = el('div', 'staff-kv__v',
    promo.ready ? '✓ 已满足晋升门槛，可以整理成 SOP' : '尚未满足晋升门槛');
  if (promo.ready) stateEl.classList.add('promo-ready');
  head.appendChild(stateEl);
  detail.appendChild(head);

  const gate = staffCard('晋升门槛', '四项全部通过才算达标');
  gate.classList.add('staff-card--wide');
  const listEl = el('div', 'promo-checks');
  for (const chk of Object.values(promo.checks)) {
    const row = el('div', 'promo-check' + (chk.passed ? ' promo-check--ok' : ' promo-check--no'));
    row.appendChild(el('span', 'promo-check__mark', chk.passed ? '✓' : '✗'));
    row.appendChild(el('span', 'promo-check__label', chk.label));
    row.appendChild(el('span', 'promo-check__value', `${chk.actual} / ${chk.required}`));
    listEl.appendChild(row);
  }
  gate.appendChild(listEl);
  detail.appendChild(gate);

  $('staffBody').classList.add('is-detail');
}

async function loadStaff() {
  if (state.staffTab === 'profiles') {
    state.staffProfiles = await api('/admin/employees');
    if (!state.staffSelected && state.staffProfiles.length) {
      state.staffSelected = state.staffProfiles[0].user_id;
    }
    renderStaffList();
    const current = state.staffProfiles.find((p) => p.user_id === state.staffSelected);
    if (current) renderStaffProfileDetail(current);
  } else if (state.staffTab === 'promotions') {
    state.staffPromotions = await api('/admin/employees/promotions');
    if (!state.staffSelected && state.staffPromotions.length) {
      state.staffSelected = state.staffPromotions[0].topic;
    }
    renderStaffList();
    const current = state.staffPromotions.find((p) => p.topic === state.staffSelected);
    if (current) renderPromotionDetail(current);
  } else {
    const keyword = $('staffSearch').value.trim();
    const query = keyword ? `?keyword=${encodeURIComponent(keyword)}` : '';
    state.staffExperiences = await api(`/admin/employees/experiences${query}`);
    if (!state.staffSelected && state.staffExperiences.length) {
      state.staffSelected = state.staffExperiences[0].id;
    }
    renderStaffList();
    const current = state.staffExperiences.find((e) => e.id === state.staffSelected);
    if (current) renderStaffExperienceDetail(current);
  }
}

async function openStaff() {
  if (!isAdmin()) {
    toast('只有管理员可以查看员工知识库');
    return;
  }
  $('staffPanel').hidden = false;
  document.body.style.overflow = 'hidden';
  try {
    await loadStaff();
  } catch (error) {
    toast(error.message);
  }
}

function closeStaff() {
  $('staffPanel').hidden = true;
  document.body.style.overflow = '';
  $('staffBody').classList.remove('is-detail');
}

async function distillAll() {
  const button = $('btnStaffDistill');
  button.disabled = true;
  button.textContent = '生成中…';
  try {
    state.staffProfiles = await api('/admin/employees:distill-all', { method: 'POST' });
    state.staffSelected = state.staffProfiles[0]?.user_id || null;
    renderStaffList();
    const current = state.staffProfiles.find((p) => p.user_id === state.staffSelected);
    if (current) renderStaffProfileDetail(current);
    toast(`已生成 ${state.staffProfiles.length} 份画像`);
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = '生成本周画像';
  }
}

// ---------------- 邀请 & 新建 ----------------

async function searchDirectory(query) {
  const list = $('inviteResults');
  list.innerHTML = '';
  const rows = await api(`/groups/directory-users?q=${encodeURIComponent(query || '')}`);
  const inGroup = new Set(((state.detail && state.detail.members) || []).map((item) => item.user_id));
  // 按部门分组：同名同角色的人靠部门才分得清，这也是「邀请不够清晰」的主因
  for (const group of groupByDeptPath(rows)) {
    const head = el('li', 'invite-group__head');
    head.appendChild(el('span', 'invite-group__name', group.label));
    head.appendChild(el('span', 'invite-group__count', String(group.members.length)));
    list.appendChild(head);
    for (const user of group.members) {
      const row = el('li', 'member-row');
      row.appendChild(el('span', 'msg__avatar', initials(user.display_name)));
      const main = el('span', 'member-row__main');
      main.appendChild(el('span', 'member-row__name', `${user.display_name}（${user.username}）`));
      const sub = [user.dept_name, user.position].filter(Boolean).join(' · ');
      if (sub) main.appendChild(el('span', 'member-row__dept', sub));
      row.appendChild(main);
      if (inGroup.has(user.id)) {
        row.appendChild(el('span', 'member-row__role', '已加入'));
      } else {
        const add = el('button', 'btn btn--ghost', '邀请');
        add.type = 'button';
        add.addEventListener('click', () => invite(user.id));
        row.appendChild(add);
      }
      list.appendChild(row);
    }
  }
  if (!rows.length) list.appendChild(el('li', 'empty', '没有匹配的账号'));
}

async function invite(userId) {
  if (!state.groupId) return;
  try {
    state.detail = await api(`/groups/${state.groupId}/members`, {
      method: 'POST',
      body: { user_id: userId, role: 'member' },
    });
    try {
      state.orgMap = await api(`/groups/${state.groupId}/org-map`);
    } catch (error) {
      // 组织接口不可用时保留原图，成员本身已经进群了
    }
    renderRoom();
    renderMembers();
    await searchDirectory($('inviteSearch').value);
    toast('已邀请入群');
  } catch (error) {
    toast(error.message);
  }
}

async function createGroup(payload) {
  const group = await api('/groups', { method: 'POST', body: payload });
  await loadGroups();
  await selectGroup(group.id);
}

// ---------------- 事件 ----------------

function showTab(name) {
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.classList.toggle('is-on', tab.dataset.tab === name);
  });
  const showTodos = name === 'todos';
  $('tabMembers').hidden = showTodos;
  $('tabTodos').hidden = !showTodos;
  if (showTodos) loadTodos();
}

function bindEvents() {
  $('composer').addEventListener('submit', sendMessage);
  $('btnAnalyze').addEventListener('click', () => runAnalysis(false));
  $('btnVoice').addEventListener('click', toggleVoice);
  $('fileInput').addEventListener('change', async (event) => {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    await uploadFiles(files);
  });
  $('groupSelect').addEventListener('change', (event) => selectGroup(event.target.value));
  $('btnTodosMobile').addEventListener('click', () => {
    $('sidePane').classList.add('is-open');
    showTab('todos');
  });
  $('btnSideClose').addEventListener('click', () => $('sidePane').classList.remove('is-open'));

  // 账号菜单：外观切换 + 解散群
  $('btnUserMenu').addEventListener('click', (event) => {
    event.stopPropagation();
    setUserMenu($('userMenuPanel').hidden);
  });
  $('userMenuPanel').addEventListener('click', (event) => event.stopPropagation());
  document.addEventListener('click', () => setUserMenu(false));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') setUserMenu(false);
  });
  for (const button of document.querySelectorAll('[data-theme-choice]')) {
    button.addEventListener('click', () => setTheme(button.dataset.themeChoice));
  }
  bindSettings();
  THEME_MEDIA.addEventListener('change', () => {
    if (savedTheme() === 'auto') applyTheme('auto');
  });
  $('btnDisband').addEventListener('click', openDisbandDialog);
  $('btnDisbandCancel').addEventListener('click', () => $('dialogDisband').close());
  $('btnDisbandConfirm').addEventListener('click', disbandGroup);
  $('btnDistill').addEventListener('click', distillCurrentGroup);

  $('btnNewGroup').addEventListener('click', () => {
    $('formNewGroup').reset();
    $('dialogNewGroup').showModal();
  });
  $('btnNewGroupCancel').addEventListener('click', () => $('dialogNewGroup').close());
  $('formNewGroup').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = new FormData($('formNewGroup'));
    const name = String(form.get('name') || '').trim();
    if (!name) {
      toast('请填写群名');
      return;
    }
    try {
      await createGroup({ name, description: String(form.get('description') || '') });
      $('dialogNewGroup').close();
      toast('项目群已创建');
    } catch (error) {
      toast(error.message);
    }
  });

  $('btnInvite').addEventListener('click', async () => {
    $('dialogInvite').showModal();
    try {
      await searchDirectory('');
    } catch (error) {
      toast(error.message);
    }
  });
  $('btnInviteClose').addEventListener('click', () => $('dialogInvite').close());
  bindComposerExtras();
  document.querySelectorAll('#ackedFilters .chip').forEach((chip) => {
    chip.addEventListener('click', async () => {
      state.ackedFilter = chip.dataset.acked;
      await loadEvents({ silent: false });
      renderFilters();
      renderEvents();
    });
  });
  $('btnStaff').addEventListener('click', openStaff);
  $('btnStaffBack').addEventListener('click', closeStaff);
  $('btnStaffDistill').addEventListener('click', distillAll);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !$('staffPanel').hidden) closeStaff();
  });
  document.querySelectorAll('[data-staff-tab]').forEach((tab) => {
    tab.addEventListener('click', async () => {
      state.staffTab = tab.dataset.staffTab;
      state.staffSelected = null;
      document.querySelectorAll('[data-staff-tab]').forEach((t) => {
        t.classList.toggle('is-on', t.dataset.staffTab === state.staffTab);
      });
      $('staffSearchWrap').hidden = state.staffTab !== 'experiences';
      $('staffBody').classList.remove('is-detail');
      try {
        await loadStaff();
      } catch (error) {
        toast(error.message);
      }
    });
  });
  $('staffSearch').addEventListener('input', () => {
    clearTimeout(loadStaff.timer);
    loadStaff.timer = setTimeout(() => loadStaff().catch(() => {}), 250);
  });
  // 铃铛是开关：开着再点就收起，而不是「什么都没发生」
  $('btnBell').addEventListener('click', () => {
    const dialog = $('dialogEvents');
    if (dialog.open) closeSheet(dialog);
    else openEvents();
  });
  bindSheetWindow();
  $('btnMicClose').addEventListener('click', () => $('dialogMic').close());
  $('btnMicRetry').addEventListener('click', async () => {
    $('dialogMic').close();
    await toggleVoice();
  });
  $('inviteSearch').addEventListener('input', (event) => {
    clearTimeout(searchDirectory.timer);
    searchDirectory.timer = setTimeout(() => searchDirectory(event.target.value), 200);
  });

  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => showTab(tab.dataset.tab));
  });

  // 成员视图：人力地图 / 平铺，选择记在本地，下次打开还是同一种看法
  document.querySelectorAll('.org-switch__btn').forEach((button) => {
    button.addEventListener('click', () => {
      state.memberView = button.dataset.view === 'list' ? 'list' : 'map';
      localStorage.setItem('group.memberView', state.memberView);
      applyMemberView();
    });
  });

  const draftEl = $('draft');
  const syncSend = () => {
    const hasText = draftEl.value.trim().length > 0;
    $('btnSend').disabled = !hasText && !state.recording;
  };
  draftEl.addEventListener('input', () => {
    draftEl.style.height = 'auto';
    draftEl.style.height = `${Math.min(draftEl.scrollHeight, 140)}px`;
    syncSend();
  });
  syncSend();
  syncComposerPlaceholder();
  window.addEventListener('resize', syncComposerPlaceholder);

  $('draft').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $('composer').requestSubmit();
    }
  });
}

boot();

// ---------------- 个人设置：头像 / 昵称 / 外观 / 字号 ----------------

/** 顶栏「我」的头像；没上传过就显示姓名首字。 */
function renderMyAvatar() {
  const box = $('meAvatar');
  if (!box || !state.me) return;
  box.textContent = '';
  const node = avatarNode('who__avatar-face', state.me.display_name, state.me.avatar_url);
  box.appendChild(node);
}

/** 设置弹窗里的头像预览（尺寸更大，样式复用同一套）。 */
function renderAvatarPreview() {
  const box = $('avatarPreview');
  if (!box || !state.me) return;
  box.textContent = '';
  box.appendChild(avatarNode('avatar-preview__face', state.me.display_name, state.me.avatar_url));
}

async function refreshMe() {
  try {
    state.me = await api('/auth/me');
  } catch (_) {
    /* 拿不到就沿用旧值，不阻塞设置面板 */
  }
  renderMyAvatar();
  renderAvatarPreview();
}

function openSettings() {
  if (!state.me) return;
  setUserMenu(false);
  $('settingName').value = state.me.display_name || '';
  renderAvatarPreview();
  applyFontChoice(savedFontChoice());
  $('dialogSettings').showModal();
}

async function uploadAvatar(file) {
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) {
    toast('头像不能超过 5MB');
    return;
  }
  const form = new FormData();
  form.append('file', file, file.name || 'avatar.png');
  try {
    const result = await api('/auth/avatar', { method: 'POST', body: form });
    state.me = { ...state.me, avatar_url: result.avatar_url };
    renderMyAvatar();
    renderAvatarPreview();
    await selectGroup(state.groupId);
    toast('头像已更新');
  } catch (error) {
    toast(error.message || '头像上传失败');
  }
}

async function removeAvatar() {
  try {
    await api('/auth/avatar', { method: 'DELETE' });
    state.me = { ...state.me, avatar_url: null };
    renderMyAvatar();
    renderAvatarPreview();
    await selectGroup(state.groupId);
    toast('头像已移除');
  } catch (error) {
    toast(error.message || '移除失败');
  }
}

async function saveDisplayName() {
  const value = $('settingName').value.trim();
  if (!value) {
    toast('昵称不能为空');
    return;
  }
  try {
    const updated = await api('/auth/display-name', { method: 'PATCH', body: { display_name: value } });
    state.me = updated;
    $('me').textContent = updated.display_name || updated.username || '';
    renderMyAvatar();
    renderAvatarPreview();
    await selectGroup(state.groupId);
    toast('昵称已保存');
  } catch (error) {
    toast(error.message || '昵称保存失败');
  }
}

// 消息字号：只影响正文与输入框，不动布局尺寸
const FONT_KEY = 'dnx-chat-font';
const FONT_SCALE = { sm: '13px', md: '14px', lg: '16px' };

function savedFontChoice() {
  try {
    const value = localStorage.getItem(FONT_KEY);
    return value === 'sm' || value === 'lg' ? value : 'md';
  } catch (_) {
    return 'md';
  }
}

function applyFontChoice(choice) {
  const size = FONT_SCALE[choice] || FONT_SCALE.md;
  document.documentElement.dataset.font = choice;
  document.documentElement.style.setProperty('--chat-font', size);
  for (const button of document.querySelectorAll('[data-font-choice]')) {
    button.setAttribute('aria-checked', String(button.dataset.fontChoice === choice));
  }
}

function setFontChoice(choice) {
  try {
    localStorage.setItem(FONT_KEY, choice);
  } catch (_) {
    /* 隐私模式写不了就只影响本次 */
  }
  applyFontChoice(choice);
}

function bindSettings() {
  $('btnSettings').addEventListener('click', openSettings);
  $('btnSettingsClose').addEventListener('click', () => $('dialogSettings').close());
  $('btnAvatarPick').addEventListener('click', () => $('avatarInput').click());
  $('avatarInput').addEventListener('change', (event) => {
    uploadAvatar(event.target.files && event.target.files[0]);
    event.target.value = '';
  });
  $('btnAvatarRemove').addEventListener('click', removeAvatar);
  $('btnSaveName').addEventListener('click', saveDisplayName);
  $('settingName').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      saveDisplayName();
    }
  });
  for (const button of document.querySelectorAll('[data-font-choice]')) {
    button.addEventListener('click', () => setFontChoice(button.dataset.fontChoice));
  }
  applyFontChoice(savedFontChoice());
}
