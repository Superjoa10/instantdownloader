from pathlib import Path
import hashlib

import pytest
import yt_dlp

from instantdownloader.core.downloader import _resolve_existing_output, build_ydl_options
from instantdownloader.core.models import DownloadConfig, DownloadJob


def test_resolve_existing_output_prefers_final_mp4(tmp_path: Path):
    prepared = tmp_path / "Video by gossipgoblin.mp4"
    captured = tmp_path / "Video by gossipgoblin.fdash-audio.m4a"
    prepared.write_text("mp4")

    assert _resolve_existing_output(prepared, captured) == prepared


def test_resolve_existing_output_falls_back_to_captured(tmp_path: Path):
    prepared = tmp_path / "missing.mp4"
    captured = tmp_path / "audio.m4a"
    captured.write_text("audio")

    assert _resolve_existing_output(prepared, captured) == captured


@pytest.mark.parametrize("kind", ["missing", "empty", "directory"])
def test_resolve_output_rejects_missing_or_invalid_file(tmp_path, kind):
    path = tmp_path / "video.mp4"
    if kind == "empty":
        path.touch()
    elif kind == "directory":
        path.mkdir()
    with pytest.raises(FileNotFoundError, match="output file"):
        _resolve_existing_output(path)


@pytest.mark.parametrize("filename", [None, "Custom name", "../bad/%(title)s"])
def test_same_titles_and_custom_names_keep_unique_ids(tmp_path, filename):
    config = DownloadConfig(output_dir=tmp_path)
    options = build_ydl_options(config, DownloadJob("https://example.com", filename=filename))
    with yt_dlp.YoutubeDL(options) as ydl:
        paths = [Path(ydl.prepare_filename({"title": "Same title", "id": item, "ext": "mp4"}))
                 for item in ("one", "two")]
    assert paths[0] != paths[1]
    assert all(path.parent == tmp_path for path in paths)
    assert "[one]" in paths[0].name
    assert "[two]" in paths[1].name


def test_output_paths_are_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    options = build_ydl_options(DownloadConfig(output_dir=Path("downloads")))
    assert Path(options["outtmpl"]).is_absolute()


@pytest.mark.parametrize("filename", [None, "Custom name"])
def test_quality_variants_have_distinct_deterministic_paths(tmp_path, filename):
    config = DownloadConfig(output_dir=tmp_path)
    info = {"title": "Same title", "id": "one", "ext": "mp4"}
    paths = []
    for quality in ("best", "1080p", "720p", "480p", "bv*+ba/b", "best[height<=720]"):
        job = DownloadJob("https://example.com/video", filename=filename, quality=quality)
        options = build_ydl_options(config, job)
        assert options["outtmpl"] == build_ydl_options(config, job)["outtmpl"]
        with yt_dlp.YoutubeDL(options) as ydl:
            path = Path(ydl.prepare_filename(info))
        suffix = quality if quality in {"best", "1080p", "720p", "480p"} else (
            "format-" + hashlib.sha256(quality.encode("utf-8")).hexdigest()[:12]
        )
        assert path.name.endswith(f"[one] [{suffix}].mp4")
        assert path.parent == tmp_path
        paths.append(path)
    assert len(set(paths)) == len(paths)


def test_quality_suffix_uses_config_unless_job_overrides(tmp_path):
    config = DownloadConfig(output_dir=tmp_path, quality="1080p")
    assert "[1080p]" in build_ydl_options(config)["outtmpl"]
    assert "[1080p]" in build_ydl_options(config, DownloadJob("https://example.com"))["outtmpl"]
    assert "[720p]" in build_ydl_options(config, DownloadJob("https://example.com", quality="720p"))["outtmpl"]
