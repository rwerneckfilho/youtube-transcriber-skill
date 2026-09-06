"""Exercise local inference cancellation without a server or model process."""
import json
import threading
import time

import pytest

from backend.skill_engine import LocalSkillEngine


class Cancelled(Exception):
    pass


class FakeResponse:
    def __init__(self, lines=(), wait_for_close=None):
        self.lines = lines
        self.wait_for_close = wait_for_close

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    def iter_lines(self):
        if self.wait_for_close is not None:
            assert self.wait_for_close.wait(3), "Streaming client was not closed"
        yield from self.lines


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.request_started = threading.Event()
        self.closed = threading.Event()
        self.exited = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        self.exited.set()

    def stream(self, *args, **kwargs):
        self.request_started.set()
        return self.response

    def close(self):
        self.closed.set()


def test_cancellation_during_client_creation_does_not_start_inference(monkeypatch):
    creating = threading.Event()
    release = threading.Event()
    client = FakeClient(FakeResponse())

    def create_client(**kwargs):
        creating.set()
        assert release.wait(3), "Client construction was not released"
        return client

    def checkpoint(*args):
        assert creating.wait(2), "Client construction did not start"
        raise Cancelled()

    monkeypatch.setattr("backend.skill_engine.httpx.Client", create_client)
    try:
        with pytest.raises(Cancelled):
            LocalSkillEngine()._generate("fixture-local", "system", "source text", checkpoint)
    finally:
        release.set()
    assert client.exited.wait(2), "A client created after cancellation was not closed"
    assert client.closed.is_set()
    assert not client.request_started.is_set(), "Inference began after cancellation"


def test_cancellation_closes_stream_without_waiting_for_a_model_token(monkeypatch):
    client = FakeClient(FakeResponse())
    client.response.wait_for_close = client.closed
    monkeypatch.setattr("backend.skill_engine.httpx.Client", lambda **kwargs: client)

    def checkpoint(*args):
        assert client.request_started.wait(2), "Inference request did not start"
        raise Cancelled()

    started = time.monotonic()
    with pytest.raises(Cancelled):
        LocalSkillEngine()._generate("fixture-local", "system", "source text", checkpoint)
    assert time.monotonic() - started < 1, "Cancellation waited for the stream read timeout"
    assert client.closed.wait(1), "Cancellation did not close the active client"
    assert client.exited.wait(1), "The inference worker did not finish after its client closed"


def test_successful_stream_uses_global_read_budget_and_returns_complete_text(monkeypatch):
    client = FakeClient(FakeResponse([
        json.dumps({"response": "first ", "done": False}),
        json.dumps({"response": "second", "done": True}),
    ]))
    configuration = {}

    def create_client(**kwargs):
        configuration.update(kwargs)
        return client

    monkeypatch.setattr("backend.skill_engine.httpx.Client", create_client)
    monkeypatch.setenv("SKILL_GENERATION_TIMEOUT", "720")
    result = LocalSkillEngine()._generate("fixture-local", "system", "source text", lambda *args: None)
    assert result == "first second"
    assert configuration["timeout"].read == 720
    assert configuration["timeout"].connect == 4
    assert configuration["trust_env"] is False
    assert configuration["follow_redirects"] is False
    assert client.closed.wait(1)
