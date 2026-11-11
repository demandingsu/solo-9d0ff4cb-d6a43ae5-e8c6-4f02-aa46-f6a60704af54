"""SQLite 状态层测试：基线、增量入列、确认幂等与并发不变量。"""
from __future__ import annotations

import concurrent.futures
from datetime import datetime

from app.models import NoticeKind, ScannedNotice
from app.store import Database, site_key_for

SITE = "http://site:8080"


def notice(nid: str, kind: NoticeKind = NoticeKind.SUSPENSION) -> ScannedNotice:
    return ScannedNotice(
        notice_id=nid,
        url=f"{SITE}/notices/{nid}.html",
        title=nid,
        kind=kind,
        published_at=datetime.fromisoformat("2026-11-11T10:00:00+08:00"),
    )


def make_db(tmp_path) -> Database:
    db = Database(tmp_path / "intake.db")
    db.initialize()
    return db


def test_first_complete_scan_establishes_baseline_without_pending(tmp_path):
    db = make_db(tmp_path)
    result = db.commit_scan(SITE, SITE + "/index.html", [notice("A"), notice("B")])

    assert result.baseline is True
    assert result.new_notices == []
    assert result.known_count == 2
    assert db.list_pending(site_key_for(SITE)) == []


def test_later_scan_enqueues_only_new_ids_and_updates_known_set(tmp_path):
    db = make_db(tmp_path)
    db.commit_scan(SITE, SITE + "/index.html", [notice("A"), notice("B")])
    result = db.commit_scan(
        SITE, SITE + "/index.html", [notice("A"), notice("B"), notice("C")]
    )

    assert result.baseline is False
    assert [n.notice_id for n in result.new_notices] == ["C"]
    assert result.known_count == 3
    pending = db.list_pending(site_key_for(SITE))
    assert [p.notice_id for p in pending] == ["C"]
    assert pending[0].url == f"{SITE}/notices/C.html"


def test_confirm_removes_and_prevents_reenqueue(tmp_path):
    db = make_db(tmp_path)
    db.commit_scan(SITE, SITE + "/index.html", [notice("A")])
    db.commit_scan(SITE, SITE + "/index.html", [notice("A"), notice("B")])

    result = db.confirm("B", site_key_for(SITE))
    assert result.status == "confirmed"
    assert db.list_pending(site_key_for(SITE)) == []

    # 再扫描仍能看到 B，但确认记录阻止它重新入列
    result2 = db.commit_scan(
        SITE, SITE + "/index.html", [notice("A"), notice("B"), notice("C")]
    )
    assert [n.notice_id for n in result2.new_notices] == ["C"]
    assert "B" not in [p.notice_id for p in db.list_pending(site_key_for(SITE))]

    # 重复确认幂等
    again = db.confirm("B", site_key_for(SITE))
    assert again.status == "already_confirmed"


def test_confirm_without_site_before_scan_creates_global_confirmation(tmp_path):
    db = make_db(tmp_path)
    db.commit_scan(SITE, SITE + "/index.html", [notice("A")])

    # 模拟“扫描还在进行、编号尚未入列”时用户先确认
    assert db.confirm("B").status == "recorded"
    result = db.commit_scan(SITE, SITE + "/index.html", [notice("A"), notice("B")])

    assert result.new_notices == []
    assert "B" not in [p.notice_id for p in db.list_pending(site_key_for(SITE))]
    assert result.known_count == 2


def test_concurrent_commits_do_not_duplicate_pending(tmp_path):
    db = make_db(tmp_path)
    db.commit_scan(SITE, SITE + "/index.html", [notice("A")])

    def commit() -> None:
        db.commit_scan(SITE, SITE + "/index.html", [notice("A"), notice("B"), notice("C")])

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: commit(), range(4)))

    pending = db.list_pending(site_key_for(SITE))
    assert sorted(p.notice_id for p in pending) == ["B", "C"]
    assert db.list_known(site_key_for(SITE))[0].notice_id == "A"


def test_concurrent_confirm_and_commit_never_reenqueue(tmp_path):
    """确认与扫描提交并发时，编号最终一定不在待交接列表中。"""
    db = make_db(tmp_path)
    db.commit_scan(SITE, SITE + "/index.html", [notice("A")])

    def commit() -> None:
        db.commit_scan(SITE, SITE + "/index.html", [notice("A"), notice("X")])

    def confirm() -> None:
        db.confirm("X", site_key_for(SITE))

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(commit), pool.submit(confirm)]
        for future in futures:
            future.result()

    assert "X" not in [p.notice_id for p in db.list_pending(site_key_for(SITE))]
