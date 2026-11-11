"""SQLite 状态存储：已收录编号集、待交接列表、确认记录。

设计要点
--------
* 站点身份 = 协议 + 主机 + 端口（与爬虫的同站判定一致）。
* 一次扫描的所有写入放在同一个 ``BEGIN IMMEDIATE`` 事务里：
  - 未完整扫描时根本不会调用本模块的写入方法，因此状态保持原状；
  - 事务内重新读取编号集/确认记录后再计算“新编号”，
    避免并发扫描基于旧快照重复入列；
  - ``pending_notices`` 与 ``known_notices`` 的主键分别为
    ``(site_key, notice_id)``，天然去重。
* 确认记录分两类：
  - ``confirmed_notices``：站点内确认；
  - ``orphan_confirmations``：未提供站点且该编号尚未被任何站点收录时，
    记录为全局确认。这样“扫描进行中、编号尚未入列时用户先确认”的并发场景，
    扫描提交时也会跳过该编号，不会把它重新入列。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .models import KnownNotice, NoticeKind, PendingNotice, ScannedNotice

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    site_key    TEXT PRIMARY KEY,
    index_url   TEXT NOT NULL,
    baseline_at TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS known_notices (
    site_key      TEXT NOT NULL,
    notice_id     TEXT NOT NULL,
    url           TEXT NOT NULL,
    title         TEXT,
    kind          TEXT,
    route         TEXT,
    published_at  TEXT,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (site_key, notice_id)
);

CREATE TABLE IF NOT EXISTS pending_notices (
    site_key      TEXT NOT NULL,
    notice_id     TEXT NOT NULL,
    url           TEXT NOT NULL,
    title         TEXT,
    kind          TEXT,
    route         TEXT,
    published_at  TEXT,
    discovered_at TEXT NOT NULL,
    PRIMARY KEY (site_key, notice_id)
);

CREATE TABLE IF NOT EXISTS confirmed_notices (
    site_key     TEXT NOT NULL,
    notice_id    TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    PRIMARY KEY (site_key, notice_id)
);

CREATE TABLE IF NOT EXISTS orphan_confirmations (
    notice_id    TEXT PRIMARY KEY,
    confirmed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_known_site ON known_notices (site_key);
CREATE INDEX IF NOT EXISTS idx_pending_site ON pending_notices (site_key);
CREATE INDEX IF NOT EXISTS idx_confirmed_site ON confirmed_notices (site_key);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def site_key_for(index_url: str) -> str:
    """把公告首页 URL 归一化为站点键：``scheme://host:port``。"""
    parts = urlsplit(index_url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        raise ValueError("index_url 必须是包含主机名的 http/https URL")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"index_url 端口非法: {index_url}") from exc
    port = port or (80 if scheme == "http" else 443)
    return f"{scheme}://{host}:{port}"


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


@dataclass
class CommitResult:
    baseline: bool
    scanned_count: int
    known_count: int
    new_notices: list[ScannedNotice]
    pending_notices: list[PendingNotice]


@dataclass
class ConfirmResult:
    status: str
    site_keys: list[str]


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    def _connect(self) -> sqlite3.Connection:
        parent = Path(self.path).parent
        if str(parent):
            parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
        finally:
            conn.close()

    # ------------------------------------------------------------------ 扫描提交
    def commit_scan(
        self,
        site_key: str,
        index_url: str,
        notices: list[ScannedNotice],
    ) -> CommitResult:
        """完整扫描后原子地更新编号集并登记新编号。"""
        # 同一编号在多个列表页/详情链接重复出现时保留第一次出现的元数据
        by_id: dict[str, ScannedNotice] = {}
        for notice in notices:
            by_id.setdefault(notice.notice_id, notice)

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = _now()

            site_row = conn.execute(
                "SELECT baseline_at FROM sites WHERE site_key = ?", (site_key,)
            ).fetchone()
            baseline = site_row is None

            known_ids = {
                row["notice_id"]
                for row in conn.execute(
                    "SELECT notice_id FROM known_notices WHERE site_key = ?",
                    (site_key,),
                )
            }
            confirmed_ids = {
                row["notice_id"]
                for row in conn.execute(
                    "SELECT notice_id FROM confirmed_notices WHERE site_key = ?",
                    (site_key,),
                )
            }
            orphan_ids = {
                row["notice_id"]
                for row in conn.execute("SELECT notice_id FROM orphan_confirmations")
            }
            already_confirmed = confirmed_ids | orphan_ids

            new_notices: list[ScannedNotice] = []
            if not baseline:
                new_notices = [
                    notice
                    for nid, notice in by_id.items()
                    if nid not in known_ids and nid not in already_confirmed
                ]

            # 同步更新编号集（首次扫描即建立基线；之后只增不删）
            for notice in by_id.values():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO known_notices
                        (site_key, notice_id, url, title, kind, route,
                         published_at, first_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        site_key,
                        notice.notice_id,
                        notice.url,
                        notice.title,
                        notice.kind.value,
                        notice.route,
                        notice.published_at.isoformat()
                        if notice.published_at
                        else None,
                        now,
                    ),
                )

            if baseline:
                conn.execute(
                    """
                    INSERT INTO sites
                        (site_key, index_url, baseline_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (site_key, index_url, now, now, now),
                )
            else:
                conn.execute(
                    "UPDATE sites SET index_url = ?, updated_at = ? WHERE site_key = ?",
                    (index_url, now, site_key),
                )

            for notice in new_notices:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO pending_notices
                        (site_key, notice_id, url, title, kind, route,
                         published_at, discovered_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        site_key,
                        notice.notice_id,
                        notice.url,
                        notice.title,
                        notice.kind.value,
                        notice.route,
                        notice.published_at.isoformat()
                        if notice.published_at
                        else None,
                        now,
                    ),
                )

            # 扫描时遇到“全局确认”的编号，落成该站点的确认记录
            for nid in by_id:
                if nid in orphan_ids:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO confirmed_notices
                            (site_key, notice_id, confirmed_at)
                        VALUES (?, ?, ?)
                        """,
                        (site_key, nid, now),
                    )

            known_count = conn.execute(
                "SELECT COUNT(*) AS c FROM known_notices WHERE site_key = ?",
                (site_key,),
            ).fetchone()["c"]
            pending = self._pending_rows(conn, site_key)
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

        return CommitResult(
            baseline=baseline,
            scanned_count=len(by_id),
            known_count=known_count,
            new_notices=new_notices,
            pending_notices=pending,
        )

    # ------------------------------------------------------------------ 只读查询
    def read_snapshot(self, site_key: str) -> tuple[bool, int, list[PendingNotice]]:
        """读取站点当前状态（用于“无法核定”时原样返回）。"""
        conn = self._connect()
        try:
            site = conn.execute(
                "SELECT baseline_at FROM sites WHERE site_key = ?", (site_key,)
            ).fetchone()
            known_count = conn.execute(
                "SELECT COUNT(*) AS c FROM known_notices WHERE site_key = ?",
                (site_key,),
            ).fetchone()["c"]
            pending = self._pending_rows(conn, site_key)
            return site is not None, int(known_count), pending
        finally:
            conn.close()

    def list_pending(self, site_key: str | None = None) -> list[PendingNotice]:
        conn = self._connect()
        try:
            return self._pending_rows(conn, site_key)
        finally:
            conn.close()

    def list_known(self, site_key: str | None = None) -> list[KnownNotice]:
        conn = self._connect()
        try:
            sql = (
                "SELECT notice_id, url, first_seen_at FROM known_notices "
                + ("WHERE site_key = ? " if site_key else "")
                + "ORDER BY notice_id"
            )
            params: tuple[str, ...] = (site_key,) if site_key else ()
            return [
                KnownNotice(
                    notice_id=row["notice_id"],
                    url=row["url"],
                    first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
                )
                for row in conn.execute(sql, params)
            ]
        finally:
            conn.close()

    @staticmethod
    def _pending_rows(
        conn: sqlite3.Connection, site_key: str | None
    ) -> list[PendingNotice]:
        sql = (
            "SELECT site_key, notice_id, url, title, kind, route, published_at, "
            "discovered_at FROM pending_notices "
            + ("WHERE site_key = ? " if site_key else "")
            + "ORDER BY discovered_at, notice_id"
        )
        params: tuple[str, ...] = (site_key,) if site_key else ()
        out: list[PendingNotice] = []
        for row in conn.execute(sql, params):
            kind_raw = row["kind"] or NoticeKind.UNKNOWN.value
            try:
                kind = NoticeKind(kind_raw)
            except ValueError:
                kind = NoticeKind.UNKNOWN
            out.append(
                PendingNotice(
                    site_key=row["site_key"],
                    notice_id=row["notice_id"],
                    url=row["url"],
                    title=row["title"],
                    kind=kind,
                    route=row["route"],
                    published_at=_dt(row["published_at"]),
                    discovered_at=datetime.fromisoformat(row["discovered_at"]),
                )
            )
        return out

    # ------------------------------------------------------------------ 确认
    def confirm(self, notice_id: str, site_key: str | None = None) -> ConfirmResult:
        """确认编号：从待交接列表移除并永久记录，后续扫描不再入列。"""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = _now()

            if site_key is not None:
                was_pending = conn.execute(
                    "SELECT 1 FROM pending_notices WHERE site_key = ? AND notice_id = ?",
                    (site_key, notice_id),
                ).fetchone()
                already = conn.execute(
                    "SELECT 1 FROM confirmed_notices "
                    "WHERE site_key = ? AND notice_id = ?",
                    (site_key, notice_id),
                ).fetchone()
                conn.execute(
                    """
                    INSERT OR IGNORE INTO confirmed_notices
                        (site_key, notice_id, confirmed_at)
                    VALUES (?, ?, ?)
                    """,
                    (site_key, notice_id, now),
                )
                conn.execute(
                    "DELETE FROM pending_notices WHERE site_key = ? AND notice_id = ?",
                    (site_key, notice_id),
                )
                conn.execute("COMMIT")
                status = (
                    "confirmed"
                    if was_pending or not already
                    else "already_confirmed"
                )
                return ConfirmResult(status=status, site_keys=[site_key])

            pending_sites = [
                row["site_key"]
                for row in conn.execute(
                    "SELECT DISTINCT site_key FROM pending_notices WHERE notice_id = ?",
                    (notice_id,),
                )
            ]
            if pending_sites:
                for sk in pending_sites:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO confirmed_notices
                            (site_key, notice_id, confirmed_at)
                        VALUES (?, ?, ?)
                        """,
                        (sk, notice_id, now),
                    )
                conn.execute(
                    "DELETE FROM pending_notices WHERE notice_id = ?",
                    (notice_id,),
                )
                conn.execute("COMMIT")
                return ConfirmResult(status="confirmed", site_keys=pending_sites)

            known_sites = [
                row["site_key"]
                for row in conn.execute(
                    "SELECT DISTINCT site_key FROM known_notices WHERE notice_id = ?",
                    (notice_id,),
                )
            ]
            confirmed_sites = [
                row["site_key"]
                for row in conn.execute(
                    "SELECT DISTINCT site_key FROM confirmed_notices "
                    "WHERE notice_id = ?",
                    (notice_id,),
                )
            ]
            if known_sites or confirmed_sites:
                targets = sorted(set(known_sites) | set(confirmed_sites))
                for sk in targets:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO confirmed_notices
                            (site_key, notice_id, confirmed_at)
                        VALUES (?, ?, ?)
                        """,
                        (sk, notice_id, now),
                    )
                conn.execute("COMMIT")
                status = "already_confirmed" if confirmed_sites else "confirmed"
                return ConfirmResult(status=status, site_keys=targets)

            # 尚无任何站点收录该编号：记录全局确认，避免扫描进行中的并发场景
            # 在提交时把用户刚确认的编号重新入列。
            conn.execute(
                """
                INSERT OR IGNORE INTO orphan_confirmations
                    (notice_id, confirmed_at)
                VALUES (?, ?)
                """,
                (notice_id, now),
            )
            conn.execute("COMMIT")
            return ConfirmResult(status="recorded", site_keys=[])
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def database_path_from_env(default: str = "data/intake.db") -> str:
    import os

    return os.environ.get("INTAKE_DB_PATH", default)
