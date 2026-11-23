"""Injeção de falhas em um cluster `kind` e execução do placar sintético."""

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from aisre import strings_pt as t
from aisre.agent.loop import Alert, investigate
from aisre.config import Settings
from aisre.harness.scenarios import SCENARIOS, Scenario
from aisre.harness.scoring import GradedResult, Scoreboard, grade, scoreboard
from aisre.llm.base import LLMProvider, build_provider
from aisre.llm.gate import ConsentRequired
from aisre.schemas import RCA
from aisre.tools import Tool, build_tools

MANIFESTS_DIR = Path(__file__).resolve().parents[3] / "harness" / "manifests"
# Nome neutro: o alerta entregue ao agente não revela a falha injetada.
EVAL_ALERT_NAME = "AisreEvalCenario"
AGENT_FLAG = "--agent"
AGENT_ENV = "AISRE_EVAL_AGENT"


class InjectionHandle(BaseModel):
    scenario_id: str
    kubeconfig: str


def manifest_path(scenario: Scenario) -> Path:
    return MANIFESTS_DIR / f"{scenario.id}.yaml"


def _kubectl(kubeconfig: str, *args: str) -> None:
    subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig, *args],
        check=True,
        capture_output=True,
        text=True,
    )


def inject(scenario: Scenario, kubeconfig: str) -> InjectionHandle:
    """Aplica o manifesto do cenário no cluster apontado pelo kubeconfig."""
    handle = InjectionHandle(scenario_id=scenario.id, kubeconfig=kubeconfig)
    _kubectl(kubeconfig, "apply", "-f", str(manifest_path(scenario)))
    return handle


def cleanup(handle: InjectionHandle) -> None:
    """Remove o que o manifesto criou (idempotente)."""
    path = MANIFESTS_DIR / f"{handle.scenario_id}.yaml"
    _kubectl(
        handle.kubeconfig,
        "delete",
        "-f",
        str(path),
        "--ignore-not-found",
        "--wait=true",
        "--timeout=120s",
    )


def run_all(
    investigate: Callable[[Scenario], RCA],
    model: str,
    *,
    scenarios: list[Scenario] | None = None,
    kubeconfig: str = "",
    inject: Callable[[Scenario, str], InjectionHandle] = inject,
    cleanup: Callable[[InjectionHandle], None] = cleanup,
) -> Scoreboard:
    results: list[GradedResult] = []
    injection_errors = 0

    def _safe_cleanup(handle: InjectionHandle) -> None:
        try:
            cleanup(handle)
        except Exception as exc:
            print(f"aviso: cleanup de {handle.scenario_id} falhou: {exc}", file=sys.stderr)

    for scenario in SCENARIOS if scenarios is None else scenarios:
        handle = InjectionHandle(scenario_id=scenario.id, kubeconfig=kubeconfig)
        try:
            handle = inject(scenario, kubeconfig)
        except Exception as exc:
            injection_errors += 1
            print(f"erro: injecao de {scenario.id} falhou: {exc}", file=sys.stderr)
            _safe_cleanup(handle)  # o apply pode ter criado recursos parciais
            results.append(GradedResult(scenario_id=scenario.id, correct=False, confidence=0.0))
            continue
        try:
            rca = investigate(scenario)
            result = GradedResult(
                scenario_id=scenario.id,
                correct=grade(rca, scenario),
                confidence=rca.confidence,
            )
        except ConsentRequired:
            raise  # erro de configuração: o operador precisa ver (o cleanup roda no finally)
        except Exception:
            result = GradedResult(scenario_id=scenario.id, correct=False, confidence=0.0)
        finally:
            _safe_cleanup(handle)
        results.append(result)
    return scoreboard(results, model, injection_errors)


def _unknown_investigate(_scenario: Scenario) -> RCA:
    return RCA(cause=None, confidence=0.0, unknown=True)


def scenario_alert(scenario: Scenario) -> Alert:
    return Alert(
        name=EVAL_ALERT_NAME,
        labels={"namespace": f"aisre-{scenario.id}"},
        fired_at=datetime.now(UTC),
    )


def agent_investigator(llm: LLMProvider, tools: list[Tool]) -> Callable[[Scenario], RCA]:
    """Adapta o agente assíncrono ao contrato síncrono de `run_all`."""

    def _investigate(scenario: Scenario) -> RCA:
        return asyncio.run(investigate(scenario_alert(scenario), tools, llm))

    return _investigate


def main(argv: list[str]) -> int:
    """Uso: python -m aisre.harness.runner <saida.json> [--agent].

    Sem `--agent` (ou AISRE_EVAL_AGENT=1) o investigador responde sempre 'desconhecido'.
    Com ele, provedor e ferramentas vêm das configurações AISRE_*.
    """
    use_agent = AGENT_FLAG in argv or os.environ.get(AGENT_ENV) == "1"
    args = [a for a in argv if a != AGENT_FLAG]
    out = Path(args[0]) if args else Path("eval/scoreboard.json")
    investigator: Callable[[Scenario], RCA] = _unknown_investigate
    model = "baseline-desconhecido"
    if use_agent:
        settings = Settings()
        try:
            llm = build_provider(settings)
        except ValueError as exc:
            print(t.ERRO_EVAL_CONFIGURACAO.format(detalhe=exc), file=sys.stderr)
            return 2
        investigator = agent_investigator(llm, build_tools(settings))
        model = f"{llm.name}:{settings.llm_model}"
    try:
        board = run_all(investigator, model, kubeconfig=os.environ.get("KUBECONFIG", ""))
    except ConsentRequired as exc:
        print(t.ERRO_EVAL_CONFIGURACAO.format(detalhe=exc), file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(board.model_dump(), ensure_ascii=False, indent=2) + "\n")
    return 1 if board.injection_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
