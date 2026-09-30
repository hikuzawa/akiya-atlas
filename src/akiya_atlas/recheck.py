"""運営主体を 90 日に 1 回確かめ直す（ADR 0018）。回し方は sitemill の `recheck`（ADR 0027）。

毎晩、確認日の古い順に 20 自治体を見る。LLM は使わない。日次は `sitemill recheck` で回し、
今夜の分の選び方・翌晩の再試行・確かめられなかったものの待たせ方・状態
（`data/state/recheck.json`）・記録（`data/runs/operator-rechecks.jsonl`）は sitemill の `rotate` が
持つ。ここにあるのは akiya-atlas の中身だけで、サービスのフック（`service.py` の `recheck_*`）から
呼ばれる:

- 公式サイト: 記録した公式ホストに届き、トップに市町村名が出るか。届いたのに出なければ解決し直し、
  別のホストになれば「公式サイトが変わった」
- 県の一覧で公式と決めたホストは、県の一覧が今もリンクしているか（県ごとに 1 回だけ取得）
- 根拠の出典ページと空き家バンクのページが今も開けるか
- 3 晩続けて成り立たなければ選び直す（rediscover と同じ評価）。URL が変わったら週次に旧新を出す
- 確かめた日の書き戻し: sitemill は確かめた日を状態ファイルにだけ書く（ADR 0027）。akiya-atlas は
  確認日を `/data/<県>/` に出すので、`recheck_done` で受け取った今夜の結果の日付を、findings の
  `evidence_checked_on` と YAML の `checked_on` へ書き戻す（09-30 までは自前のコマンド
  `recheck-operators` の後段で書き戻していた。sitemill v0.7.14 で `recheck_done` ができた）
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path
from typing import Any

from sitemill.classify import PlatformRegistry
from sitemill.clock import jst_today
from sitemill.fetch.client import FetchResult, PoliteClient
from sitemill.fetch.links import extract_links, host_of
from sitemill.recheck import (
    RecheckResult,
    RecheckTarget,
    checked_on,
    load_state,
    pick,
    unreachable,
)
from sitemill.settings import Workspace
from sitemill.store.jsonio import read_json

from akiya_atlas import expand
from akiya_atlas.official_domains import classify_host

PER_NIGHT = 20  # 1,736 自治体 ÷ 90 日 ≒ 19.3（service の recheck_per_night）
# 次の 2 つは `sitemill recheck` の既定（rotate の retry_nights・wait_days）と同じ値。サービスからは
# 変えられないので、ここは週次と選び直しの理由の文に使うだけ（違っていればテストが落ちる）
RETRY_NIGHTS = 3  # この晩数だけ続けて成り立たなければ選び直す
WAIT_DAYS = 7  # 確かめられなかった（robots.txt・通信できない）自治体を、次に見るまでの日数


def eligible(row: dict) -> bool:
    """運営主体が決まっている行だけ。決まっていない行（pending）は人のレビューの側にある。"""
    return bool(row.get("official_url")) and row.get("policy") != "pending"


def key_of(slug: str, row: dict) -> str:
    """状態と記録のキー。source の id と同じ形（`<県>-<団体コード>`）。"""
    return f"{slug}-{row['code']}"


def label_of(row: dict) -> str:
    return f"{row.get('prefecture', '')}{row.get('name', '')}"


def load_findings(ws: Workspace) -> dict[str, list[dict]]:
    return {
        p.name[len("discover-") : -len("-findings.json")]: list(
            (read_json(p) or {}).get("findings") or []
        )
        for p in sorted(ws.runs_dir.glob("discover-*-findings.json"))
    }


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _target(slug: str, row: dict) -> RecheckTarget:
    return RecheckTarget(key_of(slug, row), label_of(row), _day(row.get("evidence_checked_on")))


def targets(findings: dict[str, list[dict]]) -> list[RecheckTarget]:
    """確かめ直す自治体。サービスが持つ確認日は findings の `evidence_checked_on`。"""
    return [_target(slug, row) for slug, rows in findings.items() for row in rows if eligible(row)]


def tonight(
    ws: Workspace, *, limit: int = PER_NIGHT, today: date | None = None
) -> list[RecheckTarget]:
    """今夜見る自治体。前の晩に成り立たなかったもの（枠の外）と、確認日の古い順に limit 件。"""
    return pick(
        targets(load_findings(ws)),
        load_state(ws),
        per_night=limit,
        wait_days=WAIT_DAYS,
        today=today or jst_today(),
    )


def standing(ws: Workspace) -> list[tuple[dict, dict[str, Any], date | None]]:
    """確かめ直す自治体ごとの (findings の行, 状態, 確認日)。週次が読む。

    確認日は findings の値と状態ファイルの値の新しいほう（sitemill ADR 0027）。
    """
    state = load_state(ws)
    out: list[tuple[dict, dict[str, Any], date | None]] = []
    for slug, rows in load_findings(ws).items():
        for row in rows:
            if not eligible(row):
                continue
            target = _target(slug, row)
            st = state.get(target.key) or {}
            out.append((row, st, checked_on(target, st)))
    return out


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


def taken_from_list(ws: Workspace, slug: str) -> dict[str, str]:
    """発見のときに県の一覧から取ったリンク先のホスト（団体コードごと）。

    記録した公式ホストは、一覧のリンク先を開いて転送された先になっていることがある
    （長野県の一覧は `http://hirayamura.jp/`、記録は `www.hirayamura.jp`。北海道の一覧は
    `town.higashikawa.hokkaido.jp`、記録は `higashikawa-town.jp`）。
    """
    ref = read_json(ws.root / "data" / "reference" / f"{slug}_official_urls.json") or {}
    return {
        str(m.get("code")): host_of(str(m["official_url"]))
        for m in ref.get("municipalities") or []
        if m.get("official_url")
    }


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
    listed_as: str | None = None,
) -> tuple[str, str]:
    """1 自治体を確かめる。(ok / fail / skip / unreachable, 理由)。

    記録した公式サイトに実際に届き、トップに市町村名が出ることを見る。解決し直した結果が
    「同じホスト」でも成り立ったとはしない。固定値と県の一覧は、届かなくても同じホストを返す。
    届かない（時間切れ・接続できない・403・429）ものは sitemill の `unreachable` で見分ける。

    県の一覧は、記録したホストか、発見のときに一覧から取ったリンク先（`listed_as`）のどちらかへ
    今もリンクしていれば成り立つ。記録したホストだけで比べると、一覧が転送元（www なしなど）へ
    リンクしている自治体が、一覧が変わっていないのに毎回成り立たない（2026-09-30 に平谷村・
    根羽村）。
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
        if host not in prefecture_hosts and listed_as not in prefecture_hosts:
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
    """3 晩続けて成り立たなかった自治体を選び直す（rediscover と同じ評価）。(新しい行, 記録)。

    記録には sitemill が日時・結果・キーを足して `operator-rechecks.jsonl` に残す。週次の
    「今週 確かめ直しで選び直した自治体」が、ここの名前と旧新の URL を並べる。
    """
    muni = expand._row_to_finding(row).muni
    f = expand.assess_municipality(muni, client, PlatformRegistry(), overrides)
    new = expand.carry_subsidy_pages(row, expand._finding_to_row(f), client)
    sid = key_of(slug, row)
    keep = {f.bank_url} if f.policy == "crawl" and f.bank_url else set()
    expand._reset_source_state(ws, sid, keep)
    decided = new.get("policy") != "pending"
    record = {
        "source_id": sid,
        "name": label_of(row),
        "old_official": row.get("official_url"),
        "new_official": new.get("official_url"),
        "old_bank": row.get("bank_url"),
        "new_bank": new.get("bank_url"),
        "reason": f"{RETRY_NIGHTS} 晩続けて確かめられなかった（{reason}）"
        + ("" if decided else "。選び直しても運営主体が決まらず、人のレビューへ"),
    }
    return new, record


class _Night:
    """1 晩分。findings を 1 度だけ読み、確かめる・選び直す・書き戻すで同じ行を使う。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws
        self.findings = load_findings(ws)
        self.where = {
            key_of(slug, row): (slug, i)
            for slug, rows in self.findings.items()
            for i, row in enumerate(rows)
        }
        self._overrides: dict[str, expand.OfficialOverrides] = {}
        self._taken: dict[str, dict[str, str]] = {}
        self._lists: dict[str, set[str] | None] = {}
        self._overrides_lock = threading.Lock()
        self._lists_lock = threading.Lock()

    def overrides(self, slug: str) -> expand.OfficialOverrides:
        with self._overrides_lock:
            if slug not in self._overrides:
                self._overrides[slug] = expand.OfficialOverrides.load(self.ws, slug)
            return self._overrides[slug]

    def taken(self, slug: str) -> dict[str, str]:
        with self._overrides_lock:
            if slug not in self._taken:
                self._taken[slug] = taken_from_list(self.ws, slug)
            return self._taken[slug]

    def prefecture_hosts(self, slug: str, client: PoliteClient) -> set[str] | None:
        """県の一覧が今リンクしているホスト。並列で見ても、県ごとに 1 回だけ取る。"""
        with self._lists_lock:
            if slug not in self._lists:
                self._lists[slug] = listed_hosts(self.ws, slug, client)
            return self._lists[slug]

    def check(self, target: RecheckTarget, client: PoliteClient) -> tuple[str, str]:
        slug, i = self.where[target.key]
        row = self.findings[slug][i]
        hosts = listed_as = None
        if not classify_host(str(row["official_url"]), slug).is_official:
            hosts = self.prefecture_hosts(slug, client)
            listed_as = self.taken(slug).get(str(row["code"]))
        return check(slug, row, client, self.overrides(slug), hosts, listed_as)

    def reselect(self, target: RecheckTarget, reason: str, client: PoliteClient) -> dict[str, Any]:
        """選び直した行はその場で書く（sources と review も作り直す）。"""
        slug, i = self.where[target.key]
        rows = self.findings[slug]
        rows[i], record = reselect(self.ws, slug, rows[i], client, self.overrides(slug), reason)
        expand._write_rows(self.ws, slug, rows[0]["prefecture"], rows)
        return record

    def write_back(self, results: list[RecheckResult], state: dict[str, dict[str, Any]]) -> None:
        """今夜確かめた日を findings の確認日に進め、YAML を書き直す。

        今夜の結果（`results`）のほかに、状態ファイルの日付の方が新しいものも進める（書き戻しが
        途中で止まった晩の分を、次の晩に取り戻すため）。
        """
        tonight = {r.key: r.checked_on for r in results if r.checked_on is not None}
        touched: set[str] = set()
        for key, (slug, i) in self.where.items():
            row = self.findings[slug][i]
            days = [
                d for d in (tonight.get(key), _day((state.get(key) or {}).get("checked_on"))) if d
            ]
            if not eligible(row) or not days:
                continue
            if max(days) > (_day(row.get("evidence_checked_on")) or date.min):
                self.findings[slug][i] = {**row, "evidence_checked_on": max(days).isoformat()}
                touched.add(slug)
        for slug in sorted(touched):
            rows = self.findings[slug]
            expand._write_rows(self.ws, slug, rows[0]["prefecture"], rows)


# `sitemill recheck` が呼ぶフック（service.py の recheck_*）。1 晩の中で findings を共有するので、
# recheck_targets で作った 1 晩分を、同じ作業場所の recheck_one・recheck_reselect・recheck_done が
# 使う
_NIGHTS: dict[Path, _Night] = {}
_NIGHTS_LOCK = threading.Lock()


def _night(ws: Workspace, *, fresh: bool = False) -> _Night:
    with _NIGHTS_LOCK:
        night = _NIGHTS.get(ws.root)
        if fresh or night is None:
            night = _NIGHTS[ws.root] = _Night(ws)
        return night


def start(ws: Workspace) -> list[RecheckTarget]:
    """`recheck_targets`。今夜の findings を読み直し、確かめ直す自治体を返す。"""
    return targets(_night(ws, fresh=True).findings)


def check_one(ws: Workspace, target: RecheckTarget, client: PoliteClient) -> tuple[str, str]:
    """`recheck_one`。"""
    return _night(ws).check(target, client)


def reselect_one(
    ws: Workspace, target: RecheckTarget, reason: str, client: PoliteClient
) -> dict[str, Any]:
    """`recheck_reselect`。"""
    return _night(ws).reselect(target, reason, client)


def reselect_line(target: RecheckTarget, record: dict[str, Any]) -> str:
    """`recheck_reselect_line`。実行ログに旧新の URL を並べる。"""
    official = f"{record.get('old_official')} → {record.get('new_official')}"
    bank = f"{record.get('old_bank')} → {record.get('new_bank')}"
    return f"{target.label}: 選び直した（公式 {official}、空き家バンク {bank}）"


def done(ws: Workspace, results: list[RecheckResult]) -> None:
    """`recheck_done`。今夜確かめた日を findings と YAML に書き戻す。"""
    with _NIGHTS_LOCK:
        night = _NIGHTS.pop(ws.root, None)
    (night or _Night(ws)).write_back(results, load_state(ws))
