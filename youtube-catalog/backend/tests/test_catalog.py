import json
import shutil
import threading

import pytest

from backend.catalog import Catalog, parse_segments
from backend.tests.conftest import make_video


def test_incremental_import_preserves_originals_and_dates(catalog, collection, monkeypatch):
    folder = make_video(collection)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    catalog.scan()
    original = catalog.detail("video_1")
    assert catalog.stats()["videos"] == 1
    assert catalog.stats()["methods"] == 1
    assert original["language"] == "pt"
    assert original["categories"] == [{"id": "C1", "name": "Agentes e operações"}]
    assert original["method"].startswith("# Método")
    original_parse = catalog.parse_video
    calls = []

    def spy(video_id):
        calls.append(video_id)
        return original_parse(video_id)

    monkeypatch.setattr(catalog, "parse_video", spy)
    catalog.scan()
    assert calls == []
    assert before == {p.name: p.read_bytes() for p in folder.iterdir()}
    metadata = json.loads((folder / "video.json").read_text())
    metadata["title"] = "Novo título"
    (folder / "video.json").write_text(json.dumps(metadata))
    catalog.scan()
    assert calls == ["video_1"]
    assert catalog.detail("video_1")["title"] == "Novo título"
    assert catalog.detail("video_1")["added_at"] == original["added_at"]
    assert catalog.stats()["videos"] == 1


def test_overrides_rename_empty_reset_and_restart(catalog, collection):
    folder = make_video(collection)
    catalog.scan()
    category = catalog.save_category("Minha pesquisa")
    catalog.assign_categories("video_1", [category["id"], "C1"])
    catalog.save_category("Agentes favoritos", "C1")
    (folder / "METODO.md").write_text('---\ncluster_id: C2\ncluster: "Growth"\n---\nNovo método.')
    catalog.scan()
    restarted = Catalog(collection, catalog.data)
    detail = restarted.detail("video_1")
    assert detail["category_override"]
    assert {c["id"] for c in detail["categories"]} == {category["id"], "C1"}
    assert next(c for c in restarted.categories() if c["id"] == "C1")["name"] == "Agentes favoritos"
    restarted.assign_categories("video_1", [])
    restarted.scan()
    assert restarted.detail("video_1")["categories"] == []
    assert restarted.list_videos(category="uncategorized")["total"] == 1
    restarted.assign_categories("video_1", None)
    assert restarted.detail("video_1")["categories"] == [{"id": "C2", "name": "Growth"}]
    with pytest.raises(ValueError):
        restarted.save_category("grówth")


def test_corrupt_required_files_preserve_last_good_and_recover(catalog, collection):
    folder = make_video(collection)
    catalog.scan()
    (folder / "transcript.json").write_text('{"incomplete":')
    catalog.scan()
    assert catalog.stats()["videos"] == 1
    assert catalog.segments("video_1")["total"] == 3
    assert catalog.stats()["warnings"][0]["video_id"] == "video_1"
    make_video(collection, texts=["Transcrição reparada"])
    catalog.scan()
    assert catalog.segments("video_1")["total"] == 1
    assert catalog.stats()["warnings"] == []
    (folder / "METODO.md").write_text("---\ninvalid: [\n---\n# Método")
    catalog.scan()
    assert catalog.detail("video_1")["method"].startswith("# Método")
    assert catalog.stats()["warnings"]


def test_missing_directory_unavailable_and_restore(catalog, collection, tmp_path):
    folder = make_video(collection)
    catalog.scan()
    catalog.assign_categories("video_1", [])
    backup = tmp_path / "backup"
    shutil.move(str(folder), backup)
    catalog.scan()
    assert catalog.stats()["videos"] == 1
    assert not catalog.detail("video_1")["available"]
    assert catalog.detail("video_1")["downloads"] == []
    assert catalog.detail("video_1")["category_override"]
    shutil.move(str(backup), folder)
    catalog.scan()
    assert catalog.detail("video_1")["available"]
    assert catalog.detail("video_1")["category_override"]
    assert not catalog.stats()["warnings"]


def test_incomplete_new_video_does_not_block_others(catalog, collection):
    broken = make_video(collection, "broken")
    (broken / "transcript.json").unlink()
    make_video(collection, "complete", category=None)
    catalog.scan()
    assert catalog.stats()["videos"] == 1
    assert catalog.stats()["warnings"][0]["video_id"] == "broken"
    assert catalog.list_videos(category="uncategorized")["items"][0]["id"] == "complete"
    assert catalog.detail("complete")["method"] is None


def test_missing_required_file_marks_unavailable_but_keeps_snapshot(catalog, collection):
    folder = make_video(collection)
    catalog.scan()
    original = (folder / "transcript.json").read_bytes()
    (folder / "transcript.json").unlink()
    catalog.scan()
    assert not catalog.detail("video_1")["available"]
    assert catalog.segments("video_1")["total"] == 3
    assert catalog.detail("video_1")["downloads"] == []
    (folder / "transcript.json").write_bytes(original)
    catalog.scan()
    assert catalog.detail("video_1")["available"]
    assert not catalog.stats()["warnings"]


def test_accent_insensitive_plain_search_filters_and_jump(catalog, collection):
    make_video(collection)
    make_video(collection, "video_2", title="Marketing", channel="Outro Canal", category="C2", category_name="Growth", texts=["Um segundo conteúdo."])
    catalog.scan()
    results = catalog.list_videos(q="ACAO")
    assert results["total"] == 1
    assert results["items"][0]["match"]["start_ms"] == 0
    assert results["items"][0]["match"]["segment_index"] == 0
    assert catalog.list_videos(q="cafe", category="C1", language="pt", channel="channel-Canal Café")["total"] == 1
    assert catalog.list_videos(q="cafe", category="C2")["total"] == 0
    assert catalog.list_videos(q='" OR * -') ["total"] == 0
    assert catalog.list_videos(q="ação eficiente")["total"] == 1
    assert catalog.list_videos(sort="title", page_size=1, page=2)["items"][0]["id"] == "video_2"
    assert catalog.segments("video_1", q="operacao")["items"][0]["index"] == 1


def test_milliseconds_seconds_and_preferred_speakers(catalog, collection):
    folder = make_video(collection)
    original = json.loads((folder / "transcript.json").read_text())
    for segment in original["transcription"]:
        segment["speaker"] = "SPEAKER_01"
    (folder / "transcript-speakers.json").write_text(json.dumps(original))
    make_video(collection, "seconds", seconds=True)
    catalog.scan()
    assert catalog.segments("video_1")["items"][1]["start_ms"] == 10250
    assert catalog.segments("seconds")["items"][1]["start_ms"] == 10250
    assert catalog.segments("video_1")["items"][0]["speaker"] == "SPEAKER_01"
    assert catalog.detail("video_1")["has_speakers"]
    (folder / "transcript-speakers.json").write_text("[]")
    catalog.scan()
    assert not catalog.detail("video_1")["has_speakers"]
    assert catalog.segments("video_1")["items"][1]["start_ms"] == 10250
    assert "falantes" in catalog.stats()["warnings"][0]["message"]
    with pytest.raises(ValueError):
        parse_segments({"segments": [{"text": "bad", "start": 2, "end": 1}]})


def test_large_transcript_paginates(catalog, collection):
    make_video(collection, texts=[f"Segmento {index}" for index in range(2400)])
    catalog.scan()
    result = catalog.segments("video_1", offset=2300, limit=100)
    assert result["total"] == 2400
    assert len(result["items"]) == 100
    assert result["items"][-1]["start_ms"] > 6 * 60 * 60 * 1000


def test_symlink_outside_collection_is_rejected(catalog, collection, tmp_path):
    folder = make_video(collection)
    outside = tmp_path / "private.json"
    outside.write_text((folder / "transcript.json").read_text())
    (folder / "transcript.json").unlink()
    (folder / "transcript.json").symlink_to(outside)
    catalog.scan()
    assert catalog.stats()["videos"] == 0
    assert "fora da coleção" in catalog.stats()["warnings"][0]["message"]


def test_local_suggestions_exclude_assigned_and_need_positive_similarity(catalog, collection):
    make_video(collection, "target", category=None)
    make_video(collection, "neighbor", category="C1")
    catalog.scan()
    suggestions = catalog.detail("target")["suggestions"]
    assert suggestions and suggestions[0]["id"] == "C1"
    assert 0 < suggestions[0]["score"] <= 1
    catalog.assign_categories("target", ["C1"])
    assert catalog.detail("target")["suggestions"] == []


def test_scans_do_not_overlap(catalog, collection, monkeypatch):
    make_video(collection)
    entered, release = threading.Event(), threading.Event()
    original = catalog.parse_video
    count = []

    def paused(video_id):
        count.append(video_id)
        entered.set()
        assert release.wait(5)
        return original(video_id)

    monkeypatch.setattr(catalog, "parse_video", paused)
    catalog.request_scan()
    assert entered.wait(5)
    catalog.request_scan()
    catalog.scan()
    release.set()
    catalog.scan_thread.join(5)
    assert count == ["video_1"]
    assert not catalog.scanning
