"""データスキーマ（ADR 0002）。数値は sitemill の FieldValue（引用＋決定的パース）で持つ。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field
from sitemill.models import FieldValue

DealType = Literal["sale", "rent", "both", "unknown"]
DEAL_LABELS = {"sale": "売買", "rent": "賃貸", "both": "売買・賃貸", "unknown": "種別不明"}
# 成約・売約済みなど「現在は募集していない」ことを示す語。原文の見出しに現れる。
CLOSED_MARKERS = re.compile(
    r"成約済|ご成約|売約済|契約済|申込済|申し込み済|商談中|募集終了|掲載終了"
)


class Subsidy(BaseModel):
    """市町村単位の補助制度。出典 URL と確認日を必ず持つ。"""

    name: str
    kind: Literal["移住", "改修", "解体", "家財", "取得", "その他"]
    url: str
    summary: str = ""
    amount_text: str | None = None
    checked_on: date | None = None


class Municipality(BaseModel):
    """自治体。data/sources/*.yaml の各エントリから作る（Source と同じエントリを共有）。"""

    id: str  # Source の id と同じ
    code: str = Field(pattern=r"^\d{6}$")  # 全国地方公共団体コード（6 桁）
    name: str
    prefecture: str
    prefecture_slug: str
    slug: str
    official_url: str
    bank_url: str
    bank_label: str = "空き家バンク"
    # 巡回状況: available=巡回 / third_party_only=民間のみ / spa_unsupported=JS描画 / none=なし
    bank_status: Literal["available", "third_party_only", "spa_unsupported", "info", "none"] = (
        "available"
    )
    bank_note: str | None = None  # 非巡回ケースの説明（分類結果から自動生成）
    subsidies: list[Subsidy] = Field(default_factory=list)
    contact: str | None = None
    map_query: str | None = None

    @property
    def has_migration_subsidy(self) -> bool:
        return any(s.kind == "移住" for s in self.subsidies)

    @property
    def has_renovation_subsidy(self) -> bool:
        return any(s.kind in ("改修", "取得") for s in self.subsidies)

    @property
    def has_demolition_subsidy(self) -> bool:
        return any(s.kind in ("解体", "家財") for s in self.subsidies)

    @property
    def has_subsidy(self) -> bool:
        return bool(self.subsidies)

    @property
    def crawled(self) -> bool:
        return self.bank_status == "available"

    @property
    def path(self) -> str:
        return f"{self.prefecture_slug}/{self.slug}/"


class Listing(BaseModel):
    """1 物件。写真と本文は持たない。値は引用付きの FieldValue（ADR 0004）。"""

    record_id: str
    source_id: str
    municipality_code: str
    listing_no: str
    deal_type: DealType = "unknown"
    title: str | None = None
    summary: str | None = None
    address: FieldValue[str] = Field(default_factory=FieldValue)
    price: FieldValue[int] = Field(default_factory=FieldValue)
    rent_monthly: FieldValue[int] = Field(default_factory=FieldValue)
    land_area_m2: FieldValue[float] = Field(default_factory=FieldValue)
    floor_area_m2: FieldValue[float] = Field(default_factory=FieldValue)
    built_year: FieldValue[int] = Field(default_factory=FieldValue)
    structure: FieldValue[str] = Field(default_factory=FieldValue)
    layout: FieldValue[str] = Field(default_factory=FieldValue)
    status_text: FieldValue[str] = Field(default_factory=FieldValue)
    source_url: str
    detail_url: str | None = None
    page_kind: str = "listing_index"
    provenance: dict[str, Any] | None = None
    status: str = "active"
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def deal_label(self) -> str:
        return DEAL_LABELS.get(self.deal_type, DEAL_LABELS["unknown"])

    @property
    def primary_url(self) -> str:
        return self.detail_url or self.source_url

    @property
    def slug(self) -> str:
        return listing_slug(self.listing_no, self.record_id)

    @property
    def display_title(self) -> str:
        return self.title or f"空き家バンク物件 {self.listing_no}"

    @property
    def is_closed(self) -> bool:
        """原文の見出し・要約に成約済み等の語があれば、募集中でないとみなす。"""
        parts = [self.title, self.summary]
        if self.status_text.ok:
            parts.append(str(self.status_text.value))
        return any(p and CLOSED_MARKERS.search(p) for p in parts)

    @property
    def is_active(self) -> bool:
        """現在掲載中（stale でも成約済でもない）か。"""
        return self.status == "active" and not self.is_closed


FIELD_LABELS = {
    "address": "所在地",
    "price": "価格",
    "rent_monthly": "賃料（月額）",
    "land_area_m2": "土地面積",
    "floor_area_m2": "延床面積",
    "built_year": "築年",
    "structure": "構造",
    "layout": "間取り",
    "status_text": "状況",
}


def normalize_listing_no(text: str) -> str:
    """物件番号の表記ゆれ（全角・空白・接頭辞）を吸収してキーにする。"""
    t = unicodedata.normalize("NFKC", text).strip()
    t = re.sub(r"^(物件番号|物件No\.?|No\.?|№|番号)\s*[:：]?\s*", "", t, flags=re.I)
    t = re.sub(r"\s+", "", t)
    return t.strip("：:.- ")


def record_id_for(source_id: str, listing_no: str) -> str:
    key = f"{source_id}:{normalize_listing_no(listing_no)}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def listing_slug(listing_no: str, fallback: str) -> str:
    """URL に使う物件の識別子。

    「地-24」「家-24」のように日本語の分類を含む物件番号は、英数字だけ残すと衝突する
    （北海道当別町の実例）。日本語を含むときは番号そのものから短いしるしを足して区別する。
    """
    s = normalize_listing_no(listing_no)
    slug = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    if not slug:
        return fallback
    if re.search(r"[^\x00-\x7f]", s):
        slug = f"{slug}-{hashlib.sha1(s.encode('utf-8')).hexdigest()[:4]}"
    return slug
