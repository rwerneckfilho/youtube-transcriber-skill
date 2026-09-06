"""Migration/backup checks use small disposable files, never the real collection."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tarfile

import pytest

spec = importlib.util.spec_from_file_location("catalog_storage", Path(__file__).parents[1] / "scripts" / "storage.py")
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)


@pytest.fixture
def legacy(tmp_path):
    source, data, target = (tmp_path / n for n in ("source", "data", "volume"))
    (source / "video_one" / "nested").mkdir(parents=True)
    (source / "video_one" / "transcript.txt").write_text("An actual preserved transcript")
    (source / "video_one" / "nested" / "audio.wav").write_bytes(b"RIFF-test-audio")
    (data / "thumbnails").mkdir(parents=True)
    (data / "thumbnails" / "video_one.jpg").write_bytes(b"fixture-image")
    with sqlite3.connect(data / "catalog.sqlite3") as conn:
        conn.execute("CREATE TABLE videos(id TEXT, deleted_at TEXT, custom_category TEXT)")
        conn.execute("INSERT INTO videos VALUES('video_one','2026-01-01','Personal choice')")
    return source, data, target


def test_migration_keeps_files_sqlite_preferences_and_originals(legacy):
    source, data, target = legacy
    before = {str(p): p.read_bytes() for root in (source, data) for p in root.rglob("*") if p.is_file()}
    result = storage.import_legacy(target, source, data)
    assert result == {"files": 2, "database_imported": True, "already_imported": False}
    assert (target / "transcripts" / "video_one" / "nested" / "audio.wav").read_bytes() == b"RIFF-test-audio"
    with sqlite3.connect(target / "catalog" / "catalog.sqlite3") as conn:
        assert conn.execute("SELECT * FROM videos").fetchone() == ("video_one", "2026-01-01", "Personal choice")
    assert before == {str(p): p.read_bytes() for root in (source, data) for p in root.rglob("*") if p.is_file()}
    assert not (target / ".migration-in-progress.json").exists()


def test_repeated_import_never_resurrects_files_or_overwrites_live_data(legacy):
    source, data, target = legacy
    storage.import_legacy(target, source, data)
    copied = target / "transcripts" / "video_one" / "transcript.txt"
    copied.unlink()
    with sqlite3.connect(target / "catalog" / "catalog.sqlite3") as conn:
        conn.execute("DELETE FROM videos")
    assert storage.import_legacy(target, source, data)["already_imported"]
    assert not copied.exists()
    with sqlite3.connect(target / "catalog" / "catalog.sqlite3") as conn:
        assert conn.execute("SELECT count(*) FROM videos").fetchone()[0] == 0


def test_nonempty_volume_is_not_overwritten(legacy):
    source, data, target = legacy
    (target / "catalog").mkdir(parents=True)
    (target / "catalog" / "important.txt").write_text("keep")
    with pytest.raises(ValueError, match="already contains data"):
        storage.import_legacy(target, source, data)
    assert (target / "catalog" / "important.txt").read_text() == "keep"


def test_symlink_and_bad_model_are_rejected_before_copy(legacy):
    source, data, target = legacy
    model = source.parent / "bad-model.bin"
    model.write_bytes(b"wrong model")
    with pytest.raises(ValueError, match="SHA-256"):
        storage.import_legacy(target, source, data, model)
    (source / "outside").symlink_to(data, target_is_directory=True)
    with pytest.raises(ValueError, match="link"):
        storage.import_legacy(target, source, data)
    assert not (target / ".migration-in-progress.json").exists()


def test_interrupted_copy_is_staged_and_can_be_explicitly_retried(legacy, monkeypatch):
    source, data, target = legacy
    real_copy = storage.shutil.copy2
    def interrupted(*args, **kwargs):
        if Path(args[0]).is_relative_to(source):
            raise OSError("simulated storage interruption")
        return real_copy(*args, **kwargs)
    monkeypatch.setattr(storage.shutil, "copy2", interrupted)
    with pytest.raises(OSError):
        storage.import_legacy(target, source, data)
    assert (target / ".migration-in-progress.json").exists()
    assert not (target / "catalog" / "catalog.sqlite3").exists()
    monkeypatch.setattr(storage.shutil, "copy2", real_copy)
    assert storage.import_legacy(target, source, data)["files"] == 2
    assert not (target / ".migration-in-progress.json").exists()


def test_backup_contains_library_and_refuses_overwrite(legacy):
    source, data, target = legacy
    storage.import_legacy(target, source, data)
    destination = target.parent / "library.tar.gz"
    storage.backup(target, destination)
    with tarfile.open(destination) as archive:
        assert archive.extractfile("transcripts/video_one/transcript.txt").read() == b"An actual preserved transcript"
        assert archive.getmember("catalog/catalog.sqlite3").size > 0
    with pytest.raises(FileExistsError):
        storage.backup(target, destination)
    with pytest.raises(ValueError, match="outside"):
        storage.backup(target, target / "inside.tar.gz")


def test_failed_backup_does_not_publish_an_incomplete_archive(legacy, monkeypatch):
    source, data, target = legacy
    storage.import_legacy(target, source, data)
    def failed_add(*args, **kwargs):
        raise PermissionError("fixture permission failure")
    monkeypatch.setattr(storage.tarfile.TarFile, "add", failed_add)
    destination = target.parent / "incomplete.tar.gz"
    with pytest.raises(PermissionError):
        storage.backup(target, destination)
    assert not destination.exists()
    assert not list(target.parent.glob('.catalog-backup-*'))


def test_completed_migration_cleans_interrupted_finalization(legacy):
    source, data, target = legacy
    storage.import_legacy(target, source, data)
    (target / ".migration-in-progress.json").write_text("{}")
    (target / ".migration-stage").mkdir()
    assert storage.import_legacy(target, source, data)["already_imported"]
    assert not (target / ".migration-in-progress.json").exists()
    assert not (target / ".migration-stage").exists()


def test_wal_database_migration_keeps_committed_rows_and_source_bytes(legacy):
    source, data, target = legacy
    connection = sqlite3.connect(data / "catalog.sqlite3")
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("UPDATE videos SET custom_category='Choice committed in WAL'")
        connection.commit()
        before = {p.name: p.read_bytes() for p in data.glob('catalog.sqlite3*')}
        storage.import_legacy(target, source, data)
        with sqlite3.connect(target / 'catalog' / 'catalog.sqlite3') as copied:
            assert copied.execute('SELECT custom_category FROM videos').fetchone()[0] == 'Choice committed in WAL'
        assert before == {p.name: p.read_bytes() for p in data.glob('catalog.sqlite3*')}
    finally:
        connection.close()


def test_interrupted_model_copy_can_be_retried(legacy, monkeypatch):
    source, data, target = legacy
    model = source.parent / "model.bin"
    model.write_bytes(b"verified fixture model")
    monkeypatch.setattr(storage, "MODEL_SHA", storage.digest(model))
    real_copy = storage.shutil.copy2
    def interrupted(src, dst, *args, **kwargs):
        if src == model:
            Path(dst).write_bytes(b"partial")
            raise OSError("interrupted model")
        return real_copy(src, dst, *args, **kwargs)
    monkeypatch.setattr(storage.shutil, "copy2", interrupted)
    with pytest.raises(OSError, match="interrupted model"):
        storage.import_legacy(target, source, data, model)
    assert not (target / "models" / storage.MODEL_NAME).exists()
    monkeypatch.setattr(storage.shutil, "copy2", real_copy)
    storage.import_legacy(target, source, data, model)
    assert (target / "models" / storage.MODEL_NAME).read_bytes() == model.read_bytes()
    assert not (target / "models" / (storage.MODEL_NAME + ".part")).exists()


def test_pending_purge_refuses_migration_without_mutating_sources_or_journal(legacy):
    source, data, target = legacy
    database = data / "catalog.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE purge_jobs(video_id TEXT PRIMARY KEY, source_inode INTEGER)")
        conn.execute("INSERT INTO purge_jobs VALUES('video_one',12345)")
    before = {str(p): p.read_bytes() for root in (source, data) for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="pending permanent deletions.*source.*Trash"):
        storage.import_legacy(target, source, data)
    assert before == {str(p): p.read_bytes() for root in (source, data) for p in root.rglob("*") if p.is_file()}
    assert not list(target.iterdir())
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT * FROM purge_jobs").fetchone() == ("video_one", 12345)
        conn.execute("DELETE FROM purge_jobs")
    # An empty purge journal is compatible after the original operation is finished.
    assert storage.import_legacy(target, source, data)["database_imported"]


def test_already_imported_marker_still_precedes_source_purge_preflight(legacy):
    source, data, target = legacy
    storage.import_legacy(target, source, data)
    with sqlite3.connect(data / "catalog.sqlite3") as conn:
        conn.execute("CREATE TABLE purge_jobs(video_id TEXT)")
        conn.execute("INSERT INTO purge_jobs VALUES('video_one')")
    assert storage.import_legacy(target, source, data)["already_imported"]
