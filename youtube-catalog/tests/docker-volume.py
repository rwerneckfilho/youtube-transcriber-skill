#!/usr/bin/env python3
"""Prove named-volume persistence using synthetic data and a local Docker image.

Only port 8767 is used. No source folder is mounted, no image is pulled, and the
containers have either an internal Docker network or no network. The job worker
is disabled: queued/cancelled persistence is covered, actual processing is not.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

REPORT = Path("/tmp/youtube-catalog-volume-report.json")
KEEP, TRASH = "volume_keep", "volume_trash"
CONTAINER = None

API_REQUEST = r'''
import json, sys
from urllib.request import Request, urlopen
body = json.loads(sys.argv[3])
request = Request('http://127.0.0.1:8765/api' + sys.argv[1], method=sys.argv[2],
                  data=json.dumps(body).encode() if body is not None else None,
                  headers={'Content-Type': 'application/json'})
with urlopen(request, timeout=8) as response:
    print(response.read().decode())
'''

SEED = r'''
import base64, hashlib, json
from pathlib import Path
root = Path('/storage')
source = root / 'transcripts'
assert not list(source.iterdir()), 'The fixture requires a new, empty volume'
for video_id in ('volume_keep', 'volume_trash'):
    folder = source / video_id
    folder.mkdir()
    meta = {'id': video_id, 'title': 'Automação sintética ' + video_id,
            'channel': 'Canal sintético', 'channel_id': 'volume-channel',
            'duration': 20, 'language': 'pt', 'upload_date': '20200101',
            'description': 'Material artificial exclusivo do teste de volume.',
            'tags': ['automação', 'sintético']}
    texts = ['Persistência de automação no volume local.', 'Leitura sintética sem internet.']
    transcript = {'result': {'language': 'pt'}, 'transcription': [
        {'offsets': {'from': i * 10000, 'to': i * 10000 + 5000}, 'text': text}
        for i, text in enumerate(texts)]}
    (folder / 'video.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    (folder / 'transcript.json').write_text(json.dumps(transcript, ensure_ascii=False), encoding='utf-8')
    (folder / 'transcript.txt').write_text('\n'.join(texts), encoding='utf-8')
    (folder / 'transcript.srt').write_text('1\n00:00:00,000 --> 00:00:05,000\n' + texts[0], encoding='utf-8')
    (folder / 'METODO.md').write_text('---\ncluster_id: C1\ncluster: Categoria sintética\n---\n# Método\n\nUm método artificial.', encoding='utf-8')
thumbnails = root / 'catalog' / 'thumbnails'
thumbnails.mkdir(exist_ok=True)
image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=')
(thumbnails / 'volume_keep.png').write_bytes(image)
(root / 'models' / 'fixture-only.bin').write_bytes(b'Synthetic model persistence marker; not a Whisper model.\n')
(root / 'work' / 'fixture-only.txt').write_text('Synthetic work persistence marker.\n')
manifest = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(source.rglob('*')) if p.is_file()}
(root / 'volume-test-fixture.json').write_text(json.dumps({'purpose': 'synthetic-volume-test', 'manifest': manifest}))
print(json.dumps({'files': len(manifest), 'thumbnail_sha256': hashlib.sha256(image).hexdigest()}))
'''

VERIFY_FILES = r'''
import hashlib, json
from pathlib import Path
root = Path('/storage')
fixture = json.loads((root / 'volume-test-fixture.json').read_text())
assert fixture['purpose'] == 'synthetic-volume-test'
source = root / 'transcripts'
actual = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in sorted(source.rglob('*')) if p.is_file()}
assert actual == fixture['manifest'], 'Synthetic sources were modified'
assert (root / 'models' / 'fixture-only.bin').read_bytes() == b'Synthetic model persistence marker; not a Whisper model.\n'
assert (root / 'work' / 'fixture-only.txt').read_text() == 'Synthetic work persistence marker.\n'
assert len(list((root / 'models').iterdir())) == 1, 'A model was downloaded during the test'
assert len(list((root / 'work').iterdir())) == 1, 'Video processing was started'
print(json.dumps({'files': len(actual), 'models_downloaded': 0, 'processing_started': False}))
'''

OFFLINE = VERIFY_FILES + r'''
import re, sys
from fastapi.testclient import TestClient
from backend.app import create_app
expected = json.loads(sys.argv[1])
with TestClient(create_app(initial_scan=False, start_jobs=False, download_thumbnails=False)) as client:
    stats = client.get('/api/stats').json()
    assert (stats['videos'], stats['deleted_videos']) == (1, 1)
    active = client.get('/api/videos?q=automacao').json()
    assert active['total'] == 1 and active['items'][0]['id'] == 'volume_keep'
    trash = client.get('/api/videos?deleted=true').json()
    assert trash['total'] == 1 and trash['items'][0]['id'] == 'volume_trash'
    assert trash['items'][0]['categories'] == expected['categories']
    assert trash['items'][0]['deleted_at'] == expected['deleted_at']
    assert client.get('/api/videos/volume_trash').status_code == 404
    detail = client.get('/api/videos/volume_keep').json()
    segments = client.get('/api/videos/volume_keep/segments').json()
    assert segments['total'] == 2 and 'Persistência' in segments['items'][0]['text']
    assert detail['has_method'] and 'Um método artificial.' in detail['method']
    assert {f['filename'] for f in detail['downloads']} >= {'transcript.txt', 'transcript.srt', 'transcript.json'}
    for download in detail['downloads']:
        response = client.get(download['url'])
        assert response.status_code == 200
        original = source / 'volume_keep' / download['filename']
        assert response.content == original.read_bytes()
    cover = client.get(detail['thumbnail_url'])
    assert cover.status_code == 200
    assert hashlib.sha256(cover.content).hexdigest() == expected['thumbnail_sha256']
    assert client.get('/api/videos/volume_trash/thumbnail').status_code == 200
    tasks = client.get('/api/jobs').json()
    watches = client.get('/api/watchers').json()
    assert watches['total'] == 1 and watches['items'][0]['id'] == expected['watcher']
    assert watches['items'][0]['baseline'] == 1 and watches['items'][0]['language'] == 'es'
    assert tasks['total'] == 2
    assert {j['id']: j['status'] for j in tasks['items']} == expected['jobs']
    index = client.get('/')
    assert index.status_code == 200 and '<div id="root">' in index.text
    assets = re.findall(r'(?:src|href)="(/assets/[^"?#]+)', index.text)
    assert len(assets) >= 2
    font_count = 0
    for asset in assets:
        response = client.get(asset)
        assert response.status_code == 200 and response.content
        if asset.endswith('.css'):
            fonts = set(re.findall(r'url\((/assets/[^)]+\.woff2)\)', response.text))
            assert fonts, 'CSS has no local fonts'
            for font in fonts:
                assert client.get(font).status_code == 200
            font_count += len(fonts)
    print(json.dumps({'offline': True, 'active': stats['videos'], 'trash': stats['deleted_videos'],
                      'jobs': tasks['total'], 'assets': len(assets), 'font_files': font_count}))
'''


def docker(*args: str, timeout: int = 70) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"docker {args[0]} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def api(path: str, method: str = "GET", body=None):
    # Docker Desktop can block host-published ports on internal networks. Keep
    # outbound isolation and exercise the live HTTP server from its loopback.
    assert CONTAINER is not None
    return json.loads(docker("exec", CONTAINER, "python", "-c", API_REQUEST, path, method, json.dumps(body)))


def ready(total: int = 2):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            stats = api("/stats")
            if stats["videos"] + stats["deleted_videos"] == total and not stats["scanning"]:
                return
        except (RuntimeError, OSError):
            pass
        time.sleep(.3)
    raise AssertionError("The synthetic container did not finish importing")


def main() -> int:
    global CONTAINER
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="youtube-catalog:local", help="Image already built locally; it will not be pulled")
    args = parser.parse_args()
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", 8767)) == 0:
            raise SystemExit("Port 8767 is occupied; no resources were created or modified.")
    image_id = docker("image", "inspect", "--format", "{{.Id}}", args.image)
    name = "youtube-catalog-volume-check-" + uuid.uuid4().hex[:12]
    CONTAINER = name
    volume, network = name + "-storage", name + "-network"
    report = {"image": args.image, "image_id": image_id, "port": 8767,
              "volume": volume, "network": "internal; offline phase: none",
              "synthetic_sources_only": True, "job_worker_enabled": False,
              "limits": "Verifies queued/cancelled persistence; does not run downloads or real transcription.",
              "checks": [], "errors": []}
    created_volume = created_network = False
    common = ["--pull=never", "--read-only", "--tmpfs", "/tmp:rw,size=64m,mode=1777",
              "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
              "--mount", f"type=volume,source={volume},target=/storage",
              "-e", "DOWNLOAD_THUMBNAILS=false", "-e", "JOB_WORKER_ENABLED=false"]

    def ok(message: str):
        report["checks"].append(message)
        print("OK " + message, flush=True)

    def start_new():
        docker("run", "-d", "--name", name, "--init", *common, "--network", network,
               "-p", "127.0.0.1:8767:8765", image_id)
        ready()

    try:
        docker("volume", "create", "--label", "youtube-catalog.test=synthetic-volume", volume)
        created_volume = True
        docker("network", "create", "--internal", "--label", "youtube-catalog.test=synthetic-volume", network)
        created_network = True
        assert docker("network", "inspect", "--format", "{{.Internal}}", network) == "true"
        seed = json.loads(docker("run", "--rm", *common, "--network", "none", "--entrypoint", "python", image_id, "-c", SEED))
        assert seed["files"] == 10
        report["source_files"] = seed["files"]
        print("Ready: disposable volume, ten synthetic files and internal network", flush=True)
        start_new()
        assert api("/stats")["videos"] == 2 and api("/jobs")["total"] == 0
        ok("New named volume imports only two synthetic videos, with no external network")

        category = api("/categories", "POST", {"name": "Preferência no volume"})
        api(f"/categories/{category['id']}", "PATCH", {"name": "Preferência persistente"})
        chosen = api(f"/videos/{TRASH}/categories", "PUT", {"category_ids": [category["id"]]})
        queued = api("/jobs", "POST", {"url": "https://www.youtube.com/watch?v=abcdefghijk", "kind": "video", "language": "en"})
        cancelled = api("/jobs", "POST", {"url": "https://www.youtube.com/playlist?list=PLsynthetic_volume_test", "kind": "playlist", "language": "es"})
        cancelled = api(f"/jobs/{cancelled['id']}/cancel", "POST")
        assert queued["status"] == "queued" and cancelled["status"] == "cancelled"
        subscription = api("/watchers", "POST", {"url": "https://www.youtube.com/playlist?list=PLsynthetic_watch_test", "name": "Synthetic source", "language": "es", "interval_minutes": 60, "initial_mode": "new"})
        # Seed a successful synthetic baseline without consulting YouTube.
        docker("exec", name, "python", "-c", "import sqlite3,sys; c=sqlite3.connect('/storage/catalog/catalog.sqlite3'); c.execute(\"UPDATE watch_sources SET initialized=1 WHERE id=?\",(sys.argv[1],)); c.execute(\"INSERT INTO watch_seen VALUES(?,?,'Synthetic baseline','2026-01-01T00:00:00+00:00','baseline',NULL)\",(sys.argv[1],'abcdefghijk')); c.commit()", subscription["id"])
        api(f"/videos/{TRASH}", "DELETE")
        expected = {"categories": chosen["categories"], "added_at": chosen["added_at"],
                    "deleted_at": api("/videos?deleted=true")["items"][0]["deleted_at"],
                    "jobs": {queued["id"]: "queued", cancelled["id"]: "cancelled"},
                    "thumbnail_sha256": seed["thumbnail_sha256"], "watcher": subscription["id"]}

        def assert_saved():
            stats = api("/stats")
            assert (stats["videos"], stats["deleted_videos"]) == (1, 1)
            assert [v["id"] for v in api("/videos")["items"]] == [KEEP]
            trash = api("/videos?deleted=true")
            assert trash["total"] == 1 and trash["items"][0]["id"] == TRASH
            assert trash["items"][0]["deleted_at"] == expected["deleted_at"]
            assert trash["items"][0]["categories"] == expected["categories"]
            jobs = api("/jobs")
            watches = api("/watchers")
            assert watches["total"] == 1 and watches["items"][0]["id"] == expected["watcher"]
            assert watches["items"][0]["baseline"] == 1 and watches["items"][0]["initialized"]
            assert watches["items"][0]["language"] == "es" and watches["items"][0]["interval_minutes"] == 60
            assert jobs["total"] == 2 and {j["id"]: j["status"] for j in jobs["items"]} == expected["jobs"]
            for job_id, status in expected["jobs"].items():
                detail = api("/jobs/" + job_id)
                assert detail["status"] == status and not detail["items"]
                assert detail["language"] == ("en" if status == "queued" else "es")
                assert detail["kind"] == ("video" if status == "queued" else "playlist")
            assert api(f"/videos/{KEEP}/segments")["total"] == 2
            docker("exec", name, "python", "-c", VERIFY_FILES)

        api("/scan", "POST")
        ready()
        assert_saved()
        ok("Categories, Trash and queued/cancelled jobs persist after reimporting")
        docker("stop", "--time", "40", name)
        docker("start", name)
        ready()
        assert_saved()
        ok("Stopping and starting preserves categories, Trash, the queue and all ten source files")
        docker("stop", "--time", "40", name)
        docker("rm", name)
        start_new()
        assert_saved()
        ok("Recreating the container with the same volume preserves data, models and working files")
        restored = api(f"/videos/{TRASH}/restore", "POST")
        assert restored["category_override"] and restored["categories"] == expected["categories"]
        assert restored["added_at"] == expected["added_at"] and restored["deleted_at"] is None
        assert "Um método artificial." in restored["method"]
        assert api(f"/videos/{TRASH}/segments")["total"] == 2
        api(f"/videos/{TRASH}", "DELETE")
        expected["deleted_at"] = api("/videos?deleted=true")["items"][0]["deleted_at"]
        assert_saved()
        ok("Restoring after recreation recovers text, method analysis, addition date and edited categories")
        docker("stop", "--time", "40", name)
        docker("rm", name)
        offline = docker("run", "--rm", *common, "--network", "none", "--entrypoint", "python", image_id,
                         "-c", OFFLINE, json.dumps(expected))
        report["offline"] = json.loads(offline.splitlines()[-1])
        ok("Offline: interface, local fonts, accent-insensitive search, reading, downloads, thumbnail and persistent queue")
    except Exception as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        print("FAILED " + report["errors"][-1], file=sys.stderr, flush=True)
        logs = subprocess.run(["docker", "logs", "--tail", "35", name], capture_output=True, text=True)
        if logs.returncode == 0:
            report["container_logs"] = logs.stdout + logs.stderr
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        cleanup_errors = []
        for kind, resource, created in (("network", network, created_network), ("volume", volume, created_volume)):
            if created:
                result = subprocess.run(["docker", kind, "rm", resource], capture_output=True, text=True)
                if result.returncode:
                    cleanup_errors.append(f"{resource}: {result.stderr.strip()}")
        report["cleanup_errors"] = cleanup_errors
        report["errors"].extend(cleanup_errors)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Report: {REPORT}", flush=True)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
