"""広告の転送ページが何回開かれたかを、案件と枠ごとに数える（ADR 0010 追記、2026-09-23）。

転送ページ（`/go/<案件>/<枠>/`）は 1 枚につき 1 回、Cloudflare Web Analytics のビーコンを出す。
その表示数を `sitemill.metrics.rum_pageloads`（ホスト名で絞る。sitemill ADR 0007 追記）で取り、
**案件ごと・枠ごと**に並べる。どの枠が押されているかが分かると、枠の並び（`Offer.rank`）を
点数ではなく実測で決められる（ADR 0010）。

必要なもの（`.env` / CI の Secrets）:

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN` に **Account Analytics: Read** の権限（配置用の権限だけでは 403 になる）

ビーコンは Cloudflare が応答に自動で挿し込む形にしてあり、サイトに鍵を置かない
（`CF_WEB_ANALYTICS_TOKEN` は登録しない。ADR 0011）。挿し込みの条件（要求の形で決まり、
利用者の UA は関係ない）と、同じホスト名で登録が 2 つあるときの注意は
`sitemill.metrics.rum` の docstring にまとめてある。

広告の枠は `/owners/` 配下にしかないので、クリックが 0 のときに「届いていない」のか
「届いても押されない」のかを分けられるよう、**`/owners/` への到達率**を必ず添える。

鍵や権限が足りないときは、何を足せばよいかを書いて 0 で終わる（週次を止めない）。

使い方: uv run python -m tools.report_clicks [--days 7]
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from sitemill.metrics import rum_pageloads
from sitemill.metrics.rum import NEED
from sitemill.settings import Workspace

from akiya_atlas import affiliates


def report(counts: dict[str, int], days: int) -> list[str]:
    """案件・枠ごとの表を作る。公開中の枠は 0 回でも行を出す（増えたかを追えるように）。"""
    targets = affiliates.go_targets()
    lines = [f"## 広告のクリック（転送ページの表示、直近 {days} 日）", ""]
    if not targets:
        return lines + ["- 公開中の案件が無い"]

    by_offer: dict[str, dict[str, int]] = defaultdict(dict)
    for target in targets:
        by_offer[target.offer.id][target.placement.id] = counts.get(target.url_path, 0)

    lines += ["| 案件 | 枠 | 回数 |", "|---|---|---:|"]
    total = 0
    for offer in affiliates.active_offers():
        rows = by_offer.get(offer.id)
        if not rows:
            continue
        for placement, n in sorted(rows.items(), key=lambda kv: -kv[1]):
            total += n
            lines.append(f"| {offer.label}（{offer.advertiser}） | `{placement}` | {n} |")
    lines += ["", f"- 合計 **{total}** 回"]

    # 広告の枠は `/owners/` 配下にしかない。そこへ届いている割合を添える（2026-09-23）。
    # クリックが 0 でも、届いていないのか届いても押されないのかで打ち手が違う
    site = sum(counts.values())
    if site:
        owners = {p: n for p, n in counts.items() if p == "/owners/" or p.startswith("/owners/")}
        reached = sum(owners.values())
        pref = reached - owners.get("/owners/", 0)
        lines.append(
            f"- `/owners/` への到達率 **{reached / site * 100:.1f}%**"
            f"（{reached} / {site} 表示。うち県別 {pref}）。広告の枠はここにしかない"
        )

    # 一覧に無い `/go/` が数えられていたら出す（枠を外したあとも押されている、など）
    known = {t.url_path for t in targets}
    strays = {p: n for p, n in counts.items() if p.startswith("/go/") and p not in known and n}
    if strays:
        lines.append(
            "- 一覧に無い転送ページも数えられている: "
            + "、".join(f"`{p}` {n} 回" for p, n in sorted(strays.items(), key=lambda kv: -kv[1]))
        )
    if total == 0:
        # 0 が「押されていない」なのか「計測が届いていない」なのかは、同じ問い合わせで分かる。
        # サイト全体に表示数があれば、ビーコンは届いていて押されていないだけ
        if site:
            lines.append(
                f"- 0 回。同じ期間にサイト全体では {site} 表示あるので、計測は届いている"
                "（転送ページが押されていない）"
            )
        else:
            lines.append(
                "- 0 回。サイト全体も 0 表示で、計測が届いていない疑いがある。"
                "Cloudflare の Web Analytics の画面にページ別の表示数が出ているかを見る"
            )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="さかのぼる日数")
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    host = urlsplit(ws.site.base_url).hostname or ""
    counts = rum_pageloads(ws.secrets, host, args.days)
    if counts is None:
        print("\n".join([f"## 広告のクリック（直近 {args.days} 日）", "", NEED]))
        return 0
    print("\n".join(report(counts, args.days)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
