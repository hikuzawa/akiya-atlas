"""サイト全体のリダイレクト。www と pages.dev から apex への 301 を _redirects に出す。

Pages の _redirects はホスト付きの source（ドメイン単位のリダイレクト）を受け付ける:
  www.akiya-atlas.com/* https://akiya-atlas.com/:splat 301
  akiya-atlas-asb.pages.dev/* https://akiya-atlas.com/:splat 301
どちらも、そのホストが Pages プロジェクト（カスタムドメインか既定ホスト）に届く前提で有効になる。
ASP 向けの /go/<id> は affiliates.redirects() が持つ（このモジュールは扱わない）。
"""

from __future__ import annotations

from urllib.parse import urlsplit

from sitemill.models import Redirect

# Pages プロジェクトの既定ホスト。akiya-atlas.pages.dev は第三者が使用中のため -asb 付き
PAGES_DEV_HOST = "akiya-atlas-asb.pages.dev"


def _apex_host(base_url: str) -> str | None:
    """base_url が apex ドメインならそのホストを返す。www 付きや pages.dev なら None。"""
    host = urlsplit(base_url).netloc.lower()
    if not host or host.startswith("www.") or host.endswith(".pages.dev"):
        return None
    return host


def _to_apex(from_host: str, base_url: str) -> Redirect:
    return Redirect(from_path=f"{from_host}/*", to_url=f"{base_url.rstrip('/')}/:splat", status=301)


def www_to_apex(base_url: str) -> list[Redirect]:
    """base_url が apex ドメインなら www.<apex>/* → base_url/:splat の 301 を 1 本返す。"""
    host = _apex_host(base_url)
    return [] if host is None else [_to_apex(f"www.{host}", base_url)]


def pages_dev_to_apex(base_url: str, pages_host: str = PAGES_DEV_HOST) -> list[Redirect]:
    """base_url が apex ドメインなら <pages_host>/* → base_url/:splat の 301 を 1 本返す。"""
    return [] if _apex_host(base_url) is None else [_to_apex(pages_host, base_url)]


def for_site(base_url: str) -> list[Redirect]:
    """www → apex と pages.dev → apex の一式。base_url が暫定ホスト（pages.dev）なら空。"""
    return [*www_to_apex(base_url), *pages_dev_to_apex(base_url)]
