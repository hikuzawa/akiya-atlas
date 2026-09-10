"""公式自治体ドメインの判定と、候補 URL の生成（ADR 0007, 条件1・3）。

日本の自治体は次のいずれかを使う:
- 地理型ドメイン: city.<romaji>.<pref>.jp / town.<...>.jp / vill.<...>.jp（JPRS が自治体に限定）
- lg.jp: <...>.lg.jp（地方公共団体専用）
どちらもドメイン種別自体が運営主体の強い根拠になる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from akiya_atlas.municipalities import MunicipalityRef
from akiya_atlas.romaji import romaji_variants

_KIND_PREFIX = {"市": "city", "町": "town", "村": "vill"}


@dataclass(frozen=True)
class OfficialHost:
    host: str
    kind: str  # lg_jp / geographic_municipal / geographic_other / other
    is_official: bool
    strength: str  # strong / medium / none

    def evidence(self) -> str:
        if self.kind == "lg_jp":
            return f"公式ドメイン {self.host}（.lg.jp、地方公共団体専用ドメイン）"
        if self.kind == "geographic_municipal":
            return f"公式ドメイン {self.host}（city/town/vill の地理型ドメイン、自治体に限定）"
        if self.kind == "geographic_other":
            return f"ドメイン {self.host}（地理型 .jp。自治体運営かは要確認）"
        return f"ドメイン {self.host}"


def classify_host(host: str, pref_slug: str) -> OfficialHost:
    h = host.lower().lstrip(".")
    if h.endswith(".lg.jp"):
        return OfficialHost(h, "lg_jp", True, "strong")
    if re.match(rf"^(?:www\.)?(?:city|town|vill|village)\.[a-z0-9-]+\.{pref_slug}\.jp$", h):
        return OfficialHost(h, "geographic_municipal", True, "strong")
    if re.match(rf"^(?:www\.)?[a-z0-9-]+\.{pref_slug}\.jp$", h):
        return OfficialHost(h, "geographic_other", False, "medium")
    return OfficialHost(h, "other", False, "none")


def classify_url(url: str, pref_slug: str) -> OfficialHost:
    return classify_host(urlsplit(url).hostname or "", pref_slug)


def candidate_official_urls(muni: MunicipalityRef) -> list[str]:
    """自治体の読みから公式 URL 候補を生成する（種別に応じた prefix を優先）。"""
    variants = romaji_variants(muni.name_kana) or []
    if not variants:
        return []
    pref = muni.prefecture_slug
    prefix = _KIND_PREFIX.get(muni.kind)
    prefixes = [prefix] if prefix else ["city", "town", "vill"]
    # 種別が不明なら 3 種すべて試す
    if prefix is None:
        prefixes = ["city", "town", "vill"]
    urls: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        if u not in seen:
            seen.add(u)
            urls.append(u)

    for r in variants:
        for pfx in prefixes:
            add(f"https://www.{pfx}.{r}.{pref}.jp/")
            add(f"https://www.{pfx}.{r}.{pref}.lg.jp/")
        add(f"https://www.{r}.{pref}.jp/")
        add(f"https://www.{r}.{pref}.lg.jp/")
        add(f"https://{r}.{pref}.lg.jp/")
    return urls
