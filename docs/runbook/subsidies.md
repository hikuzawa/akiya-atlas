# 補助制度の全国収集（手元の PC で回す）

自治体の補助金ページを見つけて取り込む。設計は ADR 0013、抽出の仕様は ADR 0012 の追記。
GitHub Actions の無料枠を使わず、手元の PC で回す（全国バックフィルと同じ扱い）。

進捗は `data/runs/subsidies.json` に県ごとに残る。**途中で止めても同じコマンドを再実行すれば、
済んだ県を飛ばして続きから進む。**

## 1. 実行前のチェックリスト

| | 確認すること | やり方 |
|---|---|---|
| 1 | `.env` に `ANTHROPIC_API_KEY` がある | 抽出に使う。無ければ最初の県で止まる |
| 2 | **日次パイプラインを止めた** | 2 章。数時間走るので必須 |
| 3 | main を取り込み、作業ツリーがきれい | `git pull --rebase` → `git status --short` |
| 4 | この作業ツリーで他のコマンドを動かさない | `sitemill crawl` / `run` / `heal` は `data/state/crawl.json` を共有する。別セッションで作業中なら終わるまで待つ |
| 5 | PC がスリープしない | `powercfg /change standby-timeout-ac 0`（終わったら元に戻す） |
| 6 | 空き容量 1GB 程度 | 生 HTML は `data/raw/`（git 管理外）に溜まる |

ネットワークが切れても実行は止まらない。その県が失敗して記録に残り、次の県へ進む（6 章）。

## 2. 日次パイプラインを止める（必須）

止めないと 3 つ困る。

- **同じサイトを 2 か所から同時に取りに行く**。ホストごとの間隔は 1 プロセス内でしか守れないので、
  相手サイトへの約束（robots・3 秒間隔）が崩れる
- `data/state/crawl.json` を日次も書き換えるので、あとで rebase が競合する
- 日次の抽出と重なって API の費用が二重にかかる

GitHub の Actions → `pipeline` → **Disable workflow**。終わって反映したら Enable に戻し、
`Run workflow`（mode=full）を 1 回手で回して通ることを確認する。全国バックフィルの
`docs/runbook/backfill.md` 5 章と同じ手順。`weekly` は日曜だけなので、その日でなければ触らなくてよい。

## 3. 実行

PowerShell では文字コードを UTF-8 にしてから実行する（ログの日本語が化けるため）。

```powershell
$env:PYTHONUTF8="1"
cd C:\projects\akiya-atlas
uv run akiya-atlas subsidy-backfill 2>&1 | Tee-Object -Encoding utf8 logs\subsidies.log
```

ウィンドウを閉じても続くようにするなら、裏で動かす。**出力先は絶対パスで書く**
（`Start-Process` の `-RedirectStandardOutput` は `-WorkingDirectory` ではなく呼び出し元の
現在地から解決される）。

```powershell
New-Item -ItemType Directory -Force logs | Out-Null
$p = Start-Process uv -ArgumentList "run","akiya-atlas","subsidy-backfill" `
     -WorkingDirectory C:\projects\akiya-atlas -WindowStyle Hidden -PassThru `
     -RedirectStandardOutput C:\projects\akiya-atlas\logs\subsidies.log `
     -RedirectStandardError  C:\projects\akiya-atlas\logs\subsidies.err.log
$p.Id | Out-File logs\subsidies.pid
Get-Content logs\subsidies.log -Wait -Tail 20   # Ctrl+C で見るのをやめても実行は続く
```

1 県だけ試すときは `--only`。

```powershell
uv run akiya-atlas subsidy-backfill --only 徳島県
```

## 4. 並列と所要時間

**県は順に、県の中は市町村単位で並列**に動く（探索・巡回・抽出とも）。市町村 ≒ 別ホストなので
並列にでき、1 ホストあたりの間隔（3 秒 + robots の Crawl-delay）は並列でも縮まらない
（PoliteClient のホスト別ロックが守る。sitemill ADR 0013）。

並列数は `site.toml` の `[crawl] max_workers`（既定 8）。一時的に変えるなら `--workers`。
非力な PC や回線が細いときは 4 に下げる。

```powershell
uv run akiya-atlas subsidy-backfill --workers 4
```

実測（徳島県 24 自治体・137 ページ取得。結果は同じ 40 ページ / 15 自治体）。

| | 所要 |
|---|---|
| 逐次（並列数 1） | 6.9 分 |
| 並列 8 | **1.1 分** |

ここから見込んだ全国の値。

| 項目 | 見込み |
|---|---|
| 所要時間 | **2 時間前後**（探索 1.5 時間＋巡回・抽出 0.5 時間。並列 8） |
| 補助金ページが見つかる自治体 | 全 1,741 のうち 900 前後（53%） |
| 取り込むページ | 2,400 前後 |
| 取り込む制度 | 3,000 件前後 |
| LLM の費用 | 15 ドル前後（1 県あたり 0.2〜0.3 ドル） |

1 県あたりは 1〜3 分、北海道（185 自治体）だけ 10 分前後かかる。

途中経過はいつでも見られる。

```powershell
uv run akiya-atlas subsidy-backfill --status
```

## 5. 終わったら

1. `uv run pytest -q` と `uv run sitemill build` と `uv run akiya-atlas ad-check` を通す
2. データのみのブランチを作って push する（全国バックフィルと同じ。`docs/runbook/backfill.md` 5 章）

   ```powershell
   git switch -c data/subsidies-backfill
   git add data/subsidies data/sources data/review data/runs data/state
   git status --short | Select-String -NotMatch '^[AM]  data/'   # data 以外が混ざっていないか
   git commit -m "data: 補助制度の全国収集"
   git pull --rebase origin main
   git push -u origin data/subsidies-backfill
   ```

3. main にマージする
4. Actions で `pipeline` を Enable に戻し、`Run workflow` で 1 回手動実行して通ることを確認する
5. 以後の日次は自動で続く。補助金のページは変化が遅いので、適応間隔で週 1 回に落ちる

## 6. 途中で止めたいとき

プロセスを終わらせるだけでよい。**県の区切りで進捗を保存する**ので、次の実行は止めた県の頭から
やり直す（1 県数分なので取り戻せる）。

```powershell
Stop-Process -Id (Get-Content logs\subsidies.pid)
```

## 7. 困ったとき

- **1 県が失敗した**: 止まらずに次の県へ進み、理由が進捗ファイルに残って最後にまとめて出る。
  直してから同じコマンドを再実行すると、失敗した県だけやり直す
- **robots.txt が取れない自治体がある**: 証明書の不備などで取得できないサイトは、その回は巡回しない
  （高知県東洋町・徳島県板野町に実例）。異常ではない
- **補助金ページが 1 件も見つからない県がある**: 公式サイトの作りによる。取得率は県で差が出る
  （実測で 47〜63%）
- **費用が見込みより増える**: `--status` の合計を見る。1 県あたり 0.2〜0.3 ドルが目安。
  大きく超えるなら止めて相談する
- **`--status` で済んだはずの県が「未」と出る**: `subsidy-pages` 単体で集めた県は進捗に載らない
  （高知県が該当）。データは入っているので、もう一度回す必要はない
