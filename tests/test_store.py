from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from instantdownloader.core.models import DownloadConfig, DownloadJob, DownloadResult
from instantdownloader.core.store import Store, canonical_url, decode_config, config_payload, AppLock
from instantdownloader.core.queue import export_tasks
import pytest


def test_identity_and_recovery(tmp_path):
    store = Store(tmp_path / 'queue.db')
    cfg = DownloadConfig(output_dir=tmp_path)
    first, added = store.add(DownloadJob('https://instagram.com/reel/ABC/?igsh=x'), cfg)
    second, duplicate = store.add(DownloadJob('https://www.instagram.com/p/ABC/'), cfg)
    assert added and not duplicate and first == second
    assert store.claim()['id'] == first
    assert store.claim() is None
    store.recover()
    assert store.claim()['id'] == first


def test_missing_completed_file_requeues(tmp_path):
    store = Store(tmp_path / 'queue.db')
    cfg = DownloadConfig(output_dir=tmp_path)
    job = DownloadJob('https://example.com/video')
    task_id, _ = store.add(job, cfg)
    store.finish(task_id, DownloadResult(job.url, 'success', datetime.now(), datetime.now(), tmp_path/'missing.mp4'))
    assert store.add(job, cfg)[1]
    assert store.rows()[0]['status'] == 'pending'


def test_config_roundtrip_and_preferences(tmp_path):
    cfg = DownloadConfig(output_dir=tmp_path)
    assert asdict(decode_config(config_payload(cfg))) == asdict(cfg)
    store = Store(tmp_path/'queue.db')
    store.save_preference('theme', 'Light')
    assert Store(store.path).preference('theme') == 'Light'


def test_report_includes_pending(tmp_path):
    store = Store(tmp_path/'queue.db')
    store.add(DownloadJob('https://example.com/video'), DownloadConfig(output_dir=tmp_path))
    report = export_tasks(store, tmp_path/'report.csv')
    assert 'pending' in report.read_text()


def test_lock_exclusive(tmp_path):
    lock = AppLock(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            AppLock(tmp_path)
    finally:
        lock.close()


def test_url_validation():
    with pytest.raises(ValueError):
        canonical_url('file:///etc/passwd')
