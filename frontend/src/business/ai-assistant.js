/**
 * AI 数字员工入口（缔智元数字员工，nginx 静态站点 + Vite SPA）。
 *
 * 进入方式：
 *   1. 正常路径：向 light-mes 后端要一张 60 秒票据，拿免密跳转地址，新标签直接进对话页；
 *   2. 兜底路径：后端未配置密钥（503）或网络失败时，退回「只打开对话页」，让用户自己登录。
 *
 * 为什么先开空白页：取票据是异步的，等 await 回来再 window.open 会被浏览器当弹窗拦掉。
 */

/** 未配置时的默认地址：本机开发环境的对话页 */
export const AI_ASSISTANT_FALLBACK_URL = "http://localhost:18091/chat";

/** 环境变量兜底读取：浏览器读 import.meta.env，Node 单测里没有这个对象 */
function envUrl() {
  try {
    const env = import.meta.env;
    return env && typeof env === "object" ? env.VITE_AI_ASSISTANT_URL : undefined;
  } catch {
    return undefined;
  }
}

/** 解析 AI 对话页地址：显式参数 > 环境变量 VITE_AI_ASSISTANT_URL > 默认地址 */
export function resolveAiAssistantUrl(override) {
  const raw = String(override || envUrl() || AI_ASSISTANT_FALLBACK_URL).trim();
  const url = raw || AI_ASSISTANT_FALLBACK_URL;
  return url.replace(/\/+$/, "");
}

/** 允许带到 AI 侧的页面上下文参数：面板编码、单号、操作者 */
const CONTEXT_KEYS = ["panelCode", "formNo", "user"];

/**
 * 给 AI 地址追加当前页面上下文（panelCode / formNo / user）。
 * 空值不产生参数；已有 query（例如免密票据的 ?ticket=）用 & 追加，不覆盖。
 */
export function withAiContext(url, context) {
  const target = String(url || "").trim();
  if (!target || !context || typeof context !== "object") return target;
  const pairs = CONTEXT_KEYS
    .map((key) => [key, context[key]])
    .filter(([, value]) => value !== undefined && value !== null && String(value).trim() !== "")
    .map(([key, value]) => `${key}=${encodeURIComponent(String(value).trim())}`);
  if (!pairs.length) return target;
  const hashAt = target.indexOf("#");
  const hash = hashAt >= 0 ? target.slice(hashAt) : "";
  const base = hashAt >= 0 ? target.slice(0, hashAt) : target;
  const joiner = base.includes("?") ? "&" : "?";
  return `${base}${joiner}${pairs.join("&")}${hash}`;
}

/**
 * 决定这次点开哪个地址：拿到后端票据地址就走免密，否则退回对话页。
 * 单独抽出来是为了能单测「票据成功 / 失败 / 返回空」三种分支，不依赖浏览器。
 */
export async function resolveEntryUrl(getTicket, fallback, context) {
  const fallbackUrl = resolveAiAssistantUrl(fallback);
  if (typeof getTicket !== "function") return withAiContext(fallbackUrl, context);
  try {
    const payload = await getTicket();
    // 后端既可能直接给字符串地址，也可能给 { ticket, url }；空对象一律当没拿到
    const candidate =
      typeof payload === "string"
        ? payload
        : payload && typeof payload === "object"
          ? payload.url
          : "";
    const url = String(candidate || "").trim();
    return withAiContext(url || fallbackUrl, context);
  } catch {
    return withAiContext(fallbackUrl, context);
  }
}

/**
 * 新标签进入 AI 数字员工。
 * @param {Function} getTicket 取票据的函数（通常是 apiGetAiSsoTicket）
 * @param {Object} deps 可注入依赖：{ openWindow, context }
 *   context = { panelCode, formNo, user }，带上当前页面上下文，AI 首轮就知道你在看哪张单
 * @returns {Promise<string>} 实际打开的地址
 */
export async function openAiAssistant(getTicket, deps = {}) {
  const openWindow = deps.openWindow || ((url) => window.open(url, "_blank", "noopener,noreferrer"));
  const placeholder = deps.openWindow ? null : window.open("about:blank", "_blank");
  const target = await resolveEntryUrl(getTicket, deps.fallback, deps.context);

  if (placeholder && !placeholder.closed) {
    try {
      placeholder.opener = null;
    } catch {
      /* 跨源占位页可能禁止改 opener，忽略即可 */
    }
    placeholder.location.replace(target);
  } else {
    openWindow(target);
  }
  return target;
}
