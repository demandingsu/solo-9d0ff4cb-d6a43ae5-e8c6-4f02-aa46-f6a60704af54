"""同站分页 + 详情抓取。

完整性是本服务的核心：只有“列表页全部跟随完、所有同站详情页全部成功抓取并解析”
才算一次完整扫描。任何下列情况都会把 ``complete`` 置为 ``False``：

* 列表页或详情页 HTTP 抓取失败；
* 列表页或详情页解析失败；
* 分页出现循环（下一页回到已抓过的页面）；
* 列表页数达到上限（仍有下一页时）；
* 详情页数超过上限。

扫描不完整时调用方不得写入 SQLite，因此编号集与待交接列表保持原状。
跨站链接按约定跳过并记录，不属于失败。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .models import ScannedNotice
from .parser import is_same_site, parse_detail, parse_list_page


class FetchError(Exception):
    """抓取失败（HTTP 错误、连接错误或非法 URL）。"""


@dataclass
class CrawlResult:
    notices: list[ScannedNotice] = field(default_factory=list)
    list_pages: list[str] = field(default_factory=list)
    detail_pages: int = 0
    skipped_offsite: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    complete: bool = True


class Crawler:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_list_pages: int = 50,
        max_detail_pages: int = 500,
    ) -> None:
        if max_list_pages < 1 or max_detail_pages < 1:
            raise ValueError("页数上限必须为正整数")
        self._client = client
        self._max_list_pages = max_list_pages
        self._max_detail_pages = max_detail_pages

    async def _get(self, url: str) -> str:
        try:
            resp = await self._client.get(url, follow_redirects=True)
            resp.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc
        return resp.text

    async def crawl(self, index_url: str) -> CrawlResult:
        result = CrawlResult()
        seen_pages: set[str] = set()
        detail_urls: list[str] = []
        seen_detail_urls: set[str] = set()

        page_url: str | None = index_url
        while page_url is not None:
            # 分页循环：下一页指向已经抓过的页面
            if page_url in seen_pages:
                result.complete = False
                result.errors.append(f"分页循环：{page_url} 已抓取过，无法核定完整性")
                break
            # 页数上限：仅在仍有下一页时触发（若刚好结束则属于完整扫描）
            if len(seen_pages) >= self._max_list_pages:
                result.complete = False
                result.errors.append(
                    f"达到列表页数上限 {self._max_list_pages}，无法核定完整性"
                )
                break

            seen_pages.add(page_url)
            try:
                html = await self._get(page_url)
            except FetchError as exc:
                result.complete = False
                result.errors.append(f"列表页抓取失败 {page_url}: {exc}")
                break
            result.list_pages.append(page_url)

            try:
                page = parse_list_page(html, page_url)
            except ValueError as exc:
                result.complete = False
                result.errors.append(f"列表页解析失败 {page_url}: {exc}")
                break

            for item in page.items:
                if not is_same_site(item.url, index_url):
                    result.skipped_offsite.append(item.url)
                    continue
                if item.url not in seen_detail_urls:
                    seen_detail_urls.add(item.url)
                    detail_urls.append(item.url)

            nxt = page.next_url
            if nxt is not None and not is_same_site(nxt, index_url):
                result.skipped_offsite.append(nxt)
                nxt = None
            page_url = nxt

        if not result.complete:
            # 列表都不完整时不再解析详情；本次扫描不会产生任何写入。
            return result

        if len(detail_urls) > self._max_detail_pages:
            result.complete = False
            result.errors.append(
                f"详情页数 {len(detail_urls)} 超过上限 {self._max_detail_pages}，"
                "无法核定完整性"
            )
            return result

        for detail_url in detail_urls:
            try:
                html = await self._get(detail_url)
            except FetchError as exc:
                result.complete = False
                result.errors.append(f"详情页抓取失败 {detail_url}: {exc}")
                continue
            result.detail_pages += 1
            try:
                result.notices.append(parse_detail(html, detail_url))
            except ValueError as exc:
                result.complete = False
                result.errors.append(f"详情页解析失败 {detail_url}: {exc}")

        return result
