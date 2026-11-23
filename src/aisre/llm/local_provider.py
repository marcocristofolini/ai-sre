"""Provedor local: endpoint compatível com OpenAI (Ollama, vLLM...) via httpx."""

from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlparse

import httpx

from aisre import strings_pt as t
from aisre.llm.base import LLMResponse, Message, ToolCall


def to_openai_messages(messages: list[Message]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        d: dict = {"role": m.role, "content": m.content}
        if m.role == "tool":
            d["tool_call_id"] = m.tool_call_id
        if m.role == "assistant" and m.tool_calls:
            d["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                for c in m.tool_calls
            ]
        out.append(d)
    return out


def is_local_url(url: str) -> bool:
    """True só se o destino está dentro do ambiente: loopback, rede privada/link-local,
    nome sem ponto (compose/k8s) ou sufixos *.svc, *.cluster.local, *.local, *.localhost."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host or host.endswith((".svc", ".cluster.local", ".local", ".localhost"))
    return ip.is_loopback or ip.is_private or ip.is_link_local


class LocalProvider:
    name = "local"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # O destino real decide: um gateway hospedado exige consentimento.
        self.is_external = not is_local_url(base_url)
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = timeout
        self._transport = transport

    async def complete(self, messages: list[Message], tools: list[dict]) -> LLMResponse:
        body: dict = {"model": self._model, "messages": to_openai_messages(messages)}
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": x["name"],
                        "description": x.get("description", ""),
                        "parameters": x.get("schema", {"type": "object"}),
                    },
                }
                for x in tools
            ]
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as http:
                resp = await http.post(self._url, json=body, headers=self._headers)
                resp.raise_for_status()
                msg = resp.json()["choices"][0]["message"]
            calls = [
                ToolCall(id=c["id"], name=c["function"]["name"], args=json.loads(c["function"]["arguments"] or "{}"))
                for c in msg.get("tool_calls") or []
            ]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise RuntimeError(t.ERRO_LLM_FALHA.format(detalhe=type(exc).__name__)) from None
        return LLMResponse(text=msg.get("content") or None, tool_calls=calls)
