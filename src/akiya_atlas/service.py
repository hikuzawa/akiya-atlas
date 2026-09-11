"""sitemill の Service 実装。データ・スキーマ・ページ構成はこちらが持つ（sitemill ADR 0006）。"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from sitemill.build.pii import PHONE_RE
from sitemill.diff.state import CrawlState
from sitemill.extract import ExtractedItem, ExtractionSpec
from sitemill.fetch.links import host_of
from sitemill.models import Page, Provenance, Redirect, Source
from sitemill.parse.jp.address import has_street_number, strip_street_number
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


# 題名・要約に混じる地番らしき数字（「宮前3-15の一部」）。価格帯の「300-500万円」も巻き込むが、
# 数値は構造化した項目で持っているので、本文からは落としてよい
LOT_LIKE = re.compile(r"(?<![\d.])\d{1,5}-\d{1,4}(?![\d.㎡m])")


def scrub_lot_numbers(content: dict[str, Any]) -> bool:
    """題名・要約に残った地番と電話番号を落とす。変更があれば True。

    電話番号は仲介業者や担当者の連絡先のことがあり、そのまま載せると公開前の PII 検査で止まる。
    一次情報へのリンクは別に出しているので、本文から連絡先を持ち出す必要はない。
    """
    changed = False
    for key in ("title", "summary"):
        text = content.get(key)
        if not text:
            continue
        scrubbed = PHONE_RE.sub("", LOT_LIKE.sub("", str(text)))
        if scrubbed != text:
            content[key] = re.sub(r"[ 　]{2,}", " ", scrubbed).strip() or None
            changed = True
    return changed


def sanitize_address_fields(content: dict[str, Any]) -> bool:
    """所在地から番地・号・建物名を落とし「市町村＋大字・地区名」までにする（ADR 0002 追記）。

    落とした文字列が title / summary に含まれていればそこからも消す。変更があれば True。
    """
    changed = scrub_lot_numbers(content)
    addr = content.get("address")
    if not addr:
        return changed
    if addr.get("status") != "parsed" or not addr.get("value"):
        # 値として採らなかった引用（quote_not_in_source など）にも番地が入る。記録にも残さない
        quote = addr.get("quote")
        if quote and has_street_number(str(quote)):
            kept, _ = strip_street_number(str(quote))
            addr["quote"] = kept or None
            return True
        return changed
    original = str(addr["value"])
    kept, removed = strip_street_number(original)
    if kept and has_street_number(kept):
        # 想定していない書式（「E棟 新光248番地、F棟 …」のような複数棟の並記）で落としきれない。
        # 番地を載せるくらいなら所在地ごと捨てる。全国分の抽出を止めないための保険。
        kept, removed = "", original
    if kept == original and removed is None:
        return changed
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


def drop_wrong_prices(content: dict[str, Any]) -> bool:
    """売買価格・賃料に、単価や一時金の金額が入っていたら値を落とす。

    抽出時（spec.py）でも弾いているが、以前に保存したレコードを直すためにここでも見る。
    引用は残すので、原文に何が書いてあったかは追える。
    """
    from akiya_atlas.spec import parse_rent, parse_sale_price

    changed = False
    for key, parser in (("price", parse_sale_price), ("rent_monthly", parse_rent)):
        field = content.get(key)
        if not field or field.get("status") != "parsed" or field.get("value") is None:
            continue
        value, note = parser(str(field.get("quote") or ""))
        if value is None:
            # 注記はパーサの判断をそのまま残す（not_a_price か price_unknown か）
            field.update({"value": None, "status": "unparsed", "note": note or "not_a_price"})
            changed = True
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


def _facts_key(content: dict[str, Any]) -> tuple | None:
    """所在地・価格・延床面積がそろっているときだけ作る突き合わせキー（ADR 0009 条件2）。"""
    addr, price, floor = (content.get(k) or {} for k in ("address", "price", "floor_area_m2"))
    if not all(
        f.get("status") == "parsed" and f.get("value") is not None for f in (addr, price, floor)
    ):
        return None
    return (
        content.get("municipality_code"),
        str(addr["value"]),
        int(price["value"]),
        round(float(floor["value"]), 1),
    )


def mark_duplicates(ws: Workspace) -> dict[str, int]:
    """同一物件が複数 source にあるとき、市町村サイト側を正として他を隠す。

    隠す側には `duplicate_of` と `duplicate_reason` を書く。削除はしないので、正の側が
    消えれば次回また表に戻せる。件数は実行レポートに出す。
    """
    from akiya_atlas.data import load_municipalities, load_sources

    munis = {m.id: m for m in load_municipalities(ws)}
    sources = {s.id: s for s in load_sources(ws)}

    def is_own_site(source_id: str) -> bool:
        """その source が市町村自身のサイトか（公式ドメインと同じホストか）。"""
        muni, src = munis.get(source_id), sources.get(source_id)
        if muni is None or src is None:
            return False
        return host_of(muni.bank_url) == host_of(muni.official_url)

    stores = {sid: RecordStore(records_path(ws, sid)) for sid in munis}
    rows: list[tuple[str, dict[str, Any]]] = [
        (sid, rec) for sid, store in stores.items() for rec in store.records.values()
    ]
    groups: dict[tuple, list[tuple[str, dict[str, Any]]]] = {}
    for sid, rec in rows:
        if rec.get("status") != "active":
            continue
        no = normalize_listing_no(str(rec.get("listing_no") or ""))
        if no:
            groups.setdefault(("no", rec.get("municipality_code"), no), []).append((sid, rec))
        facts = _facts_key(rec)
        if facts is not None:
            groups.setdefault(("facts", *facts), []).append((sid, rec))

    counts = {"groups": 0, "hidden": 0, "restored": 0}
    hidden_ids: set[str] = set()
    for key, group in groups.items():
        if len(group) < 2 or len({sid for sid, _ in group}) < 2:
            continue  # 同じ source の中の重複は扱わない（一覧と詳細は既に統合済み）
        counts["groups"] += 1
        group.sort(key=lambda pair: (not is_own_site(pair[0]), pair[0]))
        keeper = group[0][1]
        for _sid, rec in group[1:]:
            rec["duplicate_of"] = keeper.get("record_id")
            rec["duplicate_reason"] = "listing_no" if key[0] == "no" else "address_price_area"
            hidden_ids.add(str(rec.get("record_id")))
            counts["hidden"] += 1
    for _sid, rec in rows:  # 重複でなくなったものは印を外す
        if rec.get("duplicate_of") and str(rec.get("record_id")) not in hidden_ids:
            rec.pop("duplicate_of", None)
            rec.pop("duplicate_reason", None)
            counts["restored"] += 1
    if counts["hidden"] or counts["restored"]:
        for store in stores.values():
            store.save()
    return counts


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

    def finalize(self, ws: Workspace, *, now: datetime) -> dict[str, int]:
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
            wrong = sum(1 for r in store.records.values() if drop_wrong_prices(r))
            if wrong:
                log.info("%s: %d 件の価格から単価・賃料の金額を除去", source.id, wrong)
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
        # 全 source が揃ってから、同じ物件が 2 か所に出ていないかを見る（ADR 0009）
        dups = mark_duplicates(ws)
        if dups["hidden"] or dups["restored"]:
            log.info("重複: %d 件を隠し、%d 件を戻した", dups["hidden"], dups["restored"])
        return dups

    def pages(self, ws: Workspace, *, now: datetime) -> list[Page]:
        return pages.build_pages(ws, Dataset.load(ws), now=now)

    def search_index(self, ws: Workspace) -> Any:
        return pages.search_index(ws, Dataset.load(ws))

    def redirects(self, ws: Workspace) -> list[Redirect]:
        # www / pages.dev → apex の 301 は _redirects では効かない（ドメイン単位は非対応）。
        # Cloudflare の Bulk Redirects で行う（docs/human-tasks.md 9）。ここは ASP の /go/ だけ
        return affiliates.redirects()

    def eval_dir(self, ws: Workspace) -> Path | None:
        return ws.fixtures_dir / "eval"

    def pii_policy(self, ws: Workspace) -> Any:
        """生成ページの公開前 PII 検査に使うポリシー（自治体の代表電話・代表メールを許可）。"""
        return pages._pii_policy(ws)

    def heal(self, ws: Workspace, *, client: Any, source_ids: list[str] | None = None) -> dict:
        """巡回したのに現行 0 件の自治体を候補の選び直しで自己修復する（heal フック）。"""
        from akiya_atlas.expand import heal

        return heal(ws, client=client, source_ids=source_ids)


service = AkiyaAtlasService()
