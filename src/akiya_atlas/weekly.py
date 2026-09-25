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

# いまのリポジトリ（hikuzawa/akiya-atlas）を作り直した時刻。GitHub の API はこれより前の実行を
# 返さない（旧リポジトリ akiya-atlas-archive にある）。期間がこれをまたぐときは、実行時間を
# この時刻以降の日数で割る。7 で割ると、実行が無かった日を 0 分として数えて少なく見積もる
REPOSITORY_SINCE = datetime(2026, 9, 15, 22, 51, 43, tzinfo=UTC)
# 1 か月の見込みを出すのに要る日数。作り直した直後の 1 日は確認の手動実行が集中していて、
# 30 倍すると 1,987 分と出た（2026-09-17）。数日たまるまでは見込みを出さない
MIN_DAYS_FOR_MONTHLY = 3.0
# 同じ失敗の繰り返しは、ここに挙げる件数だけ種類ごとに出す
ERROR_KINDS_SHOWN = 5
# robots.txt のせいで巡回できない日がこれだけ続いたら、ホスト名を出す
# （その自治体の更新が止まっている）
ROBOTS_STALE_DAYS = 3
# 巡回を見送った理由のうち robots.txt によるもの。エンジン（sitemill.fetch.robots / crawler）は
# 「取得できない」「robots.txt 202: 今回は巡回しない」のような異常な応答、
# 「robots.txt により拒否」の 3 通りを書く。どれも続けばその自治体の更新は止まるので、まとめて拾う
ROBOTS_MARK = "robots.txt"


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
    """直近 days 日に GitHub Actions が使った分数を合計し、1 か月に直した見込みを出す。

    public リポジトリなので標準ランナーの時間は枠を消費しない。数えるのは実行の重さの推移を
    見るため。取れなかったときは error を返し、「0 分」とは区別する（数字が無いことと、
    集計が壊れていることを混ぜない）。
    """
    now = now or datetime.now(UTC)
    window_start = now - timedelta(days=days)
    # 作り直す前の実行は API から見えないので、見えている期間だけで割る
    covered_from = max(window_start, REPOSITORY_SINCE)
    covered_days = max((now - covered_from).total_seconds() / 86400, 1.0)
    since = window_start.date().isoformat()
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
        "covered_days": covered_days,
        "partial": covered_from > window_start,
        "monthly": total / covered_days * 30,
    }


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or url


def _robots_url(err: str) -> str:
    """実行レポートの 1 行（`URL: robots.txt …`）から URL を取り出す。"""
    return err.split(": " + ROBOTS_MARK, 1)[0]


def robots_reason(error: str) -> str:
    """見送った理由を、打ち手が分かる言葉にする。"""
    import re

    if "拒否" in error:
        return "robots.txt で拒否。巡回先の URL を見直す"
    m = re.search(r"robots\.txt (\d{3})", error)
    if m:
        return f"robots.txt が {m.group(1)} を返す。相手に当たり直す"
    return "robots.txt を取得できない。相手に当たり直す"


def robots_failures(
    ws: Workspace, *, days: int = 7, now: datetime | None = None
) -> tuple[list[str], int, list[tuple[str, int | None, str]]]:
    """robots.txt のせいで巡回できなかったホスト。(ホスト一覧, 延べ回数, 止まっているホスト)。

    robots.txt が読めない・拒否されていると、その回は巡回しない（正しい判断）。**続くとその
    自治体だけ静かに更新が止まる**ので、週次で気づけるようにする
    （2026-09-24。8 ホストが 1 晩でまとめて失敗した回があった）。

    最初は「取得できない」だけを拾っていて、robots.txt が 202 を返し続けて 10 日止まっていた
    茨城県河内町と、拒否された検索ページを巡回先にしていた北海道当麻町を見逃した（2026-09-26）。
    いまはエンジンが書く robots の理由をすべて拾い、止まっているホストには理由を添える。

    数と延べ回数は期間内の実行レポートから数える。止まっているかどうかは実行レポートでは
    決められない（巡回間隔の適応で、その晩は対象外だったのか見送ったのかが混ざる）ので、
    巡回の状態（`data/state/crawl.json`）にいま robots の理由が残っているホストを見て、
    **最後に取得できた日からの日数**で判断する。`ROBOTS_STALE_DAYS` 日以上なら名前を出す。
    一度も取得できていないホストは日数を None で返す。
    """
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    hosts: dict[str, int] = {}
    for path in sorted(ws.runs_dir.glob("*-crawl.json")):
        if path.name.startswith(("latest-", "backfill", "discover-")):
            continue  # 直近の写しと手元の作業。二重に数えない
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        started = _dt(data.get("started_at"))
        if started is None or started < since:
            continue
        for err in data.get("errors") or []:
            if ROBOTS_MARK in str(err):
                host = _host(_robots_url(str(err)))
                hosts[host] = hosts.get(host, 0) + 1

    stalled: dict[str, tuple[int | None, str]] = {}
    state = ws.state_dir / "crawl.json"
    try:
        urls = (json.loads(state.read_text(encoding="utf-8")) or {}).get("urls") or {}
    except (OSError, json.JSONDecodeError):
        urls = {}
    for url, st in urls.items():
        error = str(st.get("error") or "")
        if ROBOTS_MARK not in error:
            continue
        host = _host(url)
        fetched = _dt(st.get("fetched_at"))
        age = None if fetched is None else (now - fetched).days
        known = stalled.get(host)
        # 同じホストの中では、いちばん長く止まっている URL の日数を採る
        if known is None or (known[0] is not None and (age is None or age > known[0])):
            stalled[host] = (age, robots_reason(error))
    named = sorted(
        (
            (h, age, reason)
            for h, (age, reason) in stalled.items()
            if age is None or age >= ROBOTS_STALE_DAYS
        ),
        key=lambda row: (row[1] is not None, -(row[1] or 0), row[0]),
    )
    return sorted(hosts), sum(hosts.values()), named


def robots_lines(
    hosts: list[str], times: int, stalled: list[tuple[str, int | None, str]]
) -> list[str]:
    """週次に出す行。1 晩だけの見送りは数だけ、続いているものは名前と理由つきで。"""
    if not hosts and not stalled:
        return ["- robots.txt で巡回できなかったホスト: なし"]
    out = [f"- robots.txt で巡回できなかったホスト: **{len(hosts)}**（延べ {times} 回）"]
    if stalled:
        out.append(
            f"  - **{ROBOTS_STALE_DAYS} 日以上取得できていない**"
            "（この自治体の掲載は更新が止まっている）"
        )
        for host, age, reason in stalled:
            since = "一度も取得できていない" if age is None else f"最終取得から {age} 日"
            out.append(f"    - {host}（{since}）: {reason}")
    return out


def error_kinds(errors: list[str]) -> list[tuple[str, int]]:
    """同じ失敗の繰り返しをまとめる。多い順。

    毎日同じ URL が 404 を返すと、1 週間で同じ行が数十件並び、他の失敗が埋もれる。
    千葉県睦沢町の存在しないページ送りは、1 週間の失敗 98 件のうち毎回の先頭を占めていた。
    """
    counts: dict[str, int] = {}
    for err in errors:
        counts[err] = counts.get(err, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


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
        "| 日付 | 処理時間 | 取得 | 変化 | 抽出 | 取込 | 新規/更新 "
        "| LLM 呼び出し | LLM 費用 | heal | ページ |",
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
        f"- 処理時間の 1 日平均: {total.seconds / max(len(rows), 1) / 60:.1f} 分"
        "（巡回から配置までの工程の合計。Actions の実行時間とは別）",
    ]
    kinds = error_kinds(total.errors)
    if kinds:
        out.append(f"- 失敗した工程: **{len(kinds)} 種類・延べ {len(total.errors)} 件**")
        for message, n in kinds[:ERROR_KINDS_SHOWN]:
            out.append(f"  - {n} 回: {message}")
        if len(kinds) > ERROR_KINDS_SHOWN:
            rest = sum(n for _, n in kinds[ERROR_KINDS_SHOWN:])
            out.append(f"  - 他 {len(kinds) - ERROR_KINDS_SHOWN} 種類・延べ {rest} 件")
    else:
        out.append("- 失敗した工程: なし")
    out += robots_lines(*robots_failures(ws, days=days, now=now))

    # LLM の費用は Actions の実行時間と別の節に置く（並べると同じ請求に見える）
    per_day = total.cost / max(len(rows), 1)
    out += [
        "",
        "## LLM の費用（Anthropic）",
        "",
        f"- 直近 {days} 日: **${total.cost:.2f}**（呼び出し {total.llm_calls} 回、"
        f"入力 {total.input_tokens:,} / 出力 {total.output_tokens:,} トークン）",
        f"- 1 日平均 ${per_day:.2f}。1 か月に直すと **約 ${per_day * 30:.2f}**",
        f"  - 単価は Haiku 4.5（入力 ${PRICE_IN:g} / 出力 ${PRICE_OUT:g}、"
        "100 万トークンあたり）で計算。"
        "請求の値とはずれることがある",
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
    out.append(f"- 直近 {days} 日: **{used['minutes']:.0f} 分**（{used['runs']} 回の実行）")
    since_jst = (REPOSITORY_SINCE + timedelta(hours=9)).date().isoformat()
    if used["covered_days"] < MIN_DAYS_FOR_MONTHLY:
        out.append(
            f"- 1 か月の見込みは出さない。いまのリポジトリを作り直した {since_jst}（JST）から "
            f"{used['covered_days']:.1f} 日分しかなく、確認の手動実行が多い時期を 30 倍すると外れる"
            f"（{MIN_DAYS_FOR_MONTHLY:g} 日分たまってから出す）"
        )
    else:
        out.append(f"- 1 か月に直すと **約 {used['monthly']:.0f} 分**")
        if used.get("partial"):
            out.append(
                f"  - いまのリポジトリを作り直した {since_jst}（JST）以降の "
                f"{used['covered_days']:.1f} 日分だけで計算した"
            )
    if used.get("partial"):
        out.append("  - 旧リポジトリ（akiya-atlas-archive）の実行は含まない")
    out.append("  - public リポジトリなので、標準ランナーの実行時間は課金されない")
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
    """data/runs の処理時間と LLM 費用を 1 か月に直した見込み（記録用。報告には出さない）。"""
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
