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

### 1-2. 正常値の目安

| 見るところ | 目安 | 外れたときの意味 |
| --- | --- | --- |
| 1 日の実行時間 | 8〜15 分 | 30 分を超えるなら、どこかの source でページ数が増えたか、相手サイトが遅い |
| 取得したサイト数 | 268 から、巡回間隔の適応が効くと 120〜160 へ下がる | 増え続けるなら適応が効いていない（`adaptive_interval` の設定を確認） |
| 変化したサイト数 | 1 日 4〜46 | 0 が数日続くなら、取得はできているのに差分検知が壊れている疑い |
| 1 日の費用 | $0.2〜1.0（変化したページ数に比例） | $3 を超える日が続くなら、毎日作り直されるページ（日付入りなど）を掴んでいる |
| 月の費用 | $10〜30 | |
| heal の点検数 | 0 件の source の数（現在 9 件前後） | 増え続けるなら、抽出が効かない型が増えている |
| heal の取り下げ | 0〜数件 | 一度に 10 件以上下がるなら、分類の判断が変わった疑い。差分を見る |
| 生成ページ数 | 6,000 前後 | 急に減ったら、データの取りこぼしかビルドの失敗 |

実行時間の内訳は、巡回 3〜4 分、抽出 2 分前後、自己修復は 0 件の source 1 件につき約 34 秒（いまは 9 件で 5 分）、
ビルド 30 秒、eval 30 秒、Actions の準備 1〜2 分。

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

### 3-3. 巡回しないと決めた自治体

物件一覧を PDF でしか出していない自治体（山口県柳井市、兵庫県小野市）は巡回せず、公式ページへの
リンクだけを出す。PDF の解析には依存の追加が要るうえ、画像が混じるため数値を確実には読めない。
推測で数値を載せない方針（ADR 0004）に合わせて、案内に徹する。

同じ扱いにしたい自治体が出たら `data/reference/municipal_overrides.json` に根拠つきで足し、
`uv run akiya-atlas rediscover <県> --code <6 桁>` を回す。

### 3-4. 新しい県の追加は要らない

47 都道府県 1,740 市町村はすべて登録済み（北方領土の 5 村を除く）。市町村合併があったときだけ
`data/reference/municipal_codes.csv` を更新して `uv run akiya-atlas expand <県>` を回す。

### 3-5. 触らない方がよいもの

- `data/state/crawl.json`: 巡回の状態。手で消すと全ページを取り直すことになる（相手サイトに負荷）
- `data/records/*.jsonl`: 物件。消すと `first_seen_at` の履歴が失われる
- `data/reference/municipal_overrides.json`: 自動では決められない自治体の確定情報。根拠つきでのみ足す

## 4. 人間側の未完了作業

詳しい手順は `docs/human-tasks.md`。公開に関わるものを再掲する。

| 作業 | いまの状態 | 影響 |
| --- | --- | --- |
| **運営者名と連絡先** | `site.toml` の `[operator]` が「準備中」 | 全ページのフッターと `/about/` に「準備中」と出る。信頼シグナルとして最優先 |
| **ASP の計測 URL** | `src/akiya_atlas/affiliates.py` の `Offer.url` が未設定 | 所有者向けの CTA が「準備中」のまま。ダミーリンクは置かない方針 |
| **Google Maps のキー** | `GOOGLE_MAPS_EMBED_KEY` 未登録 | 地図が外部リンクのフォールバック表示になる |
| **Cloudflare Web Analytics** | `CF_WEB_ANALYTICS_TOKEN` 未登録 | 閲覧数が計測されない |

いずれも無くてもパイプラインは動く。登録は `gh secret set <名前> --repo hikuzawa/akiya-atlas`。

## 5. エンジン（sitemill）の版を上げる

**CI は sitemill のタグを見る。main の変更は自動では入らない**（2026-09-12 に `v0.1.0` へ固定）。
sitemill の main では別サービス向けの拡張を進めるため、空き家の日次実行を固定版から切り離してある。
手元の開発は `../sitemill` への path 依存のままなので、ローカルでは main の変更がすぐ効く。
**手元で通っても CI では通らない**ことがある点に注意する。

### 5-1. エンジンの修正を空き家側に取り込む手順

1. sitemill 側で修正を main に入れ、テストを通す
2. 新しいタグを打って push する（版の付け方は後述）

   ```bash
   cd sitemill && git tag -a v0.1.1 -m "..." && git push origin v0.1.1
   ```

3. akiya-atlas 側で、CI が見るタグを 3 つのワークフローすべてで書き換える

   ```bash
   cd akiya-atlas
   sed -i 's/ref: v0.1.0/ref: v0.1.1/' .github/workflows/pipeline.yml .github/workflows/checks.yml .github/workflows/weekly.yml
   ```

4. 手元で `uv run pytest -q` と `uv run sitemill build` を通す（path 依存なので main の内容で検証される。
   タグと main がずれているときは、sitemill 側でタグを打った時点の内容と一致しているか確認する）
5. コミットして push する。`checks` が新しいタグで通ることを確認する
6. 日次が失敗したら、前のタグに戻す（3 の逆）。データは触らない

### 5-2. 版の付け方

- **パッチ（v0.1.x）**: 抽出や分類の直し、性能改善など、生成物の形が変わらないもの
- **マイナー（v0.x.0）**: `Service` の口が増える、設定項目が増えるなど、サービス側の対応が要るもの
- タグは sitemill の main から打つ。akiya-atlas 側の都合でエンジンを分岐させない

### 5-3. 次の版に進む条件

自動改善ループの着手条件（sitemill ADR 0014 §6）と揃えてある。

1. 日次パイプラインが **2 週間** 失敗 Issue なしで回っていること（週次 Issue の「失敗した工程 0 件」が 2 回続く）
2. `eval` の fixture が主要な型（表形式・カード形式・詳細ページ）を各 3 件以上含むこと → **達成済み**（9 件）
3. 1 か月の LLM 費用の上限を決め、実測がその半分以下であること
4. 変更してよい範囲の許可リストがコードの定数として書かれ、テストで守られていること

## 6. 費用の確認先

| 何の費用 | どこで見るか | 目安 |
| --- | --- | --- |
| LLM（抽出） | Anthropic Console → Usage（Cost で日別） | 月 $10〜30 |
| GitHub Actions | リポジトリ → Settings → Billing、または Organization の Billing → Actions | private の無料枠は月 2,000 分。日次は月 450〜540 分の見込み |
| Cloudflare Pages | Cloudflare ダッシュボード → Workers & Pages → 使用量 | 無料枠は月 500 ビルド。日次 1 回なので余裕 |
| ドメイン | 取得元の更新料 | 年 1 回 |

週次まとめにも LLM の費用は出る（トークン数から計算）。Console の請求額と桁が違うときは、
手元の実行が混ざっていないか `--source` を確かめる。
