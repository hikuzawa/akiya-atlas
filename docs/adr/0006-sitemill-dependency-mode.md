# ADR 0006: sitemill への依存は今フェーズだけ editable なパス依存にする

- ステータス: 採用（2026-09-10）

## 背景
sitemill と akiya-atlas を同時に立ち上げるため、エンジンの変更を即座に反映したい。ただし長期的にはエンジンの版を固定しないと、エンジン側の変更でサイト生成が予告なく変わる。

## 決定
- 今フェーズ: `pyproject.toml` の `[tool.uv.sources]` で `../sitemill` を editable 指定。CI でも両リポジトリを隣に checkout する
- エンジンが安定したら（目安: 3 サービスめが載る前、または初回本番デプロイ後）、`sitemill = { git = "...", tag = "vX.Y.Z" }` のタグ固定に切り替え、更新は明示的な PR で行う
- 切り替え時は ADR を追記し、CI の checkout 手順も同時に直す

## 影響
- エンジンの破壊的変更は、切り替え後は akiya-atlas 側で版上げの判断が要る
