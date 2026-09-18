"""補助制度の取り込みと読み出し（ADR 0012 の追記、2026-09-14）。

自治体の補助金ページから取り出した制度を `data/subsidies/<source id>.jsonl` に持つ。
手で `data/sources/*.yaml` に書いた制度とは別のファイルに分け、読むときに重ねる。

金額・年度・募集期間は原文の引用のまま持ち、値にするのは締切だけ（quote-then-parse、ADR 0004）。
確認日は取得した日を入れる。古くなったものは消さず、画面で鮮度を示す。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sitemill.clock import to_jst
from sitemill.diff.normalize import squash
from sitemill.extract import ExtractedItem
from sitemill.extract.pipeline import prepare_input
from sitemill.extract.quotes import verify_quote
from sitemill.models import Provenance, Source
from sitemill.settings import Workspace
from sitemill.store.raw import RawCache
from sitemill.store.records import RecordStore

from akiya_atlas.deadline import parse_period_end
from akiya_atlas.schema import SUBSIDY_KINDS, Subsidy

log = logging.getLogger(__name__)


def subsidies_path(ws: Workspace, source_id: str) -> Path:
    return ws.data_dir / "subsidies" / f"{source_id}.jsonl"


def subsidy_record_id(source_id: str, name: str) -> str:
    key = f"{source_id}:{' '.join(name.split())}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _quote_of(item: ExtractedItem, target: str) -> str | None:
    fv = item.fields.get(target)
    return (fv.quote or None) if fv is not None else None


def item_to_subsidy(
    item: ExtractedItem, *, source_id: str, url: str, checked_on: Any
) -> Subsidy | None:
    """抽出した 1 件を Subsidy にする。名前が無いものは捨てる。"""
    name = " ".join((item.free.get("name") or item.raw.get("name") or "").split())
    if not name:
        return None
    kind = (item.free.get("kind") or item.raw.get("kind") or "").strip()
    if kind not in SUBSIDY_KINDS:
        kind = "判定できず"
    scope = (item.free.get("scope") or item.raw.get("scope") or "").strip()
    if scope not in ("空き家", "住宅一般"):
        scope = "空き家"  # 判断が無ければ空き家の側に寄せる（取りこぼしを避ける）
    period = item.fields.get("period_end")
    return Subsidy(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        scope=scope,  # type: ignore[arg-type]
        url=url,
        summary=(item.free.get("summary") or "")[:100],
        amount_text=item.value("amount_text") or _quote_of(item, "amount_text"),
        checked_on=checked_on,
        kind_quote=(item.free.get("kind_quote") or None) if kind == "判定できず" else None,
        year_text=item.value("year_text") or _quote_of(item, "year_text"),
        period_text=_quote_of(item, "period_end"),
        period_end=period.value if period is not None and period.ok else None,
        source_id=source_id,
    )


# 引用した事実。どれかが動いたときだけ、区分と要約も読み直す
SUBSIDY_FACT_KEYS = ("name", "url", "amount_text", "year_text", "period_text", "period_end")


def merge_subsidy(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """引用した事実が前回と同じなら、区分・対象・要約は前回のまま残す。

    同じ本文でも LLM は言い直す（2026-09-18 の一晩で、区分が入れ替わったのが 4 件、
    要約の言い直しが 51 件。区分が変わるとバッジと絞り込みの所属まで変わる）。
    まだ「判定できず」のものは、判断が付いたときに受け取れるよう凍結しない。
    """
    out = dict(new)
    if not all(existing.get(k) == out.get(k) for k in SUBSIDY_FACT_KEYS):
        return out
    for key in ("summary", "scope"):
        if existing.get(key):
            out[key] = existing[key]
    if existing.get("kind") and existing.get("kind") != "判定できず":
        out["kind"] = existing["kind"]
        out["kind_quote"] = existing.get("kind_quote")
    return out


def ingest_subsidies(
    ws: Workspace,
    *,
    source: Source,
    url: str,
    items: list[ExtractedItem],
    provenance: Provenance,
) -> dict[str, int]:
    """抽出した補助制度を保存する。`ingest` から種別 subsidy のときに呼ぶ。"""
    store = RecordStore(subsidies_path(ws, source.id))
    counts = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0}
    now: datetime = (
        provenance.extractor.extracted_at if provenance.extractor else provenance.fetched_at
    )
    checked_on = to_jst(now).date()
    for item in items:
        row = item_to_subsidy(item, source_id=source.id, url=url, checked_on=checked_on)
        if row is None:
            counts["skipped"] += 1
            continue
        result = store.upsert(
            subsidy_record_id(source.id, row.name),
            row.model_dump(mode="json"),
            now=now,
            provenance=provenance.model_dump(mode="json"),
            merge=merge_subsidy,
        )
        counts[result] += 1
    store.save()
    log.info("%s %s: 補助制度 %s", source.id, url, counts)
    return counts


def load_subsidies(ws: Workspace, source_id: str) -> list[Subsidy]:
    """取り込み済みの補助制度。読めない行は飛ばす（1 行の壊れでページを落とさない）。"""
    path = subsidies_path(ws, source_id)
    if not path.is_file():
        return []
    out: list[Subsidy] = []
    for row in RecordStore(path).all():
        if row.get("status") == "removed":
            continue
        try:
            out.append(Subsidy.model_validate(row))
        except ValueError:
            log.warning("%s: 読めない補助制度の行を飛ばした", source_id)
    return out


# ---------------------------------------------------------------------------
# 締切の読み直し（2026-09-17）
# ---------------------------------------------------------------------------


@dataclass
class DeadlineChange:
    source_id: str
    name: str
    period_text: str
    before: str | None
    after: str | None


@dataclass
class DeadlineReparse:
    changes: list[DeadlineChange] = field(default_factory=list)
    # 締切が空の行は、引用が本文に無かった（quote_not_in_source）のか読めなかったのかが
    # 残っていない。
    # 新しい読み取りで日付が取れても、本文と照合できなかったものは値にしない
    unverified: int = 0
    no_cache: int = 0


def _quote_in_page(
    raw: RawCache, row: dict[str, Any], source: Source | None, max_chars: int
) -> bool | None:
    """引用が抽出時の本文に実在するか。本文のキャッシュが無ければ None。"""
    prov = row.get("provenance") or {}
    url, digest = prov.get("source_url"), prov.get("content_hash")
    if not url or not raw.matches_state(row["source_id"], url, digest):
        return None
    html = raw.load_text(row["source_id"], url)
    if html is None:
        return None
    page = prepare_input(
        html,
        url=url,
        kind="subsidy",
        selector=source.content_selector if source else None,
        max_chars=max_chars,
    )
    found, _ = verify_quote(row["period_text"], squash(page.text))
    return found


def reparse_deadlines(
    ws: Workspace, sources: dict[str, Source], *, now: datetime, apply: bool
) -> DeadlineReparse:
    """保存済みの募集期間の引用から、締切を読み直す。LLM は呼ばない。

    締切が入っていた行は、引用が本文と照合済みなので、新しい読み取りの結果で置き換える
    （値が消えることもある）。締切が空だった行は、本文のキャッシュと照合できたときだけ値にする。
    """
    raw = RawCache(ws.raw_dir)
    max_chars = ws.site.llm.max_input_chars
    result = DeadlineReparse()
    for path in sorted((ws.data_dir / "subsidies").glob("*.jsonl")):
        store = RecordStore(path)
        touched = False
        for row in store.all():
            text = row.get("period_text")
            if not text:
                continue
            before = row.get("period_end")
            value, _ = parse_period_end(text)
            after = value.isoformat() if value is not None else None
            if after == before:
                continue
            if before is None:
                found = _quote_in_page(raw, row, sources.get(row["source_id"]), max_chars)
                if found is None:
                    result.no_cache += 1
                    continue
                if not found:
                    result.unverified += 1
                    continue
            result.changes.append(
                DeadlineChange(row["source_id"], row.get("name", ""), text, before, after)
            )
            if apply:
                row["period_end"] = after
                row.setdefault("history", []).append(
                    {
                        "at": now.isoformat(),
                        "event": "updated",
                        "fields": ["period_end"],
                        "reason": "deadline_reparsed",
                    }
                )
                touched = True
        if touched:
            store.save()
    return result
