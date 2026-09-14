"""補助制度の全国収集: 県ごとに 探索 → 巡回 → 抽出 を回す（再開可能）。

進捗は data/runs/subsidies.json に県ごとに記録する。途中で止まっても同じコマンドを再実行すれば、
済んだ県を飛ばして続きから進む。GitHub Actions の無料枠を使わずに手元の PC で回す前提
（ADR 0013・0008）。バックフィルと同じ形にしてある。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

import yaml
from sitemill import commands
from sitemill.settings import Workspace
from sitemill.store.jsonio import read_json, write_json

from akiya_atlas.backfill import _now, prefectures
from akiya_atlas.expand import collect_subsidy_pages

log = logging.getLogger(__name__)

# Haiku 4.5 の単価（$/100 万トークン）。weekly.py と同じ値
PRICE_IN = 1.0
PRICE_OUT = 5.0


def progress_path(ws: Workspace):
    return ws.runs_dir / "subsidies.json"


def load_progress(ws: Workspace) -> dict[str, Any]:
    data = read_json(progress_path(ws)) or {}
    data.setdefault("prefectures", {})
    return data


def save_progress(ws: Workspace, data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    write_json(progress_path(ws), data)


def subsidy_source_ids(ws: Workspace, slug: str) -> list[str]:
    """その県で補助制度のページを持つ source id。"""
    path = ws.sources_dir / f"{slug}-auto.yaml"
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    for s in data.get("sources") or []:
        if any(p.get("kind") == "subsidy" for p in (s.get("pages") or [])):
            out.append(str(s["id"]))
    return out


def count_subsidies(ws: Workspace, ids: Sequence[str]) -> int:
    total = 0
    for sid in ids:
        path = ws.data_dir / "subsidies" / f"{sid}.jsonl"
        if path.is_file():
            total += sum(
                1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
            )
    return total


def run_subsidy_backfill(
    rt: commands.Runtime,
    *,
    only: Sequence[str] | None = None,
    limit: int = 4,
    force: bool = False,
    workers: int | None = None,
    echo: Callable[[str], None] = print,
) -> dict[str, Any]:
    """県を順に処理し、進捗を都度保存する。1 県で失敗しても止めない。

    県の中では市町村単位で並列に動く（探索・巡回・抽出とも）。県は順に処理する。
    """
    ws = rt.ws
    workers = max(1, workers or ws.site.crawl.max_workers)
    prog = load_progress(ws)
    table = prog["prefectures"]
    failed: list[str] = []
    for name, slug in prefectures(only):
        entry = table.setdefault(slug, {"name": name})
        if not force and entry.get("done"):
            echo(f"{name}: 済み（スキップ。やり直すなら --force）")
            continue
        t0 = time.monotonic()
        try:
            with rt.client() as client:
                lines = collect_subsidy_pages(ws, name, client=client, limit=limit, workers=workers)
                found_requests = client.request_count
            for line in lines[-1:]:
                echo(f"{name}: {line}")
            ids = subsidy_source_ids(ws, slug)
            if ids:
                crawl = commands.cmd_crawl(rt, ids, workers=workers)
                extract = commands.cmd_extract(rt, ids, workers=workers)
                llm = extract.llm.model_dump() if extract.llm else {}
                cost = (
                    llm.get("input_tokens", 0) / 1e6 * PRICE_IN
                    + llm.get("output_tokens", 0) / 1e6 * PRICE_OUT
                )
                entry["crawl"] = crawl.stages.get("crawl", {})
                entry["extract"] = extract.stages.get("extract", {})
                # やり直しで抽出するものが無かったときに、記録済みの費用を 0 で塗り潰さない
                if llm.get("calls"):
                    entry["llm"] = {**llm, "cost_usd": round(cost, 4)}
            entry["municipalities"] = len(ids)
            entry["subsidies"] = count_subsidies(ws, ids)
            entry["discover_requests"] = found_requests
        except Exception as e:  # noqa: BLE001
            entry["error"] = f"{type(e).__name__}: {e}"[:300]
            entry["minutes"] = round((time.monotonic() - t0) / 60, 1)
            save_progress(ws, prog)
            failed.append(name)
            log.exception("%s: 失敗", name)
            echo(f"{name}: 失敗（続行）: {entry['error']}")
            continue
        entry.pop("error", None)
        entry["done"] = _now()
        entry["minutes"] = round((time.monotonic() - t0) / 60, 1)
        save_progress(ws, prog)
        echo(
            f"{name}: 完了 {entry['minutes']}分 / 自治体 {entry['municipalities']} "
            f"/ 制度 {entry['subsidies']} 件 / ${entry.get('llm', {}).get('cost_usd', 0):.2f}"
        )
    if failed:
        echo(f"失敗した県: {'・'.join(failed)}（直してから同じコマンドを再実行する）")
    return prog


def status_table(ws: Workspace) -> list[str]:
    """進捗の一覧（`--status` で出す）。"""
    prog = load_progress(ws)
    table = prog.get("prefectures", {})
    out = ["| 県 | 状態 | 自治体 | 制度 | 分 | 費用 |", "| --- | --- | ---: | ---: | ---: | ---: |"]
    done = subs = munis = 0
    cost = 0.0
    for name, slug in prefectures():
        e = table.get(slug)
        if not e:
            out.append(f"| {name} | 未 | — | — | — | — |")
            continue
        state = "済" if e.get("done") else ("失敗" if e.get("error") else "途中")
        c = float(e.get("llm", {}).get("cost_usd", 0) or 0)
        out.append(
            f"| {name} | {state} | {e.get('municipalities', '—')} | {e.get('subsidies', '—')} "
            f"| {e.get('minutes', '—')} | ${c:.2f} |"
        )
        done += 1 if e.get("done") else 0
        subs += int(e.get("subsidies") or 0)
        munis += int(e.get("municipalities") or 0)
        cost += c
    out.append(f"| **合計** | {done}/47 県 | {munis} | {subs} | | **${cost:.2f}** |")
    return out
