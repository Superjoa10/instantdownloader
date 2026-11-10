"""Noninteractive discovery, reviewed manifests and dedicated-session commands."""
import sys
from dataclasses import asdict
from threading import Event, Thread

from instantdownloader.core.instagram_session import InstagramSession, SessionError
from instantdownloader.core.manifests import read_manifest, write_json
from instantdownloader.core.models import DownloadJob
from instantdownloader.core import profiles
from instantdownloader.core.publication_dates import filter_publications, load_publication_dates, publication_day
from instantdownloader.core.store import Store, data_dir


def discover(args):
    from .main import config_from_args, emit, exit_for
    profile = profiles.normalize_profile(args.profile)
    # Validate before opening a browser or starting a scan.
    filter_publications([], args.published_before, args.published_after)
    if not 1 <= args.limit <= 1000:
        raise ValueError('--limit must be between 1 and 1000.')
    if args.use_session and (args.session_file or args.login or args.discovery_browser):
        raise ValueError('--use-session cannot be combined with legacy session options.')
    local = InstagramSession(data_dir()).load() if args.use_session else None
    config = config_from_args(args)
    config.download_archive = None
    store = Store()
    cancel = Event()
    staged = {}
    base = {'schema_version': 1, 'profile': profile, 'complete': False,
            'all_collected': False, 'error_code': 'in_progress', 'items': [], 'enqueued': 0}

    def checkpoint():
        if args.manifest:
            selected, missing = filter_publications(list(staged.values()), args.published_before, args.published_after)
            write_json({**base, 'items': selected, 'date_missing': missing}, args.manifest)

    def collect(item):
        values = asdict(item)
        staged[item.id] = values
        store.save_discovery(profile, values)
        checkpoint()

    checkpoint()
    result = profiles.discover_profile(profile, limit=args.limit, scope=args.scope,
        session_file=args.session_file, login=args.login, browser=args.discovery_browser,
        local_session=local, cancel_event=cancel, on_item=collect)
    staged = {item.id: asdict(item) for item in result.items}
    needs_dates = args.fetch_dates or args.published_before is not None or args.published_after is not None
    date_message = None
    date_error = None
    if needs_dates and any(not publication_day(item.get('date')) for item in staged.values()):
        # Never issue more requests after the discovery provider has stopped.
        if not result.complete:
            date_message = 'Date lookup not started because discovery is incomplete.'
        elif local is None:
            date_error = 'session_required'
            date_message = 'Missing publication dates require --use-session. Connect using the desktop app or session connect.'
        else:
            def on_date(identifier, value):
                if value:
                    staged[identifier]['date'] = value
                    store.save_discovery(profile, staged[identifier])
                    checkpoint()
            def on_stop(code):
                nonlocal date_error
                date_error = code
            try:
                date_message = load_publication_dates(list(staged.values()), local, cancel, on_date, on_stop=on_stop)
            except KeyboardInterrupt:
                cancel.set()
                checkpoint()
                raise
            except Exception as exc:
                date_error = 'date_lookup_failed'
                date_message = f'Date lookup stopped ({type(exc).__name__}); partial dates preserved.'
    selected, missing = filter_publications(list(staged.values()), args.published_before, args.published_after)
    dates_complete = not needs_dates or missing == 0
    complete = result.complete and dates_complete and date_error is None
    error = result.error_code or date_error or ('dates_incomplete' if not dates_complete else None)
    payload = {**base, 'complete': complete, 'discovery_complete': result.complete,
               'all_collected': result.all_collected and complete,
               'message': result.message, 'error_code': error, 'failed_source': result.failed_source,
               'counts': result.counts, 'inspected': result.inspected, 'pages': result.pages,
               'exhausted': result.exhausted, 'items': selected,
               'date_missing': missing, 'dates_complete': dates_complete, 'date_message': date_message,
               'selected_count': len(selected), 'discovered_count': len(staged),
               'filters': {'published_before': args.published_before.isoformat() if args.published_before else None,
                           'published_after': args.published_after.isoformat() if args.published_after else None,
                           'inclusive': True, 'timezone': 'UTC', 'unknown_dates': 'excluded_when_filtering'}}
    if args.enqueue:
        if not args.allow_partial and (not complete or not payload['all_collected']):
            payload['enqueue_refused'] = True
        else:
            payload['enqueued'] = sum(store.add(DownloadJob(item['url'], playlist_index=item.get('playlist_index')),
                                               config, item['title'])[1] for item in selected)
    if args.manifest:
        write_json(payload, args.manifest)
    emit(args, payload)
    if payload.get('enqueue_refused'):
        return exit_for(args, error, partial=True)
    return 0 if complete else exit_for(args, error, partial=True)


def download_manifest(args):
    from .main import config_from_args, emit, run_jobs, validate_reports
    validate_reports(args, args.manifest)
    document, jobs, titles = read_manifest(args.manifest, args.allow_partial)
    config = config_from_args(args)
    if args.enqueue:
        config.download_archive = None
        store = Store()
        added = sum(store.add(job, config, title)[1] for job, title in zip(jobs, titles))
        payload = {'schema_version': 1, 'complete': True, 'items': len(jobs), 'enqueued': added,
                   'source_complete': document['complete'], 'source_all_collected': document['all_collected']}
        if args.json_report:
            write_json(payload, args.json_report)
        if args.json:
            emit(args, payload)
        else:
            print(f'Queued {added} of {len(jobs)} reviewed items. Downloads have not started.')
        return 0
    return run_jobs(args, jobs, config)


def filter_manifest(args):
    from .main import emit, exit_for
    filter_publications([], args.published_before, args.published_after)
    if args.output.expanduser().resolve() == args.manifest.expanduser().resolve():
        raise ValueError('Use a separate --output path to preserve the input manifest.')
    document, _, _ = read_manifest(args.manifest, allow_partial=True)
    items = [dict(item) for item in document['items']]
    source_complete = document.get('discovery_complete', document['complete']) is True
    needs_dates = args.fetch_dates or args.published_before is not None or args.published_after is not None
    date_error, message = [], None
    if needs_dates and any(not publication_day(item.get('date')) for item in items):
        if document.get('error_code') in ('rate_limited', 'local_throttle', 'session_unavailable'):
            date_error.append(document['error_code'])
            message = 'Date lookup not started after a provider stop; existing dates only.'
        elif not args.use_session:
            date_error.append('session_required')
            message = 'Missing dates require --use-session.'
        else:
            if any(not isinstance(item.get('id'), str) for item in items):
                raise ValueError('Date lookup requires discovered item IDs. Generate a profile manifest first.')
            local = InstagramSession(data_dir()).load()
            def update(identifier, value):
                for item in items:
                    if item['id'] == identifier and value:
                        item['date'] = value
                selected, missing = filter_publications(items, args.published_before, args.published_after)
                write_json({**document, 'complete': False, 'all_collected': False, 'enqueued': 0,
                            'error_code': 'in_progress', 'items': selected, 'date_missing': missing}, args.output)
            try:
                message = load_publication_dates(items, local, Event(), update, on_stop=date_error.append)
            except Exception as exc:
                date_error.append('date_lookup_failed')
                message = f'Date lookup stopped ({type(exc).__name__}).'
    selected, missing = filter_publications(items, args.published_before, args.published_after)
    dates_complete = not needs_dates or missing == 0
    complete = source_complete and dates_complete and not date_error
    exhausted = document.get('exhausted', {})
    source_exhausted = document['all_collected'] or (source_complete and isinstance(exhausted, dict) and bool(exhausted) and all(value is True for value in exhausted.values()))
    payload = {**document, 'complete': bool(complete), 'all_collected': source_exhausted and bool(complete),
               'enqueued': 0, 'items': selected, 'selected_count': len(selected), 'input_count': len(items),
               'date_missing': missing, 'dates_complete': dates_complete, 'date_message': message,
               'error_code': (document.get('error_code') if not source_complete else None) or (date_error[0] if date_error else None) or ('dates_incomplete' if not dates_complete else None),
               'filters': {'published_before': args.published_before.isoformat() if args.published_before else None,
                           'published_after': args.published_after.isoformat() if args.published_after else None,
                           'timezone': 'UTC', 'inclusive': True, 'unknown_dates': 'excluded_when_filtering'},
               'parent_manifest': str(args.manifest.expanduser().resolve())}
    payload.pop('enqueue_refused', None)
    write_json(payload, args.output)
    if args.json:
        emit(args, payload)
    else:
        print(f'{len(selected)} selected; {missing} without a date. Saved: {args.output.expanduser().resolve()}')
    return 0 if payload['complete'] else exit_for(args, payload['error_code'], partial=True)


def session_command(args):
    from instantdownloader.core.store import AppLock
    if args.action == 'status':
        return _session_command(args)
    lock = AppLock(data_dir())
    try:
        return _session_command(args)
    finally:
        lock.close()


def _session_command(args):
    from .main import emit
    session = InstagramSession(data_dir())
    if args.action == 'status':
        payload = {'schema_version': 1, 'state': 'pending' if session.pending_path.exists() else
                   'saved' if session.saved else 'absent', 'verified_now': False}
    elif args.action == 'delete':
        if not args.yes:
            raise ValueError('Local session deletion requires --yes. This does not revoke Instagram sessions.')
        session.delete()
        payload = {'schema_version': 1, 'state': 'absent', 'deleted': True}
    elif args.action == 'test':
        session.test(Event())
        payload = {'schema_version': 1, 'state': 'verified', 'verified_now': True}
    else:
        if args.json or not sys.stdin.isatty():
            raise SessionError('session connect requires an interactive terminal without --json. Log in manually in the dedicated browser; automations must use the saved session.')
        cancel, confirm = Event(), Event()
        prompts = Event()

        def confirmation_reader():
            while not cancel.is_set():
                if not prompts.wait(.25):
                    continue
                prompts.clear()
                try:
                    input('Finish login in the dedicated browser, then press Enter to verify (Ctrl+C cancels): ')
                except EOFError:
                    cancel.set()
                    break
                confirm.set()

        reader = Thread(target=confirmation_reader, daemon=True)
        reader.start()
        print('Cookies grant account access and will be stored locally. Your personal browser is not read. Ctrl+C cancels.')
        try:
            session.connect(cancel, confirm, on_ready=prompts.set,
                on_progress=lambda message: print(message, file=sys.stderr),
                on_attempt_error=lambda message: (print(message, file=sys.stderr), prompts.set()))
        finally:
            cancel.set()
        payload = {'schema_version': 1, 'state': 'verified', 'verified_now': True}
    if args.json:
        emit(args, payload)
    else:
        print(payload['state'])
    return 0
