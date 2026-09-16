"""改善の手動検証の集計（docs/improvements/）。ネットワーク不要。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from sitemill.settings import Workspace

from akiya_atlas import improvements


def test_compare_counts_only_the_fixed_pages_within_the_period(tmp_path: Path) -> None:
    """変更の時点で固定したページの組だけを、指定した期間だけ数える。"""
    (tmp_path / "site.toml").write_text(
        '[site]\nid="x"\nname="x"\nbase_url="https://akiya-atlas.com"\n'
        'service="akiya_atlas.service:service"\n[operator]\nname="x"\ncontact="x"\n',
        encoding="utf-8",
    )
    ws = Workspace.open(tmp_path)
    perf = tmp_path / "data/search/performance/page"
    perf.mkdir(parents=True)
    rows = [
        ("2026-09-16", "/kochi/392031/", 10, 0, 9.0),  # 変更前の日（期間外）
        ("2026-09-24", "/kochi/392031/", 20, 2, 8.0),
        ("2026-09-25", "/kochi/392031/", 10, 1, 5.0),
        ("2026-09-25", "/hokkaido/012092/", 5, 0, 9.0),  # 組に入っていないページ
    ]
    perf.joinpath("2026-09.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "date": d,
                    "page": f"https://akiya-atlas.com{p}",
                    "impressions": i,
                    "clicks": c,
                    "position": pos,
                }
            )
            + "\n"
            for d, p, i, c, pos in rows
        ),
        encoding="utf-8",
    )
    baseline = {
        "id": "0001",
        "title": "t",
        "changed_at": "2026-09-17",
        "baseline_window": ["2026-09-11", "2026-09-17"],
        "groups": {
            "掲載あり": {
                "paths": ["/kochi/392031/"],
                "baseline": {
                    "pages_seen": 1,
                    "impressions": 32,
                    "clicks": 0,
                    "ctr": 0.0,
                    "position": 8.6,
                },
            }
        },
    }
    path = tmp_path / "b.json"
    path.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    text = "\n".join(improvements.compare(ws, path, date(2026, 9, 24), date(2026, 9, 30)))
    assert (
        "変更後: 表示 30 ／ クリック 3 ／ CTR 10.0% ／ 平均 7.0 位（表示のあったページ 1）" in text
    )
    assert "変更前: 表示 32 ／ クリック 0 ／ CTR 0.0% ／ 平均 8.6 位" in text
