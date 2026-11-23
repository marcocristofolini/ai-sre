import asyncio

import pytest

from aisre.config import Settings
from aisre.llm.base import LLMResponse, Message, ToolCall, build_provider
from aisre.llm.gate import ConsentRequired, guarded


class FakeProvider:
    def __init__(self, external: bool):
        self.name = "fake"
        self.is_external = external
        self.sent: list = []

    async def complete(self, messages, tools):
        self.sent.append((messages, tools))
        return LLMResponse(text="ok", tool_calls=[])


SECRET = "Bearer abc123SECRETtoken"


def _settings(consent: bool, **kw):
    return Settings(_env_file=None, llm_external_consent=consent, **kw)


def test_guarded_external_provider_without_consent_raises():
    fake = FakeProvider(external=True)
    g = guarded(fake, _settings(False))
    with pytest.raises(ConsentRequired):
        asyncio.run(g.complete([Message(role="user", content="oi")], []))
    assert fake.sent == []


def test_guarded_external_provider_with_consent_allowed():
    g = guarded(FakeProvider(external=True), _settings(True))
    assert asyncio.run(g.complete([Message(role="user", content="oi")], [])).text == "ok"


def test_guarded_local_provider_allowed_without_consent():
    g = guarded(FakeProvider(external=False), _settings(False))
    assert asyncio.run(g.complete([Message(role="user", content="oi")], [])).text == "ok"
    assert g.is_external is False and g.name == "fake"


def test_guarded_redacts_before_sending():
    fake = FakeProvider(external=False)
    g = guarded(fake, _settings(False))
    msgs = [
        Message(role="user", content=f"use {SECRET}"),
        Message(role="tool", content="password=hunter2", tool_call_id="1"),
    ]
    asyncio.run(g.complete(msgs, [{"name": "x", "description": "d", "schema": {}}]))
    sent = fake.sent[0][0]
    assert "abc123SECRET" not in sent[0].content
    assert "hunter2" not in sent[1].content and sent[1].tool_call_id == "1"
    assert "abc123SECRET" in msgs[0].content  # original intacto


def test_guarded_redacts_assistant_tool_call_args():
    class P(FakeProvider):
        pass

    fake = P(external=False)
    g = guarded(fake, _settings(False))
    m = Message(role="assistant", content="", tool_calls=[ToolCall(id="1", name="loki", args={"q": "password=hunter2"})])
    asyncio.run(g.complete([m], []))
    assert "hunter2" not in str(fake.sent[0][0][0].tool_calls[0].args)


def test_build_provider_makes_no_network_calls_and_gates():
    p = build_provider(_settings(False, llm_provider="anthropic", llm_api_key="sk-x"))
    assert p.is_external is True
    with pytest.raises(ConsentRequired):
        asyncio.run(p.complete([Message(role="user", content="oi")], []))


def test_build_provider_local():
    p = build_provider(_settings(False, llm_provider="local", llm_base_url="http://localhost:11434"))
    assert p.is_external is False


def test_build_provider_unknown_raises():
    with pytest.raises(ValueError):
        build_provider(_settings(True, llm_provider="nope"))


def test_consent_message_has_no_secret():
    assert "sk-x" not in str(ConsentRequired())


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:8000",
        "http://10.1.2.3:8000",
        "http://192.168.0.5",
        "http://169.254.1.1",
        "http://ollama:11434",
        "http://llm.ns.svc:8000",
        "http://llm.ns.svc.cluster.local:8000",
        "http://[::1]:8000",
    ],
)
def test_local_provider_internal_url_needs_no_consent(url):
    p = build_provider(_settings(False, llm_provider="local", llm_base_url=url))
    assert p.is_external is False


@pytest.mark.parametrize("url", ["https://api.openai.com", "https://gateway.example.com/x", "http://8.8.8.8", "not a url"])
def test_local_provider_hosted_url_requires_consent(url):
    p = build_provider(_settings(False, llm_provider="local", llm_base_url=url))
    assert p.is_external is True
    with pytest.raises(ConsentRequired):
        asyncio.run(p.complete([Message(role="user", content="oi")], []))


def test_local_provider_hosted_url_with_consent_passes_gate():
    p = build_provider(_settings(True, llm_provider="local", llm_base_url="https://api.openai.com"))
    assert p.is_external is True  # passa pelo portão; a chamada real não é feita aqui
