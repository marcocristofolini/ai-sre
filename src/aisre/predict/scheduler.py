"""Agendador preditivo: somente leitura; abre investigação via callable injetado."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping

import numpy as np

from aisre.predict.detector import WorkloadDetector
from aisre.tools.prometheus import PrometheusTool

log = logging.getLogger(__name__)


class PredictiveScheduler:
    def __init__(
        self,
        detectors: Mapping[str, WorkloadDetector],
        read_point: Callable[[str], Awaitable[np.ndarray]],
        open_investigation: Callable[[str, float], Awaitable[None]],
        risk_threshold: float = 0.65,
        cooldown_seconds: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._detectors = detectors
        self._read_point = read_point
        self._open = open_investigation
        self._threshold = risk_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._last_opened: dict[str, float] = {}

    async def run_once(self) -> None:
        for workload, detector in self._detectors.items():
            try:
                await self._check(workload, detector)
            except Exception as exc:  # um workload falho não interrompe os demais
                log.warning("verificação preditiva falhou (%s)", type(exc).__name__)

    async def _check(self, workload: str, detector: WorkloadDetector) -> None:
        if detector.state != "ativo":
            return
        last = self._last_opened.get(workload)
        if last is not None and self._clock() - last < self._cooldown:
            return
        score = detector.score(await self._read_point(workload))
        if score > self._threshold:
            await self._open(workload, score)
            self._last_opened[workload] = self._clock()  # só após abrir com sucesso

    async def run_forever(self, interval_seconds: float) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(interval_seconds)


async def backfill_detector(
    detector: WorkloadDetector,
    prometheus: PrometheusTool,
    query: str,
    start: float,
    end: float,
    step: float,
) -> int:
    """Ajusta o detector com o histórico do Prometheus; devolve nº de pontos lidos.

    Falha de leitura (inclusive resposta com mais de uma série: use query agregadora) é
    registrada (só o tipo) e devolve 0. Histórico vazio ou curto demais também deixa o
    detector como estava (ativo continua ativo)."""
    try:
        history = await prometheus.query_range(query, start, end, step)
    except Exception as exc:
        log.warning("backfill do Prometheus falhou (%s)", type(exc).__name__)
        return 0
    # histórico vazio/curto não derruba um detector já ativo
    detector.fit_if_sufficient(history)
    return int(history.size)
