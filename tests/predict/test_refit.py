"""I10: detectores ativos são reajustados periodicamente (mudança de patamar permanente)."""

import asyncio

import httpx
import numpy as np

from aisre.config import Settings
from aisre.predict.detector import WorkloadDetector
from aisre.predict.wiring import Predictive
from aisre.tools.prometheus import PrometheusTool

WORKLOAD = {"name": "prod/api", "query": "sum(up)"}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _series(level):
    return [[i, str(level + ((i * 37) % 100) / 50)] for i in range(300)]


def _predictive(state, clock, **kw):
    def handler(request):
        state["calls"].append(request.url.path)
        if state.get("down"):
            raise httpx.ConnectError("x")
        if request.url.path == "/api/v1/query_range":
            values = _series(state["level"]) if not state.get("short") else _series(state["level"])[:5]
            return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix",
                                  "result": [{"metric": {}, "values": values}]}})
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector",
                              "result": [{"metric": {}, "value": [1, str(state["point"])]}]}})

    settings = Settings(_env_file=None, prometheus_url="http://prom", predict_workloads=[WORKLOAD],
                        min_baseline_points=20, predict_refit_interval_seconds=100,
                        predict_retry_max_seconds=30, **kw)
    opened = []

    async def open_inv(w, s):
        opened.append((w, s))

    tool = PrometheusTool("http://prom", transport=httpx.MockTransport(handler))
    return Predictive(settings, tool, open_inv, clock=clock), opened


def test_level_shift_stops_firing_after_refit():
    clock = Clock()
    state = {"calls": [], "level": 10.0, "point": 500.0}
    p, _ = _predictive(state, clock)
    det = p.detectors["prod/api"]

    async def run():
        await p.retry_learning()
        assert det.state == "ativo"
        high = det.score(np.array([500.0]))
        # O workload mudou de patamar para sempre: o histórico recente agora está em ~500.
        state["level"] = 500.0
        clock.t += 99
        await p.refit_active()
        assert det.score(np.array([500.0])) == high  # antes do intervalo: sem refit
        clock.t += 1
        await p.refit_active()
        assert det.score(np.array([501.0])) < high  # o novo normal deixou de ser anômalo
        assert p._next_refit["prod/api"] == clock.t + 100

    asyncio.run(run())


def test_refit_failure_or_short_history_keeps_model_and_retries_later():
    clock = Clock()
    state = {"calls": [], "level": 10.0, "point": 11.0}
    p, _ = _predictive(state, clock)
    det = p.detectors["prod/api"]

    async def run():
        await p.retry_learning()
        fits = det.fits
        for breakage in ("down", "short"):
            state[breakage] = True
            clock.t += 1000
            await p.refit_active()
            assert det.state == "ativo" and det.fits == fits  # modelo atual mantido
            assert p._next_refit["prod/api"] == clock.t + 30  # nova tentativa em retry_max
            state[breakage] = False
        clock.t += 30
        await p.refit_active()
        assert det.fits == fits + 1

    asyncio.run(run())


def test_learning_detectors_are_not_refit():
    clock = Clock()
    state = {"calls": [], "level": 10.0, "point": 11.0, "down": True}
    p, _ = _predictive(state, clock)
    asyncio.run(p.refit_active())
    assert state["calls"] == [] and p.detectors["prod/api"].state == "aprendendo"


def test_documented_degenerate_baselines():
    flat = WorkloadDetector(min_baseline_points=10)
    flat.fit(np.full(1000, 5.0))
    assert abs(flat.score(np.array([500.0])) - 0.5) < 1e-6  # plana: 0.5 constante, nunca dispara
    trend = WorkloadDetector(min_baseline_points=10)
    trend.fit(np.linspace(0, 100, 1000))
    assert 0.6 < trend.score(np.array([150.0])) < 0.7  # tendência: na fronteira do limiar padrão
    assert WorkloadDetector.__module__  # docstring documenta ambos
    from aisre.predict import detector

    assert "0.5" in detector.__doc__ and "0.65" in detector.__doc__


def test_settings_default_refit_interval_is_daily():
    assert Settings(_env_file=None).predict_refit_interval_seconds == 86400.0
