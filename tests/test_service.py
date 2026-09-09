from datetime import UTC, datetime

from sitemill.extract.spec import ExtractedItem
from sitemill.models import (
    CrawlPolicy,
    FieldStatus,
    FieldValue,
    OperatorEvidence,
    OperatorKind,
    PageKind,
    SeedPage,
    Source,
)

from akiya_atlas.service import item_to_content, merge_content

SRC = Source(
    id="nagano-tomi",
    name="東御市空き家バンク",
    operator="東御市",
    operator_kind=OperatorKind.municipality,
    operator_evidence=OperatorEvidence(
        quote="東御市役所", url="https://akiya.city.tomi.nagano.jp/"
    ),
    policy=CrawlPolicy.crawl,
    official_url="https://www.city.tomi.nagano.jp/",
    pages=[SeedPage(url="https://akiya.city.tomi.nagano.jp/", kind=PageKind.listing_index)],
)


def _fv(value=None, quote=None, status=FieldStatus.not_found, note=None):  # noqa: ANN001, ANN202
    return FieldValue(value=value, quote=quote, status=status, note=note)


def _item(no: str | None, **fields: FieldValue) -> ExtractedItem:
    item = ExtractedItem()
    if no is not None:
        item.fields["listing_no"] = _fv(no, no, FieldStatus.parsed)
    item.fields.update(fields)
    item.free = {"title": None, "summary": None, "deal_type": "sale"}
    return item


def test_item_to_content_requires_listing_no_on_index_pages() -> None:
    assert (
        item_to_content(
            _item(None),
            source=SRC,
            municipality_code="202193",
            url="https://akiya.city.tomi.nagano.jp/",
            kind="listing_index",
        )
        is None
    )
    detail = item_to_content(
        _item(None),
        source=SRC,
        municipality_code="202193",
        url="https://akiya.city.tomi.nagano.jp/2026/08/1000no277.html",
        kind="listing_detail",
    )
    assert detail is not None and detail["listing_no"] == "1000no277.html"
    assert detail["detail_url"] == "https://akiya.city.tomi.nagano.jp/2026/08/1000no277.html"


def test_item_to_content_normalizes_and_dumps_fields() -> None:
    item = _item("No.３２２", price=_fv(9_800_000, "980万円", FieldStatus.parsed))
    content = item_to_content(
        item,
        source=SRC,
        municipality_code="202193",
        url="https://akiya.city.tomi.nagano.jp/",
        kind="listing_index",
    )
    assert content is not None
    assert content["listing_no"] == "322" and content["deal_type"] == "sale"
    assert content["price"]["value"] == 9_800_000 and content["price"]["status"] == "parsed"
    assert content["detail_url"] is None and content["page_kind"] == "listing_index"


def test_merge_prefers_parsed_values_and_detail_pages() -> None:
    existing = {
        "page_kind": "listing_detail",
        "source_url": "https://x/detail/1",
        "detail_url": "https://x/detail/1",
        "title": "詳細のタイトル",
        "summary": "詳細の要約",
        "deal_type": "sale",
        "price": {"value": 9_800_000, "quote": "980万円", "status": "parsed", "note": None},
        "built_year": {"value": 1970, "quote": "昭和45年", "status": "parsed", "note": None},
    }
    from_index = {
        "page_kind": "listing_index",
        "source_url": "https://x/",
        "detail_url": None,
        "title": None,
        "summary": None,
        "deal_type": "unknown",
        "price": {"value": 9_500_000, "quote": "950万円", "status": "parsed", "note": None},
        "built_year": {
            "value": None,
            "quote": "築40年",
            "status": "unparsed",
            "note": "relative_age",
        },
    }
    merged = merge_content(existing, from_index)
    assert merged["price"]["value"] == 9_800_000  # 詳細ページ由来を優先
    assert merged["built_year"]["value"] == 1970  # parsed を優先
    assert merged["title"] == "詳細のタイトル" and merged["deal_type"] == "sale"
    assert merged["detail_url"] == "https://x/detail/1" and merged["page_kind"] == "listing_detail"
    assert merged["source_url"] == "https://x/detail/1"

    from_detail = {**from_index, "page_kind": "listing_detail", "detail_url": "https://x/detail/1"}
    merged2 = merge_content(existing, from_detail)
    assert merged2["price"]["value"] == 9_500_000  # 新しい詳細ページが勝つ
    assert merged2["built_year"]["value"] == 1970  # parsed が無ければ既存を残す


def test_now_helper_is_utc() -> None:
    assert datetime.now(UTC).tzinfo is UTC
