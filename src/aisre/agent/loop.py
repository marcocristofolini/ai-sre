"""Laço de investigação: o LLM consulta ferramentas somente leitura e entrega um RCA validado."""

from __future__ import annotations

from datetime import datetime
from typing import get_args

from pydantic import BaseModel, Field, ValidationError

from aisre import strings_pt as t
from aisre.agent import prompts as p
from aisre.llm.base import LLMProvider, Message, ToolCall
from aisre.llm.gate import ConsentRequired
from aisre.schemas import RCA, Evidence
from aisre.tools.base import Tool, ToolResult

EVIDENCE_KINDS = set(get_args(Evidence.model_fields["kind"].annotation))
SINGLE_EVIDENCE_MAX_CONFIDENCE = 0.6


class Alert(BaseModel):
    name: str
    labels: dict[str, str] = Field(default_factory=dict)
    fired_at: datetime


class _Cited(BaseModel):
    ref: str
    summary: str = ""


class _FinalAnswer(BaseModel):
    cause: str | None = None
    confidence: float
    evidence: list[_Cited] = Field(default_factory=list)
    suggested_command: str | None = None


def _kind(ref: str) -> str | None:
    kind = ref.split(":", 1)[0]
    return kind if ":" in ref and kind in EVIDENCE_KINDS else None


class _Investigation:
    def __init__(self, tools: list[Tool]) -> None:
        self.tools = {tool.name: tool for tool in tools}
        self.results: dict[str, ToolResult] = {}  # último resultado por ref, em ordem
        self.citable: set[str] = set()  # refs devolvidos com ok=True nesta investigação

    def specs(self) -> list[dict]:
        specs = [
            {
                "name": name,
                "description": getattr(tool, "description", None)
                or p.TOOL_DESCRIPTIONS.get(name, p.TOOL_DESCRIPTION_GENERIC),
                "schema": tool.schema,
            }
            for name, tool in self.tools.items()
        ]
        specs.append(
            {"name": p.FINAL_TOOL_NAME, "description": p.FINAL_TOOL_DESCRIPTION, "schema": p.FINAL_TOOL_SCHEMA}
        )
        return specs

    async def run_tool(self, call: ToolCall) -> str:
        tool = self.tools.get(call.name)
        if tool is None:
            return p.TOOL_ERR_UNKNOWN.format(nome=call.name, disponiveis=", ".join(self.tools) or "-")
        args = call.args if isinstance(call.args, dict) else {}
        schema = tool.schema if isinstance(tool.schema, dict) else {}
        for arg in schema.get("required", []):
            if arg not in args:
                return p.TOOL_ERR_MISSING_ARG.format(nome=call.name, arg=arg)
        try:
            result = await tool.call(args)
        except Exception as exc:  # ferramenta fora do contrato: vira erro para o LLM
            return p.wrap_data(
                p.TOOL_ERR_EXCEPTION.format(detalhe=str(exc) or type(exc).__name__), ok="false"
            )
        if not isinstance(result, ToolResult):
            return p.wrap_data(p.TOOL_ERR_EXCEPTION.format(detalhe=type(result).__name__), ok="false")
        self.results[result.ref] = result
        if result.ok:
            self.citable.add(result.ref)
        return p.render_tool_result(result.ref, result.ok, result.data, result.error)

    def collected(self) -> list[Evidence]:
        """Tudo que foi consultado, com o erro registrado quando a consulta falhou."""
        out = []
        for ref, res in self.results.items():
            kind = _kind(ref)
            if kind is None:
                continue
            if ref in self.citable:
                summary = t.EVIDENCIA_COLETADA_SEM_CONCLUSAO
            else:
                summary = t.EVIDENCIA_CONSULTA_FALHOU.format(erro=res.error or t.ERRO_FERRAMENTA_SEM_DETALHE)
            out.append(Evidence(kind=kind, ref=ref, summary=summary))
        return out

    def unknown(self, cited: list[Evidence] | None = None) -> RCA:
        evidence = list(cited or [])
        seen = {e.ref for e in evidence}
        evidence += [e for e in self.collected() if e.ref not in seen]
        return RCA(cause=None, confidence=0.0, unknown=True, evidence=evidence)

    def conclude(self, args: dict) -> RCA:
        try:
            answer = _FinalAnswer.model_validate(args)
        except ValidationError:
            return self.unknown()
        cited: list[Evidence] = []
        for item in answer.evidence:
            kind = _kind(item.ref)
            if item.ref in self.citable and kind and item.ref not in {e.ref for e in cited}:
                cited.append(Evidence(kind=kind, ref=item.ref, summary=item.summary.strip()))
        cause = (answer.cause or "").strip() or None
        if cause is None or not cited:
            return self.unknown(cited)
        confidence = min(max(answer.confidence, 0.0), 1.0)
        if len(cited) == 1:
            confidence = min(confidence, SINGLE_EVIDENCE_MAX_CONFIDENCE)
        command = (answer.suggested_command or "").strip() or None
        return RCA(cause=cause, confidence=confidence, evidence=cited, suggested_command=command)


async def investigate(alert: Alert, tools: list[Tool], llm: LLMProvider, max_steps: int = 12) -> RCA:
    """Investiga o alerta. Nunca levanta exceção, exceto `ConsentRequired` (erro de configuração)."""
    inv = _Investigation(tools)
    specs = inv.specs()
    messages = [
        Message(role="system", content=p.SYSTEM_PROMPT),
        Message(
            role="user",
            content=p.render_alert(alert.name, alert.labels, alert.fired_at.isoformat()),
        ),
    ]
    for _ in range(max_steps):
        try:
            response = await llm.complete(messages, specs)
        except ConsentRequired:
            raise
        except Exception:
            return inv.unknown()
        final = next((c for c in response.tool_calls if c.name == p.FINAL_TOOL_NAME), None)
        if final is not None:
            return inv.conclude(final.args)
        if not response.tool_calls:
            # Texto livre nunca vira RCA.
            if response.text:  # provedores rejeitam turno de assistente vazio
                messages.append(Message(role="assistant", content=response.text))
            messages.append(Message(role="user", content=p.NUDGE_USE_FINAL))
            continue
        messages.append(
            Message(role="assistant", content=response.text or "", tool_calls=response.tool_calls)
        )
        for tool_call in response.tool_calls:
            content = await inv.run_tool(tool_call)
            messages.append(Message(role="tool", content=content, tool_call_id=tool_call.id))
    return inv.unknown()
