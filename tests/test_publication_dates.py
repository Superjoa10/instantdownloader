from contextlib import contextmanager
from datetime import date
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from instantdownloader.core import publication_dates as dates


def test_cutoff_and_utc_day():
    assert dates.cutoff_day('07/11/2026') == date(2026, 11, 7)
    assert dates.publication_day('2026-11-07T23:30:00-03:00') == date(2026, 11, 8)
    assert dates.publication_day('2026-11-07') == date(2026, 11, 7)


@pytest.mark.parametrize('value', ['', '31/02/2026', 'tomorrow', '2026-11-07'])
def test_invalid_cutoff(value):
    with pytest.raises(ValueError):
        dates.cutoff_day(value)


@pytest.mark.parametrize('value', ['', None, 'invalid', '2026-02-31'])
def test_missing_date_not_invented(value):
    assert dates.publication_day(value) is None


def test_only_timestamp_of_exact_media_is_used():
    document = {'data': [{'code': 'other', 'taken_at': 100},
                         {'shortcode': 'target', 'taken_at_timestamp': 1735689600}]}
    assert dates.matching_timestamp(document, 'target') == '2025-01-01T00:00:00+00:00'
    assert dates.matching_timestamp(document, 'missing') is None
    assert dates.matching_timestamp({'code': 'target', 'taken_at': True}, 'target') is None


@pytest.fixture
def browser(monkeypatch):
    page = Mock(url='https://www.instagram.com/p/abc/')
    page.goto.return_value = SimpleNamespace(status=200)
    page.locator.return_value.evaluate_all.return_value = ['2025-01-01T00:00:00Z']
    state = SimpleNamespace(page=page, closed=False)

    @contextmanager
    def factory(cookies):
        try:
            yield SimpleNamespace(new_page=lambda: page)
        finally:
            state.closed = True

    monkeypatch.setattr(dates, 'instagram_browser', factory)
    return state


def test_lookup_reads_permalink_time_and_closes(browser):
    callback = Mock()
    dates.load_publication_dates([{'id': 'abc'}], {'cookies': []}, Event(), callback)
    callback.assert_called_once_with('abc', '2025-01-01')
    assert '/p/abc/' in browser.page.locator.call_args.args[0]
    assert browser.closed


def test_lookup_stops_on_429_without_next_item(browser):
    browser.page.goto.return_value.status = 429
    callback = Mock()
    result = dates.load_publication_dates([{'id': 'abc'}, {'id': 'def'}], {'cookies': []}, Event(), callback)
    assert '429' in result
    browser.page.goto.assert_called_once()
    callback.assert_not_called()
    assert browser.closed


def test_cancelled_lookup_does_not_navigate(browser):
    cancel = Event()
    cancel.set()
    dates.load_publication_dates([{'id': 'abc'}], {'cookies': []}, cancel, Mock())
    browser.page.goto.assert_not_called()


def test_lookup_does_not_pick_conflicting_times(browser):
    browser.page.locator.return_value.evaluate_all.return_value = ['2025-01-01', '2026-01-01']
    callback = Mock()
    dates.load_publication_dates([{'id': 'abc'}], {'cookies': []}, Event(), callback)
    callback.assert_called_once_with('abc', '')
