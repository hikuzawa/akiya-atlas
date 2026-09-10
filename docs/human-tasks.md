# 人間側で必要な作業（2026-09-10 更新）

AI が全自動で回すための前提として、アカウント作成や鍵の登録など人にしかできない作業を並べる。
上から順に済ませると、`uv run sitemill run` → 日次の GitHub Actions → Cloudflare Pages 公開までがつながる。

## 今すぐ必要（縦断パイプラインを完走させるため）
1. ~~**Anthropic API キー**を `akiya-atlas/.env` に書く~~ → 済み（2026-09-10）。**ただし鍵の作り直しを推奨**: 一度 `.env.example` に書かれた値がローカルの git 履歴に入り（push 前に履歴から除去済み）、監査ログにも表示されたため。作り直したら `.env` を更新し、下の表のコマンドで GitHub Secret も更新する。
   これが無いと `sitemill extract` は「.env に何を書くか」を表示して止まる。抽出モデルは `site.toml` の `[llm] model`（既定 `claude-haiku-4-5`）で変えられる。
2. **運営者名と連絡手段**を決め、`site.toml` の `[operator]` に書く（現在は「準備中」）。全ページのフッターと `/about/` に出る。

## 公開までに必要
3. ~~**GitHub リポジトリ**を作成し push する~~ → 済み（2026-09-10）。sitemill は public（https://github.com/hikuzawa/sitemill）、akiya-atlas は private（https://github.com/hikuzawa/akiya-atlas）。sitemill が public のため、CI からの sitemill checkout に読み取り用 PAT は不要。
   両リポジトリの git 作者設定はローカルで済ませてある。
4. ~~**Cloudflare アカウント**と API トークン（Account > Cloudflare Pages: Edit）・アカウント ID を発行する~~ → 済み（2026-09-10、Secrets に登録済み）。Pages プロジェクト `akiya-atlas` は手で作らなくてよい（2026-09-10 に CI が作成済み。既定ホストは `akiya-atlas-asb.pages.dev`。`akiya-atlas.pages.dev` は第三者の英語サイト「Akiya Atlas」が使用中で取得できない）。`pipeline.yml` が deploy の直前に「無ければ作成、あれば何もしない」で用意する（`wrangler pages project create`）。
5. ~~**GitHub Secrets** を akiya-atlas に登録する~~ → 済み（2026-09-10）: `ANTHROPIC_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`。
6. **Cloudflare Web Analytics** を有効にしてビーコントークンを取得し、Secrets の `CF_WEB_ANALYTICS_TOKEN` と `.env` に入れる（無くても動く。計測タグが出ないだけ）。
7. **Google Maps Platform**: Maps Embed API を有効にし、HTTP リファラで `akiya-atlas-asb.pages.dev` と `akiya-atlas.com` に制限した公開キーを発行して `GOOGLE_MAPS_EMBED_KEY` に入れる（無い間は地図は外部リンクにフォールバック）。
8. **ドメイン `akiya-atlas.com`** を取得したら、Cloudflare Pages のプロジェクト `akiya-atlas` にカスタムドメイン `akiya-atlas.com` と `www.akiya-atlas.com` を追加する。その後の AI 側の作業（`site.toml` の `base_url` を `https://akiya-atlas.com` に差し替え、www → apex の 301 を `_redirects` に出す、再 deploy）は準備済みで、「ドメイン設定した」の合図で適用する。配置前の検査 `.github/scripts/check_public_urls.py` が canonical / sitemap / robots の古いホスト残りを止める。

## 収益化のために必要
9. **ASP アカウント**（不動産一括査定・解体一括見積・空き家買取の各案件）を契約し、計測 URL を `src/akiya_atlas/affiliates.py` の `Offer.url` に入れる。入れるまで CTA は「準備中」表示。
10. 各 ASP の掲載ルール（「広告」表記など）に合わせてテンプレートの文言を確認する。

## 任意・後で
11. Street View を使う場合は Geocoding API の有効化（所在地から緯度経度を得るため）。
12. 公式 SNS（YouTube / Instagram / X）の埋め込みを使う場合、Instagram と X は oEmbed の利用登録が要る。
13. Raspberry Pi で回す場合は uv を入れ、`akiya-atlas` を clone して `.env` を置き、cron で `uv run sitemill run` を実行する。

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
