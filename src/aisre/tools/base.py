"""Contrato das ferramentas e camada HTTP somente leitura."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from aisre import strings_pt as t

# Limite de caracteres da saída de uma ferramenta, para não inundar o contexto do LLM.
MAX_OUTPUT_CHARS = 8000


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: str
    error: str | None
    ref: str  # "<kind>:<identificador>", kind em metric|log|commit|k8s


@runtime_checkable
class Tool(Protocol):
    name: str
    schema: dict

    async def call(self, args: dict) -> ToolResult: ...


def truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + t.AVISO_SAIDA_TRUNCADA.format(limite=MAX_OUTPUT_CHARS)


def error_result(ref: str, exc: BaseException | str) -> ToolResult:
    if isinstance(exc, httpx.TimeoutException | TimeoutError):
        msg = t.ERRO_FERRAMENTA_TIMEOUT
    else:
        msg = t.ERRO_FERRAMENTA_FALHA.format(detalhe=str(exc) or type(exc).__name__)
    return ToolResult(ok=False, data="", error=msg, ref=ref)


class ReadOnlyHttpClient:
    """Único ponto de saída HTTP das ferramentas: recusa qualquer método que não seja GET."""

    def __init__(
        self,
        timeout: float,
        headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout
        self._headers = headers or {}
        self._transport = transport

    async def request(self, method: str, url: str, params: dict | None = None) -> httpx.Response:
        if method.upper() != "GET":
            raise PermissionError(t.ERRO_FERRAMENTA_SOMENTE_LEITURA)
        async with httpx.AsyncClient(
            timeout=self._timeout, headers=self._headers, transport=self._transport
        ) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp

    async def get(self, url: str, params: dict | None = None) -> httpx.Response:
        return await self.request("GET", url, params)
