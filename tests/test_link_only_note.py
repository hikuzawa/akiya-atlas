"""物件一覧を巡回しないと決めた自治体の案内文（municipal_overrides.json の page_note）。

柳井市（PDF）や中間市（検索フォーム）に「物件ページを見つけられませんでした」と出ていた。
実際には見つけていて、載せない理由まで分かっている。画面にはその理由を出す。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from sitemill.settings import Workspace

from akiya_atlas import expand

REPO = Path(__file__).resolve().parents[1]
CODE = "209999"
PDF_NOTE = (
    "物件一覧は PDF で公開されています。"
    "本サイトでは PDF の内容を要約しないため、公式ページでご確認ください。"
)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    shutil.copy(REPO / "site.toml", tmp_path / "site.toml")
    for d in ("data/sources", "data/runs", "data/reference", "data/review"):
        (tmp_path / d).mkdir(parents=True)
    return Workspace.open(tmp_path)


def _override(ws: Workspace, **extra: object) -> None:
    row = {"code": CODE, "name": "架空市", "prefecture": "長野県", "link_only": True, **extra}
    path = ws.root / "data" / "reference" / "municipal_overrides.json"
    path.write_text(json.dumps({"municipalities": [row]}, ensure_ascii=False), encoding="utf-8")


def _row(subsidy_urls: list[str] | None = None) -> dict:
    row = {
        "code": CODE,
        "name": "架空市",
        "name_kana": "カクウシ",
        "prefecture": "長野県",
        "prefecture_slug": "nagano",
        "official_url": "www.city.kakuu.nagano.jp",
        "bank_url": "https://www.city.kakuu.nagano.jp/akiya/ichiran.html",
        "page_class": None,
        "confidence": 0.9,
        "operator_kind": "municipality",
        "evidence_quote": "公式ドメイン",
        "evidence_url": "https://www.city.kakuu.nagano.jp/",
        "cross_linked": False,
        "policy": "link_only",
        "reason": "物件一覧を PDF でのみ公開している",
        "proposed_action": "承認",
        "external_links": [],
    }
    if subsidy_urls:
        row["subsidy_urls"] = subsidy_urls
    return row


def _muni(ws: Workspace) -> dict:
    data = yaml.safe_load((ws.sources_dir / "nagano-auto.yaml").read_text(encoding="utf-8"))
    (entry,) = data["sources"]
    return entry


def test_the_reason_replaces_the_not_found_note(ws: Workspace) -> None:
    _override(ws, page_note=PDF_NOTE)
    expand._write_rows(ws, "nagano", "長野県", [_row()])
    entry = _muni(ws)
    assert entry["municipality"]["bank_note"] == PDF_NOTE
    assert "見つけられませんでした" not in entry["municipality"]["bank_note"]
    # 主ボタンのリンク先は、上書きで決めた公式の一覧ページのまま
    assert entry["municipality"]["bank_url"].endswith("/akiya/ichiran.html")


def test_the_reason_stays_when_subsidy_pages_are_crawled(ws: Workspace) -> None:
    """補助制度のページを巡回して policy が crawl になっても、物件の案内文は変わらない。"""
    _override(ws, page_note=PDF_NOTE)
    url = "https://www.city.kakuu.nagano.jp/kurashi/hojo.html"
    expand._write_rows(ws, "nagano", "長野県", [_row(subsidy_urls=[url])])
    entry = _muni(ws)
    assert entry["policy"] == "crawl"
    assert [p["kind"] for p in entry["pages"]] == ["subsidy"]
    assert entry["municipality"]["bank_note"] == PDF_NOTE


def test_without_a_page_note_the_default_note_is_kept(ws: Workspace) -> None:
    _override(ws)
    expand._write_rows(ws, "nagano", "長野県", [_row()])
    assert "見つけられませんでした" in _muni(ws)["municipality"]["bank_note"]
