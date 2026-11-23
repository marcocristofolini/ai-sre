import asyncio

import httpx
import numpy as np
import pytest

from aisre.config import Settings
from aisre.harness.scenarios import SCENARIOS
from aisre.predict.detector import WorkloadDetector
from aisre.predict.scheduler import PredictiveScheduler, backfill_detector
from aisre.tools.prometheus import PrometheusTool


def _normal(n, seed=0):
    return np.random.default_rng(seed).normal(100.0, 2.0, size=n)


def test_detector_stays_learning_below_min_baseline():
    det = WorkloadDetector(min_baseline_points=500)
    det.fit(_normal(499))
    assert det.state == "aprendendo"
    # aprendendo nunca dispara, nem com ponto extremo
    assert det.score(np.array([10_000.0])) == 0.0


def test_detector_active_after_baseline():
    det = WorkloadDetector(min_baseline_points=500)
    det.fit(_normal(500))
    assert det.state == "ativo"


def test_anomalous_point_scores_higher_than_normal():
    det = WorkloadDetector(min_baseline_points=500)
    det.fit(_normal(600))
    normal = det.score(np.array([100.0]))
    anomalous = det.score(np.array([160.0]))
    assert 0.0 <= normal <= 1.0 and 0.0 <= anomalous <= 1.0
    assert anomalous > normal
    assert anomalous > Settings().risk_threshold > normal


def test_fit_is_deterministic():
    a, b = WorkloadDetector(50), WorkloadDetector(50)
    a.fit(_normal(100))
    b.fit(_normal(100))
    assert a.score(np.array([130.0])) == b.score(np.array([130.0]))


def test_refit_below_baseline_returns_to_learning():
    det = WorkloadDetector(min_baseline_points=100)
    det.fit(_normal(200))
    det.fit(_normal(10))
    assert det.state == "aprendendo"
    assert det.score(np.array([1e6])) == 0.0


def test_fit_ignores_non_finite_and_empty_input():
    det = WorkloadDetector(min_baseline_points=100)
    det.fit(np.array([]))
    assert det.state == "aprendendo"
    # 99 pontos finitos + NaN/inf nao contam para a base
    series = np.concatenate([_normal(99), [np.nan, np.inf, -np.inf]])
    det.fit(series)
    assert det.state == "aprendendo"
    det.fit(np.concatenate([_normal(100), [np.nan, np.inf]]))
    assert det.state == "ativo"


def test_score_non_finite_or_empty_point_is_zero():
    det = WorkloadDetector(min_baseline_points=100)
    det.fit(_normal(200))
    assert det.score(np.array([np.nan])) == 0.0
    assert det.score(np.array([np.inf])) == 0.0
    assert det.score(np.array([])) == 0.0


def test_score_wrong_feature_count_raises_value_error():
    det = WorkloadDetector(min_baseline_points=100)
    det.fit(_normal(200))
    with pytest.raises(ValueError):
        det.score(np.array([1.0, 2.0]))


def test_min_baseline_points_must_be_positive():
    with pytest.raises(ValueError):
        WorkloadDetector(min_baseline_points=0)


# ---------------- scheduler ----------------


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _detector(active: bool):
    det = WorkloadDetector(min_baseline_points=100)
    det.fit(_normal(200 if active else 10))
    return det


def _sched(detectors, readings, opened, clock=None, cooldown=600.0, threshold=0.65):
    async def read(workload):
        value = readings[workload]
        if isinstance(value, Exception):
            raise value
        return np.array([value])

    async def open_inv(workload, score):
        opened.append(workload)

    return PredictiveScheduler(
        detectors=detectors,
        read_point=read,
        open_investigation=open_inv,
        risk_threshold=threshold,
        cooldown_seconds=cooldown,
        clock=clock or Clock(),
    )


def test_scheduler_does_not_open_investigation_while_learning():
    opened = []
    s = _sched({"eval-1/api": _detector(False)}, {"eval-1/api": 10_000.0}, opened)
    asyncio.run(s.run_once())
    assert opened == []


def test_scheduler_opens_when_active_and_above_threshold():
    opened = []
    s = _sched({"a": _detector(True), "b": _detector(True)}, {"a": 200.0, "b": 100.0}, opened)
    asyncio.run(s.run_once())
    assert opened == ["a"]


def test_scheduler_cooldown_prevents_reopen_until_elapsed():
    opened, clock = [], Clock()
    s = _sched({"a": _detector(True)}, {"a": 200.0}, opened, clock=clock, cooldown=600)
    asyncio.run(s.run_once())
    clock.now += 599
    asyncio.run(s.run_once())
    assert opened == ["a"]
    clock.now += 2
    asyncio.run(s.run_once())
    assert opened == ["a", "a"]


def test_scheduler_failure_in_one_workload_does_not_stop_others(caplog):
    opened = []
    s = _sched(
        {"bad": _detector(True), "good": _detector(True)},
        {"bad": RuntimeError("segredo-token-123"), "good": 200.0},
        opened,
    )
    with caplog.at_level("WARNING"):
        asyncio.run(s.run_once())
    assert opened == ["good"]
    assert "RuntimeError" in caplog.text
    assert "segredo-token-123" not in caplog.text


def test_scheduler_failed_open_does_not_start_cooldown():
    calls = []

    async def read(w):
        return np.array([200.0])

    async def open_inv(w, score):
        calls.append(w)
        if len(calls) == 1:
            raise RuntimeError("falha")

    s = PredictiveScheduler(
        detectors={"a": _detector(True)}, read_point=read, open_investigation=open_inv,
        risk_threshold=0.65, cooldown_seconds=600, clock=Clock(),
    )
    asyncio.run(s.run_once())
    asyncio.run(s.run_once())
    assert calls == ["a", "a"]


def test_scheduler_score_exactly_at_threshold_does_not_open():
    opened = []
    s = _sched({"a": _detector(True)}, {"a": 200.0}, opened, threshold=1.0)
    asyncio.run(s.run_once())
    assert opened == []


def test_settings_defaults():
    st = Settings()
    assert st.risk_threshold == 0.65
    assert st.min_baseline_points == 500


# ---------------- backfill ----------------


def _matrix(values):
    return {"status": "success", "data": {"resultType": "matrix",
            "result": [{"metric": {}, "values": [[i * 60, str(v)] for i, v in enumerate(values)]}]}}


def test_query_range_returns_numeric_array_read_only():
    seen = {}

    def handler(request):
        seen["method"], seen["path"] = request.method, request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=_matrix([1, 2, "NaN", 4]))

    tool = PrometheusTool("http://prom:9090", transport=httpx.MockTransport(handler))
    arr = asyncio.run(tool.query_range("up", start=0, end=180, step=60))
    assert seen["method"] == "GET" and seen["path"] == "/api/v1/query_range"
    assert seen["params"]["query"] == "up" and seen["params"]["step"] == "60"
    assert arr.shape == (4,) and arr.dtype == np.float64
    assert np.isnan(arr[2]) and arr[3] == 4.0


def test_query_range_empty_result_is_empty_array():
    tool = PrometheusTool("http://p", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"status": "success", "data": {"result": []}})))
    assert asyncio.run(tool.query_range("up", 0, 60, 60)).size == 0


def test_query_range_rejects_too_many_points():
    tool = PrometheusTool("http://p", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ValueError):
        asyncio.run(tool.query_range("up", 0, 1_000_000, 1))


def test_backfill_from_prometheus_history_shortens_learning():
    def handler(request):
        return httpx.Response(200, json=_matrix(list(_normal(500))))

    tool = PrometheusTool("http://prom:9090", transport=httpx.MockTransport(handler))
    det = WorkloadDetector(min_baseline_points=500)
    assert det.state == "aprendendo"
    n = asyncio.run(backfill_detector(det, tool, "up", start=0, end=500 * 60, step=60))
    assert n == 500
    assert det.state == "ativo"


def test_backfill_prometheus_failure_leaves_detector_learning():
    def handler(request):
        return httpx.Response(500, json={})

    tool = PrometheusTool("http://prom:9090", transport=httpx.MockTransport(handler))
    det = WorkloadDetector(min_baseline_points=10)
    n = asyncio.run(backfill_detector(det, tool, "up", start=0, end=600, step=60))
    assert n == 0 and det.state == "aprendendo"


# ---------------- harness ----------------


def test_new_predictive_scenarios_are_declared_and_neutral():
    by_id = {s.id: s for s in SCENARIOS}
    for sid in ("memory-leak-gradual", "latency-creep"):
        s = by_id[sid]
        assert s.namespace.startswith("eval-")
        assert s.expected_evidence_refs
    assert len({s.namespace for s in SCENARIOS}) == len(SCENARIOS)


def test_new_scenarios_have_manifests_in_their_namespace():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "harness" / "manifests"
    for s in SCENARIOS:
        text = (root / f"{s.id}.yaml").read_text()
        assert f"namespace: {s.namespace}" in text
        assert f"name: {s.namespace}" in text
