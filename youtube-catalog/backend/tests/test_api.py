import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.conftest import make_video


@pytest.fixture
def client(collection, tmp_path):
    make_video(collection)
    app = create_app(source_dir=collection, data_dir=tmp_path / "api-data", static_dir=tmp_path / "static", download_thumbnails=False, initial_scan=False)
    with TestClient(app) as client:
        app.state.catalog.scan()
        yield client


def test_api_contract_and_parameter_validation(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    stats = client.get("/api/stats").json()
    assert (stats["videos"], stats["channels"], stats["categories"], stats["methods"]) == (1, 1, 1, 1)
    card = client.get("/api/videos?q=acao").json()["items"][0]
    assert card["match"]["start_ms"] == 0
    assert card["thumbnail_url"].startswith("/api/")
    assert client.get("/api/videos/video_1/segments?limit=301").status_code == 422
    assert isinstance(client.get("/api/videos?page=0").json()["detail"], str)
    assert client.get("/api/videos?sort=invalid").status_code == 422
    assert client.get("/api/does-not-exist").status_code == 404
    assert client.get("/api/videos/missing").status_code == 404


def test_api_categories_and_cross_site_boundary(client):
    created = client.post("/api/categories", json={"name": "Pesquisa"})
    assert created.status_code == 201
    category_id = created.json()["id"]
    response = client.put("/api/videos/video_1/categories", json={"category_ids": [category_id]})
    assert response.json()["category_override"]
    assert client.patch(f"/api/categories/{category_id}", json={"name": "Pesquisa local"}).json()["name"] == "Pesquisa local"
    assert client.put("/api/videos/video_1/categories", json={"category_ids": ["fake"]}).status_code == 422
    assert client.delete("/api/videos/video_1/categories").json()["categories"][0]["id"] == "C1"
    assert client.post("/api/scan", headers={"Origin": "https://unrelated.example"}).status_code == 403
    assert client.post("/api/categories", json={"name": "Agentes e operações"}).status_code == 409


def test_download_whitelist_symlink_and_local_fallback(client, collection, tmp_path):
    response = client.get("/api/videos/video_1/files/transcript.txt")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert client.get("/api/videos/video_1/files/video.json").status_code == 404
    assert client.get("/api/videos/video_1/files/..%2Fvideo.json").status_code == 404
    private = tmp_path / "secret.txt"
    private.write_text("never served")
    target = collection / "video_1" / "transcript.txt"
    target.unlink()
    target.symlink_to(private)
    assert client.get("/api/videos/video_1/files/transcript.txt").status_code == 404
    image = client.get("/api/videos/video_1/thumbnail")
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/svg+xml")
    assert b"TRANSCRI" in image.content
