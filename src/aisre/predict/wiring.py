"""Liga o módulo preditivo ao app: detectores por workload, leitura no Prometheus e execução."""

from __future__ import annotations

import asyncio
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
        self.fits = 0  # ajustes que produziram modelo (para saber se um refit funcionou)

    def fit(self, series: np.ndarray) -> None:
        super().fit(series)
        if self.state == "ativo":
            self.fits += 1

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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._clock = clock
        # Reintento do backfill para detectores ainda "aprendendo": backoff exponencial limitado.
        self._next_try: dict[str, float] = {}
        self._backoff: dict[str, float] = {}
        # Reajuste periódico dos ativos: próximo horário por workload (relógio injetável).
        self._next_refit: dict[str, float] = {}
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

    async def _backfill_one(self, name: str, now: float | None = None) -> None:
        end = time.time() if now is None else now
        await backfill_detector(
            self.detectors[name], self.prometheus, self.queries[name],
            end - self.settings.predict_backfill_seconds, end,
            float(self.settings.predict_backfill_step_seconds),
        )

    async def backfill(self, now: float | None = None) -> None:
        for name in self.queries:
            await self._backfill_one(name, now)

    async def retry_learning(self) -> None:
        """Refaz o backfill dos detectores ainda "aprendendo" (Prometheus fora do ar no boot,
        histórico curto). Backoff por workload: dobra a cada falha até o máximo; sem tempestade."""
        now = self._clock()
        for name, det in self.detectors.items():
            if det.state == "ativo":
                self._next_try.pop(name, None)
                self._backoff.pop(name, None)
                self._next_refit.setdefault(name, now + self.settings.predict_refit_interval_seconds)
                continue
            if now < self._next_try.get(name, 0.0):
                continue
            try:
                await self._backfill_one(name)
            except Exception as exc:  # um workload não derruba o laço
                log.warning(t.AVISO_PREDITIVO_RETRY_FALHOU.format(tipo=type(exc).__name__))
            if det.state == "ativo":
                self._next_refit[name] = self._clock() + self.settings.predict_refit_interval_seconds
                continue
            wait = min(
                self._backoff.get(name, self.settings.predict_retry_initial_seconds / 2) * 2,
                self.settings.predict_retry_max_seconds,
            )
            self._backoff[name], self._next_try[name] = wait, self._clock() + wait

    async def refit_active(self) -> None:
        """Reajusta os detectores ativos cujo intervalo de refit venceu, com o histórico recente.

        Falha de leitura ou histórico insuficiente mantêm o modelo atual (nunca volta a "aprendendo")
        e uma nova tentativa ocorre depois de `predict_retry_max_seconds`."""
        now = self._clock()
        for name, det in self.detectors.items():
            if det.state != "ativo":
                continue
            due = self._next_refit.setdefault(name, now + self.settings.predict_refit_interval_seconds)
            if now < due:
                continue
            before = det.fits
            try:
                await self._backfill_one(name)
            except Exception as exc:  # um workload não derruba o laço
                log.warning(t.AVISO_PREDITIVO_REFIT_FALHOU.format(tipo=type(exc).__name__))
            if det.fits > before:
                self._next_refit[name] = self._clock() + self.settings.predict_refit_interval_seconds
            else:
                self._next_refit[name] = self._clock() + self.settings.predict_retry_max_seconds

    async def run(self) -> None:
        """Laço cancelável: tenta o backfill dos que ainda aprendem, reajusta os ativos quando vence
        o intervalo e avalia os ativos a cada tick."""
        while True:
            await self.retry_learning()
            await self.refit_active()
            await self.scheduler.run_once()
            await asyncio.sleep(self.settings.predict_interval_seconds)

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
