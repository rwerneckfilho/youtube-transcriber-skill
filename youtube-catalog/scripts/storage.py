"""Offline, explicit migration and backup of the Docker-managed library.

Run with the application stopped. Source mounts are read-only. Import is refused
once a live catalog exists and can be retried after an interrupted staged copy.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile


MODEL_NAME = "ggml-large-v3-turbo-q5_0.bin"
MODEL_SHA = "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def regular_tree(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"The directory must exist and cannot be a symbolic link: {root.name}")
    files = []
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError(f"Special files and symbolic links are not allowed: {path.relative_to(root)}")
        if path.is_file():
            files.append(path)
    return files


def copy_verified(source: Path, destination: Path) -> int:
    files = regular_tree(source)
    destination.mkdir(parents=True, exist_ok=True)
    for path in files:
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if digest(path) != digest(target):
            raise OSError("The copy does not match the original; import stopped.")
    return len(files)


def own_tree(root: Path):
    if os.geteuid() == 0:
        os.chown(root, 1000, 1000)
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("Storage cannot contain symbolic links.")
            os.chown(path, 1000, 1000)


def write_state(path: Path, state):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@contextmanager
def legacy_database(database: Path):
    # A WAL database may need shared-memory writes even with mode=ro. Recover
    # a private copy, including committed WAL, leaving the legacy mount intact.
    # The legacy application must be stopped before copying these files.
    with tempfile.TemporaryDirectory(prefix="catalog-migration-db-") as directory:
        snapshot = Path(directory) / "catalog.sqlite3"
        shutil.copy2(database, snapshot)
        wal = database.with_name(database.name + "-wal")
        if wal.exists():
            shutil.copy2(wal, snapshot.with_name(snapshot.name + "-wal"))
        connection = sqlite3.connect(snapshot)
        try:
            yield connection
        finally:
            connection.close()


def import_legacy(storage: Path, source: Path, data: Path | None = None, model: Path | None = None):
    storage.mkdir(parents=True, exist_ok=True)
    done = storage / ".migration-complete.json"
    journal = storage / ".migration-in-progress.json"
    stage = storage / ".migration-stage"
    if done.exists():
        # A crash after the completion marker must not block startup forever.
        journal.unlink(missing_ok=True)
        if stage.exists():
            stage.rmdir()
        return {**json.loads(done.read_text()), "already_imported": True}
    # Preflight every source before changing even the new volume.
    regular_tree(source)
    if data:
        regular_tree(data)
        database = data / "catalog.sqlite3"
        if database.is_file():
            with legacy_database(database) as original:
                has_purges = original.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='purge_jobs'").fetchone()
                if has_purges and original.execute("SELECT 1 FROM purge_jobs LIMIT 1").fetchone():
                    raise ValueError("There are pending permanent deletions in the source catalog. Complete these operations in the source application's Trash before migrating; no files or records have been changed.")
    if model and (not model.is_file() or model.is_symlink() or digest(model) != MODEL_SHA):
        raise ValueError("The supplied model does not match the expected SHA-256 checksum.")
    if journal.exists():
        state = json.loads(journal.read_text())
        if state["source"] != str(source.resolve()) or state["data"] != (str(data.resolve()) if data else None):
            raise ValueError("Resume the migration with the same source directories.")
    else:
        for name in ("catalog", "transcripts"):
            target = storage / name
            if target.exists() and any(target.iterdir()):
                raise ValueError("The volume already contains data. Use a new volume; no files have been overwritten.")
        state = {"source": str(source.resolve()), "data": str(data.resolve()) if data else None, "copied": False, "published": []}
        write_state(journal, state)
    if not state["copied"]:
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        count = copy_verified(source, stage / "transcripts")
        (stage / "catalog").mkdir()
        if data and (data / "catalog.sqlite3").exists():
            database = data / "catalog.sqlite3"
            with legacy_database(database) as original, sqlite3.connect(stage / "catalog" / "catalog.sqlite3") as copied:
                original.backup(copied)
                if copied.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("The database copy failed the integrity check.")
        if data and (data / "thumbnails").is_dir():
            copy_verified(data / "thumbnails", stage / "catalog" / "thumbnails")
        state.update(copied=True, files=count)
        write_state(journal, state)
    for name in ("catalog", "transcripts"):
        origin, target = stage / name, storage / name
        if name not in state["published"]:
            if origin.exists():
                if target.exists():
                    target.rmdir()  # Refuse to overwrite anything, including unexpected partial data.
                origin.replace(target)
            elif not target.is_dir():
                raise ValueError("The migration is incomplete; preserve the sources and check the volume.")
            state["published"].append(name)
            write_state(journal, state)
    for name in ("models", "work", "cache"):
        (storage / name).mkdir(exist_ok=True)
    if model:
        target = storage / "models" / MODEL_NAME
        if not target.exists() or digest(target) != MODEL_SHA:
            partial = target.with_suffix(target.suffix + ".part")
            shutil.copy2(model, partial)
            if digest(partial) != MODEL_SHA:
                raise ValueError("The stored model is invalid and will not be used.")
            partial.replace(target)
    own_tree(storage)
    result = {"files": state["files"], "database_imported": bool(data and (data / "catalog.sqlite3").exists()), "already_imported": False}
    write_state(done, result)
    journal.unlink()
    if stage.exists():
        stage.rmdir()
    return result


def backup(storage: Path, destination: Path):
    regular_tree(storage)
    if destination.resolve().is_relative_to(storage.resolve()):
        raise ValueError("Save the backup outside the catalog volume.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    # Publish only a complete archive; an interrupted write must not look like
    # a usable backup. Linking atomically also protects existing destinations.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".catalog-backup-", dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            with tarfile.open(fileobj=stream, mode="w:gz") as archive:
                for path in sorted(storage.iterdir()):
                    archive.add(path, arcname=path.name, recursive=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return {"backup": destination.name, "bytes": destination.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage", type=Path, default=Path("/storage"))
    commands = parser.add_subparsers(dest="command", required=True)
    migration = commands.add_parser("import-legacy")
    migration.add_argument("--source", type=Path, required=True)
    migration.add_argument("--data", type=Path)
    migration.add_argument("--model", type=Path)
    exporter = commands.add_parser("backup")
    exporter.add_argument("destination", type=Path)
    commands.add_parser("check")
    args = parser.parse_args()
    if args.command == "import-legacy":
        result = import_legacy(args.storage, args.source.resolve(), args.data.resolve() if args.data else None, args.model)
    elif args.command == "backup":
        result = backup(args.storage, args.destination)
    else:
        if (args.storage / ".migration-in-progress.json").exists():
            raise SystemExit("Migration interrupted. Resume import-legacy before starting the catalog.")
        for name in ("catalog", "transcripts", "models", "work", "cache"):
            (args.storage / name).mkdir(parents=True, exist_ok=True)
        result = {"storage": "ready"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
