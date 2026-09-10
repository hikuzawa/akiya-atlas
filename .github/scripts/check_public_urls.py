"""公開 URL の検査。dist の canonical / og:url / sitemap / robots が base_url を指すか確かめる。

基準は site.toml の [site].base_url。CI のビルド直後に走らせる（.github/workflows/pipeline.yml）。
手元でも `uv run python .github/scripts/check_public_urls.py` で dist を検査できる。
ドメイン切替（pages.dev → akiya-atlas.com）のとき、古いホストが残ったまま配置されるのを止める。
問題が無ければ 0、1 件でもあれば一覧を出して 1 で終了する。標準ライブラリだけで動く。
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

_CANONICAL = re.compile(r"""<link\b[^>]*\brel=["']canonical["'][^>]*>""", re.I)
_OG_URL = re.compile(r"""<meta\b[^>]*\bproperty=["']og:url["'][^>]*>""", re.I)
_NOINDEX = re.compile(r"""<meta\b[^>]*\bname=["']robots["'][^>]*\bnoindex\b""", re.I)
_HREF = re.compile(r"""\bhref=["']([^"']*)["']""", re.I)
_CONTENT = re.compile(r"""\bcontent=["']([^"']*)["']""", re.I)
_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.S)


def _under(url: str, base: str) -> bool:
    return url == base or url.startswith(base + "/")


def check(site_toml: Path, dist: Path, *, expect_base: str | None = None) -> tuple[list[str], str]:
    """問題の一覧と、通ったときの要約 1 行を返す。

    expect_base を渡すと、base_url がその値であることも要求する（本番ドメインの固定）。
    """
    base = str(tomllib.loads(site_toml.read_text(encoding="utf-8"))["site"]["base_url"]).rstrip("/")
    problems: list[str] = []
    if expect_base and base != expect_base.rstrip("/"):
        problems.append(f"site.toml の base_url が本番ドメイン {expect_base} ではない: {base!r}")
    if not re.match(r"^https://[^/]+$", base):
        problems.append(f"site.toml の base_url が https://ホスト の形ではない: {base!r}")
    if not dist.is_dir():
        problems.append(f"{dist} が無い。先に build を実行する")
        return problems, ""

    pages = canonicals = og_urls = 0
    for path in sorted(dist.rglob("*.html")):
        rel = path.relative_to(dist).as_posix()
        html = path.read_text(encoding="utf-8")
        pages += 1
        tags = _CANONICAL.findall(html)
        if not tags and not _NOINDEX.search(html):
            problems.append(f"{rel}: canonical が無い")
        for tag in tags:
            m = _HREF.search(tag)
            href = m.group(1) if m else ""
            canonicals += 1
            if not _under(href, base):
                problems.append(f"{rel}: canonical が base_url 以外を指す: {href!r}")
        for tag in _OG_URL.findall(html):
            m = _CONTENT.search(tag)
            url = m.group(1) if m else ""
            og_urls += 1
            if not _under(url, base):
                problems.append(f"{rel}: og:url が base_url 以外を指す: {url!r}")

    sitemap = dist / "sitemap.xml"
    locs: list[str] = []
    if sitemap.is_file():
        locs = _LOC.findall(sitemap.read_text(encoding="utf-8"))
        if not locs:
            problems.append("sitemap.xml に <loc> が 1 つも無い")
        for loc in locs:
            if not _under(loc, base):
                problems.append(f"sitemap.xml の <loc> が base_url 以外を指す: {loc!r}")
    else:
        problems.append("sitemap.xml が無い")

    robots = dist / "robots.txt"
    if robots.is_file():
        lines = [
            ln.split(":", 1)[1].strip()
            for ln in robots.read_text(encoding="utf-8").splitlines()
            if ln.lower().startswith("sitemap:")
        ]
        if not lines:
            problems.append("robots.txt に Sitemap 行が無い")
        for url in lines:
            if url != f"{base}/sitemap.xml":
                problems.append(f"robots.txt の Sitemap が {base}/sitemap.xml ではない: {url!r}")
    else:
        problems.append("robots.txt が無い")

    summary = (
        f"base_url={base} pages={pages} canonical={canonicals} og:url={og_urls} "
        f"sitemap_urls={len(locs)} robots=ok"
    )
    return problems, summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--site", default="site.toml", type=Path)
    ap.add_argument("--dist", default="dist", type=Path)
    ap.add_argument(
        "--expect-base",
        default=None,
        help="base_url がこの値であることも要求する（本番ドメインの固定。例: https://akiya-atlas.com）",
    )
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows の cp932 端末でも落ちない
    problems, summary = check(args.site, args.dist, expect_base=args.expect_base)
    if problems:
        print(f"公開 URL の検査に失敗（{len(problems)} 件）:")
        for p in problems[:50]:
            print(f"  - {p}")
        if len(problems) > 50:
            print(f"  ... 他 {len(problems) - 50} 件")
        return 1
    print(f"公開 URL の検査 OK: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
