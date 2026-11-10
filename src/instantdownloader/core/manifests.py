"""Versioned, credential-free discovery manifests for automation."""
import json
import os
import tempfile
from pathlib import Path
from datetime import date, datetime

from .models import DownloadJob
from .store import canonical_url

SCHEMA_VERSION = 1


def json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f'Unsupported JSON value: {type(value).__name__}')


def write_json(payload, path):
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, default=json_value)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


def read_manifest(path, allow_partial=False):
    document = json.loads(Path(path).expanduser().read_text(encoding='utf-8'))
    if (not isinstance(document, dict) or type(document.get('schema_version')) is not int
            or document['schema_version'] != SCHEMA_VERSION):
        raise ValueError('Unsupported manifest schema_version; generate it with profile discover.')
    if type(document.get('complete')) is not bool or type(document.get('all_collected')) is not bool:
        raise ValueError('Manifest must declare complete and all_collected as booleans.')
    if not allow_partial and (not document['complete'] or not document['all_collected']):
        raise ValueError('Manifest is incomplete or limited. Review it and explicitly pass --allow-partial to use only the listed items.')
    items = document.get('items')
    if not isinstance(items, list) or len(items) > 10000:
        raise ValueError('Manifest items must be a list of at most 10000 videos.')
    jobs, titles, seen = [], [], set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('url'), str):
            raise ValueError('Each manifest item requires a video URL.')
        url = canonical_url(item['url'])
        index = item.get('playlist_index')
        if index is not None and (type(index) is not int or index < 1):
            raise ValueError('playlist_index must be a positive integer.')
        title = item.get('title', '')
        if not isinstance(title, str):
            raise ValueError('Manifest titles must be strings.')
        identity = (url, index)
        if identity not in seen:
            seen.add(identity)
            jobs.append(DownloadJob(url, playlist_index=index))
            titles.append(title)
    return document, jobs, titles
