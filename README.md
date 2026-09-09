# akiya-atlas（空き家アトラス）

自治体が独自に運営する空き家バンクを横断検索できる日本語サイト。共通エンジン sitemill の最初の利用者。

- 検索軸: 都道府県 × 市町村 × 価格帯 × 移住・改修補助金の有無
- 物件の写真・本文は転載せず、要約と一次情報（自治体ページ）へのリンクにとどめる
- 数値は原文からの機械抽出のみ（LLM は引用を返し、パーサが値にする）
- 設計判断は `docs/adr/`、規約は `CLAUDE.md`

## 使い方

```
cp .env.example .env        # ANTHROPIC_API_KEY などを手で書く
uv sync
uv run sitemill crawl       # robots と間隔を守って巡回し、変化したページに印を付ける
uv run sitemill extract     # 変化したページだけを LLM で構造化（ANTHROPIC_API_KEY が要る）
uv run sitemill build       # dist/ に静的サイトを生成
uv run sitemill run         # 上の 3 つをまとめて実行
uv run sitemill eval        # 保存済み fixture で抽出精度を計測（外部アクセスなし）
uv run sitemill status      # 巡回状態とレコード数
uv run sitemill deploy --dry-run
uv run python -m http.server -d dist 8000   # ローカルで表示確認
```

対象の自治体と巡回設定は `data/sources/nagano.yaml`。追加するときは運営主体の根拠（引用と URL）を必ず書く。
