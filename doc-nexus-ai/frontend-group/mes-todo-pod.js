/**
 * MES 里的统一助手球（原「待办悬浮球」升级）。
 *
 * 为什么合并：
 * - 原来待办球（本文件）与语音球（工作台内）各占一个角，同屏两颗球互相挤；
 * - 用户要「一个球，两件事」：多少个待办 + 随时能说话。
 *
 * 关键约束（2026-11-17 同源化之后）：
 * - 本脚本由 MES 页面加载，与 AI 后端同源：接口走 /ai-api，带 MES 的 Bearer token；
 *   不再用 SSO 票据换会话（那是旧门户的跨源链路）。
 * - 所有资源/接口路径都不能写死以 / 开头的旧地址（同源后会打到 MES 自己身上）。
 */
(function () {
  'use strict';

  const SCRIPT = document.currentScript;
  const POLL_MS = 15000;
  const BALL = 52;
  const EDGE = 16;
  const POS_KEY = 'dnx-mp-pos';

  // 同源部署（MES 页面里加载本脚本）→ 走 /ai-api；跨源引用时退回脚本自身的源
  const SAME_ORIGIN = !SCRIPT || !SCRIPT.src || new URL(SCRIPT.src).origin === location.origin;
  const AI_ORIGIN = SAME_ORIGIN ? '' : new URL(SCRIPT.src).origin;
  const API = SAME_ORIGIN ? '/ai-api' : AI_ORIGIN + '/api';

  const state = {
    todos: [],
    needLogin: false,
    open: false,
    tab: 'todos',
    pos: { x: -1, y: -1 },
    dragging: false,
    moved: false,
    timer: null,
  };

  const CSS = [
    '.dnx-mp{position:fixed;z-index:2147483000;font-family:inherit}',
    '.dnx-mp__ball{position:relative;width:52px;height:52px;border-radius:50%;',
    '  border:1px solid #2f6b58;background:#2f6b58;color:#fff;cursor:grab;touch-action:none;',
    '  display:flex;flex-direction:column;align-items:center;justify-content:center;',
    '  box-shadow:0 10px 24px -8px rgba(17,106,91,.55),0 2px 6px rgba(0,0,0,.12);',
    '  transition:transform .16s ease,box-shadow .16s ease;-webkit-user-select:none;user-select:none}',
    '.dnx-mp__ball:hover{transform:translateY(-1px) scale(1.04)}',
    '.dnx-mp__ball[data-dragging="true"]{cursor:grabbing;transform:scale(1.06)}',
    '.dnx-mp__ball[data-empty="true"]{background:#fff;color:#2f6b58;border-color:#dce4e1}',
    '.dnx-mp__ball b{font-size:15px;line-height:17px;font-weight:600;font-variant-numeric:tabular-nums}',
    '.dnx-mp__ball span{font-size:10px;line-height:12px;opacity:.85}',
    '.dnx-mp__panel{position:fixed;background:#fff;border:1px solid #dce4e1;border-radius:12px;',
    '  box-shadow:0 18px 48px -16px rgba(0,0,0,.28);display:flex;flex-direction:column;overflow:hidden}',
    '.dnx-mp__bar{display:flex;align-items:center;gap:6px;padding:6px;border-bottom:1px solid #edf1ef}',
    '.dnx-mp__tab{height:26px;padding:0 10px;border-radius:6px;border:0;background:transparent;',
    '  font-size:12px;color:#5d6c67;cursor:pointer;font-family:inherit}',
    '.dnx-mp__tab[aria-selected="true"]{background:#e8f1ef;color:#116a5b;font-weight:600}',
    '.dnx-mp__more{margin-left:auto;font-size:12px;color:#5d6c67;text-decoration:none;padding:0 6px}',
    '.dnx-mp__more:hover{color:#116a5b}',
    '.dnx-mp__close{width:26px;height:26px;border:0;background:transparent;font-size:16px;color:#5d6c67;cursor:pointer}',
    '.dnx-mp__list{overflow-y:auto;padding:4px 0;flex:1 1 auto}',
    '.dnx-mp__item{padding:9px 12px;border-bottom:1px solid #f0ede4}',
    '.dnx-mp__item:last-child{border-bottom:0}',
    '.dnx-mp__title{font-size:13px;line-height:19px;color:#1f2328;margin:0 0 5px;font-weight:500}',
    '.dnx-mp__refs{display:flex;flex-direction:column;gap:4px}',
    '.dnx-mp__ref{display:flex;align-items:center;gap:6px;padding:3px 8px;border:1px solid #e4e1d6;',
    '  border-radius:5px;font-size:12px;color:#1f2328;text-decoration:none}',
    '.dnx-mp__ref:hover{background:#e8f0ed;border-color:#2f6b58}',
    '.dnx-mp__ref em{font-style:normal;color:#4b5158;font-variant-numeric:tabular-nums}',
    '.dnx-mp__ref i{margin-left:auto;font-style:normal;color:#116a5b}',
    '.dnx-mp__empty{padding:18px 12px;font-size:13px;color:#7a8087;text-align:center}',
    '.dnx-mp__frame{width:100%;height:100%;border:0;background:#fff}',
  ].join('');

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function injectStyle() {
    if (document.getElementById('dnx-mp-style')) return;
    const style = el('style');
    style.id = 'dnx-mp-style';
    style.textContent = CSS;
    document.head.append(style);
  }
  function mesToken() {
    try {
      const value = localStorage.getItem('mes_token');
      return value && value !== 'undefined' ? value : '';
    } catch (_) {
      return '';
    }
  }

  /** 同源后不再换会话：直接带 MES 的 token，AI 后端用同一密钥验签。 */
  function withAuth(headers) {
    const token = mesToken();
    const out = headers || {};
    if (token) out.Authorization = 'Bearer ' + token;
    return out;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(value, max));
  }

  function restorePos() {
    try {
      const saved = JSON.parse(localStorage.getItem(POS_KEY) || 'null');
      if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
        state.pos.x = clamp(saved.x, EDGE, window.innerWidth - BALL - EDGE);
        state.pos.y = clamp(saved.y, EDGE, window.innerHeight - BALL - EDGE);
        return;
      }
    } catch (_) {
      /* 读不到就用默认位置 */
    }
    state.pos.x = window.innerWidth - BALL - 20;
    state.pos.y = window.innerHeight - BALL - 20;
  }

  /** 松手后吸附到最近的左右边缘，像手机上的助手球。 */
  function snap() {
    const center = state.pos.x + BALL / 2;
    state.pos.x = center < window.innerWidth / 2 ? EDGE : window.innerWidth - BALL - EDGE;
    try {
      localStorage.setItem(POS_KEY, JSON.stringify(state.pos));
    } catch (_) {
      /* 隐私模式写不了就只在本次生效 */
    }
  }

  async function load() {
    try {
      const res = await fetch(API + '/todos?limit=200', {
        credentials: 'include',
        headers: withAuth({}),
      });
      if (res.status === 401) {
        state.needLogin = true;
        state.todos = [];
        render();
        return;
      }
      if (!res.ok) return;
      const rows = await res.json();
      const list = Array.isArray(rows) ? rows : (rows && rows.data) || [];
      state.needLogin = false;
      state.todos = list.filter(function (item) {
        return item.status !== 'done' && item.status !== 'dismissed';
      });
      render();
    } catch (_) {
      /* 网络抖动不打扰用户 */
    }
  }

  function localizeUrl(url) {
    try {
      const target = new URL(url);
      target.protocol = location.protocol;
      target.host = location.host;
      return target.toString();
    } catch (_) {
      return url;
    }
  }

  /** 面板贴在球旁边，整体不越出视口。 */
  function placePanel(panel, w, h) {
    const onRight = state.pos.x + BALL / 2 > window.innerWidth / 2;
    let left = onRight ? state.pos.x - w - 12 : state.pos.x + BALL + 12;
    left = clamp(left, EDGE, window.innerWidth - w - EDGE);
    let top = state.pos.y + BALL / 2 - h / 2;
    top = clamp(top, EDGE, window.innerHeight - h - EDGE);
    panel.style.left = left + 'px';
    panel.style.top = top + 'px';
    panel.style.width = w + 'px';
    panel.style.height = h + 'px';
  }
  function renderVoiceTab(panel) {
    const frame = el('iframe', 'dnx-mp__frame');
    frame.src = (SAME_ORIGIN ? '/ai' : AI_ORIGIN) + '/pod/?embed=1';
    frame.setAttribute('allow', 'microphone');
    frame.setAttribute('title', '语音助手');
    panel.append(frame);
  }

  function renderTodoTab(panel) {
    const list = el('div', 'dnx-mp__list');
    const count = state.todos.length;
    if (state.needLogin) {
      list.appendChild(el('div', 'dnx-mp__empty', '登录后可以在这里看待办'));
    } else if (!count) {
      list.appendChild(el('div', 'dnx-mp__empty', '没有待处理的待办'));
    } else {
      for (const todo of state.todos) {
        const item = el('div', 'dnx-mp__item');
        item.appendChild(el('p', 'dnx-mp__title', todo.title));
        const refs = Array.isArray(todo.refs) ? todo.refs : [];
        if (refs.length) {
          const box = el('div', 'dnx-mp__refs');
          for (const ref of refs) {
            const link = el('a', 'dnx-mp__ref');
            link.href = localizeUrl(ref.url);
            link.appendChild(el('em', null, (ref.label ? ref.label + ' ' : '') + (ref.docNo || '')));
            link.appendChild(el('i', null, '去处理'));
            box.appendChild(link);
          }
          item.appendChild(box);
        }
        list.appendChild(item);
      }
    }
    panel.append(list);
  }

  function render() {
    let root = document.getElementById('dnx-mp');
    if (!root) {
      root = el('div', 'dnx-mp');
      root.id = 'dnx-mp';
      document.body.append(root);
      root.addEventListener('click', function (event) { event.stopPropagation(); });
    }
    root.innerHTML = '';
    root.style.left = state.pos.x + 'px';
    root.style.top = state.pos.y + 'px';

    const count = state.todos.length;
    const ball = el('button', 'dnx-mp__ball');
    ball.type = 'button';
    ball.dataset.empty = count === 0 ? 'true' : 'false';
    ball.dataset.dragging = state.dragging ? 'true' : 'false';
    ball.setAttribute('aria-label', count ? count + ' 条待处理待办，点击打开助手' : '助手（无待办）');
    ball.append(el('b', null, state.needLogin ? '!' : String(count)), el('span', null, '助手'));
    bindDrag(ball);
    root.appendChild(ball);

    if (!state.open) return;

    const w = Math.min(380, window.innerWidth - EDGE * 2);
    const h = state.tab === 'voice'
      ? Math.min(560, window.innerHeight - EDGE * 2)
      : Math.min(440, window.innerHeight - EDGE * 2);
    const panel = el('div', 'dnx-mp__panel');
    placePanel(panel, w, h);

    const bar = el('div', 'dnx-mp__bar');
    const tabTodos = el('button', 'dnx-mp__tab', '待办 ' + count);
    tabTodos.type = 'button';
    tabTodos.setAttribute('aria-selected', state.tab === 'todos' ? 'true' : 'false');
    tabTodos.addEventListener('click', function () { state.tab = 'todos'; render(); });
    const tabVoice = el('button', 'dnx-mp__tab', '语音');
    tabVoice.type = 'button';
    tabVoice.setAttribute('aria-selected', state.tab === 'voice' ? 'true' : 'false');
    tabVoice.addEventListener('click', function () { state.tab = 'voice'; render(); });
    const more = el('a', 'dnx-mp__more', '完整工作台 ↗');
    more.href = (SAME_ORIGIN ? '/ai' : AI_ORIGIN) + '/#/todos';
    more.target = '_blank';
    more.rel = 'noopener noreferrer';
    const close = el('button', 'dnx-mp__close', '×');
    close.type = 'button';
    close.addEventListener('click', function () { state.open = false; render(); });
    bar.append(tabTodos, tabVoice, more, close);
    panel.append(bar);

    if (state.tab === 'voice') renderVoiceTab(panel);
    else renderTodoTab(panel);

    root.appendChild(panel);
  }
  /** 拖动：跟手、松手吸附左右边缘、位置记忆；位移 < 4px 视为点击。 */
  function bindDrag(ball) {
    let startX = 0;
    let startY = 0;
    let originX = 0;
    let originY = 0;

    ball.addEventListener('pointerdown', function (event) {
      state.dragging = true;
      state.moved = false;
      startX = event.clientX;
      startY = event.clientY;
      originX = state.pos.x;
      originY = state.pos.y;
      if (ball.setPointerCapture) ball.setPointerCapture(event.pointerId);
      ball.dataset.dragging = 'true';
    });

    ball.addEventListener('pointermove', function (event) {
      if (!state.dragging) return;
      const dx = event.clientX - startX;
      const dy = event.clientY - startY;
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) state.moved = true;
      state.pos.x = clamp(originX + dx, EDGE, window.innerWidth - BALL - EDGE);
      state.pos.y = clamp(originY + dy, EDGE, window.innerHeight - BALL - EDGE);
      const root = document.getElementById('dnx-mp');
      if (root) {
        root.style.left = state.pos.x + 'px';
        root.style.top = state.pos.y + 'px';
      }
    });

    // 松手：拖过就吸附；没移动=单击，开合面板。
    // 不能在 pointerdown 上 preventDefault：那会把后续 click 一起吞掉。
    ball.addEventListener('pointerup', function (event) {
      if (!state.dragging) return;
      state.dragging = false;
      if (ball.releasePointerCapture) ball.releasePointerCapture(event.pointerId);
      if (state.moved) {
        snap();
        render();
        return;
      }
      state.open = !state.open;
      render();
    });

    ball.addEventListener('pointercancel', function () {
      state.dragging = false;
      render();
    });
  }

  async function boot() {
    injectStyle();
    restorePos();
    render();
    if (!mesToken()) state.needLogin = true;
    await load();
    state.timer = setInterval(load, POLL_MS);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) load();
    });
    window.addEventListener('resize', function () {
      state.pos.x = clamp(state.pos.x, EDGE, window.innerWidth - BALL - EDGE);
      state.pos.y = clamp(state.pos.y, EDGE, window.innerHeight - BALL - EDGE);
      render();
    });
  }

  // 等 MES 前端把 localStorage 里的 token 写进去再开始
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(boot, 1200); }, { once: true });
  } else {
    setTimeout(boot, 1200);
  }
})();