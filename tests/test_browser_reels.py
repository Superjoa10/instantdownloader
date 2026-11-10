import json
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from instantdownloader.core import browser_reels as reels
from instantdownloader.core.profiles import DiscoveryResult, discover_profile


def response(codes=(), *, more=False, cursor=None, target='123', status=200,
             name='PolarisProfileReelsTabContentQuery', document=None):
    connection = {'edges': [{'node': {'media': {'code': code}}} for code in codes],
                  'page_info': {'has_next_page': more, 'end_cursor': cursor}}
    return SimpleNamespace(
        url='https://www.instagram.com/graphql/query', status=status,
        request=SimpleNamespace(post_data_json={
            'fb_api_req_friendly_name': name,
            'variables': json.dumps({'user_id': target}),
        }),
        json=Mock(return_value=document if document is not None else {'data': {'user': {'clips_connection': connection}}}),
    )


def local_session():
    return {'username': 'owner', 'cookies': [
        {'name': 'sessionid', 'value': 'fake', 'domain': '.instagram.com'},
        {'name': 'csrftoken', 'value': 'fake', 'domain': '.instagram.com'},
    ]}


def test_pages_merge_and_confirm_exhaustion():
    items = []
    collector = reels.ReelCollector(20, items.append)
    collector.receive(response(['one', 'two'], more=True, cursor='page2'))
    assert not collector.done
    collector.receive(response(['two', 'three']))
    assert collector.result.all_collected
    assert collector.result.pages == {'reels': 2}
    assert collector.result.inspected == {'reels': 4}
    assert [item.id for item in items] == ['one', 'two', 'three']
    assert collector.result.counts['reels'] == 3
    assert items[0].url == 'https://www.instagram.com/reel/one/'
    assert items[0].title == 'Reel one'


@pytest.mark.parametrize('more', [True, False])
def test_limit_in_middle_of_page_does_not_claim_all(more):
    collector = reels.ReelCollector(3)
    collector.receive(response(['a', 'b'], more=True, cursor='next'))
    collector.receive(response(['c', 'd'], more=more, cursor='last'))
    assert collector.done and collector.result.complete
    assert not collector.result.all_collected
    assert collector.result.inspected['reels'] == 3
    assert [item.id for item in collector.result.items] == ['a', 'b', 'c']


def test_exact_limit_on_terminal_page_confirms_all():
    collector = reels.ReelCollector(2)
    collector.receive(response(['a', 'b']))
    assert collector.result.all_collected


def test_empty_terminal_connection_is_verified_zero():
    collector = reels.ReelCollector(20)
    collector.receive(response())
    assert collector.result.all_collected
    assert collector.result.items == []


@pytest.mark.parametrize('document', [
    {'data': {'clips_connection': None}},
    {'data': {}},
    {'errors': [{'message': 'private provider text'}]},
    {'data': {'clips_connection': {'edges': [], 'page_info': {'has_next_page': True, 'end_cursor': 'next'}}}},
    {'data': {'clips_connection': {'edges': [], 'page_info': {'has_next_page': 'false'}}}},
])
def test_malformed_or_unavailable_is_not_verified_zero(document):
    collector = reels.ReelCollector(20)
    collector.receive(response(document=document))
    assert collector.done and not collector.result.complete
    assert not collector.result.all_collected
    assert collector.result.error_code == 'discovery_failed'
    assert 'private provider text' not in collector.result.message


@pytest.mark.parametrize('status,code', [(401, 'session_unavailable'), (403, 'session_unavailable'), (429, 'rate_limited')])
def test_blocked_response_stops_without_parsing_or_retry(status, code):
    collector = reels.ReelCollector(20)
    denied = response(status=status)
    collector.receive(denied)
    collector.receive(response(['ignored']))
    assert collector.result.error_code == code
    assert collector.result.items == []
    denied.json.assert_not_called()


@pytest.mark.parametrize('codes,cursor', [(['b'], 'same'), (['a'], 'different')])
def test_repeated_cursor_or_no_new_items_stops(codes, cursor):
    collector = reels.ReelCollector(20)
    collector.receive(response(['a'], more=True, cursor='same'))
    collector.receive(response(codes, more=True, cursor=cursor))
    assert collector.done and not collector.result.complete
    assert collector.result.items[0].id == 'a'


def test_unrelated_requests_and_other_targets_are_ignored():
    collector = reels.ReelCollector(20)
    collector.receive(response(['feed'], name='PolarisFeedQuery'))
    collector.receive(response(['a'], more=True, cursor='next'))
    collector.receive(response(['other'], target='456'))
    assert not collector.done
    assert [item.id for item in collector.result.items] == ['a']


def test_callback_error_preserves_partial_and_sanitizes():
    collector = reels.ReelCollector(20, Mock(side_effect=RuntimeError('secret token')))
    collector.receive(response(['a', 'b']))
    assert [item.id for item in collector.result.items] == ['a']
    assert not collector.result.complete
    assert 'secret token' not in collector.result.message


def test_reel_metadata():
    item = reels.reel_item({'node': {'media': {
        'code': 'abc', 'caption': {'text': 'Title'}, 'taken_at': 0,
        'image_versions2': {'candidates': [{'url': 'https://example.test/image.jpg'}]},
    }}})
    assert item.title == 'Title'
    assert item.date.startswith('1970-01-01')
    assert item.thumbnail == 'https://example.test/image.jpg'


@pytest.fixture
def browser(monkeypatch):
    state = SimpleNamespace(clock=0, closed=False, listener=None, replies=[], cancel=None, tick_seconds=0)
    page = Mock(url='https://www.instagram.com/example/reels/')

    def listen(event, callback):
        state.listener = callback

    def tick(milliseconds):
        state.clock += max(milliseconds / 1000, state.tick_seconds)
        if state.replies:
            state.listener(state.replies.pop(0))
        if state.cancel:
            state.cancel.set()

    def goto(*args, **kwargs):
        if state.replies:
            state.listener(state.replies.pop(0))
        return SimpleNamespace(status=200)

    page.on.side_effect = listen
    page.wait_for_timeout.side_effect = tick
    page.goto.side_effect = goto
    context = SimpleNamespace(new_page=lambda: page)

    @contextmanager
    def factory(cookies):
        try:
            yield context
        finally:
            state.closed = True

    monkeypatch.setattr(reels, 'instagram_browser', factory)
    monkeypatch.setattr(reels, 'time', SimpleNamespace(monotonic=lambda: state.clock))
    state.page = page
    return state


def test_browser_collects_pages_and_closes(browser):
    browser.replies = [response(['a'], more=True, cursor='next'), response(['b'])]
    result = reels.discover_browser_reels('example', 20, local_session())
    assert result.all_collected and len(result.items) == 2
    assert browser.closed
    browser.page.remove_listener.assert_called_once()


def test_browser_stall_preserves_partial_and_scrolls(browser):
    browser.replies = [response(['a'], more=True, cursor='next')]
    result = reels.discover_browser_reels('example', 20, local_session())
    assert result.error_code == 'discovery_incomplete'
    assert len(result.items) == 1
    assert browser.page.mouse.wheel.called and browser.closed


def test_progressing_scan_continues_past_three_minutes(browser):
    browser.tick_seconds = 3
    browser.replies = [response([f'video{i}'], more=True, cursor=f'cursor{i}') for i in range(250)]
    result = reels.discover_browser_reels('example', 250, local_session())
    assert browser.clock > 180
    assert result.complete and len(result.items) == 250
    assert not result.all_collected
    assert browser.closed


def test_idle_timeout_diagnostic_is_explicit(browser, caplog):
    browser.replies = [response(['a'], more=True, cursor='next')]
    result = reels.discover_browser_reels('example', 250, local_session())
    assert '25 segundos' in result.message
    assert 'Isso nao confirma bloqueio' in result.message
    assert 'reason=idle_timeout' in caplog.text


def test_browser_redirect_does_not_claim_empty_profile(browser):
    browser.page.url = 'https://www.instagram.com/accounts/login/'
    result = reels.discover_browser_reels('example', 20, local_session())
    assert result.error_code == 'session_unavailable'
    assert not result.all_collected and browser.closed


def test_browser_cancel_preserves_partial(browser):
    browser.cancel = Event()
    browser.replies = [response(['a'], more=True, cursor='next')]
    result = reels.discover_browser_reels('example', 20, local_session(), browser.cancel)
    assert result.error_code == 'cancelled'
    assert len(result.items) == 1 and browser.closed


def test_browser_pre_cancel_does_not_navigate(browser):
    cancel = Event()
    cancel.set()
    result = reels.discover_browser_reels('example', 20, local_session(), cancel)
    assert result.error_code == 'cancelled'
    browser.page.goto.assert_not_called()


def test_dedicated_reels_route_to_browser(monkeypatch):
    expected = DiscoveryResult([], True, 'Done')
    discovery = Mock(return_value=expected)
    monkeypatch.setattr(reels, 'discover_browser_reels', discovery)
    local = local_session()
    callback = Mock()
    result = discover_profile('@Example', 20, 'reels', on_item=callback, local_session=local)
    assert result is expected
    discovery.assert_called_once_with('example', 20, local, None, callback)


def test_dedicated_route_validates_before_browser(monkeypatch):
    discovery = Mock()
    monkeypatch.setattr(reels, 'discover_browser_reels', discovery)
    for overrides in ({'limit': 0}, {'login': 'other'}, {'profile': 'invalid/path'},
                      {'local_session': {'username': 'owner', 'cookies': []}}):
        args = {'profile': 'example', 'scope': 'reels', 'local_session': local_session(), **overrides}
        assert not discover_profile(**args).complete
    discovery.assert_not_called()
