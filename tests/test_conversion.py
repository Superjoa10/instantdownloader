import json
from pathlib import Path
import subprocess
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from instantdownloader.core import downloader
from instantdownloader.core.models import DownloadConfig


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"original")
    return path, DownloadConfig(output_dir=tmp_path)


def mock_probe(monkeypatch, vcodec="h264", acodec="aac", pix_fmt="yuv420p"):
    streams = [{"codec_type": "video", "codec_name": vcodec, "pix_fmt": pix_fmt}]
    if acodec:
        streams.append({"codec_type": "audio", "codec_name": acodec})
    run = MagicMock(return_value=SimpleNamespace(stdout=json.dumps({"streams": streams})))
    monkeypatch.setattr(downloader.subprocess, "run", run)
    return run


@pytest.mark.parametrize("audio", ["aac", None])
def test_compatible_mp4_is_not_reencoded(source, monkeypatch, audio):
    path, config = source
    mock_probe(monkeypatch, acodec=audio)
    popen = MagicMock(side_effect=AssertionError("Already compatible"))
    monkeypatch.setattr(downloader.subprocess, "Popen", popen)
    assert downloader.make_compatible_mp4(path, config) == path
    popen.assert_not_called()


@pytest.mark.parametrize("vcodec,acodec,pix_fmt", [
    ("hevc", "aac", "yuv420p"), ("h264", "opus", "yuv420p"), ("h264", "aac", "yuv444p"),
])
def test_conversion_is_atomic_and_preserves_source(source, monkeypatch, vcodec, acodec, pix_fmt):
    path, config = source
    target = path.with_name("video.h264.mp4")
    target.write_bytes(b"previous")
    mock_probe(monkeypatch, vcodec, acodec, pix_fmt)
    process = MagicMock()
    process.__enter__.return_value = process
    process.returncode = 0
    process.communicate.return_value = ("", "")
    temporary_paths = []

    def popen(command, **kwargs):
        temporary = Path(command[-1])
        temporary_paths.append(temporary)
        assert temporary != target
        assert temporary.parent == target.parent
        assert target.read_bytes() == b"previous"
        temporary.write_bytes(b"converted")
        return process

    monkeypatch.setattr(downloader.subprocess, "Popen", popen)
    assert downloader.make_compatible_mp4(path, config) == target
    assert target.read_bytes() == b"converted"
    assert path.read_bytes() == b"original"
    assert not temporary_paths[0].exists()


@pytest.mark.parametrize("failure", ["exit", "missing-output", "launch"])
def test_failed_conversion_cleans_temporary_and_preserves_previous(source, monkeypatch, failure):
    path, config = source
    target = path.with_name("video.h264.mp4")
    target.write_bytes(b"previous")
    mock_probe(monkeypatch, vcodec="hevc")
    process = MagicMock()
    process.__enter__.return_value = process
    process.returncode = 1 if failure == "exit" else 0
    process.communicate.return_value = ("", "encoder failed")
    if failure == "launch":
        monkeypatch.setattr(downloader.subprocess, "Popen", MagicMock(side_effect=OSError("ffmpeg missing")))
    else:
        monkeypatch.setattr(downloader.subprocess, "Popen", MagicMock(return_value=process))
    with pytest.raises((RuntimeError, FileNotFoundError, OSError)):
        downloader.make_compatible_mp4(path, config)
    assert target.read_bytes() == b"previous"
    assert path.read_bytes() == b"original"
    assert sorted(item.name for item in path.parent.iterdir()) == ["video.h264.mp4", "video.mp4"]


def test_cancelled_conversion_terminates_child_and_cleans_up(source, monkeypatch):
    path, config = source
    event = Event()
    mock_probe(monkeypatch, vcodec="hevc")
    process = MagicMock()
    process.__enter__.return_value = process

    def communicate(timeout=None):
        if timeout == 0.1:
            event.set()
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        return "", ""

    process.communicate.side_effect = communicate
    monkeypatch.setattr(downloader.subprocess, "Popen", MagicMock(return_value=process))
    with pytest.raises(downloader.DownloadCancelled):
        downloader.make_compatible_mp4(path, config, event)
    process.terminate.assert_called_once()
    assert list(path.parent.iterdir()) == [path]


def test_non_mp4_input_is_converted(source, monkeypatch):
    path, config = source
    path = path.rename(path.with_suffix(".webm"))
    process = MagicMock()
    process.__enter__.return_value = process
    process.returncode = 0
    process.communicate.return_value = ("", "")

    def popen(command, **kwargs):
        Path(command[-1]).write_bytes(b"converted")
        return process

    monkeypatch.setattr(downloader.subprocess, "Popen", popen)
    assert downloader.make_compatible_mp4(path, config).suffix == ".mp4"


def test_ffmpeg_executable_path_resolves_sibling_probe(source, monkeypatch):
    path, config = source
    config.ffmpeg_location = Path("/custom/bin/ffmpeg")
    run = mock_probe(monkeypatch)
    assert downloader.make_compatible_mp4(path, config) == path
    assert run.call_args.args[0][0] == "/custom/bin/ffprobe"
