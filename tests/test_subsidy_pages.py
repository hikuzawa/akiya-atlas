"""補助制度ページの探索（1 段だけ辿る・市町村単位で並列）のテスト。ネットワークは respx でモック。

全国 1,741 市町村ぶんを手元の PC で回す経路なので、並列でも findings の行が入れ替わらないこと、
運営主体を判定できていない（pending）自治体には触れないことを、ここで押さえる。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
import respx
from sitemill.fetch.client import PoliteClient
from sitemill.settings import Workspace

from akiya_atlas import expand

REPO = Path(__file__).resolve().parents[1]
HOST_A = "www.town.aaa.nagano.jp"
HOST_B = "www.town.bbb.nagano.jp"
HOST_C = "www.town.ccc.nagano.jp"

# A: 空き家バンクのページから直接「補助金」へ
BANK_A = (
    "<html><body><h1>空き家バンク</h1>"
    "<a href='/akiya/hojo.html'>空き家改修補助金</a>"
    "<a href='/akiya/youkou.pdf'>空き家改修補助金交付要綱</a>"  # 文書は拾わない
    "<a href='https://other.example/hojo'>移住支援補助金</a>"  # 別ホストは拾わない
    "<a href='/kurashi/gomi.html'>ごみの出し方</a>"  # 話題が合わない
    "</body></html>"
)
# B: 公式トップ →「住まい」の分類ページ → 補助金（1 段だけ辿る）
TOP_B = "<html><body><a href='/kurashi/sumai/'>住まい・空き家</a></body></html>"
SUMAI_B = (
    "<html><body><h1>住まい</h1>"
    "<a href='/kurashi/sumai/kaitai.html'>老朽空き家解体費補助金</a>"
    "<a href='/kurashi/sumai/shinsei/'>申請の手引き</a>"  # 2 段目は辿らない
    "</body></html>"
)


def _mock() -> None:
    for host in (HOST_A, HOST_B, HOST_C):
        respx.get(f"https://{host}/robots.txt").mock(return_value=httpx.Response(404))
    pages = {
        f"https://{HOST_A}/akiya/": BANK_A,
        f"https://{HOST_A}/": BANK_A,
        f"https://{HOST_B}/": TOP_B,
        f"https://{HOST_B}/kurashi/sumai/": SUMAI_B,
    }
    for url, html in pages.items():
        respx.get(url).mock(
            return_value=httpx.Response(
                200, text=html, headers={"content-type": "text/html; charset=utf-8"}
            )
        )


def _client() -> PoliteClient:
    return PoliteClient("sitemill-test/0", default_delay=0, jitter=0, sleep=lambda _s: None)


def _row(code: str, name: str, host: str, bank_path: str, policy: str = "link_only") -> dict:
    return {
        "code": code,
        "name": name,
        "name_kana": "カクウマチ",
        "prefecture": "長野県",
        "prefecture_slug": "nagano",
        "official_url": host,
        "bank_url": f"https://{host}{bank_path}" if bank_path else "",
        "page_class": "not_listing",
        "confidence": 0.8,
        "operator_kind": "municipality",
        "evidence_quote": "公式ドメイン",
        "evidence_url": f"https://{host}/",
        "cross_linked": False,
        "policy": policy,
        "reason": "",
        "proposed_action": "承認",
        "external_links": [],
    }


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    shutil.copy(REPO / "site.toml", tmp_path / "site.toml")
    for d in ("data/sources", "data/state", "data/records", "data/runs", "data/reference"):
        (tmp_path / d).mkdir(parents=True)
    shutil.copy(
        REPO / "data" / "reference" / "municipal_codes.csv",
        tmp_path / "data" / "reference" / "municipal_codes.csv",
    )
    return Workspace.open(tmp_path)


@respx.mock
def test_finds_subsidy_link_on_the_bank_page_and_ignores_documents_and_other_hosts() -> None:
    _mock()
    with _client() as c:
        found = expand.find_subsidy_pages([f"https://{HOST_A}/akiya/", f"https://{HOST_A}/"], c)
    assert [u for u, _ in found] == [f"https://{HOST_A}/akiya/hojo.html"]


@respx.mock
def test_follows_one_hop_through_a_housing_category_page() -> None:
    _mock()
    with _client() as c:
        found = expand.find_subsidy_pages(["", f"https://{HOST_B}/"], c)
    assert [u for u, _ in found] == [f"https://{HOST_B}/kurashi/sumai/kaitai.html"]


@respx.mock
def test_collect_writes_urls_in_row_order_and_skips_pending(ws: Workspace) -> None:
    _mock()
    rows = [
        _row("203033", "架空A町", HOST_A, "/akiya/"),
        _row("203041", "架空B町", HOST_B, ""),
        # 運営主体を判定できていない自治体は見に行かない（respx が未登録の取得で落ちる）
        _row("203050", "架空C町", HOST_C, "/akiya/", policy="pending"),
    ]
    expand._write_rows(ws, "nagano", "長野県", rows)
    with _client() as c:
        lines = expand.collect_subsidy_pages(ws, "長野県", client=c, workers=4)
    data = json.loads((ws.runs_dir / "discover-nagano-findings.json").read_text(encoding="utf-8"))
    out = {r["name"]: r.get("subsidy_urls") for r in data["findings"]}
    assert out["架空A町"] == [f"https://{HOST_A}/akiya/hojo.html"]
    assert out["架空B町"] == [f"https://{HOST_B}/kurashi/sumai/kaitai.html"]
    assert out["架空C町"] is None
    assert [r["code"] for r in data["findings"]] == ["203033", "203041", "203050"]
    assert "対象の 2 自治体" in lines[-1]


@respx.mock
def test_collect_adds_subsidy_pages_to_the_source_even_when_not_crawlable(ws: Workspace) -> None:
    """物件一覧が取れない自治体でも、補助金ページは巡回対象に入る（ADR 0013）。"""
    _mock()
    expand._write_rows(ws, "nagano", "長野県", [_row("203033", "架空A町", HOST_A, "/akiya/")])
    with _client() as c:
        expand.collect_subsidy_pages(ws, "長野県", client=c, workers=4)
    auto = (ws.sources_dir / "nagano-auto.yaml").read_text(encoding="utf-8")
    assert "kind: subsidy" in auto and "/akiya/hojo.html" in auto
    assert "policy: crawl" in auto
