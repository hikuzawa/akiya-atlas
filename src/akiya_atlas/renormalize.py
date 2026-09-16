"""物件番号の正規化を改めたときに、保存済みレコードの record_id を付け直す。

record_id は「source id と正規化した物件番号」から作る（`schema.record_id_for`）。正規化を改めると、
保存済みのレコードと次の取り込みで id が食い違い、同じ物件が 2 件に割れる。付け直しを一度行えば、
以後の取り込みは元のレコードを更新する。

- 付け直した id を持つレコードが無ければ、id だけ書き換える。URL は物件番号から作るので変わらない
- あれば同じ物件なので 1 件にまとめる。先に見つけたほう（公開済みの URL を持つ）の物件番号と
  初出日時を残し、中身は取り込みと同じ規則（`service.merge_content`）で新しいほうから取る
- 他のレコードの `duplicate_of` が古い id を指していれば、付け直した id に向け直す

2026-09-17 に、砂川市の「R8-8」と「<R8-8>」が 42 組 2 件ずつ載っていたのを、これでまとめた。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sitemill.settings import Workspace
from sitemill.store.jsonio import read_jsonl, write_jsonl

from akiya_atlas.schema import record_id_for

_META = {"record_id", "first_seen_at", "last_seen_at", "status", "history", "provenance"}


@dataclass
class Plan:
    renamed: dict[str, int] = field(default_factory=dict)  # source id -> 付け直した件数
    merged: dict[str, int] = field(default_factory=dict)  # source id -> まとめた件数
    ids: dict[str, str] = field(default_factory=dict)  # 古い id -> 新しい id
    files: dict[Path, list[dict[str, Any]]] = field(default_factory=dict)  # 書き直す内容

    @property
    def empty(self) -> bool:
        return not self.ids


def _merge_twin(
    a: dict[str, Any], b: dict[str, Any], new_id: str, gone_id: str, now: str
) -> dict[str, Any]:
    from akiya_atlas.service import merge_content

    first, later = sorted((a, b), key=lambda r: str(r.get("first_seen_at") or ""))
    newer = max((a, b), key=lambda r: str(r.get("last_seen_at") or ""))
    older = b if newer is a else a
    content = merge_content(older, newer)
    out = {**first, **{k: v for k, v in content.items() if k not in _META}}
    out["record_id"] = new_id
    # 公開済みの URL は先に見つけたほうの物件番号から作られている
    out["listing_no"] = first["listing_no"]
    out["first_seen_at"] = first.get("first_seen_at")
    out["last_seen_at"] = newer.get("last_seen_at")
    active = [r for r in (a, b) if r.get("status") == "active"]
    out["status"] = "active" if active else first.get("status")
    out["provenance"] = newer.get("provenance")
    history = list(first.get("history") or []) + list(later.get("history") or [])
    history.append({"at": now, "event": "merged", "from": gone_id})
    out["history"] = sorted(history, key=lambda h: str(h.get("at") or ""))
    return out


def plan(ws: Workspace, *, now: datetime | None = None) -> Plan:
    """書き換えずに、付け直しとまとめの内容を求める。"""
    stamp = (now or datetime.now(UTC)).replace(microsecond=0).isoformat()
    result = Plan()
    loaded: dict[Path, list[dict[str, Any]]] = {}
    for path in sorted((ws.data_dir / "records").glob("*.jsonl")):
        rows = [r for r in read_jsonl(path) if isinstance(r, dict) and r.get("record_id")]
        loaded[path] = rows
        by_id: dict[str, dict[str, Any]] = {str(r["record_id"]): r for r in rows}
        changed = False
        for row in rows:
            old_id = str(row["record_id"])
            new_id = record_id_for(str(row["source_id"]), str(row["listing_no"]))
            if new_id == old_id or by_id.get(old_id) is not row:
                continue
            source = str(row["source_id"])
            twin = by_id.get(new_id)
            if twin is None:
                row["record_id"] = new_id
                by_id[new_id] = row
                result.renamed[source] = result.renamed.get(source, 0) + 1
            else:
                by_id[new_id] = _merge_twin(twin, row, new_id, old_id, stamp)
                result.merged[source] = result.merged.get(source, 0) + 1
            del by_id[old_id]
            result.ids[old_id] = new_id
            changed = True
        if changed:
            result.files[path] = [by_id[k] for k in sorted(by_id)]
    if result.ids:
        # 他の source から duplicate_of で指されていれば、指し先も付け直す
        for path, rows in loaded.items():
            target = result.files.get(path, rows)
            touched = False
            for row in target:
                ref = row.get("duplicate_of")
                if ref in result.ids:
                    row["duplicate_of"] = result.ids[ref]
                    touched = True
            if touched:
                result.files[path] = target
    return result


def apply(plan_: Plan) -> int:
    """求めた内容でファイルを書き直す。書いたファイル数を返す。"""
    for path, rows in plan_.files.items():
        write_jsonl(path, rows)
    return len(plan_.files)
