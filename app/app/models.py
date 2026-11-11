"""领域模型与 API 契约。

本服务的核心对象只有三类：

* ``ScannedNotice`` —— 一次扫描从详情页解析出的公告（编号、原文链接、类型等）；
* ``KnownNotice``   —— SQLite 中“已收录编号集”的一行；
* ``PendingNotice`` —— 待交接列表中的一条公告（新编号 + 原文链接）。

所有时间统一使用带时区的 ``datetime``；对外输出 ISO 8601。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class NoticeKind(str, Enum):
    """公告类型。"""

    SUSPENSION = "suspension"      # 停运
    CORRECTION = "correction"      # 更正
    CANCELLATION = "cancellation"  # 撤销
    UNKNOWN = "unknown"            # 无法从页面确认类型（仍可收件）


class ScannedNotice(BaseModel):
    """一次完整扫描中从详情页解析出的公告。"""

    notice_id: str = Field(..., min_length=1, description="公告编号，如 N-2026-001")
    url: str = Field(..., description="详情页原文链接")
    title: str | None = None
    kind: NoticeKind = NoticeKind.UNKNOWN
    route: str | None = None
    published_at: datetime | None = Field(
        None, description="公告发布时间（页面未提供时为 None）"
    )

    @field_validator("notice_id")
    @classmethod
    def _strip_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("公告编号不能为空")
        return v

    @field_validator("published_at")
    @classmethod
    def _published_aware(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return v
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("published_at 必须带时区信息")
        return v


class KnownNotice(BaseModel):
    """已收录编号集中的一条。"""

    notice_id: str
    url: str
    first_seen_at: datetime


class PendingNotice(BaseModel):
    """待交接列表中的一条公告。"""

    site_key: str
    notice_id: str
    url: str
    title: str | None = None
    kind: NoticeKind = NoticeKind.UNKNOWN
    route: str | None = None
    published_at: datetime | None = None
    discovered_at: datetime


class SiteScanState(BaseModel):
    """一个站点当前的编号集/待交接快照（只读诊断用）。"""

    site_key: str
    index_url: str | None = None
    baseline_established: bool = False
    known_count: int = 0
    pending_count: int = 0


class ScanRequest(BaseModel):
    """扫描入参。"""

    index_url: str = Field(..., description="运营方公告首页 URL（同站分页起点）")

    @field_validator("index_url")
    @classmethod
    def _url_scheme(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("index_url 必须是 http/https URL")
        return v


class ScanResponse(BaseModel):
    """一次扫描的结果。

    ``status`` 取值：

    * ``baseline_established`` —— 首次完整扫描，建立编号集，不产生待交接项；
    * ``updated``              —— 完整扫描，已收录新编号并加入待交接列表；
    * ``undetermined``         —— 无法核定（抓取/解析失败、分页循环或超过页数上限），
      编号集与待交接列表保持扫描前原状。
    """

    status: Literal["baseline_established", "updated", "undetermined"]
    result: Literal["ok", "undetermined"]
    index_url: str
    site_key: str
    complete: bool
    baseline: bool
    scanned_count: int
    known_count: int
    list_pages_crawled: list[str] = Field(default_factory=list)
    detail_pages_crawled: int = 0
    skipped_offsite_links: list[str] = Field(default_factory=list)
    new_notices: list[ScannedNotice] = Field(default_factory=list)
    pending_notices: list[PendingNotice] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    message: str


class PendingResponse(BaseModel):
    status: Literal["ok"] = "ok"
    site_key: str | None = None
    count: int
    pending_notices: list[PendingNotice]


class KnownResponse(BaseModel):
    status: Literal["ok"] = "ok"
    site_key: str | None = None
    count: int
    known_notices: list[KnownNotice]


class ConfirmRequest(BaseModel):
    """确认入参。

    ``index_url`` 可省略：省略时按编号在所有已扫描站点中查找并确认；
    若该编号尚未被任何站点收录，则记录为全局确认，之后任何扫描都不会再把它入列。
    """

    notice_id: str = Field(..., min_length=1)
    index_url: str | None = Field(
        None, description="站点公告首页 URL；提供时只确认该站点"
    )

    @field_validator("notice_id")
    @classmethod
    def _strip_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("notice_id 不能为空")
        return v

    @field_validator("index_url")
    @classmethod
    def _url_scheme(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("index_url 必须是 http/https URL")
        return v


class ConfirmResponse(BaseModel):
    """确认结果。"""

    status: Literal["confirmed", "already_confirmed", "recorded", "not_found"]
    notice_id: str
    site_keys: list[str] = Field(default_factory=list)
    message: str
