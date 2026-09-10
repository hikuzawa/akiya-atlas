# data/reference/ 出典

自治体巡回を都道府県単位で全市区町村へ広げるための、全国の団体コード基準表。

## 全国地方公共団体コード（都道府県コード及び市区町村コード）

| 項目 | 内容 |
| --- | --- |
| 提供元 | 総務省 |
| 出典元ページ | https://www.soumu.go.jp/denshijiti/code.html |
| ダウンロードURL | https://www.soumu.go.jp/main_content/000925835.xlsx |
| 取得日 (download date) | 2026-09-10 |
| データ基準日 | 令和6年1月1日（2024-01-01）現在の団体 |
| ライセンス | 政府標準利用規約（第2.0版） |
| 原ファイル名 | 000925835.xlsx |
| 原ファイル形式 | Microsoft Excel (.xlsx / OOXML)。内部XMLはUTF-8 |

### ライセンス
- 総務省ウェブサイト利用規約: https://www.soumu.go.jp/menu_kyotsuu/policy/tyosaku.html
- 総務省サイトのコンテンツは「政府標準利用規約（第2.0版）」に準拠。出典を明示すれば、複製・公衆送信・翻訳・変形・翻案等が可能（CC BY 4.0 と互換）。
- 出典表示例: 「出典: 総務省『都道府県コード及び市区町村コード』（令和6年1月1日）」

### 取得方法
- `curl`（User-Agent: `sitemill-recon/0.0 (+https://akiya-atlas.pages.dev/about/)`、タイムアウト30秒、リクエスト前に3秒の待機）。
- 生ファイルは無加工で `data/reference/000925835.xlsx` に保存（拡張子・ファイル名を維持）。
- 注記: 出典元ページはCSVを提供していない（PDFとExcelのみ）。そのため原本はxlsxを採用した。

### 原ファイル（000925835.xlsx）の構成
- シート1「R6.1.1現在の団体」= 都道府県＋市区町村（本表はこのシートを使用）
- シート2「R6.1.1政令指定都市」= 政令指定都市の行政区（本表では未使用）
- シート1の列レイアウト:
  - A = 団体コード（6桁・検査数字含む、テキスト格納で先頭ゼロ保持）
  - B = 都道府県名（漢字）
  - C = 市区町村名（漢字）※都道府県行は空
  - D = 都道府県名（カナ・半角カタカナ）
  - E = 市区町村名（カナ・半角カタカナ）※都道府県行は空
- ヘッダーセルおよび一部の団体名セルには、ふりがな（phonetic run = `rPh`）が埋め込まれている。抽出時に `rPh` を除外し、表示テキストのみを取得している（除外しないと「滝沢市シ」「色丹村シコタンムラ」等の混入が発生する）。

## 正規化CSV: municipal_codes.csv

- 文字コード: UTF-8（BOMなし）／ 改行: LF
- ヘッダー列（順序厳守）: `code,prefecture,prefecture_kana,municipality,municipality_kana`
  - `code` = 6桁の全国地方公共団体コード（検査数字を含む）
  - `prefecture` / `prefecture_kana` = 都道府県名（漢字 / 半角カナ）
  - `municipality` / `municipality_kana` = 市区町村名（漢字 / 半角カナ）
  - 都道府県レベルの行は `municipality` と `municipality_kana` を空欄にする
- 原本xlsxの列順（code, 都道府県漢字, 市区町村漢字, 都道府県カナ, 市区町村カナ）から、上記のヘッダー順へ並べ替えている。
- 生成方法: Python標準ライブラリ（`zipfile` + `xml.etree.ElementTree`）のみでOOXMLを直接パース。openpyxl等の依存パッケージは追加していない。

### 件数（municipal_codes.csv）
- 総行数（ヘッダー除く）: 1794
- 都道府県: 47
- 市区町村: 1747（内訳: 市 792 / 町 743 / 村 189 / 特別区 23）
  - 村189には北方領土の6村（色丹村・泊村・留夜別村・留別村・紗那村・蘂取村）を含む。これらを除いた実行政ベースの市区町村数は1741。

## {slug}_official_urls.json — 県の公的な市町村リンク集からの公式URL表

各県の公式サイトにある市町村リンク集を 1 ページだけ取得し、コード表の市町村名と突き合わせた表。
候補ドメインの推測（`city.<名>.<県>.jp` 等）で解決できない自治体の公式URLを解決するために使う
（ADR 0007 条件A / ADR 0008）。突き合わせの規則は `src/akiya_atlas/reference.py` を参照。

| 県 | 出典ページ | URL | 対応 |
| --- | --- | --- | --- |
| 北海道 | 北海道 道内179市町村（50音別） | https://www.pref.hokkaido.lg.jp/link/shichoson/aiueo.html | 178/185 |
| 青森県 | 青森県 市町村ホームページ | https://www.pref.aomori.lg.jp/soshiki/zaimu/shichoson/shichoson.html | 40/40 |
| 岩手県 | 岩手県 県内市町村へのリンク | https://www.pref.iwate.jp/kensei/toukei/toukei/1012206.html | 33/33 |
| 宮城県 | 宮城県 県内市町村リンク集 | https://www.pref.miyagi.jp/soshiki/kohou/link01.html | 35/35 |
| 秋田県 | 秋田県 県内市町村のウェブサイト | https://www.pref.akita.lg.jp/pages/archive/46079 | 25/25 |
| 山形県 | 山形県 山形県内市町村ページ | https://www.pref.yamagata.jp/020026/kensei/information/clink.html | 35/35 |
| 福島県 | 福島県 市役所・町村役場一覧 | https://www.pref.fukushima.lg.jp/sec/01145a/yakuba.html | 59/59 |
| 茨城県 | 茨城県 県内の市町村 | https://www.pref.ibaraki.jp/towns/index.html | 44/44 |
| 栃木県 | 栃木県 リンク集（市町一覧） | https://www.pref.tochigi.lg.jp/c05/intro/tochigi/link/lkshityouson.html | 25/25 |
| 群馬県 | 群馬県 県内市町村等ホームページ外部リンク集 | https://www.pref.gunma.jp/page/14523.html | 35/35 |
| 埼玉県 | 埼玉県 関係機関へのリンク（市町村ホームページ一覧） | https://www.pref.saitama.lg.jp/a0301/wwwlink.html | 63/63 |
| 千葉県 | 千葉県 市町村一覧 | https://www.pref.chiba.lg.jp/kouhou/ichiran.html | 54/54 |
| 東京都 | 東京都 リンク集／都内区市町村 | https://www.metro.tokyo.lg.jp/sitemap/link/link04 | 62/62 |
| 神奈川県 | 神奈川県内の市町村 | https://www.pref.kanagawa.jp/docs/ie2/cnt/f530001/p780102.html | 33/33 |
| 新潟県 | 新潟県 リンク集：県内市町村 | https://www.pref.niigata.lg.jp/site/link/link-sichoson.html | 30/30 |
| 富山県 | 富山県 県内の市町村情報 | https://www.pref.toyama.jp/1021/kensei/kenseiunei/kensei/gaiyou/profile/city.html | 15/15 |
| 石川県 | 石川県内市町のページ | https://www.pref.ishikawa.lg.jp/shimachi.html | 19/19 |
| 福井県 | 福井県内市町リンク集 | https://www.pref.fukui.lg.jp/doc/dx-suishin/shimachi_list.html | 17/17 |
| 山梨県 | 山梨県市町村リンクページ | https://www.pref.yamanashi.jp/link/link_city.html | 27/27 |
| 長野県 | 長野県 市町村一覧（役場所在地・電話番号等） | https://www.pref.nagano.lg.jp/shichoson/kensei/shichoson/gappei/gappei/mejiiko/ichiran/index.html | 77/77 |
| 岐阜県 | 岐阜県の市町村一覧 | https://www.pref.gifu.lg.jp/page/6058.html | 42/42 |
| 静岡県 | 静岡県 県内市町リンク集 | https://www.pref.shizuoka.jp/kensei/link/1007806.html | 35/35 |
| 愛知県 | 愛知県 県内の市町村（リンク集） | https://www.pref.aichi.jp/site/userguide/link-citytown.html | 54/54 |
| 三重県 | 三重県 県内市町（公式サイト） | https://www.pref.mie.lg.jp/link/link1.htm | 29/29 |
| 滋賀県 | 滋賀県 県内の市町一覧 | https://www.pref.shiga.lg.jp/ab00/7739.html | 19/19 |
| 京都府 | 京都府 府内市町村・官公庁・都道府県等 | https://www.pref.kyoto.jp/link.html | 26/26 |
| 大阪府 | 大阪府内の市町村 | https://www.pref.osaka.lg.jp/o070050/koho/links/city.html | 43/43 |
| 兵庫県 | 兵庫県 リンク集（県内市町案内） | https://web.pref.hyogo.lg.jp/link/pref.html | 41/41 |
| 奈良県 | 奈良県 県内市町村 | https://www.pref.nara.lg.jp/n002/1232.html | 39/39 |
| 和歌山県 | 和歌山県 市町村のホームページ（リンク集） | https://www.pref.wakayama.lg.jp/link/shichoson.html | 30/30 |
| 鳥取県 | 鳥取県 県内の市町村（とりネット） | https://www.pref.tottori.lg.jp/9577.htm | 19/19 |
| 島根県 | 島根県リンク（市町村） | https://www.pref.shimane.lg.jp/link01.html | 19/19 |
| 岡山県 | 岡山県 市町村 | https://www.pref.okayama.jp/page/1000.html | 27/27 |
| 広島県 | 広島県 県内の市・町 | https://www.pref.hiroshima.lg.jp/soshiki/19/1168589482341.html | 23/23 |
| 山口県 | 山口県 市町（関連リンク） | https://www.pref.yamaguchi.lg.jp/soshiki/21/26969.html | 19/19 |
| 徳島県 | 徳島県の市町村一覧 | https://www.pref.tokushima.lg.jp/kenseijoho/kanrennochiiki/shichouson/ | 24/24 |
| 香川県 | 香川県 香川県内市町リンク | https://www.pref.kagawa.lg.jp/kocho/shokai/profile/w3cz95150312151105.html | 17/17 |
| 愛媛県 | 愛媛県 県内市町情報 | https://www.pref.ehime.jp/page/14990.html | 20/20 |
| 高知県 | 高知県 県内市町村 | https://www.pref.kochi.lg.jp/link/kennai_shichoson.html | 34/34 |
| 福岡県 | 福岡県内市町村へのリンク集 | https://www.pref.fukuoka.lg.jp/contents/shichoson01.html | 60/60 |
| 佐賀県 | 佐賀県 県内市町リンク | https://www.pref.saga.lg.jp/kiji0034791/index.html | 20/20 |
| 長崎県 | 長崎県 県内市町 | https://www.pref.nagasaki.jp/pages/page-31.html | 21/21 |
| 熊本県 | 熊本県 県内市町村のホームページ（リンク集） | https://www.pref.kumamoto.jp/soshiki/1/177925.html | 45/45 |
| 大分県 | 大分県 県内市町村（リンク集） | https://www.pref.oita.jp/soshiki/75008/kennai-sityoson.html | 18/18 |
| 宮崎県 | 宮崎県 県内市町村一覧 | https://www.pref.miyazaki.lg.jp/kohosenryaku/kense/shichoson/shichosonmap.html | 26/26 |
| 鹿児島県 | 鹿児島県 県内市町村 | https://www.pref.kagoshima.jp/aa02/link/shichoson.html | 43/43 |
| 沖縄県 | 沖縄県 リンク集（市町村ホームページ） | https://www.pref.okinawa.lg.jp/about/1018107.html | 41/41 |

- 取得日は各 JSON の `checked_on`。ライセンス上の扱いは「事実（自治体名と公式URLの対応）」の参照で、
  ページ本文は保存しない。生成物には出典 URL を残す。
- 北海道の 7 件だけが未対応。内訳は北方領土の 5 村（色丹村・留夜別村・留別村・紗那村・蘂取村。
  ウェブサイトが無い）と、同名が 2 つある泊村（古宇郡・国後郡）。同名は取り違えを避けて対応づけない。
- 残りの 46 県は全市区町村が対応済み（東京都は特別区 23 を含む）。
