import json
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from instantdownloader.cli.main import main
from instantdownloader.core import profiles
from instantdownloader.core.models import DownloadResult, VideoInfo
from instantdownloader.core.manifests import read_manifest, write_json


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv('INSTANTDOWNLOADER_HOME', str(tmp_path / 'state'))


def video(code='ABC', date='2025-07-31'):
    return profiles.DiscoveredVideo(code, f'https://www.instagram.com/reel/{code}/', code, '', date, sources=('reels',))


def discovery(monkeypatch, items, complete=True, exhausted=True, error=None):
    def scan(profile, **kwargs):
        print('provider diagnostics')
        for item in items:
            kwargs['on_item'](item)
        return profiles.DiscoveryResult(items, complete, 'Scan finished', {'reels': len(items)},
                                        {'reels': exhausted}, error, None if complete else 'reels', {'reels': 2})
    fake = Mock(side_effect=scan)
    monkeypatch.setattr(profiles, 'discover_profile', fake)
    return fake


def session(monkeypatch):
    fake = {'username': 'owner', 'cookies': [{'name': 'sessionid', 'value': 'private-cookie'}]}
    monkeypatch.setattr('instantdownloader.cli.automation.InstagramSession.load', lambda self: fake)
    return fake


def test_filtered_discovery_manifest_and_enqueue(monkeypatch, tmp_path, capsys):
    from instantdownloader.core.store import Store
    discovery(monkeypatch, [video('OLD', '2025-07-30'), video('DAY', '2025-07-31T23:59:59Z'), video('NEW', '2025-08-01')])
    path = tmp_path / 'list.json'
    assert main(['profile', 'discover', 'example', '--scope', 'reels', '--published-before', '2025-07-31',
                 '--manifest', str(path), '--enqueue', '--json']) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert 'provider diagnostics' in output.err
    assert payload == json.loads(path.read_text())
    assert payload['schema_version'] == 1 and payload['all_collected']
    assert payload['selected_count'] == 2 and payload['enqueued'] == 2
    assert [item['id'] for item in payload['items']] == ['OLD', 'DAY']
    assert len(Store().rows()) == 2


def test_date_range_and_utc_boundary(monkeypatch, capsys):
    discovery(monkeypatch, [video('DAY', '2025-07-31T23:00:00-03:00'), video('BEFORE', '2025-07-31')])
    assert main(['profile', 'example', '--published-after', '2025-08-01', '--published-before', '2025-08-01', '--json']) == 0
    assert [item['id'] for item in json.loads(capsys.readouterr().out)['items']] == ['DAY']


def test_legacy_profile_named_discover_is_not_rewritten(monkeypatch, capsys):
    scan = discovery(monkeypatch, [video()])
    assert main(['profile', 'discover', '--scope', 'reels', '--json']) == 0
    assert scan.call_args.args == ('discover',)
    assert json.loads(capsys.readouterr().out)['profile'] == 'discover'


@pytest.mark.parametrize('options', [
    ['--published-before', '31/07/2025'],
    ['--published-before', '20250731'],
    ['--published-after', '2026-01-01', '--published-before', '2025-01-01'],
    ['--limit', '0'], ['--use-session', '--login', 'other'],
])
def test_invalid_filter_does_not_query(monkeypatch, capsys, options):
    scan = Mock()
    monkeypatch.setattr(profiles, 'discover_profile', scan)
    assert main(['profile', 'example', *options, '--json']) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload['error_code'] == 'invalid_input'
    scan.assert_not_called()


def test_saved_session_is_explicit_and_not_serialized(monkeypatch, capsys):
    local = session(monkeypatch)
    scan = discovery(monkeypatch, [video()])
    assert main(['profile', 'example', '--scope', 'reels', '--use-session', '--json']) == 0
    assert scan.call_args.kwargs['local_session'] is local
    output = capsys.readouterr().out
    assert 'private-cookie' not in output
    assert 'cookies' not in output


def test_missing_session_actionable_json(capsys):
    assert main(['profile', 'example', '--use-session', '--json']) == 4
    assert json.loads(capsys.readouterr().out)['error_code'] == 'session_required'


def test_filter_unknown_dates_without_session_is_incomplete(monkeypatch, capsys):
    from instantdownloader.core.store import Store
    discovery(monkeypatch, [video('KNOWN'), video('UNKNOWN', '')])
    assert main(['profile', 'example', '--published-before', '2025-08-01', '--enqueue', '--json']) == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload['date_missing'] == 1 and payload['selected_count'] == 1
    assert not payload['complete'] and payload['enqueue_refused']
    assert Store().rows() == []


def test_fetch_missing_dates_and_filter_after_lookup(monkeypatch, capsys):
    session(monkeypatch)
    discovery(monkeypatch, [video('OLD', ''), video('NEW', '')])
    def lookup(items, local, cancel, on_date, on_stop=None):
        on_date('OLD', '2025-07-31')
        on_date('NEW', '2025-08-02')
        return 'Done'
    monkeypatch.setattr('instantdownloader.cli.automation.load_publication_dates', lookup)
    assert main(['profile', 'example', '--use-session', '--published-before', '2025-07-31', '--json']) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['dates_complete'] and payload['date_missing'] == 0
    assert [item['id'] for item in payload['items']] == ['OLD']


def test_date_lookup_rate_limit_stops_and_refuses_enqueue(monkeypatch, capsys):
    session(monkeypatch)
    discovery(monkeypatch, [video('UNKNOWN', '')])
    def lookup(items, local, cancel, on_date, on_stop=None):
        on_stop('rate_limited')
        return 'Stopped'
    monkeypatch.setattr('instantdownloader.cli.automation.load_publication_dates', lookup)
    assert main(['profile', 'example', '--use-session', '--fetch-dates', '--enqueue', '--json']) == 5
    payload = json.loads(capsys.readouterr().out)
    assert payload['error_code'] == 'rate_limited' and payload['enqueue_refused']


def test_discovery_error_does_not_issue_date_requests(monkeypatch, capsys):
    session(monkeypatch)
    discovery(monkeypatch, [video('UNKNOWN', '')], complete=False, exhausted=False, error='rate_limited')
    dates = Mock()
    monkeypatch.setattr('instantdownloader.cli.automation.load_publication_dates', dates)
    assert main(['profile', 'example', '--use-session', '--fetch-dates', '--json']) == 5
    dates.assert_not_called()
    assert json.loads(capsys.readouterr().out)['items']


def test_limited_manifest_requires_allow_partial(monkeypatch, tmp_path, capsys):
    from instantdownloader.core.store import Store
    discovery(monkeypatch, [video()], exhausted=False)
    path = tmp_path / 'limited.json'
    assert main(['profile', 'example', '--manifest', str(path), '--json']) == 0
    capsys.readouterr()
    assert main(['download-list', str(path), '--enqueue', '--json']) == 2
    assert Store().rows() == []
    assert 'allow-partial' in json.loads(capsys.readouterr().out)['message']
    assert main(['download-list', str(path), '--enqueue', '--allow-partial', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['enqueued'] == 1


def test_limited_discovery_refuses_enqueue_unless_explicit(monkeypatch, capsys):
    from instantdownloader.core.store import Store
    discovery(monkeypatch, [video()], exhausted=False)
    assert main(['profile', 'example', '--enqueue', '--json']) == 3
    assert json.loads(capsys.readouterr().out)['enqueue_refused']
    assert not Store().rows()
    assert main(['profile', 'example', '--enqueue', '--allow-partial', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['enqueued'] == 1


def test_interrupt_preserves_incomplete_checkpoint(monkeypatch, tmp_path, capsys):
    def scan(profile, **kwargs):
        kwargs['on_item'](video())
        raise KeyboardInterrupt()
    monkeypatch.setattr(profiles, 'discover_profile', scan)
    path = tmp_path / 'partial.json'
    assert main(['profile', 'example', '--manifest', str(path), '--json']) == 130
    assert json.loads(capsys.readouterr().out)['error_code'] == 'cancelled'
    saved = json.loads(path.read_text())
    assert not saved['complete'] and len(saved['items']) == 1


@pytest.mark.parametrize('change', [
    {'schema_version': 99}, {'complete': 'true'}, {'items': [{'url': 'file:///private/file'}]},
    {'items': [{'url': 'https://example.com/a', 'playlist_index': True}]},
])
def test_bad_manifest_rejected_before_download(tmp_path, monkeypatch, capsys, change):
    path = tmp_path / 'bad.json'
    write_json({'schema_version': 1, 'complete': True, 'all_collected': True, 'items': [], **change}, path)
    batch = Mock()
    monkeypatch.setattr('instantdownloader.cli.main.download_batch', batch)
    assert main(['download-list', str(path), '--json']) == 2
    assert json.loads(capsys.readouterr().out)['error_code'] == 'invalid_input'
    batch.assert_not_called()


def test_manifest_deduplicates_video_identity_and_ignores_config(tmp_path):
    path = tmp_path / 'list.json'
    write_json({'schema_version': 1, 'complete': True, 'all_collected': True, 'items': [
        {'url': 'https://www.instagram.com/reel/ABC/', 'config': {'cookies': 'secret'}},
        {'url': 'https://www.instagram.com/p/ABC/'},
        {'url': 'https://www.instagram.com/p/ABC/', 'playlist_index': 2},
    ]}, path)
    _, jobs, _ = read_manifest(path)
    assert len(jobs) == 2
    assert all(not hasattr(job, 'config') for job in jobs)


def test_manifest_download_json_and_report(monkeypatch, tmp_path, capsys):
    path, report = tmp_path / 'list.json', tmp_path / 'report.json'
    write_json({'schema_version': 1, 'complete': True, 'all_collected': True,
                'items': [{'url': 'https://example.com/video'}]}, path)
    now = datetime.now()
    def batch(jobs, config, concurrency, callback, **kwargs):
        print('download progress')
        result = DownloadResult(jobs[0].url, 'success', now, now, tmp_path/'out.mp4')
        callback(result)
        return [result]
    monkeypatch.setattr('instantdownloader.cli.main.download_batch', batch)
    assert main(['download-list', str(path), '--json', '--json-report', str(report), '--report', str(tmp_path/'report.csv')]) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload == json.loads(report.read_text())
    assert payload['results'][0]['started_at'] == now.isoformat()
    assert 'download progress' in output.err


def test_session_status_does_not_load_cookies(monkeypatch, capsys):
    load = Mock(side_effect=AssertionError('Do not read credentials for status'))
    monkeypatch.setattr('instantdownloader.cli.automation.InstagramSession.load', load)
    assert main(['session', 'status', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['state'] == 'absent'
    load.assert_not_called()


def test_session_delete_requires_explicit_confirmation(capsys):
    assert main(['session', 'delete', '--json']) == 2
    assert json.loads(capsys.readouterr().out)['error_code'] == 'invalid_input'
    assert main(['session', 'delete', '--yes', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['deleted']


def test_session_connect_never_prompts_automation(monkeypatch, capsys):
    connect = Mock()
    monkeypatch.setattr('instantdownloader.cli.automation.InstagramSession.connect', connect)
    assert main(['session', 'connect', '--json']) == 4
    assert 'interactive' in json.loads(capsys.readouterr().out)['message']
    connect.assert_not_called()


def test_queue_add_json_and_retry_failed_only(tmp_path, capsys):
    from instantdownloader.core.store import Store
    assert main(['queue', 'add', 'https://example.com/a', '--json']) == 0
    identifier = json.loads(capsys.readouterr().out)['id']
    store = Store()
    now = datetime.now()
    store.finish(identifier, DownloadResult('https://example.com/a', 'error', now, now, error='DNS'))
    assert main(['queue', 'retry', '--failed', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['retried_ids'] == [identifier]
    assert store.rows()[0]['status'] == 'pending'


def test_queue_run_only_reports_this_execution(monkeypatch, tmp_path, capsys):
    from instantdownloader.core.store import Store
    assert main(['queue', 'add', 'https://example.com/a', '--sleep', '0', '--max-sleep', '0', '--json']) == 0
    capsys.readouterr()
    def download(job, config, **kwargs):
        print('provider progress')
        now = datetime.now()
        return DownloadResult(job.url, 'success', now, now, tmp_path / 'out.mp4')
    monkeypatch.setattr('instantdownloader.core.queue.download_job', download)
    assert main(['queue', 'run', '--json']) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload['counts'] == {'success': 1}
    assert payload['pending'] == 0 and Path(payload['report']).is_file()
    assert 'provider progress' in output.err
    assert Store().rows()[0]['status'] == 'success'


def test_analyze_provider_stdout_does_not_break_json(monkeypatch, capsys):
    def analyze(*args):
        print('extractor diagnostic')
        return VideoInfo('https://example.com/a', title='Test')
    monkeypatch.setattr('instantdownloader.cli.main.analyze_url', analyze)
    assert main(['analyze', 'https://example.com/a', '--json']) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)['title'] == 'Test'
    assert 'extractor diagnostic' in output.err


def test_filter_saved_manifest_without_network(tmp_path, monkeypatch, capsys):
    source, target = tmp_path/'list.json', tmp_path/'filtered.json'
    write_json({'schema_version': 1, 'complete': True, 'all_collected': True,
                'items': [vars_item for vars_item in (
                    {'url': video('A').url, 'date': '2025-07-31'},
                    {'url': video('B').url, 'date': '2025-08-01'},
                )]}, source)
    original = source.read_bytes()
    lookup = Mock(side_effect=AssertionError('No lookup needed'))
    monkeypatch.setattr('instantdownloader.cli.automation.load_publication_dates', lookup)
    assert main(['filter-list', str(source), '--published-before', '2025-07-31', '--output', str(target), '--json']) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['selected_count'] == 1 and payload['input_count'] == 2
    assert source.read_bytes() == original
    lookup.assert_not_called()


def test_filter_does_not_claim_incomplete_source_is_complete(tmp_path, capsys):
    source, target = tmp_path/'list.json', tmp_path/'filtered.json'
    write_json({'schema_version': 1, 'complete': False, 'all_collected': False,
                'error_code': 'discovery_incomplete',
                'items': [{'url': video().url, 'date': '2025-07-31'}]}, source)
    assert main(['filter-list', str(source), '--published-before', '2025-07-31', '--output', str(target), '--json']) == 3
    assert not json.loads(capsys.readouterr().out)['complete']


def test_report_cannot_overwrite_source(tmp_path, monkeypatch, capsys):
    source = tmp_path/'list.json'
    write_json({'schema_version': 1, 'complete': True, 'all_collected': True, 'items': []}, source)
    original = source.read_bytes()
    assert main(['download-list', str(source), '--report', str(source), '--json']) == 2
    assert 'overwrite' in json.loads(capsys.readouterr().out)['message']
    assert source.read_bytes() == original


def test_download_url_json_failure_and_report(monkeypatch, tmp_path, capsys):
    now = datetime.now()
    def download(job, config):
        print('progress on stderr')
        return DownloadResult(job.url, 'rate_limited', now, now, error='HTTP 429')
    monkeypatch.setattr('instantdownloader.cli.main.download_job', download)
    path = tmp_path/'one.json'
    assert main(['download', 'https://example.com/a', '--report', str(tmp_path/'one.csv'), '--json-report', str(path), '--json']) == 5
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert not payload['complete'] and payload['counts'] == {'rate_limited': 1}
    assert json.loads(path.read_text()) == payload
    assert 'progress on stderr' in output.err


def test_excel_json_end_to_end_without_network(monkeypatch, tmp_path, capsys):
    import openpyxl
    source = tmp_path/'links.xlsx'
    book = openpyxl.Workbook()
    book.active.append(['url'])
    book.active.append(['https://example.com/a'])
    book.save(source)
    def download(job, config, **kwargs):
        now = datetime.now()
        return DownloadResult(job.url, 'success', now, now, tmp_path/'a.mp4')
    monkeypatch.setattr('instantdownloader.core.batch.download_job', download)
    assert main(['batch', str(source), '--json', '--report', str(tmp_path/'excel.csv')]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['counts'] == {'success': 1} and len(payload['results']) == 1


def test_queue_run_rate_limit_leaves_others_pending(monkeypatch, tmp_path, capsys):
    assert main(['queue', 'add', 'https://example.com/a', '--json']) == 0
    capsys.readouterr()
    assert main(['queue', 'add', 'https://example.com/b', '--json']) == 0
    capsys.readouterr()
    def download(job, config, **kwargs):
        now = datetime.now()
        return DownloadResult(job.url, 'rate_limited', now, now, error='HTTP 429')
    monkeypatch.setattr('instantdownloader.core.queue.download_job', download)
    assert main(['queue', 'run', '--json']) == 5
    payload = json.loads(capsys.readouterr().out)
    assert payload['pending'] == 1 and payload['counts'] == {'rate_limited': 1}
    assert main(['queue', 'retry', '--failed', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['retried_ids'] == []


def test_interrupted_download_list_preserves_results(monkeypatch, tmp_path, capsys):
    source, report = tmp_path/'list.json', tmp_path/'result.json'
    write_json({'schema_version': 1, 'complete': True, 'all_collected': True, 'items': [{'url': video().url}]}, source)
    monkeypatch.setattr('instantdownloader.cli.main.download_batch', Mock(side_effect=KeyboardInterrupt()))
    assert main(['download-list', str(source), '--json', '--report', str(tmp_path/'result.csv'), '--json-report', str(report)]) == 130
    payload = json.loads(capsys.readouterr().out)
    assert payload['error_code'] == 'cancelled'
    assert payload == json.loads(report.read_text())


def test_real_cli_process_filter_enqueue_and_list(tmp_path):
    import subprocess
    import sys
    source, target = tmp_path/'list.json', tmp_path/'filtered.json'
    write_json({'schema_version': 1, 'complete': True, 'all_collected': True, 'items': [
        {'id': 'ABC', 'url': video().url, 'date': '2025-07-31'},
        {'id': 'DEF', 'url': video('DEF').url, 'date': '2025-08-01'},
    ]}, source)
    def command(*args):
        result = subprocess.run([sys.executable, '-m', 'instantdownloader.cli.main', *args],
                                capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    payload = command('filter-list', str(source), '--published-before', '2025-07-31',
                      '--output', str(target), '--json')
    assert payload['selected_count'] == 1
    assert command('download-list', str(target), '--enqueue', '--output-dir', str(tmp_path/'media'), '--json')['enqueued'] == 1
    rows = command('queue', 'list', '--json')
    assert len(rows) == 1 and rows[0]['status'] == 'pending'
    assert command('session', 'status', '--json')['state'] == 'absent'
