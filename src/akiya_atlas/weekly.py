"""日次パイプラインの 1 週間をまとめる（実行時間・差分件数・費用・自己修復・取り下げ）。

`data/runs/` に毎回残る実行レポートだけを読む。相手サイトには一切アクセスしない。
タグを打つか、まだ様子を見るかの判断材料にする（sitemill ADR 0014 の着手条件と同じ入力）。
"""

from __future__ import annotations

import json
import os
import subprocess
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


def collect(
    ws: Workspace, *, days: int = 7, now: datetime | None = None, source: str = "ci"
) -> list[DayRow]:
    """直近 N 日の実行レポートを日ごとにまとめる（ファイルだけを読む）。

    source は "ci"（日次パイプラインだけ）/ "local"（手元の作業だけ）/ "all"。
    バックフィルのような手元の実行を混ぜると、日次の所要時間と費用を読み違えるため
    既定は "ci" にしている。古いレポートには印が無いので "all" でだけ数える。
    """
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
        is_ci = bool(data.get("ci"))
        if (source == "ci" and not is_ci) or (source == "local" and is_ci):
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


@dataclass
class TakedownScope:
    """取り下げがいま隠しているもの。依頼の件数ではなく、ページで数える。"""

    pages: int = 0
    per_request: list[tuple[int, int]] = field(default_factory=list)  # (Issue 番号, ページ数)
    unscoped: int = 0  # 対象が広すぎて自動では適用しなかった依頼
    other_issues: dict[str, int] = field(default_factory=dict)


def takedown_scope(ws: Workspace) -> TakedownScope:
    """取り下げで非表示になっている掲載中の物件ページを数える（ADR 0016 追記）。

    2026-09-12 のテスト送信は県のページを指していて、長野県の物件を 5 日間隠した。そのあいだ
    週次には「非表示にしているページ: 1 件」と出ていた。数えていたのは依頼の件数だった。
    """
    from akiya_atlas.data import Dataset
    from akiya_atlas.pages import hidden_listing_counts
    from akiya_atlas.takedown import TakedownList

    tl = TakedownList.load(ws)
    paths = tuple(t.path for t in tl.items)
    counts = hidden_listing_counts(Dataset.load(ws), paths) if paths else {}
    return TakedownScope(
        pages=sum(counts.values()),
        per_request=[(t.issue, counts.get(t.path, 0)) for t in tl.items],
        unscoped=len(tl.unscoped),
        other_issues=dict(tl.other_issues),
    )


def takedown_lines(scope: TakedownScope) -> list[str]:
    """報告用の行。公開リポジトリの Issue や実行の要約に出すので、対象のパスは書かない。

    パスを書くと、隠した物件がどれかを公開の場で示すことになる。依頼の中身は非公開の ops 側で見る。
    """
    out = [
        f"- 取り下げにより {scope.pages} ページを非表示（依頼 {len(scope.per_request)} 件）"
        + (f"（他ラベルの開いている Issue: {scope.other_issues}）" if scope.other_issues else "")
    ]
    if scope.per_request:
        each = "、".join(f"#{issue} {n} ページ" for issue, n in scope.per_request)
        out.append(f"  - 依頼ごと（ops の Issue）: {each}")
    if scope.unscoped:
        out.append(
            f"- 対象が物件・市町村のページではないため、自動では適用していない依頼: "
            f"{scope.unscoped} 件（ops で対象を確かめる）"
        )
    return out


def reselections(
    ws: Workspace, *, days: int = 7, now: datetime | None = None
) -> list[dict[str, Any]]:
    """heal が data/sources を書き換えた記録のうち、直近 days 日のもの。"""
    path = ws.runs_dir / "heal-reselections.jsonl"
    if not path.is_file():
        return []
    edge = (now or datetime.now(UTC)) - timedelta(days=days)
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        at = _dt(row.get("at"))
        if at is None or at >= edge:
            out.append(row)
    return out


def _gh_json(args: list[str]) -> Any:
    """gh の出力を JSON として読む。取れなければ None を返す。"""
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True, encoding="utf-8", timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def actions_minutes(
    repo: str, *, days: int = 7, now: datetime | None = None, limit: int = 100
) -> dict[str, Any]:
    """直近 days 日に GitHub Actions が使った分数（課金される値）を合計する。

    無料枠 2,000 分に近づいたら気づけるようにする。取れなかったときは error を返し、
    「0 分」とは区別する（数字が無いことと、集計が壊れていることを混ぜない）。
    """
    since = ((now or datetime.now(UTC)) - timedelta(days=days)).date().isoformat()
    runs = _gh_json(
        [
            "api",
            "-X",
            "GET",
            f"repos/{repo}/actions/runs",
            "-f",
            f"created=>={since}",
            "-f",
            f"per_page={limit}",
            "--jq",
            "[.workflow_runs[] | {id: .id, name: .name}]",
        ]
    )
    if runs is None:
        return {"error": "GitHub API から実行一覧を取れなかった"}
    by_workflow: dict[str, float] = {}
    total = 0.0
    measured = False  # 課金値が取れず、実測時間で数えた回があるか
    for run in runs:
        got = _gh_json(
            [
                "api",
                f"repos/{repo}/actions/runs/{run['id']}/timing",
                "--jq",
                "{billable: ([.billable[]?.total_ms] | add // 0), ran: (.run_duration_ms // 0)}",
            ]
        )
        if not isinstance(got, dict):
            continue
        # 課金される値が 0 で返るアカウント設定がある。そのときは実測の所要時間で数える
        ms = float(got.get("billable") or 0)
        if ms <= 0:
            ms = float(got.get("ran") or 0)
            measured = measured or ms > 0
        minutes = ms / 60000.0
        total += minutes
        by_workflow[run["name"]] = by_workflow.get(run["name"], 0.0) + minutes
    return {
        "minutes": total,
        "runs": len(runs),
        "by_workflow": by_workflow,
        "truncated": len(runs) >= limit,
        "measured": measured,
    }


def _cell(value: Any) -> str:
    text = str(value or "").replace("|", "｜").strip()
    return text or "—"


def report(
    ws: Workspace,
    *,
    days: int = 7,
    now: datetime | None = None,
    source: str = "ci",
    repo: str | None = None,
) -> list[str]:
    """報告用の行を返す（Markdown の表）。"""
    rows = collect(ws, days=days, now=now, source=source)
    scope = takedown_scope(ws)
    kind = {"ci": "日次パイプライン", "local": "手元の実行", "all": "すべての実行"}[source]
    out = [
        f"## {kind}の直近 {days} 日",
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
        f"状態を見直し {total.heal_downgraded} 件 / 再巡回 {total.heal_recrawl} 件",
        *takedown_lines(scope),
        f"- 失敗した工程: {len(total.errors)} 件"
        + (f" — {total.errors[:3]}" if total.errors else ""),
        f"- 1 日あたりの平均: {total.seconds / max(len(rows), 1) / 60:.1f} 分 / "
        f"${total.cost / max(len(rows), 1):.2f}",
    ]

    picks = reselections(ws, days=days, now=now)
    out += ["", f"## 今週 heal が選び直した自治体（{len(picks)} 件）", ""]
    if picks:
        out += ["| 自治体 | 旧 URL | 新 URL | 理由 |", "| --- | --- | --- | --- |"]
        for r in picks:
            out.append(
                f"| {_cell(r.get('name') or r.get('source_id'))} | {_cell(r.get('old_url'))} "
                f"| {_cell(r.get('new_url'))} | {_cell(r.get('reason'))} |"
            )
    else:
        out.append("この期間に掲載ページを選び直した自治体はありません。")

    repo = repo or os.environ.get("GH_REPO", "")
    out += ["", "## GitHub Actions の実行時間", ""]
    if not repo:
        out.append("リポジトリが分からないので集計していません（`--repo` か `GH_REPO` で渡す）。")
        return out
    used = actions_minutes(repo, days=days, now=now)
    if used.get("error"):
        out.append(f"取れませんでした（{used['error']}）。残りは Billing の画面で確かめる。")
        return out
    monthly = used["minutes"] / max(days, 1) * 30
    out += [
        f"- 直近 {days} 日: **{used['minutes']:.0f} 分**（{used['runs']} 回の実行）",
        f"- 1 か月に直すと **{monthly:.0f} 分**。private の無料枠 2,000 分の {monthly / 20:.0f}%",
    ]
    if used.get("measured"):
        out.append(
            "  - GitHub が課金値を 0 で返すので、実行の所要時間で数えている"
            "（請求の値とは数分ずれる）"
        )
    for name, minutes in sorted(used["by_workflow"].items(), key=lambda kv: -kv[1]):
        out.append(f"  - {name}: {minutes:.0f} 分")
    if used.get("truncated"):
        out.append("  - 実行の数が上限に当たったので、ここに出ているのは一部（実際はもっと多い）")
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


def write_snapshot(ws: Workspace, *, days: int = 7, source: str = "ci") -> Path:
    """まとめを data/runs/weekly-<日付>.json に残す（次回との比較用）。"""
    rows = collect(ws, days=days, source=source)
    path = ws.runs_dir / f"weekly-{datetime.now(UTC).date().isoformat()}.json"
    scope = takedown_scope(ws)
    path.write_text(
        json.dumps(
            {
                "days": days,
                "source": source,
                "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
                "per_day": [vars(r) | {"cost": round(r.cost, 4)} for r in rows],
                "month_estimate": month_estimate(rows),
                "takedown_hidden_pages": scope.pages,
                "takedown_requests": len(scope.per_request),
                "takedown_unscoped": scope.unscoped,
                "other_open_issues": scope.other_issues,
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
