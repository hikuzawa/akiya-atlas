"""`akiya-atlas` コマンド。sitemill の CLI にこのサービス固有のコマンドを足して起動する。

- `expand <都道府県>`: 全市町村を自動発見し、運営主体を確認できたものを sources に採用する
- `rediscover <都道府県> --code ...`: 指定した市町村だけ候補を選び直す（誤採用の再評価）
- `heal` / `crawl` / `extract` / `build` / `run` などは sitemill のものをそのまま使う
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated

import typer


def _register(app: typer.Typer) -> None:
    from sitemill import commands
    from sitemill.settings import SecretsError

    from akiya_atlas import expand

    @app.command("expand")
    def expand_cmd(
        prefecture: Annotated[str, typer.Argument(help="都道府県名（例: 宮城県）")],
        limit: Annotated[int | None, typer.Option("--limit", help="評価する市町村数の上限")] = None,
        workers: Annotated[
            int | None, typer.Option("--workers", "-w", help="市町村（ホスト）単位の並列数")
        ] = None,
    ) -> None:
        """都道府県の全市町村を自動発見し、運営主体を確認できたものを sources に採用する。"""
        rt = commands.Runtime.open()
        with rt.client() as client:
            report = expand.discover_and_write(
                rt.ws, prefecture, limit=limit, client=client, workers=workers
            )
            requests = client.request_count
        typer.echo(
            f"{report.prefecture}: 対象={report.total} 既存スキップ={report.skipped_existing} "
            f"crawl={report.adopted_crawl} link_only={report.adopted_link_only} "
            f"pending={report.pending} requests={requests}"
        )

    @app.command("rediscover")
    def rediscover_cmd(
        prefecture: Annotated[str, typer.Argument(help="都道府県名")],
        code: Annotated[list[str], typer.Option("--code", "-c", help="市町村コード（6 桁）")],
    ) -> None:
        """指定した市町村だけ候補を選び直し、findings と sources/review を書き直す。"""
        rt = commands.Runtime.open()
        with rt.client() as client:
            for line in expand.rediscover_codes(rt.ws, prefecture, code, client=client):
                typer.echo(line)

    @app.command("subsidy-pages")
    def subsidy_pages_cmd(
        prefecture: Annotated[str, typer.Argument(help="都道府県名")],
        limit: Annotated[
            int, typer.Option("--limit", help="1 自治体あたりに拾うページ数の上限")
        ] = 4,
    ) -> None:
        """空き家バンクのページから補助制度のページを見つけ、巡回の対象に足す。"""
        rt = commands.Runtime.open()
        with rt.client() as client:
            for line in expand.collect_subsidy_pages(rt.ws, prefecture, client=client, limit=limit):
                typer.echo(line)

    @app.command("subsidy-backfill")
    def subsidy_backfill_cmd(
        only: Annotated[
            list[str] | None,
            typer.Option("--only", help="県名・スラッグ・2 桁コード（省略時は全県）"),
        ] = None,
        limit: Annotated[
            int, typer.Option("--limit", help="1 自治体あたりに拾うページ数の上限")
        ] = 4,
        force: Annotated[bool, typer.Option("--force", help="済みの県もやり直す")] = False,
        status: Annotated[bool, typer.Option("--status", help="進捗だけ出す")] = False,
    ) -> None:
        """県ごとに 補助制度のページの探索 → 巡回 → 抽出 を回す（再開可能）。"""
        from akiya_atlas import subsidy_backfill

        rt = commands.Runtime.open()
        if status:
            for line in subsidy_backfill.status_table(rt.ws):
                typer.echo(line)
            return
        subsidy_backfill.run_subsidy_backfill(
            rt, only=only, limit=limit, force=force, echo=typer.echo
        )

    @app.command("official-urls")
    def official_urls_cmd(
        prefecture: Annotated[str, typer.Argument(help="都道府県名")],
        page_url: Annotated[str, typer.Argument(help="県サイトの市町村リンク集ページの URL")],
        name: Annotated[str, typer.Option("--name", help="出典名（例: 香川県内市町リンク）")] = "",
    ) -> None:
        """県の市町村リンク集から code→公式URL の対応表を作り data/reference/ に保存する。"""
        from akiya_atlas import reference

        rt = commands.Runtime.open()
        with rt.client() as client:
            table = reference.build_official_urls(
                rt.ws, prefecture, page_url, client=client, name=name
            )
        typer.echo(
            f"{table.prefecture}: 対応 {len(table.matched)} 件・未対応 {len(table.unmatched)} 件 "
            f"→ data/reference/{table.slug}_official_urls.json"
        )
        for n in table.unmatched:
            typer.echo(f"  未対応: {n}（候補ドメインの推測で解決できなければ pending になる）")
        for n in table.ambiguous:
            typer.echo(f"  同名あり: {n}（取り違えを避けるため対応づけない）")
        for n in table.duplicates:
            typer.echo(f"  重複: {n}")
        for n in table.ignored:
            typer.echo(f"  無視: {n}")

    @app.command("weekly-report")
    def weekly_report_cmd(
        days: Annotated[int, typer.Option("--days", help="さかのぼる日数")] = 7,
        save: Annotated[
            bool, typer.Option("--save/--no-save", help="data/runs に記録を残す")
        ] = True,
        source: Annotated[
            str, typer.Option("--source", help="ci（日次のみ）/ local（手元のみ）/ all")
        ] = "ci",
        repo: Annotated[
            str,
            typer.Option(
                "--repo", help="Actions の実行時間を集計する owner/repo（既定は GH_REPO）"
            ),
        ] = "",
    ) -> None:
        """日次パイプラインの直近 N 日をまとめる（実行時間・差分・費用・自己修復・取り下げ）。"""
        from akiya_atlas import weekly

        rt = commands.Runtime.open()
        for line in weekly.report(rt.ws, days=days, source=source, repo=repo or None):
            typer.echo(line)
        est = weekly.month_estimate(weekly.collect(rt.ws, days=days, source=source))
        typer.echo("")
        typer.echo(
            f"1 か月に直すと: 約 {est['minutes']:.0f} 分 / 約 ${est['cost']:.2f}"
            "（GitHub Actions の無料枠は月 2,000 分）"
        )
        if save:
            typer.echo(f"記録: {weekly.write_snapshot(rt.ws, days=days, source=source).name}")

    @app.command("takedowns")
    def takedowns_cmd(
        repo: Annotated[
            str, typer.Option("--repo", help="Issue を読むリポジトリ（owner/name）")
        ] = "hikuzawa/akiya-atlas",
        limit: Annotated[int, typer.Option("--limit", help="読む Issue の上限")] = 100,
    ) -> None:
        """取り下げ依頼の Issue を読み、非表示にするページの一覧を更新する。

        takedown ラベルだけを対象にする。needs-human と municipality は数えるだけで何もしない。
        """
        from akiya_atlas import takedown

        rt = commands.Runtime.open()
        result = takedown.sync(rt.ws, repo, limit=limit)
        typer.echo(f"非表示にするページ: {len(result.items)} 件")
        for t in result.items:
            typer.echo(f"  #{t.issue} {t.path}")
        for name, n in sorted(result.other_issues.items()):
            typer.echo(f"  （{name} の開いている Issue {n} 件は読むだけ）")

    @app.command("backfill")
    def backfill_cmd(
        only: Annotated[
            list[str] | None,
            typer.Option("--only", help="対象の都道府県（名・スラッグ・2 桁コード）。省略時は全県"),
        ] = None,
        workers: Annotated[int | None, typer.Option("--workers", "-w", help="並列数")] = None,
        force: Annotated[bool, typer.Option("--force", help="済みの工程もやり直す")] = False,
        stages: Annotated[
            str, typer.Option("--stages", help="実行する工程（カンマ区切り）")
        ] = "discover,crawl,extract,heal",
        status: Annotated[bool, typer.Option("--status", help="進捗を表示して終了")] = False,
    ) -> None:
        """全国バックフィル。県ごとに 発見→巡回→抽出→自己修復 を回す（再開可能）。"""
        from akiya_atlas import backfill

        rt = commands.Runtime.open()
        if status:
            for line in backfill.status_table(rt.ws, only):
                typer.echo(line)
            return
        wanted = tuple(s.strip() for s in stages.split(",") if s.strip())
        try:
            backfill.run_backfill(
                rt, only=only, workers=workers, force=force, stages=wanted, echo=typer.echo
            )
        except SecretsError as e:
            typer.echo(f"停止: {e}", err=True)
            raise typer.Exit(code=3) from e

    @app.command("ad-check")
    def ad_check_cmd(
        dist: Annotated[Path, typer.Option("--dist", help="検査する生成物のディレクトリ")] = Path(
            "dist"
        ),
    ) -> None:
        """広告掲載の検査（ADR 0010）。広告表記の有無と位置、/go/ 経由、宣言との一致を確かめる。

        CI では build の直後に走る。1 件でも問題があれば 1 で終了してビルドを止める。
        """
        from akiya_atlas import ad_check

        problems, summary = ad_check.check(dist)
        if problems:
            typer.echo(f"広告掲載の検査に失敗（{len(problems)} 件）:", err=True)
            for msg in problems[:50]:
                typer.echo(f"  - {msg}", err=True)
            if len(problems) > 50:
                typer.echo(f"  ... 他 {len(problems) - 50} 件", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"広告掲載の検査 OK: {summary}")

    @app.command("ad-urls")
    def ad_urls_cmd(
        offer: Annotated[
            str | None, typer.Option("--offer", help="案件 ID で絞り込む（例: kaitai-110）")
        ] = None,
        dist: Annotated[Path, typer.Option("--dist", help="読む生成物のディレクトリ")] = Path(
            "dist"
        ),
    ) -> None:
        """ASP に届け出る掲載 URL の一覧を出す（反映後に人が提出する）。"""
        from akiya_atlas import ad_check, affiliates

        rt = commands.Runtime.open()
        rows = ad_check.ad_urls(dist, rt.ws.site.base_url, offer_id=offer)
        if not rows:
            typer.echo("掲載中の広告はありません（計測 URL が入っていない、または未ビルド）")
            return
        current = ""
        for item, url in rows:
            if item.id != current:
                current = item.id
                asp = affiliates.asp_of(item)
                if asp is None:
                    where = "ASP の管理画面"
                elif asp.submit_label:
                    where = f"{asp.name} の{asp.submit_label}"
                else:
                    where = f"{asp.name}（掲載 URL の個別届け出は不要）"
                typer.echo("")
                head = f"{item.name or item.label}（{item.id} / プログラム {item.program_id}）"
                typer.echo(f"# {head}")
                typer.echo(f"# 提出先: {where}")
            typer.echo(url)


def main() -> None:
    from sitemill.cli import app

    # Windows の既定は cp932 で、表に出る記号（全角ダッシュなど）で落ちる。
    # 手順書は手元での実行を前提にしているので、出力を UTF-8 に寄せる
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    root = Path(__file__).resolve().parents[2]
    if (root / "site.toml").is_file() and "--root" not in sys.argv:
        os.chdir(root)
    _register(app)
    app()
