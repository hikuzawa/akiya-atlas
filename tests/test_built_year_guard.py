"""掲載日を築年にしない（2026-09-17、三原市）。

三原市の一覧は見出しの無い列に掲載日「令和８年 ６月29日」を並べていて、LLM が「令和８年」を
築年に選び、80 件が「2026年築」と出た。プロンプトで禁じても選び続けたので、本文で確かめて外す。
"""

from __future__ import annotations

from sitemill.diff.normalize import squash
from sitemill.extract import ExtractedItem
from sitemill.models import FieldValue

from akiya_atlas.service import (
    NOTE_LISTING_DATE,
    built_year_is_a_date,
    drop_dates_quoted_as_built_year,
    merge_content,
)

# 三原市の実物の並び（ゼロ幅スペース入り）
MIHARA = squash(
    "所在地：大和町萩原 構 造：木造２階建 ※物件情報はこちら [PDFファイル／1024KB] "
    "交渉中 令和８年​ ６月９日 414 所在地：糸崎６丁目 売買 300万円 令和７年​ ９月11日 "
    "・オール電化住宅で、令和6年にキッチン・浴室を改修 売買 220万円 令和6年​ 10月17日"
)


def test_a_year_cut_out_of_a_listing_date_is_not_a_built_year() -> None:
    assert built_year_is_a_date("令和８年", MIHARA)
    assert built_year_is_a_date("令和7年", MIHARA)
    # 掲載日のほかに改修の年としても現れる。どちらも築年ではない
    assert built_year_is_a_date("令和6年", MIHARA)


def test_a_labelled_built_year_is_kept_even_next_to_a_date() -> None:
    page = squash("建築年月日：昭和50年 3月1日 延床面積 98.5㎡ 登録日 令和8年 6月1日")
    assert not built_year_is_a_date("昭和50年", page)
    assert not built_year_is_a_date(
        "昭和50年", squash("昭和50年築 令和8年 6月1日掲載 昭和50年 4月1日")
    )
    # 年月だけの建築年（日が無い）は日付ではない
    assert not built_year_is_a_date("令和4年", squash("建物 木造2階建て 令和4年11月"))
    assert not built_year_is_a_date("令和4年", squash("所在地 薬谷町 令和4年 土地面積 230.51㎡"))
    # 本文に無い引用はここでは判断しない（引用の照合が別にある）
    assert not built_year_is_a_date("平成2年", MIHARA)


def test_the_dropped_year_replaces_the_old_parsed_one_on_merge() -> None:
    item = ExtractedItem(
        fields={"built_year": FieldValue(value=2026, quote="令和８年", status="parsed")}
    )
    assert drop_dates_quoted_as_built_year([item], MIHARA) == 1
    dropped = item.fields["built_year"]
    assert dropped.value is None and dropped.note == NOTE_LISTING_DATE

    old = {"built_year": {"value": 2026, "quote": "令和8年", "status": "parsed", "note": None}}
    new = {"built_year": dropped.model_dump(mode="json"), "page_kind": "listing_index"}
    # 通常は以前の parsed を残すが、誤りと分かって外した値は残さない
    assert merge_content(old, new)["built_year"]["status"] == "unparsed"
