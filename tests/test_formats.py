from pathlib import Path

import pytest
import yt_dlp

from instantdownloader.core.downloader import build_ydl_options, resolve_format, annotate_dimensions
from instantdownloader.core.models import DownloadConfig


def test_resolve_known_quality():
    assert "short_side<=1080" in resolve_format("1080p")


def test_resolve_raw_selector():
    assert resolve_format("bv*+ba/b") == "bv*+ba/b"


def test_analyze_options_do_not_use_download_archive(tmp_path: Path):
    config = DownloadConfig(
        output_dir=tmp_path,
        download_archive=tmp_path / "download_archive.txt",
    )

    options = build_ydl_options(config, download=False)

    assert "download_archive" not in options


@pytest.mark.parametrize("size", [480, 720, 1080])
@pytest.mark.parametrize("portrait", [False, True])
@pytest.mark.parametrize("split", [False, True])
def test_quality_selectors_enforce_short_side(size, portrait, split):
    def video(short_side, name):
        width, height = (short_side, short_side * 2) if portrait else (short_side * 2, short_side)
        return {"format_id": name, "width": width, "height": height,
                "vcodec": "h264", "acodec": "none" if split else "aac",
                "ext": "mp4", "url": f"https://example.com/{name}"}

    formats = [video(size, "allowed"), video(size * 2, "too-big")]
    if split:
        formats.insert(0, {"format_id": "audio", "vcodec": "none", "acodec": "aac",
                           "ext": "m4a", "url": "https://example.com/audio"})
    annotate_dimensions({'formats': formats})
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        selector = ydl.build_format_selector(resolve_format(f"{size}p"))
        selected = ydl._select_formats(formats, selector)
        assert len(selected) == 1
        assert selected[0]["format_id"] == ("allowed+audio" if split else "allowed")
        assert not ydl._select_formats([item for item in formats if item["format_id"] != "allowed"], selector)


def test_quality_does_not_accept_unknown_dimensions():
    formats = [{"format_id": "unknown", "vcodec": "h264", "acodec": "aac", "ext": "mp4"}]
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        selector = ydl.build_format_selector(resolve_format("720p"))
        assert not ydl._select_formats(formats, selector)


def test_cookiefile_takes_precedence_over_browser(tmp_path):
    config = DownloadConfig(output_dir=tmp_path, cookiefile=tmp_path / "cookies.txt", use_cookies=True)
    options = build_ydl_options(config)
    assert options["cookiefile"] == str(tmp_path / "cookies.txt")
    assert "cookiesfrombrowser" not in options


@pytest.mark.parametrize('size', [480, 720, 1080])
@pytest.mark.parametrize('portrait', [True, False])
def test_quality_prefers_largest_allowed_not_first_orientation_branch(size, portrait):
    formats = []
    for dimension in sorted({240, 360, size, size*2}):
        w, h = (dimension, dimension*2) if portrait else (dimension*2, dimension)
        formats.append({'format_id': str(dimension), 'width': w, 'height': h,
                        'vcodec': 'h264', 'acodec': 'aac', 'ext': 'mp4', 'url': 'https://example.com/a'})
    annotate_dimensions({'formats': formats})
    with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
        chosen = ydl._select_formats(formats, ydl.build_format_selector(resolve_format(f'{size}p')))
    assert chosen[0]['format_id'] == str(size)
