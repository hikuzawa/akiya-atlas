# ADR 0002: 物件レコードのスキーマと要約の方針

- ステータス: 採用（2026-09-10）

## 背景
写真・本文は転載しない。数値は原文からの機械抽出のみ。検索軸に必要な項目を過不足なく持つ。

## 決定
- `Listing`: record_id、municipality_code（JIS 6 桁）、listing_no、deal_type（sale/rent/both/unknown）、title、summary、address_text、price / rent_monthly / land_area_m2 / floor_area_m2 / built_year（いずれも sitemill の `FieldValue`）、structure、layout、status、detail_url、provenance、first_seen_at、last_seen_at、history
- record_id は `sha1(source_id + listing_no)` の先頭 16 桁。listing_no が無い場合は detail_url を使う
- 要約は 120 字以内。原文の 30 文字以上の逐語一致を含む場合は転載とみなし、定型文（「〇〇市の空き家バンク物件 No.X（売買）」）に置き換える
- 一覧ページと詳細ページの両方から抽出し、同じ record_id は項目ごとに「parsed を優先、詳細ページを優先」で統合する
- 一定期間（既定 30 日）見つからないレコードは削除せず `stale` として「掲載終了の可能性」を表示する

## 影響
- 抽出プロンプトは `src/akiya_atlas/prompts/listing_v1.md`。版を上げたら ADR に追記する

## 追記（2026-09-10）: 所在地は「市町村＋大字・地区名」まで
所在地は番地・号・建物名を保持しない（表示もデータも）。丁目・字（小字）は地名として残す。
取り込み時（`item_to_content`）と後処理（`finalize`）で sitemill の `parse.jp.address.strip_street_number` を適用し、
落とした文字列が要約・見出しに含まれていればそこからも消す。ビルド時（`pages.ensure_no_street_numbers`）に
番地が残っていればビルドを中止し、コミット済みレコードにも番地が無いことをテスト（`test_address_policy.py`）で検査する。
