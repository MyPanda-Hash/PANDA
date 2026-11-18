// 员工画像（#/staff）：管理员视角的「这个人用 AI 干了什么」。
//
// 两个数据源，职责不同：
//   1. /admin/employees              —— 周期职能卡片（数字统计 + 模型结论）
//   2. /admin/employees/{id}/memories —— 会话记忆（具体做了哪些事，可回溯到会话）
// 记忆由管理员在页面上手动触发蒸馏（POST .../{id}:distill-sessions），
// 后端把会话正文交给模型提炼后落库，同一周期覆盖写。

import { EMPLOYEES } from './api.js';
import { el, icon, clear, formatMoment, formatRange, timeAgo, toast } from './util.js';

const WIDE_QUERY = '(min-width: 1180px)';
const METRICS = [
  { key: 'chat_sessions', label: '个人会话', unit: '个' },
  { key: 'chat_messages', label: '消息', unit: '条' },
  { key: 'group_messages', label: '群聊发言', unit: '条' },
  { key: 'todo_completed', label: '完成待办', unit: '条' },
  { key: 'todo_assigned', label: '接收待办', unit: '条' },
  { key: 'events_acked', label: '认领发现', unit: '次' },
  { key: 'actions_total', label: '系统操作', unit: '次' },
  { key: 'active_days', label: '活跃天数', unit: '天' },
];

let ctx = null;
let host = null;
let parts = null;
let state = null;
let mounted = false;
let media = null;
let onSearch = null;
let drawer = null;

// ---------------------------------------------------------------- 生命周期

export async function mount(root, context) {
  ctx = context;
  host = root;
  mounted = true;
  media = window.matchMedia(WIDE_QUERY);
  state = {
    profiles: [],
    memories: [],
    loading: true,
    loadingMemories: false,
    distilling: false,
    error: '',
    errorMemories: '',
    selectedId: null,
    keyword: '',
  };

  buildShell();
  render();

  if (typeof media.addEventListener === 'function') {
    media.addEventListener('change', onMediaChange);
  }

  onSearch = (event) => {
    state.keyword = String((event && event.detail) || '').trim();
    renderList();
  };
  window.addEventListener('desk:search', onSearch);

  await loadProfiles();
}

export async function unmount() {
  mounted = false;
  if (media && typeof media.removeEventListener === 'function') {
    media.removeEventListener('change', onMediaChange);
  }
  if (onSearch) window.removeEventListener('desk:search', onSearch);
  onSearch = null;
  closeDrawer();
  if (host) clear(host);
  host = null;
  parts = null;
  state = null;
  ctx = null;
}

function onMediaChange() {
  render();
}

async function loadProfiles() {
  state.loading = true;
  state.error = '';
  render();
  try {
    const rows = await EMPLOYEES.list();
    if (!mounted) return;
    // 同一人每周期一份画像；列表按人取最新一期，否则一个人会出现好几行
    state.profiles = dedupeByUser(Array.isArray(rows) ? rows.filter((row) => row && row.id) : []);
    if (state.profiles.length && !state.profiles.some((p) => p.id === state.selectedId)) {
      state.selectedId = state.profiles[0].id;
    }
  } catch (err) {
    if (!mounted) return;
    state.profiles = [];
    state.error = describe(err);
  }
  if (!mounted) return;
  state.loading = false;
  render();
  if (currentProfile()) await loadMemories();
}

async function loadMemories() {
  const profile = currentProfile();
  if (!profile) return;
  state.loadingMemories = true;
  state.errorMemories = '';
  renderMemories();
  try {
    const rows = await EMPLOYEES.memories(profile.user_id);
    if (!mounted) return;
    state.memories = Array.isArray(rows) ? rows.filter((row) => row && row.id) : [];
  } catch (err) {
    if (!mounted) return;
    state.memories = [];
    state.errorMemories = describe(err);
  }
  if (!mounted) return;
  state.loadingMemories = false;
  renderMemories();
}

async function runDistill() {
  const profile = currentProfile();
  if (!profile || state.distilling) return;
  state.distilling = true;
  renderMemories();
  try {
    await EMPLOYEES.distillSessions(profile.user_id, 30);
    if (!mounted) return;
    toast('会话记忆已更新');
    await loadMemories();
  } catch (err) {
    if (!mounted) return;
    toast(describe(err), 'error');
  } finally {
    if (mounted) {
      state.distilling = false;
      renderMemories();
    }
  }
}

function describe(err) {
  if (!err) return '请求失败';
  if (err.status === 401) return '登录已过期，请重新登录后再试';
  if (err.status === 403) return '只有管理员可以查看员工画像';
  if (err.status === 0) return '连不上 AI 后端，先确认服务在运行';
  return err.message || '请求失败';
}

// ---------------------------------------------------------------- 骨架

function buildShell() {
  clear(host);
  const page = el('div', { cls: 'staff-page' });
  const listBody = el('div', { cls: 'card__body card__body--flush staff-list__body' });
  const detailBody = el('div', { cls: 'staff-detail__body' });
  const memoryBody = el('div', { cls: 'staff-memory__body' });

  const listCard = el('section', { cls: 'card staff-list' }, [
    el('div', { cls: 'card__head' }, [
      el('h2', { cls: 'card__title', text: '团队成员' }),
      el('div', { cls: 'card__head-actions' }, [
        el(
          'button',
          {
            cls: 'icon-btn',
            attrs: { type: 'button', 'aria-label': '刷新', title: '刷新' },
            on: { click: () => loadProfiles() },
          },
          [icon('thunderbolt')]
        ),
      ]),
    ]),
    listBody,
  ]);

  const detailCard = el('section', { cls: 'card staff-detail' }, [detailBody]);

  const memoryCard = el('section', { cls: 'card staff-memory' }, [
    el('div', { cls: 'card__head' }, [
      el('h2', { cls: 'card__title', text: 'AI 会话记忆' }),
      el('span', { cls: 'tag', attrs: { 'data-tone': 'accent' }, text: '会话蒸馏' }),
      el('div', { cls: 'card__head-actions' }, [
        el('span', { cls: 'staff-memory__hint', text: '把最近 30 天的会话蒸成一页记录' }),
        el('button', {
          cls: 'btn btn--primary staff-memory__btn',
          attrs: { type: 'button', 'data-action': 'distill' },
          text: '蒸馏会话',
          on: { click: () => runDistill() },
        }),
      ]),
    ]),
    memoryBody,
  ]);

  // 铺满：列表窄、详情宽、记忆整行横跨（记忆是「最近做了什么」，横向读更顺）
  page.append(
    el('div', { cls: 'staff-grid' }, [listCard, detailCard]),
    memoryCard
  );
  host.append(page);
  parts = { listBody, detailBody, detailCard, memoryBody, memoryCard };
}

function render() {
  renderList();
  renderDetail();
  renderMemories();
}
function renderList() {
  if (!parts) return;
  const body = clear(parts.listBody);

  if (state.loading && !state.profiles.length) {
    for (let i = 0; i < 4; i += 1) {
      body.append(
        el('div', { cls: 'list__row staff-row staff-row--skeleton' }, [
          el('div', { cls: 'skeleton staff-row__skeleton' }),
        ])
      );
    }
    return;
  }

  if (state.error) {
    body.append(emptyBox('alert', '画像没取到', state.error, '重试', () => loadProfiles()));
    return;
  }

  const rows = filtered();
  if (!rows.length) {
    body.append(
      emptyBox(
        'user',
        state.keyword ? '没有匹配的成员' : '还没有员工画像',
        state.keyword ? '换个关键词试试，或清空搜索框。' : '画像由管理员生成后出现，一周期一份。'
      )
    );
    return;
  }

  for (const profile of rows) {
    const name = displayName(profile);
    const active = profile.id === state.selectedId;
    const row = el(
      'button',
      {
        cls: 'list__row staff-row',
        attrs: { type: 'button', 'aria-current': active ? 'true' : 'false' },
        on: { click: () => select(profile.id) },
      },
      [
        el('span', { cls: 'avatar avatar--sm staff-row__avatar', text: initials(name) }),
        el('span', { cls: 'list__main' }, [
          el('span', { cls: 'list__title', text: name }),
          el('span', {
            cls: 'list__meta',
            text: `活跃 ${profile.active_days || 0} 天 · 待办 ${profile.todo_completed || 0}/${profile.todo_assigned || 0}`,
          }),
        ]),
        el('span', { cls: 'tag staff-row__rate', attrs: { 'data-tone': todoTone(profile) }, text: todoRate(profile) }),
      ]
    );
    body.append(row);
  }
}

function renderDetail() {
  if (!parts) return;
  const body = clear(parts.detailBody);

  if (state.loading && !state.profiles.length) {
    body.append(el('div', { cls: 'skeleton staff-detail__skeleton' }));
    return;
  }

  const profile = currentProfile();
  if (!profile) {
    body.append(emptyBox('user', '选一位成员', '左侧列表里点一个人，看这一周期他做了什么、卡在哪。'));
    return;
  }

  const name = displayName(profile);
  body.append(
    el('header', { cls: 'staff-detail__head' }, [
      el('span', { cls: 'avatar staff-detail__avatar', text: initials(name) }),
      el('div', { cls: 'grow' }, [
        el('h2', { cls: 'staff-detail__name', text: name }),
        el('p', {
          cls: 'secondary staff-detail__period',
          text: `${period(profile)} · ${profile.generated_by === 'model' ? 'AI 蒸馏' : '规则生成'}`,
        }),
      ]),
    ])
  );

  const tags = Array.isArray(profile.capability_tags) ? profile.capability_tags : [];
  if (tags.length) {
    body.append(
      el(
        'div',
        { cls: 'tag-row staff-tags' },
        tags.map((tag) => el('span', { cls: 'tag', attrs: { 'data-tone': 'accent' }, text: tag }))
      )
    );
  }

  body.append(
    el(
      'div',
      { cls: 'stats staff-stats' },
      METRICS.map((metric) =>
        el('div', { cls: 'stat' }, [
          el('div', { cls: 'stat__label', text: metric.label }),
          el('div', { cls: 'stat__num num', text: String(profile[metric.key] ?? 0) + metric.unit }),
        ])
      )
    )
  );

  if (profile.avg_response_hours !== null && profile.avg_response_hours !== undefined) {
    body.append(el('p', { cls: 'staff-note secondary', text: `平均响应 ${profile.avg_response_hours} 小时` }));
  }

  if (profile.summary) body.append(block('画像摘要', el('p', { cls: 'staff-text', text: profile.summary })));
  if (profile.activity_digest) {
    body.append(block('活跃摘要', el('p', { cls: 'staff-text secondary', text: profile.activity_digest })));
  }

  const columns = [
    { key: 'strengths', title: '优势', tone: 'ok' },
    { key: 'risks', title: '风险', tone: 'warn' },
    { key: 'focus_areas', title: '关注点', tone: 'muted' },
  ]
    .map(({ key, title, tone }) => {
      const items = Array.isArray(profile[key]) ? profile[key] : [];
      if (!items.length) return null;
      return block(
        title,
        el(
          'ul',
          { cls: 'staff-list-bullets' },
          items.map((item) => el('li', { cls: `staff-bullet staff-bullet--${tone}`, text: item }))
        )
      );
    })
    .filter(Boolean);
  if (columns.length) body.append(el('div', { cls: 'staff-columns' }, columns));

  body.append(
    el('p', {
      cls: 'staff-foot muted',
      text: `生成于 ${formatMoment(profile.generated_at) || profile.generated_at || '未知时间'}`,
    })
  );
}

// ---------------------------------------------------------------- 会话记忆

function renderMemories() {
  if (!parts) return;
  const body = clear(parts.memoryBody);
  const btn = parts.memoryCard.querySelector('[data-action="distill"]');
  if (btn) {
    btn.disabled = Boolean(state.distilling);
    btn.textContent = state.distilling ? '蒸馏中…' : '蒸馏会话';
  }

  if (!currentProfile()) {
    body.append(el('p', { cls: 'staff-memory__empty muted', text: '选一位成员后显示他的 AI 会话记忆。' }));
    return;
  }

  if (state.loadingMemories) {
    body.append(
      el('div', { cls: 'staff-memory__list' }, [
        el('div', { cls: 'skeleton staff-memory__skeleton' }),
        el('div', { cls: 'skeleton staff-memory__skeleton' }),
      ])
    );
    return;
  }

  if (state.errorMemories) {
    body.append(emptyBox('alert', '记忆没取到', state.errorMemories, '重试', () => loadMemories()));
    return;
  }

  if (!state.memories.length) {
    body.append(
      emptyBox(
        'thunderbolt',
        '还没有会话记忆',
        '点右上角「蒸馏会话」，把这个人最近的 AI 会话提炼成一页记录：做了什么、得到什么结论、沉淀了什么经验。',
        '立即蒸馏',
        () => runDistill()
      )
    );
    return;
  }

  body.append(el('div', { cls: 'staff-memory__list' }, state.memories.map(memoryCardEl)));
}

function memoryCardEl(memory) {
  const highlights = Array.isArray(memory.highlights) ? memory.highlights : [];
  const lessons = Array.isArray(memory.lessons) ? memory.lessons : [];
  const tools = Array.isArray(memory.tools) ? memory.tools : [];
  const confidence = Math.round((memory.confidence || 0) * 100);

  const card = el('article', { cls: 'staff-memory__item' });
  card.append(
    el('header', { cls: 'staff-memory__head' }, [
      el('div', { cls: 'staff-memory__title' }, [
        memory.topic
          ? el('span', { cls: 'tag', attrs: { 'data-tone': 'accent' }, text: memory.topic })
          : null,
        el('span', {
          cls: 'staff-memory__when',
          text: `统计区间 ${formatRange(memory.period_start, memory.period_end)} · 生成于 ${formatMoment(memory.created_at) || '未知'}`,
        }),
      ]),
      el('div', { cls: 'staff-memory__meta' }, [
        el('span', { cls: 'staff-memory__stat num', text: `${memory.session_count} 会话` }),
        el('span', { cls: 'staff-memory__stat num', text: `${memory.message_count} 消息` }),
        el('span', {
          cls: 'staff-memory__conf',
          attrs: { title: '证据充分度：会话越多越可信' },
          text: `证据 ${confidence}%`,
        }),
      ]),
    ])
  );

  if (memory.headline) card.append(el('p', { cls: 'staff-memory__headline', text: memory.headline }));

  if (highlights.length) {
    card.append(
      el(
        'ul',
        { cls: 'staff-memory__items' },
        highlights.map((item) =>
          el('li', { cls: 'staff-memory__item-row' }, [
            el('span', { cls: 'staff-memory__dot', attrs: { 'aria-hidden': 'true' } }),
            el('div', { cls: 'grow' }, [
              el('span', { cls: 'staff-memory__item-title', text: item.title || '' }),
              item.detail ? el('span', { cls: 'staff-memory__item-detail', text: item.detail }) : null,
            ]),
          ])
        )
      )
    );
  }

  if (lessons.length) {
    card.append(
      el('div', { cls: 'staff-memory__lessons' }, [
        el('h4', { cls: 'staff-memory__sub', text: '沉淀经验' }),
        el('ul', { cls: 'staff-list-bullets' }, lessons.map((x) => el('li', { cls: 'staff-bullet staff-bullet--ok', text: x }))),
      ])
    );
  }

  if (tools.length) {
    card.append(
      el('div', { cls: 'staff-memory__tools' }, tools.map((t) => el('span', { cls: 'tag', attrs: { 'data-tone': 'muted' }, text: t })))
    );
  }

  return card;
}

// ---------------------------------------------------------------- 交互

function filtered() {
  const keyword = (state.keyword || '').toLowerCase();
  if (!keyword) return state.profiles;
  return state.profiles.filter((profile) => {
    const hay = [displayName(profile), profile.username, profile.summary, profile.activity_digest]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    return hay.includes(keyword);
  });
}

function currentProfile() {
  if (!state) return null;
  return filtered().find((row) => row.id === state.selectedId) || null;
}

function select(id) {
  if (state.selectedId === id) return;
  state.selectedId = id;
  state.memories = [];
  renderList();
  if (media && media.matches) {
    renderDetail();
    renderMemories();
    loadMemories();
  } else {
    renderDetail();
    renderMemories();
    openDrawer();
  }
}

// ---------------------------------------------------------------- 窄屏抽屉

function openDrawer() {
  if (media && media.matches) return;
  const profile = currentProfile();
  if (!profile) return;
  closeDrawer();
  const holder = el('div', { cls: 'staff-drawer__inner' });
  const root = el('div', {
    cls: 'drawer staff-drawer',
    attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-label': '员工画像' },
  });
  root.append(
    el('div', { cls: 'drawer__mask', on: { click: () => closeDrawer() } }),
    el('div', { cls: 'drawer__panel staff-drawer__panel' }, [holder])
  );
  root.holder = holder;
  (document.getElementById('overlayRoot') || document.body).append(root);
  drawer = root;
  if (parts) holder.append(parts.detailBody.cloneNode(true));
}

function closeDrawer() {
  if (drawer && drawer.parentNode) drawer.parentNode.removeChild(drawer);
  drawer = null;
}

// ---------------------------------------------------------------- 小工具

function dedupeByUser(rows) {
  const seen = new Map();
  for (const row of rows) {
    const key = row.user_id || row.id;
    const prev = seen.get(key);
    if (!prev || String(row.generated_at || '') > String(prev.generated_at || '')) {
      seen.set(key, row);
    }
  }
  return [...seen.values()];
}

function displayName(profile) {
  return profile.display_name || profile.username || '未命名成员';
}

function initials(name) {
  return String(name || '?').trim().slice(0, 1) || '?';
}

function period(profile) {
  const start = (profile.period_start || '').slice(5, 10);
  const end = (profile.period_end || '').slice(5, 10);
  return start && end ? `${start} ~ ${end}` : '统计周期未知';
}

function todoRate(profile) {
  const assigned = Number(profile.todo_assigned || 0);
  const done = Number(profile.todo_completed || 0);
  if (!assigned) return '无待办';
  return `${Math.round((done / assigned) * 100)}%`;
}

function todoTone(profile) {
  const assigned = Number(profile.todo_assigned || 0);
  const done = Number(profile.todo_completed || 0);
  if (!assigned) return 'muted';
  const rate = done / assigned;
  if (rate >= 0.8) return 'accent';
  if (rate >= 0.4) return 'warn';
  return 'danger';
}

function block(title, content) {
  return el('section', { cls: 'staff-block' }, [
    el('h3', { cls: 'staff-block__title', text: title }),
    content,
  ]);
}

function emptyBox(iconName, title, text, actionLabel, onAction) {
  const box = el('div', { cls: 'empty staff-empty' }, [
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