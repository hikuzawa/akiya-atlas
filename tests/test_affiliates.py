"""広告の掲載方針（ADR 0010）。枠の選び方と、ビルド時の検査が実際に止めることを確かめる。

本番の案件は計測 URL が入るまで `ready` が偽なので、ここでは差し替えた案件でビルドする。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sitemill import commands
from sitemill.settings import Workspace

from akiya_atlas import ad_check, affiliates
from akiya_atlas.affiliates import Asp, Offer
from akiya_atlas.schema import Listing, record_id_for
from akiya_atlas.service import service

REPO = Path(__file__).resolve().parents[1]
TRACKING = "https://px.a8.net/svt/ejp?a8mat=TEST0001"

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
    municipality:
      code: "202193"
      name: 東御市
      prefecture: 長野県
      prefecture_slug: nagano
      slug: 202193-tomi
      bank_url: https://akiya.city.tomi.nagano.jp/
"""


def _ready_offers() -> tuple[Offer, ...]:
    """本番の解体案件の計測 URL を、試験用の値に差し替えたもの。"""
    return tuple(
        Offer(**{**vars(o), "url": TRACKING}) if o.id == "kaitai-110" else o
        for o in affiliates.OFFERS
    )


def _pending_offers() -> tuple[Offer, ...]:
    """どの案件も契約前の状態（計測 URL が無い）。"""
    return tuple(Offer(**{**vars(o), "url": None}) for o in affiliates.OFFERS)


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
    row = Listing(
        record_id=record_id_for("nagano-tomi", "322"),
        source_id="nagano-tomi",
        municipality_code="202193",
        listing_no="322",
        source_url="https://example.invalid/nagano-tomi/322",
        first_seen_at="2026-09-01T00:00:00+00:00",
        last_seen_at="2026-09-10T00:00:00+00:00",
        deal_type="sale",
        title="原口の木造住宅",
        summary="東御市原口の住宅。",
    ).model_dump(mode="json")
    (tmp_path / "data" / "records" / "nagano-tomi.jsonl").write_text(
        json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    w = Workspace.open(tmp_path)
    w.ensure_dirs()
    return w


def test_one_offer_per_kind_in_a_slot() -> None:
    """同じ種別が複数あっても、枠に出るのは rank の小さい 1 件だけ（ADR 0010）。"""
    base = next(o for o in affiliates.OFFERS if o.id == "kaitai-110")
    other = Offer(**{**vars(base), "id": "kaitai-other", "label": "別の解体見積", "rank": 5})
    with pytest.MonkeyPatch.context() as mp:
        pending = (Offer(**{**vars(base), "url": None}), Offer(**{**vars(other), "url": None}))
        mp.setattr(affiliates, "OFFERS", pending)
        assert affiliates.offers_for("owners-flow-demolition") == []  # 契約前は出ない
        mp.setattr(affiliates, "OFFERS", (base, Offer(**{**vars(other), "url": TRACKING})))
        chosen = affiliates.offers_for("owners-flow-demolition")
        assert [o.id for o in chosen] == ["kaitai-other"]  # rank 5 < 10


def test_slot_rejects_a_kind_it_does_not_accept() -> None:
    """枠に置ける種別は宣言で決まる。解体の枠に査定は入らない。"""
    satei = Offer(
        id="satei-x",
        label="査定",
        kind=affiliates.KIND_SATEI,
        description="",
        asp="a8",
        url=TRACKING,
        placements=("owners-flow-demolition",),
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(affiliates, "OFFERS", (satei,))
        assert affiliates.offers_for("owners-flow-demolition") == []
    with pytest.raises(KeyError):
        affiliates.offers_for("owners-nowhere")


def test_pending_offer_is_not_published(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    """計測 URL が入るまでは広告リンクも広告表記も出ない（ダミーリンクを置かない）。"""
    monkeypatch.setattr(affiliates, "OFFERS", _pending_offers())
    commands.cmd_build(commands.Runtime(ws=ws, service=service))
    dist = ws.dist_dir
    owners = (dist / "owners/index.html").read_text(encoding="utf-8")
    assert "準備中" in owners and "/go/" not in owners
    assert "data-ad-notice" not in owners
    assert (dist / "_redirects").read_text(encoding="utf-8") == ""
    problems, _ = ad_check.check(dist)
    assert problems == []


def test_published_offer_goes_through_a_redirect_page(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """計測 URL が入ると、宣言した枠と転送ページが揃い、検査が通る。"""
    monkeypatch.setattr(affiliates, "OFFERS", _ready_offers())
    commands.cmd_build(commands.Runtime(ws=ws, service=service))
    dist = ws.dist_dir

    owners = (dist / "owners/index.html").read_text(encoding="utf-8")
    # 宣言した 2 つの枠に出て、直リンクは無い
    assert "/go/kaitai-110/owners-consult/" in owners
    assert "/go/kaitai-110/owners-flow-demolition/" in owners
    assert "px.a8.net" not in owners
    # 広告表記は本文の冒頭（見出しの直後）で、最初の広告リンクより前
    body = owners[owners.index("<main") :]
    assert body.index("data-ad-notice") < body.index("/go/kaitai-110")
    assert body.index("data-ad-notice") < ad_check.FIRST_VIEW_CHARS
    assert 'rel="sponsored noopener"' in owners

    go = (dist / "go/kaitai-110/owners-flow-demolition/index.html").read_text(encoding="utf-8")
    assert TRACKING in go and "noindex" in go
    assert "シェアリングテクノロジー株式会社" in go and "data-ad-notice" in go
    # 計測ビーコンが送られたのを見てから転送する（ADR 0011）。打ち切りの上限も入れる
    assert "/cdn-cgi/rum" in go and "1500" in go
    # 転送ページは検索結果に出さない
    assert "/go/" not in (dist / "sitemap.xml").read_text(encoding="utf-8")
    # 枠を含まない素の導線は _redirects に残す
    assert (dist / "_redirects").read_text(encoding="utf-8").strip() == (
        f"/go/kaitai-110 {TRACKING} 302"
    )
    # 物件ページには広告を置かないので広告表記も出さない（ADR 0010）
    listing = (dist / "nagano/202193-tomi/322/index.html").read_text(encoding="utf-8")
    assert "data-ad-notice" not in listing and "/go/" not in listing

    problems, summary = ad_check.check(dist)
    assert problems == [] and "kaitai-110" in summary

    urls = ad_check.ad_urls(dist, "https://akiya-atlas.com")
    assert [u for _, u in urls] == [
        "https://akiya-atlas.com/owners/",
        "https://akiya-atlas.com/go/kaitai-110/owners-consult/",
        "https://akiya-atlas.com/go/kaitai-110/owners-flow-demolition/",
    ]


def test_check_stops_the_build_when_the_disclosure_is_missing(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """広告表記を消すとビルドが止まる（A8 の掲載規約と景表法のため）。"""
    monkeypatch.setattr(affiliates, "OFFERS", _ready_offers())
    (ws.root / "templates" / "partials" / "macros.html").write_text(
        (ws.root / "templates" / "partials" / "macros.html")
        .read_text(encoding="utf-8")
        .replace("data-ad-notice", "data-removed"),
        encoding="utf-8",
    )
    commands.cmd_build(commands.Runtime(ws=ws, service=service))
    problems, _ = ad_check.check(ws.dist_dir)
    assert any("広告表記（data-ad-notice）が無い" in p for p in problems)


def test_check_stops_the_build_on_a_direct_link_or_a_wrong_host(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ASP への直リンクと、許可外ホストへの転送はどちらも止める。"""
    monkeypatch.setattr(affiliates, "OFFERS", _ready_offers())
    commands.cmd_build(commands.Runtime(ws=ws, service=service))
    dist = ws.dist_dir
    about = dist / "about/index.html"
    about.write_text(
        about.read_text(encoding="utf-8").replace(
            "</main>", f'<a href="{TRACKING}">広告</a></main>'
        ),
        encoding="utf-8",
    )
    (dist / "_redirects").write_text(
        "/go/kaitai-110 https://evil.example.com/x 302\n", encoding="utf-8"
    )
    problems, _ = ad_check.check(dist)
    assert any("直リンク" in p for p in problems)
    assert any("許可ホスト" in p for p in problems)


def test_forbidden_phrase_of_the_asp_stops_the_build(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ASP が禁じる表現を掲載ページに書くと止まる（規約はデータとして持つ）。"""
    monkeypatch.setattr(affiliates, "OFFERS", _ready_offers())
    monkeypatch.setitem(
        affiliates.ASPS,
        "a8",
        Asp(**{**vars(affiliates.ASPS["a8"]), "forbidden_phrases": ("解体までの流れ",)}),
    )
    commands.cmd_build(commands.Runtime(ws=ws, service=service))
    problems, _ = ad_check.check(ws.dist_dir)
    assert any("禁止表現" in p for p in problems)
