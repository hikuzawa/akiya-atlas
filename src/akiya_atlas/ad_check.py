"""広告掲載の検査と掲載 URL の一覧（ADR 0010）。

`affiliates.py` の宣言（どの案件をどの枠に出すか）と `dist` の実物を突き合わせる。
1 件でも食い違えばビルドを止める。`uv run akiya-atlas ad-check` で手元でも同じ検査ができ、
CI では build の直後に走る（`.github/workflows/pipeline.yml`）。

検査する内容:
1. 広告リンクを含むページに広告表記（`data-ad-notice`）がある
2. 広告表記が最初の広告リンクより前、かつ本文の冒頭にある（ファーストビューで見える位置の近似）
3. 広告リンクは `/go/<offer>/<placement>/` だけ。ASP ホストへの直リンクは転送ページ以外に無い
4. 広告リンクの rel に sponsored が入っている
5. 宣言した枠に実際にリンクが出ていて、転送ページが生成されている（逆に未宣言の枠が無い）
6. `_redirects` の `/go/` の宛先ホストが ASP の許可ホストに一致する
7. 掲載ページに ASP の禁止表現が出ていない
"""

from __future__ import annotations

import re
from pathlib import Path

from akiya_atlas import affiliates
from akiya_atlas.affiliates import Offer

# 本文の冒頭とみなす幅。/owners/ では見出しとリード文の直後に表記が来る位置。
# ここを超えると、スクロールしないと広告表記が見えない恐れがあるとみなす
FIRST_VIEW_CHARS = 1200

_A_TAG = re.compile(r"""<a\b[^>]*>""", re.I)
_HREF = re.compile(r"""\bhref=["']([^"']*)["']""", re.I)
_REL = re.compile(r"""\brel=["']([^"']*)["']""", re.I)
_MAIN = re.compile(r"""<main\b[^>]*>""", re.I)
_NOTICE = re.compile(r"""<[^>]*\bdata-ad-notice\b[^>]*>""", re.I)
_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<script\b.*?</script>", re.I | re.S)
# href の先頭として絶対に現れない印。計測 URL が未設定の案件を素通しにしないために使う
_NO_MATCH = "urn:no-match"


def _visible_text(html: str) -> str:
    return _TAG.sub(" ", _SCRIPT.sub(" ", html))


def _links(html: str, *, prefixes: tuple[str, ...]) -> list[tuple[int, str, str]]:
    """(位置, href, rel) の一覧。href が prefixes のどれかで始まるものだけを返す。"""
    out = []
    for m in _A_TAG.finditer(html):
        tag = m.group(0)
        href_m = _HREF.search(tag)
        if not href_m or not href_m.group(1).startswith(prefixes):
            continue
        rel_m = _REL.search(tag)
        out.append((m.start(), href_m.group(1), rel_m.group(1) if rel_m else ""))
    return out


def _ad_links(html: str, target: affiliates.GoTarget | None = None) -> list[tuple[int, str, str]]:
    """そのページの広告リンク。転送ページでは ASP の計測 URL そのものが広告リンクになる。"""
    prefixes = ("/go/",) if target is None else ("/go/", target.offer.url or _NO_MATCH)
    return _links(html, prefixes=prefixes)


def _html_files(dist: Path) -> dict[str, str]:
    return {
        p.relative_to(dist).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(dist.rglob("*.html"))
    }


def _url_path(rel: str) -> str:
    p = "/" + rel
    return p[: -len("index.html")] if p.endswith("index.html") else p


def _check_page(rel: str, html: str, go: affiliates.GoTarget | None = None) -> list[str]:
    """1 ページ分。go を渡すとそのページを転送ページとして見る。"""
    problems: list[str] = []
    links = _ad_links(html, go)
    if not links:
        if _NOTICE.search(html):
            problems.append(f"{rel}: 広告リンクが無いのに広告表記が出ている")
        return problems

    declared = {t.url_path: t for t in affiliates.go_targets()}
    asps = set()
    for _pos, href, rel_attr in links:
        target = declared.get(href) if href.startswith("/go/") else go
        if target is None:
            problems.append(f"{rel}: 宣言していない広告リンク {href}")
            continue
        asp = affiliates.asp_of(target.offer)
        if asp is None:
            problems.append(f"{rel}: {target.offer.id} に ASP が設定されていない")
            continue
        asps.add(asp)
        if asp.requires_sponsored_rel and "sponsored" not in rel_attr.split():
            problems.append(f'{rel}: {href} の rel に sponsored が無い（rel="{rel_attr}"）')

    if any(a.requires_ad_notice for a in asps):
        notice = _NOTICE.search(html)
        first_link = min(pos for pos, _, _ in links)
        if not notice:
            problems.append(f"{rel}: 広告リンクがあるのに広告表記（data-ad-notice）が無い")
        else:
            main = _MAIN.search(html)
            main_at = main.end() if main else 0
            if notice.start() > first_link:
                problems.append(f"{rel}: 広告表記が最初の広告リンクより後ろにある")
            elif notice.start() - main_at > FIRST_VIEW_CHARS:
                problems.append(
                    f"{rel}: 広告表記が本文の冒頭から {notice.start() - main_at} 文字目にあり、"
                    f"ファーストビューで見えない恐れがある（上限 {FIRST_VIEW_CHARS}）"
                )

    text = _visible_text(html)
    for asp in asps:
        for phrase in asp.forbidden_phrases:
            if phrase in text:
                problems.append(f"{rel}: {asp.name} の禁止表現「{phrase}」が出ている")

    return problems


def _check_direct_links(files: dict[str, str]) -> list[str]:
    """転送ページ以外に ASP ホストが現れていないか（広告リンク以外の場所も見る）。"""
    problems: list[str] = []
    hosts = {
        host
        for o in affiliates.active_offers()
        if (asp := affiliates.asp_of(o))
        for host in asp.allowed_link_hosts
    }
    for rel, html in files.items():
        if rel.startswith("go/"):
            continue
        for host in hosts:
            if host in html:
                problems.append(f"{rel}: ASP への直リンク（{host}）がある。/go/ を経由する")
    return problems


def _check_coverage(dist: Path, files: dict[str, str]) -> list[str]:
    """宣言した枠が実際に出ているか、転送ページが生成されているか。"""
    problems: list[str] = []
    by_path = {_url_path(rel): html for rel, html in files.items()}
    for target in affiliates.go_targets():
        page = by_path.get(target.placement.page_path)
        if page is None:
            problems.append(
                f"{target.placement.page_path} が生成されていない"
                f"（{target.offer.id} の枠 {target.placement.id} の掲載先）"
            )
        elif target.url_path not in page:
            problems.append(
                f"{target.placement.page_path}: {target.offer.id} の枠 "
                f"{target.placement.id} のリンクが出ていない"
            )
        go_file = dist / target.url_path.strip("/") / "index.html"
        if not go_file.is_file():
            problems.append(f"転送ページ {target.url_path} が生成されていない")
            continue
        go_html = go_file.read_text(encoding="utf-8")
        if target.offer.url and target.offer.url not in go_html:
            problems.append(f"転送ページ {target.url_path} に計測 URL が入っていない")
        if "noindex" not in go_html:
            problems.append(f"転送ページ {target.url_path} が noindex になっていない")
    return problems


def _check_redirects(dist: Path) -> list[str]:
    problems: list[str] = []
    path = dist / "_redirects"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    seen: set[str] = set()
    for line in lines:
        parts = line.split()
        if len(parts) < 2 or not parts[0].startswith("/go/"):
            continue
        seen.add(parts[0])
        offer = next((o for o in affiliates.OFFERS if o.path == parts[0]), None)
        if offer is None:
            problems.append(f"_redirects: 未定義の案件への転送 {parts[0]}")
            continue
        asp = affiliates.asp_of(offer)
        host = affiliates.link_host(parts[1])
        if asp is None:
            problems.append(f"_redirects: {offer.id} に ASP が設定されていない")
        elif host not in asp.allowed_link_hosts:
            problems.append(
                f"_redirects: {parts[0]} の宛先ホスト {host} が {asp.name} の"
                f"許可ホスト {asp.allowed_link_hosts} に無い"
            )
    for offer in affiliates.active_offers():
        if offer.path not in seen:
            problems.append(f"_redirects: {offer.path} が出ていない")
    return problems


def check(dist: Path) -> tuple[list[str], str]:
    """問題の一覧と、通ったときの要約 1 行を返す。"""
    if not dist.is_dir():
        return [f"{dist} が無い。先に build を実行する"], ""
    files = _html_files(dist)
    go_pages = {t.url_path.strip("/") + "/index.html": t for t in affiliates.go_targets()}
    problems: list[str] = []
    pages_with_ads = 0
    for rel, html in sorted(files.items()):
        go = go_pages.get(rel)
        if _ad_links(html, go):
            pages_with_ads += 1
        problems += _check_page(rel, html, go)
    problems += _check_direct_links(files)
    problems += _check_coverage(dist, files)
    problems += _check_redirects(dist)
    # 同じ問題が 2 経路から出ることがあるので、順序を保ったまま重複を落とす
    problems = list(dict.fromkeys(problems))
    active = affiliates.active_offers()
    summary = (
        f"有効な案件 {len(active)} 件（{', '.join(o.id for o in active) or 'なし'}）／"
        f"転送ページ {len(affiliates.go_targets())} 枚／広告のあるページ {pages_with_ads} 枚"
    )
    return problems, summary


def ad_urls(dist: Path, base_url: str, *, offer_id: str | None = None) -> list[tuple[Offer, str]]:
    """ASP に届け出る掲載 URL の一覧。(案件, 絶対 URL) を案件ごとにまとめて返す。

    掲載ページ（広告リンクを置いたページ）と転送ページの両方を出す。転送ページは
    広告リンクそのものなので、届け出先によっては掲載ページだけでよい（runbook に注記）。
    """
    base = base_url.rstrip("/")
    files = _html_files(dist) if dist.is_dir() else {}
    by_path = {_url_path(rel): html for rel, html in files.items()}
    out: list[tuple[Offer, str]] = []
    for offer in affiliates.active_offers():
        if offer_id and offer.id != offer_id:
            continue
        paths: list[str] = []
        for target in affiliates.go_targets():
            if target.offer.id != offer.id:
                continue
            page = target.placement.page_path
            if page not in paths and target.url_path in by_path.get(page, ""):
                paths.append(page)
            paths.append(target.url_path)
        for p in dict.fromkeys(paths):
            out.append((offer, base + p))
    return out
