import asyncio
import functools
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import urlencode

import httpx
import pytest
from authhelp import login, with_auth
from fastapi.testclient import TestClient

from aisre.agent.loop import Alert
from aisre.api import hooks
from aisre.api.ingest import run_investigation
from aisre.api.main import create_app
from aisre.config import Settings
from aisre.db import MemoryStore
from aisre.memory import Memory
from aisre.schemas import RCA, Evidence
from aisre.slack import post_rca

def arun(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        return asyncio.run(fn(*a, **k))

    return wrapper


SECRET = "s3cr3t-signing"
TOKEN = "xoxb-token-segredo"
CMD = "kubectl rollout restart deploy/api -n prod"


@pytest.fixture(autouse=True)
def _clean_hooks():
    hooks.on_rca_ready.clear()
    yield
    hooks.on_rca_ready.clear()


def _rca(**kw):
    base = dict(
        cause="OOM no api",
        confidence=0.8,
        evidence=[Evidence(kind="log", ref="log:a", summary="oom killed")],
        suggested_command=CMD,
    )
    base.update(kw)
    return RCA(**base)


def _unknown():
    return RCA(unknown=True, confidence=0.1, evidence=[Evidence(kind="metric", ref="m:1", summary="cpu normal")])


def _settings(**kw):
    base = dict(
        slack_bot_token=TOKEN,
        slack_signing_secret=SECRET,
        slack_channel="C123",
        slack_approvers=["U1"],
    )
    base.update(kw)
    return Settings(_env_file=None, **base)


def _sign(body: bytes, ts: str | None = None, secret: str = SECRET):
    ts = ts or str(int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
            "Content-Type": "application/x-www-form-urlencoded"}


def _form(user="U1", value="1", **extra):
    payload = {"type": "block_actions", "user": {"id": user},
               "actions": [{"action_id": "aisre_approve", "value": value}], **extra}
    return urlencode({"payload": json.dumps(payload)}).encode()


async def _seed(store, rca=None):
    inc, _ = await store.create_if_new(Alert(name="A", labels={}, fired_at=__import__("datetime").datetime.now(__import__("datetime").UTC)), 300)
    if rca is not None:
        await store.complete(inc.id, rca)
    return inc


def _setup(rca="default", settings=None, transport=None):
    import asyncio

    store = MemoryStore()
    inc = asyncio.run(_seed(store, _rca() if rca == "default" else rca))
    app = create_app(with_auth(settings or _settings()), store, slack_transport=transport)
    client = TestClient(app)
    login(client)  # só para ler /incidents; /slack/actions segue autenticado pela assinatura
    return client, store, inc


def _post(client, body, headers=None):
    return client.post("/slack/actions", content=body, headers=headers if headers is not None else _sign(body))


# --- aprovação ---
def test_approval_from_listed_user_is_recorded():
    client, store, inc = _setup()
    r = _post(client, _form("U1", str(inc.id)))
    assert r.status_code == 200
    got = client.get(f"/incidents/{inc.id}").json()
    assert got["approvals"] == [{"user_id": "U1", "command": CMD}]


def test_approval_from_unlisted_user_is_rejected_403():
    client, store, inc = _setup()
    r = _post(client, _form("U999", str(inc.id)))
    assert r.status_code == 403
    assert client.get(f"/incidents/{inc.id}").json()["approvals"] == []


def test_invalid_slack_signature_is_rejected():
    client, store, inc = _setup()
    body = _form("U1", str(inc.id))
    h = _sign(body, secret="outro-segredo")
    assert _post(client, body, h).status_code == 401
    assert client.get(f"/incidents/{inc.id}").json()["approvals"] == []


@pytest.mark.parametrize("missing", ["X-Slack-Signature", "X-Slack-Request-Timestamp"])
def test_missing_signature_headers_rejected(missing):
    client, _, inc = _setup()
    body = _form("U1", str(inc.id))
    h = _sign(body)
    del h[missing]
    assert _post(client, body, h).status_code == 401


@pytest.mark.parametrize("delta", [-400, 400])
def test_stale_or_future_timestamp_rejected(delta):
    client, _, inc = _setup()
    body = _form("U1", str(inc.id))
    h = _sign(body, str(int(time.time()) + delta))
    assert _post(client, body, h).status_code == 401


def test_non_numeric_timestamp_rejected():
    client, _, inc = _setup()
    body = _form("U1", str(inc.id))
    assert _post(client, body, _sign(body, "abc")).status_code == 401


def test_signature_covers_raw_body():
    client, _, inc = _setup()
    body = _form("U1", str(inc.id))
    h = _sign(body)
    assert _post(client, body + b"&x=1", h).status_code == 401


def test_slack_not_configured_returns_503_and_app_starts():
    client, _, inc = _setup(settings=Settings(_env_file=None, slack_approvers=["U1"]))
    body = _form("U1", str(inc.id))
    assert _post(client, body).status_code == 503


def test_missing_signing_secret_fails_closed():
    client, _, inc = _setup(settings=_settings(slack_signing_secret=None))
    body = _form("U1", str(inc.id))
    assert _post(client, body).status_code in (401, 403, 503)
    assert client.get(f"/incidents/{inc.id}").json()["approvals"] == []


def test_command_comes_from_stored_rca_not_from_payload():
    client, store, inc = _setup()
    payload = {"type": "block_actions", "user": {"id": "U1"},
               "actions": [{"action_id": "aisre_approve", "value": str(inc.id),
                            "command": "rm -rf /", "suggested_command": "rm -rf /"}],
               "message": {"text": "rm -rf /"}, "command": "rm -rf /"}
    body = urlencode({"payload": json.dumps(payload)}).encode()
    assert _post(client, body).status_code == 200
    assert client.get(f"/incidents/{inc.id}").json()["approvals"][0]["command"] == CMD


def test_approval_is_idempotent_per_user():
    client, _, inc = _setup(settings=_settings(slack_approvers=["U1", "U2"]))
    for _ in range(2):
        assert _post(client, _form("U1", str(inc.id))).status_code == 200
    assert len(client.get(f"/incidents/{inc.id}").json()["approvals"]) == 1
    assert _post(client, _form("U2", str(inc.id))).status_code == 200
    assert len(client.get(f"/incidents/{inc.id}").json()["approvals"]) == 2


def test_approval_without_rca_or_command_is_409():
    client, _, inc = _setup(rca=None)
    assert _post(client, _form("U1", str(inc.id))).status_code == 409
    client, _, inc = _setup(rca=_rca(suggested_command=None))
    assert _post(client, _form("U1", str(inc.id))).status_code == 409
    client, _, inc = _setup(rca=_unknown())
    assert _post(client, _form("U1", str(inc.id))).status_code == 409


def test_approval_unknown_incident_404():
    client, _, _ = _setup()
    assert _post(client, _form("U1", "999")).status_code == 404


@pytest.mark.parametrize("body", [
    b"", b"payload=", b"payload=not-json", b"foo=bar",
    urlencode({"payload": "[]"}).encode(),
    urlencode({"payload": json.dumps({"user": "x"})}).encode(),
    urlencode({"payload": json.dumps({"user": {"id": "U1"}, "actions": []})}).encode(),
    urlencode({"payload": json.dumps({"user": {"id": "U1"}, "actions": [{"action_id": "aisre_approve", "value": "abc"}]})}).encode(),
    urlencode({"payload": json.dumps({"user": {"id": "U1"}, "actions": [{"action_id": "outra", "value": "1"}]})}).encode(),
    b"\xff\xfe=%ff",
])
def test_malformed_payload_is_4xx_not_500(body):
    client, _, _ = _setup()
    r = _post(client, body)
    assert r.status_code in (400, 422)


def test_oversized_body_rejected():
    client, _, _ = _setup()
    body = b"payload=" + b"a" * 200_000
    assert _post(client, body).status_code == 413


def test_approval_does_not_execute_command(monkeypatch):
    import asyncio
    import subprocess

    calls = []
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr("os.system", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(asyncio, "create_subprocess_shell", lambda *a, **k: calls.append(a))
    hits = []

    def handler(request):
        hits.append(request)
        return httpx.Response(200, json={"ok": True})

    client, _, inc = _setup(transport=httpx.MockTransport(handler))
    assert _post(client, _form("U1", str(inc.id))).status_code == 200
    assert calls == []
    assert hits == []  # nenhuma chamada de rede (nem ao cluster) na aprovação


def test_slack_module_has_no_execution_primitives():
    import aisre.api.routes_slack as r
    import aisre.slack as s

    for mod in (r, s):
        src = __import__("pathlib").Path(mod.__file__).read_text(encoding="utf-8")
        for bad in ("subprocess", "os.system", "kubernetes", "create_subprocess"):
            assert bad not in src


# --- publicação ---
def _capture():
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    return sent, httpx.MockTransport(handler)


@arun
async def test_post_rca_sends_blocks_with_approve_button():
    sent, tr = _capture()
    await post_rca("C123", 7, _rca(), token=TOKEN, transport=tr)
    req = sent[0]
    assert req.url.path.endswith("/chat.postMessage")
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    body = json.loads(req.content)
    assert body["channel"] == "C123"
    text = json.dumps(body, ensure_ascii=False)
    assert all(x in text for x in ("Aprovar", "OOM no api", "log:a", "oom killed", CMD, "80%"))
    buttons = [e for b in body["blocks"] if b["type"] == "actions" for e in b["elements"]]
    assert len(buttons) == 1 and buttons[0]["value"] == "7" and buttons[0]["action_id"] == "aisre_approve"


@arun
async def test_post_rca_unknown_has_no_button_and_says_dont_know():
    sent, tr = _capture()
    await post_rca("C123", 7, _unknown(), token=TOKEN, transport=tr)
    body = json.loads(sent[0].content)
    text = json.dumps(body, ensure_ascii=False)
    assert "não sei" in text.lower() and "cpu normal" in text
    assert not [b for b in body["blocks"] if b["type"] == "actions"]
    assert "Aprovar" not in text


@arun
async def test_post_rca_escapes_mrkdwn_injection_and_code_block_breakout():
    sent, tr = _capture()
    evil = "<!channel> <!here> <@U123> <https://evil|click> & ```\n<!everyone>"
    rca = _rca(cause=evil, suggested_command="ls <!channel> &",
               evidence=[Evidence(kind="log", ref="<!here>", summary=evil)])
    await post_rca("C123", 7, rca, token=TOKEN, transport=tr)
    body = json.loads(sent[0].content)
    blob = json.dumps(body, ensure_ascii=False)
    for bad in ("<!channel>", "<!here>", "<@U123>", "<https://evil", "<!everyone>"):
        assert bad not in blob
    assert "&lt;!channel&gt;" in blob and "&amp;" in blob
    # o comando fica num bloco de código que o texto não consegue fechar
    code = [b["text"]["text"] for b in body["blocks"] if b["type"] == "section" and b["text"]["text"].startswith("```")]
    assert code and code[0].count("```") == 2 and "ls &lt;!channel&gt; &amp;" in code[0]


@arun
async def test_post_rca_truncates_to_slack_limits():
    sent, tr = _capture()
    huge = "&" * 50_000
    rca = _rca(cause=huge, suggested_command="ls",
               evidence=[Evidence(kind="log", ref=huge, summary=huge) for _ in range(40)])
    await post_rca("C123", 7, rca, token=TOKEN, transport=tr)
    body = json.loads(sent[0].content)
    assert len(body["blocks"]) <= 50
    for b in body["blocks"]:
        if b["type"] == "section":
            assert len(b["text"]["text"]) <= 3000
    assert len(body["text"]) <= 3000
    evid = [b for b in body["blocks"] if b["type"] == "section" and "log" in b["text"]["text"]]
    assert sum(b["text"]["text"].count("[log]") for b in evid) <= 5
    for b in body["blocks"]:  # entidades nunca cortadas ao meio
        if b["type"] == "section":
            assert "&" not in b["text"]["text"].replace("&amp;", "").replace("&lt;", "").replace("&gt;", "")


@arun
async def test_post_rca_noop_without_token_or_channel():
    sent, tr = _capture()
    await post_rca("C123", 7, _rca(), token=None, transport=tr)
    await post_rca("", 7, _rca(), token=TOKEN, transport=tr)
    assert sent == []


@pytest.mark.parametrize("kind", ["http500", "okfalse", "timeout", "garbage"])
@arun
async def test_post_rca_failure_is_logged_without_secrets_and_not_raised(kind, caplog):
    def handler(request):
        if kind == "http500":
            return httpx.Response(500, text=f"corpo-secreto {TOKEN}")
        if kind == "okfalse":
            return httpx.Response(200, json={"ok": False, "error": "channel_not_found", "x": TOKEN})
        if kind == "timeout":
            raise httpx.ReadTimeout(f"timeout {TOKEN}")
        return httpx.Response(200, text=f"not json {TOKEN}")

    with caplog.at_level(logging.DEBUG):
        await post_rca("C123", 7, _rca(), token=TOKEN, transport=httpx.MockTransport(handler))
    assert caplog.records
    assert TOKEN not in caplog.text and "corpo-secreto" not in caplog.text
    if kind == "okfalse":
        assert "channel_not_found" in caplog.text
    if kind == "timeout":
        assert "ReadTimeout" in caplog.text


def test_app_wires_post_rca_hook_per_app_and_failure_does_not_break_ingestion():
    import asyncio

    sent, tr = _capture()
    store = MemoryStore()
    app = create_app(_settings(), store, slack_transport=tr)
    inc = asyncio.run(_seed(store))
    asyncio.run(run_investigation(inc.id, Alert(name="A", labels={}, fired_at=inc.fired_at), store,
                                  _const(_rca()), Memory(store), 5, extra_hooks=app.state.rca_hooks))
    assert len(sent) == 1
    assert hooks.on_rca_ready == []  # sem registro global

    def boom(request):
        raise httpx.ConnectError("x")

    store2 = MemoryStore()
    app2 = create_app(_settings(), store2, slack_transport=httpx.MockTransport(boom))
    inc2 = asyncio.run(_seed(store2))
    asyncio.run(run_investigation(inc2.id, Alert(name="A", labels={}, fired_at=inc2.fired_at), store2,
                                  _const(_rca()), Memory(store2), 5, extra_hooks=app2.state.rca_hooks))
    assert asyncio.run(store2.get(inc2.id)).status == "done"


def _const(rca):
    async def run(alert):
        return rca

    return run


def test_end_to_end_webhook_posts_to_slack():
    sent, tr = _capture()
    app = create_app(_settings(webhook_token="tok"), MemoryStore(), _const(_rca()), slack_transport=tr)
    client = TestClient(app, headers={"Authorization": "Bearer tok"})
    r = client.post("/alerts/webhook", json={"alerts": [{
        "status": "firing", "labels": {"alertname": "X"}, "annotations": {},
        "startsAt": "2026-11-23T10:00:00Z"}]})
    assert r.status_code in (200, 202)
    assert len(sent) == 1


# --- armazenamento ---
@arun
async def test_memory_store_record_approval_idempotent():
    store = MemoryStore()
    inc = await _seed(store, _rca())
    a = await store.record_approval(inc.id, "U1", CMD)
    b = await store.record_approval(inc.id, "U1", "outro")
    assert a == b and b.command == CMD
    assert len(await store.list_approvals(inc.id)) == 1


def test_migration_0002_packaged_and_applied_after_0001():
    import importlib.resources

    sql = importlib.resources.files("aisre").joinpath("migrations/0002_approvals.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS approvals" in sql and "UNIQUE" in sql.upper()
    from aisre import db_postgres

    names = [n for n, _ in db_postgres.read_migrations()]
    assert names == ["0001_init.sql", "0002_approvals.sql"]


def _buttons(body):
    return [e for b in body["blocks"] if b["type"] == "actions" for e in b["elements"]]


@pytest.mark.parametrize("cmd", ["x" * 3000, "&" * 600, "echo `id`", "echo ```; <!channel>"])
@arun
async def test_command_not_shown_in_full_and_unaltered_gets_no_button(cmd):
    sent, tr = _capture()
    await post_rca("C123", 7, _rca(suggested_command=cmd), token=TOKEN, transport=tr)
    body = json.loads(sent[0].content)
    assert _buttons(body) == []
    assert "Aprovar" not in json.dumps(body, ensure_ascii=False)
    assert "OOM no api" in json.dumps(body, ensure_ascii=False)


def test_displayed_command_equals_recorded_command():
    import html

    cmd = "kubectl get pods -n prod -o 'jsonpath={.items[*].metadata.name}' && echo <ok>"
    sent, tr = _capture()
    rca = _rca(suggested_command=cmd)
    asyncio.run(post_rca("C123", 7, rca, token=TOKEN, transport=tr))
    body = json.loads(sent[0].content)
    assert len(_buttons(body)) == 1
    code = [b["text"]["text"] for b in body["blocks"] if b["type"] == "section" and b["text"]["text"].startswith("```")][0]
    assert html.unescape(code[3:-3]) == cmd
    client, _, inc = _setup(rca=rca)
    assert _post(client, _form("U1", str(inc.id))).status_code == 200
    assert client.get(f"/incidents/{inc.id}").json()["approvals"][0]["command"] == cmd


# --- I5: caracteres invisíveis, aviso de IA, lista de leitura e auto-link ---

from aisre.slack import build_message, command_is_read_only, command_is_showable  # noqa: E402


@pytest.mark.parametrize(
    "cmd",
    [
        "kubectl get pods‮ -n prod",  # RLO (bidi override)
        "kubectl get pods⁦x⁩",  # isolates
        "kubectl get​ pods",  # zero-width space
        "kubectl get pods\x1b[2K",  # escape ANSI
        "kubectl get pods\rrm -rf /",  # retorno de carro esconde o início no terminal
        "kubectl get pods rm",  # separador de linha
        "kubectl get pods﻿",  # BOM
    ],
)
def test_command_with_invisible_or_control_chars_is_not_showable(cmd):
    assert command_is_showable(cmd) is False
    blob = json.dumps(build_message(7, _rca(suggested_command=cmd)), ensure_ascii=False)
    assert "Aprovar" not in blob


def test_dashboard_approvable_uses_same_rule_for_invisible_chars():
    client, _, inc = _setup(rca=_rca(suggested_command="kubectl get pods‮"))
    detail = client.get(f"/incidents/{inc.id}").json()
    assert detail["approvable"] is False


@pytest.mark.parametrize(
    "cmd,expected",
    [
        ("kubectl get pods -n prod", True),
        ("kubectl describe pod api-1 -n prod", True),
        ("kubectl logs api-1 -n prod --tail=100", True),
        ("kubectl top pod -n prod", True),
        ("kubectl rollout restart deploy/api -n prod", False),
        ("kubectl get pods; kubectl delete ns prod", False),
        ("kubectl get pods && rm -rf /", False),
        ("kubectl get pods | sh", False),
        ("kubectl get pods $(id)", False),
        ("kubectl delete pod x", False),
        ("kubectlget pods", False),
    ],
)
def test_read_only_allow_list(cmd, expected):
    assert command_is_read_only(cmd) is expected


def _all_text(body):
    return json.dumps(body, ensure_ascii=False)


def test_slack_warns_command_is_ai_generated_and_flags_non_read_only():
    body = build_message(7, _rca(suggested_command=CMD))  # rollout restart: altera estado
    blob = _all_text(body)
    assert "gerado por IA a partir de dados não confiáveis" in blob
    assert "revise antes de executar manualmente" in blob
    assert "fora da lista de comandos somente leitura" in blob
    ro = _all_text(build_message(7, _rca(suggested_command="kubectl get pods -n prod")))
    assert "gerado por IA a partir de dados não confiáveis" in ro
    assert "fora da lista de comandos somente leitura" not in ro


def test_slack_mrkdwn_is_verbatim_and_links_do_not_unfurl():
    sent, tr = _capture()
    rca = _rca(cause="veja https://evil.example/login", evidence=[Evidence(kind="log", ref="log:a", summary="www.evil.example")])
    asyncio.run(post_rca("C123", 7, rca, token=TOKEN, transport=tr))
    body = json.loads(sent[0].content)
    assert body["unfurl_links"] is False and body["unfurl_media"] is False
    for b in body["blocks"]:
        if b["type"] == "section" and b["text"]["type"] == "mrkdwn":
            assert b["text"].get("verbatim") is True


@pytest.mark.parametrize(
    "cmd,reason,text",
    [
        ("kubectl get pods\nkubectl delete ns prod", "controle", "quebra de linha/caracteres de controle não pode ser aprovado"),
        ("kubectl get pods\x1b[2J", "controle", "quebra de linha/caracteres de controle não pode ser aprovado"),
        ("echo `id`", "crase", "crase"),
        ("kubectl get pods " + "a" * 3000, "longo", "longo demais"),
    ],
)
def test_blocked_command_explains_why_in_slack_and_dashboard(cmd, reason, text):
    from aisre.slack import command_block_reason

    assert command_block_reason(cmd) == reason
    blob = json.dumps(build_message(7, _rca(suggested_command=cmd)), ensure_ascii=False)
    assert "Aprovar" not in blob and text in blob
    client, _, inc = _setup(rca=_rca(suggested_command=cmd))
    detail = client.get(f"/incidents/{inc.id}").json()
    assert detail["approvable"] is False and detail["approval_blocked_reason"] == reason


def test_plain_newline_stays_unapprovable_and_ok_command_has_no_reason():
    client, _, inc = _setup(rca=_rca(suggested_command="kubectl get pods"))
    detail = client.get(f"/incidents/{inc.id}").json()
    assert detail["approvable"] is True and detail["approval_blocked_reason"] is None
    client, _, inc = _setup(rca=_rca(suggested_command="kubectl get pods\nkubectl get nodes"))
    r = client.post(f"/incidents/{inc.id}/approve", headers={"Origin": "http://testserver"})
    assert r.status_code == 409
