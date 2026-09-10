"""www → apex の 301 が base_url から正しく導かれることを確かめる。"""

from akiya_atlas.site_redirects import www_to_apex


def test_apex_domain_redirects_www_to_apex():
    (r,) = www_to_apex("https://akiya-atlas.com")
    assert (r.from_path, r.to_url, r.status) == (
        "www.akiya-atlas.com/*",
        "https://akiya-atlas.com/:splat",
        301,
    )


def test_trailing_slash_in_base_url_is_ignored():
    (r,) = www_to_apex("https://akiya-atlas.com/")
    assert r.to_url == "https://akiya-atlas.com/:splat"


def test_pages_dev_and_www_base_urls_emit_nothing():
    assert www_to_apex("https://akiya-atlas.pages.dev") == []
    assert www_to_apex("https://www.akiya-atlas.com") == []
