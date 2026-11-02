from io import BytesIO
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest
from PIL import Image, UnidentifiedImageError

from instantdownloader.core import thumbnail


def image_bytes(mode="RGB", size=(80, 60), format="PNG"):
    with BytesIO() as stream:
        Image.new(mode, size).save(stream, format=format)
        return stream.getvalue()


class Response(BytesIO):
    def __init__(self, data, headers=None, url="https://cdn.example/image.png"):
        super().__init__(data)
        self.headers = headers or {}
        self.url = url
        self.read_sizes = []

    def geturl(self):
        return self.url

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


@pytest.fixture
def transport(monkeypatch):
    opener = Mock()
    opener.open.side_effect = AssertionError("A mocked response must be configured")
    factory = Mock(return_value=opener)
    monkeypatch.setattr(thumbnail, "build_opener", factory)

    def respond(data, headers=None, url="https://cdn.example/image.png"):
        response = Response(data, headers, url)
        opener.open.side_effect = None
        opener.open.return_value = response
        return response

    return opener, factory, respond


@pytest.mark.parametrize("mode,size,expected", [
    ("RGB", (1600, 900), (640, 360)),
    ("RGBA", (900, 1600), (270, 480)),
    ("P", (80, 60), (80, 60)),
    ("L", (800, 800), (480, 480)),
])
def test_decodes_rgb_resizes_and_detaches(transport, mode, size, expected):
    opener, _, respond = transport
    response = respond(image_bytes(mode, size))
    result = thumbnail.load_thumbnail("https://cdn.example/image.png")
    assert isinstance(result, Image.Image)
    assert result.mode == "RGB"
    assert result.size == expected
    assert response.closed
    assert result.getpixel((0, 0)) == (0, 0, 0)
    assert opener.open.call_args.kwargs == {"timeout": 8}
    assert opener.open.call_args.args[0].full_url == "https://cdn.example/image.png"
    assert all(0 < size <= 64 * 1024 for size in response.read_sizes)


@pytest.mark.parametrize("url", [
    "", "relative.png", "//cdn.example/image.png", "file:///tmp/image.png",
    "ftp://cdn.example/image.png", "data:image/png;base64,abc", "https:///missing-host",
    "https://user:pass@cdn.example/image.png", "https://cdn.example:99999/image.png",
    "https://cdn.example:bad/image.png", "https://cdn.example/image\n.png", None,
])
def test_invalid_urls_never_request(transport, url):
    opener, factory, _ = transport
    with pytest.raises(ValueError):
        thumbnail.load_thumbnail(url)
    factory.assert_not_called()
    opener.open.assert_not_called()


def test_http_is_supported_and_mime_type_is_not_trusted(transport):
    _, _, respond = transport
    respond(image_bytes(), {"Content-Type": "application/octet-stream"})
    assert thumbnail.load_thumbnail("http://cdn.example/image").mode == "RGB"


@pytest.mark.parametrize("length", [str(thumbnail.MAX_BYTES + 1), "-1", "not-a-number"])
def test_rejects_content_length_before_reading(transport, length):
    _, _, respond = transport
    response = respond(b"", {"Content-Length": length})
    with pytest.raises(ValueError):
        thumbnail.load_thumbnail("https://cdn.example/image.png")
    assert response.read_sizes == []
    assert response.closed


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "1"}])
def test_enforces_stream_limit_even_when_length_missing_or_false(transport, headers):
    _, _, respond = transport
    response = respond(b"x" * (thumbnail.MAX_BYTES + 100), headers)
    with pytest.raises(ValueError, match="5 MiB"):
        thumbnail.load_thumbnail("https://cdn.example/image.png")
    assert sum(response.read_sizes) == thumbnail.MAX_BYTES + 1
    assert response.closed


def test_exact_byte_limit_is_allowed(transport, monkeypatch):
    _, _, respond = transport
    data = image_bytes()
    monkeypatch.setattr(thumbnail, "MAX_BYTES", len(data))
    respond(data, {"Content-Length": str(len(data))})
    assert thumbnail.load_thumbnail("https://cdn.example/image.png").size == (80, 60)


def test_pixel_limit_checked_before_decode(transport, monkeypatch):
    _, _, respond = transport
    respond(image_bytes(size=(101, 100)))
    monkeypatch.setattr(thumbnail, "MAX_PIXELS", 10_000)
    decode = Mock(side_effect=AssertionError("Oversized image was decoded"))
    monkeypatch.setattr(Image.Image, "convert", decode)
    with pytest.raises(ValueError, match="pixel limit"):
        thumbnail.load_thumbnail("https://cdn.example/image.png")
    decode.assert_not_called()


def test_exact_pixel_limit_is_allowed(transport, monkeypatch):
    _, _, respond = transport
    respond(image_bytes(size=(100, 100)))
    monkeypatch.setattr(thumbnail, "MAX_PIXELS", 10_000)
    assert thumbnail.load_thumbnail("https://cdn.example/image.png").size == (100, 100)


@pytest.mark.parametrize("data", [b"", b"<html>not an image</html>", b"GIF89a"])
def test_invalid_content_is_not_returned_as_image(transport, data):
    _, _, respond = transport
    response = respond(data, {"Content-Type": "image/png"})
    with pytest.raises((UnidentifiedImageError, OSError)):
        thumbnail.load_thumbnail("https://cdn.example/image.png")
    assert response.closed


def test_truncated_image_is_rejected(transport):
    _, _, respond = transport
    respond(image_bytes()[:-20])
    with pytest.raises((OSError, SyntaxError)):
        thumbnail.load_thumbnail("https://cdn.example/image.png")


@pytest.mark.parametrize("error", [TimeoutError("timed out"), URLError("offline"),
    HTTPError("https://cdn.example/image.png", 429, "Too Many Requests", {}, None)])
def test_transport_failures_propagate_without_retry(transport, error):
    opener, _, _ = transport
    opener.open.side_effect = error
    with pytest.raises(type(error)):
        thumbnail.load_thumbnail("https://cdn.example/image.png")
    opener.open.assert_called_once()


def test_read_failure_closes_response(transport):
    _, _, respond = transport
    response = respond(b"")
    response.read = Mock(side_effect=TimeoutError("read timeout"))
    with pytest.raises(TimeoutError):
        thumbnail.load_thumbnail("https://cdn.example/image.png")
    assert response.closed


@pytest.mark.parametrize("target", ["file:///tmp/image.png", "ftp://cdn.example/image.png",
                                      "https://user:pass@cdn.example/image.png"])
def test_unsafe_redirect_is_rejected_before_following(target):
    handler = thumbnail._HTTPOnlyRedirects()
    with pytest.raises(ValueError):
        handler.redirect_request(Request("https://cdn.example/original"), None, 302, "Found", {}, target)


def test_https_redirect_is_allowed():
    handler = thumbnail._HTTPOnlyRedirects()
    target = "https://other.example/image.png"
    result = handler.redirect_request(Request("https://cdn.example/original"), None, 302, "Found", {}, target)
    assert result.full_url == target


def test_redirect_handler_is_installed(transport):
    _, factory, respond = transport
    respond(image_bytes())
    thumbnail.load_thumbnail("https://cdn.example/image.png")
    assert isinstance(factory.call_args.args[0], thumbnail._HTTPOnlyRedirects)
