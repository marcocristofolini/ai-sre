"""C2 (token do webhook), I3 (investigações em paralelo, fila limitada, cancelamento)."""

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from aisre.api import hooks
from aisre.api.main import create_app
from aisre.config import Settings
from aisre.db import MemoryStore
from aisre.schemas import RCA, Evidence
from aisre import strings_pt as t

TOKEN = "um-token-longo-de-teste"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _rca():
    return RCA(cause="OOM", confidence=0.7, evidence=[Evidence(kind="log", ref="log:a", summary="oom")])


def _alert(**labels):
    return {"status": "firing", "labels": {"alertname": "X", **labels}, "startsAt": "2026-11-23T10:00:00Z"}


class Fake:
    def __init__(self):
        self.calls = []

    async def __call__(self, alert):
        self.calls.append(alert)
        return _rca()


@pytest.fixture(autouse=True)
def _clean_hooks():
    hooks.on_rca_ready.clear()
    yield
    hooks.on_rca_ready.clear()


def _app(investigator=None, store=None, **kw):
    settings = Settings(_env_file=None, **kw)
    return create_app(settings, store or MemoryStore(), investigator or Fake())


# ------------------------------------------------------------------ C2


def test_webhook_without_configured_token_fails_closed_503():
    fake = Fake()
    client = TestClient(_app(fake))
    r = client.post("/alerts/webhook", json={"alerts": [_alert()]})
    assert r.status_code == 503
    assert r.json()["detail"] == t.ERRO_WEBHOOK_NAO_CONFIGURADO
    assert "AISRE_WEBHOOK_TOKEN" in r.json()["detail"]
    assert fake.calls == []


def test_webhook_explicit_allow_unauthenticated_accepts_without_token(caplog):
    fake = Fake()
    with caplog.at_level("WARNING"):
        client = TestClient(_app(fake, webhook_allow_unauthenticated=True))
    assert "AISRE_WEBHOOK_ALLOW_UNAUTHENTICATED" in caplog.text
    assert client.post("/alerts/webhook", json={"alerts": [_alert()]}).status_code == 202
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer errado"},
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": TOKEN},
        {"Authorization": f"Bearer {TOKEN}x"},
        {"Authorization": "Bearer "},
    ],
)
def test_webhook_rejects_missing_or_wrong_token_401(headers):
    fake = Fake()
    client = TestClient(_app(fake, webhook_token=SecretStr(TOKEN)))
    r = client.post("/alerts/webhook", json={"alerts": [_alert()]}, headers=headers)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert TOKEN not in r.text
    assert fake.calls == []


def test_token_is_required_even_when_allow_unauthenticated_is_set():
    client = TestClient(_app(webhook_token=SecretStr(TOKEN), webhook_allow_unauthenticated=True))
    assert client.post("/alerts/webhook", json={"alerts": [_alert()]}).status_code == 401
    assert client.post("/alerts/webhook", json={"alerts": [_alert()]}, headers=AUTH).status_code == 202


def test_webhook_accepts_correct_bearer_token():
    fake = Fake()
    client = TestClient(_app(fake, webhook_token=SecretStr(TOKEN)))
    r = client.post("/alerts/webhook", json={"alerts": [_alert()]}, headers={"Authorization": f"bearer {TOKEN}"})
    assert r.status_code == 202 and len(fake.calls) == 1


def test_token_is_checked_before_reading_the_body():
    client = TestClient(_app(webhook_token=SecretStr(TOKEN), max_webhook_body_bytes=10))
    r = client.post("/alerts/webhook", content=b"x" * 1000, headers={"content-type": "application/json"})
    assert r.status_code == 401


def test_webhook_uses_constant_time_compare():
    import inspect

    from aisre.api import routes_alerts

    assert "hmac.compare_digest" in inspect.getsource(routes_alerts._check_token)


# ------------------------------------------------------------------ I3


class Peak:
    """Investigador que espera até `want` investigações estarem ativas ao mesmo tempo."""

    def __init__(self, want, wait=3.0):
        self.want, self.wait = want, wait
        self.active = self.peak = 0

    async def __call__(self, alert):
        self.active += 1
        self.peak = max(self.peak, self.active)
        deadline = time.monotonic() + self.wait
        while self.active < self.want and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.01)
        self.active -= 1
        return _rca()


def test_investigations_run_concurrently_up_to_the_semaphore():
    inv = Peak(want=3)
    client = TestClient(_app(inv, webhook_token=SecretStr(TOKEN), max_concurrent_investigations=3))
    started = time.monotonic()
    r = client.post("/alerts/webhook", json={"alerts": [_alert(i=str(i)) for i in range(3)]}, headers=AUTH)
    assert r.status_code == 202
    assert inv.peak == 3
    assert time.monotonic() - started < 2.5  # sequencial esperaria o prazo de cada uma


def test_concurrency_is_still_bounded_by_max_concurrent():
    inv = Peak(want=3, wait=0.3)
    client = TestClient(_app(inv, webhook_token=SecretStr(TOKEN), max_concurrent_investigations=2))
    client.post("/alerts/webhook", json={"alerts": [_alert(i=str(i)) for i in range(4)]}, headers=AUTH)
    assert inv.peak == 2


class Blocking:
    def __init__(self):
        self.release = threading.Event()
        self.started = 0

    async def __call__(self, alert):
        self.started += 1
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return _rca()


def _wait(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.01)
    return cond()


def test_full_queue_returns_503_and_does_not_create_incident():
    inv = Blocking()
    store = MemoryStore()
    app = _app(inv, store, webhook_token=SecretStr(TOKEN), max_pending_investigations=2)
    with TestClient(app) as client:  # com lifespan: a resposta não espera a investigação
        r = client.post("/alerts/webhook", json={"alerts": [_alert(i="1"), _alert(i="2")]}, headers=AUTH)
        assert r.status_code == 202
        assert _wait(lambda: inv.started == 2)
        r = client.post("/alerts/webhook", json={"alerts": [_alert(i="3")]}, headers=AUTH)
        assert r.status_code == 503 and r.json()["detail"] == t.ERRO_FILA_INVESTIGACOES_CHEIA
        assert len(asyncio.run(store.list())) == 2
        inv.release.set()
        assert _wait(lambda: not app.state.investigation_tasks)
        # Reentrega do Alertmanager depois que a fila esvaziou é aceita.
        assert client.post("/alerts/webhook", json={"alerts": [_alert(i="3")]}, headers=AUTH).status_code == 202


def test_shutdown_cancels_pending_investigations_and_marks_them_failed():
    inv = Blocking()
    store = MemoryStore()
    app = _app(inv, store, webhook_token=SecretStr(TOKEN))
    with TestClient(app) as client:
        client.post("/alerts/webhook", json={"alerts": [_alert()]}, headers=AUTH)
        assert _wait(lambda: inv.started == 1)
    incidents = asyncio.run(store.list())
    assert [i.status for i in incidents] == ["failed"]
    assert incidents[0].error == t.ERRO_INVESTIGACAO_INTERROMPIDA
    assert not app.state.investigation_tasks
