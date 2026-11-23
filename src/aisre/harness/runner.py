"""Injeção de falhas em um cluster `kind` e execução do placar sintético."""

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from aisre import strings_pt as t
from aisre.agent.loop import Alert, investigate
from aisre.config import Settings
from aisre.harness.scenarios import SCENARIOS, ClusterCheck, Scenario, execution_order
from aisre.harness.scoring import GradedResult, Scoreboard, grade, scoreboard
from aisre.llm.base import LLMProvider, build_provider
from aisre.llm.gate import ConsentRequired
from aisre.schemas import RCA
from aisre.tools import Tool, build_tools

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFESTS_DIR = REPO_ROOT / "harness" / "manifests"
# Nome neutro: o alerta entregue ao agente não revela a falha injetada.
EVAL_ALERT_NAME = "AisreEvalCenario"
AGENT_FLAG = "--agent"
AGENT_ENV = "AISRE_EVAL_AGENT"
ALLOW_NON_KIND_FLAG = "--allow-non-kind"
KIND_CONTEXT = "kind-aisre-eval"  # contexto que `kind create cluster --name aisre-eval` grava
POLL_SECONDS = 5.0


class InjectionHandle(BaseModel):
    scenario_id: str
    kubeconfig: str


class SettleTimeout(Exception):
    def __init__(self, seconds: int) -> None:
        super().__init__(t.EVAL_NAO_MANIFESTOU.format(segundos=seconds))
        self.seconds = seconds


def manifest_path(scenario: Scenario) -> Path:
    return MANIFESTS_DIR / f"{scenario.id}.yaml"


def _kubectl(kubeconfig: str, *args: str) -> str:
    if not kubeconfig:
        # `kubectl --kubeconfig ""` cai no contexto padrão do usuário: nunca.
        raise ValueError(t.ERRO_EVAL_KUBECONFIG_VAZIO)
    return subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig, *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def current_context(kubeconfig: str) -> str:
    return _kubectl(kubeconfig, "config", "current-context").strip()


def check_target_cluster(
    kubeconfig: str, allow_non_kind: bool = False, context_of: Callable[[str], str] = current_context
) -> str | None:
    """Mensagem de erro se o alvo não for o cluster kind dedicado; None se pode injetar."""
    if not kubeconfig:
        return t.ERRO_EVAL_KUBECONFIG_VAZIO
    if allow_non_kind:
        return None
    try:
        ctx = context_of(kubeconfig)
    except Exception as exc:  # noqa: BLE001
        ctx = f"<{type(exc).__name__}>"
    if ctx != KIND_CONTEXT:
        return t.ERRO_EVAL_CONTEXTO.format(atual=ctx, esperado=KIND_CONTEXT)
    return None


def wait_for(
    check: ClusterCheck,
    kubeconfig: str,
    *,
    run: Callable[..., str] = _kubectl,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    interval: float = POLL_SECONDS,
) -> bool:
    """Consulta `kubectl <args>` até a saída conter `expect` ou estourar o prazo."""
    deadline = clock() + check.timeout_seconds
    while True:
        try:
            if check.expect in run(kubeconfig, *check.args):
                return True
        except subprocess.CalledProcessError:
            pass  # recurso ainda não existe: tenta de novo
        if clock() >= deadline:
            return False
        sleep(interval)


def inject(scenario: Scenario, kubeconfig: str) -> InjectionHandle:
    """Aplica o manifesto do cenário no cluster apontado pelo kubeconfig."""
    handle = InjectionHandle(scenario_id=scenario.id, kubeconfig=kubeconfig)
    _kubectl(kubeconfig, "apply", "-f", str(manifest_path(scenario)))
    return handle


def settle(scenario: Scenario, kubeconfig: str) -> None:
    """Espera a falha se manifestar (ex.: OOMKilled, DiskPressure) antes de investigar."""
    if scenario.settle is not None and not wait_for(scenario.settle, kubeconfig):
        raise SettleTimeout(scenario.settle.timeout_seconds)


def _scenario(scenario_id: str) -> Scenario | None:
    return next((s for s in SCENARIOS if s.id == scenario_id), None)


def cleanup(handle: InjectionHandle) -> None:
    """Remove o que o manifesto criou (idempotente) e, se o cenário pede, espera o cluster normalizar.

    Se a condição não some (ex.: o pod que enchia o disco foi despejado sem rodar o preStop), aplica
    o manifesto `<id>-cleaner.yaml`, quando existe, e espera de novo."""
    path = MANIFESTS_DIR / f"{handle.scenario_id}.yaml"
    _kubectl(handle.kubeconfig, "delete", "-f", str(path), "--ignore-not-found", "--wait=true", "--timeout=120s")
    scenario = _scenario(handle.scenario_id)
    if scenario is None or scenario.clear is None or wait_for(scenario.clear, handle.kubeconfig):
        return
    cleaner = MANIFESTS_DIR / f"{handle.scenario_id}-cleaner.yaml"
    if cleaner.is_file():
        _kubectl(handle.kubeconfig, "apply", "-f", str(cleaner))
        try:
            cleared = wait_for(scenario.clear, handle.kubeconfig)
        finally:
            _kubectl(handle.kubeconfig, "delete", "-f", str(cleaner), "--ignore-not-found", "--wait=true", "--timeout=120s")
        if cleared:
            return
    raise RuntimeError(t.EVAL_NAO_NORMALIZOU.format(segundos=scenario.clear.timeout_seconds))


def run_all(
    investigate: Callable[[Scenario], RCA],
    model: str,
    *,
    scenarios: list[Scenario] | None = None,
    kubeconfig: str = "",
    inject: Callable[[Scenario, str], InjectionHandle] = inject,
    cleanup: Callable[[InjectionHandle], None] = cleanup,
    settle: Callable[[Scenario, str], None] = settle,
    enabled_tools: set[str] | None = None,
    git_commit: str | None = None,
) -> Scoreboard:
    """Roda os cenários e devolve o placar.

    `enabled_tools`: ferramentas habilitadas no ambiente; cenários cujas evidências exigem só
    ferramentas ausentes NÃO são injetados e saem do total ("não medível", com motivo).
    None = não verificado (todos contam)."""
    results: list[GradedResult] = []
    injection_errors = 0

    def _safe_cleanup(handle: InjectionHandle) -> None:
        try:
            cleanup(handle)
        except Exception as exc:
            print(f"aviso: cleanup de {handle.scenario_id} falhou: {exc}", file=sys.stderr)

    for scenario in execution_order(SCENARIOS if scenarios is None else scenarios):
        needed = scenario.required_tools()
        if enabled_tools is not None and not needed & enabled_tools:
            reason = t.EVAL_NAO_MEDIVEL.format(ferramentas=", ".join(sorted(needed)))
            results.append(
                GradedResult(scenario_id=scenario.id, correct=False, confidence=0.0, measurable=False, reason=reason)
            )
            continue
        handle = InjectionHandle(scenario_id=scenario.id, kubeconfig=kubeconfig)
        try:
            handle = inject(scenario, kubeconfig)
            settle(scenario, kubeconfig)
        except Exception as exc:
            injection_errors += 1
            reason = str(exc) if isinstance(exc, SettleTimeout) else t.EVAL_INJECAO_FALHOU.format(tipo=type(exc).__name__)
            print(f"erro: injecao de {scenario.id} falhou: {exc}", file=sys.stderr)
            _safe_cleanup(handle)  # o apply pode ter criado recursos parciais
            results.append(GradedResult(scenario_id=scenario.id, correct=False, confidence=0.0, reason=reason))
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
    return scoreboard(
        results,
        model,
        injection_errors,
        timestamp=datetime.now(UTC).isoformat(),
        git_commit=git_commit,
        enabled_tools=None if enabled_tools is None else sorted(enabled_tools),
    )


def git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], check=True, capture_output=True, text=True, timeout=10
        )
    except Exception:  # noqa: BLE001 - sem git ou fora de um repositório
        return None
    return out.stdout.strip() or None


def _unknown_investigate(_scenario: Scenario) -> RCA:
    return RCA(cause=None, confidence=0.0, unknown=True)


def scenario_alert(scenario: Scenario) -> Alert:
    return Alert(
        name=EVAL_ALERT_NAME,
        labels={"namespace": scenario.namespace},
        fired_at=datetime.now(UTC),
    )


def agent_investigator(
    provider_factory: Callable[[], LLMProvider], tools: list[Tool]
) -> Callable[[Scenario], RCA]:
    """Adapta o agente assíncrono ao contrato síncrono de `run_all`.

    Cada cenário roda em um `asyncio.run` próprio; o provedor (e seu cliente HTTP
    assíncrono, preso ao event loop) é criado a cada cenário para nunca ser reutilizado
    em um loop já fechado.
    """

    def _investigate(scenario: Scenario) -> RCA:
        return asyncio.run(investigate(scenario_alert(scenario), tools, provider_factory()))

    return _investigate


def main(argv: list[str]) -> int:
    """Uso: python -m aisre.harness.runner <saida.json> [--agent] [--allow-non-kind].

    Sem `--agent` (ou AISRE_EVAL_AGENT=1) o investigador responde sempre 'desconhecido'.
    Com ele, provedor e ferramentas vêm das configurações AISRE_*. KUBECONFIG é obrigatório e o
    contexto atual precisa ser `kind-aisre-eval`, salvo `--allow-non-kind` explícito.
    """
    use_agent = AGENT_FLAG in argv or os.environ.get(AGENT_ENV) == "1"
    allow_non_kind = ALLOW_NON_KIND_FLAG in argv
    args = [a for a in argv if a not in (AGENT_FLAG, ALLOW_NON_KIND_FLAG)]
    out = Path(args[0]) if args else Path("eval/scoreboard.json")
    kubeconfig = os.environ.get("KUBECONFIG", "")
    error = check_target_cluster(kubeconfig, allow_non_kind)
    if error:
        print(f"erro: {error}", file=sys.stderr)
        return 2
    investigator: Callable[[Scenario], RCA] = _unknown_investigate
    model = "baseline-desconhecido"
    settings = Settings()
    tools = build_tools(settings)
    if use_agent:
        try:
            llm = build_provider(settings)  # valida a configuração antes de injetar falhas
        except ValueError as exc:
            print(t.ERRO_EVAL_CONFIGURACAO.format(detalhe=exc), file=sys.stderr)
            return 2
        investigator = agent_investigator(lambda: build_provider(settings), tools)
        model = f"{llm.name}:{settings.llm_model}"
    try:
        board = run_all(
            investigator,
            model,
            kubeconfig=kubeconfig,
            enabled_tools={tool.name for tool in tools},
            git_commit=git_commit(),
        )
    except ConsentRequired as exc:
        print(t.ERRO_EVAL_CONFIGURACAO.format(detalhe=exc), file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(board.model_dump(), ensure_ascii=False, indent=2) + "\n")
    return 1 if board.injection_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
