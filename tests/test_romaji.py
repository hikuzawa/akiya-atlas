import pytest

from akiya_atlas.romaji import romaji_variants


@pytest.mark.parametrize(
    ("kana", "expected_first"),
    [
        ("ﾄｳﾐｼ", "toumi"),  # 東御市 -> tomi も候補に
        ("ｻｸｼ", "saku"),
        ("ｲｲﾔﾏｼ", "iiyama"),
        ("ｵｵﾏﾁｼ", "oomachi"),  # omachi も候補
        ("ｲﾅｼ", "ina"),
        ("ﾏﾂﾓﾄｼ", "matsumoto"),
        ("ｶﾙｲｻﾞﾜﾏﾁ", "karuizawa"),
        ("ﾁﾉｼ", "chino"),
    ],
)
def test_variants(kana: str, expected_first: str) -> None:
    v = romaji_variants(kana)
    assert v[0] == expected_first


def test_long_vowel_collapse_variants() -> None:
    v = romaji_variants("ﾄｳﾐｼ")  # トウミ
    assert "tomi" in v and "toumi" in v
    v2 = romaji_variants("ｵｵﾏﾁｼ")  # オオマチ
    assert "omachi" in v2 and "oomachi" in v2


def test_empty() -> None:
    assert romaji_variants("") == []
