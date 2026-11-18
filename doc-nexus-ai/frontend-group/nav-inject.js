// 给「AI 数字员工」侧边栏加一个「项目群聊」入口。
//
// 原前端只有构建产物、没有源码，所以不改 bundle：这段脚本由 serve-ai-web.mjs
// 注入 index.html，在浏览器里找到导航栏，克隆一个已有的导航项改成群聊入口。
// 找不到导航栏时退化成左下角悬浮按钮，保证入口始终可达。
(() => {
  const HREF = '/group';
  const LABEL = '项目群聊';
  const MARK = 'data-dnx-group-entry';

  function alreadyInserted(root) {
    return !!root.querySelector(`[${MARK}]`);
  }

  function findNavItems() {
    const candidates = Array.from(
      document.querySelectorAll('a[href^="/"], button[data-href], [role="link"]')
    ).filter((node) => {
      const text = (node.textContent || '').trim();
      if (!text || text.length > 12) return false;
      const rect = node.getBoundingClientRect();
      return rect.width > 40 && rect.height > 24 && rect.top < window.innerHeight * 0.9;
    });
    // 只保留在左侧导航区域里的项（x 靠左、纵向排列）
    const left = candidates.filter((node) => node.getBoundingClientRect().left < 320);
    return left.length ? left : [];
  }

  function buildEntry(template) {
    let node;
    if (template && template.tagName === 'A') {
      node = template.cloneNode(true);
      node.setAttribute('href', HREF);
    } else {
      node = document.createElement('a');
      node.setAttribute('href', HREF);
      node.style.cssText =
        'display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:6px;' +
        'color:inherit;text-decoration:none;font-size:14px;';
    }
    node.setAttribute(MARK, '1');
    node.removeAttribute('aria-current');
    node.classList.remove('active', 'is-active', 'selected');
    // 保留图标元素，只替换文字节点
    const textNodes = Array.from(node.querySelectorAll('*')).filter(
      (child) => child.children.length === 0 && (child.textContent || '').trim()
    );
    if (textNodes.length) {
      textNodes[textNodes.length - 1].textContent = LABEL;
    } else {
      node.textContent = LABEL;
    }
    // 去掉可能残留的角标/数字
    node.querySelectorAll('[class*="badge"], [class*="count"]').forEach((badge) => {
      if (badge.textContent && /^\d+$/.test(badge.textContent.trim())) badge.remove();
    });
    return node;
  }

  function insertIntoNav() {
    if (document.querySelector(`[${MARK}]`)) return true;
    const items = findNavItems();
    if (!items.length) return false;
    const template = items[Math.min(1, items.length - 1)];
    const entry = buildEntry(template);
    const parent = template.parentElement;
    if (!parent) return false;
    parent.insertBefore(entry, template.nextSibling);
    entry.addEventListener('click', () => {
      window.location.href = HREF;
    });
    return true;
  }

  function installFallback() {
    if (document.querySelector(`[${MARK}]`)) return;
    const link = document.createElement('a');
    link.href = HREF;
    link.textContent = LABEL;
    link.setAttribute(MARK, '1');
    link.style.cssText =
      'position:fixed;left:16px;bottom:16px;z-index:2147483000;display:inline-flex;' +
      'align-items:center;gap:6px;height:36px;padding:0 14px;border-radius:18px;' +
      'background:#2f6b58;color:#fff;font-size:13px;text-decoration:none;' +
      'box-shadow:0 1px 2px rgba(31,35,40,.18);';
    document.body.appendChild(link);
  }

  let attempts = 0;
  function tick() {
    attempts += 1;
    if (insertIntoNav()) return;
    if (attempts > 40) {
      installFallback();
      return;
    }
    setTimeout(tick, 250);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tick, { once: true });
  } else {
    tick();
  }

  // 前端是 SPA，切换路由可能重绘侧边栏：保持入口存在。
  const observer = new MutationObserver(() => {
    if (!alreadyInserted(document)) insertIntoNav();
  });
  document.addEventListener('DOMContentLoaded', () => {
    observer.observe(document.body, { childList: true, subtree: true });
  });
})();

// 语音悬浮窗入口：在导航里加一项「语音助手」，点开是一个 380×600 的小窗口，
// 可以一直浮在屏幕上（关掉页面它不会自己消失，是独立的浏览器窗口）。
(() => {
  const MARK = 'data-dnx-voice-entry';
  const LABEL = '语音助手';
  const POD = '/pod';

  function openPod() {
    // 固定窗口名：重复点击只会复用同一个窗口，不会开出第二个
    const win = window.open(
      POD,
      'dnx-voice-pod',
      'width=420,height=660,menubar=no,toolbar=no,location=no,status=no'
    );
    if (win) win.focus();
  }

  function buildEntry(template) {
    let node;
    if (template && template.tagName === 'A') {
      node = template.cloneNode(true);
      node.setAttribute('href', '#voice');
    } else {
      node = document.createElement('a');
      node.setAttribute('href', '#voice');
      node.style.cssText =
        'display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:6px;' +
        'color:inherit;text-decoration:none;font-size:14px;cursor:pointer;';
    }
    node.setAttribute(MARK, '1');
    // 克隆的是群聊入口，必须把它的标记摘掉，否则两个模块会互相以为已插入
    node.removeAttribute('data-dnx-group-entry');
    node.removeAttribute('aria-current');
    node.classList.remove('active', 'is-active', 'selected');
    const textNodes = Array.from(node.querySelectorAll('*')).filter(
      (child) => child.children.length === 0 && (child.textContent || '').trim()
    );
    if (textNodes.length) {
      textNodes[textNodes.length - 1].textContent = LABEL;
    } else {
      node.textContent = LABEL;
    }
    node.querySelectorAll('[class*="badge"], [class*="count"]').forEach((badge) => {
      if (badge.textContent && /^\d+$/.test(badge.textContent.trim())) badge.remove();
    });
    return node;
  }

  function insert() {
    if (document.querySelector(`[${MARK}]`)) return true;
    const groupEntry = document.querySelector('[data-dnx-group-entry]');
    const anchor = groupEntry || null;
    let template = null;
    if (anchor && anchor.parentElement) {
      template = anchor;
    } else {
      const left = Array.from(
        document.querySelectorAll('a[href^="/"], [role="link"]')
      ).filter((node) => node.getBoundingClientRect().left < 320);
      template = left[Math.min(1, left.length - 1)] || null;
    }
    if (!template || !template.parentElement) return false;
    const entry = buildEntry(template);
    template.parentElement.insertBefore(entry, template.nextSibling);
    entry.addEventListener('click', (event) => {
      event.preventDefault();
      openPod();
    });
    return true;
  }

  let attempts = 0;
  function tick() {
    attempts += 1;
    if (insert()) return;
    if (attempts > 40) return;
    setTimeout(tick, 250);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tick, { once: true });
  } else {
    tick();
  }
})();
