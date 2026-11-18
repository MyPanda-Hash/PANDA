#!/usr/bin/env node
// 新版 AI 数字员工前端（待办工作台）的本地服务：:18092
//   - /                → doc-nexus-ai/frontend-desk（无构建步骤，源码直接跑）
//   - /api/*           → 反代到 FastAPI :8000（剥掉 /api 前缀，重写 Set-Cookie 的 Path）
//   - /pod/*           → frontend-group/pod（实时语音窗，和本页同源，Cookie 直接可用）
//   - WebSocket 升级    → 隧道给后端，语音走 /api/assistant/voice/ws
//
// 旧的 serve-ai-web.mjs（:18091）保持不动：它是参照物，坏了不影响新界面。
// 用法：node serve-ai-desk.mjs [--port 18092] [--backend 8000] [--mock 18094]

import http from "node:http";
import net from "node:net";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const BASE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(BASE, "doc-nexus-ai/frontend-desk");
const POD_ROOT = path.join(BASE, "doc-nexus-ai/frontend-group/pod");
const GROUP_ROOT = path.join(BASE, "doc-nexus-ai/frontend-group");

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index > -1 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

const PORT = Number(arg("port", 18092));
const BACKEND = { host: "127.0.0.1", port: Number(arg("backend", 8000)) };
// 演示/截图模式：所有 /api/* 交给本地 mock 服务，界面上会显示角标，避免误当成真数据
const MOCK = { host: "127.0.0.1", port: Number(arg("mock", 18094)) };
const MOCK_ENABLED = process.argv.includes("--mock");

const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
  ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2", ".map": "application/json",
};

function proxy(req, res, targetPath, { backend = BACKEND } = {}) {
  const opts = {
    host: backend.host, port: backend.port, method: req.method,
    path: targetPath, headers: { ...req.headers, host: `localhost:${backend.port}` },
  };
  const upstream = http.request(opts, (ur) => {
    const headers = { ...ur.headers };
    if (headers["set-cookie"]) {
      headers["set-cookie"] = headers["set-cookie"].map((c) =>
        String(c).replace(/Path=\/auth(;|$)/i, "Path=/api/auth$1"));
    }
    res.writeHead(ur.statusCode || 502, headers);
    ur.pipe(res);
  });
  upstream.on("error", (e) => {
    if (!res.headersSent) res.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
    res.end("upstream error: " + e.message);
  });
  req.pipe(upstream);
}

// 默认 no-store，不是 no-cache：
// no-cache 只表示「用之前先验证」，但这里没发 ETag/Last-Modified，
// 浏览器无从验证，就一直用旧文件——改了源码刷新没反应，排查半天
// （2026-11-17 踩过：desk 的 main.js 改了但页面始终跑旧版本）。
function serveFile(res, file, { cache = "no-store" } = {}) {
  fs.stat(file, (err, st) => {
    if (err || !st.isFile()) { res.writeHead(404).end("not found"); return; }
    const headers = { "content-type": MIME[path.extname(file).toLowerCase()] || "application/octet-stream", "cache-control": cache };
    res.writeHead(200, headers);
    fs.createReadStream(file).pipe(res);
  });
}

/** 只允许读 root 里的文件，挡住 ../ 穿越 */
function safeJoin(root, relPath) {
  const file = path.join(root, path.normalize(relPath).replace(/^(\.\.[/\\])+/, ""));
  return file.startsWith(root) ? file : null;
}

/**
 * 给 index.html 打补丁：<base href> 必须与「这次请求实际挂在哪个前缀下」一致。
 *
 * index.html 里写死的是 /ai/ —— 那是给 MES 同源部署（MES 反代 /ai → 18092）用的。
 * 但本服务也常被直接访问 :18092/，此时写死的 /ai/ 会让所有相对路径变成
 * /ai/js/main.js → 404 → 模块不加载 → 白页。
 * 2026-11-17 排查很久：页面骨架在、接口全 200、就是没有内容，因为 script 根本没执行。
 */
function serveDeskIndex(res, prefix) {
  fs.readFile(path.join(ROOT, "index.html"), "utf-8", (err, html) => {
    if (err) { res.writeHead(404).end("not found"); return; }
    const patched = html.replace(/<base href="[^"]*">/, '<base href="' + prefix + '">');
    const body = Buffer.from(patched, "utf-8");
    res.writeHead(200, { "content-type": MIME[".html"], "content-length": body.length, "cache-control": "no-store" });
    res.end(body);
  });
}

function serveDesk(req, res, urlPath) {
  let rel = decodeURIComponent(urlPath.split("?")[0]);
  if (rel.endsWith("/")) rel += "index.html";
  let file = safeJoin(ROOT, rel);
  if (!file) { res.writeHead(403).end("forbidden"); return; }
  // 挂在前缀下（/ai/...）就沿用前缀，直接访问根就落到 "/"。
  // 但反代（MES 的 vite preview）会把 /ai 前缀 rewrite 掉，到这里 rel 已经是 "/"，
  // 靠 rel 判断会把 base 写成 "/"，页面里的 js/main.js 就解析成 /js/main.js ——
  // 撞上 MES 的 SPA 回落拿到 HTML，模块不加载，整页只剩静态骨架。
  // 所以反代必须在 X-Desk-Prefix 里说明真实前缀，这里优先信它。
  const forcedPrefix = String(req.headers["x-desk-prefix"] || "").trim();
  const prefix = forcedPrefix || (rel.startsWith("/ai/") ? "/ai/" : "/");
  fs.stat(file, (err, st) => {
    if (err || !st.isFile()) {
      if (path.extname(rel)) { res.writeHead(404).end("not found"); return; }
      serveDeskIndex(res, prefix);          // SPA 回落，同样要改 base
      return;
    }
    if (path.basename(file) === "index.html") {
      serveDeskIndex(res, prefix);
      return;
    }
    serveFile(res, file);
  });
}

function serveSub(req, res, urlPath, root, stripPrefix) {
  let rel = decodeURIComponent(urlPath.split("?")[0]).replace(stripPrefix, "");
  if (!rel || rel === "/") rel = "/index.html";
  const file = safeJoin(root, rel);
  if (!file) { res.writeHead(403).end("forbidden"); return; }
  serveFile(res, file);
}

// 开发/验收专用：把界面塞进固定宽度的 iframe 里量三档（1440/768/375）。
// 放在同源上，截图脚本才能读 iframe 里的 scrollWidth 做无横向滚动判定。
// 生产路径不会引用它。
function shotHarness(res, qs) {
  const width = Math.max(320, Math.min(1920, Number(qs.get("w") || 1440)));
  const route = (qs.get("r") || "todos").replace(/[^a-z]/g, "") || "todos";
  const theme = qs.get("theme") === "dark" ? "dark" : "light";
  const src = `/?theme=${theme}#/${route}`;
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>shot ${width}</title>
<style>html,body{margin:0;background:#e5e7eb}iframe{border:0;display:block;width:${width}px;height:900px;background:#fff}</style>
</head><body><iframe id="app" src="${src}" title="app"></iframe></body></html>`;
  const body = Buffer.from(html, "utf-8");
  res.writeHead(200, { "content-type": MIME[".html"], "content-length": body.length, "cache-control": "no-store" });
  res.end(body);
}

function proxyUpgrade(req, socket, head) {
  const p = req.url || "/";
  if (!p.startsWith("/api/")) { socket.destroy(); return; }
  const target = p.slice(4);
  const upstream = net.connect(BACKEND.port, BACKEND.host, () => {
    const headers = { ...req.headers, host: `${BACKEND.host}:${BACKEND.port}` };
    let raw = `GET ${target} HTTP/1.1\r\n`;
    for (const [key, value] of Object.entries(headers)) {
      if (value === undefined) continue;
      raw += Array.isArray(value)
        ? value.map((v) => `${key}: ${v}\r\n`).join("")
        : `${key}: ${value}\r\n`;
    }
    raw += "\r\n";
    upstream.write(raw);
    if (head && head.length) upstream.write(head);
    socket.pipe(upstream);
    upstream.pipe(socket);
  });
  upstream.on("error", () => socket.destroy());
  socket.on("error", () => upstream.destroy());
}

const server = http.createServer((req, res) => {
  const urlPath = req.url || "/";
  const pathname = urlPath.split("?")[0];

  if (pathname === "/healthz") {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ status: "ok", mode: MOCK_ENABLED ? "mock" : "live", port: PORT }));
    return;
  }

  if (pathname === "/_shot.html") {
    shotHarness(res, new URL(urlPath, "http://x").searchParams);
    return;
  }

  if (pathname.startsWith("/api/")) {
    // 群聊页既有约定：/api/* → 后端 /*；mock 模式下同样剥前缀
    const target = pathname.slice(4) + (urlPath.includes("?") ? `?${urlPath.split("?").slice(1).join("?")}` : "");
    proxy(req, res, target, { backend: MOCK_ENABLED ? MOCK : BACKEND });
    return;
  }

  // MES 单点登录后后端 302 到 /chat（旧门户的对话页路径）。
  // 新工作台没有 /chat 这个路径，落到这里就转到待办首页，避免出现空白页。
  if (pathname === "/chat" || pathname === "/login") {
    res.writeHead(302, { location: "/#/todos", "cache-control": "no-store" });
    res.end();
    return;
  }

  if (pathname === "/pod" || pathname.startsWith("/pod/")) {
    serveSub(req, res, urlPath, POD_ROOT, /^\/pod/);
    return;
  }

  // 群聊/语音窗里用的是绝对路径脚本（/notify-bubble.js 等），主站也要能取到
  if (/^\/(notify-bubble|nav-inject|mes-todo-pod|org-view|audio-wav)\.js$/.test(pathname)) {
    serveFile(res, path.join(GROUP_ROOT, pathname.slice(1)), { cache: "no-cache" });
    return;
  }

  if (pathname === "/group" || pathname.startsWith("/group/")) {
    serveSub(req, res, urlPath, GROUP_ROOT, /^\/group/);
    return;
  }

  // DeepSeek 官方 chat-demo 的构建产物（MIT）：界面与官网一致。
  // 单独一个目录，避免与 hand-written 的 frontend-group 源码混在一起。
  if (pathname === "/chat-deepseek" || pathname.startsWith("/chat-deepseek/")) {
    serveSub(req, res, urlPath, path.join(GROUP_ROOT, "chat-deepseek"), /^\/chat-deepseek/);
    return;
  }

  serveDesk(req, res, urlPath);
});

server.on("upgrade", proxyUpgrade);

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[desk] http://127.0.0.1:${PORT}  (${MOCK_ENABLED ? `mock → :${MOCK.port}` : `api → :${BACKEND.port}`})`);
});
