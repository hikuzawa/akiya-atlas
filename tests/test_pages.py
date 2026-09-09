"""サンプルデータから全ページを生成し、信頼シグナル・検索索引・写真なしの方針を確かめる。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sitemill import commands
from sitemill.models import FieldStatus, FieldValue
from sitemill.settings import Workspace

from akiya_atlas.data import Dataset
from akiya_atlas.pages import price_band
from akiya_atlas.schema import Listing, record_id_for
from akiya_atlas.service import service

REPO = Path(__file__).resolve().parents[1]

SOURCES_YAML = """
sources:
  - id: nagano-tomi
    name: 東御市空き家バンク
    operator: 東御市
    operator_kind: municipality
    operator_evidence:
      quote: "東御市役所 Copyright © TOMI City."
      url: https://akiya.city.tomi.nagano.jp/
      checked_on: 2026-09-10
    policy: crawl
    official_url: https://www.city.tomi.nagano.jp/
    pages:
      - url: https://akiya.city.tomi.nagano.jp/
        kind: listing_index
    external_links:
      - label: アットホーム 空き家バンク（東御市）
        url: https://tomi-c20219.akiya-athome.jp/
        note: 民間プラットフォーム
    municipality:
      code: "202193"
      name: 東御市
      prefecture: 長野県
      prefecture_slug: nagano
      slug: 202193-tomi
      bank_url: https://akiya.city.tomi.nagano.jp/
      subsidies:
        - name: 空き家改修補助
          kind: 改修
          url: https://www.city.tomi.nagano.jp/x
          summary: 改修費の一部を補助
          checked_on: 2026-09-10
  - id: nagano-saku
    name: 佐久市空き家バンク
    operator: 佐久市
    operator_kind: municipality
    operator_evidence:
      quote: "佐久市 企画部 移住交流推進課"
      url: https://39ijyu.com/
    policy: crawl
    official_url: https://www.city.saku.nagano.jp/
    pages:
      - url: https://39ijyu.com/all.php?kubun=IE
        kind: listing_index
    municipality:
      code: "202177"
      name: 佐久市
      prefecture: 長野県
      prefecture_slug: nagano
      slug: 202177-saku
      bank_url: https://39ijyu.com/index2.php?kubun=IE
"""


def _fv(value, quote, status=FieldStatus.parsed):  # noqa: ANN001, ANN202
    return FieldValue(value=value, quote=quote, status=status)


def _listing(source_id: str, code: str, no: str, **kw) -> dict:  # noqa: ANN003
    ls = Listing(
        record_id=record_id_for(source_id, no),
        source_id=source_id,
        municipality_code=code,
        listing_no=no,
        source_url=f"https://example.invalid/{source_id}/{no}",
        first_seen_at="2026-09-01T00:00:00+00:00",
        last_seen_at="2026-09-10T00:00:00+00:00",
        **kw,
    )
    return ls.model_dump(mode="json")


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workspace:
    monkeypatch.delenv("GOOGLE_MAPS_EMBED_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    shutil.copy(REPO / "site.toml", tmp_path / "site.toml")
    shutil.copytree(REPO / "templates", tmp_path / "templates")
    shutil.copytree(REPO / "static", tmp_path / "static")
    (tmp_path / "data" / "sources").mkdir(parents=True)
    (tmp_path / "data" / "sources" / "nagano.yaml").write_text(SOURCES_YAML, encoding="utf-8")
    (tmp_path / "data" / "records").mkdir()
    rows = [
        _listing(
            "nagano-tomi",
            "202193",
            "322",
            deal_type="sale",
            title="原口の木造住宅",
            summary="東御市原口の住宅。",
            address=_fv("東御市原口", "東御市原口"),
            price=_fv(10_000_000, "1000万円"),
            built_year=_fv(1970, "昭和45年"),
            floor_area_m2=_fv(98.5, "98.5㎡"),
            detail_url="https://akiya.city.tomi.nagano.jp/2026/08/1000no277.html",
            page_kind="listing_detail",
            provenance={"fetched_at": "2026-09-10T01:02:03+00:00", "extractor": {"model": "m"}},
        ),
        _listing(
            "nagano-tomi",
            "202193",
            "290",
            deal_type="rent",
            rent_monthly=_fv(85_000, "85,000円"),
            built_year=_fv(None, "築40年", FieldStatus.unparsed),
        ),
        _listing("nagano-tomi", "202193", "8", deal_type="sale", status="stale"),
        _listing("nagano-saku", "202177", "293", deal_type="sale", price=_fv(2_500_000, "250万円")),
    ]
    (tmp_path / "data" / "records" / "nagano-tomi.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows[:3]) + "\n", encoding="utf-8"
    )
    (tmp_path / "data" / "records" / "nagano-saku.jsonl").write_text(
        json.dumps(rows[3], ensure_ascii=False) + "\n", encoding="utf-8"
    )
    w = Workspace.open(tmp_path)
    w.ensure_dirs()
    return w


def test_price_band() -> None:
    assert (
        price_band(Listing.model_validate(_listing("s", "1", "a", price=_fv(500_000, "50万円"))))
        == "100万円未満"
    )
    assert (
        price_band(Listing.model_validate(_listing("s", "1", "b", price=_fv(3_000_000, "300万円"))))
        == "300〜500万円"
    )
    assert (
        price_band(
            Listing.model_validate(_listing("s", "1", "c", price=_fv(20_000_000, "2000万円")))
        )
        == "1,000万円以上"
    )
    assert price_band(Listing.model_validate(_listing("s", "1", "d", deal_type="rent"))) == "賃貸"
    assert price_band(Listing.model_validate(_listing("s", "1", "e"))) == "価格記載なし・応相談"


def test_dataset_and_search_index(ws: Workspace) -> None:
    ds = Dataset.load(ws)
    assert [m.name for m in ds.municipalities] == ["東御市", "佐久市"]
    assert len(ds.listings) == 4
    tomi = ds.muni_by_source["nagano-tomi"]
    assert len(ds.listings_for(tomi, active_only=True)) == 2
    index = service.search_index(ws)
    assert len(index) == 3  # stale は検索に出さない
    row = next(r for r in index if r["no"] == "322")
    assert row["band"] == "1,000万円以上" and row["subsidy_renovation"] is True
    assert row["url"] == "/nagano/202193-tomi/322/"


def test_build_generates_all_pages_with_trust_and_no_photos(ws: Workspace) -> None:
    rt = commands.Runtime(ws=ws, service=service)
    report = commands.cmd_build(rt)
    dist = ws.dist_dir
    expected = [
        "index.html",
        "nagano/index.html",
        "nagano/202193-tomi/index.html",
        "nagano/202193-tomi/322/index.html",
        "nagano/202193-tomi/8/index.html",
        "nagano/202177-saku/293/index.html",
        "owners/index.html",
        "about/index.html",
        "data/index.html",
        "search/index.json",
        "sitemap.xml",
        "robots.txt",
        "_redirects",
        "static/style.css",
        "static/search.js",
    ]
    for rel in expected:
        assert (dist / rel).is_file(), rel
    assert report.stages["build"]["pages"] == 11

    listing = (dist / "nagano/202193-tomi/322/index.html").read_text(encoding="utf-8")
    assert (
        "data-sitemill-trust" in listing
        and "1,000万円" in listing
        and "昭和45年" not in listing.split("原文:")[0]
    )
    assert "https://akiya.city.tomi.nagano.jp/2026/08/1000no277.html" in listing
    assert "<img" not in listing  # 写真は載せない
    assert "sm-embed-fallback" in listing  # 地図キーが無いので外部リンクにフォールバック
    assert "準備中" in listing  # 運営者情報は仮置き

    muni = (dist / "nagano/202193-tomi/index.html").read_text(encoding="utf-8")
    assert "掲載終了の可能性" in muni and "空き家改修補助" in muni
    assert "アットホーム 空き家バンク（東御市）" in muni and 'rel="noopener nofollow"' in muni

    owners = (dist / "owners/index.html").read_text(encoding="utf-8")
    assert "準備中" in owners and "/go/" not in owners  # ダミーリンクを置かない
    assert 'data-generated="sitemill.charts"' in owners

    assert (dist / "_redirects").read_text(encoding="utf-8") == ""
    sitemap = (dist / "sitemap.xml").read_text(encoding="utf-8")
    assert "https://akiya-atlas.pages.dev/nagano/202193-tomi/322/" in sitemap
