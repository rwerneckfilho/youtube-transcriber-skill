"""Bounded, optional thumbnail downloads; no remote URLs reach the interface."""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import httpx

LOG = logging.getLogger(__name__)
MAX_BYTES = 2 * 1024 * 1024
FALLBACK = """<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
<rect width="640" height="360" fill="#0b253a"/>
<text x="320" y="181" text-anchor="middle" font-size="56" font-family="Arial,sans-serif" fill="#ffffff"><tspan font-weight="700">rw</tspan><tspan fill="#6aa2ff"> / </tspan><tspan font-weight="600">ai</tspan></text>
<text x="320" y="239" text-anchor="middle" font-size="17" font-family="Arial,sans-serif" fill="#d8e2ed">TRANSCRIÇÃO LOCAL</text></svg>"""
TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def valid_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        return len(url) < 4096 and parsed.scheme == "https" and (hostname == "ytimg.com" or hostname.endswith(".ytimg.com")) and parsed.port in (None, 443) and parsed.username is None and parsed.password is None
    except (TypeError, ValueError):
        return False


def valid_image(data: bytes, content_type: str) -> bool:
    if content_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
    if content_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n") and b"IEND" in data[-16:]
    return content_type == "image/webp" and data.startswith(b"RIFF") and data[8:12] == b"WEBP"


class ThumbnailManager:
    def __init__(self, catalog, enabled: bool, transport=None):
        self.catalog = catalog
        self.enabled = enabled
        self.directory = catalog.data / "thumbnails"
        self.directory.mkdir(exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="thumbnail")
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.pending = set()
        self.futures = {}
        self.deleting = set()
        self.retry_after = {}
        self.transport = transport
        self.closed = False

    def cached(self, video_id: str) -> Path | None:
        for extension in TYPES.values():
            path = self.directory / (video_id + extension)
            if path.is_file() and path.resolve().is_relative_to(self.directory.resolve()):
                return path
        return None

    def enqueue_missing(self):
        if not self.enabled or self.closed:
            return
        with self.catalog.db() as conn:
            rows = list(conn.execute("SELECT id,thumbnail_source FROM videos WHERE available=1 AND deleted_at IS NULL"))
        for row in rows:
            video_id, url = row["id"], row["thumbnail_source"]
            if self.cached(video_id) or not valid_url(url):
                continue
            with self.lock:
                if self.closed or video_id in self.deleting or video_id in self.pending or self.retry_after.get(video_id, 0) > time.monotonic():
                    continue
                self.pending.add(video_id)
                self.futures[video_id] = self.executor.submit(self.download, video_id, url)

    def download(self, video_id: str, url: str):
        temporary = self.directory / (video_id + ".part")
        try:
            with self.lock:
                if not valid_url(url) or self.closed or video_id in self.deleting:
                    return
            start = time.monotonic()
            with httpx.Client(timeout=httpx.Timeout(8, connect=5), follow_redirects=False, trust_env=False, transport=self.transport) as client:
                with client.stream("GET", url, headers={"User-Agent": "YouTube-Catalog-Local/1.0", "Accept": "image/jpeg,image/png,image/webp"}) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";")[0].lower()
                    if content_type not in TYPES:
                        raise ValueError("Tipo de imagem não permitido.")
                    if int(response.headers.get("content-length", "0")) > MAX_BYTES:
                        raise ValueError("Capa excede 2 MB.")
                    chunks, size = [], 0
                    for chunk in response.iter_bytes(16384):
                        size += len(chunk)
                        if self.closed or size > MAX_BYTES or time.monotonic() - start > 15:
                            raise ValueError("Download de capa interrompido.")
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    if not valid_image(data, content_type):
                        raise ValueError("Conteúdo de imagem inválido.")
            with self.lock:
                if self.closed or video_id in self.deleting:
                    return
                temporary.write_bytes(data)
                os.replace(temporary, self.directory / (video_id + TYPES[content_type]))
        except Exception as exc:
            LOG.info("Capa local substituta para %s (%s)", video_id, type(exc).__name__)
            with self.lock:
                self.retry_after[video_id] = time.monotonic() + 300
        finally:
            try:
                temporary.unlink(missing_ok=True)
            finally:
                with self.condition:
                    self.pending.discard(video_id)
                    self.futures.pop(video_id, None)
                    self.condition.notify_all()

    def begin_delete(self, video_id: str):
        """Cancel queued work and wait for a running download before removing files."""
        deadline = time.monotonic() + 25
        with self.condition:
            self.deleting.add(video_id)
            future = self.futures.get(video_id)
            if future is not None and future.cancel():
                self.futures.pop(video_id, None)
                self.pending.discard(video_id)
            while video_id in self.pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Ainda há um download de capa encerrando. Tente concluir novamente.")
                self.condition.wait(remaining)

    def remove_files(self, video_id: str):
        if self.directory.is_symlink():
            raise OSError("A pasta de capas não pode ser um link simbólico.")
        for suffix in (*TYPES.values(), ".part"):
            (self.directory / (video_id + suffix)).unlink(missing_ok=True)

    def finish_delete(self, video_id: str):
        with self.lock:
            self.retry_after.pop(video_id, None)
            self.deleting.discard(video_id)

    def close(self):
        with self.lock:
            self.closed = True
        self.executor.shutdown(wait=True, cancel_futures=True)
