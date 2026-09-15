# 人間側で必要な作業（2026-09-10 更新）

AI が全自動で回すための前提として、アカウント作成や鍵の登録など人にしかできない作業を並べる。
上から順に済ませると、`uv run sitemill run` → 日次の GitHub Actions → Cloudflare Pages 公開までがつながる。

## 今すぐ必要（縦断パイプラインを完走させるため）
1. ~~**Anthropic API キー**を `akiya-atlas/.env` に書く~~ → 済み（2026-09-10）。**ただし鍵の作り直しを推奨**: 一度 `.env.example` に書かれた値がローカルの git 履歴に入り（push 前に履歴から除去済み）、監査ログにも表示されたため。作り直したら `.env` を更新し、下の表のコマンドで GitHub Secret も更新する。
   これが無いと `sitemill extract` は「.env に何を書くか」を表示して止まる。抽出モデルは `site.toml` の `[llm] model`（既定 `claude-haiku-4-5`）で変えられる。
2. ~~**運営者名と連絡手段**を決め、`site.toml` の `[operator]` に書く~~ → 済み（2026-09-12）。運営者は「空き家アトラス 運営」、連絡先は `tools/contact_form/` の Apps Script が作った Google フォーム。フッターと `/about/` に出る。お預かりする情報の扱いは `/about/` のプライバシーポリシーに記載済み

## 公開までに必要
3. ~~**GitHub リポジトリ**を作成し push する~~ → 済み（2026-09-10）。sitemill は public（https://github.com/hikuzawa/sitemill）、akiya-atlas は private（https://github.com/hikuzawa/akiya-atlas）。sitemill が public のため、CI からの sitemill checkout に読み取り用 PAT は不要。
   両リポジトリの git 作者設定はローカルで済ませてある。
4. ~~**Cloudflare アカウント**と API トークン（Account > Cloudflare Pages: Edit）・アカウント ID を発行する~~ → 済み（2026-09-10、Secrets に登録済み）。Pages プロジェクト `akiya-atlas` は手で作らなくてよい（2026-09-10 に CI が作成済み。既定ホストは `akiya-atlas-asb.pages.dev`。`akiya-atlas.pages.dev` は第三者の英語サイト「Akiya Atlas」が使用中で取得できない）。`pipeline.yml` が deploy の直前に「無ければ作成、あれば何もしない」で用意する（`wrangler pages project create`）。
5. ~~**GitHub Secrets** を akiya-atlas に登録する~~ → 済み（2026-09-10）: `ANTHROPIC_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`。
6. ~~**Cloudflare Web Analytics** を有効にする~~ → 済み（2026-09-12）。RUM の「自動挿入」モードで有効化済みで、Cloudflare が配信時に計測タグを差し込む（apex と pages.dev の両方で確認済み）。**`CF_WEB_ANALYTICS_TOKEN` は設定しない**（入れるとタグが 2 つ出て二重計測になる。ADR 0011）。
7. **Google Maps Platform**: Maps Embed API を有効にし、HTTP リファラで `akiya-atlas-asb.pages.dev` と `akiya-atlas.com` に制限した公開キーを発行して `GOOGLE_MAPS_EMBED_KEY` に入れる（無い間は地図は外部リンクにフォールバック）。
8. ~~**ドメイン `akiya-atlas.com`** を取得し、Pages にカスタムドメイン（apex と www）を追加する~~ → 済み（2026-09-10）。`site.toml` の `base_url` は `https://akiya-atlas.com` に切替済みで、CI が配置前に本番ドメインを固定検査する。
9. **www と pages.dev から apex への 301（Bulk Redirects、約 5 分）**。Pages の `_redirects` はドメイン単位のリダイレクトに非対応（公式の Advanced redirects 表で ❌。wrangler はエラーを出さずに受理するが効かない）なので、Cloudflare ダッシュボードのアカウントレベル「Bulk redirects」で行う。
   1. 「Create Bulk Redirect List」で 2 件を登録: Source `https://www.akiya-atlas.com` → Target `https://akiya-atlas.com`、Status 301。Source `https://akiya-atlas-asb.pages.dev` → Target `https://akiya-atlas.com`、Status 301。各項目の「Edit parameters」で「Subpath matching」「Preserve path suffix」「Preserve query string」を有効にする（「Include subdomains」は無効のまま。プレビュー `<hash>.akiya-atlas-asb.pages.dev` を巻き込まないため）。
   2. 「Create Bulk Redirect Rule」でそのリストを選び「Save and Deploy」。
   3. 確認: `curl -sI https://www.akiya-atlas.com/nagano/?x=1` が 301 で `location: https://akiya-atlas.com/nagano/?x=1`、`curl -sI https://akiya-atlas-asb.pages.dev/` が 301 で apex を指す。
   www だけならゾーンの Redirect Rules テンプレート「Redirect from WWW to Root」でもよいが、pages.dev は Bulk Redirects でしか扱えない。

9. ~~**Google Search Console** の登録と取り込み~~ → 済み（2026-09-14）。
   検索パフォーマンスとインデックス状況を日次で取り込み、週次 Issue に出す（sitemill ADR 0023）。
   - サービスアカウントを Search Console のプロパティに「制限付き」で追加済み
   - 鍵は `.env` と GitHub Secrets の `GOOGLE_SEARCH_CONSOLE_KEY`（JSON を base64 にした 1 行）
   - `site.toml` に `[search_console]` は書かない。`base_url` のホストから `sc-domain:akiya-atlas.com` を組み立てる
   - 取り込んだ記録は `data/search/` にコミットされる。鍵が無い間は取り込みだけが飛び、日次は止まらない

## 収益化のために必要
10. **ASP アカウント**（不動産一括査定・解体一括見積・空き家買取の各案件）を契約し、計測 URL を `src/akiya_atlas/affiliates.py` の `Offer.url` に入れる。入れるまで CTA は「準備中」表示。
    - A8.net の解体案件（解体工事110番、プログラム `s00000015223012`）は 2026-09-12 に承認済み。`kaitai-110` として登録してあり、**計測 URL だけが未設定**。A8 の管理画面で発行して渡せば公開される
    - 案件の渡し方・登録手順・飛び先 URL の確認・掲載 URL の届け出は `akiya-atlas-ops/docs/affiliates.md`
11. **掲載 URL の届け出**: 反映後に `uv run akiya-atlas ad-urls --offer <案件>` の出力を、A8 の「広告掲載URL管理」に登録する。
12. 各 ASP の掲載ルールは `affiliates.py` の `ASPS` にデータとして持ち、ビルド時に検査する（ADR 0010）。規約の変更通知が来たらここを直す。広告表記は `templates/partials/macros.html` の `ad_notice` にあり、`Offer.url` が入った時点で `/owners/` の冒頭に自動で出る（表記だけが先に出ることはない）。

## 任意・後で
13. Street View を使う場合は Geocoding API の有効化（所在地から緯度経度を得るため）。
14. 公式 SNS（YouTube / Instagram / X）の埋め込みを使う場合、Instagram と X は oEmbed の利用登録が要る。
15. Raspberry Pi で回す場合は uv を入れ、`akiya-atlas` を clone して `.env` を置き、cron で `uv run sitemill run` を実行する。

## AI 側で次に行う作業（人の作業を待たずに進められるもの）
- 伊那市の物件情報サイト（SPA）の JSON API の有無と規約確認。使えなければ link_only のまま
- 東御市・佐久市・大町市の補助制度ページの確認と `data/sources/nagano.yaml` への追記（出典 URL と確認日付き）
- 本番の LLM 応答を `tests/fixtures/eval/` に保存し、手作りの応答と入れ替えて抽出精度を再計測
- 巡回 2 回目以降の差分検知（304 / ハッシュ一致）が実サイトで期待どおりか確認

## GitHub Secrets の登録コマンド（akiya-atlas、2026-09-10 追記）
値は画面に出さず、ファイルや標準入力から流し込む。登録後は `gh secret list --repo hikuzawa/akiya-atlas` で確認する。

| Secret | 値の発行場所 | 登録コマンド |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic Console → API Keys。この環境の `.env` から登録済み。鍵を作り直したら再実行 | `grep '^ANTHROPIC_API_KEY=' .env \| cut -d= -f2- \| gh secret set ANTHROPIC_API_KEY --repo hikuzawa/akiya-atlas` |
| `CLOUDFLARE_API_TOKEN` | Cloudflare ダッシュボード → My Profile → API Tokens → Create Token → テンプレート「Cloudflare Pages — Edit」（Account Resources に対象アカウント） | `gh secret set CLOUDFLARE_API_TOKEN --repo hikuzawa/akiya-atlas`（プロンプトに貼り付け） |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare ダッシュボード → Workers & Pages の概要ページ右側「Account ID」 | `gh secret set CLOUDFLARE_ACCOUNT_ID --repo hikuzawa/akiya-atlas` |
| `GOOGLE_MAPS_EMBED_KEY`（任意） | Google Cloud Console → APIs & Services → Credentials。Maps Embed API のみ、HTTP リファラで制限 | `gh secret set GOOGLE_MAPS_EMBED_KEY --repo hikuzawa/akiya-atlas` |
| `GOOGLE_SEARCH_CONSOLE_KEY` | Google Cloud のサービスアカウントの JSON を base64 にした 1 行。Search Console のプロパティに「制限付き」で追加しておく | `base64 -w0 key.json \| gh secret set GOOGLE_SEARCH_CONSOLE_KEY --repo hikuzawa/akiya-atlas` |
| `OPS_REPO_TOKEN` | **必須**。GitHub → Settings → Developer settings → Personal access tokens （fine-grained）。対象リポジトリを `hikuzawa/akiya-atlas-ops` だけに絞り、権限は **Contents: Read-only** と **Issues: Read-only** | `gh secret set OPS_REPO_TOKEN --repo hikuzawa/akiya-atlas` |
| `CF_WEB_ANALYTICS_TOKEN` | **登録しない**。計測は Cloudflare の RUM 自動挿入で行うため、値を入れるとタグが 2 つ出て二重計測になる（ADR 0011） | — |

注意: `.env.example` には値を書かない（git にコミットされる）。値は `.env` だけに置く。

`OPS_REPO_TOKEN` が無いと日次パイプラインは**失敗する**。保存済み HTML の checkout と、
取り下げ依頼（お問い合わせから起票される Issue）の読み取りに要るため。取り下げ依頼を黙って
読み飛ばすより、止まって気づけるほうがよい。

## リポジトリを作り直した（2026-09-16）

個人メールと再配布できない fixture を履歴から消したが、**マージ済み PR の参照（`refs/pull/*`）が
書き換え前の履歴を保持し続ける**ことが分かった。force push でも GC でも消えない。public にすると
`git fetch origin refs/pull/2/head` で旧履歴を丸ごと復元できてしまうので、作り直した。

- 旧: `hikuzawa/akiya-atlas-archive`（private のまま保存。**Actions は無効化済み**。
  有効のままだと日次のスケジュールがこちらでも動き、本番へ配置してしまう）
- 新: `hikuzawa/akiya-atlas`（書き換え済みの main だけ。PR 参照ゼロ）

失ったのはマージ済み PR 2 件と Issue 3 件（pipeline-failure 2・週次まとめ 1）。
**Secret は引き継がれないので再登録が要る。**

| Secret | 取り直し方 |
| --- | --- |
| `ANTHROPIC_API_KEY` | `.env` にある。下のコマンドで登録 |
| `GOOGLE_SEARCH_CONSOLE_KEY` | `.env` にある。下のコマンドで登録 |
| `CLOUDFLARE_ACCOUNT_ID` | **`.env` は空**。Cloudflare ダッシュボード → Workers & Pages の右側からコピー |
| `CLOUDFLARE_API_TOKEN` | **`.env` は空。値は二度と読めない**ので、テンプレート「Cloudflare Pages — Edit」で作り直す（古いトークンは使わなくなるので失効させてよい） |
| `OPS_REPO_TOKEN` | 新規発行（下の表） |

Cloudflare の 2 つが揃うまで、日次は build と検査まで通って**配置だけスキップ**される（run は成功扱い）。

## 公開リポジトリになった（2026-09-16）

`hikuzawa/akiya-atlas` は public、`hikuzawa/akiya-atlas-ops` は private。
公開できないものは ops 側に置く（保存済み HTML・ASP の申請状況と選定の実データ・お問い合わせ Issue）。

人がやる作業:

1. お問い合わせフォーム（Apps Script）のスクリプトプロパティ `GITHUB_REPO` を
   `hikuzawa/akiya-atlas-ops` に変更する。フォームの編集画面 → 拡張機能 → Apps Script →
   プロジェクトの設定 → スクリプト プロパティ。**変更するまで、新しいお問い合わせは public 側に
   起票される**
2. Apps Script の `GITHUB_TOKEN` に ops リポジトリの Issues: Read and write の権限があることを
   確認する（ラベルの作成にも要る）。`setup()` を 1 度実行するとラベルが作られる
3. `OPS_REPO_TOKEN` を akiya-atlas の Secret に登録する（上の表）
4. public にした直後に `pipeline` を `mode=deploy-only` で 1 本手動実行し、ジョブが起動することを
   確かめる（Actions の支払いが止まっていたため）
5. public にしたら Settings → Code security で Secret scanning と Push protection を有効にする
   （public なら無料）
