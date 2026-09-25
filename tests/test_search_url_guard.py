"""サイト内検索の結果・検索画面を巡回先にしない歯止め（2026-09-26）。

「検索フォーム型は自動で検索を送らず、リンクのみにする」方針は上書きファイルで自治体ごとに
決めていたが、発見のコードには歯止めが無く、GET の検索結果 URL を巡回先に採っていた。
常総市は物件一覧が `search.php?keyword=空き家`（掲載中の 3 件はすべて検索結果の抜粋から）、
当麻町は補助制度の巡回先が `/search/node?keys=住宅補助`（相手の robots.txt が拒否し、一度も
取得できていない）。検索結果を巡回するのは、検索を自動で送るのと同じこと。
"""

from __future__ import annotations

import httpx
import pytest
import respx
from sitemill.fetch.client import PoliteClient

from akiya_atlas import expand

SEARCH_URLS = [
    # 巡回先に採っていたもの
    "https://www.city.joso.lg.jp/search.php?keyword=%E7%A9%BA%E3%81%8D%E5%AE%B6",
    "https://www.town.tohma.hokkaido.jp/search/node?keys=住宅補助",
    # 物件一覧の場所として持っていたもの（link_only なので巡回はしていない）
    "https://www.town.urakawa.hokkaido.jp/gyosei/search/?q=空き家",
    "https://www2.town.asahi.mie.jp/search3/ftsservlet?keyword=空家&btnG=検索",
    "https://www.vill.matsukawa.nagano.jp/search/?q=空き家バンク",
    # 検索画面（上書きファイルで「検索フォームへの案内」と決めているもの）
    "https://www.city.nakama.lg.jp/akiya/search/search.php",
    # よくある形
    "https://example.lg.jp/?s=空き家",
    "https://example.lg.jp/kensaku/result.html?word=空き家",
]
NOT_SEARCH_URLS = [
    "https://www.city.aki.kochi.jp/life/dtl.php?hdnKey=1234",
    "https://akiya.city.tomi.nagano.jp/",
    "https://www.city.example.lg.jp/akiya/list.html?page=2",  # ページ送り
    "https://www.city.example.lg.jp/akiya/list.php?area=3&type=1",  # 絞り込み
    "https://www.town.tohma.hokkaido.jp/recommend-06",
    "https://www.city.nakama.lg.jp/soshiki/19/1272.html",
    "https://www.city.example.lg.jp/researcher/index.html",  # search を含む別の語
]


@pytest.mark.parametrize("url", SEARCH_URLS)
def test_search_urls_are_recognised(url: str) -> None:
    assert expand.is_site_search_url(url), url


@pytest.mark.parametrize("url", NOT_SEARCH_URLS)
def test_ordinary_pages_are_not_mistaken_for_search(url: str) -> None:
    assert not expand.is_site_search_url(url), url


HOST = "www.town.kakuu.hokkaido.jp"
BASE = f"https://{HOST}"


def _client() -> PoliteClient:
    return PoliteClient("sitemill-test/0", default_delay=0, jitter=0, sleep=lambda _s: None)


def _mock(routes: dict[str, str]) -> None:
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(404))
    for path, html in routes.items():
        respx.get(f"{BASE}{path}").mock(
            return_value=httpx.Response(
                200, text=html, headers={"content-type": "text/html; charset=utf-8"}
            )
        )


@respx.mock
def test_a_search_link_is_not_taken_as_a_subsidy_page() -> None:
    """当麻町の形。「住宅補助」のタグが検索結果を指していても、制度のページとして拾わない。"""
    _mock(
        {
            "/": (
                "<html><body>"
                "<a href='/search/node?keys=住宅補助'>住宅補助</a>"
                "<a href='/kurashi/akiya-hojo.html'>空き家改修補助金</a>"
                "</body></html>"
            ),
        }
    )
    with _client() as c:
        found = expand.find_subsidy_pages([BASE + "/"], c)
    urls = [u for u, _ in found]
    assert BASE + "/kurashi/akiya-hojo.html" in urls
    assert not any(expand.is_site_search_url(u) for u in urls)


@respx.mock
def test_a_search_link_is_not_a_bank_candidate() -> None:
    """常総市の形。「空き家」のリンクが検索結果を指していても、空き家バンクの候補にしない。"""
    from akiya_atlas.official_domains import classify_host

    _mock(
        {
            "/": (
                "<html><head><title>架空町</title></head><body>"
                "<a href='/search.php?keyword=空き家'>空き家</a>"
                "<a href='/akiya/list.html'>空き家バンク 物件一覧</a>"
                "</body></html>"
            ),
        }
    )
    for path in ("/sitemap.xml", "/sitemap_index.xml"):
        respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(404))
    official = classify_host(HOST, "hokkaido")
    with _client() as c:
        cands = expand._collect_bank_candidates(BASE + "/", official, c)
    urls = [cand.url for cand in cands]
    assert BASE + "/akiya/list.html" in urls
    assert not any(expand.is_site_search_url(u) for u in urls)


def test_no_committed_seed_is_a_site_search() -> None:
    """発見の歯止めを通らない経路（上書きファイル・手作業）で戻っていないことを、
    コミット済みのデータで確かめる（ADR 0016 の考え方）。

    物件一覧の場所（bank_url）は見ない。検索フォームしか無い自治体は、上書きファイルで
    「検索フォームへの案内」と決めて、フォームの URL を載せている（中間市・国東市）。
    巡回先（pages）に検索が入るのだけが、検索を自動で送ることになる。
    """
    from pathlib import Path

    from sitemill.settings import Workspace

    from akiya_atlas.data import load_sources

    root = Path(__file__).resolve().parents[1]
    ws = Workspace.open(root)
    bad = [
        (source.id, page.url)
        for source in load_sources(ws)
        for page in source.pages
        if expand.is_site_search_url(page.url)
    ]
    assert not bad, bad
