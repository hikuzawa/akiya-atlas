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
