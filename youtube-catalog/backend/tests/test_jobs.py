"""Queue tests use temporary collections and fake subprocess/network pipelines."""
import hashlib
import json
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.jobs import JobCancelled, JobError, JobInterrupted, JobQueue, Pipeline, validate_url
from backend.tests.conftest import make_video

VID = "Abcdefghijk"
VID2 = "Zbcdefghijk"
URL = "https://www.youtube.com/watch?v=" + VID
PLAYLIST = "https://www.youtube.com/playlist?list=PLabcdefghijklmnop"


class FakePipeline:
    def __init__(self, queue):
        self.queue = queue
        self.entries = [{"id": VID, "title": "A video"}]
        self.fail = set()
        self.calls = []
        self.discoveries = 0
        self.before_transcribe = None
        self.discovery_error = None
        self.killed = False

    def kill(self):
        self.killed = True

    def discover(self, job):
        self.discoveries += 1
        if self.discovery_error:
            raise self.discovery_error
        return "Complete playlist", self.entries

    def transcribe(self, job, item):
        self.calls.append(item["video_id"])
        if self.before_transcribe:
            self.before_transcribe(job, item)
        if item["video_id"] in self.fail:
            raise JobError("download_failed", "Simulated unavailable download")
        return make_video(self.queue.work_path(job["id"]), item["video_id"], category=None)


@pytest.fixture
def queue(catalog, tmp_path):
    return JobQueue(catalog, tmp_path / "work", tmp_path / "models", pipeline_factory=FakePipeline)


@pytest.fixture
def jobs_client(collection, tmp_path):
    app = create_app(source_dir=collection, data_dir=tmp_path / "api-data", download_thumbnails=False, initial_scan=False, start_jobs=False)
    with TestClient(app) as client:
        app.state.jobs.pipeline = FakePipeline(app.state.jobs)
        yield client


@pytest.mark.parametrize("url", ["http://youtube.com/watch?v=" + VID, "https://evil.example/watch?v=" + VID, "https://youtube.com.evil.example/watch?v=" + VID, "https://user@youtube.com/watch?v=" + VID, "https://youtube.com:443/watch?v=" + VID, "https://youtube.com/@channel", "file:///tmp/anything", "https://youtube.com/watch?v=../secret", "https://youtu.be/abc;touch", "https://youtube.com/playlist?list=bad", "https://youtube.com/watch?v=" + VID + "&list=bad"])
def test_strict_youtube_url_validation(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_url_canonicalization_explicit_kind_and_no_shell_syntax():
    assert validate_url("https://youtu.be/" + VID + "?t=23") == (URL, "video")
    assert validate_url("https://www.youtube.com/shorts/" + VID) == (URL, "video")
    mixed = URL + "&list=PLabcdefghijklmnop&index=2"
    assert validate_url(mixed) == (PLAYLIST, "playlist")
    assert validate_url(mixed, "video") == (URL, "video")
    with pytest.raises(ValueError):
        validate_url(URL, "playlist")
    assert validate_url(URL + "&ignored=$(anything)") == (URL, "video")


def test_api_contract_bounds_dedup_and_history(jobs_client):
    client = jobs_client
    submitted = client.post("/api/jobs", json={"url": URL, "language": "es", "kind": "auto"})
    assert submitted.status_code == 202
    job = submitted.json()
    assert job["language"] == "es" and job["kind"] == "video" and job["status"] == "queued"
    assert job["items"] == [] and job["logs"] == []
    duplicate = client.post("/api/jobs", json={"url": "https://youtu.be/" + VID, "language": "es"}).json()
    assert duplicate["id"] == job["id"]
    assert client.get("/api/jobs").json()["total"] == 1
    assert client.get("/api/jobs/" + job["id"]).status_code == 200
    assert client.get("/api/jobs/missing").status_code == 404
    assert client.get("/api/jobs?page_size=101").status_code == 422
    assert client.post("/api/jobs", json={"url": URL, "language": "de"}).status_code == 422
    assert client.post("/api/jobs", json={"url": "x" * 2049}).status_code == 422
    invalid = client.post("/api/jobs", json={"url": "https://localhost/a"})
    assert invalid.status_code == 422 and invalid.json()["error_code"] == "invalid_url"
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 409
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/jobs/{job['id']}/retry").json()["status"] == "queued"
    client.app.state.jobs.run_once()
    assert client.get("/api/jobs/" + job["id"]).json()["status"] == "completed"
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 409


def test_every_playlist_item_is_recorded_without_truncation_and_failures_continue(queue):
    ids = [f"v{index:010d}" for index in range(125)]
    queue.pipeline.entries = [{"id": video_id, "title": str(index)} for index, video_id in enumerate(ids)]
    queue.pipeline.entries.extend([None, {"id": VID, "title": "[Private video]"}, {"id": ids[0], "title": "duplicate"}])
    queue.pipeline.fail = {ids[10], ids[80]}
    job = queue.enqueue(PLAYLIST, "pt")
    assert queue.run_once()
    done = queue.detail(job["id"])
    assert done["total"] == 128 and len(done["items"]) == 128
    assert done["completed"] == 123 and done["failed"] == 4 and done["skipped"] == 1
    assert done["status"] == "partial"
    assert queue.pipeline.calls == ids
    assert queue.catalog.stats()["videos"] == 123
    queue.pipeline.fail.clear()
    queue.retry(job["id"])
    queue.run_once()
    retried = queue.detail(job["id"])
    assert retried["completed"] == 126 and retried["failed"] == 1
    assert queue.pipeline.discoveries == 1
    assert len(queue.pipeline.calls) == 128
    assert queue.catalog.stats()["videos"] == 126


def test_existing_trash_and_purge_entries_are_never_overwritten(queue, collection):
    folder = make_video(collection, VID)
    make_video(collection, VID2)
    queue.catalog.scan()
    queue.catalog.set_deleted(VID, True)
    with queue.catalog.db() as conn:
        conn.execute("INSERT INTO purge_jobs(video_id,quarantine,phase,created_at) VALUES(?,?,'missing','now')", (VID2, ".catalog-purge-" + "1" * 32))
    originals = {p.name: p.read_bytes() for p in folder.iterdir()}
    queue.pipeline.entries = [{"id": VID}, {"id": VID2}]
    job = queue.enqueue(PLAYLIST)
    queue.run_once()
    result = queue.detail(job["id"])
    assert result["skipped"] == 2 and result["completed"] == 0
    assert not queue.pipeline.calls
    assert queue.catalog.list_videos(deleted=True)["total"] == 1
    assert originals == {p.name: p.read_bytes() for p in folder.iterdir()}


def test_incomplete_existing_folder_fails_explicitly_and_playlist_continues(queue, collection):
    partial = collection / VID
    partial.mkdir()
    (partial / "source.webm").write_bytes(b"preserved unfinished original")
    queue.pipeline.entries = [{"id": VID}, {"id": VID2}]
    job = queue.enqueue(PLAYLIST)
    queue.run_once()
    result = queue.detail(job["id"])
    assert result["status"] == "partial"
    assert result["items"][0]["status"] == "failed" and result["items"][0]["error_code"] == "storage_error"
    assert result["completed"] == 1 and result["skipped"] == 0
    assert queue.pipeline.calls == [VID2]
    assert (partial / "source.webm").read_bytes() == b"preserved unfinished original"


def test_published_but_not_imported_folder_is_recovered_without_retranscription(queue, collection):
    make_video(collection, VID)
    assert queue.catalog.stats()["videos"] == 0
    job = queue.enqueue(URL)
    queue.run_once()
    assert queue.detail(job["id"])["skipped"] == 1
    assert queue.catalog.stats()["videos"] == 1
    assert not queue.pipeline.calls


def test_queued_cancel_persists_restart_and_retry(queue):
    job = queue.enqueue(URL)
    queue.cancel(job["id"])
    restarted = JobQueue(queue.catalog, queue.work_dir, queue.model_dir, FakePipeline)
    assert restarted.detail(job["id"])["status"] == "cancelled"
    assert not restarted.run_once()
    restarted.retry(job["id"])
    restarted.run_once()
    assert restarted.detail(job["id"])["status"] == "completed"


def test_running_cancel_keeps_finished_items_and_retry_only_unfinished(queue):
    queue.pipeline.entries = [{"id": VID}, {"id": VID2}]
    job = queue.enqueue(PLAYLIST)

    def cancel_second(job, item):
        if item["video_id"] == VID2:
            queue.cancel(job["id"])
            queue.check(job["id"])

    queue.pipeline.before_transcribe = cancel_second
    queue.run_once()
    result = queue.detail(job["id"])
    assert result["status"] == "cancelled" and result["completed"] == 1
    assert result["items"][1]["status"] == "cancelled"
    queue.pipeline.before_transcribe = None
    queue.retry(job["id"])
    queue.run_once()
    assert queue.detail(job["id"])["completed"] == 2
    assert queue.pipeline.calls == [VID, VID2, VID2]


def test_shutdown_requeues_and_restart_does_not_duplicate_completed_items(queue):
    queue.pipeline.entries = [{"id": VID}, {"id": VID2}]
    job = queue.enqueue(PLAYLIST)

    def shutdown_second(job, item):
        if item["video_id"] == VID2:
            queue.stop.set()
            queue.check(job["id"])

    queue.pipeline.before_transcribe = shutdown_second
    queue.run_once()
    assert queue.detail(job["id"])["status"] == "queued"
    restarted = JobQueue(queue.catalog, queue.work_dir, queue.model_dir, FakePipeline)
    restarted.run_once()
    assert restarted.detail(job["id"])["completed"] == 2
    assert restarted.pipeline.calls == [VID2]
    assert restarted.pipeline.discoveries == 0
    assert restarted.catalog.stats()["videos"] == 2


def test_hard_crash_running_state_recovers_and_cancel_stays_cancelled(queue):
    job = queue.enqueue(URL)
    with queue.catalog.db() as conn:
        conn.execute("UPDATE jobs SET status='running',stage='transcribing' WHERE id=?", (job["id"],))
    restarted = JobQueue(queue.catalog, queue.work_dir, queue.model_dir, FakePipeline)
    assert restarted.detail(job["id"])["status"] == "queued"
    with queue.catalog.db() as conn:
        conn.execute("UPDATE jobs SET status='running',cancel_requested=1 WHERE id=?", (job["id"],))
    cancelled = JobQueue(queue.catalog, queue.work_dir, queue.model_dir, FakePipeline)
    assert cancelled.detail(job["id"])["status"] == "cancelled"


def test_discovery_failure_is_explicit_and_retry_re_discovers(queue):
    job = queue.enqueue(PLAYLIST)
    queue.pipeline.discovery_error = JobError("discovery_failed", "Private playlist")
    queue.run_once()
    assert queue.detail(job["id"])["error_code"] == "discovery_failed"
    queue.pipeline.discovery_error = None
    queue.retry(job["id"])
    queue.run_once()
    assert queue.detail(job["id"])["status"] == "completed"
    assert queue.pipeline.discoveries == 2


def test_worker_is_serial_and_another_enqueue_is_nonblocking(queue):
    entered, release = threading.Event(), threading.Event()

    def pause(job, item):
        entered.set()
        assert release.wait(5)

    queue.pipeline.before_transcribe = pause
    queue.enqueue(URL)
    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(queue.run_once)
        assert entered.wait(5)
        assert not queue.run_once()
        queued = queue.enqueue("https://youtu.be/" + VID2)
        assert queued["status"] == "queued"
        release.set()
        assert running.result(timeout=5)


def test_incomplete_publication_is_rebuilt_and_invalid_artifacts_never_published(queue, monkeypatch):
    job = queue.enqueue(URL)
    real_transcribe = queue.pipeline.transcribe

    def partial(job, item):
        folder = real_transcribe(job, item)
        staged = queue.catalog.source / (".catalog-publish-" + item["id"])
        staged.mkdir()
        (staged / "transcript.json").write_text("incomplete")
        return folder

    monkeypatch.setattr(queue.pipeline, "transcribe", partial)
    queue.run_once()
    assert queue.detail(job["id"])["completed"] == 1
    assert not list(queue.catalog.source.glob(".catalog-publish-*"))
    second = queue.enqueue("https://youtu.be/" + VID2)
    queue.pipeline.entries = [{"id": VID2}]

    def invalid(job, item):
        folder = real_transcribe(job, item)
        (folder / "transcript.json").write_text("invalid json")
        return folder

    monkeypatch.setattr(queue.pipeline, "transcribe", invalid)
    queue.run_once()
    assert queue.detail(second["id"])["items"][0]["error_code"] == "invalid_artifacts"
    assert not (queue.catalog.source / VID2).exists()


def test_pipeline_arguments_language_outputs_and_resume(queue, monkeypatch):
    pipeline = Pipeline(queue)
    job = queue.enqueue(URL, "es")
    item = {"id": "1" * 32, "video_id": VID}
    commands = []
    model = queue.model_dir / "tiny-model"
    model.write_bytes(b"fixture")

    def simulated_run(job_id, stage, command, output=None, timeout=None):
        commands.append((stage, command))
        folder = queue.work_path(job_id) / item["id"]
        if output:
            output.write_text(json.dumps({"id": VID, "title": "A title", "duration": 1, "language": "es"}))
        elif command[0] == "yt-dlp":
            (folder / "source.webm").write_bytes(b"audio")
        elif command[0] == "ffmpeg":
            (folder / "audio.wav").write_bytes(b"wav")
        else:
            (folder / "transcript.json").write_text(json.dumps({"transcription": [{"text": "Hola", "offsets": {"from": 0, "to": 1000}}]}))
            (folder / "transcript.txt").write_text("Hola")
            (folder / "transcript.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHola")

    monkeypatch.setattr(pipeline, "run", simulated_run)
    monkeypatch.setattr(pipeline, "ensure_model", lambda job_id: model)
    folder = pipeline.transcribe(job, item)
    whisper = commands[-1][1]
    assert whisper[whisper.index("--language") + 1] == "es"
    assert {"--output-txt", "--output-srt", "--output-json"}.issubset(whisper)
    assert commands[0][1][-2:] == ["--", URL]
    assert "--ignore-config" in commands[0][1]
    assert pipeline.transcribe(job, item) == folder and len(commands) == 4


def test_pipeline_rejects_live_before_audio_download(queue, monkeypatch):
    pipeline = Pipeline(queue)
    job = queue.enqueue(URL)
    calls = []

    def live(job_id, stage, command, output=None, timeout=None):
        calls.append(command)
        output.write_text(json.dumps({"id": VID, "title": "Live", "is_live": True}))

    monkeypatch.setattr(pipeline, "run", live)
    with pytest.raises(JobError) as raised:
        pipeline.transcribe(job, {"id": "2" * 32, "video_id": VID})
    assert raised.value.code == "live_unsupported" and len(calls) == 1


def test_model_checksum_download_cache_and_bad_model(queue, monkeypatch):
    import backend.jobs as module
    payload = b"fixture multilingual model"
    monkeypatch.setattr(module, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())
    job = queue.enqueue(URL)
    real_client = httpx.Client
    calls = []

    def respond(request):
        calls.append(request.url)
        return httpx.Response(200, content=payload)

    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    pipeline = Pipeline(queue)
    model = pipeline.ensure_model(job["id"])
    assert model.read_bytes() == payload
    assert pipeline.ensure_model(job["id"]) == model and len(calls) == 1
    model.write_bytes(b"corrupt model")
    with pytest.raises(JobError) as raised:
        pipeline.ensure_model(job["id"])
    assert raised.value.code == "model_checksum"


def test_model_partial_completed_before_interruption_uses_hash_without_network(queue, monkeypatch):
    import backend.jobs as module
    payload = b"complete partial model"
    monkeypatch.setattr(module, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())
    (queue.model_dir / (module.MODEL_NAME + ".part")).write_bytes(payload)
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: pytest.fail("Unexpected network"))
    job = queue.enqueue(URL)
    assert Pipeline(queue).ensure_model(job["id"]).read_bytes() == payload


def test_real_subprocess_is_cancelled_with_its_process_group(queue, tmp_path):
    pipeline = Pipeline(queue)
    queue.pipeline = pipeline
    job = queue.enqueue(URL)
    queue.active_job = job["id"]
    pidfile = tmp_path / "child.pid"
    command = [sys.executable, "-c", "import subprocess,time,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); open(sys.argv[1],'w').write(str(p.pid)); time.sleep(30)", str(pidfile)]
    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(pipeline.run, job["id"], "transcribing", command)
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pidfile.exists()
        queue.cancel(job["id"])
        with pytest.raises(JobCancelled):
            running.result(timeout=5)
    assert pipeline.process is None
