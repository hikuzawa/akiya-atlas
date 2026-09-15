# tests/fixtures/

- `eval/` — 抽出精度の計測ケース（期待値と、記録しておいた LLM の応答）。**このリポジトリに入っている**
- `html/` — 自治体の公開ページを保存したもの。**このリポジトリには入っていない**（git 管理外）

## なぜ html/ が無いのか

保存した HTML の著作権は各自治体・運営者にあり、`SOURCES.md` に「再配布しない」と書いて
取得しました。このリポジトリは public なので、置いたままにするとその約束を破ることになります。
非公開の [akiya-atlas-ops](https://github.com/hikuzawa/akiya-atlas-ops) の `fixtures/html/` に
移してあります（出典の一覧もそちら）。

## 手元で使う

```
git clone https://github.com/hikuzawa/akiya-atlas-ops.git ../akiya-atlas-ops
cp -r ../akiya-atlas-ops/fixtures/html tests/fixtures/html
```

無くても `uv run pytest` は通ります。保存済み HTML を使う試験は skip し、`sitemill eval` も
ケースを飛ばします。**巡回・抽出・ビルドには要りません。**

CI は Secret `OPS_REPO_TOKEN`（ops リポジトリの contents:read を持つ PAT）で checkout します。
Secret の無い実行（fork からの PR など）では同じく skip されます。
