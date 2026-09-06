"""Behavioral boundaries of local synthesis, evidence selection and packaging."""
import json

import httpx
import pytest
import yaml

from backend.skill_engine import LANGUAGE_REMINDERS, MAX_CITATION_DIAGNOSTICS, MAX_RETRY_FEEDBACK, LocalSkillEngine, SkillDraft, SkillEngineError, local_url, select_evidence, validate_evidence, validate_language


def source(video_id, count=12):
    return {"id": video_id, "title": f"Dashboard layout {video_id}", "channel": "Example", "description": "A practical tutorial",
            "tags": ["dashboard", "layout"], "method": "## Processo\n\nComparar a hierarquia e verificar os dados.",
            "segments": [{"index": index, "start_ms": index * 1000, "end_ms": (index + 1) * 1000,
                          "text": f"Compare dashboard layout and verify the values for scenario {index}.", "speaker": None}
                         for index in range(count)]}


def draft():
    return {"name": "review-dashboard", "title": "Review a dashboard", "description": "Review dashboard layouts when an operational interface needs clearer decisions.",
            "when_to_use": "Apply this procedure to an existing operational dashboard.",
            "inputs": ["Current dashboard and the user's primary decision"],
            "steps": [{"title": "Check the data", "source_rule": "Compare the dashboard layout and verify its values in the scenario.", "instruction": "Compare the displayed values with the supplied dataset before changing the interface.",
                       "expected_result": "A list of discrepancies with the original data.", "evidence": [{"video_id": "first", "segment_index": 1, "quote": "Compare dashboard layout and verify the values for scenario 1."}]},
                      {"title": "Review the layout", "source_rule": "Compare dashboard layouts and verify the scenario values.", "instruction": "Group relevant metrics by the user's decision and compare the new hierarchy with the original.",
                       "expected_result": "An annotated layout preserving the verified values.", "evidence": [{"video_id": "second", "segment_index": 2, "quote": "Compare dashboard layout and verify the values for scenario 2."}]}],
            "deliverable": "An annotated dashboard review with verified values and a proposed hierarchy.",
            "quality_checks": ["All values match the source dataset.", "The primary decision is visibly supported."],
            "limitations": ["The user's decision and source dataset must be supplied."]}


def fake_engine(monkeypatch):
    engine = LocalSkillEngine(url="http://localhost:11434")
    monkeypatch.setattr(engine, "status", lambda: {"available": True, "model": "local-model", "models": ["local-model"]})
    monkeypatch.setattr(engine, "_generate", lambda *args: json.dumps(draft()))
    return engine


def portuguese_draft():
    result = draft()
    result.update(title="Revisar um dashboard", description="Revise o dashboard operacional para que as informações e os dados estejam coerentes com a decisão do usuário.",
                  when_to_use="Use quando o usuário precisa revisar as informações do dashboard existente.",
                  inputs=["Dashboard atual, decisão do usuário e dados de referência"],
                  deliverable="Um diagnóstico do dashboard com os dados verificados e uma proposta de organização da interface.",
                  quality_checks=["Os valores devem corresponder aos dados de referência.", "A proposta deve explicar a relação com a decisão do usuário."],
                  limitations=["A decisão do usuário e os dados de referência devem ser fornecidos antes da revisão."])
    for step in result["steps"]:
        step.update(title="Conferir os dados e o layout", source_rule="Compare o layout do dashboard e confira os valores do cenário antes da revisão.",
                    instruction="Confira os valores do dashboard com os dados fornecidos pelo usuário. Organize a proposta de layout com as informações relevantes para a decisão, sem alterar os dados originais.",
                    expected_result="Uma lista das diferenças entre o dashboard e os dados, com uma proposta de layout para a decisão.")
    return result


def test_english_procedure_is_rejected_despite_portuguese_metadata():
    generated = portuguese_draft()
    generated["steps"] = draft()["steps"]
    with pytest.raises(ValueError, match="Wrong prose language in procedure.*English.*Portuguese"):
        validate_language(SkillDraft.model_validate(generated), "pt")


def test_original_quotes_and_technical_terms_do_not_count_as_wrong_language():
    generated = portuguese_draft()
    # English source quotations can dominate the package without being authored
    # prose. Terms such as dashboard, layout and UI do not identify a language.
    for step in generated["steps"]:
        step["evidence"][0]["quote"] = "The values should match the data and the user should see the result before changing the layout. " * 4
        step["instruction"] += " Confira também UI, spacing, hover e empty state na proposta."
    validate_language(SkillDraft.model_validate(generated), "pt")


def test_short_technical_text_abstains_and_clear_spanish_is_recognized():
    generated = SkillDraft.model_validate(draft())
    for step in generated.steps:
        step.source_rule = "Dashboard layout review"
        step.title = "UI review"
        step.instruction = "Dashboard UI: spacing, hover, empty state."
        step.expected_result = "Layout review"
    generated.title = "Dashboard UI"
    generated.description = "Dashboard layout review"
    generated.when_to_use = "UI review"
    generated.inputs = ["Dashboard UI"]
    generated.deliverable = "Layout review"
    generated.quality_checks = ["Spacing", "Hover"]
    generated.limitations = ["UI context"]
    validate_language(generated, "pt")
    for step in generated.steps:
        step.instruction = "Comprueba el contenido de la pantalla con los datos del usuario y las decisiones del equipo. Organiza el resultado en una propuesta con las etiquetas y los valores, sin cambiar el significado del contenido."
    validate_language(generated, "es")
    with pytest.raises(ValueError, match="Spanish.*English"):
        validate_language(generated, "en")


@pytest.mark.parametrize("url", ["https://api.example.com", "http://192.168.1.20:11434", "http://localhost.evil.test", "http://user:pass@localhost:11434", "http://localhost:11434/other", "http://localhost:11434?target=example.com"])
def test_remote_inference_configuration_rejected(url):
    with pytest.raises(SkillEngineError, match="local"):
        local_url(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "http://[::1]:11434", "http://host.docker.internal:11434", "http://ollama:11434"])
def test_local_inference_endpoints(url):
    assert local_url(url) == url


def test_cloud_backed_models_are_not_selected(monkeypatch):
    original = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"model_info": {"general.architecture": "qwen2"}, "models": [
        {"name": "remote:cloud", "size": 100}, {"name": "gpt-oss:120b-cloud", "size": 100}, {"name": "remote-hidden", "size": 100, "remote_model": "other"},
        {"name": "embedding", "size": 100, "capabilities": ["embedding"]},
        {"name": "local-text", "size": 100, "capabilities": ["completion"]}]}))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    result = LocalSkillEngine(url="http://localhost:11434").status()
    assert result["available"]
    assert result["models"] == ["local-text"]
    assert result["model"] == "local-text"
    assert not LocalSkillEngine(url="http://localhost:11434", model="remote:cloud").status()["available"]


def test_cloud_model_alias_is_rejected_before_inference(monkeypatch):
    original = httpx.Client
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"models": [{"name": "innocent-name", "size": 123}], "remote_model": "remote-model", "remote_host": "https://ollama.com"} if request.url.path == "/api/show" else {"models": [{"name": "innocent-name", "size": 123}]})
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    result = LocalSkillEngine(url="http://localhost:11434").status()
    assert not result["available"]
    assert result["error_code"] == "skill_local_only"
    assert "/api/generate" not in paths


def test_evidence_selection_covers_end_of_long_recording():
    video = source("long", 4000)
    chosen, sampled = select_evidence(video, "dashboard layout", 12000)
    assert sampled
    indexes = [segment["index"] for segment in chosen]
    assert indexes == sorted(set(indexes))
    assert {min(index // 1000, 3) for index in indexes} == {0, 1, 2, 3}
    assert sum(len(segment["text"]) + 45 for segment in chosen) <= 12000


def test_complete_short_transcript_is_not_reported_as_sampled():
    video = source("short", 5)
    chosen, sampled = select_evidence(video, "dashboard", 4000)
    assert not sampled
    assert chosen == video["segments"]


def test_single_oversized_segment_cannot_overflow_model_context():
    video = source("huge", 1)
    video["segments"][0]["text"] = "dashboard " * 50000
    chosen, sampled = select_evidence(video, "dashboard", 4000)
    assert sampled
    assert len(chosen[0]["text"]) < 4000
    assert len(video["segments"][0]["text"]) == 500000


def test_hallucinated_or_missing_source_references_are_rejected():
    generated = SkillDraft.model_validate(draft())
    allowed = {"first": {1: source("first")["segments"][1]}, "second": {2: source("second")["segments"][2]}}
    validate_evidence(generated, allowed)
    generated.steps[0].evidence[0].video_id = "unknown-video"
    with pytest.raises(ValueError, match="Unknown evidence"):
        validate_evidence(generated, allowed)
    generated = SkillDraft.model_validate(draft())
    with pytest.raises(ValueError, match="Missing"):
        validate_evidence(generated, {**allowed, "third": {3: {}}})


def test_existing_segment_with_fabricated_quote_is_rejected():
    generated = SkillDraft.model_validate(draft())
    allowed = {"first": {1: {"text": "Welcome to this video. Subscribe to my channel."}}, "second": {2: source("second")["segments"][2]}}
    with pytest.raises(ValueError, match="not verbatim"):
        validate_evidence(generated, allowed)


def test_near_quote_stays_rejected_and_hints_use_only_actual_same_video_text():
    generated = SkillDraft.model_validate(draft())
    paraphrase = "So if we turn the department and employment into chips or took these numbers and right align them, things start to shape up."
    original = "But for example, if we turned the department and employment into chips or took these numbers and right align them, things start to shape up."
    generated.steps[0].evidence[0].quote = paraphrase
    generated.steps[0].evidence[0].segment_index = 14
    allowed = {"first": {7: {"text": "But for example, if we turned the department and"},
                         8: {"text": "employment into chips or took these numbers and right align them, things start to shape up."}},
               "second": {2: source("second")["segments"][2], 14: {"text": paraphrase + " OTHER_VIDEO_ONLY_TEXT"}}}
    with pytest.raises(ValueError, match="not verbatim") as error:
        validate_evidence(generated, allowed)
    message = str(error.value)
    assert original in message
    assert '"video_id": "first", "segment_index": 7' in message
    assert "OTHER_VIDEO_ONLY_TEXT" not in message
    assert generated.steps[0].evidence[0].quote == paraphrase
    assert generated.steps[0].evidence[0].segment_index == 14


def test_retry_diagnostics_collect_several_quote_failures_and_remain_bounded():
    payload = draft()
    payload["steps"] *= 5
    generated = SkillDraft.model_validate(payload)
    allowed = {"first": {1: source("first")["segments"][1]}, "second": {2: source("second")["segments"][2]}}
    for step in generated.steps:
        step.evidence[0].quote = step.evidence[0].quote.replace("Compare", "Comparing")
    with pytest.raises(ValueError) as error:
        validate_evidence(generated, allowed)
    message = str(error.value)
    assert "not verbatim at first/1" in message
    assert "not verbatim at second/2" in message
    assert message.count("Quote is not verbatim") == MAX_CITATION_DIAGNOSTICS
    assert len(message) <= MAX_RETRY_FEEDBACK


@pytest.mark.parametrize("quote", [
    "If you want to check out Mobbin, it'll be the very first link down below.",
    "Subscribe to my channel to get new design tutorials every week.",
    "Which is exactly what today's sponsor, Mobbin, makes dead simple.",
    "Você encontra o link na descrição para conhecer o produto.",
    "Puedes visitar el enlace en la descripción para comprar el producto.",
])
def test_verbatim_promotion_cannot_ground_a_procedure_step(quote):
    # Reproduces a real generated user-testing step citing a sponsor's link.
    generated = SkillDraft.model_validate(draft())
    generated.steps[0].source_rule = "Conduct usability tests with users to evaluate the dashboard."
    generated.steps[0].evidence[0].quote = quote
    allowed = {"first": {1: {"text": quote}}, "second": {2: source("second")["segments"][2]}}
    with pytest.raises(ValueError, match="Promotional citation"):
        validate_evidence(generated, allowed)


def test_example_confirmation_question_needs_its_actual_teaching_context():
    generated = SkillDraft.model_validate(draft())
    generated.steps[0].evidence[0].quote = "Are you sure?"
    allowed = {"first": {1: {"text": "Are you sure?"}}, "second": {2: source("second")["segments"][2]}}
    with pytest.raises(ValueError, match="no standalone teaching content"):
        validate_evidence(generated, allowed)
    generated.steps[0].evidence[0].quote = "For anything destructive or expensive or final, add a confirmation. Are you sure?"
    allowed["first"][0] = {"text": "For anything destructive or expensive or final, add a confirmation."}
    validate_evidence(generated, allowed)
    assert generated.steps[0].evidence[0].segment_index == 0


def test_teaching_quote_is_allowed_next_to_promotion_and_with_commercial_terms():
    generated = SkillDraft.model_validate(draft())
    quote = "For a subscription purchase, place the link below the price and show the recurring charge."
    generated.steps[0].evidence[0].quote = quote
    allowed = {"first": {1: {"text": "Subscribe to my channel. " + quote}}, "second": {2: source("second")["segments"][2]}}
    validate_evidence(generated, allowed)


def test_quote_spanning_consecutive_segments_resolves_to_its_start():
    generated = SkillDraft.model_validate(draft())
    allowed = {"first": {1: {"text": "Compare dashboard layout and verify"}, 2: {"text": "the values for scenario 1."}}, "second": {2: source("second")["segments"][2]}}
    validate_evidence(generated, allowed)
    allowed["first"][1]["text"] = "Welcome to this video"
    allowed["first"][2]["text"] = generated.steps[0].evidence[0].quote
    validate_evidence(generated, allowed)
    assert generated.steps[0].evidence[0].segment_index == 2


def test_valid_quote_corrects_wrong_index_but_never_changes_source():
    generated = SkillDraft.model_validate(draft())
    generated.steps[0].evidence[0].segment_index = 999
    allowed = {"first": {1: source("first")["segments"][1]}, "second": {2: source("second")["segments"][2]}}
    validate_evidence(generated, allowed)
    assert generated.steps[0].evidence[0].segment_index == 1
    allowed["first"][1]["text"] = "Subscribe to the channel for weekly videos."
    with pytest.raises(ValueError, match="not verbatim"):
        validate_evidence(generated, allowed)


def test_package_is_installable_and_keeps_complete_sources(monkeypatch):
    engine = fake_engine(monkeypatch)
    result = engine.generate([source("first"), source("second")], "", "Review dashboard", "en", lambda *args: None)
    files = {file["path"]: file["content"] for file in result["files"]}
    assert set(files) == {"SKILL.md", "references/sources.md", "references/validation.md", "references/video-first.md", "references/video-second.md"}
    assert yaml.safe_load(files["SKILL.md"].split("---", 2)[1])["name"] == result["name"]
    assert "#segment-1)" in files["SKILL.md"]
    assert "### segment-11" in files["references/video-first.md"]
    assert not result["validation"]["execution_tested"]
    assert result["validation"]["sampled_video_ids"] == []
    assert "Rule extracted from the sources" in files["SKILL.md"]
    assert draft()["steps"][0]["source_rule"] in files["SKILL.md"]
    assert "do not establish that the passages support every AI instruction" in files["references/validation.md"]


def test_source_notes_do_not_displace_evidence_or_enter_synthesis_context(monkeypatch):
    engine = fake_engine(monkeypatch)
    videos = [source("first", 140), source("second", 140)]
    for video in videos:
        video["method"] = "## Processo\n" + "IMPORTED_BOILERPLATE " * 500
    requests = []

    def generate(model, system, prompt, checkpoint):
        requests.append(json.JSONDecoder().raw_decode(prompt)[0])
        return json.dumps(draft())

    monkeypatch.setattr(engine, "_generate", generate)
    result = engine.generate(videos, "", "Review dashboard", "en", lambda *args: None)
    assert result["validation"]["sampled_video_ids"] == []
    assert [len(item["evidence"]) for item in requests[0]["sources"]] == [140, 140]
    assert "IMPORTED_BOILERPLATE" not in json.dumps(requests[0])
    # Grammar order is part of the evidence-first authoring contract: a model
    # chooses passages and their rule before writing an action or broad outline.
    assert "response_schema" not in requests[0]
    schema = SkillDraft.model_json_schema()
    assert next(iter(schema["properties"])) == "steps"
    assert list(schema["$defs"]["ProcedureStep"]["properties"])[:2] == ["evidence", "source_rule"]


def test_promotional_output_is_retried_with_targeted_feedback(monkeypatch):
    engine = fake_engine(monkeypatch)
    videos = [source("first"), source("second")]
    quote = "If you want to check out Mobbin, it'll be the very first link down below."
    videos[0]["segments"].append({"index": 99, "start_ms": 99000, "end_ms": 100000, "text": quote})
    promoted = draft()
    promoted["steps"][0]["evidence"] = [{"video_id": "first", "segment_index": 99, "quote": quote}]
    prompts = []

    def generate(model, system, prompt, checkpoint):
        prompts.append(prompt)
        return json.dumps(promoted if len(prompts) == 1 else draft())

    monkeypatch.setattr(engine, "_generate", generate)
    result = engine.generate(videos, "", "Review dashboard", "en", lambda *args: None)
    assert len(prompts) == 2
    assert "Promotional citation at first/99" in prompts[1]
    assert "#segment-99)" not in result["skill_markdown"]


def test_language_retry_keeps_final_reminder_after_source_and_validation_feedback(monkeypatch):
    engine = fake_engine(monkeypatch)
    generated = portuguese_draft()
    generated["steps"] = draft()["steps"]
    # Include an independent citation error so one retry can address both defects.
    generated["steps"][0]["evidence"][0]["quote"] = "A completely invented phrase that is absent from the source."
    prompts = []

    def generate(model, system, prompt, checkpoint):
        prompts.append(prompt)
        return json.dumps(generated if len(prompts) == 1 else portuguese_draft())

    monkeypatch.setattr(engine, "_generate", generate)
    result = engine.generate([source("first"), source("second")], "", "Revisar o dashboard", "pt", lambda *args: None)
    assert len(prompts) == 2
    assert all(prompt.endswith(LANGUAGE_REMINDERS["pt"]) for prompt in prompts)
    assert "Wrong prose language in procedure" in prompts[1]
    assert "not verbatim" in prompts[1]
    assert "Conferir os dados e o layout" in result["skill_markdown"]


def test_retry_receives_true_wording_and_only_accepts_literal_correction(monkeypatch):
    engine = fake_engine(monkeypatch)
    videos = [source("first"), source("second")]
    videos[0]["segments"][7]["text"] = "But for example, if we turned the department and"
    videos[0]["segments"][8]["text"] = "employment into chips or took these numbers and right align them, things start to shape up."
    original = videos[0]["segments"][7]["text"] + " " + videos[0]["segments"][8]["text"]
    paraphrased = draft()
    paraphrased["steps"][0]["evidence"] = [{"video_id": "first", "segment_index": 14,
                                            "quote": original.replace("But for example, if we turned", "So if we turn")}]
    corrected = draft()
    corrected["steps"][0]["evidence"] = [{"video_id": "first", "segment_index": 7, "quote": original}]
    prompts = []

    def generate(model, system, prompt, checkpoint):
        prompts.append(prompt)
        return json.dumps(paraphrased if len(prompts) == 1 else corrected)

    monkeypatch.setattr(engine, "_generate", generate)
    result = engine.generate(videos, "", "Review dashboard", "en", lambda *args: None)
    assert len(prompts) == 2
    feedback = prompts[1].split("Correct these validation errors from the previous attempt: ", 1)[1]
    assert original in feedback
    assert "Closest ACTUAL selected passages from the same video" in feedback
    assert "#segment-7)" in result["skill_markdown"]
    assert original in result["skill_markdown"]
    # A hint must never turn a near match into acceptance on the final attempt.
    monkeypatch.setattr(engine, "_generate", lambda *args: json.dumps(paraphrased))
    with pytest.raises(SkillEngineError) as error:
        engine.generate(videos, "", "Review dashboard", "en", lambda *args: None)
    assert error.value.code == "skill_invalid_output"


def test_wrong_language_does_not_add_more_than_two_inference_attempts(monkeypatch):
    engine = fake_engine(monkeypatch)
    calls = []

    def generate(*args):
        calls.append(args)
        return json.dumps(draft())

    monkeypatch.setattr(engine, "_generate", generate)
    with pytest.raises(SkillEngineError) as error:
        engine.generate([source("first"), source("second")], "", "Revisar o dashboard", "pt", lambda *args: None)
    assert error.value.code == "skill_invalid_output"
    assert len(calls) == 2


def test_invalid_model_output_has_bounded_retry(monkeypatch):
    engine = fake_engine(monkeypatch)
    calls = []
    def malformed(*args):
        calls.append(args)
        return "not json"
    monkeypatch.setattr(engine, "_generate", malformed)
    with pytest.raises(SkillEngineError) as error:
        engine.generate([source("first"), source("second")], "", "", "pt", lambda *args: None)
    assert error.value.code == "skill_invalid_output"
    assert len(calls) == 2


def test_checkpoint_cancellation_propagates(monkeypatch):
    engine = fake_engine(monkeypatch)
    class Cancelled(Exception):
        pass
    def cancel(*args):
        raise Cancelled()
    with pytest.raises(Cancelled):
        engine.generate([source("first")], "", "", "pt", cancel)
