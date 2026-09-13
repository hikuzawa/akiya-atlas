# akiya-atlas

sitemill の最初の利用者。自治体が独自に運営する空き家バンクを横断検索できる日本語サイト。親フォルダの CLAUDE.md の配置ルールに従う。

## 構成
- `src/akiya_atlas/` サービス定義（`service.py`）、抽出スキーマ（`schema.py`）、プロンプト（`prompts/`）、ページ生成（`pages.py`）、ASP 設定（`affiliates.py`）
- `data/sources/*.yaml` 自治体と巡回 URL と巡回方針（人と AI が編集する一次設定）
- `data/state/` `data/records/` `data/runs/` パイプラインの状態と成果物（コミット対象）
- `data/raw/` `data/llm_cache/` ローカルキャッシュ（git 管理外）
- `templates/` Jinja2、`static/` CSS/JS、`dist/` 生成物（git 管理外）
- `site.toml` サイト設定。基準 URL はここ 1 か所だけで差し替える
- `src/akiya_atlas/ad_check.py` 広告掲載の検査（広告表記の有無と位置・`/go/` 経由・ASP 規約との一致）。CI では build の直後に走る
- `docs/adr/` 設計判断
- `tests/fixtures/html/` 保存済み HTML（出典 URL と取得日を `SOURCES.md` に記す）、`tests/fixtures/eval/` 抽出精度の計測ケース

## コマンド（このディレクトリで実行）
- `uv sync` / `uv run pytest` / `uv run ruff check src tests`
- `uv run sitemill discover|crawl|extract|heal|build|run|eval` / `uv run sitemill deploy --dry-run`（`run` は crawl→extract→heal→build。`--workers` でホスト並列数）
- `uv run akiya-atlas expand <県>|rediscover <県> --code ...|official-urls <県> <URL>|backfill [--status]|takedowns|ad-check|ad-urls`（都道府県の自動発見、特定市町村の選び直し、県リンク集からの公式URL表、全国バックフィル、取り下げ依頼の取り込み）。初回バックフィルの手順は `docs/runbook/backfill.md`
- 生成物の確認は `dist/` の HTML をブラウザペインで直接開くか、`uv run python -m http.server -d dist 8000`

## 守ること
- 対話・報告・質問は日本語で行う（コードとコミットメッセージは英語でよい）
- 物件の写真と詳細本文は載せない。要約（120 字以内）＋数値＋一次情報リンクのみ
- 巡回するのは自治体または自治体の移住推進組織が運営主体と明記されたサイトだけ。民間プラットフォームはリンクのみ（sources.yaml の `policy`）。運営主体の根拠（引用と URL）を yaml に残す
- 人間レビューに回すのは、AIが取れる情報をすべて取った後も**運営主体が判定できない案件だけ**にする。公式ドメインの解決（lg.jp/地理型）や相互リンクで運営主体が確認できたら、分類に応じて自動で採用する（物件一覧→巡回、登録制/JS/制度案内→リンクのみ）。公式URLは、まず候補URL、次に県の公的な市町村一覧から解決し、解決元URLを根拠に残す
- 価格・面積・築年などは sitemill の quote-then-parse でのみ値にする。LLM の推定値を混ぜない
- すべてのページに信頼シグナル（更新日時・一次情報リンク・運営者・件数）を出す。欠けるとビルドが失敗する
- 実在の物件・場所を AI 画像生成で描かない。図解は SVG のコード生成に限り「自動生成」と明記する
- 所有者向け CTA は ASP 契約が無い間は「準備中」。ダミーリンクは置かない
- 広告（アフィリエイト）は `/owners/` の枠にだけ置く。物件・市町村・トップには置かない。リンクは必ず `/go/<案件>/<枠>/` の転送ページ経由にし、広告表記をページ冒頭に出す。掲載場所と ASP 規約は `src/akiya_atlas/affiliates.py` のデータで決まり、欠ければ `ad-check` がビルドを止める（ADR 0010）。案件の追加手順は `docs/runbook/affiliates.md`
- 秘密情報は `.env` にだけ置く（手で書く。パスワードマネージャーや環境変数を探索しない）。鍵が無ければ止めて「.env に何を書くか」を提示する
- `.env.example` にはプレースホルダー（空の値）だけを置く。値を書いた時点でコミット前フックが止める
- コミット前フックは `.githooks/pre-commit`（`uv run sitemill scan-secrets --staged`、gitleaks があれば併用）。clone 後に一度 `git config core.hooksPath .githooks` で有効化する。CI でも全履歴を走査する
- 取得した生 HTML はコミットしない。テストに要る数ページだけ `tests/fixtures/html/` に置く
- **CI は sitemill のタグ固定（現在 `v0.4.1`）。sitemill の main の変更は自動では反映されない**（ADR 0006）。手元は `../sitemill` への editable 依存のままなので、ローカルで通っても CI で通らないことがある。エンジンの修正を取り込むときは、sitemill でタグを打ってから `.github/workflows/` 3 本の `ref:` を上げる（手順は `docs/runbook/operations.md` の 5 章）
- 区切りごとに `uv run pytest` と `uv run ruff check` を通してからコミットする。コミットはこのディレクトリ内で行う
