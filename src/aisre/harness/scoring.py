from pydantic import BaseModel

from aisre import strings_pt as t
from aisre.harness.scenarios import Scenario
from aisre.schemas import RCA


class GradedResult(BaseModel):
    scenario_id: str
    correct: bool
    confidence: float


class Scoreboard(BaseModel):
    correct: int
    total: int
    model: str
    injection_errors: int = 0
    label: str = t.ROTULO_CENARIOS_SINTETICOS


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def grade(rca: RCA, scenario: Scenario) -> bool:
    if rca.unknown or rca.cause is None:
        return False
    if _norm(rca.cause) != _norm(scenario.expected_cause):
        return False
    patterns = [p for p in scenario.expected_evidence_refs if p]
    return any(e.ref.startswith(p) for e in rca.evidence for p in patterns)


def calibration(
    results: list[tuple[float, bool]], bins: int = 5
) -> list[tuple[float, float]]:
    """Por faixa não vazia: (confiança média, taxa de acerto)."""
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for conf, ok in results:
        buckets[min(int(conf * bins), bins - 1)].append((conf, ok))
    return [
        (sum(c for c, _ in b) / len(b), sum(1 for _, ok in b if ok) / len(b))
        for b in buckets
        if b
    ]


def scoreboard(
    results: list[GradedResult], model: str, injection_errors: int = 0
) -> Scoreboard:
    return Scoreboard(
        correct=sum(1 for r in results if r.correct),
        total=len(results),
        model=model,
        injection_errors=injection_errors,
    )
