from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

from aisre import strings_pt as t
from aisre.agent.loop import Alert
from aisre.api.ingest import run_investigation

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


@router.post("/alerts/webhook", status_code=202)
async def webhook(request: Request, background: BackgroundTasks):
    app = request.app
    settings = app.state.settings
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
    if len(body["alerts"]) > settings.max_alerts_per_request:
        return _error(
            422,
            t.ERRO_WEBHOOK_ALERTAS_DEMAIS.format(
                total=len(body["alerts"]), limite=settings.max_alerts_per_request
            ),
        )

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

    results = []
    store_failed = False
    for alert in alerts:
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
            background.add_task(
                run_investigation,
                incident.id,
                alert,
                app.state.store,
                app.state.investigator,
                app.state.memory,
                settings.investigation_timeout_seconds,
                app.state.investigation_semaphore,
                app.state.rca_hooks,
            )
        results.append({"incident_id": incident.id, "duplicate": not created})
    if store_failed:
        # 503: o Alertmanager reenvia; os já criados continuam sendo investigados e
        # a reentrega os deduplica.
        return JSONResponse(
            status_code=503,
            content={"detail": t.ERRO_ARMAZENAMENTO_INDISPONIVEL, "incidents": results},
            background=background,
        )
    return {"incidents": results}
