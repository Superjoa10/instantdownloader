from datetime import datetime
from pathlib import Path
from threading import Event

import pytest
import yt_dlp

from instantdownloader.core import downloader
from instantdownloader.core.models import DownloadConfig, DownloadJob


@pytest.fixture
def fake_media(tmp_path, monkeypatch):
    raw = {"id": "123", "title": "Example", "extractor_key": "Test", "ext": "mp4"}
    processed = []
    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", lambda self, *a, **kw: dict(raw))
    monkeypatch.setattr(downloader, "probe_resolution", lambda *a: (720, 1280))

    def process(ydl, info, download=True):
        processed.append(info)
        Path(ydl.prepare_filename(info)).write_bytes(b"video")
        ydl.record_download_archive(info)
        return info

    monkeypatch.setattr(yt_dlp.YoutubeDL, "process_ie_result", process)
    config = DownloadConfig(output_dir=tmp_path, download_archive=tmp_path / "archive.txt")
    return config, raw, processed


def test_archive_skip_is_ok_without_fabricated_path(fake_media):
    config, raw, processed = fake_media
    config.download_archive.write_text("test 123\n")
    result = downloader.download_job(DownloadJob("https://example.com/video"), config)
    assert result.status == "skipped"
    assert result.ok
    assert result.output_path is None
    assert not processed
    assert result.finished_at >= result.started_at


def test_legacy_archive_ids_still_skip(fake_media):
    config, raw, processed = fake_media
    raw["_old_archive_ids"] = ["legacy old123"]
    config.download_archive.write_text("legacy old123\n")
    result = downloader.download_job(DownloadJob("https://example.com/video"), config)
    assert result.status == "skipped"
    assert not processed


def test_overwrite_retries_archived_missing_output(fake_media):
    config, raw, processed = fake_media
    config.download_archive.write_text("test 123\n")
    config.overwrite = True
    result = downloader.download_job(DownloadJob("https://example.com/video"), config)
    assert result.status == "success"
    assert result.output_path.is_file()
    assert len(processed) == 1


def test_archive_is_written_only_after_conversion(fake_media, monkeypatch):
    config, raw, processed = fake_media
    config.compatible_mp4 = True
    conversion_finished = []

    def convert(path, config, cancel_event):
        assert not config.download_archive.exists()
        conversion_finished.append(datetime.now())
        return path

    monkeypatch.setattr(downloader, "make_compatible_mp4", convert)
    result = downloader.download_job(DownloadJob("https://example.com/video"), config)
    assert result.status == "success"
    assert config.download_archive.read_text() == "test 123\n"
    assert result.finished_at >= conversion_finished[0]


def test_successful_conversion_keeps_only_compatible_download(fake_media, monkeypatch):
    config, raw, processed = fake_media
    config.compatible_mp4 = True
    sources = []

    def convert(path, config, cancel_event):
        sources.append(path)
        target = path.with_name(path.stem + ' [compatible].mp4')
        target.write_bytes(b'validated conversion')
        return target

    monkeypatch.setattr(downloader, 'make_compatible_mp4', convert)
    unrelated = config.output_dir / 'unrelated.mp4'
    unrelated.write_bytes(b'keep')
    result = downloader.download_job(DownloadJob('https://example.com/video'), config)
    assert result.status == 'success'
    assert result.output_path.read_bytes() == b'validated conversion'
    assert not sources[0].exists()
    assert unrelated.read_bytes() == b'keep'
    assert config.download_archive.read_text() == 'test 123\n'


def test_cancel_after_conversion_preserves_source(fake_media, monkeypatch):
    config, raw, processed = fake_media
    config.compatible_mp4 = True
    cancel = Event()
    sources = []

    def convert(path, config, cancel_event):
        sources.append(path)
        target = path.with_name(path.stem + ' [compatible].mp4')
        target.write_bytes(b'converted')
        cancel.set()
        return target

    monkeypatch.setattr(downloader, 'make_compatible_mp4', convert)
    result = downloader.download_job(DownloadJob('https://example.com/video'), config, cancel_event=cancel)
    assert result.status == 'cancelled'
    assert sources[0].exists()
    assert not config.download_archive.exists()


@pytest.mark.parametrize("failure", [RuntimeError("conversion failed"), downloader.DownloadCancelled("cancelled")])
def test_failed_conversion_does_not_poison_archive(fake_media, monkeypatch, failure):
    config, raw, processed = fake_media
    config.compatible_mp4 = True
    config.download_archive.write_text("test unrelated\n")

    def convert(*args):
        raise failure

    monkeypatch.setattr(downloader, "make_compatible_mp4", convert)
    result = downloader.download_job(DownloadJob("https://example.com/video"), config)
    assert result.status == ("cancelled" if isinstance(failure, downloader.DownloadCancelled) else "error")
    assert result.output_path is None
    assert config.download_archive.read_text() == "test unrelated\n"
    assert list(config.output_dir.glob('*.mp4'))


def test_missing_output_never_records_archive(fake_media, monkeypatch):
    config, raw, processed = fake_media

    def process(ydl, info, download=True):
        ydl.record_download_archive(info)
        return info

    monkeypatch.setattr(yt_dlp.YoutubeDL, "process_ie_result", process)
    result = downloader.download_job(DownloadJob("https://example.com/video"), config)
    assert result.status == "error"
    assert "output file" in result.error
    assert not config.download_archive.exists()


def test_cancellation_after_download_never_records_archive(fake_media, monkeypatch):
    config, raw, processed = fake_media
    event = Event()

    def probe(*args):
        event.set()
        return (720, 1280)

    monkeypatch.setattr(downloader, "probe_resolution", probe)
    result = downloader.download_job(DownloadJob("https://example.com/video"), config, cancel_event=event)
    assert result.status == "cancelled"
    assert not config.download_archive.exists()
