# akiya-atlas（空き家アトラス）

自治体が独自に運営する空き家バンクを横断検索できる日本語サイト。共通エンジン
[sitemill](https://github.com/hikuzawa/sitemill) の最初の利用者。

公開中のサイト: <https://akiya-atlas.com>

- 検索軸: 都道府県 × 市町村 × 価格帯 × 移住・改修補助金の有無
- 物件の写真・本文は転載せず、要約と一次情報（自治体ページ）へのリンクにとどめる
- 数値は原文からの機械抽出のみ（LLM は引用を返し、パーサが値にする）
- 設計判断は `docs/adr/`、規約は `CLAUDE.md`

## このリポジトリは何か

本番で動いているサイトの全部（巡回設定・抽出仕様・テンプレート・取得済みデータ・
CI）です。空き家バンクは自治体ごとに別々に運営されていて、様式も更新の仕方も
ばらばらです。それを毎日巡回して 1 つの索引にまとめる作り方を、動く形のまま
残しています。

同じ作り方を他の分野に流用したい場合は、汎用のエンジン部分が
[sitemill](https://github.com/hikuzawa/sitemill) に分けてあります。

## 巡回の方針

- **robots.txt を尊重します。** `Disallow` のパスは取りません。同じサイトへの
  アクセスは数秒以上の間隔を空け、変化のないページは再取得しません
- User-Agent は問い合わせ先を含む形で名乗ります（サイトの
  [このサイトについて](https://akiya-atlas.com/about/) に現物を掲載）
- 巡回するのは、自治体または自治体の移住推進組織が運営主体と明記されたサイトだけ。
  民間プラットフォームはリンクのみ。運営主体の根拠（引用と URL）を
  `data/sources/*.yaml` に残します
- **巡回を止めてほしい場合**は
  [お問い合わせフォーム](https://akiya-atlas.com/about/) からご連絡ください。
  掲載の取り下げも同じ窓口で受け付け、依頼のあったページは次回のビルドで非表示にします

## データの出典とライセンス

| 対象 | 出典 | 扱い |
| --- | --- | --- |
| `src/` `templates/` `static/` `tools/` `.github/` `docs/` `tests/`（fixtures を除く） | このリポジトリ | MIT（`LICENSE`） |
| `data/records/` `data/subsidies/` `data/sources/` `data/review/` | 各自治体の公開ページ | 事実の記録。原文の権利は各自治体・運営者に帰属。各レコードに出典 URL と取得日 |
| `data/reference/`（団体コード表） | 総務省「都道府県コード及び市区町村コード」（令和6年1月1日） | 政府標準利用規約（第2.0版）。`data/reference/SOURCES.md` |
| `tests/fixtures/eval/` | 抽出結果の期待値（引用と値のみ） | MIT |

コードを再利用するときは MIT の条件で自由にどうぞ。**データをそのまま再配布する
ことは想定していません。** 各自治体のページが一次情報であり、本サイトの値は
そこからの機械抽出です。

## 免責

掲載内容の正確性には努めていますが、自治体ページの更新や抽出の誤りにより実際と
異なる場合があります。物件の申込み・内覧・契約は、必ず各自治体および関係者に直接
ご確認ください。**本サイトは物件の仲介・紹介を行いません。** 本リポジトリのコードと
データは現状のまま提供され、利用によって生じた損害について作者は責任を負いません。

所有者向けのページ（`/owners/`）にはアフィリエイトリンクを掲載しており、ページの
冒頭とリンクの横に広告である旨を明記しています。掲載している自治体の情報の選び方や
並び順には影響しません（`docs/adr/0010-affiliate-placements-and-compliance.md`）。

## 使い方

clone 後、まずコミット前フック（秘密の混入を止める）を有効化する:

```
git config core.hooksPath .githooks
```

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

対象の自治体と巡回設定は `data/sources/*.yaml`。追加するときは運営主体の根拠
（引用と URL）を必ず書く。

**鍵は `.env` にだけ置く。** リポジトリには入れない。`.env.example` は空の
プレースホルダーだけを置き、値を書いた時点でコミット前フックが止める。CI でも
全履歴を走査している（`.github/workflows/checks.yml`）。
