"""公開前の歯止めで見る割合（sitemill ADR 0026、akiya-atlas ADR 0019）。

平常の値は 09-15〜09-29 の 15 晩の日次のデータで数えた（掲載中でない物件 5.8〜6.2%、掲載中 0 件の
巡回自治体 1.1〜1.9%、運営主体を判定できない自治体 0%）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sitemill.build.guard import GuardMetric, evaluate
from sitemill.settings import Workspace

from akiya_atlas.service import PUBLISH_LIMITS, service

REPO = Path(__file__).resolve().parents[1]
NORMAL = {
    "listings_not_live": 0.058,
    "crawled_without_live": 0.011,
    "operator_undetermined": 0.0,
}


def _baseline() -> dict[str, dict]:
    return {name: {"count": 0, "total": 0, "share": share} for name, share in NORMAL.items()}


def test_every_limit_has_a_reason_with_the_normal_range() -> None:
    for name, limit in PUBLISH_LIMITS.items():
        assert "平常" in limit.reason, name
        assert limit.max_rise is not None, name


def test_the_committed_data_passes_without_a_baseline() -> None:
    """初回（基準が無い晩）は割合の上限だけを見る。いまのデータで止まらないこと。"""
    values = service.publish_metrics(Workspace.open(REPO), now=datetime.now(UTC))
    assert set(values) == set(PUBLISH_LIMITS)
    assert values["listings_not_live"].total > 1000
    assert values["crawled_without_live"].total > 100
    assert values["operator_undetermined"].total > 1000
    assert evaluate(values, {}, PUBLISH_LIMITS) == []


def test_a_normal_night_is_not_stopped() -> None:
    """平常の 1 晩の動き（物件 0.4pt・自治体 1 つ・判定できない自治体 数件）は止めない。"""
    values = {
        "listings_not_live": GuardMetric(310, 5000),
        "crawled_without_live": GuardMetric(4, 264),
        "operator_undetermined": GuardMetric(3, 1742),
    }
    assert evaluate(values, _baseline(), PUBLISH_LIMITS) == []


def test_listings_going_stale_together_stop_the_deploy() -> None:
    """1 県分の物件がまとめて古くなると止まる。知らせには理由の文が入る。"""
    values = {
        "listings_not_live": GuardMetric(600, 5000),
        "crawled_without_live": GuardMetric(3, 264),
        "operator_undetermined": GuardMetric(0, 1742),
    }
    breaches = evaluate(values, _baseline(), PUBLISH_LIMITS)
    assert len(breaches) == 1
    assert breaches[0].startswith("listings_not_live") and "掲載中でない物件" in breaches[0]


def test_many_municipalities_losing_their_listings_stop_the_deploy() -> None:
    values = {
        "listings_not_live": GuardMetric(299, 5146),
        "crawled_without_live": GuardMetric(13, 264),
        "operator_undetermined": GuardMetric(0, 1742),
    }
    breaches = evaluate(values, _baseline(), PUBLISH_LIMITS)
    assert [b.split(":")[0] for b in breaches] == ["crawled_without_live"]


def test_official_site_resolution_breaking_stops_the_deploy() -> None:
    values = {
        "listings_not_live": GuardMetric(299, 5146),
        "crawled_without_live": GuardMetric(3, 264),
        "operator_undetermined": GuardMetric(40, 1742),
    }
    breaches = evaluate(values, _baseline(), PUBLISH_LIMITS)
    assert [b.split(":")[0] for b in breaches] == ["operator_undetermined"]
