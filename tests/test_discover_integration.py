"""discover_and_write の結合テスト（respx でモック）。resolve→find→decide→書き出しを通す。"""

from pathlib import Path

import httpx
import pytest
import respx
import yaml
from sitemill.models import Source
from sitemill.review import ReviewQueue
from sitemill.settings import Workspace

from akiya_atlas.expand import discover_and_write
from akiya_atlas.schema import Municipality

SITE_TOML = """
[site]
id = "akiya-atlas"
name = "空き家アトラス"
base_url = "https://akiya-atlas.pages.dev"
service = "akiya_atlas.service:service"
[operator]
name = "準備中"
contact = "準備中"
[crawl]
default_delay_seconds = 0
jitter_seconds = 0
[llm]
provider = "fixture"
"""

# 架空の 2 市（長野県コード 20 系、実在コードと衝突しない 209001/209002）
CODE_TABLE = """code,prefecture,prefecture_kana,municipality,municipality_kana
209001,長野県,ﾅｶﾞﾉｹﾝ,あ市,ｱｼ
209002,長野県,ﾅｶﾞﾉｹﾝ,い市,ｲｼ
"""

INDEX = (
    "<html><body><main><h1>空き家バンク物件一覧</h1>"
    + "".join(f"<div>[売買{300 + i * 50}万円]No.{i} 地名{i}</div>" for i in range(6))
    + "</main></body></html>"
)


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workspace:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "site.toml").write_text(SITE_TOML, encoding="utf-8")
    (tmp_path / "data" / "reference").mkdir(parents=True)
    (tmp_path / "data" / "reference" / "municipal_codes.csv").write_text(
        CODE_TABLE, encoding="utf-8"
    )
    (tmp_path / "data" / "sources").mkdir(parents=True)
    w = Workspace.open(tmp_path)
    w.ensure_dirs()
    return w


@respx.mock
def test_discover_writes_sources_and_review(ws: Workspace) -> None:
    for host in ("www.city.a.nagano.jp", "www.city.i.nagano.jp", "x.akiya-athome.jp"):
        respx.get(f"https://{host}/robots.txt").mock(return_value=httpx.Response(404))
    # あ市: 公式が同一ホストの空き家バンク一覧にリンク → crawl
    respx.get("https://www.city.a.nagano.jp/").mock(
        return_value=httpx.Response(200, text='<a href="/akiya/">空き家バンク</a>')
    )
    respx.get("https://www.city.a.nagano.jp/akiya/").mock(
        return_value=httpx.Response(200, text=INDEX)
    )
    # い市: 公式が民間プラットフォームにリンク → link_only
    respx.get("https://www.city.i.nagano.jp/").mock(
        return_value=httpx.Response(
            200, text='<a href="https://x.akiya-athome.jp/">空き家バンク（アットホーム）</a>'
        )
    )
    respx.get("https://x.akiya-athome.jp/").mock(
        return_value=httpx.Response(200, text="<html><body><main>物件一覧</main></body></html>")
    )
    respx.route().mock(return_value=httpx.Response(404))  # 他の候補 URL は未到達

    report = discover_and_write(ws, "長野県")
    assert report.total == 2 and report.adopted_crawl == 1 and report.adopted_link_only == 1

    auto = yaml.safe_load((ws.sources_dir / "nagano-auto.yaml").read_text(encoding="utf-8"))
    by_id = {e["id"]: e for e in auto["sources"]}
    assert by_id["nagano-209001"]["policy"] == "crawl"
    assert by_id["nagano-209002"]["policy"] == "link_only"
    # 生成された source は妥当に load できる
    a = Source.model_validate(by_id["nagano-209001"])
    assert a.crawlable and a.operator_kind.value == "municipality"
    b_muni = Municipality.model_validate(
        {
            "id": "nagano-209002",
            "official_url": by_id["nagano-209002"]["official_url"],
            **by_id["nagano-209002"]["municipality"],
        }
    )
    assert b_muni.bank_status == "third_party_only" and not b_muni.crawled
    # 楽園信州リンクが link_only に付く
    assert any(
        "rakuen-akiya.jp" in e["url"] for e in by_id["nagano-209002"].get("external_links", [])
    )

    queue = ReviewQueue.load(ws.root / "data" / "review" / "nagano.yaml")
    assert len(queue.candidates) == 2
    assert {c.key for c in queue.candidates} == {"209001", "209002"}


@respx.mock
def test_discover_skips_existing_municipalities(ws: Workspace) -> None:
    # 209001 を既存 source として登録済みにする → discover はスキップ
    existing = {
        "sources": [
            {
                "id": "nagano-a",
                "name": "あ市空き家バンク",
                "operator": "あ市",
                "operator_kind": "municipality",
                "official_url": "https://www.city.a.nagano.jp/",
                "policy": "link_only",
                "municipality": {
                    "code": "209001",
                    "name": "あ市",
                    "prefecture": "長野県",
                    "prefecture_slug": "nagano",
                    "slug": "209001-a",
                    "bank_url": "https://www.city.a.nagano.jp/",
                },
            }
        ]
    }
    (ws.sources_dir / "nagano.yaml").write_text(
        yaml.safe_dump(existing, allow_unicode=True), encoding="utf-8"
    )
    respx.route().mock(
        return_value=httpx.Response(404)
    )  # い市の候補 URL は未到達（公式解決できず）
    report = discover_and_write(ws, "nagano")
    assert report.skipped_existing == 1
    assert all(f.muni.code != "209001" for f in report.findings)
