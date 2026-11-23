"""Laço de investigação: o LLM consulta ferramentas somente leitura e entrega um RCA validado."""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import get_args

from pydantic import BaseModel, Field, ValidationError

from aisre import strings_pt as t
from aisre.agent import prompts as p
from aisre.llm.base import LLMProvider, Message, ToolCall
from aisre.llm.gate import ConsentRequired
from aisre.schemas import RCA, Evidence
from aisre.tools.base import Tool, ToolResult, safe_error_text

EVIDENCE_KINDS = set(get_args(Evidence.model_fields["kind"].annotation))
SINGLE_EVIDENCE_MAX_CONFIDENCE = 0.6

log = logging.getLogger(__name__)


class Alert(BaseModel):
    name: str
    labels: dict[str, str] = Field(default_factory=dict)
    fired_at: datetime


class _Cited(BaseModel):
    id: str | None = None  # id opaco (E1, E2...) mostrado no cabeçalho do dado
    ref: str | None = None  # aceito se igual ao ref original ou ao exibido
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
        # id opaco e ref exibido (escapado) -> ref original; só para resultados ok.
        self.ids: dict[str, str] = {}
        self.aliases: dict[str, str] = {}

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
                p.TOOL_ERR_EXCEPTION.format(detalhe=safe_error_text(str(exc)) or type(exc).__name__),
                ok="false",
            )
        if not isinstance(result, ToolResult):
            return p.wrap_data(p.TOOL_ERR_EXCEPTION.format(detalhe=type(result).__name__), ok="false")
        self.results[result.ref] = result
        evidence_id = None
        if result.ok:
            self.citable.add(result.ref)
            evidence_id = next((i for i, r in self.ids.items() if r == result.ref), None)
            if evidence_id is None:
                evidence_id = f"E{len(self.ids) + 1}"
                self.ids[evidence_id] = result.ref
            self.aliases[p.displayed_ref(result.ref)] = result.ref
        return p.render_tool_result(result.ref, result.ok, result.data, result.error, evidence_id)

    def resolve(self, item: _Cited) -> str | None:
        """Ref original citado, ou None se não foi devolvido com ok=True aqui."""
        if item.id is not None and item.id.strip() in self.ids:
            return self.ids[item.id.strip()]
        if item.ref is not None:
            if item.ref in self.citable:
                return item.ref
            return self.aliases.get(item.ref)
        return None

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
                # Defesa em profundidade: ferramentas fora do contrato podem devolver erro bruto.
                erro = safe_error_text(res.error) if res.error else t.ERRO_FERRAMENTA_SEM_DETALHE
                summary = t.EVIDENCIA_CONSULTA_FALHOU.format(erro=erro)
            out.append(Evidence(kind=kind, ref=ref, summary=summary))
        return out

    def unknown(self, cited: list[Evidence] | None = None) -> RCA:
        evidence = list(cited or [])
        seen = {e.ref for e in evidence}
        evidence += [e for e in self.collected() if e.ref not in seen]
        return RCA(cause=None, confidence=0.0, unknown=True, evidence=evidence)

    def conclude(self, args: dict) -> RCA:
        try:
            return self._conclude(args)
        except Exception:  # qualquer resposta final inválida vira "não sei"
            return self.unknown()

    def _conclude(self, args: dict) -> RCA:
        try:
            answer = _FinalAnswer.model_validate(args)
        except ValidationError:
            return self.unknown()
        cited: list[Evidence] = []
        for item in answer.evidence:
            ref = self.resolve(item)
            kind = _kind(ref) if ref else None
            if ref and kind and ref not in {e.ref for e in cited}:
                cited.append(Evidence(kind=kind, ref=ref, summary=item.summary.strip()))
        cause = (answer.cause or "").strip() or None
        if cause is None or not cited or not math.isfinite(answer.confidence):
            return self.unknown(cited)
        confidence = min(max(answer.confidence, 0.0), 1.0)
        if len(cited) == 1:
            confidence = min(confidence, SINGLE_EVIDENCE_MAX_CONFIDENCE)
        command = (answer.suggested_command or "").strip() or None
        return RCA(cause=cause, confidence=confidence, evidence=cited, suggested_command=command)


async def investigate(
    alert: Alert,
    tools: list[Tool],
    llm: LLMProvider,
    max_steps: int = 12,
    past: list | None = None,
) -> RCA:
    """Investiga o alerta. Nunca levanta exceção, exceto `ConsentRequired` (erro de configuração).

    `past`: incidentes anteriores semelhantes (`PastIncident`), entregues como dado não confiável
    e sem id: nunca são evidência citável (os ids E1.. vêm só das ferramentas desta investigação)."""
    inv = _Investigation(tools)
    specs = inv.specs()
    messages = [
        Message(role="system", content=p.SYSTEM_PROMPT),
        Message(
            role="user",
            content="\n".join(
                part
                for part in (
                    p.render_alert(alert.name, alert.labels, alert.fired_at.isoformat()),
                    p.render_past_incidents(past or []),
                )
                if part
            ),
        ),
    ]
    for _ in range(max_steps):
        try:
            response = await llm.complete(messages, specs)
        except ConsentRequired:
            raise
        except Exception as exc:
            # Só o tipo: a mensagem pode conter trechos do pedido.
            log.warning(t.AVISO_LLM_FALHA_INVESTIGACAO.format(tipo=type(exc).__name__))
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
