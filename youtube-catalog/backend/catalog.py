"""Local catalog storage, incremental import and accent-insensitive search."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import stat
import threading
import unicodedata
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml

LOG = logging.getLogger(__name__)
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
FILES = {
    "transcript.txt": "TXT · original",
    "transcript.srt": "SRT · original",
    "transcript.json": "JSON · original",
    "transcript-speakers.txt": "TXT · com falantes",
    "transcript-speakers.srt": "SRT · com falantes",
    "transcript-speakers.json": "JSON · com falantes",
}
IMPORT_FILES = ("video.json", "METODO.md", *FILES)
LANGUAGES = {"pt": "Português", "en": "Inglês", "es": "Espanhol", "fr": "Francês", "de": "Alemão", "it": "Italiano", "und": "Não informado"}


class PurgeConflict(ValueError):
    """A permanent deletion cannot proceed in the current catalog state."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))


def words(value: str) -> list[str]:
    return re.findall(r"[^\W_]+", normalize(value), flags=re.UNICODE)[:100]


def fts_query(value: str, operation: str = "AND") -> str:
    return f" {operation} ".join(f'"{word}"' for word in words(value))


def plain_match(text: str, query: str) -> bool:
    value = normalize(text)
    return all(term in value for term in words(query))


def safe_file(root: Path, video_id: str, filename: str) -> Path:
    if not VIDEO_ID.fullmatch(video_id) or filename not in IMPORT_FILES:
        raise ValueError("Arquivo não permitido.")
    path = root / video_id / filename
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("O arquivo aponta para fora da coleção.")
    return path


def read_json(path: Path) -> dict:
    if path.stat().st_size > 100 * 1024 * 1024:
        raise ValueError(f"{path.name}: arquivo excede 100 MB.")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: objeto JSON esperado.")
    return data


def number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}: número inválido.")
    return float(value)


def parse_segments(data: dict) -> list[dict]:
    raw = data.get("transcription", data.get("segments"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("Transcrição vazia ou sem segmentos válidos.")
    result = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError("Segmento de transcrição inválido.")
        offsets = item.get("offsets")
        if isinstance(offsets, dict):
            start = number(offsets.get("from"), "Início")
            end = number(offsets.get("to"), "Fim")
        elif "start_ms" in item and "end_ms" in item:
            start = number(item["start_ms"], "Início")
            end = number(item["end_ms"], "Fim")
        else:
            start = number(item.get("start"), "Início") * 1000
            end = number(item.get("end"), "Fim") * 1000
        if start < 0 or end < start:
            raise ValueError("Intervalo de transcrição inválido.")
        text = item["text"].strip()
        speaker = item.get("speaker")
        if text:
            result.append({"index": len(result), "start_ms": round(start), "end_ms": round(end), "text": text, "speaker": str(speaker) if speaker is not None else None})
    if not result:
        raise ValueError("Transcrição sem texto.")
    return result


def parse_method(path: Path) -> tuple[dict, str | None]:
    if not path.exists():
        return {}, None
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("METODO.md excede 2 MB.")
    text = path.read_text(encoding="utf-8-sig")
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return {}, text.strip() or None
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)(.*)\Z", text, flags=re.S)
    if not match:
        raise ValueError("METODO.md: cabeçalho YAML incompleto.")
    metadata = yaml.safe_load(match.group(1)) or {}
    if not isinstance(metadata, dict):
        raise ValueError("METODO.md: cabeçalho YAML inválido.")
    return metadata, match.group(2).strip() or None


SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, channel TEXT NOT NULL, channel_id TEXT NOT NULL,
 duration REAL NOT NULL, language TEXT NOT NULL, published_at TEXT, added_at TEXT NOT NULL,
 available INTEGER NOT NULL DEFAULT 1, description TEXT NOT NULL, method TEXT, tags TEXT NOT NULL,
 has_speakers INTEGER NOT NULL, thumbnail_source TEXT, category_override INTEGER NOT NULL DEFAULT 0,
 deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS segments (
 video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE, idx INTEGER NOT NULL,
 start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, text TEXT NOT NULL, speaker TEXT,
 PRIMARY KEY(video_id,idx)
);
CREATE TABLE IF NOT EXISTS categories (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, name_normalized TEXT NOT NULL UNIQUE, renamed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS video_categories (
 video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
 category_id TEXT NOT NULL REFERENCES categories(id), source TEXT NOT NULL,
 PRIMARY KEY(video_id,category_id,source)
);
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, fingerprint TEXT, warning TEXT, present INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS purge_jobs (
 video_id TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
 quarantine TEXT NOT NULL, phase TEXT NOT NULL, source_device INTEGER, source_inode INTEGER,
 created_at TEXT NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS video_fts USING fts5(
 video_id UNINDEXED, title, channel, tags, transcript, tokenize='unicode61 remove_diacritics 2'
);
CREATE VIRTUAL TABLE IF NOT EXISTS segment_fts USING fts5(
 video_id UNINDEXED, idx UNINDEXED, text, tokenize='unicode61 remove_diacritics 2'
);
CREATE INDEX IF NOT EXISTS video_channel_idx ON videos(channel_id);
CREATE INDEX IF NOT EXISTS video_language_idx ON videos(language);
"""


class Catalog:
    def __init__(self, source: Path, data: Path):
        self.source = source.resolve()
        self.data = data.resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data / "catalog.sqlite3"
        self.scan_lock = threading.Lock()
        self.stop = threading.Event()
        self.scanning = False
        self.scan_thread: threading.Thread | None = None
        self.thumbnail_manager = None
        with self.db() as conn:
            conn.executescript(SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
            if "deleted_at" not in columns:
                conn.execute("ALTER TABLE videos ADD COLUMN deleted_at TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS video_deleted_idx ON videos(deleted_at)")
            conn.execute("PRAGMA user_version=3")

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def fingerprint(self, video_id: str) -> str:
        values = []
        for filename in IMPORT_FILES:
            path = safe_file(self.source, video_id, filename)
            if path.exists():
                if not path.is_file():
                    raise ValueError(f"{filename}: não é um arquivo regular.")
                stat = path.stat()
                values.append((filename, stat.st_size, stat.st_mtime_ns, stat.st_ino))
            else:
                values.append((filename, None))
        return hashlib.sha256(json.dumps(values).encode()).hexdigest()

    def parse_video(self, video_id: str) -> dict:
        paths = {name: safe_file(self.source, video_id, name) for name in IMPORT_FILES}
        meta = read_json(paths["video.json"])
        if meta.get("id") != video_id or not isinstance(meta.get("title"), str) or not meta["title"].strip():
            raise ValueError("video.json: identificador ou título inválido.")
        transcript = read_json(paths["transcript.json"])
        segments = parse_segments(transcript)
        warning = None
        if paths["transcript-speakers.json"].exists():
            try:
                preferred = parse_segments(read_json(paths["transcript-speakers.json"]))
                if not any(s["speaker"] for s in preferred):
                    raise ValueError("Falantes ausentes.")
                segments = preferred
            except (OSError, ValueError, TypeError, KeyError) as exc:
                warning = "Versão com falantes inválida; exibindo a transcrição original."
                LOG.warning("%s: %s (%s)", video_id, warning, type(exc).__name__)
        method_meta, method = parse_method(paths["METODO.md"])
        imported = []
        if method_meta.get("cluster_id") and method_meta.get("cluster"):
            category_id, category_name = str(method_meta["cluster_id"]).strip(), str(method_meta["cluster"]).strip()
            if not re.fullmatch(r"C[0-9]+", category_id) or not category_name or len(category_name) > 160:
                raise ValueError("METODO.md: categoria inválida.")
            imported.append((category_id, category_name))
        tags = []
        for raw_tags in (meta.get("tags"), method_meta.get("tags")):
            if raw_tags is not None and not isinstance(raw_tags, list):
                raise ValueError("Tags devem ser uma lista.")
            for tag in raw_tags or []:
                if isinstance(tag, str) and tag.strip() and tag.strip() not in tags:
                    tags.append(tag.strip()[:200])
        channel = str(meta.get("channel") or meta.get("uploader") or "Canal não informado")
        channel_id = str(meta.get("channel_id") or meta.get("uploader_id") or "local-" + hashlib.sha256(channel.encode()).hexdigest()[:16])
        language = transcript.get("result", {}).get("language") if isinstance(transcript.get("result"), dict) else None
        language = str(language or transcript.get("language") or meta.get("language") or "und").lower().split("-")[0]
        duration = number(meta.get("duration", max(s["end_ms"] for s in segments) / 1000) or 0, "Duração")
        if duration < 0:
            raise ValueError("Duração inválida.")
        published = None
        if meta.get("upload_date"):
            try:
                published = datetime.strptime(str(meta["upload_date"]), "%Y%m%d").replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                pass
        if not published and isinstance(meta.get("timestamp"), (int, float)):
            try:
                published = datetime.fromtimestamp(meta["timestamp"], timezone.utc).isoformat()
            except (ValueError, OverflowError, OSError):
                pass
        stat = paths["transcript.json"].stat()
        added = datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_mtime), timezone.utc).isoformat()
        thumbnail = meta.get("thumbnail")
        return {
            "id": video_id, "title": meta["title"].strip(), "channel": channel, "channel_id": channel_id,
            "duration": duration, "language": language, "published_at": published, "added_at": added,
            "description": str(meta.get("description") or ""), "method": method,
            "tags": json.dumps(tags, ensure_ascii=False), "has_speakers": int(any(s["speaker"] for s in segments)),
            "thumbnail_source": thumbnail if isinstance(thumbnail, str) else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "segments": segments, "imported": imported, "warning": warning,
        }

    def import_video(self, video: dict, fingerprint: str):
        with self.db() as conn:
            conn.execute("""INSERT INTO videos(id,title,channel,channel_id,duration,language,published_at,added_at,
                description,method,tags,has_speakers,thumbnail_source,available)
                VALUES(:id,:title,:channel,:channel_id,:duration,:language,:published_at,:added_at,
                :description,:method,:tags,:has_speakers,:thumbnail_source,1)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,channel=excluded.channel,channel_id=excluded.channel_id,
                duration=excluded.duration,language=excluded.language,published_at=excluded.published_at,
                description=excluded.description,method=excluded.method,tags=excluded.tags,
                has_speakers=excluded.has_speakers,thumbnail_source=excluded.thumbnail_source,available=1""", video)
            conn.execute("DELETE FROM segments WHERE video_id=?", (video["id"],))
            conn.execute("DELETE FROM segment_fts WHERE video_id=?", (video["id"],))
            conn.execute("DELETE FROM video_fts WHERE video_id=?", (video["id"],))
            conn.executemany("INSERT INTO segments VALUES(?,?,?,?,?,?)", ((video["id"], s["index"], s["start_ms"], s["end_ms"], s["text"], s["speaker"]) for s in video["segments"]))
            conn.executemany("INSERT INTO segment_fts(video_id,idx,text) VALUES(?,?,?)", ((video["id"], s["index"], s["text"]) for s in video["segments"]))
            conn.execute("INSERT INTO video_fts(video_id,title,channel,tags,transcript) VALUES(?,?,?,?,?)", (video["id"], video["title"], video["channel"], " ".join(json.loads(video["tags"])), "\n".join(s["text"] for s in video["segments"])))
            conn.execute("DELETE FROM video_categories WHERE video_id=? AND source='imported'", (video["id"],))
            for category_id, name in video["imported"]:
                existing = conn.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone()
                if not existing:
                    conflict = conn.execute("SELECT id FROM categories WHERE name_normalized=?", (normalize(name),)).fetchone()
                    if conflict:
                        # Preserve the stable imported id even when a user already used its label.
                        name = f"{name} ({category_id})"
                    conn.execute("INSERT INTO categories(id,name,name_normalized) VALUES(?,?,?)", (category_id, name, normalize(name)))
                elif not existing["renamed"] and existing["name"] != name:
                    conflict = conn.execute("SELECT id FROM categories WHERE name_normalized=? AND id<>?", (normalize(name), category_id)).fetchone()
                    if not conflict:
                        conn.execute("UPDATE categories SET name=?,name_normalized=? WHERE id=?", (name, normalize(name), category_id))
                conn.execute("INSERT INTO video_categories VALUES(?,?,'imported')", (video["id"], category_id))
            conn.execute("INSERT INTO sources(id,fingerprint,warning,present) VALUES(?,?,?,1) ON CONFLICT(id) DO UPDATE SET fingerprint=excluded.fingerprint,warning=excluded.warning,present=1", (video["id"], fingerprint, video["warning"]))

    def scan(self, already_locked: bool = False):
        if not already_locked and not self.scan_lock.acquire(blocking=False):
            return
        self.scanning = True
        try:
            if not self.source.is_dir():
                raise OSError("A pasta de transcrições não está disponível.")
            with self.db() as conn:
                previous = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM sources")}
                purging = {r[0] for r in conn.execute("SELECT video_id FROM purge_jobs")}
            seen = set()
            for folder in sorted(self.source.iterdir()):
                if self.stop.is_set():
                    return
                video_id = folder.name
                if not VIDEO_ID.fullmatch(video_id) or not folder.is_dir():
                    continue
                if not (folder / "video.json").exists() and not (folder / "transcript.json").exists() and video_id not in previous:
                    continue
                seen.add(video_id)
                if video_id in purging:
                    continue
                fingerprint = None
                try:
                    fingerprint = self.fingerprint(video_id)
                    old = previous.get(video_id)
                    if old and old["fingerprint"] == fingerprint and old["present"]:
                        continue
                    video = self.parse_video(video_id)
                    if self.fingerprint(video_id) != fingerprint:
                        raise ValueError("Os arquivos mudaram durante a leitura; nova tentativa na próxima atualização.")
                    self.import_video(video, fingerprint)
                except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError, sqlite3.Error) as exc:
                    if isinstance(exc, FileNotFoundError):
                        message = "Arquivo necessário ausente; última importação válida preservada."
                    elif isinstance(exc, (json.JSONDecodeError, yaml.YAMLError)):
                        message = "Arquivo JSON ou YAML inválido; última importação válida preservada."
                    else:
                        message = str(exc)[:300] or "Não foi possível importar este vídeo."
                    LOG.warning("Importing %s: %s", video_id, message)
                    with self.db() as conn:
                        conn.execute("INSERT INTO sources(id,fingerprint,warning,present) VALUES(?,?,?,1) ON CONFLICT(id) DO UPDATE SET fingerprint=excluded.fingerprint,warning=excluded.warning,present=1", (video_id, fingerprint, message))
                        try:
                            core_available = all(safe_file(self.source, video_id, name).is_file() for name in ("video.json", "transcript.json"))
                        except ValueError:
                            core_available = False
                        if not core_available:
                            conn.execute("UPDATE videos SET available=0 WHERE id=?", (video_id,))
            with self.db() as conn:
                for video_id in set(previous) - seen - purging:
                    conn.execute("UPDATE videos SET available=0 WHERE id=?", (video_id,))
                    conn.execute("UPDATE sources SET present=0,warning=? WHERE id=?", ("Pasta de origem removida; conteúdo importado preservado.", video_id))
                conn.execute("INSERT OR REPLACE INTO settings VALUES('last_scan',?)", (now(),))
                conn.execute("DELETE FROM settings WHERE key='scan_error'")
            if self.thumbnail_manager:
                self.thumbnail_manager.enqueue_missing()
        except Exception:
            LOG.exception("Failed to refresh the catalog")
            with self.db() as conn:
                conn.execute("INSERT OR REPLACE INTO settings VALUES('scan_error',?)", ("A atualização falhou. Confira se a pasta de transcrições está montada e pode ser lida.",))
        finally:
            self.scanning = False
            self.scan_lock.release()

    def request_scan(self):
        if not self.scan_lock.acquire(blocking=False):
            return
        self.scanning = True
        self.scan_thread = threading.Thread(target=self.scan, kwargs={"already_locked": True}, daemon=True, name="catalog-scan")
        self.scan_thread.start()

    def effective_categories(self, conn, video_ids: list[str] | None = None) -> dict[str, list[dict]]:
        params = []
        where = " AND v.deleted_at IS NULL"
        if video_ids is not None:
            if not video_ids:
                return {}
            where = " AND v.id IN (" + ",".join("?" for _ in video_ids) + ")"
            params = video_ids
        result = {}
        for row in conn.execute("""SELECT v.id video_id,c.id,c.name FROM videos v JOIN video_categories vc ON vc.video_id=v.id
            JOIN categories c ON c.id=vc.category_id WHERE vc.source=CASE WHEN v.category_override=1 THEN 'user' ELSE 'imported' END""" + where + " ORDER BY c.name COLLATE NOCASE", params):
            result.setdefault(row["video_id"], []).append({"id": row["id"], "name": row["name"]})
        return result

    def summary(self, row, categories, match=None) -> dict:
        keys = ("id", "title", "channel", "channel_id", "duration", "language", "published_at", "added_at", "deleted_at")
        result = {key: row[key] for key in keys}
        cached = self.thumbnail_manager.cached(row["id"]) if self.thumbnail_manager else None
        version = str(cached.stat().st_mtime_ns) if cached else "local"
        result.update(available=bool(row["available"]), has_method=bool(row["method"]), has_speakers=bool(row["has_speakers"]), categories=categories.get(row["id"], []), tags=json.loads(row["tags"]), thumbnail_url=f"/api/videos/{row['id']}/thumbnail?v={version}", match=match)
        with self.db() as conn:
            purge = conn.execute("SELECT error FROM purge_jobs WHERE video_id=?", (row["id"],)).fetchone()
        result.update(purge_pending=purge is not None, purge_error=purge["error"] if purge else None)
        return result

    def list_videos(self, q="", channel="", category="", language="", sort="", page=1, page_size=24, deleted=False) -> dict:
        with self.db() as conn:
            conditions, params = ["v.deleted_at IS NOT NULL" if deleted else "v.deleted_at IS NULL"], []
            ranks, matches = {}, {}
            query = fts_query(q)
            if q.strip() and not query:
                return {"items": [], "total": 0, "page": page, "page_size": page_size}
            if query:
                ranks = {row["video_id"]: row["rank"] for row in conn.execute("SELECT video_id,bm25(video_fts,0,8,5,4,1) rank FROM video_fts WHERE video_fts MATCH ?", (query,))}
                if not ranks:
                    return {"items": [], "total": 0, "page": page, "page_size": page_size}
                conditions.append("v.id IN (" + ",".join("?" for _ in ranks) + ")")
                params.extend(ranks)
            for field, value in (("channel_id", channel), ("language", language)):
                if value:
                    conditions.append(f"v.{field}=?")
                    params.append(value)
            effective = "vc.video_id=v.id AND vc.source=CASE WHEN v.category_override=1 THEN 'user' ELSE 'imported' END"
            if category == "uncategorized":
                conditions.append(f"NOT EXISTS(SELECT 1 FROM video_categories vc WHERE {effective})")
            elif category:
                conditions.append(f"EXISTS(SELECT 1 FROM video_categories vc WHERE {effective} AND vc.category_id=?)")
                params.append(category)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            sort = sort or ("relevance" if query else "added")
            order = {"added": "v.added_at DESC,v.id", "published": "v.published_at DESC,v.id", "title": "v.title COLLATE NOCASE,v.id", "relevance": "v.added_at DESC,v.id"}[sort]
            if deleted and sort == "added":
                order = "v.deleted_at DESC,v.id"
            total = conn.execute("SELECT count(*) FROM videos v" + where, params).fetchone()[0]
            if sort == "relevance" and query:
                rows = list(conn.execute("SELECT v.* FROM videos v" + where, params))
                rows.sort(key=lambda r: (ranks.get(r["id"], 0), r["id"]))
                rows = rows[(page - 1) * page_size:page * page_size]
            else:
                rows = list(conn.execute("SELECT v.* FROM videos v" + where + " ORDER BY " + order + " LIMIT ? OFFSET ?", [*params, page_size, (page - 1) * page_size]))
            ids = [row["id"] for row in rows]
            if query and ids:
                # Find a useful jump target even when terms span multiple segments or metadata.
                hit_query = fts_query(q, "OR")
                for row in conn.execute("SELECT video_id,idx,bm25(segment_fts) rank FROM segment_fts WHERE segment_fts MATCH ? AND video_id IN (" + ",".join("?" for _ in ids) + ") ORDER BY rank", [hit_query, *ids]):
                    if row["video_id"] in matches:
                        continue
                    segment = conn.execute("SELECT * FROM segments WHERE video_id=? AND idx=?", (row["video_id"], row["idx"])).fetchone()
                    text = segment["text"]
                    if len(text) > 450:
                        found = [normalize(text).find(term) for term in words(q)]
                        at = max(0, min((n for n in found if n >= 0), default=0) - 100)
                        text = ("…" if at else "") + text[at:at + 450] + ("…" if at + 450 < len(text) else "")
                    matches[row["video_id"]] = {"text": text, "start_ms": segment["start_ms"], "segment_index": segment["idx"]}
            categories = self.effective_categories(conn, ids)
            return {"items": [self.summary(row, categories, matches.get(row["id"])) for row in rows], "total": total, "page": page, "page_size": page_size}

    def categories(self) -> list[dict]:
        with self.db() as conn:
            effective = self.effective_categories(conn)
            counts = Counter(c["id"] for group in effective.values() for c in group)
            return [{"id": row["id"], "name": row["name"], "count": counts[row["id"]]} for row in conn.execute("SELECT * FROM categories ORDER BY name COLLATE NOCASE")]

    def channels(self) -> list[dict]:
        with self.db() as conn:
            return [dict(row) for row in conn.execute("SELECT channel_id id,MAX(channel) name,count(*) count FROM videos WHERE deleted_at IS NULL GROUP BY channel_id ORDER BY name COLLATE NOCASE")]

    def stats(self) -> dict:
        with self.db() as conn:
            data = dict(conn.execute("SELECT count(*) videos,count(DISTINCT channel_id) channels,(SELECT count(*) FROM categories) categories,coalesce(sum(method IS NOT NULL AND method<>''),0) methods,coalesce(sum(duration)/3600.0,0) hours,(SELECT count(*) FROM videos WHERE deleted_at IS NOT NULL) deleted_videos FROM videos WHERE deleted_at IS NULL").fetchone())
            data["languages"] = [{"id": row["language"], "name": LANGUAGES.get(row["language"], row["language"].upper()), "count": row["count"]} for row in conn.execute("SELECT language,count(*) count FROM videos WHERE deleted_at IS NULL GROUP BY language ORDER BY count DESC,language")]
            data["warnings"] = [{"video_id": row["id"], "message": row["warning"]} for row in conn.execute("SELECT id,warning FROM sources WHERE warning IS NOT NULL AND NOT EXISTS(SELECT 1 FROM videos v WHERE v.id=sources.id AND v.deleted_at IS NOT NULL) ORDER BY id")]
            data["warnings"].extend({"video_id": row["video_id"], "kind": "purge", "message": row["error"] or "Exclusão definitiva interrompida. Abra a Lixeira para concluir; a restauração está bloqueada."} for row in conn.execute("SELECT video_id,error FROM purge_jobs"))
            settings = dict(conn.execute("SELECT key,value FROM settings"))
            if settings.get("scan_error"):
                data["warnings"].insert(0, {"video_id": "", "message": settings["scan_error"]})
            data.update(scanning=self.scanning, last_scan=settings.get("last_scan"))
            return data

    def detail(self, video_id: str) -> dict | None:
        with self.db() as conn:
            row = conn.execute("SELECT * FROM videos WHERE id=? AND deleted_at IS NULL", (video_id,)).fetchone()
            if not row:
                return None
            categories = self.effective_categories(conn, [video_id])
            result = self.summary(row, categories)
            downloads = []
            if row["available"]:
                for filename, label in FILES.items():
                    try:
                        path = safe_file(self.source, video_id, filename)
                        if path.is_file():
                            downloads.append({"filename": filename, "label": label, "url": f"/api/videos/{video_id}/files/{filename}"})
                    except ValueError:
                        continue
            result.update(description=row["description"], method=row["method"], downloads=downloads, category_override=bool(row["category_override"]), suggestions=self.suggestions(conn, row))
            return result

    def segments(self, video_id: str, offset=0, limit=100, q="") -> dict | None:
        with self.db() as conn:
            if not conn.execute("SELECT 1 FROM videos WHERE id=? AND deleted_at IS NULL", (video_id,)).fetchone():
                return None
            if q.strip():
                rows = [row for row in conn.execute("SELECT * FROM segments WHERE video_id=? ORDER BY idx", (video_id,)) if plain_match(row["text"], q)]
                total = len(rows)
                rows = rows[offset:offset + limit]
            else:
                total = conn.execute("SELECT count(*) FROM segments WHERE video_id=?", (video_id,)).fetchone()[0]
                rows = conn.execute("SELECT * FROM segments WHERE video_id=? ORDER BY idx LIMIT ? OFFSET ?", (video_id, limit, offset)).fetchall()
            return {"items": [{"index": r["idx"], "start_ms": r["start_ms"], "end_ms": r["end_ms"], "text": r["text"], "speaker": r["speaker"]} for r in rows], "total": total, "offset": offset, "limit": limit}

    def set_deleted(self, video_id: str, deleted: bool):
        """Hide a catalog entry or restore it without changing source artifacts."""
        with self.scan_lock, self.db() as conn:
            if not conn.execute("SELECT 1 FROM videos WHERE id=?", (video_id,)).fetchone():
                raise KeyError("Vídeo não encontrado.")
            if not deleted and conn.execute("SELECT 1 FROM purge_jobs WHERE video_id=?", (video_id,)).fetchone():
                raise PurgeConflict("A exclusão definitiva foi iniciada. Conclua a operação na Lixeira antes de continuar.")
            if deleted:
                conn.execute("UPDATE videos SET deleted_at=coalesce(deleted_at,?) WHERE id=?", (now(), video_id))
            else:
                conn.execute("UPDATE videos SET deleted_at=NULL WHERE id=?", (video_id,))

    @staticmethod
    def _entry_stat(root_fd: int, name: str):
        try:
            return os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    @staticmethod
    def _check_directory(entry, label: str):
        if entry is not None and (stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode)):
            raise PurgeConflict(f"{label} não é uma pasta real. Links simbólicos não podem ser excluídos por este recurso.")

    def _open_writable_source(self) -> int:
        """Pin the root with a descriptor and prove it is present and writable."""
        root_fd = os.open(self.source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if not stat.S_IMODE(os.fstat(root_fd).st_mode) & 0o222:
                raise PermissionError("A coleção está sem permissão de escrita.")
            probe = ".catalog-purge-check-" + uuid.uuid4().hex
            probe_fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
            os.close(probe_fd)
            os.unlink(probe, dir_fd=root_fd)
            return root_fd
        except BaseException:
            os.close(root_fd)
            raise

    def _remove_quarantine(self, root_fd: int, quarantine: str):
        if not shutil.rmtree.avoids_symlink_attacks:
            raise OSError("O sistema não oferece remoção segura de pastas.")
        shutil.rmtree(quarantine, dir_fd=root_fd)
        os.fsync(root_fd)

    def _remove_thumbnail_files(self, video_id: str):
        if self.thumbnail_manager:
            self.thumbnail_manager.remove_files(video_id)
            return
        directory = self.data / "thumbnails"
        if directory.is_symlink():
            raise OSError("A pasta de capas não pode ser um link simbólico.")
        for suffix in (".jpg", ".png", ".webp", ".part"):
            (directory / (video_id + suffix)).unlink(missing_ok=True)

    def permanent_delete(self, video_id: str, confirmation: str):
        """Erase one confirmed trash entry; unfinished work stays durable and explicit.

        The journal is never resumed automatically. A retry with the same explicit
        confirmation can finish an interrupted removal, while imports and restore
        cannot resurrect a half-removed source. Shared category definitions remain.
        """
        if not VIDEO_ID.fullmatch(video_id):
            raise KeyError("Vídeo não encontrado.")
        if confirmation != video_id:
            raise ValueError("A confirmação deve ser exatamente o identificador do vídeo.")
        with self.scan_lock:
            with self.db() as conn:
                row = conn.execute("SELECT deleted_at FROM videos WHERE id=?", (video_id,)).fetchone()
                if not row:
                    raise KeyError("Vídeo não encontrado.")
                if row["deleted_at"] is None:
                    raise PurgeConflict("Mova o vídeo para a Lixeira antes de excluí-lo definitivamente.")
                job = conn.execute("SELECT * FROM purge_jobs WHERE video_id=?", (video_id,)).fetchone()
            root_fd = None
            try:
                root_fd = self._open_writable_source()
                original = self._entry_stat(root_fd, video_id)
                self._check_directory(original, "A origem do vídeo")
                if not job:
                    quarantine = ".catalog-purge-" + uuid.uuid4().hex
                    if self._entry_stat(root_fd, quarantine) is not None:
                        raise PurgeConflict("Não foi possível reservar a pasta de exclusão. Tente novamente.")
                    with self.db() as conn:
                        conn.execute("INSERT INTO purge_jobs(video_id,quarantine,phase,source_device,source_inode,created_at) VALUES(?,?,?,?,?,?)", (video_id, quarantine, "prepared" if original else "missing", original.st_dev if original else None, original.st_ino if original else None, now()))
                        job = conn.execute("SELECT * FROM purge_jobs WHERE video_id=?", (video_id,)).fetchone()
                quarantine = job["quarantine"]
                if not re.fullmatch(r"\.catalog-purge-[0-9a-f]{32}", quarantine):
                    raise PurgeConflict("Registro de exclusão inválido; nenhuma pasta foi apagada.")
                quarantined = self._entry_stat(root_fd, quarantine)
                self._check_directory(quarantined, "A pasta de exclusão")
                if original is not None and quarantined is not None:
                    raise PurgeConflict("A origem foi recriada durante a exclusão. Mova a nova pasta antes de concluir esta operação.")
                if original is not None:
                    if job["phase"] != "prepared" or (original.st_dev, original.st_ino) != (job["source_device"], job["source_inode"]):
                        raise PurgeConflict("A pasta original foi substituída. Nenhum arquivo novo será apagado; confira a origem antes de tentar novamente.")
                if quarantined is not None and (quarantined.st_dev, quarantined.st_ino) != (job["source_device"], job["source_inode"]):
                    raise PurgeConflict("A pasta de exclusão foi substituída. A operação foi bloqueada.")
                if self.thumbnail_manager:
                    self.thumbnail_manager.begin_delete(video_id)
                if original is not None:
                    os.rename(video_id, quarantine, src_dir_fd=root_fd, dst_dir_fd=root_fd)
                    os.fsync(root_fd)
                    quarantined = self._entry_stat(root_fd, quarantine)
                    self._check_directory(quarantined, "A pasta de exclusão")
                    if quarantined is None or (quarantined.st_dev, quarantined.st_ino) != (job["source_device"], job["source_inode"]):
                        raise PurgeConflict("A origem mudou durante a operação. A remoção foi interrompida.")
                with self.db() as conn:
                    conn.execute("UPDATE purge_jobs SET phase='removing',error=NULL WHERE video_id=?", (video_id,))
                    conn.execute("UPDATE videos SET available=0 WHERE id=?", (video_id,))
                if quarantined is not None:
                    self._remove_quarantine(root_fd, quarantine)
                if self._entry_stat(root_fd, video_id) is not None:
                    raise PurgeConflict("Uma nova pasta apareceu para este vídeo. Mova essa pasta antes de concluir a exclusão pendente.")
                self._remove_thumbnail_files(video_id)
                with self.db() as conn:
                    conn.execute("DELETE FROM segment_fts WHERE video_id=?", (video_id,))
                    conn.execute("DELETE FROM video_fts WHERE video_id=?", (video_id,))
                    conn.execute("DELETE FROM sources WHERE id=?", (video_id,))
                    conn.execute("DELETE FROM videos WHERE id=?", (video_id,))
                if self.thumbnail_manager:
                    self.thumbnail_manager.finish_delete(video_id)
            except (OSError, sqlite3.Error, PurgeConflict) as exc:
                message = str(exc) if isinstance(exc, PurgeConflict) else "Não foi possível concluir a exclusão definitiva. Confira a montagem de escrita e as permissões da coleção/cache; o item permanece na Lixeira. Uma remoção iniciada pode estar parcial: tente concluir novamente."
                with self.db() as conn:
                    conn.execute("UPDATE purge_jobs SET error=? WHERE video_id=?", (message, video_id))
                LOG.warning("Permanent deletion of %s interrupted (%s)", video_id, type(exc).__name__)
                if isinstance(exc, PurgeConflict):
                    raise
                raise OSError(message) from exc
            finally:
                if root_fd is not None:
                    os.close(root_fd)

    def save_category(self, name: str, category_id: str | None = None) -> dict:
        name = " ".join(name.split())
        if not name or len(name) > 160:
            raise ValueError("Use um nome entre 1 e 160 caracteres.")
        with self.db() as conn:
            if category_id and not conn.execute("SELECT 1 FROM categories WHERE id=?", (category_id,)).fetchone():
                raise KeyError("Categoria não encontrada.")
            conflict = conn.execute("SELECT id FROM categories WHERE name_normalized=?", (normalize(name),)).fetchone()
            if conflict and conflict["id"] != category_id:
                raise ValueError("Já existe uma categoria com esse nome.")
            if category_id:
                conn.execute("UPDATE categories SET name=?,name_normalized=?,renamed=1 WHERE id=?", (name, normalize(name), category_id))
            else:
                category_id = "user-" + uuid.uuid4().hex
                conn.execute("INSERT INTO categories(id,name,name_normalized,renamed) VALUES(?,?,?,1)", (category_id, name, normalize(name)))
        return next(c for c in self.categories() if c["id"] == category_id)

    def assign_categories(self, video_id: str, ids: list[str] | None):
        with self.db() as conn:
            if not conn.execute("SELECT 1 FROM videos WHERE id=? AND deleted_at IS NULL", (video_id,)).fetchone():
                raise KeyError("Vídeo não encontrado.")
            if ids is not None:
                ids = list(dict.fromkeys(ids))
                known = {row[0] for row in conn.execute("SELECT id FROM categories")}
                if not set(ids).issubset(known):
                    raise ValueError("Uma ou mais categorias não existem.")
            conn.execute("DELETE FROM video_categories WHERE video_id=? AND source='user'", (video_id,))
            conn.execute("UPDATE videos SET category_override=? WHERE id=?", (int(ids is not None), video_id))
            if ids:
                conn.executemany("INSERT INTO video_categories VALUES(?,?,'user')", ((video_id, category_id) for category_id in ids))

    def suggestions(self, conn, target) -> list[dict]:
        stopwords = set("a o as os de da do das dos e em no na nos nas um uma para por com que se the a an of to and in for is it this that with on i you your how at from are ai ia video youtube https www com".split())
        rows = list(conn.execute("SELECT id,title,description,tags FROM videos WHERE deleted_at IS NULL"))
        documents = {}
        for row in rows:
            # Weight a repeated title above boilerplate channel descriptions.
            text = ((row["title"] + " ") * 3) + row["description"] + " " + " ".join(json.loads(row["tags"]))
            tokens = re.findall(r"[^\W_]+", normalize(text), flags=re.UNICODE)
            documents[row["id"]] = Counter(w for w in tokens if len(w) > 2 and w not in stopwords)
        df = Counter(word for doc in documents.values() for word in doc)
        vectors = {}
        for video_id, doc in documents.items():
            vector = {word: (1 + math.log(count)) * (1 + math.log((len(rows) + 1) / (df[word] + 1))) for word, count in doc.items()}
            length = math.sqrt(sum(value * value for value in vector.values())) or 1
            vectors[video_id] = {word: value / length for word, value in vector.items()}
        target_vector = vectors.get(target["id"], {})
        effective = self.effective_categories(conn)
        assigned = {c["id"] for c in effective.get(target["id"], [])}
        scores, names = {}, {}
        for video_id, categories in effective.items():
            if video_id == target["id"]:
                continue
            similarity = sum(value * vectors.get(video_id, {}).get(word, 0) for word, value in target_vector.items())
            if similarity <= 0:
                continue
            for category in categories:
                if category["id"] not in assigned:
                    scores[category["id"]] = max(scores.get(category["id"], 0), similarity)
                    names[category["id"]] = category["name"]
        return [{"id": category_id, "name": names[category_id], "score": round(score, 4)} for category_id, score in sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))[:3]]
