"""Skill tests use disposable catalogs and a fake local engine, never real media."""
import io
import json
import threading
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.skills import SkillError, SkillQueue, package_result, recommendations
from backend.tests.conftest import make_video


def output():
    markdown = "---\nname: pricing-workflow\ndescription: Design a pricing experiment.\n---\n\n# Workflow\n\nCompare the evidence.\n"
    return {"title": "Pricing workflow", "name": "pricing-workflow", "skill_markdown": markdown,
            "files": [{"path": "SKILL.md", "content": markdown}, {"path": "references/sources.md", "content": "Video evidence"}],
            "warnings": ["Check the source examples."], "model": "fixture-local", "validation": {"grounded": True}}


class FakeEngine:
    def __init__(self):
        self.sources = None
        self.failure = None

    def status(self):
        return {"available": True, "model": "fixture-local", "models": ["fixture-local"]}

    def generate(self, sources, title, objective, language, checkpoint):
        self.sources = sources
        checkpoint("generating", 50, "Working locally")
        if self.failure:
            raise self.failure
        return output()


@pytest.fixture
def queue(catalog, collection):
    make_video(collection)
    catalog.scan()
    return SkillQueue(catalog, FakeEngine())


def thematic(catalog, collection, video_id, title, tags, description, category="C1", method=None):
    make_video(collection, video_id, title=title, channel=video_id, category=category)
    catalog.scan()
    with catalog.db() as conn:
        conn.execute("UPDATE videos SET tags=?,description=?,method=? WHERE id=?", (json.dumps(tags), description, method, video_id))


def test_recommendations_have_grounded_tags_and_exclude_ineligible(catalog, collection):
    thematic(catalog, collection, "pricing", "Paywall pricing experiments", ["paywall", "pricing"], "Optimize subscription prices through controlled conversion experiments.")
    thematic(catalog, collection, "design", "Paywall onboarding design", ["paywall", "onboarding"], "Design onboarding screens that communicate subscription benefits clearly.")
    thematic(catalog, collection, "garden", "Growing tropical orchids", ["orchids"], "Water roots and balance tropical humidity.")
    thematic(catalog, collection, "deleted", "Paywall test setup", ["paywall"], "Measure experiments and subscription retention.")
    thematic(catalog, collection, "missing", "Paywall cohort tracking", ["paywall"], "Subscription analytics across different cohorts.")
    thematic(catalog, collection, "empty", "Paywall segmentation", ["paywall"], "Build offers for segmented audiences.")
    catalog.set_deleted("deleted", True)
    with catalog.db() as conn:
        conn.execute("UPDATE videos SET available=0 WHERE id='missing'")
        conn.execute("DELETE FROM segments WHERE video_id='empty'")
    result = recommendations(catalog, "en")
    assert result["total"] == 1
    group = result["items"][0]
    assert set(group["video_ids"]) == {"pricing", "design"}
    assert group["shared_tags"] == ["paywall"]
    assert group["title"] == "Combine knowledge about paywall"
    assert "paywall" in group["reason"]
    assert "shared tags" in group["reason"]
    assert [video["id"] for video in group["videos"]] == group["video_ids"]
    assert recommendations(catalog, "es")["items"][0]["reason"].startswith("Conexión")
    assert recommendations(catalog, "es")["items"][0]["title"] == "Combinar conocimientos sobre paywall"


def test_category_and_method_boilerplate_cannot_create_random_suggestion(catalog, collection):
    boilerplate = "## Quando usar\nUse agent automation to improve business processes and customer strategy.\n## Processo\n1. Review inputs and outputs carefully before executing the workflow.\n## Limitações\nAlways verify evidence and validate results."
    thematic(catalog, collection, "garden", "Growing tropical orchids", ["tutorial"], "Humidity roots and blooming", method=boilerplate)
    thematic(catalog, collection, "galaxy", "Observing neutron stars", ["course"], "Telescopes pulsars gravity radiation", method=boilerplate)
    assert recommendations(catalog) == {"items": [], "total": 0}


def test_source_specific_method_process_can_connect_complementary_videos(catalog, collection):
    thematic(catalog, collection, "one", "Revenue experiment setup", [], "Build initial commercial hypotheses", method="## Descrição\nMeasure paywall conversion with subscription cohorts.\n## Processo\nCompare pricing options against a measured baseline and estimate confidence.")
    thematic(catalog, collection, "two", "Subscription experiment analytics", [], "Explore retention and churn", method="## Descrição\nAnalyze paywall conversion across subscription cohorts.\n## Processo\nTrack pricing changes over time with retention dashboards and segment customers.")
    result = recommendations(catalog)
    assert result["total"] == 1
    assert result["items"][0]["shared_tags"] == []
    assert "termos presentes" in result["items"][0]["reason"]


def test_success_preserves_originals_and_produces_safe_download(queue, collection):
    before = {str(path.relative_to(collection)): path.read_bytes() for path in collection.rglob("*") if path.is_file()}
    job = queue.enqueue(["video_1"], "My skill", "Combine useful evidence", "en")
    assert job["status"] == "queued"
    assert queue.run_once()
    completed = queue.detail(job["id"])
    assert completed["status"] == "completed" and completed["progress"] == 100
    assert completed["files"] and completed["warnings"]
    assert "skill_markdown" not in queue.list()["items"][0]
    payload, filename = queue.download(job["id"])
    assert filename == "pricing-workflow.zip"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == ["pricing-workflow/SKILL.md", "pricing-workflow/references/sources.md"]
        assert archive.read("pricing-workflow/SKILL.md").decode() == completed["skill_markdown"]
    assert before == {str(path.relative_to(collection)): path.read_bytes() for path in collection.rglob("*") if path.is_file()}
    assert queue.engine.sources[0]["segments"][1]["start_ms"] == 10250
    restored = SkillQueue(queue.catalog, FakeEngine())
    assert restored.download(job["id"]) == (payload, filename)
    assert not restored.run_once()


def test_snapshot_survives_source_updates_and_permanent_catalog_removal(queue):
    job = queue.enqueue(["video_1"])
    with queue.catalog.db() as conn:
        conn.execute("UPDATE segments SET text='Changed later'")
        conn.execute("DELETE FROM videos WHERE id='video_1'")
    assert queue.run_once()
    assert queue.detail(job["id"])["status"] == "completed"
    assert queue.engine.sources[0]["segments"][0]["text"] == "Ação e estratégia no início."
    assert queue.engine.sources[0]["title"] == "Automação com agentes"


@pytest.mark.parametrize("ids,title,objective,language,code", [
    ([], "", "", "pt", "invalid_selection"),
    (["video_1"] * 2, "", "", "pt", "invalid_selection"),
    (["../outside"], "", "", "pt", "invalid_selection"),
    ([f"v{i}" for i in range(9)], "", "", "pt", "invalid_selection"),
    (["video_1"], "x" * 121, "", "pt", "invalid_input"),
    (["video_1"], "", "x" * 2001, "pt", "invalid_input"),
    (["video_1"], "", "", "fr", "invalid_input"),
    (["missing"], "", "", "pt", "source_unavailable"),
])
def test_input_bounds_are_rejected_without_enqueuing(queue, ids, title, objective, language, code):
    with pytest.raises(SkillError) as error:
        queue.enqueue(ids, title, objective, language)
    assert error.value.code == code
    assert queue.list()["total"] == 0


def test_trash_unavailable_and_empty_transcript_cannot_be_enqueued(queue):
    queue.catalog.set_deleted("video_1", True)
    with pytest.raises(SkillError, match="Lixeira"):
        queue.enqueue(["video_1"])
    queue.catalog.set_deleted("video_1", False)
    with queue.catalog.db() as conn:
        conn.execute("UPDATE videos SET available=0")
    with pytest.raises(SkillError):
        queue.enqueue(["video_1"])
    with queue.catalog.db() as conn:
        conn.execute("UPDATE videos SET available=1")
        conn.execute("DELETE FROM segments")
    with pytest.raises(SkillError):
        queue.enqueue(["video_1"])


def test_failure_is_visible_and_retry_keeps_snapshot(queue):
    queue.engine.failure = SkillError("model_missing", "Install a local model", 503)
    job = queue.enqueue(["video_1"])
    queue.run_once()
    assert queue.detail(job["id"])["error_code"] == "model_missing"
    with pytest.raises(SkillError):
        queue.download(job["id"])
    queue.engine.failure = None
    assert queue.retry(job["id"])["status"] == "queued"
    queue.run_once()
    assert queue.detail(job["id"])["status"] == "completed"
    assert queue.list()["total"] == 1
    with pytest.raises(SkillError) as error:
        queue.retry(job["id"])
    assert error.value.code == "invalid_state"


def test_queued_cancellation_is_persistent_and_can_be_retried(queue):
    job = queue.enqueue(["video_1"])
    assert queue.cancel(job["id"])["status"] == "cancelled"
    restored = SkillQueue(queue.catalog, FakeEngine())
    assert not restored.run_once()
    assert restored.retry(job["id"])["status"] == "queued"
    restored.run_once()
    assert restored.detail(job["id"])["status"] == "completed"


class PausingEngine(FakeEngine):
    def __init__(self, catalog):
        super().__init__()
        self.entered, self.release = threading.Event(), threading.Event()
        self.catalog = catalog

    def generate(self, sources, title, objective, language, checkpoint):
        assert self.catalog.scan_lock.acquire(blocking=False), "The model call must not hold the scanner lock"
        self.catalog.scan_lock.release()
        self.entered.set()
        while not self.release.wait(.02):
            checkpoint("generating", 40, "Waiting in fake engine")
        checkpoint("generating", 80, "Fake engine finished")
        return output()


def test_running_cancellation_interrupts_checkpoint_and_publishes_nothing(queue):
    queue._engine = PausingEngine(queue.catalog)
    job = queue.enqueue(["video_1"])
    worker = threading.Thread(target=queue.run_once)
    worker.start()
    assert queue.engine.entered.wait(2)
    assert not queue.run_once(), "Only one generation can run at a time"
    queue.cancel(job["id"])
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert queue.detail(job["id"])["status"] == "cancelled"
    with pytest.raises(SkillError):
        queue.download(job["id"])


def test_shutdown_requeues_and_restart_completes_once(queue):
    queue._engine = PausingEngine(queue.catalog)
    job = queue.enqueue(["video_1"])
    queue.start()
    assert queue.engine.entered.wait(2)
    queue.close()
    assert not queue.thread.is_alive()
    assert queue.detail(job["id"])["status"] == "queued"
    restored = SkillQueue(queue.catalog, FakeEngine())
    restored.run_once()
    assert restored.detail(job["id"])["status"] == "completed"
    assert restored.list()["total"] == 1


def test_crash_recovery_preserves_cancellation_and_requeues_unfinished(queue):
    first, second = queue.enqueue(["video_1"]), queue.enqueue(["video_1"], objective="Another generation")
    with queue.catalog.db() as conn:
        conn.execute("UPDATE skill_jobs SET status='generating',stage='generating',progress=45")
        conn.execute("UPDATE skill_jobs SET cancel_requested=1 WHERE id=?", (second["id"],))
    restored = SkillQueue(queue.catalog, FakeEngine())
    assert restored.detail(first["id"])["status"] == "queued"
    assert restored.detail(second["id"])["status"] == "cancelled"
    assert restored.run_once()
    assert not restored.run_once()


@pytest.mark.parametrize("path", ["../outside.txt", "/tmp/file.txt", "references/../../outside.md", "scripts/run.sh", "references/nested/file.md", "references/file.md\\outside", "SKILL.md"])
def test_generated_package_rejects_traversal_executables_and_duplicates(path):
    result = output()
    result["files"].append({"path": path, "content": "Do not write this"})
    with pytest.raises(SkillError) as error:
        package_result(result)
    assert error.value.code == "invalid_package"


def test_inconsistent_or_oversized_package_is_rejected(monkeypatch):
    result = output()
    result["skill_markdown"] = "Different content"
    with pytest.raises(SkillError):
        package_result(result)
    monkeypatch.setattr("backend.skills.MAX_PACKAGE", 10)
    with pytest.raises(SkillError):
        package_result(output())


def test_api_contract_validations_download_and_cross_site_boundary(collection, tmp_path):
    make_video(collection)
    app = create_app(source_dir=collection, data_dir=tmp_path / "api", download_thumbnails=False,
                     initial_scan=False, start_jobs=False, start_skills=False, skill_engine=FakeEngine())
    with TestClient(app) as client:
        app.state.catalog.scan()
        assert client.get("/api/skills/status").json()["available"]
        assert client.get("/api/skills/suggestions?language=en").json() == {"items": [], "total": 0}
        assert client.get("/api/skills/missing").status_code == 404
        invalid = client.post("/api/skills", json={"video_ids": ["video_1"], "language": "fr"})
        assert invalid.status_code == 422 and invalid.json()["error_code"] == "invalid_input"
        assert client.post("/api/skills", json={"video_ids": ["video_1"]}, headers={"Origin": "https://outside.example"}).status_code == 403
        submitted = client.post("/api/skills", json={"video_ids": ["video_1"], "language": "en"})
        assert submitted.status_code == 202
        job = submitted.json()
        assert client.get(f"/api/skills/{job['id']}/download.zip").status_code == 409
        app.state.skills.run_once()
        detail = client.get(f"/api/skills/{job['id']}").json()
        assert detail["status"] == "completed" and detail["skill_markdown"]
        download = client.get(detail["download_url"])
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/zip"
        assert client.get("/api/skills").json()["total"] == 1
        assert client.post(f"/api/skills/{job['id']}/cancel").status_code == 409


def test_repeated_channel_description_does_not_connect_unrelated_topics(catalog, collection):
    repeated = "Pat Smith teaches practical methods for innovative business development."
    thematic(catalog, collection, "garden", "Orchid cultivation", ["tutorial"], repeated)
    thematic(catalog, collection, "galaxy", "Observing pulsars", ["course"], repeated)
    with catalog.db() as conn:
        conn.execute("UPDATE videos SET channel='Pat Smith',channel_id='same-channel'")
    assert recommendations(catalog)["total"] == 0


def test_pending_queue_is_bounded_and_complete_jobs_do_not_take_capacity(queue):
    first = queue.enqueue(["video_1"], title="Original")
    for index in range(29):
        queue.enqueue(["video_1"], objective=f"Generation {index}")
    assert queue.enqueue(["video_1"], title=" Original ")["id"] == first["id"]
    with pytest.raises(SkillError) as error:
        queue.enqueue(["video_1"])
    assert error.value.code == "queue_full" and error.value.status == 429
    queue.run_once()
    assert queue.enqueue(["video_1"])["status"] == "queued"


def test_active_duplicate_normalizes_selection_and_input_and_keeps_snapshot(queue, collection):
    make_video(collection, "video_2", title="Second source")
    queue.catalog.scan()
    original = queue.enqueue(["video_2", "video_1"], title=" A useful skill ", objective=" A useful objective ", language="en")
    duplicate = queue.enqueue(["video_1", "video_2"], title="A useful skill", objective="A useful objective", language="en")
    assert duplicate["id"] == original["id"]
    assert duplicate["video_ids"] == ["video_2", "video_1"]
    with queue.catalog.db() as conn:
        conn.execute("DELETE FROM videos")
    assert queue.enqueue(["video_1", "video_2"], title="A useful skill", objective="A useful objective", language="en")["id"] == original["id"]
    assert queue.list()["total"] == 1


def test_default_title_dedup_is_order_independent_and_cancel_retry_remain_valid(queue, collection):
    make_video(collection, "video_2", title="Second source")
    queue.catalog.scan()
    original = queue.enqueue(["video_2", "video_1"])
    assert queue.enqueue(["video_1", "video_2"])["id"] == original["id"]
    queue.cancel(original["id"])
    queue.retry(original["id"])
    assert queue.enqueue(["video_1", "video_2"])["id"] == original["id"]
    queue.run_once()
    assert queue.enqueue(["video_1", "video_2"])["id"] != original["id"]


def test_history_is_paginated_with_real_total_and_no_duplicate_pages(queue):
    for index in range(24):
        job = queue.enqueue(["video_1"], objective=f"History entry {index}")
        queue.cancel(job["id"])
    first, second = queue.list(), queue.list(page=2)
    assert (first["total"], len(first["items"]), first["page_size"]) == (24, 20, 20)
    assert (second["total"], len(second["items"]), second["page"]) == (24, 4, 2)
    assert not {job["id"] for job in first["items"]} & {job["id"] for job in second["items"]}
    assert queue.list(page=3)["items"] == []
