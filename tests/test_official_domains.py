from akiya_atlas.municipalities import MunicipalityRef
from akiya_atlas.official_domains import candidate_official_urls, classify_host


def test_classify_host() -> None:
    assert classify_host("www.city.tomi.nagano.jp", "nagano").kind == "geographic_municipal"
    assert classify_host("www.city.tomi.nagano.jp", "nagano").is_official
    assert classify_host("pref.nagano.lg.jp", "nagano").kind == "lg_jp"
    assert classify_host("town.karuizawa.nagano.jp", "nagano").is_official
    assert not classify_host("www.inacity.jp", "nagano").is_official
    assert not classify_host("39ijyu.com", "nagano").is_official
    # 地理型だが city/town/vill でない -> medium（自治体運営かは要確認）
    med = classify_host("www.example.nagano.jp", "nagano")
    assert med.kind == "geographic_other" and not med.is_official


def test_candidate_urls_prefer_kind_prefix() -> None:
    m = MunicipalityRef(
        code="202193",
        prefecture="長野県",
        prefecture_slug="nagano",
        name="東御市",
        name_kana="ﾄｳﾐｼ",
    )
    urls = candidate_official_urls(m)
    assert "https://www.city.tomi.nagano.jp/" in urls
    assert any("city.toumi.nagano.jp" in u for u in urls)
    assert "https://www.city.tomi.lg.jp/" in urls  # city.<name>.lg.jp（長野県で多い形式）
    # 市なので town/vill prefix は生成しない
    assert not any("town." in u or "vill." in u for u in urls)


def test_candidate_urls_town() -> None:
    m = MunicipalityRef(
        code="203645",
        prefecture="長野県",
        prefecture_slug="nagano",
        name="軽井沢町",
        name_kana="ｶﾙｲｻﾞﾜﾏﾁ",
    )
    urls = candidate_official_urls(m)
    assert any("town.karuizawa.nagano.jp" in u for u in urls)
    assert not any("city." in u for u in urls)
