"""運営主体の確認日は、実際に確かめた自治体だけ進める（2026-09-26）。

県の YAML を作り直すたびに全自治体の確認日を今日にしていたため、`subsidy-pages --code` や
`rediscover --code` で 1 自治体を見ただけで、北海道は 179 件・茨城県は 44 件の確認日が進んだ。
`/data/<県>/` に出る「確認日」は運営主体を最後に確かめた日なので、確かめていないものを
進めると「最近確認した」という表示が嘘になる。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from akiya_atlas import expand


def _entry(sid: str, day: str | None) -> dict:
    return {
        "id": sid,
        "operator_evidence": {"quote": "公式ドメイン", "url": "https://x/", "checked_on": day},
    }


def test_an_entry_not_checked_this_time_keeps_its_date(tmp_path: Path) -> None:
    auto = tmp_path / "hokkaido-auto.yaml"
    auto.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    _entry("hokkaido-014541", "2026-09-15"),
                    _entry("hokkaido-016071", "2026-09-15"),
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    adopted = [
        _entry("hokkaido-014541", None),  # 今回は確かめていない（subsidy-pages で巡回先だけ変えた）
        _entry("hokkaido-016071", "2026-09-26"),  # 今回 rediscover で確かめた
        _entry("hokkaido-099999", None),  # 初めて載る
    ]
    expand._keep_checked_on(auto, adopted)
    days = {e["id"]: e["operator_evidence"]["checked_on"] for e in adopted}
    assert days["hokkaido-014541"] == "2026-09-15"
    assert days["hokkaido-016071"] == "2026-09-26"
    assert days["hokkaido-099999"]  # 今日（初めて確かめた）


def test_a_resolved_official_host_stamps_the_day_and_survives_the_row() -> None:
    """`decide` で公式ホストを解決したときだけ日付が入り、発見の記録に残る。"""
    from akiya_atlas.official_domains import classify_host

    muni = expand.MunicipalityRef(
        code="014541", prefecture="北海道", prefecture_slug="hokkaido", name="当麻町"
    )
    official = classify_host("www.town.tohma.hokkaido.jp", "hokkaido")
    f = expand.decide(muni, official, None, cross_linked=False, bank_host_official=False)
    assert f.evidence_checked_on  # 確かめた
    back = expand._row_to_finding(expand._finding_to_row(f))
    assert back.evidence_checked_on == f.evidence_checked_on

    unresolved = expand.decide(muni, None, None, cross_linked=False, bank_host_official=False)
    assert unresolved.evidence_checked_on is None  # 確かめられなかったものは進めない
