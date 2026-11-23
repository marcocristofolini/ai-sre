"""Provedor externo sobre o SDK oficial da Anthropic."""

from __future__ import annotations

from aisre import strings_pt as t
from aisre.llm.base import LLMResponse, Message, ToolCall


def to_anthropic_request(messages: list[Message], tools: list[dict]) -> tuple[str, list[dict], list[dict]]:
    """Função pura: (system, messages, tools) no formato do SDK."""
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    out: list[dict] = []
    for m in messages:
        if m.role == "system":
            continue
        if m.role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.tool_call_id or "", "content": m.content}
            last = out[-1] if out else None
            if last and last["role"] == "user" and isinstance(last["content"], list) and all(
                b.get("type") == "tool_result" for b in last["content"]
            ):
                last["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m.role == "assistant" and m.tool_calls:
            blocks: list[dict] = [{"type": "text", "text": m.content}] if m.content else []
            blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.args} for c in m.tool_calls]
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": m.role, "content": m.content})
    mapped_tools = [
        {"name": x["name"], "description": x.get("description", ""), "input_schema": x.get("schema", {"type": "object"})}
        for x in tools
    ]
    return system, out, mapped_tools


class AnthropicProvider:
    name = "anthropic"
    is_external = True

    def __init__(self, api_key: str, model: str, max_tokens: int = 4096) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._client = None

    async def complete(self, messages: list[Message], tools: list[dict]) -> LLMResponse:
        import anthropic

        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        system, msgs, mapped = to_anthropic_request(messages, tools)
        kwargs: dict = {"model": self._model, "max_tokens": self._max_tokens, "messages": msgs}
        if system:
            kwargs["system"] = system
        if mapped:
            kwargs["tools"] = mapped
        try:
            resp = await self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            # Nunca propagar a mensagem original: pode conter trechos do pedido.
            raise RuntimeError(t.ERRO_LLM_FALHA.format(detalhe=type(exc).__name__)) from None
        texts = [b.text for b in resp.content if b.type == "text"]
        calls = [ToolCall(id=b.id, name=b.name, args=dict(b.input)) for b in resp.content if b.type == "tool_use"]
        return LLMResponse(text="".join(texts) or None, tool_calls=calls)
