#!/usr/bin/env python3
"""离线演示流程（仅标准库）：上新 → 失败重试 → 确认后不再提示。

可作为脚本运行，也被 demo/run_compose_demo.sh 与 run_local_demo.sh 调用。

    python3 -I demo/flow.py \
        --api-base http://127.0.0.1:8000 \
        --site-base http://127.0.0.1:8080 \
        --index-url http://notice-site:8080/index.html
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request


def call(method: str, url: str, payload: dict | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ids(items: list[dict]) -> list[str]:
    return [item["notice_id"] for item in items]


def show(label: str, response: dict) -> None:
    print(f"\n== {label} ==")
    print(f"status        : {response.get('status')}")
    print(f"known_count   : {response.get('known_count')}")
    print(f"new           : {sorted(ids(response.get('new_notices', []))) or '（无）'}")
    print(f"pending({len(response.get('pending_notices', []))})  : "
          f"{sorted(ids(response.get('pending_notices', []))) or '（空）'}")
    if response.get("errors"):
        print(f"errors        : {response['errors'][0]}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--site-base", default="http://127.0.0.1:8080")
    parser.add_argument("--index-url", required=True)
    parser.add_argument("--reset-site", action="store_true")
    args = parser.parse_args()

    api = args.api_base.rstrip("/")
    control = args.site_base.rstrip("/") + "/control"

    if args.reset_site:
        call("POST", control + "/reset", {})

    def scan() -> dict:
        return call("POST", api + "/scan", {"index_url": args.index_url})

    def batch(name: str) -> None:
        call("POST", control + "/batch", {"batch": name})

    def failure(mode: str, notice_ids: list[str] | None = None) -> None:
        call(
            "POST",
            control + "/failure",
            {"mode": mode, "notice_ids": notice_ids or []},
        )

    # 1) 首次完整扫描：建立已收录编号集，不产生待交接项
    first = scan()
    show("1. 首次完整扫描（批次 v1）", first)
    require(first["status"] == "baseline_established", "首次扫描应为基线建立")
    require(first["pending_notices"] == [], "基线扫描不应产生待交接项")
    require(first["known_count"] == 10, "基线应收录 10 条")

    # 2) 运营方上新：完整扫描后新编号进入待交接列表
    batch("v2")
    second = scan()
    show("2. 切换批次 v2 后扫描（上新）", second)
    require(second["status"] == "updated", "上新扫描应为 updated")
    require(
        sorted(ids(second["new_notices"]))
        == ["N-2026-011", "N-2026-012", "N-2026-013", "N-2026-014"],
        "应发现 v2 的 4 条新公告",
    )
    require(len(second["pending_notices"]) == 4, "待交接列表应有 4 条")

    # 3) 详情故障：无法核定，编号集与待交接列表保持原状
    batch("v3")
    failure("http500", ["N-2026-015"])
    failed = scan()
    show("3. 批次 v3 + N-2026-015 详情故障（无法核定）", failed)
    require(failed["status"] == "undetermined", "详情故障应返回无法核定")
    require(failed["known_count"] == second["known_count"], "失败不得改变编号集")
    require(
        failed["pending_notices"] == second["pending_notices"],
        "失败不得改变待交接列表",
    )
    require(failed["errors"], "应返回失败原因")

    # 4) 清除故障后重试：上一次失败不留下任何半成品
    failure("none")
    retried = scan()
    show("4. 清除故障后重试（批次 v3）", retried)
    require(retried["status"] == "updated", "重试应成功")
    require(
        sorted(ids(retried["new_notices"]))
        == ["N-2026-015", "N-2026-016", "N-2026-017"],
        "应一次性收齐 v3 的 3 条新公告",
    )
    require(retried["known_count"] == 17, "编号集应更新到 17 条")

    # 5) 确认：从待交接列表移除，之后扫描不再提示
    for nid in ("N-2026-011", "N-2026-015"):
        confirmed = call(
            "POST", api + "/confirm", {"notice_id": nid, "index_url": args.index_url}
        )
        print(f"\n确认 {nid}: {confirmed['status']} - {confirmed['message']}")
        require(confirmed["status"] == "confirmed", f"{nid} 应确认成功")

    final = scan()
    show("5. 确认后再次扫描（已确认公告不再入列）", final)
    require(final["new_notices"] == [], "已确认后不应再有新公告")
    require(
        "N-2026-011" not in ids(final["pending_notices"])
        and "N-2026-015" not in ids(final["pending_notices"]),
        "已确认编号不应重新入列",
    )
    require(len(final["pending_notices"]) == 5, "剩余待交接应为 5 条")

    print("\n演示完成：基线 → 上新 → 失败保持原状 → 重试成功 → 确认不再提示。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"\n演示校验失败: {exc}", file=sys.stderr)
        raise SystemExit(1)
