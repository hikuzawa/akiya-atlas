"""運営主体の確かめ直し（ADR 0018）のテスト。

ネットワークは respx でモックするか、確かめ方を差し替える。
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx
import yaml
from sitemill.fetch.client import PoliteClient
from sitemill.settings import Workspace

from akiya_atlas import expand, recheck, weekly

REPO = Path(__file__).resolve().parents[1]
HOST = "www.city.kakuu.nagano.jp"


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    shutil.copy(REPO / "site.toml", tmp_path / "site.toml")
    for d in ("data/sources", "data/state", "data/records", "data/runs", "data/reference"):
        (tmp_path / d).mkdir(parents=True)
    return Workspace.open(tmp_path)


def _client() -> PoliteClient:
    return PoliteClient("sitemill-test/0", default_delay=0, jitter=0, sleep=lambda _s: None)


def _row(code: str, name: str, checked: str | None, **extra: object) -> dict:
    host = extra.pop("host", HOST)
    return {
        "code": code,
        "name": name,
        "name_kana": "",
        "prefecture": "長野県",
        "prefecture_slug": "nagano",
        "official_url": host,
        "bank_url": f"https://{host}/akiya/{code}.html",
        "page_class": "listing_index",
        "confidence": 0.9,
        "operator_kind": "municipality",
        "evidence_quote": f"公式ドメイン {host}",
        "evidence_url": f"https://{host}/",
        "evidence_checked_on": checked,
        "cross_linked": False,
        "policy": "crawl",
        "reason": "",
        "proposed_action": "承認",
        "external_links": [],
        **extra,
    }


def _findings(ws: Workspace) -> dict[str, dict]:
    path = ws.runs_dir / "discover-nagano-findings.json"
    return {r["code"]: r for r in json.loads(path.read_text(encoding="utf-8"))["findings"]}


def _yaml_dates(ws: Workspace) -> dict[str, str]:
    data = yaml.safe_load((ws.sources_dir / "nagano-auto.yaml").read_text(encoding="utf-8"))
    return {s["id"]: str(s["operator_evidence"]["checked_on"]) for s in data["sources"]}


def test_the_oldest_are_picked_after_last_nights_failures() -> None:
    """再試行が先、そのあと確認日の古い順。運営主体が決まっていない行は見ない。"""
    rows = [
        _row("200001", "甲市", "2026-09-15"),
        _row("200002", "乙町", "2026-09-10"),
        _row("200003", "丙村", "2026-09-11"),
        _row("200004", "丁町", "2026-09-27", recheck_failures=1),
        _row("200005", "戊村", "2026-09-01", policy="pending"),
        # 確かめられなかったばかりの行は待たせる。古い日付のまま毎晩の枠を取り続けないように
        _row("200006", "己町", "2026-09-01", recheck_last_tried="2026-09-25"),
    ]
    picked = recheck.pick({"nagano": rows}, limit=2, today=date(2026, 9, 28))
    assert [rows[i]["name"] for _, i in picked] == ["丁町", "乙町", "丙村"]
    later = recheck.pick({"nagano": rows}, limit=2, today=date(2026, 10, 2))
    assert [rows[i]["name"] for _, i in later] == ["丁町", "己町", "乙町"]


def test_each_result_is_written_where_it_belongs(ws: Workspace) -> None:
    """成り立てば確認日を今日に（findings と YAML の両方）。成り立たなければ日付はそのままで晩数を
    残す。robots.txt で取れない・通信できないものは、日付も失敗の数も動かさず、待たせる。
    1 件ずつ記録に残す。"""
    expand._write_rows(
        ws,
        "nagano",
        "長野県",
        [
            _row("200001", "甲市", "2026-09-10"),
            _row("200002", "乙町", "2026-09-10"),
            _row("200003", "丙村", "2026-09-10"),
            _row("200004", "丁町", "2026-09-10"),
        ],
    )
    results = {
        "甲市": ("ok", ""),
        "乙町": ("fail", "空き家バンクのページを開けない（HTTP 404）"),
        "丙村": ("skip", "robots.txt で公式サイトを取得できない"),
        "丁町": ("unreachable", "公式サイトに通信できない（ConnectTimeout）"),
    }
    with _client() as c:
        report = recheck.recheck(
            ws,
            client=c,
            today=date(2026, 9, 28),
            checker=lambda slug, row, *a: results[row["name"]],
        )
    counts = (report.checked, report.ok, report.failed, report.skipped, report.unreachable)
    assert counts == (4, 1, 1, 1, 1)
    rows = _findings(ws)
    assert rows["200001"]["evidence_checked_on"] == "2026-09-28"
    assert rows["200002"]["evidence_checked_on"] == "2026-09-10"
    assert rows["200002"]["recheck_failures"] == 1
    assert rows["200002"]["recheck_failed_since"] == "2026-09-28"
    for code in ("200003", "200004"):
        assert rows[code]["evidence_checked_on"] == "2026-09-10"
        assert "recheck_failures" not in rows[code]
        assert rows[code]["recheck_last_tried"] == "2026-09-28"
    assert "通信できない" in rows["200004"]["recheck_waiting_reason"]
    assert _yaml_dates(ws)["nagano-200001"] == "2026-09-28"
    log = (ws.runs_dir / "operator-rechecks.jsonl").read_text(encoding="utf-8").splitlines()
    results_logged = sorted(json.loads(line)["result"] for line in log)
    assert results_logged == ["fail", "ok", "skip", "unreachable"]

    text = "\n".join(weekly.recheck_lines(ws, [json.loads(line) for line in log]))
    assert "robots.txt で見送り 1・通信できない 1" in text
    assert "長野県丁町（2026-09-28 から）: 公式サイトに通信できない" in text


def test_three_failed_nights_reselect_and_the_weekly_shows_old_and_new(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 晩続けて成り立たなければ選び直す。公式サイトの URL が変わったら、週次に旧新が並ぶ。

    公式サイトの移転は自治体では時々起きる。行き先が自動で変わったことを見えるようにする。
    補助制度のページは新しいサイトで探し直す（前のページは移転前のサイトにある）。
    """
    old = _row("200001", "甲市", "2026-09-10", subsidy_urls=[f"https://{HOST}/hojo.html"])
    expand._write_rows(ws, "nagano", "長野県", [old])
    moved = _row("200001", "甲市", None, host="www.city.kakuu.lg.jp")
    moved_finding = expand._row_to_finding(moved)
    moved_finding.evidence_checked_on = "2026-09-30"
    monkeypatch.setattr(expand, "assess_municipality", lambda *a, **k: moved_finding)
    monkeypatch.setattr(
        expand,
        "find_subsidy_pages",
        lambda *a, **k: [("https://www.city.kakuu.lg.jp/hojo/", "補助")],
    )
    reason = "公式サイトを開けない、または市町村名が出ない"
    with _client() as c:
        for day in (28, 29, 30):
            report = recheck.recheck(
                ws, client=c, today=date(2026, 9, day), checker=lambda *a: ("fail", reason)
            )
    assert report.reselected == 1
    row = _findings(ws)["200001"]
    assert row["official_url"] == "www.city.kakuu.lg.jp"
    assert "recheck_failures" not in row
    assert row["subsidy_urls"] == ["https://www.city.kakuu.lg.jp/hojo/"]

    entries = weekly.recheck_entries(ws, days=7, now=datetime.now(UTC))
    text = "\n".join(weekly.recheck_reselection_lines(entries))
    assert "## 今週 確かめ直しで選び直した自治体（1 件）" in text
    assert f"| 長野県甲市 | {HOST} | www.city.kakuu.lg.jp | 公式サイトが変わった。" in text
    assert "3 晩続けて確かめられなかった" in text


def test_the_weekly_names_what_does_not_hold(ws: Workspace) -> None:
    rows = [
        _row("200001", "甲市", "2026-09-10"),
        _row(
            "200002",
            "乙町",
            "2026-09-11",
            recheck_failures=2,
            recheck_failed_since="2026-09-27",
            recheck_reason="県の市町村一覧が、記録した公式サイトにリンクしていない",
        ),
    ]
    expand._write_rows(ws, "nagano", "長野県", rows)
    entries = [
        {"result": "ok", "at": "2026-09-28T03:30:00+09:00"},
        {"result": "fail", "at": "2026-09-28T03:30:00+09:00"},
        {"result": "skip", "at": "2026-09-28T03:30:00+09:00"},
    ]
    text = "\n".join(weekly.recheck_lines(ws, entries))
    counts = "**3 件**（成り立った 1・成り立たなかった 1・robots.txt で見送り 1・通信できない 0）"
    assert counts in text
    assert "いちばん古い確認日 2026-09-10" in text
    assert "長野県乙町（2026-09-27 から、2 晩目）: 県の市町村一覧が" in text


def test_subsidy_pages_survive_a_reselection() -> None:
    """選び直すと補助制度のページが空になり、補助制度が巡回から黙って外れていた。

    発見の評価は補助制度のページを探さない（2026-09-27 に発見。実害はまだ無かった）。
    """
    old = {"official_url": HOST, "subsidy_urls": [f"https://{HOST}/hojo.html"]}
    same = {"official_url": HOST, "subsidy_urls": []}
    assert expand.carry_subsidy_pages(old, same, None)["subsidy_urls"] == old["subsidy_urls"]
    found = {"official_url": HOST, "subsidy_urls": [f"https://{HOST}/new.html"]}
    assert expand.carry_subsidy_pages(old, found, None) == found  # 見つかったものを優先


BANK = f"https://{HOST}/akiya/200001.html"
TOP = "<html><head><title>架空市公式ホームページ</title></head><body>架空市役所</body></html>"


def _mock_site(robots: str | None = None, bank_status: int = 200) -> None:
    respx.get(f"https://{HOST}/robots.txt").mock(
        return_value=httpx.Response(200, text=robots) if robots else httpx.Response(404)
    )
    respx.get(f"https://{HOST}/").mock(
        return_value=httpx.Response(200, text=TOP, headers={"content-type": "text/html"})
    )
    respx.get(BANK).mock(
        return_value=httpx.Response(
            bank_status,
            text="<html><body>空き家バンク</body></html>",
            headers={"content-type": "text/html"},
        )
    )


@respx.mock
def test_check_holds_when_the_site_still_names_itself(ws: Workspace) -> None:
    _mock_site()
    row = _row("200001", "架空市", "2026-09-10")
    with _client() as c:
        assert recheck.check(
            "nagano", row, c, expand.OfficialOverrides.load(ws, "nagano"), None
        ) == ("ok", "")


@respx.mock
def test_check_fails_when_the_bank_page_is_gone(ws: Workspace) -> None:
    _mock_site(bank_status=404)
    row = _row("200001", "架空市", "2026-09-10")
    with _client() as c:
        result, reason = recheck.check(
            "nagano", row, c, expand.OfficialOverrides.load(ws, "nagano"), None
        )
    assert result == "fail" and "空き家バンクのページを開けない（HTTP 404）" in reason


@respx.mock
def test_check_is_skipped_when_robots_forbids_it(ws: Workspace) -> None:
    """robots.txt で取れないなら確かめていない。失敗にも数えない。"""
    _mock_site(robots="User-agent: *\nDisallow: /\n")
    row = _row("200001", "架空市", "2026-09-10")
    with _client() as c:
        result, _ = recheck.check(
            "nagano", row, c, expand.OfficialOverrides.load(ws, "nagano"), None
        )
    assert result == "skip"


@respx.mock
def test_a_site_that_drops_the_connection_is_not_a_failure(ws: Workspace) -> None:
    """時間切れ・接続拒否・403 は、ページの有無について何も言っていない。失敗に数えると
    3 晩で選び直しが走り、毎回同じ結果を繰り返す（japan-open-today の国内サイトで実例）。"""
    respx.get(f"https://{HOST}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"http://{HOST}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"https://{HOST}/").mock(side_effect=httpx.ConnectTimeout("timed out"))
    respx.get(f"http://{HOST}/").mock(return_value=httpx.Response(403))
    row = _row("200001", "架空市", "2026-09-10")
    with _client() as c:
        result, reason = recheck.check(
            "nagano", row, c, expand.OfficialOverrides.load(ws, "nagano"), None
        )
    assert result == "unreachable" and "通信できない" in reason


@respx.mock
def test_a_top_page_without_the_name_fails_and_a_move_is_named(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """届いたのに名乗りが無ければ成り立たない。解決し直して別のホストになれば、移転として出す。"""
    respx.get(f"https://{HOST}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"https://{HOST}/").mock(
        return_value=httpx.Response(
            200,
            text="<html><body>このサイトは移転しました</body></html>",
            headers={"content-type": "text/html"},
        )
    )
    row = _row("200001", "架空市", "2026-09-10")
    overrides = expand.OfficialOverrides.load(ws, "nagano")
    monkeypatch.setattr(expand, "resolve_official", lambda *a, **k: None)
    with _client() as c:
        assert recheck.check("nagano", row, c, overrides, None) == (
            "fail",
            "公式サイトのトップに市町村名が出ない",
        )
    new_host = expand.classify_host("www.city.kakuu.lg.jp", "nagano")
    moved = expand.OfficialResolution(
        "https://www.city.kakuu.lg.jp/", new_host, "引用", "https://x/"
    )
    monkeypatch.setattr(expand, "resolve_official", lambda *a, **k: moved)
    with _client() as c:
        result, reason = recheck.check("nagano", row, c, overrides, None)
    assert result == "fail" and "公式サイトが www.city.kakuu.lg.jp になっている" in reason


def test_an_unchanged_review_keeps_its_time(ws: Workspace) -> None:
    """候補が同じなら review の作った時刻を動かさない（毎晩 1 行の差分になっていた）。"""
    rows = [_row("200001", "甲市", "2026-09-10")]
    expand._write_rows(ws, "nagano", "長野県", rows)
    path = ws.root / "data" / "review" / "nagano.yaml"
    first = yaml.safe_load(path.read_text(encoding="utf-8"))["created_at"]
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["created_at"] = "2026-09-01T00:00:00+00:00"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    expand._write_rows(ws, "nagano", "長野県", rows)
    assert (
        yaml.safe_load(path.read_text(encoding="utf-8"))["created_at"]
        == "2026-09-01T00:00:00+00:00"
    )
    assert first
