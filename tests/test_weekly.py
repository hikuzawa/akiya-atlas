"""日次パイプラインの週次まとめのテスト。実行レポートのファイルだけを読む。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sitemill.settings import Workspace

from akiya_atlas import weekly

SITE_TOML = """
[site]
id = "akiya-atlas"
name = "空き家アトラス"
base_url = "https://akiya-atlas.com"
service = "akiya_atlas.service:service"
[operator]
name = "準備中"
contact = "準備中"
"""


def _run(ws: Workspace, name: str, started: datetime, minutes: float, **stages: object) -> None:
    finished = started + timedelta(minutes=minutes)
    payload = {
        "command": name.split("-")[-1],
        "service": "akiya-atlas",
        "started_at": started.isoformat().replace("+00:00", "Z"),
        "finished_at": finished.isoformat().replace("+00:00", "Z"),
        "stages": stages.pop("stages", {}),
        "llm": stages.pop("llm", {"calls": 0, "input_tokens": 0, "output_tokens": 0}),
        "errors": stages.pop("errors", []),
        "ci": stages.pop("ci", False),
    }
    (ws.runs_dir / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    (tmp_path / "site.toml").write_text(SITE_TOML, encoding="utf-8")
    w = Workspace.open(tmp_path)
    w.ensure_dirs()
    return w


def test_ci_and_local_runs_are_counted_apart(ws: Workspace) -> None:
    """手元のバックフィルを日次に混ぜない（混ぜると所要時間と費用を読み違える）。"""
    now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
    _run(ws, "20260911-030000-crawl", now, 5.0, stages={"crawl": {"fetched": 270}}, ci=True)
    _run(ws, "20260911-100000-crawl", now, 90.0, stages={"crawl": {"fetched": 9000}})
    ci_rows = weekly.collect(ws, days=7, now=now)  # 既定は ci
    assert [r.fetched for r in ci_rows] == [270]
    local_rows = weekly.collect(ws, days=7, now=now, source="local")
    assert [r.fetched for r in local_rows] == [9000]
    all_rows = weekly.collect(ws, days=7, now=now, source="all")
    assert [r.fetched for r in all_rows] == [9270]


def test_collect_sums_each_day_and_skips_old_runs(ws: Workspace) -> None:
    now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
    _run(
        ws,
        "20260910-030000-crawl",
        now - timedelta(days=1),
        3.5,
        stages={"crawl": {"fetched": 270, "changed": 12}},
    )
    _run(
        ws,
        "20260910-031000-extract",
        now - timedelta(days=1),
        2.0,
        stages={"extract": {"pages": 12, "items": 140}, "ingest": {"created": 9, "updated": 131}},
        llm={"calls": 12, "input_tokens": 60_000, "output_tokens": 30_000},
    )
    _run(
        ws,
        "20260911-030000-crawl",
        now,
        4.0,
        stages={"crawl": {"fetched": 271, "changed": 4}},
    )
    _run(ws, "20260801-030000-crawl", now - timedelta(days=41), 9.0, stages={"crawl": {}})

    rows = weekly.collect(ws, days=7, now=now, source="all")
    assert [r.date for r in rows] == ["2026-09-10", "2026-09-11"]  # 41 日前は入らない
    first = rows[0]
    assert first.fetched == 270 and first.changed == 12 and first.items == 140
    assert first.created == 9 and first.updated == 131 and first.llm_calls == 12
    assert first.seconds == pytest.approx(5.5 * 60)
    assert first.cost == pytest.approx(60_000 / 1e6 * 1.0 + 30_000 / 1e6 * 5.0)


def test_report_and_month_estimate(ws: Workspace) -> None:
    now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
    _run(
        ws,
        "20260911-030000-crawl",
        now,
        10.0,
        stages={"crawl": {"fetched": 270, "changed": 5}, "build": {"pages": 6070}},
        llm={"calls": 5, "input_tokens": 100_000, "output_tokens": 50_000},
    )
    lines = weekly.report(ws, days=7, now=now, source="all")
    text = "\n".join(lines)
    assert "すべての実行の直近 7 日" in text and "2026-09-11" in text
    ci_text = "\n".join(weekly.report(ws, days=7, now=now, source="ci"))
    assert "日次パイプラインの直近 7 日" in ci_text  # 見出しで対象が分かる
    assert "取り下げ依頼で非表示にしているページ: 0 件" in text
    est = weekly.month_estimate(weekly.collect(ws, days=7, now=now, source="all"))
    assert est["minutes"] == pytest.approx(300.0)  # 10 分/日 × 30
    assert est["cost"] == pytest.approx((100_000 / 1e6 + 50_000 / 1e6 * 5) * 30)


def test_snapshot_is_written(ws: Workspace) -> None:
    _run(ws, "20260911-030000-crawl", datetime.now(UTC), 1.0, stages={"crawl": {"fetched": 3}})
    path = weekly.write_snapshot(ws, days=7, source="all")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["days"] == 7 and data["per_day"] and "month_estimate" in data
