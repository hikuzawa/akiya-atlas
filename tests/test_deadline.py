"""補助制度の締切の読み取り（2026-09-17）。

受付中の制度に「募集終了」と出すと、利用者は申請を諦める。以前は始まりの日を締切にしていて、
「募集終了」253 件の半数以上が誤りだった。例はすべて実データの引用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from akiya_atlas.deadline import parse_period_end
from akiya_atlas.subsidies import reparse_deadlines


@pytest.mark.parametrize(
    ("quote", "deadline"),
    [
        # 終わりの日に年が無い。以前は始まりの 4 月 13 日を締切にし、伊勢崎市で「募集終了」と出た
        ("令和8年4月13日(月曜日)から10月30日(金曜日)", date(2026, 10, 30)),
        ("令和8年9月7日(月)~18日(金)", date(2026, 9, 18)),
        ("令和8年4月13日 月曜日 ~6月30日 火曜日", date(2026, 6, 30)),
        ("令和8年12月1日から1月15日まで", date(2027, 1, 15)),
        ("令和8年4月21日(火曜日)〜9年1月29日(金曜日)", date(2027, 1, 29)),
        # 年が書いてある期間
        ("令和8年4月1日から令和9年3月31日まで", date(2027, 3, 31)),
        ("2026年12月28日まで", date(2026, 12, 28)),
        ("令和8年(2026年)4月1日(水)から令和8年(2026年)8月31日(月)まで", date(2026, 8, 31)),
        ("令和9(2027)年3月31日までの間に", date(2027, 3, 31)),
        ("R8.2月末までに工事を終了し", date(2026, 2, 28)),
        ("令和8年4月1日以降に着工し、令和9年3月31日までに完了", date(2027, 3, 31)),
        # 時刻と時間帯
        ("令和8年4月20日(月曜日) 午前8時30分~令和8年6月24日", date(2026, 6, 24)),
        (
            "令和8年6月1日(月曜日)~11月30日(月曜日)午前9時~午後4時30分(土日祝は除く)",
            date(2026, 11, 30),
        ),
        # 期間が 2 つ
        (
            "前期:令和8年4月9日(木曜日)10:00から4月16日(木曜日)12:00まで、"
            "後期:令和8年8月18日(火曜日)8:30から8月24日(月曜日)17:00まで",
            date(2026, 8, 24),
        ),
        (
            "令和8年4月1日(水曜)~7月31日(金曜)、事前申請受付:令和8年8月24日(月曜)~9月4日(金曜)"
            "12:00まで、令和8年12月18日(金曜)または予算の上限に達するまで",
            date(2026, 12, 18),
        ),
        # 終わりだと書いてある
        ("申込期限:令和8年7月17日(金)", date(2026, 7, 17)),
        ("令和8年3月31日をもちまして終了", date(2026, 3, 31)),
        ("令和8年6月25日木曜日に予算額に達したため、受付を終了", date(2026, 6, 25)),
        ("平成25年7月1日以降、制度の終了は令和7年3月31日", date(2025, 3, 31)),
        ("補助率1/2、令和8年11月30日まで", date(2026, 11, 30)),
    ],
)
def test_the_deadline_is_a_date_written_as_an_end(quote: str, deadline: date) -> None:
    assert parse_period_end(quote)[0] == deadline


@pytest.mark.parametrize(
    ("quote", "note"),
    [
        # 始まりの日しか無い
        ("令和8年4月1日から受付開始", "start_only"),
        ("令和8年4月28日(火曜日)から受付開始、予算額に達し次第、受付を終了", "start_only"),
        ("令和7年4月1日以降の取得に限ります", "start_only"),
        ("令和8年6月10日(水)に開始", "start_only"),
        ("令和8年4月1日~(予算がなくなり次第終了)", "start_only"),
        # 年の無い日付を、期間の終わりでもないのに令和 3 年の続きと読まない
        (
            "令和3年4月1日以降に実施した住宅改修支援事業について対象とし、各年度の3月末日までに交付申請",
            "start_only",
        ),
        # 始まりか締切か書いていない
        ("令和8年7月10日(金曜日) 午前9時", "no_date"),
        ("令和8年4月1日(ただし定数12基に達し次第、受付を終了させていただきます。)", "no_date"),
        ("4月27日(月曜日)から5月29日(金曜日)", "no_date"),  # 年が無い。今年で補わない
        ("予算がなくなり次第終了", "no_date"),
        # 締切のあとも受け付けている
        (
            "令和8年5月19日(火)8時30分から令和8年6月30日(火)17時まで、"
            "令和8年7月1日(水)8時30分から予算上限に達するまで",
            "reopens_after_deadline",
        ),
        (
            "事前申込締め切り:令和8年4月30日(木曜日)まで→既に終了しているため随時募集となります",
            "still_open",
        ),
        # 原文の曜日が年と合わない（2027-12-25 は土曜）。誤記を値にしない
        ("令和8年5月7日(木曜日)から令和9年12月25日(金曜日)まで", "weekday_mismatch"),
    ],
)
def test_no_deadline_when_the_quote_does_not_say_when_it_ends(quote: str, note: str) -> None:
    assert parse_period_end(quote) == (None, note)


@dataclass
class _Llm:
    max_input_chars: int = 60_000


@dataclass
class _Site:
    llm: _Llm


@dataclass
class _Ws:
    data_dir: Path
    raw_dir: Path
    site: _Site


def _write_page(ws: _Ws, source_id: str, url: str, html: str, digest: str) -> None:
    from sitemill.store.raw import RawCache

    RawCache(ws.raw_dir).save(
        source_id, url, html.encode("utf-8"), {"content_hash": digest, "encoding": "utf-8"}
    )


def test_reparse_fixes_start_dates_and_values_empty_ones_only_when_the_quote_is_in_the_page(
    tmp_path: Path,
) -> None:
    ws = _Ws(tmp_path / "data", tmp_path / "raw", _Site(_Llm()))
    (ws.data_dir / "subsidies").mkdir(parents=True)
    url = "https://www.city.example.lg.jp/hojo.html"
    _write_page(
        ws,
        "gunma-102075",
        url,
        "<html><body><p>令和8年(2026年)4月1日(水)から令和8年(2026年)8月31日(月)まで</p></body></html>",
        "sha256:a",
    )
    prov = {"source_url": url, "content_hash": "sha256:a"}
    rows = [
        # 締切が入っていた（照合済み）。始まりの日だったので直す
        {
            "record_id": "1",
            "name": "耐震",
            "period_text": "令和8年4月13日(月曜日)から10月30日(金曜日)",
            "period_end": "2026-04-13",
        },
        # 締切が空。引用が本文にあるので値にする
        {
            "record_id": "2",
            "name": "解体",
            "period_text": "令和8年(2026年)4月1日(水)から令和8年(2026年)8月31日(月)まで",
            "period_end": None,
        },
        # 締切が空。引用が本文に無い（LLM がつなぎ合わせた）ので値にしない
        {
            "record_id": "3",
            "name": "改修",
            "period_text": "令和8年4月1日(水曜日)~令和9年2月28日まで",
            "period_end": None,
        },
    ]
    path = ws.data_dir / "subsidies" / "gunma-102075.jsonl"
    path.write_text(
        "".join(
            json.dumps({**r, "source_id": "gunma-102075", "provenance": prov}, ensure_ascii=False)
            + "\n"
            for r in rows
        ),
        encoding="utf-8",
    )
    now = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)

    dry = reparse_deadlines(ws, {}, now=now, apply=False)  # type: ignore[arg-type]
    assert [(c.name, c.after) for c in dry.changes] == [
        ("耐震", "2026-10-30"),
        ("解体", "2026-08-31"),
    ]
    assert dry.unverified == 1
    assert "2026-04-13" in path.read_text(encoding="utf-8")  # 試し実行では書き換えない

    reparse_deadlines(ws, {}, now=now, apply=True)  # type: ignore[arg-type]
    saved = {
        r["record_id"]: r
        for r in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    }
    assert saved["1"]["period_end"] == "2026-10-30"
    assert saved["1"]["history"][-1]["reason"] == "deadline_reparsed"
    assert saved["2"]["period_end"] == "2026-08-31"
    assert saved["3"]["period_end"] is None and "history" not in saved["3"]


TODAY = date(2026, 9, 17)  # 令和 8 年度


@pytest.mark.parametrize(
    ("year_text", "past"),
    [
        ("令和7年度", True),
        ("令和6年度", True),
        ("2025年度", True),
        ("平成24年度~30年度", True),
        ("令和4年度~令和6年度", True),
        ("令和8年度", False),
        ("令和8(2026)年度", False),
        # 終わりの年度が今年度以降
        ("令和7年度~令和9年度", False),
        ("令和2年度~8年度", False),
        # 始まりだけを書いた制度は続いているとみなす
        ("令和7年度から", False),
        ("平成24年度から", False),
        ("令和7年度~", False),
        (None, False),
        ("通年", False),
    ],
)
def test_past_fiscal_year_only_when_every_written_year_is_before_this_one(
    year_text: str | None, past: bool
) -> None:
    from akiya_atlas.deadline import maybe_past_fiscal_year

    assert maybe_past_fiscal_year(year_text, TODAY) is past


def test_the_fiscal_year_turns_in_april() -> None:
    from akiya_atlas.deadline import maybe_past_fiscal_year

    assert not maybe_past_fiscal_year("令和7年度", date(2026, 3, 31))
    assert maybe_past_fiscal_year("令和7年度", date(2026, 4, 1))


def test_a_readable_deadline_wins_over_the_past_year_mark() -> None:
    """締切が読めれば「募集終了」か受付中かが分かるので、過年度の印は出さない。"""
    from akiya_atlas.schema import Subsidy

    base = {"name": "x", "kind": "改修", "url": "https://e.example/", "year_text": "令和7年度"}
    assert Subsidy(**base).maybe_past_year
    assert not Subsidy(**base, period_end=date(2025, 12, 26)).maybe_past_year
