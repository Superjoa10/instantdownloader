"""Bounded Instagram discovery without downloads or persistence."""

from __future__ import annotations

import re
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from os import PathLike, fspath
from threading import Event
from typing import Any, Literal
from urllib.parse import urlsplit

LOG = logging.getLogger('instantdownloader.session')


def failure_evidence(exc):
    """Extract only fixed diagnostic labels; never expose provider URLs or bodies."""
    text = str(exc).lower()
    status = None
    match = re.search(r'\bhttp[ :]+([45]\d\d)\b|responded with [\"\x27]([45]\d\d)\b', text)
    if match:
        status = int(next(value for value in match.groups() if value))
    for code, phrase in ((429, '429 too many requests'), (401, '401 unauthorized'),
                         (403, '403 forbidden'), (400, '400 bad request')):
        if phrase in text:
            status = code
    if type(exc).__name__ == 'TooManyRequestsException':
        status = 429
    if 'redirected to login page' in text:
        reason = 'login_redirect'
    elif 'challenge_required' in text or 'checkpoint_required' in text:
        reason = 'challenge_required'
    elif status == 429:
        reason = 'http_429'
    elif 'feedback_required' in text:
        reason = 'feedback_required'
    elif 'please wait' in text:
        reason = 'provider_wait_message'
    else:
        reason = 'unclassified'
    return status, reason


@dataclass(slots=True)
class DiscoveredVideo:
    id: str
    url: str
    title: str
    thumbnail: str
    date: str
    playlist_index: int | None = None
    sources: tuple[str, ...] = ()


@dataclass(slots=True)
class DiscoveryResult:
    items: list[DiscoveredVideo]
    complete: bool
    message: str
    inspected: dict[str, int] = field(default_factory=dict)
    exhausted: dict[str, bool] = field(default_factory=dict)
    error_code: str | None = None
    failed_source: str | None = None
    pages: dict[str, int] = field(default_factory=dict)

    @property
    def counts(self) -> dict[str, int]:
        return {**{source: sum(source in item.sources for item in self.items)
                   for source in ("posts", "reels")}, "unique": len(self.items)}

    @property
    def all_collected(self) -> bool:
        return self.complete and bool(self.exhausted) and all(self.exhausted.values())


def _discover_combined(profile, limit, cancel_event, on_item, session_file, login, browser, local_session):
    result = DiscoveryResult([], False, "", exhausted={"posts": False, "reels": False})
    positions: dict[str, int] = {}
    messages = []

    def collect(item):
        if item.id in positions:
            index = positions[item.id]
            previous = result.items[index]
            item = replace(previous, sources=tuple(dict.fromkeys((*previous.sources, *item.sources))))
            result.items[index] = item
        else:
            positions[item.id] = len(result.items)
            result.items.append(item)
        if on_item:
            on_item(item)

    for source in ("posts", "reels"):
        part = discover_profile(profile, limit, source, cancel_event, collect, session_file, login, browser, local_session)
        result.inspected.update(part.inspected)
        result.exhausted.update(part.exhausted)
        result.pages.update(part.pages)
        messages.append(f"{source}: {part.message}")
        if not part.complete:
            result.error_code = part.error_code
            result.failed_source = part.failed_source
            break
    else:
        result.complete = True
    result.message = " ".join(messages)
    return result


class _DiscoveryStopped(Exception):
    def __init__(self, message: str, code: str = 'rate_limited'):
        super().__init__(message)
        self.code = code


class ReelsUnavailableError(Exception):
    pass


class BrowserSessionError(Exception):
    pass


def _reels_connection(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get('data') or {}
    connection = data.get('xdt_api__v1__clips__user__connection_v2')
    # A null connection means unavailable, not an empty or exhausted profile.
    if not isinstance(connection, dict):
        raise ReelsUnavailableError('Reels unavailable in this session. Configure an authorized Instagram session.')
    edges, page = connection.get('edges'), connection.get('page_info')
    if (not isinstance(edges, list) or not isinstance(page, dict)
            or type(page.get('has_next_page')) is not bool
            or (page['has_next_page'] and (not edges or not page.get('end_cursor')))):
        raise ReelsUnavailableError('Instagram returned an incomplete Reels response; collection cannot be confirmed.')
    return connection


def _iter_reels(context: Any, account: Any) -> Iterator[Any]:
    import instaloader

    # Instaloader 4.16 get_reels() still reloads profile metadata and uses the old
    # GraphQL endpoint. Reuse its paginator on the web endpoint with the known ID.
    return instaloader.NodeIterator(
        context=context,
        query_hash=None,
        doc_id='7845543455542541',
        query_variables={'data': {'page_size': 12, 'include_feed_video': True,
                                  'target_user_id': str(account.userid)}},
        query_referer=f'https://www.instagram.com/{account.username}/reels/',
        edge_extractor=_reels_connection,
        node_wrapper=lambda node: instaloader.Post.from_shortcode(context, node['media']['code']),
        web_api=True,
    )


def _load_browser_session(loader: Any, browser: str, username: str) -> None:
    from yt_dlp.cookies import extract_cookies_from_browser

    try:
        jar = extract_cookies_from_browser(browser)
        values = {
            cookie.name: cookie.value for cookie in jar
            if (cookie.domain.lstrip('.') == 'instagram.com'
                or cookie.domain.lstrip('.').endswith('.instagram.com'))
            and not cookie.is_expired()
            and cookie.name in {'sessionid', 'csrftoken', 'ds_user_id', 'mid', 'ig_did', 'rur', 'datr'}
        }
    except Exception:
        raise BrowserSessionError('Could not read browser cookies. Check browser access or use an Instaloader session file.') from None
    if not values.get('sessionid') or not values.get('csrftoken'):
        raise BrowserSessionError('No active Instagram session found in the selected browser. Sign in there first.')
    loader.load_session(username, values)


_USERNAME = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?")
_RESERVED = {
    "p", "reel", "reels", "tv", "stories", "explore", "accounts",
    "direct", "about", "developer", "developers", "legal", "privacy",
    "terms", "web", "api", "share", "challenge", "oauth",
}


def normalize_profile(profile: str) -> str:
    """Return a lowercase username from a profile reference, or raise ValueError."""
    if not isinstance(profile, str):
        raise ValueError("Profile must be a username, @username, or Instagram profile URL.")
    value = profile.strip()
    if value.lower().startswith(("instagram.com/", "www.instagram.com/")):
        value = "https://" + value
    if "://" in value:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.netloc.lower() not in {"instagram.com", "www.instagram.com"}
            or not re.fullmatch(r"/[^/]+/?", parsed.path)
        ):
            raise ValueError("Use an Instagram profile URL, not a post or other page URL.")
        value = parsed.path.strip("/")
    elif value.startswith("@"):
        value = value[1:]
    if not _USERNAME.fullmatch(value) or ".." in value or value.lower() in _RESERVED:
        raise ValueError("Invalid Instagram profile username (post links are not profiles).")
    return value.lower()


def discover_profile(
    profile: str,
    limit: int = 10,
    scope: Literal["posts", "reels", "all"] = "posts",
    cancel_event: Event | None = None,
    on_item: Callable[[DiscoveredVideo], None] | None = None,
    session_file: str | PathLike[str] | None = None,
    login: str | None = None,
    browser: str | None = None,
    local_session: dict | None = None,
) -> DiscoveryResult:
    """Inspect at most ``limit`` posts per source, including photos and duplicates.

    ``complete`` means the requested bounded scan succeeded, not that the whole
    profile was crawled. Invalid arguments, cancellation, and failures return an
    incomplete result. Combined scans emit an updated item when its ID occurs
    in both sources; callbacks must upsert by ID. Persistence is the caller's
    responsibility. An item remains in the
    result if its callback fails, and discovery stops immediately.

    ``login`` loads an existing Instaloader session (its default file unless
    ``session_file`` is supplied). A session file requires its login username.
    ``browser`` explicitly opts into importing Instagram cookies in memory and
    requires ``login``; it is mutually exclusive with ``session_file``. No browser
    cookies are read by default. No interactive login or session writes occur.
    Cancellation is cooperative; Instaloader requests have a 15-second timeout.

    ``local_session`` is the dedicated browser's authorized username/cookie
    state. It is mutually exclusive with legacy authentication options and is
    never included in results. Callers must not log this argument.
    For Reels it loads the selected profile in an isolated headless browser,
    collecting native paginated responses without a fixed GraphQL document ID.
    Navigation has a 30-second timeout; the scan stops after 25 seconds without
    a new page, preserving incomplete results. Progressing scans are bounded
    by the requested inspection limit, not by a fixed total duration.

    Sidecar IDs are ``shortcode:video_index``. Their one-based playlist indices
    count videos only, matching yt-dlp's GraphQL node extractor. Its product API
    extractor may retain photo entries; download callers must account for that
    alternate representation before applying these indices.
    """
    if scope == "all":
        return _discover_combined(profile, limit, cancel_event, on_item, session_file, login, browser, local_session)
    result = DiscoveryResult([], False, "")
    loader = None
    inspected = 0
    stage = "validation"

    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise _DiscoveryStopped("Discovery cancelled.", 'cancelled')

    def checked(iterator: Iterator[Any]) -> Iterator[Any]:
        while True:
            check_cancelled()
            try:
                value = next(iterator)
            except StopIteration:
                return
            check_cancelled()
            yield value

    try:
        username = normalize_profile(profile)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000.")
        if scope not in {"posts", "reels"}:
            raise ValueError("scope must be 'posts', 'reels', or 'all'.")
        result.inspected[scope] = 0
        result.exhausted[scope] = False
        if session_file is not None and not login:
            raise ValueError("session_file requires the session's login username.")
        if browser is not None:
            if browser not in {'chrome', 'firefox', 'edge', 'safari'}:
                raise ValueError('Unsupported discovery browser.')
            if not login or session_file is not None:
                raise ValueError('Discovery browser requires login and cannot be combined with session_file.')
        session_username = normalize_profile(login) if login is not None else None
        if local_session is not None:
            if browser or session_file or login:
                raise ValueError('Dedicated session cannot be combined with other login options.')
            session_username = normalize_profile(local_session['username'])
            from .instagram_session import instagram_cookies
            values = {c['name']: c['value'] for c in instagram_cookies(local_session['cookies'])}
            if not values.get('sessionid') or not values.get('csrftoken'):
                raise BrowserSessionError('Dedicated session expired. Reconnect Instagram.')
        check_cancelled()
        if local_session is not None and scope == 'reels':
            from .browser_reels import discover_browser_reels
            return discover_browser_reels(username, limit, local_session, cancel_event, on_item)
        stage = "loading optional dependency"
        import instaloader

        class StopOnRateLimit(instaloader.RateController):
            def handle_429(self, query_type: str) -> None:
                raise _DiscoveryStopped("Instagram returned HTTP 429; discovery stopped without retrying.")

            def sleep(self, secs: float) -> None:
                check_cancelled()
                if secs > 0:
                    raise _DiscoveryStopped(
                        "Local request pacing requires a wait; discovery stopped without waiting. "
                        "This does not confirm an HTTP 429 response.", 'local_throttle')

            def wait_before_query(self, query_type: str) -> None:
                check_cancelled()
                super().wait_before_query(query_type)

        stage = "initializing Instaloader"
        loader = instaloader.Instaloader(
            quiet=True,
            sleep=False,
            max_connection_attempts=1,
            request_timeout=15,
            rate_controller=StopOnRateLimit,
            fatal_status_codes=[429],
        )
        if local_session is not None:
            stage = 'loading dedicated session'
            loader.load_session(session_username, values)
        elif browser is not None:
            stage = 'loading browser session'
            _load_browser_session(loader, browser, session_username)
        elif session_username is not None:
            stage = "loading session"
            loader.load_session_from_file(
                session_username, filename=fspath(session_file) if session_file is not None else None,
            )
        check_cancelled()
        stage = "loading profile"
        account = instaloader.Profile.from_username(loader.context, username)
        check_cancelled()
        stage = f"reading {scope}"
        posts = checked(iter(_iter_reels(loader.context, account) if scope == "reels" else account.get_posts()))
        seen: set[str] = set()
        for _ in range(limit):
            stage = f"reading {scope}"
            try:
                post = next(posts)
            except StopIteration:
                result.complete = True
                result.exhausted[scope] = True
                result.message = f"Feed exhausted after inspecting {inspected} posts; found {len(result.items)} videos."
                break
            inspected += 1
            result.inspected[scope] = inspected
            stage = "reading post metadata"
            sidecar = post.typename == "GraphSidecar"
            if not sidecar and not post.is_video:
                continue
            shortcode = str(post.shortcode)
            title = post.caption or f"Instagram video {shortcode}"
            date = post.date_utc.isoformat()
            url = f"https://www.instagram.com/p/{shortcode}/"
            nodes = checked(iter(post.get_sidecar_nodes())) if sidecar else iter((post,))
            video_index = 0
            for node in nodes:
                check_cancelled()
                if not node.is_video:
                    continue
                video_index += 1
                item_id = f"{shortcode}:{video_index}" if sidecar else shortcode
                if item_id in seen:
                    continue
                item = DiscoveredVideo(
                    id=item_id,
                    url=url,
                    title=title,
                    thumbnail=node.display_url if sidecar else post.url,
                    date=date,
                    playlist_index=video_index if sidecar else None,
                    sources=(scope,),
                )
                check_cancelled()
                seen.add(item_id)
                result.items.append(item)
                if on_item is not None:
                    stage = "on_item callback"
                    on_item(item)
                check_cancelled()
                stage = "reading post metadata"
        else:
            check_cancelled()
            result.complete = True
            result.message = (
                f"Inspection limit reached ({inspected} posts); found {len(result.items)} videos. "
                "Additional posts may exist."
            )
    except _DiscoveryStopped as exc:
        result.error_code = exc.code
        result.message = f"{exc} Inspected {inspected} posts; retained {len(result.items)} videos."
    except ModuleNotFoundError as exc:
        result.error_code = 'dependency_missing'
        if stage == "loading optional dependency" and exc.name == "instaloader":
            result.message = "Profile discovery requires the optional instaloader dependency."
        else:
            result.message = f"Discovery failed during {stage}: {type(exc).__name__}: {exc}"
    except Exception as exc:
        name = type(exc).__name__
        http_status, reason = failure_evidence(exc)
        LOG.warning('stage=profile_failure source=%s operation=%s error_type=%s http_status=%s reason=%s inspected=%d',
                    scope, stage.replace(' ', '_'), name, http_status, reason, inspected)
        rate_limited = reason in {'http_429', 'provider_wait_message', 'feedback_required'}
        result.error_code = (
            'rate_limited' if rate_limited else
            'session_unavailable' if reason in {'login_redirect', 'challenge_required'} or isinstance(exc, ReelsUnavailableError) or name in {
                'ProfileNotExistsException', 'LoginRequiredException', 'PrivateProfileNotFollowedException'} else
            'browser_session' if isinstance(exc, BrowserSessionError) else
            'invalid_input' if stage == 'validation' else 'discovery_failed'
        )
        detail = type(exc).__name__ if local_session is not None else f'{type(exc).__name__}: {exc}'
        result.message = (
            f"Discovery failed during {stage}: {detail}. "
            f"Inspected {inspected} posts; retained {len(result.items)} videos."
        )
        result.message += f" Evidence: {reason}; HTTP status: {http_status if http_status is not None else 'unknown'}."
        if type(exc).__name__ in {"ProfileNotExistsException", "LoginRequiredException"}:
            result.message += (
                " Profile metadata may be unavailable to this session; this is not a verified zero-video count."
                " Check the username and configure an existing authorized Instaloader session."
            )
    finally:
        if loader is not None:
            try:
                loader.close()
            except Exception as exc:
                result.complete = False
                result.error_code = 'discovery_failed'
                detail = type(exc).__name__ if local_session is not None else f'{type(exc).__name__}: {exc}'
                result.message += f" Session cleanup failed: {detail}."
    if not result.complete:
        result.failed_source = scope
    return result
