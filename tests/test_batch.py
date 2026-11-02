from datetime import datetime
from threading import Event, Lock
from unittest.mock import MagicMock

import pytest

from instantdownloader.core import batch
from instantdownloader.core.models import DownloadConfig, DownloadJob, DownloadResult


def result(job, status="success"):
    now = datetime.now()
    return DownloadResult(job.url, status, now, now)


@pytest.mark.parametrize("concurrency", [1, 2, 99])
def test_rate_limit_stops_scheduling_and_reports_all_jobs(tmp_path, monkeypatch, concurrency):
    jobs = [DownloadJob(str(index)) for index in range(6)]
    started = []
    observed = Event()
    callback = MagicMock(side_effect=lambda item: observed.set() if item.status == "rate_limited" else None)

    def download(job, config, cancel_event=None):
        started.append(job.url)
        if job.url == "0":
            return result(job, "rate_limited")
        assert observed.wait(3)
        return result(job)

    monkeypatch.setattr(batch, "download_job", download)
    config = DownloadConfig(output_dir=tmp_path, sleep_interval=0, max_sleep_interval=0)
    results = batch.download_batch(jobs, config, concurrency, callback)
    assert set(started) <= {"0", "1"}
    assert [item.url for item in results] == [job.url for job in jobs]
    assert results[0].status == "rate_limited"
    assert all(item.status == "pending" for item in results[2:])
    assert callback.call_count == len(jobs)


@pytest.mark.parametrize("concurrency", [1, 2])
def test_precancelled_batch_reports_every_job(tmp_path, monkeypatch, concurrency):
    event = Event()
    event.set()
    download = MagicMock()
    monkeypatch.setattr(batch, "download_job", download)
    jobs = [DownloadJob(str(index)) for index in range(3)]
    results = batch.download_batch(jobs, DownloadConfig(output_dir=tmp_path), concurrency, cancel_event=event)
    assert [item.status for item in results] == ["cancelled"] * 3
    download.assert_not_called()


def test_archive_batches_are_sequential(tmp_path, monkeypatch):
    executor = MagicMock(side_effect=AssertionError("Archive jobs must not use the thread pool"))
    monkeypatch.setattr(batch, "ThreadPoolExecutor", executor)
    monkeypatch.setattr(batch, "download_job", lambda job, config, **kw: result(job))
    config = DownloadConfig(output_dir=tmp_path, download_archive=tmp_path / "archive.txt",
                            sleep_interval=0, max_sleep_interval=0)
    jobs = [DownloadJob(str(index)) for index in range(3)]
    assert len(batch.download_batch(jobs, config, concurrency=2)) == 3
    executor.assert_not_called()


@pytest.mark.parametrize("concurrency", [1, 2])
def test_can_explicitly_continue_after_rate_limit(tmp_path, monkeypatch, concurrency):
    monkeypatch.setattr(batch, "download_job", lambda job, config, **kw: result(job, "rate_limited"))
    config = DownloadConfig(output_dir=tmp_path, stop_on_rate_limit=False, sleep_interval=0, max_sleep_interval=0)
    results = batch.download_batch([DownloadJob(str(i)) for i in range(4)], config, concurrency)
    assert [item.status for item in results] == ["rate_limited"] * 4


def test_cancel_interrupts_sequential_delay(tmp_path, monkeypatch):
    event = Event()
    config = DownloadConfig(output_dir=tmp_path, sleep_interval=100, max_sleep_interval=100)

    def download(job, config, cancel_event=None):
        assert cancel_event is event
        event.set()
        return result(job)

    download_mock = MagicMock(side_effect=download)
    monkeypatch.setattr(batch, "download_job", download_mock)
    results = batch.download_batch([DownloadJob("first"), DownloadJob("second")], config, cancel_event=event)
    assert [item.status for item in results] == ["success", "cancelled"]
    assert download_mock.call_count == 1


def test_parallel_cancellation_does_not_start_backlog(tmp_path, monkeypatch):
    event = Event()
    config = DownloadConfig(output_dir=tmp_path)
    started = []
    lock = Lock()

    def download(job, config, cancel_event=None):
        with lock:
            started.append(job.url)
        assert cancel_event is event
        event.set()
        return result(job, "cancelled")

    monkeypatch.setattr(batch, "download_job", download)
    results = batch.download_batch([DownloadJob(str(i)) for i in range(5)], config, 2, cancel_event=event)
    assert len(started) <= 2
    assert [item.status for item in results] == ["cancelled"] * 5
