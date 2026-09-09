# akiya-atlas（空き家アトラス）

全国の自治体が独自に運営する空き家バンクを横断検索できる日本語サイト。sitemill エンジンの最初の利用者。

- 検索軸: 都道府県 × 市町村 × 価格帯 × 移住・改修補助金の有無
- 物件の写真・本文は転載せず、要約と一次情報（自治体ページ）へのリンクにとどめる
- 設計判断は `docs/adr/`、規約は `CLAUDE.md`

## 使い方
```
uv sync
uv run sitemill run          # discover → crawl → extract → build
uv run sitemill build        # 生成だけ
uv run python -m http.server -d dist 8000
```
