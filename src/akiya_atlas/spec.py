"""物件の抽出仕様。LLM は引用だけを返し、値は sitemill の決定的パーサが作る（ADR 0004）。"""

from __future__ import annotations

import re
from importlib import resources
from typing import Any

from sitemill.extract import ExtractionSpec, QuoteField
from sitemill.parse.jp import (
    normalize_text,
    parse_area_m2,
    parse_date,
    parse_year,
    parse_yen,
)

from akiya_atlas.schema import SUBSIDY_KINDS

# 「坪単価 75,000円」「駐車場用賃料 月1万円」のような、物件価格ではない金額を弾く。
# 原文にはこうした数字も並ぶので、引用が取れても値にしない（ADR 0004 の quote-then-parse）
_NOT_A_PRICE = re.compile(
    r"単価|坪\s*(?:あ|当)?たり|坪\s*\d|\d\s*坪で|/\s*坪|㎡\s*(?:あ|当)?たり|平米\s*(?:あ|当)?たり|"
    r"賃料|家賃|月額|月\s*\d|管理費|共益費|敷金|礼金|"
    r"手数料|税|補助|助成|上限|報酬|保証金|更新料"
)
# 0 円を本物の価格とみなす語（無償譲渡）。無ければ「0万円」等は価格未定として値にしない
_FREE_OF_CHARGE = re.compile("無償|無料|贈与|ゼロ円|0円|０円")
_NOT_A_RENT = re.compile(r"坪単価|単価|敷金|礼金|管理費|共益費|手数料|税|補助|助成|上限|保証金")


def parse_sale_price(quote: str) -> tuple[int | None, str | None]:
    """売買価格の引用を値にする。単価や賃料などの金額は値にしない。

    パーサは必ず (値, 注記) を返す（sitemill の QuoteField の約束）。値にしないときは
    (None, "not_a_price") を返し、引用は残す。

    0 は「無償譲渡」なら本物の価格だが、「0万円」のような価格未定の書き方でも出る。
    無償だと分かる語があるときだけ 0 を値にする（留萌市の実例で分けた）。
    """
    if _NOT_A_PRICE.search(quote or ""):
        return None, "not_a_price"
    value, note = parse_yen(quote)
    if value == 0 and not _FREE_OF_CHARGE.search(quote or ""):
        return None, "price_unknown"
    return value, note


def parse_rent(quote: str) -> tuple[int | None, str | None]:
    """月額賃料の引用を値にする。単価や一時金は値にしない。"""
    if _NOT_A_RENT.search(quote or ""):
        return None, "not_a_price"
    return parse_yen(quote)


PROMPT_VERSION = "listing_v1"
SUBSIDY_PROMPT_VERSION = "subsidy_v1"


def _nullable(kind: str, description: str) -> dict[str, Any]:
    return {"type": [kind, "null"], "description": description}


LISTING_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "listing_no_quote": _nullable("string", "物件番号・管理番号の原文（例: No.329、A-12）"),
        "deal_type": {
            "type": "string",
            "enum": ["sale", "rent", "both", "unknown"],
            "description": "売買=sale、賃貸=rent、両方=both、不明=unknown",
        },
        "title": _nullable("string", "30 字以内の見出し（自分の言葉で）"),
        "summary": _nullable("string", "100 字以内の要約（自分の言葉で。本文の文を写さない）"),
        "address_quote": _nullable(
            "string", "所在地の原文（市町村名以降の地名まで。番地や個人名は含めない）"
        ),
        "price_quote": _nullable("string", "売買価格の原文（例: 980万円、応相談）"),
        "rent_quote": _nullable("string", "月額賃料の原文（例: 5万円、50,000円）"),
        "land_area_quote": _nullable("string", "土地面積の原文（例: 300㎡、90坪）"),
        "floor_area_quote": _nullable("string", "延床面積・建物面積の原文（例: 98.5㎡）"),
        "built_year_quote": _nullable(
            "string", "築年・建築年の原文（例: 昭和45年、1975年、築40年）"
        ),
        "structure_quote": _nullable("string", "構造の原文（例: 木造2階建）"),
        "layout_quote": _nullable("string", "間取りの原文（例: 5DK）"),
        "status_quote": _nullable("string", "状況の原文（例: 交渉中、成約済）"),
    },
    "required": [
        "listing_no_quote",
        "deal_type",
        "title",
        "summary",
        "address_quote",
        "price_quote",
        "rent_quote",
        "land_area_quote",
        "floor_area_quote",
        "built_year_quote",
        "structure_quote",
        "layout_quote",
        "status_quote",
    ],
    "additionalProperties": False,
}

LISTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"listings": {"type": "array", "items": LISTING_ITEM_SCHEMA}},
    "required": ["listings"],
    "additionalProperties": False,
}


def load_prompt(version: str = PROMPT_VERSION) -> str:
    return (
        resources.files("akiya_atlas")
        .joinpath("prompts", f"{version}.md")
        .read_text(encoding="utf-8")
    )


def summary_fallback(raw: dict[str, Any]) -> str:
    no = raw.get("listing_no_quote") or "番号不明"
    return f"空き家バンク掲載物件（{no}）。詳しい内容は自治体のページでご確認ください。"


LISTING_SPEC = ExtractionSpec(
    name="listing",
    prompt_version=PROMPT_VERSION,
    system_prompt=load_prompt(),
    output_schema=LISTING_SCHEMA,
    quote_fields=(
        QuoteField("listing_no_quote", "listing_no", None),
        QuoteField("address_quote", "address", None),
        QuoteField("price_quote", "price", parse_sale_price),
        QuoteField("rent_quote", "rent_monthly", parse_rent),
        QuoteField("land_area_quote", "land_area_m2", parse_area_m2),
        QuoteField("floor_area_quote", "floor_area_m2", parse_area_m2),
        QuoteField("built_year_quote", "built_year", parse_year),
        QuoteField("structure_quote", "structure", None),
        QuoteField("layout_quote", "layout", None),
        QuoteField("status_quote", "status_text", None),
    ),
    items_key="listings",
    free_text_fields=("title", "summary", "deal_type"),
    summary_field="summary",
    summary_max_chars=120,
    verbatim_overlap_chars=30,
    summary_fallback=summary_fallback,
)

SUBSIDY_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "kind": {"type": "string", "enum": list(SUBSIDY_KINDS)},
        "scope": {"type": "string", "enum": ["空き家", "住宅一般"]},
        "kind_quote": {"type": ["string", "null"]},
        "summary": {"type": "string"},
        "amount_quote": {"type": ["string", "null"]},
        "year_quote": {"type": ["string", "null"]},
        "period_quote": {"type": ["string", "null"]},
    },
    "required": [
        "name", "kind", "scope", "kind_quote", "summary",
        "amount_quote", "year_quote", "period_quote",
    ],
    "additionalProperties": False,
}

SUBSIDY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"subsidies": {"type": "array", "items": SUBSIDY_ITEM_SCHEMA}},
    "required": ["subsidies"],
    "additionalProperties": False,
}

# 「令和8年4月1日」「2026年3月31日」のような日付。締切を取るために全部拾う
_DATE_LIKE = re.compile(
    r"(?:令和|平成|昭和|R|H)?\s*(?:\d{1,4}|元)\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
)


def parse_period_end(quote: str) -> tuple[Any, str | None]:
    """募集期間の引用から締切を取る。日付が複数あれば最も後ろの日付を締切とみなす。

    「予算がなくなり次第終了」「随時受付」のように日付が無い書き方は値にしない。
    引用は残るので、画面には原文のまま出せる（ADR 0004）。
    """
    found = []
    for m in _DATE_LIKE.finditer(normalize_text(quote or "")):
        value, _ = parse_date(m.group(0))
        if value is not None:
            found.append(value)
    if not found:
        return None, "no_date"
    return max(found), None


SUBSIDY_SPEC = ExtractionSpec(
    name="subsidy",
    prompt_version=SUBSIDY_PROMPT_VERSION,
    system_prompt=load_prompt(SUBSIDY_PROMPT_VERSION),
    output_schema=SUBSIDY_SCHEMA,
    quote_fields=(
        QuoteField("amount_quote", "amount_text", None),
        QuoteField("year_quote", "year_text", None),
        QuoteField("period_quote", "period_end", parse_period_end),
    ),
    items_key="subsidies",
    free_text_fields=("name", "kind", "scope", "kind_quote", "summary"),
    summary_field="summary",
    summary_max_chars=100,
    verbatim_overlap_chars=30,
)

SPECS_BY_KIND = {
    "listing_index": LISTING_SPEC,
    "listing_detail": LISTING_SPEC,
    "subsidy": SUBSIDY_SPEC,
}


def spec_for_kind(kind: str) -> ExtractionSpec | None:
    return SPECS_BY_KIND.get(kind)
