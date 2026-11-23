"""Injeção de falhas em um cluster `kind` e execução do placar sintético."""

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from aisre.harness.scenarios import SCENARIOS, Scenario
from aisre.harness.scoring import GradedResult, Scoreboard, grade, scoreboard
from aisre.schemas import RCA

MANIFESTS_DIR = Path(__file__).resolve().parents[3] / "harness" / "manifests"


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
        "--wait=false",
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
    for scenario in SCENARIOS if scenarios is None else scenarios:
        try:
            handle = inject(scenario, kubeconfig)
        except Exception:
            results.append(GradedResult(scenario_id=scenario.id, correct=False, confidence=0.0))
            continue
        try:
            try:
                rca = investigate(scenario)
                result = GradedResult(
                    scenario_id=scenario.id,
                    correct=grade(rca, scenario),
                    confidence=rca.confidence,
                )
            except Exception:
                result = GradedResult(
                    scenario_id=scenario.id, correct=False, confidence=0.0
                )
        finally:
            cleanup(handle)
        results.append(result)
    return scoreboard(results, model)


def _unknown_investigate(_scenario: Scenario) -> RCA:
    return RCA(cause=None, confidence=0.0, unknown=True)


def main(argv: list[str]) -> int:
    """Uso: python -m aisre.harness.runner <saida.json> (investigador sempre 'desconhecido')."""
    out = Path(argv[0]) if argv else Path("eval/scoreboard.json")
    board = run_all(
        _unknown_investigate,
        "baseline-desconhecido",
        kubeconfig=os.environ.get("KUBECONFIG", ""),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(board.model_dump(), ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
