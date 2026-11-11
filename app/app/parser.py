"""公告列表页/详情页解析。

页面结构约定（随附的本地示例站严格遵循，真实站点接入时只需替换本模块的选择器）：

列表页
  * 公告容器：``main.notice-list``（也接受 ``#notice-list`` / ``.notice-list``）；
  * 每条公告：``<article class="notice" data-id="编号" data-kind="类型">``，
    内部有 ``<a class="detail-link" href="详情链接">标题</a>``；
  * 下一页：``nav.pager a[rel=next]``（也接受任意 ``a[rel=next]``）。

详情页
  * 根节点：``<article class="notice-detail" data-notice-id="编号" data-kind="类型">``；
  * 字段表：``<table class="notice-fields">`` 中的 ``<tr data-field="字段名">``；
  * 时间使用 ``<time datetime="带时区的 ISO 8601">``。

解析失败（缺少列表容器、列表项缺详情链接、详情缺编号等）会抛出 ``ValueError``，
由爬虫统一视为“无法核定”，本次扫描不会改动任何状态。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .models import NoticeKind, ScannedNotice

KIND_MAP = {
    "suspension": NoticeKind.SUSPENSION,
    "correction": NoticeKind.CORRECTION,
    "cancellation": NoticeKind.CANCELLATION,
    "unknown": NoticeKind.UNKNOWN,
    "停运": NoticeKind.SUSPENSION,
    "更正": NoticeKind.CORRECTION,
    "撤销": NoticeKind.CANCELLATION,
}

FIELD_KEYS = {
    "notice_id": ("notice_id", "公告编号", "编号"),
    "kind": ("kind", "类型", "公告类型"),
    "route": ("route", "线路", "线路名称"),
    "published_at": ("published_at", "发布时间", "发布日期"),
}


@dataclass(frozen=True)
class ListItem:
    url: str
    notice_id: str | None
    kind_raw: str | None
    title: str | None


@dataclass(frozen=True)
class ListPage:
    items: list[ListItem]
    next_url: str | None


def _abs_url(base: str, href: str) -> str:
    return urljoin(base, href)


def is_same_site(url: str, base_url: str) -> bool:
    """同站判定：协议 + 主机 + 端口一致。"""
    try:
        a, b = urlparse(url), urlparse(base_url)
    except ValueError:
        return False
    if not a.hostname or not b.hostname:
        return False
    scheme_a = a.scheme.lower()
    scheme_b = b.scheme.lower()
    try:
        port_a = a.port or (80 if scheme_a == "http" else 443)
        port_b = b.port or (80 if scheme_b == "http" else 443)
    except ValueError:
        return False
    return (scheme_a, a.hostname.lower(), port_a) == (
        scheme_b,
        b.hostname.lower(),
        port_b,
    )


def parse_list_page(html: str, page_url: str) -> ListPage:
    """解析列表页；结构不可识别时抛 ``ValueError``。"""
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("main.notice-list, #notice-list, .notice-list")
    if container is None:
        raise ValueError("列表页缺少公告列表容器 main.notice-list")

    items: list[ListItem] = []
    for art in container.select("article.notice"):
        link = art.select_one("a.detail-link[href]") or art.select_one("a[href]")
        if link is None or not link.get("href"):
            raise ValueError("列表项缺少详情链接")
        items.append(
            ListItem(
                url=_abs_url(page_url, link["href"]),
                notice_id=(art.get("data-id") or "").strip() or None,
                kind_raw=(art.get("data-kind") or "").strip() or None,
                title=link.get_text(strip=True) or None,
            )
        )

    nxt = soup.select_one("nav.pager a[rel=next]") or soup.select_one("a[rel=next]")
    next_url = None
    if nxt is not None and nxt.get("href"):
        next_url = _abs_url(page_url, nxt["href"])

    return ListPage(items=items, next_url=next_url)


def _parse_time(value: str) -> datetime:
    v = value.strip()
    if " " in v:
        v = v.replace(" ", "T", 1)
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"时间缺少时区信息: {value!r}")
    return dt


def _field_rows(soup: BeautifulSoup) -> dict[str, str]:
    rows: dict[str, str] = {}
    for tr in soup.select("table.notice-fields tr[data-field]"):
        key = tr["data-field"]
        cell = tr.select_one("td")
        if cell is None:
            continue
        time_el = cell.select_one("time[datetime]")
        rows[key] = time_el["datetime"] if time_el else cell.get_text(strip=True)
    return rows


def _pick(rows: dict[str, str], *names: str) -> str | None:
    for name in names:
        value = rows.get(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return None


def _kind_from(raw: str | None, title: str | None) -> NoticeKind:
    if raw:
        kind = KIND_MAP.get(raw.strip().lower())
        if kind is not None:
            return kind
        kind = KIND_MAP.get(raw.strip())
        if kind is not None:
            return kind
    text = f"{raw or ''} {title or ''}"
    if "撤销" in text:
        return NoticeKind.CANCELLATION
    if "更正" in text:
        return NoticeKind.CORRECTION
    if "停运" in text:
        return NoticeKind.SUSPENSION
    return NoticeKind.UNKNOWN


def parse_detail(html: str, detail_url: str) -> ScannedNotice:
    """解析详情页；缺少可识别编号时抛 ``ValueError``。"""
    soup = BeautifulSoup(html, "html.parser")
    art = soup.select_one("article.notice-detail")
    rows = _field_rows(soup)

    title_el = soup.select_one("h1.notice-title, h1, title")
    title = title_el.get_text(strip=True) if title_el else None

    notice_id = None
    kind_raw = None
    if art is not None:
        notice_id = (art.get("data-notice-id") or "").strip() or None
        kind_raw = (art.get("data-kind") or "").strip() or None
    notice_id = notice_id or _pick(rows, *FIELD_KEYS["notice_id"])
    kind_raw = kind_raw or _pick(rows, *FIELD_KEYS["kind"])
    if not notice_id:
        raise ValueError(f"详情页缺少公告编号: {detail_url}")

    published_raw = _pick(rows, *FIELD_KEYS["published_at"])
    published_at = None
    if published_raw:
        published_at = _parse_time(published_raw)

    return ScannedNotice(
        notice_id=notice_id,
        url=detail_url,
        title=title,
        kind=_kind_from(kind_raw, title),
        route=_pick(rows, *FIELD_KEYS["route"]),
        published_at=published_at,
    )
