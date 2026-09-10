"""www / pages.dev → apex の 301 が base_url から正しく導かれることを確かめる。"""

from akiya_atlas.site_redirects import PAGES_DEV_HOST, for_site, pages_dev_to_apex, www_to_apex


def test_apex_domain_redirects_www_to_apex():
    (r,) = www_to_apex("https://akiya-atlas.com")
    assert (r.from_path, r.to_url, r.status) == (
        "www.akiya-atlas.com/*",
        "https://akiya-atlas.com/:splat",
        301,
    )


def test_apex_domain_redirects_pages_dev_to_apex():
    (r,) = pages_dev_to_apex("https://akiya-atlas.com")
    assert (r.from_path, r.to_url, r.status) == (
        f"{PAGES_DEV_HOST}/*",
        "https://akiya-atlas.com/:splat",
        301,
    )


def test_for_site_orders_www_then_pages_dev_and_ignores_trailing_slash():
    rs = for_site("https://akiya-atlas.com/")
    assert [r.from_path for r in rs] == ["www.akiya-atlas.com/*", f"{PAGES_DEV_HOST}/*"]
    assert {r.to_url for r in rs} == {"https://akiya-atlas.com/:splat"}


def test_interim_pages_dev_and_www_base_urls_emit_nothing():
    assert for_site("https://akiya-atlas-asb.pages.dev") == []
    assert for_site("https://www.akiya-atlas.com") == []
