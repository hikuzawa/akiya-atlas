"""ASP のオファー設定と掲載規約（ADR 0004, 0010）。

契約前は「準備中」を出し、ダミーリンクは置かない。

ここは「何をどこに出すか」のデータだけを持つ。実際に出ているかの検査は `ad_check.py`、
案件を追加するときの受け渡し様式は `akiya-atlas-ops/docs/affiliates.md`。

- 広告リンクは必ず `/go/<offer_id>/<placement>/` を経由する（直リンクを置かない）。
  転送ページで計測ビーコンが 1 回動くので、どのページのどの枠から何回押されたかが後で分かる。
- `_redirects` には枠を含まない `/go/<offer_id>` も出す（紙やメールから辿るときの素の導線）。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from sitemill.models import Redirect

# 種別。1 つの枠に同じ種別を 2 件以上出さない（ADR 0010）
KIND_SATEI = "査定"
KIND_KAITAI = "解体"
KIND_KAITORI = "買取"
# 空き家の片づけ・残置物の撤去と、遺品整理は分ける。利用者にとって別のもので、
# 所有者が求めるのは前者が多い（ADR 0010 の追記、2026-09-13）
KIND_KATAZUKE = "片付け"
KIND_IHIN = "遺品整理"
KIND_REFORM = "リフォーム"
ALL_KINDS = (KIND_SATEI, KIND_KAITAI, KIND_KAITORI, KIND_KATAZUKE, KIND_IHIN, KIND_REFORM)


@dataclass(frozen=True)
class Asp:
    """ASP ごとの掲載規約。ビルド時の検査（`ad_check.py`）はこの値だけを根拠にする。

    規約が変わったらここを直す。ここに書いていない制約は検査されないので、
    機械で確かめられないもの（画像の使い方など）は `Offer.constraints` に文章で残す。
    """

    id: str
    name: str
    # 広告リンクを含むページに「広告である」表示が要るか
    requires_ad_notice: bool = True
    # 広告リンクの rel に sponsored が要るか
    requires_sponsored_rel: bool = True
    # 計測 URL に使ってよいホスト。_redirects の宛先がここに無ければビルドを止める
    allowed_link_hosts: tuple[str, ...] = ()
    # 掲載ページに出してはいけない表現。A8 は文言の定めが無いので空
    forbidden_phrases: tuple[str, ...] = ()
    # 掲載 URL を届け出る画面の呼び名（`akiya-atlas ad-urls` の出力に添える）。
    # 空なら、その ASP には掲載 URL の個別届け出が無い
    submit_label: str = ""


ASPS: dict[str, Asp] = {
    "a8": Asp(
        id="a8",
        name="A8.net",
        requires_ad_notice=True,  # 一般消費者が閲覧できる位置に広告とわかる表示が必須
        requires_sponsored_rel=True,
        allowed_link_hosts=("px.a8.net", "www16.a8.net", "www17.a8.net", "www18.a8.net"),
        forbidden_phrases=(),
        submit_label="広告掲載URL管理",
    ),
    "moshimo": Asp(
        id="moshimo",
        name="もしもアフィリエイト",
        # 景品表示法のステマ規制とガイドラインで、広告と分かる表示が要る
        requires_ad_notice=True,
        requires_sponsored_rel=True,
        # 広告リンクは af.moshimo.com の click。i.moshimo.com の 1x1 画像は使わない
        allowed_link_hosts=("af.moshimo.com",),
        forbidden_phrases=(),
        # 掲載 URL の個別届け出は無い。提携はメディア単位で、申請時にサイトが広告主へ通知される
        submit_label="",
    ),
}


@dataclass(frozen=True)
class Placement:
    """広告を置ける枠。どのページのどの文脈に、どの種別を置けるかを決める（ADR 0010）。"""

    id: str
    # 掲載されるページの URL パス。掲載 URL の届け出と検査に使う。
    # `{pref}` を含む枠は都道府県ごとのページに出る（ADR 0012）
    page_path: str
    label: str  # 人が読む説明
    kinds: tuple[str, ...]  # この枠に置いてよい種別

    @property
    def per_prefecture(self) -> bool:
        return "{pref}" in self.page_path

    def path_for(self, pref_slug: str = "") -> str:
        return self.page_path.format(pref=pref_slug) if self.per_prefecture else self.page_path


PLACEMENTS: tuple[Placement, ...] = (
    Placement("owners-consult", "/owners/", "所有者向けページの「相談先」", ALL_KINDS),
    Placement(
        "owners-flow-sale", "/owners/", "「売却・賃貸までの流れ」の直後", (KIND_SATEI, KIND_KAITORI)
    ),
    Placement("owners-flow-demolition", "/owners/", "「解体までの流れ」の直後", (KIND_KAITAI,)),
    Placement(
        "owners-cleanup",
        "/owners/",
        "「6 つの選択肢」の片づけの文脈",
        (KIND_KATAZUKE, KIND_IHIN),
    ),
    Placement(
        "owners-renovation", "/owners/", "「6 つの選択肢」の住む・貸すの文脈", (KIND_REFORM,)
    ),
    # 都道府県ごとの所有者向けページ（ADR 0012）。地域限定の案件はここにだけ出す
    Placement("owners-pref-consult", "/owners/{pref}/", "都道府県ページの「相談先」", ALL_KINDS),
)
PLACEMENT_BY_ID = {p.id: p for p in PLACEMENTS}


@dataclass(frozen=True)
class Offer:
    """1 案件。`akiya-atlas-ops/docs/affiliates.md` の受け渡し様式と 1 対 1 で対応する。"""

    id: str
    label: str  # ボタンに出す短い名前
    kind: str
    description: str
    # 以下は契約後に埋まる。url が入るまで「準備中」表示のまま（ダミーリンクは置かない）
    asp: str = ""
    name: str = ""  # ASP 上の案件名
    advertiser: str = ""
    program_id: str = ""
    url: str | None = None  # ASP の計測 URL
    landing_prefix: str = ""  # 飛び先 URL の制約。登録時に人が 1 度確認する
    reward_condition: str = ""
    cookie_days: int | None = None  # 再訪問期間
    constraints: tuple[str, ...] = ()  # 機械で確かめられない制約を文章で残す
    placements: tuple[str, ...] = ()
    # 選定ツールの点数（sitemill ADR 0019。0 は未評価）。登録のとき `offers emit` が入れる
    score: int = 0
    # 対応する都道府県名。空なら全国対応（ADR 0012）。「首都圏」のような語は登録時に人が展開する
    regions: tuple[str, ...] = ()
    region_quote: str = ""  # 対応エリアの原文。推測で広げないための根拠
    # 同じ種別が複数あるときは小さい方だけを出す。None なら点数から決める（点数が高いほど前）
    rank: int | None = None
    approved_on: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.url)

    @property
    def order(self) -> int:
        """枠の中での並び。小さいほど前。

        最初は選定の点数を初期値にする（根拠のある値が自動で入る）。掲載後は `/go/` の
        クリック数と ASP の成果数を見て `rank` を手で入れ、そちらを優先する（ADR 0010）。
        """
        if self.rank is not None:
            return self.rank
        return max(1, 100 - self.score)

    @property
    def path(self) -> str:
        """枠を含まない導線。`_redirects` に出す。"""
        return f"/go/{self.id}"

    def placement_path(self, placement: str) -> str:
        return f"/go/{self.id}/{placement}/"


OFFERS: tuple[Offer, ...] = (
    Offer(
        id="satei",
        label="不動産一括査定",
        kind=KIND_SATEI,
        description="複数の不動産会社に空き家の査定をまとめて依頼できます。売るか貸すかの判断材料に。",
        placements=("owners-consult", "owners-flow-sale"),
    ),
    Offer(
        id="kaitai-110",
        label="解体一括見積",
        kind=KIND_KAITAI,
        description="解体費用を複数社で比較。自治体の解体補助と組み合わせると負担を抑えられることがあります。",
        asp="a8",
        name="どんな構造の建物でもお任せください！【解体工事110番】",
        advertiser="シェアリングテクノロジー株式会社",
        program_id="s00000015223012",
        # テキスト素材の計測 URL。バナー素材とは a8mat が別なので、テキスト導線にはこちらを使う
        url="https://px.a8.net/svt/ejp?a8mat=4BC9F9+52S3HU+39GM+1ZG8B6",
        landing_prefix="https://aff.life-110.com/?st_site=kaitai_kouji&st_aff=a8",
        reward_condition="WEB または電話申込後、30 日以内に見積もり対応加盟店の手配完了",
        cookie_days=90,
        constraints=(
            "リスティング広告での集客は不可（本サイトは自然検索のみ）",
            "芸能人の画像を使用しない",
            "サイトロゴを編集しない",
            # 2026-09-12 に確認済み: px.a8.net から 2 回転送され landing_prefix の配下に着く
            "飛び先は landing_prefix の配下であること（登録時に 1 度だけ人が確認する）",
            # インプレッション計測用の 1×1 画像は置かない。成果の計測には不要で、掲載ページの
            # すべての表示で a8.net への通信が発生し「Cookie を使っていない」開示と噛み合わないため
            "インプレッション計測タグ（0.gif）は使用しない",
        ),
        placements=("owners-consult", "owners-flow-demolition"),
        rank=10,
        approved_on="2026-09-12",
    ),
    Offer(
        id="katazuke-center",
        label="空き家片付けセンター",
        kind=KIND_KATAZUKE,
        description=(
            "残置物の撤去・片づけから、買取・管理・再活用までの相談をまとめて受け付けます。"
            "申込みは WEB のフォームから。"
        ),
        asp="moshimo",
        name="空き家片付けセンター｜空き家の相談・空き家買取・残置物撤去等の申込",
        advertiser="株式会社つなぐ",
        program_id="6468",
        url="https://af.moshimo.com/af/c/click?a_id=5801836&p_id=6468&pc_id=18273&pl_id=82753",
        landing_prefix="https://akiyakatadzuke.com/",
        reward_condition=(
            "問い合わせ（申込）完了時。承認は本人確認のうえ成約・入金まで（承認期限 30 日）"
        ),
        cookie_days=90,
        constraints=(
            "リスティング（検索連動型）広告での集客は不可。サービス名・ブランド名・社名は"
            "除外ワードに設定する（本サイトは自然検索のみ）",
            "アダルト系サイトへの掲載は不可",
            "電話申込は成果対象外。WEB のフォームへ案内する",
            # 2026-09-13 に確認済み: af.moshimo.com から www 経由で landing_prefix に着く
            "飛び先は landing_prefix の配下であること（登録時に 1 度だけ人が確認する）",
            # A8 と同じ理由。掲載ページのすべての表示で ASP への通信が発生し、
            # 「本サイトは Cookie を使っていない」という開示と噛み合わないため
            "インプレッション計測タグ（i.moshimo.com の 1x1 画像）は使用しない",
        ),
        placements=("owners-consult", "owners-cleanup"),
        rank=10,
        approved_on="2026-09-13",
    ),
    # 地域限定の案件の 1 件目（ADR 0012）。`regions` を持つので /owners/hokkaido/ にだけ出る
    Offer(
        id="hoan-home-sapporo",
        label="札幌の片付け・不用品回収",
        kind=KIND_KATAZUKE,
        description=(
            "札幌市と近郊の不用品回収・空き家の片づけ。片づけた後の清掃まで同じ窓口で相談できます。"
            "旭川・室蘭・苫小牧・釧路にも対応、道内のそれ以外は交通費を別に見積もって相談。"
        ),
        asp="moshimo",
        name="Hoan Home Sapporo｜札幌の不用品回収・空き家片付けの契約",
        # 屋号は Hoan Home。運営会社は公式サイトの会社概要で確認した（2026-09-16）
        advertiser="SMホールディングス株式会社",
        program_id="7704",
        url="https://af.moshimo.com/af/c/click?a_id=5803570&p_id=7704&pc_id=22304&pl_id=96008",
        landing_prefix="https://hoanhome-sapporo.com/",
        reward_condition=(
            "問い合わせ（見積依頼）完了時に計測。承認は広告主が契約成立を確認してから"
            "（承認期限 60 日）。問い合わせだけでは承認されない"
        ),
        cookie_days=90,
        constraints=(
            "リスティング（検索連動型）広告での集客は不可。サービス名・ブランド名・社名は"
            "除外ワードに設定する（本サイトは自然検索のみ）",
            "「必ず最安」「地域最安値」「何でも無料」など、広告主が公式に出していない価格・"
            "優位性を断定しない",
            "広告主の公式サイト・公式アカウントだと誤認させる書き方をしない",
            "商号・ブランド名・ロゴ・画像を加工して広告素材に使わない",
            "対応エリア外は受注できず否認になる。対応する市と、それ以外は要相談であることを"
            "掲載文に書く",
            # 2026-09-16 に確認済み: af.moshimo.com から landing_prefix に 1 回で着く
            "飛び先は landing_prefix の配下であること（登録時に 1 度だけ人が確認する）",
            # 他の案件と同じ理由。掲載ページのすべての表示で ASP への通信が発生し、
            # 「本サイトは Cookie を使っていない」という開示と噛み合わないため
            "インプレッション計測タグ（i.moshimo.com の 1x1 画像）は使用しない",
        ),
        placements=("owners-pref-consult",),
        # 札幌市限定ではない。原文が道内の他都市を挙げ、それ以外の道内も交通費で対応と
        # 明記しているので、県の単位では北海道 1 県に収まる（推定で広げていない）
        regions=("北海道",),
        region_quote=(
            "もしもの案件情報「札幌市を中心に半径100km程度まで対応し、それ以外の北海道エリアに"
            "ついても別途交通費にて対応可能です。」／公式サイトの会社概要「対応エリア "
            "札幌市 / 札幌近郊 / 旭川市 / 室蘭市 / 苫小牧市 / 釧路市」（2026-09-16 確認）"
        ),
        rank=10,
        approved_on="2026-09-16",
    ),
    Offer(
        id="kaitori",
        label="空き家買取",
        kind=KIND_KAITORI,
        description="仲介では売りにくい物件の買取相談。",
        placements=("owners-consult",),
    ),
)


def active_offers() -> list[Offer]:
    return [o for o in OFFERS if o.ready]


def offers_for(
    placement: str, *, pref: str | None = None, include_pending: bool = False
) -> list[Offer]:
    """枠に出す案件。同じ種別は 1 件だけに絞る（ADR 0010）。

    `pref` は都道府県名。対応地域を持つ案件はその県のページにだけ出し、同じ種別なら全国対応より
    先に採る（ADR 0012）。地域限定が無ければ全国対応に落ち、どちらも無ければその種別は出さない。
    include_pending=True のときは契約前の案件も「準備中」として返す（相談先の一覧）。
    """
    if placement not in PLACEMENT_BY_ID:
        raise KeyError(f"未定義の枠: {placement}")
    allowed = PLACEMENT_BY_ID[placement].kinds
    cands = [
        o
        for o in OFFERS
        if placement in o.placements
        and o.kind in allowed
        and (o.ready or include_pending)
        and (not o.regions or (pref is not None and pref in o.regions))
    ]

    def _local(o: Offer) -> int:
        return 0 if o.regions else 1  # その県の案件を先に

    chosen: dict[str, Offer] = {}
    for o in sorted(cands, key=lambda o: (not o.ready, _local(o), o.order, o.id)):
        chosen.setdefault(o.kind, o)
    return sorted(chosen.values(), key=lambda o: (ALL_KINDS.index(o.kind), _local(o), o.order))


def placed(offer: Offer) -> list[str]:
    """その案件が実際に出る枠（契約前は空）。"""
    return [p for p in offer.placements if offer.ready and p in PLACEMENT_BY_ID]


def page_offers(page_path: str, *, pref: str | None = None, pref_slug: str = "") -> list[Offer]:
    """そのページに広告リンクが出る案件。広告表記を出すかの判断に使う。

    都道府県ごとのページでは `pref`（県名）と `pref_slug`（URL の断片）を渡す（ADR 0012）。
    """
    seen: dict[str, Offer] = {}
    for p in PLACEMENTS:
        if p.path_for(pref_slug) != page_path:
            continue
        for o in offers_for(p.id, pref=pref):
            seen[o.id] = o
    return list(seen.values())


@dataclass(frozen=True)
class GoTarget:
    """転送ページ 1 枚分。ページ生成（pages.py）と検査（ad_check.py）が同じ一覧を見る。"""

    offer: Offer
    placement: Placement

    @property
    def url_path(self) -> str:
        return self.offer.placement_path(self.placement.id)


def go_targets() -> list[GoTarget]:
    return [
        GoTarget(offer=o, placement=PLACEMENT_BY_ID[p])
        for o in OFFERS
        if o.ready
        for p in placed(o)
    ]


def asp_of(offer: Offer) -> Asp | None:
    return ASPS.get(offer.asp)


def link_host(url: str) -> str:
    return urlsplit(url).hostname or ""


def redirects() -> list[Redirect]:
    """/go/<id> → ASP の URL。契約済みのものだけ _redirects に出す。

    枠つきの `/go/<id>/<placement>/` は転送ページ（HTML）なのでここには出さない。
    """
    return [Redirect(from_path=o.path, to_url=o.url, status=302) for o in OFFERS if o.url]
