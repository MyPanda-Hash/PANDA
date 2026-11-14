// MES 里的待办悬浮窗。
//
// 群聊页有「我的待办」，但人在 MES 里干活时不会切过去看。这里在 MES 界面上
// 放一个小球：实时显示未处理待办的条数，点开列出每条待办关联的单据，
// 点单据直接跳到 MES 对应表单页去处理。
//
// 登录态：MES 和 AI 是两套账号。优先用 MES 现成的 /api/ai/sso-ticket 换 AI 会话，
// 换不到就退化成「去登录」，不假装有数据。
(() => {
  if (window.__dnxMesTodoPod) return;
  window.__dnxMesTodoPod = true;

  const SCRIPT = document.currentScript;
  const AI_ORIGIN = SCRIPT && SCRIPT.src ? new URL(SCRIPT.src).origin : 'http://localhost:18091';
  const POLL_MS = 15000;
  const MES_NATIVE =
    typeof location !== 'undefined' && /:(4173|5173)$/.test(location.host || '');

  // 只在 MES 页面里生效，免得 AI 门户上也冒出来
  if (!MES_NATIVE) return;

  const state = { todos: [], open: false, needLogin: false, timer: 0 };

  const CSS = `
  .dnx-mp{position:fixed;right:20px;bottom:20px;z-index:2147483000;
    font:14px/22px -apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
  .dnx-mp__ball{position:relative;width:48px;height:48px;border-radius:50%;border:1px solid #2f6b58;
    background:#2f6b58;color:#fff;font-size:12px;font-weight:600;cursor:pointer;
    display:flex;flex-direction:column;align-items:center;justify-content:center;gap:0;
    box-shadow:0 6px 16px rgba(0,0,0,.16)}
  .dnx-mp__ball:hover{filter:brightness(1.06)}
  .dnx-mp__ball span{font-size:11px;line-height:12px;opacity:.85}
  .dnx-mp__ball b{font-size:16px;line-height:18px;font-variant-numeric:tabular-nums}
  .dnx-mp__ball[data-empty="true"]{background:#fff;color:#2f6b58;border-color:#e4e1d6}
  .dnx-mp__panel{position:absolute;right:0;bottom:60px;width:340px;max-width:calc(100vw - 32px);
    max-height:min(60vh,520px);display:flex;flex-direction:column;background:#fff;
    border:1px solid #e4e1d6;border-radius:8px;box-shadow:0 8px 24px rgba(31,35,40,.16);overflow:hidden}
  .dnx-mp__head{display:flex;align-items:center;gap:8px;padding:12px;border-bottom:1px solid #e4e1d6}
  .dnx-mp__head b{font-size:14px;color:#1f2328;font-weight:600}
  .dnx-mp__head span{margin-left:auto;font-size:12px;color:#7a8087}
  .dnx-mp__list{overflow-y:auto;padding:4px 0}
  .dnx-mp__item{padding:10px 12px;border-bottom:1px solid #f0ede4}
  .dnx-mp__item:last-child{border-bottom:0}
  .dnx-mp__title{font-size:13px;line-height:20px;color:#1f2328;margin:0 0 6px;font-weight:500}
  .dnx-mp__refs{display:flex;flex-direction:column;gap:4px}
  .dnx-mp__ref{display:flex;align-items:center;gap:6px;padding:4px 8px;border:1px solid #e4e1d6;
    border-radius:6px;color:#2f6b58;font-size:12px;text-decoration:none;background:#fff}
  .dnx-mp__ref:hover{background:#e8f0ed;border-color:#2f6b58}
  .dnx-mp__ref em{font-style:normal;color:#4b5158;font-variant-numeric:tabular-nums;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .dnx-mp__ref i{margin-left:auto;font-style:normal}
  .dnx-mp__empty{padding:24px 12px;text-align:center;color:#7a8087;font-size:13px}
  .dnx-mp__foot{padding:8px 12px;border-top:1px solid #e4e1d6;display:flex;justify-content:flex-end}
  .dnx-mp__foot a{font-size:12px;color:#2f6b58;text-decoration:none}
  `;


  /** 后端拼的链接可能指向另一个回环别名，这里换成当前页面自己的源。 */
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

  function injectStyle() {
    if (document.getElementById('dnx-mp-style')) return;
    const style = document.createElement('style');
    style.id = 'dnx-mp-style';
    style.textContent = CSS;
    document.head.append(style);
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function mesToken() {
    try {
      const value = localStorage.getItem('mes_token');
      return value && value !== 'undefined' ? value : '';
    } catch (_) {
      return '';
    }
  }

  /** 用 MES 的票据换一个 AI 会话；换不到就返回 false。 */
  async function ensureSession() {
    try {
      const me = await fetch(`${AI_ORIGIN}/api/auth/me`, { credentials: 'include' });
      if (me.ok) return true;
    } catch (_) {
      return false;
    }
    const token = mesToken();
    if (!token) return false;
    try {
      const res = await fetch('/api/ai/sso-ticket', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) return false;
      const payload = await res.json();
      const ticket = payload?.data?.ticket;
      if (!ticket) return false;
      const exchange = await fetch(
        `${AI_ORIGIN}/api/auth/mes-sso?ticket=${encodeURIComponent(ticket)}`,
        { credentials: 'include', redirect: 'follow' }
      );
      if (!exchange.ok && exchange.type !== 'opaqueredirect') return false;
      const again = await fetch(`${AI_ORIGIN}/api/auth/me`, { credentials: 'include' });
      return again.ok;
    } catch (_) {
      return false;
    }
  }

  async function load() {
    try {
      const res = await fetch(`${AI_ORIGIN}/api/todos`, { credentials: 'include' });
      if (res.status === 401) {
        state.needLogin = true;
        state.todos = [];
        render();
        return;
      }
      if (!res.ok) return;
      const rows = await res.json();
      state.needLogin = false;
      state.todos = (Array.isArray(rows) ? rows : []).filter(
        (item) => item.status !== 'done' && item.status !== 'dismissed'
      );
      render();
    } catch (_) {
      // 网络抖动不打扰用户
    }
  }

  function render() {
    let root = document.getElementById('dnx-mp');
    if (!root) {
      root = el('div', 'dnx-mp');
      root.id = 'dnx-mp';
      document.body.append(root);
      root.addEventListener('click', (event) => event.stopPropagation());
    }
    root.innerHTML = '';

    const count = state.todos.length;
    const ball = el('button', 'dnx-mp__ball');
    ball.type = 'button';
    ball.dataset.empty = count === 0 ? 'true' : 'false';
    ball.setAttribute('aria-label', count ? `${count} 条待处理待办` : '没有待办');
    ball.append(el('b', null, state.needLogin ? '!' : String(count)), el('span', null, '待办'));
    ball.addEventListener('click', () => {
      state.open = !state.open;
      render();
    });
    root.appendChild(ball);

    if (!state.open) return;

    const panel = el('div', 'dnx-mp__panel');
    const head = el('div', 'dnx-mp__head');
    head.append(el('b', null, '我的待办'), el('span', null, `${count} 条未处理`));
    panel.appendChild(head);

    const list = el('div', 'dnx-mp__list');
    if (state.needLogin) {
      const empty = el('div', 'dnx-mp__empty');
      empty.append(el('div', null, '登录后可以在这里看待办'));
      const link = el('a', 'dnx-mp__ref', '去登录 ↗');
      link.href = `${AI_ORIGIN}/`;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.style.marginTop = '8px';
      empty.appendChild(link);
      list.appendChild(empty);
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
            // 悬浮窗本来就跑在 MES 里，直接用当前源，避免 localhost/127.0.0.1 串台
            link.href = localizeUrl(ref.url);
            link.appendChild(el('em', null, `${ref.label || ''} ${ref.docNo}`.trim()));
            link.appendChild(el('i', null, '去处理'));
            box.appendChild(link);
          }
          item.appendChild(box);
        }
        list.appendChild(item);
      }
    }
    panel.appendChild(list);

    const foot = el('div', 'dnx-mp__foot');
    const open = el('a', null, '在群聊里看待办 ↗');
    open.href = `${AI_ORIGIN}/group`;
    open.target = '_blank';
    open.rel = 'noopener noreferrer';
    foot.appendChild(open);
    panel.appendChild(foot);
    root.appendChild(panel);
  }

  async function boot() {
    injectStyle();
    const ok = await ensureSession();
    if (!ok) state.needLogin = true;
    await load();
    state.timer = setInterval(load, POLL_MS);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) load();
    });
  }

  // 等 MES 前端把 localStorage 里的 token 写进去再开始
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(boot, 1200), { once: true });
  } else {
    setTimeout(boot, 1200);
  }
})();