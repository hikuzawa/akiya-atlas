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
- `tests/fixtures/eval/` 抽出精度の計測ケース（保存済み HTML は git 管理外。非公開の `akiya-atlas-ops` から持ってくる）

## コマンド（このディレクトリで実行）
- `uv sync` / `uv run pytest` / `uv run ruff check src tests`
- `uv run sitemill discover|crawl|extract|heal|build|run|eval` / `uv run sitemill deploy --dry-run`（`run` は crawl→extract→heal→build。`--workers` でホスト並列数）
- `uv run akiya-atlas expand <県>|rediscover <県> --code ...|official-urls <県> <URL>|backfill [--status]|subsidy-pages <県>|subsidy-backfill [--status]|takedowns|ad-check|ad-urls`（都道府県の自動発見、特定市町村の選び直し、県リンク集からの公式URL表、全国バックフィル、補助制度ページの探索と全国収集、取り下げ依頼の取り込み）。初回バックフィルの手順は `docs/runbook/backfill.md`、補助制度の全国収集は `docs/runbook/subsidies.md`
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
- 広告（アフィリエイト）は `/owners/` の枠にだけ置く。物件・市町村・トップには置かない。リンクは必ず `/go/<案件>/<枠>/` の転送ページ経由にし、広告表記をページ冒頭に出す。掲載場所と ASP 規約は `src/akiya_atlas/affiliates.py` のデータで決まり、欠ければ `ad-check` がビルドを止める（ADR 0010）。案件の追加手順は `akiya-atlas-ops/docs/affiliates.md`
- 守りたい性質は、書き込む道具ではなく**公開する直前に**確かめる（ADR 0016、sitemill ADR 0024）。`akiya-atlas publish-check` が配置の前に、巡回をやめた自治体の物件・`municipal_overrides.json` の扱い・取り下げ・公言している巡回数を見て、破れていれば配置を止める。データで確かめられる分は `tests/test_publish_check.py` でコミット済みのデータにも当てる。性質を足すときは、何が起きてなぜ既存の歯止めで見えなかったかを ADR に一緒に書く
- 巡回をやめる判断（`link_only`、物件ページを seed から外す）は、取り込み済みの物件に触れない。`finalize` の `retire_unlisted` が `status: retired` にして公開から外す（削除しない。URL には noindex の掲載終了ページが残り、サイトマップからは外れる）。「物件を巡回しているか」は `policy` ではなくseed のページ種別で判定する（補助制度のために `crawl` になっている自治体がある）
- 取り下げ依頼が自動で効くのは、**物件 1 件か市町村 1 つのページを指すものだけ**（ADR 0016 追記）。県・トップなどを指す依頼は隠さず `takedowns.json` の `unscoped` に残し、人が対象を確かめて Issue の URL を直す。県のページを指す依頼 1 件で長野県の全物件が 5 日間隠れたことがある。取り込みと公開前検査は非表示のページ数を必ず出す
- CI の `actions/cache` の `path` にコミット対象（`data/records`・`data/state`・`data/sources`・`data/review`・`data/subsidies`・`data/search`・`data/runs`・`data/reference`）を含めない。今は `data/raw` と `data/llm_cache` だけ（sitemill ADR 0024）
- 秘密情報は `.env` にだけ置く（手で書く。パスワードマネージャーや環境変数を探索しない）。鍵が無ければ止めて「.env に何を書くか」を提示する
- `.env.example` にはプレースホルダー（空の値）だけを置く。値を書いた時点でコミット前フックが止める
- コミット前フックは `.githooks/pre-commit`（`uv run sitemill scan-secrets --staged`、gitleaks があれば併用）。clone 後に一度 `git config core.hooksPath .githooks` で有効化する。CI でも全履歴を走査する
- 取得した生 HTML はコミットしない。**このリポジトリは public**。テストに要る数ページは非公開の `akiya-atlas-ops` の `fixtures/html/` に置き、`tests/fixtures/html/` へ複写して使う（git 管理外。無ければテストと eval は skip する）
- 公開できないものは `akiya-atlas-ops`（private）に置く。保存済み HTML（再配布しない約束）、ASP の申請状況と選定の実データ（他社の数字）、お問い合わせから起票される Issue（プライバシーポリシーで「非公開」と公言している）
- **CI は sitemill のタグ固定（現在 `v0.6.0`）。sitemill の main の変更は自動では反映されない**（ADR 0006）。手元は `../sitemill` への editable 依存のままなので、ローカルで通っても CI で通らないことがある。エンジンの修正を取り込むときは、sitemill でタグを打ってから `.github/workflows/` 4 本（checks・pipeline・search・weekly）の `ref:` を上げる（手順は `docs/runbook/operations.md` の 5 章）
- **このディレクトリは複数のセッションが同時に使う**（worktree 分離は効かない）。他人の未コミットの変更は自分の build やテストにも入るので、結果を使う前に `git status` を見る。他人の変更には触らず、コミットは `git commit -m ... -- 自分のファイル` の形にする（新しく作ったファイルは、その前に自分の分だけ `git add -- 新しいファイル`。`git add` のあとの素の `git commit` は、他人が `add` 済みの変更まで含める。2026-09-17 に実際に起きた）。`pull --rebase` が止まるなら `fetch` ＋ `merge --ff-only`。詳しくは親フォルダの CLAUDE.md「作業環境の制約」
- 検索の数字を見て公開ページを変えるときは、`docs/improvements/NNNN-*.md` に仮説・変更・変更前の数字・判断の基準を**結果を見る前に**書き、比べるページの組を `NNNN-baseline.json` に固定する。変更後は `uv run akiya-atlas improvement-report` で同じ組を数える（0001 が最初の例）
- 区切りごとに `uv run pytest` と `uv run ruff check` を通してからコミットする。コミットはこのディレクトリ内で行う
