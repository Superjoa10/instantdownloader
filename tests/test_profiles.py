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

    account = SimpleNamespace(get_posts=Mock(return_value=iter(())), get_reels=Mock(return_value=iter(())))
    loader = Mock(context=object())
    module = SimpleNamespace(
        RateController=RateController,
        Instaloader=Mock(return_value=loader),
        Profile=SimpleNamespace(from_username=Mock(return_value=account)),
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
        "id", "url", "title", "thumbnail", "date", "playlist_index",
    ]


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
    del backend.account.get_reels
    result = profiles.discover_profile("example", scope="reels")
    assert not result.complete
    assert "does not support reels" in result.message
    backend.account.get_posts.assert_not_called()


def test_invalid_scope(backend):
    result = profiles.discover_profile("example", scope="stories")
    assert not result.complete
    backend.module.Instaloader.assert_not_called()


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
