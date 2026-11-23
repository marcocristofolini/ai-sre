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


def test_kubernetes_evidence_survives_truncation_for_large_pod():
    big = "x" * 5000
    pod = {
        "metadata": {"name": "api", "namespace": "default",
                     "managedFields": [{"manager": big, "fieldsV1": {big: {}}} for _ in range(10)]},
        "spec": {"containers": [{"name": "c", "env": [{"name": f"V{i}", "value": big} for i in range(5)]}]},
        "status": {"phase": "Running",
                   "containerStatuses": [{"name": "c", "restartCount": 3,
                                          "lastState": {"terminated": {"reason": "OOMKilled"}}}]},
    }

    class Big(FakeCore):
        def read_namespaced_pod(self, name, namespace, **kw):
            return pod

        def list_node(self, **kw):
            return {"items": [{"metadata": {"name": f"n{i}", "managedFields": [big] * 5},
                               "spec": {"taints": [big]},
                               "status": {"conditions": [{"type": "DiskPressure", "status": "True",
                                                          "message": "pouco disco"}]}}
                              for i in range(3)]}

    tool = KubernetesTool(client=Big(), timeout=5)
    res = asyncio.run(tool.call({"kind": "pod", "namespace": "default", "name": "api"}))
    assert res.ok and "OOMKilled" in res.data and "managedFields" not in res.data
    res = asyncio.run(tool.call({"kind": "nodes"}))
    assert res.ok and "DiskPressure" in res.data and "managedFields" not in res.data


def test_kubernetes_projects_to_dict_objects_snake_case():
    class Obj:
        def to_dict(self):
            return {"metadata": {"name": "p"}, "status": {"container_statuses": [
                {"name": "c", "restart_count": 1,
                 "last_state": {"terminated": {"reason": "OOMKilled"}}}]}}

    class C(FakeCore):
        def read_namespaced_pod(self, name, namespace, **kw):
            return Obj()

    res = asyncio.run(KubernetesTool(client=C(), timeout=5).call({"kind": "pod", "name": "p"}))
    assert "OOMKilled" in res.data and "lastState" in res.data


def test_github_ref_matches_returned_data():
    commits = [{"sha": "aaaaaaa1111", "commit": {"message": "primeiro"}},
               {"sha": "bbbbbbb2222", "commit": {"message": "segundo"}}]

    def handler(request):
        if request.url.path.endswith("/commits/bbbbbbb2222"):
            return httpx.Response(200, json=commits[1])
        n = int(request.url.params.get("per_page", 30))
        return httpx.Response(200, json=commits[:n])

    tool = GitHubTool("o/r", "tok", timeout=5, transport=httpx.MockTransport(handler))
    res = asyncio.run(tool.call({}))
    assert res.ref == "commit:o/r@aaaaaaa" and "aaaaaaa1111" in res.data and "bbbbbbb" not in res.data
    res = asyncio.run(tool.call({"sha": "bbbbbbb2222"}))
    assert res.ref == "commit:o/r@bbbbbbb" and "bbbbbbb2222" in res.data and "aaaaaaa" not in res.data
