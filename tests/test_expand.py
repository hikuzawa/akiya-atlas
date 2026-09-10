"""発見の意思決定ロジック（純関数 decide）と Source/Candidate 変換のテスト。ネットワーク不要。"""

from sitemill.classify import ClassifiedPage, PageClass
from sitemill.models import CrawlPolicy, OperatorKind

from akiya_atlas.expand import decide, finding_to_candidate, finding_to_source
from akiya_atlas.municipalities import MunicipalityRef
from akiya_atlas.official_domains import classify_host

TOMI = MunicipalityRef(
    code="202193",
    prefecture="長野県",
    prefecture_slug="nagano",
    name="東御市",
    name_kana="トウミシ",
)
OFFICIAL = classify_host("www.city.tomi.nagano.jp", "nagano")  # geographic_municipal, official


def _cp(pc: PageClass, url="https://akiya.example/", conf=0.8) -> ClassifiedPage:
    return ClassifiedPage(url=url, page_class=pc, confidence=conf)


def test_no_official_url_goes_pending_for_url_fix() -> None:
    f = decide(TOMI, None, None, cross_linked=False, bank_host_official=False)
    assert f.policy == "pending" and f.proposed_action == "URL修正"


def test_official_but_no_bank_page_is_pending_no_bank() -> None:
    f = decide(TOMI, OFFICIAL, None, cross_linked=False, bank_host_official=False)
    assert f.policy == "pending" and "掲載なし" in f.reason


def test_official_domain_static_listing_auto_crawl() -> None:
    f = decide(
        TOMI, OFFICIAL, _cp(PageClass.listing_index), cross_linked=False, bank_host_official=True
    )
    assert f.policy == "crawl" and f.operator_kind is OperatorKind.municipality
    assert f.confidence >= 0.7 and "自治体ドメイン" in f.reason


def test_cross_linked_third_party_host_but_listing_is_municipality() -> None:
    # 掲載は非公式ホストだが、公式サイトから空き家バンクとして案内 → 相互リンクで自治体運営
    f = decide(
        TOMI,
        OFFICIAL,
        _cp(PageClass.listing_index, url="https://39ijyu.com/all.php"),
        cross_linked=True,
        bank_host_official=False,
    )
    assert f.policy == "crawl" and f.operator_kind is OperatorKind.municipality
    assert "案内されている" in (f.evidence_quote or "")


def test_third_party_platform_is_link_only() -> None:
    f = decide(
        TOMI, OFFICIAL, _cp(PageClass.third_party), cross_linked=False, bank_host_official=False
    )
    assert f.policy == "link_only" and "民間" in f.reason


def test_spa_is_link_only() -> None:
    f = decide(TOMI, OFFICIAL, _cp(PageClass.spa), cross_linked=False, bank_host_official=False)
    assert f.policy == "link_only" and "JavaScript" in f.reason


def test_listing_without_operator_evidence_is_pending() -> None:
    f = decide(
        TOMI,
        OFFICIAL,
        _cp(PageClass.listing_index, url="https://random.example/"),
        cross_linked=False,
        bank_host_official=False,
    )
    assert f.policy == "pending" and f.operator_kind is OperatorKind.unknown


def test_finding_to_source_only_for_crawl() -> None:
    crawl = decide(
        TOMI, OFFICIAL, _cp(PageClass.listing_index), cross_linked=False, bank_host_official=True
    )
    src = finding_to_source(crawl, existing_ids=set())
    assert src is not None and src.policy is CrawlPolicy.crawl
    assert src.id == "nagano-202193" and src.operator_evidence.checked_on is not None
    assert finding_to_source(crawl, existing_ids={"nagano-202193"}) is None  # 既存はスキップ
    link = decide(
        TOMI, OFFICIAL, _cp(PageClass.third_party), cross_linked=False, bank_host_official=False
    )
    assert finding_to_source(link, existing_ids=set()) is None


def test_finding_to_candidate() -> None:
    f = decide(TOMI, None, None, cross_linked=False, bank_host_official=False)
    c = finding_to_candidate(f)
    assert c.key == "202193" and c.label == "長野県 東御市" and c.proposed_action == "URL修正"
    assert c.proposed_policy == "pending"
