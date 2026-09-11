"""全国バックフィル: 都道府県ごとに 発見 → 巡回 → 抽出 → 自己修復 を回す（再開可能）。

進捗は data/runs/backfill.json に都道府県×工程で記録する。途中で止まっても同じコマンドを
再実行すれば、済んだ工程を飛ばして続きから進む。GitHub Actions の無料枠を使わずにローカル PC や
Raspberry Pi で回す前提（ADR 0008）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sitemill import commands
from sitemill.settings import Workspace
from sitemill.store.jsonio import read_json, write_json

from akiya_atlas.expand import discover_and_write
from akiya_atlas.municipalities import PREFECTURE_NAMES, PREFECTURE_SLUGS

log = logging.getLogger(__name__)

STAGES: tuple[str, ...] = ("discover", "crawl", "extract", "heal")


def progress_path(ws: Workspace) -> Path:
    return ws.runs_dir / "backfill.json"


def load_progress(ws: Workspace) -> dict[str, Any]:
    data = read_json(progress_path(ws)) or {}
    data.setdefault("prefectures", {})
    return data


def save_progress(ws: Workspace, data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    write_json(progress_path(ws), data)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def prefectures(only: Sequence[str] | None = None) -> list[tuple[str, str]]:
    """(県名, スラッグ) を JIS コード順で返す。

    only には県名・スラッグ・2 桁コードを混ぜて指定できる。"""
    rows = [(PREFECTURE_NAMES[c], PREFECTURE_SLUGS[c], c) for c in sorted(PREFECTURE_NAMES)]
    if only:
        wanted = {o.strip() for o in only if o.strip()}
        rows = [r for r in rows if r[0] in wanted or r[1] in wanted or r[2] in wanted]
    return [(name, slug) for name, slug, _ in rows]


def source_ids_for(rt: commands.Runtime, slug: str) -> list[str]:
    """その県で巡回対象になっている source id（自動発見・手動登録の両方）。

    全国分では source が 1,700 件になるので、市町村の県は 1 回だけ読んで辞書で引く
    （source ごとに読み直すと yaml の再解析で 1 県あたり数分かかる）。
    """
    from akiya_atlas.data import load_municipalities

    pref_of = {m.id: m.prefecture_slug for m in load_municipalities(rt.ws)}
    return [
        s.id
        for s in rt.service.sources(rt.ws)
        if s.crawlable and (s.id.startswith(slug + "-") or pref_of.get(s.id) == slug)
    ]


def status_table(ws: Workspace, only: Sequence[str] | None = None) -> list[str]:
    prog = load_progress(ws)["prefectures"]
    lines = []
    for name, slug in prefectures(only):
        entry = prog.get(slug, {})
        marks = " ".join(f"{st}={'済' if entry.get(st) else '－'}" for st in STAGES)
        extra = entry.get("discover_stats")
        stats = (
            f" crawl={extra.get('adopted_crawl')} link_only={extra.get('adopted_link_only')}"
            f" pending={extra.get('pending')}"
            if extra
            else ""
        )
        lines.append(f"{name:<5} {slug:<10} {marks}{stats}")
    return lines


def run_backfill(
    rt: commands.Runtime,
    *,
    only: Sequence[str] | None = None,
    workers: int | None = None,
    force: bool = False,
    stages: Sequence[str] = STAGES,
    echo: Callable[[str], None] = print,
) -> dict[str, Any]:
    """都道府県を順に処理し、進捗を都度保存する。

    1 県で失敗しても止めない。全国分は数時間かかるので、1 件の想定外（相手サイトの書式、
    通信の失敗）で残りの県を落とさないほうがよい。失敗は進捗ファイルに残し、最後にまとめて出す。
    """
    ws = rt.ws
    prog = load_progress(ws)
    table = prog["prefectures"]
    wanted = [st for st in STAGES if st in stages]
    failed: list[str] = []
    for name, slug in prefectures(only):
        entry = table.setdefault(slug, {"name": name})
        # backfill 以前に手動で discover 済みの県（長野・沖縄・香川）は発見済みとして扱う
        if not entry.get("discover") and (ws.runs_dir / f"discover-{slug}.json").is_file():
            entry["discover"] = "既存"
        if not force and all(entry.get(st) for st in wanted):
            echo(f"{name}: 済み（スキップ。やり直すなら --force）")
            continue
        t0 = time.monotonic()
        try:
            _run_prefecture(rt, name, slug, entry, prog, wanted, workers, force, echo)
        except Exception as e:  # noqa: BLE001
            entry["error"] = f"{type(e).__name__}: {e}"[:300]
            entry["elapsed_minutes"] = round((time.monotonic() - t0) / 60, 1)
            save_progress(ws, prog)
            failed.append(name)
            log.exception("%s: 失敗", name)
            echo(f"{name}: 失敗（続行）: {entry['error']}")
            continue
        entry.pop("error", None)
        entry["elapsed_minutes"] = round((time.monotonic() - t0) / 60, 1)
        save_progress(ws, prog)
        echo(f"{name}: 完了 {entry['elapsed_minutes']}分")
    if failed:
        echo(f"失敗した県: {'・'.join(failed)}（直してから同じコマンドを再実行する）")
    return prog


def _run_prefecture(  # noqa: PLR0913
    rt: commands.Runtime,
    name: str,
    slug: str,
    entry: dict[str, Any],
    prog: dict[str, Any],
    wanted: Sequence[str],
    workers: int | None,
    force: bool,
    echo: Callable[[str], None],
) -> None:
    """1 県分の 発見→巡回→抽出→自己修復。工程ごとに進捗を保存する。"""
    ws = rt.ws
    t0 = time.monotonic()
    if "discover" in wanted and (force or not entry.get("discover")):
        with rt.client() as client:
            report = discover_and_write(ws, name, client=client, workers=workers)
            requests = client.request_count
        entry["discover"] = _now()
        entry["discover_stats"] = {
            "total": report.total,
            "skipped_existing": report.skipped_existing,
            "adopted_crawl": report.adopted_crawl,
            "adopted_link_only": report.adopted_link_only,
            "pending": report.pending,
            "requests": requests,
            "minutes": round((time.monotonic() - t0) / 60, 1),
        }
        save_progress(ws, prog)
        echo(
            f"{name}: 発見 {entry['discover_stats']['minutes']}分 対象={report.total} "
            f"crawl={report.adopted_crawl} link_only={report.adopted_link_only} "
            f"pending={report.pending}"
        )
    ids = source_ids_for(rt, slug)
    if "crawl" in wanted and (force or not entry.get("crawl")):
        if ids:
            rep = commands.cmd_crawl(rt, ids, workers=workers)
            entry["crawl_stats"] = rep.stages.get("crawl", {})
        entry["crawl"] = _now()
        save_progress(ws, prog)
        echo(f"{name}: 巡回 {len(ids)} source {entry.get('crawl_stats', {})}")
    if "extract" in wanted and (force or not entry.get("extract")):
        if ids:
            rep = commands.cmd_extract(rt, ids, workers=workers)
            entry["extract_stats"] = {
                **rep.stages.get("extract", {}),
                "llm_calls": rep.llm.calls,
                "input_tokens": rep.llm.input_tokens,
                "output_tokens": rep.llm.output_tokens,
            }
        entry["extract"] = _now()
        save_progress(ws, prog)
        echo(f"{name}: 抽出 {entry.get('extract_stats', {})}")
    if "heal" in wanted and (force or not entry.get("heal")):
        if ids:
            rep = commands.cmd_heal(rt, ids)
            entry["heal_stats"] = rep.stages.get("heal", {})
            for note in rep.notes:
                echo(f"  - {note}")
        entry["heal"] = _now()
        save_progress(ws, prog)
