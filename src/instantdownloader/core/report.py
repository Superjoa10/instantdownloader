from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path

from .models import DownloadResult


REPORT_COLUMNS = [
    "url",
    "status",
    "output_path",
    "title",
    "width",
    "height",
    "error",
    "started_at",
    "finished_at",
]


def _safe_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def write_report(results: list[DownloadResult], path: str | Path) -> Path:
    report_path = Path(path).expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", newline="", encoding="utf-8", dir=report_path.parent,
            prefix=f".{report_path.name}.", suffix=".tmp", delete=False,
        ) as file:
            temporary_path = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=REPORT_COLUMNS)
            writer.writeheader()
            for result in results:
                writer.writerow({key: _safe_cell(value) for key, value in {
                    "url": result.url,
                    "status": result.status,
                    "output_path": str(result.output_path.expanduser().resolve()) if result.output_path else "",
                    "title": result.title or "",
                    "width": result.width or "",
                    "height": result.height or "",
                    "error": result.error or "",
                    "started_at": result.started_at.isoformat(timespec="seconds") if result.status != 'pending' else '',
                    "finished_at": result.finished_at.isoformat(timespec="seconds") if result.status not in ('pending', 'downloading', 'converting') else '',
                }.items()})
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, report_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return report_path
