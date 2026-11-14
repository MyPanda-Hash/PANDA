// 站内通知气泡：像微信消息那样从右下角滑出来。
//
// 被注入到三个地方：门户（18091）、项目群聊（/group）、语音悬浮窗（/pod）。
// 同一个脚本，同源 Cookie 鉴权，只做一件事：轮询未读，弹气泡。
//
// 只提醒一次：弹过的 id 记在 localStorage 里，刷新页面也不会再弹同一件事。
(() => {
  if (window.__dnxNotifyBubble) return;
  window.__dnxNotifyBubble = true;

  const POLL_MS = 8000;
  const AUTO_CLOSE_MS = 9000;
  const SEEN_KEY = 'dnx_notified_ids';
  const MAX_SEEN = 200;
  const MAX_CARDS = 3;

  const KIND_LABEL = {
    todo_assigned: '待办',
    todo_nudged: '催办',
    todo_escalated: '升级',
    notice: '通知',
    system: '系统',
  };
  const KIND_COLOR = {
    todo_assigned: '#2f6b58',
    todo_nudged: '#9a5b16',
    todo_escalated: '#a4372f',
    notice: '#2b5f86',
    system: '#4b5158',
  };

  const seen = new Set(readSeen());

  function readSeen() {
    try {
      const raw = localStorage.getItem(SEEN_KEY);
      const list = raw ? JSON.parse(raw) : [];
      return Array.isArray(list) ? list : [];
    } catch (_) {
      return [];
    }
  }

  function rememberSeen(id) {
    seen.add(id);
    try {
      const list = Array.from(seen).slice(-MAX_SEEN);
      localStorage.setItem(SEEN_KEY, JSON.stringify(list));
    } catch (_) {
      /* 隐私模式下写不了 localStorage，退化成内存去重 */
    }
  }

  // ---- 样式：一次性注入，跟随挂载页面，不依赖外部 CSS ----

  const CSS = `
  .dnx-nb-stack{position:fixed;right:20px;bottom:20px;z-index:2147483001;display:flex;
    flex-direction:column;gap:10px;align-items:flex-end;pointer-events:none;
    font:14px/22px -apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
  .dnx-nb{pointer-events:auto;width:340px;max-width:calc(100vw - 32px);background:#fff;
    border:1px solid #e4e1d6;border-radius:8px;box-shadow:0 8px 24px rgba(31,35,40,.14);
    padding:10px 12px;color:#1f2328;opacity:0;transform:translateX(14px);
    transition:opacity .18s ease,transform .18s ease;cursor:default}
  .dnx-nb.is-in{opacity:1;transform:translateX(0)}
  .dnx-nb.is-out{opacity:0;transform:translateX(14px)}
  .dnx-nb-head{display:flex;align-items:center;gap:8px;margin-bottom:6px}
  .dnx-nb-ava{width:28px;height:28px;border-radius:6px;flex:0 0 auto;display:flex;
    align-items:center;justify-content:center;color:#fff;font-size:13px;font-weight:600}
  .dnx-nb-who{font-size:12px;color:#7a8087;display:flex;align-items:baseline;gap:6px;min-width:0}
  .dnx-nb-who b{color:#1f2328;font-size:13px;font-weight:600}
  .dnx-nb-time{margin-left:auto;font-size:11px;color:#7a8087;white-space:nowrap}
  .dnx-nb-title{font-size:14px;font-weight:600;line-height:20px;margin:0 0 2px;
    overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;
    -webkit-box-orient:vertical}
  .dnx-nb-body{font-size:13px;line-height:19px;color:#4b5158;margin:0;
    overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;
    -webkit-box-orient:vertical;word-break:break-word}
  .dnx-nb-foot{display:flex;justify-content:flex-end;gap:8px;margin-top:8px}
  .dnx-nb-btn{height:26px;padding:0 10px;border-radius:6px;border:1px solid #e4e1d6;
    background:#fff;color:#4b5158;font-size:12px;cursor:pointer;font-family:inherit}
  .dnx-nb-btn:hover{border-color:#d2cec0;background:#f5f4ee}
  .dnx-nb-btn.is-primary{background:#2f6b58;border-color:#2f6b58;color:#fff}
  .dnx-nb-btn.is-primary:hover{filter:brightness(1.06)}
  @media (max-width:420px){
    .dnx-nb-stack{right:10px;left:10px;bottom:10px;align-items:stretch}
    .dnx-nb{width:auto}
  }`;

  function injectStyle() {
    if (document.getElementById('dnx-nb-style')) return;
    const style = document.createElement('style');
    style.id = 'dnx-nb-style';
    style.textContent = CSS;
    document.head.append(style);
  }

  function stack() {
    let node = document.getElementById('dnx-nb-stack');
    if (!node) {
      node = document.createElement('div');
      node.id = 'dnx-nb-stack';
      node.className = 'dnx-nb-stack';
      document.body.append(node);
    }
    return node;
  }

  function timeLabel(iso) {
    const then = new Date(iso).getTime();
    if (!Number.isFinite(then)) return '';
    const mins = Math.floor((Date.now() - then) / 60000);
    if (mins < 1) return '刚刚';
    if (mins < 60) return `${mins} 分钟前`;
    return `${Math.floor(mins / 60)} 小时前`;
  }

  function openTarget(item) {
    const url = item.refType === 'group' || item.groupId ? '/group' : '/group';
    try {
      const win = window.open(url, 'dnx-group');
      if (win) win.focus();
      else window.location.href = url;
    } catch (_) {
      window.location.href = url;
    }
  }

  function markRead(ids) {
    if (!ids.length) return;
    fetch('/api/notifications/read', {
      method: 'POST',
      credentials: 'include',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ids }),
    }).catch(() => {});
  }

  function systemNotify(item) {
    if (!('Notification' in window)) return;
    if (Notification.permission !== 'granted') return;
    if (!document.hidden) return;
    try {
      const note = new Notification(`小缔 · ${KIND_LABEL[item.kind] || '提醒'}`, {
        body: `${item.title}${item.actorName ? `（来自 ${item.actorName}）` : ''}`,
        tag: item.id,
        silent: true,
      });
      note.onclick = () => {
        window.focus();
        openTarget(item);
      };
    } catch (_) {
      /* 桌面通知不可用就只留页内气泡 */
    }
  }

  function show(item) {
    const host = stack();
    const card = document.createElement('div');
    card.className = 'dnx-nb';

    const color = KIND_COLOR[item.kind] || '#2f6b58';
    const head = document.createElement('div');
    head.className = 'dnx-nb-head';

    const ava = document.createElement('div');
    ava.className = 'dnx-nb-ava';
    ava.style.background = color;
    ava.textContent = '缔';

    const who = document.createElement('div');
    who.className = 'dnx-nb-who';
    const name = document.createElement('b');
    name.textContent = `小缔 · ${KIND_LABEL[item.kind] || '提醒'}`;
    who.append(name);
    const actor = document.createElement('span');
    actor.textContent = item.actorName ? `来自 ${item.actorName}` : '';
    who.append(actor);

    const time = document.createElement('span');
    time.className = 'dnx-nb-time';
    time.textContent = timeLabel(item.createdAt);

    head.append(ava, who, time);

    const title = document.createElement('p');
    title.className = 'dnx-nb-title';
    title.textContent = item.title;
    const body = document.createElement('p');
    body.className = 'dnx-nb-body';
    body.textContent = item.body || '';

    const foot = document.createElement('div');
    foot.className = 'dnx-nb-foot';
    const dismiss = document.createElement('button');
    dismiss.className = 'dnx-nb-btn';
    dismiss.type = 'button';
    dismiss.textContent = '知道了';
    const go = document.createElement('button');
    go.className = 'dnx-nb-btn is-primary';
    go.type = 'button';
    go.textContent = '去处理';

    foot.append(dismiss, go);
    card.append(head, title);
    if (item.body) card.append(body);
    card.append(foot);

    // 最多同时在屏 3 条，旧的先收
    const alive = host.querySelectorAll('.dnx-nb');
    if (alive.length >= MAX_CARDS) alive[0].remove();
    host.append(card);
    requestAnimationFrame(() => card.classList.add('is-in'));

    let timer = 0;
    const close = (mark) => {
      clearTimeout(timer);
      card.classList.remove('is-in');
      card.classList.add('is-out');
      setTimeout(() => card.remove(), 200);
      if (mark) markRead([item.id]);
    };
    timer = setTimeout(() => close(false), AUTO_CLOSE_MS);

    card.addEventListener('mouseenter', () => clearTimeout(timer));
    card.addEventListener('mouseleave', () => {
      timer = setTimeout(() => close(false), 3000);
    });
    dismiss.addEventListener('click', (event) => {
      event.stopPropagation();
      close(true);
    });
    go.addEventListener('click', (event) => {
      event.stopPropagation();
      close(true);
      openTarget(item);
    });
  }

  async function poll() {
    let payload = null;
    try {
      const res = await fetch('/api/notifications?unread=1&limit=10', {
        credentials: 'include',
        headers: { accept: 'application/json' },
      });
      if (!res.ok) return;
      payload = await res.json();
    } catch (_) {
      return;
    }
    const items = (payload && payload.data && payload.data.items) || [];
    // 服务端按时间倒序返回，先弹早的那条更符合阅读顺序
    items
      .slice()
      .reverse()
      .forEach((item) => {
        if (seen.has(item.id)) return;
        rememberSeen(item.id);
        show(item);
        systemNotify(item);
      });
  }

  function start() {
    injectStyle();
    poll();
    setInterval(poll, POLL_MS);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) poll();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();