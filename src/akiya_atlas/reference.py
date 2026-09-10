"""県の公的な「市町村リンク集」から code→公式URL の対応表を作る。

保存先は data/reference/{slug}_official_urls.json。

候補ドメインの推測が外れる独自ドメイン（例: 東かがわ市 higashikagawa.jp、今帰仁村 nakijin.jp）は、
この表が正解源になる（ADR 0007 / 0008）。ページは礼儀正しく 1 回だけ取得する。

リンク集の書き方は県ごとに違うので、次の順で市町村名を見つける（全国 47 県で検証）。
1. アンカー文字列そのもの（「東大阪市（外部サイトへリンク）」のような装飾は落とす）
2. 装飾が落としきれないときは、含まれる市町村名のうち**最も長いもの**
   （「南相馬市（みなみそうまし）」を相馬市と取り違えないため）
3. アンカーが URL や画像のときは、その行（表の行・箇条書き）のテキスト
4. それでも分からなければ、直前の見出し（石川県は市町名が h3、リンクは「ホームページ：」の段落）
名前は異体字（檮原町/梼原町）とヶ/ケの揺れを吸収してから比べる。
同じ県に同名の市町村が複数あるとき（北海道の泊村）は、取り違えを避けて対応づけない。
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sitemill.fetch.client import PoliteClient
from sitemill.fetch.links import extract_link_rows, host_of
from sitemill.settings import Workspace

from akiya_atlas.municipalities import MunicipalityRef, default_code_table_path, municipalities_for

_NOISE = re.compile(
    r"（?外部リンク）?|\(外部リンク\)|外部サイト|ホームページ|公式サイト|公式|へ|のページ|役場|役所|ウェブサイト|\s+"
)
# 市町村名に出る異体字とかなの揺れ（比較用。表に書く名前はコード表のまま）
_VARIANTS = str.maketrans(
    {
        "檮": "梼",
        "竈": "釜",
        "瀧": "滝",
        "澤": "沢",
        "邊": "辺",
        "邉": "辺",
        "嶋": "島",
        "嶽": "岳",
        "龍": "竜",
        "曾": "曽",
        "舘": "館",
        "冨": "富",
        "藏": "蔵",
        "惠": "恵",
        "濱": "浜",
        "榮": "栄",
        "眞": "真",
        "德": "徳",
        "齋": "斎",
        "祗": "祇",
        "ヶ": "ケ",
        "ヵ": "カ",
    }
)
MAX_ROW_CONTEXT = 60  # 行のテキストから名前を拾うときの上限（長い段落は誤対応のもと）
MAX_HEADING = 40  # 見出しから名前を拾うときの上限


def _norm(text: str) -> str:
    return _NOISE.sub("", unicodedata.normalize("NFKC", text)).strip().translate(_VARIANTS)


@dataclass
class OfficialUrlTable:
    prefecture: str
    slug: str
    source_url: str
    source_name: str
    matched: dict[str, str] = field(default_factory=dict)  # code -> url
    unmatched: list[str] = field(default_factory=list)  # 市町村名
    ignored: list[str] = field(default_factory=list)  # 名前が一致したがページ内リンクだった等
    ambiguous: list[str] = field(default_factory=list)  # 同名が複数ある市町村（対応づけない）
    duplicates: list[str] = field(default_factory=list)  # 同じ URL が複数の市町村に付いた
    names: dict[str, str] = field(default_factory=dict)  # code -> 市町村名

    def to_json(self) -> dict:
        return {
            "source_url": self.source_url,
            "source_name": self.source_name,
            "checked_on": date.today().isoformat(),
            "unmatched": self.unmatched,
            "ambiguous": self.ambiguous,
            "municipalities": [
                {"code": code, "name": self.names.get(code, ""), "official_url": url}
                for code, url in sorted(self.matched.items())
            ],
        }


def _find(
    by_name: dict[str, MunicipalityRef], names_desc: list[str], key: str
) -> MunicipalityRef | None:
    """正規化済みの文字列から市町村を1つ選ぶ。完全一致 → 含まれる最長の名前の順。"""
    exact = by_name.get(key)
    if exact is not None:
        return exact
    return next((by_name[n] for n in names_desc if n in key), None)


def match_links(
    munis: list[MunicipalityRef],
    links: Sequence[tuple[str, ...]],
    *,
    page_host: str,
) -> OfficialUrlTable:
    """(アンカー文字列, URL[, 行のテキスト][, 見出し]) を市町村名に突き合わせる。純関数。"""
    table = OfficialUrlTable(
        prefecture=munis[0].prefecture if munis else "",
        slug=munis[0].prefecture_slug if munis else "",
        source_url="",
        source_name="",
    )
    table.names = {m.code: m.name for m in munis}
    counts = Counter(_norm(m.name) for m in munis)
    by_name: dict[str, MunicipalityRef] = {}
    for m in munis:
        key = _norm(m.name)
        if counts[key] > 1:  # 同名（北海道の泊村）は取り違えるので対応づけない
            if m.name not in table.ambiguous:
                table.ambiguous.append(m.name)
            continue
        by_name[key] = m
    names_desc = sorted(by_name, key=len, reverse=True)
    for link in links:
        text, url = link[0], link[1]
        context = link[2] if len(link) > 2 else ""
        heading = link[3] if len(link) > 3 else ""
        m = _find(by_name, names_desc, _norm(text))
        if m is None and 0 < len(context) <= MAX_ROW_CONTEXT:
            m = _find(by_name, names_desc, _norm(context))
        if m is None and 0 < len(heading) <= MAX_HEADING:
            m = _find(by_name, names_desc, _norm(heading))
        if m is None:
            continue
        if host_of(url) == page_host:
            table.ignored.append(f"{m.name}: 県サイト内のページ {url}")
            continue  # 県サイト内の紹介ページではなく、市町村自身のサイトを採る
        if m.code not in table.matched:
            table.matched[m.code] = url
    table.unmatched = [m.name for m in munis if m.code not in table.matched]
    seen: dict[str, str] = {}
    for code, url in sorted(table.matched.items()):
        if url in seen:
            table.duplicates.append(f"{table.names[seen[url]]} と {table.names[code]}: {url}")
        seen[url] = code
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
    rows = [
        (r.text, r.url, r.context, r.heading) for r in extract_link_rows(res.text, res.final_url)
    ]
    table = match_links(munis, rows, page_host=host_of(res.final_url))
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
