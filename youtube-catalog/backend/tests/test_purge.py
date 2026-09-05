"""Permanent deletion tests use disposable fixture collections exclusively."""
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.catalog import Catalog, PurgeConflict
from backend.tests.conftest import make_video
from backend.thumbnails import ThumbnailManager


@pytest.fixture
def purge_client(collection, tmp_path):
    make_video(collection)
    make_video(collection, "keep")
    app = create_app(source_dir=collection, data_dir=tmp_path / "purge-data", static_dir=tmp_path / "static", download_thumbnails=False, initial_scan=False, scan_interval=3600)
    with TestClient(app) as client:
        app.state.catalog.scan()
        yield client


def purge(client, video_id="video_1", confirmation=None):
    return client.request("DELETE", f"/api/videos/{video_id}/permanent", json={"confirmation": video_id if confirmation is None else confirmation})


def to_trash(client):
    assert client.delete("/api/videos/video_1").status_code == 200


def test_requires_trash_exact_confirmation_and_existing_video(purge_client, collection):
    client = purge_client
    original = {str(p.relative_to(collection)): p.read_bytes() for p in collection.glob("*/*")}
    assert purge(client).status_code == 409
    to_trash(client)
    assert purge(client, confirmation="wrong-id").status_code == 422
    assert purge(client, confirmation="video_1 ").status_code == 422
    assert client.request("DELETE", "/api/videos/video_1/permanent", json={}).status_code == 422
    assert purge(client, video_id="missing").status_code == 404
    assert client.request("DELETE", "/api/videos/%2e%2e/permanent", json={"confirmation": ".."}).status_code == 404
    assert original == {str(p.relative_to(collection)): p.read_bytes() for p in collection.glob("*/*")}
    with client.app.state.catalog.db() as conn:
        assert conn.execute("SELECT count(*) FROM purge_jobs").fetchone()[0] == 0


def test_removes_every_original_index_and_cache_only_for_confirmed_video(purge_client, collection):
    client = purge_client
    catalog = client.app.state.catalog
    folder = collection / "video_1"
    nested = folder / "assets" / "audio"
    nested.mkdir(parents=True)
    (nested / "sound.wav").write_bytes(b"audio-original")
    (folder / "video.mp4").write_bytes(b"original-video")
    (folder / "private-extra.any").write_bytes(b"custom artifact")
    (collection / "INDEX.md").write_text("Shared index remains untouched.")
    sibling = {p.name: p.read_bytes() for p in (collection / "keep").iterdir()}
    custom = client.post("/api/categories", json={"name": "Preferência pessoal"}).json()
    client.put("/api/videos/video_1/categories", json={"category_ids": [custom["id"]]})
    manager = client.app.state.thumbnails
    for suffix in (".jpg", ".png", ".webp", ".part"):
        (manager.directory / ("video_1" + suffix)).write_bytes(b"cached data")
    (manager.directory / "keep.jpg").write_bytes(b"keep cache")
    to_trash(client)
    response = purge(client)
    assert response.status_code == 200 and response.json() == {"id": "video_1", "permanently_deleted": True}
    assert not folder.exists()
    assert not list(collection.glob(".catalog-purge-*"))
    assert sibling == {p.name: p.read_bytes() for p in (collection / "keep").iterdir()}
    assert (collection / "INDEX.md").read_text() == "Shared index remains untouched."
    assert (manager.directory / "keep.jpg").read_bytes() == b"keep cache"
    assert not list(manager.directory.glob("video_1*"))
    with catalog.db() as conn:
        for table, key in (("videos", "id"), ("sources", "id"), ("segments", "video_id"), ("video_fts", "video_id"), ("segment_fts", "video_id"), ("video_categories", "video_id"), ("purge_jobs", "video_id")):
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE {key}=?", ("video_1",)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM categories WHERE id=?", (custom["id"],)).fetchone()[0] == 1
    catalog.scan()
    assert catalog.stats()["videos"] == 1 and catalog.stats()["deleted_videos"] == 0
    assert client.get("/api/videos?deleted=true").json()["total"] == 0
    assert client.post("/api/videos/video_1/restore").status_code == 404
    assert purge(client).status_code == 404
    restarted = Catalog(collection, catalog.data)
    restarted.scan()
    assert restarted.stats()["videos"] == 1 and not restarted.stats()["warnings"]


def test_nested_symlinks_are_unlinked_without_touching_targets(purge_client, collection, tmp_path):
    client = purge_client
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("preserve me")
    folder = collection / "video_1"
    (folder / "external-directory").symlink_to(outside, target_is_directory=True)
    (folder / "external-file").symlink_to(secret)
    (folder / "other-video").symlink_to(collection / "keep", target_is_directory=True)
    to_trash(client)
    assert purge(client).status_code == 200
    assert secret.read_text() == "preserve me"
    assert (collection / "keep" / "transcript.json").is_file()


def test_video_directory_symlink_is_rejected(purge_client, collection, tmp_path):
    client = purge_client
    to_trash(client)
    folder = collection / "video_1"
    moved = tmp_path / "elsewhere"
    shutil.move(folder, moved)
    folder.symlink_to(moved, target_is_directory=True)
    response = purge(client)
    assert response.status_code == 409
    assert "simbólicos" in response.json()["detail"]
    assert folder.is_symlink() and (moved / "transcript.json").is_file()
    assert client.get("/api/stats").json()["deleted_videos"] == 1


def test_missing_source_video_can_be_purged_but_missing_root_cannot(purge_client, collection, tmp_path):
    client = purge_client
    to_trash(client)
    root_backup = tmp_path / "unmounted"
    collection.rename(root_backup)
    response = purge(client)
    assert response.status_code == 503
    assert client.get("/api/stats").json()["deleted_videos"] == 1
    root_backup.rename(collection)
    shutil.rmtree(collection / "video_1")
    assert purge(client).status_code == 200
    assert client.get("/api/stats").json()["deleted_videos"] == 0


def test_readonly_root_fails_without_starting_physical_removal(purge_client, collection):
    client = purge_client
    to_trash(client)
    collection.chmod(0o555)
    try:
        response = purge(client)
        assert response.status_code == 503
        assert (collection / "video_1" / "video.json").is_file()
        assert not client.get("/api/videos?deleted=true").json()["items"][0]["purge_pending"]
    finally:
        collection.chmod(0o755)
    assert client.post("/api/videos/video_1/restore").status_code == 200


def test_partial_filesystem_failure_is_durable_blocks_restore_and_retries(purge_client, collection, monkeypatch):
    client = purge_client
    catalog = client.app.state.catalog
    category = catalog.save_category("Preservar até concluir")
    catalog.assign_categories("video_1", [category["id"]])
    to_trash(client)
    remove = catalog._remove_quarantine

    def partly_remove(root_fd, quarantine):
        (collection / quarantine / "transcript.txt").unlink()
        raise PermissionError("simulated locked original")

    monkeypatch.setattr(catalog, "_remove_quarantine", partly_remove)
    response = purge(client)
    assert response.status_code == 503
    trash = client.get("/api/videos?deleted=true").json()["items"][0]
    assert trash["purge_pending"] and trash["purge_error"] and not trash["available"]
    assert trash["categories"] == [{"id": category["id"], "name": "Preservar até concluir"}]
    assert client.post("/api/videos/video_1/restore").status_code == 409
    assert client.get("/api/stats").json()["warnings"]
    remaining = sorted(p.name for p in next(collection.glob(".catalog-purge-*")).iterdir())
    restarted = Catalog(collection, catalog.data)
    restarted.scan()
    assert restarted.stats()["deleted_videos"] == 1
    assert restarted.list_videos()["total"] == 1
    assert remaining == sorted(p.name for p in next(collection.glob(".catalog-purge-*")).iterdir())
    monkeypatch.setattr(catalog, "_remove_quarantine", remove)
    assert purge(client).status_code == 200
    assert not list(collection.glob(".catalog-purge-*"))
    assert not client.get("/api/stats").json()["warnings"]


def test_failure_after_originals_removed_retains_record_until_retry(purge_client, collection, monkeypatch):
    client = purge_client
    catalog = client.app.state.catalog
    to_trash(client)
    original_cleanup = catalog._remove_thumbnail_files

    def failing_cleanup(video_id):
        raise OSError("cache permission denied")

    monkeypatch.setattr(catalog, "_remove_thumbnail_files", failing_cleanup)
    assert purge(client).status_code == 503
    assert not (collection / "video_1").exists()
    assert client.get("/api/videos?deleted=true").json()["items"][0]["purge_pending"]
    catalog.scan()
    assert catalog.stats()["deleted_videos"] == 1
    monkeypatch.setattr(catalog, "_remove_thumbnail_files", original_cleanup)
    assert purge(client).status_code == 200


def test_interrupted_process_is_not_automatically_resumed(purge_client, collection, monkeypatch):
    catalog = purge_client.app.state.catalog
    to_trash(purge_client)

    class ProcessCrash(BaseException):
        pass

    def interrupted(root_fd, quarantine):
        raise ProcessCrash()

    monkeypatch.setattr(catalog, "_remove_quarantine", interrupted)
    with pytest.raises(ProcessCrash):
        catalog.permanent_delete("video_1", "video_1")
    staged = next(collection.glob(".catalog-purge-*"))
    assert (staged / "transcript.txt").is_file()
    restarted = Catalog(collection, catalog.data)
    restarted.scan()
    assert (staged / "transcript.txt").is_file()
    assert restarted.list_videos(deleted=True)["items"][0]["purge_pending"]
    with pytest.raises(PurgeConflict):
        restarted.set_deleted("video_1", False)
    restarted.permanent_delete("video_1", "video_1")
    assert not staged.exists() and restarted.stats()["deleted_videos"] == 0


def test_new_source_replacing_quarantined_video_is_never_deleted(purge_client, collection, monkeypatch):
    client = purge_client
    catalog = client.app.state.catalog
    to_trash(client)
    original_remove = catalog._remove_quarantine

    def recreate_source(root_fd, quarantine):
        make_video(collection, "video_1", title="Newly generated original")
        original_remove(root_fd, quarantine)

    monkeypatch.setattr(catalog, "_remove_quarantine", recreate_source)
    response = purge(client)
    assert response.status_code == 409
    assert (collection / "video_1" / "transcript.txt").is_file()
    assert "Newly generated original" in (collection / "video_1" / "video.json").read_text()
    catalog.scan()
    assert catalog.stats()["videos"] == 1 and catalog.stats()["deleted_videos"] == 1
    assert catalog.list_videos(deleted=True)["items"][0]["title"] != "Newly generated original"


def test_restore_and_scan_cannot_race_permanent_deletion(purge_client, collection, monkeypatch):
    catalog = purge_client.app.state.catalog
    to_trash(purge_client)
    entered, release, restore_started = threading.Event(), threading.Event(), threading.Event()
    original_remove = catalog._remove_quarantine

    def paused_remove(root_fd, quarantine):
        entered.set()
        assert release.wait(5)
        original_remove(root_fd, quarantine)

    monkeypatch.setattr(catalog, "_remove_quarantine", paused_remove)

    def restoring():
        restore_started.set()
        catalog.set_deleted("video_1", False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        deleting = pool.submit(catalog.permanent_delete, "video_1", "video_1")
        assert entered.wait(5)
        restoring_future = pool.submit(restoring)
        assert restore_started.wait(5)
        catalog.request_scan()
        assert not restoring_future.done()
        release.set()
        deleting.result(timeout=5)
        with pytest.raises(KeyError):
            restoring_future.result(timeout=5)
    catalog.scan()
    assert catalog.stats()["videos"] == 1 and catalog.stats()["deleted_videos"] == 0


def test_inflight_thumbnail_cannot_recreate_purged_cache(catalog, collection):
    make_video(collection)
    catalog.scan()
    entered, release = threading.Event(), threading.Event()

    def response(request):
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"\xff\xd8\xffcached\xff\xd9")

    manager = ThumbnailManager(catalog, True, httpx.MockTransport(response))
    catalog.thumbnail_manager = manager
    manager.enqueue_missing()
    assert entered.wait(5)
    catalog.set_deleted("video_1", True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        deleting = pool.submit(catalog.permanent_delete, "video_1", "video_1")
        release.set()
        deleting.result(timeout=5)
    assert manager.cached("video_1") is None
    assert not list(manager.directory.glob("video_1*"))
    assert not manager.pending
    manager.close()
