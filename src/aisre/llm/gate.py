"""Consentimento para provedores externos e redação de tudo que sai."""

from __future__ import annotations

from aisre import strings_pt as t
from aisre.config import Settings
from aisre.llm.base import LLMProvider, LLMResponse, Message, ToolCall
from aisre.llm.redaction import redact


class ConsentRequired(Exception):
    def __init__(self) -> None:
        super().__init__(t.ERRO_CONSENTIMENTO_LLM)


def _redact_value(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


def redact_message(m: Message) -> Message:
    return Message(
        role=m.role,
        content=redact(m.content),
        tool_call_id=m.tool_call_id,
        tool_calls=[ToolCall(id=c.id, name=c.name, args=_redact_value(c.args)) for c in m.tool_calls],
    )


class _Guarded:
    def __init__(self, inner: LLMProvider, settings: Settings) -> None:
        self._inner = inner
        self._consent = settings.llm_external_consent
        self.name = inner.name
        self.is_external = inner.is_external

    async def complete(self, messages: list[Message], tools: list[dict]) -> LLMResponse:
        if self._inner.is_external and not self._consent:
            raise ConsentRequired()
        return await self._inner.complete([redact_message(m) for m in messages], tools)


def guarded(provider: LLMProvider, settings: Settings) -> LLMProvider:
    return _Guarded(provider, settings)
