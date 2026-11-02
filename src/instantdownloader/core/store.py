from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .models import DownloadConfig, DownloadJob, DownloadResult


def data_dir() -> Path:
    override = os.environ.get("INSTANTDOWNLOADER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/InstantDownloader"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "InstantDownloader"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "instantdownloader"


def canonical_url(url: str) -> str:
    if any(char.isspace() or ord(char) < 32 for char in url.strip()) or '\\' in url:
        raise ValueError('A URL nao pode conter espacos ou caracteres de controle.')
    parts = urlsplit(url.strip())
    _ = parts.port
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None or parts.password is not None:
        raise ValueError("Informe uma URL HTTP ou HTTPS valida.")
    host = parts.hostname.lower()
    if host in ("instagram.com", "www.instagram.com"):
        segments = parts.path.strip("/").split("/")
        if len(segments) == 2 and segments[0] in ("p", "reel", "reels", "tv") and not parts.query:
            return f"https://www.instagram.com/p/{segments[1]}/"
        if len(segments) == 2 and segments[0] in ("p", "reel", "reels", "tv"):
            from urllib.parse import parse_qsl, urlencode
            query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                               if k not in ('igsh', 'igshid', 'fbclid') and not k.startswith('utm_')])
            return f"https://www.instagram.com/p/{segments[1]}/" + (f'?{query}' if query else '')
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, parts.query, ""))


def config_payload(config: DownloadConfig) -> str:
    return json.dumps(asdict(config), default=str)


def decode_config(payload: str) -> DownloadConfig:
    values = json.loads(payload)
    for field in ("output_dir", "ffmpeg_location", "download_archive", "cookiefile"):
        if values.get(field):
            values[field] = Path(values[field])
    return DownloadConfig(**values)


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "queue.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY,
                    identity TEXT NOT NULL UNIQUE,
                    job TEXT NOT NULL,
                    config TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    output_path TEXT,
                    error TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS discoveries (
                    profile TEXT NOT NULL,
                    id TEXT NOT NULL,
                    item TEXT NOT NULL,
                    PRIMARY KEY(profile, id)
                );
            """)
            db.execute('BEGIN IMMEDIATE')
            columns = {row[1] for row in db.execute('PRAGMA table_info(tasks)')}
            for column in ('width', 'height'):
                if column not in columns:
                    db.execute(f'ALTER TABLE tasks ADD COLUMN {column} INTEGER')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, job: DownloadJob, config: DownloadConfig, title: str = "") -> tuple[int, bool]:
        if job.playlist_index is not None and (type(job.playlist_index) is not int or job.playlist_index < 1):
            raise ValueError('O indice do video deve ser um inteiro a partir de 1.')
        values = asdict(job)
        values["url"] = canonical_url(job.url)
        payload = config_payload(config)
        # Quality and destination are part of the identity: different exports are intentional.
        identity_fields = [values['url'], values.get('filename'), values.get('folder'),
                           values.get('playlist_index'), values.get('quality') or config.quality,
                           str(config.output_dir.expanduser().resolve()), config.compatible_mp4]
        identity = hashlib.sha256(json.dumps(identity_fields, sort_keys=True).encode()).hexdigest()
        with self.connect() as db:
            cur = db.execute(
                "INSERT OR IGNORE INTO tasks(identity,job,config,title,created_at) VALUES(?,?,?,?,?)",
                (identity, json.dumps(values), payload, title or values["url"], datetime.now().isoformat()),
            )
            added = cur.rowcount > 0
            row = db.execute("SELECT id,status,output_path FROM tasks WHERE identity=?", (identity,)).fetchone()
            if not added and row["status"] in ("success", "skipped") and not Path(row["output_path"] or "").is_file():
                db.execute("UPDATE tasks SET status='pending',error=NULL WHERE id=?", (row["id"],))
                added = True
            return row["id"], added

    def rows(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM tasks ORDER BY id DESC")]

    def claim(self) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tasks WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE tasks SET status='downloading',started_at=?,error=NULL WHERE id=?", (datetime.now().isoformat(), row["id"]))
                return dict(row)
        return None

    def finish(self, task_id: int, result: DownloadResult):
        with self.connect() as db:
            db.execute(
                "UPDATE tasks SET status=?,output_path=?,error=?,started_at=?,finished_at=?,title=COALESCE(?,title),width=?,height=? WHERE id=?",
                (result.status, str(result.output_path.resolve()) if result.output_path else None, result.error,
                 result.started_at.isoformat(), result.finished_at.isoformat(), result.title, result.width, result.height, task_id),
            )

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE tasks SET status='pending',error='Execucao interrompida; pronta para retomar.' WHERE status IN ('downloading','converting')")

    def retry(self, task_id: int):
        with self.connect() as db:
            db.execute("UPDATE tasks SET status='pending',error=NULL,started_at=NULL,finished_at=NULL,output_path=NULL WHERE id=? AND status NOT IN ('downloading','converting','pending')", (task_id,))

    def cancel(self, task_id: int):
        with self.connect() as db:
            db.execute("UPDATE tasks SET status='cancelled' WHERE id=? AND status='pending'", (task_id,))

    def converting(self, task_id: int):
        with self.connect() as db:
            db.execute("UPDATE tasks SET status='converting' WHERE id=? AND status='downloading'", (task_id,))

    def preference(self, key: str, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM preferences WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def save_preference(self, key: str, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO preferences VALUES(?,?)", (key, json.dumps(value)))

    def save_discovery(self, profile: str, item: dict):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO discoveries VALUES(?,?,?)", (profile, item["id"], json.dumps(item)))

    def discoveries(self, profile: str) -> list[dict]:
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT item FROM discoveries WHERE profile=? ORDER BY rowid", (profile,))]


class AppLock:
    """OS lock released even on a crash; protects recovery from another running instance."""
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.file = (directory / "desktop.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                self.file.write(b"0")
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RuntimeError("O InstantDownloader ja esta aberto. Feche a outra janela primeiro.") from exc

    def close(self):
        self.file.close()
