"""同じ物件が 2 つの source に出たときの扱いのテスト（ADR 0009）。ネットワーク不要。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sitemill.settings import Workspace

from akiya_atlas.data import Dataset
from akiya_atlas.service import mark_duplicates

SITE_TOML = """
[site]
id = "akiya-atlas"
name = "空き家アトラス"
base_url = "https://akiya-atlas.com"
service = "akiya_atlas.service:service"
[operator]
name = "準備中"
contact = "準備中"
"""

# 市町村自身のサイト（bank_url のホスト＝公式ドメイン）と、県の横断サイト
SOURCES = """
sources:
  - id: nagano-own
    name: 架空市空き家バンク
    operator: 架空市
    operator_kind: municipality
    policy: crawl
    official_url: https://www.city.kakuu.nagano.jp/
    operator_evidence:
      quote: 公式ドメイン www.city.kakuu.nagano.jp
      url: https://www.city.kakuu.nagano.jp/
      checked_on: 2026-09-12
    pages:
      - url: https://www.city.kakuu.nagano.jp/akiya/
        kind: listing_index
    municipality:
      code: "209001"
      name: 架空市
      prefecture: 長野県
      prefecture_slug: nagano
      slug: "209001"
      bank_url: https://www.city.kakuu.nagano.jp/akiya/
  - id: nagano-portal
    name: 架空市空き家バンク（県の横断サイト）
    operator: 架空市
    operator_kind: municipality
    policy: crawl
    official_url: https://www.city.kakuu.nagano.jp/
    operator_evidence:
      quote: 県の横断サイトに架空市の空き家バンクとして掲載
      url: https://portal.example.jp/kakuu/
      checked_on: 2026-09-12
    pages:
      - url: https://portal.example.jp/kakuu/
        kind: listing_index
    municipality:
      code: "209001"
      name: 架空市
      prefecture: 長野県
      prefecture_slug: nagano
      slug: "209001-portal"
      bank_url: https://portal.example.jp/kakuu/
"""


def _rec(source_id: str, no: str, **fields: object) -> dict:
    base = {
        "record_id": f"{source_id}-{no}",
        "source_id": source_id,
        "municipality_code": "209001",
        "listing_no": no,
        "deal_type": "sale",
        "source_url": f"https://example.invalid/{source_id}/{no}",
        "status": "active",
        "title": f"物件 {no}",
    }
    base.update(fields)
    return base


def _fv(value: object) -> dict:
    return {"value": value, "quote": str(value), "status": "parsed", "note": None}


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    (tmp_path / "site.toml").write_text(SITE_TOML, encoding="utf-8")
    (tmp_path / "data" / "sources").mkdir(parents=True)
    (tmp_path / "data" / "sources" / "nagano.yaml").write_text(SOURCES, encoding="utf-8")
    (tmp_path / "data" / "records").mkdir()
    w = Workspace.open(tmp_path)
    w.ensure_dirs()
    return w


def _write(ws: Workspace, source_id: str, rows: list[dict]) -> None:
    (ws.records_dir / f"{source_id}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )


def test_the_municipal_record_wins_when_the_listing_number_matches(ws: Workspace) -> None:
    _write(ws, "nagano-own", [_rec("nagano-own", "A-12")])
    _write(ws, "nagano-portal", [_rec("nagano-portal", "A-12")])
    counts = mark_duplicates(ws)
    assert counts["groups"] == 1 and counts["hidden"] == 1
    portal = json.loads((ws.records_dir / "nagano-portal.jsonl").read_text(encoding="utf-8"))
    own = json.loads((ws.records_dir / "nagano-own.jsonl").read_text(encoding="utf-8"))
    assert portal["duplicate_of"] == "nagano-own-A-12"
    assert portal["duplicate_reason"] == "listing_no"
    assert "duplicate_of" not in own  # 市町村サイト側は残る
    # 数えるときも出すときも、隠した側は入らない
    ds = Dataset.load(ws)
    muni = next(m for m in ds.municipalities if m.id == "nagano-portal")
    assert ds.listings_for(muni, active_only=True) == []


def test_address_price_and_area_together_identify_one_property(ws: Workspace) -> None:
    facts = {
        "address": _fv("架空市大字みどり"),
        "price": _fv(3_500_000),
        "floor_area_m2": _fv(98.5),
    }
    _write(ws, "nagano-own", [_rec("nagano-own", "12", **facts)])
    _write(ws, "nagano-portal", [_rec("nagano-portal", "P-99", **facts)])
    counts = mark_duplicates(ws)
    assert counts["hidden"] == 1
    portal = json.loads((ws.records_dir / "nagano-portal.jsonl").read_text(encoding="utf-8"))
    assert portal["duplicate_reason"] == "address_price_area"


def test_a_missing_field_means_they_are_different_properties(ws: Workspace) -> None:
    """3 つそろっていないときは束ねない（推測で同一視しない）。"""
    partial = {"address": _fv("架空市大字みどり"), "price": _fv(3_500_000)}
    _write(ws, "nagano-own", [_rec("nagano-own", "12", **partial)])
    _write(ws, "nagano-portal", [_rec("nagano-portal", "P-99", **partial)])
    assert mark_duplicates(ws)["hidden"] == 0


def test_the_flag_is_removed_when_the_other_record_disappears(ws: Workspace) -> None:
    _write(ws, "nagano-own", [_rec("nagano-own", "A-12")])
    _write(ws, "nagano-portal", [_rec("nagano-portal", "A-12")])
    mark_duplicates(ws)
    _write(ws, "nagano-own", [])  # 市町村側が消えた
    counts = mark_duplicates(ws)
    assert counts["restored"] == 1
    portal = json.loads((ws.records_dir / "nagano-portal.jsonl").read_text(encoding="utf-8"))
    assert "duplicate_of" not in portal
