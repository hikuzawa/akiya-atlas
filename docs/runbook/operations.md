# 運用手順書（日々の確認と手作業）

全国 47 都道府県の巡回が始まったあとの運用をまとめる。読む人は次の 2 通りを想定している。

- **新しく開いたセッションの AI**: 何を見れば現状が分かるか、何を勝手に変えてよくないかを知るため
- **運営者（人間）**: 週に 1 度の確認と、人にしかできない作業を進めるため

前提の設計は ADR に置いてある。巡回の礼儀は sitemill ADR 0003 と 0015、公開前の検査は 0012、
発見と自己修復は akiya-atlas ADR 0007 と 0008、重複の扱いは 0009。

## 1. 毎週の確認

### 1-1. どこを見るか

毎週月曜 06:17 JST に `weekly` ワークフローが動き、**直近 7 日のまとめを Issue に投稿する**。
Issue のタイトルは「週次まとめ YYYY-MM-DD」。ふだんはこれだけ見ればよい。

手元で同じものを出すこともできる（相手サイトには一切アクセスしない）。

```bash
uv run akiya-atlas weekly-report --days 7
```

既定は CI（日次パイプライン）の実行だけを数える。手元で流したバックフィルなどを含めたいときは
`--source all`、手元の作業だけなら `--source local`。

週次 Issue の後ろには**検索の状況**が付く（sitemill ADR 0023）。日次が取り込んだ Search Console の
記録を読むだけなので、鍵が無くても出る。まず見るのは「検査した N ページのうち M ページが登録済み」の
行で、公開直後は 0 が続くのが普通。全ページを一度に検査しているわけではない（1 日 200 件ずつ回る）ので、
**検査していない分を「登録されていない」と読まない**。数字が貯まるまで目安は置かない。

週次 Issue には、ほかに 2 つの節が付く。

- **今週 heal が選び直した自治体**: 掲載ページを差し替えた・取り下げた自治体を、旧 URL・新 URL・理由つきで
  並べる。自動で直した内容を後から追うための記録で、元は `data/runs/heal-reselections.jsonl`
- **GitHub Actions の実行時間**: 直近 7 日の合計と、1 か月に直した見込み、無料枠 2,000 分に対する割合。
  ワークフロー別の内訳も出る
- **robots.txt で巡回できなかったホスト**: 数と延べ回数。読めない・拒否された回は巡回しない（正しい判断）
  ぶん、**続くとその自治体だけ静かに更新が止まる**。1 晩だけの失敗は数だけ出し、
  **最後に取得できた日から 3 日以上**たったホストは名前と日数を別行で出す。
  止まっているかどうかは実行レポートでは決められない（巡回間隔の適応で「今夜は対象外」と
  失敗が混ざる）ので、`data/state/crawl.json` に残っている失敗と最終取得日で判断している。
  名前が出たら、そのホストに当たり直して相手側か自分側かを見る（2026-09-23 は 8 ホストが
  1 晩でまとめて失敗したが、当たり直すと全部 200 で、こちら側の取りこぼしだった）。
  エンジンが書く理由は 3 通りあり、名前の横に打ち手を添える: 取得できない・異常な応答
  （`robots.txt 202` など）は「相手に当たり直す」、拒否は「巡回先の URL を見直す」。
  最初は「取得できない」だけを拾っていて、202 を返し続けて 10 日止まっていた茨城県河内町と、
  拒否された検索ページを巡回先にしていた北海道当麻町を見逃した（2026-09-26 に直した）
- **広告のクリック**: 転送ページ（`/go/<案件>/<枠>/`）が開かれた回数を、案件と枠ごとに出す
  （`tools/report_clicks.py`）。Cloudflare Web Analytics を**ホスト名**で絞って読む。
  0 回のときは、同じ期間のサイト全体の表示数を添えて「押されていない」のか「計測が届いて
  いない」のかが分かるようにしている。読み取りには `CLOUDFLARE_API_TOKEN` に
  **Account Analytics: Read** が要る（配置用の権限だけでは 403）
- **`/owners/` への到達率**: `/owners/` 配下の表示 ÷ サイト全体の表示。広告の枠はここにしか
  無いので、クリックが 0 のときに「届いていない」のか「届いても押されない」のかを分ける。
  **2026-09-23 の初回は 1.3%**（6 / 461 表示。うち県別 1）。訪問の大半は市町村・物件ページで
  止まっている。理由は 2 つ考えられ、まだ決められない
  - 市町村ページの「所有者の方へ」の導線が弱い
  - そもそも所有者が来ていない（移住を考えている人が多い）
  数週間の推移と、週次の検索の状況に出る検索語を見てから判断する（先に導線をいじらない）

巡回の実行レポート（`data/runs/*-crawl.json` の `notes`）には、**キャッシュが巡回状態より古くて
取り直した URL** が出る（sitemill v0.7.3 から。多い日は頭の 10 件）。`crawl.stale_cache` が
0 でない日に、どのページで起きたかを見る。自己修復が動いた記録なので、失敗ではない。

### 1-2. 正常値の目安

| 見るところ | 目安 | 外れたときの意味 |
| --- | --- | --- |
| 1 日の実行時間 | 35〜45 分（うち Search Console の URL 検査が 25 分前後） | 検査を除いて 30 分を超えるなら、どこかの source でページ数が増えたか、相手サイトが遅い |
| 取得したサイト数 | 268 から、巡回間隔の適応が効くと 120〜160 へ下がる | 増え続けるなら適応が効いていない（`adaptive_interval` の設定を確認） |
| 変化したサイト数 | 1 日 4〜46 | 0 が数日続くなら、取得はできているのに差分検知が壊れている疑い |
| 1 日の費用 | $0.2〜1.0（変化したページ数に比例） | $3 を超える日が続くなら、毎日作り直されるページ（日付入りなど）を掴んでいる |
| 月の費用 | $10〜30 | |
| heal の点検数 | 0 件の source の数（現在 9 件前後） | 増え続けるなら、抽出が効かない型が増えている |
| heal の取り下げ | 0〜数件 | 一度に 10 件以上下がるなら、分類の判断が変わった疑い。差分を見る |
| 生成ページ数 | 6,000 前後 | 急に減ったら、データの取りこぼしかビルドの失敗 |
| lastmod が動いたページ数 | 1 日 20〜300（`build.lastmod_changed`） | 数千なら `<main>` に日付・時刻の表示が増えた（3-6）。数日 0 なら差分検知かビルドが壊れている |

実行時間の内訳は、巡回 3〜4 分、抽出 2 分前後、自己修復は 0 件の source 1 件につき約 34 秒（いまは 9 件で 5 分）、
ビルド 30 秒、eval 30 秒、Actions の準備 1〜2 分、Search Console の取り込み 25 分前後。
取り込みの大半は URL 検査で、1 件 7 秒前後 × 200 件。5,896 ページを一巡するのに 30 日かかる。
短くしたいときは `sitemill search fetch --inspect <件数>` を減らす（0 で検査を止める）。
30 分で打ち切る設定にしてあり、打ち切られた日の検査は翌日に回る（日次全体は止まらない）。

**Actions の使用量は週次 Issue の数字で見る。** 1 か月に直して無料枠 2,000 分の 50% を超えたら、
まず URL 検査の件数を減らす（下の目安）。それでも足りなければ、手動実行の回数を見直す。

#### URL 検査の件数を減らす目安

いまは 1 日 200 件で、5,896 ページを一巡するのに 30 日かかる。次の 2 つがそろったら 50 件ほどに減らす。

1. 週次の検索の状況に**表示回数が出はじめている**（インデックスが進み、検索に載り始めた）
2. **一巡が終わっている**（「検査した N ページ」がサイトの URL 数に届いた）

そのあとは、新しく増えたページと状態が変わったページを拾えれば足りる。減らすと日次が 20 分ほど短くなり、
Actions の月間が 600 分ほど下がる。変えるのは `.github/workflows/pipeline.yml` の取り込みの段で、
`uv run sitemill search fetch --inspect 50` にする。

### 1-3. 異常のときにまず見る場所

1. **失敗 Issue**（ラベル `pipeline-failure`）。日次が失敗すると自動で作られ、以後は同じ Issue に追記される
2. **Actions の該当 run のログ**。`gh run list --workflow pipeline --limit 5` で一覧、`gh run view <id> --log-failed`
3. **`data/runs/latest-*.json`**。工程ごとの件数・所要時間・LLM 使用量・エラー文が入っている
4. **`data/runs/weekly-*.json`**。週次まとめの記録。前の週と比べる

巡回のエラーは 1 件 1 行で `errors` に残る。`robots.txt を取得できないため今回は巡回しない` は
候補ドメインの推測が外れただけで、異常ではない。

## 2. Issue のラベル別の扱い

| ラベル | 誰が作るか | 扱い |
| --- | --- | --- |
| `pipeline-failure` | 日次パイプラインが失敗したとき自動 | 原因を直し、復旧を確認したら閉じる。同じ Issue に追記されるので、閉じ忘れると次の失敗が見えにくい |
| `takedown` | お問い合わせフォームの自動返信（掲載の削除・訂正依頼） | **自動で次回ビルドから非表示になる**（本文 1 行目の JSON にある URL）。対応が済んで掲載を戻してよくなったら Issue を閉じる。次回のビルドで戻る |
| `needs-human` | 自動返信が判断に迷ったとき | 人が読んで判断する。AI は**読むだけで何もしない** |
| `municipality` | 自治体・移住推進組織からの連絡 | 人が対応する。AI は**読むだけで何もしない**。掲載方針の変更依頼なら、内容に応じて `data/sources/` を直す判断が要る |

非表示になっているページは `data/reference/takedowns.json` で確認できる。手元で取り込み直すには
`uv run akiya-atlas takedowns`。

## 3. 手動でできること

### 3-1. データを触らずに配置だけやり直す

テンプレートや CSS を変えたとき、巡回せずに build → 検査 → 配置だけを行う。

```bash
gh workflow run pipeline -f mode=deploy-only -f deploy=true
```

### 3-2. 特定の自治体を選び直す

掲載ページが変わった、間違ったページを掴んでいる、といったときに使う。市町村コードは 6 桁。

```bash
uv run akiya-atlas rediscover 新潟県 --code 152021
```

選び直した結果は `data/sources/<県>-auto.yaml` と `data/runs/discover-<県>-findings.json` に入る。
そのあと `uv run sitemill crawl` と `uv run sitemill extract` を回すと反映される。

0 件が続く自治体は、毎日の `heal` が自動で選び直す。手で `rediscover` するのは、heal が保留にした
ものを人が判断したときだけでよい。

### 3-3. 物件一覧を巡回しないと決めた自治体

`data/reference/municipal_overrides.json` で `link_only` にした自治体は、**物件一覧を**巡回せず、
物件については公式ページへのリンクだけを出す。いまは 5 自治体。

| 理由 | 自治体 |
|---|---|
| 物件一覧を PDF でしか出していない | 山口県柳井市、兵庫県小野市 |
| 検索フォームを送らないと物件が出ない | 福岡県中間市、大分県国東市、石川県穴水町 |

PDF の解析には依存の追加が要るうえ、画像が混じるため数値を確実には読めない。推測で数値を載せない
方針（ADR 0004）に合わせて、案内に徹する。検索フォームは自動で送らない。

**補助制度のページはこの判定と別に探して巡回する**（ADR 0013）。見つかった自治体（小野市・中間市）は
sources の `policy` が `crawl` になるが、`pages` は `kind: subsidy` だけで、物件一覧は入らない。
`policy` だけを見て「物件を巡回している」と読まないこと。物件一覧が取れるかは `bank_status` を見る。

同じ扱いにしたい自治体が出たら `data/reference/municipal_overrides.json` に根拠つきで足し、
`uv run akiya-atlas rediscover <県> --code <6 桁>` を回す。

### 3-4. 新しい県の追加は要らない

47 都道府県 1,740 市町村はすべて登録済み（北方領土の 5 村を除く）。市町村合併があったときだけ
`data/reference/municipal_codes.csv` を更新して `uv run akiya-atlas expand <県>` を回す。

### 3-5. 触らない方がよいもの

- `data/state/crawl.json`: 巡回の状態。手で消すと全ページを取り直すことになる（相手サイトに負荷）
- `data/records/*.jsonl`: 物件。消すと `first_seen_at` の履歴が失われる
- `data/reference/municipal_overrides.json`: 自動では決められない自治体の確定情報。根拠つきでのみ足す
- `data/sources/<県>-auto.yaml` と `data/review/<県>.yaml`: **heal が毎晩書き直すことがある**。
  手で直しても次の heal で上書きされる。固定したい判断は `data/reference/municipal_overrides.json` に置く
- `data/state/lastmod.json`: サイトマップの日付の台帳。消すと全ページが「今日」になる。
  テンプレートを変えた回の作り直しはエンジンが自動でやる（3-6）

### 3-6. ページの見た目を変えたときの lastmod

サイトマップの `lastmod` は `<main>` の指紋を `data/state/lastmod.json` と比べて決める
（sitemill ADR 0025）。**テンプレートを変えると指紋も変わる**ので、そのまま配置すると翌日の実行で
全ページが「今日変わった」になり、Google に渡す手がかりが消える。実測では、取得時刻に印を付けた
だけで 6,547 ページ中 5,717 ページの指紋が変わった（2026-09-19）。

**v0.7.4 からはエンジンが自動で作り直す。** 台帳にテンプレートの鍵（`layout_key`）が入り、
鍵が変わった回は指紋だけ作り直して日付を据え置く。手で作り直す作業は要らない。

見るのは実行レポートの次の 2 つ。

- `build.lastmod_relearned`: 作り直したページ数。テンプレートを変えた回だけ大きくなる
- 翌日の `build.lastmod_changed`: いつもの水準（1 日 20〜300）に戻っていること。
  戻らないなら、日付・時刻の表示に `data-sitemill-volatile` を付け忘れている

**作り直した回は、事実が変わったページも日付が据え置かれる**（その変化は翌日の実行で拾う）。
全ページを「今日」にするより害が小さい、という判断（sitemill ADR 0025 の追記）。

手元で見た目を確かめるときは、共有の `dist` と台帳を書き換えないよう worktree を使う。

```bash
git worktree add --detach <一時場所> HEAD   # そこへ変更したファイルを写す
PYTHONPATH=<一時場所>/src <本体>/.venv/Scripts/python.exe -c "import sys; sys.argv=['akiya-atlas','build']; from akiya_atlas.cli import main; main()"
```

`akiya-atlas` コマンドは cwd ではなくパッケージの位置で作業場所を決めるので、`PYTHONPATH` を
付けないと共有の作業ツリーを書き換える。

## 4. 人間側の未完了作業

詳しい手順は `docs/human-tasks.md`。公開に関わるものを再掲する。

| 作業 | いまの状態 | 影響 |
| --- | --- | --- |
| ~~**運営者名と連絡先**~~ | 済（2026-09-12）。`site.toml` の `[operator]` に「空き家アトラス 運営」とお問い合わせフォームの URL を入れた | 全ページのフッターと `/about/` に出る。フォームは `tools/contact_form/` のApps Script が作ったもので、届いた依頼は takedown / needs-human / municipality の Issue になる |
| **ASP の計測 URL**（残り 2 種別） | 解体（A8）と片付け（もしも）は掲載中。査定・買取・リフォームは未契約 | 未契約の種別は `/owners/` の相談先に「準備中」と出る。ダミーリンクは置かない |
| ~~**Search Console**~~ | 済（2026-09-14）。サービスアカウントの鍵を `.env` と Secrets の `GOOGLE_SEARCH_CONSOLE_KEY` に登録し、日次が取り込む | 週次 Issue に検索の状況が出る。鍵が無いと取り込みだけが飛び、日次は止まらない |
| **Google Maps のキー** | `GOOGLE_MAPS_EMBED_KEY` 未登録 | 地図が外部リンクのフォールバック表示になる |
| ~~**Cloudflare Web Analytics**~~ | 済（2026-09-12）。RUM の自動挿入で有効。**`CF_WEB_ANALYTICS_TOKEN` は登録しない**（入れるとタグが 2 つ出て二重計測。ADR 0011） | 閲覧数と `/go/` のクリック数が取れる |

プライバシーポリシーは `/about/` に載せてある。お問い合わせの扱い（AI での分類・自動返信、Issue への転記）を変えたときは、`tools/contact_form/` の実装と `/about/` の記載の両方を直す。

いずれも無くてもパイプラインは動く。登録は `gh secret set <名前> --repo hikuzawa/akiya-atlas`。

## 5. エンジン（sitemill）の版を上げる

**CI は sitemill のタグを見る。main の変更は自動では入らない**（2026-09-12 に `v0.1.0` へ固定）。
sitemill の main では別サービス向けの拡張を進めるため、空き家の日次実行を固定版から切り離してある。
手元の開発は `../sitemill` への path 依存のままなので、ローカルでは main の変更がすぐ効く。
**手元で通っても CI では通らない**ことがある点に注意する。

### 5-1. エンジンの修正を空き家側に取り込む手順

1. sitemill 側で修正を入れ、テストを通す（main が別作業で使えないときは 5-2 を見る）
2. 新しいタグを打って push する（版の付け方は 5-3）

   ```bash
   git tag -a v0.4.2 -m "..." && git push origin v0.4.2
   ```

3. akiya-atlas 側で、CI が見るタグを 3 つのワークフローすべてで書き換える

   ```bash
   sed -i 's/ref: v0.4.1/ref: v0.4.2/' .github/workflows/pipeline.yml .github/workflows/checks.yml .github/workflows/weekly.yml
   ```

   `CLAUDE.md` の「現在 vX.Y.Z」の記載も同じときに直す（akiya-atlas と sitemill の両方）。
4. 手元で `uv run pytest -q` と `uv run sitemill build` を通す。path 依存なので**検証されるのは
   main の内容で、CI が使うタグではない**。main がタグより進んでいるときは 5-2 の最後のやり方で
   固定版を入れて確かめる
5. コミットして push する。`checks` が新しいタグで通ることを確認する
6. 反映は `gh workflow run pipeline -f mode=deploy-only -f deploy=true`。本番で該当箇所を目で確かめる
7. 日次が失敗したら、前のタグに戻す（3 の逆）。データは触らない

### 5-2. main が別の作業で使われているときは保守ブランチで出す

sitemill の main で別サービス向けの改修が進んでいる間は、そこに空き家向けの修正を混ぜない。
**作業ツリー `C:\projects\sitemill` は別のセッションが編集していることがあるので触らない。**
今のタグから保守ブランチを worktree で切って直す。

```bash
git -C ../sitemill worktree add -b release/0.1 ../sitemill-rel01 v0.1.0
```

1. 切った先（`../sitemill-rel01`）で直し、`uv sync` してから `uv run pytest -q` と `uv run ruff check src tests` を通す
2. コミットしてタグを打ち、ブランチとタグの両方を push する（`git push origin release/0.1` と `git push origin v0.4.2`）
3. worktree を消す。**worktree の中からは消せない**ので、先に別のディレクトリへ移ってから実行する

   ```bash
   git -C ../sitemill worktree remove ../sitemill-rel01 && git -C ../sitemill worktree prune
   ```

   Windows では空のディレクトリが残ることがある。`git worktree list` に出なくなっていれば git の状態は正しい
4. **main への取り込みは自分でやらない。** sitemill に「release/0.1 の修正を main に cherry-pick する」
   Issue を立て、main を触っているセッションに任せる。両方から同じファイルを直すと衝突する
5. 手元の検証は path 依存（main）では通らない。**CI が使う版で確かめてからタグを上げる。**
   タグの中身を作業ツリーの外に取り出し、`PYTHONPATH` で手前に置くのが軽い（共有ツリーにも
   `.venv` にも触らないので、別セッションが sitemill を編集中でも安全）。

   ```bash
   mkdir -p /tmp/sitemill-v041 && git -C ../sitemill archive v0.4.1 | tar -x -C /tmp/sitemill-v041
   PYTHONPATH=/tmp/sitemill-v041/src uv run pytest -q
   PYTHONPATH=/tmp/sitemill-v041/src uv run sitemill build
   ```

   `sitemill.__file__` を出して、取り出した方を読んでいることを確かめてから測る。
   仮想環境ごと入れ替えたいときは次のやり方もあるが、`uv run` は実行のたびに path 依存へ
   戻すので、この間は `.venv/Scripts/` から直に呼ぶ。

   ```bash
   uv pip install "sitemill @ git+https://github.com/hikuzawa/sitemill@v0.4.1"
   .venv/Scripts/python -m pytest -q
   uv sync
   ```

**`uv.lock` の扱いは 2 通りある。**

- **版を上げるときは更新して commit する。** エンジンの依存が増えることがある（v0.4.1 で
  `google-auth` が増えた）。lock を直さないと、CI の `uv sync --frozen` が「lock が古い」で落ちる。
  `uv lock` を実行し、`sitemill` の版がタグと同じになっていることを確かめてからコミットする
- **タグの版と main の版が違うときは `uv lock` では作れない。** lock に書かれるのは
  `../sitemill`（main）の版なので、タグの版と食い違う。`git -C ../sitemill show <タグ>:pyproject.toml`
  で版を確かめ、`uv.lock` の `name = "sitemill"` の `version` を手でその値に合わせる。依存そのものは
  タグ間で変わらないことが多いので、書き換えるのはこの 1 行だけでよい（2026-09-15 の v0.5.2 で実施）
- **それ以外では commit しない。** 手元で `uv run` を回すだけでも、lock の `sitemill` の版が
  `../sitemill`（main）の版に書き換わる。main がタグより先に進んでいるときにその lock を入れると、
  タグを checkout する CI で落ちる。`git checkout -- uv.lock` で戻す

sitemill の設定や型に項目が増えたときは、その項目が main に入るまで手元では欠ける。akiya 側は
「入っている版でだけ渡す」書き方にしておく（例: `pages.py` の `operator_info` が `contact_label` を
`OperatorInfo.model_fields` にあるときだけ渡す）。cherry-pick が main に入ったら素直な呼び出しに戻してよい。

### 5-3. 版の付け方

- **パッチ（v0.1.x）**: 抽出や分類の直し、性能改善など、生成物の形が変わらないもの
- **マイナー（v0.x.0）**: `Service` の口が増える、設定項目が増えるなど、サービス側の対応が要るもの
- タグは sitemill の main から打つ。akiya-atlas 側の都合でエンジンを分岐させない

### 5-4. 次の版に進む条件

自動改善ループの着手条件（sitemill ADR 0014 §6）と揃えてある。

1. 日次パイプラインが **2 週間** 失敗 Issue なしで回っていること（週次 Issue の「失敗した工程 0 件」が 2 回続く）
2. `eval` の fixture が主要な型（表形式・カード形式・詳細ページ）を各 3 件以上含むこと → **達成済み**（9 件）
3. 1 か月の LLM 費用の上限を決め、実測がその半分以下であること
4. 変更してよい範囲の許可リストがコードの定数として書かれ、テストで守られていること

## 6. 費用の確認先

| 何の費用 | どこで見るか | 目安 |
| --- | --- | --- |
| LLM（抽出） | Anthropic Console → Usage（Cost で日別） | 月 $10〜30 |
| GitHub Actions | リポジトリ → Settings → Billing、または Organization の Billing → Actions | private の無料枠は月 2,000 分。日次は月 1,050〜1,350 分の見込み（Search Console の取り込みを入れて 3 倍に増えた）。無料枠に近づいたら `--inspect` を減らす |
| Cloudflare Pages | Cloudflare ダッシュボード → Workers & Pages → 使用量 | 無料枠は月 500 ビルド。日次 1 回なので余裕 |
| ドメイン | 取得元の更新料 | 年 1 回 |

週次まとめにも LLM の費用は出る（トークン数から計算）。Console の請求額と桁が違うときは、
手元の実行が混ざっていないか `--source` を確かめる。
