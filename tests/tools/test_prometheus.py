import asyncio

import httpx

from aisre.tools.base import MAX_OUTPUT_CHARS
from aisre.tools.prometheus import PrometheusTool


def _tool(handler, timeout=10):
    return PrometheusTool(
        "http://prom:9090", timeout=timeout, transport=httpx.MockTransport(handler)
    )


def test_prometheus_success_has_ref_and_data():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/api/v1/query"
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    res = asyncio.run(_tool(handler).call({"query": "http_request_duration_p99"}))
    assert res.ok and res.error is None
    assert res.ref == "metric:http_request_duration_p99"
    assert "success" in res.data


def test_prometheus_timeout_returns_error_result_not_exception():
    def handler(request):
        raise httpx.ReadTimeout("lento", request=request)

    res = asyncio.run(_tool(handler).call({"query": "up"}))
    assert res.ok is False
    assert res.error
    assert res.data == ""
    assert res.ref == "metric:up"


def test_prometheus_http_error_status_is_error_result():
    res = asyncio.run(_tool(lambda r: httpx.Response(500, text="boom")).call({"query": "up"}))
    assert res.ok is False and res.error


def test_prometheus_missing_query_is_error_result():
    res = asyncio.run(_tool(lambda r: httpx.Response(200, json={})).call({}))
    assert res.ok is False and res.error


def test_prometheus_output_truncated():
    big = "x" * (MAX_OUTPUT_CHARS * 3)
    res = asyncio.run(_tool(lambda r: httpx.Response(200, text=big)).call({"query": "up"}))
    assert res.ok
    assert len(res.data) <= MAX_OUTPUT_CHARS + 200
