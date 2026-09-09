"""物件の抽出仕様。LLM は引用だけを返し、値は sitemill の決定的パーサが作る（ADR 0004）。"""

from __future__ import annotations

from importlib import resources
from typing import Any

from sitemill.extract import ExtractionSpec, QuoteField
from sitemill.parse.jp import parse_area_m2, parse_year, parse_yen

PROMPT_VERSION = "listing_v1"


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
        QuoteField("price_quote", "price", parse_yen),
        QuoteField("rent_quote", "rent_monthly", parse_yen),
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

SPECS_BY_KIND = {"listing_index": LISTING_SPEC, "listing_detail": LISTING_SPEC}


def spec_for_kind(kind: str) -> ExtractionSpec | None:
    return SPECS_BY_KIND.get(kind)
