"""物件番号の囲み括弧と、保存済みレコードの id の付け直し。

砂川市は一覧で「<R8-8>」と書いている。抽出が 9/15 から括弧ごと引用するようになり、同じ物件が
「R8-8」と「<R8-8>」の 2 件に割れて、本番に 42 件が二重に載った。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from sitemill.settings import Workspace

from akiya_atlas import renormalize
from akiya_atlas.data import _make_slugs_unique
from akiya_atlas.schema import Listing, listing_slug, normalize_listing_no, record_id_for

REPO = Path(__file__).resolve().parents[1]


def _old_id(source_id: str, no: str) -> str:
    """括弧を外す前の正規化で作られていた id（保存済みのデータにはこれが入っている）。"""
    return hashlib.sha1(f"{source_id}:{no}".encode()).hexdigest()[:16]


def test_wrapping_brackets_are_not_part_of_the_number() -> None:
    assert normalize_listing_no("<R8-8>") == "R8-8"
    assert normalize_listing_no("(2606-5)") == "2606-5"
    assert normalize_listing_no("（R8-8）") == "R8-8"  # 全角も NFKC で同じ
    assert normalize_listing_no("< R8-8 >") == "R8-8"
    assert normalize_listing_no("2506-3(1)") == "2506-3(1)"  # 途中の括弧は番号の一部
    assert normalize_listing_no("【164】") == "【164】"  # 外すと公開済みの URL が変わる
    assert record_id_for("s", "<R8-8>") == record_id_for("s", "R8-8")


def test_case_does_not_split_a_listing() -> None:
    """飯山市は同じ物件が「A358」と「a358」で取り込まれ、2 件ずつ載った。"""
    assert record_id_for("nagano-iiyama", "a358") == record_id_for("nagano-iiyama", "A358")


def test_published_urls_do_not_move() -> None:
    """括弧を外しても、大小文字をそろえても、URL の元になるスラグは変わらない。"""
    assert listing_slug("<R8-8>", "x") == listing_slug("R8-8", "x") == "r8-8"
    assert listing_slug("(2606-5)", "x") == "2606-5"
    assert listing_slug("【164】", "x").startswith("164-")  # 以前どおり印つき
    # 日本語を含む番号の印は、大文字にそろえる前の綴りから作る（そろえると URL が動く）
    mark = hashlib.sha1("地No.5".encode()).hexdigest()[:4]
    assert listing_slug("地No.5", "x") == f"no-5-{mark}"


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    shutil.copy(REPO / "site.toml", tmp_path / "site.toml")
    (tmp_path / "data" / "records").mkdir(parents=True)
    return Workspace.open(tmp_path)


def _row(source: str, no: str, rid: str, **kw: object) -> dict:
    return {
        "record_id": rid,
        "source_id": source,
        "municipality_code": "012262",
        "listing_no": no,
        "source_url": "https://www.city.sunagawa.hokkaido.jp/akiya.html",
        "page_kind": "listing_index",
        "status": "active",
        "history": [{"at": kw.get("first_seen_at"), "event": "created"}],
        **kw,
    }


def _price(value: int) -> dict:
    return {"value": value, "quote": f"{value}万円", "status": "parsed"}


def _write(ws: Workspace, source: str, rows: list[dict]) -> Path:
    path = ws.data_dir / "records" / f"{source}.jsonl"
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    return path


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_a_split_listing_becomes_one_record_under_its_published_number(ws: Workspace) -> None:
    src = "hokkaido-012262"
    plain = _row(
        src, "R8-8", _old_id(src, "R8-8"),
        first_seen_at="2026-09-11T02:37:42+00:00", last_seen_at="2026-09-15T11:18:00+00:00",
        price=_price(100),
    )  # fmt: skip
    bracketed = _row(
        src, "<R8-8>", _old_id(src, "<R8-8>"),
        first_seen_at="2026-09-15T11:18:00+00:00", last_seen_at="2026-09-16T21:00:00+00:00",
        price=_price(120),
    )  # fmt: skip
    path = _write(ws, src, [plain, bracketed])

    plan = renormalize.plan(ws)
    assert plan.merged == {src: 1} and not plan.renamed
    renormalize.apply(plan)

    (row,) = _read(path)
    assert row["record_id"] == record_id_for(src, "R8-8")
    assert row["listing_no"] == "R8-8"  # 公開済みの URL の元
    assert row["first_seen_at"] == "2026-09-11T02:37:42+00:00"
    assert row["last_seen_at"] == "2026-09-16T21:00:00+00:00"
    assert row["price"]["value"] == 120  # 新しいほうの値
    assert row["history"][-1]["event"] == "merged"
    assert row["history"][-1]["from"] == _old_id(src, "<R8-8>")


def test_a_lone_bracketed_number_keeps_its_record_and_duplicates_follow(ws: Workspace) -> None:
    fukui, other = "fukui-182087", "fukui-999999"
    old = _old_id(fukui, "(2606-5)")
    path = _write(ws, fukui, [_row(fukui, "(2606-5)", old, first_seen_at="2026-09-11")])
    other_path = _write(ws, other, [_row(other, "A-1", _old_id(other, "A-1"), duplicate_of=old)])

    plan = renormalize.plan(ws)
    assert plan.renamed == {fukui: 1} and not plan.merged
    renormalize.apply(plan)

    (row,) = _read(path)
    assert row["record_id"] == record_id_for(fukui, "(2606-5)")
    assert row["listing_no"] == "(2606-5)"
    assert _read(other_path)[0]["duplicate_of"] == row["record_id"]
    assert renormalize.plan(ws).empty  # 2 回目は何もしない


def test_an_active_listing_keeps_the_plain_url_over_an_older_ended_one() -> None:
    def _ls(no: str, status: str, first: str) -> Listing:
        return Listing(
            record_id=_old_id("s", no), source_id="s", municipality_code="012262",
            listing_no=no, source_url="https://example.invalid/", status=status,
            first_seen_at=first,
        )  # fmt: skip

    ended = _ls("H30.15", "retired", "2026-09-01T00:00:00+00:00")
    active = _ls("H30-15", "active", "2026-09-12T00:00:00+00:00")
    _make_slugs_unique([ended, active])
    assert active.slug == "h30-15"
    assert ended.slug.startswith("h30-15-")
