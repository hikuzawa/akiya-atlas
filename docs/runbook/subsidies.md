# 補助制度の全国収集（手元の PC で回す）

自治体の補助金ページを見つけて取り込む。設計は ADR 0013、抽出の仕様は ADR 0012 の追記。
GitHub Actions の無料枠を使わず、手元の PC で回す（全国バックフィルと同じ扱い）。

進捗は `data/runs/subsidies.json` に県ごとに残る。**途中で止めても同じコマンドを再実行すれば、
済んだ県を飛ばして続きから進む。**

## 1. 前提

- `.env` に `ANTHROPIC_API_KEY` があること（抽出に使う）
- 最新の main を取り込んでいること。日次が走っている間に始めると、データのコミットで競合する
- PC がスリープしないこと（Windows の例）

  ```powershell
  powercfg /change standby-timeout-ac 0
  ```

## 2. 実行

PowerShell では文字コードを UTF-8 にしてから実行する（ログの日本語が化けるため）。

```powershell
$env:PYTHONUTF8="1"
cd C:\projects\akiya-atlas
uv run akiya-atlas subsidy-backfill 2>&1 | Tee-Object -Encoding utf8 logs\subsidies.log
```

長いので、閉じても続くように裏で動かす形にしてもよい。

```powershell
$p = Start-Process -FilePath "uv" -ArgumentList "run","akiya-atlas","subsidy-backfill" -WorkingDirectory "C:\projects\akiya-atlas" -WindowStyle Hidden -PassThru -RedirectStandardOutput "logs\subsidies.log" -RedirectStandardError "logs\subsidies.err"
$p.Id
```

1 県だけ試すときは `--only`。

```powershell
uv run akiya-atlas subsidy-backfill --only 徳島県
```

## 3. 見ておく数字

実測（高知県 34 自治体・徳島県 24 自治体）から見込んだ全国の値。

| 項目 | 見込み |
|---|---|
| 所要時間 | 8 時間前後（1 自治体あたり 0.3 分） |
| 補助金ページが見つかる自治体 | 全 1,741 のうち 900 前後（53%） |
| 取り込むページ | 2,400 前後 |
| 取り込む制度 | 3,000 件前後 |
| LLM の費用 | 15 ドル前後 |

途中経過はいつでも見られる。

```powershell
uv run akiya-atlas subsidy-backfill --status
```

**1 県で失敗しても止まらない。** 失敗した県は進捗ファイルに理由が残り、最後にまとめて出る。
直してから同じコマンドを再実行すると、失敗した県だけやり直す。

## 4. 終わったら

1. `uv run pytest -q` と `uv run sitemill build` を通す
2. データのみのブランチを作って PR にする（全国バックフィルと同じ。`docs/runbook/backfill.md` の 5 章）

   ```powershell
   git switch -c data/subsidies-backfill
   git add data/subsidies data/sources data/review data/runs
   git commit -m "data: 補助制度の全国収集"
   ```

3. 日次はそのあと自動で続く。補助金のページは変化が遅いので、適応間隔で週 1 回に落ちる

## 5. 途中で止めたいとき

プロセスを終わらせるだけでよい。県の区切りで進捗を保存しているので、次の実行は途中の県から始まる。

```powershell
Stop-Process -Id $p.Id
```

## 6. 困ったとき

- **robots.txt が取れない自治体がある**: 証明書の不備などで取得できないサイトは、その回は巡回しない
  （高知県東洋町・徳島県板野町に実例）。異常ではない
- **補助金ページが 1 件も見つからない県がある**: 公式サイトの作りによる。取得率は県で差が出る
  （実測で 47〜63%）
- **費用が見込みより増える**: `--status` の合計を見る。1 県あたり 0.2 ドル前後が目安
