"""市町村ページのタイトルと説明文（docs/improvements/0001）。

持っていない数字を書かないことを、ページの状態ごとに確かめる。
"""

from __future__ import annotations

from datetime import UTC, datetime

from akiya_atlas.pages import municipality_meta
from akiya_atlas.schema import FieldValue, Listing, Municipality, Subsidy

FETCHED = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)  # JST では 9 月 17 日


def _muni(**kw: object) -> Municipality:
    base = {
        "id": "kochi-392031",
        "code": "392031",
        "name": "安芸市",
        "prefecture": "高知県",
        "prefecture_slug": "kochi",
        "slug": "392031",
        "official_url": "https://www.city.aki.kochi.jp/",
        "bank_url": "https://www.city.aki.kochi.jp/iju/reside/list.php",
    }
    return Municipality(**{**base, **kw})


def _listing(no: str, price: int | None) -> Listing:
    return Listing(
        record_id=f"r{no}",
        source_id="kochi-392031",
        municipality_code="392031",
        listing_no=no,
        source_url="https://www.city.aki.kochi.jp/iju/reside/list.php",
        price=FieldValue(value=price, quote=f"{price}円", status="parsed")
        if price is not None
        else FieldValue(),
    )


SUBSIDY = Subsidy(name="移住支援", kind="移住", url="https://www.city.aki.kochi.jp/s.html")


def test_a_page_with_listings_says_how_many_and_from_what_price() -> None:
    """安芸市の形。検索語と一致する見出しは残し、件数・価格・確認日・補助制度を足す。"""
    active = [
        _listing("1", 500_000),
        _listing("2", 3_500_000),
        _listing("3", 0),
        _listing("4", None),
    ]
    title, desc = municipality_meta(_muni(subsidies=[SUBSIDY]), active, None, FETCHED)
    assert title == "安芸市の空き家バンク 掲載4件（高知県）"
    assert desc == (
        "高知県安芸市の空き家バンクに掲載中の4件を一覧にしました（9月17日確認）。"
        "売買は50万円から、無償譲渡1件。補助制度1件と、市の公式ページへのリンク付き。"
    )


def test_a_crawled_page_with_no_listings_says_so_only_when_the_list_was_read() -> None:
    """一覧を取得できていて 0 件なら「掲載なし」と言う。取り込めていないなら言わない。"""
    muni = _muni(name="睦沢町")
    _, empty = municipality_meta(muni, [], "empty", FETCHED)
    assert "いま掲載中の物件がありません（9月17日確認）" in empty
    assert "町の公式ページ" in empty
    _, unavailable = municipality_meta(muni, [], "unavailable", FETCHED)
    assert "ありません" not in unavailable and "確認）" not in unavailable
    assert "物件は町の公式ページでご確認ください" in unavailable


def test_a_page_we_do_not_crawl_never_claims_listings_or_a_check_date() -> None:
    """夕張市の形。以前は全ページで「掲載物件の要約」と書いていたが、物件が無い町では事実と違う。"""
    title, desc = municipality_meta(
        _muni(name="夕張市", prefecture="北海道", bank_status="none"), [], None, FETCHED
    )
    assert title == "夕張市の空き家バンク（北海道）"
    assert (
        desc
        == "北海道夕張市の空き家バンクの窓口をまとめました。物件は市の公式ページでご確認ください。"
    )
    assert "掲載" not in desc and "要約" not in desc and "確認）" not in desc

    title, desc = municipality_meta(
        _muni(
            name="開成町", prefecture="神奈川県", bank_status="none", subsidies=[SUBSIDY, SUBSIDY]
        ),
        [],
        None,
        FETCHED,
    )
    assert title == "開成町の空き家バンクと補助制度（神奈川県）"
    assert "窓口と補助制度2件" in desc
