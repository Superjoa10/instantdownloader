from pathlib import Path
from unittest.mock import MagicMock

import pytest
from openpyxl import Workbook

from instantdownloader.core import excel
from instantdownloader.core.excel import read_jobs_from_excel


def test_read_jobs_from_excel(tmp_path: Path):
    path = tmp_path / "links.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["url", "filename", "folder", "quality"])
    sheet.append(["https://example.com/video", "video-1", "folder-a", "720p"])
    workbook.save(path)

    jobs = read_jobs_from_excel(path)

    assert len(jobs) == 1
    assert jobs[0].url == "https://example.com/video"
    assert jobs[0].filename == "video-1"
    assert jobs[0].folder == "folder-a"
    assert jobs[0].quality == "720p"


def workbook_file(tmp_path, rows):
    path = tmp_path / "links.xlsx"
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    workbook.save(path)
    workbook.close()
    return path


@pytest.mark.parametrize("url", ["file:///tmp/private", "ftp://example.com/video", "example.com/video",
                                      "https://", "https://example.com:bad/video", "https://a b/video",
                                      "https://user:secret@example.com/video"])
def test_invalid_urls_have_row_errors(tmp_path, url):
    path = workbook_file(tmp_path, [["url"], [url]])
    with pytest.raises(ValueError, match="Row 2"):
        read_jobs_from_excel(path)


def test_canonical_duplicates_report_both_rows(tmp_path):
    path = workbook_file(tmp_path, [["url"], ["https://EXAMPLE.com:443/video/?b=2&a=1"],
                                   ["https://example.com/video?a=1&b=2&utm_source=share#fragment"]])
    with pytest.raises(ValueError, match="Row 3: duplicate URL/selection of row 2"):
        read_jobs_from_excel(path)


def test_multiple_errors_reported_together(tmp_path):
    path = workbook_file(tmp_path, [["url", "filename"], ["bad", "one"], [None, "two"],
                                   ["https://example.com", None]])
    with pytest.raises(ValueError) as error:
        read_jobs_from_excel(path)
    assert "Row 2" in str(error.value)
    assert "Row 3: URL is required" in str(error.value)


def test_distinct_carousel_indices_are_not_duplicates(tmp_path):
    path = workbook_file(tmp_path, [["url", "playlist_index"], ["https://example.com/list", 1],
                                   ["https://example.com/list", 2]])
    assert [job.playlist_index for job in read_jobs_from_excel(path)] == [1, 2]


@pytest.mark.parametrize("rows,sheet", [([], None), ([["url"], ["https://example.com"]], None),
                                        ([["wrong"]], None), ([["url", "URL"]], None),
                                        ([["url"], ["bad"]], None), ([["url"]], "Missing")])
def test_workbook_closed_on_all_paths(monkeypatch, rows, sheet):
    workbook = MagicMock()
    workbook.active.iter_rows.return_value = iter(rows)
    workbook.sheetnames = ["Sheet"]
    monkeypatch.setattr(excel, "load_workbook", lambda *a, **kw: workbook)
    try:
        read_jobs_from_excel("unused.xlsx", sheet)
    except ValueError:
        pass
    workbook.close.assert_called_once()
