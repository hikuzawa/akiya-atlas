"""取り下げ依頼（takedown Issue）の読み取りのテスト。ネットワーク不要。"""

from __future__ import annotations

from akiya_atlas.takedown import TakedownList, collect, parse_meta, site_path

BASE = "https://akiya-atlas.com"


def _issue(number: int, labels: list[str], urls: list[str], *, category: str = "takedown") -> dict:
    meta = (
        '{"kind":"akiya-atlas-contact","category":"'
        + category
        + '","urls":'
        + str(urls).replace("'", '"')
        + ',"received_at":"2026-09-11T00:00:00.000Z","confidence":0.94}'
    )
    body = f"<!-- {meta} -->\n## 種別\n{category}\n\n## 対象 URL\n" + "\n".join(
        f"- {u}" for u in urls
    )
    return {
        "number": number,
        "title": "[お問い合わせ] 掲載の削除依頼",
        "body": body,
        "labels": [{"name": n} for n in labels],
    }


def test_parse_meta_reads_the_first_line_json_comment() -> None:
    issue = _issue(12, ["takedown"], [f"{BASE}/nagano/203076/25/"])
    meta = parse_meta(issue["body"])
    assert meta is not None
    assert meta["category"] == "takedown" and meta["urls"] == [f"{BASE}/nagano/203076/25/"]
    assert parse_meta("本文だけで JSON コメントが無い") is None
    assert parse_meta("<!-- {壊れた json -->") is None


def test_site_path_keeps_only_our_own_pages() -> None:
    assert site_path(f"{BASE}/nagano/203076/25/", BASE) == "/nagano/203076/25/"
    assert site_path(f"{BASE}/nagano/203076/25", BASE) == "/nagano/203076/25/"
    assert site_path(f"{BASE}/sitemap.xml", BASE) == "/sitemap.xml"
    assert site_path("https://www.city.example.lg.jp/akiya/", BASE) is None
    assert site_path("", BASE) is None


def test_collect_takes_takedown_only_and_counts_the_rest() -> None:
    issues = [
        _issue(11, ["takedown"], [f"{BASE}/nagano/203076/25/", "https://other.example/x"]),
        _issue(12, ["takedown"], [f"{BASE}/nagano/203076/25/"]),  # 同じページの重複依頼
        _issue(13, ["needs-human"], [f"{BASE}/toyama/163422/1/"], category="needs_human"),
        _issue(14, ["municipality"], [f"{BASE}/toyama/163422/2/"], category="municipality"),
    ]
    result = collect(issues, base_url=BASE)
    assert result.paths == {"/nagano/203076/25/"}
    assert [t.issue for t in result.items] == [11]
    assert result.other_issues == {"needs-human": 1, "municipality": 1}


def test_round_trip_through_the_file(tmp_path) -> None:  # noqa: ANN001
    from sitemill.settings import Workspace

    (tmp_path / "site.toml").write_text(
        '[site]\nid="x"\nname="x"\nbase_url="https://akiya-atlas.com"\n'
        'service="akiya_atlas.service:service"\n[operator]\nname="x"\ncontact="x"\n',
        encoding="utf-8",
    )
    ws = Workspace.open(tmp_path)
    ws.ensure_dirs()
    result = collect([_issue(21, ["takedown"], [f"{BASE}/nagano/203076/25/"])], base_url=BASE)
    result.save(ws)
    again = TakedownList.load(ws)
    assert again.paths == {"/nagano/203076/25/"}
    assert again.items[0].issue == 21


def test_a_takedown_broader_than_one_municipality_is_held_back() -> None:
    """自動で効くのは物件 1 件か市町村 1 つのページだけ（ADR 0016 追記）。

    2026-09-12 の依頼は「当町の成約済み物件を」と書きつつ県のページ /nagano/ を指していて、
    長野県の 241 件が 9/17 まで隠れた。範囲外は隠さず、人の判断に回す。
    """
    issues = [
        _issue(31, ["takedown"], [f"{BASE}/nagano/"]),  # 県のページ
        _issue(32, ["takedown"], [f"{BASE}/"]),  # トップ
        _issue(33, ["takedown"], [f"{BASE}/owners/nagano/"]),  # 所有者向けの県ページ
        _issue(34, ["takedown"], [f"{BASE}/hyogo/282189/"]),  # 市町村（6 桁）
        _issue(35, ["takedown"], [f"{BASE}/nagano/202193-tomi/"]),  # 市町村（コード-名前）
        _issue(36, ["takedown"], [f"{BASE}/nagano/202193-tomi/322/"]),  # 物件
    ]
    result = collect(issues, base_url=BASE)
    assert result.paths == {"/hyogo/282189/", "/nagano/202193-tomi/", "/nagano/202193-tomi/322/"}
    assert sorted(t.path for t in result.unscoped) == ["/", "/nagano/", "/owners/nagano/"]


def test_an_out_of_scope_row_written_by_hand_is_not_applied(tmp_path) -> None:  # noqa: ANN001
    """一覧を手で書き換えて範囲外のパスを入れても、読むときに効かせない。"""
    import json

    from sitemill.settings import Workspace

    (tmp_path / "site.toml").write_text(
        '[site]\nid="x"\nname="x"\nbase_url="https://akiya-atlas.com"\n'
        'service="akiya_atlas.service:service"\n[operator]\nname="x"\ncontact="x"\n',
        encoding="utf-8",
    )
    ws = Workspace.open(tmp_path)
    ws.ensure_dirs()
    result = collect([_issue(41, ["takedown"], [f"{BASE}/nagano/"])], base_url=BASE)
    result.save(ws)
    path = tmp_path / "data/reference/takedowns.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["takedowns"] == [] and data["unscoped"][0]["path"] == "/nagano/"
    # 9/12 から 9/17 までの一覧と同じ形（範囲外のパスが takedowns に入っている）
    data["takedowns"] = data.pop("unscoped")
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert TakedownList.load(ws).paths == set()
