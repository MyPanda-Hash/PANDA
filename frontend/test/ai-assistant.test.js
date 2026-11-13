import test from "node:test";
import assert from "node:assert/strict";

import {
  AI_ASSISTANT_FALLBACK_URL,
  openAiAssistant,
  resolveAiAssistantUrl,
  resolveEntryUrl,
  withAiContext,
} from "../src/business/ai-assistant.js";

test("默认指向本机 AI 数字员工 /chat", () => {
  assert.equal(AI_ASSISTANT_FALLBACK_URL, "http://localhost:18091/chat");
  assert.equal(resolveAiAssistantUrl(), AI_ASSISTANT_FALLBACK_URL);
  assert.equal(resolveAiAssistantUrl(""), AI_ASSISTANT_FALLBACK_URL);
  assert.equal(resolveAiAssistantUrl("   "), AI_ASSISTANT_FALLBACK_URL);
});

test("显式传入地址优先，用于部署换域名", () => {
  assert.equal(resolveAiAssistantUrl("https://ai.example.com/chat"), "https://ai.example.com/chat");
});

test("去掉结尾多余斜杠，避免拼出 //chat", () => {
  assert.equal(resolveAiAssistantUrl("https://ai.example.com/"), "https://ai.example.com");
  assert.equal(resolveAiAssistantUrl("https://ai.example.com///"), "https://ai.example.com");
});

test("拿到后端票据地址时走免密跳转", async () => {
  const url = await resolveEntryUrl(async () => ({ ticket: "abc.def", url: "http://localhost:18091/api/auth/mes-sso?ticket=abc.def" }));
  assert.equal(url, "http://localhost:18091/api/auth/mes-sso?ticket=abc.def");
});

test("后端未配置密钥（抛错）时退回对话页", async () => {
  const url = await resolveEntryUrl(async () => {
    throw new Error("503 未配置 AI 单点登录密钥");
  });
  assert.equal(url, AI_ASSISTANT_FALLBACK_URL);
});

test("后端返回空地址时退回对话页", async () => {
  assert.equal(await resolveEntryUrl(async () => ({})), AI_ASSISTANT_FALLBACK_URL);
  assert.equal(await resolveEntryUrl(async () => null), AI_ASSISTANT_FALLBACK_URL);
  assert.equal(await resolveEntryUrl(undefined), AI_ASSISTANT_FALLBACK_URL);
});

test("没拿到票据时打开的仍是对话页，不会误开空白页", async () => {
  const opened = [];
  const target = await openAiAssistant(
    async () => {
      throw new Error("offline");
    },
    { openWindow: (url) => opened.push(url) },
  );
  assert.equal(target, AI_ASSISTANT_FALLBACK_URL);
  assert.deepEqual(opened, [AI_ASSISTANT_FALLBACK_URL]);
});

test("拿到票据后只打开一次后端给的地址", async () => {
  const opened = [];
  const target = await openAiAssistant(
    async () => ({ url: "http://localhost:18091/api/auth/mes-sso?ticket=t1" }),
    { openWindow: (url) => opened.push(url) },
  );
  assert.equal(target, "http://localhost:18091/api/auth/mes-sso?ticket=t1");
  assert.deepEqual(opened, [target]);
});

test("把当前页面上下文带到 AI：面板编码 / 单号 / 操作者", () => {
  const url = withAiContext("http://localhost:18091/chat", {
    panelCode: "SALE_OUT",
    formNo: "SO-20261110-001",
    user: "sales",
  });
  const parsed = new URL(url);
  assert.equal(parsed.searchParams.get("panelCode"), "SALE_OUT");
  assert.equal(parsed.searchParams.get("formNo"), "SO-20261110-001");
  assert.equal(parsed.searchParams.get("user"), "sales");
});

test("没有上下文时地址保持原样，不产生空参数", () => {
  assert.equal(withAiContext("http://localhost:18091/chat", {}), "http://localhost:18091/chat");
  assert.equal(withAiContext("http://localhost:18091/chat", null), "http://localhost:18091/chat");
  assert.equal(withAiContext("http://localhost:18091/chat", { panelCode: "", formNo: "  " }), "http://localhost:18091/chat");
});

test("票据地址已带 query 时追加而不是覆盖", () => {
  const url = withAiContext("http://localhost:18091/api/auth/mes-sso?ticket=t1", { formNo: "SO-1" });
  const parsed = new URL(url);
  assert.equal(parsed.searchParams.get("ticket"), "t1");
  assert.equal(parsed.searchParams.get("formNo"), "SO-1");
});

test("免密跳转与兜底页都会带上上下文", async () => {
  const ctx = { panelCode: "SALE_OUT", formNo: "SO-1", user: "sales" };
  const ticketUrl = await resolveEntryUrl(async () => ({ url: "http://localhost:18091/api/auth/mes-sso?ticket=t1" }), "", ctx);
  assert.equal(new URL(ticketUrl).searchParams.get("panelCode"), "SALE_OUT");

  const fallbackUrl = await resolveEntryUrl(async () => {
    throw new Error("offline");
  }, "", ctx);
  assert.equal(new URL(fallbackUrl).searchParams.get("formNo"), "SO-1");
});

test("openAiAssistant 打开的就是带上下文的地址", async () => {
  const opened = [];
  const target = await openAiAssistant(
    async () => ({ url: "http://localhost:18091/api/auth/mes-sso?ticket=t1" }),
    { openWindow: (url) => opened.push(url), context: { formNo: "SO-20261110-001" } },
  );
  assert.equal(new URL(target).searchParams.get("formNo"), "SO-20261110-001");
  assert.deepEqual(opened, [target]);
});
