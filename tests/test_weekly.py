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
    assert "取り下げにより 0 ページを非表示（依頼 0 件）" in text
    est = weekly.month_estimate(weekly.collect(ws, days=7, now=now, source="all"))
    assert est["minutes"] == pytest.approx(300.0)  # 10 分/日 × 30
    assert est["cost"] == pytest.approx((100_000 / 1e6 + 50_000 / 1e6 * 5) * 30)


def test_takedown_lines_count_pages_and_never_name_the_page() -> None:
    """週次は公開リポジトリの Issue に出る。何ページ隠れたかは出し、どのページかは出さない。"""
    scope = weekly.TakedownScope(pages=192, per_request=[(1, 191), (4, 1)], unscoped=2)
    text = "\n".join(weekly.takedown_lines(scope))
    assert "取り下げにより 192 ページを非表示（依頼 2 件）" in text
    assert "#1 191 ページ" in text and "#4 1 ページ" in text  # 1 件で大量に隠していれば目立つ
    assert "自動では適用していない依頼: 2 件" in text
    assert "/nagano/" not in text and "http" not in text


def test_reselections_are_listed_for_the_week(ws: Workspace) -> None:
    """heal が掲載ページを選び直したら、週次に自治体・旧 URL・新 URL・理由が出る。"""
    now = datetime(2026, 9, 14, 12, tzinfo=UTC)
    rows = [
        {
            "at": (now - timedelta(days=2)).isoformat(),
            "source_id": "niigata-152021",
            "name": "新潟県長岡市",
            "old_url": "https://example.lg.jp/old",
            "new_url": "https://example.lg.jp/new",
            "reason": "詳細ページを辿るようにした（7 本）",
        },
        {
            "at": (now - timedelta(days=30)).isoformat(),  # 期間の外
            "source_id": "niigata-152030",
            "name": "新潟県三条市",
            "reason": "古い記録",
        },
    ]
    path = ws.runs_dir / "heal-reselections.jsonl"
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + chr(10) for r in rows), encoding="utf-8"
    )

    picked = weekly.reselections(ws, days=7, now=now)
    assert [r["source_id"] for r in picked] == ["niigata-152021"]

    text = chr(10).join(weekly.report(ws, days=7, now=now))
    assert "## 今週 heal が選び直した自治体（1 件）" in text
    assert "新潟県長岡市" in text and "https://example.lg.jp/new" in text
    assert "詳細ページを辿るようにした" in text
    assert "三条市" not in text


def test_report_says_when_it_cannot_count_actions_minutes(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """リポジトリが分からないときは 0 分と書かず、集計していないことを書く。"""
    monkeypatch.delenv("GH_REPO", raising=False)  # 環境に設定があっても外部に出ない
    text = chr(10).join(weekly.report(ws, days=7, repo=None))
    assert "## GitHub Actions の実行時間" in text
    assert "集計していません" in text
    assert "0 分" not in text.split("## GitHub Actions")[1]


def test_snapshot_is_written(ws: Workspace) -> None:
    _run(ws, "20260911-030000-crawl", datetime.now(UTC), 1.0, stages={"crawl": {"fetched": 3}})
    path = weekly.write_snapshot(ws, days=7, source="all")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["days"] == 7 and data["per_day"] and "month_estimate" in data


def test_repeated_failures_are_grouped_by_kind(ws: Workspace) -> None:
    """同じ失敗の繰り返しは「N 種類・延べ M 件」にまとめる。

    睦沢町の 404 が毎回並び、1 週間の失敗 98 件の先頭を占めて他が埋もれていた（2026-09-17）。
    """
    now = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    same = "https://www.town.mutsuzawa.chiba.jp/akiya/page/2: HTTP 404"
    for day in range(3):
        _run(
            ws,
            f"2026091{4 + day}-030000-crawl",
            now - timedelta(days=day),
            1.0,
            errors=[same] + (["https://example.lg.jp/x.html: HTTP 403"] if day == 0 else []),
        )
    text = "\n".join(weekly.report(ws, days=7, now=now, source="all"))
    assert "**2 種類・延べ 4 件**" in text
    assert f"  - 3 回: crawl: {same}" in text
    assert text.count("mutsuzawa") == 1  # 繰り返しを 1 行にまとめる


def test_llm_cost_has_its_own_section_and_the_old_free_tier_line_is_gone(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM の費用は Actions の節と分ける。public なので無料枠の割合は出さない。"""
    now = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    _run(
        ws,
        "20260917-030000-extract",
        now,
        10.0,
        llm={"calls": 5, "input_tokens": 100_000, "output_tokens": 50_000},
    )
    monkeypatch.setattr(
        weekly,
        "actions_minutes",
        lambda repo, **_: {
            "minutes": 66.0,
            "runs": 22,
            "by_workflow": {"pipeline": 66.0},
            "truncated": False,
            "measured": False,
            "covered_days": 5.0,
            "partial": True,
            "monthly": 66.0 / 5.0 * 30,
        },
    )
    text = "\n".join(weekly.report(ws, days=7, now=now, source="all", repo="o/r"))
    llm, actions = text.split("## LLM の費用（Anthropic）")[1].split("## GitHub Actions の実行時間")
    assert "$0.35" in llm and "1 か月に直すと **約 $10.50**" in llm
    assert "$" not in actions  # Actions の節に費用を並べない
    assert "無料枠" not in text and "private" not in text
    assert "1 か月に直すと **約 396 分**" in actions  # 5 日分で割る（7 で割ると 283 分）
    assert "旧リポジトリ（akiya-atlas-archive）の実行は含まない" in actions


def test_no_monthly_estimate_until_a_few_days_have_passed(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """作り直した直後は日数が足りない。1 日分を 30 倍した数字は出さない（1,987 分と出た）。"""
    monkeypatch.setattr(
        weekly,
        "actions_minutes",
        lambda repo, **_: {
            "minutes": 67.0,
            "runs": 24,
            "by_workflow": {},
            "truncated": False,
            "measured": False,
            "covered_days": 1.0,
            "partial": True,
            "monthly": 67.0 * 30,
        },
    )
    now = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    actions = (
        chr(10)
        .join(weekly.report(ws, days=7, now=now, repo="o/r"))
        .split("## GitHub Actions の実行時間")[1]
    )
    assert "1 か月の見込みは出さない" in actions and "2010" not in actions
    assert "2026-09-16（JST）" in actions


def test_actions_minutes_divides_by_the_days_the_repository_has_existed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作り直す前の実行は API から見えないので、見えている日数で割る。"""
    calls = []

    def fake(args: list[str]) -> object:
        calls.append(args)
        if any(a.endswith("/actions/runs") for a in args):
            return [{"id": 1, "name": "pipeline"}, {"id": 2, "name": "search"}]
        return {"billable": 0, "ran": 30 * 60_000}  # 30 分ずつ

    monkeypatch.setattr(weekly, "_gh_json", fake)
    now = weekly.REPOSITORY_SINCE + timedelta(days=2)
    got = weekly.actions_minutes("o/r", days=7, now=now)
    assert got["minutes"] == pytest.approx(60.0)
    assert got["partial"] and got["covered_days"] == pytest.approx(2.0)
    assert got["monthly"] == pytest.approx(60.0 / 2.0 * 30)
    later = weekly.actions_minutes("o/r", days=7, now=weekly.REPOSITORY_SINCE + timedelta(days=30))
    assert not later["partial"] and later["monthly"] == pytest.approx(60.0 / 7 * 30)


def _crawl_state(ws: Workspace, urls: dict[str, dict[str, object]]) -> None:
    (ws.state_dir / "crawl.json").write_text(
        json.dumps({"urls": urls}, ensure_ascii=False), encoding="utf-8"
    )


def test_a_one_night_robots_failure_is_only_a_number(ws: Workspace) -> None:
    """1 晩だけ取れなかったホストは数だけ。名前を並べても打ち手が無い。"""
    now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    _run(
        ws,
        "20260923-210000-crawl",
        now - timedelta(days=1),
        5.0,
        ci=True,
        errors=[
            "https://izumo-akiyataisaku.jp/bank/: robots.txt を取得できないため今回は巡回しない",
            "http://www.chichibuakiyabank.com/: robots.txt を取得できないため今回は巡回しない",
        ],
    )
    _crawl_state(
        ws,
        {
            "https://izumo-akiyataisaku.jp/bank/": {
                "error": "robots.txt を取得できないため今回は巡回しない",
                "fetched_at": (now - timedelta(days=1)).isoformat(),
            }
        },
    )
    hosts, times, stalled = weekly.robots_failures(ws, days=7, now=now)
    assert hosts == ["izumo-akiyataisaku.jp", "www.chichibuakiyabank.com"]
    assert times == 2 and stalled == []
    line = "\n".join(weekly.robots_lines(hosts, times, stalled))
    assert "**2**（延べ 2 回）" in line and "取得できていない" not in line


def test_a_host_stuck_for_three_days_is_named(ws: Workspace) -> None:
    """続いた分はその自治体の更新が止まっている。週次を読んですぐ分かるよう名前を出す。"""
    now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    _run(
        ws,
        "20260923-210000-crawl",
        now - timedelta(days=1),
        5.0,
        ci=True,
        errors=["https://classo.jp/: robots.txt を取得できないため今回は巡回しない"],
    )
    _crawl_state(
        ws,
        {
            "https://classo.jp/": {
                "error": "robots.txt を取得できないため今回は巡回しない",
                "fetched_at": (now - timedelta(days=4)).isoformat(),
            },
            "https://akiya.vill.yahiko.niigata.jp/": {
                "error": "robots.txt を取得できないため今回は巡回しない",
                "fetched_at": None,
            },
        },
    )
    _, _, stalled = weekly.robots_failures(ws, days=7, now=now)
    line = "\n".join(weekly.robots_lines(["classo.jp"], 1, stalled))
    assert "classo.jp（最終取得から 4 日）" in line
    assert "akiya.vill.yahiko.niigata.jp（一度も取得できていない）" in line
    assert "更新が止まっている" in line


def test_the_latest_copy_is_not_counted_twice(ws: Workspace) -> None:
    """`latest-crawl.json` は直近の実行の写し。数えると延べ回数が倍になる。"""
    now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    for name in ("20260923-210000-crawl", "latest-crawl"):
        _run(
            ws,
            name,
            now - timedelta(days=1),
            5.0,
            ci=True,
            errors=["https://classo.jp/: robots.txt を取得できないため今回は巡回しない"],
        )
    hosts, times, _ = weekly.robots_failures(ws, days=7, now=now)
    assert hosts == ["classo.jp"] and times == 1


def test_no_robots_failure_says_so(ws: Workspace) -> None:
    now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    hosts, times, stalled = weekly.robots_failures(ws, days=7, now=now)
    assert weekly.robots_lines(hosts, times, stalled) == [
        "- robots.txt を取得できなかったホスト: なし"
    ]
