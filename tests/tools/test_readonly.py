import asyncio

import httpx
import pytest

from aisre.config import Settings
from aisre.tools import build_tools
from aisre.tools.base import ReadOnlyHttpClient
from aisre.tools.github import GitHubTool
from aisre.tools.kubernetes import KubernetesTool, ReadOnlyK8s


def test_readonly_http_client_refuses_non_get():
    client = ReadOnlyHttpClient(timeout=1, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(PermissionError):
            asyncio.run(client.request(method, "http://x/y"))


def test_github_tool_uses_only_get_requests():
    seen = []

    def handler(request):
        seen.append(request.method)
        return httpx.Response(200, json=[{"sha": "abcdef1234567", "commit": {"message": "m"}}])

    tool = GitHubTool("o/r", "tok", timeout=5, transport=httpx.MockTransport(handler))
    res = asyncio.run(tool.call({}))
    assert res.ok
    assert res.ref.startswith("commit:")
    assert seen and set(seen) == {"GET"}
    assert not hasattr(tool, "post")


def test_github_failure_is_error_result():
    def handler(request):
        raise httpx.ConnectError("x", request=request)

    tool = GitHubTool("o/r", "tok", timeout=5, transport=httpx.MockTransport(handler))
    res = asyncio.run(tool.call({}))
    assert res.ok is False and res.error and res.ref.startswith("commit:")


class FakeCore:
    def __init__(self):
        self.calls = []

    def read_namespaced_pod(self, name, namespace, **kw):
        self.calls.append("read_namespaced_pod")
        return {"status": {"containerStatuses": [{"lastState": {"terminated": {"reason": "OOMKilled"}}}]}}

    def list_namespaced_pod(self, namespace, **kw):
        self.calls.append("list_namespaced_pod")
        return {"items": []}

    def list_node(self, **kw):
        self.calls.append("list_node")
        return {"items": []}

    def delete_namespaced_pod(self, name, namespace):  # nunca deve ser chamado
        self.calls.append("delete_namespaced_pod")

    def create_namespaced_pod(self, namespace, body):
        self.calls.append("create_namespaced_pod")

    def patch_node(self, name, body):
        self.calls.append("patch_node")


def test_kubernetes_tool_uses_only_get_list_watch_verbs():
    fake = FakeCore()
    tool = KubernetesTool(client=fake, timeout=5)
    asyncio.run(tool.call({"kind": "pod", "namespace": "default", "name": "api"}))
    asyncio.run(tool.call({"kind": "pods", "namespace": "default"}))
    asyncio.run(tool.call({"kind": "nodes"}))
    assert set(fake.calls) <= {"read_namespaced_pod", "list_namespaced_pod", "list_node"}
    assert fake.calls


def test_readonly_k8s_proxy_refuses_write_verbs():
    fake = FakeCore()
    ro = ReadOnlyK8s(fake)
    for verb in ("delete_namespaced_pod", "create_namespaced_pod", "patch_node"):
        with pytest.raises(PermissionError):
            getattr(ro, verb)
    assert fake.calls == []


def test_kubernetes_failure_is_error_result():
    class Broken(FakeCore):
        def list_node(self, **kw):
            raise RuntimeError("cluster fora")

    res = asyncio.run(KubernetesTool(client=Broken(), timeout=5).call({"kind": "nodes"}))
    assert res.ok is False and res.error and res.ref.startswith("k8s:")


def test_kubernetes_timeout_is_error_result():
    import time

    class Slow(FakeCore):
        def list_node(self, **kw):
            time.sleep(1)

    res = asyncio.run(KubernetesTool(client=Slow(), timeout=0.05).call({"kind": "nodes"}))
    assert res.ok is False and res.error


def test_build_tools_skips_unconfigured():
    assert build_tools(Settings(_env_file=None)) == []
    s = Settings(_env_file=None, prometheus_url="http://p", loki_url="http://l",
                 github_token="t", github_repo="o/r")
    assert {t.name for t in build_tools(s)} == {"prometheus", "loki", "github"}
    s2 = Settings(_env_file=None, kubernetes_enabled=True)
    assert [t.name for t in build_tools(s2)] == ["kubernetes"]
