"""Playlist discovery uses fake metadata; all transcription/source writes are isolated."""
import json
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.jobs import JobQueue, Pipeline
from backend.tests.conftest import make_video
from backend.tests.test_jobs import FakePipeline, VID, VID2, PLAYLIST
from backend.watchers import PlaylistScanner, PlaylistWatchers, WatchError, WatchInterrupted


class Scanner:
    entries = None
    error = None
    callback = None

    def __init__(self):
        self.entries = [{"id": VID, "title": "First video"}]
        self.calls = []

    def scan(self, url):
        self.calls.append(url)
        if self.callback:
            self.callback()
        if self.error:
            raise self.error
        return "Test playlist", self.entries


@pytest.fixture
def watch(catalog, tmp_path):
    queue = JobQueue(catalog, tmp_path / "work", tmp_path / "models", pipeline_factory=FakePipeline)
    clock = [datetime(2026, 9, 6, tzinfo=timezone.utc)]
    result = PlaylistWatchers(catalog, queue, Scanner(), clock=lambda: clock[0])
    result.test_clock = clock
    return result


def source(watch):
    return watch.list()["items"][0]


def count(watch, table):
    assert table in ("jobs", "job_items", "videos", "watch_seen")
    with watch.catalog.db() as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_initial_import_queue_handoff_and_incremental_dedup(watch):
    watch.create(PLAYLIST)
    assert watch.run_once()
    assert source(watch)["queued"] == 1
    assert not watch.run_once()  # Not due yet.
    watch.jobs.run_once()
    assert watch.jobs.pipeline.calls == [VID]
    assert watch.jobs.pipeline.discoveries == 0  # Never rediscover/import the whole playlist.
    assert source(watch)["recent"][0]["processing_status"] == "completed"
    watch.scanner.entries = [{"id": VID2}, {"id": VID}, {"id": VID2}]
    watch.check_now(source(watch)["id"])
    watch.run_once()
    watch.jobs.run_once()
    assert watch.jobs.pipeline.calls == [VID, VID2]
    # Removal, reorder and return must not cause another transcription.
    for entries in ([], [{"id": VID2}, {"id": VID}]):
        watch.scanner.entries = entries
        watch.check_now(source(watch)["id"])
        watch.run_once()
    assert count(watch, "jobs") == 2
    assert count(watch, "videos") == 2


@pytest.mark.parametrize("empty", [False, True])
def test_future_only_first_successful_baseline_including_empty(watch, empty):
    watch.create(PLAYLIST, initial_mode="new")
    watch.scanner.error = RuntimeError("Network failure")
    watch.run_once()
    assert not source(watch)["initialized"]
    watch.scanner.error = None
    if empty:
        watch.scanner.entries = []
    watch.check_now(source(watch)["id"])
    watch.run_once()
    assert source(watch)["initialized"] and count(watch, "jobs") == 0
    watch.scanner.entries = [{"id": VID}, {"id": VID2}]
    watch.check_now(source(watch)["id"])
    watch.run_once()
    assert source(watch)["queued"] == (2 if empty else 1)


def test_pause_resume_partial_settings_due_time_and_restart(watch):
    identifier = watch.create(PLAYLIST, name="Research", language="es", interval_minutes=60)["id"]
    watch.update(identifier, enabled=False)
    assert not watch.run_once()
    assert source(watch)["name"] == "Research" and source(watch)["language"] == "es"
    with pytest.raises(WatchError, match="Retome"):
        watch.check_now(identifier)
    watch.update(identifier, enabled=True)
    watch.run_once()
    watch.test_clock[0] += timedelta(minutes=59)
    assert not watch.run_once()
    watch.test_clock[0] += timedelta(minutes=1)
    assert watch.run_once()
    with watch.catalog.db() as conn:
        conn.execute("UPDATE watch_sources SET status='checking'")
    restarted = PlaylistWatchers(watch.catalog, watch.jobs, watch.scanner, clock=watch.clock)
    assert source(restarted)["status"] == "idle"
    assert restarted.run_once()
    assert count(restarted, "jobs") == 1
    assert source(restarted)["interval_minutes"] == 60


def test_existing_catalog_and_cross_source_queue_dedup(watch):
    make_video(watch.catalog.source, VID)
    watch.catalog.scan()
    watch.scanner.entries.append({"id": VID2})
    watch.create(PLAYLIST)
    watch.create(PLAYLIST + "x")
    watch.run_once()
    watch.run_once()
    assert count(watch, "jobs") == 1 and count(watch, "job_items") == 1
    for item in watch.list()["items"]:
        assert item["known"] == 2 and item["queued"] == 1
        assert {v["state"] for v in item["recent"]} == {"existing", "queued"}
    watch.remove(source(watch)["id"])
    assert count(watch, "jobs") == 1 and count(watch, "videos") == 1


def test_backoff_preserves_seen_and_recovers(watch):
    watch.create(PLAYLIST)
    watch.run_once()
    last_success = source(watch)["last_success"]
    watch.scanner.error = RuntimeError("Secret upstream details must not reach the UI")
    for minutes in (15, 30, 60, 120, 120):
        watch.check_now(source(watch)["id"])
        watch.run_once()
        item = source(watch)
        assert item["status"] == "error" and item["known"] == 1
        assert item["last_success"] == last_success
        assert "Secret" not in item["last_error"]
        assert datetime.fromisoformat(item["next_check"]) == watch.clock() + timedelta(minutes=minutes)
    watch.scanner.error = None
    watch.check_now(source(watch)["id"])
    watch.run_once()
    assert source(watch)["last_error"] is None and source(watch)["failures"] == 0
    assert count(watch, "jobs") == 1


def test_unavailable_live_and_invalid_entries_are_reconsidered(watch):
    watch.create(PLAYLIST)
    watch.scanner.entries = [None, {"id": "bad"}, {"id": VID, "availability": "private"}, {"id": VID2, "live_status": "is_upcoming"}]
    watch.run_once()
    assert source(watch)["last_unavailable"] == 4 and source(watch)["known"] == 0
    watch.scanner.entries = [{"id": VID}, {"id": VID2}]
    watch.check_now(source(watch)["id"])
    watch.run_once()
    assert source(watch)["queued"] == 2


def test_batches_pending_persist_and_transcription_failure_requires_explicit_retry(watch):
    watch.scanner.entries = [{"id": f"v{index:010d}"} for index in range(105)]
    watch.create(PLAYLIST)
    watch.run_once()
    assert source(watch)["pending"] == 5 and source(watch)["queued"] == 100
    watch.dispatch_pending()
    assert count(watch, "jobs") == 1
    with watch.catalog.db() as conn:
        conn.execute("UPDATE jobs SET status='failed'")
        conn.execute("UPDATE job_items SET status='failed'")
    watch.dispatch_pending()
    assert source(watch)["pending"] == 0 and count(watch, "jobs") == 2
    watch.check_now(source(watch)["id"])
    watch.run_once()
    assert count(watch, "job_items") == 105  # Failed IDs are not silently resubmitted.


def test_queue_capacity_retains_pending_and_atomic_handoff(watch):
    for i in range(100):
        watch.jobs.enqueue(f"https://www.youtube.com/watch?v=v{i:010d}")
    watch.create(PLAYLIST)
    watch.run_once()
    assert source(watch)["pending"] == 1
    with watch.catalog.db() as conn:
        conn.execute("UPDATE jobs SET status='completed'")
        conn.execute("CREATE TRIGGER fail_handoff BEFORE INSERT ON job_items BEGIN SELECT RAISE(ABORT,'test rollback'); END")
    with pytest.raises(sqlite3.IntegrityError):
        watch.dispatch_pending()
    assert count(watch, "jobs") == 100 and source(watch)["pending"] == 1
    with watch.catalog.db() as conn:
        conn.execute("DROP TRIGGER fail_handoff")
    watch.dispatch_pending()
    assert count(watch, "jobs") == 101 and source(watch)["queued"] == 1


@pytest.mark.parametrize("action", ["pause", "remove", "stop"])
def test_changes_during_network_read_prevent_enqueue(watch, action):
    identifier = watch.create(PLAYLIST)["id"]
    def change():
        if action == "pause":
            watch.update(identifier, enabled=False)
        elif action == "remove":
            watch.remove(identifier)
        else:
            watch.stop.set()
    watch.scanner.callback = change
    watch.run_once()
    assert count(watch, "jobs") == 0 and count(watch, "watch_seen") == 0


def test_concurrent_scans_are_coalesced(watch):
    identifier = watch.create(PLAYLIST)["id"]
    entered, release = threading.Event(), threading.Event()
    def block():
        entered.set()
        assert release.wait(5)
    watch.scanner.callback = block
    worker = threading.Thread(target=watch.run_once)
    worker.start()
    try:
        assert entered.wait(5)
        watch.check_now(identifier)
        assert not watch.run_once()
    finally:
        release.set()
        worker.join(5)
    assert len(watch.scanner.calls) == 1 and count(watch, "jobs") == 1


def test_api_validation_partial_updates_duplicate_and_removal(collection, tmp_path):
    app = create_app(collection, tmp_path / "data", download_thumbnails=False, initial_scan=False,
                     start_jobs=False, start_skills=False, start_watchers=False, playlist_scanner=Scanner())
    with TestClient(app) as client:
        for body in ({"url": "https://localhost/playlist?list=PLabcdefgh"}, {"url": PLAYLIST, "interval_minutes": 14}, {"url": PLAYLIST, "language": "de"}):
            assert client.post("/api/watchers", json=body).status_code == 422
        created = client.post("/api/watchers", json={"url": PLAYLIST, "name": "Research", "interval_minutes": 60, "language": "es"})
        assert created.status_code == 201
        path = "/api/watchers/" + created.json()["id"]
        assert client.post("/api/watchers", json={"url": PLAYLIST + "&index=1"}).status_code == 409
        assert client.patch(path, json={"enabled": False}).status_code == 200
        item = client.get("/api/watchers").json()["items"][0]
        assert item["name"] == "Research" and item["interval_minutes"] == 60 and item["language"] == "es"
        assert client.post(path + "/check").status_code == 409
        assert client.patch(path, json={"enabled": True}).status_code == 200
        assert client.post(path + "/check").status_code == 202
        app.state.watchers.run_once()
        assert client.delete(path, headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.delete(path).status_code == 200
        assert client.get("/api/watchers").json()["total"] == 0
        assert client.get("/api/jobs").json()["total"] == 1
        assert client.delete(path).status_code == 404


@pytest.mark.parametrize("payload,code", [
    ({"id": "PLabcdefghijklmnop", "entries": []}, None),
    ({"id": "wrong", "entries": []}, "watch_invalid_response"),
    ({"id": "PLabcdefghijklmnop"}, "watch_invalid_response"),
    ("invalid", "watch_invalid_response"),
])
def test_scanner_process_boundary_validates_complete_response(monkeypatch, payload, code):
    raw = json.dumps(payload)
    monkeypatch.setattr(Pipeline, "yt_args", staticmethod(lambda: [sys.executable, "-c", "import sys;sys.stdout.write(" + repr(raw) + ")"]))
    scanner = PlaylistScanner(threading.Event())
    if code:
        with pytest.raises(WatchError) as exc:
            scanner.scan(PLAYLIST)
        assert exc.value.code == code
    else:
        assert scanner.scan(PLAYLIST) == ("", [])


def test_scanner_rejects_url_and_stopped_requests_before_spawn(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("No process should start")
    monkeypatch.setattr("backend.watchers.subprocess.Popen", fail)
    stopped = threading.Event()
    scanner = PlaylistScanner(stopped)
    with pytest.raises(ValueError):
        scanner.scan("https://localhost/playlist?list=PLabcdefghijklmnop")
    stopped.set()
    with pytest.raises(WatchInterrupted):
        scanner.scan(PLAYLIST)
