"""ASP のオファー設定（ADR 0004）。契約前は「準備中」を出し、ダミーリンクは置かない。"""

from __future__ import annotations

from dataclasses import dataclass

from sitemill.models import Redirect


@dataclass(frozen=True)
class Offer:
    id: str
    label: str
    kind: str  # 査定 / 解体 / 買取
    description: str
    url: str | None = None  # ASP の計測 URL。契約後に入れる

    @property
    def ready(self) -> bool:
        return bool(self.url)

    @property
    def path(self) -> str:
        return f"/go/{self.id}"


OFFERS: tuple[Offer, ...] = (
    Offer(
        id="satei",
        label="不動産一括査定",
        kind="査定",
        description="複数の不動産会社に空き家の査定をまとめて依頼できます。売るか貸すかの判断材料に。",
    ),
    Offer(
        id="kaitai",
        label="解体一括見積",
        kind="解体",
        description="解体費用を複数社で比較。自治体の解体補助と組み合わせると負担を抑えられることがあります。",
    ),
    Offer(
        id="kaitori",
        label="空き家買取",
        kind="買取",
        description="仲介では売りにくい物件の買取相談。",
    ),
)


def active_offers() -> list[Offer]:
    return [o for o in OFFERS if o.ready]


def redirects() -> list[Redirect]:
    """/go/<id> → ASP の URL。契約済みのものだけ _redirects に出す。"""
    return [Redirect(from_path=o.path, to_url=o.url, status=302) for o in OFFERS if o.url]
