# 04. sitemill / pages.py 側への提案（このセッションではコードを触らない）

テンプレートと静的アセットだけで実現できないものを、影響の小さい順に挙げる。いずれも本線セッションが取り込むかを判断する。
テンプレートは「変数があれば使い、無ければ代替表示」の書き方にして、提案の採否に依存しないようにする。

## A. `pages.py` の context 追加（akiya-atlas 側、小さい）
1. **物件行に数値を足す**（`listing_row` と `search_index`）: `price_yen: int | None`、`rent_yen: int | None`、`floor_area_m2: float | None`、
   `land_area_m2`、`layout`、`structure`、`has_detail: bool`（`detail_url` があるか）。
   目的: 価格の中央値・最安の計算、「詳細あり」バッジ、カードの面積・間取り表示。今は `price` が文字列（"350万円"）で計算できない。
2. **集計値を渡す**: 都道府県・市町村・トップに `stats` として `price_median`、`price_min`、`built_min`、`built_max`、`built_median`、
   `crawled_municipalities`（公開中の市町村数）、`subsidy_municipalities`（補助金ありの市町村数）、`band_counts`（価格帯ごとの件数）。
   都道府県ページには `chart_built`（築年の分布）も。
3. **都道府県行に `crawled` と `subsidy_count` を足す**（トップの都道府県カードのミニバー用）。
4. **`recent` 行に補助金フラグ**（トップの「最近確認した物件」カードのバッジ用）。

## B. sitemill.charts（汎用。他サービスでも使う）
1. **`chart_css` の読み込み順**: サービス側 CSS で上書きしやすいよう、`<style>{{ chart_css }}</style>` を先、`style.css` を後に置く
   （テンプレート側で対応可能。sitemill 側では `_LIGHT_TOKENS` を `:root` レベルのトークン参照にすると更に扱いやすい）。
2. **`flow_diagram` の可読性**: 箱 130×64px・文字 11px 相当は小さい。`box_w`/`box_h`/フォントサイズを引数にし、スマホでは
   縦 1 列（`per_row=1`）に切り替えられるようにする。番号は箱の外の丸バッジに。
3. **`choropleth(svg_text, values, classes)` の追加**: 事前に用意した SVG（`id` = 地域コード）に、値に応じたクラスと `<title>`、リンクを
   付けて返す汎用関数。今回はテンプレート（Jinja）で行うが、他サービス（都道府県 × 市町村の集計を持つサイト全般）でも使える。
4. **`mini_bars(bins)`（スパークライン）**: 数字の帯用の小さな分布。今回は Jinja マクロで実装する。

## C. データ・ライセンス
1. 地図データ（国土数値情報 N03、CC BY 4.0）の出典表記を `/data/` の「埋め込み・図解の利用元」に追加する文言をテンプレート側で入れる。
   もし sitemill にライセンス台帳（データ源ごとの判定と出典）を持たせるなら、そこに登録する。
2. sitemill のホワイトリストに **公共データ利用規約（第 1.0 版、PDL1.0）** を追加する提案。国土数値情報のサイト全体の規約が 2026-03-23 から
   PDL1.0 に移行しており、個別データが CC BY 4.0 表記でないものも出てくる。PDL1.0 は政府標準利用規約 2.0 の後継で CC BY 4.0 互換。

## D. 将来（写真）
- `Listing` に `photos: list[{url, license, credit, checked_on}]` を持たせ、ライセンス判定がホワイトリストに一致したものだけ出力する。
  テンプレートには `photo_slot` の置き場所を先に用意しておく（データが無い間は出力しない）。
