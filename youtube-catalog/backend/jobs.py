"""Persistent, single-worker YouTube transcription queue. No shell execution."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import selectors
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import yaml

from .catalog import now, number, parse_segments, read_json

LOG = logging.getLogger(__name__)
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,150}$")
MODEL_NAME = "ggml-large-v3-turbo-q5_0.bin"
MODEL_SHA256 = "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/" + MODEL_NAME
ACTIVE = ("queued", "discovering", "running")
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY,url TEXT NOT NULL,kind TEXT NOT NULL,language TEXT NOT NULL,title TEXT NOT NULL,
 status TEXT NOT NULL,stage TEXT NOT NULL,total INTEGER NOT NULL DEFAULT 0,
 cancel_requested INTEGER NOT NULL DEFAULT 0,discovered INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,error TEXT,error_code TEXT
);
CREATE TABLE IF NOT EXISTS job_items (
 id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,position INTEGER NOT NULL,
 video_id TEXT,title TEXT NOT NULL,status TEXT NOT NULL,stage TEXT NOT NULL,error TEXT,error_code TEXT,
 updated_at TEXT NOT NULL,UNIQUE(job_id,position)
);
CREATE INDEX IF NOT EXISTS job_items_job ON job_items(job_id,position);
CREATE TABLE IF NOT EXISTS job_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 time TEXT NOT NULL,stage TEXT NOT NULL,message TEXT NOT NULL
);
"""


class JobError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class JobInterrupted(Exception):
    pass


class JobCancelled(Exception):
    pass


def validate_artifacts(folder, video_id):
    try:
        for filename in ("video.json", "transcript.json", "transcript.txt", "transcript.srt"):
            path = folder / filename
            if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
                raise ValueError()
        meta = read_json(folder / "video.json")
        if meta.get("id") != video_id or not isinstance(meta.get("title"), str) or not meta["title"].strip():
            raise ValueError()
        if number(meta.get("duration", 0) or 0, "Duração") < 0 or (meta.get("tags") is not None and not isinstance(meta["tags"], list)):
            raise ValueError()
        parse_segments(read_json(folder / "transcript.json"))
    except (OSError, ValueError) as exc:
        raise JobError("invalid_artifacts", "A transcrição não produziu todos os arquivos válidos; a coleção não foi alterada.") from exc


def validate_url(url: str, kind: str = "auto") -> tuple[str, str]:
    if not isinstance(url, str) or not url.strip() or len(url) > 2048 or kind not in ("auto", "video", "playlist"):
        raise ValueError("Informe uma URL válida de vídeo ou playlist do YouTube.")
    try:
        parts = urlsplit(url.strip())
        if parts.scheme != "https" or parts.hostname not in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be") or parts.port is not None or parts.username or parts.password:
            raise ValueError()
        query = parse_qs(parts.query, max_num_fields=30)
        playlist = query.get("list", [None])[0]
        video = None
        path = parts.path.rstrip("/")
        if parts.hostname in ("youtu.be", "www.youtu.be"):
            video = path.removeprefix("/")
        elif path == "/watch":
            video = query.get("v", [None])[0]
        elif re.fullmatch(r"/(shorts|live|embed)/[^/]+", path):
            video = path.rsplit("/", 1)[-1]
        elif path != "/playlist":
            raise ValueError()
        if playlist is not None and not PLAYLIST_ID.fullmatch(playlist):
            raise ValueError()
        if video is not None and not YOUTUBE_ID.fullmatch(video):
            raise ValueError()
        resolved = "playlist" if kind == "playlist" or (kind == "auto" and playlist) else "video"
        if resolved == "playlist" and playlist:
            return "https://www.youtube.com/playlist?list=" + playlist, resolved
        if resolved == "video" and video:
            return "https://www.youtube.com/watch?v=" + video, resolved
    except (ValueError, TypeError):
        pass
    raise ValueError("Use uma URL HTTPS de vídeo ou playlist do YouTube; canais, lives em andamento e outros sites não são aceitos.")


class Pipeline:
    def __init__(self, queue):
        self.queue = queue
        self.process = None
        self.process_lock = threading.Lock()
        self.verified_model = None

    def kill(self):
        with self.process_lock:
            process = self.process
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def run(self, job_id, stage, command, output: Path | None = None, timeout=7200):
        self.queue.check(job_id)
        failure = {"discovering": "discovery_failed", "downloading": "download_failed", "converting": "conversion_failed", "transcribing": "transcription_failed"}.get(stage, "storage_error")
        started = time.monotonic()
        stream = output.open("wb") if output else None
        try:
            try:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream if stream else subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, env={**os.environ, "NO_COLOR": "1", "HF_HUB_DISABLE_TELEMETRY": "1"})
            except OSError as exc:
                raise JobError(failure, "Não foi possível iniciar a ferramenta local necessária.") from exc
            with self.process_lock:
                self.process = process
            selector = selectors.DefaultSelector()
            for pipe in (process.stderr, process.stdout if not stream else None):
                if pipe:
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ)
            last_log, buffer = 0.0, ""
            succeeded = False
            try:
                while process.poll() is None or selector.get_map():
                    self.queue.check(job_id)
                    if time.monotonic() - started > timeout:
                        raise JobError(failure, "A etapa excedeu o tempo de execução permitido.")
                    for key, _ in selector.select(timeout=0.2):
                        chunk = os.read(key.fileobj.fileno(), 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        buffer = (buffer + chunk.decode("utf-8", "replace"))[-1200:]
                        if time.monotonic() - last_log > 2:
                            self.queue.log(job_id, stage, buffer.strip()[-700:])
                            last_log, buffer = time.monotonic(), ""
                if buffer.strip():
                    self.queue.log(job_id, stage, buffer.strip()[-700:])
                self.queue.check(job_id)
                if process.wait() != 0:
                    raise JobError(failure, "A ferramenta local não concluiu esta etapa. Consulte o registro da tarefa.")
                succeeded = True
            finally:
                selector.close()
                if not succeeded:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                if process.poll() is None:
                    self.kill()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=3)
                if not succeeded:
                    # Children can outlive a terminated parent; finish the whole group.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                for pipe in (process.stdout, process.stderr):
                    if pipe:
                        pipe.close()
                with self.process_lock:
                    self.process = None
        finally:
            if stream:
                stream.close()

    @staticmethod
    def yt_args():
        return ["yt-dlp", "--ignore-config", "--no-cache-dir", "--js-runtimes", "node", "--socket-timeout", "30", "--retries", "3"]

    def discover(self, job):
        output = self.queue.work_path(job["id"]) / "discovery.json"
        args = self.yt_args() + ["--flat-playlist", "--skip-download", "--dump-single-json", "--ignore-errors", "--yes-playlist" if job["kind"] == "playlist" else "--no-playlist", "--", job["url"]]
        self.run(job["id"], "discovering", args, output=output, timeout=1800)
        try:
            data = json.loads(output.read_text())
            if not isinstance(data, dict):
                raise ValueError()
            entries = data.get("entries") if job["kind"] == "playlist" else [data]
            if not isinstance(entries, list) or not entries:
                raise ValueError()
            return str(data.get("title") or job["title"]), entries
        except (OSError, ValueError) as exc:
            raise JobError("discovery_failed", "Não foi possível listar os vídeos. A playlist pode estar vazia, privada ou indisponível.") from exc

    def ensure_model(self, job_id):
        model = self.queue.model_dir / MODEL_NAME
        self.queue.stage(job_id, "model")
        if model.is_symlink():
            raise JobError("model_checksum", "O modelo local não pode ser um link simbólico.")
        if model.is_file():
            key = (model.stat().st_size, model.stat().st_mtime_ns)
            if self.verified_model == key:
                return model
            digest = hashlib.sha256()
            with model.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    self.queue.check(job_id)
                    digest.update(chunk)
            if digest.hexdigest() != MODEL_SHA256:
                raise JobError("model_checksum", "O modelo local falhou na verificação SHA-256. Remova o arquivo inválido para baixá-lo novamente.")
            self.verified_model = key
            return model
        partial = self.queue.model_dir / (MODEL_NAME + ".part")
        if partial.is_symlink():
            raise JobError("model_download_failed", "O arquivo parcial do modelo não pode ser um link simbólico.")
        offset = partial.stat().st_size if partial.is_file() else 0
        if offset:
            digest = hashlib.sha256()
            with partial.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    self.queue.check(job_id)
                    digest.update(chunk)
            if digest.hexdigest() == MODEL_SHA256:
                os.replace(partial, model)
                self.verified_model = (model.stat().st_size, model.stat().st_mtime_ns)
                return model
        started = time.monotonic()
        try:
            with httpx.Client(timeout=httpx.Timeout(30, connect=15), follow_redirects=False, trust_env=False) as client:
                url = MODEL_URL
                for _ in range(8):
                    self.queue.check(job_id)
                    host = urlsplit(url).hostname or ""
                    if urlsplit(url).scheme != "https" or not any(host == suffix or host.endswith("." + suffix) for suffix in ("huggingface.co", "hf.co")):
                        raise JobError("model_download_failed", "O download do modelo redirecionou para um destino não permitido.")
                    with client.stream("GET", url, headers={"Range": f"bytes={offset}-"} if offset else {}) as response:
                        if response.is_redirect:
                            from urllib.parse import urljoin
                            url = urljoin(url, response.headers["location"])
                            continue
                        if response.status_code == 416 and offset:
                            partial.unlink(missing_ok=True)
                            offset, url = 0, MODEL_URL
                            continue
                        response.raise_for_status()
                        append = offset > 0 and response.status_code == 206 and response.headers.get("content-range", "").startswith(f"bytes {offset}-")
                        if response.status_code == 206 and not append:
                            raise JobError("model_download_failed", "Resposta parcial do modelo inválida.")
                        with partial.open("ab" if append else "wb") as handle:
                            size, reported = offset if append else 0, 0
                            for chunk in response.iter_bytes(1024 * 1024):
                                self.queue.check(job_id)
                                size += len(chunk)
                                if size > 800 * 1024 * 1024 or time.monotonic() - started > 3600:
                                    raise JobError("model_download_failed", "O download do modelo excedeu os limites de tamanho ou tempo.")
                                handle.write(chunk)
                                if size - reported >= 25 * 1024 * 1024:
                                    self.queue.log(job_id, "model", f"Modelo: {size // (1024 * 1024)} MB recebidos.")
                                    reported = size
                        break
                else:
                    raise JobError("model_download_failed", "Muitos redirecionamentos no download do modelo.")
            digest = hashlib.sha256()
            with partial.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    self.queue.check(job_id)
                    digest.update(chunk)
            if digest.hexdigest() != MODEL_SHA256:
                partial.unlink()
                raise JobError("model_checksum", "O modelo recebido falhou na verificação SHA-256; nenhum áudio foi processado.")
            os.replace(partial, model)
            self.verified_model = (model.stat().st_size, model.stat().st_mtime_ns)
            return model
        except (httpx.HTTPError, OSError) as exc:
            raise JobError("model_download_failed", "Não foi possível baixar o modelo local. O download poderá continuar na próxima tentativa.") from exc

    def transcribe(self, job, item):
        video_id = item["video_id"]
        folder = self.queue.work_path(job["id"]) / item["id"]
        if folder.is_symlink():
            raise JobError("storage_error", "A área de trabalho do vídeo não pode ser um link simbólico.")
        folder.mkdir(exist_ok=True)
        try:
            validate_artifacts(folder, video_id)
            self.queue.log(job["id"], "publishing", "Transcrição já concluída; retomando a publicação dos arquivos locais.")
            return folder
        except JobError:
            pass
        url = "https://www.youtube.com/watch?v=" + video_id
        self.queue.stage(job["id"], "downloading", item["id"])
        self.run(job["id"], "downloading", self.yt_args() + ["--no-playlist", "--skip-download", "--dump-single-json", "--", url], output=folder / "video.json", timeout=300)
        try:
            meta = read_json(folder / "video.json")
            if meta.get("id") != video_id or not isinstance(meta.get("title"), str) or not meta["title"].strip():
                raise ValueError()
            if meta.get("is_live") or meta.get("live_status") in ("is_live", "is_upcoming", "post_live"):
                raise JobError("live_unsupported", "Transmissões em andamento ou agendadas não são suportadas. Tente novamente quando o vídeo estiver publicado.")
        except (OSError, ValueError) as exc:
            raise JobError("unavailable", "Os metadados deste vídeo não estão disponíveis.") from exc
        self.run(job["id"], "downloading", self.yt_args() + ["--no-playlist", "--format", "bestaudio/best", "--match-filter", "!is_live & live_status != is_upcoming", "--newline", "--no-mtime", "--output", str(folder / "source.%(ext)s"), "--", url], timeout=7200)
        audio = [p for p in folder.glob("source.*") if p.is_file() and ".part" not in p.name and ".ytdl" not in p.name and not p.is_symlink()]
        if not audio:
            raise JobError("download_failed", "O download não produziu um arquivo de áudio.")
        self.queue.stage(job["id"], "converting", item["id"])
        self.run(job["id"], "converting", ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(audio[0]), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(folder / "audio.wav")], timeout=7200)
        self.queue.stage(job["id"], "model", item["id"])
        model = self.ensure_model(job["id"])
        self.queue.stage(job["id"], "transcribing", item["id"])
        threads = max(1, min(8, int(os.getenv("WHISPER_THREADS", "6"))))
        self.run(job["id"], "transcribing", ["whisper-cli", "--model", str(model), "--file", str(folder / "audio.wav"), "--language", job["language"], "--threads", str(threads), "--output-txt", "--output-srt", "--output-json", "--output-file", str(folder / "transcript")], timeout=172800)
        validate_artifacts(folder, video_id)
        return folder


class JobQueue:
    def __init__(self, catalog, work_dir: Path, model_dir: Path, pipeline_factory=Pipeline):
        self.catalog = catalog
        self.work_dir = Path(work_dir).resolve()
        self.model_dir = Path(model_dir).resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.worker_lock = threading.Lock()
        self.thread = None
        self.active_job = None
        self.pipeline = pipeline_factory(self)
        with catalog.db() as conn:
            conn.executescript(SCHEMA)
            # A stopped container resumes unfinished work; explicit cancellations persist.
            conn.execute("UPDATE job_items SET status='queued',stage='queued',updated_at=? WHERE status='running' AND job_id IN(SELECT id FROM jobs WHERE cancel_requested=0)", (now(),))
            conn.execute("UPDATE job_items SET status='cancelled',stage='cancelled',error_code='cancelled',updated_at=? WHERE status IN('queued','running') AND job_id IN(SELECT id FROM jobs WHERE cancel_requested=1)", (now(),))
            conn.execute("UPDATE jobs SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'queued' END,stage=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'queued' END,updated_at=? WHERE status IN('running','discovering')", (now(),))

    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True, name="transcription-worker")
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        self.pipeline.kill()
        if self.thread:
            self.thread.join(timeout=40)

    def work_path(self, job_id):
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("Identificador de tarefa inválido.")
        path = self.work_dir / job_id
        if path.is_symlink():
            raise JobError("storage_error", "A área de trabalho não pode ser um link simbólico.")
        path.mkdir(exist_ok=True)
        return path

    def check(self, job_id):
        if self.stop.is_set():
            raise JobInterrupted()
        with self.catalog.db() as conn:
            row = conn.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row[0]:
            raise JobCancelled()

    def log(self, job_id, stage, message):
        # Strip control characters; raw process output is displayed as text only.
        message = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(message))[-1000:]
        if not message:
            return
        with self.catalog.db() as conn:
            conn.execute("INSERT INTO job_logs(job_id,time,stage,message) VALUES(?,?,?,?)", (job_id, now(), stage, message))
            conn.execute("DELETE FROM job_logs WHERE job_id=? AND id NOT IN(SELECT id FROM job_logs WHERE job_id=? ORDER BY id DESC LIMIT 200)", (job_id, job_id))

    def stage(self, job_id, stage, item_id=None):
        with self.catalog.db() as conn:
            conn.execute("UPDATE jobs SET stage=?,updated_at=? WHERE id=?", (stage, now(), job_id))
            if item_id:
                conn.execute("UPDATE job_items SET stage=?,updated_at=? WHERE id=?", (stage, now(), item_id))
        self.log(job_id, stage, {"model": "Preparando o modelo local.", "downloading": "Baixando áudio do vídeo.", "converting": "Convertendo áudio para transcrição.", "transcribing": "Transcrevendo no próprio computador.", "publishing": "Salvando os arquivos no catálogo.", "discovering": "Listando todos os vídeos da URL."}.get(stage, stage))

    def detail(self, job_id, include_items=True):
        with self.catalog.db() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return None
            result = {key: row[key] for key in row.keys() if key != "discovered"}
            result["cancel_requested"] = bool(result["cancel_requested"])
            counts = dict(conn.execute("SELECT status,count(*) FROM job_items WHERE job_id=? GROUP BY status", (job_id,)))
            result.update(completed=counts.get("completed", 0), failed=counts.get("failed", 0), skipped=counts.get("skipped", 0))
            if include_items:
                result["items"] = [dict(r) for r in conn.execute("SELECT id,position,video_id,title,status,stage,error,error_code,updated_at FROM job_items WHERE job_id=? ORDER BY position", (job_id,))]
                result["logs"] = [dict(r) for r in conn.execute("SELECT id,time,stage,message FROM job_logs WHERE job_id=? ORDER BY id", (job_id,))]
            return result

    def list(self, page=1, page_size=10):
        with self.catalog.db() as conn:
            total = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
            ids = [r[0] for r in conn.execute("SELECT id FROM jobs ORDER BY created_at DESC,id LIMIT ? OFFSET ?", (page_size, (page - 1) * page_size))]
        return {"items": [self.detail(job_id, False) for job_id in ids], "total": total, "page": page, "page_size": page_size}

    def enqueue(self, url, language="auto", kind="auto"):
        url, kind = validate_url(url, kind)
        if language not in ("auto", "pt", "en", "es"):
            raise ValueError("Idioma inválido.")
        with self.catalog.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute("SELECT id FROM jobs WHERE url=? AND language=? AND kind=? AND status IN('queued','discovering','running')", (url, language, kind)).fetchone()
            if duplicate:
                job_id = duplicate[0]
            else:
                if conn.execute("SELECT count(*) FROM jobs WHERE status IN('queued','discovering','running')").fetchone()[0] >= 100:
                    raise JobError("queue_full", "Há 100 tarefas pendentes. Aguarde ou cancele algumas antes de adicionar outra.")
                job_id = uuid.uuid4().hex
                conn.execute("INSERT INTO jobs(id,url,kind,language,title,status,stage,created_at,updated_at) VALUES(?,?,?,?,?,'queued','queued',?,?)", (job_id, url, kind, language, "Playlist do YouTube" if kind == "playlist" else "Vídeo do YouTube", now(), now()))
        self.wake.set()
        return self.detail(job_id)

    def cancel(self, job_id):
        with self.catalog.db() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError("Tarefa não encontrada.")
            if row[0] not in ACTIVE and row[0] != "cancelled":
                raise ValueError("Esta tarefa já terminou.")
            conn.execute("UPDATE jobs SET cancel_requested=1,updated_at=? WHERE id=?", (now(), job_id))
            if row[0] == "queued":
                conn.execute("UPDATE jobs SET status='cancelled',stage='cancelled' WHERE id=?", (job_id,))
                conn.execute("UPDATE job_items SET status='cancelled',stage='cancelled',error_code='cancelled',updated_at=? WHERE job_id=? AND status='queued'", (now(), job_id))
        if self.active_job == job_id:
            self.pipeline.kill()
        self.wake.set()
        return self.detail(job_id)

    def retry(self, job_id):
        with self.catalog.db() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError("Tarefa não encontrada.")
            if row["status"] not in ("failed", "partial", "cancelled"):
                raise ValueError("Somente tarefas com falhas ou canceladas podem ser retomadas.")
            if conn.execute("SELECT count(*) FROM jobs WHERE status IN('queued','discovering','running')").fetchone()[0] >= 100:
                raise ValueError("Há 100 tarefas pendentes. Aguarde ou cancele algumas antes de retomar outra.")
            conn.execute("UPDATE job_items SET status='queued',stage='queued',error=NULL,error_code=NULL,updated_at=? WHERE job_id=? AND status IN('failed','cancelled')", (now(), job_id))
            conn.execute("UPDATE jobs SET status='queued',stage='queued',cancel_requested=0,error=NULL,error_code=NULL,updated_at=? WHERE id=?", (now(), job_id))
        self.wake.set()
        return self.detail(job_id)

    def loop(self):
        while not self.stop.is_set():
            try:
                if self.run_once():
                    continue
            except Exception:
                LOG.exception("Falha inesperada no worker de transcrição")
            self.wake.wait(1)
            self.wake.clear()

    def existing(self, video_id):
        with self.catalog.scan_lock:
            return self._existing_locked(video_id)

    def _existing_locked(self, video_id):
        with self.catalog.db() as conn:
            if conn.execute("SELECT 1 FROM videos WHERE id=?", (video_id,)).fetchone():
                return True
        target = self.catalog.source / video_id
        if target.is_symlink():
            raise JobError("storage_error", "Já existe um link simbólico para este vídeo. A origem foi preservada e não será sobrescrita.")
        if not target.exists():
            return False
        try:
            validate_artifacts(target, video_id)
            self.catalog.import_video(self.catalog.parse_video(video_id), self.catalog.fingerprint(video_id))
        except (JobError, OSError, ValueError, TypeError, KeyError, yaml.YAMLError, sqlite3.Error) as exc:
            raise JobError("storage_error", "Já existe uma pasta incompleta ou inválida para este vídeo. Confira os arquivos originais antes de tentar novamente; nada foi sobrescrito.") from exc
        return True

    def publish(self, job, item, folder):
        self.stage(job["id"], "publishing", item["id"])
        catalog, video_id = self.catalog, item["video_id"]
        validate_artifacts(folder, video_id)
        with catalog.scan_lock:
            self.check(job["id"])
            if self._existing_locked(video_id):
                return False
            target = catalog.source / video_id
            if not catalog.source.is_dir() or catalog.source.is_symlink():
                raise JobError("storage_error", "A pasta de transcrições não está disponível para escrita.")
            staged = catalog.source / (".catalog-publish-" + item["id"])
            if staged.is_symlink():
                raise JobError("storage_error", "A área de publicação não pode ser um link simbólico.")
            if staged.exists():
                if not staged.is_dir() or not shutil.rmtree.avoids_symlink_attacks:
                    raise JobError("storage_error", "A publicação anterior não pode ser retomada com segurança.")
                shutil.rmtree(staged)
            try:
                staged.mkdir()
                for filename in ("video.json", "transcript.json", "transcript.txt", "transcript.srt"):
                    origin = folder / filename
                    if origin.is_symlink():
                        raise JobError("invalid_artifacts", "Arquivo gerado inválido.")
                    shutil.copyfile(origin, staged / filename)
                self.check(job["id"])
                if target.exists() or target.is_symlink():
                    self._existing_locked(video_id)
                    return False
                os.rename(staged, target)
                catalog.import_video(catalog.parse_video(video_id), catalog.fingerprint(video_id))
            finally:
                if staged.is_dir() and not staged.is_symlink():
                    shutil.rmtree(staged)
        if catalog.thumbnail_manager:
            catalog.thumbnail_manager.enqueue_missing()
        try:
            shutil.rmtree(folder)
        except OSError:
            self.log(job["id"], "completed", "Transcrição salva. Alguns arquivos temporários não puderam ser removidos da área de trabalho.")
        return True

    def run_once(self):
        if not self.worker_lock.acquire(blocking=False):
            return False
        job_id = None
        try:
            with self.catalog.db() as conn:
                row = conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
                if not row:
                    return False
                job = dict(row)
                job_id = job["id"]
                conn.execute("UPDATE jobs SET status='discovering',stage='discovering',updated_at=? WHERE id=?", (now(), job_id))
            self.active_job = job_id
            self.check(job_id)
            if not job["discovered"]:
                self.stage(job_id, "discovering")
                title, entries = self.pipeline.discover(job)
                self.check(job_id)
                seen = set()
                with self.catalog.db() as conn:
                    for position, entry in enumerate(entries, 1):
                        entry = entry if isinstance(entry, dict) else {}
                        video_id = entry.get("id")
                        valid = isinstance(video_id, str) and bool(YOUTUBE_ID.fullmatch(video_id))
                        unavailable = not valid or entry.get("availability") in ("private", "premium_only", "subscriber_only", "needs_auth") or entry.get("title") in ("[Private video]", "[Deleted video]")
                        duplicate = valid and video_id in seen
                        status = "failed" if unavailable else "skipped" if duplicate else "queued"
                        code = "unavailable" if unavailable else "existing" if duplicate else None
                        conn.execute("INSERT INTO job_items VALUES(?,?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, job_id, position, video_id if valid else None, str(entry.get("title") or f"Vídeo indisponível (#{position})"), status, "failed" if unavailable else "completed" if duplicate else "queued", "Vídeo indisponível." if unavailable else None, code, now()))
                        if valid:
                            seen.add(video_id)
                    conn.execute("UPDATE jobs SET title=?,total=?,discovered=1,updated_at=? WHERE id=?", (title, len(entries), now(), job_id))
            with self.catalog.db() as conn:
                conn.execute("UPDATE jobs SET status='running',updated_at=? WHERE id=?", (now(), job_id))
                items = [dict(r) for r in conn.execute("SELECT * FROM job_items WHERE job_id=? AND status='queued' ORDER BY position", (job_id,))]
            for item in items:
                self.check(job_id)
                code = error = None
                existing_error, already_exists = None, False
                if item["video_id"]:
                    try:
                        already_exists = self.existing(item["video_id"])
                    except JobError as exc:
                        existing_error = exc
                if not item["video_id"]:
                    status, code, error = "failed", "unavailable", "Este item da playlist não tem um vídeo acessível."
                elif existing_error:
                    status, code, error = "failed", existing_error.code, str(existing_error)
                elif already_exists:
                    status, code = "skipped", "existing"
                else:
                    with self.catalog.db() as conn:
                        conn.execute("UPDATE job_items SET status='running',updated_at=? WHERE id=?", (now(), item["id"]))
                    try:
                        folder = self.pipeline.transcribe(job, item)
                        self.check(job_id)
                        published = self.publish(job, item, folder)
                        status, code = ("completed", None) if published else ("skipped", "existing")
                    except JobError as exc:
                        status, code, error = "failed", exc.code, str(exc)
                    except (OSError, ValueError, sqlite3.Error) as exc:
                        status, code, error = "failed", "storage_error", "Não foi possível salvar este vídeo. Confira espaço e permissões de armazenamento."
                        LOG.warning("Item %s: %s", item["id"], type(exc).__name__)
                with self.catalog.db() as conn:
                    conn.execute("UPDATE job_items SET status=?,stage=?,error=?,error_code=?,updated_at=? WHERE id=?", (status, "failed" if status == "failed" else "completed", error, code, now(), item["id"]))
                    if code in ("model_download_failed", "model_checksum"):
                        conn.execute("UPDATE job_items SET status='failed',stage='failed',error=?,error_code=?,updated_at=? WHERE job_id=? AND status='queued'", (error, code, now(), job_id))
                if error:
                    self.log(job_id, "failed", error)
                if code in ("model_download_failed", "model_checksum"):
                    break
            self.check(job_id)
            with self.catalog.db() as conn:
                counts = dict(conn.execute("SELECT status,count(*) FROM job_items WHERE job_id=? GROUP BY status", (job_id,)))
                failed = counts.get("failed", 0)
                successful = counts.get("completed", 0) + counts.get("skipped", 0)
                status = "partial" if failed and successful else "failed" if failed else "completed"
                conn.execute("UPDATE jobs SET status=?,stage=?,updated_at=?,error=?,error_code=? WHERE id=?", (status, "failed" if failed else "completed", now(), "Um ou mais vídeos não puderam ser concluídos." if failed else None, "items_failed" if failed else None, job_id))
            return True
        except JobInterrupted:
            with self.catalog.db() as conn:
                conn.execute("UPDATE jobs SET status='queued',stage='queued',updated_at=? WHERE id=?", (now(), job_id))
                conn.execute("UPDATE job_items SET status='queued',stage='queued',updated_at=? WHERE job_id=? AND status='running'", (now(), job_id))
            return True
        except JobCancelled:
            with self.catalog.db() as conn:
                conn.execute("UPDATE jobs SET status='cancelled',stage='cancelled',error_code='cancelled',updated_at=? WHERE id=?", (now(), job_id))
                conn.execute("UPDATE job_items SET status='cancelled',stage='cancelled',error_code='cancelled',updated_at=? WHERE job_id=? AND status IN('queued','running')", (now(), job_id))
            return True
        except Exception as exc:
            if job_id:
                code = exc.code if isinstance(exc, JobError) else "discovery_failed"
                message = str(exc) if isinstance(exc, JobError) else "Não foi possível preparar esta tarefa. Consulte o registro e tente novamente."
                with self.catalog.db() as conn:
                    conn.execute("UPDATE jobs SET status='failed',stage='failed',error=?,error_code=?,updated_at=? WHERE id=?", (message, code, now(), job_id))
                    conn.execute("UPDATE job_items SET status='failed',stage='failed',error=?,error_code=?,updated_at=? WHERE job_id=? AND status='running'", (message, code, now(), job_id))
                self.log(job_id, "failed", message)
            LOG.exception("Falha na tarefa de transcrição")
            return True
        finally:
            self.active_job = None
            self.worker_lock.release()
