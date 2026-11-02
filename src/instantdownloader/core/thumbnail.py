"""Bounded thumbnail fetching and decoding; no GUI-thread operations."""

from __future__ import annotations

from io import BytesIO
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image


MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_SIZE = (640, 480)
TIMEOUT = 8


def _validate_url(url: str) -> None:
    if not isinstance(url, str) or any(char.isspace() or ord(char) < 32 for char in url):
        raise ValueError("Thumbnail URL must be an HTTP(S) URL without whitespace.")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Thumbnail URL must be an HTTP(S) URL without credentials.")
    # Accessing port also validates malformed or out-of-range port numbers.
    _ = parsed.port


class _HTTPOnlyRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def load_thumbnail(url: str) -> Image.Image:
    """Return a detached RGB image fitting 640x480, without upscaling.

    Requests use an eight-second socket timeout. Responses are limited to 5 MiB
    and decoded images to 20 million pixels. Invalid URLs/limits raise ValueError;
    transport and Pillow decode errors propagate. Redirects must also be HTTP(S).
    """
    _validate_url(url)
    request = Request(url, headers={"User-Agent": "instantdownloader", "Accept": "image/*"})
    opener = build_opener(_HTTPOnlyRedirects())
    with opener.open(request, timeout=TIMEOUT) as response:
        _validate_url(response.geturl())
        length = response.headers.get("Content-Length")
        if length is not None:
            try:
                declared_size = int(length)
            except (TypeError, ValueError) as exc:
                raise ValueError("Invalid thumbnail Content-Length.") from exc
            if declared_size < 0 or declared_size > MAX_BYTES:
                raise ValueError("Thumbnail exceeds the 5 MiB response limit or has invalid length.")
        payload = bytearray()
        while True:
            chunk = response.read(min(64 * 1024, MAX_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > MAX_BYTES:
                raise ValueError("Thumbnail exceeds the 5 MiB response limit.")

    with BytesIO(payload) as stream:
        with Image.open(stream) as source:
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("Thumbnail exceeds the 20 million pixel limit.")
            source.verify()
        stream.seek(0)
        with Image.open(stream) as source:
            image = source.convert("RGB")
    image.thumbnail(MAX_SIZE, Image.Resampling.LANCZOS)
    return image
