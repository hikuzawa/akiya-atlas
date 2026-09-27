"""サイト内検索の結果と検索画面の URL を見分ける（2026-09-26）。

「検索フォーム型は自動で検索を送らず、リンクのみにする」方針は上書きファイルで自治体ごとに
決めていたが、発見のコードには歯止めが無く、GET の検索結果 URL を 2 件巡回先に採っていた
（常総市の物件一覧 search.php?keyword=空き家、当麻町の補助制度 /search/node?keys=住宅補助）。
検索結果を巡回するのは、検索を自動で送るのと同じこと。発見は候補の段階で外し（expand）、
物件の統合は検索結果から取った内容を詳細ページとして扱わない（service.merge_content）。
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

_SEARCH_PATH = re.compile(
    r"(?:^|/)(?:search|kensaku|site-?search)(?:/|\.(?:php|html?|cgi|aspx?|jsp)|$)", re.I
)
_SEARCH_KEYS = frozenset(
    {
        "keys",
        "keyword",
        "keywords",
        "kw",
        "q",
        "query",
        "s",
        "search",
        "word",
        "searchword",
        "free_word",
        "freeword",
    }
)


def is_site_search_url(url: str) -> bool:
    """サイト内検索の結果か検索画面の URL か。

    パスに `search`（`/search/`・`search.php` など）があるか、検索語の引数
    （`?keys=` `?keyword=` `?q=` `?s=` など）を持つものを検索とみなす。
    一覧のページ送り（`?page=2`）や絞り込み（`?area=1`）は検索ではないので通す。
    """
    parts = urlsplit(url)
    if _SEARCH_PATH.search(parts.path):
        return True
    keys = {k.lower() for k, _ in parse_qsl(parts.query, keep_blank_values=True)}
    return bool(keys & _SEARCH_KEYS)
