import asyncio

import httpx

from aisre.tools.loki import LokiTool


def _tool(handler):
    return LokiTool("http://loki:3100", timeout=10, transport=httpx.MockTransport(handler))


def test_loki_success():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/loki/api/v1/query_range"
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    res = asyncio.run(_tool(handler).call({"query": '{app="api"}'}))
    assert res.ok
    assert res.ref.startswith("log:")


def test_loki_unreachable_returns_error_result():
    def handler(request):
        raise httpx.ConnectError("sem rota", request=request)

    res = asyncio.run(_tool(handler).call({"query": '{app="api"}'}))
    assert res.ok is False
    assert res.error
    assert res.ref.startswith("log:")
