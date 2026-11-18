// 项目群聊（#/groups）：把 frontend-group 的群聊页嵌进来，而不是重写一份。
//
// 为什么用 iframe 而不是搬代码：群聊页是 2954 行 app.js + 2654 行 app.css，
// 含 org-view 组织架构图、附件、语音、@、群内 AI 分析、待办弹层等一整套交互，
// 而且它已经在 :18092 同源托管（serve-ai-desk.mjs），搬进 SPA 等于重写一遍。
// iframe 同源 → Cookie 直接复用，不用再登一次。
//
// 主题：iframe 是独立文档，读不到外层 data-theme，只能靠 dnx-theme 这个
// localStorage 键（两边共用同一个键，见 frontend-group/index.html 第 12 行）。

import { el, clear, appPath } from './util.js';

// 用 appPath 解析：挂在 /ai/ 下时是 /ai/group/，独立站点时是 /group/
// 带 embed=1：群聊页自己那条顶栏（返回/标题/管理员）与本页标题重复，让它隐藏
const GROUP_SRC = appPath('group/?embed=1');
const STAGE_TIMEOUT = 8000;

let host = null;
let frame = null;
let timer = null;
let onMessage = null;

export async function mount(root) {
  host = root;
  clear(host);
  // 群聊页读的是 dnx-theme（frontend-group/index.html 第 12 行），读不到就跟系统走；
  // 外壳若被 ?theme= 强制过，两边会不一致（浅色外壳里嵌出深色群聊）。
  // 这里把当前外壳主题写进去，让 iframe 首次渲染就跟外层同色。
  try {
    const current = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
    if (localStorage.getItem('dnx-theme') !== current) localStorage.setItem('dnx-theme', current);
  } catch (_) {
    /* 隐私模式下写不了就退回各画各的 */
  }

  const stage = el('div', { cls: 'group-stage' });
  const status = el('div', { cls: 'group-stage__status', attrs: { role: 'status' } }, [
    el('span', { cls: 'skeleton group-stage__skeleton' }),
  ]);

  frame = el('iframe', {
    cls: 'group-frame',
    attrs: {
      src: GROUP_SRC,
      title: '项目群聊',
      // 同源页面，不需要额外权限；剪贴板与麦克风由群聊页自己按需申请
      loading: 'eager',
    },
  });

  frame.addEventListener('load', () => {
    if (status.parentNode) status.remove();
  });

  stage.append(frame, status);
  host.append(stage);

  // 一直被网关/登录页顶掉时 load 也会触发，所以再看一眼里面到底有没有群聊的壳
  timer = window.setTimeout(() => {
    timer = null;
    if (!frame || !frame.contentDocument) return;
    const ready = frame.contentDocument.querySelector('.topbar') || frame.contentDocument.querySelector('body');
    if (!ready || !frame.contentDocument.body.children.length) {
      showFallback(host);
    }
  }, STAGE_TIMEOUT);
}

export async function unmount() {
  if (timer) window.clearTimeout(timer);
  timer = null;
  if (onMessage) window.removeEventListener('message', onMessage);
  onMessage = null;
  if (frame && frame.parentNode) frame.parentNode.removeChild(frame);
  frame = null;
  if (host) clear(host);
  host = null;
}

function showFallback(host) {
  if (!host) return;
  clear(host);
  host.append(
    el('div', { cls: 'empty group-empty' }, [
      el('div', { cls: 'empty__icon', attrs: { style: '--empty-icon: url(/icons/team.svg)' } }),
      el('h3', { text: '群聊没加载出来' }),
      el('p', { text: '多半是登录态失效，或者静态服务把 /group 落到了别的页面。' }),
      el('button', {
        cls: 'btn btn--primary',
        attrs: { type: 'button' },
        text: '在新标签页打开',
        on: { click: () => window.open(GROUP_SRC, '_blank', 'noopener') },
      }),
    ])
  );
}
