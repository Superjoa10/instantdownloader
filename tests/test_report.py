import csv
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from instantdownloader.core import report
from instantdownloader.core.models import DownloadResult


def test_report_and_output_paths_are_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    now = datetime.now()
    results = [DownloadResult("https://example.com/video", "success", now, now,
                              output_path=Path("downloads/video.mp4"), title="A, title\nwith newline"),
               DownloadResult("https://example.com/skipped", "skipped", now, now)]
    path = report.write_report(results, "reports/result.csv")
    assert path == tmp_path / "reports/result.csv"
    with path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    assert rows[0]["output_path"] == str(tmp_path / "downloads/video.mp4")
    assert rows[0]["title"] == results[0].title
    assert rows[1]["status"] == "skipped"
    assert rows[1]["output_path"] == ""


@pytest.mark.parametrize("failure", ["write", "replace"])
def test_report_failure_preserves_existing_report_and_cleans_temp(tmp_path, monkeypatch, failure):
    path = tmp_path / "report.csv"
    path.write_text("previous report")
    if failure == "write":
        monkeypatch.setattr(report.csv, "DictWriter", MagicMock(side_effect=OSError("write failed")))
    else:
        monkeypatch.setattr(report.os, "replace", MagicMock(side_effect=OSError("replace failed")))
    with pytest.raises(OSError):
        report.write_report([], path)
    assert path.read_text() == "previous report"
    assert list(tmp_path.iterdir()) == [path]


def test_empty_report_still_has_header(tmp_path):
    path = report.write_report([], tmp_path / "empty.csv")
    with path.open(newline="", encoding="utf-8") as file:
        assert next(csv.reader(file)) == report.REPORT_COLUMNS


def test_external_metadata_cannot_become_excel_formula(tmp_path):
    now = datetime.now()
    path = report.write_report([DownloadResult('https://example.com/a', 'success', now, now, title='=HYPERLINK("bad")')], tmp_path/'safe.csv')
    with path.open(newline='') as stream:
        assert next(csv.DictReader(stream))['title'].startswith("'=")
