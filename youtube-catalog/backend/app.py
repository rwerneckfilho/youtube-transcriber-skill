"""One local ASGI service for the API, thumbnails and compiled React app."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from .catalog import Catalog, FILES, VIDEO_ID, PurgeConflict, safe_file
from .thumbnails import FALLBACK, TYPES, ThumbnailManager
from .jobs import JobError, JobQueue


class CategoryName(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class CategoryAssignment(BaseModel):
    category_ids: list[str] = Field(max_length=100)


class PermanentDeletion(BaseModel):
    confirmation: str = Field(min_length=1, max_length=80)


class JobSubmission(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    language: Literal["auto", "pt", "en", "es"] = "auto"
    kind: Literal["auto", "video", "playlist"] = "auto"


def create_app(source_dir=None, data_dir=None, static_dir=None, download_thumbnails=None, scan_interval=None, initial_scan=True, start_jobs=None, work_dir=None, model_dir=None) -> FastAPI:
    source = Path(source_dir or os.getenv("TRANSCRIPTS_DIR", "/transcripts"))
    data = Path(data_dir or os.getenv("DATA_DIR", "/data"))
    static = Path(static_dir or os.getenv("STATIC_DIR", "/app/static")).resolve()
    enabled = download_thumbnails if download_thumbnails is not None else os.getenv("DOWNLOAD_THUMBNAILS", "true").lower() not in ("0", "false", "no")
    interval = max(1, float(scan_interval if scan_interval is not None else os.getenv("SCAN_INTERVAL_SECONDS", "60")))
    jobs_enabled = start_jobs if start_jobs is not None else os.getenv("JOB_WORKER_ENABLED", "true").lower() not in ("false", "0", "no")

    @asynccontextmanager
    async def lifespan(app):
        catalog = Catalog(source, data)
        thumbnails = ThumbnailManager(catalog, enabled)
        catalog.thumbnail_manager = thumbnails
        app.state.catalog = catalog
        app.state.thumbnails = thumbnails
        jobs = JobQueue(catalog, Path(work_dir or os.getenv("WORK_DIR", str(data / "work"))), Path(model_dir or os.getenv("MODEL_DIR", str(data / "models"))))
        app.state.jobs = jobs
        if initial_scan:
            catalog.request_scan()
        if jobs_enabled:
            jobs.start()

        async def periodic():
            while True:
                await asyncio.sleep(interval)
                catalog.request_scan()

        timer = asyncio.create_task(periodic())
        try:
            yield
        finally:
            timer.cancel()
            try:
                await timer
            except asyncio.CancelledError:
                pass
            await asyncio.to_thread(jobs.close)
            catalog.stop.set()
            if catalog.scan_thread:
                await asyncio.to_thread(catalog.scan_thread.join)
            await asyncio.to_thread(thumbnails.close)

    app = FastAPI(title="rw-ai · Catálogo de transcrições", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def local_browser_boundary(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if (origin and origin.rstrip("/") != str(request.base_url).rstrip("/")) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Abra o aplicativo local para realizar essa alteração."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({"detail": "Dados inválidos. Confira os campos e os limites da solicitação."}, status_code=422)

    def catalog() -> Catalog:
        return app.state.catalog

    def require_video(video_id: str, include_deleted=False):
        if not VIDEO_ID.fullmatch(video_id):
            raise HTTPException(404, "Vídeo não encontrado.")
        with catalog().db() as conn:
            row = conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        if not row or (row["deleted_at"] is not None and not include_deleted):
            raise HTTPException(404, "Vídeo não encontrado.")
        return row

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/jobs")
    def jobs(page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100)):
        return app.state.jobs.list(page, page_size)

    @app.post("/api/jobs", status_code=202)
    def submit_job(body: JobSubmission):
        try:
            return app.state.jobs.enqueue(body.url, body.language, body.kind)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc), "error_code": "invalid_url"}, status_code=422)
        except JobError as exc:
            return JSONResponse({"detail": str(exc), "error_code": exc.code}, status_code=429)

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str):
        result = app.state.jobs.detail(job_id)
        if result is None:
            raise HTTPException(404, "Tarefa não encontrada.")
        return result

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        try:
            return app.state.jobs.cancel(job_id)
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str):
        try:
            return app.state.jobs.retry(job_id)
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/stats")
    def stats():
        return catalog().stats()

    @app.get("/api/categories")
    def categories():
        return catalog().categories()

    @app.post("/api/categories", status_code=201)
    def create_category(body: CategoryName):
        try:
            return catalog().save_category(body.name)
        except ValueError as exc:
            raise HTTPException(409 if "Já existe" in str(exc) else 422, str(exc)) from exc

    @app.patch("/api/categories/{category_id}")
    def rename_category(category_id: str, body: CategoryName):
        try:
            return catalog().save_category(body.name, category_id)
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc
        except ValueError as exc:
            raise HTTPException(409 if "Já existe" in str(exc) else 422, str(exc)) from exc

    @app.get("/api/channels")
    def channels():
        return catalog().channels()

    @app.get("/api/videos")
    def videos(q: str = Query("", max_length=300), channel: str = Query("", max_length=200), category: str = Query("", max_length=200), language: str = Query("", max_length=30), sort: Literal["", "added", "published", "title", "relevance"] = "", page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100), deleted: bool = False):
        return catalog().list_videos(q, channel, category, language, sort, page, page_size, deleted)

    @app.delete("/api/videos/{video_id}")
    def delete_video(video_id: str):
        require_video(video_id, include_deleted=True)
        try:
            catalog().set_deleted(video_id, True)
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc
        return {"id": video_id, "deleted": True}

    @app.post("/api/videos/{video_id}/restore")
    def restore_video(video_id: str):
        require_video(video_id, include_deleted=True)
        try:
            catalog().set_deleted(video_id, False)
        except PurgeConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc
        return catalog().detail(video_id)

    @app.delete("/api/videos/{video_id}/permanent")
    def permanent_delete(video_id: str, body: PermanentDeletion):
        require_video(video_id, include_deleted=True)
        try:
            catalog().permanent_delete(video_id, body.confirmation)
        except PurgeConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc
        except OSError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"id": video_id, "permanently_deleted": True}

    @app.get("/api/videos/{video_id}")
    def video_detail(video_id: str):
        require_video(video_id)
        return catalog().detail(video_id)

    @app.get("/api/videos/{video_id}/segments")
    def segments(video_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=300), q: str = Query("", max_length=300)):
        require_video(video_id)
        return catalog().segments(video_id, offset, limit, q)

    @app.put("/api/videos/{video_id}/categories")
    def assign(video_id: str, body: CategoryAssignment):
        require_video(video_id)
        try:
            catalog().assign_categories(video_id, body.category_ids)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return catalog().detail(video_id)

    @app.delete("/api/videos/{video_id}/categories")
    def reset_categories(video_id: str):
        require_video(video_id)
        catalog().assign_categories(video_id, None)
        return catalog().detail(video_id)

    @app.post("/api/scan", status_code=202)
    def scan():
        catalog().request_scan()
        return {"scanning": True}

    @app.get("/api/videos/{video_id}/thumbnail")
    def thumbnail(video_id: str):
        require_video(video_id, include_deleted=True)
        cached = app.state.thumbnails.cached(video_id)
        if cached:
            media_type = next(key for key, suffix in TYPES.items() if suffix == cached.suffix)
            return FileResponse(cached, media_type=media_type, headers={"Cache-Control": "public, max-age=3600"})
        return Response(FALLBACK, media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})

    @app.get("/api/videos/{video_id}/files/{filename}")
    def download(video_id: str, filename: str):
        video = require_video(video_id)
        if not video["available"] or filename not in FILES:
            raise HTTPException(404, "Arquivo não disponível.")
        try:
            path = safe_file(catalog().source, video_id, filename)
        except ValueError as exc:
            raise HTTPException(404, "Arquivo não disponível.") from exc
        if not path.is_file():
            raise HTTPException(404, "Arquivo não disponível.")
        return FileResponse(path, filename=f"{video_id}-{filename}", media_type="application/octet-stream")

    @app.get("/{path:path}")
    def spa(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Rota não encontrada.")
        requested = (static / path).resolve()
        if path and requested.is_relative_to(static) and requested.is_file():
            return FileResponse(requested)
        if Path(path).suffix:
            raise HTTPException(404, "Arquivo não encontrado.")
        index = static / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return Response("rw-ai: interface do catálogo ainda não compilada.", status_code=503, media_type="text/plain")

    return app


app = create_app()
