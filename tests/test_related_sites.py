"""「物件を探せるサイト」（関連する民間サイト）に、選んだページ自身のメニューを入れない（2026-09-26）。

選んだ空き家バンクのページが民間プラットフォームのとき、そのページのリンクを関連サイトとして
拾っていた。「最近見た物件 0 件」「お気に入り」「1021 件」などのメニューや、自社の一般の不動産
一覧への誘導まで並んでいた（函館市・須坂市・南木曽町・下田市・足利市・真岡市と、選び直した
松川村）。関連サイトは、市町村自身のページから民間サイトへ張られたリンクだけから取る。
"""

from __future__ import annotations

import httpx
import respx
from sitemill.classify import PlatformRegistry
from sitemill.fetch.client import PoliteClient

from akiya_atlas import expand
from akiya_atlas.official_domains import classify_host


def _client() -> PoliteClient:
    return PoliteClient("sitemill-test/0", default_delay=0, jitter=0, sleep=lambda _s: None)


def _html(url: str, body: str) -> None:
    respx.get(url).mock(
        return_value=httpx.Response(
            200, text=body, headers={"content-type": "text/html; charset=utf-8"}
        )
    )


def _probe(official_host: str, pref: str, portal_page: str, portal_body: str, link_text: str):
    """市町村の公式トップが民間の空き家バンクへリンクしている形で、空き家バンクを探す。"""
    official = f"https://{official_host}"
    portal_root = portal_page.split("/", 3)
    portal_origin = "/".join(portal_root[:3])
    for origin in (official, portal_origin):
        respx.get(f"{origin}/robots.txt").mock(return_value=httpx.Response(404))
    for path in ("/sitemap.xml", "/sitemap_index.xml"):
        respx.get(f"{official}{path}").mock(return_value=httpx.Response(404))
    _html(
        f"{official}/",
        f"<html><head><title>公式</title></head><body><a href='{portal_page}'>{link_text}</a>"
        "</body></html>",
    )
    _html(portal_page, portal_body)
    with _client() as c:
        return expand.find_bank_page(
            f"{official}/", classify_host(official_host, pref), c, PlatformRegistry()
        )


@respx.mock
def test_a_portal_page_does_not_list_its_own_menu_as_related_sites() -> None:
    """楽園信州の形（県単位の民間サイト）。"""
    portal = "https://rakuen-akiya.jp"
    probe = _probe(
        "www.vill.kakuu.nagano.jp",
        "nagano",
        f"{portal}/",
        "<html><body><h1>楽園信州空き家バンク</h1>"
        f"<a href='{portal}/favorite/'>お気に入り</a>"
        f"<a href='{portal}/history/'>閲覧履歴</a>"
        f"<a href='{portal}/housesearch/all/'>1021 件</a>"
        # 自社の一般の不動産一覧への誘導。ホストは違うが、市町村の案内ではない
        "<a href='https://www.athome.co.jp/kodate/chuko/nagano/list/'>"
        "空き家バンク以外の中古一戸建てを探す (athome)</a>"
        "</body></html>",
        "楽園信州空き家バンク・空き地バンク",
    )
    assert probe.url == f"{portal}/"
    assert probe.externals == []  # メニューも、自社の一般の一覧への誘導も拾わない


@respx.mock
def test_hakodate_does_not_list_the_athome_menu_as_related_sites() -> None:
    """函館市の形（アットホームの自治体サブドメイン）。

    2026-09-26 まで、函館市の「物件を探せるサイト」には、選んだページ
    hakodate-c01202.akiya-athome.jp のメニュー（最近見た物件・検討リスト・種別ごとの検索）、
    お知らせ、物件詳細、athome.co.jp への誘導、プライバシーポリシーの 34 件が並んでいた。
    リンクの文言と行き先は、そのとき data/sources/hokkaido-auto.yaml に入っていたもの。
    """
    bank = "https://hakodate-c01202.akiya-athome.jp/"
    athome = "https://www.athome.co.jp"
    links = [
        (f"{bank}mypage/history/list/", "最近見た物件 0 件"),
        (f"{bank}mypage/favorite/list/", "検討リスト 0 件"),
        (f"{bank}mypage/hope/list/", "保存した条件 0 件"),
        (f"{bank}buy/house/area/", "戸建(売買)"),
        (f"{bank}buy/land/map/", "土地(売買)"),
        (f"{bank}rent/live/rail/", "住まい(賃貸)"),
        (f"{bank}contents/news/detail/?id=1571", "函館市空き家バンクを公開しました。"),
        (f"{bank}contents/news/list/", "もっと見る"),
        (f"{bank}bukken/detail/buy/%E5%87%BD%E9%A4%A8%E5%B8%82-47838", "akiya-athome.jp"),
        (
            f"{athome}/kodate/chuko/hokkaido/hakodate-city/list/",
            "空き家バンク以外の<br>函館市の<br>中古一戸建てを探す<br>(athome)",
        ),
        (f"{athome}/tochi/hokkaido/hakodate-city/list/", "空き家バンク以外の函館市の土地を探す"),
        (f"{bank}contents/static/privacy-policy/", "プライバシーポリシー"),
    ]
    body = "".join(f"<a href='{u}'>{t}</a>" for u, t in links)
    probe = _probe(
        "www.city.hakodate.hokkaido.jp",
        "hokkaido",
        bank,
        f"<html><body><h1>函館市空き家バンク</h1>{body}</body></html>",
        "函館市空き家バンク",
    )
    assert probe.url == bank
    assert probe.externals == []
