from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class DownloadConfig:
    output_dir: Path
    quality: str = "best"
    use_cookies: bool = False
    browser: str = "chrome"
    ffmpeg_location: Path | None = None
    sleep_interval: float = 3.0
    max_sleep_interval: float = 8.0
    retries: int = 3
    fragment_retries: int = 3
    rate_limit: int | None = 8_000_000
    overwrite: bool = False
    stop_on_rate_limit: bool = True
    download_archive: Path | None = None
    compatible_mp4: bool = False
    cookiefile: Path | None = None


@dataclass(slots=True)
class DownloadJob:
    url: str
    filename: str | None = None
    folder: str | None = None
    quality: str | None = None
    playlist_index: int | None = None


@dataclass(slots=True)
class FormatInfo:
    format_id: str
    ext: str | None = None
    width: int | None = None
    height: int | None = None
    vcodec: str | None = None
    acodec: str | None = None
    filesize: int | None = None
    note: str | None = None


@dataclass(slots=True)
class VideoInfo:
    url: str
    title: str | None = None
    uploader: str | None = None
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    thumbnail: str | None = None
    formats: list[FormatInfo] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class DownloadResult:
    url: str
    status: str
    started_at: datetime
    finished_at: datetime
    output_path: Path | None = None
    title: str | None = None
    width: int | None = None
    height: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"success", "skipped"}
