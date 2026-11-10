import json
from pathlib import Path
import subprocess
import shutil
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from instantdownloader.core import downloader
from instantdownloader.core.models import DownloadConfig


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "validate_media", MagicMock())
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
    target = path.with_name("video [compatible].mp4")
    target.write_bytes(b"previous")
    mock_probe(monkeypatch, vcodec, acodec, pix_fmt)
    monkeypatch.setattr(downloader, "_compatible_streams", MagicMock(side_effect=[False, True]))
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
    target = path.with_name("video [compatible].mp4")
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
    assert sorted(item.name for item in path.parent.iterdir()) == ["video [compatible].mp4", "video.mp4"]


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
    mock_probe(monkeypatch)
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


def test_incompatible_conversion_never_replaces_previous(source, monkeypatch):
    path, config = source
    target = path.with_name('video [compatible].mp4')
    target.write_bytes(b'previous')
    monkeypatch.setattr(downloader, '_compatible_streams', lambda *args: False)
    monkeypatch.setattr(downloader, '_run_media_command', lambda command, *_: Path(command[-1]).write_bytes(b'bad-output'))
    with pytest.raises(RuntimeError, match='not H.264'):
        downloader.make_compatible_mp4(path, config)
    assert target.read_bytes() == b'previous'
    assert path.read_bytes() == b'original'
    assert len(list(path.parent.iterdir())) == 2


def test_decode_failure_never_replaces_previous(source, monkeypatch):
    path, config = source
    target = path.with_name('video [compatible].mp4')
    target.write_bytes(b'previous')
    monkeypatch.setattr(downloader, '_compatible_streams', MagicMock(side_effect=[False, True]))
    monkeypatch.setattr(downloader, '_run_media_command', lambda command, *_: Path(command[-1]).write_bytes(b'bad-output'))
    monkeypatch.setattr(downloader, 'validate_media', MagicMock(side_effect=RuntimeError('decode failed')))
    with pytest.raises(RuntimeError, match='decode failed'):
        downloader.make_compatible_mp4(path, config)
    assert target.read_bytes() == b'previous'
    assert path.read_bytes() == b'original'
    assert len(list(path.parent.iterdir())) == 2


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='Requires FFmpeg/FFprobe')
@pytest.mark.parametrize('audio', [True, False])
def test_real_full_range_conversion_is_limited_range_and_decodable(tmp_path, audio):
    path = tmp_path/'full-range.mp4'
    command = ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=64x96:rate=10']
    if audio:
        command += ['-f', 'lavfi', '-i', 'sine=frequency=440']
    command += ['-t', '0.3', '-vf', 'scale=out_range=pc', '-c:v', 'libx264',
                '-pix_fmt', 'yuvj420p', '-color_range', 'pc', '-colorspace', 'bt709',
                '-color_primaries', 'bt709', '-color_trc', 'bt709', '-c:a', 'aac', str(path)]
    subprocess.run(command, check=True, capture_output=True)
    config = DownloadConfig(output_dir=tmp_path)
    assert not downloader._compatible_streams(path, config)
    converted = downloader.make_compatible_mp4(path, config)
    assert converted != path and path.exists()
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(converted)]))
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    assert video['pix_fmt'] == 'yuv420p'
    assert video['color_range'] == 'tv'
    assert video['codec_tag_string'] == 'avc1'
    assert any(s['codec_type'] == 'audio' for s in info['streams']) == audio
    downloader.validate_media(converted, config)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='Requires FFmpeg')
def test_real_invalid_media_rejected(tmp_path):
    path = tmp_path/'broken.mp4'
    path.write_bytes(b'not a video')
    with pytest.raises(RuntimeError, match='validation failed'):
        downloader.validate_media(path, DownloadConfig(output_dir=tmp_path))
