"""運営主体を 90 日に 1 回確かめ直す（ADR 0018）。

毎晩、確認日（findings の `evidence_checked_on`）の古い順に 20 自治体を見る。LLM は使わない。

- 公式サイト: 記録した公式ホストに届き、トップに市町村名が出るか。届いたのに出なければ解決し直し、
  別のホストになれば「公式サイトが変わった」
- 県の一覧で公式と決めたホストは、県の一覧が今もリンクしているか（県ごとに 1 回だけ取得）
- 根拠の出典ページと空き家バンクのページが今も開けるか

成り立てば確認日を今日にする。成り立たなければ日付は進めず、翌晩も見る。3 晩続いたら選び直し、
URL が変わったら週次に旧新を出す。robots.txt で取れなかったものと、通信できなかったもの
（時間切れ・接続できない・403・429）は、確かめていないので日付も失敗の数も動かさず、7 日後に見直す。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sitemill.classify import PlatformRegistry
from sitemill.clock import jst_now, jst_today
from sitemill.fetch.client import FetchResult, PoliteClient
from sitemill.fetch.links import extract_links, host_of
from sitemill.settings import Workspace
from sitemill.store.jsonio import read_json

from akiya_atlas import expand
from akiya_atlas.official_domains import classify_host

PER_NIGHT = 20  # 1,736 自治体 ÷ 90 日 ≒ 19.3
RETRY_NIGHTS = 3  # この晩数だけ続けて成り立たなければ選び直す
WAIT_DAYS = 7  # 確かめられなかった（robots.txt・通信できない）自治体を、次に見るまでの日数
LOG_NAME = "operator-rechecks.jsonl"
FAIL_KEYS = ("recheck_failures", "recheck_failed_since", "recheck_reason")
WAIT_KEYS = ("recheck_waiting_since", "recheck_waiting_reason", "recheck_last_tried")
# ページの有無について何も言っていない応答。海外の実行環境（GitHub Actions）からの接続を
# 落とす国内サイトがある（japan-open-today の takamatsu.or.jp・teshima-navi.jp が 9/13 から
# 毎晩時間切れ）。「成り立たなかった」に数えると 3 晩で選び直しが走り、毎回同じ結果を繰り返す
REFUSED = (403, 429)


@dataclass
class Outcome:
    slug: str
    index: int
    result: str  # ok / fail / skip / unreachable
    reason: str = ""


@dataclass
class RecheckReport:
    checked: int = 0
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    unreachable: int = 0
    reselected: int = 0
    lines: list[str] = field(default_factory=list)


def eligible(row: dict) -> bool:
    """運営主体が決まっている行だけ。決まっていない行（pending）は人のレビューの側にある。"""
    return bool(row.get("official_url")) and row.get("policy") != "pending"


def waiting(row: dict, today: date) -> bool:
    """確かめられなかったばかりの行。日付が古いまま残るので、待たせないと毎晩の枠を取り続ける。"""
    last = row.get("recheck_last_tried")
    try:
        return bool(last) and (today - date.fromisoformat(str(last))).days < WAIT_DAYS
    except ValueError:
        return False


def pick(
    findings: dict[str, list[dict]], limit: int = PER_NIGHT, today: date | None = None
) -> list[tuple[str, int]]:
    """今夜見る行。成り立たなかった行（翌晩の再試行）と、確認日の古い順に limit 件。"""
    today = today or jst_today()
    retry: list[tuple[str, int]] = []
    queue: list[tuple[str, str, str, int]] = []
    for slug, rows in findings.items():
        for i, row in enumerate(rows):
            if not eligible(row) or waiting(row, today):
                continue
            if row.get("recheck_failures"):
                retry.append((slug, i))
            else:
                queue.append((str(row.get("evidence_checked_on") or ""), slug, str(row["code"]), i))
    queue.sort()
    return retry + [(slug, i) for _, slug, _, i in queue[:limit]]


def listed_hosts(ws: Workspace, slug: str, client: PoliteClient) -> set[str] | None:
    """県の市町村一覧が今リンクしているホスト。一覧が無い・取れないときは None（見送る）。"""
    ref = read_json(ws.root / "data" / "reference" / f"{slug}_official_urls.json")
    url = (ref or {}).get("source_url")
    if not url:
        return None
    res = client.get(url)
    if not res.ok:
        return None
    return {host_of(ln.url) for ln in extract_links(res.text, res.final_url)}


def unreachable(res: FetchResult) -> bool:
    """届かなかった（時間切れ・接続できない・拒否）。ページがあるかどうかは分からない。"""
    return (res.status == 0 and bool(res.error) and not res.blocked) or res.status in REFUSED


def _why(res: FetchResult) -> str:
    return res.error or f"HTTP {res.status}"


def _open_top(client: PoliteClient, host: str) -> FetchResult:
    res = client.get(f"https://{host}/")
    if res.ok or res.blocked:
        return res
    alt = client.get(f"http://{host}/")
    return alt if alt.ok else res


def check(
    slug: str,
    row: dict,
    client: PoliteClient,
    overrides: expand.OfficialOverrides,
    prefecture_hosts: set[str] | None,
) -> tuple[str, str]:
    """1 自治体を確かめる。(ok / fail / skip / unreachable, 理由)。

    記録した公式サイトに実際に届き、トップに市町村名が出ることを見る。解決し直した結果が
    「同じホスト」でも成り立ったとはしない。固定値と県の一覧は、届かなくても同じホストを返す。
    """
    host = str(row["official_url"])
    muni = expand._row_to_finding(row).muni
    top = _open_top(client, host)
    if top.blocked:
        return "skip", "robots.txt で公式サイトを取得できない"
    if unreachable(top):
        return "unreachable", f"公式サイトに通信できない（{_why(top)}）"
    if not (top.ok and expand.page_names_municipality(top.text, muni)):
        # 届いたのに名乗りが無い・ページが無い。移転していれば、解決し直すと別のホストになる
        moved = expand.resolve_official(muni, client, overrides)
        if moved is not None and moved.host.host != host:
            return "fail", f"公式サイトが {moved.host.host} になっている（記録は {host}）"
        if top.ok:
            return "fail", "公式サイトのトップに市町村名が出ない"
        return "fail", f"公式サイトを開けない（{_why(top)}）"
    if not classify_host(host, slug).is_official and prefecture_hosts is not None:
        if host not in prefecture_hosts:
            return "fail", "県の市町村一覧が、記録した公式サイトにリンクしていない"
    pages = {
        "根拠のページ": row.get("evidence_url"),
        "空き家バンクのページ": row.get("bank_url"),
    }
    seen: set[str] = set()
    for label, url in pages.items():
        if not url or url in seen:
            continue
        seen.add(url)
        if not client.robots.allowed(url):
            continue  # 公式サイトは確かめられている。ここだけ見送る
        res = client.get(url)
        if res.ok or res.blocked or unreachable(res):
            continue  # 届かないだけなら、無くなったとは言えない
        return "fail", f"{label}を開けない（{_why(res)}）"
    return "ok", ""


def reselect(
    ws: Workspace,
    slug: str,
    row: dict,
    client: PoliteClient,
    overrides: expand.OfficialOverrides,
    reason: str,
) -> tuple[dict, dict]:
    """3 晩続けて成り立たなかった自治体を選び直す（rediscover と同じ評価）。(新しい行, 記録)。"""
    muni = expand._row_to_finding(row).muni
    f = expand.assess_municipality(muni, client, PlatformRegistry(), overrides)
    new = expand.carry_subsidy_pages(row, expand._finding_to_row(f), client)
    sid = f"{slug}-{row['code']}"
    keep = {f.bank_url} if f.policy == "crawl" and f.bank_url else set()
    expand._reset_source_state(ws, sid, keep)
    decided = new.get("policy") != "pending"
    record = {
        "at": jst_now().isoformat(timespec="seconds"),
        "result": "reselect",
        "source_id": sid,
        "name": f"{row.get('prefecture', '')}{row.get('name', '')}",
        "old_official": row.get("official_url"),
        "new_official": new.get("official_url"),
        "old_bank": row.get("bank_url"),
        "new_bank": new.get("bank_url"),
        "reason": f"{RETRY_NIGHTS} 晩続けて確かめられなかった（{reason}）"
        + ("" if decided else "。選び直しても運営主体が決まらず、人のレビューへ"),
    }
    return new, record


def recheck(
    ws: Workspace,
    *,
    client: PoliteClient,
    limit: int = PER_NIGHT,
    workers: int = 1,
    today: date | None = None,
    checker: Callable[..., tuple[str, str]] = check,
) -> RecheckReport:
    """今夜の分を確かめ直し、findings・sources・記録を書く。"""
    today = today or jst_today()
    today_s = today.isoformat()
    paths = sorted(ws.runs_dir.glob("discover-*-findings.json"))
    findings = {
        p.name[len("discover-") : -len("-findings.json")]: list(
            (read_json(p) or {}).get("findings") or []
        )
        for p in paths
    }
    targets = pick(findings, limit, today)
    overrides = {slug: expand.OfficialOverrides.load(ws, slug) for slug in {s for s, _ in targets}}
    # 県の一覧で公式と決めたホストがある県だけ、一覧を 1 回ずつ取る（並列の前に済ませる）
    need_list = {
        slug
        for slug, i in targets
        if not classify_host(str(findings[slug][i]["official_url"]), slug).is_official
    }
    lists = {slug: listed_hosts(ws, slug, client) for slug in sorted(need_list)}

    def run(target: tuple[str, int]) -> Outcome:
        slug, i = target
        row = findings[slug][i]
        try:
            result, reason = checker(slug, row, client, overrides[slug], lists.get(slug))
        except Exception as e:  # noqa: BLE001 - 1 件の失敗で夜の分を止めない。記録に残す
            result, reason = "fail", f"確かめる途中で失敗した（{type(e).__name__}: {e}）"
        return Outcome(slug, i, result, reason)

    if workers > 1 and len(targets) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(run, targets))
    else:
        outcomes = [run(t) for t in targets]

    report = RecheckReport()
    log: list[dict[str, Any]] = []
    touched: set[str] = set()
    for o in outcomes:
        rows = findings[o.slug]
        row = rows[o.index]
        name = f"{row.get('prefecture', '')}{row.get('name', '')}"
        report.checked += 1
        entry: dict[str, Any] = {
            "at": jst_now().isoformat(timespec="seconds"),
            "result": o.result,
            "source_id": f"{o.slug}-{row['code']}",
            "name": name,
        }
        if o.result in ("skip", "unreachable"):
            # 確かめていないので日付は進めず、失敗にも数えない。WAIT_DAYS 後にまた見る
            if o.result == "skip":
                report.skipped += 1
            else:
                report.unreachable += 1
            rows[o.index] = {
                **row,
                "recheck_waiting_since": row.get("recheck_waiting_since") or today_s,
                "recheck_waiting_reason": o.reason,
                "recheck_last_tried": today_s,
            }
            touched.add(o.slug)
            entry["reason"] = o.reason
        elif o.result == "ok":
            report.ok += 1
            done = expand._without(row, FAIL_KEYS + WAIT_KEYS)
            rows[o.index] = {**done, "evidence_checked_on": today_s}
            touched.add(o.slug)
        else:
            report.failed += 1
            nights = int(row.get("recheck_failures") or 0) + 1
            rows[o.index] = {
                **expand._without(row, WAIT_KEYS),
                "recheck_failures": nights,
                "recheck_failed_since": row.get("recheck_failed_since") or today_s,
                "recheck_reason": o.reason,
            }
            touched.add(o.slug)
            entry |= {"reason": o.reason, "failures": nights}
            report.lines.append(f"{name}: 成り立たない（{nights} 晩目）: {o.reason}")
            if nights >= RETRY_NIGHTS:
                new, record = reselect(ws, o.slug, row, client, overrides[o.slug], o.reason)
                rows[o.index] = new
                report.reselected += 1
                log.append(entry)
                entry = record
                official = f"{record['old_official']} → {record['new_official']}"
                bank = f"{record['old_bank']} → {record['new_bank']}"
                report.lines.append(f"{name}: 選び直した（公式 {official}、空き家バンク {bank}）")
        log.append(entry)
    for slug in sorted(touched):
        rows = findings[slug]
        expand._write_rows(ws, slug, rows[0]["prefecture"], rows)
    if log:
        path = ws.runs_dir / LOG_NAME
        with path.open("a", encoding="utf-8", newline="\n") as f:
            for entry in log:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    report.lines.insert(
        0,
        f"運営主体の確かめ直し: {report.checked} 自治体（成り立った {report.ok}・"
        f"成り立たなかった {report.failed}・robots.txt で見送り {report.skipped}・"
        f"通信できない {report.unreachable}・選び直し {report.reselected}）",
    )
    return report
