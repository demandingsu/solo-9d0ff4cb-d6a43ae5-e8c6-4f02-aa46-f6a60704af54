"""本地示例公告站的数据批次与 HTML 渲染。

三批公告用于离线演示：

* ``v1`` —— 首次扫描的基线（10 条，3 个列表页）；
* ``v2`` —— 运营方上新 4 条（含停运、更正、撤销）；
* ``v3`` —— 再上新 3 条，其中 ``N-2026-015`` 可被配置为详情故障，
  用于演示“失败 → 无法核定 → 重试成功”。
"""
from __future__ import annotations

import html
from dataclasses import dataclass

PAGE_SIZE = 4


@dataclass(frozen=True)
class Notice:
    notice_id: str
    kind: str  # suspension | correction | cancellation
    title: str
    route: str
    published_at: str
    suspend_start: str | None = None
    suspend_end: str | None = None


def _build_v1() -> list[Notice]:
    rows: list[Notice] = []
    routes = ["12 路", "18 路", "25 路", "33 路", "5 路", "40 路", "50 路", "66 路"]
    for idx, route in enumerate(routes, start=1):
        rows.append(
            Notice(
                notice_id=f"N-2026-{idx:03d}",
                kind="suspension",
                title=f"{route}临时停运公告",
                route=route,
                published_at=f"2026-11-{idx + 1:02d}T09:00:00+08:00",
                suspend_start=f"2026-11-{idx + 3:02d}T06:00:00+08:00",
                suspend_end=f"2026-11-{idx + 8:02d}T22:00:00+08:00",
            )
        )
    rows.extend(
        [
            Notice(
                "N-2026-009",
                "correction",
                "25 路停运时间更正公告",
                "25 路",
                "2026-11-10T15:00:00+08:00",
                "2026-11-11T06:00:00+08:00",
                "2026-11-20T22:00:00+08:00",
            ),
            Notice(
                "N-2026-010",
                "cancellation",
                "33 路停运撤销公告",
                "33 路",
                "2026-11-10T18:00:00+08:00",
            ),
        ]
    )
    return rows


V1: list[Notice] = _build_v1()

V2: list[Notice] = V1 + [
    Notice(
        "N-2026-011",
        "suspension",
        "70 路临时停运公告",
        "70 路",
        "2026-11-12T08:30:00+08:00",
        "2026-11-13T06:00:00+08:00",
        "2026-11-16T22:00:00+08:00",
    ),
    Notice(
        "N-2026-012",
        "correction",
        "12 路停运时间更正公告",
        "12 路",
        "2026-11-12T09:00:00+08:00",
        "2026-11-13T06:00:00+08:00",
        "2026-11-18T22:00:00+08:00",
    ),
    Notice(
        "N-2026-013",
        "cancellation",
        "18 路停运撤销公告",
        "18 路",
        "2026-11-12T10:00:00+08:00",
    ),
    Notice(
        "N-2026-014",
        "suspension",
        "88 路临时停运公告",
        "88 路",
        "2026-11-12T11:00:00+08:00",
        "2026-11-14T06:00:00+08:00",
        "2026-11-17T22:00:00+08:00",
    ),
]

V3: list[Notice] = V2 + [
    Notice(
        "N-2026-015",
        "correction",
        "70 路停运时间更正公告",
        "70 路",
        "2026-11-13T08:00:00+08:00",
        "2026-11-14T06:00:00+08:00",
        "2026-11-19T22:00:00+08:00",
    ),
    Notice(
        "N-2026-016",
        "cancellation",
        "40 路停运撤销公告",
        "40 路",
        "2026-11-13T09:00:00+08:00",
    ),
    Notice(
        "N-2026-017",
        "suspension",
        "99 路临时停运公告",
        "99 路",
        "2026-11-13T10:00:00+08:00",
        "2026-11-15T06:00:00+08:00",
        "2026-11-18T22:00:00+08:00",
    ),
]

BATCHES: dict[str, list[Notice]] = {"v1": V1, "v2": V2, "v3": V3}

KIND_CN = {"suspension": "停运", "correction": "更正", "cancellation": "撤销"}


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def detail_path(notice: Notice) -> str:
    return f"/notices/{notice.notice_id}.html"


def total_pages(batch: list[Notice], page_size: int = PAGE_SIZE) -> int:
    return max(1, (len(batch) + page_size - 1) // page_size)


def page_notices(
    batch: list[Notice], page_idx: int, page_size: int = PAGE_SIZE
) -> list[Notice]:
    start = page_idx * page_size
    return batch[start : start + page_size]


def render_list(
    items: list[Notice],
    page_idx: int,
    pages: int,
    *,
    loop_next: bool = False,
) -> str:
    articles: list[str] = []
    for notice in items:
        articles.append(
            f'    <article class="notice" data-id="{_esc(notice.notice_id)}" '
            f'data-kind="{_esc(notice.kind)}">\n'
            f'      <a class="detail-link" href="{_esc(detail_path(notice))}">'
            f"{_esc(notice.title)}</a>\n"
            f'      <time class="published" datetime="{_esc(notice.published_at)}">'
            f"{_esc(notice.published_at.replace('T', ' '))}</time>\n"
            f"    </article>"
        )

    pager: list[str] = []
    if page_idx > 0:
        prev_name = "index.html" if page_idx == 1 else f"page{page_idx}.html"
        pager.append(f'<a rel="prev" href="/{prev_name}">上一页</a>')
    if loop_next:
        target = "/index.html" if pages == 1 else "/page2.html"
        pager.append(f'<a rel="next" href="{target}">下一页</a>')
    elif page_idx < pages - 1:
        next_name = "page2.html" if page_idx == 0 else f"page{page_idx + 2}.html"
        pager.append(f'<a rel="next" href="/{next_name}">下一页</a>')
    pager_html = f'  <nav class="pager">{" ".join(pager)}</nav>' if pager else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>线路公告列表（第 {page_idx + 1} 页）</title></head>
<body>
  <h1>线路公告列表</h1>
  <main class="notice-list">
{chr(10).join(articles)}
  </main>
{pager_html}
</body>
</html>
"""


def render_detail(notice: Notice) -> str:
    rows: list[str] = []

    def add(field: str, label: str, value: str | None, is_time: bool = False) -> None:
        if value is None:
            return
        if is_time:
            cell = (
                f'<time datetime="{_esc(value)}">'
                f"{_esc(value.replace('T', ' '))}</time>"
            )
        else:
            cell = _esc(value)
        rows.append(
            f'      <tr data-field="{field}"><th>{label}</th>'
            f"<td>{cell}</td></tr>"
        )

    add("notice_id", "公告编号", notice.notice_id)
    add("kind", "类型", KIND_CN[notice.kind])
    add("route", "线路", notice.route)
    add("suspend_start", "停运开始时间", notice.suspend_start, is_time=True)
    add("suspend_end", "停运结束时间", notice.suspend_end, is_time=True)
    add("published_at", "发布时间", notice.published_at, is_time=True)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{_esc(notice.title)}</title></head>
<body>
  <article class="notice-detail" data-notice-id="{_esc(notice.notice_id)}"
           data-kind="{_esc(notice.kind)}">
    <h1 class="notice-title">{_esc(notice.title)}</h1>
    <table class="notice-fields">
{chr(10).join(rows)}
    </table>
    <p><a href="/index.html">返回公告列表</a></p>
  </article>
</body>
</html>
"""


def render_malformed_detail(notice_id: str) -> str:
    """详情页结构损坏（HTTP 200 但缺少编号字段，解析会失败）。"""
    return (
        "<!DOCTYPE html><html><body><article class=\"notice-detail\">"
        f"<h1>公告 {_esc(notice_id)} 加载失败</h1>"
        "<p>详情字段未渲染</p></article></body></html>"
    )
