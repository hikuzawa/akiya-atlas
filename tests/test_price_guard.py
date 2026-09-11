"""売買価格・賃料に、単価や一時金の金額を入れないことのテスト（全国展開で発見）。"""

from __future__ import annotations

from akiya_atlas.service import drop_wrong_prices
from akiya_atlas.spec import parse_rent, parse_sale_price


def test_sale_price_ignores_unit_prices_and_rent() -> None:
    # 入善町の実例。空き地の原文に坪単価や駐車場の賃料が並ぶ
    assert parse_sale_price("[坪単価75,000円]") == (None, "not_a_price")
    assert parse_sale_price("[駐車場用賃料 月1万円]") == (None, "not_a_price")
    assert parse_sale_price("㎡単価 3万円") == (None, "not_a_price")
    assert parse_sale_price("管理費 5,000円") == (None, "not_a_price")
    # 南相馬市の実例。坪あたりの単価が売買価格として抽出されていた
    assert parse_sale_price("坪あたり3万円程度") == (None, "not_a_price")
    assert parse_sale_price("坪3万円程度") == (None, "not_a_price")
    assert parse_sale_price("坪あたり4万4,000円程度価格応談") == (None, "not_a_price")
    # 無償譲渡や格安物件は本物の売買価格なので残す
    assert parse_sale_price("0円(無償)") == (0, None)
    assert parse_sale_price("0円") == (0, None)
    assert parse_sale_price("1万円") == (10_000, None)
    # 留萌市の実例。「0万円」は無償ではなく価格未定の書き方なので値にしない
    assert parse_sale_price("0万円") == (None, "price_unknown")
    assert parse_sale_price("350万円") == (3_500_000, None)  # (値, 注記) を返す
    assert parse_sale_price("1,000万円") == (10_000_000, None)


def test_rent_ignores_deposits_and_unit_prices() -> None:
    assert parse_rent("敷金 10万円") == (None, "not_a_price")
    assert parse_rent("坪単価 6,000円") == (None, "not_a_price")
    assert parse_rent("月額 5万円") == (50_000, None)
    assert parse_rent("50,000円") == (50_000, None)


def test_saved_records_are_corrected() -> None:
    content = {
        "price": {"value": 75000, "quote": "[坪単価75,000円]", "status": "parsed", "note": None},
        "rent_monthly": {"value": 100000, "quote": "敷金 10万円", "status": "parsed", "note": None},
    }
    assert drop_wrong_prices(content)
    assert content["price"]["value"] is None and content["price"]["status"] == "unparsed"
    assert content["price"]["quote"] == "[坪単価75,000円]"  # 原文は残す
    assert content["rent_monthly"]["value"] is None
    # 正しい値は触らない
    ok = {"price": {"value": 3_500_000, "quote": "350万円", "status": "parsed", "note": None}}
    assert not drop_wrong_prices(ok)
    assert ok["price"]["value"] == 3_500_000


def test_parser_always_returns_a_pair() -> None:
    """sitemill の抽出は (値, 注記) に展開するので、None を返してはいけない。"""
    for q in ("350万円", "[坪単価75,000円]", "", "応相談"):
        assert isinstance(parse_sale_price(q), tuple) and len(parse_sale_price(q)) == 2
        assert isinstance(parse_rent(q), tuple) and len(parse_rent(q)) == 2
