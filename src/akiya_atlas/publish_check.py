"""公開直前の性質の検査（ADR 0016、sitemill ADR 0024）。

データがどの経路で今の形になったか（巡回・手編集・マージ・キャッシュの復元）に関係なく、
**公開する生成物とその直前のデータ**に対して、守りたい性質を確かめる。取り込み側の歯止めは
その道具を通ったものしか守らないので、保証はここで持つ。

検査する性質（どれも、破れたら配置を止める）:

1. 物件ページを巡回していない情報源の物件は、インデックスできる形で公開しない
   （兵庫県小野市: 2026-09-12 に巡回をやめたのに、9/11 に取り込んだ 1 件が 9/16 まで公開されていた）
2. 人が固定した扱い（`data/reference/municipal_overrides.json`）が守られている。
   `link_only` の自治体は物件ページを巡回しない。`no_website` の自治体はサイトに載せない
3. 取り下げ依頼のパス（自身と配下）の物件ページを、インデックスできる形で公開しない
4. 「◯市町村の空き家バンクを巡回」の数が、巡回中（`bank_status: available`）の数と一致し、
   その全部が実際に物件ページを巡回している

1・2・4 の一部はデータだけで確かめられるので、コミット済みのデータに対するテストにも当てる
（`tests/test_publish_check.py`）。push のたびに気づける。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from sitemill.models import Source
from sitemill.settings import Workspace

from akiya_atlas.data import load_entries, municipality_from_entry, records_path
from akiya_atlas.pages import hidden_paths, is_hidden
from akiya_atlas.schema import Municipality
from akiya_atlas.service import crawls_listings

OVERRIDES = Path("data/reference/municipal_overrides.json")
_NOINDEX = re.compile(r"""<meta\s+name=["']robots["']\s+content=["'][^"']*noindex""", re.I)
_LOC = re.compile(r"<loc>([^<]+)</loc>")
# トップのリード文。「巡回している市町村数」を公言している場所（ADR 0015）
_CRAWLED_CLAIM = re.compile(r"全国\s*([\d,]+)\s*市町村の空き家バンクを毎日巡回")


def _overrides(ws: Workspace) -> list[dict]:
    path = ws.root / OVERRIDES
    if not path.is_file():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("municipalities", []))


Loaded = tuple[dict[str, Source], list[Municipality]]


def load(ws: Workspace) -> Loaded:
    """情報源と市町村を 1 回だけ読む。data/sources は全国分で読むのに 2 秒かかる。"""
    entries = load_entries(ws)
    sources = {e["id"]: Source.model_validate(e) for e in entries}
    munis = [m for m in (municipality_from_entry(e) for e in entries) if m is not None]
    return sources, munis


# ---------------------------------------------------------------------------
# データだけで確かめられるもの（テストからも呼ぶ）
# ---------------------------------------------------------------------------


def check_data(ws: Workspace, loaded: Loaded | None = None) -> list[str]:
    problems: list[str] = []
    sources, munis = loaded or load(ws)

    # 1. 物件ページを巡回していない情報源に、公開される状態（active / stale）のレコードが無い
    for source in sources.values():
        if crawls_listings(source):
            continue
        path = records_path(ws, source.id)
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("status") in ("active", "stale"):
                problems.append(
                    f"{source.id}: 物件ページを巡回していないのに、公開される状態の物件がある"
                    f"（{rec.get('listing_no')}, status={rec.get('status')}）。retire_unlisted が"
                    "効いていない"
                )

    # 2. 人が固定した扱い
    by_code: dict[str, list] = {}
    for m in munis:
        by_code.setdefault(m.code, []).append(m)
    for ov in _overrides(ws):
        code, name = str(ov.get("code")), f"{ov.get('prefecture', '')}{ov.get('name', '')}"
        if ov.get("link_only"):
            for m in by_code.get(code, []):
                if crawls_listings(sources[m.id]):
                    problems.append(
                        f"{code} {name}: municipal_overrides で link_only と決めたのに、"
                        "物件ページを巡回している"
                    )
        if ov.get("no_website") and by_code.get(code):
            problems.append(
                f"{code} {name}: municipal_overrides で no_website（サイトに載せない）なのに、"
                "data/sources に市町村がある"
            )

    # 4. 巡回中と言っている市町村は、実際に物件ページを巡回している
    for m in munis:
        if m.crawled and not crawls_listings(sources[m.id]):
            problems.append(
                f"{m.code} {m.prefecture}{m.name}: bank_status=available"
                "（巡回中として数える）なのに、"
                "物件ページを巡回していない"
            )
    return problems


# ---------------------------------------------------------------------------
# 生成物を見るもの（配置の直前）
# ---------------------------------------------------------------------------


def _listing_pages(dist: Path, muni_path: str) -> list[Path]:
    """市町村の配下にある物件ページ（/<県>/<市町村>/<物件>/index.html）。"""
    base = dist / muni_path
    return sorted(p for p in base.glob("*/index.html")) if base.is_dir() else []


def _indexable(page: Path) -> bool:
    return not _NOINDEX.search(page.read_text(encoding="utf-8"))


def _sitemap_paths(dist: Path, base_url: str) -> set[str]:
    base = base_url.rstrip("/")
    out: set[str] = set()
    for sm in dist.glob("sitemap*.xml"):
        for loc in _LOC.findall(sm.read_text(encoding="utf-8")):
            out.add(loc[len(base) :] if loc.startswith(base) else loc)
    return out


def check_dist(ws: Workspace, dist: Path, loaded: Loaded | None = None) -> list[str]:
    problems: list[str] = []
    if not dist.is_dir():
        return [f"{dist} が無い。先に build を実行する"]
    sources, munis = loaded or load(ws)
    in_sitemap = _sitemap_paths(dist, ws.site.base_url)

    def _published_listings(muni_path: str) -> list[str]:
        """インデックスできる物件ページと、サイトマップに載っている物件 URL。"""
        found = [
            "/" + p.parent.relative_to(dist).as_posix() + "/"
            for p in _listing_pages(dist, muni_path)
            if _indexable(p)
        ]
        prefix = "/" + muni_path
        found += [u for u in in_sitemap if u.startswith(prefix) and u != prefix]
        return sorted(set(found))

    # 1. 物件ページを巡回していない情報源の物件
    for m in munis:
        if crawls_listings(sources[m.id]):
            continue
        for url in _published_listings(m.path):
            problems.append(
                f"{url}: {m.prefecture}{m.name} は物件ページを巡回していないのに、"
                "物件ページが公開されている"
            )

    # 2. 人が固定した扱い
    for ov in _overrides(ws):
        code = str(ov.get("code"))
        name = f"{ov.get('prefecture', '')}{ov.get('name', '')}"
        if ov.get("link_only"):
            for m in (m for m in munis if m.code == code):
                for url in _published_listings(m.path):
                    problems.append(
                        f"{url}: {name} は link_only と決めたのに、物件ページが公開されている"
                    )
        if ov.get("no_website"):
            for d in dist.glob("*/*/"):
                if d.name == code or d.name.startswith(f"{code}-"):
                    problems.append(
                        f"/{d.relative_to(dist).as_posix()}/: {name} は no_website"
                        "（サイトに載せない）なのにページがある"
                    )

    # 3. 取り下げ依頼
    hidden = hidden_paths(ws)
    if hidden:
        for m in munis:
            for page in _listing_pages(dist, m.path):
                url = "/" + page.parent.relative_to(dist).as_posix() + "/"
                if is_hidden(url, hidden) and _indexable(page):
                    problems.append(
                        f"{url}: 取り下げ依頼の対象なのに、インデックスできる形で公開されている"
                    )
        for url in in_sitemap:
            if url.count("/") >= 4 and is_hidden(url, hidden):  # /<県>/<市町村>/<物件>/
                problems.append(f"{url}: 取り下げ依頼の対象なのに、サイトマップに載っている")

    # 4. 公言している巡回数
    top = dist / "index.html"
    crawled = sum(1 for m in munis if m.crawled)
    if top.is_file():
        claim = _CRAWLED_CLAIM.search(top.read_text(encoding="utf-8"))
        if claim is None:
            problems.append(
                "index.html: 「全国 N 市町村の空き家バンクを毎日巡回」の文が見つからない"
            )
        elif int(claim.group(1).replace(",", "")) != crawled:
            problems.append(
                f"index.html: 巡回している市町村数を {claim.group(1)} と書いているが、"
                f"巡回中（bank_status=available）は {crawled}"
            )
    return problems


def check(ws: Workspace, dist: Path) -> tuple[list[str], str]:
    """問題の一覧と、通ったときの要約 1 行を返す。"""
    loaded = load(ws)
    problems = list(dict.fromkeys(check_data(ws, loaded) + check_dist(ws, dist, loaded)))
    sources, munis = loaded
    retired_pages = sum(
        1
        for m in munis
        if not crawls_listings(sources[m.id])
        for p in _listing_pages(dist, m.path)
        if not _indexable(p)
    )
    summary = (
        f"巡回中 {sum(1 for m in munis if m.crawled)} 市町村／"
        f"固定した扱い {len(_overrides(ws))} 件／"
        f"取り下げ {len(hidden_paths(ws))} 件／掲載終了（noindex）{retired_pages} ページ"
    )
    return problems, summary
