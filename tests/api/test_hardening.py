import asyncio
import importlib.resources
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from aisre.agent.loop import Alert
from aisre.api import hooks
from aisre.api.ingest import run_investigation
from aisre.api.main import create_app
from aisre.config import Settings
from aisre.db import MemoryStore
from aisre.memory import Memory
from aisre.schemas import RCA, Evidence


def _rca():
    return RCA(cause="OOM", confidence=0.7, evidence=[Evidence(kind="log", ref="log:a", summary="oom")])


def _alert(**labels):
    labels = {"alertname": "HighErrorRate", **labels}
    return {"status": "firing", "labels": labels, "startsAt": "2026-11-23T10:00:00Z"}


class Fake:
    def __init__(self):
        self.calls = []

    async def __call__(self, alert):
        self.calls.append(alert)
        return _rca()


def _client(store=None, investigator=None, **settings):
    app = create_app(Settings(_env_file=None, **settings), store or MemoryStore(), investigator or Fake())
    return TestClient(app), app


@pytest.fixture(autouse=True)
def _clean_hooks():
    hooks.on_rca_ready.clear()
    yield
    hooks.on_rca_ready.clear()


def _post_raw(client, raw: bytes):
    return client.post("/alerts/webhook", content=raw, headers={"content-type": "application/json"})


# --- Finding 1 ---
@pytest.mark.parametrize("raw", [b"[" * 200000, b'{"a":' * 100000])
def test_deeply_nested_json_returns_422(raw):
    client, _ = _client()
    assert _post_raw(client, raw).status_code == 422


def test_lone_surrogate_label_returns_422():
    client, _ = _client()
    raw = json.dumps({"alerts": [_alert(service="\ud800")]}).encode()
    assert _post_raw(client, raw).status_code == 422


def test_nul_label_and_alertname_return_422():
    client, _ = _client()
    for labels in ({"service": "a\u0000b"}, {"alertname": "x\u0000"}):
        a = _alert()
        a["labels"].update(labels)
        assert client.post("/alerts/webhook", json={"alerts": [a]}).status_code == 422


def test_oversized_body_is_rejected():
    client, _ = _client(max_webhook_body_bytes=200)
    r = client.post("/alerts/webhook", json={"alerts": [_alert(pad="x" * 500)]})
    assert r.status_code == 413


def test_too_many_alerts_returns_422():
    client, _ = _client(max_alerts_per_request=2)
    r = client.post("/alerts/webhook", json={"alerts": [_alert(i=str(i)) for i in range(3)]})
    assert r.status_code == 422


# --- Finding 2 ---
class FlakyStore(MemoryStore):
    async def create_if_new(self, alert, window_seconds):
        if alert.labels.get("bad"):
            raise RuntimeError("db down")
        return await super().create_if_new(alert, window_seconds)


def test_store_error_on_one_alert_does_not_poison_others():
    fake = Fake()
    client, _ = _client(FlakyStore(), fake)
    r = client.post("/alerts/webhook", json={"alerts": [_alert(i="1"), _alert(bad="1"), _alert(i="2")]})
    assert r.status_code == 503
    assert len(fake.calls) == 2
    assert all(i["status"] == "done" for i in client.get("/incidents").json())


def test_cancellation_marks_incident_failed_and_propagates():
    async def run():
        store = MemoryStore()
        alert = Alert(name="A", labels={}, fired_at=datetime.now(UTC))
        inc, _ = await store.create_if_new(alert, 300)

        async def forever(a):
            await asyncio.sleep(3600)

        task = asyncio.create_task(run_investigation(inc.id, alert, store, forever, Memory(store), 300))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return await store.get(inc.id)

    assert asyncio.run(run()).status == "failed"


def test_fail_stale_marks_only_old_investigating():
    async def run():
        now = [datetime(2026, 11, 23, 10, tzinfo=UTC)]
        store = MemoryStore(clock=lambda: now[0])
        old, _ = await store.create_if_new(Alert(name="old", labels={}, fired_at=now[0]), 300)
        now[0] += timedelta(seconds=600)
        new, _ = await store.create_if_new(Alert(name="new", labels={}, fired_at=now[0]), 300)
        n = await store.fail_stale(300, "interrompida")
        return n, await store.get(old.id), await store.get(new.id)

    n, old, new = asyncio.run(run())
    assert n == 1 and old.status == "failed" and new.status == "investigating"


# --- Finding 3 ---
def test_investigations_respect_concurrency_limit():
    async def run():
        store = MemoryStore()
        running = peak = 0

        async def slow(a):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.02)
            running -= 1
            return _rca()

        sem = asyncio.Semaphore(2)
        jobs = []
        for i in range(8):
            alert = Alert(name="A", labels={"i": str(i)}, fired_at=datetime.now(UTC))
            inc, _ = await store.create_if_new(alert, 300)
            jobs.append(run_investigation(inc.id, alert, store, slow, Memory(store), 300, semaphore=sem))
        await asyncio.gather(*jobs)
        return peak

    assert asyncio.run(run()) == 2


def test_app_wires_concurrency_setting():
    _, app = _client(max_concurrent_investigations=2)
    assert app.state.investigation_semaphore._value == 2


# --- Finding 4 ---
class LifecycleStore(MemoryStore):
    def __init__(self):
        super().__init__()
        self.events = []

    async def open(self):
        self.events.append("open")

    async def migrate(self):
        self.events.append("migrate")

    async def fail_stale(self, older_than_seconds, error):
        self.events.append("sweep")
        return 0

    async def close(self):
        self.events.append("close")


def test_lifespan_opens_migrates_sweeps_and_closes():
    store = LifecycleStore()
    client, _ = _client(store)
    with client:
        assert store.events == ["open", "migrate", "sweep"]
    assert store.events[-1] == "close"


def test_migration_is_packaged_inside_the_package():
    sql = importlib.resources.files("aisre").joinpath("migrations/0001_init.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS memories" in sql
    from aisre import db_postgres

    assert db_postgres.read_migration() == sql


# --- Finding 5 ---
class BrokenStore(MemoryStore):
    async def complete(self, incident_id, rca):
        raise RuntimeError("segredo-db")

    async def fail(self, incident_id, error):
        raise RuntimeError("segredo-db")


def test_run_investigation_never_raises_when_store_fails(caplog):
    async def run():
        store = BrokenStore()
        alert = Alert(name="A", labels={}, fired_at=datetime.now(UTC))
        inc, _ = await store.create_if_new(alert, 300)

        async def ok(a):
            return _rca()

        async def boom(a):
            raise RuntimeError("x")

        await run_investigation(inc.id, alert, store, ok, Memory(store), 300)
        await run_investigation(inc.id, alert, store, boom, Memory(store), 300)

    asyncio.run(run())
    assert "segredo-db" not in caplog.text
