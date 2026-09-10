# akiya-atlas

sitemill の最初の利用者。自治体が独自に運営する空き家バンクを横断検索できる日本語サイト。親フォルダの CLAUDE.md の配置ルールに従う。

## 構成
- `src/akiya_atlas/` サービス定義（`service.py`）、抽出スキーマ（`schema.py`）、プロンプト（`prompts/`）、ページ生成（`pages.py`）、ASP 設定（`affiliates.py`）
- `data/sources/*.yaml` 自治体と巡回 URL と巡回方針（人と AI が編集する一次設定）
- `data/state/` `data/records/` `data/runs/` パイプラインの状態と成果物（コミット対象）
- `data/raw/` `data/llm_cache/` ローカルキャッシュ（git 管理外）
- `templates/` Jinja2、`static/` CSS/JS、`dist/` 生成物（git 管理外）
- `site.toml` サイト設定。基準 URL はここ 1 か所だけで差し替える
- `docs/adr/` 設計判断
- `tests/fixtures/html/` 保存済み HTML（出典 URL と取得日を `SOURCES.md` に記す）、`tests/fixtures/eval/` 抽出精度の計測ケース

## コマンド（このディレクトリで実行）
- `uv sync` / `uv run pytest` / `uv run ruff check src tests`
- `uv run sitemill discover|crawl|extract|build|run|eval` / `uv run sitemill deploy --dry-run`
- 生成物の確認は `dist/` の HTML をブラウザペインで直接開くか、`uv run python -m http.server -d dist 8000`

## 守ること
- 物件の写真と詳細本文は載せない。要約（120 字以内）＋数値＋一次情報リンクのみ
- 巡回するのは自治体または自治体の移住推進組織が運営主体と明記されたサイトだけ。民間プラットフォームはリンクのみ（sources.yaml の `policy`）。運営主体の根拠（引用と URL）を yaml に残す
- 価格・面積・築年などは sitemill の quote-then-parse でのみ値にする。LLM の推定値を混ぜない
- すべてのページに信頼シグナル（更新日時・一次情報リンク・運営者・件数）を出す。欠けるとビルドが失敗する
- 実在の物件・場所を AI 画像生成で描かない。図解は SVG のコード生成に限り「自動生成」と明記する
- 所有者向け CTA は ASP 契約が無い間は「準備中」。ダミーリンクは置かない
- 秘密情報は `.env` にだけ置く（手で書く。パスワードマネージャーや環境変数を探索しない）。鍵が無ければ止めて「.env に何を書くか」を提示する
- `.env.example` にはプレースホルダー（空の値）だけを置く。値を書いた時点でコミット前フックが止める
- コミット前フックは `.githooks/pre-commit`（`uv run sitemill scan-secrets --staged`、gitleaks があれば併用）。clone 後に一度 `git config core.hooksPath .githooks` で有効化する。CI でも全履歴を走査する
- 取得した生 HTML はコミットしない。テストに要る数ページだけ `tests/fixtures/html/` に置く
- sitemill は今のフェーズでは `../sitemill` への editable 依存。安定したら git タグ固定に切り替える（ADR 0006）
- 区切りごとに `uv run pytest` と `uv run ruff check` を通してからコミットする。コミットはこのディレクトリ内で行う
