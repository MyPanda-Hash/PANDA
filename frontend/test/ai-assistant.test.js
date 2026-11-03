import test from "node:test";
import assert from "node:assert/strict";

import {
  AI_ASSISTANT_FALLBACK_URL,
  openAiAssistant,
  resolveAiAssistantUrl,
  resolveEntryUrl,
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
