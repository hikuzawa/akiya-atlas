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


def test_longest_name_wins_so_decorated_anchors_are_not_confused() -> None:
    # 大阪府の実ページ: 「東大阪市（外部サイトへリンク）」を大阪市に取り違えていた
    munis = [_m("271004", "大阪市"), _m("272272", "東大阪市"), _m("072031", "相馬市")]
    munis.append(_m("072125", "南相馬市"))
    links = [
        ("東大阪市（外部サイトへリンク）", "http://www.city.higashiosaka.lg.jp/"),
        ("大阪市（外部サイトへリンク）", "https://www.city.osaka.lg.jp/index.html"),
        ("南相馬市（みなみそうまし）（外部リンク）", "http://www.city.minamisoma.lg.jp/"),
        ("相馬市（そうまし）（外部リンク）", "http://www.city.soma.fukushima.jp/"),
    ]
    table = match_links(munis, links, page_host="www.pref.osaka.lg.jp")
    assert table.matched["272272"] == "http://www.city.higashiosaka.lg.jp/"
    assert table.matched["271004"] == "https://www.city.osaka.lg.jp/index.html"
    assert table.matched["072125"] == "http://www.city.minamisoma.lg.jp/"
    assert table.matched["072031"] == "http://www.city.soma.fukushima.jp/"
    assert table.unmatched == [] and table.duplicates == []


def test_old_form_characters_match() -> None:
    # 高知県の実ページは「檮原町」表記、コード表は「梼原町」
    table = match_links(
        [_m("394050", "梼原町")],
        [("檮原町", "http://www.town.yusuhara.kochi.jp/")],
        page_host="www.pref.kochi.lg.jp",
    )
    assert table.matched == {"394050": "http://www.town.yusuhara.kochi.jp/"}


def test_name_in_the_row_is_used_when_the_anchor_is_a_url() -> None:
    # 石川県の実ページはアンカー文字列が URL そのもの。行（表の行）に市町名がある
    links = [
        (
            "https://www.city.wajima.ishikawa.jp/",
            "https://www.city.wajima.ishikawa.jp/",
            "輪島市 https://www.city.wajima.ishikawa.jp/",
        )
    ]
    table = match_links([_m("172049", "輪島市")], links, page_host="www.pref.ishikawa.lg.jp")
    assert table.matched == {"172049": "https://www.city.wajima.ishikawa.jp/"}
    # 長すぎる行は根拠にしない
    long_row = ("https://x.example/", "https://x.example/", "輪島市 " + "あ" * 80)
    assert match_links([_m("172049", "輪島市")], [long_row], page_host="p").matched == {}


def test_heading_is_the_last_resort() -> None:
    # 石川県: アンカーは URL、行は住所や電話番号で長い。直前の見出しに市町名がある
    links = [
        (
            "https://www.city.suzu.lg.jp/",
            "https://www.city.suzu.lg.jp/",
            "郵便番号：927-1295 住所：珠洲市上戸町北方1字6-2 電話番号：0768-82-2222 ホームページ： https://www.city.suzu.lg.jp/",
            "珠洲市（すずし）",
        )
    ]
    table = match_links([_m("172057", "珠洲市")], links, page_host="www.pref.ishikawa.lg.jp")
    assert table.matched == {"172057": "https://www.city.suzu.lg.jp/"}


def test_same_name_in_one_prefecture_is_left_unmatched() -> None:
    # 北海道には泊村が2つ（古宇郡・国後郡）。どちらか分からないので対応づけない
    munis = [_m("013943", "泊村"), _m("016951", "泊村"), _m("012025", "函館市")]
    links = [
        ("泊村", "http://www.vill.tomari.hokkaido.jp/"),
        ("函館市", "https://www.city.hakodate.hokkaido.jp/"),
    ]
    table = match_links(munis, links, page_host="www.pref.hokkaido.lg.jp")
    assert table.ambiguous == ["泊村"] and "013943" not in table.matched
    assert table.matched["012025"] == "https://www.city.hakodate.hokkaido.jp/"


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
