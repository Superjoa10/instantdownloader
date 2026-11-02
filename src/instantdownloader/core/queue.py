from __future__ import annotations

import json
import random
import threading
import uuid
from datetime import datetime
from pathlib import Path

from .downloader import download_job
from .models import DownloadJob, DownloadResult
from .report import write_report
from .store import Store, decode_config


def export_tasks(store: Store, path: Path, task_ids: set[int] | None = None) -> Path:
    results = []
    for row in reversed(store.rows()):
        if task_ids is not None and row['id'] not in task_ids:
            continue
        results.append(DownloadResult(
            url=json.loads(row['job'])['url'], status=row['status'], title=row['title'],
            output_path=Path(row['output_path']) if row['output_path'] else None,
            error=row['error'],
            width=row.get('width'), height=row.get('height'),
            started_at=datetime.fromisoformat(row['started_at'] or row['created_at']),
            finished_at=datetime.fromisoformat(row['finished_at'] or row['created_at']),
        ))
    return write_report(results, path)


class QueueRunner:
    def __init__(self, store: Store, emit):
        self.store = store
        self.emit = emit
        self.cancel_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.error: str | None = None
        self.processed_ids: set[int] = set()

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, report_dir: Path):
        if self.running:
            return
        self.cancel_event.clear()
        self.error = None
        self.processed_ids.clear()
        self.thread = threading.Thread(target=self._run, args=(report_dir,), daemon=False)
        self.thread.start()

    def stop(self):
        self.cancel_event.set()

    def _run(self, report_dir):
        report = report_dir / f"download-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.csv"
        ids = {r['id'] for r in self.store.rows() if r['status'] == 'pending'}
        active = None
        try:
            export_tasks(self.store, report, ids)
            while not self.cancel_event.is_set():
                row = self.store.claim()
                if row is None:
                    break
                active = row
                ids.add(row['id'])
                config = decode_config(row['config'])
                job = DownloadJob(**json.loads(row['job']))
                self.emit('queue_status', f"Baixando: {row['title']}")
                self.emit('refresh', None)

                def progress(data):
                    if data.get('status') == 'converting':
                        self.store.converting(row['id'])
                        self.emit('refresh', None)
                    total = data.get('total_bytes') or data.get('total_bytes_estimate')
                    fraction = data.get('downloaded_bytes', 0) / total if total else None
                    self.emit('transfer', {'id': row['id'], 'fraction': fraction, 'status': data.get('status', 'downloading')})

                try:
                    result = download_job(job, config, progress_callback=progress, cancel_event=self.cancel_event)
                except Exception as exc:
                    result = DownloadResult(job.url, 'error', datetime.now(), datetime.now(), error=str(exc))
                self.store.finish(row['id'], result)
                active = None
                self.processed_ids.add(row['id'])
                export_tasks(self.store, report, ids)
                self.emit('refresh', None)
                if result.status == 'rate_limited':
                    self.emit('queue_status', 'Fila pausada: limite do Instagram. Aguarde antes de retomar.')
                    break
                if not any(r['status'] == 'pending' for r in self.store.rows()):
                    break
                if self.cancel_event.wait(max(0, random.uniform(config.sleep_interval, config.max_sleep_interval))):
                    break
            self.emit('report', str(report.resolve()))
            if self.cancel_event.is_set():
                self.emit('queue_status', f'Fila parada. Pendentes preservados. Relatorio: {report.resolve()}')
            elif any(r['status'] == 'rate_limited' for r in self.store.rows() if r['id'] in self.processed_ids):
                self.emit('queue_status', f'Fila pausada por limite de acesso. Relatorio: {report.resolve()}')
        except Exception as exc:
            self.error = str(exc)
            if active is not None:
                now = datetime.now()
                self.store.finish(active['id'], DownloadResult(active['title'], 'error', now, now, error=str(exc)))
            self.emit('error', f"Fila interrompida. Os resultados permanecem salvos. {exc}")
        finally:
            self.emit('queue_done', None)
