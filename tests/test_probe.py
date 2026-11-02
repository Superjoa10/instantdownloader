from pathlib import Path
from unittest.mock import patch

from instantdownloader.core.downloader import probe_resolution
from instantdownloader.core.models import DownloadConfig


def test_probe_resolution_returns_width_height(tmp_path: Path):
    config = DownloadConfig(output_dir=tmp_path)
    video = tmp_path / "video.mp4"
    video.write_text("video")

    completed = type("Completed", (), {"stdout": '{"streams":[{"width":720,"height":1280}]}'})

    with patch("instantdownloader.core.downloader.subprocess.run", return_value=completed):
        assert probe_resolution(video, config) == (720, 1280)


def test_probe_resolution_falls_back_on_error(tmp_path: Path):
    config = DownloadConfig(output_dir=tmp_path)

    with patch("instantdownloader.core.downloader.subprocess.run", side_effect=OSError):
        assert probe_resolution(tmp_path / "missing.mp4", config) == (None, None)
