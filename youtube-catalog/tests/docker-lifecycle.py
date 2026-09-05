#!/usr/bin/env python3
"""Check Docker persistence and offline operation using disposable catalog data."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parents[1]
NAME = f"youtube-catalog-check-{os.getpid()}"
BASE = "http://127.0.0.1:8766"


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout.strip()


def api(path, method="GET", body=None):
    request = Request(BASE + "/api" + path, method=method,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def ready():
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            stats = api("/stats")
            if stats["videos"] and not stats["scanning"]:
                return
        except (URLError, OSError):
            pass
        time.sleep(.3)
    raise AssertionError("Container de teste não terminou a importação")


def main():
    with tempfile.TemporaryDirectory(prefix="youtube-catalog-lifecycle-") as directory:
        test_data = Path(directory)
        covers = PROJECT / "data" / "thumbnails"
        if covers.is_dir():
            (test_data / "thumbnails").mkdir()
            for cover in covers.iterdir():
                if cover.suffix in (".jpg", ".png", ".webp"):
                    shutil.copy2(cover, test_data / "thumbnails" / cover.name)
        common = ["--name", NAME, "--user", f"{os.getuid()}:{os.getgid()}",
                  "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL",
                  "--security-opt", "no-new-privileges:true",
                  "-v", f"{PROJECT.parent / 'yt-transcripts'}:/transcripts:ro",
                  "-v", f"{test_data}:/data", "-e", "TRANSCRIPTS_DIR=/transcripts",
                  "-e", "DATA_DIR=/data", "-e", "WORK_DIR=/tmp/jobs-work",
                  "-e", "MODEL_DIR=/tmp/jobs-models", "-e", "JOB_WORKER_ENABLED=false",
                  "-e", "DOWNLOAD_THUMBNAILS=false"]
        run_args = ["run", "-d", *common, "-p", "127.0.0.1:8766:8765", "youtube-catalog:local"]
        try:
            docker(*run_args)
            ready()
            selected = api("/videos?page_size=1")["items"][0]["id"]
            category = api("/categories", "POST", {"name": "Categoria temporária de teste"})
            api(f"/categories/{category['id']}", "PATCH", {"name": "Persistência confirmada"})
            api(f"/videos/{selected}/categories", "PUT", {"category_ids": [category["id"]]})

            def assert_saved():
                detail = api(f"/videos/{selected}")
                assert detail["category_override"]
                assert detail["categories"] == [{"id": category["id"], "name": "Persistência confirmada"}]

            api("/scan", "POST")
            ready()
            assert_saved()
            print("OK categorias criadas, renomeadas e preservadas após atualização", flush=True)
            docker("stop", NAME)
            docker("start", NAME)
            ready()
            assert_saved()
            print("OK categorias preservadas após desligar e ligar", flush=True)
            api(f"/videos/{selected}", "DELETE")
            api("/scan", "POST")
            ready()

            def assert_deleted():
                assert selected not in {v["id"] for v in api("/videos?page_size=100")["items"]}
                assert selected in {v["id"] for v in api("/videos?deleted=true&page_size=100")["items"]}

            assert_deleted()
            docker("stop", NAME)
            docker("start", NAME)
            ready()
            assert_deleted()
            print("OK exclusão preservada após atualizar, desligar e ligar", flush=True)
            docker("rm", "-f", NAME)
            docker(*run_args)
            ready()
            assert_deleted()
            api(f"/videos/{selected}/restore", "POST")
            assert_saved()
            print("OK exclusão preservada na recriação e restauração com categorias intactas", flush=True)
            docker("rm", "-f", NAME)

            offline = '''
from backend.app import create_app
from fastapi.testclient import TestClient
from pathlib import Path
with TestClient(create_app(initial_scan=False, download_thumbnails=False)) as client:
    assert client.get('/').status_code == 200
    stats=client.get('/api/stats').json()
    assert stats['videos'] > 0
    video=client.get('/api/videos?page_size=1').json()['items'][0]
    assert client.get('/api/videos?q=Claude').json()['total'] > 0
    assert client.get('/api/videos/'+video['id']+'/segments').json()['items']
    assert client.get('/api/videos/'+video['id']).json()['downloads']
    assert client.get(video['thumbnail_url']).status_code == 200
    covers=[p for p in Path('/data/thumbnails').glob('*') if p.suffix in ('.jpg','.png','.webp')]
    for cover in covers[:1]:
        r=client.get('/api/videos/'+cover.stem+'/thumbnail')
        assert r.content == cover.read_bytes()
    print('OK interface, busca, leitura e capas com container sem rede; capas em cache:',len(covers))
'''
            print(docker("run", "--rm", *common, "--network", "none", "youtube-catalog:local", "python", "-c", offline), flush=True)
        finally:
            subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)


if __name__ == "__main__":
    main()
