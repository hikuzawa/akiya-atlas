from pathlib import Path

import pytest

from akiya_atlas.municipalities import (
    PREFECTURE_SLUGS,
    MunicipalityRef,
    _resolve_pref_code,
    load_code_table,
    municipalities_for,
)

REPO = Path(__file__).resolve().parents[1]
TABLE = REPO / "data" / "reference" / "municipal_codes.csv"

SYNTH = """code,prefecture,prefecture_kana,municipality,municipality_kana
200000,長野県,ﾅｶﾞﾉｹﾝ,,
202011,長野県,ﾅｶﾞﾉｹﾝ,長野市,ﾅｶﾞﾉｼ
202193,長野県,ﾅｶﾞﾉｹﾝ,東御市,ﾄｳﾐｼ
131016,東京都,ﾄｳｷｮｳﾄ,千代田区,ﾁﾖﾀﾞｸ
141003,神奈川県,ｶﾅｶﾞﾜｹﾝ,横浜市鶴見区,ﾖｺﾊﾏｼﾂﾙﾐｸ
"""


def test_resolve_prefecture_code() -> None:
    assert _resolve_pref_code("長野県") == "20"
    assert _resolve_pref_code("nagano") == "20"
    assert _resolve_pref_code("20") == "20"
    assert _resolve_pref_code("長野") == "20"
    with pytest.raises(ValueError):
        _resolve_pref_code("架空県")


def test_loader_skips_prefecture_and_ward_rows(tmp_path: Path) -> None:
    csv = tmp_path / "codes.csv"
    csv.write_text(SYNTH, encoding="utf-8")
    rows = load_code_table(csv)
    names = [m.name for m in rows]
    assert "長野市" in names and "東御市" in names
    assert "千代田区" in names  # 特別区（東京23区）は市町村レベルなので含める
    assert "横浜市鶴見区" not in names  # 政令市の行政区は除外
    assert all(len(m.code) == 6 for m in rows)


def test_municipality_kind_and_slug() -> None:
    m = MunicipalityRef(
        code="202193",
        prefecture="長野県",
        prefecture_slug="nagano",
        name="東御市",
        name_kana="ﾄｳﾐｼ",
    )
    assert m.kind == "市" and m.slug == "202193" and m.prefecture_code == "20"


def test_prefecture_slugs_complete() -> None:
    assert len(PREFECTURE_SLUGS) == 47 and PREFECTURE_SLUGS["20"] == "nagano"


@pytest.mark.skipif(not TABLE.is_file(), reason="コード表 CSV が無い")
def test_real_code_table_nagano_has_77() -> None:
    nagano = municipalities_for("長野県", TABLE)
    assert len(nagano) == 77
    kinds = {k: sum(1 for m in nagano if m.kind == k) for k in ("市", "町", "村")}
    assert kinds == {"市": 19, "町": 23, "村": 35}
    assert all(m.prefecture_code == "20" for m in nagano)
