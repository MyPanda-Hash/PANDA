// 待办工作台：统计 → AI 生产概况 → 待办列表 + 详情 → AI 主动发现。
// 数据只走 /api/*；状态写回后就地更新，不整页刷新（契约见 CONTRACT.md §4/§5）。
//
// 布局策略：≥1024px 是主从双栏（列表 + 吸顶详情），<1024px 详情改用 .drawer；
// 两个断点共用同一段详情渲染，避免两套 DOM 走偏。

import { TODOS, WATCH, BRIEFING } from './api.js';
import {
  el,
  icon,
  clear,
  timeAgo,
  formatMoment,
  parseTodoDetail,
  toast,
  priorityTone,
  statusTone,
  alignMesHost,
  PRIORITY_LABEL,
  STATUS_LABEL,
  SEVERITY_LABEL,
} from './util.js';

const WIDE_QUERY = '(min-width: 1024px)';
const TAB_DEFS = [
  { key: 'pending', label: '待处理' },
  { key: 'doing', label: '进行中' },
  { key: 'done', label: '已完成' },
];
const FILTER_DEFS = [
  { key: 'all', label: '全部' },
  { key: 'ai', label: 'AI 整理' },
  { key: 'high', label: '高优先级' },
];
const PRIORITY_WEIGHT = { urgent: 0, high: 1, normal: 2, low: 3 };

let ctx = null;
let host = null;
let parts = null;
let state = null;
let mounted = false;
let media = null;
let drawer = null;
let speaking = false;
let onSearch = null;
let onKeydown = null;
let onMediaChange = null;
const acking = new Set();
let eventsTimer = null;

// ---------------------------------------------------------------- 生命周期

export async function mount(root, context) {
  ctx = context;
  host = root;
  mounted = true;
  speaking = false;
  acking.clear();
  state = {
    todos: [],
    events: [],
    briefing: null,
    watch: null,
    loadingTodos: true,
    loadingEvents: true,
    loadingBriefing: true,
    errorTodos: '',
    errorEvents: '',
    errorBriefing: '',
    tab: 'pending',
    filter: 'all',
    query: '',
    selectedId: '',
    busy: false,
    // AI 发现分组：哪些域被整体收起、哪些域展开了全部条目（默认每域只显示最严重一条）
    collapsedDomains: new Set(),
    domainExpanded: new Set(),
    // AI 发现的域筛选：空串表示全部。点芯片切换，选中后只渲染该域。
    domainFilter: '',
  };

  buildShell();

  media = window.matchMedia(WIDE_QUERY);
  onSearch = (event) => {
    const query = String((event && event.detail) || '').trim();
    if (query === state.query) return;
    state.query = query;
    refreshListAndDetail();
  };
  onKeydown = (event) => {
    if (event.key === 'Escape') closeDrawer();
  };
  onMediaChange = () => {
    if (media.matches) closeDrawer();
    renderDetail();
  };
  // 巡检是后端定时跑的（默认 1 分钟一轮）。页面不主动刷新就会出现
  // 「业务已经处理完、事件已被 resolved，这里还留着」的假待办。
  // 静默刷新：不切 loading 骨架，避免列表闪烁。
  eventsTimer = window.setInterval(() => {
    if (!mounted || document.hidden) return;
    silentRefreshEvents();
  }, 30000);

  window.addEventListener('desk:search', onSearch);
  window.addEventListener('keydown', onKeydown);
  if (media.addEventListener) media.addEventListener('change', onMediaChange);

  await Promise.all([loadTodos(), loadBriefing(), loadEvents()]);
}

export async function unmount() {
  mounted = false;
  if (eventsTimer) window.clearInterval(eventsTimer);
  eventsTimer = null;
  stopSpeaking();
  closeDrawer();
  if (onSearch) window.removeEventListener('desk:search', onSearch);
  if (onKeydown) window.removeEventListener('keydown', onKeydown);
  if (media && onMediaChange && media.removeEventListener) media.removeEventListener('change', onMediaChange);
  onSearch = null;
  onKeydown = null;
  onMediaChange = null;
  media = null;
  parts = null;
  host = null;
  ctx = null;
  state = null;
  acking.clear();
}

// ---------------------------------------------------------------- 取数

async function loadTodos() {
  if (!mounted) return;
  state.loadingTodos = true;
  state.errorTodos = '';
  renderStats();
  renderList();
  try {
    const rows = await TODOS.list('limit=200');
    if (!mounted) return;
    state.todos = Array.isArray(rows) ? rows.filter((row) => row && row.id) : [];
  } catch (err) {
    if (!mounted) return;
    state.todos = [];
    state.errorTodos = describe(err);
  }
  if (!mounted) return;
  state.loadingTodos = false;
  renderStats();
  refreshListAndDetail();
}

async function loadBriefing() {
  if (!mounted) return;
  state.loadingBriefing = true;
  state.errorBriefing = '';
  renderBriefing();
  try {
    const payload = await BRIEFING.latest();
    if (!mounted) return;
    state.briefing = payload && typeof payload === 'object' ? payload : null;
  } catch (err) {
    if (!mounted) return;
    state.briefing = null;
    state.errorBriefing = describe(err);
  }
  if (!mounted) return;
  state.loadingBriefing = false;
  renderBriefing();
}

async function loadEvents() {
  if (!mounted) return;
  state.loadingEvents = true;
  state.errorEvents = '';
  renderEvents();
  try {
    // 巡检状态只是辅助文案，取不到不影响问题列表
    const [rows, watch] = await Promise.all([
      WATCH.active(50), // open + handled：认领后还要能看到是谁在跟
      WATCH.status().catch(() => null),
    ]);
    if (!mounted) return;
    state.events = Array.isArray(rows) ? rows.filter((row) => row && row.id) : [];
    state.watch = watch && typeof watch === 'object' ? watch : null;
  } catch (err) {
    if (!mounted) return;
    state.events = [];
    state.watch = null;
    state.errorEvents = describe(err);
  }
  if (!mounted) return;
  state.loadingEvents = false;
  renderEvents();
  renderStats();
}

/** 静默拉一次事件：成功就就地重画，失败保持原样不打扰用户。 */
async function silentRefreshEvents() {
  if (!mounted) return;
  try {
    const rows = await WATCH.active(50);
    if (!mounted) return;
    // 后端在条件消失时会把这批事件标成 resolved，所以下一轮拉取里自然就没了——
    // 这就是「业务处理完，待办跟着消失」的实现方式，前端不需要自己判断。
    state.events = Array.isArray(rows) ? rows.filter((row) => row && row.id) : [];
    renderEvents();
    renderStats();
    if (typeof ctx?.refreshCounts === 'function') ctx.refreshCounts();
  } catch (_) {
    /* 静默失败：下一轮再试 */
  }
}

function describe(err) {
  if (!err) return '请求失败';
  if (err.status === 401) return '登录已过期，请重新登录后再试';
  if (err.status === 0) return '连不上 AI 后端，先确认服务在运行';
  return err.message || '请求失败';
}

// ---------------------------------------------------------------- 骨架

function buildShell() {
  clear(host);
  const page = el('div', { cls: 'stack-24 todo-page' });

  const stats = el('div', { cls: 'stats todo-stats', attrs: { role: 'group', 'aria-label': '待办统计' } });

  const briefBody = el('div', { cls: 'card__body todo-brief__body' });
  const speakBtn = el(
    'button',
    {
      cls: 'btn todo-brief__speak',
      attrs: { type: 'button', 'aria-pressed': 'false' },
      on: { click: toggleSpeak },
    },
    [icon('sound'), el('span', { text: '朗读概况' })]
  );
  const briefRefresh = el('button', {
    cls: 'btn btn--text',
    attrs: { type: 'button' },
    text: '刷新',
    on: { click: () => loadBriefing() },
  });
  const briefingCard = el('section', { cls: 'card todo-brief', attrs: { 'aria-label': 'AI 生产概况' } }, [
    el('div', { cls: 'card__head' }, [
      el('h2', { text: 'AI 生产概况' }),
      tag('AI 主动汇报', 'accent'),
      el('div', { cls: 'card__head-actions' }, [briefRefresh, speakBtn]),
    ]),
    briefBody,
  ]);

  const totalTag = el('span', { cls: 'tag todo-list__total', text: '读取中' });
  const segBox = el('div', { cls: 'seg todo-seg', attrs: { role: 'tablist', 'aria-label': '待办筛选' } });
  const tabBox = el('div', { cls: 'tabbar todo-tabs', attrs: { role: 'tablist', 'aria-label': '待办状态' } });
  const listBody = el('div', { cls: 'card__body card__body--flush todo-list__body' });
  const footText = el('span', { cls: 'muted todo-list__foot-text' });
  const listRefresh = el('button', {
    cls: 'btn btn--text btn--sm',
    attrs: { type: 'button' },
    text: '刷新',
    on: { click: () => loadTodos() },
  });
  const listFoot = el('div', { cls: 'card__foot todo-list__foot' }, [footText, el('span', { cls: 'grow' }), listRefresh]);
  const listCard = el('section', { cls: 'card todo-list-card', attrs: { 'aria-label': '待办列表' } }, [
    el('div', { cls: 'card__head' }, [
      el('h2', { text: '待办' }),
      totalTag,
      el('div', { cls: 'card__head-actions' }, [segBox]),
    ]),
    tabBox,
    listBody,
    listFoot,
  ]);

  const detail = el('aside', { cls: 'card todo-detail', attrs: { 'aria-label': '待办详情' } });
  const split = el('div', { cls: 'todo-split' }, [listCard, detail]);

  const eventsStatus = el('span', { cls: 'todo-events__status muted' });
  const eventsBody = el('div', { cls: 'card__body card__body--flush todo-events__body' });
  const eventsCard = el('section', { cls: 'card todo-events', attrs: { 'aria-label': 'AI 主动发现' } }, [
    el('div', { cls: 'card__head' }, [
      el('h2', { text: 'AI 发现' }),
      tag('主动巡检', 'accent'),
      eventsStatus,
      el('div', { cls: 'card__head-actions' }, [
        el('button', {
          cls: 'btn btn--text',
          attrs: { type: 'button' },
          text: '刷新',
          on: { click: () => loadEvents() },
        }),
      ]),
    ]),
    eventsBody,
  ]);

  page.append(stats, briefingCard, split, eventsCard);
  host.append(page);

  parts = {
    stats,
    briefBody,
    speakBtn,
    totalTag,
    segBox,
    tabBox,
    listBody,
    footText,
    listCard,
    detail,
    eventsStatus,
    eventsBody,
    eventsCard,
  };

  renderStats();
  refreshListAndDetail();
  renderBriefing();
  renderEvents();
}

// ---------------------------------------------------------------- 统计

function renderStats() {
  if (!parts) return;
  const box = clear(parts.stats);
  const todos = state.todos;
  const pending = todos.filter((todo) => todo.status === 'pending' || todo.status === 'accepted');
  const doing = todos.filter((todo) => todo.status === 'doing');
  const open = pending.concat(doing);
  const urgent = open.filter((todo) => todo.priority === 'urgent');
  const high = open.filter((todo) => todo.priority === 'urgent' || todo.priority === 'high');
  const highOnly = high.filter((todo) => todo.priority === 'high');
  const done = todos.filter((todo) => todo.status === 'done');
  const dismissed = todos.filter((todo) => todo.status === 'dismissed');
  const openEvents = state.events.filter((event) => event.status === 'open');
  const criticalEvents = openEvents.filter((event) => event.severity === 'critical');

  box.append(
    statCard({
      iconName: 'clock-circle',
      label: '待处理',
      value: pending.length,
      foot: `其中紧急 ${urgent.length} 条`,
      loading: state.loadingTodos,
      onClick: () => setTab('pending'),
    }),
    statCard({
      iconName: 'alert',
      label: '高优先级',
      value: high.length,
      foot: `紧急 ${urgent.length} · 高 ${highOnly.length}`,
      tone: 'warn',
      loading: state.loadingTodos,
      onClick: () => setFilter('high'),
    }),
    statCard({
      iconName: 'thunderbolt',
      label: 'AI 发现',
      value: openEvents.length,
      foot: state.errorEvents
        ? '巡检结果没取到'
        : openEvents.length
          ? `严重 ${criticalEvents.length} 条待处置`
          : '巡检暂无异常',
      loading: state.loadingEvents,
      // 不用平滑滚动：低性能/无头环境下 rAF 被节流会卡住不动，直接定位更可靠
      onClick: () => parts.eventsCard.scrollIntoView({ block: 'start' }),
    }),
    statCard({
      iconName: 'check-square',
      label: '已完成',
      value: done.length,
      foot: dismissed.length ? `含 ${dismissed.length} 条已取消` : `全部 ${todos.length} 条待办`,
      tone: 'muted',
      loading: state.loadingTodos,
      onClick: () => setTab('done'),
    })
  );
}

function statCard({ iconName, label, value, foot, tone, loading, onClick }) {
  const node = el('button', {
    cls: 'card stat todo-stat',
    attrs: { type: 'button', 'aria-label': `${label}：${loading ? '读取中' : value} 条` },
    on: { click: onClick },
  });
  node.append(el('span', { cls: 'stat__label' }, [icon(iconName), el('span', { text: label })]));
  if (loading) {
    node.append(el('span', { cls: 'skeleton todo-skel todo-skel--stat' }));
  } else {
    const num = el('span', { cls: 'stat__num', text: String(value) });
    if (tone) num.dataset.tone = tone;
    node.append(num);
  }
  node.append(el('span', { cls: 'todo-stat__foot secondary', text: loading ? '' : foot }));
  return node;
}

function setTab(tab) {
  state.tab = tab;
  refreshListAndDetail();
}

function setFilter(filter) {
  state.filter = filter;
  refreshListAndDetail();
}

// ---------------------------------------------------------------- AI 生产概况

function renderBriefing() {
  if (!parts) return;
  const body = clear(parts.briefBody);
  parts.speakBtn.disabled = state.loadingBriefing || !briefingText();

  if (state.loadingBriefing) {
    body.append(
      el('div', { cls: 'skeleton todo-skel todo-skel--brief' }),
      el('div', { cls: 'skeleton todo-skel todo-skel--brief-sm' })
    );
    return;
  }
  if (state.errorBriefing) {
    body.append(inlineError(state.errorBriefing, '重新获取', () => loadBriefing()));
    return;
  }
  const data = state.briefing && state.briefing.data ? state.briefing.data : null;
  const items = data && Array.isArray(data.items) ? data.items : [];
  if (!data || (!data.text && !items.length)) {
    body.append(inlineError('还没有生产概况，可能是 MES 数据还没同步。', '重新获取', () => loadBriefing()));
    return;
  }

  body.append(el('p', { cls: 'todo-brief__text', text: String(data.text || '') }));
  if (items.length) {
    body.append(
      el(
        'div',
        { cls: 'todo-brief__items' },
        items.map((item) =>
          el('span', { cls: 'todo-brief__item' }, [
            el('span', { cls: 'todo-brief__item-label', text: String(item.label || '') }),
            el('span', { cls: 'todo-brief__item-value', text: String(item.value == null ? '' : item.value) }),
          ])
        )
      )
    );
  }
  const notes = [];
  if (data.generatedAt) notes.push(`数据日期 ${data.generatedAt}`);
  if (data.note) notes.push(String(data.note));
  if (data.ok === false) notes.push('部分指标暂时取不到，已跳过');
  if (notes.length) body.append(el('p', { cls: 'todo-brief__foot muted', text: notes.join(' · ') }));
}

function briefingText() {
  const data = state && state.briefing && state.briefing.data;
  return data && data.text ? String(data.text) : '';
}

function inlineError(text, actionLabel, onAction) {
  return el('div', { cls: 'todo-inline-error' }, [
    el('p', { cls: 'todo-inline-error__text', text }),
    el('button', {
      cls: 'btn btn--text btn--sm',
      attrs: { type: 'button' },
      text: actionLabel,
      on: { click: onAction },
    }),
  ]);
}

function toggleSpeak() {
  if (!('speechSynthesis' in window) || typeof window.SpeechSynthesisUtterance !== 'function') {
    toast('当前浏览器不支持朗读', 'error');
    return;
  }
  if (speaking) {
    stopSpeaking();
    return;
  }
  const text = briefingText();
  if (!text) return;
  const utter = new window.SpeechSynthesisUtterance(text);
  utter.lang = 'zh-CN';
  utter.rate = 1;
  utter.onend = () => setSpeaking(false);
  utter.onerror = () => setSpeaking(false);
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utter);
  setSpeaking(true);
}

function setSpeaking(next) {
  speaking = next;
  if (!parts || !parts.speakBtn) return;
  parts.speakBtn.setAttribute('aria-pressed', next ? 'true' : 'false');
  parts.speakBtn.replaceChildren(icon(next ? 'audio' : 'sound'), el('span', { text: next ? '停止朗读' : '朗读概况' }));
}

function stopSpeaking() {
  if (!('speechSynthesis' in window)) return;
  window.speechSynthesis.cancel();
  if (speaking) setSpeaking(false);
}

// ---------------------------------------------------------------- 待办列表

function refreshListAndDetail() {
  renderList();
  renderDetail();
}

function renderList() {
  if (!parts) return;
  ensureSelection();
  const rows = visibleTodos();
  renderTabs();
  renderSeg();

  const dismissed = state.todos.filter((todo) => todo.status === 'dismissed').length;
  parts.totalTag.textContent = state.loadingTodos ? '读取中' : `全部 ${state.todos.length - dismissed}`;
  parts.footText.textContent = state.loadingTodos
    ? '正在读取待办…'
    : state.errorTodos
      ? '读取失败'
      : `当前显示 ${rows.length} 条`;

  const body = clear(parts.listBody);
  if (state.loadingTodos) {
    body.append(skeletonRows(5));
    return;
  }
  if (state.errorTodos) {
    body.append(
      emptyBox({
        iconName: 'alert',
        title: '待办没读出来',
        text: state.errorTodos,
        actionLabel: '重新加载',
        onAction: () => loadTodos(),
      })
    );
    return;
  }
  if (!state.todos.length) {
    body.append(
      emptyBox({
        iconName: 'check-square',
        title: '还没有待办',
        text: 'AI 在群聊里整理出要做的事之后，会出现在这里。',
        actionLabel: '刷新看看',
        onAction: () => loadTodos(),
      })
    );
    return;
  }
  if (!rows.length) {
    body.append(emptyBox(emptyForFilter()));
    return;
  }
  if (state.query.trim()) body.append(searchNote(rows.length));
  for (const todo of rows) body.append(todoRow(todo));
}

function renderTabs() {
  const counts = tabCounts();
  const box = clear(parts.tabBox);
  for (const def of TAB_DEFS) {
    box.append(
      el(
        'button',
        {
          cls: 'tabbar__btn',
          attrs: {
            type: 'button',
            role: 'tab',
            'aria-selected': state.tab === def.key ? 'true' : 'false',
          },
          on: { click: () => { if (state.tab !== def.key) setTab(def.key); } },
        },
        [el('span', { text: def.label }), el('b', { text: String(counts[def.key] || 0) })]
      )
    );
  }
}

function renderSeg() {
  const box = clear(parts.segBox);
  for (const def of FILTER_DEFS) {
    box.append(
      el('button', {
        cls: 'seg__btn',
        attrs: {
          type: 'button',
          role: 'tab',
          'aria-selected': state.filter === def.key ? 'true' : 'false',
        },
        text: def.label,
        on: { click: () => { if (state.filter !== def.key) setFilter(def.key); } },
      })
    );
  }
}

function tabCounts() {
  const counts = { pending: 0, doing: 0, done: 0 };
  for (const todo of state.todos) {
    if (todo.status === 'dismissed') continue;
    if (todo.status === 'pending' || todo.status === 'accepted') counts.pending += 1;
    else if (todo.status === 'doing') counts.doing += 1;
    else if (todo.status === 'done') counts.done += 1;
  }
  return counts;
}

function inTab(todo) {
  if (todo.status === 'dismissed') return false;
  if (state.tab === 'pending') return todo.status === 'pending' || todo.status === 'accepted';
  if (state.tab === 'doing') return todo.status === 'doing';
  return todo.status === 'done';
}

function visibleTodos() {
  const query = state.query.trim().toLowerCase();
  return state.todos
    .filter(inTab)
    .filter((todo) => (state.filter === 'ai' ? todo.origin === 'ai' : true))
    .filter((todo) => (state.filter === 'high' ? todo.priority === 'urgent' || todo.priority === 'high' : true))
    .filter((todo) => (query ? matchesQuery(todo, query) : true))
    .sort(compareTodo);
}

function matchesQuery(todo, query) {
  const refs = Array.isArray(todo.refs) ? todo.refs.map((ref) => `${ref.label || ''} ${ref.docNo || ''}`) : [];
  const haystack = [todo.title, todo.detail, todo.group_name, todo.assignee_display_name]
    .concat(refs)
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return haystack.includes(query);
}

function compareTodo(a, b) {
  const weight = (PRIORITY_WEIGHT[a.priority] == null ? 9 : PRIORITY_WEIGHT[a.priority])
    - (PRIORITY_WEIGHT[b.priority] == null ? 9 : PRIORITY_WEIGHT[b.priority]);
  if (weight !== 0) return weight;
  const left = new Date(a.created_at || 0).getTime();
  const right = new Date(b.created_at || 0).getTime();
  return (Number.isFinite(right) ? right : 0) - (Number.isFinite(left) ? left : 0);
}

function ensureSelection() {
  const rows = visibleTodos();
  if (!rows.length) {
    state.selectedId = '';
    return;
  }
  if (!rows.some((todo) => todo.id === state.selectedId)) state.selectedId = rows[0].id;
}

function todoRow(todo) {
  const meta = [
    tag(PRIORITY_LABEL[todo.priority] || todo.priority || '普通', priorityTone(todo.priority)),
    tag(STATUS_LABEL[todo.status] || todo.status, statusTone(todo.status)),
  ];
  if (todo.origin === 'ai') meta.push(tag('AI 整理', 'muted'));
  const parsed = parseTodoDetail(todo);
  if (parsed.items.length) meta.push(metaItem('check-square', `${parsed.items.length} 件事`));
  const refs = Array.isArray(todo.refs) ? todo.refs : [];
  if (refs.length) meta.push(metaItem('file-text', `${refs.length} 张单据`));
  if (Number(todo.nudge_count || 0) > 0) meta.push(metaItem('bell', `催办 ${todo.nudge_count} 次`));
  if (todo.group_name) meta.push(el('span', { cls: 'todo-meta todo-meta--group', text: todo.group_name }));

  return el(
    'button',
    {
      cls: 'list__row todo-row',
      attrs: {
        type: 'button',
        'aria-current': todo.id === state.selectedId ? 'true' : 'false',
        'aria-label': `查看待办：${todo.title}`,
      },
      on: { click: () => selectTodo(todo.id) },
    },
    [
      el('span', { cls: 'list__main' }, [
        el('span', { cls: 'list__title todo-row__title', text: todo.title }),
        el('span', { cls: 'list__meta todo-row__meta' }, meta),
      ]),
      el('span', { cls: 'todo-row__side' }, [
        el('span', { cls: 'list__time', text: timeAgo(todo.created_at) }),
        todo.status === 'doing' ? tag('进行中', 'accent') : null,
      ]),
    ]
  );
}

function emptyForFilter() {
  if (state.query.trim()) {
    return {
      iconName: 'search',
      title: '没有匹配的待办',
      text: `标题、说明或单据号里都没有「${state.query}」。`,
      actionLabel: '清除搜索',
      onAction: clearSearch,
    };
  }
  if (state.filter !== 'all') {
    return {
      iconName: 'check-square',
      title: '这一栏没有符合筛选的待办',
      text: '换个筛选条件，或回到全部待办。',
      actionLabel: '看全部待办',
      onAction: () => setFilter('all'),
    };
  }
  if (state.tab === 'done') {
    return {
      iconName: 'check-square',
      title: '还没有完成的待办',
      text: '处理完的待办会归到这一栏。',
      actionLabel: '回到待处理',
      onAction: () => setTab('pending'),
    };
  }
  if (state.tab === 'doing') {
    return {
      iconName: 'clock-circle',
      title: '没有正在处理的待办',
      text: '在「待处理」里点开一条，选「开始处理」就会进到这一栏。',
      actionLabel: '回到待处理',
      onAction: () => setTab('pending'),
    };
  }
  return {
    iconName: 'inbox',
    title: '待处理的都清空了',
    text: 'AI 整理出新待办会立刻出现在这里。',
    actionLabel: '看看已完成',
    onAction: () => setTab('done'),
  };
}

function searchNote(count) {
  return el('div', { cls: 'todo-search-note' }, [
    icon('search'),
    el('span', { cls: 'todo-search-note__text', text: `搜索「${state.query}」· 匹配 ${count} 条` }),
    el('button', {
      cls: 'btn btn--text btn--sm',
      attrs: { type: 'button' },
      text: '清除',
      on: { click: clearSearch },
    }),
  ]);
}

function clearSearch() {
  state.query = '';
  const input = document.getElementById('globalSearch');
  if (input) input.value = '';
  refreshListAndDetail();
}

// ---------------------------------------------------------------- 详情

function renderDetail() {
  if (!parts) return;
  const todo = state.todos.find((row) => row.id === state.selectedId) || null;
  const pane = clear(parts.detail);
  if (!todo) {
    pane.append(detailPlaceholder());
    if (drawer) closeDrawer();
    return;
  }
  fillDetail(pane, todo);
  if (drawer && drawer.holder) fillDetail(clear(drawer.holder), todo, { onClose: closeDrawer });
}

function detailPlaceholder() {
  return el('div', { cls: 'todo-detail__placeholder' }, [
    el('div', { cls: 'empty__icon', attrs: { style: '--empty-icon: url(/icons/check-square.svg)' } }),
    el('h3', { text: '选一条待办看详情' }),
    el('p', { cls: 'secondary', text: '这里会显示说明、要做的事、关联单据和催办记录。' }),
  ]);
}

function fillDetail(container, todo, options = {}) {
  const drawerMode = Boolean(options.onClose);
  const head = el('div', {
    cls: `${drawerMode ? 'drawer__head' : 'card__head'} todo-detail__head`,
  });
  head.append(
    el('div', { cls: 'todo-detail__headmain' }, [
      el('h2', { cls: 'todo-detail__title', text: todo.title || '待办' }),
      el('div', { cls: 'row wrap todo-detail__tags' }, [
        tag(PRIORITY_LABEL[todo.priority] || todo.priority || '普通', priorityTone(todo.priority)),
        tag(STATUS_LABEL[todo.status] || todo.status, statusTone(todo.status)),
        tag(todo.origin === 'ai' ? 'AI 整理' : '人工派发', 'muted'),
      ]),
    ])
  );
  if (drawerMode) {
    head.append(
      el('button', {
        cls: 'btn btn--text btn--sm',
        attrs: { type: 'button', 'data-action': 'close-detail' },
        text: '关闭',
        on: { click: options.onClose },
      })
    );
  }

  const body = el('div', { cls: `${drawerMode ? 'drawer__body' : ''} todo-detail__body`.trim() });
  const info = parseTodoDetail(todo);

  if (info.summary) body.append(el('p', { cls: 'todo-detail__text', text: info.summary }));
  if (info.items.length) {
    body.append(
      detailBlock(
        '要做的事',
        el(
          'ol',
          { cls: 'todo-steps' },
          info.items.map((item, index) =>
            el('li', { cls: 'todo-step' }, [
              el('span', { cls: 'todo-step__no', text: String(index + 1), attrs: { 'aria-hidden': 'true' } }),
              el('span', { cls: 'todo-step__body' }, [
                el('span', { cls: 'todo-step__title', text: item.title || `第 ${index + 1} 项` }),
                item.note ? el('span', { cls: 'todo-step__note', text: item.note }) : null,
              ]),
            ])
          )
        )
      )
    );
  }
  if (info.notes.length) {
    body.append(
      detailBlock(
        '备注',
        el(
          'div',
          { cls: 'todo-detail__notes' },
          info.notes.map((note) => el('p', { cls: 'todo-detail__note', text: note }))
        )
      )
    );
  }
  if (!info.summary && !info.items.length && !info.notes.length) {
    body.append(el('p', { cls: 'todo-detail__note', text: '这条待办没有留下更多说明。' }));
  }

  const refs = Array.isArray(todo.refs) ? todo.refs : [];
  if (refs.length) {
    body.append(detailBlock(`关联单据（${refs.length}）`, el('div', { cls: 'todo-refs' }, refs.map(refNode))));
  }

  const metaRows = [];
  const pushMeta = (key, value) => {
    if (value) metaRows.push([key, String(value)]);
  };
  pushMeta('派发人', todo.assignee_display_name);
  pushMeta('来源群', todo.group_name);
  pushMeta('创建时间', formatMoment(todo.created_at));
  pushMeta('截止时间', todo.due_at ? formatMoment(todo.due_at) : '');
  pushMeta(
    '催办',
    Number(todo.nudge_count || 0) > 0
      ? `催过 ${todo.nudge_count} 次${todo.nudged_at ? `（最近 ${timeAgo(todo.nudged_at)}）` : ''}`
      : ''
  );
  pushMeta('升级', todo.escalated_at ? formatMoment(todo.escalated_at) : '');
  pushMeta('来源消息', todo.source_message_id ? '来自群聊消息' : '');
  pushMeta('AI 置信度', todo.confidence == null ? '' : `${Math.round(Number(todo.confidence) * 100)}%`);
  pushMeta('关联单据', refs.length ? `${refs.length} 张` : '无');

  if (metaRows.length) {
    body.append(
      detailBlock(
        '记录',
        el(
          'dl',
          { cls: 'todo-meta-grid' },
          metaRows.map(([key, value]) =>
            el('div', { cls: 'todo-meta-pair' }, [
              el('dt', { cls: 'todo-meta-grid__key', text: key }),
              el('dd', { cls: 'todo-meta-grid__val', text: value }),
            ])
          )
        )
      )
    );
  }

  const foot = el('div', { cls: `${drawerMode ? 'drawer__foot' : 'card__foot'} todo-detail__foot` }, footActions(todo));

  container.append(head, body, foot);
}

function detailBlock(label, content) {
  const block = el('section', { cls: 'todo-block' });
  if (label) block.append(el('h3', { cls: 'todo-block__label', text: label }));
  for (const child of [].concat(content)) if (child) block.append(child);
  return block;
}

function refNode(ref) {
  const label = String(ref.label || ref.panel || '单据');
  const docNo = String(ref.docNo || '');
  const url = ref.url ? alignMesHost(ref.url) : '';
  const inner = [
    icon('file-text'),
    el('span', { cls: 'todo-ref__label', text: label }),
    docNo ? el('span', { cls: 'todo-ref__no mono', text: docNo }) : null,
  ];
  if (url) {
    return el(
      'a',
      { cls: 'todo-ref', attrs: { href: url, target: '_blank', rel: 'noopener noreferrer', title: '在 MES 里打开这张单据' } },
      inner
    );
  }
  return el('span', { cls: 'todo-ref todo-ref--plain', attrs: { title: '后端没给这张单据的链接' } }, inner);
}

function footActions(todo) {
  const nodes = [];
  if (todo.status !== 'done' && todo.status !== 'dismissed') {
    nodes.push(
      el('button', {
        cls: 'btn btn--danger',
        attrs: { type: 'button', disabled: state.busy ? true : null },
        text: '不处理',
        on: { click: () => updateStatus(todo, 'dismissed') },
      })
    );
  }
  nodes.push(el('span', { cls: 'grow' }));
  const primary = primaryAction(todo);
  if (primary) {
    nodes.push(
      el('button', {
        cls: 'btn btn--primary',
        attrs: { type: 'button', disabled: state.busy ? true : null },
        text: state.busy ? '处理中…' : primary.label,
        on: { click: () => updateStatus(todo, primary.status) },
      })
    );
  }
  return nodes;
}

function primaryAction(todo) {
  if (todo.status === 'pending' || todo.status === 'accepted') return { label: '开始处理', status: 'doing' };
  if (todo.status === 'doing') return { label: '标记完成', status: 'done' };
  if (todo.status === 'done') return { label: '重新打开', status: 'pending' };
  if (todo.status === 'dismissed') return { label: '恢复为待处理', status: 'pending' };
  return null;
}

function selectTodo(id) {
  state.selectedId = id;
  renderList();
  renderDetail();
  if (!media || !media.matches) openDrawer();
}

function openDrawer() {
  if (media && media.matches) return;
  const todo = state.todos.find((row) => row.id === state.selectedId);
  if (!todo) return;
  closeDrawer();
  const holder = el('div', { cls: 'todo-drawer__inner' });
  const root = el('div', {
    cls: 'drawer todo-drawer',
    attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-label': '待办详情' },
  });
  root.append(
    el('div', { cls: 'drawer__mask', on: { click: () => closeDrawer() } }),
    el('div', { cls: 'drawer__panel todo-drawer__panel' }, [holder])
  );
  root.holder = holder;
  const overlay = document.getElementById('overlayRoot') || document.body;
  overlay.append(root);
  drawer = root;
  fillDetail(holder, todo, { onClose: closeDrawer });
  const closeBtn = holder.querySelector('[data-action="close-detail"]');
  if (closeBtn && typeof closeBtn.focus === 'function') closeBtn.focus();
}

function closeDrawer() {
  if (!drawer) return;
  drawer.remove();
  drawer = null;
}

async function updateStatus(todo, status) {
  if (state.busy) return;
  state.busy = true;
  renderDetail();
  let ok = false;
  try {
    const updated = await TODOS.setStatus(todo.id, status);
    if (!mounted) return;
    const next = updated && updated.id ? updated : { ...todo, status };
    const index = state.todos.findIndex((row) => row.id === todo.id);
    if (index >= 0) state.todos[index] = next;
    state.selectedId = '';
    ok = true;
    toast(`已更新为「${STATUS_LABEL[status] || status}」`);
    if (ctx && typeof ctx.refreshCounts === 'function') ctx.refreshCounts();
  } catch (err) {
    if (!mounted) return;
    toast(describe(err), 'error');
  } finally {
    if (!mounted) return;
    state.busy = false;
    if (ok) closeDrawer();
    renderStats();
    refreshListAndDetail();
  }
}

// ---------------------------------------------------------------- AI 发现

// ---------------------------------------------------------------- AI 发现（按业务域分组）

// 24 条事件平铺成一片很难扫。改成：先按业务域分堆（生产/仓储/销售/采购/质量/审批），
// 每堆默认只展开最严重的一条，其余折起来看数量。要细节再点开。
const DOMAIN_ORDER = ['production', 'warehouse', 'sales', 'purchase', 'quality', 'approval'];
const DOMAIN_TONE = {
  production: 'accent',
  warehouse: 'warn',
  sales: '',
  purchase: '',
  quality: 'danger',
  approval: 'muted',
};
const DOMAIN_ICON = {
  production: 'thunderbolt',
  warehouse: 'home',
  sales: 'user',
  purchase: 'inbox',
  quality: 'alert',
  approval: 'check-square',
};

function renderEvents() {
  if (!parts) return;
  // open = 待认领；handled = 已认领但业务还没闭环。
  // 只显示 open 会导致「一认领就从眼前消失」，看不出是谁在跟、跟到哪一步。
  const openEvents = state.events.filter(
    (event) => event.status === 'open' || event.status === 'handled'
  );
  const interval = state.watch && Number(state.watch.interval_seconds) > 0
    ? Math.max(1, Math.round(Number(state.watch.interval_seconds) / 60))
    : 0;
  const bits = [];
  if (interval) bits.push(`每 ${interval} 分钟巡检一次`);
  if (!state.loadingEvents && !state.errorEvents) {
    const pending = openEvents.filter((e) => e.status === 'open').length;
    const claimed = openEvents.filter((e) => e.status === 'handled').length;
    bits.push(claimed ? `待认领 ${pending} · 跟进中 ${claimed}` : `待处理 ${pending} 条`);
  }
  parts.eventsStatus.textContent = bits.join(' · ');

  const body = clear(parts.eventsBody);
  if (state.loadingEvents) {
    body.append(skeletonRows(3));
    return;
  }
  if (state.errorEvents) {
    body.append(
      emptyBox({
        iconName: 'alert',
        title: '巡检结果没读出来',
        text: state.errorEvents,
        actionLabel: '重新加载',
        onAction: () => loadEvents(),
      })
    );
    return;
  }
  if (!openEvents.length) {
    body.append(
      emptyBox({
        iconName: 'thunderbolt',
        title: '巡检没发现新问题',
        text: 'AI 会定时扫 MES 的工单、库存和交期，发现异常就记在这里。',
        actionLabel: '立即巡检',
        onAction: () => loadEvents(),
      })
    );
    return;
  }

  // 先给个总览条：哪个域最要紧一眼看到（这排芯片本身也是筛选器）
  const grouped = groupByDomain(openEvents);
  const activeDomain = state.domainFilter;
  const visible = activeDomain
    ? openEvents.filter((e) => (DOMAIN_ORDER.includes(e.domain) ? e.domain : 'other') === activeDomain)
    : openEvents;
  body.append(domainSummaryRow(grouped, visible.length));

  // 筛选到某域时只画它；筛选已失效（该域没数据了）就自动回到全部
  if (activeDomain && !visible.length) {
    state.domainFilter = '';
    renderEvents();
    return;
  }

  for (const key of DOMAIN_ORDER) {
    if (activeDomain && activeDomain !== key) continue;
    const bucket = grouped[key];
    if (!bucket || !bucket.length) continue;
    body.append(domainSection(key, bucket));
  }
  const others = grouped.other;
  if ((!activeDomain || activeDomain === 'other') && others && others.length) {
    body.append(domainSection('other', others));
  }
}

function groupByDomain(events) {
  const grouped = {};
  for (const event of events) {
    const key = DOMAIN_ORDER.includes(event.domain) ? event.domain : 'other';
    (grouped[key] = grouped[key] || []).push(event);
  }
  // 每个域内部：严重程度优先，其次存在时间久的靠前
  for (const key of Object.keys(grouped)) {
    grouped[key].sort((a, b) => {
      const rank = { critical: 0, warning: 1, info: 2 };
      const ra = rank[a.severity] ?? 3;
      const rb = rank[b.severity] ?? 3;
      if (ra !== rb) return ra - rb;
      return (Number(b.age_hours) || 0) - (Number(a.age_hours) || 0);
    });
  }
  return grouped;
}

function domainLabel(key) {
  const sample = (state.events || []).find((e) => (e.domain || 'other') === key);
  if (sample && sample.domain_label) return sample.domain_label;
  return { production: '生产', warehouse: '仓储', sales: '销售', purchase: '采购', quality: '质量', approval: '审批', other: '其它' }[key] || key;
}

// 顶部总览：一行小标签，点哪个滚到哪个域（域多时靠它快速跳）
/**
 * AI 发现的分类概览条。
 *
 * 这排芯片原来是纯展示（span），点不动；现在每个都能筛选：
 * 点一次只看该域，再点一次回到全部。选中态用 aria-pressed + 描边表示，
 * 不靠颜色堆叠，和列表的筛选口径保持一致。
 */
function domainSummaryRow(grouped, total) {
  const row = el('div', { cls: 'todo-events__summary' });
  const keys = DOMAIN_ORDER.filter((k) => grouped[k] && grouped[k].length);
  if (grouped.other && grouped.other.length) keys.push('other');

  const active = state.domainFilter;

  // 「全部」只在有筛选时出现，避免默认状态下多一个无用按钮
  if (active) {
    row.append(el('button', {
      cls: 'todo-events__pill todo-events__pill--reset',
      attrs: { type: 'button' },
      text: '全部',
      on: { click: () => setDomainFilter('') },
    }));
  }

  for (const key of keys) {
    const bucket = grouped[key];
    const critical = bucket.filter((e) => e.severity === 'critical').length;
    const on = active === key;
    const count = on ? total : bucket.length;
    row.append(
      el('button', {
        cls: 'todo-events__pill' + (on ? ' is-on' : ''),
        attrs: {
          type: 'button',
          'data-tone': DOMAIN_TONE[key] || '',
          'aria-pressed': on ? 'true' : 'false',
          title: on ? '取消筛选' : '只看' + domainLabel(key),
        },
        on: { click: () => setDomainFilter(on ? '' : key) },
      }, [
        el('span', { cls: 'todo-events__pill-name', text: domainLabel(key) }),
        el('span', { cls: 'todo-events__pill-count num', text: String(bucket.length) }),
        critical
          ? el('span', {
              cls: 'todo-events__pill-crit',
              attrs: { title: `其中严重 ${critical} 条` },
              text: `${critical} 严重`,
            })
          : null,
      ])
    );
  }
  row.append(el('span', {
    cls: 'todo-events__total muted',
    text: active ? `已筛出 ${total} 条` : `共 ${total} 条`,
  }));
  return row;
}

/** 切换 AI 发现的域筛选。传空串表示回到全部。 */
function setDomainFilter(domain) {
  state.domainFilter = domain || '';
  renderEvents();
}

// 一个业务域：标题行（图标 + 名称 + 计数）可点折叠，默认只展开第一条
function domainSection(key, events) {
  const collapsed = state.collapsedDomains.has(key);
  const section = el('section', { cls: 'todo-domain', attrs: { 'data-domain': key } });

  const head = el('button', {
    cls: 'todo-domain__head',
    attrs: { type: 'button', 'aria-expanded': collapsed ? 'false' : 'true' },
    on: {
      click: () => {
        if (collapsed) state.collapsedDomains.delete(key);
        else state.collapsedDomains.add(key);
        renderEvents();
      },
    },
  }, [
    el('span', { cls: 'todo-domain__chevron', attrs: { 'aria-hidden': 'true' } }, [icon(collapsed ? 'filter' : 'filter')]),
    el('span', { cls: 'todo-domain__icon' }, [icon(DOMAIN_ICON[key] || 'inbox')]),
    el('span', { cls: 'todo-domain__name', text: domainLabel(key) }),
    el('span', { cls: 'tag todo-domain__count', attrs: { 'data-tone': DOMAIN_TONE[key] || 'muted' }, text: `${events.length} 条` }),
    el('span', { cls: 'todo-domain__hint muted', text: collapsed ? '已收起' : '' }),
  ]);
  section.append(head);

  if (!collapsed) {
    const list = el('div', { cls: 'todo-domain__list' });
    const shown = events.slice(0, state.domainExpanded.has(key) ? events.length : 1);
    for (const event of shown) list.append(eventRow(event));
    if (events.length > shown.length) {
      list.append(
        el('button', {
          cls: 'btn btn--text btn--block todo-domain__more',
          attrs: { type: 'button' },
          text: `展开其余 ${events.length - shown.length} 条`,
          on: { click: () => { state.domainExpanded.add(key); renderEvents(); } },
        })
      );
    } else if (state.domainExpanded.has(key) && events.length > 1) {
      list.append(
        el('button', {
          cls: 'btn btn--text btn--block todo-domain__more',
          attrs: { type: 'button' },
          text: '收起',
          on: { click: () => { state.domainExpanded.delete(key); renderEvents(); } },
        })
      );
    }
    section.append(list);
  }
  return section;
}

function eventRow(event) {
  const actions = el('div', { cls: 'todo-event__actions' });
  const mondayUrl = event.monday_url ? alignMesHost(event.monday_url) : '';
  if (mondayUrl) {
    actions.append(
      el('a', {
        cls: 'btn btn--sm',
        attrs: { href: mondayUrl, target: '_blank', rel: 'noopener noreferrer' },
        text: '去 MES 处理',
      })
    );
  }
  const claimed = event.status === 'handled';
  // 已认领的不再给「认领」按钮，改给「撤回」，避免重复认领或误以为没人管
  if (claimed) {
    actions.append(
      el('span', { cls: 'todo-event__owner', attrs: { title: '当前跟进人' } }, [
        el('span', { cls: 'avatar avatar--xs', text: String(event.acked_by || '?').slice(0, 1) }),
        el('span', { cls: 'todo-event__owner-name', text: String(event.acked_by || '已认领') }),
      ])
    );
  } else {
    const ackBtn = el('button', {
      cls: 'btn btn--sm btn--text',
      attrs: { type: 'button', disabled: acking.has(event.id) ? true : null },
      text: acking.has(event.id) ? '认领中…' : '认领',
      on: { click: () => ackEvent(event, ackBtn) },
    });
    actions.append(ackBtn);
  }

  const meta = [];
  // 单据号放最前：认领之后最该看到的就是「在跟哪张单」
  if (event.doc_no) meta.push(el('span', { cls: 'todo-event__doc mono', text: String(event.doc_no) }));
  if (claimed && event.acked_at) {
    meta.push(el('span', { cls: 'todo-event__claimed', text: `${String(event.acked_by || '')} 于 ${formatMoment(event.acked_at) || ''} 认领` }));
  }
  if (Number(event.age_hours) > 0) meta.push(el('span', { text: `已存在 ${formatAge(event.age_hours)}` }));
  if (Number(event.reminders) > 0) meta.push(el('span', { text: `提醒过 ${event.reminders} 次` }));
  if (event.escalated) meta.push(tag('已升级', 'danger'));

  return el('div', {
    cls: 'todo-event',
    attrs: { 'data-claimed': claimed ? 'true' : 'false' },
  }, [
    el('div', { cls: 'todo-event__main' }, [
      el('div', { cls: 'todo-event__head' }, [
        tag(SEVERITY_LABEL[event.severity] || event.severity || '提示', severityTone(event.severity)),
        el('p', { cls: 'todo-event__title', text: String(event.title || '') }),
      ]),
      event.detail
        ? el('p', { cls: 'todo-event__detail', text: String(event.detail), attrs: { title: String(event.detail) } })
        : null,
      event.suggested_action
        ? el('p', { cls: 'todo-event__action' }, [icon('thunderbolt'), el('span', { text: String(event.suggested_action) })])
        : null,
      meta.length ? el('div', { cls: 'todo-event__meta' }, meta) : null,
    ]),
    actions,
  ]);
}

function severityTone(severity) {
  if (severity === 'critical') return 'danger';
  if (severity === 'warning') return 'warn';
  return '';
}

function formatAge(hours) {
  const value = Number(hours);
  if (!Number.isFinite(value) || value <= 0) return '';
  if (value < 1) return '不到 1 小时';
  if (value < 24) return `${Math.round(value)} 小时`;
  return `${Math.round(value / 24)} 天`;
}

async function ackEvent(event, button) {
  if (acking.has(event.id)) return;
  acking.add(event.id);
  if (button) {
    button.disabled = true;
    button.textContent = '认领中…';
  }
  try {
    const updated = await WATCH.ack(event.id);
    if (!mounted) return;
    const index = state.events.findIndex((row) => row.id === event.id);
    if (index >= 0) state.events[index] = updated && updated.id ? updated : { ...event, status: 'acked' };
    toast('已认领，AI 会继续跟这条');
  } catch (err) {
    if (!mounted) return;
    toast(describe(err), 'error');
  } finally {
    acking.delete(event.id);
    if (mounted) {
      renderEvents();
      renderStats();
    }
  }
}

// ---------------------------------------------------------------- 小件

function tag(text, tone) {
  const node = el('span', { cls: 'tag', text });
  if (tone) node.dataset.tone = tone;
  return node;
}

function metaItem(iconName, text) {
  return el('span', { cls: 'todo-meta' }, [icon(iconName), el('span', { text })]);
}

function skeletonRows(count) {
  const box = el('div', { cls: 'todo-skeletons', attrs: { 'aria-hidden': 'true' } });
  for (let index = 0; index < count; index += 1) {
    box.append(
      el('div', { cls: 'list__row todo-row todo-row--skeleton' }, [
        el('span', { cls: 'list__main' }, [
          el('span', { cls: 'skeleton todo-skel todo-skel--title' }),
          el('span', { cls: 'skeleton todo-skel todo-skel--meta' }),
        ]),
        el('span', { cls: 'skeleton todo-skel todo-skel--time' }),
      ])
    );
  }
  return box;
}

function emptyBox({ iconName, title, text, actionLabel, onAction }) {
  const box = el('div', { cls: 'empty todo-empty' }, [
    el('div', { cls: 'empty__icon', attrs: { style: `--empty-icon: url(/icons/${iconName}.svg)` } }),
    el('h3', { text: title }),
    text ? el('p', { text }) : null,
  ]);
  if (actionLabel) {
    box.append(
      el('button', {
        cls: 'btn btn--primary',
        attrs: { type: 'button' },
        text: actionLabel,
        on: { click: onAction },
      })
    );
  }
  return box;
}