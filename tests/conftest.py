"""pytest 共享夹具：本地示例站子进程 + 独立 SQLite 的 API 客户端。"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
SITE_SERVER = ROOT / "site" / "server.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def demo_site() -> str:
    """启动一个全新的示例公告站进程，返回站点基地址。"""
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(SITE_SERVER),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--batch",
            "v1",
        ],
        cwd=str(SITE_SERVER.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(base + "/index.html", timeout=0.2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if proc.poll() is not None:
                raise RuntimeError("示例公告站进程提前退出")
            time.sleep(0.05)
        else:
            raise RuntimeError("示例公告站启动超时")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()


@pytest_asyncio.fixture
async def api(tmp_path, demo_site):
    """返回 (AsyncClient, 站点基地址)；每个用例使用独立数据库。"""
    app = create_app(str(tmp_path / "intake.db"))
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, demo_site


async def site_control(base: str, path: str, payload: dict | None = None) -> dict:
    async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
        if payload is None:
            resp = await client.get(path)
        else:
            resp = await client.post(path, json=payload)
        resp.raise_for_status()
        return resp.json()


def index_url(base: str) -> str:
    return base + "/index.html"
