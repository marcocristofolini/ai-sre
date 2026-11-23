import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from aisre.api import hooks
from aisre.api.main import create_app
from aisre.config import Settings
from aisre.db import MemoryStore
from aisre.llm.gate import ConsentRequired
from aisre.schemas import RCA, Evidence


def _rca():
    return RCA(cause="OOM", confidence=0.7, evidence=[Evidence(kind="log", ref="log:a", summary="oom")])


def _payload(*alerts):
    return {"alerts": list(alerts)}


def _alert(status="firing", **labels):
    labels = {"alertname": "HighErrorRate", "service": "api", **labels}
    return {
        "status": status,
        "labels": labels,
        "annotations": {"summary": "x"},
        "startsAt": "2026-11-23T10:00:00Z",
    }


class Fake:
    def __init__(self, result=None, exc=None):
        self.calls = []
        self.result, self.exc = result, exc

    async def __call__(self, alert):
        self.calls.append(alert)
        if self.exc:
            raise self.exc
        return self.result or _rca()


def _client(investigator=None, store=None, **settings):
    app = create_app(Settings(_env_file=None, **settings), store or MemoryStore(), investigator or Fake())
    return TestClient(app), app


@pytest.fixture(autouse=True)
def _clean_hooks():
    hooks.on_rca_ready.clear()
    yield
    hooks.on_rca_ready.clear()


@pytest.mark.parametrize("body", [{}, {"alerts": []}, {"alerts": "x"}, [], {"alerts": [1]},
                                  {"alerts": [{"status": "firing"}]},
                                  {"alerts": [_alert(startsAt=None)]},
                                  {"alerts": [{"status": "firing", "labels": {}, "startsAt": "2026-11-23T10:00:00Z"}]},
                                  {"alerts": [{"status": "firing", "labels": {"alertname": 3}, "startsAt": "2026-11-23T10:00:00Z"}]}])
def test_empty_payload_returns_422_not_500(body):
    client, _ = _client()
    r = client.post("/alerts/webhook", json=body)
    assert r.status_code == 422


def test_no_body_returns_422():
    client, _ = _client()
    assert client.post("/alerts/webhook").status_code == 422


def test_malformed_json_returns_422():
    client, _ = _client()
    r = client.post("/alerts/webhook", content=b'{"alerts": [', headers={"content-type": "application/json"})
    assert r.status_code == 422
    r = client.post("/alerts/webhook", content=b"\xff\xfe", headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_422_does_not_echo_input():
    client, _ = _client()
    bad = _alert()
    bad["labels"]["token"] = 123
    bad["labels"]["secret"] = "s3cr3t-value"
    r = client.post("/alerts/webhook", json=_payload(bad))
    assert r.status_code == 422 and "s3cr3t-value" not in r.text


def test_firing_creates_incident_and_runs_investigation():
    fake = Fake()
    client, _ = _client(fake)
    r = client.post("/alerts/webhook", json=_payload(_alert()))
    assert r.status_code == 202
    assert len(fake.calls) == 1 and fake.calls[0].name == "HighErrorRate"
    inc = client.get("/incidents").json()
    assert len(inc) == 1 and inc[0]["status"] == "done" and inc[0]["rca"]["cause"] == "OOM"
    one = client.get(f"/incidents/{inc[0]['id']}")
    assert one.status_code == 200 and one.json()["rca"]["confidence"] == 0.7


def test_resolved_alerts_are_ignored():
    fake = Fake()
    client, _ = _client(fake)
    r = client.post("/alerts/webhook", json=_payload(_alert(status="resolved")))
    assert r.status_code == 202 and fake.calls == [] and client.get("/incidents").json() == []


def test_unknown_incident_is_404():
    client, _ = _client()
    assert client.get("/incidents/999").status_code == 404


def test_duplicate_alert_within_window_creates_one_incident():
    fake = Fake()
    client, _ = _client(fake)
    for _ in range(5):
        assert client.post("/alerts/webhook", json=_payload(_alert())).status_code == 202
    assert len(client.get("/incidents").json()) == 1 and len(fake.calls) == 1
    # mesmos rótulos em outra ordem: mesma impressão digital
    client.post("/alerts/webhook", json=_payload({**_alert(), "labels": {"service": "api", "alertname": "HighErrorRate"}}))
    assert len(client.get("/incidents").json()) == 1
    # rótulos diferentes: incidente novo
    client.post("/alerts/webhook", json=_payload(_alert(service="db")))
    assert len(client.get("/incidents").json()) == 2


def test_duplicate_within_same_payload_deduplicated():
    fake = Fake()
    client, _ = _client(fake)
    client.post("/alerts/webhook", json=_payload(_alert(), _alert()))
    assert len(fake.calls) == 1


def test_duplicate_after_window_creates_new_incident():
    now = [datetime(2026, 11, 23, 10, tzinfo=UTC)]
    store = MemoryStore(clock=lambda: now[0])
    fake = Fake()
    client, _ = _client(fake, store, dedupe_window_seconds=300)
    client.post("/alerts/webhook", json=_payload(_alert()))
    now[0] += timedelta(seconds=301)
    client.post("/alerts/webhook", json=_payload(_alert()))
    assert len(client.get("/incidents").json()) == 2


def test_failing_investigation_is_recorded_and_service_survives(caplog):
    client, _ = _client(Fake(exc=RuntimeError("segredo-xyz")))
    assert client.post("/alerts/webhook", json=_payload(_alert())).status_code == 202
    inc = client.get("/incidents").json()[0]
    assert inc["status"] == "failed" and inc["rca"] is None
    assert "segredo-xyz" not in inc["error"] and "RuntimeError" in inc["error"]
    assert client.get("/incidents").status_code == 200


def test_consent_required_is_logged_and_recorded(caplog):
    client, _ = _client(Fake(exc=ConsentRequired()))
    with caplog.at_level(logging.ERROR):
        assert client.post("/alerts/webhook", json=_payload(_alert())).status_code == 202
    inc = client.get("/incidents").json()[0]
    assert inc["status"] == "failed" and "AISRE_LLM_EXTERNAL_CONSENT" in inc["error"]
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_never_ending_investigation_times_out():
    import asyncio

    async def forever(alert):
        await asyncio.sleep(3600)

    client, _ = _client(forever, investigation_timeout_seconds=1)
    assert client.post("/alerts/webhook", json=_payload(_alert())).status_code == 202
    assert client.get("/incidents").json()[0]["status"] == "failed"


def test_on_rca_ready_hook_called_and_hook_failure_isolated():
    seen = []

    async def bad(incident_id, rca):
        raise RuntimeError("boom")

    async def good(incident_id, rca):
        seen.append((incident_id, rca.cause))

    hooks.on_rca_ready.extend([bad, good])
    client, _ = _client()
    client.post("/alerts/webhook", json=_payload(_alert()))
    assert seen == [(1, "OOM")]


def test_successful_rca_is_saved_to_memory():
    client, app = _client()
    client.post("/alerts/webhook", json=_payload(_alert()))
    import asyncio

    found = asyncio.run(app.state.memory.similar("OOM", k=1))
    assert found and found[0].incident_id == 1


def test_responses_do_not_expose_secrets():
    client, _ = _client(llm_api_key="sk-super-secret", github_token="ghp_secret")
    client.post("/alerts/webhook", json=_payload(_alert()))
    text = client.get("/incidents").text + client.get("/incidents/1").text
    assert "sk-super-secret" not in text and "ghp_secret" not in text
