"""FastAPI 入口：增量收件 / 待交接列表 / 确认。

接口
----
* ``POST /scan``     按公告首页 URL 做一次同站全量扫描，完整才写入状态；
* ``GET  /pending``  查看待交接列表（可按 index_url 过滤）；
* ``POST /confirm``  确认指定编号，之后不再提示；
* ``GET  /known``    查看已收录编号集（诊断/核对用）；
* ``GET  /health``   健康检查。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import FastAPI, HTTPException, Query

from .crawler import Crawler
from .models import (
    ConfirmRequest,
    ConfirmResponse,
    KnownResponse,
    PendingResponse,
    ScanRequest,
    ScanResponse,
)
from .store import Database, database_path_from_env, site_key_for

logger = logging.getLogger("notice_intake")


def _positive_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是正整数") from exc
    if value < 1:
        raise RuntimeError(f"{name} 必须是正整数")
    return value


def create_app(db_path: str | None = None) -> FastAPI:
    database = Database(db_path or database_path_from_env())
    max_list_pages = _positive_env("MAX_LIST_PAGES", 50)
    max_detail_pages = _positive_env("MAX_DETAIL_PAGES", 500)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.initialize()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={"User-Agent": "notice-intake-api/1.0"},
        ) as client:
            app.state.http_client = client
            yield

    app = FastAPI(
        title="线路公告增量收件 API",
        version="1.0.0",
        description=(
            "按公告首页做同站分页/详情扫描；首次完整扫描建立 SQLite 已收录编号集，"
            "此后仅完整扫描才把新编号加入待交接列表。抓取/解析失败、分页循环或"
            "超过页数上限时返回“无法核定”，不改变任何状态。"
        ),
        lifespan=lifespan,
    )
    app.state.db = database

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/scan", response_model=ScanResponse)
    async def scan(req: ScanRequest) -> ScanResponse:
        try:
            site_key = site_key_for(req.index_url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        crawler = Crawler(
            app.state.http_client,
            max_list_pages=max_list_pages,
            max_detail_pages=max_detail_pages,
        )
        crawl = await crawler.crawl(req.index_url)

        if not crawl.complete:
            # 无法核定：只读当前快照原样返回，绝不写入。
            baseline, known_count, pending = database.read_snapshot(site_key)
            first_error = crawl.errors[0] if crawl.errors else "未知原因"
            logger.warning(
                "扫描无法核定 site=%s errors=%d: %s",
                site_key,
                len(crawl.errors),
                first_error,
            )
            return ScanResponse(
                status="undetermined",
                result="undetermined",
                index_url=req.index_url,
                site_key=site_key,
                complete=False,
                baseline=baseline,
                scanned_count=len(crawl.notices),
                known_count=known_count,
                list_pages_crawled=crawl.list_pages,
                detail_pages_crawled=crawl.detail_pages,
                skipped_offsite_links=crawl.skipped_offsite,
                new_notices=[],
                pending_notices=pending,
                errors=crawl.errors,
                message=f"无法核定：{first_error}",
            )

        commit = database.commit_scan(site_key, req.index_url, crawl.notices)

        if commit.baseline:
            status = "baseline_established"
            message = (
                f"首次完整扫描完成，已建立编号集（{commit.known_count} 条），"
                "已有公告不进入待交接列表"
            )
        else:
            status = "updated"
            if commit.new_notices:
                message = (
                    f"完整扫描完成，新增 {len(commit.new_notices)} 条公告，"
                    "已加入待交接列表"
                )
            else:
                message = "完整扫描完成，无新增公告"

        logger.info(
            "扫描完成 site=%s baseline=%s scanned=%d known=%d new=%d",
            site_key,
            commit.baseline,
            commit.scanned_count,
            commit.known_count,
            len(commit.new_notices),
        )

        return ScanResponse(
            status=status,
            result="ok",
            index_url=req.index_url,
            site_key=site_key,
            complete=True,
            baseline=commit.baseline,
            scanned_count=commit.scanned_count,
            known_count=commit.known_count,
            list_pages_crawled=crawl.list_pages,
            detail_pages_crawled=crawl.detail_pages,
            skipped_offsite_links=crawl.skipped_offsite,
            new_notices=commit.new_notices,
            pending_notices=commit.pending_notices,
            errors=[],
            message=message,
        )

    @app.get("/pending", response_model=PendingResponse)
    async def pending(
        index_url: Annotated[str | None, Query(description="公告首页 URL")] = None,
    ) -> PendingResponse:
        site_key = None
        if index_url is not None:
            try:
                site_key = site_key_for(index_url)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        items = database.list_pending(site_key)
        return PendingResponse(
            site_key=site_key, count=len(items), pending_notices=items
        )

    @app.post("/confirm", response_model=ConfirmResponse)
    async def confirm(req: ConfirmRequest) -> ConfirmResponse:
        site_key = None
        if req.index_url is not None:
            try:
                site_key = site_key_for(req.index_url)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        result = database.confirm(req.notice_id, site_key)
        messages = {
            "confirmed": "已确认，该编号不再进入待交接列表",
            "already_confirmed": "该编号此前已确认，无需重复确认",
            "recorded": "该编号尚未被扫描收录，已记录为确认，后续扫描不会入列",
            "not_found": "未找到该编号",
        }
        return ConfirmResponse(
            status=result.status,
            notice_id=req.notice_id,
            site_keys=result.site_keys,
            message=messages[result.status],
        )

    @app.get("/known", response_model=KnownResponse)
    async def known(
        index_url: Annotated[str | None, Query(description="公告首页 URL")] = None,
    ) -> KnownResponse:
        site_key = None
        if index_url is not None:
            try:
                site_key = site_key_for(index_url)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        items = database.list_known(site_key)
        return KnownResponse(
            site_key=site_key, count=len(items), known_notices=items
        )

    return app


app = create_app()
