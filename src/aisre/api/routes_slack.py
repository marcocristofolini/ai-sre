"""Ações interativas do Slack. Só REGISTRA a aprovação; nada é executado."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request

from aisre import strings_pt as t
from aisre.slack import APPROVE_ACTION_ID

router = APIRouter()

REPLAY_WINDOW_SECONDS = 300
MAX_BODY_BYTES = 100_000


def _verify(secret: str, timestamp: str | None, signature: str | None, body: bytes) -> bool:
    if not secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > REPLAY_WINDOW_SECONDS:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _parse(body: bytes) -> tuple[str, int]:
    """Devolve (user_id, incident_id) ou levanta 400."""
    bad = HTTPException(status_code=400, detail=t.ERRO_SLACK_PAYLOAD)
    try:
        form = parse_qs(body.decode("utf-8"), strict_parsing=False)
        payload = json.loads(form["payload"][0])
        user_id = payload["user"]["id"]
        actions = payload["actions"]
        action = next(a for a in actions if a["action_id"] == APPROVE_ACTION_ID)
        incident_id = int(action["value"])
    except (KeyError, IndexError, TypeError, ValueError, StopIteration, UnicodeDecodeError):
        raise bad from None
    if not isinstance(user_id, str) or not user_id or isinstance(action["value"], bool):
        raise bad
    if not 0 < incident_id < 2**63:
        raise bad
    return user_id, incident_id


@router.post("/slack/actions")
async def slack_actions(request: Request):
    settings = request.app.state.settings
    if not settings.slack_signing_secret or not settings.slack_channel or not settings.slack_bot_token:
        raise HTTPException(status_code=503, detail=t.ERRO_SLACK_NAO_CONFIGURADO)
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=t.ERRO_SLACK_CORPO_GRANDE.format(limite=MAX_BODY_BYTES))
    if not _verify(
        settings.slack_signing_secret.get_secret_value(),
        request.headers.get("X-Slack-Request-Timestamp"),
        request.headers.get("X-Slack-Signature"),
        body,
    ):
        raise HTTPException(status_code=401, detail=t.ERRO_SLACK_ASSINATURA)
    user_id, incident_id = _parse(body)
    if user_id not in settings.slack_approvers:
        raise HTTPException(status_code=403, detail=t.ERRO_SLACK_NAO_APROVADOR)
    store = request.app.state.store
    incident = await store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=t.ERRO_INCIDENTE_NAO_ENCONTRADO)
    # O comando vem do RCA persistido, nunca do payload do Slack.
    rca = incident.rca
    if rca is None or rca.unknown or not rca.suggested_command:
        raise HTTPException(status_code=409, detail=t.ERRO_SLACK_SEM_COMANDO)
    await store.record_approval(incident_id, user_id, rca.suggested_command)
    return {"ok": True, "text": t.SLACK_APROVACAO_REGISTRADA}
