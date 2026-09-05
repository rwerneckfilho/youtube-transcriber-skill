"""Deletion regression checks; every source and database is a disposable fixture."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.catalog import Catalog
from backend.tests.conftest import make_video


@pytest.fixture
def deletion_client(collection, tmp_path):
    app = create_app(source_dir=collection, data_dir=tmp_path / "deletion-data",
                     static_dir=tmp_path / "static", download_thumbnails=False,
                     initial_scan=False, scan_interval=3600)
    with TestClient(app) as client:
        yield client


def populate(client, source):
    make_video(source, "removed", channel="Canal removido", texts=["palavraexclusiva e automação"])
    make_video(source, "kept", channel="Canal ativo", category="C2", category_name="Pesquisa")
    make_video(source, "target", channel="Canal ativo", category=None)
    client.app.state.catalog.scan()


def test_delete_excludes_all_active_counts_search_and_suggestions(deletion_client, collection):
    client = deletion_client
    populate(client, collection)
    before = {str(path.relative_to(collection)): path.read_bytes() for path in collection.glob("*/*")}
    assert "C1" in {item["id"] for item in client.get("/api/videos/target").json()["suggestions"]}
    deleted = client.delete("/api/videos/removed")
    assert deleted.status_code == 200 and deleted.json() == {"id": "removed", "deleted": True}
    assert {item["id"] for item in client.get("/api/videos").json()["items"]} == {"kept", "target"}
    assert client.get("/api/videos?q=palavraexclusiva").json()["total"] == 0
    stats = client.get("/api/stats").json()
    assert (stats["videos"], stats["deleted_videos"], stats["channels"], stats["methods"]) == (2, 1, 1, 1)
    assert stats["hours"] == pytest.approx(2 * 22300 / 3600)
    assert stats["languages"] == [{"id": "pt", "name": "Português", "count": 2}]
    assert client.get("/api/channels").json() == [{"id": "channel-Canal ativo", "name": "Canal ativo", "count": 2}]
    counts = {item["id"]: item["count"] for item in client.get("/api/categories").json()}
    assert counts == {"C1": 0, "C2": 1}
    assert "C1" not in {item["id"] for item in client.get("/api/videos/target").json()["suggestions"]}
    assert before == {str(path.relative_to(collection)): path.read_bytes() for path in collection.glob("*/*")}


def test_trash_is_explicit_searchable_paginated_and_newest_first(deletion_client, collection):
    client = deletion_client
    populate(client, collection)
    client.delete("/api/videos/removed")
    client.delete("/api/videos/kept")
    first = client.get("/api/videos?deleted=true&page_size=1").json()
    second = client.get("/api/videos?deleted=true&page_size=1&page=2").json()
    assert first["total"] == second["total"] == 2
    assert first["items"][0]["id"] == "kept"
    assert second["items"][0]["id"] == "removed"
    assert first["items"][0]["deleted_at"]
    search = client.get("/api/videos?deleted=true&q=palavraexclusiva&category=C1").json()
    assert search["total"] == 1 and search["items"][0]["id"] == "removed"
    assert client.get("/api/videos?deleted=false").json()["total"] == 1
    assert client.get("/api/videos?deleted=true&page=99").json()["items"] == []


def test_deleted_content_is_hidden_but_cached_thumbnail_remains(deletion_client, collection):
    client = deletion_client
    make_video(collection)
    client.app.state.catalog.scan()
    thumbnail = client.app.state.thumbnails.directory / "video_1.jpg"
    cached_bytes = b"\xff\xd8\xfffixture-jpeg\xff\xd9"
    thumbnail.write_bytes(cached_bytes)
    assert client.get("/api/videos/video_1/files/transcript.txt").status_code == 200
    client.delete("/api/videos/video_1")
    for path in ["/api/videos/video_1", "/api/videos/video_1/segments", "/api/videos/video_1/files/transcript.txt"]:
        response = client.get(path)
        assert response.status_code == 404
        assert isinstance(response.json()["detail"], str)
    image = client.get("/api/videos/video_1/thumbnail")
    assert image.status_code == 200 and image.content == cached_bytes
    assert image.headers["content-type"].startswith("image/jpeg")


def test_delete_is_idempotent_restore_preserves_overrides_text_and_dates(deletion_client, collection):
    client = deletion_client
    make_video(collection)
    catalog = client.app.state.catalog
    catalog.scan()
    category = client.post("/api/categories", json={"name": "Favoritos pessoais"}).json()
    expected = client.put("/api/videos/video_1/categories", json={"category_ids": [category["id"]]}).json()
    segments = client.get("/api/videos/video_1/segments").json()
    assert client.delete("/api/videos/video_1").status_code == 200
    timestamp = client.get("/api/videos?deleted=true").json()["items"][0]["deleted_at"]
    assert client.delete("/api/videos/video_1").status_code == 200
    assert client.get("/api/videos?deleted=true").json()["items"][0]["deleted_at"] == timestamp
    assert client.get("/api/stats").json()["deleted_videos"] == 1
    restored = client.post("/api/videos/video_1/restore")
    assert restored.status_code == 200
    actual = restored.json()
    assert actual["deleted_at"] is None
    assert actual["category_override"] and actual["categories"] == expected["categories"]
    assert actual["added_at"] == expected["added_at"] and actual["method"] == expected["method"]
    assert client.get("/api/videos/video_1/segments").json() == segments
    assert client.get("/api/stats").json()["deleted_videos"] == 0
    assert client.get("/api/videos/video_1/files/transcript.txt").status_code == 200
    assert client.delete("/api/videos/missing").status_code == 404
    assert client.post("/api/videos/missing/restore").status_code == 404


def test_scan_does_not_restore_deleted_video_even_after_source_update_or_restart(deletion_client, collection):
    client = deletion_client
    folder = make_video(collection)
    catalog = client.app.state.catalog
    catalog.scan()
    client.put("/api/videos/video_1/categories", json={"category_ids": []})
    client.delete("/api/videos/video_1")
    metadata = json.loads((folder / "video.json").read_text())
    metadata["title"] = "Título atualizado enquanto excluído"
    (folder / "video.json").write_text(json.dumps(metadata))
    catalog.scan()
    assert client.get("/api/videos").json()["total"] == 0
    assert client.get("/api/videos?deleted=true").json()["items"][0]["title"] == metadata["title"]
    restarted = Catalog(collection, catalog.data)
    restarted.scan()
    assert restarted.stats()["videos"] == 0 and restarted.stats()["deleted_videos"] == 1
    restored = client.post("/api/videos/video_1/restore").json()
    assert restored["title"] == metadata["title"]
    assert restored["category_override"] and restored["categories"] == []
    catalog.scan()
    assert client.get("/api/videos").json()["total"] == 1


def test_existing_v1_database_migration_preserves_every_imported_record(catalog, collection):
    make_video(collection)
    make_video(collection, "second", category=None)
    catalog.scan()
    custom = catalog.save_category("Classificação mantida")
    catalog.assign_categories("video_1", [custom["id"]])
    expected = catalog.detail("video_1")
    segments = catalog.segments("video_1")
    # Reproduce the existing v1 shape: all imported tables/data, no deletion column.
    with sqlite3.connect(catalog.db_path) as conn:
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql LIKE '%deleted_at%'"):
            conn.execute('DROP INDEX "' + name.replace('"', '""') + '"')
        conn.execute("ALTER TABLE videos DROP COLUMN deleted_at")
        conn.execute("PRAGMA user_version=1")
    migrated = Catalog(collection, catalog.data)
    assert migrated.stats()["videos"] == 2 and migrated.stats()["deleted_videos"] == 0
    actual = migrated.detail("video_1")
    assert actual["deleted_at"] is None and actual["categories"] == expected["categories"]
    assert actual["category_override"] and actual["added_at"] == expected["added_at"]
    assert migrated.segments("video_1") == segments
    assert migrated.list_videos(q="acao")["total"] == 2
    # Startup migration is repeatable and must not overwrite the user's categories.
    again = Catalog(collection, catalog.data)
    assert again.detail("video_1")["categories"] == expected["categories"]
    with again.db() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 2
