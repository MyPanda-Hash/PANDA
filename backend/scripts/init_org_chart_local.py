#!/usr/bin/env python3
"""本地版组织架构图解析服务（不出网）。

MES 的初始化向导把图片 POST 到这里，这里再交给**本机**视觉模型（Ollama /
LM Studio / vLLM 等任何 OpenAI 兼容端点，默认 `http://127.0.0.1:11434/v1`），
用的仍是数字员工里那套提示词与结果归一化（直接 import `app.services.init_org_chart`），
所以云端与本地两条路不会各写一份解析逻辑。

启动：
    cd doc-nexus-ai/backend
    ORGCHART_TOKEN=<与 MES_INIT_AI_TOKEN 一致> ./.venv/bin/python scripts/init_org_chart_local.py

需要的环境变量：
    ORGCHART_TOKEN     必填，与 MES 的 MES_INIT_AI_TOKEN 相同（未设则拒绝启动）
    LOCAL_VLM_URL      本机模型地址，默认 http://127.0.0.1:11434/v1
    LOCAL_VLM_MODEL    模型名，默认 qwen2.5vl:7b
    ORGCHART_PORT      监听端口，默认 18093

为什么单独一个进程：图片不能脱敏，云端默认被数据策略拦截；把解析固定在本机
127.0.0.1 上，MES 里怎么配都不会把组织架构图发出去。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 允许直接从仓库根跑：把 backend 加进 sys.path 以便 import app.*
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.init_org_chart import (  # noqa: E402
    OrgChartError,
    build_messages,
    parse_reply,
)

HOST = os.environ.get("ORGCHART_HOST", "127.0.0.1")
PORT = int(os.environ.get("ORGCHART_PORT", "18093"))
TOKEN = (os.environ.get("ORGCHART_TOKEN") or "").strip()
VLM_URL = (os.environ.get("LOCAL_VLM_URL") or "http://127.0.0.1:11434/v1").rstrip("/")
VLM_MODEL = os.environ.get("LOCAL_VLM_MODEL") or "qwen2.5vl:7b"
VLM_TIMEOUT = float(os.environ.get("LOCAL_VLM_TIMEOUT") or 300)
MAX_BODY = 32 * 1024 * 1024


def _read_multipart(body: bytes, content_type: str) -> dict[str, tuple[str, bytes, str]]:
    """极简 multipart 解析：只取 file 与 hint，够用即可（不引第三方库）。"""
    marker = "boundary="
    if marker not in content_type:
        raise ValueError("需要 multipart/form-data")
    boundary = content_type.split(marker, 1)[1].split(";")[0].strip().strip('"')
    delimiter = b"--" + boundary.encode()
    fields: dict[str, tuple[str, bytes, str]] = {}
    for part in body.split(delimiter):
        if not part or part in (b"--\r\n", b"--"):
            continue
        head, _, payload = part.partition(b"\r\n\r\n")
        if not payload:
            continue
        payload = payload.rstrip(b"\r\n")
        text_head = head.decode("utf-8", "replace")
        name = ""
        filename = ""
        ctype = ""
        for line in text_head.split("\r\n"):
            lower = line.lower()
            if lower.startswith("content-disposition"):
                for piece in line.split(";"):
                    piece = piece.strip()
                    if piece.startswith("name="):
                        name = piece[5:].strip().strip('"')
                    elif piece.startswith("filename="):
                        filename = piece[9:].strip().strip('"')
            elif lower.startswith("content-type:"):
                ctype = line.split(":", 1)[1].strip()
        if name:
            fields[name] = (filename, payload, ctype)
    return fields


def _call_local_vlm(image_bytes: bytes, media_type: str, hint: str) -> dict:
    messages = build_messages(image_bytes, media_type, hint)
    payload = {
        "model": VLM_MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 3000,
        "stream": False,
    }
    request = urllib.request.Request(
        VLM_URL + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=VLM_TIMEOUT) as response:
            data = json.load(response)
    except urllib.error.URLError as exc:
        raise OrgChartError(
            "local_vlm_unreachable",
            f"连不上本机视觉模型 {VLM_URL}（{exc.reason}）："
            f"先把模型跑起来，例如 ollama serve 后 ollama pull {VLM_MODEL}",
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise OrgChartError("local_vlm_timeout", f"本机视觉模型超时：{exc}") from exc
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OrgChartError("bad_reply", "本机模型返回结构不符合 OpenAI 兼容格式") from exc
    if not isinstance(text, str) or not text.strip():
        raise OrgChartError(
            "vision_blind",
            f"本机模型 {VLM_MODEL} 没有返回内容，可能不是视觉模型（换 qwen2.5vl 之类支持图片的）",
        )
    parsed = parse_reply(text)
    parsed["model"] = VLM_MODEL
    parsed["endpoint"] = "local"
    return parsed


class Handler(BaseHTTPRequestHandler):
    server_version = "init-orgchart-local/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401 - 标准库签名
        sys.stderr.write("[orgchart-local] " + fmt % args + "\n")

    def _read_body(self) -> bytes:
        """读请求体：Java 的 multipart 可能走 chunked（没有 Content-Length），两种都要支持。"""
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            data = b""
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    break
                try:
                    size = int(line.split(b";")[0], 16)
                except ValueError as exc:
                    raise ValueError("chunked 长度行不合法") from exc
                if size == 0:
                    self.rfile.readline()
                    break
                data += self.rfile.read(size)
                self.rfile.read(2)
                if len(data) > MAX_BODY:
                    raise ValueError("请求体过大")
            return data
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("请求体过大")
        return self.rfile.read(length) if length > 0 else b""

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - 标准库签名
        if self.path.split("?")[0] == "/health":
            self._json(200, {"status": "ok", "model": VLM_MODEL, "vlm": VLM_URL})
            return
        self._json(404, {"message": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - 标准库签名
        if self.path.split("?")[0] != "/internal/v1/init/org-chart":
            self._json(404, {"message": "not found"})
            return
        if not TOKEN:
            self._json(503, {"message": "本地解析服务未配置令牌（ORGCHART_TOKEN）"})
            return
        if (self.headers.get("X-Nexus-Internal-Token") or "").strip() != TOKEN:
            self._json(401, {"message": "内部令牌无效"})
            return
        try:
            body = self._read_body()
        except ValueError as exc:
            self._json(413 if "过大" in str(exc) else 400, {"code": "bad_body", "message": str(exc)})
            return
        if not body:
            self._json(400, {"code": "empty_body", "message": "没有请求体"})
            return
        try:
            fields = _read_multipart(body, self.headers.get("Content-Type") or "")
        except ValueError as exc:
            self._json(400, {"code": "bad_form", "message": str(exc)})
            return
        file_part = fields.get("file")
        if not file_part or not file_part[1]:
            self._json(400, {"code": "no_file", "message": "缺少图片（字段名应为 file）"})
            return
        _filename, image_bytes, content_type = file_part
        media_type = content_type or "image/png"
        hint = fields.get("hint", ("", b"", ""))[1].decode("utf-8", "replace")
        try:
            result = _call_local_vlm(image_bytes, media_type, hint)
        except OrgChartError as exc:
            self.log_message("reject %s: %s", exc.code, exc.message)
            status = 503 if exc.code.startswith("local_vlm_") else 400
            self._json(status, {"code": exc.code, "message": exc.message})
            return
        self.log_message(
            "parsed %d depts via %s", len(result.get("depts") or []), result.get("model")
        )
        self._json(200, result)


def main() -> int:
    if not TOKEN:
        print("未设置 ORGCHART_TOKEN（必须与 MES 的 MES_INIT_AI_TOKEN 一致），拒绝启动", file=sys.stderr)
        return 2
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"本地组织架构解析已启动：http://{HOST}:{PORT} → 本机模型 {VLM_URL} ({VLM_MODEL})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())