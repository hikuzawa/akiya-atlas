"""候補の選択器（一覧らしさ・ページネーション・案内→一覧の追従）と自己修復（heal）のテスト。

ネットワークは respx でモックする。栄村（案内→一覧）・小川村（2 ページ目→1 ページ目）・
小布施町/坂出市（補助金ページの誤採用→info）を合成 HTML で再現する。
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from sitemill.classify import PlatformRegistry
from sitemill.diff.state import CrawlState
from sitemill.fetch.client import PoliteClient
from sitemill.settings import Workspace

from akiya_atlas import expand
from akiya_atlas.official_domains import classify_host

REPO = Path(__file__).resolve().parents[1]
HOST = "www.city.kakuu.nagano.jp"
BASE = f"https://{HOST}"
OFFICIAL = classify_host(HOST, "nagano")
SID = "nagano-209999"


def _rows(n: int, start: int = 1, closed: bool = False) -> str:
    mark = "【ご成約済】" if closed else ""
    return "".join(
        f"<tr><td>No.{i}</td><td>{mark}古民家{i} {i}50万円</td>"
        f"<td>木造 {70 + i}㎡ 築{30 + i}年</td><td>大字{i}地区</td></tr>"
        for i in range(start, start + n)
    )


TOP = (
    "<html><body><a href='/akiya/hojo.html'>空き家改修補助金</a>"
    "<a href='/akiya/guide.html'>空き家バンク</a></body></html>"
)
LIST = (
    "<html><body><h1>空き家バンク登録物件一覧</h1><table>"
    "<tr><th>番号</th><th>価格</th><th>建物</th><th>所在地</th></tr>"
    f"{_rows(5)}</table><a href='/akiya/list.html?page=2'>次へ</a></body></html>"
)
LIST_P2 = (
    "<html><body><h1>空き家バンク登録物件一覧</h1><table>"
    "<tr><th>番号</th><th>価格</th><th>建物</th><th>所在地</th></tr>"
    f"{_rows(3, start=6, closed=True)}</table><a href='/akiya/list.html'>前へ</a></body></html>"
)
HOJO = (
    "<html><body><h1>空き家改修補助金</h1><p>補助率 2 分の 1、上限額 50万円。加算で最大 80万円。"
    "家財処分は上限 10万円。申請書（様式第1号）を提出。対象者・申請期間は要綱を参照。"
    "補助金の交付は予算の範囲内。</p></body></html>"
)
GUIDE_WITH_LIST = (
    "<html><body><h1>空き家バンクのご案内</h1><p>登録の流れ。補助金は上限50万円、改修は上限100万円。"
    "申請書（様式第1号）と要綱、対象者。</p>"
    "<a href='/akiya/list.html'>登録物件一覧</a><a href='/akiya/hojo.html'>改修補助金</a>"
    "</body></html>"
)
GUIDE_NO_LIST = GUIDE_WITH_LIST.replace("<a href='/akiya/list.html'>登録物件一覧</a>", "")


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


SITE = {
    "/": TOP,
    "/akiya/hojo.html": HOJO,
    "/akiya/guide.html": GUIDE_WITH_LIST,
    "/akiya/list.html": LIST,
    "/akiya/list.html?page=2": LIST_P2,
}


@respx.mock
def test_selector_prefers_listing_over_guide_and_subsidy() -> None:
    _mock(SITE)
    with _client() as c:
        probe = expand.find_bank_page(BASE + "/", OFFICIAL, c, PlatformRegistry())
    assert probe.url == BASE + "/akiya/list.html"
    assert probe.classified is not None and probe.classified.page_class.value == "listing_index"
    assert probe.listing is not None and probe.listing.rows == 5
    assert probe.pagination_pattern and probe.host_official
    assert any("guide.html" in u or "hojo.html" in u for u in probe.alternatives)


@respx.mock
def test_selector_moves_from_page2_to_first_page() -> None:
    _mock({"/akiya/list.html": LIST, "/akiya/list.html?page=2": LIST_P2})
    cands = [
        expand._Cand(
            url=BASE + "/akiya/list.html?page=2", text="空き家情報", found_on=BASE, score=6
        )
    ]
    with _client() as c:
        probe = expand.select_bank_page(cands, OFFICIAL, c, PlatformRegistry())
    assert probe.url == BASE + "/akiya/list.html"
    assert probe.listing is not None and probe.listing.pagination.current_page == 1


@respx.mock
def test_selector_reports_subsidy_only_site_as_not_listing() -> None:
    _mock({"/": TOP, "/akiya/hojo.html": HOJO, "/akiya/guide.html": GUIDE_NO_LIST})
    with _client() as c:
        probe = expand.find_bank_page(BASE + "/", OFFICIAL, c, PlatformRegistry())
    assert probe.classified is not None and probe.classified.page_class.value == "not_listing"


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    shutil.copy(REPO / "site.toml", tmp_path / "site.toml")
    for d in ("data/sources", "data/state", "data/records", "data/runs", "data/reference"):
        (tmp_path / d).mkdir(parents=True)
    return Workspace.open(tmp_path)


def _row(bank_url: str) -> dict:
    return {
        "code": "209999",
        "name": "架空市",
        "name_kana": "カクウシ",
        "prefecture": "長野県",
        "prefecture_slug": "nagano",
        "official_url": HOST,
        "bank_url": bank_url,
        "page_class": "listing_index",
        "confidence": 0.8,
        "operator_kind": "municipality",
        "evidence_quote": "公式ドメイン",
        "evidence_url": BASE + "/",
        "cross_linked": False,
        "policy": "crawl",
        "reason": "",
        "proposed_action": "承認",
        "external_links": [],
    }


def _mark_crawled(ws: Workspace, url: str) -> None:
    st = CrawlState()
    st.get_or_create(url, SID, "listing_index").fetched_at = datetime.now(UTC)
    st.save(ws.state_dir / "crawl.json")


@respx.mock
def test_heal_swaps_wrongly_adopted_subsidy_page_for_the_listing(ws: Workspace) -> None:
    _mock(SITE)
    wrong = BASE + "/akiya/hojo.html"
    expand._write_rows(ws, "nagano", "長野県", [_row(wrong)])  # 誤採用の状態を再現
    _mark_crawled(ws, wrong)  # 巡回済みだがレコード 0 件
    with _client() as c:
        result = expand.heal(ws, client=c)
    assert result["checked"] == 1 and result["recrawl"] == [SID] and not result["downgraded"]
    data = json.loads((ws.runs_dir / "discover-nagano-findings.json").read_text(encoding="utf-8"))
    row = data["findings"][0]
    assert row["bank_url"] == BASE + "/akiya/list.html" and row["pagination_pattern"]
    auto = (ws.sources_dir / "nagano-auto.yaml").read_text(encoding="utf-8")
    assert "/akiya/list.html" in auto and "follow" in auto and "listing_index" in auto
    assert CrawlState.load(ws.state_dir / "crawl.json").get(wrong) is None  # 古い状態は消える


@respx.mock
def test_heal_downgrades_to_info_when_no_listing_exists(ws: Workspace) -> None:
    _mock({"/": TOP, "/akiya/hojo.html": HOJO, "/akiya/guide.html": GUIDE_NO_LIST})
    wrong = BASE + "/akiya/hojo.html"
    expand._write_rows(ws, "nagano", "長野県", [_row(wrong)])
    _mark_crawled(ws, wrong)
    with _client() as c:
        result = expand.heal(ws, client=c)
    assert result["checked"] == 1 and result["downgraded"] == [SID] and not result["recrawl"]
    data = json.loads((ws.runs_dir / "discover-nagano-findings.json").read_text(encoding="utf-8"))
    row = data["findings"][0]
    assert row["policy"] == "link_only" and row["page_class"] == "not_listing"
    auto = (ws.sources_dir / "nagano-auto.yaml").read_text(encoding="utf-8")
    assert "bank_status: info" in auto and "policy: link_only" in auto


@respx.mock
def test_heal_skips_sources_with_active_listings_or_not_yet_crawled(ws: Workspace) -> None:
    _mock(SITE)
    expand._write_rows(ws, "nagano", "長野県", [_row(BASE + "/akiya/list.html")])
    with _client() as c:  # 巡回記録が無い → 対象外
        result = expand.heal(ws, client=c)
    assert result["checked"] == 0 and not result["changed"]


def test_two_municipalities_on_one_official_site_go_to_human_review() -> None:
    """北海道には泊村が 2 つ（古宇郡・国後郡）。候補ドメインの推測は同じ URL に当たる。

    片方に相手のサイトを結び付けて公開しないよう、両方を人間確認に回す。
    """
    from sitemill.models import OperatorKind

    from akiya_atlas.expand import MunicipalityFinding, _flag_shared_official_urls
    from akiya_atlas.municipalities import MunicipalityRef

    def _f(code: str, name: str, url: str) -> MunicipalityFinding:
        return MunicipalityFinding(
            muni=MunicipalityRef(
                code=code,
                prefecture="北海道",
                prefecture_slug="hokkaido",
                name=name,
                name_kana="",
            ),
            official_url=url,
            bank_url=url,
            operator_kind=OperatorKind.municipality,
            evidence_quote="公式ドメイン",
            policy="link_only",
            confidence=0.6,
        )

    findings = [
        _f("014036", "泊村", "https://www.vill.tomari.hokkaido.jp/"),
        _f("016969", "泊村", "https://www.vill.tomari.hokkaido.jp/"),
        _f("012025", "函館市", "https://www.city.hakodate.hokkaido.jp/"),
    ]
    _flag_shared_official_urls(findings)
    assert [f.policy for f in findings] == ["pending", "pending", "link_only"]
    assert findings[0].bank_url is None and "取り違え" in findings[0].reason
    assert findings[2].bank_url == "https://www.city.hakodate.hokkaido.jp/"
