"""公開直前の性質の検査と、巡回をやめた情報源の物件の退役（ADR 0016、sitemill ADR 0024）。

事故の実例: 兵庫県小野市は 2026-09-12 に「物件一覧が PDF だけなので巡回しない」と決めたが、
9/11 に取り込んだ 1 件が 9/16 まで公開され、サイトマップにも載っていた。巡回をやめる判断は
data/sources を書き換えるだけで、取り込み済みのレコードには触れていなかった。
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from sitemill import commands
from sitemill.settings import Workspace

from akiya_atlas import publish_check
from akiya_atlas.schema import Listing, record_id_for
from akiya_atlas.service import retire_unlisted, service

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)

SOURCE = {
    "id": "nagano-tomi",
    "name": "東御市空き家バンク",
    "operator": "東御市",
    "operator_kind": "municipality",
    "operator_evidence": {
        "quote": "東御市役所 Copyright © TOMI City.",
        "url": "https://akiya.city.tomi.nagano.jp/",
        "checked_on": "2026-09-10",
    },
    "policy": "crawl",
    "official_url": "https://www.city.tomi.nagano.jp/",
    "pages": [{"url": "https://akiya.city.tomi.nagano.jp/", "kind": "listing_index"}],
    "municipality": {
        "code": "202193",
        "name": "東御市",
        "prefecture": "長野県",
        "prefecture_slug": "nagano",
        "slug": "202193-tomi",
        "bank_url": "https://akiya.city.tomi.nagano.jp/",
    },
}


def _write_source(ws_root: Path, *, pages: list[dict]) -> None:
    src = {**SOURCE, "pages": pages}
    # 物件ページを巡回しないなら、巡回中としては数えない（サイトの「◯市町村を巡回」に入れない）
    if not any(p["kind"].startswith("listing") for p in pages):
        src["municipality"] = {**src["municipality"], "bank_status": "none"}
    (ws_root / "data" / "sources" / "nagano.yaml").write_text(
        yaml.safe_dump({"sources": [src]}, allow_unicode=True), encoding="utf-8"
    )


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workspace:
    monkeypatch.delenv("GOOGLE_MAPS_EMBED_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    shutil.copy(REPO / "site.toml", tmp_path / "site.toml")
    shutil.copytree(REPO / "templates", tmp_path / "templates")
    shutil.copytree(REPO / "static", tmp_path / "static")
    (tmp_path / "data" / "sources").mkdir(parents=True)
    (tmp_path / "data" / "records").mkdir()
    _write_source(tmp_path, pages=SOURCE["pages"])
    row = Listing(
        record_id=record_id_for("nagano-tomi", "322"),
        source_id="nagano-tomi",
        municipality_code="202193",
        listing_no="322",
        source_url="https://akiya.city.tomi.nagano.jp/",
        first_seen_at="2026-09-11T00:00:00+00:00",
        last_seen_at="2026-09-11T00:00:00+00:00",
        deal_type="sale",
        title="原口の木造住宅",
        summary="東御市原口の住宅。",
    ).model_dump(mode="json")
    (tmp_path / "data" / "records" / "nagano-tomi.jsonl").write_text(
        json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    w = Workspace.open(tmp_path)
    w.ensure_dirs()
    return w


def _status(ws: Workspace) -> str:
    line = (ws.root / "data/records/nagano-tomi.jsonl").read_text(encoding="utf-8").splitlines()[0]
    return json.loads(line)["status"]


LISTING = "nagano/202193-tomi/322/index.html"


def test_committed_data_keeps_the_published_properties() -> None:
    """コミット済みのデータそのものが性質を満たしている（push のたびに気づけるように）。

    配置の直前の検査だけだと、日次が走るまで気づかない。データで確かめられる分はここでも見る。
    """
    assert publish_check.check_data(Workspace.open(REPO)) == []


def test_the_check_stops_a_listing_whose_source_is_no_longer_crawled(ws: Workspace) -> None:
    """小野市の再現。巡回をやめても退役させなければ、物件ページが公開されたままになる。"""
    commands.cmd_build(commands.Runtime(ws=ws, service=service))
    assert publish_check.check(ws, ws.dist_dir)[0] == []  # 巡回しているうちは問題ない

    # 物件一覧をやめて補助制度のページだけにする（補助制度の収集が実際にやっている形）
    _write_source(
        ws.root, pages=[{"url": "https://www.city.tomi.nagano.jp/h.html", "kind": "subsidy"}]
    )
    commands.cmd_build(commands.Runtime(ws=ws, service=service))
    problems, _ = publish_check.check(ws, ws.dist_dir)
    assert any("公開される状態の物件がある" in p for p in problems)  # データで気づく
    assert any(
        "/nagano/202193-tomi/322/" in p and "公開されている" in p for p in problems
    )  # 生成物で気づく


def test_retired_listing_leaves_a_noindex_page_out_of_the_sitemap(ws: Workspace) -> None:
    """退役した物件は一覧にも出さない。URL には noindex の掲載終了ページを残し、
    サイトマップから外す。

    削除して 404 にすると、既に索引された URL から来た人が行き止まりになる。Cloudflare Pages の
    静的配信では 410 を返せないので、noindex で検索結果から外してもらう。
    """
    _write_source(
        ws.root, pages=[{"url": "https://www.city.tomi.nagano.jp/h.html", "kind": "subsidy"}]
    )
    assert retire_unlisted(ws, now=NOW) == {"retired": 1, "restored": 0}
    assert _status(ws) == "retired"
    commands.cmd_build(commands.Runtime(ws=ws, service=service))

    page = (ws.dist_dir / LISTING).read_text(encoding="utf-8")
    assert "掲載は終了しました" in page
    assert 'name="robots" content="noindex' in page
    assert "原口の木造住宅" not in page  # 公開しないと決めた中身は出さない
    assert "/nagano/202193-tomi/322/" not in (ws.dist_dir / "sitemap.xml").read_text(
        encoding="utf-8"
    )
    muni = (ws.dist_dir / "nagano/202193-tomi/index.html").read_text(encoding="utf-8")
    assert "原口の木造住宅" not in muni
    assert publish_check.check(ws, ws.dist_dir)[0] == []


def test_retired_listing_comes_back_when_the_source_is_crawled_again(ws: Workspace) -> None:
    """退役は削除ではない。巡回に戻したら、見えていなければ stale、見えたら active に戻る。"""
    _write_source(
        ws.root, pages=[{"url": "https://www.city.tomi.nagano.jp/h.html", "kind": "subsidy"}]
    )
    retire_unlisted(ws, now=NOW)
    _write_source(ws.root, pages=SOURCE["pages"])
    assert retire_unlisted(ws, now=NOW) == {"retired": 0, "restored": 1}
    assert _status(ws) == "stale"  # 退役の後に取り込み直されていない


def test_the_check_notices_when_the_crawled_count_on_the_top_page_drifts(ws: Workspace) -> None:
    """トップで公言している巡回数と、巡回中として数えている数がずれたら止める。"""
    commands.cmd_build(commands.Runtime(ws=ws, service=service))
    top = ws.dist_dir / "index.html"
    html = top.read_text(encoding="utf-8")
    assert "全国 1 市町村の空き家バンクを毎日巡回" in html
    top.write_text(html.replace("全国 1 市町村", "全国 1,741 市町村"), encoding="utf-8")
    problems, _ = publish_check.check(ws, ws.dist_dir)
    assert any("1,741" in p for p in problems)
