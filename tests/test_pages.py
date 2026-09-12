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
    # 索引は都道府県ごとに分かれている（入口・最近確認した分・県ごとのファイル）
    assert set(index) == {"index.json", "recent.json", "nagano.json"}
    assert index["index.json"]["total"] == 3  # stale は検索に出さない
    rows = index["nagano.json"]
    assert len(rows) == 3 and len(index["recent.json"]) == 3
    row = next(r for r in rows if r["no"] == "322")
    assert row["band"] == "1,000万円以上" and row["subsidy_renovation"] is True
    assert row["url"] == "/nagano/202193-tomi/322/"
    assert row["has_detail"] is True and row["floor_area_m2"] == 98.5  # 詳細ページあり
    pref = index["index.json"]["prefectures"][0]
    assert pref["slug"] == "nagano" and pref["count"] == 3
    assert {m["code"] for m in pref["municipalities"]} == {"202193", "202177"}


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
        "404.html",
        "search/index.json",
        "sitemap.xml",
        "robots.txt",
        "_redirects",
        "static/style.css",
        "static/search.js",
    ]
    for rel in expected:
        assert (dist / rel).is_file(), rel
    assert report.stages["build"]["pages"] == 12

    listing = (dist / "nagano/202193-tomi/322/index.html").read_text(encoding="utf-8")
    assert (
        "data-sitemill-trust" in listing
        and "1,000万円" in listing
        and "昭和45年" not in listing.split("原文:")[0]
    )
    assert "https://akiya.city.tomi.nagano.jp/2026/08/1000no277.html" in listing
    assert "<img" not in listing  # 写真は載せない
    assert "sm-embed-fallback" in listing  # 地図キーが無いので外部リンクにフォールバック
    assert "空き家アトラス 運営" in listing  # 運営者情報はフッターの信頼ブロックに出る
    assert "data-ad-notice" not in listing  # 物件ページには広告を置かない（ADR 0010）

    muni = (dist / "nagano/202193-tomi/index.html").read_text(encoding="utf-8")
    assert "掲載終了の可能性" in muni and "空き家改修補助" in muni
    assert "アットホーム 空き家バンク（東御市）" in muni and 'rel="noopener nofollow"' in muni

    owners = (dist / "owners/index.html").read_text(encoding="utf-8")
    assert "準備中" in owners and "/go/" not in owners  # ダミーリンクを置かない
    assert "data-ad-notice" not in owners  # 広告リンクが有効になるまで表記も出さない

    about = (dist / "about/index.html").read_text(encoding="utf-8")
    assert "プライバシーポリシー" in about and "docs.google.com/forms" in about
    # 地図キーが無い間は埋め込まないので、Google の Cookie と通信には触れない
    assert "本サイトは Cookie を使用していません" in about
    # 計測は Cloudflare の自動挿入。トークンが無くても使用している事実は開示する（ADR 0011）
    assert "Cloudflare Web Analytics を使用しています" in about
    assert "ページを開いただけでは Google への通信は発生しません" in about
    assert 'data-generated="sitemill.charts"' in owners

    # ASP 未契約の間は /go/ を出さない。www / pages.dev → apex は Bulk Redirects で行う
    assert (dist / "_redirects").read_text(encoding="utf-8") == ""
    sitemap = (dist / "sitemap.xml").read_text(encoding="utf-8")
    assert f"{ws.site.base_url}/nagano/202193-tomi/322/" in sitemap  # 基準 URL は site.toml に従う


def test_pii_check_passes_clean_and_flags_personal_info(ws: Workspace) -> None:
    from sitemill.build.site import BuildError

    from akiya_atlas.pages import ensure_no_pii

    ds = Dataset.load(ws)
    ensure_no_pii(ds, ws)  # サンプルデータは合格

    # 個人の氏名・メール・電話を要約に混ぜると検出してビルドを止める
    ds.listings[0].summary = "所有者 佐藤一郎 様 連絡先 taro@gmail.com 090-1234-5678"
    with pytest.raises(BuildError, match="個人情報"):
        ensure_no_pii(ds, ws)


def test_municipal_representative_phone_is_whitelisted(ws: Workspace) -> None:
    from sitemill.build.pii import scan_text

    from akiya_atlas.pages import _pii_policy

    # 運営根拠の引用に代表電話を載せると、その番号は代表電話として許可される
    yaml_path = ws.sources_dir / "nagano.yaml"
    text = yaml_path.read_text(encoding="utf-8").replace(
        'quote: "東御市役所 Copyright © TOMI City."',
        'quote: "東御市役所 電話：0268-62-1111（代表） Copyright © TOMI City."',
    )
    yaml_path.write_text(text, encoding="utf-8")
    policy = _pii_policy(ws)
    assert scan_text("お問い合わせ 0268-62-1111", policy=policy) == []
    # 一方で個人の携帯番号は許可されない
    assert [f.kind for f in scan_text("090-1234-5678", policy=policy)] == ["phone"]


def test_is_closed_and_is_active() -> None:
    sold = Listing.model_validate(
        _listing("s", "1", "a", title="【ご成約済】古民家", price=_fv(3_000_000, "300万円"))
    )
    assert sold.is_closed and not sold.is_active
    live = Listing.model_validate(_listing("s", "1", "b", title="静かな古民家"))
    assert not live.is_closed and live.is_active
    # stale も非アクティブ
    stale = Listing.model_validate(_listing("s", "1", "c", title="家", status="stale"))
    assert not stale.is_active


def test_listing_slug_separates_japanese_prefixed_numbers() -> None:
    """「地-24」と「家-24」は英数字だけ残すと同じになる（北海道当別町の実例）。"""
    from akiya_atlas.schema import listing_slug

    a = listing_slug("地-24", "fallbackA")
    b = listing_slug("家-24", "fallbackB")
    assert a != b and a.startswith("24-") and b.startswith("24-")
    # 英数字だけの番号はこれまでどおり
    assert listing_slug("A-12", "x") == "a-12" and listing_slug("25", "x") == "25"
    assert listing_slug("？？", "fallback") == "fallback"


def test_page_says_not_yet_imported_when_the_listing_exists_but_we_read_nothing() -> None:
    """一覧は確認できているのに 0 件のとき、「募集なし」ではなく「取り込めていない」と出す。"""
    from akiya_atlas.schema import Municipality

    payload = {
        "id": "niigata-152021",
        "code": "152021",
        "name": "架空市",
        "prefecture": "新潟県",
        "prefecture_slug": "niigata",
        "slug": "152021",
        "official_url": "https://example.lg.jp/",
        "bank_url": "https://example.lg.jp/akiya/",
        "bank_status": "available",
        "extract_pending": True,
    }
    muni = Municipality.model_validate(payload)
    assert muni.crawled and muni.extract_pending
    plain = Municipality.model_validate({**payload, "extract_pending": False})
    assert not plain.extract_pending


def test_takedown_hides_the_listing_from_pages_and_search(ws: Workspace) -> None:
    """取り下げ依頼のあったページは次のビルドから消える（Issue を閉じれば戻る）。"""
    import json as _json

    from akiya_atlas.takedown import collect

    target = "https://akiya-atlas.com/nagano/202193-tomi/322/"
    issue = {
        "number": 7,
        "title": "[お問い合わせ] 掲載の削除依頼",
        "body": '<!-- {"kind":"akiya-atlas-contact","category":"takedown","urls":["'
        + target
        + '"]} -->',
        "labels": [{"name": "takedown"}],
    }
    collect([issue], base_url="https://akiya-atlas.com").save(ws)

    rt = commands.Runtime(ws=ws, service=service)
    commands.cmd_build(rt)
    dist = ws.dist_dir
    assert not (dist / "nagano/202193-tomi/322/index.html").exists()
    muni_page = (dist / "nagano/202193-tomi/index.html").read_text(encoding="utf-8")
    assert "原口の木造住宅" not in muni_page
    rows = _json.loads((dist / "search/nagano.json").read_text(encoding="utf-8"))
    assert all(r["url"] != "/nagano/202193-tomi/322/" for r in rows)
    sitemap = (dist / "sitemap.xml").read_text(encoding="utf-8")
    assert "/nagano/202193-tomi/322/" not in sitemap


def test_price_quote_is_shown_without_brackets() -> None:
    """数値化できない価格は原文の引用を出す。囲みの角括弧は表示崩れに見えるので外す。"""
    from sitemill.models import FieldStatus, FieldValue

    from akiya_atlas.pages import price_text
    from akiya_atlas.schema import Listing

    def _mk(quote: str) -> Listing:
        return Listing(
            record_id="a",
            source_id="s",
            municipality_code="000000",
            listing_no="1",
            source_url="https://example.invalid/",
            price=FieldValue(quote=quote, status=FieldStatus.unparsed),
        )

    assert price_text(_mk("[無償譲渡]")) == "無償譲渡"
    assert price_text(_mk("［応相談］")) == "応相談"
    assert price_text(_mk("応相談")) == "応相談"
    assert price_text(_mk("[]")) == "—"
