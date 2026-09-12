"""ASP のオファー設定と掲載規約（ADR 0004, 0010）。

契約前は「準備中」を出し、ダミーリンクは置かない。

ここは「何をどこに出すか」のデータだけを持つ。実際に出ているかの検査は `ad_check.py`、
案件を追加するときの受け渡し様式は `docs/runbook/affiliates.md`。

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
KIND_IHIN = "遺品整理"
KIND_REFORM = "リフォーム"
ALL_KINDS = (KIND_SATEI, KIND_KAITAI, KIND_KAITORI, KIND_IHIN, KIND_REFORM)


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
    # 掲載 URL を届け出る画面の呼び名（`akiya-atlas ad-urls` の出力に添える）
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
}


@dataclass(frozen=True)
class Placement:
    """広告を置ける枠。どのページのどの文脈に、どの種別を置けるかを決める（ADR 0010）。"""

    id: str
    page_path: str  # 掲載されるページの URL パス。掲載 URL の届け出と検査に使う
    label: str  # 人が読む説明
    kinds: tuple[str, ...]  # この枠に置いてよい種別


PLACEMENTS: tuple[Placement, ...] = (
    Placement("owners-consult", "/owners/", "所有者向けページの「相談先」", ALL_KINDS),
    Placement(
        "owners-flow-sale", "/owners/", "「売却・賃貸までの流れ」の直後", (KIND_SATEI, KIND_KAITORI)
    ),
    Placement("owners-flow-demolition", "/owners/", "「解体までの流れ」の直後", (KIND_KAITAI,)),
    Placement("owners-cleanup", "/owners/", "「6 つの選択肢」の片づけの文脈", (KIND_IHIN,)),
    Placement(
        "owners-renovation", "/owners/", "「6 つの選択肢」の住む・貸すの文脈", (KIND_REFORM,)
    ),
)
PLACEMENT_BY_ID = {p.id: p for p in PLACEMENTS}


@dataclass(frozen=True)
class Offer:
    """1 案件。`docs/runbook/affiliates.md` の受け渡し様式と 1 対 1 で対応する。"""

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
    rank: int = 100  # 同じ種別が複数あるときは小さい方だけを出す
    approved_on: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.url)

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
        url=None,  # 計測 URL が届いたらここに入れる。入れた時点で公開される
        landing_prefix="https://aff.life-110.com/?st_site=kaitai_kouji&st_aff=a8",
        reward_condition="WEB または電話申込後、30 日以内に見積もり対応加盟店の手配完了",
        cookie_days=90,
        constraints=(
            "リスティング広告での集客は不可（本サイトは自然検索のみ）",
            "芸能人の画像を使用しない",
            "サイトロゴを編集しない",
            "飛び先は landing_prefix の配下であること（登録時に 1 度だけ人が確認する）",
        ),
        placements=("owners-consult", "owners-flow-demolition"),
        rank=10,
        approved_on="2026-09-12",
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


def offers_for(placement: str, *, include_pending: bool = False) -> list[Offer]:
    """枠に出す案件。同じ種別は rank の小さい 1 件だけに絞る（ADR 0010）。

    include_pending=True のときは契約前の案件も「準備中」として返す（相談先の一覧）。
    """
    if placement not in PLACEMENT_BY_ID:
        raise KeyError(f"未定義の枠: {placement}")
    allowed = PLACEMENT_BY_ID[placement].kinds
    cands = [
        o
        for o in OFFERS
        if placement in o.placements and o.kind in allowed and (o.ready or include_pending)
    ]
    chosen: dict[str, Offer] = {}
    for o in sorted(cands, key=lambda o: (not o.ready, o.rank, o.id)):
        chosen.setdefault(o.kind, o)
    return sorted(chosen.values(), key=lambda o: (ALL_KINDS.index(o.kind), o.rank))


def placed(offer: Offer) -> list[str]:
    """その案件が実際に出る枠（契約前は空）。"""
    return [p for p in offer.placements if offer.ready and p in PLACEMENT_BY_ID]


def page_offers(page_path: str) -> list[Offer]:
    """そのページに広告リンクが出る案件。広告表記を出すかの判断に使う。"""
    seen: dict[str, Offer] = {}
    for p in PLACEMENTS:
        if p.page_path != page_path:
            continue
        for o in offers_for(p.id):
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
