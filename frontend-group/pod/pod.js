/* 语音悬浮窗
   浏览器只连本机 /api/assistant/voice/ws：密钥、人设、工具都在后端。
   这里负责三件事：采麦克风（16 kHz 单声道 PCM16）、播下行语音、把
   字幕与工具结果画出来。

   协议要点（MOSS-Live）：
   - 上行二进制 = 裸 PCM16 帧，20 ms 一帧 320 采样；
   - 下行二进制 = 同样的 PCM16；
   - 下行 JSON 是事件，服务端已过滤白名单；
   - 打断由模型听音频自行判断，前端收到 response.audio.stop 时清播放队列。 */

(() => {
  "use strict";

  // 同源部署时本页可能在 /ai/pod/ 下：接口与 WS 都要走 /ai-api（MES 反代到 AI 后端）。
// 直接写 /api 会打到 MES 自己的接口上，表现为「还没登录」。
const API_BASE = location.pathname.startsWith("/ai/") ? "/ai-api" : "/api";
const WS_PATH = API_BASE + "/assistant/voice/ws";

/** MES 是唯一身份源：浏览器只存 mes_token，语音页同源也要带它。
 *  之前这里只用 credentials:include（cookie），同源后 cookie 不存在，
 *  于是 fetch /auth/me 拿到 401，语音窗一直显示「还没登录」。 */
function mesToken() {
  try {
    const value = localStorage.getItem("mes_token");
    return value && value !== "undefined" ? value : "";
  } catch (_) {
    return "";
  }
}

function authHeaders() {
  const token = mesToken();
  return token ? { Authorization: "Bearer " + token } : {};
}
  const SAMPLE_RATE = 16000;
  const FRAME_SAMPLES = 320; // 20 ms

  const el = {
    pod: document.getElementById("pod"),
    head: document.getElementById("podHead"),
    title: document.getElementById("podTitle"),
    clock: document.getElementById("podClock"),
    body: document.getElementById("podBody"),
    empty: document.getElementById("podEmpty"),
    stream: document.getElementById("podStream"),
    status: document.getElementById("podStatus"),
    call: document.getElementById("btnCall"),
    callIcon: document.getElementById("callIcon"),
    callLabel: document.getElementById("callLabel"),
    mute: document.getElementById("btnMute"),
    hint: document.getElementById("podHint"),
    fold: document.getElementById("btnFold"),
    orb: document.getElementById("podOrb"),
    orbWrap: document.getElementById("podOrbWrap"),
    orbHint: document.getElementById("orbHint"),
    pin: document.getElementById("btnPin"),
    close: document.getElementById("btnClose"),
    gate: document.getElementById("loginGate"),
    retry: document.getElementById("btnRetry"),
    briefing: document.getElementById("podBriefing"),
    briefingText: document.getElementById("briefingText"),
    briefingItems: document.getElementById("briefingItems"),
    briefingTime: document.getElementById("briefingTime"),
    briefingRefresh: document.getElementById("btnBriefingRefresh"),
  };

  const state = {
    phase: "idle",      // idle | connecting | live | error
    muted: false,
    maxSeconds: 900,
    startedAt: 0,
    ws: null,
    wsOpen: false,
    ctx: null,
    stream: null,
    tap: null,
    source: null,
    sink: null,
    playAt: 0,
    playing: new Set(),
    pending: [],
    pip: null,
    orb: false,
    orbWindow: false,
    orbPos: null,
    drag: null,
    userTurn: null,
    aiTurn: null,
    toolCards: new Map(),
    clockTimer: 0,
  };

  // ---------------------------------------------------------------- 工具

  const $ = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };

  function setPhase(phase, statusText) {
    state.phase = phase;
    document.documentElement.dataset.orbLive = phase === "live" ? "1" : "0";
    el.pod.dataset.state = phase;
    if (statusText != null) el.status.textContent = statusText;
    const hung = phase === "live" || phase === "connecting";
    el.call.dataset.mode = hung ? "hangup" : "call";
    el.callIcon.textContent = hung ? "■" : "●";
    el.callLabel.textContent = hung ? "挂断" : "开始通话";
    el.mute.disabled = phase !== "live";
  }

  function scrollToEnd() {
    el.stream.scrollTop = el.stream.scrollHeight;
  }

  function clearStream() {
    el.stream.innerHTML = "";
    state.userTurn = null;
    state.aiTurn = null;
    state.toolCards = new Map();
  }

  function ensureStream() {
    if (!el.stream.hidden) return;
    el.empty.hidden = true;
    el.stream.hidden = false;
  }

  // ---------------------------------------------------------------- 渲染

  function userTurn() {
    ensureStream();
    if (!state.userTurn) {
      const turn = $("div", "turn user");
      const bubble = $("div", "bubble", "");
      turn.append(bubble);
      el.stream.append(turn);
      state.userTurn = { turn, bubble, final: false };
      // 用户开口就表示新的一轮，上一轮的助手气泡不再续写
      state.aiTurn = null;
      state.toolCards = new Map();
    }
    return state.userTurn;
  }

  function aiTurn() {
    ensureStream();
    if (!state.aiTurn) {
      const turn = $("div", "turn ai");
      const bubble = $("div", "bubble pending", "");
      turn.append(bubble);
      el.stream.append(turn);
      state.aiTurn = { turn, bubble };
    }
    return state.aiTurn;
  }

  function setUserText(text) {
    const t = userTurn();
    t.bubble.textContent = text || "";
    scrollToEnd();
  }

  function setAiText(text, done) {
    const t = aiTurn();
    t.bubble.classList.toggle("pending", !done);
    t.bubble.textContent = text || "";
    scrollToEnd();
  }

  function showToolRunning(name, callId) {
    const t = aiTurn();
    const key = callId || name;
    if (state.toolCards.has(key)) return;
    const card = $("div", "tool-card");
    card.dataset.state = "running";
    const head = $("div", "tool-head");
    head.append($("span", "spin"), $("span", null, toolLabel(name)));
    card.append(head);
    t.turn.insertBefore(card, t.bubble);
    state.toolCards.set(key, card);
    scrollToEnd();
  }

  function showToolDone(payload) {
    const t = aiTurn();
    const key = payload.callId || payload.name;
    const card = state.toolCards.get(key) || $("div", "tool-card");
    card.innerHTML = "";
    card.dataset.state = "done";
    card.dataset.kind = payload.kind || "query";
    const head = $("div", "tool-head");

    // 派待办 / 发通知：只说结果，不摆单据行
    if (payload.kind === "todo" || payload.kind === "notice" || payload.kind === "nudge") {
      const verb =
        payload.kind === "todo"
          ? payload.merged
            ? "已并进待办"
            : "已派待办"
          : payload.kind === "nudge"
            ? "已催办"
            : "已通知";
      head.append($("span", null, [verb, payload.person].filter(Boolean).join(" · ")));
      card.append(head);
      const body = payload.title || payload.message || "";
      if (body) card.append($("div", "tool-line", body));
      if (payload.merged && payload.mergedInto) {
        card.append($("div", "tool-note", `并到已有的一条：${payload.mergedInto}`));
      }
      if (payload.duplicated) card.append($("div", "tool-note", "同名待办已存在，未重复派"));
      if (!state.toolCards.has(key)) {
        t.turn.insertBefore(card, t.bubble);
        state.toolCards.set(key, card);
      }
      scrollToEnd();
      return;
    }

    if (payload.ok === false) {
      const reason =
        payload.kind === "limit"
          ? "本次通话派活已达上限"
          : (payload.message || "没办成").replace(/^找不到「.*?」。/, "没找到这个人。").slice(0, 60);
      head.append($("span", null, reason));
      card.append(head);
      if (!state.toolCards.has(key)) {
        t.turn.insertBefore(card, t.bubble);
        state.toolCards.set(key, card);
      }
      scrollToEnd();
      return;
    }

    const count = Number(payload.count || 0);
    head.append($("span", null, `${payload.panelName || "查询"} · ${count} 条`));
    card.append(head);

    const items = Array.isArray(payload.items) ? payload.items : [];
    if (items.length) {
      const list = $("ul", "tool-list");
      items.forEach((item) => {
        const li = $("li");
        li.append($("span", "no", item.title || "—"));
        if (item.status) li.append($("span", null, item.status));
        if (item.extra) li.append($("span", "tag", item.extra));
        list.append(li);
      });
      card.append(list);
    } else if (payload.ok !== false) {
      card.classList.add("empty-result");
      card.append($("div", null, "没有查到符合条件的单据"));
    }
    if (!state.toolCards.has(key)) {
      t.turn.insertBefore(card, t.bubble);
      state.toolCards.set(key, card);
    }
    scrollToEnd();
  }

  function toolLabel(name) {
    const map = {
      query_sales_orders: "正在查销售订单",
      query_work_orders: "正在查生产工单",
      query_purchase_orders: "正在查采购订单",
      query_arrivals: "正在查到货单",
      query_inventory: "正在查库存",
      list_my_todos: "正在查我的待办",
      assign_todo: "正在派待办",
      send_notice: "正在发通知",
      nudge_todo: "正在催办",
      query_todo_status: "正在查待办进度",
      production_briefing: "正在汇总生产情况",
      trace_doc: "正在追这张单",
      doc_detail: "正在看单据明细",
    };
    return map[name] || "正在查 MES";
  }

  // ---------------------------------------------------------------- 音频

  function resampleTo16k(input, inRate) {
    if (inRate === SAMPLE_RATE) return input;
    const ratio = inRate / SAMPLE_RATE;
    const outLength = Math.floor(input.length / ratio);
    const out = new Float32Array(outLength);
    for (let i = 0; i < outLength; i += 1) {
      const pos = i * ratio;
      const lo = Math.floor(pos);
      const hi = Math.min(input.length - 1, lo + 1);
      const frac = pos - lo;
      out[i] = input[lo] * (1 - frac) + input[hi] * frac;
    }
    return out;
  }

  function onMicFrames(frames) {
    if (!state.wsOpen || !state.ctx) return;
    const rate = state.ctx.sampleRate;
    const samples = state.muted ? new Float32Array(frames.length) : frames;
    const resampled = resampleTo16k(samples, rate);

    for (let i = 0; i < resampled.length; i += 1) {
      const v = Math.max(-1, Math.min(1, resampled[i]));
      state.pending.push((v * 32767) | 0);
    }
    while (state.pending.length >= FRAME_SAMPLES) {
      const frame = new Int16Array(FRAME_SAMPLES);
      for (let i = 0; i < FRAME_SAMPLES; i += 1) frame[i] = state.pending[i];
      state.pending = state.pending.slice(FRAME_SAMPLES);
      state.ws.send(frame.buffer);
    }
  }

  /** 麦克风停掉时补静音，保持上游音频流连续（模型靠它判断语音起止）。 */
  function pumpSilence() {
    if (!state.wsOpen) return;
    const frame = new Int16Array(FRAME_SAMPLES);
    state.pending = [];
    state.ws.send(frame.buffer);
  }

  function playPcm(arrayBuffer) {
    if (!state.ctx) return;
    const i16 = new Int16Array(arrayBuffer);
    if (!i16.length) return;
    const buffer = state.ctx.createBuffer(1, i16.length, SAMPLE_RATE);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < i16.length; i += 1) channel[i] = i16[i] / 32768;

    const node = state.ctx.createBufferSource();
    node.buffer = buffer;
    node.connect(state.ctx.destination);

    const now = state.ctx.currentTime;
    const start = Math.max(now + 0.05, state.playAt);
    node.start(start);
    state.playAt = start + buffer.duration;
    state.playing.add(node);
    document.documentElement.dataset.orbSpeaking = "1";
    node.onended = () => {
      state.playing.delete(node);
      if (!state.playing.size) delete document.documentElement.dataset.orbSpeaking;
    };
  }

  function stopPlayback() {
    state.playing.forEach((node) => {
      try { node.stop(); } catch (_) { /* 已结束 */ }
    });
    state.playing.clear();
    state.playAt = 0;
    delete document.documentElement.dataset.orbSpeaking;
  }

  // ---------------------------------------------------------------- 通话

  async function openAudio() {
    if (state.ctx) return;
    const ctx = new AudioContext({ sampleRate: SAMPLE_RATE, latencyHint: "interactive" });
    await ctx.resume();
    // 相对路径：本页可能挂在 /ai/pod/ 下，绝对 /pod/ 会打到 MES 上 404，
    // 表现为「麦克风初始化失败」（addModule 抛错，与权限无关）。
    await ctx.audioWorklet.addModule("pcm-worklet.js");

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    const source = ctx.createMediaStreamSource(stream);
    const tap = new AudioWorkletNode(ctx, "pcm-tap");
    const sink = ctx.createGain();
    sink.gain.value = 0; // 必须有下游节点，worklet 才会持续被拉取
    source.connect(tap);
    tap.connect(sink);
    sink.connect(ctx.destination);
    tap.port.onmessage = (event) => onMicFrames(event.data);

    state.ctx = ctx;
    state.stream = stream;
    state.source = source;
    state.tap = tap;
    state.sink = sink;
  }

  function closeAudio() {
    try { state.tap && (state.tap.port.onmessage = null); } catch (_) {}
    try { state.source && state.source.disconnect(); } catch (_) {}
    try { state.tap && state.tap.disconnect(); } catch (_) {}
    try { state.sink && state.sink.disconnect(); } catch (_) {}
    if (state.stream) state.stream.getTracks().forEach((track) => track.stop());
    stopPlayback();
    if (state.ctx) state.ctx.close().catch(() => {});
    state.ctx = null;
    state.stream = null;
    state.source = null;
    state.tap = null;
    state.sink = null;
    state.pending = [];
  }

  function connectSocket() {
    return new Promise((resolve, reject) => {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const ticket = mesToken();
      // WebSocket 带不了 Authorization 头，后端支持 ?token= 复用同一枚 JWT
      const wsUrl = `${proto}//${location.host}${WS_PATH}` + (ticket ? `?token=${encodeURIComponent(ticket)}` : "");
      const ws = new WebSocket(wsUrl);
      ws.binaryType = "arraybuffer";
      let settled = false;

      ws.onopen = () => {
        state.wsOpen = true;
        settled = true;
        resolve();
      };
      ws.onmessage = (event) => {
        if (typeof event.data === "string") {
          handleEvent(JSON.parse(event.data));
        } else {
          playPcm(event.data);
        }
      };
      ws.onerror = () => {
        if (!settled) {
          settled = true;
          reject(new Error("ws_error"));
        }
      };
      ws.onclose = (event) => {
        state.wsOpen = false;
        if (!settled) {
          settled = true;
          reject(new Error(event.code === 4401 ? "unauthorized" : "ws_closed"));
          return;
        }
        if (state.phase === "live") {
          endCall(event.code === 4401 ? "登录已过期" : "连接已断开");
        }
      };
      state.ws = ws;
    });
  }

  function handleEvent(event) {
    const data = event.data || {};
    switch (event.action) {
      case "pod.live":
        state.maxSeconds = data.maxSeconds || 900;
        state.startedAt = Date.now();
        setPhase("live", "在听…");
        startClock();
        break;
      case "pod.error":
        setPhase("error", data.message || "语音通道异常");
        state.hint.textContent = "点「开始通话」重试";
        break;
      case "pod.turn_end":
        if (state.aiTurn) state.aiTurn.bubble.classList.remove("pending");
        break;
      case "pod.tool":
        if (data.state === "running") showToolRunning(data.name, data.callId);
        else showToolDone(data);
        break;
      case "input.transcript.updated":
      case "input.transcript.done":
        setUserText(data.text);
        if (event.action === "input.transcript.done") {
          setPhase("live", "在想…");
        }
        break;
      case "output.transcript.updated":
        setAiText(data.text, false);
        setPhase("live", "小缔在说…");
        break;
      case "output.transcript.done":
        setAiText(data.text, true);
        notifyAnswer(data.text || "");
        break;
      case "response.audio.stop":
      case "response.interrupted":
        stopPlayback();
        break;
      case "response.ended":
        if (state.aiTurn) state.aiTurn.bubble.classList.remove("pending");
        setPhase("live", "在听…");
        break;
      case "session.closing":
        endCall("服务端已结束会话");
        break;
      case "error":
        console.warn("[pod] upstream error", data);
        break;
      default:
        break;
    }
  }

  /** 回答念完时如果窗口不在前台，用标题闪烁 + 系统通知把人叫回来。 */
  function notifyAnswer(text) {
    if (!text) return;
    if (document.hasFocus()) return;
    const original = "小缔 · 语音";
    let on = false;
    const timer = setInterval(() => {
      on = !on;
      document.title = on ? "● 小缔有回复" : original;
      if (on) flashBadge(true);
    }, 900);
    const stop = () => {
      clearInterval(timer);
      document.title = original;
      flashBadge(false);
    };
    window.addEventListener("focus", stop, { once: true });
    setTimeout(stop, 12000);

    if (!("Notification" in window)) return;
    const show = () => {
      try {
        const note = new Notification("小缔 · 语音", {
          body: text.slice(0, 80),
          tag: "dnx-voice-answer",
          silent: true,
        });
        note.onclick = () => window.focus();
      } catch (_) {
        /* 用户没授权就只靠标题闪烁 */
      }
    };
    if (Notification.permission === "granted") show();
    else if (Notification.permission === "default") {
      Notification.requestPermission().then((state) => {
        if (state === "granted") show();
      });
    }
  }

  function flashBadge(on) {
    el.dot.style.background = on ? "var(--warn)" : "";
  }

  function startClock() {
    stopClock();
    state.clockTimer = setInterval(() => {
      const used = Math.floor((Date.now() - state.startedAt) / 1000);
      const left = Math.max(0, state.maxSeconds - used);
      el.clock.textContent = `${fmt(used)} / ${fmt(state.maxSeconds)}${left < 60 ? " 即将结束" : ""}`;
      if (left === 0) endCall("已达时长上限");
    }, 1000);
  }

  function stopClock() {
    if (state.clockTimer) clearInterval(state.clockTimer);
    state.clockTimer = 0;
    el.clock.textContent = "";
  }

  function fmt(seconds) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  async function startCall() {
    if (state.phase === "connecting" || state.phase === "live") return;
    setPhase("connecting", "正在连接…");
    el.call.disabled = true;
    clearStream();
    stopPlayback();

    try {
      await openAudio();
    } catch (err) {
      el.call.disabled = false;
      if (err && (err.name === "NotAllowedError" || err.name === "SecurityError")) {
        setPhase("error", "没有麦克风权限");
        state.hint.textContent = "请点地址栏的麦克风图标允许后再试";
      } else if (err && err.name === "NotFoundError") {
        setPhase("error", "没有检测到麦克风");
      } else {
        setPhase("error", "麦克风初始化失败");
        console.error("[pod] audio", err);
      }
      closeAudio();
      return;
    }

    try {
      await connectSocket();
    } catch (err) {
      closeAudio();
      settleError(err);
      return;
    } finally {
      el.call.disabled = false;
    }

    state.muted = false;
    el.mute.setAttribute("aria-pressed", "false");
    el.mute.textContent = "静音";
    state.hint.textContent = "说话时会被打断，随时可以插话";
  }

  function settleError(err) {
    const code = err && err.message;
    if (code === "unauthorized") {
      showGate();
      setPhase("idle", "未登录");
    } else {
      setPhase("error", "连接失败，点按钮重试");
    }
  }

  function endCall(reason) {
    stopClock();
    if (state.ws) {
      try { state.ws.close(); } catch (_) {}
      state.ws = null;
    }
    state.wsOpen = false;
    closeAudio();
    setPhase("idle", reason || "未通话");
    el.clock.textContent = "";
    state.hint.textContent = "点「开始通话」重新连";
  }

  function toggleMute() {
    state.muted = !state.muted;
    el.mute.setAttribute("aria-pressed", state.muted ? "true" : "false");
    el.mute.textContent = state.muted ? "已静音" : "静音";
  }

  // ---------------------------------------------------------------- 登录态

  async function checkLogin() {
    try {
      const res = await fetch(API_BASE + "/auth/me", { credentials: "include", headers: authHeaders() });
      return res.ok;
    } catch (_) {
      return false;
    }
  }

  function showGate() {
    el.gate.hidden = false;
    setPhase("idle", "未登录");
  }

  /** 主动汇报：打开悬浮窗就把当前账号权限内的生产概况拉出来（不念，等人问）。 */
  async function loadBriefing() {
    if (!el.briefing) return;
    try {
      const res = await fetch(API_BASE + "/assistant/briefing", { credentials: "include", headers: authHeaders() });
      if (!res.ok) return;
      const payload = await res.json();
      const data = payload.data || {};
      if (!data.text) return;
      el.briefing.hidden = false;
      el.briefingText.textContent = data.text;
      el.briefingTime.textContent = data.generatedAt || "";
      el.briefingItems.innerHTML = "";
      for (const item of (data.items || []).slice(0, 4)) {
        const li = $("li", "briefing__item");
        li.append($("span", "briefing__item-title", item.title || "—"));
        if (item.status) li.append($("span", "briefing__tag", item.status));
        if (item.extra) li.append($("span", "briefing__extra", item.extra));
        el.briefingItems.append(li);
      }
      // 有跳过的板块就说明一句：让他知道报告不是全的
      if (Array.isArray(data.skipped) && data.skipped.length) {
        const note = $("li", "briefing__skip", `无权限未报：${data.skipped.join("、")}`);
        el.briefingItems.append(note);
      }
    } catch (_) {
      /* 拿不到就不显示，不打扰通话 */
    }
  }

  async function boot() {
    const ok = await checkLogin();
    if (!ok) {
      showGate();
      return;
    }
    const res = await fetch(API_BASE + "/assistant/voice/status", { credentials: "include", headers: authHeaders() });
    if (!res.ok) return;
    const payload = await res.json();
    const info = payload.data || {};
    if (!info.available) {
      setPhase("error", info.reason || "语音通道未启用");
      el.call.disabled = true;
      return;
    }
    state.maxSeconds = info.maxSeconds || 900;
    el.hint.textContent = `单次最长 ${Math.round(state.maxSeconds / 60)} 分钟`;
    await loadBriefing();
    el.briefingRefresh?.addEventListener("click", loadBriefing);
  }

  // ---------------------------------------------------------------- 钉在最前

  async function togglePin() {
    if (!("documentPictureInPicture" in window)) {
      el.hint.textContent = "此浏览器不支持置顶，请用 Chrome / Edge";
      return;
    }
    if (state.pip) {
      state.pip.close();
      state.pip = null;
      return;
    }
    try {
      const pip = await window.documentPictureInPicture.requestWindow({
        width: 380,
        height: 600,
      });
      state.pip = pip;
      // 样式与字体要复制过去，否则窗口里是无样式裸 DOM
      const link = pip.document.createElement("link");
      link.rel = "stylesheet";
      link.href = "pod.css";
      pip.document.head.append(link);
      pip.document.body.style.margin = "0";
      pip.document.title = "小缔 · 语音";
      pip.document.body.append(el.pod);
      el.pin.setAttribute("aria-pressed", "true");
      pip.addEventListener("pagehide", () => {
        document.body.append(el.pod);
        el.pin.setAttribute("aria-pressed", "false");
        state.pip = null;
      });
    } catch (err) {
      el.hint.textContent = "置顶窗口打开失败";
      console.warn("[pod] pip", err);
    }
  }

  
// ---------------------------------------------------------------- 悬浮球

const ORB_WINDOW = { width: 148, height: 176 };
const POD_WINDOW = { width: 420, height: 660 };

/** 最小化：能缩窗口就缩成小球，缩不了（当成标签页打开时）就在页面里浮一个球。 */
function enterOrb() {
  if (state.orb) return;
  state.orb = true;
  state.orbPos = { x: window.screenX, y: window.screenY };
  document.documentElement.dataset.orb = "1";
  document.documentElement.dataset.orbLive = state.phase === "live" ? "1" : "0";

  // 只有脚本打开的窗口才允许改尺寸；改不动就退化成页面内浮球
  try {
    window.resizeTo(ORB_WINDOW.width, ORB_WINDOW.height);
  } catch (_) {
    /* 忽略，下面用尺寸判断 */
  }
  window.setTimeout(() => {
    // 缩得动才是「窗口里的球」；缩不动（当成标签页打开）就退化成右下角浮球
    state.orbWindow = window.outerWidth > 0 && window.outerWidth <= ORB_WINDOW.width + 80;
    document.documentElement.classList.toggle("is-inline", !state.orbWindow);
    el.orb.title = state.orbWindow ? "拖动移动位置 · 点击展开" : "点击展开";
    if (el.orbHint) el.orbHint.hidden = state.orbWindow;
  }, 240);
}

function exitOrb() {
  if (!state.orb) return;
  state.orb = false;
  document.documentElement.dataset.orb = "0";
  document.documentElement.classList.remove("is-inline");
  delete document.documentElement.dataset.orbDragging;
  try {
    window.resizeTo(POD_WINDOW.width, POD_WINDOW.height);
  } catch (_) {
    /* 标签页模式不缩 */
  }
  if (state.orbWindow && state.orbPos) {
    try {
      window.moveTo(state.orbPos.x, state.orbPos.y);
    } catch (_) {
      /* 不允许移动就算了 */
    }
  }
  window.setTimeout(() => el.call && el.call.focus(), 60);
}

function bindOrbDrag() {
  if (!el.orb) return;
  el.orb.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    // 先记状态再抓指针：抓取失败（合成事件、老浏览器）不能把整个拖拽逻辑带崩
    state.drag = {
      screenX: event.screenX,
      screenY: event.screenY,
      winX: window.screenX,
      winY: window.screenY,
      moved: false,
    };
    try {
      el.orb.setPointerCapture(event.pointerId);
    } catch (_) {
      /* 抓不到就靠 document 上的事件继续，不影响使用 */
    }
  });
  el.orb.addEventListener("pointermove", (event) => {
    if (!state.drag) return;
    const dx = event.screenX - state.drag.screenX;
    const dy = event.screenY - state.drag.screenY;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
      state.drag.moved = true;
      document.documentElement.dataset.orbDragging = "1";
    }
    if (!state.orbWindow) return;
    try {
      window.moveTo(state.drag.winX + dx, state.drag.winY + dy);
    } catch (_) {
      /* 有些环境禁止移动窗口，球就不动 */
    }
  });
  const finish = () => {
    if (!state.drag) return;
    const moved = state.drag.moved;
    state.drag = null;
    delete document.documentElement.dataset.orbDragging;
    // 拖动过就不算点击，避免一拖就展开
    if (!moved) exitOrb();
  };
  el.orb.addEventListener("pointerup", finish);
  el.orb.addEventListener("dblclick", () => exitOrb());
  el.orb.addEventListener("pointercancel", () => {
    state.drag = null;
  });
}

// ---------------------------------------------------------------- 事件绑定

  el.call.addEventListener("click", () => {
    if (state.phase === "live" || state.phase === "connecting") endCall();
    else startCall();
  });

  el.mute.addEventListener("click", toggleMute);
  el.pin.addEventListener("click", togglePin);

  el.fold.addEventListener("click", () => enterOrb());

  el.close.addEventListener("click", () => {
    if (state.phase === "live") endCall();
    window.close();
  });

  el.retry.addEventListener("click", async () => {
    if (await checkLogin()) {
      el.gate.hidden = true;
      boot();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.code !== "Space") return;
    if (event.target && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
    event.preventDefault();
    if (state.phase === "live") toggleMute();
    else startCall();
  });

  window.addEventListener("beforeunload", () => {
    if (state.phase === "live") endCall();
  });

  bindOrbDrag();
  setPhase("idle", "未通话");
  boot();
})();