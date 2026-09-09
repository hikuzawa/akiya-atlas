"""データの読み込み。sources/*.yaml（自治体と巡回設定）と records/*.jsonl（物件）をまとめて扱う。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from sitemill.diff.state import CrawlState
from sitemill.models import Source
from sitemill.settings import Workspace
from sitemill.store.records import RecordStore

from akiya_atlas.schema import Listing, Municipality


def load_entries(ws: Workspace) -> list[dict[str, Any]]:
    """data/sources/*.yaml の sources 配列を、ファイル名順に連結する。"""
    entries: list[dict[str, Any]] = []
    for path in sorted(ws.sources_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for entry in data.get("sources", []) or []:
            entry.setdefault("_file", path.name)
            entries.append(entry)
    ids = [e.get("id") for e in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("sources の id が重複している")
    return entries


def load_sources(ws: Workspace) -> list[Source]:
    return [Source.model_validate(e) for e in load_entries(ws)]


def municipality_from_entry(entry: dict[str, Any]) -> Municipality | None:
    muni = entry.get("municipality")
    if not muni:
        return None
    payload = {"id": entry["id"], "official_url": entry["official_url"], **muni}
    try:
        return Municipality.model_validate(payload)
    except ValidationError as e:
        raise ValueError(f"{entry['id']} の municipality 設定が不正: {e}") from e


def load_municipalities(ws: Workspace) -> list[Municipality]:
    out: list[Municipality] = []
    for entry in load_entries(ws):
        m = municipality_from_entry(entry)
        if m is not None:
            out.append(m)
    return out


def records_path(ws: Workspace, source_id: str) -> Path:
    return ws.records_dir / f"{source_id}.jsonl"


def load_listings(ws: Workspace, source_id: str) -> list[Listing]:
    out: list[Listing] = []
    for row in RecordStore(records_path(ws, source_id)).all():
        try:
            out.append(Listing.model_validate(row))
        except ValidationError as e:  # 壊れた行は捨てずに警告として残す
            raise ValueError(f"{source_id}: レコードが不正 ({row.get('record_id')}): {e}") from e
    return out


@dataclass
class Dataset:
    sources: list[Source]
    municipalities: list[Municipality]
    listings: list[Listing]
    state: CrawlState
    by_source: dict[str, Source] = field(default_factory=dict)
    muni_by_source: dict[str, Municipality] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_source = {s.id: s for s in self.sources}
        self.muni_by_source = {m.id: m for m in self.municipalities}

    @classmethod
    def load(cls, ws: Workspace) -> Dataset:
        sources = load_sources(ws)
        municipalities = load_municipalities(ws)
        listings: list[Listing] = []
        for m in municipalities:
            listings.extend(load_listings(ws, m.id))
        state = CrawlState.load(ws.state_dir / "crawl.json")
        return cls(sources=sources, municipalities=municipalities, listings=listings, state=state)

    def listings_for(self, muni: Municipality, *, active_only: bool = False) -> list[Listing]:
        rows = [ls for ls in self.listings if ls.source_id == muni.id]
        if active_only:
            rows = [ls for ls in rows if ls.status == "active"]
        return sorted(rows, key=lambda ls: (ls.status != "active", ls.listing_no))

    def prefectures(self) -> list[tuple[str, str]]:
        seen: dict[str, str] = {}
        for m in self.municipalities:
            seen.setdefault(m.prefecture_slug, m.prefecture)
        return sorted(seen.items())

    def municipalities_in(self, prefecture_slug: str) -> list[Municipality]:
        return sorted(
            (m for m in self.municipalities if m.prefecture_slug == prefecture_slug),
            key=lambda m: m.code,
        )

    def last_fetched(self, source_id: str):  # noqa: ANN201 - datetime | None
        times = [s.fetched_at for s in self.state.for_source(source_id) if s.fetched_at]
        return max(times) if times else None
