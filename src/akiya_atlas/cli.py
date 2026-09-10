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
        for n in table.ignored:
            typer.echo(f"  無視: {n}")

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


def main() -> None:
    from sitemill.cli import app

    root = Path(__file__).resolve().parents[2]
    if (root / "site.toml").is_file() and "--root" not in sys.argv:
        os.chdir(root)
    _register(app)
    app()
