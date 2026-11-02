from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import subprocess
import json
import os
import tempfile
from threading import Event, Lock
from typing import Any, Callable

import yt_dlp

from .models import DownloadConfig, DownloadJob, DownloadResult, FormatInfo, VideoInfo

ProgressCallback = Callable[[dict[str, Any]], None]


QUALITY_FORMATS = {
    "best": "bestvideo+bestaudio/best",
    **{
        f"{size}p": (
            f"bestvideo[short_side<={size}]+bestaudio/"
            f"best[short_side<={size}]"
        )
        for size in (1080, 720, 480)
    },
}

_ARCHIVE_LOCK = Lock()


class DownloadCancelled(Exception):
    pass


def _check_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelled("Download cancelled.")


def resolve_format(quality: str) -> str:
    return QUALITY_FORMATS.get(quality, quality)


def annotate_dimensions(raw: dict[str, Any]) -> dict[str, Any]:
    """Supply one numeric filter field so portrait and landscape share the same ranking."""
    for item in [raw, *(raw.get('formats') or [])]:
        width, height = item.get('width'), item.get('height')
        if isinstance(width, (int, float)) and isinstance(height, (int, float)) and min(width, height) > 0:
            item['short_side'] = min(width, height)
    return raw


def build_output_template(config: DownloadConfig, job: DownloadJob | None = None) -> str:
    output_dir = config.output_dir.expanduser().resolve()
    if job and job.folder:
        output_dir = output_dir / _clean_path_part(job.folder)

    quality = job.quality if job and job.quality else config.quality
    quality_suffix = quality if quality in QUALITY_FORMATS else (
        "format-" + hashlib.sha256(quality.encode("utf-8")).hexdigest()[:12]
    )
    name = "%(title).180B"
    if job and job.filename:
        name = _clean_path_part(job.filename).encode("utf-8")[:180].decode("utf-8", "ignore")
    name = f"{name} [%(id)s] [{quality_suffix}].%(ext)s"

    return str(output_dir / name)


def build_ydl_options(
    config: DownloadConfig,
    job: DownloadJob | None = None,
    progress_callback: ProgressCallback | None = None,
    download: bool = True,
) -> dict[str, Any]:
    quality = job.quality if job and job.quality else config.quality

    options: dict[str, Any] = {
        "format": resolve_format(quality),
        "merge_output_format": "mp4",
        "outtmpl": build_output_template(config, job),
        "noplaylist": not (job and job.playlist_index is not None),
        "retries": config.retries,
        "fragment_retries": config.fragment_retries,
        "continuedl": True,
        "overwrites": config.overwrite,
        "quiet": True,
        "no_warnings": False,
        "socket_timeout": 15,
        "extractor_retries": 1,
    }

    if download:
        options["sleep_interval"] = config.sleep_interval
        options["max_sleep_interval"] = config.max_sleep_interval

    if config.rate_limit:
        options["ratelimit"] = config.rate_limit

    if config.cookiefile:
        options["cookiefile"] = str(config.cookiefile.expanduser().resolve())
    elif config.use_cookies:
        options["cookiesfrombrowser"] = (config.browser,)

    if config.ffmpeg_location:
        options["ffmpeg_location"] = str(config.ffmpeg_location)

    if download and config.download_archive:
        options["download_archive"] = str(config.download_archive.expanduser().resolve())

    if progress_callback:
        options["progress_hooks"] = [progress_callback]

    return options


def analyze_url(url: str, config: DownloadConfig) -> VideoInfo:
    options = build_ydl_options(config, download=False)
    options["skip_download"] = True

    with yt_dlp.YoutubeDL(options) as ydl:
        raw = ydl.extract_info(url, download=False, process=False)
        if raw is not None:
            raw = _select_video(ydl, raw, playlist_index=None, cancel_event=None)
            raw = ydl.process_ie_result(annotate_dimensions(raw), download=False)

    if raw is None:
        raise RuntimeError("Could not load metadata for this URL.")

    formats = [
        FormatInfo(
            format_id=str(item.get("format_id", "")),
            ext=item.get("ext"),
            width=item.get("width"),
            height=item.get("height"),
            vcodec=item.get("vcodec"),
            acodec=item.get("acodec"),
            filesize=item.get("filesize") or item.get("filesize_approx"),
            note=item.get("format_note"),
        )
        for item in raw.get("formats", [])
    ]

    return VideoInfo(
        url=url,
        title=raw.get("title"),
        uploader=raw.get("uploader") or raw.get("channel"),
        duration=raw.get("duration"),
        width=raw.get("width"),
        height=raw.get("height"),
        thumbnail=raw.get("thumbnail"),
        formats=formats,
        raw=yt_dlp.YoutubeDL.sanitize_info(raw),
    )


def download_job(
    job: DownloadJob,
    config: DownloadConfig,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> DownloadResult:
    """Download one video; cancellation is cooperative at hooks and conversion.

    An archive hit returns ``skipped`` with no assumed local path. Set
    ``config.overwrite`` to retry an archived item whose output is missing.
    ``playlist_index`` is a 1-based video-only ordinal, not a raw photo/video
    carousel position.
    """
    started_at = datetime.now()
    captured_path: Path | None = None
    archive_locked = False

    def progress_hook(data: dict[str, Any]) -> None:
        nonlocal captured_path
        _check_cancelled(cancel_event)
        info = data.get("info_dict") or {}
        if data.get("status") == "finished" and info.get("filepath"):
            captured_path = Path(info["filepath"])
        if progress_callback:
            progress_callback(data)
        _check_cancelled(cancel_event)

    try:
        _check_cancelled(cancel_event)
        if job.playlist_index is not None and (
            type(job.playlist_index) is not int or job.playlist_index < 1
        ):
            raise ValueError("playlist_index must be a positive, 1-based video index.")
        # Serialize archive users in this process, including callers outside batch.
        if config.download_archive:
            while not _ARCHIVE_LOCK.acquire(timeout=0.1):
                _check_cancelled(cancel_event)
            archive_locked = True
            _check_cancelled(cancel_event)
            config.download_archive.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        config.output_dir.mkdir(parents=True, exist_ok=True)
        if job.folder:
            (config.output_dir / _clean_path_part(job.folder)).mkdir(
                parents=True,
                exist_ok=True,
            )

        options = build_ydl_options(config, job, progress_hook, download=True)
        options["postprocessor_hooks"] = [progress_hook]
        with yt_dlp.YoutubeDL(options) as ydl:
            archived = False
            preparing = True
            original_check = ydl.in_download_archive
            original_record = ydl.record_download_archive
            deferred_records: list[dict[str, Any]] = []

            def check_archive(info: dict[str, Any]) -> bool:
                nonlocal archived
                matched = not preparing and not config.overwrite and original_check(info)
                archived = archived or matched
                return matched

            ydl.in_download_archive = check_archive
            ydl.record_download_archive = lambda info: deferred_records.append(dict(info))
            raw = ydl.extract_info(job.url, download=False, process=False)
            _check_cancelled(cancel_event)
            if raw is not None:
                raw = _select_video(ydl, raw, job.playlist_index, cancel_event)
                preparing = False
                if check_archive(raw):
                    archived = True
                else:
                    raw = ydl.process_ie_result(annotate_dimensions(raw), download=True)
            _check_cancelled(cancel_event)
            if archived:
                return DownloadResult(
                    url=job.url,
                    status="skipped",
                    started_at=started_at,
                    finished_at=datetime.now(),
                    title=raw.get("title") if raw else None,
                )
            if raw is None:
                raise RuntimeError("No video metadata or output was returned; download was not completed.")
            prepared = Path(ydl.prepare_filename(raw))
            explicit_path = raw.get("filepath") or raw.get("_filename")
            output_path = _resolve_existing_output(
                Path(explicit_path) if explicit_path else prepared, captured_path
            )
            _check_cancelled(cancel_event)
            if config.compatible_mp4:
                progress_hook({"status": "converting"})
                output_path = make_compatible_mp4(output_path, config, cancel_event)
            _require_output(output_path)
            probed_width, probed_height = probe_resolution(output_path, config)
            _check_cancelled(cancel_event)
            # yt-dlp's own archive formatter retains extractor and legacy-ID semantics.
            if config.download_archive:
                original_record(deferred_records[-1] if deferred_records else raw)

        return DownloadResult(
            url=job.url,
            status="success",
            started_at=started_at,
            finished_at=datetime.now(),
            output_path=output_path,
            title=raw.get("title"),
            width=probed_width or raw.get("width"),
            height=probed_height or raw.get("height"),
        )

    except Exception as exc:  # yt-dlp raises several concrete subclasses.
        return DownloadResult(
            url=job.url,
            status=(
                "cancelled" if isinstance(exc, DownloadCancelled)
                or (cancel_event is not None and cancel_event.is_set())
                else "rate_limited" if is_rate_limit_error(exc) else "error"
            ),
            started_at=started_at,
            finished_at=datetime.now(),
            error=str(exc),
        )
    finally:
        if archive_locked:
            _ARCHIVE_LOCK.release()


def is_rate_limit_error(error: Exception | str) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in (
        "429", "rate-limit", "too many requests", "please wait a few minutes",
        "feedback_required", "temporarily blocked", "try again later",
    ))


def _is_video_entry(info: dict[str, Any]) -> bool:
    if (info.get("ext") or "").lower() in {"jpg", "jpeg", "png", "webp", "gif", "m4a", "mp3", "wav", "ogg"}:
        return False
    formats = info.get("formats") or []
    if formats:
        return any(item.get("vcodec") != "none" for item in formats)
    # Instagram's product extractor keeps photos as entries with no formats.
    if "formats" in info and not info.get("url"):
        return False
    return info.get("vcodec") != "none"


def _select_video(
    ydl: Any, raw: dict[str, Any], playlist_index: int | None, cancel_event: Event | None,
) -> dict[str, Any]:
    # Resolve indirections without processing a playlist or downloading media.
    for _ in range(20):
        _check_cancelled(cancel_event)
        kind = raw.get("_type", "video")
        if kind in {"url", "url_transparent"}:
            resolved = ydl.extract_info(raw["url"], download=False, process=False, ie_key=raw.get("ie_key"))
            if resolved is None:
                raise RuntimeError("Could not resolve the selected video metadata.")
            if kind == "url_transparent":
                excluded = {"_type", "url", "ie_key", "id", "extractor", "extractor_key"}
                resolved = {**resolved, **{
                    key: value for key, value in raw.items()
                    if key not in excluded and value is not None
                }}
            raw = resolved
            continue
        if kind in {"playlist", "multi_video"} or "entries" in raw:
            if playlist_index is None:
                raise ValueError(
                    "This URL is a collection. Use a direct video URL or set playlist_index "
                    "to a 1-based video index to download exactly one item."
                )
            video_index = 0
            selected = None
            for entry in raw.get("entries") or []:
                _check_cancelled(cancel_event)
                if entry is not None and _is_video_entry(entry):
                    video_index += 1
                    if video_index == playlist_index:
                        selected = entry
                        break
            if selected is None:
                raise ValueError(f"playlist_index {playlist_index} is out of range for this collection.")
            selected = dict(selected)
            for key in ("extractor", "extractor_key", "webpage_url", "original_url"):
                if key in raw:
                    selected.setdefault(key, raw[key])
            raw = selected
            playlist_index = None
            continue
        if playlist_index not in (None, 1):
            raise ValueError("playlist_index is out of range: this URL contains one video.")
        if not _is_video_entry(raw):
            raise ValueError("The selected item is not a video. Use a direct video URL.")
        if raw.get("additional_urls"):
            raise ValueError("This item requests additional downloads; use a direct single-video URL.")
        return raw
    raise ValueError("Too many nested collection/URL redirects; use a direct video URL.")


def _media_binary(config: DownloadConfig, name: str) -> str:
    location = config.ffmpeg_location
    if location is None:
        return name
    location = location.expanduser()
    if location.name in {"ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"}:
        return str(location.with_name(name + location.suffix))
    return str(location / name)


def _compatible_streams(path: Path, config: DownloadConfig) -> bool:
    completed = subprocess.run(
        [_media_binary(config, "ffprobe"), "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,pix_fmt", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    streams = json.loads(completed.stdout).get("streams", [])
    video = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
    return bool(video) and all(
        stream.get("codec_name") == "h264" and stream.get("pix_fmt") == "yuv420p"
        for stream in video
    ) and all(stream.get("codec_name") == "aac" for stream in audio)


def make_compatible_mp4(
    path: Path, config: DownloadConfig, cancel_event: Event | None = None,
) -> Path:
    _check_cancelled(cancel_event)
    _require_output(path)
    if path.suffix.lower() == ".mp4" and _compatible_streams(path, config):
        _check_cancelled(cancel_event)
        return path.resolve()

    compatible_path = path.with_name(f"{path.stem}.h264.mp4")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".mp4", dir=path.parent)
    os.close(fd)
    temporary_path = Path(temporary)

    command = [
        _media_binary(config, "ffmpeg"),
        "-y",
        "-nostdin",
        "-i",
        str(path),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(temporary_path),
    ]

    try:
        _check_cancelled(cancel_event)
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                while True:
                    _check_cancelled(cancel_event)
                    try:
                        _, stderr = process.communicate(timeout=0.1)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except BaseException:
                process.terminate()
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                raise
            if process.returncode:
                raise RuntimeError(f"ffmpeg conversion failed: {stderr.strip()[-2000:]}")
        _check_cancelled(cancel_event)
        _require_output(temporary_path)
        os.replace(temporary_path, compatible_path)
        return compatible_path.resolve()
    finally:
        temporary_path.unlink(missing_ok=True)


def probe_resolution(path: Path, config: DownloadConfig) -> tuple[int | None, int | None]:
    ffprobe = _media_binary(config, "ffprobe")
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(path),
    ]

    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
        payload = json.loads(completed.stdout)
        stream = payload.get("streams", [{}])[0]
        return stream.get("width"), stream.get("height")
    except Exception:
        return None, None


def _resolve_existing_output(prepared: Path, captured: Path | None = None) -> Path:
    candidates = []

    if prepared.suffix:
        candidates.append(prepared.with_suffix(".mp4"))
    candidates.append(prepared)

    if captured:
        if captured.suffix:
            candidates.append(captured.with_suffix(".mp4"))
        candidates.append(captured)

    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate.resolve()

    raise FileNotFoundError(f"Download did not produce a nonempty output file: {prepared}")


def _require_output(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f"Download did not produce a nonempty output file: {path}")


def _clean_path_part(value: str) -> str:
    keep = []
    for char in value.strip():
        if char.isalnum() or char in (" ", "-", "_", "."):
            keep.append(char)
    cleaned = "".join(keep).strip(" .")
    return cleaned or "download"
