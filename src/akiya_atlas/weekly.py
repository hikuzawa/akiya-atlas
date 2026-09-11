"""日次パイプラインの 1 週間をまとめる（実行時間・差分件数・費用・自己修復・取り下げ）。

`data/runs/` に毎回残る実行レポートだけを読む。相手サイトには一切アクセスしない。
タグを打つか、まだ様子を見るかの判断材料にする（sitemill ADR 0014 の着手条件と同じ入力）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sitemill.settings import Workspace

# Haiku 4.5 の単価（$/100 万トークン）。モデルを変えたらここも変える
PRICE_IN = 1.0
PRICE_OUT = 5.0


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class DayRow:
    """1 日ぶんの実行の合計。"""

    date: str
    seconds: float = 0.0
    fetched: int = 0
    changed: int = 0
    pages: int = 0
    items: int = 0
    created: int = 0
    updated: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    heal_checked: int = 0
    heal_changed: int = 0
    heal_downgraded: int = 0
    heal_recrawl: int = 0
    build_pages: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def cost(self) -> float:
        return self.input_tokens / 1e6 * PRICE_IN + self.output_tokens / 1e6 * PRICE_OUT


def collect(ws: Workspace, *, days: int = 7, now: datetime | None = None) -> list[DayRow]:
    """直近 N 日の実行レポートを日ごとにまとめる（純関数に近い。ファイルだけを読む）。"""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    rows: dict[str, DayRow] = {}
    for path in sorted(ws.runs_dir.glob("*.json")):
        if path.name.startswith(("latest-", "backfill", "discover-")):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        started = _dt(data.get("started_at"))
        if started is None or started < since:
            continue
        key = started.date().isoformat()
        row = rows.setdefault(key, DayRow(date=key))
        finished = _dt(data.get("finished_at"))
        if finished is not None:
            row.seconds += (finished - started).total_seconds()
        stages: dict[str, Any] = data.get("stages") or {}
        crawl = stages.get("crawl") or {}
        row.fetched += crawl.get("fetched", 0)
        row.changed += crawl.get("changed", 0)
        extract = stages.get("extract") or {}
        row.pages += extract.get("pages", 0)
        row.items += extract.get("items", 0)
        ingest = stages.get("ingest") or {}
        row.created += ingest.get("created", 0)
        row.updated += ingest.get("updated", 0)
        heal = stages.get("heal") or {}
        row.heal_checked += heal.get("checked", 0)
        row.heal_changed += (
            len(heal.get("changed", []) or [])
            if isinstance(heal.get("changed"), list)
            else heal.get("changed", 0)
        )
        row.heal_downgraded += heal.get("downgraded", 0)
        row.heal_recrawl += heal.get("recrawl", 0)
        build = stages.get("build") or {}
        row.build_pages = max(row.build_pages, build.get("pages", 0))
        llm = data.get("llm") or {}
        row.llm_calls += llm.get("calls", 0)
        row.input_tokens += llm.get("input_tokens", 0)
        row.output_tokens += llm.get("output_tokens", 0)
        for err in data.get("errors") or []:
            row.errors.append(f"{data.get('command', '?')}: {str(err)[:120]}")
    return [rows[k] for k in sorted(rows)]


def takedown_count(ws: Workspace) -> tuple[int, dict[str, int]]:
    from akiya_atlas.takedown import TakedownList

    tl = TakedownList.load(ws)
    return len(tl.items), tl.other_issues


def report(ws: Workspace, *, days: int = 7, now: datetime | None = None) -> list[str]:
    """報告用の行を返す（Markdown の表）。"""
    rows = collect(ws, days=days, now=now)
    hidden, others = takedown_count(ws)
    out = [
        f"## 日次パイプラインの直近 {days} 日",
        "",
        "| 日付 | 実行 | 取得 | 変化 | 抽出 | 取込 | 新規/更新 | LLM | 費用 | heal | ページ |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    total = DayRow(date="合計")
    for r in rows:
        out.append(
            f"| {r.date} | {r.seconds / 60:.1f}分 | {r.fetched} | {r.changed} | {r.pages} | "
            f"{r.items} | {r.created}/{r.updated} | {r.llm_calls} | ${r.cost:.2f} | "
            f"{r.heal_checked} | {r.build_pages or '—'} |"
        )
        for name in (
            "seconds fetched changed pages items created updated llm_calls "
            "input_tokens output_tokens heal_checked heal_changed heal_downgraded heal_recrawl"
        ).split():
            setattr(total, name, getattr(total, name) + getattr(r, name))
        total.errors.extend(r.errors)
    out.append(
        f"| **合計** | **{total.seconds / 60:.0f}分** | {total.fetched} | {total.changed} | "
        f"{total.pages} | {total.items} | {total.created}/{total.updated} | {total.llm_calls} | "
        f"**${total.cost:.2f}** | {total.heal_checked} | — |"
    )
    out += [
        "",
        f"- 自己修復: 点検 {total.heal_checked} 件 / 変更 {total.heal_changed} 件 / "
        f"取り下げ {total.heal_downgraded} 件 / 再巡回 {total.heal_recrawl} 件",
        f"- 取り下げ依頼で非表示にしているページ: {hidden} 件"
        + (f"（他ラベルの開いている Issue: {others}）" if others else ""),
        f"- 失敗した工程: {len(total.errors)} 件"
        + (f" — {total.errors[:3]}" if total.errors else ""),
        f"- 1 日あたりの平均: {total.seconds / max(len(rows), 1) / 60:.1f} 分 / "
        f"${total.cost / max(len(rows), 1):.2f}",
    ]
    return out


def month_estimate(rows: list[DayRow]) -> dict[str, float]:
    """1 か月に直した見込み（GitHub Actions の無料枠 2,000 分との比較用）。"""
    if not rows:
        return {"minutes": 0.0, "cost": 0.0}
    days = len(rows)
    return {
        "minutes": sum(r.seconds for r in rows) / 60 / days * 30,
        "cost": sum(r.cost for r in rows) / days * 30,
    }


def write_snapshot(ws: Workspace, *, days: int = 7) -> Path:
    """まとめを data/runs/weekly-<日付>.json に残す（次回との比較用）。"""
    rows = collect(ws, days=days)
    path = ws.runs_dir / f"weekly-{datetime.now(UTC).date().isoformat()}.json"
    hidden, others = takedown_count(ws)
    path.write_text(
        json.dumps(
            {
                "days": days,
                "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
                "per_day": [vars(r) | {"cost": round(r.cost, 4)} for r in rows],
                "month_estimate": month_estimate(rows),
                "takedowns_hidden": hidden,
                "other_open_issues": others,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path
