from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from instantdownloader.core.batch import download_batch
from instantdownloader.core.config import default_report_path, make_config
from instantdownloader.core.downloader import analyze_url, download_job
from instantdownloader.core.excel import read_jobs_from_excel
from instantdownloader.core.models import DownloadJob, DownloadResult
from instantdownloader.core.report import write_report


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print('Interrupted. Completed queue results remain saved.', file=sys.stderr)
        return 130
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="instantdownloader",
        description="Download Instagram videos with conservative queue controls.",
    )
    subparsers = parser.add_subparsers(required=True)

    gui = subparsers.add_parser('gui', help='Open the CustomTkinter desktop app (requires gui extra).')
    gui.set_defaults(func=handle_gui)

    analyze = subparsers.add_parser("analyze", help="Show available video info/formats.")
    add_common_options(analyze)
    analyze.add_argument("url")
    analyze.add_argument('--json', action='store_true', help='Print machine-readable metadata.')
    analyze.set_defaults(func=handle_analyze)

    download = subparsers.add_parser("download", help="Download one URL.")
    add_common_options(download)
    download.add_argument("url")
    download.add_argument("--filename", help="Optional output filename without extension.")
    download.add_argument("--folder", help="Optional subfolder inside output directory.")
    download.add_argument("--report", type=Path, help="Optional CSV report path.")
    download.add_argument('--playlist-index', type=int, help='Select one video from a carousel, 1-based, excluding photos.')
    download.set_defaults(func=handle_download)

    batch = subparsers.add_parser("batch", help="Download URLs from an Excel file.")
    add_common_options(batch)
    batch.add_argument("excel_path", type=Path)
    batch.add_argument("--sheet", help="Excel sheet name. Defaults to active sheet.")
    batch.add_argument("--report", type=Path, default=default_report_path())
    batch.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Parallel downloads. Conservative cap: 2.",
    )
    batch.set_defaults(func=handle_batch)

    profile = subparsers.add_parser('profile', help='Discover a bounded sample of profile videos (experimental).')
    add_common_options(profile)
    profile.add_argument('profile')
    profile.add_argument('--limit', type=int, default=10, help='Maximum posts inspected, 1..1000. Not a video count.')
    profile.add_argument('--scope', choices=['posts', 'reels'], default='posts')
    profile.add_argument('--manifest', type=Path, help='Write discovered items and completion state to JSON.')
    profile.add_argument('--enqueue', action='store_true', help='Add discoveries to the persistent queue; does not download.')
    profile.add_argument('--session-file', type=Path, help='Existing Instaloader session file; requires --login.')
    profile.add_argument('--login', help='Username associated with the existing session, not a password.')
    profile.set_defaults(func=handle_profile)

    queue = subparsers.add_parser('queue', help='Manage the same persistent queue as the desktop app.')
    commands = queue.add_subparsers(required=True)
    add = commands.add_parser('add', help='Queue one URL.')
    add_common_options(add)
    add.add_argument('url')
    add.add_argument('--playlist-index', type=int)
    add.set_defaults(func=handle_queue_add)
    listing = commands.add_parser('list', help='List tasks without exposing configuration or session paths.')
    listing.add_argument('--json', action='store_true')
    listing.set_defaults(func=handle_queue_list)
    run = commands.add_parser('run', help='Resume pending tasks sequentially.')
    run.add_argument('--report-dir', type=Path)
    run.set_defaults(func=handle_queue_run)
    retry = commands.add_parser('retry', help='Return selected completed or failed tasks to the queue.')
    retry.add_argument('ids', type=int, nargs='+')
    retry.set_defaults(func=handle_queue_retry)

    return parser


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-dir", type=Path, default=Path.cwd() / "downloads")
    parser.add_argument(
        "--quality",
        default="best",
        help="best, 1080p, 720p, 480p, or a raw yt-dlp format selector.",
    )
    parser.add_argument("--cookies", action="store_true", help="Use browser cookies.")
    parser.add_argument("--browser", default="chrome", help="chrome, edge, firefox, safari.")
    parser.add_argument("--ffmpeg-location", type=Path)
    parser.add_argument("--sleep", type=float, default=3.0, help="Minimum sleep between items.")
    parser.add_argument("--max-sleep", type=float, default=8.0, help="Maximum sleep between items.")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--compatible-mp4",
        action="store_true",
        help="Transcode final MP4 to H.264/AAC for QuickTime/Windows compatibility.",
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Ignore download_archive.txt. Existing output files are still preserved.",
    )


def config_from_args(args: argparse.Namespace):
    config = make_config(
        output_dir=args.output_dir,
        quality=args.quality,
        use_cookies=args.cookies,
        browser=args.browser,
        ffmpeg_location=args.ffmpeg_location,
        sleep_interval=args.sleep,
        max_sleep_interval=args.max_sleep,
        retries=args.retries,
        compatible_mp4=args.compatible_mp4,
    )
    if args.no_archive:
        config.download_archive = None
    return config


def handle_analyze(args: argparse.Namespace) -> int:
    config = config_from_args(args)
    info = analyze_url(args.url, config)
    if args.json:
        payload = asdict(info)
        payload.pop('raw', None)
        print(json.dumps(payload, ensure_ascii=True))
        return 0

    print(f"Title: {info.title or '-'}")
    print(f"Uploader: {info.uploader or '-'}")
    print(f"Duration: {info.duration or '-'}")
    print(f"Resolution: {_resolution(info.width, info.height)}")
    print(f"Thumbnail: {info.thumbnail or '-'}")
    print()
    print("Available formats:")

    for item in info.formats:
        print(
            "  "
            f"{item.format_id:>8} "
            f"{_resolution(item.width, item.height):>10} "
            f"{item.ext or '-':>5} "
            f"v={item.vcodec or '-'} "
            f"a={item.acodec or '-'} "
            f"{item.note or ''}"
        )

    return 0


def handle_download(args: argparse.Namespace) -> int:
    config = config_from_args(args)
    job = DownloadJob(
        url=args.url,
        filename=args.filename,
        folder=args.folder,
        quality=args.quality,
        playlist_index=args.playlist_index,
    )
    result = download_job(job, config)

    report_path = write_report([result], args.report or default_report_path())
    print(f"Report: {report_path}")

    if result.ok:
        print(f"{'Already archived' if result.status == 'skipped' else 'Downloaded'}: {result.output_path or result.url}")
        return 0

    print(f"Failed: {result.error}")
    return 2


def handle_batch(args: argparse.Namespace) -> int:
    config = config_from_args(args)
    jobs = read_jobs_from_excel(args.excel_path, args.sheet)

    if not jobs:
        print("No URLs found in Excel file.")
        return 1

    print(f"Found {len(jobs)} job(s). Starting downloads...")

    now = datetime.now()
    snapshots = {job.url: DownloadResult(job.url, 'pending', now, now) for job in jobs}
    write_report(list(snapshots.values()), args.report)

    def on_result(result):
        marker = "OK" if result.ok else "FAIL"
        target = result.output_path or result.error or ""
        print(f"[{marker}] {result.url} -> {target}")
        snapshots[result.url] = result
        write_report(list(snapshots.values()), args.report)

    results = download_batch(jobs, config, args.concurrency, on_result)
    report_path = write_report(results, args.report)

    failures = [item for item in results if not item.ok]
    print(f"Report: {report_path}")
    print(f"Success: {len(results) - len(failures)} | Failures: {len(failures)}")

    return 0 if not failures else 2


def handle_profile(args):
    from instantdownloader.core.profiles import discover_profile, normalize_profile
    from instantdownloader.core.store import Store
    profile = normalize_profile(args.profile)
    store = Store()
    config = config_from_args(args)
    config.download_archive = None
    added = 0
    def on_item(item):
        nonlocal added
        store.save_discovery(profile, asdict(item))
        if args.enqueue:
            added += store.add(DownloadJob(item.url, playlist_index=item.playlist_index), config, item.title)[1]
    result = discover_profile(profile, limit=args.limit, scope=args.scope,
        session_file=args.session_file, login=args.login, on_item=on_item)
    payload = {'profile': profile, 'complete': result.complete, 'message': result.message,
               'items': [asdict(item) for item in result.items], 'enqueued': added}
    if args.manifest:
        import os
        import tempfile
        path = args.manifest.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix='.json')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(payload, stream, ensure_ascii=True, indent=2)
            os.replace(tmp, path)
        finally:
            Path(tmp).unlink(missing_ok=True)
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0 if result.complete else 2


def handle_gui(args):
    try:
        from instantdownloader.gui import main as open_gui
    except ModuleNotFoundError as exc:
        raise RuntimeError('Install desktop dependencies with: python -m pip install -e ".[gui]"') from exc
    open_gui()
    return 0


def handle_queue_add(args):
    from instantdownloader.core.store import Store
    config = config_from_args(args)
    config.download_archive = None
    task_id, added = Store().add(DownloadJob(args.url, playlist_index=args.playlist_index), config)
    print(f"{'Queued' if added else 'Already registered'}: {task_id}")
    return 0


def handle_queue_list(args):
    from instantdownloader.core.store import Store
    rows = [{key: row[key] for key in ('id', 'title', 'status', 'output_path', 'error')} for row in Store().rows()]
    if args.json:
        print(json.dumps(rows, ensure_ascii=True))
    else:
        for row in rows:
            print(f"{row['id']:>5}  {row['status']:<14} {row['title']}")
    return 0


def handle_queue_run(args):
    from instantdownloader.core.queue import QueueRunner
    from instantdownloader.core.store import AppLock, Store
    store = Store()
    lock = AppLock(store.path.parent)
    runner = QueueRunner(store, lambda kind, payload: print(payload) if kind in ('queue_status', 'report', 'error') else None)
    try:
        store.recover()
        runner.start(args.report_dir or store.path.parent/'reports')
        try:
            runner.thread.join()
        except KeyboardInterrupt:
            runner.stop()
            runner.thread.join()
            return 130
        return 0 if not runner.error and not any(r['status'] in ('error', 'rate_limited') for r in store.rows() if r['id'] in runner.processed_ids) else 2
    finally:
        lock.close()


def handle_queue_retry(args):
    from instantdownloader.core.store import Store
    store = Store()
    for task_id in args.ids:
        store.retry(task_id)
    print('Selected tasks returned to pending when eligible.')
    return 0


def _resolution(width: int | None, height: int | None) -> str:
    if width and height:
        return f"{width}x{height}"
    if height:
        return f"{height}p"
    return "-"


if __name__ == "__main__":
    raise SystemExit(main())
