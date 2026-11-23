import asyncio
import json

import httpx

from aisre.llm.anthropic_provider import to_anthropic_request
from aisre.llm.base import Message, ToolCall
from aisre.llm.local_provider import LocalProvider

TOOLS = [{"name": "loki", "description": "logs", "schema": {"type": "object", "properties": {}}}]


def test_anthropic_mapping():
    msgs = [
        Message(role="system", content="sys"),
        Message(role="user", content="oi"),
        Message(role="assistant", content="vou ver", tool_calls=[ToolCall(id="t1", name="loki", args={"q": "x"})]),
        Message(role="tool", content="resultado", tool_call_id="t1"),
    ]
    system, out, tools = to_anthropic_request(msgs, TOOLS)
    assert system == "sys"
    assert out[0] == {"role": "user", "content": "oi"}
    assert out[1]["content"][1] == {"type": "tool_use", "id": "t1", "name": "loki", "input": {"q": "x"}}
    assert out[2] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "resultado"}]}
    assert tools == [{"name": "loki", "description": "logs", "input_schema": {"type": "object", "properties": {}}}]


def test_anthropic_mapping_merges_consecutive_tool_results():
    msgs = [
        Message(role="user", content="oi"),
        Message(role="assistant", content="", tool_calls=[ToolCall(id="a", name="n", args={}), ToolCall(id="b", name="n", args={})]),
        Message(role="tool", content="1", tool_call_id="a"),
        Message(role="tool", content="2", tool_call_id="b"),
    ]
    _, out, _ = to_anthropic_request(msgs, [])
    assert len(out) == 3 and len(out[2]["content"]) == 2


def _local(handler, key=""):
    return LocalProvider("http://llm.local:11434", "m", transport=httpx.MockTransport(handler))


def test_local_provider_text_and_tool_calls():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "loki", "arguments": '{"q": "x"}'}}],
        }}]})

    p = _local(handler)
    msgs = [Message(role="user", content="oi"),
            Message(role="assistant", content="", tool_calls=[ToolCall(id="p", name="loki", args={"q": "y"})]),
            Message(role="tool", content="r", tool_call_id="p")]
    res = asyncio.run(p.complete(msgs, TOOLS))
    assert seen["url"] == "http://llm.local:11434/v1/chat/completions"
    assert seen["body"]["tools"][0]["function"]["name"] == "loki"
    assert seen["body"]["messages"][1]["tool_calls"][0]["function"]["arguments"] == '{"q": "y"}'
    assert seen["body"]["messages"][2]["tool_call_id"] == "p"
    assert res.text is None and res.tool_calls == [ToolCall(id="c1", name="loki", args={"q": "x"})]
    assert p.is_external is False


def test_local_provider_error_has_no_body():
    def handler(request):
        return httpx.Response(500, text="segredo-no-corpo")

    try:
        asyncio.run(_local(handler).complete([Message(role="user", content="x")], []))
    except Exception as exc:  # noqa: BLE001
        assert "segredo-no-corpo" not in str(exc)
    else:
        raise AssertionError("deveria falhar")


# --- I11: endpoint local não passa por proxy do ambiente ---------------------------------------


import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize("url,trust", [("http://ollama.llm.svc:11434", False), ("http://127.0.0.1:8000", False),
                                         ("https://gateway.example.com", True)])
def test_local_provider_ignores_env_proxy_only_for_local_urls(monkeypatch, url, trust):
    import asyncio as _asyncio

    import httpx as _httpx

    from aisre.llm.local_provider import LocalProvider

    seen = {}
    real = _httpx.AsyncClient

    def capture(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.externo.example:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.externo.example:3128")
    monkeypatch.setattr(_httpx, "AsyncClient", capture)
    transport = _httpx.MockTransport(lambda r: _httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
    provider = LocalProvider(url, "m", transport=transport)
    _asyncio.run(provider.complete([], []))
    assert seen["trust_env"] is trust
