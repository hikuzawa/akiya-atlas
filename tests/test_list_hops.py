"""案内ページから物件一覧へ 2 段まで辿る（奥多摩町、2026-09-27）。

奥多摩町の空家バンクは、案内（メニューだけ）→「空家バンク登録物件一覧」（リンクが 1 本ある
だけのページ）→ 物件一覧（CGI、17 件）の 2 段だった。発見は 1 段しか辿らず、しかも案内の
メニューの「0円空家バンク」を価格、メニューの項目を物件行と数えて「一覧らしい」と見ていたので、
辿りもせずに案内を巡回先にした。本文が 127 文字しかないので抽出は毎晩 0 件で、heal は
「本文を取り出せないページ」として 09-11 から保留し続けた。
"""

from __future__ import annotations

import httpx
import pytest
import respx
from sitemill.classify import PageClass, PlatformRegistry
from sitemill.fetch.client import PoliteClient

from akiya_atlas import expand
from akiya_atlas.official_domains import classify_host

HOST = "www.town.kakuu.tokyo.jp"
BASE = f"https://{HOST}"
HUB = f"{BASE}/iju/akiya/index.html"
MID = f"{BASE}/iju/akiya/list.html"
LIST = f"{BASE}/cgi-bin/bukken.php/1/list?page_no=10"

HUB_HTML = (
    "<html><head><title>空家バンク</title></head><body><ul>"
    "<li><a href='/iju/akiya/about.html'>空家バンクとは</a></li>"
    "<li><a href='/iju/akiya/list.html'>空家バンク登録物件一覧</a></li>"
    "<li><a href='/iju/akiya/buy/index.html'>空家を買う・借りる</a></li>"
    "<li><a href='/iju/akiya/zero.html'>0円空家バンク</a></li>"
    "</ul></body></html>"
)
MID_HTML = (
    "<html><head><title>空家バンク登録物件一覧</title></head><body>"
    "<h1>空家バンク登録物件一覧</h1>"
    "<p><a href='/cgi-bin/bukken.php/1/list?page_no=10'>空家バンク登録物件一覧</a></p>"
    "</body></html>"
)


def _item(no: int, price: str, place: str, land: str) -> str:
    return (
        f"<div><h3><a href='/cgi-bin/bukken.php/1/detail/{no}'>物件番号:{no}</a></h3>"
        f"<dl><dt>契約分類</dt><dd>売買物件</dd><dt>価格</dt><dd>{price}</dd>"
        f"<dt>所在地</dt><dd>架空町{place}</dd><dt>敷地面積等</dt><dd>土地 {land}m2</dd></dl></div>"
    )


LIST_HTML = (
    "<html><head><title>検索結果</title></head><body>"
    + _item(11, "230万円", "一丁目1番", "195.04")
    + _item(12, "430万円", "二丁目2番", "141.81")
    + _item(13, "200万円", "三丁目3番", "152.06")
    + _item(14, "550万円", "四丁目4番", "285.86")
    + "</body></html>"
)


def _client() -> PoliteClient:
    return PoliteClient("sitemill-test/0", default_delay=0, jitter=0, sleep=lambda _s: None)


def _mock(pages: dict[str, str]) -> dict[str, respx.Route]:
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(404))
    return {
        url: respx.get(url).mock(
            return_value=httpx.Response(
                200, text=html, headers={"content-type": "text/html; charset=utf-8"}
            )
        )
        for url, html in pages.items()
    }


def _select(start: str) -> expand.BankProbe:
    cand = expand._Cand(url=start, text="空家バンク", found_on=f"{BASE}/", score=5)
    with _client() as c:
        return expand.select_bank_page([cand], classify_host(HOST, "tokyo"), c, PlatformRegistry())


@respx.mock
def test_a_bank_list_two_links_away_is_found() -> None:
    """案内 →（リンクだけのページ）→ 一覧。1 段で止めると案内が残る。"""
    _mock({HUB: HUB_HTML, MID: MID_HTML, LIST: LIST_HTML})
    assert _select(HUB).url == LIST


@respx.mock
def test_a_menu_counted_as_a_list_does_not_stop_the_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """奥多摩町の形。メニューを行と数えて「一覧」と分類された案内からも辿る。

    実物は、メニューの「0円空家バンク」が価格 1 件、メニューの項目が物件行 2 と数えられて
    一覧に分類された。その分類を、案内ページにだけ当てる。
    """
    real = expand.classify_page
    as_listing = real(LIST_HTML, LIST, platforms=PlatformRegistry())
    assert as_listing.page_class is PageClass.listing_index

    def classify(html: str, url: str, **kw):
        return as_listing if url == HUB else real(html, url, **kw)

    monkeypatch.setattr(expand, "classify_page", classify)
    _mock({HUB: HUB_HTML, MID: MID_HTML, LIST: LIST_HTML})
    assert _select(HUB).url == LIST


@respx.mock
def test_a_real_small_list_is_not_left_for_a_linked_list() -> None:
    """本文のある一覧からは辿らない。

    物件番号の無い 2 件の一覧は物件の手がかりが足りないが、「成約済み物件一覧」へは移らない。
    """
    small = f"{BASE}/iju/akiya/bukken.html"
    sold = f"{BASE}/iju/akiya/sold.html"
    small_html = (
        "<html><head><title>空き家バンク物件一覧</title></head><body><h1>空き家バンク物件一覧</h1>"
        "<p>町が運営する空き家バンクに登録された物件です。内覧のお申し込みは、利用登録のうえ"
        "町の担当窓口までご連絡ください。掲載内容は所有者の申告に基づきます。現地の状況と異なる"
        "場合がありますので、必ず現地をご確認ください。契約は当事者間で行っていただきます。</p>"
        "<table><tr><th>所在地</th><th>価格</th><th>面積</th></tr>"
        "<tr><td>架空町一丁目</td><td>300万円</td><td>土地 200.5m2</td></tr>"
        "<tr><td>架空町二丁目</td><td>450万円</td><td>土地 180.2m2</td></tr></table>"
        "<p><a href='/iju/akiya/sold.html'>成約済み物件一覧</a></p></body></html>"
    )
    routes = _mock({small: small_html, sold: LIST_HTML})
    assert _select(small).url == small
    assert not routes[sold].called
