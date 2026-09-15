"""全国収集の進み方（済み印・中断）のテスト。ネットワークも LLM も使わない。

2026-09-15 の全国実行で、API の残高切れにより 8 県ぶんの抽出が全滅した。当時は「済み」印が
付いてしまい、再実行でも飛ばされる状態になった。巡回は成功しているので惜しいのはページでは
なく印のほうで、ここを落とすと取りこぼしに気づけない。
"""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from sitemill import commands
from sitemill.settings import Workspace

from akiya_atlas import subsidy_backfill

REPO = Path(__file__).resolve().parents[1]
PREFS = [("滋賀県", "shiga"), ("京都府", "kyoto"), ("大阪府", "osaka")]


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    shutil.copy(REPO / "site.toml", tmp_path / "site.toml")
    w = Workspace.open(tmp_path)
    w.ensure_dirs()
    return w


class _Rt:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    @contextmanager
    def client(self):  # noqa: ANN202
        yield SimpleNamespace(request_count=3)


def _setup(monkeypatch: pytest.MonkeyPatch, extract: dict, seen: list[str]) -> None:
    def _collect(ws, name, **kw):  # noqa: ANN001, ANN003, ANN202
        seen.append(name)
        return [f"{name}: 2 ページ"]

    monkeypatch.setattr(subsidy_backfill, "prefectures", lambda only=None: PREFS)
    monkeypatch.setattr(subsidy_backfill, "collect_subsidy_pages", _collect)
    monkeypatch.setattr(subsidy_backfill, "subsidy_source_ids", lambda ws, slug: ["s-1"])
    monkeypatch.setattr(subsidy_backfill, "count_subsidies", lambda ws, ids: 0)
    monkeypatch.setattr(
        commands, "cmd_crawl", lambda *a, **k: SimpleNamespace(stages={"crawl": {"fetched": 2}})
    )
    monkeypatch.setattr(
        commands,
        "cmd_extract",
        lambda *a, **k: SimpleNamespace(stages={"extract": extract}, llm=None),
    )


def test_a_prefecture_whose_extraction_all_failed_is_not_marked_done(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    _setup(monkeypatch, {"llm_errors": 5}, seen)
    prog = subsidy_backfill.run_subsidy_backfill(_Rt(ws), echo=lambda _s: None)
    table = prog["prefectures"]
    assert not any(e.get("done") for e in table.values())
    assert "抽出が 5 ページとも失敗した" in table["shiga"]["error"]
    # 続けて失敗したら中断する。鍵切れなら、残りの県を巡回しても無駄になる
    assert seen == ["滋賀県", "京都府"]


def test_one_failed_page_does_not_block_the_prefecture(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    _setup(monkeypatch, {"pages": 9, "items": 20, "llm_errors": 1}, seen)
    prog = subsidy_backfill.run_subsidy_backfill(_Rt(ws), echo=lambda _s: None)
    table = prog["prefectures"]
    assert all(e.get("done") for e in table.values()) and len(table) == 3
    assert seen == [name for name, _ in PREFS]


def test_a_failed_prefecture_is_retried_on_the_next_run(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """済み印が付いていなければ、次の実行でその県からやり直す。"""
    seen: list[str] = []
    _setup(monkeypatch, {"llm_errors": 5}, seen)
    subsidy_backfill.run_subsidy_backfill(_Rt(ws), echo=lambda _s: None)
    seen.clear()
    _setup(monkeypatch, {"pages": 5, "items": 12}, seen)
    monkeypatch.setattr(subsidy_backfill, "count_subsidies", lambda ws, ids: 12)
    prog = subsidy_backfill.run_subsidy_backfill(_Rt(ws), echo=lambda _s: None)
    assert seen == [name for name, _ in PREFS]  # 失敗した県も飛ばさない
    assert all(e.get("done") and not e.get("error") for e in prog["prefectures"].values())
