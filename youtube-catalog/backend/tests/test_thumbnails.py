import httpx
import pytest

from backend.thumbnails import MAX_BYTES, ThumbnailManager, valid_url
from backend.tests.conftest import make_video


@pytest.mark.parametrize("url", ["http://i.ytimg.com/foo", "https://ytimg.com.evil.test/foo", "https://127.0.0.1/foo", "https://i.ytimg.com:8443/foo", "https://user:secret@i.ytimg.com/foo", "file:///secret"])
def test_remote_thumbnail_url_allowlist(url):
    assert not valid_url(url)


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"location": "https://127.0.0.1/secret"}),
    httpx.Response(200, headers={"content-type": "image/svg+xml"}, content=b"<svg/>"),
    httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"not-an-image"),
    httpx.Response(200, headers={"content-type": "image/jpeg", "content-length": str(MAX_BYTES + 1)}, content=b""),
    httpx.Response(404),
])
def test_image_failures_keep_fallback_and_backoff(catalog, response):
    calls = []

    def handler(request):
        calls.append(request.url)
        return response

    manager = ThumbnailManager(catalog, True, httpx.MockTransport(handler))
    manager.download("video_1", "https://i.ytimg.com/vi/video_1/hqdefault.jpg")
    assert manager.cached("video_1") is None
    assert "video_1" in manager.retry_after
    assert len(calls) == 1
    assert not list(manager.directory.glob("*.part"))
    manager.close()


def test_valid_image_is_cached_without_remote_dependency(catalog, collection):
    make_video(collection)
    catalog.scan()
    data = b"\xff\xd8\xff" + b"image data" + b"\xff\xd9"
    manager = ThumbnailManager(catalog, True, httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "image/jpeg"}, content=data)))
    catalog.thumbnail_manager = manager
    placeholder_url = catalog.detail("video_1")["thumbnail_url"]
    manager.download("video_1", "https://i.ytimg.com/vi/video_1/hqdefault.jpg")
    assert manager.cached("video_1").read_bytes() == data
    assert catalog.detail("video_1")["thumbnail_url"] != placeholder_url
    manager.close()
