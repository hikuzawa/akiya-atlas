"""所在地は「市町村＋大字・地区名」まで。番地・号・建物名をデータにも表示にも残さない方針の検査。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from sitemill.build.site import BuildError
from sitemill.parse.jp.address import has_street_number

from akiya_atlas.service import sanitize_address_fields

REPO = Path(__file__).resolve().parents[1]
LOT_LIKE = re.compile(r"(?<![\d.])\d{1,5}-\d{1,4}(?![\d.㎡m])")


def _addr(value: str) -> dict:
    return {"value": value, "quote": value, "status": "parsed", "note": None}


def test_sanitize_removes_street_number_from_address_title_summary() -> None:
    content = {
        "address": _addr("東御市鞍掛593-1"),
        "title": "東御市鞍掛593-1の住宅",
        "summary": "所在地は東御市鞍掛593-1。畑付き。",
    }
    assert sanitize_address_fields(content)
    assert (
        content["address"]["value"] == "東御市鞍掛" and content["address"]["quote"] == "東御市鞍掛"
    )
    assert content["address"]["note"] == "street_number_removed"
    assert "593-1" not in content["title"] and "593-1" not in content["summary"]
    assert not sanitize_address_fields(content)  # 冪等

    only_number = {"address": _addr("123-4")}
    assert sanitize_address_fields(only_number)
    assert only_number["address"]["status"] == "unparsed"
    assert not sanitize_address_fields({"address": _addr("飯山市大字飯山")})


def test_committed_records_keep_no_street_numbers() -> None:
    checked = 0
    for path in sorted((REPO / "data" / "records").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            addr = row.get("address") or {}
            for v in (addr.get("value"), addr.get("quote")):
                assert not (v and has_street_number(str(v))), (path.name, row["record_id"], v)
            for key in ("title", "summary"):
                text = row.get(key) or ""
                assert not LOT_LIKE.search(text), (path.name, row["record_id"], key, text)
            checked += 1
    assert checked > 0, "レコードが無い（抽出後に実行する）"


def test_build_refuses_records_with_street_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sitemill.models import FieldStatus, FieldValue
    from sitemill.settings import Workspace

    from akiya_atlas.data import Dataset
    from akiya_atlas.pages import ensure_no_street_numbers
    from akiya_atlas.schema import Listing

    ls = Listing(
        record_id="x",
        source_id="s",
        municipality_code="202193",
        listing_no="1",
        source_url="https://x/",
        address=FieldValue(
            value="東御市鞍掛593-1", quote="東御市鞍掛593-1", status=FieldStatus.parsed
        ),
    )
    from sitemill.diff.state import CrawlState

    ds = Dataset(sources=[], municipalities=[], listings=[ls], state=CrawlState())
    with pytest.raises(BuildError, match="番地"):
        ensure_no_street_numbers(ds)
    del monkeypatch, Workspace, tmp_path
