"""改善の手動検証：変更前後の検索の数字を、同じ物差しで比べる（docs/improvements/）。

sitemill ADR 0014（自動改善ループ）の第 2 段階に進む前に、人が 1 件ずつ回して、
「何を変えたら何が動いたか」を記録する。

- 変更の時点で、比べるページの組（グループ）をファイルに固定する。後から組を決め直すと、
  物件が増えた町がグループを移り、前後の比較が崩れる
- 数字は `data/search/performance/page/*.jsonl`（Search Console の取り込み）だけから数える。
  相手サイトにも Google にも追加で問い合わせない
- 平均順位も並べる。CTR が動いても、順位が動いていれば見出しの効果とは言えない
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sitemill.settings import Workspace

BASE_URL = "https://akiya-atlas.com"


@dataclass
class Metrics:
    pages_seen: int = 0  # 期間中に 1 回でも表示されたページ
    impressions: int = 0
    clicks: int = 0
    position_weighted: float = 0.0

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    @property
    def position(self) -> float | None:
        return self.position_weighted / self.impressions if self.impressions else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "pages_seen": self.pages_seen,
            "impressions": self.impressions,
            "clicks": self.clicks,
            "ctr": round(self.ctr, 4),
            "position": round(self.position, 1) if self.position is not None else None,
        }


def page_rows(ws: Workspace, start: date, end: date) -> dict[str, tuple[int, int, float]]:
    """期間中のページごとの（表示, クリック, 順位×表示）。パスで引く。"""
    out: dict[str, list[float]] = {}
    for path in sorted((ws.root / "data/search/performance/page").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            day = date.fromisoformat(row["date"])
            if not (start <= day <= end):
                continue
            page = str(row["page"]).removeprefix(BASE_URL)
            acc = out.setdefault(page, [0, 0, 0.0])
            acc[0] += int(row["impressions"])
            acc[1] += int(row["clicks"])
            acc[2] += float(row["position"]) * int(row["impressions"])
    return {k: (int(v[0]), int(v[1]), v[2]) for k, v in out.items()}


def measure(rows: dict[str, tuple[int, int, float]], paths: list[str]) -> Metrics:
    m = Metrics()
    for p in paths:
        if p in rows:
            imp, clk, pw = rows[p]
            m.pages_seen += 1
            m.impressions += imp
            m.clicks += clk
            m.position_weighted += pw
    return m


def _fmt(m: dict[str, Any]) -> str:
    pos = f"{m['position']:.1f} 位" if m.get("position") is not None else "—"
    return (
        f"表示 {m['impressions']} ／ クリック {m['clicks']} ／ CTR {m['ctr'] * 100:.1f}% "
        f"／ 平均 {pos}（表示のあったページ {m['pages_seen']}）"
    )


def compare(ws: Workspace, baseline_file: Path, start: date, end: date) -> list[str]:
    """ベースラインのファイルと、指定した期間の数字を並べる。"""
    base = json.loads(baseline_file.read_text(encoding="utf-8"))
    rows = page_rows(ws, start, end)
    out = [
        f"## {base['id']}: {base['title']}",
        "",
        f"- 変更: {base['changed_at']}",
        f"- 変更前: {base['baseline_window'][0]} 〜 {base['baseline_window'][1]}",
        f"- 変更後: {start.isoformat()} 〜 {end.isoformat()}",
        "",
    ]
    for name, group in base["groups"].items():
        after = measure(rows, group["paths"]).as_dict()
        out += [
            f"### {name}（{len(group['paths'])} ページ）",
            f"- 変更前: {_fmt(group['baseline'])}",
            f"- 変更後: {_fmt(after)}",
            "",
        ]
    for path, note in base.get("watch", {}).items():
        before = note["baseline"]
        after = measure(rows, [path]).as_dict()
        out += [
            f"### 個別: {note['name']}（{path}）",
            f"- 変更前: {_fmt(before)}",
            f"- 変更後: {_fmt(after)}",
            "",
        ]
    return out
