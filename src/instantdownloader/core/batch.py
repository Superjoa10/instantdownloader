from __future__ import annotations

import random
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from threading import Event
from typing import Callable

from .downloader import download_job
from .models import DownloadConfig, DownloadJob, DownloadResult

ResultCallback = Callable[[DownloadResult], None]


def download_batch(
    jobs: list[DownloadJob],
    config: DownloadConfig,
    concurrency: int = 1,
    result_callback: ResultCallback | None = None,
    cancel_event: Event | None = None,
) -> list[DownloadResult]:
    """Return one result per job in input order, including unstarted jobs.

    Archive-enabled batches are sequential so each job sees prior archive writes.
    Other batches have at most two active downloads and no speculative backlog.
    """
    concurrency = max(1, min(concurrency, 2))
    if config.download_archive:
        concurrency = 1

    if concurrency == 1:
        return _download_sequential(jobs, config, result_callback, cancel_event)

    return _download_parallel(jobs, config, concurrency, result_callback, cancel_event)


def _unstarted(job: DownloadJob, status: str) -> DownloadResult:
    now = datetime.now()
    return DownloadResult(
        url=job.url, status=status, started_at=now, finished_at=now,
        error="Cancelled before starting." if status == "cancelled"
        else "Not started because another job was rate limited. Retry later.",
    )


def _download_sequential(
    jobs: list[DownloadJob],
    config: DownloadConfig,
    result_callback: ResultCallback | None,
    cancel_event: Event | None,
) -> list[DownloadResult]:
    results: list[DownloadResult] = []
    rate_limited = False

    for index, job in enumerate(jobs):
        if cancel_event is not None and cancel_event.is_set():
            result = _unstarted(job, "cancelled")
        elif rate_limited:
            result = _unstarted(job, "pending")
        else:
            result = download_job(job, config, cancel_event=cancel_event)
        results.append(result)

        if result_callback:
            result_callback(result)

        if config.stop_on_rate_limit and result.status == "rate_limited":
            rate_limited = True

        if index < len(jobs) - 1 and not rate_limited and result.status != "cancelled":
            delay = max(0, random.uniform(config.sleep_interval, config.max_sleep_interval))
            if cancel_event is not None:
                cancel_event.wait(delay)
            else:
                time.sleep(delay)

    return results


def _download_parallel(
    jobs: list[DownloadJob],
    config: DownloadConfig,
    concurrency: int,
    result_callback: ResultCallback | None,
    cancel_event: Event | None,
) -> list[DownloadResult]:
    results: dict[int, DownloadResult] = {}
    next_index = 0
    rate_limited = False
    rate_limit_seen = Event()

    def run(job: DownloadJob) -> DownloadResult:
        result = download_job(job, config, cancel_event=cancel_event)
        if config.stop_on_rate_limit and result.status == "rate_limited":
            rate_limit_seen.set()
        return result

    def record(index: int, result: DownloadResult) -> None:
        results[index] = result
        if result_callback:
            result_callback(result)

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {}
        while futures or next_index < len(jobs):
            cancelled = cancel_event is not None and cancel_event.is_set()
            if cancelled or rate_limited or rate_limit_seen.is_set():
                for index in range(next_index, len(jobs)):
                    record(index, _unstarted(jobs[index], "cancelled" if cancelled else "pending"))
                next_index = len(jobs)
            else:
                while next_index < len(jobs) and len(futures) < concurrency:
                    if rate_limit_seen.is_set() or (cancel_event is not None and cancel_event.is_set()):
                        break
                    future = executor.submit(run, jobs[next_index])
                    futures[future] = next_index
                    next_index += 1
            if not futures:
                continue
            completed, _ = wait(futures, return_when=FIRST_COMPLETED)
            # Observe every completed result before replenishing the bounded window.
            completed |= {future for future in futures if future.done()}
            for future in completed:
                index = futures.pop(future)
                result = future.result()
                if config.stop_on_rate_limit and result.status == "rate_limited":
                    rate_limited = True
                record(index, result)

    return [results[index] for index in range(len(jobs))]
