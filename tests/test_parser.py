"""解析器测试：列表容器、分页链接、详情字段与失败路径。"""
from __future__ import annotations

import pytest

import batches
from app.parser import is_same_site, parse_detail, parse_list_page


def test_parse_list_page_and_next_link():
    batch = batches.V1
    pages = batches.total_pages(batch)
    html = batches.render_list(batches.page_notices(batch, 0), 0, pages)
    page = parse_list_page(html, "http://site:8080/index.html")

    assert [item.notice_id for item in page.items] == [
        "N-2026-001",
        "N-2026-002",
        "N-2026-003",
        "N-2026-004",
    ]
    assert page.items[0].url == "http://site:8080/notices/N-2026-001.html"
    assert page.items[0].kind_raw == "suspension"
    assert page.next_url == "http://site:8080/page2.html"


def test_last_page_has_no_next_link():
    batch = batches.V1
    pages = batches.total_pages(batch)
    html = batches.render_list(batches.page_notices(batch, pages - 1), pages - 1, pages)
    assert parse_list_page(html, "http://site:8080/page3.html").next_url is None


def test_parse_suspension_detail():
    notice = batches.V1[0]
    parsed = parse_detail(batches.render_detail(notice), "http://site:8080/notices/x.html")

    assert parsed.notice_id == notice.notice_id
    assert parsed.route == notice.route
    assert parsed.kind.value == "suspension"
    assert parsed.published_at is not None
    assert parsed.published_at.utcoffset() is not None
    assert parsed.title == notice.title


def test_parse_correction_and_cancellation_kinds():
    corr = next(n for n in batches.V2 if n.notice_id == "N-2026-012")
    cancel = next(n for n in batches.V2 if n.notice_id == "N-2026-013")
    assert parse_detail(batches.render_detail(corr), "http://x/1").kind.value == "correction"
    assert parse_detail(batches.render_detail(cancel), "http://x/2").kind.value == "cancellation"


def test_malformed_detail_raises():
    with pytest.raises(ValueError):
        parse_detail(batches.render_malformed_detail("N-2026-015"), "http://x/3")


def test_missing_list_container_raises():
    with pytest.raises(ValueError):
        parse_list_page("<html><body><h1>维护中</h1></body></html>", "http://x/")


def test_same_site_requires_scheme_host_port_match():
    assert is_same_site("http://site:8080/a", "http://site:8080/index.html")
    assert not is_same_site("https://site:8080/a", "http://site:8080/index.html")
    assert not is_same_site("http://site:9090/a", "http://site:8080/index.html")
    assert not is_same_site("http://other:8080/a", "http://site:8080/index.html")
