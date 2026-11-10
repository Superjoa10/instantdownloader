import json
from unittest.mock import patch
import pytest

from instantdownloader.cli.main import main
from instantdownloader.core.models import VideoInfo


def test_cli_version(capsys):
    from instantdownloader import __version__
    with pytest.raises(SystemExit) as stopped:
        main(['--version'])
    assert stopped.value.code == 0
    assert capsys.readouterr().out.strip() == f'instantdownloader {__version__}'


def test_cli_queue_roundtrip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('INSTANTDOWNLOADER_HOME', str(tmp_path/'state'))
    assert main(['queue', 'add', 'https://instagram.com/reel/ABC/', '--output-dir', str(tmp_path/'media')]) == 0
    capsys.readouterr()
    assert main(['queue', 'list', '--json']) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]['status'] == 'pending'
    assert 'config' not in data[0]


def test_analyze_json(capsys):
    with patch('instantdownloader.cli.main.analyze_url', return_value=VideoInfo('https://example.com/a', title='Video')):
        assert main(['analyze', 'https://example.com/a', '--json']) == 0
    data = json.loads(capsys.readouterr().out)
    assert data['title'] == 'Video' and 'raw' not in data


def test_cli_errors_are_readable(capsys):
    assert main(['download', 'https://example.com/a', '--sleep', '-1']) == 2
    assert 'Error:' in capsys.readouterr().err


def test_cli_combined_profile_counts_and_enqueue(tmp_path, monkeypatch, capsys):
    from instantdownloader.core.profiles import DiscoveredVideo, DiscoveryResult
    monkeypatch.setenv('INSTANTDOWNLOADER_HOME', str(tmp_path/'state'))
    def discover(profile, **kwargs):
        assert kwargs['scope'] == 'all'
        feed = DiscoveredVideo('ABC', 'https://www.instagram.com/p/ABC/', 'Video', '', '', sources=('posts',))
        shared = DiscoveredVideo('ABC', feed.url, feed.title, '', '', sources=('posts', 'reels'))
        reel = DiscoveredVideo('DEF', 'https://www.instagram.com/p/DEF/', 'Reel', '', '', sources=('reels',))
        for item in (feed, shared, reel):
            kwargs['on_item'](item)
        return DiscoveryResult([shared, reel], True, 'Done', {'posts': 1, 'reels': 2}, {'posts': True, 'reels': True})
    with patch('instantdownloader.core.profiles.discover_profile', side_effect=discover):
        assert main(['profile', '@example', '--scope', 'all', '--enqueue']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['counts'] == {'posts': 1, 'reels': 2, 'unique': 2}
    assert result['all_collected'] and result['enqueued'] == 2
    assert main(['queue', 'list', '--json']) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2


def test_profile_browser_opt_in_and_structured_error(tmp_path, monkeypatch, capsys):
    from instantdownloader.core.profiles import DiscoveryResult
    monkeypatch.setenv('INSTANTDOWNLOADER_HOME', str(tmp_path/'state'))
    failure = DiscoveryResult([], False, 'Reels unavailable', error_code='session_unavailable', failed_source='reels')
    with patch('instantdownloader.core.profiles.discover_profile', return_value=failure) as discover:
        assert main(['profile', '@example', '--scope', 'reels', '--discovery-browser', 'chrome', '--login', 'owner']) == 2
    assert discover.call_args.kwargs['browser'] == 'chrome'
    assert discover.call_args.kwargs['login'] == 'owner'
    data = json.loads(capsys.readouterr().out)
    assert data['error_code'] == 'session_unavailable'
    assert data['failed_source'] == 'reels' and not data['all_collected']
