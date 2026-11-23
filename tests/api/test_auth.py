import logging
import subprocess
from datetime import UTC, datetime

import pytest
from authhelp import PASSWORD, SESSION_SECRET, USER, auth_kwargs, login
from fastapi.testclient import TestClient

from aisre.agent.loop import Alert
from aisre.api.main import create_app
from aisre.auth import LoginThrottle, hash_password, read_session, sign_session, verify_password
from aisre.config import Settings
from aisre.db import MemoryStore
from aisre.schemas import RCA, Evidence

CMD = "kubectl rollout restart deploy/api -n prod"


def _rca(**kw):
    base = dict(cause="OOM", confidence=0.8, evidence=[Evidence(kind="log", ref="log:a", summary="oom")],
                suggested_command=CMD)
    base.update(kw)
    return RCA(**base)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _app(rca="default", clock=None, **kw):
    import asyncio

    store = MemoryStore()
    inc, _ = asyncio.run(store.create_if_new(Alert(name="A", labels={}, fired_at=datetime.now(UTC)), 300))
    if rca is not None:
        asyncio.run(store.complete(inc.id, _rca() if rca == "default" else rca))
    settings = Settings(_env_file=None, **auth_kwargs(**kw))
    app = create_app(settings, store, clock=clock)
    return TestClient(app), store, inc


# --- 401 sem sessão ---
def test_unauthenticated_requests_get_401():
    client, _, inc = _app()
    for method, path in [
        ("get", "/incidents"), ("get", f"/incidents/{inc.id}"), ("get", "/risk"),
        ("get", "/scoreboard"), ("get", "/integrations"), ("get", "/auth/me"),
        ("post", f"/incidents/{inc.id}/approve"),
    ]:
        r = getattr(client, method)(path)
        assert r.status_code == 401, path


def test_machine_endpoints_do_not_need_a_session():
    client, _, _ = _app(slack_bot_token="x", slack_signing_secret="s", slack_channel="C")
    assert client.get("/healthz").status_code == 200
    alert = {"status": "firing", "labels": {"alertname": "X"}, "startsAt": "2026-11-23T10:00:00Z"}
    assert client.post("/alerts/webhook", json={"alerts": [alert]}).status_code == 202
    r = client.post("/slack/actions", content=b"payload=%7B%7D")
    assert r.status_code == 401 and "Slack" in r.json()["detail"]  # recusado pela assinatura, não pela sessão


# --- login ---
def test_login_sets_hardened_cookie_and_grants_access():
    client, _, _ = _app()
    r = login(client)
    assert r.status_code == 200 and r.json() == {"user": USER}
    assert client.get("/incidents").status_code == 200
    assert client.get("/auth/me").json() == {"user": USER}


def test_cookie_flags_httponly_samesite_and_secure_by_default():
    settings = Settings(_env_file=None, **{k: v for k, v in auth_kwargs().items() if k != "session_cookie_secure"})
    assert settings.session_cookie_secure is True
    client = TestClient(create_app(settings, MemoryStore()), base_url="https://testserver")
    cookie = login(client).headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" in cookie
    assert PASSWORD not in cookie


def test_cookie_secure_can_be_disabled_for_local_dev():
    client, _, _ = _app()
    assert "secure" not in login(client).headers["set-cookie"].lower()


@pytest.mark.parametrize("user,password", [("admin", "errada"), ("outro", PASSWORD), ("admin", ""), ("", "")])
def test_wrong_credentials_are_401(user, password):
    client, _, _ = _app()
    r = client.post("/auth/login", json={"username": user, "password": password})
    assert r.status_code == 401 and "set-cookie" not in r.headers
    assert client.get("/incidents").status_code == 401


@pytest.mark.parametrize("missing", ["admin_password_hash", "session_secret"])
def test_login_fails_closed_without_configured_secrets(missing):
    client, _, _ = _app(**{missing: ""})
    r = login(client)
    assert r.status_code == 503 and "não configurad" in r.json()["detail"].lower()
    assert client.get("/incidents").status_code == 401


def test_no_default_credentials():
    settings = Settings(_env_file=None)
    assert settings.admin_password_hash.get_secret_value() == ""
    assert settings.session_secret.get_secret_value() == ""
    client = TestClient(create_app(settings, MemoryStore()))
    for pw in ("", "admin", "password", "changeme"):
        assert client.post("/auth/login", json={"username": "admin", "password": pw}).status_code == 503


def test_tampered_or_forged_cookie_is_rejected():
    client, _, _ = _app()
    login(client)
    token = client.cookies.get("aisre_session")
    payload, sig = token.split(".")
    for bad in (f"{payload}.{'0' * len(sig)}", f"{payload}x.{sig}", "lixo", f"{payload}."):
        c2 = TestClient(client.app)
        c2.cookies.set("aisre_session", bad)
        assert c2.get("/incidents").status_code == 401
    forged = sign_session("outro-segredo-qualquer-xxxxxxxx", USER, 1_000_000.0, 3600)
    c3 = TestClient(client.app)
    c3.cookies.set("aisre_session", forged)
    assert c3.get("/incidents").status_code == 401


def test_session_expires():
    clock = Clock()
    client, _, _ = _app(clock=clock, session_ttl_seconds=60)
    login(client)
    assert client.get("/incidents").status_code == 200
    clock.t += 61
    assert client.get("/incidents").status_code == 401


def test_logout_clears_cookie():
    client, _, _ = _app()
    login(client)
    r = client.post("/auth/logout")
    assert r.status_code == 200
    assert client.get("/incidents").status_code == 401


def test_login_throttle_locks_out_then_recovers():
    clock = Clock()
    client, _, _ = _app(clock=clock)
    for _ in range(5):
        assert login(client, "errada").status_code == 401
    r = login(client)  # senha correta, mas bloqueado
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    clock.t += 24 * 3600
    assert login(client).status_code == 200


def test_login_never_logs_credentials(caplog):
    caplog.set_level(logging.DEBUG)
    client, _, _ = _app()
    login(client, "senha-errada-xyz")
    login(client)
    assert "senha-errada-xyz" not in caplog.text and PASSWORD not in caplog.text


def test_cross_origin_post_is_refused():
    client, _, inc = _app()
    login(client)
    r = client.post(f"/incidents/{inc.id}/approve", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.get(f"/incidents/{inc.id}").json()["approvals"] == []


# --- helpers puros ---
def test_hash_format_and_verify():
    h = hash_password("segredo", n=2**4, r=8, p=1)
    kind, n, r, p, salt, digest = h.split("$")
    assert (kind, n, r, p) == ("scrypt", "16", "8", "1") and len(salt) == 32 and len(digest) == 64
    assert verify_password("segredo", h) and not verify_password("outro", h)
    assert hash_password("segredo", n=2**4) != h  # sal aleatório


@pytest.mark.parametrize("bad", ["", "x", "scrypt$a$b$c$d$e", "bcrypt$16$8$1$00$00", "scrypt$16$8$1$zz$zz",
                                 "scrypt$3$8$1$00$00", f"scrypt${2**30}$8$1$00$00"])
def test_verify_malformed_hash_is_false(bad):
    assert verify_password("x", bad) is False


def test_session_token_roundtrip_and_limits():
    tok = sign_session(SESSION_SECRET, "admin", 100.0, 50)
    assert read_session(SESSION_SECRET, tok, 120.0) == "admin"
    assert read_session(SESSION_SECRET, tok, 151.0) is None
    assert read_session("", tok, 120.0) is None
    assert read_session(SESSION_SECRET, "a" * 10_000, 120.0) is None


def test_throttle_unit():
    clock = Clock()
    th = LoginThrottle(max_per_ip=2, max_per_user=3, window_seconds=60, lockout_seconds=120, clock=clock)
    assert th.retry_after("1.1.1.1", "a") == 0
    th.failure("1.1.1.1", "a")
    th.failure("1.1.1.1", "a")
    assert th.retry_after("1.1.1.1", "b") > 0  # por IP
    assert th.retry_after("2.2.2.2", "b") == 0
    th.failure("2.2.2.2", "a")
    th.failure("3.3.3.3", "a")
    assert th.retry_after("4.4.4.4", "a") > 0  # por usuário
    clock.t += 121
    assert th.retry_after("1.1.1.1", "a") == 0


# --- aprovação pelo dashboard ---
def test_approve_records_command_from_stored_rca_ignoring_body():
    client, store, inc = _app()
    login(client)
    r = client.post(f"/incidents/{inc.id}/approve", json={"command": "rm -rf /"})
    assert r.status_code == 200 and r.json()["ok"] is True
    detail = client.get(f"/incidents/{inc.id}").json()
    assert detail["approvals"] == [{"user_id": f"web:{USER}", "command": CMD}]
    assert detail["approvable"] is True


def test_approve_is_idempotent():
    client, _, inc = _app()
    login(client)
    client.post(f"/incidents/{inc.id}/approve")
    client.post(f"/incidents/{inc.id}/approve")
    assert len(client.get(f"/incidents/{inc.id}").json()["approvals"]) == 1


@pytest.mark.parametrize("rca", [
    None,
    RCA(unknown=True, confidence=0.1, evidence=[Evidence(kind="metric", ref="m:1", summary="x")]),
    RCA(cause="OOM", confidence=0.8, evidence=[Evidence(kind="log", ref="l", summary="x")], suggested_command=None),
    RCA(cause="OOM", confidence=0.8, evidence=[Evidence(kind="log", ref="l", summary="x")],
        suggested_command="echo `id`"),
    RCA(cause="OOM", confidence=0.8, evidence=[Evidence(kind="log", ref="l", summary="x")],
        suggested_command="echo " + "a" * 3000),
])
def test_approve_refused_for_unknown_missing_or_unshowable_command(rca):
    client, _, inc = _app(rca=rca)
    login(client)
    assert client.post(f"/incidents/{inc.id}/approve").status_code == 409
    detail = client.get(f"/incidents/{inc.id}").json()
    assert detail["approvals"] == [] and detail["approvable"] is False


def test_approve_unknown_incident_404():
    client, _, _ = _app()
    login(client)
    assert client.post("/incidents/999/approve").status_code == 404


def test_approve_executes_nothing(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("não deve executar nada")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr("asyncio.create_subprocess_exec", boom)
    monkeypatch.setattr("asyncio.create_subprocess_shell", boom)
    client, _, inc = _app()
    login(client)
    assert client.post(f"/incidents/{inc.id}/approve").status_code == 200


def test_only_configured_admin_can_approve():
    # Cookie assinado para outro usuário (mesmo com o segredo certo) não vale.
    client, _, inc = _app()
    client.cookies.set("aisre_session", sign_session(SESSION_SECRET, "intruso", 1.0e12, 3600))
    assert client.post(f"/incidents/{inc.id}/approve").status_code == 401


def test_healthz():
    client, _, _ = _app()
    assert client.get("/healthz").json() == {"status": "ok"}
