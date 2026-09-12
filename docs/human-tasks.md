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
6. **Cloudflare Web Analytics** を有効にしてビーコントークンを取得し、Secrets の `CF_WEB_ANALYTICS_TOKEN` と `.env` に入れる（無くても動く。計測タグが出ないだけ）。
7. **Google Maps Platform**: Maps Embed API を有効にし、HTTP リファラで `akiya-atlas-asb.pages.dev` と `akiya-atlas.com` に制限した公開キーを発行して `GOOGLE_MAPS_EMBED_KEY` に入れる（無い間は地図は外部リンクにフォールバック）。
8. ~~**ドメイン `akiya-atlas.com`** を取得し、Pages にカスタムドメイン（apex と www）を追加する~~ → 済み（2026-09-10）。`site.toml` の `base_url` は `https://akiya-atlas.com` に切替済みで、CI が配置前に本番ドメインを固定検査する。
9. **www と pages.dev から apex への 301（Bulk Redirects、約 5 分）**。Pages の `_redirects` はドメイン単位のリダイレクトに非対応（公式の Advanced redirects 表で ❌。wrangler はエラーを出さずに受理するが効かない）なので、Cloudflare ダッシュボードのアカウントレベル「Bulk redirects」で行う。
   1. 「Create Bulk Redirect List」で 2 件を登録: Source `https://www.akiya-atlas.com` → Target `https://akiya-atlas.com`、Status 301。Source `https://akiya-atlas-asb.pages.dev` → Target `https://akiya-atlas.com`、Status 301。各項目の「Edit parameters」で「Subpath matching」「Preserve path suffix」「Preserve query string」を有効にする（「Include subdomains」は無効のまま。プレビュー `<hash>.akiya-atlas-asb.pages.dev` を巻き込まないため）。
   2. 「Create Bulk Redirect Rule」でそのリストを選び「Save and Deploy」。
   3. 確認: `curl -sI https://www.akiya-atlas.com/nagano/?x=1` が 301 で `location: https://akiya-atlas.com/nagano/?x=1`、`curl -sI https://akiya-atlas-asb.pages.dev/` が 301 で apex を指す。
   www だけならゾーンの Redirect Rules テンプレート「Redirect from WWW to Root」でもよいが、pages.dev は Bulk Redirects でしか扱えない。

## 収益化のために必要
10. **ASP アカウント**（不動産一括査定・解体一括見積・空き家買取の各案件）を契約し、計測 URL を `src/akiya_atlas/affiliates.py` の `Offer.url` に入れる。入れるまで CTA は「準備中」表示。
    - A8.net の解体案件（解体工事110番、プログラム `s00000015223012`）は 2026-09-12 に承認済み。`kaitai-110` として登録してあり、**計測 URL だけが未設定**。A8 の管理画面で発行して渡せば公開される
    - 案件の渡し方・登録手順・飛び先 URL の確認・掲載 URL の届け出は `docs/runbook/affiliates.md`
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
| `CF_WEB_ANALYTICS_TOKEN`（任意） | Cloudflare → Web Analytics → サイト追加 → JS スニペット内の token | `gh secret set CF_WEB_ANALYTICS_TOKEN --repo hikuzawa/akiya-atlas` |

注意: `.env.example` には値を書かない（git にコミットされる）。値は `.env` だけに置く。
