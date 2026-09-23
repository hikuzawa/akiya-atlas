"""広告の転送ページの表示数の表（tools/report_clicks）。

0 回のときに「押されていない」と「計測が届いていない」を取り違えないことを固定する。
2026-09-23 に japan-open-today で、ビーコンが 1 つも出ていないのに数字だけ見て
「押されていない」と読みかけた例が出たため。
"""

from __future__ import annotations

from tools.report_clicks import report

from akiya_atlas import affiliates


def _path_of(index: int = 0) -> str:
    return affiliates.go_targets()[index].url_path


def test_counts_land_on_the_right_row() -> None:
    out = "\n".join(report({_path_of(0): 4, "/owners/": 99}, 7))
    assert "| 4 |" in out
    assert "合計 **4** 回" in out


def test_zero_with_traffic_says_it_is_not_measured_away() -> None:
    out = "\n".join(report({"/owners/": 12, "/": 100}, 7))
    assert "合計 **0** 回" in out
    assert "112 表示あるので、計測は届いている" in out


def test_zero_without_traffic_points_at_the_measurement() -> None:
    out = "\n".join(report({}, 7))
    assert "計測が届いていない疑いがある" in out


def test_a_retired_transfer_page_is_still_reported() -> None:
    """枠から外したあとも押されているページは、表に出ないので別行で知らせる。"""
    out = "\n".join(report({"/go/removed-offer/owners-consult/": 3}, 7))
    assert "一覧に無い転送ページ" in out and "/go/removed-offer/owners-consult/" in out
