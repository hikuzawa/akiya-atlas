"""全国バックフィル（進捗・再開）と県リンク集の突き合わせ（reference）のテスト。ネットワーク不要。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sitemill import commands
from sitemill.settings import Workspace

from akiya_atlas import backfill
from akiya_atlas.municipalities import MunicipalityRef
from akiya_atlas.reference import match_links
from akiya_atlas.service import service

REPO = Path(__file__).resolve().parents[1]


def test_prefectures_are_47_in_jis_order_and_filterable() -> None:
    rows = backfill.prefectures()
    assert len(rows) == 47 and rows[0] == ("北海道", "hokkaido") and rows[-1][1] == "okinawa"
    assert backfill.prefectures(["香川県"]) == [("香川県", "kagawa")]
    assert backfill.prefectures(["37", "nagano"]) == [("長野県", "nagano"), ("香川県", "kagawa")]


@pytest.fixture
def rt(tmp_path: Path) -> commands.Runtime:
    shutil.copy(REPO / "site.toml", tmp_path / "site.toml")
    for d in ("data/sources", "data/state", "data/records", "data/runs", "data/reference"):
        (tmp_path / d).mkdir(parents=True)
    ws = Workspace.open(tmp_path)
    ws.ensure_dirs()
    return commands.Runtime(ws=ws, service=service)


def test_backfill_records_progress_and_resumes(rt: commands.Runtime) -> None:
    lines: list[str] = []
    backfill.run_backfill(rt, only=["香川県"], stages=("crawl",), echo=lines.append)
    prog = backfill.load_progress(rt.ws)["prefectures"]
    assert prog["kagawa"]["crawl"] and prog["kagawa"]["name"] == "香川県"
    assert not prog["kagawa"].get("discover")
    # 2 回目は済みとして飛ばす
    lines.clear()
    backfill.run_backfill(rt, only=["香川県"], stages=("crawl",), echo=lines.append)
    assert any("スキップ" in ln for ln in lines)
    # 以前に手動で discover した県は発見済み扱い
    (rt.ws.runs_dir / "discover-nagano.json").write_text("{}", encoding="utf-8")
    backfill.run_backfill(rt, only=["長野県"], stages=("discover", "crawl"), echo=lines.append)
    prog = backfill.load_progress(rt.ws)["prefectures"]
    assert prog["nagano"]["discover"] == "既存" and prog["nagano"]["crawl"]
    assert any("香川県" in ln for ln in backfill.status_table(rt.ws))


def _m(code: str, name: str) -> MunicipalityRef:
    return MunicipalityRef(
        code=code, prefecture="香川県", prefecture_slug="kagawa", name=name, name_kana=""
    )


def test_match_links_handles_decorated_anchors_and_internal_pages() -> None:
    munis = [_m("372013", "高松市"), _m("372072", "東かがわ市"), _m("373222", "土庄町")]
    links = [
        ("高松市（外部リンク）", "http://www.city.takamatsu.kagawa.jp/"),
        ("東かがわ市ホームページ", "http://www.higashikagawa.jp/"),
        ("土庄町", "https://www.pref.kagawa.lg.jp/shokai/tonosho.html"),  # 県サイト内 → 無視
        ("観光情報", "https://www.my-kagawa.jp/"),
    ]
    table = match_links(munis, links, page_host="www.pref.kagawa.lg.jp")
    assert table.matched == {
        "372013": "http://www.city.takamatsu.kagawa.jp/",
        "372072": "http://www.higashikagawa.jp/",
    }
    assert table.unmatched == ["土庄町"] and len(table.ignored) == 1
    table.names = {m.code: m.name for m in munis}
    rows = table.to_json()["municipalities"]
    assert rows[0] == {
        "code": "372013",
        "name": "高松市",
        "official_url": "http://www.city.takamatsu.kagawa.jp/",
    }
