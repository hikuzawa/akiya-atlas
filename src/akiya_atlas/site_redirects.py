"""サイト全体のリダイレクト。www → apex の 301 を Cloudflare Pages の _redirects に出す。

Pages の _redirects はホスト付きの source（ドメイン単位のリダイレクト）を受け付ける:
  www.akiya-atlas.com/* https://akiya-atlas.com/:splat 301
両ホストを Pages プロジェクトのカスタムドメインに追加した状態で有効になる。
ASP 向けの /go/<id> は affiliates.redirects() が持つ（このモジュールは扱わない）。
"""

from __future__ import annotations

from urllib.parse import urlsplit

from sitemill.models import Redirect


def www_to_apex(base_url: str) -> list[Redirect]:
    """base_url が apex ドメインなら www.<apex>/* → base_url/:splat の 301 を 1 本返す。

    base_url 自体が www 付き、または pages.dev（www が存在しない）のときは何も出さない。
    """
    host = urlsplit(base_url).netloc.lower()
    if not host or host.startswith("www.") or host.endswith(".pages.dev"):
        return []
    return [
        Redirect(
            from_path=f"www.{host}/*",
            to_url=f"{base_url.rstrip('/')}/:splat",
            status=301,
        )
    ]
