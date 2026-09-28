"""補助制度の確認日を、出どころのページを読めた日まで進める（2026-09-28）。

確認日を抽出した日にしていたので、中身が変わらず抽出し直さないページの制度は、毎晩読めて
いても確認日が動かず、180 日後に一斉に「情報が古い可能性」になるところだった（09-14〜15 の
全国収集の 2,638 件が 2027-03-14〜15 に）。japan-open-today で起きた鮮度の障害と同じ形。
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sitemill.diff.state import CrawlState
from sitemill.models import Source
from sitemill.settings import Workspace

from akiya_atlas.schema import Subsidy
from akiya_atlas.subsidies import mark_read, subsidies_path

REPO = Path(__file__).resolve().parents[1]
PAGE = "https://www.city.kakuu.nagano.jp/kurashi/akiya-hojo.html"


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    shutil.copy(REPO / "site.toml", tmp_path / "site.toml")
    for d in ("data/sources", "data/state", "data/subsidies"):
        (tmp_path / d).mkdir(parents=True)
    return Workspace.open(tmp_path)


def _subsidy(ws: Workspace, name: str, page: str, checked_on: str) -> None:
    row = {
        "name": name,
        "kind": "改修",
        "scope": "空き家",
        "url": page,
        "checked_on": checked_on,
        "source_id": "nagano-209999",
        "provenance": {"source_url": page},
    }
    path = subsidies_path(ws, "nagano-209999")
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"record_id": name, "status": "active", **row}, ensure_ascii=False))
        f.write("\n")


def _state(**pages: dict) -> CrawlState:
    state = CrawlState()
    for url, fields in pages.items():
        st = state.get_or_create(url, "nagano-209999", "subsidy")
        for key, value in fields.items():
            setattr(st, key, value)
    return state


def _checked(ws: Workspace) -> dict[str, str]:
    path = subsidies_path(ws, "nagano-209999")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {r["name"]: r["checked_on"] for r in rows}


def _source() -> Source:
    return Source.model_validate(
        {
            "id": "nagano-209999",
            "name": "架空市",
            "operator": "架空市",
            "operator_kind": "municipality",
            "official_url": "https://www.city.kakuu.nagano.jp/",
            "policy": "crawl",
            "operator_evidence": {
                "quote": "公式ドメイン www.city.kakuu.nagano.jp",
                "url": "https://www.city.kakuu.nagano.jp/",
                "checked_on": "2026-09-11",
            },
            "pages": [{"url": PAGE, "kind": "subsidy"}],
        }
    )


READ = datetime(2026, 9, 27, 21, 30, tzinfo=UTC)  # 日本時間 09-28 に読めた


def test_the_check_date_follows_the_last_read_of_an_unchanged_page(ws: Workspace) -> None:
    _subsidy(ws, "空き家改修補助", PAGE, "2026-09-15")
    state = _state(
        **{PAGE: {"fetched_at": READ, "content_hash": "h1", "extracted_hash": "h1", "error": None}}
    )
    assert mark_read(ws, [_source()], state) == 1
    assert _checked(ws) == {"空き家改修補助": "2026-09-28"}
    # 180 日たっても、読めている限り「情報が古い可能性」にはならない
    row = Subsidy(name="x", kind="改修", url=PAGE, checked_on=date(2026, 9, 28))
    assert not row.stale


@pytest.mark.parametrize(
    ("why", "fields"),
    [
        (
            "取得に失敗している",
            {"fetched_at": READ, "content_hash": "h1", "extracted_hash": "h1", "error": "HTTP 404"},
        ),
        (
            "中身が変わってまだ抽出していない",
            {"fetched_at": READ, "content_hash": "h2", "extracted_hash": "h1", "error": None},
        ),
        (
            "読めたのが確認日より前",
            {
                "fetched_at": datetime(2026, 9, 10, tzinfo=UTC),
                "content_hash": "h1",
                "extracted_hash": "h1",
                "error": None,
            },
        ),
    ],
)
def test_the_check_date_does_not_move_without_a_clean_read(
    ws: Workspace, why: str, fields: dict
) -> None:
    _subsidy(ws, "空き家改修補助", PAGE, "2026-09-15")
    state = _state(**{PAGE: fields})
    assert mark_read(ws, [_source()], state) == 0, why
    assert _checked(ws) == {"空き家改修補助": "2026-09-15"}


def test_a_page_no_longer_read_is_left_to_age(ws: Workspace) -> None:
    """巡回先から外れたページの制度は、読めないので進めない（古くなったことが表示に出る）。"""
    _subsidy(ws, "空き家改修補助", PAGE, "2026-09-15")
    assert mark_read(ws, [_source()], CrawlState()) == 0
    assert _checked(ws) == {"空き家改修補助": "2026-09-15"}
