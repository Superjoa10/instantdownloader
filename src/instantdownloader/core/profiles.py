"""Bounded Instagram discovery without downloads or persistence."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from os import PathLike, fspath
from threading import Event
from typing import Any, Literal
from urllib.parse import urlsplit


@dataclass(slots=True)
class DiscoveredVideo:
    id: str
    url: str
    title: str
    thumbnail: str
    date: str
    playlist_index: int | None = None


@dataclass(slots=True)
class DiscoveryResult:
    items: list[DiscoveredVideo]
    complete: bool
    message: str


class _DiscoveryStopped(Exception):
    pass


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
    scope: Literal["posts", "reels"] = "posts",
    cancel_event: Event | None = None,
    on_item: Callable[[DiscoveredVideo], None] | None = None,
    session_file: str | PathLike[str] | None = None,
    login: str | None = None,
) -> DiscoveryResult:
    """Inspect at most ``limit`` posts, including photos and duplicate posts.

    ``complete`` means the requested bounded scan succeeded, not that the whole
    profile was crawled. Invalid arguments, cancellation, and failures return an
    incomplete result. Callbacks run synchronously once per unique discovered
    item; persistence is the caller's responsibility. An item remains in the
    result if its callback fails, and discovery stops immediately.

    ``login`` loads an existing Instaloader session (its default file unless
    ``session_file`` is supplied). A session file requires its login username.
    No interactive login, browser-cookie extraction, or session writes occur.
    Cancellation is cooperative; an in-flight request has a 15-second timeout.

    Sidecar IDs are ``shortcode:video_index``. Their one-based playlist indices
    count videos only, matching yt-dlp's GraphQL node extractor. Its product API
    extractor may retain photo entries; download callers must account for that
    alternate representation before applying these indices.
    """
    result = DiscoveryResult([], False, "")
    loader = None
    inspected = 0
    stage = "validation"

    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise _DiscoveryStopped("Discovery cancelled.")

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
            raise ValueError("scope must be 'posts' or 'reels'.")
        if session_file is not None and not login:
            raise ValueError("session_file requires the session's login username.")
        session_username = normalize_profile(login) if login is not None else None
        check_cancelled()
        stage = "loading optional dependency"
        import instaloader

        class StopOnRateLimit(instaloader.RateController):
            def handle_429(self, query_type: str) -> None:
                raise _DiscoveryStopped("Instagram returned HTTP 429; discovery stopped without retrying.")

            def sleep(self, secs: float) -> None:
                check_cancelled()
                if secs > 0:
                    raise _DiscoveryStopped("Instagram rate-limit wait required; discovery stopped without waiting.")

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
        if session_username is not None:
            stage = "loading session"
            loader.load_session_from_file(
                session_username, filename=fspath(session_file) if session_file is not None else None,
            )
        check_cancelled()
        stage = "loading profile"
        account = instaloader.Profile.from_username(loader.context, username)
        check_cancelled()
        stage = f"reading {scope}"
        if scope == "reels" and not callable(getattr(account, "get_reels", None)):
            raise RuntimeError("This Instaloader version does not support reels; update Instaloader.")
        posts = checked(iter(account.get_reels() if scope == "reels" else account.get_posts()))
        seen: set[str] = set()
        for _ in range(limit):
            stage = f"reading {scope}"
            try:
                post = next(posts)
            except StopIteration:
                result.complete = True
                result.message = f"Feed exhausted after inspecting {inspected} posts; found {len(result.items)} videos."
                break
            inspected += 1
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
        result.message = f"{exc} Inspected {inspected} posts; retained {len(result.items)} videos."
    except ModuleNotFoundError as exc:
        if stage == "loading optional dependency" and exc.name == "instaloader":
            result.message = "Profile discovery requires the optional instaloader dependency."
        else:
            result.message = f"Discovery failed during {stage}: {type(exc).__name__}: {exc}"
    except Exception as exc:
        result.message = (
            f"Discovery failed during {stage}: {type(exc).__name__}: {exc}. "
            f"Inspected {inspected} posts; retained {len(result.items)} videos."
        )
    finally:
        if loader is not None:
            try:
                loader.close()
            except Exception as exc:
                result.complete = False
                result.message += f" Session cleanup failed: {type(exc).__name__}: {exc}."
    return result
