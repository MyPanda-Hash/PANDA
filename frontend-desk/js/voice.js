// 语音助手（#/voice）与对话页右侧的语音停靠面板。
// 语音协议、麦克风、字幕全在 frontend-group/pod 里，由同源 iframe 加载；
// 这里只做外壳：可用性状态卡、生产概况 + 朗读、iframe 的尺寸与失败提示。
//
// 两个出口共用同一批积木（iface/状态/概况），只有外壳密度不同：
//   mount(root, ctx)        整页视图
//   mountPanel(container, ctx) → { close() }  对话页右侧停靠，内部就一个 /pod/?embed=1

import * as apiModule from './api.js';
import * as utilModule from './util.js';

// 同 groups：挂在 /ai/ 下时要解析成 /ai/pod/，否则落到 MES 根路径
const POD_EMBED = utilModule.appPath('pod/?embed=1');
const POD_PAGE = utilModule.appPath('pod/');
// iframe 同源，所以能在 load 后自己检查内容；但被网关/登录页顶掉时 load 照样会触发，
// 因此再看一眼有没有 #pod，并给一个「始终没动静」的兜底计时。
const STAGE_TIMEOUT = 8000;

let pageHandle = null;

function deps(ctx) {
  // chat.js 可能只传薄 ctx；缺字段时回落到直接 import 的同一模块
  return {
    api: (ctx && ctx.api) || apiModule,
    util: (ctx && ctx.util) || utilModule,
  };
}

function currentTheme() {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

function podEmbedSrc() {
  // 深色主题只能靠参数带进去：iframe 是独立文档，读不到外层的 data-theme。
  // 切主题后需要重新挂载才会跟随（见交付说明里的已知限制）。
  return currentTheme() === 'dark' ? `${POD_EMBED}&theme=dark` : POD_EMBED;
}

/** 打开独立语音窗：新标签页，顺手掐掉 opener，避免被它反向操作本页 */
function openStandalone() {
  const win = window.open(POD_PAGE, '_blank');
  if (win) win.opener = null;
}

/** API 失败 → 可读文案 + 下一步动作的类型 */
function errInfo(err) {
  const status = err && typeof err.status === 'number' ? err.status : null;
  if (status === 401) {
    return { kind: 'auth', title: '登录已过期', detail: '语音与概况都要登录态，重新登录后再回本页即可恢复。' };
  }
  if (status === 0) {
    return { kind: 'offline', title: '连不上 AI 后端', detail: '本机的 FastAPI（:8000）没起来，起来后点「重新检查」。' };
  }
  return { kind: 'error', title: '接口返回失败', detail: (err && err.message) || '未知错误' };
}

// ---------------------------------------------------------------- 朗读

/** speechSynthesis 的 error 事件只给英文枚举，翻成人话再展示 */
function describeSpeechError(code) {
  const key = String(code || '').toLowerCase();
  if (/not-allowed|notallowed/.test(key)) return '朗读被浏览器拦住，先点一下页面再试';
  if (/synthesis-failed|synthesis-unavailable/.test(key)) return '系统语音合成失败，可能没有可用的中文音色';
  if (/network/.test(key)) return '语音合成需要联网，当前网络不可用';
  if (/interrupted|cancel/.test(key)) return ''; // 自己点了「停止朗读」，不算失败
  return key ? `朗读失败（${key}）` : '朗读失败';
}

/**
 * 浏览器自带 TTS（window.speechSynthesis），不依赖后端。
 * 中文要显式指定 lang='zh-CN'，否则 Safari/Chrome 会用系统默认英文音色念中文。
 */
function createSpeaker(onChange) {
  const supported = typeof window !== 'undefined'
    && 'speechSynthesis' in window
    && typeof window.SpeechSynthesisUtterance === 'function';
  const speaker = { supported, speaking: false, speak, stop };

  function emit(speaking, error) {
    if (speaker.speaking === speaking && !error) return;
    speaker.speaking = speaking;
    if (typeof onChange === 'function') onChange(speaking, error || null);
  }

  function pickVoice() {
    let voices = [];
    try { voices = window.speechSynthesis.getVoices() || []; } catch (_) { voices = []; }
    // 有 zh-CN 就优先用；一个都没有时交给浏览器默认音色（勉强能念，不去拦）
    return voices.find((voice) => /^zh[-_]?(CN|Hans)/i.test(voice.lang)) || null;
  }

  function stop() {
    if (!supported) return;
    try { window.speechSynthesis.cancel(); } catch (_) { /* 没有队列可取消 */ }
    emit(false);
  }

  function speak(text) {
    const content = String(text || '').trim();
    if (!supported || !content) return;
    stop();
    const utter = new window.SpeechSynthesisUtterance(content);
    utter.lang = 'zh-CN';
    utter.rate = 1.02;
    utter.pitch = 1;
    const voice = pickVoice();
    if (voice) utter.voice = voice;
    utter.onend = () => emit(false);
    utter.onerror = (event) => {
      // 自己点了「停止」也会走 error；describeSpeechError 对这两个枚举返回空串
      emit(false, describeSpeechError(event && event.error));
    };
    try {
      window.speechSynthesis.speak(utter);
      emit(true);
    } catch (err) {
      emit(false, `浏览器拒绝了朗读请求：${(err && err.message) || '未知原因'}`);
    }
  }

  return speaker;
}

// ---------------------------------------------------------------- iframe

/**
 * 主区语音窗。失败时盖一层可读提示（重试 / 打开独立窗口 / 去对话页），
 * 不把 iframe 白屏或浏览器的错误页当结果展示。
 */
function createStage({ util, mode }) {
  const src = podEmbedSrc();
  const frame = util.el('iframe', {
    cls: 'voice-frame',
    attrs: {
      src,
      title: '小缔 · 实时语音窗',
      // 麦克风与自动播放权限要显式传进 iframe，否则里面的 getUserMedia 直接被拒
      allow: 'microphone; autoplay; clipboard-write',
      referrerpolicy: 'same-origin',
    },
  });
  const overlay = util.el('div', { cls: 'voice-frame-error', attrs: { hidden: true } });
  const node = util.el('div', { cls: `voice-stage voice-stage--${mode}` }, [frame, overlay]);
  let watchdog = 0;
  let destroyed = false;

  function probe() {
    try {
      const doc = frame.contentDocument;
      return !!(doc && doc.getElementById('pod'));
    } catch (_) {
      return false;
    }
  }

  function clearWatchdog() {
    if (watchdog) clearTimeout(watchdog);
    watchdog = 0;
  }

  function armWatchdog() {
    clearWatchdog();
    watchdog = setTimeout(() => {
      if (destroyed || probe()) return;
      fail('等了 8 秒还没出现语音界面。可能是 /pod 静态资源被挡，或服务刚重启。');
    }, STAGE_TIMEOUT);
  }

  function fail(message) {
    if (destroyed) return;
    clearWatchdog();
    overlay.replaceChildren(
      util.el('div', { cls: 'empty voice-empty' }, [
        util.el('div', { cls: 'empty__icon', attrs: { style: '--empty-icon: url(/icons/alert.svg)' } }),
        util.el('h3', { text: '语音窗没加载出来' }),
        util.el('p', { text: message }),
        util.el('div', { cls: 'row wrap voice-empty__actions' }, [
          util.el('button', {
            cls: 'btn btn--primary', text: '重试', attrs: { type: 'button' },
            on: { click: retry },
          }),
          util.el('button', {
            cls: 'btn', text: '打开独立窗口', attrs: { type: 'button' },
            on: { click: openStandalone },
          }),
          util.el('a', { cls: 'btn btn--text', text: '先去对话页', attrs: { href: '#/chat' } }),
        ]),
      ])
    );
    overlay.hidden = false;
  }

  function retry() {
    if (destroyed) return;
    overlay.hidden = true;
    overlay.replaceChildren();
    // 换一个 URL 才能让浏览器重新发请求；iframe 的 srcdoc/同址刷新都不靠谱
    frame.src = `${src}${src.includes('?') ? '&' : '?'}retry=${Date.now()}`;
    armWatchdog();
  }

  frame.addEventListener('load', () => {
    if (destroyed) return;
    clearWatchdog();
    if (probe()) {
      overlay.hidden = true;
      overlay.replaceChildren();
      return;
    }
    fail('语音窗返回的内容不是语音界面（可能被代理或登录页顶掉了）。');
  });
  frame.addEventListener('error', () => fail('浏览器没能取到 /pod/?embed=1。'));
  armWatchdog();

  return {
    node,
    destroy() {
      destroyed = true;
      clearWatchdog();
      // 直接删节点就够了：iframe 文档连同里面的 WS / 麦克风 / 定时器一起销毁
      node.remove();
    },
  };
}

// ---------------------------------------------------------------- 状态卡

function buildStatusCard({ api, util, onInfo }) {
  const tagEl = util.el('span', { cls: 'tag', text: '检查中', attrs: { 'data-tone': 'muted' } });
  const recheck = util.el('button', { cls: 'btn btn--text btn--sm', text: '重新检查', attrs: { type: 'button' } });
  const body = util.el('div', { cls: 'card__body voice-status__body' });
  const node = util.el('section', { cls: 'card voice-status' }, [
    util.el('div', { cls: 'card__head' }, [
      util.icon('audio'),
      util.el('h2', { text: '语音通道' }),
      tagEl,
      util.el('div', { cls: 'card__head-actions' }, recheck),
    ]),
    body,
  ]);

  function setTag(text, tone) {
    tagEl.textContent = text;
    if (tone) tagEl.dataset.tone = tone;
    else delete tagEl.dataset.tone;
  }

  function code(text) {
    return util.el('code', { cls: 'voice-code', text });
  }

  function fact(label, value) {
    const shown = value == null || value === '' ? '—' : String(value);
    return util.el('span', { cls: 'voice-fact' }, [
      util.el('span', { cls: 'voice-fact__label', text: label }),
      util.el('b', { cls: 'voice-fact__value', text: shown }),
    ]);
  }

  function renderPending() {
    setTag('检查中', 'muted');
    body.replaceChildren(
      util.el('span', { cls: 'skeleton voice-skel voice-skel--text' }),
      util.el('span', { cls: 'skeleton voice-skel voice-skel--short' })
    );
  }

  function renderAvailable(info) {
    setTag('可用', 'accent');
    const minutes = info.maxSeconds ? `${Math.round(info.maxSeconds / 60)} 分钟` : '—';
    body.replaceChildren(
      util.el('p', { cls: 'voice-status__ok', text: '实时语音可用。在下面的语音窗里点「开始通话」就能直接说话；MES 单据只查不改。' }),
      util.el('div', { cls: 'voice-facts' }, [
        fact('模型', info.model),
        fact('音色', info.voiceId),
        fact('单次最长', minutes),
        fact('派待办 / 通知', info.writeEnabled ? `开启 · 每通 ${info.writeLimit == null ? '—' : info.writeLimit} 条` : '已关闭'),
        fact('进行中会话', info.activeSessions == null ? 0 : info.activeSessions),
      ])
    );
  }

  /** 不可用不是空白：把后端给的 reason 原样显示，再给一份能照着做的检查清单 */
  function renderUnavailable(reason) {
    setTag('不可用', 'danger');
    body.replaceChildren(
      util.el('p', { cls: 'voice-status__reason' }, [
        util.el('span', { cls: 'voice-status__reason-label', text: '后端给出的原因：' }),
        util.el('span', { text: reason || '后端没有给出原因' }),
      ]),
      util.el('p', { cls: 'voice-status__hint', text: '语音关着不影响待办与对话，先用文字也能把活派下去。' }),
      util.el('div', { cls: 'voice-check' }, [
        util.el('h3', { text: '检查配置' }),
        util.el('ol', { cls: 'voice-check__list' }, [
          util.el('li', null, [
            '打开 ', code('doc-nexus-ai/.env'), '，确认 ', code('MOSS_VOICE_ENABLED=true'), '、',
            code('MOSS_API_KEY'), ' 已填、', code('MOSS_REALTIME_URL'), ' 以 ', code('wss://'), ' 开头。',
          ]),
          util.el('li', { text: '改完重启 AI 后端：Python 不用构建，重启进程就会重新读配置。' }),
          util.el('li', null, [
            '回来点右上「重新检查」；也可以直接看 ', code('GET /api/assistant/voice/status'),
            ' 返回的 ', code('available'), ' 与 ', code('reason'), '。',
          ]),
          util.el('li', { text: '还不行就刷新一次页面，或在「对话」页试试录音转文字——两边读的是同一份配置与同一个后端。' }),
        ]),
      ]),
      util.el('div', { cls: 'row wrap voice-status__actions' }, [
        util.el('button', { cls: 'btn btn--primary', text: '重新检查', attrs: { type: 'button' }, on: { click: refresh } }),
        util.el('a', { cls: 'btn btn--text', text: '去对话页', attrs: { href: '#/chat' } }),
      ])
    );
  }

  function renderFailure(info) {
    setTag(info.kind === 'auth' ? '未登录' : '检查失败', 'danger');
    const actions = [];
    if (info.kind === 'auth' && typeof api.legacyLoginUrl === 'function') {
      actions.push(util.el('a', { cls: 'btn btn--primary', text: '重新登录', attrs: { href: api.legacyLoginUrl() } }));
    } else {
      actions.push(util.el('button', { cls: 'btn btn--primary', text: '重新检查', attrs: { type: 'button' }, on: { click: refresh } }));
    }
    actions.push(util.el('a', { cls: 'btn btn--text', text: '去对话页', attrs: { href: '#/chat' } }));
    body.replaceChildren(
      util.el('p', { cls: 'voice-status__reason' }, [
        util.el('span', { cls: 'voice-status__reason-label', text: `${info.title}：` }),
        util.el('span', { text: info.detail }),
      ]),
      util.el('div', { cls: 'row wrap voice-status__actions' }, actions)
    );
  }

  async function refresh() {
    renderPending();
    recheck.disabled = true;
    try {
      const payload = await api.VOICE.status();
      const info = (payload && payload.data) || {};
      if (info.available) renderAvailable(info);
      else renderUnavailable(info.reason);
      if (typeof onInfo === 'function') onInfo(info);
    } catch (err) {
      const failure = errInfo(err);
      renderFailure(failure);
      if (typeof onInfo === 'function') onInfo(null);
    } finally {
      recheck.disabled = false;
    }
  }

  recheck.addEventListener('click', refresh);
  return { node, refresh };
}

// ---------------------------------------------------------------- 生产概况

function buildBriefingCard({ api, util }) {
  const body = util.el('div', { cls: 'card__body voice-brief__body' });
  const speakLabel = util.el('span', { text: '朗读' });
  const speakBtn = util.el('button', {
    cls: 'btn btn--sm',
    attrs: { type: 'button', 'aria-pressed': 'false' },
  }, [util.icon('sound'), speakLabel]);
  const refreshBtn = util.el('button', { cls: 'btn btn--text btn--sm', text: '刷新', attrs: { type: 'button' } });
  const node = util.el('section', { cls: 'card voice-brief' }, [
    util.el('div', { cls: 'card__head' }, [
      util.icon('file-text'),
      util.el('h2', { text: '生产概况' }),
      util.el('span', { cls: 'tag', text: 'AI 汇报' }),
      util.el('div', { cls: 'card__head-actions' }, [speakBtn, refreshBtn]),
    ]),
    body,
  ]);

  let text = '';
  let loaded = false;

  const speaker = createSpeaker((speaking, error) => {
    speakLabel.textContent = speaking ? '停止朗读' : '朗读';
    speakBtn.setAttribute('aria-pressed', speaking ? 'true' : 'false');
    if (error) util.toast(error, 'error');
  });

  function syncSpeakButton() {
    if (!speaker.supported) {
      speakBtn.disabled = true;
      speakBtn.title = '当前浏览器不支持语音合成，没法朗读';
      return;
    }
    speakBtn.disabled = !text;
    speakBtn.title = text ? '用系统语音念这段生产概况' : '还没有概况可念';
  }

  function renderLoading() {
    body.replaceChildren(
      util.el('span', { cls: 'skeleton voice-skel voice-skel--text' }),
      util.el('span', { cls: 'skeleton voice-skel voice-skel--text' }),
      util.el('span', { cls: 'skeleton voice-skel voice-skel--short' })
    );
  }

  function speechNote() {
    if (speaker.supported) return null;
    return util.el('p', { cls: 'voice-brief__note', text: '当前浏览器不支持语音合成（没有 speechSynthesis），只能看文字。' });
  }

  function renderData(data) {
    const lines = [util.el('p', { cls: 'voice-brief__text', text })];
    const items = Array.isArray(data.items) ? data.items.slice(0, 6) : [];
    if (items.length) {
      lines.push(util.el('div', { cls: 'voice-brief__facts' }, items.map((item) => util.el('div', { cls: 'voice-brief__fact' }, [
        util.el('span', { cls: 'voice-brief__fact-label', text: String(item.label || item.title || '—') }),
        util.el('b', { cls: 'voice-brief__fact-value', text: String(item.value == null ? (item.extra || '—') : item.value) }),
      ]))));
    }
    if (Array.isArray(data.skipped) && data.skipped.length) {
      lines.push(util.el('p', { cls: 'voice-brief__note', text: `无权限未报：${data.skipped.join('、')}` }));
    }
    if (data.generatedAt) {
      lines.push(util.el('p', { cls: 'voice-brief__meta', text: `生成于 ${data.generatedAt}` }));
    }
    const note = speechNote();
    if (note) lines.push(note);
    body.replaceChildren(...lines);
  }

  function renderEmpty() {
    body.replaceChildren(
      util.el('div', { cls: 'empty voice-empty' }, [
        util.el('h3', { text: '今天还没有可汇报的内容' }),
        util.el('p', { text: 'AI 还没生成概况；有新的待办或异常后会补上。' }),
        util.el('button', { cls: 'btn btn--primary', text: '刷新', attrs: { type: 'button' }, on: { click: refresh } }),
      ])
    );
  }

  function renderFailure(info) {
    const actions = [];
    if (info.kind === 'auth' && typeof api.legacyLoginUrl === 'function') {
      actions.push(util.el('a', { cls: 'btn btn--primary', text: '重新登录', attrs: { href: api.legacyLoginUrl() } }));
    } else {
      actions.push(util.el('button', { cls: 'btn btn--primary', text: '重试', attrs: { type: 'button' }, on: { click: refresh } }));
    }
    body.replaceChildren(
      util.el('p', { cls: 'voice-brief__note', text: `${info.title}：${info.detail}` }),
      util.el('div', { cls: 'row wrap voice-status__actions' }, actions)
    );
  }

  async function refresh() {
    if (!loaded) renderLoading();
    refreshBtn.disabled = true;
    try {
      const payload = await api.BRIEFING.latest();
      const data = (payload && payload.data) || {};
      const value = String(data.text || '').trim();
      loaded = true;
      if (!value) {
        text = '';
        renderEmpty();
      } else {
        text = value;
        renderData(data);
      }
      if (speaker.speaking) speaker.stop();
    } catch (err) {
      loaded = true;
      text = '';
      renderFailure(errInfo(err));
    } finally {
      refreshBtn.disabled = false;
      syncSpeakButton();
    }
  }

  speakBtn.addEventListener('click', () => {
    if (speaker.speaking) speaker.stop();
    else speaker.speak(text);
  });
  refreshBtn.addEventListener('click', refresh);
  syncSpeakButton();

  return { node, refresh, destroy: () => speaker.stop() };
}

// ---------------------------------------------------------------- 整页

/**
 * 整页语音：按「手机语音助手」的形态组织。
 *
 * 取舍：
 * - 主视觉只留给语音球（pod iframe 本身就是那颗球），不再套「实时对话」卡片头；
 * - 「语音通道可用 / 模型 / 音色 / 时长」压缩成顶部一条状态行，不用大信息卡；
 * - 「使用说明」整块删掉 —— 会用它的人不需要读说明书，需要的人也不会读；
 * - 生产概况保留成右侧窄栏，语音页仍能「朗读概况」，但不抢主视觉。
 */
function buildPage({ api, util }) {
  // 顶部状态行：一个圆点 + 一句状态 + 关键参数，替代原来的大状态卡
  const dot = util.el('span', { cls: 'va-dot', attrs: { 'data-tone': 'muted' } });
  const stateText = util.el('span', { cls: 'va-state', text: '检查中…' });
  const metaText = util.el('span', { cls: 'va-meta' });
  const recheckBtn = util.el('button', { cls: 'btn btn--text btn--sm', text: '重新检查', attrs: { type: 'button' } });
  const externalBtn = util.el('button', { cls: 'btn btn--sm', attrs: { type: 'button' } }, [
    util.icon('phone'),
    util.el('span', { text: '独立窗口' }),
  ]);
  externalBtn.addEventListener('click', openStandalone);

  const briefCard = buildBriefingCard({ api, util });
  const stage = createStage({ util, mode: 'page' });

  let info = null;

  async function refresh() {
    recheckBtn.disabled = true
    stateText.textContent = '检查中…'
    dot.dataset.tone = 'muted'
    try {
      const payload = await api.VOICE.status()
      info = (payload && payload.data) || {}
      if (info.available) {
        dot.dataset.tone = 'accent'
        stateText.textContent = '可以开始对话'
        const bits = []
        if (info.model) bits.push(info.model)
        if (info.voiceId) bits.push('音色 ' + String(info.voiceId).slice(0, 8))
        if (info.maxSeconds) bits.push('单次 ' + Math.round(info.maxSeconds / 60) + ' 分钟')
        if (info.writeEnabled === false) bits.push('只读模式')
        metaText.textContent = bits.join(' · ')
      } else {
        dot.dataset.tone = 'warn'
        stateText.textContent = info.reason || '语音通道不可用'
        metaText.textContent = ''
      }
    } catch (err) {
      dot.dataset.tone = 'danger'
      stateText.textContent = (errInfo(err) || {}).message || '语音通道检查失败'
      metaText.textContent = ''
    } finally {
      recheckBtn.disabled = false
    }
  }

  recheckBtn.addEventListener('click', () => {
    refresh()
    briefCard.refresh()
  })

  const node = util.el('div', { cls: 'voice-page va' }, [
    util.el('div', { cls: 'va-bar' }, [
      dot,
      stateText,
      metaText,
      util.el('span', { cls: 'grow' }),
      recheckBtn,
      externalBtn,
    ]),
    util.el('div', { cls: 'va-main' }, [
      util.el('div', { cls: 'va-stage' }, [stage.node]),
      util.el('div', { cls: 'va-side' }, [briefCard.node]),
    ]),
  ])

  return {
    node,
    async refresh() {
      await Promise.all([refresh(), briefCard.refresh()])
    },
    async destroy() {
      briefCard.destroy()
      stage.destroy()
      node.remove()
    },
  }
}

// ---------------------------------------------------------------- 停靠面板

/**
 * 对话页右侧的语音停靠面板。内部就是同源 iframe /pod/?embed=1，
 * 不复制任何语音逻辑；close() 把 iframe 节点删掉，里面的会话/WS/麦克风随之销毁。
 */
function buildPanel({ api, util, container }) {
  const stateEl = util.el('span', { cls: 'voice-panel__state', text: '检查中', attrs: { 'data-tone': 'muted' } });
  const noteEl = util.el('span', { cls: 'voice-panel__note muted', text: '' });
  const bodyEl = util.el('div', { cls: 'voice-panel__body' });
  const closeBtn = util.el('button', {
    cls: 'btn btn--text btn--sm', text: '收起',
    attrs: { type: 'button', 'aria-label': '收起语音面板' },
  });
  const node = util.el('div', { cls: 'voice-panel' }, [
    util.el('div', { cls: 'voice-panel__head' }, [
      util.icon('audio'),
      util.el('span', { cls: 'voice-panel__title', text: '语音' }),
      stateEl,
      util.el('span', { cls: 'grow' }),
      closeBtn,
    ]),
    bodyEl,
    util.el('div', { cls: 'voice-panel__foot' }, [
      noteEl,
      util.el('span', { cls: 'grow' }),
      util.el('button', {
        cls: 'btn btn--sm', attrs: { type: 'button' }, on: { click: openStandalone },
      }, [util.icon('phone'), util.el('span', { text: '独立窗口' })]),
    ]),
  ]);

  let stage = null;
  let destroyed = false;

  function showStage() {
    if (stage) {
      if (bodyEl.firstChild !== stage.node) bodyEl.replaceChildren(stage.node);
      return;
    }
    stage = createStage({ util, mode: 'panel' });
    bodyEl.replaceChildren(stage.node);
  }

  function showUnavailable(reason) {
    if (stage) {
      stage.destroy();
      stage = null;
    }
    bodyEl.replaceChildren(
      util.el('div', { cls: 'voice-panel__off' }, [
        util.el('div', { cls: 'empty__icon', attrs: { style: '--empty-icon: url(/icons/alert.svg)' } }),
        util.el('p', { cls: 'voice-panel__off-title', text: '语音通道不可用' }),
        util.el('p', { cls: 'voice-panel__off-reason', text: reason || '后端没有给出原因' }),
        util.el('div', { cls: 'row wrap voice-panel__off-actions' }, [
          util.el('button', { cls: 'btn btn--sm', text: '重新检查', attrs: { type: 'button' }, on: { click: refreshStatus } }),
          util.el('a', { cls: 'btn btn--sm btn--text', text: '看检查步骤', attrs: { href: '#/voice' } }),
        ]),
      ])
    );
  }

  async function refreshStatus() {
    if (destroyed) return;
    stateEl.textContent = '检查中';
    stateEl.dataset.tone = 'muted';
    try {
      const payload = await api.VOICE.status();
      if (destroyed) return;
      const info = (payload && payload.data) || {};
      if (info.available) {
        stateEl.textContent = '可用';
        stateEl.dataset.tone = 'accent';
        noteEl.textContent = info.maxSeconds ? `单次最长 ${Math.round(info.maxSeconds / 60)} 分钟` : '实时语音';
        showStage();
      } else {
        stateEl.textContent = '不可用';
        stateEl.dataset.tone = 'danger';
        noteEl.textContent = '打字也能干活';
        showUnavailable(info.reason);
      }
    } catch (err) {
      if (destroyed) return;
      const failure = errInfo(err);
      stateEl.textContent = failure.kind === 'auth' ? '未登录' : '不可用';
      stateEl.dataset.tone = 'danger';
      noteEl.textContent = '';
      showUnavailable(failure.kind === 'auth'
        ? '登录已过期，重新登录后再打开这个面板'
        : `${failure.title}：${failure.detail}`);
    }
  }

  function close() {
    if (destroyed) return;
    destroyed = true;
    if (stage) {
      stage.destroy();
      stage = null;
    }
    node.remove();
  }

  closeBtn.addEventListener('click', close);
  container.append(node);
  refreshStatus();

  return { close };
}

// ---------------------------------------------------------------- 出口

export async function mount(root, ctx) {
  const { api, util } = deps(ctx);
  if (pageHandle) {
    await pageHandle.destroy();
    pageHandle = null;
  }
  const handle = buildPage({ api, util });
  pageHandle = handle;
  root.append(handle.node);
  await handle.refresh();
}

export async function unmount() {
  if (!pageHandle) return;
  const handle = pageHandle;
  pageHandle = null;
  await handle.destroy();
}

export function mountPanel(container, ctx) {
  const { api, util } = deps(ctx);
  return buildPanel({ api, util, container });
}
