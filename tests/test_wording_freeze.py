"""値が変わっていないのに言い回しだけが書き換わるのを止める（2026-09-19）。

一覧ページに 1 件でも変化があると、そのページの全物件を読み直す。同じ本文でも LLM は
別の言葉で要約するので、変わっていない物件まで題名・要約・引用が書き換わり、サイトマップの
lastmod が毎晩「変わった」と言い続ける。実測では一晩 972 ページのうち、値が動いたのは
42 件、言い回しだけが動いたのは 386 件だった（docs/data-issues.md）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sitemill.store.records import RecordStore

from akiya_atlas.service import merge_content
from akiya_atlas.subsidies import merge_subsidy


def _fv(value=None, quote=None, status="parsed"):  # noqa: ANN001, ANN202
    return {"value": value, "quote": quote, "status": status, "note": None}


def _listing(*, title: str, summary: str, price_quote: str, price: int = 3_500_000) -> dict:
    return {
        "source_id": "nagano-tomi",
        "municipality_code": "202193",
        "listing_no": "122",
        "source_url": "https://x/",
        "page_kind": "listing_index",
        "deal_type": "sale",
        "detail_url": None,
        "title": title,
        "summary": summary,
        "price": _fv(price, price_quote),
        "floor_area_m2": _fv(130.78, "約130.78平方メートル(約39坪)"),
    }


def test_same_values_keep_the_stored_wording() -> None:
    stored = _listing(
        title="東深井の便利な土地",
        summary="スーパーが近くにある日当たりのよい便利な場所の土地。",
        price_quote="350万円",
    )
    reworded = _listing(
        title="東深井の便利な土地、買い物に近い",
        summary="日当たりがよく、スーパーが近い便利な土地。",
        price_quote="350万円（応相談）",
    )
    reworded["floor_area_m2"] = _fv(130.78, "約130.78平方メートル")

    merged = merge_content(stored, reworded)

    assert merged["title"] == stored["title"]
    assert merged["summary"] == stored["summary"]
    assert merged["price"]["quote"] == "350万円"
    assert merged["floor_area_m2"]["quote"] == "約130.78平方メートル(約39坪)"


def test_a_changed_value_brings_the_new_wording_with_it() -> None:
    stored = _listing(title="古い題名", summary="古い要約。", price_quote="70万円", price=700_000)
    lowered = _listing(
        title="値下げした物件", summary="価格を下げました。", price_quote="120万円", price=1_200_000
    )

    merged = merge_content(stored, lowered)

    assert merged["price"]["value"] == 1_200_000
    assert merged["title"] == "値下げした物件" and merged["summary"] == "価格を下げました。"


def test_the_first_detail_page_still_replaces_the_list_wording() -> None:
    """値が同じでも、詳細ページの書き方のほうが厚い。初めて来たときは受け取る。"""
    stored = _listing(title="一覧の題名", summary="一覧の要約。", price_quote="350万円")
    from_detail = {
        **_listing(title="詳細の題名", summary="詳細の要約。", price_quote="350万円"),
        "page_kind": "listing_detail",
        "detail_url": "https://x/detail/122",
        "source_url": "https://x/detail/122",
    }

    merged = merge_content(stored, from_detail)

    assert merged["title"] == "詳細の題名" and merged["summary"] == "詳細の要約。"


def test_the_store_reports_unchanged_so_the_page_never_moves(tmp_path: Path) -> None:
    """凍結の目的は「ページが動かないこと」。upsert が unchanged を返すところまで見る。"""
    store = RecordStore(tmp_path / "rec.jsonl")
    now = datetime(2026, 9, 19, tzinfo=UTC)
    stored = _listing(title="東深井の便利な土地", summary="便利な土地。", price_quote="350万円")
    assert store.upsert("r1", stored, now=now, merge=merge_content) == "created"

    reworded = _listing(
        title="東深井の土地、便利", summary="便利な場所の土地。", price_quote="350万円"
    )
    result = store.upsert("r1", reworded, now=now, merge=merge_content)

    assert result == "unchanged"
    assert store.records["r1"]["title"] == "東深井の便利な土地"
    assert len(store.records["r1"]["history"]) == 1


def _subsidy(*, kind: str, summary: str, amount: str = "上限50万円") -> dict:
    return {
        "name": "老朽危険空き家解体事業補助金",
        "kind": kind,
        "scope": "空き家",
        "url": "https://x/subsidy",
        "summary": summary,
        "amount_text": amount,
        "year_text": "令和8年度",
        "period_text": None,
        "period_end": None,
        "kind_quote": None,
        "checked_on": "2026-09-19",
        "source_id": "nagano-tomi",
    }


def test_a_subsidy_keeps_its_kind_when_nothing_quoted_changed() -> None:
    stored = _subsidy(kind="改修", summary="旧基準木造住宅の除却工事の費用を補助する事業。")
    reread = _subsidy(kind="その他", summary="旧基準木造住宅の除却工事に対し費用を補助する制度。")

    merged = merge_subsidy(stored, reread)

    assert merged["kind"] == "改修"
    assert merged["summary"] == stored["summary"]
    assert merged["checked_on"] == "2026-09-19"


def test_a_changed_amount_makes_the_subsidy_be_judged_again() -> None:
    stored = _subsidy(kind="改修", summary="古い要約。", amount="上限50万円")
    updated = _subsidy(kind="解体", summary="新しい要約。", amount="上限80万円")

    merged = merge_subsidy(stored, updated)

    assert merged["kind"] == "解体" and merged["summary"] == "新しい要約。"


def test_an_undecided_subsidy_accepts_a_decision() -> None:
    stored = _subsidy(kind="判定できず", summary="要約。")
    stored["kind_quote"] = "どの区分にも当たらない"
    decided = _subsidy(kind="解体", summary="要約。")

    merged = merge_subsidy(stored, decided)

    assert merged["kind"] == "解体" and merged["kind_quote"] is None
