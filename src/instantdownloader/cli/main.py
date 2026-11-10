from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from threading import Event

from instantdownloader import __version__
from instantdownloader.core.batch import download_batch
from instantdownloader.core.config import default_report_path, make_config
from instantdownloader.core.downloader import analyze_url, download_job
from instantdownloader.core.excel import read_jobs_from_excel
from instantdownloader.core.models import DownloadJob, DownloadResult
from instantdownloader.core.report import write_report
from instantdownloader.core.manifests import write_json, json_value


class InputError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(message)


def emit(args, payload):
    args.payload = payload


def exit_for(args, code=None, partial=False):
    if not getattr(args, 'json', False):
        return 2
    if code in ('session_unavailable', 'browser_session', 'session_required'):
        return 4
    if code in ('rate_limited', 'local_throttle'):
        return 5
    return 3 if partial else 2


def iso_day(value):
    try:
        day = date.fromisoformat(value)
        if day.isoformat() != value:
            raise ValueError()
        return day
    except ValueError:
        raise argparse.ArgumentTypeError('Dates must use YYYY-MM-DD.') from None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) > 2 and argv[:2] == ['profile', 'discover'] and not argv[2].startswith('-'):
        argv.pop(1)
    parser = build_parser()
    args = None
    machine = '--json' in argv
    try:
        args = parser.parse_args(argv)
        if machine or args.func == handle_profile:
            with redirect_stdout(sys.stderr):
                code = args.func(args)
        else:
            code = args.func(args)
        if hasattr(args, 'payload'):
            print(json.dumps(args.payload, ensure_ascii=True, default=json_value))
        return code
    except KeyboardInterrupt:
        if machine:
            print(json.dumps({'schema_version': 1, 'complete': False, 'error_code': 'cancelled',
                              'message': 'Interrupted; completed results remain saved.'}))
        else:
            print('Interrupted. Completed queue results remain saved.', file=sys.stderr)
        return 130
    except Exception as exc:
        from instantdownloader.core.instagram_session import SessionError
        code = 'session_required' if isinstance(exc, SessionError) else 'invalid_input' if isinstance(exc, (ValueError, OSError)) else 'operation_failed'
        message = str(exc) if isinstance(exc, (ValueError, OSError, SessionError)) else f'{type(exc).__name__}: operation failed; inspect stderr diagnostics.'
        if machine:
            print(json.dumps({'schema_version': 1, 'complete': False, 'error_code': code, 'message': message}))
        else:
            print(f'Error: {message}', file=sys.stderr)
        return exit_for(args, code)


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(
        prog="instantdownloader",
        description="Download Instagram videos with conservative queue controls.",
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    subparsers = parser.add_subparsers(required=True)

    gui = subparsers.add_parser('gui', help='Open the CustomTkinter desktop app (requires gui extra).')
    gui.set_defaults(func=handle_gui)

    analyze = subparsers.add_parser("analyze", help="Show available video info/formats.")
    add_common_options(analyze)
    analyze.add_argument("url")
    analyze.set_defaults(func=handle_analyze)

    download = subparsers.add_parser("download", help="Download one URL.")
    add_common_options(download)
    download.add_argument("url")
    download.add_argument("--filename", help="Optional output filename without extension.")
    download.add_argument("--folder", help="Optional subfolder inside output directory.")
    download.add_argument("--report", type=Path, help="Optional CSV report path.")
    download.add_argument('--json-report', type=Path, help='Write the structured result atomically to JSON.')
    download.add_argument('--playlist-index', type=int, help='Select one video from a carousel, 1-based, excluding photos.')
    download.set_defaults(func=handle_download)

    batch = subparsers.add_parser("batch", help="Download URLs from an Excel file.")
    add_common_options(batch)
    batch.add_argument("excel_path", type=Path)
    batch.add_argument("--sheet", help="Excel sheet name. Defaults to active sheet.")
    batch.add_argument("--report", type=Path, default=default_report_path())
    batch.add_argument('--json-report', type=Path)
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
    profile.add_argument('--limit', type=int, default=10, help='Maximum posts inspected per source, 1..1000. Not a video count.')
    profile.add_argument('--scope', choices=['posts', 'reels', 'all'], default='posts')
    profile.add_argument('--manifest', '--output', type=Path, help='Write discovered items and completion state to JSON.')
    profile.add_argument('--use-session', action='store_true', help='Use the dedicated session saved by the desktop app; never reads your personal browser.')
    profile.add_argument('--published-before', type=iso_day, help='Inclusive publication day, YYYY-MM-DD, UTC.')
    profile.add_argument('--published-after', type=iso_day, help='Inclusive publication day, YYYY-MM-DD, UTC.')
    profile.add_argument('--fetch-dates', action='store_true', help='Resolve missing publication dates, one post at a time; requires --use-session when missing.')
    profile.add_argument('--allow-partial', action='store_true', help='Explicitly allow enqueue of incomplete/limited discoveries.')
    profile.add_argument('--enqueue', action='store_true', help='Add discoveries to the persistent queue; does not download.')
    profile.add_argument('--session-file', type=Path, help='Existing Instaloader session file; requires --login.')
    profile.add_argument('--login', help='Username associated with the existing session, not a password.')
    profile.add_argument('--discovery-browser', choices=['chrome', 'firefox', 'edge', 'safari'],
                         help='Explicitly import Instagram cookies for discovery; requires --login. Never saves cookies.')
    profile.set_defaults(func=handle_profile)

    manifest = subparsers.add_parser('download-list', help='Download or enqueue the reviewed items from a profile JSON manifest.')
    add_common_options(manifest)
    manifest.add_argument('manifest', type=Path)
    manifest.add_argument('--allow-partial', action='store_true')
    manifest.add_argument('--enqueue', action='store_true', help='Only enqueue; do not start downloads.')
    manifest.add_argument('--report', type=Path, default=default_report_path())
    manifest.add_argument('--json-report', type=Path)
    manifest.add_argument('--concurrency', type=int, choices=[1, 2], default=1)
    manifest.set_defaults(func=handle_manifest)

    filtering = subparsers.add_parser('filter-list', help='Filter a saved manifest without rediscovering a profile.')
    filtering.add_argument('manifest', type=Path)
    filtering.add_argument('--output', type=Path, required=True)
    filtering.add_argument('--published-before', type=iso_day)
    filtering.add_argument('--published-after', type=iso_day)
    filtering.add_argument('--fetch-dates', action='store_true')
    filtering.add_argument('--use-session', action='store_true')
    filtering.add_argument('--json', action='store_true')
    filtering.set_defaults(func=handle_filter)

    session = subparsers.add_parser('session', help='Manage the dedicated Instagram session without exposing cookies.')
    actions = session.add_subparsers(dest='action', required=True)
    for action in ('status', 'test', 'connect', 'delete'):
        command = actions.add_parser(action)
        command.add_argument('--json', action='store_true')
        if action == 'delete':
            command.add_argument('--yes', action='store_true', help='Confirm local session deletion.')
        command.set_defaults(func=handle_session)

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
    run.add_argument('--json', action='store_true')
    run.add_argument('--json-report', type=Path)
    run.set_defaults(func=handle_queue_run)
    retry = commands.add_parser('retry', help='Return selected completed or failed tasks to the queue.')
    retry.add_argument('ids', type=int, nargs='*')
    retry.add_argument('--failed', action='store_true', help='Retry error/cancelled tasks, never rate-limited tasks automatically.')
    retry.add_argument('--json', action='store_true')
    retry.set_defaults(func=handle_queue_retry)

    return parser


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--json', action='store_true', help='One JSON result on stdout; diagnostics on stderr.')
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
        emit(args, payload)
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
    validate_reports(args)
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
    if args.json or args.json_report:
        payload = download_payload([result], report_path)
        if args.json_report:
            write_json(payload, args.json_report)
        if args.json:
            emit(args, payload)
            return 0 if result.ok else exit_for(args, result.status)
    print(f"Report: {report_path}")

    if result.ok:
        print(f"{'Already archived' if result.status == 'skipped' else 'Downloaded'}: {result.output_path or result.url}")
        return 0

    print(f"Failed: {result.error}")
    return 2


def handle_batch(args: argparse.Namespace) -> int:
    validate_reports(args, args.excel_path)
    config = config_from_args(args)
    jobs = read_jobs_from_excel(args.excel_path, args.sheet)
    return run_jobs(args, jobs, config)


def download_payload(results, report_path):
    from collections import Counter
    return {'schema_version': 1, 'complete': all(item.ok for item in results),
            'counts': dict(Counter(item.status for item in results)),
            'report': str(report_path), 'results': [asdict(item) for item in results]}


def validate_reports(args, source=None):
    outputs = [path.expanduser().resolve() for path in (getattr(args, 'report', None), args.json_report) if path]
    if len(set(outputs)) != len(outputs):
        raise ValueError('CSV and JSON reports require different output paths.')
    if source is not None and source.expanduser().resolve() in outputs:
        raise ValueError('Reports must not overwrite the input file.')


def run_jobs(args, jobs, config):
    if not jobs:
        report_path = write_report([], args.report)
        payload = download_payload([], report_path)
        if args.json_report:
            write_json(payload, args.json_report)
        if args.json:
            emit(args, payload)
        else:
            print('No URLs to download.')
        return 0

    print(f"Found {len(jobs)} job(s). Starting downloads...")

    now = datetime.now()
    snapshots = [DownloadResult(job.url, 'pending', now, now) for job in jobs]
    write_report(snapshots, args.report)
    completed = set()

    def on_result(result):
        marker = "OK" if result.ok else "FAIL"
        target = result.output_path or result.error or ""
        print(f"[{marker}] {result.url} -> {target}")
        index = next(index for index, job in enumerate(jobs) if index not in completed and job.url == result.url)
        completed.add(index)
        snapshots[index] = result
        write_report(snapshots, args.report)
        if args.json_report:
            write_json(download_payload(snapshots, args.report.expanduser().resolve()), args.json_report)

    cancel = Event()
    try:
        results = download_batch(jobs, config, args.concurrency, on_result, cancel_event=cancel)
    except KeyboardInterrupt:
        cancel.set()
        report_path = write_report(snapshots, args.report)
        payload = {**download_payload(snapshots, report_path), 'complete': False, 'error_code': 'cancelled'}
        if args.json_report:
            write_json(payload, args.json_report)
        if args.json:
            emit(args, payload)
        return 130
    report_path = write_report(results, args.report)

    failures = [item for item in results if not item.ok]
    payload = download_payload(results, report_path)
    if args.json_report:
        write_json(payload, args.json_report)
    if args.json:
        emit(args, payload)
    print(f"Report: {report_path}")
    print(f"Success: {len(results) - len(failures)} | Failures: {len(failures)}")

    return 0 if not failures else exit_for(args, 'rate_limited' if any(item.status == 'rate_limited' for item in failures) else None, partial=True)


def handle_profile(args):
    from .automation import discover
    return discover(args)


def handle_manifest(args):
    from .automation import download_manifest
    return download_manifest(args)


def handle_session(args):
    from .automation import session_command
    return session_command(args)


def handle_filter(args):
    from .automation import filter_manifest
    return filter_manifest(args)


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
    if args.json:
        emit(args, {'schema_version': 1, 'id': task_id, 'added': added})
    else:
        print(f"{'Queued' if added else 'Already registered'}: {task_id}")
    return 0


def handle_queue_list(args):
    from instantdownloader.core.store import Store
    rows = [{key: row[key] for key in ('id', 'title', 'status', 'output_path', 'error')} for row in Store().rows()]
    if args.json:
        emit(args, rows)
    else:
        for row in rows:
            print(f"{row['id']:>5}  {row['status']:<14} {row['title']}")
    return 0


def handle_queue_run(args):
    from instantdownloader.core.queue import QueueRunner
    from instantdownloader.core.store import AppLock, Store
    store = Store()
    lock = AppLock(store.path.parent)
    reports = []
    def event(kind, payload):
        if kind == 'report':
            reports.append(payload)
        if kind in ('queue_status', 'report', 'error'):
            print(payload, file=sys.stderr if args.json else sys.stdout)
    runner = QueueRunner(store, event)
    try:
        store.recover()
        runner.start(args.report_dir or store.path.parent/'reports')
        interrupted = False
        try:
            runner.thread.join()
        except KeyboardInterrupt:
            runner.stop()
            runner.thread.join()
            interrupted = True
        from collections import Counter
        rows = store.rows()
        processed = [row for row in rows if row['id'] in runner.processed_ids]
        failures = any(row['status'] not in ('success', 'skipped') for row in processed)
        payload = {'schema_version': 1, 'complete': not interrupted and not runner.error and not failures,
                   'processed_ids': sorted(runner.processed_ids),
                   'counts': dict(Counter(row['status'] for row in processed)),
                   'pending': sum(row['status'] == 'pending' for row in rows),
                   'report': reports[-1] if reports else None, 'error': runner.error,
                   'error_code': 'cancelled' if interrupted else 'operation_failed' if runner.error else None}
        if args.json_report:
            write_json(payload, args.json_report)
        if args.json:
            emit(args, payload)
        if interrupted:
            return 130
        return 0 if payload['complete'] else exit_for(args, 'rate_limited' if any(row['status'] == 'rate_limited' for row in processed) else None, partial=True)
    finally:
        lock.close()


def handle_queue_retry(args):
    from instantdownloader.core.store import Store
    store = Store()
    if not args.ids and not args.failed:
        raise ValueError('Specify task IDs or --failed.')
    rows = store.rows()
    ids = set(args.ids)
    if args.failed:
        ids.update(row['id'] for row in rows if row['status'] in ('error', 'cancelled'))
    if ids - {row['id'] for row in rows}:
        raise ValueError('One or more task IDs do not exist.')
    eligible = [row['id'] for row in rows if row['id'] in ids and row['status'] not in ('pending', 'downloading', 'converting')]
    for task_id in eligible:
        store.retry(task_id)
    if args.json:
        emit(args, {'schema_version': 1, 'retried_ids': sorted(eligible)})
    else:
        print(f'{len(eligible)} selected tasks returned to pending.')
    return 0


def _resolution(width: int | None, height: int | None) -> str:
    if width and height:
        return f"{width}x{height}"
    if height:
        return f"{height}p"
    return "-"


if __name__ == "__main__":
    raise SystemExit(main())
