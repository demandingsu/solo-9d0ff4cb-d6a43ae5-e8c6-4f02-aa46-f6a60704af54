#!/usr/bin/env python3
"""可切换批次与详情故障的本地示例公告站（纯标准库，可离线运行）。

启动：
    python3 -I site/server.py --host 0.0.0.0 --port 8080

控制接口（宿主机访问 http://127.0.0.1:8080）：
    GET  /control/state
    POST /control/batch              {"batch": "v1"|"v2"|"v3"}
    POST /control/failure            {"mode": "http500"|"malformed"|"none",
                                      "notice_ids": ["N-2026-015"]}
    POST /control/list-failure       {"enabled": true}
    POST /control/pagination-loop    {"enabled": true}
    POST /control/reset

也支持等价的 GET 形式，便于用 curl 快速操作：
    GET /control/batch?name=v2
    GET /control/failure?mode=http500&ids=N-2026-015
    GET /control/list-failure?enabled=1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

# 兼容 python -I（隔离模式）：显式把脚本目录加入模块搜索路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import batches  # noqa: E402

DEFAULT_STATE = {
    "batch": "v1",
    "detail_failure_mode": "none",  # none | http500 | malformed
    "detail_failure_ids": [],       # 空表示当前批次的所有详情
    "detail_delay_ms": 0,           # 每个详情响应的延迟（并发演示用）
    "list_failure": False,
    "pagination_loop": False,
}

_state = dict(DEFAULT_STATE)
_lock = threading.Lock()


def _snapshot() -> dict:
    with _lock:
        return dict(_state, detail_failure_ids=list(_state["detail_failure_ids"]))


def _update(**changes) -> dict:
    with _lock:
        _state.update(changes)
        return dict(_state, detail_failure_ids=list(_state["detail_failure_ids"]))


class Handler(BaseHTTPRequestHandler):
    server_version = "NoticeDemoSite/1.0"

    # ---------------------------------------------------------------- utilities
    def log_message(self, fmt: str, *args) -> None:  # pragma: no cover - noise
        print(f"[site] {self.address_string()} {fmt % args}", flush=True)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status: int, text: str) -> None:
        self._send(status, text.encode("utf-8"), "text/html; charset=utf-8")

    def _json(self, status: int, payload: dict) -> None:
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _body_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"请求体必须是 JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return value

    @staticmethod
    def _as_bool(value: str | bool | None) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _as_int(value: str | int | None) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _failure_for(self, notice_id: str) -> str:
        snap = _snapshot()
        mode = snap["detail_failure_mode"]
        ids = snap["detail_failure_ids"]
        if mode == "none":
            return "none"
        if ids and notice_id not in ids:
            return "none"
        return mode

    # ---------------------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path == "/control/state":
            self._json(200, _snapshot())
            return

        if path == "/control/batch":
            name = (query.get("name") or [""])[0]
            self._set_batch(name)
            return

        if path == "/control/failure":
            mode = (query.get("mode") or ["none"])[0]
            ids_raw = (query.get("ids") or [""])[0]
            ids = [x.strip() for x in ids_raw.split(",") if x.strip()]
            self._set_failure(mode, ids)
            return

        if path == "/control/list-failure":
            enabled = self._as_bool((query.get("enabled") or ["1"])[0])
            self._json(200, _update(list_failure=enabled))
            return

        if path == "/control/pagination-loop":
            enabled = self._as_bool((query.get("enabled") or ["1"])[0])
            self._json(200, _update(pagination_loop=enabled))
            return

        if path == "/control/delay":
            delay = self._as_int((query.get("ms") or ["0"])[0])
            self._json(200, _update(detail_delay_ms=delay))
            return

        if path == "/control/reset":
            self._json(200, _update(**DEFAULT_STATE))
            return

        if path in ("/", "/index.html", "/page1.html"):
            self._serve_list(0)
            return

        if path.startswith("/page") and path.endswith(".html"):
            raw = path[len("/page") : -len(".html")]
            try:
                page_no = int(raw)
            except ValueError:
                self._html(404, "<h1>404</h1>")
                return
            self._serve_list(page_no - 1)
            return

        if path.startswith("/notices/") and path.endswith(".html"):
            notice_id = unquote(path[len("/notices/") : -len(".html")])
            self._serve_detail(notice_id)
            return

        self._html(404, "<h1>404 Not Found</h1>")

    # ---------------------------------------------------------------- POST
    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            body = self._body_json()
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return

        if path == "/control/batch":
            name = str(body.get("batch") or body.get("name") or "")
            self._set_batch(name)
            return
        if path == "/control/failure":
            mode = str(body.get("mode") or "none")
            ids = body.get("notice_ids") or body.get("ids") or []
            if isinstance(ids, str):
                ids = [x.strip() for x in ids.split(",") if x.strip()]
            self._set_failure(mode, [str(x) for x in ids])
            return
        if path == "/control/list-failure":
            self._json(
                200, _update(list_failure=self._as_bool(body.get("enabled", True)))
            )
            return
        if path == "/control/pagination-loop":
            self._json(
                200,
                _update(pagination_loop=self._as_bool(body.get("enabled", True))),
            )
            return
        if path == "/control/delay":
            delay = self._as_int(body.get("ms", 0))
            self._json(200, _update(detail_delay_ms=delay))
            return
        if path == "/control/reset":
            self._json(200, _update(**DEFAULT_STATE))
            return
        self._json(404, {"error": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/control/failure":
            self._json(200, _update(detail_failure_mode="none", detail_failure_ids=[]))
            return
        self._json(404, {"error": "not found"})

    # ---------------------------------------------------------------- control helpers
    def _set_batch(self, name: str) -> None:
        if name not in batches.BATCHES:
            self._json(
                400,
                {"error": f"未知批次 {name!r}，可选: {sorted(batches.BATCHES)}"},
            )
            return
        self._json(200, _update(batch=name))

    def _set_failure(self, mode: str, ids: list[str]) -> None:
        if mode not in ("none", "http500", "malformed"):
            self._json(
                400,
                {"error": "mode 必须是 none / http500 / malformed"},
            )
            return
        if mode == "none":
            ids = []
        self._json(
            200,
            _update(detail_failure_mode=mode, detail_failure_ids=ids),
        )

    # ---------------------------------------------------------------- pages
    def _serve_list(self, page_idx: int) -> None:
        snap = _snapshot()
        if snap["list_failure"]:
            self._html(500, "<h1>列表页暂时不可用</h1>")
            return
        batch = batches.BATCHES[snap["batch"]]
        pages = batches.total_pages(batch)
        if page_idx < 0 or page_idx >= pages:
            self._html(404, "<h1>404 列表页不存在</h1>")
            return
        self._html(
            200,
            batches.render_list(
                batches.page_notices(batch, page_idx),
                page_idx,
                pages,
                loop_next=snap["pagination_loop"],
            ),
        )

    def _serve_detail(self, notice_id: str) -> None:
        snap = _snapshot()
        notice = next(
            (n for n in batches.BATCHES[snap["batch"]] if n.notice_id == notice_id),
            None,
        )
        if notice is None:
            self._html(404, "<h1>404 公告不存在</h1>")
            return

        mode = self._failure_for(notice_id)
        delay_ms = int(snap.get("detail_delay_ms") or 0)
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)
        if mode == "http500":
            self._html(500, "<h1>详情页暂时不可用</h1>")
            return
        if mode == "malformed":
            self._html(200, batches.render_malformed_detail(notice_id))
            return
        self._html(200, batches.render_detail(notice))


def serve(host: str, port: int, default_batch: str) -> None:
    _update(batch=default_batch)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(
        f"示例公告站已启动: http://{host}:{port}/index.html "
        f"(批次 {default_batch}, {len(batches.BATCHES[default_batch])} 条公告)",
        flush=True,
    )
    httpd.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="可切换批次的本地示例公告站")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--batch", default="v1", choices=sorted(batches.BATCHES))
    args = parser.parse_args()
    serve(args.host, args.port, args.batch)


if __name__ == "__main__":
    main()
