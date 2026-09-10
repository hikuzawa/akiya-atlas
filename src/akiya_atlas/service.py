"""sitemill の Service 実装。データ・スキーマ・ページ構成はこちらが持つ（sitemill ADR 0006）。"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from sitemill.diff.state import CrawlState
from sitemill.extract import ExtractedItem, ExtractionSpec
from sitemill.models import Page, Provenance, Redirect, Source
from sitemill.parse.jp.address import has_street_number, strip_street_number
from sitemill.settings import Workspace
from sitemill.store.records import RecordStore

from akiya_atlas import affiliates, pages, site_redirects
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


def sanitize_address_fields(content: dict[str, Any]) -> bool:
    """所在地から番地・号・建物名を落とし「市町村＋大字・地区名」までにする（ADR 0002 追記）。

    落とした文字列が title / summary に含まれていればそこからも消す。変更があれば True。
    """
    changed = False
    addr = content.get("address")
    if not addr or addr.get("status") != "parsed" or not addr.get("value"):
        return False
    original = str(addr["value"])
    kept, removed = strip_street_number(original)
    if kept == original and removed is None:
        return False
    if kept:
        addr["value"] = kept
        addr["quote"] = kept
        if removed:
            addr["note"] = "street_number_removed"
    else:
        addr.update(
            {"value": None, "quote": None, "status": "unparsed", "note": "street_number_only"}
        )
    changed = True
    if removed:
        for key in ("title", "summary"):
            text = content.get(key)
            if text and removed in text:
                content[key] = text.replace(removed, "").strip() or None
    return changed


def assert_no_street_numbers(records: list[dict[str, Any]], *, where: str) -> None:
    bad = [
        r.get("record_id")
        for r in records
        for v in ((r.get("address") or {}).get("value"), (r.get("address") or {}).get("quote"))
        if v and has_street_number(str(v))
    ]
    if bad:
        raise ValueError(f"{where}: 番地が残っている所在地がある: {bad[:5]}")


def item_to_content(
    item: ExtractedItem, *, source: Source, municipality_code: str, url: str, kind: str
) -> dict[str, Any] | None:
    """抽出結果 1 件をレコードの内容にする。物件番号が無い一覧項目は捨てる。"""
    no = item.value("listing_no")
    if not no:
        if kind == "listing_detail":
            no = url.rstrip("/").rsplit("/", 1)[-1] or url  # 詳細ページは URL 末尾で代替
        else:
            # 物件番号を持たない一覧（例: 小川村）は、題名から安定 ID を作る。
            # 「【ご成約済】」等の状態接頭辞は除いてから採番し、状態が変わっても同一物件とみなす。
            title = (item.free.get("title") or "").strip()
            base = re.sub(r"^【[^】]*】", "", title).strip()
            if not base:
                return None
            no = "T" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]
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
    sanitize_address_fields(content)
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
            normalized = sum(1 for r in store.records.values() if sanitize_address_fields(r))
            if normalized:
                log.info("%s: %d 件の所在地から番地を除去", source.id, normalized)
            assert_no_street_numbers(store.all(), where=source.id)
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
        return [*site_redirects.www_to_apex(ws.site.base_url), *affiliates.redirects()]

    def eval_dir(self, ws: Workspace) -> Path | None:
        return ws.fixtures_dir / "eval"

    def pii_policy(self, ws: Workspace) -> Any:
        """生成ページの公開前 PII 検査に使うポリシー（自治体の代表電話・代表メールを許可）。"""
        return pages._pii_policy(ws)


service = AkiyaAtlasService()
