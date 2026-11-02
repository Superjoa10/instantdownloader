from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from instantdownloader.core.models import DownloadConfig, DownloadJob, DownloadResult
from instantdownloader.core.queue import QueueRunner
from instantdownloader.core.store import Store


def test_runner_persists_each_result_and_stops_on_rate_limit(tmp_path):
    store = Store(tmp_path/'queue.db')
    config = DownloadConfig(output_dir=tmp_path, sleep_interval=0, max_sleep_interval=0)
    for i in range(3):
        store.add(DownloadJob(f'https://example.com/{i}'), config)
    events = []
    runner = QueueRunner(store, lambda *args: events.append(args))
    def blocked(job, *args, **kwargs):
        return DownloadResult(job.url, 'rate_limited', datetime.now(), datetime.now(), error='HTTP 429')
    with patch('instantdownloader.core.queue.download_job', side_effect=blocked) as download:
        runner.start(tmp_path/'reports')
        runner.thread.join(5)
    assert not runner.running
    assert download.call_count == 1
    assert sorted(r['status'] for r in store.rows()) == ['pending', 'pending', 'rate_limited']
    report = next((tmp_path/'reports').glob('*.csv'))
    assert report.read_text().count('pending') == 2
    assert len(runner.processed_ids) == 1


def test_runner_reports_export_failure_without_losing_tasks(tmp_path):
    store = Store(tmp_path/'queue.db')
    store.add(DownloadJob('https://example.com/a'), DownloadConfig(output_dir=tmp_path))
    events = []
    runner = QueueRunner(store, lambda *args: events.append(args))
    with patch('instantdownloader.core.queue.export_tasks', side_effect=OSError('disk full')):
        runner.start(tmp_path/'reports')
        runner.thread.join(5)
    assert runner.error == 'disk full'
    assert store.rows()[0]['status'] == 'pending'
    assert any(e[0] == 'error' for e in events)
