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
    """解体案件だけを試験用の計測 URL で公開した状態。他の案件は契約前として伏せる。

    本番の案件が増えても、この試験が見るのは 1 件だけにする（枠と転送ページの対応を見たいので、
    公開中の件数に左右されないようにする）。
    """
    return tuple(
        Offer(**{**vars(o), "url": TRACKING if o.id == "kaitai-110" else None})
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


def test_score_orders_the_slot_until_someone_sets_a_rank() -> None:
    """rank を書かなければ選定の点数で並ぶ。数値を入れたらそちらが勝つ（ADR 0010）。"""
    base = next(o for o in affiliates.OFFERS if o.id == "kaitai-110")
    high = Offer(**{**vars(base), "id": "kaitai-high", "rank": None, "score": 86, "url": TRACKING})
    low = Offer(**{**vars(base), "id": "kaitai-low", "rank": None, "score": 61, "url": TRACKING})
    assert high.order < low.order  # 点数が高い方が前
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(affiliates, "OFFERS", (low, high))
        assert [o.id for o in affiliates.offers_for("owners-flow-demolition")] == ["kaitai-high"]
        # 実績を見て低い点数の方を前に出したくなったら rank で上書きする
        mp.setattr(affiliates, "OFFERS", (Offer(**{**vars(low), "rank": 1}), high))
        assert [o.id for o in affiliates.offers_for("owners-flow-demolition")] == ["kaitai-low"]


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


def test_check_reads_a_tracking_url_with_several_parameters(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """計測 URL に & が複数あると href は &amp; になる。検査は同じ URL として読む。

    A8 の URL には & が無いので気づかなかった。もしもの URL で最初に出た。
    """
    tracking = "https://af.moshimo.com/af/c/click?a_id=1&p_id=2&pc_id=3&pl_id=4"
    base = next(o for o in affiliates.OFFERS if o.id == "katazuke-center")
    monkeypatch.setattr(affiliates, "OFFERS", (Offer(**{**vars(base), "url": tracking}),))
    commands.cmd_build(commands.Runtime(ws=ws, service=service))

    go = (ws.dist_dir / "go/katazuke-center/owners-consult/index.html").read_text(encoding="utf-8")
    assert "&amp;p_id=2" in go  # HTML としては escape されている
    problems, summary = ad_check.check(ws.dist_dir)
    assert problems == [] and "katazuke-center" in summary


def test_a_region_limited_offer_shows_only_on_its_prefecture(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """対応地域を持つ案件は、その県の所有者向けページにだけ出る（ADR 0012）。"""
    local = Offer(
        id="katazuke-nagano",
        label="長野の空き家片付け",
        kind=affiliates.KIND_KATAZUKE,
        description="県内の片づけ・残置物撤去の相談先。",
        asp="a8",
        advertiser="テスト事業者",
        url=TRACKING,
        regions=("長野県",),
        region_quote="長野県内のみ対応",
        placements=("owners-pref-consult",),
    )
    monkeypatch.setattr(affiliates, "OFFERS", (local,))
    commands.cmd_build(commands.Runtime(ws=ws, service=service))
    dist = ws.dist_dir

    pref = (dist / "owners/nagano/index.html").read_text(encoding="utf-8")
    assert "/go/katazuke-nagano/owners-pref-consult/" in pref
    assert "data-ad-notice" in pref  # 広告が出るページには表記を出す
    nationwide = (dist / "owners/index.html").read_text(encoding="utf-8")
    assert "/go/katazuke-nagano/" not in nationwide  # 全国のページには出さない
    assert "data-ad-notice" not in nationwide

    problems, _ = ad_check.check(dist)
    assert problems == []
    urls = [u for _, u in ad_check.ad_urls(dist, "https://akiya-atlas.com")]
    assert "https://akiya-atlas.com/owners/nagano/" in urls


def test_offers_for_prefers_a_local_offer_over_a_nationwide_one() -> None:
    """同じ種別なら、その県の案件を全国対応より先に採る（ADR 0012）。"""
    base = next(o for o in affiliates.OFFERS if o.id == "katazuke-center")
    everywhere = Offer(**{**vars(base), "id": "zenkoku", "url": TRACKING, "score": 90})
    local = Offer(
        **{
            **vars(base),
            "id": "nagano-only",
            "url": TRACKING,
            "score": 40,
            "regions": ("長野県",),
            "placements": ("owners-pref-consult",),
        }
    )
    everywhere = Offer(**{**vars(everywhere), "placements": ("owners-pref-consult",)})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(affiliates, "OFFERS", (everywhere, local))
        chosen = affiliates.offers_for("owners-pref-consult", pref="長野県")
        assert [o.id for o in chosen] == ["nagano-only"]  # 点数が低くても地元が先
        other = affiliates.offers_for("owners-pref-consult", pref="愛知県")
        assert [o.id for o in other] == ["zenkoku"]  # 対象外の県では全国対応に落ちる


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
