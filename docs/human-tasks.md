# 人間側で必要な作業（2026-09-10 時点）

AI が全自動で回すための前提として、アカウント作成や鍵の登録など人にしかできない作業を並べる。
上から順に済ませると、`uv run sitemill run` → 日次の GitHub Actions → Cloudflare Pages 公開までがつながる。

## 今すぐ必要（縦断パイプラインを完走させるため）
1. **Anthropic API キー**を `akiya-atlas/.env` に書く（`.env.example` をコピーして `ANTHROPIC_API_KEY=...`）。
   これが無いと `sitemill extract` は「.env に何を書くか」を表示して止まる。抽出モデルは `site.toml` の `[llm] model`（既定 `claude-haiku-4-5`）で変えられる。
2. **運営者名と連絡手段**を決め、`site.toml` の `[operator]` に書く（現在は「準備中」）。全ページのフッターと `/about/` に出る。

## 公開までに必要
3. **GitHub リポジトリ**（private）を作成し push する: `hikuzawa/sitemill`、`hikuzawa/akiya-atlas`。
   両リポジトリの git 作者設定はローカルで済ませてある。
4. **Cloudflare アカウント**と **Pages プロジェクト `akiya-atlas`**（Direct Upload）を作る。API トークン（Pages 編集権限）とアカウント ID を発行する。
5. **GitHub Secrets** を akiya-atlas に登録する: `ANTHROPIC_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`。
   sitemill が private の場合、akiya-atlas のワークフローが sitemill を checkout できるよう `SITEMILL_READ_TOKEN`（sitemill 読み取り権限の fine-grained PAT）も登録する。
6. **Cloudflare Web Analytics** を有効にしてビーコントークンを取得し、Secrets の `CF_WEB_ANALYTICS_TOKEN` と `.env` に入れる（無くても動く。計測タグが出ないだけ）。
7. **Google Maps Platform**: Maps Embed API を有効にし、HTTP リファラで `akiya-atlas.pages.dev` と `akiya-atlas.com` に制限した公開キーを発行して `GOOGLE_MAPS_EMBED_KEY` に入れる（無い間は地図は外部リンクにフォールバック）。
8. **ドメイン `akiya-atlas.com`** を取得したら、Cloudflare Pages にカスタムドメインを設定し、`site.toml` の `base_url` を 1 行差し替える。

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
| `SITEMILL_READ_TOKEN` | GitHub → Settings → Developer settings → Personal access tokens → Fine-grained → Repository access を `hikuzawa/sitemill` のみ、Permissions は Contents: Read-only（有効期限を設定） | `gh secret set SITEMILL_READ_TOKEN --repo hikuzawa/akiya-atlas` |
| `GOOGLE_MAPS_EMBED_KEY`（任意） | Google Cloud Console → APIs & Services → Credentials。Maps Embed API のみ、HTTP リファラで制限 | `gh secret set GOOGLE_MAPS_EMBED_KEY --repo hikuzawa/akiya-atlas` |
| `CF_WEB_ANALYTICS_TOKEN`（任意） | Cloudflare → Web Analytics → サイト追加 → JS スニペット内の token | `gh secret set CF_WEB_ANALYTICS_TOKEN --repo hikuzawa/akiya-atlas` |

注意: `.env.example` には値を書かない（git にコミットされる）。値は `.env` だけに置く。
