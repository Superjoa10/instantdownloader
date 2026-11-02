from pathlib import Path
from threading import Event
from unittest.mock import MagicMock

import pytest
import yt_dlp

from instantdownloader.core import downloader
from instantdownloader.core.models import DownloadConfig, DownloadJob


@pytest.fixture
def media(tmp_path, monkeypatch):
    raw = {"id": "first", "title": "Video", "ext": "mp4", "extractor_key": "Test"}
    process = MagicMock()

    def download(ydl, info, download=True):
        process(info)
        path = Path(ydl.prepare_filename(info))
        path.write_bytes(b"video")
        return info

    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", lambda *a, **kw: dict(raw))
    monkeypatch.setattr(yt_dlp.YoutubeDL, "process_ie_result", download)
    monkeypatch.setattr(downloader, "probe_resolution", lambda *a: (720, 1280))
    return DownloadConfig(output_dir=tmp_path), raw, process


@pytest.mark.parametrize("kind", ["playlist", "multi_video", "compat_list"])
def test_collection_rejected_before_any_download(media, kind):
    config, raw, process = media
    raw.update(_type=kind, entries=[{"id": "child", "ext": "mp4"}])
    result = downloader.download_job(DownloadJob("https://example.com/list"), config)
    assert result.status == "error"
    assert "playlist_index" in result.error
    process.assert_not_called()


def test_playlist_index_selects_one_video_and_inherits_extractor(media):
    config, raw, process = media
    raw.update(_type="playlist", entries=[
        {"id": "image", "ext": "jpg"},
        {"id": "first", "title": "One", "ext": "mp4"},
        {"id": "audio", "vcodec": "none"},
        {"id": "second", "title": "Two", "ext": "mp4"},
    ])
    config.download_archive = config.output_dir / "archive.txt"
    result = downloader.download_job(DownloadJob("https://example.com/list", playlist_index=2), config)
    assert result.status == "success"
    assert "[second]" in result.output_path.name
    assert process.call_count == 1
    assert process.call_args.args[0]["extractor_key"] == "Test"
    assert config.download_archive.read_text() == "test second\n"


@pytest.mark.parametrize("index", [0, -1, True, "1", 1.5, 5])
def test_invalid_playlist_index_never_downloads(media, index):
    config, raw, process = media
    raw.update(_type="playlist", entries=[{"id": "one", "ext": "mp4"}])
    result = downloader.download_job(DownloadJob("https://example.com/list", playlist_index=index), config)
    assert result.status == "error"
    assert "playlist_index" in result.error
    process.assert_not_called()


def test_no_metadata_is_error_not_archive_skip(media, monkeypatch):
    config, raw, process = media
    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", lambda *a, **kw: None)
    result = downloader.download_job(DownloadJob("https://example.com/video"), config)
    assert result.status == "error"
    assert not result.ok
    assert result.output_path is None
    process.assert_not_called()


def test_pre_cancelled_never_constructs_downloader(tmp_path, monkeypatch):
    factory = MagicMock()
    monkeypatch.setattr(yt_dlp, "YoutubeDL", factory)
    event = Event()
    event.set()
    result = downloader.download_job(DownloadJob("https://example.com/video"),
                                     DownloadConfig(output_dir=tmp_path), cancel_event=event)
    assert result.status == "cancelled"
    factory.assert_not_called()


def test_progress_callback_can_cancel_download(media, monkeypatch):
    config, raw, process = media
    event = Event()

    def download(ydl, info, download=True):
        ydl.params["progress_hooks"][0]({"status": "downloading"})
        pytest.fail("Cancellation should have interrupted this download")

    monkeypatch.setattr(yt_dlp.YoutubeDL, "process_ie_result", download)
    result = downloader.download_job(DownloadJob("https://example.com/video"), config,
                                     progress_callback=lambda data: event.set(), cancel_event=event)
    assert result.status == "cancelled"


def test_rate_limit_is_distinct(media, monkeypatch):
    config, raw, process = media

    def extract(*a, **kw):
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", extract)
    result = downloader.download_job(DownloadJob("https://example.com/video"), config)
    assert result.status == "rate_limited"
    process.assert_not_called()


def test_redirected_collection_is_rejected_without_downloading(media, monkeypatch):
    config, raw, process = media
    extract = MagicMock(side_effect=[
        {"_type": "url", "url": "https://example.com/list"},
        {"_type": "playlist", "entries": [{"id": "one", "ext": "mp4"}]},
    ])
    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", extract)
    result = downloader.download_job(DownloadJob("https://example.com/redirect"), config)
    assert result.status == "error"
    assert all(call.kwargs["download"] is False for call in extract.call_args_list)
    process.assert_not_called()


@pytest.mark.parametrize("index,expected_id", [(1, "C"), (2, "E")])
def test_installed_product_carousel_uses_video_ordinal_for_actual_download(tmp_path, monkeypatch, index, expected_id):
    from yt_dlp.extractor.instagram import InstagramIE

    product = {"carousel_media": [
        {"pk": "1"},
        {"pk": "2", "video_versions": [{"url": "https://cdn.example/first.mp4"}]},
        {"pk": "3"},
        {"pk": "4", "video_versions": [{"url": "https://cdn.example/second.mp4"}]},
    ]}
    raw = InstagramIE()._extract_product(product, get_comments=False)
    raw.update(extractor="instagram", extractor_key="Instagram")
    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", lambda *a, **kw: raw)
    monkeypatch.setattr(yt_dlp.YoutubeDL, "urlopen", MagicMock(side_effect=AssertionError("No network allowed")))
    monkeypatch.setattr(downloader, "probe_resolution", lambda *a: (640, 480))
    downloaded = []

    def process_info(ydl, info):
        downloaded.append(info["id"])
        Path(ydl.prepare_filename(info)).write_bytes(b"video")
        ydl.record_download_archive(info)

    monkeypatch.setattr(yt_dlp.YoutubeDL, "process_info", process_info)
    config = DownloadConfig(output_dir=tmp_path, download_archive=tmp_path / "archive.txt")
    result = downloader.download_job(DownloadJob("https://www.instagram.com/p/album/", playlist_index=index), config)
    assert result.status == "success", result.error
    assert downloaded == [expected_id]
    assert f"[{expected_id}]" in result.output_path.name
    assert config.download_archive.read_text() == f"instagram {expected_id}\n"


@pytest.mark.parametrize("kind", ["playlist", "multi_video", "compat_list"])
def test_analyze_rejects_collection_without_processing_entries(tmp_path, monkeypatch, kind):
    entries = MagicMock()
    entries.__iter__.side_effect = AssertionError("Collection entries must not be evaluated")
    extract = MagicMock(return_value={"_type": kind, "title": "Collection", "entries": entries})
    process = MagicMock(side_effect=AssertionError("Collection must not be processed"))
    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", extract)
    monkeypatch.setattr(yt_dlp.YoutubeDL, "process_ie_result", process)
    with pytest.raises(ValueError, match="collection.*direct video URL"):
        downloader.analyze_url("https://example.com/list", DownloadConfig(output_dir=tmp_path))
    extract.assert_called_once_with("https://example.com/list", download=False, process=False)
    process.assert_not_called()


def test_analyze_rejects_redirected_collection(tmp_path, monkeypatch):
    extract = MagicMock(side_effect=[
        {"_type": "url", "url": "https://example.com/list"},
        {"_type": "playlist", "entries": []},
    ])
    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", extract)
    with pytest.raises(ValueError, match="collection"):
        downloader.analyze_url("https://example.com/redirect", DownloadConfig(output_dir=tmp_path))
    assert all(call.kwargs["download"] is False for call in extract.call_args_list)


def test_analyze_single_video_preserves_metadata_without_download(tmp_path, monkeypatch):
    raw = {"id": "one", "title": "One video", "extractor_key": "Test", "formats": [
        {"format_id": "720", "url": "https://example.com/video.mp4", "ext": "mp4",
         "vcodec": "h264", "acodec": "aac", "width": 1280, "height": 720},
    ]}
    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", lambda *a, **kw: raw)
    monkeypatch.setattr(yt_dlp.YoutubeDL, "urlopen", MagicMock(side_effect=AssertionError("No network allowed")))
    process_info = MagicMock(side_effect=AssertionError("Analysis must not download"))
    monkeypatch.setattr(yt_dlp.YoutubeDL, "process_info", process_info)
    result = downloader.analyze_url("https://example.com/video", DownloadConfig(output_dir=tmp_path))
    assert result.title == "One video"
    assert result.formats[0].format_id == "720"
    assert (result.formats[0].width, result.formats[0].height) == (1280, 720)
    process_info.assert_not_called()


def test_converting_progress_precedes_conversion(media, monkeypatch):
    config, raw, process = media
    config.compatible_mp4 = True
    events = []

    def convert(path, config, cancel_event):
        assert events == [{"status": "converting"}]
        return path

    monkeypatch.setattr(downloader, "make_compatible_mp4", convert)
    result = downloader.download_job(DownloadJob("https://example.com/video"), config,
                                     progress_callback=events.append)
    assert result.status == "success"
    assert events == [{"status": "converting"}]


def test_converting_callback_can_cancel_before_conversion(media, monkeypatch):
    config, raw, process = media
    config.compatible_mp4 = True
    config.download_archive = config.output_dir / "archive.txt"
    event = Event()
    convert = MagicMock(side_effect=AssertionError("Cancelled conversion must not start"))
    monkeypatch.setattr(downloader, "make_compatible_mp4", convert)

    def progress(data):
        assert data == {"status": "converting"}
        event.set()

    result = downloader.download_job(DownloadJob("https://example.com/video"), config,
                                     progress_callback=progress, cancel_event=event)
    assert result.status == "cancelled"
    assert not config.download_archive.exists()
    convert.assert_not_called()
