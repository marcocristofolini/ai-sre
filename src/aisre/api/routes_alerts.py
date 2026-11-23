from __future__ import annotations

import asyncio
import hmac
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field, ValidationError, field_validator

from aisre import strings_pt as t
from aisre.agent.loop import Alert
from aisre.api.ingest import investigation_queue_full, spawn_investigation
from aisre.config import Settings

router = APIRouter()
log = logging.getLogger(__name__)


def _check_text(value: str) -> str:
    # NUL não é aceito por TEXT/JSONB do Postgres; substitutos soltos não codificam em UTF-8.
    if "\x00" in value:
        raise ValueError(t.ERRO_TEXTO_INVALIDO)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(t.ERRO_TEXTO_INVALIDO) from None
    return value


class _AMAlert(BaseModel):
    status: str
    labels: dict[str, str] = Field(default_factory=dict)
    startsAt: datetime

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        return _check_text(v)

    @field_validator("labels")
    @classmethod
    def _labels(cls, v: dict[str, str]) -> dict[str, str]:
        for key, value in v.items():
            _check_text(key)
            _check_text(value)
        return v


def _error(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail})


def _check_token(request: Request, settings: Settings) -> JSONResponse | None:
    """`Authorization: Bearer <token>` em tempo constante; sem token configurado, falha fechado."""
    expected = settings.webhook_token.get_secret_value()
    if not expected:
        if settings.webhook_allow_unauthenticated:
            return None
        return _error(503, t.ERRO_WEBHOOK_NAO_CONFIGURADO)
    scheme, _, given = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(given.strip().encode(), expected.encode()):
        return JSONResponse(
            status_code=401,
            content={"detail": t.ERRO_WEBHOOK_NAO_AUTORIZADO},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return None


async def _await_without_lifespan(tasks: list[asyncio.Task]) -> None:
    # Sem lifespan (ex.: servidor ASGI sem ciclo de vida, TestClient fora de `with`) ninguém cancela
    # nem aguarda as tarefas no desligamento: a própria requisição as aguarda, sem cancelá-las.
    if tasks:
        await asyncio.wait(tasks)


@router.post("/alerts/webhook", status_code=202)
async def webhook(request: Request):
    app = request.app
    settings = app.state.settings
    denied = _check_token(request, settings)
    if denied is not None:
        return denied
    limit = settings.max_webhook_body_bytes
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return _error(413, t.ERRO_WEBHOOK_MUITO_GRANDE.format(limite=limit))
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > limit:
            return _error(413, t.ERRO_WEBHOOK_MUITO_GRANDE.format(limite=limit))
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):  # UnicodeDecodeError é ValueError
        return _error(422, t.ERRO_WEBHOOK_JSON_INVALIDO)
    if not isinstance(body, dict) or not isinstance(body.get("alerts"), list):
        return _error(422, t.ERRO_WEBHOOK_PAYLOAD_INVALIDO)
    if not body["alerts"]:
        return _error(422, t.ERRO_WEBHOOK_SEM_ALERTAS)
    alerts: list[Alert] = []
    for index, raw_alert in enumerate(body["alerts"]):
        try:
            am = _AMAlert.model_validate(raw_alert)
            name = am.labels.get("alertname", "")
            if not name:
                raise ValueError(t.ERRO_ALERTA_SEM_NOME)
        except (ValidationError, ValueError) as exc:
            # Nunca ecoa o valor recebido; apenas o campo/motivo.
            if isinstance(exc, ValidationError):
                detail = ", ".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())
            else:
                detail = str(exc)
            return _error(422, t.ERRO_WEBHOOK_ALERTA_INVALIDO.format(indice=index, detalhe=detail))
        if am.status == "firing":
            alerts.append(Alert(name=name, labels=am.labels, fired_at=am.startsAt))

    # O limite conta só alertas firing; o excesso é descartado com 202 (o Alertmanager não reenvia 4xx).
    truncated = None
    limit_alerts = settings.max_alerts_per_request
    if len(alerts) > limit_alerts:
        log.warning(t.AVISO_WEBHOOK_TRUNCADO.format(total=len(alerts), limite=limit_alerts))
        truncated = {"firing": len(alerts), "investigated": limit_alerts}
        alerts = alerts[:limit_alerts]

    results = []
    spawned: list[asyncio.Task] = []
    store_failed = False
    queue_full = False
    for alert in alerts:
        if investigation_queue_full(app.state):
            queue_full = True
            results.append({"incident_id": None, "duplicate": False, "error": True})
            continue
        try:  # um alerta com falha no armazenamento não pode envenenar os demais
            incident, created = await app.state.store.create_if_new(
                alert, settings.dedupe_window_seconds
            )
        except Exception as exc:  # noqa: BLE001 - só o tipo
            log.warning(t.AVISO_STORE_ALERTA_FALHOU.format(tipo=type(exc).__name__))
            store_failed = True
            results.append({"incident_id": None, "duplicate": False, "error": True})
            continue
        if created:
            spawned.append(spawn_investigation(app.state, incident.id, alert))
        results.append({"incident_id": incident.id, "duplicate": not created})
    background = None if app.state.lifespan_active else BackgroundTask(_await_without_lifespan, spawned)
    if queue_full:
        log.warning(t.AVISO_FILA_INVESTIGACOES_CHEIA.format(limite=settings.max_pending_investigations))
    if store_failed or queue_full:
        # 503: o Alertmanager reenvia; os já criados continuam sendo investigados e
        # a reentrega os deduplica.
        detail = t.ERRO_ARMAZENAMENTO_INDISPONIVEL if store_failed else t.ERRO_FILA_INVESTIGACOES_CHEIA
        return JSONResponse(
            status_code=503, content={"detail": detail, "incidents": results}, background=background
        )
    content: dict = {"incidents": results}
    if truncated is not None:
        content["truncated"] = truncated
    return JSONResponse(status_code=202, content=content, background=background)
