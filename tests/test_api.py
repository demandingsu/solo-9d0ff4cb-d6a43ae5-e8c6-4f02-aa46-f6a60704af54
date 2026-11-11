"""API 端到端测试：基线、上新、失败保持不变、重试与确认。"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from tests.conftest import index_url, site_control

pytestmark = pytest.mark.asyncio


async def scan(client: AsyncClient, base: str) -> dict:
    resp = await client.post("/scan", json={"index_url": index_url(base)})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_baseline_then_new_notices_then_confirm_no_reentry(api):
    client, base = api

    first = await scan(client, base)
    assert first["status"] == "baseline_established"
    assert first["baseline"] is True
    assert first["complete"] is True
    assert first["known_count"] == 10
    assert first["new_notices"] == []
    assert first["pending_notices"] == []

    await site_control(base, "/control/batch", {"batch": "v2"})
    second = await scan(client, base)
    assert second["status"] == "updated"
    assert second["baseline"] is False
    assert second["known_count"] == 14
    assert sorted(n["notice_id"] for n in second["new_notices"]) == [
        "N-2026-011",
        "N-2026-012",
        "N-2026-013",
        "N-2026-014",
    ]
    assert len(second["pending_notices"]) == 4

    confirmed = await client.post(
        "/confirm",
        json={"notice_id": "N-2026-011", "index_url": index_url(base)},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"

    again = await client.post(
        "/confirm",
        json={"notice_id": "N-2026-011", "index_url": index_url(base)},
    )
    assert again.json()["status"] == "already_confirmed"

    third = await scan(client, base)
    assert third["status"] == "updated"
    assert third["new_notices"] == []
    assert "N-2026-011" not in [
        n["notice_id"] for n in third["pending_notices"]
    ]
    assert len(third["pending_notices"]) == 3

    pending = (await client.get("/pending", params={"index_url": index_url(base)})).json()
    assert pending["count"] == 3
    assert pending["site_key"].startswith("http://127.0.0.1:")

    known = (await client.get("/known", params={"index_url": index_url(base)})).json()
    assert known["count"] == 14


async def test_detail_failure_returns_undetermined_and_state_is_unchanged(api):
    client, base = api
    first = await scan(client, base)
    assert first["known_count"] == 10

    await site_control(base, "/control/batch", {"batch": "v3"})
    await site_control(
        base,
        "/control/failure",
        {"mode": "http500", "notice_ids": ["N-2026-015"]},
    )

    failed = await scan(client, base)
    assert failed["status"] == "undetermined"
    assert failed["result"] == "undetermined"
    assert failed["complete"] is False
    assert failed["new_notices"] == []
    assert failed["known_count"] == 10
    assert failed["pending_notices"] == []
    assert failed["errors"]

    # 失败没有影响编号集：恢复后可一次性收齐 v3 的 7 条新公告
    await site_control(base, "/control/failure", {"mode": "none"})
    retried = await scan(client, base)
    assert retried["status"] == "updated"
    assert retried["known_count"] == 17
    assert sorted(n["notice_id"] for n in retried["new_notices"]) == [
        "N-2026-011",
        "N-2026-012",
        "N-2026-013",
        "N-2026-014",
        "N-2026-015",
        "N-2026-016",
        "N-2026-017",
    ]
    assert len(retried["pending_notices"]) == 7


async def test_parse_failure_keeps_pending_unchanged(api):
    client, base = api
    await scan(client, base)  # baseline

    await site_control(base, "/control/batch", {"batch": "v2"})
    updated = await scan(client, base)
    before_pending = updated["pending_notices"]

    await site_control(base, "/control/batch", {"batch": "v3"})
    await site_control(
        base,
        "/control/failure",
        {"mode": "malformed", "notice_ids": ["N-2026-015"]},
    )
    failed = await scan(client, base)
    assert failed["status"] == "undetermined"
    assert failed["pending_notices"] == before_pending
    assert failed["known_count"] == 14


async def test_pagination_loop_does_not_establish_baseline(api):
    client, base = api
    await site_control(base, "/control/pagination-loop", {"enabled": True})

    failed = await scan(client, base)
    assert failed["status"] == "undetermined"
    assert failed["baseline"] is False
    assert failed["known_count"] == 0
    assert failed["pending_notices"] == []

    await site_control(base, "/control/pagination-loop", {"enabled": False})
    retried = await scan(client, base)
    assert retried["status"] == "baseline_established"
    assert retried["known_count"] == 10


async def test_list_failure_returns_undetermined(api):
    client, base = api
    await site_control(base, "/control/list-failure", {"enabled": True})
    failed = await scan(client, base)
    assert failed["status"] == "undetermined"
    assert failed["known_count"] == 0
    assert any("列表页抓取失败" in e for e in failed["errors"])


async def test_list_page_limit_from_env(demo_site, tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_LIST_PAGES", "2")
    app = create_app(str(tmp_path / "limited.db"))
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/scan", json={"index_url": index_url(demo_site)}
            )
    data = resp.json()
    assert data["status"] == "undetermined"
    assert any("列表页数上限" in e for e in data["errors"])
    assert data["known_count"] == 0


async def test_concurrent_scans_do_not_duplicate_pending(api):
    client, base = api
    await scan(client, base)
    await site_control(base, "/control/batch", {"batch": "v2"})

    first, second = await asyncio.gather(scan(client, base), scan(client, base))
    new_ids = {
        n["notice_id"]
        for response in (first, second)
        for n in response["new_notices"]
    }
    assert new_ids == {
        "N-2026-011",
        "N-2026-012",
        "N-2026-013",
        "N-2026-014",
    }
    pending = (await client.get("/pending")).json()
    assert pending["count"] == 4
    assert len({n["notice_id"] for n in pending["pending_notices"]}) == 4


async def test_confirm_during_scan_prevents_reentry(api):
    client, base = api
    await scan(client, base)
    await site_control(base, "/control/batch", {"batch": "v2"})

    # 让详情抓取变慢，确保确认发生在扫描提交之前
    await site_control(base, "/control/delay", {"ms": 200})
    scan_task = asyncio.create_task(scan(client, base))
    await asyncio.sleep(0.15)

    confirmed = await client.post("/confirm", json={"notice_id": "N-2026-011"})
    assert confirmed.json()["status"] == "recorded"

    result = await scan_task
    await site_control(base, "/control/delay", {"ms": 0})

    assert result["status"] == "updated"
    assert "N-2026-011" not in [n["notice_id"] for n in result["new_notices"]]
    assert "N-2026-011" not in [
        n["notice_id"] for n in result["pending_notices"]
    ]
    assert result["known_count"] == 14

    following = await scan(client, base)
    assert following["new_notices"] == []
    assert "N-2026-011" not in [
        n["notice_id"] for n in following["pending_notices"]
    ]


async def test_confirm_unknown_id_is_recorded_not_reenqueued(api):
    client, base = api
    await scan(client, base)
    await site_control(base, "/control/batch", {"batch": "v2"})

    # 尚未扫描到该编号就确认：记录为全局确认
    data = (await client.post("/confirm", json={"notice_id": "N-2026-011"})).json()
    assert data["status"] == "recorded"

    result = await scan(client, base)
    assert "N-2026-011" not in [n["notice_id"] for n in result["new_notices"]]
    assert "N-2026-011" not in [
        n["notice_id"] for n in result["pending_notices"]
    ]
    assert result["known_count"] == 14
