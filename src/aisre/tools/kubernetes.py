import asyncio
import json
from typing import Any

from aisre import strings_pt as t
from aisre.tools.base import ToolResult, error_result, truncate

# Verbos de leitura permitidos (get/list/watch); read_* é o "get" do cliente oficial.
_ALLOWED_PREFIXES = ("get_", "list_", "read_", "watch_")


class ReadOnlyK8s:
    """Proxy do cliente Kubernetes que só expõe métodos de leitura."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        if not name.startswith(_ALLOWED_PREFIXES):
            raise PermissionError(t.ERRO_FERRAMENTA_SOMENTE_LEITURA)
        return getattr(self._client, name)


def _default_client() -> Any:
    from kubernetes import client, config

    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config()
    return client.CoreV1Api()


def _to_text(obj: Any) -> str:
    if hasattr(obj, "to_dict"):
        obj = obj.to_dict()
    return json.dumps(obj, default=str)


class KubernetesTool:
    name = "kubernetes"
    schema = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["pod", "pods", "nodes", "events"]},
            "namespace": {"type": "string"},
            "name": {"type": "string"},
        },
        "required": ["kind"],
    }

    def __init__(self, client: Any = None, timeout: float = 10) -> None:
        self._raw = client
        self._timeout = timeout

    def _api(self) -> ReadOnlyK8s:
        if self._raw is None:
            self._raw = _default_client()
        return ReadOnlyK8s(self._raw)

    def _query(self, api: ReadOnlyK8s, kind: str, ns: str, name: str) -> Any:
        to = {"_request_timeout": self._timeout}
        if kind == "pod":
            return api.read_namespaced_pod(name, ns, **to)
        if kind == "pods":
            return api.list_namespaced_pod(ns, **to)
        if kind == "nodes":
            return api.list_node(**to)
        if kind == "events":
            return api.list_namespaced_event(ns, **to)
        raise ValueError(t.ERRO_FERRAMENTA_TIPO_K8S.format(tipo=kind))

    async def call(self, args: dict) -> ToolResult:
        kind = str(args.get("kind") or "")
        ns = str(args.get("namespace") or "default")
        name = str(args.get("name") or "")
        ref = f"k8s:{kind or 'desconhecido'}" + (f"/{name}" if name else "")
        try:
            if kind == "pod" and not name:
                raise ValueError(t.ERRO_FERRAMENTA_ARGUMENTO.format(nome="name"))
            api = self._api()
            result = await asyncio.wait_for(
                asyncio.to_thread(self._query, api, kind, ns, name), timeout=self._timeout
            )
            return ToolResult(ok=True, data=truncate(_to_text(result)), error=None, ref=ref)
        except Exception as exc:
            return error_result(ref, exc)
