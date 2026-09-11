# 初回バックフィル手順書（残り 44 県の発見と初回抽出）

GitHub Actions の無料枠を使わず、手元の PC または Raspberry Pi で全国の初回発見・初回抽出を回し、
結果（`data/**`）だけを main に反映する手順。設計は ADR 0008 / sitemill ADR 0013。
長野・沖縄・香川は済みなので、対象は残り 44 県。

## 1. 前提の準備

### 1-1. 機材と OS
- **PC**: Windows / macOS / Linux いずれも可。常時給電できること。
- **Raspberry Pi**: Pi 4 か Pi 5（メモリ 4GB 以上推奨）、Raspberry Pi OS 64-bit。
  電源は公式アダプタ、ストレージは microSD より USB SSD が安心。夜間に無人で回すので
  `tmux` か `nohup` で実行する（後述）。

### 1-2. ツール
```bash
# uv（Python の環境と依存を管理）
curl -LsSf https://astral.sh/uv/install.sh | sh    # Windows は PowerShell: irm https://astral.sh/uv/install.ps1 | iex
uv python install 3.12
sudo apt-get install -y git tmux                     # Raspberry Pi の場合
```

### 1-3. リポジトリ
`sitemill` は `../sitemill` への editable 依存なので、**2 つを隣同士に置く**。
```bash
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/hikuzawa/sitemill.git
git clone https://github.com/hikuzawa/akiya-atlas.git      # private。gh auth login か PAT が要る
cd akiya-atlas
uv sync
git config core.hooksPath .githooks                        # コミット前の秘密情報検査
uv run pytest -q                                           # 動作確認（外部アクセスなし）
```

### 1-4. 秘密情報（`.env`）
`akiya-atlas/.env` を手で作る。必要なのは抽出用の鍵だけ。他は空でよい。
```
ANTHROPIC_API_KEY=<値>
```
`.env` は git 管理外。値は他の場所から探索しない（CLAUDE.md の規約）。

### 1-5. Windows（PowerShell）で回す場合
PowerShell 7 で検証済み（富山県で発見→巡回→抽出→自己修復を通した）。次の 4 点だけ Linux と違う。

1. **uv の導入**: `irm https://astral.sh/uv/install.ps1 | iex`
2. **文字コード**: セッションの最初に `$env:PYTHONUTF8 = "1"` を入れる。Windows の既定ロケールは
   cp932 のままなので、これが無いと文字コードを明示していない読み書きが化ける。ログに残すときは
   `Tee-Object -Encoding utf8`（既定の `>` は PowerShell 5.1 だと UTF-16 になる）。
   ```powershell
   $env:PYTHONUTF8 = "1"
   uv run akiya-atlas backfill --only 富山県 2>&1 | Tee-Object -FilePath logs\backfill.log -Append -Encoding utf8
   ```
   コマンドの引数に日本語（県名）をそのまま渡してよい。
3. **長時間の実行**: tmux の代わりに `Start-Process` で切り離す。ウィンドウを閉じても走り続ける。
   ```powershell
   New-Item -ItemType Directory -Force logs | Out-Null
   $p = Start-Process uv -ArgumentList "run","akiya-atlas","backfill","--stages","discover","--workers","8" `
        -RedirectStandardOutput logs\backfill-discover.log -RedirectStandardError logs\backfill-discover.err.log `
        -WindowStyle Hidden -PassThru
   $p.Id | Out-File logs\backfill.pid                      # 止めるとき: Stop-Process -Id (Get-Content logs\backfill.pid)
   Get-Content logs\backfill-discover.log -Wait -Tail 20   # 県ごとの結果（Ctrl+C で見るのをやめても実行は続く）
   (Select-String logs\backfill-discover.err.log -Pattern "policy=").Count   # 判定済みの市町村数
   ```
   `logs/` は git 管理外。標準出力（`.log`）には県ごとの 1 行、標準エラー（`.err.log`）には市町村ごとの
   判定と robots の警告が出る。1 県に 10 分以上かかることがある（北海道は 185 市町村）ので、
   細かい進捗は `.err.log` の `policy=` の行数を見るのが早い。
4. **スリープさせない**: 実行前に電源設定を変える。終わったら元に戻す。
   ```powershell
   powercfg /change standby-timeout-ac 0    # スリープしない（AC 電源時）
   powercfg /change hibernate-timeout-ac 0  # 休止状態にしない
   ```
   画面が消えるのは構わない。ノート PC は AC につないでおく（バッテリー時の設定は `-dc`）。

### 1-6. 並列数
`site.toml` の `[crawl] max_workers` が既定の並列数（8）。Raspberry Pi 4 なら 4 程度に下げる。
コマンドの `--workers` で一時的に上書きもできる。並列にしても **1 ホストあたりの間隔（3 秒 + robots の
Crawl-delay）は縮まらない**（ホスト別ロックで担保）。

## 2. 県の市町村リンク集（**47 県ぶん用意済み。作業は不要**）
候補ドメインの推測が外れる自治体（独自ドメイン: `higashikagawa.jp`、`nakijin.jp` 等）は、県の公的な
市町村リンク集を正解源にする。**47 都道府県すべての表が `data/reference/{slug}_official_urls.json` に
入っている**（出典 URL は `data/reference/SOURCES.md` の表）。46 県は全市区町村を解決済み、北海道だけ
178/185（北方領土の 5 村はサイトが無く、同名が 2 つある泊村は取り違えを避けて対応づけない）。

作り直したいときだけ、次のコマンドを使う（ページを礼儀正しく 1 回だけ取得する）:
```bash
uv run akiya-atlas official-urls 宮城県 "<リンク集ページの URL>" --name "宮城県 市町村ホームページ一覧"
```
「未対応」が出た市町村は、推測ドメインで解決できれば問題なく、できなければその市町村だけ
`pending`（人間確認）になる。表を差し替えたら `uv run akiya-atlas expand 宮城県` で再発見する。

## 3. 実行

### 3-1. まず発見だけ全県（LLM 費用ゼロ）
```bash
tmux new -s backfill                       # Raspberry Pi / Linux。切断しても続く
cd ~/projects/akiya-atlas
uv run akiya-atlas backfill --stages discover --workers 8 2>&1 | tee -a backfill-discover.log
```
- 進捗は `data/runs/backfill.json` に県×工程で記録される。`uv run akiya-atlas backfill --status` で一覧。
- 済みの県（長野・沖縄・香川）は自動的に発見済み扱いになる。
- 所要の目安（実測: 富山県 15 市町村・並列 8 で 1.1 分＝1 市町村あたり約 0.07 分）。
  残り 1,600 市町村で **2 時間前後**。Raspberry Pi（並列 4）なら 4〜6 時間。夜間に回す想定。
- 終わったら `--status` で県ごとの `crawl=` `link_only=` `pending=` を確認する。pending が多い県は
  2 の表を用意して `uv run akiya-atlas expand <県名>` で再発見する。

### 3-2. 巡回・抽出・自己修復
```bash
uv run akiya-atlas backfill --stages crawl,extract,heal --workers 8 2>&1 | tee -a backfill-extract.log
```
- 巡回対象は静的な物件一覧を持つ自治体だけ（全国実測で 270 自治体＝全体の 16%）。
- 所要の目安（**全国実測 2026-09-11**）: 巡回 3.6 分・抽出 2.2 分・自己修復 28 分。
  自己修復は「0 件だった自治体」1 件につき約 34 秒かかるので、初回はここが一番長い。
- 抽出の費用は Haiku 4.5 で **全国 271 ページ・3,739 項目で約 $4.8**（入力 157 万・出力 64 万トークン）。
  `data/runs/latest-extract.json` の `llm.input_tokens / output_tokens` で実費を確認できる。
- `heal` は「巡回したのに 0 件」の自治体を同一サイト内で選び直し、より一覧らしいページがあれば差し替えて
  その場で再巡回・再抽出、無ければ状態を info/none に見直す。人手の修正は要らない。

### 3-3. 1 県だけ・やり直し
```bash
uv run akiya-atlas backfill --only 宮城県                  # 1 県だけ全工程
uv run akiya-atlas backfill --only 宮城県 --force           # 済みでもやり直す
uv run akiya-atlas expand 宮城県                            # 発見だけやり直す（auto.yaml を再生成）
uv run akiya-atlas rediscover 宮城県 --code 042021          # 特定の市町村だけ候補を選び直す
```

## 4. 途中で止まった場合の再開
- **同じコマンドをもう一度実行するだけ**。`data/runs/backfill.json` にある済みの工程は飛ばす。
  Windows なら `Get-Content logs\backfill-discover.log -Tail 5` で最後にどこまで進んだかを見てから再実行する。
- 発見の途中で止まった県は、その県の発見だけ最初からやり直す（1 県数分）。
- 巡回は `data/state/crawl.json` の条件付き GET で続きから、抽出は未処理（pending）のページだけが対象。
- Raspberry Pi の電源断・SSH 切断: `tmux attach -t backfill` で戻る。プロセスが死んでいれば再実行。
- 途中経過を守るため、日次のうちに 1 度は `git commit`（下記 5）しておくと安心。

## 5. 終了後に結果を main に反映する
1. 手元で検証する（公開前チェックと PII 検査が通ることを確認）:
   ```bash
   uv run sitemill build
   uv run pytest -q
   ```
2. **日次パイプラインを一時停止**する（同じ `data/state/crawl.json` を日次も更新するため、競合を避ける）。
   GitHub の Actions → `pipeline` → 「Disable workflow」。反映後に「Enable workflow」で戻す。
3. データだけをブランチにコミットして push する（`data/**` のみのコミットは checks.yml が走らない）:
   ```bash
   git checkout -b backfill/2026-09
   git add data/                         # sources / records / state / runs / reference / review
   git status --short | grep -v '^A  data/\|^M  data/' # data 以外が混ざっていないことを確認
   git commit -m "data: nationwide backfill (44 prefectures)"
   git pull --rebase origin main
   git push -u origin backfill/2026-09
   ```
   rebase で `data/state/crawl.json` が競合したら、両方の URL を残して `fetched_at` が新しい方を採る
   （sitemill の CrawlState は URL ごとの辞書なので、上下どちらも捨てない）。
4. GitHub で PR を作って main にマージする（自分のリポジトリなら直接 main へ push でもよい）。
5. Actions で `pipeline` を Enable に戻し、`Run workflow` で手動実行して build と deploy が通ることを確認する。
6. 以後の日次は GitHub Actions のホスト並列の単一ジョブで回る（月 900〜1,200 分の見込み、無料枠内）。

## 6. 困ったとき
- `停止: ANTHROPIC_API_KEY が設定されていません` → `.env` を確認（1-4）。
- `robots.txt を取得できないため今回は巡回しない` が大量に出る → 候補ドメインの推測（DNS 失敗）で正常。
  県の表（2）があれば減る。
- `公開前チェックに失敗` / `個人情報らしき文字列` → 該当 source を `rediscover` で選び直すか、
  `data/reference/municipal_phones.json` に自治体の代表電話を登録する（ADR 0012）。
- 1 県が異常に遅い → `--workers` を下げる、または `--only` で後回しにして先に他県を進める。
