"""補助制度の取り込み（ADR 0012 の追記）。金額・年度・募集期間は引用のまま、締切だけ値にする。"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sitemill.extract import ExtractedItem
from sitemill.models import FieldValue

from akiya_atlas.schema import SUBSIDY_STALE_DAYS, Subsidy
from akiya_atlas.spec import parse_period_end
from akiya_atlas.subsidies import item_to_subsidy


def _item(**raw: object) -> ExtractedItem:
    free = {k: raw.get(k) for k in ("name", "kind", "scope", "kind_quote", "summary")}
    fields = {}
    for target, quote, value in (
        ("amount_text", raw.get("amount"), raw.get("amount")),
        ("year_text", raw.get("year"), raw.get("year")),
        ("period_end", raw.get("period"), raw.get("period_end")),
    ):
        if quote is None:
            continue
        fields[target] = FieldValue(
            value=value, quote=str(quote), status="parsed" if value else "unparsed"
        )
    return ExtractedItem(fields=fields, free=free, raw=dict(raw))


def test_period_end_takes_the_last_date_and_ignores_prose() -> None:
    assert parse_period_end("令和8年4月1日から令和9年3月31日まで")[0] == date(2027, 3, 31)
    assert parse_period_end("2026年12月28日まで")[0] == date(2026, 12, 28)
    assert parse_period_end("予算がなくなり次第終了") == (None, "no_date")


def test_quotes_are_kept_and_only_the_deadline_becomes_a_value() -> None:
    item = _item(
        name="老朽危険空き家解体事業補助金",
        kind="解体",
        summary="老朽化した危険な空き家の解体に要する経費を補助します。",
        amount="上限50万円",
        year="令和8年度",
        period="令和8年4月1日から令和9年3月31日まで",
        period_end=date(2027, 3, 31),
    )
    s = item_to_subsidy(
        item, source_id="kochi-392031", url="https://e.example/x", checked_on=date(2026, 9, 14)
    )
    assert s is not None
    assert s.amount_text == "上限50万円" and s.year_text == "令和8年度"
    assert s.period_text == "令和8年4月1日から令和9年3月31日まで"
    assert s.period_end == date(2027, 3, 31)
    assert s.kind == "解体" and s.kind_quote is None


def test_an_unknown_kind_is_recorded_as_undecidable_with_its_quote() -> None:
    """決められないときは「その他」に寄せず、判定できずとして原文を残す。"""
    item = _item(
        name="空家等利活用支援事業", kind="よく分からない", kind_quote="空家等の利活用に係る経費"
    )
    s = item_to_subsidy(
        item, source_id="x", url="https://e.example/y", checked_on=date(2026, 9, 14)
    )
    assert s is not None and s.kind == "判定できず"
    assert s.kind_quote == "空家等の利活用に係る経費"


def test_freshness_and_closing_are_shown_not_deleted() -> None:
    """古い情報も募集が終わった制度も消さず、印で示す。"""
    old = Subsidy(
        name="x",
        kind="改修",
        url="https://e.example/",
        checked_on=date(2026, 9, 14)
        - __import__("datetime").timedelta(days=SUBSIDY_STALE_DAYS + 1),
    )
    assert old.stale
    ended = Subsidy(
        name="y",
        kind="解体",
        url="https://e.example/",
        checked_on=date.today(),
        period_end=date(2020, 1, 1),
    )
    assert ended.closed and not ended.stale
    open_one = Subsidy(name="z", kind="移住", url="https://e.example/", checked_on=date.today())
    assert not open_one.closed and not open_one.stale


def test_general_housing_programmes_are_marked_apart_from_vacant_house_ones() -> None:
    """耐震やブロック塀のような住宅一般の制度は、空き家向けと分けて並べるために印を持つ。"""
    general = item_to_subsidy(
        _item(name="住宅等耐震改修費補助金", kind="改修", scope="住宅一般"),
        source_id="x",
        url="u",
        checked_on=date.today(),
    )
    assert general is not None and general.scope == "住宅一般"
    vacant = item_to_subsidy(
        _item(name="空き家改修補助金", kind="改修", scope="空き家"),
        source_id="x",
        url="u",
        checked_on=date.today(),
    )
    assert vacant is not None and vacant.scope == "空き家"
    # 判断が無い・読めないときは空き家の側に寄せる（取りこぼしを避ける）
    unknown = item_to_subsidy(
        _item(name="よく分からない補助金", kind="その他", scope="???"),
        source_id="x",
        url="u",
        checked_on=date.today(),
    )
    assert unknown is not None and unknown.scope == "空き家"


def test_a_row_without_a_name_is_skipped() -> None:
    assert (
        item_to_subsidy(
            _item(name="  ", kind="改修"), source_id="x", url="u", checked_on=date.today()
        )
        is None
    )
    assert datetime.now(UTC) is not None
