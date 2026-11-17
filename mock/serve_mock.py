#!/usr/bin/env python3
"""新版 AI 数字员工前端的 mock 后端（:18094，仅本地验收用）。

真后端需要 MES 单点登录，MES 没起时登不进去；这个 mock 用 fixtures.json
（从真实 SQLite 只读导出的待办/AI 发现/会话）把界面喂饱，用来做 1440/768/375
三档截图与交互验收。**不是生产数据源**，界面会显示「演示数据」角标。

跑法：
    python3 doc-nexus-ai/mock/serve_mock.py            # :18094
    node serve-ai-desk.mjs --mock                       # :18092 走 mock
"""

from __future__ import annotations

import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
FIXTURES = json.loads((ROOT / "fixtures.json").read_text("utf-8"))
STATE = json.loads(json.dumps(FIXTURES))
STATE["todos"] = STATE["todos"]

VOICE_REPLIES = [
    "今天最要紧的是两张到货单还没检，先把 DH-20261112-001 和 DH-20261112-002 补检了，再回销售订单的排产。",
    "生产加工单 SO-2026-08-0004-2 已经拖了 86 天，建议今天定一个明确的完工日期，否则整条交付链都压着。",
    "我把今天的待办按优先级排了一遍：紧急 9 条、普通 7 条。要我逐条念给你听吗？",
]


def sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静音，避免刷屏
        pass

    # ------------------------------------------------------------ 工具
    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _query(self):
        return parse_qs(urlparse(self.path).query)

    # ------------------------------------------------------------ 路由
    def do_GET(self):
        path = urlparse(self.path).path
        q = self._query()

        # MES 入口在 mock 下也要能落地：直接给演示身份，而不是 500。
        # 真实 SSO 票据校验由 :8000 负责，mock 只服务界面验收。
        if path == "/auth/mes-sso":
            return self._json(STATE["user"])

        if path == "/auth/login":
            return self._json({"access_token": "mock", "user": STATE["user"]})

        if path == "/auth/me":
            return self._json(STATE["user"])

        if path == "/todos":
            status = (q.get("status") or [None])[0]
            rows = STATE["todos"]
            if status:
                rows = [t for t in rows if t["status"] == status]
            return self._json(rows)

        if path == "/assistant/events":
            status = (q.get("status") or ["open"])[0]
            rows = [e for e in STATE["events"] if not status or e["status"] == status]
            return self._json(rows)

        if path == "/assistant/status":
            return self._json(
                {
                    "enabled": True,
                    "interval_seconds": 900,
                    "open_for_me": len([e for e in STATE["events"] if e["status"] == "open"]),
                    "open_total": len(STATE["events"]),
                    "feed_group": "AI 调度台",
                }
            )

        if path in ("/assistant/briefing", "/assistant/briefing/"):
            return self._json(STATE["briefing"])

        if path == "/assistant/briefing/notifications":
            return self._json({"data": {"items": STATE["notifications"]["data"]["items"]}})

        if path == "/notifications":
            return self._json(STATE["notifications"])

        if path == "/groups":
            return self._json(STATE.get("groups", []))

        if path == "/groups/directory-users":
            return self._json([])

        m = re.fullmatch(r"/groups/([^/]+)/messages", path)
        if m:
            return self._json(STATE.get("groupMessages", {}).get(m.group(1), []))

        # 群聊页右侧「人力地图」用；mock 没有真实组织架构，给空图而不是 404
        m = re.fullmatch(r"/groups/([^/]+)/org-map", path)
        if m:
            return self._json({"nodes": [], "edges": []})

        m = re.fullmatch(r"/groups/([^/]+)/members", path)
        if m:
            return self._json([])

        if path == "/assistant/facets":
            return self._json({"by_kind": {}, "by_severity": {}})

        m = re.fullmatch(r"/groups/([^/]+)", path)
        if m:
            gid = m.group(1)
            found = next((g for g in STATE.get("groups", []) if g["id"] == gid), None)
            return self._json(found) if found else self._json({"detail": "群不存在"}, 404)

        if path == "/admin/employees":
            user_id = (q.get("user_id") or [None])[0]
            rows = STATE.get("employees", [])
            if user_id:
                rows = [p for p in rows if p["user_id"] == user_id]
            return self._json(rows)

        if path == "/assistant/voice/status":
            return self._json(STATE["voice"])

        if path == "/chat/sessions":
            return self._json(STATE["sessions"])

        match = re.fullmatch(r"/chat/sessions/([^/]+)", path)
        if match:
            return self._json(self._session_detail(match.group(1)))

        if path == "/chat/capabilities":
            return self._json({"models": [], "skills": [], "media": {}})

        return self._json({"detail": "mock 未实现：" + path}, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/auth/logout":
            self.send_response(204)
            self.send_header("content-length", "0")
            self.end_headers()
            return

        if path == "/notifications/read":
            STATE["notifications"]["data"]["unread"] = 0
            return self._json({"data": {"marked": 3, "unread": 0}})

        if path == "/asr/transcribe":
            time.sleep(0.4)
            return self._json({"text": "把今天高优先级的待办念一遍"})

        if path == "/chat/sessions":
            body = self._body()
            sid = f"sess_mock_{int(time.time() * 1000)}"
            row = {
                "id": sid, "title": body.get("title") or "新对话", "messages_revision": 1,
                "agent_session_id": None, "preferred_model": None, "category_id": None,
                "category_name": None, "pinned": False, "pinned_at": None, "archived": False,
                "archived_at": None, "writing_project_id": None, "message_count": 0,
                "created_at": "2026-11-16T14:00:00+08:00", "updated_at": "2026-11-16T14:00:00+08:00",
            }
            STATE["sessions"].insert(0, row)
            return self._json({**row, "messages": [], "has_more_messages": False}, 201)

        if path == "/chat/messages":
            return self._stream_chat()

        match = re.fullmatch(r"/assistant/events/([^/]+):ack", path)
        if match:
            for event in STATE["events"]:
                if event["id"] == match.group(1):
                    event["status"] = "acked"
                    event["acked_by"] = "管理员"
                    return self._json(event)
            return self._json({"detail": "事件不存在"}, 404)

        return self._json({"detail": "mock 未实现：" + path}, 404)

    def do_PATCH(self):
        path = urlparse(self.path).path
        match = re.fullmatch(r"/todos/([^/]+)", path)
        if match:
            body = self._body()
            for todo in STATE["todos"]:
                if todo["id"] == match.group(1):
                    status = body.get("status")
                    if status in ("pending", "accepted", "doing", "done", "dismissed"):
                        todo["status"] = status
                    return self._json(todo)
            return self._json({"detail": "待办不存在"}, 404)
        return self._json({"detail": "mock 未实现：" + path}, 404)

    # ------------------------------------------------------------ 会话详情
    def _session_detail(self, session_id: str):
        summary = next((s for s in STATE["sessions"] if s["id"] == session_id), None)
        if not summary:
            return {"detail": "会话不存在"}
        messages = [
            {
                "id": "msg_u1", "role": "user", "content": "今天有什么要处理的？", "parts": [],
                "references": [], "space_ids": [], "document_ids": [], "created_at": "2026-11-16T09:02:00+08:00",
                "metadata": {},
            },
            {
                "id": "msg_a1", "role": "assistant",
                "content": "今天有 16 条待办，其中 9 条高优先级。最急的两件事：\n\n1. **两张到货单未检**：DH-20261112-001、DH-20261112-002，补检后采购入库才能走。\n2. **生产加工单 SO-2026-08-0004-2 逾期 86 天**：需要今天定完工日期。",
                "parts": [], "references": [], "space_ids": [], "document_ids": [],
                "created_at": "2026-11-16T09:02:20+08:00", "metadata": {},
            },
            {
                "id": "msg_u2", "role": "user", "content": "把高优先级的列成清单", "parts": [],
                "references": [], "space_ids": [], "document_ids": [], "created_at": "2026-11-16T09:05:00+08:00",
                "metadata": {},
            },
            {
                "id": "msg_a2", "role": "assistant",
                "content": "高优先级 9 条，按外部阻塞优先排：\n\n- 核查两张未检到货单并补检（关联 DH-20261112-001 / 002）\n- 恢复或重开生产加工单 SO-2026-08-0004-1\n- 跟进北方机械木箱样件并组织验收\n- 核对明天中天客户的接待准备\n- 汇总今日生产日报供晚上开会\n\n要我把这些直接派给对应的人吗？",
                "parts": [], "references": [], "space_ids": [], "document_ids": [],
                "created_at": "2026-11-16T09:05:12+08:00", "metadata": {},
            },
        ]
        return {**summary, "messages": messages, "has_more_messages": False}

    # ------------------------------------------------------------ SSE
    def _stream_chat(self):
        body = self._body()
        session_id = body.get("local_session_id") or body.get("session_id") or "sess_mock_live"
        if not any(s["id"] == session_id for s in STATE["sessions"]):
            session_id = STATE["sessions"][0]["id"] if STATE["sessions"] else "sess_mock_live"

        reply = VOICE_REPLIES[0]
        text = str(body.get("message") or "")
        if "语音" in text:
            reply = VOICE_REPLIES[1]
        elif "待办" in text:
            reply = "现在有 16 条待处理，9 条高优先级、7 条普通。已在「待办」页按优先级排好，点开任意一条能看到要做的事和关联单据。"

        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "keep-alive")
        self.end_headers()

        def write(payload):
            self.wfile.write(sse(payload))
            self.wfile.flush()

        write({"type": "session", "session": {"id": session_id}})
        write({"type": "assistant_message", "phase": "start",
               "message": {"id": "msg_stream", "role": "assistant", "content": "", "created_at": "2026-11-16T14:00:00+08:00"}})
        for chunk in [reply[i:i + 6] for i in range(0, len(reply), 6)]:
            write({"type": "event", "assistant_message_id": "msg_stream",
                   "item": {"type": "text_delta", "delta": chunk}})
            time.sleep(0.02)
        write({"type": "assistant_message", "phase": "complete",
               "message": {"id": "msg_stream", "role": "assistant", "content": reply,
                           "created_at": "2026-11-16T14:00:00+08:00", "parts": [], "references": []},
               "server_persisted": True})
        write({"type": "done"})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18094
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[mock] http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
