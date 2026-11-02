import json
from unittest.mock import patch

from instantdownloader.cli.main import main
from instantdownloader.core.models import VideoInfo


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
