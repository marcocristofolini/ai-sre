import re
import unicodedata

from pydantic import BaseModel

from aisre import strings_pt as t
from aisre.harness.scenarios import Scenario
from aisre.schemas import RCA


class GradedResult(BaseModel):
    scenario_id: str
    correct: bool
    confidence: float
    measurable: bool = True
    reason: str | None = None  # motivo quando não medível (ou falha de injeção)


class CalibrationBin(BaseModel):
    confidence: float
    hit_rate: float


class Scoreboard(BaseModel):
    """Placar de `make eval`. Campos depois de `calibration` são opcionais: arquivos antigos
    (sem eles) continuam válidos para a API e o dashboard."""

    correct: int  # só cenários medíveis
    total: int  # só cenários medíveis
    model: str
    injection_errors: int = 0
    label: str = t.ROTULO_CENARIOS_SINTETICOS
    calibration: list[CalibrationBin] = []
    timestamp: str | None = None  # ISO 8601 (UTC) do fim da execução
    git_commit: str | None = None
    enabled_tools: list[str] | None = None  # None = não verificado (todas as medições contam)
    not_measurable: int = 0
    scenarios: list[GradedResult] = []


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(ch for ch in folded if not unicodedata.combining(ch)).split())


def cause_matches(cause: str, scenario: Scenario) -> bool:
    """Igual à causa esperada, ou contém todas as palavras de algum grupo aceito (veja scenarios)."""
    norm = _norm(cause)
    if norm == _norm(scenario.expected_cause):
        return True
    for group in scenario.accepted_causes:
        if group and all(re.search(r"(?<!\w)" + re.escape(_norm(kw)), norm) for kw in group):
            return True
    return False


def grade(rca: RCA, scenario: Scenario) -> bool:
    if rca.unknown or rca.cause is None:
        return False
    if not cause_matches(rca.cause, scenario):
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
    results: list[GradedResult],
    model: str,
    injection_errors: int = 0,
    *,
    timestamp: str | None = None,
    git_commit: str | None = None,
    enabled_tools: list[str] | None = None,
) -> Scoreboard:
    measured = [r for r in results if r.measurable]
    return Scoreboard(
        correct=sum(1 for r in measured if r.correct),
        total=len(measured),
        model=model,
        injection_errors=injection_errors,
        calibration=[
            CalibrationBin(confidence=c, hit_rate=h)
            for c, h in calibration([(r.confidence, r.correct) for r in measured])
        ],
        timestamp=timestamp,
        git_commit=git_commit,
        enabled_tools=enabled_tools,
        not_measurable=len(results) - len(measured),
        scenarios=results,
    )
