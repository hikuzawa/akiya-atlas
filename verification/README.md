# サイト検証ファイルの置き場

このディレクトリに置いたファイルは、ビルド時に `dist/` の直下へそのまま複写される。
Google Search Console や Bing などの「HTML ファイルによるサイト所有権の確認」に使う。

## 使い方
1. Search Console で「HTML ファイル」方式を選び、`googudxxxxxxxx.html` をダウンロードする。
2. そのファイルをこのディレクトリに置く（例: `verification/googudxxxxxxxx.html`）。
3. `uv run sitemill build` すると `dist/googudxxxxxxxx.html` として出力され、`/googudxxxxxxxx.html` で配信される。

## メタタグ方式（併用可）
`.env` に `GOOGLE_SITE_VERIFICATION=<トークン>` を書くと、全ページの `<head>` に
`<meta name="google-site-verification" ...>` を出力する（未設定なら出力しない）。

検証ファイルはサイトごとに固有なので、実ファイルはコミットしても害はないが、
不要になったら削除してよい。この README 以外のファイルは今は置いていない。
