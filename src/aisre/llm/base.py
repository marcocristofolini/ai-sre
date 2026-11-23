"""Tipos e contrato de provedores de LLM (BYO-LLM)."""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from aisre import strings_pt as t
from aisre.config import Settings


class ToolCall(BaseModel):
    id: str
    name: str
    args: dict


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    # Chamadas de ferramenta emitidas pelo assistente (necessárias para reenviar o histórico).
    tool_calls: list[ToolCall] = Field(default_factory=list)


class LLMResponse(BaseModel):
    text: str | None
    tool_calls: list[ToolCall] = Field(default_factory=list)


@runtime_checkable
class LLMProvider(Protocol):
    """`tools`: lista de {"name", "description", "schema" (JSON Schema)}."""

    name: str
    is_external: bool

    async def complete(self, messages: list[Message], tools: list[dict]) -> LLMResponse: ...


def build_provider(settings: Settings) -> LLMProvider:
    """Cria o provedor configurado já protegido por `guarded`. Não faz chamadas de rede."""
    from aisre.llm.gate import guarded

    name = settings.llm_provider.strip().lower()
    if name == "anthropic":
        from aisre.llm.anthropic_provider import AnthropicProvider

        key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
        if not key:
            raise ValueError(t.ERRO_LLM_SEM_CHAVE)
        provider: LLMProvider = AnthropicProvider(key, settings.llm_model)
    elif name == "local":
        from aisre.llm.local_provider import LocalProvider

        if not settings.llm_base_url:
            raise ValueError(t.ERRO_LLM_SEM_URL_LOCAL)
        key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
        provider = LocalProvider(settings.llm_base_url, settings.llm_model, api_key=key)
    else:
        raise ValueError(t.ERRO_LLM_PROVEDOR_DESCONHECIDO.format(nome=settings.llm_provider))
    return guarded(provider, settings)
