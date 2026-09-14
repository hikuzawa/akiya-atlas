"""補助制度の取り込みと読み出し（ADR 0012 の追記、2026-09-14）。

自治体の補助金ページから取り出した制度を `data/subsidies/<source id>.jsonl` に持つ。
手で `data/sources/*.yaml` に書いた制度とは別のファイルに分け、読むときに重ねる。

金額・年度・募集期間は原文の引用のまま持ち、値にするのは締切だけ（quote-then-parse、ADR 0004）。
確認日は取得した日を入れる。古くなったものは消さず、画面で鮮度を示す。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from sitemill.clock import to_jst
from sitemill.extract import ExtractedItem
from sitemill.models import Provenance, Source
from sitemill.settings import Workspace
from sitemill.store.records import RecordStore

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
    period = item.fields.get("period_end")
    return Subsidy(
        name=name,
        kind=kind,  # type: ignore[arg-type]
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
