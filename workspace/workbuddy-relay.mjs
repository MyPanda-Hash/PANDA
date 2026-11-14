#!/usr/bin/env node
/**
 * WorkBuddy → OpenAI 兼容中转（供 doc-nexus-ai / 其它本地应用使用）
 *
 * 背景：WorkBuddy 的模型由 DSH 插件在本机起 OpenAI 兼容 shim（127.0.0.1:629xx），
 * 其 Bearer 原本是进程内随机生成；配合对插件的一行补丁
 * （DSH_WORKBUDDY_SHIM_SECRET，缺省 wb-local-relay-2026）即可被外部复用。
 *
 * 用法: node workbuddy-relay.mjs      # 监听 127.0.0.1:18092
 * 依赖: DSH 正在运行（shim 随 DSH 生命周期存在）
 */
import http from "node:http";
import { execFile } from "node:child_process";

const LISTEN = { host: "127.0.0.1", port: 18092 };
const TOKEN = process.env.DSH_WORKBUDDY_SHIM_SECRET || "wb-local-relay-2026";

let upstreams = [];
let cursor = 0;

function lsofListeningPorts() {
  return new Promise((resolve) => {
    execFile("lsof", ["-nP", "-iTCP", "-sTCP:LISTEN"], { timeout: 5000 }, (err, stdout) => {
      const ports = new Set();
      for (const line of String(stdout || "").split("\n")) {
        if (!/DeepSeek/i.test(line)) continue;
        const m = line.match(/127\.0\.0\.1:(\d+)/);
        if (m) ports.add(Number(m[1]));
      }
      resolve([...ports]);
    });
  });
}

function probe(port) {
  return new Promise((resolve) => {
    const req = http.request(
      { host: "127.0.0.1", port, path: "/healthz", method: "GET", timeout: 1500,
        headers: { authorization: "Bearer " + TOKEN } },
      (res) => { res.resume(); resolve(res.statusCode === 200 ? port : null); }
    );
    req.on("timeout", () => { req.destroy(); resolve(null); });
    req.on("error", () => resolve(null));
    req.end();
  });
}

async function refreshUpstreams() {
  const fromLsof = await lsofListeningPorts();
  const cands = fromLsof.length ? fromLsof : Array.from({ length: 60 }, (_, i) => 62940 + i);
  const found = [];
  await Promise.all(cands.map(async (p) => {
    const ok = await probe(p);
    if (ok) found.push(ok);
  }));
  found.sort((a, b) => a - b);
  upstreams = found;
  return found;
}

function pick() {
  if (upstreams.length === 0) return null;
  const port = upstreams[cursor % upstreams.length];
  cursor++;
  return port;
}

function forward(port, req, res, body) {
  const opts = {
    host: "127.0.0.1", port, method: req.method, path: req.url,
    headers: { ...req.headers, authorization: "Bearer " + TOKEN, host: "127.0.0.1:" + port },
  };
  const up = http.request(opts, (ur) => {
    res.writeHead(ur.statusCode || 502, ur.headers);   // SSE 直通，不缓冲
    ur.pipe(res);
  });
  up.on("error", (e) => {
    upstreams = [];            // DSH 可能重启换了端口 → 下次请求重新发现
    if (!res.headersSent) res.writeHead(502, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: { message: "upstream " + e.message, type: "relay" } }));
  });
  if (body) up.write(body);
  up.end();
}

http.createServer((req, res) => {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", async () => {
    const body = chunks.length ? Buffer.concat(chunks) : null;
    if (req.url === "/healthz") {
      const found = await refreshUpstreams();
      res.writeHead(found.length ? 200 : 503, { "content-type": "application/json" });
      res.end(JSON.stringify({ ok: found.length > 0, shims: found, token: "set" }));
      return;
    }
    if (upstreams.length === 0) await refreshUpstreams();
    const port = pick();
    if (!port) {
      res.writeHead(503, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: { message: "没有可用的 WorkBuddy shim（DSH 未运行或插件补丁未生效）", type: "relay" } }));
      return;
    }
    forward(port, req, res, body);
  });
}).listen(LISTEN.port, LISTEN.host, () => {
  console.log("workbuddy-relay: http://" + LISTEN.host + ":" + LISTEN.port + "/v1");
  refreshUpstreams().then((f) => console.log("  shims:", f.length ? f.join(",") : "(未发现)"));
});
