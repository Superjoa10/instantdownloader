from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from openpyxl import load_workbook

from .models import DownloadJob


REQUIRED_COLUMNS = {"url"}
OPTIONAL_COLUMNS = {"filename", "folder", "quality", "playlist_index"}


def read_jobs_from_excel(path: str | Path, sheet: str | None = None) -> list[DownloadJob]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet and sheet not in workbook.sheetnames:
            raise ValueError(f"Excel sheet {sheet!r} does not exist.")
        worksheet = workbook[sheet] if sheet else workbook.active
        return _read_worksheet(worksheet)
    finally:
        workbook.close()


def _read_worksheet(worksheet) -> list[DownloadJob]:
    if worksheet is None:
        raise ValueError("Excel workbook has no active worksheet.")

    rows = worksheet.iter_rows(values_only=True)
    try:
        header = next(rows)
    except StopIteration:
        return []

    columns = _normalize_header(header)
    missing = REQUIRED_COLUMNS - set(columns)
    if missing:
        raise ValueError(f"Missing required Excel columns: {', '.join(sorted(missing))}")

    jobs: list[DownloadJob] = []
    seen: dict[tuple[str, int | None], int] = {}
    errors: list[str] = []
    for row_number, row in enumerate(rows, start=2):
        item = {
            column: _clean_cell(row[index] if index < len(row) else None)
            for column, index in columns.items()
        }
        url = item.get("url")
        if not url:
            if any(item.values()):
                errors.append(f"Row {row_number}: URL is required.")
            continue
        try:
            canonical = _canonical_url(url)
            index_text = item.get("playlist_index")
            playlist_index = int(index_text) if index_text else None
            if playlist_index is not None and playlist_index < 1:
                raise ValueError("playlist_index must be a positive, 1-based video index")
        except ValueError as exc:
            errors.append(f"Row {row_number}: {exc} ({url!r}).")
            continue
        key = (canonical, playlist_index)
        if key in seen:
            errors.append(f"Row {row_number}: duplicate URL/selection of row {seen[key]} ({url!r}).")
            continue
        seen[key] = row_number
        jobs.append(
            DownloadJob(
                url=url,
                filename=item.get("filename") or None,
                folder=item.get("folder") or None,
                quality=item.get("quality") or None,
                playlist_index=playlist_index,
            )
        )

    if errors:
        raise ValueError("Invalid Excel rows:\n" + "\n".join(errors))
    return jobs


def _canonical_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
        host = (parsed.hostname or "").lower()
    except ValueError as exc:
        raise ValueError("invalid HTTP(S) URL") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"} or not host
        or parsed.username is not None or parsed.password is not None
        or any(char.isspace() or ord(char) < 32 for char in url) or "\\" in url
    ):
        raise ValueError("URL must be an absolute HTTP(S) URL without credentials or whitespace")
    scheme = parsed.scheme.lower()
    if ":" in host:
        host = f"[{host}]"
    if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
        host += f":{port}"
    # Keep media selectors and signed query parameters; discard only known tracking keys.
    query = urlencode(sorted(
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"igsh", "igshid", "fbclid", "gclid"}
    ))
    return urlunsplit((scheme, host, parsed.path.rstrip("/") or "/", query, ""))


def _normalize_header(header: tuple[object, ...]) -> dict[str, int]:
    columns: dict[str, int] = {}
    allowed = REQUIRED_COLUMNS | OPTIONAL_COLUMNS

    for index, value in enumerate(header):
        if value is None:
            continue
        name = str(value).strip().lower()
        if name in allowed:
            if name in columns:
                raise ValueError(f"Row 1: duplicate Excel column {name!r}.")
            columns[name] = index

    return columns


def _clean_cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
