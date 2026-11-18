#!/usr/bin/env node
// doc-nexus-ai 前端静态服务 + 反向代理（复刻 deploy/nginx.conf 的语义，无需 Docker）
//   /api/*            -> 后端 /*（剥离 /api 前缀），并重写 Set-Cookie 的 Path=/auth -> /api/auth
//   /health           -> 后端 /health
//   /storage-upload/* -> 后端 /storage-upload/*
//   /assets/*         -> 静态，Cache-Control: public, max-age=7d
//   其它              -> 静态文件，找不到则回落 index.html（SPA）
import http from "node:http";
import net from "node:net";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const BASE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(BASE, "doc-nexus-ai/frontend/dist");
// 群聊页与导航入口是本仓库自己写的，不修改原 dist 产物。
const GROUP_ROOT = path.join(BASE, "doc-nexus-ai/frontend-group");
const POD_ROOT = path.join(GROUP_ROOT, "pod");
const NAV_SCRIPT = path.join(GROUP_ROOT, "nav-inject.js");
const NOTIFY_SCRIPT = path.join(GROUP_ROOT, "notify-bubble.js");
const MES_POD_SCRIPT = path.join(GROUP_ROOT, "mes-todo-pod.js");
const BACKEND = { host: "127.0.0.1", port: 8000 };
const PORT = 18091;

const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
  ".webp": "image/webp", ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8", ".map": "application/json",
};

function proxy(req, res, targetPath, extra) {
  const opts = {
    host: BACKEND.host, port: BACKEND.port, method: req.method,
    path: targetPath, headers: { ...req.headers, host: `localhost:${BACKEND.port}` },
  };
  const upstream = http.request(opts, (ur) => {
    const headers = { ...ur.headers };
    if (extra?.rewriteCookie && headers["set-cookie"]) {
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

// 发给浏览器的 index.html 都注入一段导航脚本，为「项目群聊」加入口。
// 原 dist 不动，注入只发生在响应阶段，随时可撤。
function sendIndex(res) {
  fs.readFile(path.join(ROOT, "index.html"), (err, buf) => {
    if (err) { res.writeHead(404).end("not found"); return; }
    let html = String(buf);
    if (!html.includes("/group-nav.js")) {
      html = html.replace(
        "</body>",
        '  <script src="/group-nav.js" defer></script>\n' +
        '  <script src="/notify-bubble.js" defer></script>\n' +
        '  <script src="/session-guard.js" defer></script>\n</body>'
      );
    }
    const body = Buffer.from(html);
    res.writeHead(200, { "content-type": MIME[".html"], "content-length": body.length });
    res.end(body);
  });
}

function serveGroup(req, res, urlPath) {
  let rel = decodeURIComponent(urlPath.split("?")[0]).replace(/^\/group/, "");
  if (!rel || rel === "/") rel = "/index.html";
  const file = path.join(GROUP_ROOT, path.normalize(rel).replace(/^(\.\.[/\\])+/, ""));
  if (!file.startsWith(GROUP_ROOT)) { res.writeHead(403).end("forbidden"); return; }
  fs.stat(file, (err, st) => {
    if (err || !st.isFile()) { res.writeHead(404).end("not found"); return; }
    const ext = path.extname(file).toLowerCase();
    res.writeHead(200, {
      "content-type": MIME[ext] || "application/octet-stream",
      "cache-control": "no-cache",
    });
    fs.createReadStream(file).pipe(res);
  });
}

// 语音悬浮窗：/pod 是一个独立小窗口，直接被 window.open 打开。
// 它和 API 同源，Cookie 能带上，不需要跨源鉴权。
function servePod(req, res, urlPath) {
  let rel = decodeURIComponent(urlPath.split("?")[0]).replace(/^\/pod/, "");
  if (!rel || rel === "/") rel = "/index.html";
  const file = path.join(POD_ROOT, path.normalize(rel).replace(/^(\.\.[/\\])+/, ""));
  if (!file.startsWith(POD_ROOT)) { res.writeHead(403).end("forbidden"); return; }
  fs.stat(file, (err, st) => {
    if (err || !st.isFile()) { res.writeHead(404).end("not found"); return; }
    const ext = path.extname(file).toLowerCase();
    res.writeHead(200, {
      "content-type": MIME[ext] || "application/octet-stream",
      "cache-control": "no-cache",
    });
    fs.createReadStream(file).pipe(res);
  });
}

function serveStatic(req, res, urlPath) {
  // 会话兜底：SPA 在「未登录 + 深链」时只会一直显示「正在验证登录状态…」，
  // 不会自己去登录页。这段脚本把死等变成一次跳转（原 dist 不动）。
  if (urlPath.split("?")[0] === "/session-guard.js") {
    const js = `
(function () {
  var KEY = "dnx-session-guard";
  function isLoginPage() {
    var p = location.pathname;
    return p === "/login" || p === "/setup" || p === "/change-password";
  }
  function stuckCardVisible() {
    var text = document.body ? document.body.innerText : "";
    return text.indexOf("正在验证登录状态") >= 0;
  }
  function check() {
    if (isLoginPage() || !stuckCardVisible()) return true;
    var last = Number(sessionStorage.getItem(KEY) || 0);
    if (Date.now() - last < 10000) return true;   // 防来回跳
    sessionStorage.setItem(KEY, String(Date.now()));
    location.replace("/login");
    return false;
  }
  // 给 SPA 一点时间自己完成刷新；仍然卡住才跳登录页
  setTimeout(function () {
    if (!check()) return;
    var tries = 0;
    var timer = setInterval(function () {
      if (!check() || ++tries > 10) clearInterval(timer);
    }, 1000);
  }, 4000);
})();
`;
    const body = Buffer.from(js, "utf-8");
    res.writeHead(200, { "content-type": "text/javascript; charset=utf-8", "content-length": body.length });
    res.end(body);
    return;
  }

  let rel = decodeURIComponent(urlPath.split("?")[0]);
  if (rel.endsWith("/")) rel += "index.html";
  let file = path.join(ROOT, path.normalize(rel).replace(/^(\.\.[/\\])+/, ""));
  if (!file.startsWith(ROOT)) { res.writeHead(403).end("forbidden"); return; }
  fs.stat(file, (err, st) => {
    if (err || !st.isFile()) {
      if (path.extname(rel)) { res.writeHead(404).end("not found"); return; }
      sendIndex(res);                                  // SPA 回落
      return;
    }
    if (path.basename(file) === "index.html") { sendIndex(res); return; }
    const ext = path.extname(file).toLowerCase();
    const headers = { "content-type": MIME[ext] || "application/octet-stream", "content-length": st.size };
    if (rel.startsWith("/assets/")) headers["cache-control"] = "public, max-age=604800";
    res.writeHead(200, headers);
    fs.createReadStream(file).pipe(res);
  });
}

// WebSocket 升级：语音悬浮窗要从同源连 /api/assistant/voice/ws，
// 否则 Cookie 带不过去。这里按原样的 TCP 隧道把握手与后续帧转给后端。
function proxyUpgrade(req, socket, head) {
  const p = req.url || "/";
  if (!p.startsWith("/api/")) {
    socket.destroy();
    return;
  }
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
  const p = req.url || "/";
  if (p === "/group-nav.js") {
    fs.readFile(NAV_SCRIPT, (err, buf) => {
      if (err) return res.writeHead(404).end("not found");
      res.writeHead(200, { "content-type": MIME[".js"], "cache-control": "no-cache" });
      res.end(buf);
    });
    return;
  }
  if (p === "/group" || p.startsWith("/group/") || p.startsWith("/group?")) {
    return serveGroup(req, res, p);
  }
  if (p === "/mes-todo-pod.js") {
    fs.readFile(MES_POD_SCRIPT, (err, buf) => {
      if (err) return res.writeHead(404).end("not found");
      res.writeHead(200, {
        "content-type": MIME[".js"],
        "cache-control": "no-cache",
        "access-control-allow-origin": "*",
      });
      res.end(buf);
    });
    return;
  }
  if (p === "/notify-bubble.js") {
    fs.readFile(NOTIFY_SCRIPT, (err, buf) => {
      if (err) return res.writeHead(404).end("not found");
      res.writeHead(200, { "content-type": MIME[".js"], "cache-control": "no-cache" });
      res.end(buf);
    });
    return;
  }
  if (p === "/pod" || p.startsWith("/pod/") || p.startsWith("/pod?")) {
    return servePod(req, res, p);
  }
  if (p === "/health" || p.startsWith("/health?")) return proxy(req, res, "/health");
  if (p.startsWith("/api/")) return proxy(req, res, p.slice(4), { rewriteCookie: true });
  if (p.startsWith("/storage-upload/")) return proxy(req, res, p);
  return serveStatic(req, res, p);
});

server.on("upgrade", proxyUpgrade);

server.listen(PORT, "0.0.0.0", () => {
  console.log(`doc-nexus-ai web: http://localhost:${PORT}  (dist=${ROOT}, backend=:${BACKEND.port})`);
});
