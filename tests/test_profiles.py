import builtins
import importlib
import sys
from datetime import datetime
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from instantdownloader.core import profiles


def post(shortcode="abc", *, video=True, nodes=None):
    return SimpleNamespace(
        shortcode=shortcode,
        typename="GraphSidecar" if nodes is not None else "GraphVideo" if video else "GraphImage",
        is_video=video if nodes is None else False,
        caption="Example caption",
        date_utc=datetime(2026, 1, 2, 3, 4, 5),
        url="https://cdn.example/post.jpg",
        get_sidecar_nodes=Mock(side_effect=lambda: iter(nodes)),
    )


def node(video, name):
    return SimpleNamespace(is_video=video, display_url=f"https://cdn.example/{name}.jpg")


@pytest.fixture
def backend(monkeypatch):
    class RateController:
        def __init__(self, context):
            self.context = context

        def wait_before_query(self, query_type):
            pass

    account = SimpleNamespace(username='example', userid=123, get_posts=Mock(return_value=iter(())), get_reels=Mock(return_value=iter(())))
    loader = Mock(context=object())
    module = SimpleNamespace(
        RateController=RateController,
        Instaloader=Mock(return_value=loader),
        Profile=SimpleNamespace(from_username=Mock(return_value=account)),
        NodeIterator=Mock(side_effect=lambda **kwargs: account.get_reels()),
        Post=SimpleNamespace(from_shortcode=Mock()),
    )
    monkeypatch.setitem(sys.modules, "instaloader", module)
    return SimpleNamespace(module=module, account=account, loader=loader)


@pytest.mark.parametrize("value", [
    "Example.user", "@Example.user", "instagram.com/Example.user",
    "www.instagram.com/Example.user/", "https://www.instagram.com/Example.user/?igsh=abc",
    "http://instagram.com/Example.user#section",
])
def test_accepts_only_profile_forms(backend, value):
    assert profiles.normalize_profile(value) == "example.user"
    result = profiles.discover_profile(value)
    assert result.complete
    backend.module.Profile.from_username.assert_called_once_with(backend.loader.context, "example.user")
    backend.account.get_reels.assert_not_called()
    backend.loader.load_session_from_file.assert_not_called()
    backend.loader.close.assert_called_once_with()


@pytest.mark.parametrize("value", [
    "", "@", "a b", "a..b", ".name", "name.", "a" * 31, "user/name", "@@name",
    "https://instagram.com/", "https://instagram.com/p/abc/",
    "https://instagram.com/reel/abc/", "https://instagram.com/name/reels/",
    "https://instagram.com/explore/", "https://instagram.com/p/",
    "https://example.com/name", "https://instagram.com.evil.test/name",
    "https://instagram.com@evil.test/name", "https://user@instagram.com/name",
    "https://instagram.com:443/name", "ftp://instagram.com/name",
    "https://instagram.com/%6eame", "https://instagram.com//name", None,
])
def test_invalid_profiles_do_not_construct_loader(backend, value):
    with pytest.raises(ValueError):
        profiles.normalize_profile(value)
    result = profiles.discover_profile(value)
    assert not result.complete
    assert result.items == []
    assert "validation" in result.message
    backend.module.Instaloader.assert_not_called()


def test_ui_import_contract():
    from dataclasses import fields, is_dataclass

    from instantdownloader.core.profiles import DiscoveredVideo, discover_profile, normalize_profile
    from instantdownloader.core.thumbnail import load_thumbnail

    assert normalize_profile("@Example") == "example"
    assert callable(discover_profile)
    assert callable(load_thumbnail)
    assert is_dataclass(DiscoveredVideo)
    assert [field.name for field in fields(DiscoveredVideo)] == [
        "id", "url", "title", "thumbnail", "date", "playlist_index", "sources",
    ]


def test_dedicated_session_loads_without_personal_browser(backend):
    local = {'username': 'owner', 'cookies': [
        {'name': 'sessionid', 'value': 'secret', 'domain': '.instagram.com'},
        {'name': 'csrftoken', 'value': 'csrf', 'domain': '.instagram.com'},
        {'name': 'other', 'value': 'ignored', 'domain': '.example.com'},
    ]}
    result = profiles.discover_profile('example', local_session=local)
    assert result.complete
    backend.loader.load_session.assert_called_once_with('owner', {'sessionid': 'secret', 'csrftoken': 'csrf'})
    backend.loader.load_session_from_file.assert_not_called()
    result = profiles.discover_profile('example', local_session=local, login='other')
    assert result.error_code == 'invalid_input'


def test_dedicated_session_provider_errors_are_sanitized(backend):
    local = {'username': 'owner', 'cookies': [
        {'name': 'sessionid', 'value': 'secret', 'domain': '.instagram.com'},
        {'name': 'csrftoken', 'value': 'csrf', 'domain': '.instagram.com'},
    ]}
    backend.loader.load_session.side_effect = RuntimeError('secret')
    result = profiles.discover_profile('example', local_session=local)
    assert not result.complete
    assert 'secret' not in result.message


@pytest.mark.parametrize("limit", [0, -1, 1001, 1.5, True, "10", None])
def test_limit_validation(backend, limit):
    result = profiles.discover_profile("example", limit=limit)
    assert not result.complete
    assert "between 1 and 1000" in result.message
    backend.module.Instaloader.assert_not_called()


def test_default_limit_counts_photos_and_never_looks_ahead(backend):
    def posts():
        for index in range(10):
            yield post(str(index), video=index == 0)
        pytest.fail("Discovery inspected more than the default ten posts")

    backend.account.get_posts.return_value = posts()
    result = profiles.discover_profile("example")
    assert result.complete
    assert [item.id for item in result.items] == ["0"]
    assert "limit reached (10 posts)" in result.message
    assert "Additional posts may exist" in result.message


@pytest.mark.parametrize("limit", [1, 1000])
def test_limit_boundaries(backend, limit):
    backend.account.get_posts.return_value = iter(post(str(i), video=False) for i in range(limit + 1))
    result = profiles.discover_profile("example", limit=limit)
    assert result.complete
    assert f"({limit} posts)" in result.message


def test_sidecars_video_indices_dedup_and_callback(backend):
    carousel = post("album", nodes=[node(False, "photo"), node(True, "first"),
                                    node(False, "photo2"), node(True, "second")])
    backend.account.get_posts.return_value = iter([post(), carousel, carousel, post(video=False), post()])
    saved = []
    result = profiles.discover_profile("example", on_item=saved.append)
    assert result.complete
    assert "Feed exhausted after inspecting 5 posts" in result.message
    assert saved == result.items
    assert [item.id for item in result.items] == ["abc", "album:1", "album:2"]
    assert [item.playlist_index for item in result.items] == [None, 1, 2]
    assert result.items[1].thumbnail == "https://cdn.example/first.jpg"
    assert result.items[2].thumbnail == "https://cdn.example/second.jpg"
    assert result.items[1].url == "https://www.instagram.com/p/album/"
    assert result.items[0].date == "2026-01-02T03:04:05"
    assert result.items[0].title == "Example caption"


def test_reels_scope_does_not_fall_back_to_posts(backend):
    backend.account.get_reels.return_value = iter([post()])
    assert profiles.discover_profile("example", scope="reels").complete
    backend.account.get_posts.assert_not_called()
    backend.module.NodeIterator.side_effect = profiles.ReelsUnavailableError('Reels unavailable')
    result = profiles.discover_profile("example", scope="reels")
    assert not result.complete
    assert result.error_code == 'session_unavailable'
    assert result.failed_source == 'reels'
    backend.account.get_posts.assert_not_called()


def test_reels_uses_known_profile_id_and_web_endpoint(backend):
    backend.account._obtain_metadata = Mock(side_effect=AssertionError('Redundant metadata lookup'))
    profiles.discover_profile('example', scope='reels')
    options = backend.module.NodeIterator.call_args.kwargs
    assert options['web_api'] is True
    assert options['query_variables']['data']['target_user_id'] == '123'
    assert options['query_referer'] == 'https://www.instagram.com/example/reels/'
    assert options['edge_extractor'] is profiles._reels_connection
    backend.account._obtain_metadata.assert_not_called()


@pytest.mark.parametrize('connection', [None, {}, {'edges': []}, {'edges': [], 'page_info': {}},
    {'edges': [], 'page_info': {'has_next_page': True, 'end_cursor': 'next'}}])
def test_null_or_incomplete_reels_is_not_an_empty_success(connection):
    with pytest.raises(profiles.ReelsUnavailableError):
        profiles._reels_connection({'data': {'xdt_api__v1__clips__user__connection_v2': connection}})


def test_reels_real_paginator_uses_web_endpoint_on_every_page(monkeypatch):
    instaloader = pytest.importorskip('instaloader')
    def response(code, more):
        return {'data': {'xdt_api__v1__clips__user__connection_v2': {
            'edges': [{'node': {'media': {'code': code}}}],
            'page_info': {'has_next_page': more, 'end_cursor': code},
        }}}
    context = SimpleNamespace(doc_id_graphql_query=Mock(side_effect=[response('first', True), response('second', False)]))
    monkeypatch.setattr(instaloader.Post, 'from_shortcode', lambda context, code: code)
    iterator = profiles._iter_reels(context, SimpleNamespace(userid=123, username='example'))
    assert list(iterator) == ['first', 'second']
    calls = context.doc_id_graphql_query.call_args_list
    assert len(calls) == 2 and all(call.args[3] is True for call in calls)
    assert calls[1].args[1]['after'] == 'first'


def test_explicit_empty_reels_connection_is_valid():
    connection = {'edges': [], 'page_info': {'has_next_page': False, 'end_cursor': None}}
    assert profiles._reels_connection({'data': {'xdt_api__v1__clips__user__connection_v2': connection}}) == connection


def test_anonymous_discovery_never_reads_browser_cookies(backend, monkeypatch):
    read = Mock(side_effect=AssertionError('Unauthorized cookie read'))
    monkeypatch.setattr('yt_dlp.cookies.extract_cookies_from_browser', read)
    assert profiles.discover_profile('example', scope='all').complete
    read.assert_not_called()


def cookie(name, value, domain='.instagram.com', expired=False):
    return SimpleNamespace(name=name, value=value, domain=domain, is_expired=lambda: expired)


def test_explicit_browser_session_filters_domains_and_never_saves(backend, monkeypatch):
    read = Mock(return_value=[cookie('sessionid', 'session'), cookie('csrftoken', 'csrf'),
        cookie('sessionid', 'wrong', 'notinstagram.com'), cookie('csrftoken', 'wrong', 'instagram.com.evil.test'),
        cookie('sessionid', 'expired', expired=True), cookie('unrelated', 'unused')])
    monkeypatch.setattr('yt_dlp.cookies.extract_cookies_from_browser', read)
    result = profiles.discover_profile('example', scope='reels', browser='chrome', login='owner')
    assert result.complete
    read.assert_called_once_with('chrome')
    backend.loader.load_session.assert_called_once_with('owner', {'sessionid': 'session', 'csrftoken': 'csrf'})
    backend.loader.save_session_to_file.assert_not_called()
    backend.loader.load_session_from_file.assert_not_called()
    assert 'csrf' not in result.message and 'sessionid' not in result.message


@pytest.mark.parametrize('kwargs', [{'browser': 'chrome'}, {'browser': 'unknown', 'login': 'owner'},
                                   {'browser': 'chrome', 'login': 'owner', 'session_file': 'session'}])
def test_invalid_browser_settings_do_not_read_cookies(backend, monkeypatch, kwargs):
    read = Mock(side_effect=AssertionError('No cookie read'))
    monkeypatch.setattr('yt_dlp.cookies.extract_cookies_from_browser', read)
    result = profiles.discover_profile('example', **kwargs)
    assert result.error_code == 'invalid_input'
    read.assert_not_called()


def test_missing_browser_session_never_falls_back_to_anonymous(backend, monkeypatch):
    monkeypatch.setattr('yt_dlp.cookies.extract_cookies_from_browser', Mock(return_value=[]))
    result = profiles.discover_profile('example', browser='chrome', login='owner')
    assert result.error_code == 'browser_session' and not result.complete
    backend.module.Profile.from_username.assert_not_called()


def test_cookie_read_failure_does_not_leak_exception_secrets(backend, monkeypatch):
    monkeypatch.setattr('yt_dlp.cookies.extract_cookies_from_browser', Mock(side_effect=RuntimeError('secret-cookie-value')))
    result = profiles.discover_profile('example', browser='chrome', login='owner')
    assert result.error_code == 'browser_session'
    assert 'secret-cookie-value' not in result.message


def test_invalid_scope(backend):
    result = profiles.discover_profile("example", scope="stories")
    assert not result.complete
    backend.module.Instaloader.assert_not_called()


def test_combined_deduplicates_and_counts_overlapping_sources(backend):
    backend.account.get_posts.return_value = iter([post('shared'), post('feed'), post(video=False)])
    backend.account.get_reels.return_value = iter([post('shared'), post('reel')])
    emitted = []
    result = profiles.discover_profile('example', scope='all', on_item=emitted.append)
    assert result.complete and result.all_collected
    assert result.counts == {'posts': 2, 'reels': 2, 'unique': 3}
    assert result.inspected == {'posts': 3, 'reels': 2}
    assert result.exhausted == {'posts': True, 'reels': True}
    assert result.items[0].sources == ('posts', 'reels')
    assert [item.id for item in emitted] == ['shared', 'feed', 'shared', 'reel']


def test_combined_limit_is_per_source_and_not_full_collection(backend):
    backend.account.get_posts.return_value = iter([post('one'), post('two')])
    backend.account.get_reels.return_value = iter([post('reel'), post('other')])
    result = profiles.discover_profile('example', scope='all', limit=1)
    assert result.complete and not result.all_collected
    assert result.inspected == {'posts': 1, 'reels': 1}
    assert result.counts == {'posts': 1, 'reels': 1, 'unique': 2}


def test_combined_rate_limit_does_not_request_next_source(backend):
    def feed():
        yield post('one')
        raise RuntimeError('HTTP 429')
    backend.account.get_posts.return_value = feed()
    result = profiles.discover_profile('example', scope='all')
    assert not result.complete and not result.all_collected
    assert result.counts['unique'] == 1
    backend.account.get_reels.assert_not_called()


def test_combined_reels_failure_preserves_feed_and_partial_reels(backend):
    def reels():
        yield post('reel')
        raise RuntimeError('login required')
    backend.account.get_posts.return_value = iter([post('feed')])
    backend.account.get_reels.return_value = reels()
    result = profiles.discover_profile('example', scope='all')
    assert not result.complete and not result.all_collected
    assert result.exhausted == {'posts': True, 'reels': False}
    assert result.counts == {'posts': 1, 'reels': 1, 'unique': 2}
    assert 'login required' in result.message


def test_combined_cancellation_preserves_items_without_requesting_reels(backend):
    event = Event()
    backend.account.get_posts.return_value = iter([post()])
    result = profiles.discover_profile('example', scope='all', cancel_event=event, on_item=lambda _: event.set())
    assert not result.complete and len(result.items) == 1
    backend.account.get_reels.assert_not_called()


@pytest.mark.parametrize("error", [RuntimeError("HTTP 429"), TimeoutError("timed out"),
                                        PermissionError("private profile")])
def test_partial_results_on_iteration_error(backend, error):
    def posts():
        yield post()
        raise error

    backend.account.get_posts.return_value = posts()
    saved = []
    result = profiles.discover_profile("example", on_item=saved.append)
    assert not result.complete
    assert [item.id for item in result.items] == ["abc"]
    assert saved == result.items
    assert str(error) in result.message
    backend.loader.close.assert_called_once_with()


def test_sidecar_failure_preserves_already_yielded_video(backend):
    def nodes():
        yield node(True, "first")
        raise RuntimeError("sidecar interrupted")

    carousel = post(nodes=[])
    carousel.get_sidecar_nodes.side_effect = nodes
    backend.account.get_posts.return_value = iter([carousel])
    result = profiles.discover_profile("example")
    assert not result.complete
    assert [item.id for item in result.items] == ["abc:1"]
    assert "sidecar interrupted" in result.message


def test_cancellation_before_start(backend):
    event = Event()
    event.set()
    result = profiles.discover_profile("example", cancel_event=event)
    assert not result.complete
    assert "cancelled" in result.message
    backend.module.Instaloader.assert_not_called()


def test_cancellation_in_callback_stops_with_partial_sidecar(backend):
    event = Event()
    backend.account.get_posts.return_value = iter([post(nodes=[node(True, "one"), node(True, "two")])])
    result = profiles.discover_profile("example", limit=1, cancel_event=event, on_item=lambda _: event.set())
    assert not result.complete
    assert [item.id for item in result.items] == ["abc:1"]
    assert "cancelled" in result.message


def test_cancellation_during_next_discards_unprocessed_post(backend):
    event = Event()

    def posts():
        event.set()
        yield post()

    backend.account.get_posts.return_value = posts()
    result = profiles.discover_profile("example", cancel_event=event)
    assert not result.complete
    assert result.items == []


def test_callback_failure_is_explicit_and_preserves_item(backend):
    backend.account.get_posts.return_value = iter([post(), post("next")])
    callback = Mock(side_effect=OSError("disk full"))
    result = profiles.discover_profile("example", on_item=callback)
    assert not result.complete
    assert len(result.items) == 1
    assert "on_item callback" in result.message
    assert "disk full" in result.message
    callback.assert_called_once_with(result.items[0])


def test_explicit_session(backend, tmp_path):
    session = tmp_path / "session"
    assert profiles.discover_profile("example", login="@Owner", session_file=session).complete
    backend.loader.load_session_from_file.assert_called_once_with("owner", filename=str(session))
    backend.loader.login.assert_not_called()
    backend.loader.save_session_to_file.assert_not_called()


def test_explicit_login_loads_default_session(backend):
    assert profiles.discover_profile("example", login="owner").complete
    backend.loader.load_session_from_file.assert_called_once_with("owner", filename=None)


def test_session_without_login_is_rejected(backend):
    result = profiles.discover_profile("example", session_file="session")
    assert not result.complete
    assert "requires" in result.message
    backend.module.Instaloader.assert_not_called()


def test_session_failure_never_falls_back_to_anonymous(backend):
    backend.loader.load_session_from_file.side_effect = FileNotFoundError("session missing")
    result = profiles.discover_profile("example", login="owner")
    assert not result.complete
    assert "loading session" in result.message
    backend.module.Profile.from_username.assert_not_called()
    backend.loader.close.assert_called_once_with()


def test_request_bounds_and_rate_controller(backend):
    profiles.discover_profile("example")
    options = backend.module.Instaloader.call_args.kwargs
    assert options["request_timeout"] == 15
    assert options["max_connection_attempts"] == 1
    assert options["fatal_status_codes"] == [429]
    assert options["sleep"] is False
    controller = options["rate_controller"](backend.loader.context)
    assert isinstance(controller, backend.module.RateController)
    with pytest.raises(Exception, match="HTTP 429"):
        controller.handle_429("graphql")
    with pytest.raises(Exception, match="without waiting"):
        controller.sleep(600)


def test_rate_limit_controller_error_returns_partial_results(backend):
    def posts():
        yield post()
        options = backend.module.Instaloader.call_args.kwargs
        options["rate_controller"](backend.loader.context).handle_429("graphql")
        pytest.fail("Discovery retried after HTTP 429")

    backend.account.get_posts.return_value = posts()
    result = profiles.discover_profile("example")
    assert not result.complete
    assert len(result.items) == 1
    assert "HTTP 429" in result.message


def test_local_pacing_is_not_reported_as_remote_rate_limit(backend):
    def feed():
        options = backend.module.Instaloader.call_args.kwargs
        options['rate_controller'](backend.loader.context).sleep(10)
        pytest.fail('Discovery continued after local pacing stop')
        yield

    backend.account.get_posts.return_value = feed()
    result = profiles.discover_profile('example', scope='all')
    assert result.error_code == 'local_throttle'
    assert result.failed_source == 'posts'
    assert not result.complete
    assert 'does not confirm' in result.message
    backend.account.get_reels.assert_not_called()


@pytest.mark.parametrize('message,status,reason', [
    ('HTTP 429', 429, 'http_429'),
    ('Query responded with "429 Too Many Requests"', 429, 'http_429'),
    ('401 Unauthorized - Please wait a few minutes', 401, 'provider_wait_message'),
    ('400 Bad Request - feedback_required', 400, 'feedback_required'),
    ('Redirected to login page. You have been logged out, please wait', None, 'login_redirect'),
    ('400 Bad Request - challenge_required', 400, 'challenge_required'),
    ('https://example.test/profile429?token=secret', None, 'unclassified'),
])
def test_provider_evidence_uses_fixed_labels(message, status, reason):
    assert profiles.failure_evidence(RuntimeError(message)) == (status, reason)


def test_login_redirect_is_not_rate_limit_and_diagnostics_are_safe(backend, caplog):
    local = {'username': 'owner', 'cookies': [
        {'name': 'sessionid', 'value': 'secret', 'domain': '.instagram.com'},
        {'name': 'csrftoken', 'value': 'csrf', 'domain': '.instagram.com'},
    ]}
    backend.module.Profile.from_username.side_effect = RuntimeError(
        'Redirected to login page. Please wait. https://example.test/?token=secret')
    result = profiles.discover_profile('example', local_session=local)
    assert result.error_code == 'session_unavailable'
    assert 'login_redirect' in result.message
    assert 'operation=loading_profile' in caplog.text
    assert 'reason=login_redirect' in caplog.text
    assert 'token=secret' not in result.message + caplog.text


def test_rate_controller_checks_cancellation_before_queries(backend):
    event = Event()
    profiles.discover_profile("example", cancel_event=event)
    controller = backend.module.Instaloader.call_args.kwargs["rate_controller"](backend.loader.context)
    event.set()
    with pytest.raises(Exception, match="cancelled"):
        controller.wait_before_query("graphql")


def test_missing_optional_dependency(monkeypatch):
    monkeypatch.setitem(sys.modules, "instaloader", None)
    result = profiles.discover_profile("example")
    assert not result.complete
    assert "optional instaloader dependency" in result.message


def test_instaloader_import_is_lazy(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "instaloader":
            pytest.fail("Instaloader was imported at module load time")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    importlib.reload(profiles)


def test_cleanup_error_does_not_lose_results(backend):
    backend.account.get_posts.return_value = iter([post()])
    backend.loader.close.side_effect = OSError("close failed")
    result = profiles.discover_profile("example")
    assert not result.complete
    assert len(result.items) == 1
    assert "cleanup failed" in result.message


def test_installed_ytdlp_graphql_extractor_filters_photos():
    instagram = pytest.importorskip("yt_dlp.extractor.instagram")
    nodes = [
        {"id": "photo", "is_video": False},
        {"id": "first", "is_video": True, "video_url": "https://cdn.example/first.mp4",
         "dimensions": {"width": 640, "height": 480}},
        {"id": "photo2", "is_video": False},
        {"id": "second", "is_video": True, "video_url": "https://cdn.example/second.mp4",
         "dimensions": {"width": 640, "height": 480}},
    ]
    entries = list(instagram.InstagramIE()._extract_nodes(nodes, is_direct=True))
    assert [entry["id"] for entry in entries] == ["first", "second"]


def test_installed_ytdlp_product_extractor_retains_photo_positions():
    instagram = pytest.importorskip("yt_dlp.extractor.instagram")
    product = {"carousel_media": [
        {"pk": "1"},
        {"pk": "2", "video_versions": [{"url": "https://cdn.example/first.mp4"}]},
        {"pk": "3"},
        {"pk": "4", "video_versions": [{"url": "https://cdn.example/second.mp4"}]},
    ]}
    entries = instagram.InstagramIE()._extract_product(product, get_comments=False)["entries"]
    # Discovery's video-only indices need reconciliation on this extraction path.
    assert [index for index, entry in enumerate(entries, 1) if entry["formats"]] == [2, 4]


def test_real_instaloader_constructor_with_mocked_profile(monkeypatch):
    instaloader = pytest.importorskip("instaloader")
    requests = pytest.importorskip("requests")
    network = Mock(side_effect=AssertionError("No network is allowed in this test"))
    monkeypatch.setattr(requests.Session, "request", network)
    account = SimpleNamespace(get_posts=lambda: iter(()))
    monkeypatch.setattr(instaloader.Profile, "from_username", Mock(return_value=account))
    result = profiles.discover_profile("example")
    assert result.complete
    network.assert_not_called()
