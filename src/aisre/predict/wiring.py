"""Liga o módulo preditivo ao app: detectores por workload, leitura no Prometheus e execução."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

import numpy as np

from aisre import strings_pt as t
from aisre.config import Settings
from aisre.predict.detector import WorkloadDetector
from aisre.predict.scheduler import PredictiveScheduler, backfill_detector
from aisre.tools.prometheus import PrometheusTool

log = logging.getLogger(__name__)


class ObservedDetector(WorkloadDetector):
    """Guarda o último score calculado, para `/risk` mostrar o estado ao vivo sem reconsultar."""

    def __init__(self, min_baseline_points: int = 500) -> None:
        super().__init__(min_baseline_points)
        self.last_score = 0.0

    def score(self, point: np.ndarray) -> float:
        value = super().score(point)
        self.last_score = value
        return value


class Predictive:
    def __init__(
        self,
        settings: Settings,
        prometheus: PrometheusTool,
        open_investigation: Callable[[str, float], Awaitable[None]],
    ) -> None:
        self.settings = settings
        self.prometheus = prometheus
        self.queries = {w.name: w.query for w in settings.predict_workloads}
        self.detectors: dict[str, ObservedDetector] = {
            name: ObservedDetector(settings.min_baseline_points) for name in self.queries
        }
        self.scheduler = PredictiveScheduler(
            self.detectors,
            self._read_point,
            open_investigation,
            risk_threshold=settings.risk_threshold,
        )

    async def _read_point(self, workload: str) -> np.ndarray:
        return await self.prometheus.query_instant(self.queries[workload])

    async def backfill(self, now: float | None = None) -> None:
        end = time.time() if now is None else now
        start = end - self.settings.predict_backfill_seconds
        for name, query in self.queries.items():
            await backfill_detector(
                self.detectors[name], self.prometheus, query, start, end,
                float(self.settings.predict_backfill_step_seconds),
            )

    async def run(self) -> None:
        """Backfill e depois o laço do agendador (cancelável)."""
        await self.backfill()
        await self.scheduler.run_forever(self.settings.predict_interval_seconds)

    def risk(self) -> list[dict]:
        return [
            {
                "workload": name,
                "state": det.state,
                "score": round(det.last_score if det.state == "ativo" else 0.0, 4),
                "threshold": self.settings.risk_threshold,
            }
            for name, det in self.detectors.items()
        ]
