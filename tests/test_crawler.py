"""爬虫测试：完整扫描、详情失败、分页循环与页数上限。"""
from __future__ import annotations

import httpx
import pytest

from app.crawler import Crawler
from tests.conftest import index_url, site_control

pytestmark = pytest.mark.asyncio


async def crawl(base: str, **kwargs) -> object:
    async with httpx.AsyncClient(timeout=10.0) as client:
        crawler = Crawler(client, **kwargs)
        return await crawler.crawl(index_url(base))


async def test_complete_scan_follows_all_pages_and_details(demo_site):
    result = await crawl(demo_site)
    assert result.complete is True
    assert len(result.list_pages) == 3
    assert result.detail_pages == 10
    assert len(result.notices) == 10
    assert "N-2026-010" in {n.notice_id for n in result.notices}


async def test_detail_http_failure_marks_incomplete(demo_site):
    await site_control(demo_site, "/control/batch", {"batch": "v3"})
    await site_control(
        demo_site,
        "/control/failure",
        {"mode": "http500", "notice_ids": ["N-2026-015"]},
    )

    result = await crawl(demo_site)
    assert result.complete is False
    assert any("详情页抓取失败" in e for e in result.errors)


async def test_detail_parse_failure_marks_incomplete(demo_site):
    await site_control(demo_site, "/control/batch", {"batch": "v3"})
    await site_control(
        demo_site,
        "/control/failure",
        {"mode": "malformed", "notice_ids": ["N-2026-015"]},
    )

    result = await crawl(demo_site)
    assert result.complete is False
    assert any("详情页解析失败" in e for e in result.errors)


async def test_list_failure_marks_incomplete(demo_site):
    await site_control(demo_site, "/control/list-failure", {"enabled": True})
    result = await crawl(demo_site)
    assert result.complete is False
    assert result.list_pages == []
    assert any("列表页抓取失败" in e for e in result.errors)


async def test_pagination_loop_marks_incomplete(demo_site):
    await site_control(demo_site, "/control/pagination-loop", {"enabled": True})
    result = await crawl(demo_site)
    assert result.complete is False
    assert any("分页循环" in e for e in result.errors)


async def test_list_page_limit_marks_incomplete(demo_site):
    result = await crawl(demo_site, max_list_pages=2)
    assert result.complete is False
    assert len(result.list_pages) == 2
    assert any("列表页数上限" in e for e in result.errors)


async def test_detail_page_limit_marks_incomplete(demo_site):
    result = await crawl(demo_site, max_detail_pages=3)
    assert result.complete is False
    assert result.detail_pages == 0
    assert any("详情页数" in e for e in result.errors)
