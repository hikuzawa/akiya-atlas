"""読みから推測した公式サイトは、その市町村の名前がページに出るときだけ採用する。

2026-09-17 に 7 自治体が、同じ読みの別の自治体の `.lg.jp` を公式サイトとして採用していた
（滋賀県湖南市と高知県香南市が `www.city.konan.lg.jp` ＝愛知県江南市、など）。当たったページには
その市町村の名前が 1 回も出ていなかった（docs/data-issues.md）。
"""

from __future__ import annotations

import httpx
import respx
from sitemill.fetch.client import PoliteClient

from akiya_atlas import expand
from akiya_atlas.municipalities import MunicipalityRef

KONAN_SHIGA = MunicipalityRef(
    code="252115", prefecture="滋賀県", prefecture_slug="shiga", name="湖南市", name_kana="コナンシ"
)


def _html(title: str, body: str = "") -> httpx.Response:
    return httpx.Response(
        200,
        text=f"<html><head><title>{title}</title></head><body>{body}</body></html>",
        headers={"content-type": "text/html; charset=utf-8"},
    )


def _client() -> PoliteClient:
    return PoliteClient("sitemill-test/0", default_delay=0, jitter=0, sleep=lambda _s: None)


def test_name_check_absorbs_spelling_variants() -> None:
    tsurugashima = MunicipalityRef(
        code="112429", prefecture="埼玉県", prefecture_slug="saitama", name="鶴ヶ島市",
        name_kana="ツルガシマシ",
    )  # fmt: skip
    assert expand.page_names_municipality("<title>鶴ケ島市ホームページ</title>", tsurugashima)
    assert expand.page_names_municipality("<p>鶴ヶ島市役所</p>", tsurugashima)
    assert not expand.page_names_municipality("<title>江南市公式ホームページ</title>", KONAN_SHIGA)
    assert not expand.page_names_municipality("", KONAN_SHIGA)
    assert not expand.page_names_municipality(None, KONAN_SHIGA)


@respx.mock
def test_a_same_reading_site_is_not_taken_and_the_prefecture_list_is_used() -> None:
    # 読みから推測した www.city.konan.lg.jp は愛知県江南市のサイト
    respx.get("https://www.city.konan.lg.jp/").mock(return_value=_html("江南市公式ホームページ"))
    respx.get("https://www.city.shiga-konan.lg.jp/").mock(return_value=_html("ホーム／湖南市"))
    respx.route().mock(return_value=httpx.Response(404))  # ほかの候補と robots.txt は無い
    overrides = expand.OfficialOverrides(
        by_code={"252115": "https://www.city.shiga-konan.lg.jp/"},
        source_url="https://www.pref.shiga.lg.jp/ab00/7739.html",
        source_name="滋賀県 県内の市町一覧",
    )
    with _client() as c:
        assert expand.resolve_official_url(KONAN_SHIGA, c) is None  # 推測は採用しない
        resolved = expand.resolve_official(KONAN_SHIGA, c, overrides)
    assert resolved is not None
    assert resolved.host.host == "www.city.shiga-konan.lg.jp"
    assert "県内の市町一覧に掲載された公式サイト" in resolved.evidence_quote


@respx.mock
def test_the_guess_is_still_taken_when_the_page_names_the_municipality() -> None:
    respx.get("https://www.city.konan.lg.jp/").mock(return_value=_html("ホーム／湖南市"))
    respx.route().mock(return_value=httpx.Response(404))
    with _client() as c:
        found = expand.resolve_official_url(KONAN_SHIGA, c)
    assert found is not None and found[1].host == "www.city.konan.lg.jp"
