from __future__ import annotations

from pathlib import Path
from datetime import datetime
from uuid import uuid4

from .models import DownloadConfig


def default_output_dir() -> Path:
    return Path.cwd() / "downloads"


def default_report_path() -> Path:
    return Path.cwd() / "reports" / f"download-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}.csv"


def make_config(
    output_dir: str | Path | None = None,
    quality: str = "best",
    use_cookies: bool = False,
    browser: str = "chrome",
    ffmpeg_location: str | Path | None = None,
    sleep_interval: float = 3.0,
    max_sleep_interval: float = 8.0,
    retries: int = 3,
    rate_limit: int | None = 8_000_000,
    compatible_mp4: bool = False,
) -> DownloadConfig:
    if sleep_interval < 0 or max_sleep_interval < sleep_interval:
        raise ValueError('Sleep intervals must satisfy 0 <= sleep <= max-sleep.')
    if retries < 0 or (rate_limit is not None and rate_limit <= 0):
        raise ValueError('Retries must be nonnegative and rate limit positive.')
    resolved_output = (Path(output_dir) if output_dir else default_output_dir()).expanduser().resolve()
    archive = resolved_output / "download_archive.txt"

    return DownloadConfig(
        output_dir=resolved_output,
        quality=quality,
        use_cookies=use_cookies,
        browser=browser,
        ffmpeg_location=Path(ffmpeg_location) if ffmpeg_location else None,
        sleep_interval=sleep_interval,
        max_sleep_interval=max_sleep_interval,
        retries=retries,
        rate_limit=rate_limit,
        download_archive=archive,
        compatible_mp4=compatible_mp4,
    )
