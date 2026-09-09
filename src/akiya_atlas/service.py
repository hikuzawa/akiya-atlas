"""sitemill の Service 実装。データ・スキーマ・ページ構成はこちらが持つ（sitemill ADR 0006）。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from sitemill.diff.state import CrawlState
from sitemill.extract import ExtractedItem, ExtractionSpec
from sitemill.models import Page, Provenance, Redirect, Source
from sitemill.settings import Workspace
from sitemill.store.records import RecordStore

from akiya_atlas import affiliates, pages
from akiya_atlas.data import Dataset, load_municipalities, load_sources, records_path
from akiya_atlas.schema import normalize_listing_no, record_id_for
from akiya_atlas.spec import spec_for_kind

log = logging.getLogger(__name__)

FIELD_KEYS = (
    "address",
    "price",
    "rent_monthly",
    "land_area_m2",
    "floor_area_m2",
    "built_year",
    "structure",
    "layout",
    "status_text",
)
STALE_AFTER_DAYS = 30


def item_to_content(
    item: ExtractedItem, *, source: Source, municipality_code: str, url: str, kind: str
) -> dict[str, Any] | None:
    """抽出結果 1 件をレコードの内容にする。物件番号が無い一覧項目は捨てる。"""
    no = item.value("listing_no")
    if not no:
        if kind != "listing_detail":
            return None
        no = url.rstrip("/").rsplit("/", 1)[-1] or url  # 詳細ページは URL 末尾で代替
    listing_no = normalize_listing_no(str(no))
    if not listing_no:
        return None
    deal = item.free.get("deal_type") or "unknown"
    if deal not in ("sale", "rent", "both", "unknown"):
        deal = "unknown"
    content: dict[str, Any] = {
        "source_id": source.id,
        "municipality_code": municipality_code,
        "listing_no": listing_no,
        "deal_type": deal,
        "title": item.free.get("title"),
        "summary": item.free.get("summary"),
        "source_url": url,
        "detail_url": url if kind == "listing_detail" else None,
        "page_kind": kind,
    }
    for key in FIELD_KEYS:
        fv = item.fields.get(key)
        content[key] = fv.model_dump(mode="json") if fv is not None else None
    return content


def merge_content(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """項目ごとに parsed を優先し、詳細ページ由来を一覧ページ由来より優先する（ADR 0002）。"""
    out = dict(new)
    new_is_detail = new.get("page_kind") == "listing_detail"
    old_is_detail = existing.get("page_kind") == "listing_detail"
    for key in FIELD_KEYS:
        old, cur = existing.get(key) or {}, new.get(key) or {}
        old_ok, cur_ok = old.get("status") == "parsed", cur.get("status") == "parsed"
        if old_ok and (not cur_ok or (old_is_detail and not new_is_detail)):
            out[key] = old
    for key in ("title", "summary"):
        if not out.get(key) or (old_is_detail and not new_is_detail and existing.get(key)):
            out[key] = existing.get(key) or out.get(key)
    if out.get("deal_type") == "unknown" and existing.get("deal_type") not in (None, "unknown"):
        out["deal_type"] = existing["deal_type"]
    if not new_is_detail:
        out["detail_url"] = existing.get("detail_url")
        if old_is_detail:
            out["page_kind"] = "listing_detail"
            out["source_url"] = existing.get("source_url", out["source_url"])
    return out


class AkiyaAtlasService:
    id = "akiya-atlas"

    def sources(self, ws: Workspace) -> list[Source]:
        return load_sources(ws)

    def extraction_spec(self, kind: str) -> ExtractionSpec | None:
        return spec_for_kind(kind)

    def ingest(
        self,
        ws: Workspace,
        *,
        source: Source,
        url: str,
        kind: str,
        items: Sequence[ExtractedItem],
        provenance: Provenance,
    ) -> dict[str, int]:
        munis = {m.id: m for m in load_municipalities(ws)}
        muni = munis.get(source.id)
        code = muni.code if muni else "000000"
        store = RecordStore(records_path(ws, source.id))
        counts = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0}
        now = provenance.extractor.extracted_at if provenance.extractor else provenance.fetched_at
        for item in items:
            content = item_to_content(
                item, source=source, municipality_code=code, url=url, kind=kind
            )
            if content is None:
                counts["skipped"] += 1
                continue
            rid = record_id_for(source.id, content["listing_no"])
            result = store.upsert(
                rid,
                content,
                now=now,
                provenance=provenance.model_dump(mode="json"),
                merge=merge_content,
            )
            counts[result] += 1
        store.save()
        log.info("%s %s: %s", source.id, url, counts)
        return counts

    def finalize(self, ws: Workspace, *, now: datetime) -> None:
        """変化の無いページのレコードは見えているとみなし、古いものを stale にする。"""
        state = CrawlState.load(ws.state_dir / "crawl.json")
        for source in load_sources(ws):
            if not source.crawlable:
                continue
            store = RecordStore(records_path(ws, source.id))
            if not len(store):
                continue
            for record in store.records.values():
                st = state.get(record.get("source_url", ""))
                if (
                    st is not None
                    and st.error is None
                    and st.fetched_at is not None
                    and st.content_hash is not None
                    and st.content_hash == st.extracted_hash
                ):
                    seen = st.fetched_at.isoformat()
                    if seen > (record.get("last_seen_at") or ""):
                        record["last_seen_at"] = seen
            stale = store.mark_stale(now=now, max_age_days=STALE_AFTER_DAYS)
            if stale:
                log.info("%s: %d 件を stale に", source.id, stale)
            store.save()

    def pages(self, ws: Workspace, *, now: datetime) -> list[Page]:
        return pages.build_pages(ws, Dataset.load(ws), now=now)

    def search_index(self, ws: Workspace) -> Any:
        return pages.search_index(ws, Dataset.load(ws))

    def redirects(self, ws: Workspace) -> list[Redirect]:
        return affiliates.redirects()

    def eval_dir(self, ws: Workspace) -> Path | None:
        return ws.fixtures_dir / "eval"


service = AkiyaAtlasService()
