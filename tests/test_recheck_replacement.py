"""確かめ直しを sitemill の rotate に置き換えたとき（2026-09-30）の比較を、縮めて残したもの。

置き換え前の自前の回し方（`0c5ebeb` の `recheck.py`・`weekly.py`）で、下の架空の 36 自治体を
同じ判定で回した結果を固定値（`fixtures/recheck_replacement.json`）にしてある。置き換え後のコードも
同じ値を出すことを確かめてから保存した（置き換え前のコードはもう無いので、作り直すことはできない）。
本番のデータでの比較（1 件ずつ 100 晩・8 並列 30 晩）は ADR 0018 の 09-30 の追記。

- 1 件ずつ順に 20 晩: 毎晩の確かめる自治体と順番、記録（結果と続いた晩数）、選び直しの記録。
  7 晩目と 20 晩目の findings（確認日・公式サイト・方針）、成り立たない・待ちの状態、週次の行
- 8 並列で 7 晩: 同じ値。確かめる順番は並列だと決まらないので、予定の順番と記録の順番で見る
- 選び直し（別のホストになるもの・運営主体が決まらずレビューへ回るもの）と、見送り（robots.txt・
  通信できない）が両方起きる。始めから待っている自治体と、前の晩に成り立たなかった自治体も置く

判定は自治体と日付のハッシュで決め、いつも同じ結果になる自治体（`ALWAYS`）を混ぜる。選び直しの
評価（発見と同じ評価）は、同じ行から同じ結果を作るものに差し替える。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import sitemill.recheck as engine
import yaml
from sitemill.fetch.client import PoliteClient
from sitemill.settings import Workspace

from akiya_atlas import expand, recheck, weekly

REPO = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).parent / "fixtures" / "recheck_replacement.json"
JST = timezone(timedelta(hours=9))
START = date(2026, 10, 1)
PER_NIGHT = 10
SNAPSHOTS = (7, 20)
PREFECTURES = {"gunma": ("群馬県", "10"), "nagano": ("長野県", "20"), "okinawa": ("沖縄県", "47")}
NAMES = "甲乙丙丁戊己庚辛壬癸子丑"
KINDS = "市町村"

# いつも同じ結果になる自治体。確認日を古くして 1 晩目に選ばれるようにしてある
ALWAYS = {
    "gunma-100002": ("fail", "公式サイトのトップに市町村名が出ない"),  # 3 晩で選び直し → 別のホスト
    "nagano-200003": ("fail", "公式サイトを開けない（HTTP 404）"),  # 3 晩で選び直し → レビューへ
    "okinawa-470004": ("skip", "robots.txt で公式サイトを取得できない"),
    "nagano-200007": ("unreachable", "公式サイトに通信できない（ConnectTimeout）"),
}
PENDING_ON_RESELECT = {"200003"}  # 選び直しても運営主体が決まらない
PENDING = {"nagano-200012"}  # 始めから運営主体が決まっていない（確かめ直しの対象外）
INITIAL_STATE: dict[str, dict[str, Any]] = {
    # 北相木村と同じ形。10-06 まで待つ
    "okinawa-470001": {
        "waiting_since": "2026-09-29",
        "waiting_reason": "robots.txt で公式サイトを取得できない",
        "last_tried": "2026-09-29",
    },
    # 前の晩に成り立たなかった。1 晩目は枠の外で確かめる
    "gunma-100006": {
        "failures": 1,
        "failed_since": "2026-09-30",
        "reason": "空き家バンクのページを開けない（HTTP 404）",
    },
}


def dataset() -> dict[str, list[dict]]:
    """県ごとの findings の行。確認日は 09-10〜09-20 に散らし、県をまたいで同じ日を作る。"""
    out: dict[str, list[dict]] = {}
    for n, (slug, (pref, prefix)) in enumerate(PREFECTURES.items()):
        rows = []
        for i in range(12):
            code = f"{prefix}{i + 1:04d}"
            key = f"{slug}-{code}"
            host = f"www.{slug}{code}.example.jp"
            checked = (date(2026, 9, 10) + timedelta(days=(3 * i + 5 * n) % 11)).isoformat()
            if key in ALWAYS:
                checked = "2026-09-01"
            elif key in INITIAL_STATE:
                checked = "2026-09-05"  # 待ちが明けた晩（10-06）に、いちばん古いものとして選ばれる
            rows.append(
                {
                    "code": code,
                    "name": f"{NAMES[i]}{KINDS[i % 3]}",
                    "name_kana": "",
                    "prefecture": pref,
                    "prefecture_slug": slug,
                    "official_url": host,
                    "bank_url": f"https://{host}/akiya/",
                    "page_class": "listing_index",
                    "confidence": 0.9,
                    "operator_kind": "municipality",
                    "evidence_quote": f"公式ドメイン {host}",
                    "evidence_url": f"https://{host}/",
                    "evidence_checked_on": checked,
                    "cross_linked": False,
                    "policy": "pending" if key in PENDING else "crawl",
                    "reason": "",
                    "proposed_action": "承認",
                    "external_links": [],
                }
            )
        out[slug] = rows
    return out


def outcome(key: str, day: date) -> tuple[str, str]:
    if key in ALWAYS:
        return ALWAYS[key]
    r = int(hashlib.sha256(f"{key}|{day.isoformat()}".encode()).hexdigest()[:8], 16) % 100
    if r < 8:
        return "fail", "空き家バンクのページを開けない（HTTP 404）"
    if r < 12:
        return "unreachable", "公式サイトに通信できない（HTTP 403）"
    if r < 15:
        return "skip", "robots.txt で公式サイトを取得できない"
    return "ok", ""


def fake_assess(ws: Workspace, day: date) -> Callable[..., expand.MunicipalityFinding]:
    """選び直しの評価の代わり。別のホストに移る（名前に moved. を足す）か、決まらない。"""

    def assess(muni, client, registry, overrides) -> expand.MunicipalityFinding:
        if muni.code in PENDING_ON_RESELECT:
            return expand.decide(muni, None, None, cross_linked=False, bank_host_official=False)
        row = next(
            r for rows in recheck.load_findings(ws).values() for r in rows if r["code"] == muni.code
        )
        f = expand._row_to_finding(row)
        f.official_url = f"moved.{row['official_url']}"
        f.evidence_checked_on = day.isoformat()
        return f

    return assess


def new_workspace(root: Path, rows: dict[str, list[dict]]) -> Workspace:
    shutil.copy(REPO / "site.toml", root / "site.toml")
    for d in ("data/sources", "data/state", "data/records", "data/runs", "data/reference"):
        (root / d).mkdir(parents=True, exist_ok=True)
    ws = Workspace.open(root)
    for slug, rs in rows.items():
        expand._write_rows(ws, slug, rs[0]["prefecture"], rs)
    return ws


def log_entries(ws: Workspace) -> list[dict]:
    path = ws.runs_dir / "operator-rechecks.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def night_record(day: date, planned: list[str], entries: list[dict]) -> dict:
    """1 晩の記録。置き換え前は source_id、後は key（sitemill の形）なので、どちらでも読む。"""
    fields = ("source_id", "name", "old_official", "new_official", "old_bank", "new_bank", "reason")
    return {
        "date": day.isoformat(),
        "planned": planned,
        "log": [
            [e.get("key") or e.get("source_id"), e["result"], e.get("failures")]
            for e in entries
            if e["result"] != "reselect"
        ],
        "reselect": [{k: e.get(k) for k in fields} for e in entries if e["result"] == "reselect"],
    }


def row_view(findings: dict[str, list[dict]]) -> dict[str, list]:
    """自治体ごとの (確認日, 公式サイト, 方針)。"""
    fields = ("evidence_checked_on", "official_url", "policy")
    return {
        f"{slug}-{r['code']}": [r.get(k) for k in fields]
        for slug, rows in findings.items()
        for r in rows
    }


def state_view(state: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """成り立たない・待ちの状態。確認日（checked_on）は findings の方で見る。

    sitemill は成り立たなかった晩にも `last_tried` を書く（置き換え前は待ちのときだけ）。
    選び方には効かない（待ちかどうかは `waiting_since` で見る）ので、待ちでなければ外して比べる。
    """
    out = {}
    for key, st in sorted(state.items()):
        view = {k: v for k, v in st.items() if k != "checked_on"}
        if "waiting_since" not in view:
            view.pop("last_tried", None)
        if view:
            out[key] = view
    return out


def yaml_dates(ws: Workspace) -> dict[str, str]:
    out = {}
    for path in sorted(ws.sources_dir.glob("*-auto.yaml")):
        for s in yaml.safe_load(path.read_text(encoding="utf-8"))["sources"]:
            out[s["id"]] = str(s["operator_evidence"]["checked_on"])
    return out


def snapshot(ws: Workspace, at: datetime) -> dict:
    findings = recheck.load_findings(ws)
    entries = weekly.recheck_entries(ws, days=7, now=at.astimezone(UTC))
    # 表示の確認日（YAML）が findings と同じところまで進んでいること（固定値ではなく、その場で）
    rows = row_view(findings)
    for sid, day in yaml_dates(ws).items():
        assert rows[sid][0] == day, sid
    return {
        "rows": rows,
        "state": state_view(engine.load_state(ws)),
        "weekly": weekly.recheck_lines(ws, entries) + weekly.recheck_reselection_lines(entries),
    }


def run_nights(ws: Workspace, mp: pytest.MonkeyPatch, nights: int, workers: int) -> dict:
    """置き換え後のコードで nights 晩回し、比べる値を集める。"""
    out: dict[str, Any] = {"nights": [], "after": {}}
    seen = len(log_entries(ws))
    for n in range(nights):
        day = START + timedelta(days=n)
        at = datetime(day.year, day.month, day.day, 8, 30, tzinfo=JST)
        mp.setattr(engine, "jst_now", lambda at=at: at)
        mp.setattr(expand, "assess_municipality", fake_assess(ws, day))
        planned = [t.key for t in recheck.tonight(ws, limit=PER_NIGHT, today=day)]
        calls: list[str] = []

        def checker(
            slug: str, row: dict, *_: object, day: date = day, calls: list[str] = calls
        ) -> tuple[str, str]:
            key = f"{slug}-{row['code']}"
            calls.append(key)
            return outcome(key, day)

        client = PoliteClient("sitemill-test/0", default_delay=0, jitter=0, sleep=lambda _s: None)
        with client as c:
            recheck.recheck(
                ws, client=c, limit=PER_NIGHT, workers=workers, today=day, checker=checker
            )
        assert (calls if workers == 1 else sorted(calls)) == (
            planned if workers == 1 else sorted(planned)
        ), day
        entries = log_entries(ws)
        out["nights"].append(night_record(day, planned, entries[seen:]))
        seen = len(entries)
        if n + 1 in SNAPSHOTS:
            out["after"][str(n + 1)] = snapshot(ws, at)
    return out


def _start(tmp_path: Path) -> Workspace:
    ws = new_workspace(tmp_path, dataset())
    (ws.state_dir / "recheck.json").write_text(
        json.dumps(INITIAL_STATE, ensure_ascii=False), encoding="utf-8"
    )
    return ws


def _golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_the_golden_nights_cover_both_reselections_and_skips() -> None:
    """固定値そのものが、見たいことを含んでいること（縮めたときに抜け落ちないように）。"""
    golden = _golden()
    results = {e[1] for night in golden["nights"] for e in night["log"]}
    assert results == {"ok", "fail", "skip", "unreachable"}
    firsts = golden["nights"][:7]
    reselected = [r for night in firsts for r in night["reselect"]]
    assert {r["source_id"] for r in reselected} >= {"gunma-100002", "nagano-200003"}
    assert any(r["new_official"] is None for r in reselected)  # 決まらずレビューへ
    assert any(str(r["new_official"]).startswith("moved.") for r in reselected)  # 別のホスト
    assert any(e[1] in ("skip", "unreachable") for night in firsts for e in night["log"])
    assert "okinawa-470001" not in {e[0] for night in golden["nights"][:5] for e in night["log"]}
    assert "okinawa-470001" in golden["nights"][5]["planned"]  # 10-06 に待ちが明ける
    assert golden["nights"][0]["planned"][0] == "gunma-100006"  # 前の晩の失敗は枠の外で先に


def test_twenty_nights_in_series_match_the_code_before_the_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    got = run_nights(_start(tmp_path), monkeypatch, nights=20, workers=1)
    golden = _golden()
    for mine, theirs in zip(got["nights"], golden["nights"], strict=True):
        assert mine == theirs, mine["date"]
    assert got["after"] == golden["after"]


def test_seven_nights_with_eight_workers_match_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    got = run_nights(_start(tmp_path), monkeypatch, nights=7, workers=8)
    golden = _golden()
    for mine, theirs in zip(got["nights"], golden["nights"][:7], strict=True):
        assert mine == theirs, mine["date"]
    assert got["after"]["7"] == golden["after"]["7"]
