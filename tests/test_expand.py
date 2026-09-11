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


def test_official_resolved_but_no_bank_page_links_to_official() -> None:
    # 公式が解決できれば運営主体は確認済み → 人間に回さず公式へのリンクのみ
    f = decide(TOMI, OFFICIAL, None, cross_linked=False, bank_host_official=False)
    assert f.policy == "link_only" and f.operator_kind is OperatorKind.municipality
    assert "公式へのリンク" in f.reason


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
    f = decide(TOMI, OFFICIAL, _cp(PageClass.spa), cross_linked=False, bank_host_official=True)
    assert f.policy == "link_only" and "JavaScript" in f.reason


def test_off_host_non_listing_page_is_not_used_as_bank_page() -> None:
    # 公式外の非物件ページ（例: URL に aki を含む他省庁のページ）は根拠にせず、公式へのリンクのみ
    from akiya_atlas.expand import bank_status_of

    f = decide(
        TOMI,
        OFFICIAL,
        _cp(PageClass.not_listing, url="https://www.mlit.go.jp/river/wakinkyu/"),
        cross_linked=False,
        bank_host_official=False,
    )
    assert f.policy == "link_only" and f.classified is None
    assert f.bank_url == "https://www.city.tomi.nagano.jp/" and bank_status_of(f) == "none"


def test_listing_on_unverified_host_links_to_official_not_pending() -> None:
    # 公式解決済み・物件一覧が非公式ホスト・相互リンク無し → 巡回せず公式へリンク
    f = decide(
        TOMI,
        OFFICIAL,
        _cp(PageClass.listing_index, url="https://random.example/"),
        cross_linked=False,
        bank_host_official=False,
    )
    assert f.policy == "link_only" and f.operator_kind is OperatorKind.municipality


def test_not_listing_on_official_is_info_link_only() -> None:
    from akiya_atlas.expand import bank_status_of

    f = decide(
        TOMI, OFFICIAL, _cp(PageClass.not_listing), cross_linked=False, bank_host_official=True
    )
    assert f.policy == "link_only" and bank_status_of(f) == "info"


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


def _finding(policy: str, pc, muni_code="203000", host="www.city.x.nagano.jp", bank="https://x/"):  # noqa: ANN001
    from sitemill.classify import ClassifiedPage
    from sitemill.models import OperatorKind

    from akiya_atlas.expand import MunicipalityFinding
    from akiya_atlas.municipalities import MunicipalityRef

    m = MunicipalityRef(
        code=muni_code,
        prefecture="長野県",
        prefecture_slug="nagano",
        name="架空市",
        name_kana="カクウシ",
    )
    classified = ClassifiedPage(url=bank, page_class=pc, confidence=0.8) if pc else None
    return MunicipalityFinding(
        muni=m,
        official_url=host,
        bank_url=bank if pc else None,
        classified=classified,
        operator_kind=OperatorKind.municipality if pc else OperatorKind.unknown,
        evidence_quote="公式ドメイン" if pc else None,
        policy=policy,
        confidence=0.85,
    )


def test_source_dict_loads_as_source_and_municipality() -> None:
    import yaml
    from sitemill.models import Source

    from akiya_atlas.expand import bank_status_of, finding_to_source_dict
    from akiya_atlas.schema import Municipality

    cases = [
        ("crawl", PageClass.listing_index, "available"),
        ("link_only", PageClass.third_party, "third_party_only"),
        ("link_only", PageClass.spa, "spa_unsupported"),
    ]
    for policy, pc, expected_status in cases:
        f = _finding(policy, pc)
        assert bank_status_of(f) == expected_status
        entry = finding_to_source_dict(f, prefecture_name="長野県")
        # yaml 経由で Source として妥当
        loaded = yaml.safe_load(yaml.safe_dump({"sources": [entry]}, allow_unicode=True))
        src = Source.model_validate(loaded["sources"][0])
        assert src.id == "nagano-203000"
        assert src.crawlable == (policy == "crawl")
        # municipality ブロックも妥当
        muni_payload = {
            "id": src.id,
            "official_url": entry["official_url"],
            **entry["municipality"],
        }
        muni = Municipality.model_validate(muni_payload)
        assert muni.bank_status == expected_status
        assert muni.crawled == (expected_status == "available")


def test_no_bank_case_is_none_status() -> None:
    # 公式解決済み・バンク未特定 → link_only（公式へリンク）・bank_status=none
    from akiya_atlas.expand import bank_status_of

    f = decide(TOMI, OFFICIAL, None, cross_linked=False, bank_host_official=False)
    assert f.policy == "link_only" and bank_status_of(f) == "none"


def test_nagano_no_crawl_gets_rakuen_link() -> None:
    from akiya_atlas.expand import finding_to_source_dict

    f = _finding("link_only", PageClass.third_party)
    entry = finding_to_source_dict(f, prefecture_name="長野県")
    urls = [e["url"] for e in entry.get("external_links", [])]
    assert any("rakuen-akiya.jp" in u for u in urls)


def test_site_is_up_tells_a_blocked_site_from_a_wrong_url(monkeypatch) -> None:  # noqa: ANN001
    """取得できない理由で扱いを分ける。

    403 や robots 拒否・古い TLS は「サイトはあるが読めない」→ 県の一覧を根拠にリンクだけ出す。
    404 は一覧の URL が古いということなので、人間確認に回す。
    """
    from datetime import UTC, datetime

    from sitemill.fetch.client import FetchResult

    from akiya_atlas.expand import site_is_up

    def _res(status: int, error: str | None = None) -> FetchResult:
        return FetchResult(
            url="https://x.example/",
            final_url="https://x.example/",
            status=status,
            fetched_at=datetime.now(UTC),
            error=error,
        )

    assert site_is_up(_res(403), "https://x.example/") is True
    assert site_is_up(_res(404), "https://x.example/") is False
    # HTTP の応答が無いときは名前解決できるかで決める
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [()])
    assert site_is_up(_res(0, "robots.txt により拒否"), "https://x.example/") is True

    def _fail(*a: object, **k: object) -> None:
        raise OSError("getaddrinfo failed")

    monkeypatch.setattr("socket.getaddrinfo", _fail)
    assert site_is_up(_res(0, "robots.txt を取得できない"), "https://x.example/") is False
