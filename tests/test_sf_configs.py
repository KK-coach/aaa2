"""Az elfogadási SF-konfigurációk képzése a GUI-ból mentettből (tests/acceptance/sf_configs.py)."""
import pytest

from tests.acceptance.sf_configs import (
    DESKTOP_WINDOW,
    SOURCE,
    START_FOLDER_ONLY,
    WINDOW,
    primitive_fields,
    read_value,
    with_values,
)

SAVED = SOURCE.read_bytes()


def test_saved_config_is_mobile_googlebot_window():
    assert [read_value(SAVED, WINDOW, field)
            for field in ("mWidth", "mHeight", "mIsMobile", "mTouchEnabled")] == [
        411, 731, True, True]


def test_saved_config_values_read_from_the_class_descriptor():
    crawl = "seo.spider.config.SpiderCrawlConfig"
    assert read_value(SAVED, crawl, "mAjaxTimeoutMillis") == 5000
    assert read_value(SAVED, crawl, "mFollowInternalNoFollow") is True
    assert read_value(SAVED, crawl, "mAutoDiscoverSitemaps") is True
    assert read_value(SAVED, "seo.spider.config.SpiderInternalURLConfig",
                      "mCrawlOutsideStartFolder") is True


def test_desktop_changes_only_the_window_bytes():
    desktop = with_values(SAVED, DESKTOP_WINDOW)
    assert [read_value(desktop, WINDOW, field)
            for field in ("mWidth", "mHeight", "mIsMobile", "mTouchEnabled", "mResizeToContent")
            ] == [1920, 1080, False, False, True]
    changed = [i for i, (a, b) in enumerate(zip(SAVED, desktop, strict=True)) if a != b]
    offsets = {offset for _, offset in primitive_fields(SAVED, WINDOW).values()}
    assert len(changed) == 6 and len(desktop) == len(SAVED)
    assert all(any(0 <= i - offset < 4 for offset in offsets) for i in changed)


def test_ngx_also_stays_in_start_folder():
    ngx = with_values(with_values(SAVED, DESKTOP_WINDOW), START_FOLDER_ONLY)
    assert [read_value(ngx, cls, field) for cls, field, _ in START_FOLDER_ONLY] == [False, False]
    assert sum(1 for a, b in zip(SAVED, ngx, strict=True) if a != b) == 8


def test_unknown_class_is_refused():
    with pytest.raises(ValueError, match="0 példányosított leíró"):
        primitive_fields(SAVED, "seo.spider.config.NincsIlyen")


def test_object_field_is_not_a_primitive():
    with pytest.raises(KeyError):
        read_value(SAVED, "seo.spider.config.SpiderInternalURLConfig", "mInternalRegexes")
