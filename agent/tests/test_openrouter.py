import io
import json
import urllib.error

import pytest

from nova_agent import openrouter as orouter
from nova_agent.openrouter import OpenRouterClient, OpenRouterError


class FakeResponse:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")
        self.status = 200

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def make_client(monkeypatch, responses):
    calls = []
    slept = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        item = responses[len(calls) - 1]
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)

    monkeypatch.setattr(orouter.urllib.request, "urlopen", fake_urlopen)
    client = OpenRouterClient(
        api_key="sk-or-test", model="test/model", sleep=slept.append
    )
    return client, calls, slept


def _http_error(code, body=b"oops"):
    return urllib.error.HTTPError(
        "https://openrouter.ai/api/v1/chat/completions",
        code,
        "err",
        {},
        io.BytesIO(body),
    )


def test_missing_key_rejected():
    with pytest.raises(OpenRouterError):
        OpenRouterClient(api_key="", model="m")


def test_chat_request_shape(monkeypatch):
    payload = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
    client, calls, _ = make_client(monkeypatch, [payload])
    message = client.chat(
        [{"role": "user", "content": "hello"}],
        tools=[{"type": "function", "function": {"name": "x"}}],
        temperature=0.1,
    )
    assert message["content"] == "hi"
    request = calls[0]
    assert request.full_url == "https://openrouter.ai/api/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer sk-or-test"
    assert request.get_header("X-title") == "NOVA AI Agent"
    body = json.loads(request.data.decode("utf-8"))
    assert body["model"] == "test/model"
    assert body["temperature"] == 0.1
    assert body["tool_choice"] == "auto"
    assert body["tools"][0]["function"]["name"] == "x"
    assert body["messages"][0]["content"] == "hello"


def test_content_none_normalized(monkeypatch):
    payload = {"choices": [{"message": {"role": "assistant", "content": None}}]}
    client, _, _ = make_client(monkeypatch, [payload])
    message = client.chat([])
    assert message["content"] == ""


def test_retries_on_transient_then_succeeds(monkeypatch):
    payload = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
    client, calls, slept = make_client(
        monkeypatch, [_http_error(500), _http_error(429), payload]
    )
    message = client.chat([{"role": "user", "content": "x"}])
    assert message["content"] == "ok"
    assert len(calls) == 3
    assert len(slept) == 2


def test_no_retry_on_auth_error(monkeypatch):
    client, calls, _ = make_client(
        monkeypatch, [_http_error(401, b'{"error": "invalid key"}')]
    )
    with pytest.raises(OpenRouterError) as excinfo:
        client.chat([])
    assert "401" in str(excinfo.value)
    assert len(calls) == 1


def test_retries_exhausted(monkeypatch):
    client, calls, _ = make_client(monkeypatch, [_http_error(503)] * 4)
    with pytest.raises(OpenRouterError):
        client.chat([])
    assert len(calls) == 4  # 1 try + 3 retries


def test_unexpected_payload(monkeypatch):
    client, _, _ = make_client(monkeypatch, [{"nope": 1}])
    with pytest.raises(OpenRouterError) as excinfo:
        client.chat([])
    assert "Unexpected" in str(excinfo.value)


def test_connection_error_retried(monkeypatch):
    payload = {"choices": [{"message": {"role": "assistant", "content": "back"}}]}
    client, calls, slept = make_client(
        monkeypatch, [urllib.error.URLError("dns down"), payload]
    )
    message = client.chat([])
    assert message["content"] == "back"
    assert len(calls) == 2
    assert slept
