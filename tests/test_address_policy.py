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


def test_a_shop_loses_its_honorific_but_a_surname_keeps_it() -> None:
    """「元醤油屋さんの広々物件」で日次のビルドが止まった（2026-09-17、長崎県南島原市）。

    氏名検出は漢字 2〜4 文字＋「さん」を人名とみなす。要約はこちらが書く文なので、店に敬称を
    付けない形にそろえる。ただし 2 文字の「◯屋さん」は姓のことがあるので外さない（土屋・古屋）。
    """
    content = {
        "title": "有家町中須川の元醤油屋さんの広々物件",
        "summary": "有家町中須川にある元醤油屋さんの物件。呉服屋さんの隣。",
    }
    assert sanitize_address_fields(content)
    assert content["title"] == "有家町中須川の元醤油屋の広々物件"
    assert "呉服屋の隣" in content["summary"] and "さん" not in content["summary"]
    assert not sanitize_address_fields(content)  # 冪等

    keep = {"title": "土屋さんの紹介物件", "summary": "パン屋さん近くの土地。"}
    assert not sanitize_address_fields(keep)  # 姓と、漢字 1 文字の店名はそのまま


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


def test_sanitize_never_leaves_a_street_number_behind() -> None:
    """全国分で出てきた書き方。整えたあとの所在地に番地が残らないことを固定する。"""
    tricky = [
        "E棟 新光248番地、F棟 新光252番地1",  # 複数棟の並記
        "砂川市晴見3条北9丁目",  # 北海道の条丁目（地区なので残す）
        "西4条南10丁目",
        "上川郡東川町西5号北44番地",
        "中頸城郡妙高高原町大字田口",
    ]
    for text in tricky:
        content = {"address": _addr(text)}
        sanitize_address_fields(content)
        value = content["address"]["value"]
        assert not (value and has_street_number(str(value))), (text, value)
    # 条丁目はそのまま残る
    grid = {"address": _addr("砂川市晴見3条北9丁目")}
    assert not sanitize_address_fields(grid)
    assert grid["address"]["value"] == "砂川市晴見3条北9丁目"
