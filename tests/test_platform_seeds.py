"""民間プラットフォームのページを巡回先にしない（2026-09-28）。

民間プラットフォームはリンクのみで、巡回しない（CLAUDE.md「守ること」）。ところが補助制度の
ページは空き家バンクのページからも辿って探すので、空き家バンクがプラットフォームにある足利市では、
プラットフォームのお知らせ 2 件が補助制度のページとして巡回先に入り、09-15 から毎晩取りにいって
いた（相手が 403 で断っていたので、物件や補助制度として出たものは無い）。
"""

from __future__ import annotations

import httpx
import respx
from sitemill.classify import PlatformRegistry
from sitemill.fetch.client import PoliteClient

from akiya_atlas import expand

OFFICIAL = "https://www.city.kakuu.tochigi.jp"
PORTAL = "https://kakuu-c09999.akiya-athome.jp"


def _client() -> PoliteClient:
    return PoliteClient("sitemill-test/0", default_delay=0, jitter=0, sleep=lambda _s: None)


def _html(url: str, body: str) -> respx.Route:
    return respx.get(url).mock(
        return_value=httpx.Response(
            200, text=f"<html><body>{body}</body></html>", headers={"content-type": "text/html"}
        )
    )


@respx.mock
def test_subsidy_pages_are_not_taken_from_a_platform() -> None:
    for origin in (OFFICIAL, PORTAL):
        respx.get(f"{origin}/robots.txt").mock(return_value=httpx.Response(404))
    portal = _html(
        f"{PORTAL}/",
        f"<a href='{PORTAL}/contents/news/detail/?id=1537'>空き家改修の補助金のお知らせ</a>",
    )
    _html(f"{OFFICIAL}/", "<a href='/kurashi/akiya-hojo.html'>空き家改修補助金</a>")
    with _client() as c:
        found = expand.find_subsidy_pages([f"{PORTAL}/", f"{OFFICIAL}/"], c)
    assert [u for u, _ in found] == [f"{OFFICIAL}/kurashi/akiya-hojo.html"]
    assert not portal.called  # プラットフォームには取りにいかない


def test_no_committed_seed_is_on_a_private_platform() -> None:
    """コミット済みの巡回先に、民間プラットフォームのページが無いこと。"""
    from pathlib import Path

    from sitemill.settings import Workspace

    from akiya_atlas.data import load_sources

    root = Path(__file__).resolve().parents[1]
    platforms = PlatformRegistry()
    bad = [
        (source.id, page.url)
        for source in load_sources(Workspace.open(root))
        for page in source.pages
        if platforms.is_platform(page.url)
    ]
    assert not bad, bad
