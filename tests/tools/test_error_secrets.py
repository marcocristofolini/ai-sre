"""C1: erros de ferramenta nunca carregam credenciais da URL para RCA, dashboard ou Slack."""

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from aisre.agent.loop import Alert, investigate
from aisre.agent.prompts import FINAL_TOOL_NAME
from aisre.db import MemoryStore
from aisre.llm.base import LLMResponse, ToolCall
from aisre.slack import build_message
from aisre.tools.base import ToolResult, error_result
from aisre.tools.loki import LokiTool
from aisre.tools.prometheus import PrometheusTool

PW = "s3cretpw"
ALERT = Alert(name="X", labels={}, fired_at=datetime(2026, 11, 23, tzinfo=UTC))


class _LLM:
    name = "fake"
    is_external = False

    def __init__(self, script):
        self.script, self.n = script, 0

    async def complete(self, messages, tools):
        self.n += 1
        return self.script[min(self.n, len(self.script)) - 1]


def _call(name, args, id="c1"):
    return LLMResponse(text=None, tool_calls=[ToolCall(id=id, name=name, args=args)])


@pytest.mark.parametrize("status", [401, 403, 500])
def test_http_status_error_reports_only_status_code(status):
    tool = PrometheusTool(
        f"http://user:{PW}@prom:9090", transport=httpx.MockTransport(lambda r: httpx.Response(status))
    )
    res = asyncio.run(tool.call({"query": "up"}))
    assert res.ok is False
    assert PW not in res.error and "user:" not in res.error and "prom:9090" not in res.error
    assert str(status) in res.error


def test_transport_error_message_has_userinfo_stripped():
    def handler(request):
        raise httpx.ConnectError(f"falhou ao conectar em {request.url}", request=request)

    tool = LokiTool(f"http://admin:{PW}@loki:3100", transport=httpx.MockTransport(handler))
    res = asyncio.run(tool.call({"query": '{app="x"}'}))
    assert res.ok is False and PW not in res.error and "admin" not in res.error
    assert "loki:3100" in res.error  # o host continua útil para diagnóstico


@pytest.mark.parametrize(
    "msg",
    [
        f"erro em https://tok{PW}@host/x",
        f"erro em https://u:{PW}@host/x",
        f"Authorization: Bearer {PW}",
        f"password={PW}",
    ],
)
def test_generic_exception_detail_is_redacted(msg):
    res = error_result("metric:up", RuntimeError(msg))
    assert PW not in res.error


def test_stored_rca_and_slack_message_never_contain_url_password():
    tool = PrometheusTool(
        f"http://user:{PW}@prom:9090", transport=httpx.MockTransport(lambda r: httpx.Response(401))
    )
    llm = _LLM([_call("prometheus", {"query": "up"}), _call(FINAL_TOOL_NAME, {"cause": None, "confidence": 0})])
    rca = asyncio.run(investigate(ALERT, [tool], llm))
    assert rca.unknown and rca.evidence
    assert PW not in rca.model_dump_json()

    async def store_roundtrip():
        store = MemoryStore()
        inc, _ = await store.create_if_new(ALERT, 300)
        await store.complete(inc.id, rca)
        return (await store.get(inc.id)).model_dump_json()

    assert PW not in asyncio.run(store_roundtrip())
    assert PW not in json.dumps(build_message(1, rca), ensure_ascii=False)


class _LeakyTool:
    """Ferramenta fora do contrato: devolve erro bruto com segredo."""

    name = "leaky"
    schema = {"type": "object", "properties": {}}

    async def call(self, args):
        return ToolResult(ok=False, data="", error=f"falha em http://u:{PW}@db token={PW}", ref="metric:x")


def test_collected_evidence_redacts_tool_error_as_defence_in_depth():
    llm = _LLM([_call("leaky", {}), _call(FINAL_TOOL_NAME, {"cause": None, "confidence": 0})])
    rca = asyncio.run(investigate(ALERT, [_LeakyTool()], llm))
    assert rca.evidence and PW not in rca.model_dump_json()
