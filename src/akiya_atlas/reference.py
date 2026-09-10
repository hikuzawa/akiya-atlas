"""県の公的な「市町村リンク集」から code→公式URL の対応表を作る。

保存先は data/reference/{slug}_official_urls.json。

候補ドメインの推測が外れる独自ドメイン（例: 東かがわ市 higashikagawa.jp、今帰仁村 nakijin.jp）は、
この表が正解源になる（ADR 0007 / 0008）。ページは礼儀正しく 1 回だけ取得する。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sitemill.fetch.client import PoliteClient
from sitemill.fetch.links import extract_links, host_of
from sitemill.settings import Workspace

from akiya_atlas.municipalities import MunicipalityRef, default_code_table_path, municipalities_for

_NOISE = re.compile(
    r"（?外部リンク）?|\(外部リンク\)|外部サイト|ホームページ|公式サイト|公式|へ|のページ|役場|役所|ウェブサイト|\s+"
)


def _norm(text: str) -> str:
    return _NOISE.sub("", unicodedata.normalize("NFKC", text)).strip()


@dataclass
class OfficialUrlTable:
    prefecture: str
    slug: str
    source_url: str
    source_name: str
    matched: dict[str, str] = field(default_factory=dict)  # code -> url
    unmatched: list[str] = field(default_factory=list)  # 市町村名
    ignored: list[str] = field(default_factory=list)  # 名前が一致したがページ内リンクだった等
    names: dict[str, str] = field(default_factory=dict)  # code -> 市町村名

    def to_json(self) -> dict:
        return {
            "source_url": self.source_url,
            "source_name": self.source_name,
            "checked_on": date.today().isoformat(),
            "municipalities": [
                {"code": code, "name": self.names.get(code, ""), "official_url": url}
                for code, url in sorted(self.matched.items())
            ],
        }


def match_links(
    munis: list[MunicipalityRef], links: list[tuple[str, str]], *, page_host: str
) -> OfficialUrlTable:
    """(アンカー文字列, URL) の一覧を市町村名に突き合わせる。純関数（テスト対象）。"""
    table = OfficialUrlTable(
        prefecture=munis[0].prefecture if munis else "",
        slug=munis[0].prefecture_slug if munis else "",
        source_url="",
        source_name="",
    )
    table.names = {m.code: m.name for m in munis}
    by_name = {_norm(m.name): m for m in munis}
    for text, url in links:
        key = _norm(text)
        m = by_name.get(key)
        if m is None:
            # 装飾（役所・外部リンク等）を落としても一致しなければ、
            # アンカー文字列の中に市町村名がそのまま含まれるかを見る
            m = next((mm for name, mm in by_name.items() if name and name in key), None)
        if m is None:
            continue
        if host_of(url) == page_host:
            table.ignored.append(f"{m.name}: 県サイト内のページ {url}")
            continue  # 県サイト内の紹介ページではなく、市町村自身のサイトを採る
        if m.code not in table.matched:
            table.matched[m.code] = url
    table.unmatched = [m.name for m in munis if m.code not in table.matched]
    return table


def build_official_urls(
    ws: Workspace, prefecture: str, page_url: str, *, client: PoliteClient, name: str = ""
) -> OfficialUrlTable:
    """県の市町村リンク集を取得して対応表を作り、data/reference/ に保存する。"""
    munis = municipalities_for(prefecture, default_code_table_path(ws.root))
    if not munis:
        raise ValueError(f"{prefecture}: 市町村が見つからない（コード表を確認）")
    res = client.get(page_url)
    if not res.ok:
        raise RuntimeError(f"{page_url}: 取得できない（{res.error or res.status}）")
    links = [(ln.text, ln.url) for ln in extract_links(res.text, res.final_url)]
    table = match_links(munis, links, page_host=host_of(res.final_url))
    table.source_url = res.final_url
    table.source_name = name or f"{table.prefecture}の市町村リンク集"
    out = ws.root / "data" / "reference" / f"{table.slug}_official_urls.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(table.to_json(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return table


def official_urls_path(ws: Workspace, slug: str) -> Path:
    return ws.root / "data" / "reference" / f"{slug}_official_urls.json"
