"""掲載の取り下げ依頼（お問い合わせフォーム → GitHub Issue）を読み、非表示にする URL を集める。

お問い合わせフォームの自動返信（tools/contact_form/AutoReply.gs）は、takedown と判定した依頼に
`takedown` ラベルの Issue を作る。その本文の 1 行目には機械可読な JSON コメントが入っている:

    <!-- {"kind":"akiya-atlas-contact","category":"takedown","urls":[...],...} -->

ここでは **開いている takedown Issue だけ**を読み、本サイトの URL を `data/reference/takedowns.json`
に書き出す。ビルドはこの一覧に載っている物件ページを出力しない（次回ビルドから消える）。
needs-human と municipality の Issue は読むだけで何もしない（人が対応する）。

Issue を閉じれば次回の実行で一覧から外れ、掲載が戻る。
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sitemill.settings import Workspace

META_LINE = re.compile(r"<!--\s*(\{.*?\})\s*-->", re.S)
LISTING_PATH = re.compile(r"^/(?P<pref>[a-z-]+)/(?P<muni>[0-9]{6})/(?P<slug>[^/]+)/$")
MUNI_PATH = re.compile(r"^/(?P<pref>[a-z-]+)/(?P<muni>[0-9]{6})/$")


def takedowns_path(ws: Workspace) -> Path:
    return ws.root / "data" / "reference" / "takedowns.json"


@dataclass
class Takedown:
    """1 件の取り下げ依頼。URL は本サイトのページを指す。"""

    issue: int
    url: str
    path: str
    received_at: str = ""
    title: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "issue": self.issue,
            "url": self.url,
            "path": self.path,
            "received_at": self.received_at,
            "title": self.title,
        }


@dataclass
class TakedownList:
    """非表示にするページの一覧。パスで引く。"""

    items: list[Takedown] = field(default_factory=list)
    other_issues: dict[str, int] = field(default_factory=dict)  # 読むだけのラベルの件数

    @property
    def paths(self) -> set[str]:
        return {t.path for t in self.items}

    def to_json(self) -> dict[str, Any]:
        return {
            "_about": "取り下げ依頼で非表示にするページ。Issue を閉じると次回の実行で戻る",
            "updated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "other_open_issues": self.other_issues,
            "takedowns": [t.to_json() for t in sorted(self.items, key=lambda t: t.path)],
        }

    @classmethod
    def load(cls, ws: Workspace) -> TakedownList:
        path = takedowns_path(ws)
        if not path.is_file():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            items=[Takedown(**{k: v for k, v in row.items()}) for row in data.get("takedowns", [])],
            other_issues=data.get("other_open_issues", {}),
        )

    def save(self, ws: Workspace) -> Path:
        path = takedowns_path(ws)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_json(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return path


def parse_meta(body: str) -> dict[str, Any] | None:
    """Issue 本文の先頭にある JSON コメントを読む。無い・壊れていれば None。"""
    m = META_LINE.search(body or "")
    if not m:
        return None
    try:
        meta = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return meta if isinstance(meta, dict) else None


def site_path(url: str, base_url: str) -> str | None:
    """本サイトの URL ならパス（末尾スラッシュつき）を返す。外部サイトなら None。"""
    if not url:
        return None
    base = urlsplit(base_url)
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or parts.netloc.lower() != base.netloc.lower():
        return None
    path = parts.path or "/"
    if not path.endswith("/") and "." not in path.rsplit("/", 1)[-1]:
        path += "/"
    return path


def collect(issues: list[dict[str, Any]], *, base_url: str) -> TakedownList:
    """Issue の一覧から、非表示にするページを集める（純関数。テスト対象）。

    takedown だけを対象にする。needs-human と municipality は件数を数えるだけ。
    """
    out = TakedownList()
    for issue in issues:
        labels = {
            (lb.get("name") if isinstance(lb, dict) else str(lb)) for lb in issue.get("labels", [])
        }
        if "takedown" not in labels:
            for name in labels:
                out.other_issues[name] = out.other_issues.get(name, 0) + 1
            continue
        meta = parse_meta(issue.get("body", "")) or {}
        if meta.get("category") not in (None, "takedown"):
            continue
        urls = [u for u in (meta.get("urls") or []) if isinstance(u, str)]
        for url in urls:
            path = site_path(url, base_url)
            if path is None:
                continue  # 外部サイトの URL は本サイトでは消せない
            if path in out.paths:
                continue
            out.items.append(
                Takedown(
                    issue=int(issue.get("number") or 0),
                    url=url,
                    path=path,
                    received_at=str(meta.get("received_at") or ""),
                    title=str(issue.get("title") or ""),
                )
            )
    return out


def fetch_issues(repo: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """gh CLI で開いている Issue を取る（GitHub Actions では GITHUB_TOKEN を使う）。"""
    proc = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            "number,title,body,labels",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh issue list に失敗: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout or "[]")


def sync(ws: Workspace, repo: str, *, limit: int = 100) -> TakedownList:
    """Issue を読んで data/reference/takedowns.json を更新する。"""
    issues = fetch_issues(repo, limit=limit)
    result = collect(issues, base_url=ws.site.base_url)
    result.save(ws)
    return result
