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

/**
 * 决定这次点开哪个地址：拿到后端票据地址就走免密，否则退回对话页。
 * 单独抽出来是为了能单测「票据成功 / 失败 / 返回空」三种分支，不依赖浏览器。
 */
export async function resolveEntryUrl(getTicket, fallback) {
  const fallbackUrl = resolveAiAssistantUrl(fallback);
  if (typeof getTicket !== "function") return fallbackUrl;
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
    return url || fallbackUrl;
  } catch {
    return fallbackUrl;
  }
}

/**
 * 新标签进入 AI 数字员工。
 * @param {Function} getTicket 取票据的函数（通常是 apiGetAiSsoTicket）
 * @param {Object} deps 可注入依赖，仅测试用：{ openWindow }
 * @returns {Promise<string>} 实际打开的地址
 */
export async function openAiAssistant(getTicket, deps = {}) {
  const openWindow = deps.openWindow || ((url) => window.open(url, "_blank", "noopener,noreferrer"));
  const placeholder = deps.openWindow ? null : window.open("about:blank", "_blank");
  const target = await resolveEntryUrl(getTicket);

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
