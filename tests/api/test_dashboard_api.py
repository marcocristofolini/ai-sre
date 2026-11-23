import asyncio
import json
import time

import httpx
import numpy as np
import pytest
from authhelp import auth_kwargs, login
from fastapi.testclient import TestClient

from aisre.api.main import create_app
from aisre.config import Settings
from aisre.db import MemoryStore
from aisre.harness.scoring import GradedResult, scoreboard
from aisre.schemas import RCA, Evidence
from aisre.tools.prometheus import PrometheusTool

WORKLOADS = [
    {"name": "prod/api", "query": 'sum(rate(container_cpu_usage_seconds_total{namespace="prod"}[5m]))'},
]


def _client(investigator=None, transport=None, store=None, **kw):
    settings = Settings(_env_file=None, **auth_kwargs(**kw))
    app = create_app(settings, store or MemoryStore(), investigator, prometheus_transport=transport)
    client = TestClient(app)
    login(client)
    return client, app


# --- /scoreboard ---
def test_scoreboard_without_file_says_not_measured_and_invents_nothing(tmp_path):
    client, _ = _client(scoreboard_path=str(tmp_path / "nao-existe.json"))
    r = client.get("/scoreboard")
    assert r.status_code == 200 and r.json() == {"measured": False, "reason": "missing"}


def test_scoreboard_reads_file_with_model_label_and_calibration(tmp_path):
    board = scoreboard(
        [GradedResult(scenario_id="a", correct=True, confidence=0.9),
         GradedResult(scenario_id="b", correct=False, confidence=0.2)],
        "anthropic:claude-x",
    )
    path = tmp_path / "scoreboard.json"
    path.write_text(json.dumps(board.model_dump()))
    client, _ = _client(scoreboard_path=str(path))
    body = client.get("/scoreboard").json()
    assert body["measured"] is True and body["correct"] == 1 and body["total"] == 2
    assert body["model"] == "anthropic:claude-x" and body["label"] == "cenários sintéticos"
    assert body["calibration"] == [{"confidence": 0.2, "hit_rate": 0.0}, {"confidence": 0.9, "hit_rate": 1.0}]


@pytest.mark.parametrize("content", ["{", "[]", '{"correct": 5, "total": 2, "model": "m"}', '{"x": 1}'])
def test_scoreboard_invalid_file_is_not_measured(tmp_path, content):
    path = tmp_path / "s.json"
    path.write_text(content)
    client, _ = _client(scoreboard_path=str(path))
    assert client.get("/scoreboard").json() == {"measured": False, "reason": "invalid"}


def test_old_scoreboard_file_without_calibration_still_loads(tmp_path):
    path = tmp_path / "s.json"
    path.write_text('{"correct": 1, "total": 2, "model": "m", "injection_errors": 0, "label": "cenários sintéticos"}')
    client, _ = _client(scoreboard_path=str(path))
    assert client.get("/scoreboard").json()["calibration"] == []


def test_scoreboard_with_unmeasurable_scenarios_serves_provenance(tmp_path):
    board = scoreboard(
        [GradedResult(scenario_id="a", correct=True, confidence=0.9),
         GradedResult(scenario_id="b", correct=False, confidence=0.0, measurable=False, reason="não medível")],
        "m", timestamp="2026-11-23T00:00:00+00:00", git_commit="abc", enabled_tools=["kubernetes"],
    )
    path = tmp_path / "s.json"
    path.write_text(json.dumps(board.model_dump()))
    client, _ = _client(scoreboard_path=str(path))
    body = client.get("/scoreboard").json()
    assert (body["correct"], body["total"], body["not_measurable"]) == (1, 1, 1)
    assert body["git_commit"] == "abc" and body["enabled_tools"] == ["kubernetes"]
    assert [r["measurable"] for r in body["scenarios"]] == [True, False]


# --- /integrations ---
def test_integrations_status_never_exposes_secrets():
    client, _ = _client(
        llm_provider="anthropic", llm_api_key="sk-segredo", slack_bot_token="xoxb-segredo",
        slack_signing_secret="sign-segredo", slack_channel="C1", github_token="ghp_segredo",
        github_repo="org/repo", prometheus_url="http://user:pw-segredo@prom:9090",
        database_url="postgresql://u:db-segredo@h/db",
    )
    r = client.get("/integrations")
    for secret in ("sk-segredo", "xoxb-segredo", "sign-segredo", "ghp_segredo", "pw-segredo", "db-segredo", "prom:9090"):
        assert secret not in r.text
    by_id = {i["id"]: i for i in r.json()}
    assert by_id["slack"]["configured"] and by_id["github"]["configured"] and not by_id["loki"]["configured"]


# --- /risk e preditivo ---
def test_risk_is_empty_without_configuration():
    client, app = _client()
    assert client.get("/risk").json() == []
    assert app.state.predictive is None


def test_scheduler_not_started_without_prometheus_or_workloads():
    for kw in ({"predict_workloads": WORKLOADS}, {"prometheus_url": "http://prom:9090"}):
        client, app = _client(**kw)
        assert app.state.predictive is None and client.get("/risk").json() == []


def _prom_handler(point: float, calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        calls.append(request.url.path)
        if request.url.path == "/api/v1/query_range":
            vals = [[i, str(10.0 + ((i * 37) % 100) / 50)] for i in range(300)]
            return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix",
                                  "result": [{"metric": {}, "values": vals}]}})
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector",
                              "result": [{"metric": {}, "value": [1, str(point)]}]}})

    return handler


def _wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _predict_settings(**kw):
    return dict(prometheus_url="http://prom:9090", predict_workloads=WORKLOADS, min_baseline_points=20,
                predict_interval_seconds=0.02, risk_threshold=0.6, **kw)


def test_predictive_backfills_scores_and_opens_background_investigation():
    calls: list = []
    started = []
    release = []

    async def investigator(alert):
        started.append(alert)
        while not release:  # só termina quando o teste libera: prova que não roda inline
            await asyncio.sleep(0.01)
        return RCA(cause=None, confidence=0.1, unknown=True,
                   evidence=[Evidence(kind="metric", ref="m:1", summary="x")])

    transport = httpx.MockTransport(_prom_handler(500.0, calls))  # ponto muito fora da linha de base
    settings = Settings(_env_file=None, **auth_kwargs(**_predict_settings()))
    app = create_app(settings, MemoryStore(), investigator, prometheus_transport=transport)
    with TestClient(app) as client:
        login(client)
        assert _wait(lambda: client.get("/risk").json()[0]["state"] == "ativo")
        assert _wait(lambda: len(started) == 1)
        row = client.get("/risk").json()[0]
        assert row["workload"] == "prod/api" and row["threshold"] == 0.6 and row["score"] > 0.6
        assert "/api/v1/query_range" in calls and "/api/v1/query" in calls
        incidents = client.get("/incidents").json()
        assert len(incidents) == 1 and incidents[0]["alert_name"] == "RiscoPreditivo"
        assert incidents[0]["status"] == "investigating"  # enfileirada, ainda em curso
        assert incidents[0]["labels"]["workload"] == "prod/api"
        release.append(1)
        assert _wait(lambda: client.get("/incidents").json()[0]["status"] == "done")
        time.sleep(0.1)  # cooldown: nenhuma segunda investigação
        assert len(started) == 1


def test_predictive_normal_point_opens_nothing_and_shows_low_score():
    calls: list = []
    transport = httpx.MockTransport(_prom_handler(11.0, calls))
    settings = Settings(_env_file=None, **auth_kwargs(**_predict_settings()))
    app = create_app(settings, MemoryStore(), prometheus_transport=transport)
    with TestClient(app) as client:
        login(client)
        assert _wait(lambda: client.get("/risk").json()[0]["state"] == "ativo")
        assert _wait(lambda: client.get("/risk").json()[0]["score"] > 0)
        assert client.get("/risk").json()[0]["score"] <= 0.6
        assert client.get("/incidents").json() == []


def test_predictive_recovers_after_prometheus_outage_at_startup():
    inner = _prom_handler(11.0, [])
    state = {"down": True, "failed": 0}

    def handler(request):
        if state["down"]:
            state["failed"] += 1
            raise httpx.ConnectError("sem rede")
        return inner(request)

    settings = Settings(_env_file=None, **auth_kwargs(**_predict_settings(
        predict_retry_initial_seconds=0.02, predict_retry_max_seconds=0.05)))
    app = create_app(settings, MemoryStore(), prometheus_transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        login(client)
        assert _wait(lambda: state["failed"] >= 2)  # tentou de novo, com backoff
        assert client.get("/risk").json() == [
            {"workload": "prod/api", "state": "aprendendo", "score": 0.0, "threshold": 0.6}
        ]
        state["down"] = False  # Prometheus volta
        assert _wait(lambda: client.get("/risk").json()[0]["state"] == "ativo")
        assert _wait(lambda: client.get("/risk").json()[0]["score"] > 0)


def test_retry_backoff_is_bounded_and_not_a_storm():
    import logging

    class Clock:
        t = 0.0

        def __call__(self):
            return self.t

    calls = []

    def handler(request):
        calls.append(request.url.path)
        raise httpx.ConnectError("x")

    settings = Settings(_env_file=None, **auth_kwargs(**_predict_settings(
        predict_retry_initial_seconds=10, predict_retry_max_seconds=40)))
    from aisre.predict.wiring import Predictive

    clock = Clock()
    p = Predictive(settings, PrometheusTool("http://prom:9090", transport=httpx.MockTransport(handler)),
                   lambda w, s: None, clock=clock)

    async def run():
        await p.retry_learning()  # t=0: tenta, próxima em 10
        await p.retry_learning()  # sem avançar o relógio: não tenta
        assert len(calls) == 1
        for expected_wait in (10, 20, 40, 40):
            clock.t += expected_wait - 0.1
            await p.retry_learning()
            n = len(calls)
            clock.t += 0.1
            await p.retry_learning()
            assert len(calls) == n + 1
            assert p._next_try["prod/api"] - clock.t == expected_wait * (2 if expected_wait < 40 else 1)

    asyncio.run(run())


def test_lifespan_cancels_predictive_task_cleanly():
    transport = httpx.MockTransport(_prom_handler(11.0, []))
    settings = Settings(_env_file=None, **auth_kwargs(**_predict_settings()))
    app = create_app(settings, MemoryStore(), prometheus_transport=transport)
    with TestClient(app) as client:
        login(client)
        assert _wait(lambda: client.get("/risk").json()[0]["state"] == "ativo")
    # saiu do lifespan sem erro nem aviso (pytest -W error); nada fica pendente
    assert app.state.predictive is not None


def test_settings_reject_duplicate_or_malformed_workloads():
    with pytest.raises(ValueError):
        Settings(_env_file=None, predict_workloads=[WORKLOADS[0], WORKLOADS[0]])
    with pytest.raises(ValueError):
        Settings(_env_file=None, predict_workloads=[{"name": "semBarra", "query": "up"}])


def test_predict_workloads_from_env_json(monkeypatch):
    monkeypatch.setenv("AISRE_PREDICT_WORKLOADS", json.dumps(WORKLOADS))
    assert Settings(_env_file=None).predict_workloads[0].name == "prod/api"


# --- PrometheusTool.query_instant ---
def _tool(handler):
    return PrometheusTool("http://prom:9090", transport=httpx.MockTransport(handler))


def _vec(result):
    return lambda request: httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": result}})


def test_query_instant_single_series():
    got = asyncio.run(_tool(_vec([{"metric": {}, "value": [1, "3.5"]}])).query_instant("up"))
    assert got.tolist() == [3.5]


def test_query_instant_empty_and_errors():
    async def run():
        assert (await _tool(_vec([])).query_instant("up")).size == 0
        two = [{"metric": {}, "value": [1, "1"]}, {"metric": {}, "value": [1, "2"]}]
        with pytest.raises(ValueError):
            await _tool(_vec(two)).query_instant("up")
        with pytest.raises(httpx.HTTPError):
            await _tool(lambda r: httpx.Response(500)).query_instant("up")
        nan = await _tool(_vec([{"metric": {}, "value": [1, "NaN"]}])).query_instant("up")
        assert np.isnan(nan[0])

    asyncio.run(run())


def test_query_instant_is_get_only():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"data": {"resultType": "vector", "result": []}})

    asyncio.run(_tool(handler).query_instant("up"))
    assert seen == [("GET", "/api/v1/query")]


# --- estáticos ---
def test_static_dist_is_served_and_api_unaffected(tmp_path):
    (tmp_path / "index.html").write_text("<html>aisre-ui</html>")
    client, _ = _client(web_dist_dir=str(tmp_path))
    assert "aisre-ui" in client.get("/").text
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/risk").json() == []
    anon = TestClient(client.app)
    assert anon.get("/incidents").status_code == 401


def test_no_static_mount_without_dist(tmp_path):
    client, _ = _client(web_dist_dir=str(tmp_path / "nada"))
    assert client.get("/").status_code == 404
