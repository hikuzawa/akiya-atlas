"""集計（価格の中央値・最安）と、一覧→詳細の追従を見つける仕組みのテスト。"""

from __future__ import annotations

import httpx
import respx
from sitemill.fetch.client import PoliteClient
from sitemill.models import FieldStatus, FieldValue

from akiya_atlas.expand import detect_detail_follow
from akiya_atlas.pages import stats_of
from akiya_atlas.schema import Listing, record_id_for

HOST = "www.city.kakuu.niigata.jp"
BASE = f"https://{HOST}"


def _fv(value, quote, status=FieldStatus.parsed):  # noqa: ANN001, ANN202
    return FieldValue(value=value, quote=quote, status=status)


def _listing(no: str, **kw) -> Listing:  # noqa: ANN003
    return Listing(
        record_id=record_id_for("s", no),
        source_id="s",
        municipality_code="152021",
        listing_no=no,
        source_url=f"https://example.invalid/{no}",
        **kw,
    )


def test_stats_of_gives_median_min_and_bands() -> None:
    rows = [
        _listing(
            "1", deal_type="sale", price=_fv(1_000_000, "100万円"), built_year=_fv(1970, "昭和45年")
        ),
        _listing(
            "2", deal_type="sale", price=_fv(3_000_000, "300万円"), built_year=_fv(1990, "平成2年")
        ),
        _listing("3", deal_type="sale", price=_fv(9_000_000, "900万円")),
        _listing("4", deal_type="rent", rent_monthly=_fv(50_000, "5万円")),
    ]
    st = stats_of(rows)
    assert st["listings"] == 4 and st["priced"] == 3
    assert st["price_min"] == 1_000_000 and st["price_median"] == 3_000_000
    assert st["paid"] == 3 and st["free"] == 0
    assert st["built_count"] == 2 and st["built_min"] == 1970 and st["built_max"] == 1990
    assert st["built_median"] == 1980  # 2 件のときは中間
    assert sum(st["band_counts"]) == 3 and st["band_labels"][st["band_top"]]
    assert st["sale"] == 3 and st["rent"] == 1 and st["with_detail"] == 0
    assert stats_of([])["price_median"] is None


def test_free_listings_are_counted_apart_from_the_cheapest() -> None:
    """0 円（無償譲渡）は最安・中央値に混ぜず、件数として別に出す。"""
    rows = [
        _listing("1", deal_type="sale", price=_fv(0, "0円")),
        _listing("2", deal_type="sale", price=_fv(0, "0円(無償)")),
        _listing("3", deal_type="sale", price=_fv(1_200_000, "120万円")),
        _listing("4", deal_type="sale", price=_fv(4_000_000, "400万円")),
        _listing("5", deal_type="sale", price=_fv(9_000_000, "900万円")),
    ]
    st = stats_of(rows)
    assert st["priced"] == 5 and st["paid"] == 3 and st["free"] == 2
    assert st["price_min"] == 1_200_000  # 0 円は最安にしない
    assert st["price_median"] == 4_000_000
    # 価格が全部無償なら、最安も中央値も出さない
    only_free = stats_of([_listing("6", deal_type="sale", price=_fv(0, "0円"))])
    assert only_free["free"] == 1 and only_free["price_min"] is None
    assert only_free["price_median"] is None


def test_money_is_formatted_in_one_place() -> None:
    """金額の整形は sitemill の yen だけを使う（テンプレートは |yen）。"""
    from pathlib import Path as _Path

    from sitemill.build.site import yen

    assert yen(400_000) == "40万円" and yen(9_000) == "9,000円"
    for name in ("index.html", "prefecture.html", "municipality.html"):
        text = _Path("templates", name).read_text(encoding="utf-8")
        assert "price_text_" not in text, name  # 整形済み文字列は渡さない
        assert "|yen" in text, name


def _client() -> PoliteClient:
    return PoliteClient("sitemill-test/0", default_delay=0, jitter=0, sleep=lambda _s: None)


INDEX = (
    "<html><body><h1>空き家バンク</h1><ul>"
    + "".join(
        f'<li><a href="/akiya/tochio/to{800 + i}.html">物件 No.8-{i}</a></li>' for i in range(1, 6)
    )
    + '<li><a href="/akiya/akiyabank.html">空き家バンクとは</a></li>'
    "</ul></body></html>"
)
DETAIL = (
    "<html><body><h1>栃尾地域の売買物件</h1>"
    "<table><tr><th>価格</th><td>250万円</td></tr>"
    "<tr><th>面積</th><td>112.36㎡</td></tr>"
    "<tr><th>築年</th><td>昭和51年</td></tr></table></body></html>"
)
NOT_DETAIL = "<html><body><h1>空き家バンクとは</h1><p>制度の説明です。</p></body></html>"


@respx.mock
def test_detect_detail_follow_finds_the_repeated_shape() -> None:
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(404))
    for i in range(1, 6):
        respx.get(f"{BASE}/akiya/tochio/to{800 + i}.html").mock(
            return_value=httpx.Response(
                200, text=DETAIL, headers={"content-type": "text/html; charset=utf-8"}
            )
        )
    respx.get(f"{BASE}/akiya/akiyabank.html").mock(
        return_value=httpx.Response(
            200, text=NOT_DETAIL, headers={"content-type": "text/html; charset=utf-8"}
        )
    )
    with _client() as c:
        found = detect_detail_follow(f"{BASE}/akiya/", INDEX, c)
    assert found is not None
    pattern, count = found
    assert count == 5
    import re

    assert re.match(pattern, f"{BASE}/akiya/tochio/to853.html")
    assert re.match(pattern, f"http://{HOST}/akiya/tochio/to853.html")  # スキームは問わない
    assert not re.match(pattern, f"{BASE}/akiya/akiyabank.html")


@respx.mock
def test_detect_detail_follow_ignores_pages_without_property_numbers() -> None:
    """案内ページが並ぶだけのサイトでは追従しない（日田市のように list が案内のことがある）。"""
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(404))
    html = (
        "<html><body><ul>"
        + "".join(f'<li><a href="/site/iju/list{i}.html">案内 {i}</a></li>' for i in range(1, 6))
        + "</ul></body></html>"
    )
    for i in range(1, 6):
        respx.get(f"{BASE}/site/iju/list{i}.html").mock(
            return_value=httpx.Response(
                200, text=NOT_DETAIL, headers={"content-type": "text/html; charset=utf-8"}
            )
        )
    with _client() as c:
        assert detect_detail_follow(f"{BASE}/site/iju/2680.html", html, c) is None
