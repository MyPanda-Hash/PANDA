// 小工具：DOM 构造、时间、文案标签、提示条。
// 无构建步骤，浏览器直接当 ES 模块跑。

/** 造元素：el('div', 'cls', '文本') / el('button', {cls:'x', attrs:{...}}, child) */
export function el(tag, opts, children) {
  const node = document.createElement(tag);
  if (typeof opts === 'string') {
    if (opts) node.className = opts;
  } else if (opts) {
    if (opts.cls) node.className = opts.cls;
    if (opts.text != null) node.textContent = String(opts.text);
    if (opts.html != null) node.innerHTML = opts.html;
    for (const [key, value] of Object.entries(opts.attrs || {})) {
      if (value == null || value === false) continue;
      if (key === 'dataset') Object.assign(node.dataset, value);
      else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const [key, value] of Object.entries(opts.on || {})) {
      node.addEventListener(key, value);
    }
  }
  for (const child of [].concat(children == null ? [] : children)) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

/** 设计库图标（/icons/*.svg，mask 着色） */
export function icon(name, cls = '') {
  return el('span', { cls: `icon ${cls}`.trim(), attrs: { 'data-icon': name, 'aria-hidden': 'true' } });
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function timeAgo(iso) {
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

export function formatMoment(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** 完整日期时间区间：跨天写两个日期，同一天只写一个，用于「统计周期」这类需要看清楚的场景。 */
export function formatRange(startIso, endIso) {
  const start = formatMoment(startIso);
  const end = formatMoment(endIso);
  if (!start && !end) return '';
  if (!start) return end;
  if (!end) return start;
  return start.slice(0, 10) === end.slice(0, 10)
    ? `${start} — ${end.slice(11)}`
    : `${start} — ${end}`;
}

export function greet() {
  const h = new Date().getHours();
  if (h < 6) return '夜深了';
  if (h < 11) return '早上好';
  if (h < 14) return '中午好';
  if (h < 18) return '下午好';
  return '晚上好';
}

export const PRIORITY_LABEL = { urgent: '紧急', high: '高', normal: '普通', low: '低' };
export const STATUS_LABEL = {
  pending: '待处理',
  accepted: '已接受',
  doing: '进行中',
  done: '已完成',
  dismissed: '已取消',
};

export const SEVERITY_LABEL = { critical: '严重', warning: '警告', info: '提示' };

export function priorityTone(priority) {
  if (priority === 'urgent') return 'danger';
  if (priority === 'high') return 'warn';
  if (priority === 'low') return 'muted';
  return '';
}

export function statusTone(status) {
  if (status === 'doing') return 'accent';
  if (status === 'done' || status === 'dismissed') return 'muted';
  return '';
}

/**
 * 待办明细结构化：AI 写下来的格式是
 *   「主说明 / （语音派发，来自「某某」）/ · 子项（补充说明）」
 * 直接当一段文字渲染会成一坨，抽屉里拆成说明 + 有序子项。
 * （逻辑沿用群聊页已验收的版本，见 frontend-group/app.js parseTodoDetail）
 */
export function parseTodoDetail(todo) {
  const raw = String(todo.detail || '');
  const summary = [];
  const items = [];
  const notes = [];
  for (const rawLine of raw.split(/\r?\n/)) {
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
  const summaryText = summary.join('\n').trim();
  const sameAsTitle = summaryText && summaryText === String(todo.title || '').trim();
  return {
    summary: sameAsTitle ? '' : summaryText,
    items: items.map((item) => (item.title === todo.title ? { ...item, title: '' } : item)),
    notes,
  };
}

function splitNote(text) {
  const match = text.match(/^([\s\S]*?)[（(]([^（）()]{4,})[）)]$/);
  if (!match) return { title: text, note: '' };
  return { title: match[1].trim(), note: match[2].trim() };
}

/** 顶部提示条（轻量成功/失败提示，不用模态） */
export function toast(text, tone = 'ok') {
  const root = document.getElementById('toastRoot');
  if (!root) return;
  const node = el('div', { cls: 'toast', text, attrs: { role: tone === 'error' ? 'alert' : 'status' } });
  if (tone === 'error') node.dataset.tone = 'error';
  root.append(node);
  setTimeout(() => node.remove(), tone === 'error' ? 4200 : 2400);
}

export function debounce(fn, ms = 200) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

/** 把 MES 链接换成当前能访问的源（后端按配置拼，可能指向另一个回环别名） */
/**
 * 把工作台内部路径解析成真实地址。
 *
 * 为什么不能直接写 '/group/'：同源部署时工作台挂在 /ai/ 下（<base href="/ai/">），
 * 以斜杠开头的绝对路径会**绕过 base** 落到 MES 根路径上，结果群聊 iframe 里
 * 嵌进来的是 MES 自己（2026-11-17 实测踩过）。统一用 baseURI 解析，两种部署都成立。
 */
export function appPath(path) {
  try {
    return new URL(path, document.baseURI).toString();
  } catch (_) {
    return path;
  }
}

export function alignMesHost(url, base) {
  if (!url) return '';
  try {
    const target = new URL(url);
    if (base) {
      const host = new URL(base);
      if (target.port === host.port || /:(4173|5173)$/.test(target.host)) return target.toString();
    }
    return target.toString();
  } catch (_) {
    return url;
  }
}

export function initials(name) {
  const text = String(name || '').trim();
  if (!text) return '?';
  // 中文名取姓（管理员 → 管），英文名取首字母缩写
  if (/[\u4e00-\u9fa5]/.test(text)) return text.slice(0, 1);
  const parts = text.split(/[\s._-]+/).filter(Boolean);
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : text.slice(0, 2)).toUpperCase();
}
