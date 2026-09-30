"""運営主体の確かめ直し（ADR 0018）のテスト。回し方は sitemill の `recheck`（ADR 0027）。

`sitemill recheck`（`commands.cmd_recheck`）をサービスのフックごと回す。ネットワークは respx で
モックするか、確かめ方（`recheck.check`）を差し替える。成り立たなかった晩数と待ちの状態は
sitemill の状態ファイル（`data/state/recheck.json`）、確認日は findings と YAML にある。
"""

from __future__ import annotations

import inspect
import json
import shutil
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx
import sitemill.recheck as engine
import yaml
from sitemill import commands
from sitemill.fetch.client import PoliteClient
from sitemill.models.run import RunReport
from sitemill.settings import Workspace

from akiya_atlas import expand, recheck, weekly
from akiya_atlas.service import service

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


def _state(ws: Workspace) -> dict[str, dict]:
    return json.loads((ws.state_dir / "recheck.json").read_text(encoding="utf-8"))


def _write_state(ws: Workspace, state: dict[str, dict]) -> None:
    (ws.state_dir / "recheck.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )


def _night(
    ws: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    day: date,
    *,
    checker: Callable[..., tuple[str, str]] | None = None,
    limit: int | None = None,
) -> RunReport:
    """`sitemill recheck` を day の晩として回す。巡回の間隔は待たない。"""
    monkeypatch.setattr(engine, "jst_today", lambda: day)
    if checker is not None:
        monkeypatch.setattr(recheck, "check", checker)
    rt = commands.Runtime.open(ws.root, service=service)
    rt.client = _client  # type: ignore[method-assign]
    return commands.cmd_recheck(rt, limit=limit)


def test_the_engine_defaults_are_the_ones_this_service_explains() -> None:
    """3 晩で選び直す・7 日待つは `sitemill recheck` の既定。週次や理由の文はこの値で書いてある。"""
    params = inspect.signature(engine.rotate).parameters
    assert params["retry_nights"].default == recheck.RETRY_NIGHTS
    assert params["wait_days"].default == recheck.WAIT_DAYS
    assert service.recheck_per_night == recheck.PER_NIGHT


def test_the_oldest_are_picked_after_last_nights_failures(ws: Workspace) -> None:
    """再試行が先、そのあと確認日の古い順。運営主体が決まっていない行は見ない。"""
    expand._write_rows(
        ws,
        "nagano",
        "長野県",
        [
            _row("200001", "甲市", "2026-09-15"),
            _row("200002", "乙町", "2026-09-10"),
            _row("200003", "丙村", "2026-09-11"),
            _row("200004", "丁町", "2026-09-27"),
            _row("200005", "戊村", "2026-09-01", policy="pending"),
            _row("200006", "己町", "2026-09-01"),
            _row("200007", "庚村", "2026-09-05"),
        ],
    )
    _write_state(
        ws,
        {
            "nagano-200004": {"failures": 1, "failed_since": "2026-09-27", "reason": "x"},
            # 確かめられなかったばかりの行は待たせる。古い日付のまま毎晩の枠を取り続けないように
            "nagano-200006": {
                "waiting_since": "2026-09-25",
                "waiting_reason": "robots.txt で公式サイトを取得できない",
                "last_tried": "2026-09-25",
            },
            # 確認日は findings と状態ファイルの新しいほう（sitemill recheck で確かめた日）
            "nagano-200007": {"checked_on": "2026-09-26"},
        },
    )
    picked = recheck.tonight(ws, limit=2, today=date(2026, 9, 28))
    assert [t.label for t in picked] == ["長野県丁町", "長野県乙町", "長野県丙村"]
    later = recheck.tonight(ws, limit=2, today=date(2026, 10, 2))
    assert [t.label for t in later] == ["長野県丁町", "長野県己町", "長野県乙町"]


def test_each_result_is_written_where_it_belongs(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成り立てば確認日を今日に（findings と YAML の両方。`recheck_done` が書き戻す）。
    成り立たなければ日付はそのままで晩数を残す。robots.txt で取れない・通信できないものは、
    日付も失敗の数も動かさず、待たせる。晩数と待ちは sitemill の状態ファイルに、1 件ずつの結果は
    記録に残る。"""
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
    report = _night(
        ws, monkeypatch, date(2026, 9, 28), checker=lambda slug, row, *a: results[row["name"]]
    )
    counts = report.stages["recheck"]
    assert counts["checked"] == 4
    assert [counts[k] for k in ("ok", "fail", "skip", "unreachable")] == [1, 1, 1, 1]
    rows, state = _findings(ws), _state(ws)
    assert rows["200001"]["evidence_checked_on"] == "2026-09-28"
    assert state["nagano-200001"] == {"checked_on": "2026-09-28"}
    assert rows["200002"]["evidence_checked_on"] == "2026-09-10"
    assert state["nagano-200002"]["failures"] == 1
    assert state["nagano-200002"]["failed_since"] == "2026-09-28"
    for code in ("200003", "200004"):
        assert rows[code]["evidence_checked_on"] == "2026-09-10"
        assert "failures" not in state[f"nagano-{code}"]
        assert state[f"nagano-{code}"]["last_tried"] == "2026-09-28"
    assert "通信できない" in state["nagano-200004"]["waiting_reason"]
    assert not any(k.startswith("recheck_") for row in rows.values() for k in row)
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
    for day in (28, 29, 30):
        report = _night(ws, monkeypatch, date(2026, 9, day), checker=lambda *a: ("fail", reason))
    assert report.stages["recheck"]["reselected"] == 1
    # 実行ログの選び直しの行は、旧新の URL を並べる（recheck_reselect_line）
    assert (
        f"長野県甲市: 選び直した（公式 {HOST} → www.city.kakuu.lg.jp、空き家バンク "
        f"https://{HOST}/akiya/200001.html → https://www.city.kakuu.lg.jp/akiya/200001.html）"
    ) in report.notes
    row = _findings(ws)["200001"]
    assert row["official_url"] == "www.city.kakuu.lg.jp"
    assert row["evidence_checked_on"] == "2026-09-30"
    assert _state(ws)["nagano-200001"] == {"checked_on": "2026-09-30"}
    assert row["subsidy_urls"] == ["https://www.city.kakuu.lg.jp/hojo/"]

    entries = weekly.recheck_entries(ws, days=7, now=datetime.now(UTC))
    text = "\n".join(weekly.recheck_reselection_lines(entries))
    assert "## 今週 確かめ直しで選び直した自治体（1 件）" in text
    assert f"| 長野県甲市 | {HOST} | www.city.kakuu.lg.jp | 公式サイトが変わった。" in text
    assert "3 晩続けて確かめられなかった" in text


def test_a_night_whose_write_back_stopped_is_caught_up_the_next_night(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sitemill は確かめた日を先に状態ファイルへ書く。書き戻しがその晩に止まっても（09-20 の甲市）、
    次の晩の `recheck_done` が findings と YAML に書き戻し、`/data/<県>/` の確認日が進む。"""
    expand._write_rows(
        ws,
        "nagano",
        "長野県",
        [_row("200001", "甲市", "2026-09-10"), _row("200002", "乙町", "2026-09-15")],
    )
    _write_state(ws, {"nagano-200001": {"checked_on": "2026-09-20"}})
    report = _night(ws, monkeypatch, date(2026, 9, 28), checker=lambda *a: ("ok", ""), limit=1)
    assert report.stages["recheck"]["checked"] == 1  # 乙町だけ（甲市の確認日は 09-20）
    rows, dates = _findings(ws), _yaml_dates(ws)
    assert rows["200002"]["evidence_checked_on"] == dates["nagano-200002"] == "2026-09-28"
    assert rows["200001"]["evidence_checked_on"] == dates["nagano-200001"] == "2026-09-20"


def test_the_weekly_names_what_does_not_hold(ws: Workspace) -> None:
    rows = [_row("200001", "甲市", "2026-09-10"), _row("200002", "乙町", "2026-09-11")]
    expand._write_rows(ws, "nagano", "長野県", rows)
    _write_state(
        ws,
        {
            "nagano-200002": {
                "failures": 2,
                "failed_since": "2026-09-27",
                "reason": "県の市町村一覧が、記録した公式サイトにリンクしていない",
                "last_tried": "2026-09-28",
            }
        },
    )
    # 09-28 までの記録は source_id と name、09-30 からは sitemill の key と label
    entries = [
        {"result": "ok", "at": "2026-09-28T03:30:00+09:00", "source_id": "nagano-200001"},
        {"result": "fail", "at": "2026-09-30T03:30:00+09:00", "key": "nagano-200002"},
        {"result": "skip", "at": "2026-09-30T03:30:00+09:00", "key": "nagano-200001"},
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


PREF_LIST = "https://www.pref.nagano.lg.jp/ichiran/index.html"
BARE = "kakuumura.jp"  # 地理型でも lg.jp でもない。県の一覧で公式と決めたホスト


def _mock_bare_site(list_links: list[str]) -> None:
    """県の一覧（list_links へリンク）と、一覧から www つきへ転送される村のサイト。"""
    body = "".join(f'<a href="{u}">架空村</a>' for u in list_links)
    respx.get("https://www.pref.nagano.lg.jp/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(PREF_LIST).mock(
        return_value=httpx.Response(
            200, text=f"<html><body>{body}</body></html>", headers={"content-type": "text/html"}
        )
    )
    respx.get(f"https://www.{BARE}/robots.txt").mock(return_value=httpx.Response(404))
    page = "<html><head><title>架空村公式</title></head><body>架空村役場</body></html>"
    for url in (f"https://www.{BARE}/", f"https://www.{BARE}/akiya/200001.html"):
        respx.get(url).mock(
            return_value=httpx.Response(200, text=page, headers={"content-type": "text/html"})
        )


def _write_list_reference(ws: Workspace, linked: str) -> None:
    (ws.root / "data" / "reference" / "nagano_official_urls.json").write_text(
        json.dumps(
            {
                "source_url": PREF_LIST,
                "municipalities": [{"code": "200001", "name": "架空村", "official_url": linked}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


@respx.mock
def test_a_list_linking_the_address_before_the_redirect_still_holds(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """県の一覧は `http://kakuumura.jp/` へリンクし、記録は転送先の `www.kakuumura.jp`。

    記録したホストだけで比べると、一覧が変わっていないのに毎回「一覧がリンクしていない」になり、
    3 晩で選び直しが走る（2026-09-30 に長野県の平谷村・根羽村。全国で 4 自治体が同じ形）。
    一覧が発見のときと同じ先へリンクしていれば成り立つ。
    """
    _mock_bare_site([f"http://{BARE}/"])
    _write_list_reference(ws, f"http://{BARE}/")
    expand._write_rows(
        ws, "nagano", "長野県", [_row("200001", "架空村", "2026-09-10", host=f"www.{BARE}")]
    )
    report = _night(ws, monkeypatch, date(2026, 9, 30))
    assert report.stages["recheck"]["ok"] == 1, report.notes
    assert _findings(ws)["200001"]["evidence_checked_on"] == "2026-09-30"


@respx.mock
def test_a_list_that_stopped_linking_the_site_still_fails(ws: Workspace) -> None:
    """一覧が別の先へリンクするようになったら、記録したホストでも発見のときの先でもないので成り立たない。"""
    _mock_bare_site(["https://www.vill.kakuu.lg.jp/"])
    _write_list_reference(ws, f"http://{BARE}/")
    row = _row("200001", "架空村", "2026-09-10", host=f"www.{BARE}")
    overrides = expand.OfficialOverrides.load(ws, "nagano")
    with _client() as c:
        hosts = recheck.listed_hosts(ws, "nagano", c)
        assert recheck.check("nagano", row, c, overrides, hosts, BARE) == (
            "fail",
            "県の市町村一覧が、記録した公式サイトにリンクしていない",
        )


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
