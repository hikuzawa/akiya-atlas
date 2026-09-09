from akiya_atlas.schema import (
    Listing,
    Municipality,
    Subsidy,
    listing_slug,
    normalize_listing_no,
    record_id_for,
)


def test_normalize_listing_no_absorbs_notation() -> None:
    assert normalize_listing_no("No.３２２") == "322"
    assert normalize_listing_no("物件番号 15") == "15"
    assert normalize_listing_no("A-12") == "A-12"
    assert normalize_listing_no(" №7 ") == "7"
    assert normalize_listing_no("NO.293") == "293"


def test_record_id_is_stable_across_notations() -> None:
    assert record_id_for("nagano-tomi", "No.322") == record_id_for("nagano-tomi", "３２２")
    assert record_id_for("nagano-tomi", "322") != record_id_for("nagano-saku", "322")
    assert len(record_id_for("x", "1")) == 16


def test_listing_slug() -> None:
    assert listing_slug("A-12", "fb") == "a-12"
    assert listing_slug("№7", "fb") == "7"
    assert listing_slug("物件", "fb") == "fb"


def test_municipality_flags_and_path() -> None:
    m = Municipality(
        id="nagano-tomi",
        code="202193",
        name="東御市",
        prefecture="長野県",
        prefecture_slug="nagano",
        slug="202193-tomi",
        official_url="https://www.city.tomi.nagano.jp/",
        bank_url="https://akiya.city.tomi.nagano.jp/",
        subsidies=[Subsidy(name="改修補助", kind="改修", url="https://example.com/a")],
    )
    assert m.has_renovation_subsidy and not m.has_migration_subsidy and m.has_subsidy
    assert m.path == "nagano/202193-tomi/"


def test_listing_defaults_and_labels() -> None:
    ls = Listing(
        record_id="abc",
        source_id="nagano-tomi",
        municipality_code="202193",
        listing_no="322",
        source_url="https://akiya.city.tomi.nagano.jp/",
    )
    assert ls.deal_label == "種別不明" and not ls.price.ok
    assert ls.display_title == "空き家バンク物件 322" and ls.slug == "322"
    assert ls.primary_url == "https://akiya.city.tomi.nagano.jp/"
