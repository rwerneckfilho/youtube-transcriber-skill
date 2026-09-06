"""Local playlist subscriptions with durable discovery and atomic queue handoff."""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from .jobs import Pipeline, YOUTUBE_ID, validate_url

LOG = logging.getLogger(__name__)
SCHEMA = """
CREATE TABLE IF NOT EXISTS watch_sources (
 id TEXT PRIMARY KEY,url TEXT NOT NULL UNIQUE,name TEXT NOT NULL DEFAULT '',title TEXT NOT NULL DEFAULT '',
 language TEXT NOT NULL,interval_minutes INTEGER NOT NULL,initial_mode TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1,initialized INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'idle',
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,next_check TEXT NOT NULL,
 last_checked TEXT,last_success TEXT,last_error TEXT,error_code TEXT,failures INTEGER NOT NULL DEFAULT 0,
 last_new INTEGER NOT NULL DEFAULT 0,last_unavailable INTEGER NOT NULL DEFAULT 0,last_job_id TEXT
);
CREATE TABLE IF NOT EXISTS watch_seen (
 source_id TEXT NOT NULL REFERENCES watch_sources(id) ON DELETE CASCADE,video_id TEXT NOT NULL,
 title TEXT NOT NULL,detected_at TEXT NOT NULL,state TEXT NOT NULL,job_id TEXT,
 PRIMARY KEY(source_id,video_id)
);
CREATE INDEX IF NOT EXISTS watch_due ON watch_sources(enabled,next_check);
CREATE INDEX IF NOT EXISTS watch_pending ON watch_seen(source_id,state,detected_at);
"""


class WatchError(Exception):
    def __init__(self, code, message, status=422):
        self.code, self.status = code, status
        super().__init__(message)


class WatchInterrupted(Exception):
    pass


def utcnow():
    return datetime.now(timezone.utc)


class PlaylistScanner:
    def __init__(self, stop):
        self.stop = stop

    def scan(self, url):
        # Validate again at the process boundary; no shell, cookies or account login.
        url, _ = validate_url(url, "playlist")
        command = Pipeline.yt_args() + ["--flat-playlist", "--skip-download", "--dump-single-json", "--yes-playlist", "--", url]
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            if self.stop.is_set():
                raise WatchInterrupted()
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                       start_new_session=True, env={**os.environ, "NO_COLOR": "1"})
            started = time.monotonic()
            try:
                while process.poll() is None:
                    if self.stop.wait(.2):
                        raise WatchInterrupted()
                    if time.monotonic() - started > 180:
                        raise WatchError("watch_timeout", "A verificação demorou demais. Tentaremos novamente mais tarde.")
                    if os.fstat(output.fileno()).st_size > 32 * 1024 * 1024 or os.fstat(errors.fileno()).st_size > 1024 * 1024:
                        raise WatchError("watch_too_large", "A playlist excedeu o limite de leitura do acompanhamento.")
                if self.stop.is_set():
                    raise WatchInterrupted()
                if process.returncode:
                    raise WatchError("watch_unavailable", "Não foi possível acessar a playlist. Confira o link, a visibilidade e a conexão.")
                output.seek(0)
                raw = output.read(32 * 1024 * 1024 + 1)
                if len(raw) > 32 * 1024 * 1024:
                    raise WatchError("watch_too_large", "A playlist excedeu o limite de leitura do acompanhamento.")
                try:
                    result = json.loads(raw)
                    expected = parse_qs(urlsplit(url).query)["list"][0]
                    if not isinstance(result, dict) or result.get("id") != expected or not isinstance(result.get("entries"), list):
                        raise ValueError()
                    if len(result["entries"]) > 10000:
                        raise WatchError("watch_too_large", "A playlist excedeu o limite de leitura do acompanhamento.")
                    return str(result.get("title") or "")[:300], result["entries"]
                except (ValueError, TypeError, KeyError) as exc:
                    raise WatchError("watch_invalid_response", "A leitura da playlist ficou incompleta. Tentaremos novamente mais tarde.") from exc
            finally:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=3)
                    except ProcessLookupError:
                        pass


class PlaylistWatchers:
    def __init__(self, catalog, jobs, scanner=None, clock=utcnow):
        self.catalog, self.jobs, self.clock = catalog, jobs, clock
        self.stop, self.wake = threading.Event(), threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.scanner = scanner or PlaylistScanner(self.stop)
        with catalog.db() as conn:
            conn.executescript(SCHEMA)
            conn.execute("UPDATE watch_sources SET status='idle',next_check=? WHERE status='checking'", (self.stamp(),))

    def stamp(self):
        return self.clock().isoformat()

    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True, name="playlist-watcher")
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=8)

    def loop(self):
        while not self.stop.is_set():
            try:
                self.dispatch_pending()
                if self.run_once():
                    continue
            except Exception:
                LOG.exception("Playlist watcher iteration failed")
            self.wake.wait(15)
            self.wake.clear()

    def list(self):
        with self.catalog.db() as conn:
            rows = conn.execute("SELECT * FROM watch_sources ORDER BY created_at,id").fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["enabled"], item["initialized"] = bool(row["enabled"]), bool(row["initialized"])
                counts = dict(conn.execute("SELECT state,count(*) FROM watch_seen WHERE source_id=? GROUP BY state", (row["id"],)))
                item.update(known=sum(counts.values()), pending=counts.get("pending", 0), queued=counts.get("queued", 0), baseline=counts.get("baseline", 0))
                item["recent"] = [dict(r) for r in conn.execute("""SELECT w.video_id,w.title,w.detected_at,w.state,w.job_id,
                    (SELECT i.status FROM job_items i WHERE i.job_id=w.job_id AND i.video_id=w.video_id LIMIT 1) AS processing_status
                    FROM watch_seen w WHERE w.source_id=? ORDER BY w.detected_at DESC,w.video_id LIMIT 10""", (row["id"],))]
                items.append(item)
            return {"items": items, "total": len(items)}

    def create(self, url, name="", language="auto", interval_minutes=120, initial_mode="all", enabled=True):
        try:
            url, _ = validate_url(url, "playlist")
        except ValueError as exc:
            raise WatchError("watch_invalid_url", "Informe o link completo de uma playlist do YouTube.") from exc
        self.validate(name, language, interval_minutes)
        if initial_mode not in ("all", "new"):
            raise WatchError("watch_invalid_input", "Confira as configurações do acompanhamento.")
        identifier, now = uuid.uuid4().hex, self.stamp()
        with self.catalog.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM watch_sources WHERE url=?", (url,)).fetchone():
                raise WatchError("watch_duplicate", "Esta playlist já está sendo acompanhada.", 409)
            if conn.execute("SELECT count(*) FROM watch_sources").fetchone()[0] >= 50:
                raise WatchError("watch_limit", "O limite é de 50 playlists acompanhadas.", 409)
            conn.execute("""INSERT INTO watch_sources(id,url,name,language,interval_minutes,initial_mode,enabled,created_at,updated_at,next_check)
                            VALUES(?,?,?,?,?,?,?,?,?,?)""", (identifier, url, name.strip(), language, interval_minutes, initial_mode, int(enabled), now, now, now))
        self.wake.set()
        return {"id": identifier}

    @staticmethod
    def validate(name, language, interval):
        if not isinstance(name, str) or len(name) > 160 or language not in ("auto", "pt", "en", "es") or not isinstance(interval, int) or isinstance(interval, bool) or not 15 <= interval <= 1440:
            raise WatchError("watch_invalid_input", "Confira as configurações do acompanhamento.")

    def update(self, identifier, **changes):
        with self.catalog.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM watch_sources WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise WatchError("watch_not_found", "Acompanhamento não encontrado.", 404)
            settings = {key: changes.get(key, row[key]) for key in ("name", "language", "interval_minutes", "enabled")}
            name, language, interval_minutes, enabled = (settings[key] for key in ("name", "language", "interval_minutes", "enabled"))
            self.validate(name, language, interval_minutes)
            next_check = self.stamp() if enabled and not row["enabled"] else (self.clock() + timedelta(minutes=interval_minutes)).isoformat() if interval_minutes != row["interval_minutes"] else row["next_check"]
            conn.execute("UPDATE watch_sources SET name=?,language=?,interval_minutes=?,enabled=?,next_check=?,updated_at=? WHERE id=?", (name.strip(), language, interval_minutes, int(enabled), next_check, self.stamp(), identifier))
        self.wake.set()

    def check_now(self, identifier):
        with self.catalog.db() as conn:
            row = conn.execute("SELECT enabled,status FROM watch_sources WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise WatchError("watch_not_found", "Acompanhamento não encontrado.", 404)
            if not row["enabled"]:
                raise WatchError("watch_paused", "Retome o acompanhamento antes de verificar.", 409)
            if row["status"] != "checking":
                conn.execute("UPDATE watch_sources SET next_check=?,updated_at=? WHERE id=?", (self.stamp(), self.stamp(), identifier))
        self.wake.set()

    def remove(self, identifier):
        with self.catalog.db() as conn:
            if not conn.execute("DELETE FROM watch_sources WHERE id=?", (identifier,)).rowcount:
                raise WatchError("watch_not_found", "Acompanhamento não encontrado.", 404)

    def run_once(self):
        if self.stop.is_set() or not self.lock.acquire(blocking=False):
            return False
        source = None
        try:
            with self.catalog.db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM watch_sources WHERE enabled=1 AND next_check<=? ORDER BY next_check,id LIMIT 1", (self.stamp(),)).fetchone()
                if not row:
                    return False
                source = dict(row)
                conn.execute("UPDATE watch_sources SET status='checking',updated_at=? WHERE id=?", (self.stamp(), source["id"]))
            title, entries = self.scanner.scan(source["url"])
            if self.stop.is_set():
                raise WatchInterrupted()
            if not isinstance(entries, list) or len(entries) > 10000:
                raise WatchError("watch_invalid_response", "A leitura da playlist ficou incompleta. Tentaremos novamente mais tarde.")
            videos, unavailable = {}, 0
            for entry in entries:
                if not isinstance(entry, dict) or not YOUTUBE_ID.fullmatch(str(entry.get("id", ""))) or entry.get("availability") in ("private", "premium_only", "subscriber_only", "needs_auth") or entry.get("title") in ("[Private video]", "[Deleted video]") or entry.get("live_status") in ("is_live", "is_upcoming"):
                    unavailable += 1
                    continue
                videos[entry["id"]] = str(entry.get("title") or entry["id"])[:500]
            with self.catalog.db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = conn.execute("SELECT * FROM watch_sources WHERE id=?", (source["id"],)).fetchone()
                if not current or not current["enabled"]:
                    if current:
                        conn.execute("UPDATE watch_sources SET status='idle' WHERE id=?", (source["id"],))
                    return True
                baseline = not current["initialized"] and current["initial_mode"] == "new"
                added = 0
                for video_id, video_title in videos.items():
                    state = "baseline" if baseline else "pending"
                    added += conn.execute("INSERT OR IGNORE INTO watch_seen VALUES(?,?,?,?,?,NULL)", (source["id"], video_id, video_title, self.stamp(), state)).rowcount
                now = self.stamp()
                conn.execute("""UPDATE watch_sources SET title=?,status='idle',initialized=1,last_checked=?,last_success=?,
                    next_check=?,updated_at=?,last_error=NULL,error_code=NULL,failures=0,last_new=?,last_unavailable=? WHERE id=?""",
                    (title[:300], now, now, (self.clock() + timedelta(minutes=current["interval_minutes"])).isoformat(), now, 0 if baseline else added, unavailable, source["id"]))
            self.dispatch_pending()
            return True
        except WatchInterrupted:
            if source:
                with self.catalog.db() as conn:
                    conn.execute("UPDATE watch_sources SET status='idle',next_check=? WHERE id=?", (self.stamp(), source["id"]))
            return False
        except Exception as exc:
            if source:
                code = exc.code if isinstance(exc, WatchError) else "watch_unavailable"
                message = str(exc) if isinstance(exc, WatchError) else "Não foi possível acessar a playlist. Confira o link, a visibilidade e a conexão."
                with self.catalog.db() as conn:
                    current = conn.execute("SELECT failures,interval_minutes FROM watch_sources WHERE id=?", (source["id"],)).fetchone()
                    if current:
                        delay = min(current["interval_minutes"], 15 * 2 ** min(current["failures"], 6))
                        conn.execute("UPDATE watch_sources SET status='error',last_checked=?,last_error=?,error_code=?,failures=failures+1,next_check=?,updated_at=? WHERE id=?", (self.stamp(), message, code, (self.clock() + timedelta(minutes=delay)).isoformat(), self.stamp(), source["id"]))
                LOG.warning("Playlist check failed: %s", code)
            return True
        finally:
            self.lock.release()

    def dispatch_pending(self):
        if self.stop.is_set():
            return
        with self.catalog.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for source in conn.execute("SELECT * FROM watch_sources WHERE enabled=1 ORDER BY created_at,id").fetchall():
                # An existing batch for this source drains before another is created.
                if conn.execute("SELECT 1 FROM jobs WHERE id=? AND status IN('queued','discovering','running')", (source["last_job_id"],)).fetchone():
                    continue
                pending = conn.execute("SELECT * FROM watch_seen WHERE source_id=? AND state='pending' ORDER BY detected_at,video_id LIMIT 100", (source["id"],)).fetchall()
                ready = []
                for item in pending:
                    if conn.execute("SELECT 1 FROM videos WHERE id=?", (item["video_id"],)).fetchone():
                        conn.execute("UPDATE watch_seen SET state='existing' WHERE source_id=? AND video_id=?", (source["id"], item["video_id"]))
                    else:
                        queued = conn.execute("SELECT i.job_id FROM job_items i JOIN jobs j ON j.id=i.job_id WHERE i.video_id=? AND j.status IN('queued','discovering','running') AND i.status IN('queued','running') LIMIT 1", (item["video_id"],)).fetchone()
                        if queued:
                            conn.execute("UPDATE watch_seen SET state='queued',job_id=? WHERE source_id=? AND video_id=?", (queued[0], source["id"], item["video_id"]))
                        else:
                            ready.append(item)
                if not ready or conn.execute("SELECT count(*) FROM jobs WHERE status IN('queued','discovering','running')").fetchone()[0] >= 100:
                    continue
                job_id, now = uuid.uuid4().hex, self.stamp()
                title = source["name"] or source["title"] or "Playlist do YouTube"
                conn.execute("""INSERT INTO jobs(id,url,kind,language,title,status,stage,total,discovered,created_at,updated_at)
                                VALUES(?,?,'playlist',?,?,'queued','queued',?,1,?,?)""", (job_id, source["url"], source["language"], title, len(ready), now, now))
                for position, item in enumerate(ready, 1):
                    conn.execute("INSERT INTO job_items VALUES(?,?,?,?,?,'queued','queued',NULL,NULL,?)", (uuid.uuid4().hex, job_id, position, item["video_id"], item["title"], now))
                    conn.execute("UPDATE watch_seen SET state='queued',job_id=? WHERE source_id=? AND video_id=?", (job_id, source["id"], item["video_id"]))
                conn.execute("UPDATE watch_sources SET last_job_id=?,updated_at=? WHERE id=?", (job_id, now, source["id"]))
        self.jobs.wake.set()
