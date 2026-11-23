from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from aisre import strings_pt as t
from aisre.agent.loop import Alert
from aisre.api.ingest import run_investigation

router = APIRouter()


class _AMAlert(BaseModel):
    status: str
    labels: dict[str, str] = Field(default_factory=dict)
    startsAt: datetime


def _422(detail: str) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": detail})


@router.post("/alerts/webhook", status_code=202)
async def webhook(request: Request, background: BackgroundTasks):
    try:
        body = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        return _422(t.ERRO_WEBHOOK_JSON_INVALIDO)
    if not isinstance(body, dict) or not isinstance(body.get("alerts"), list):
        return _422(t.ERRO_WEBHOOK_PAYLOAD_INVALIDO)
    if not body["alerts"]:
        return _422(t.ERRO_WEBHOOK_SEM_ALERTAS)

    alerts: list[Alert] = []
    for index, raw in enumerate(body["alerts"]):
        try:
            am = _AMAlert.model_validate(raw)
            name = am.labels.get("alertname", "")
            if not name:
                raise ValueError(t.ERRO_ALERTA_SEM_NOME)
        except (ValidationError, ValueError) as exc:
            # Nunca ecoa o valor recebido; apenas o campo/motivo.
            if isinstance(exc, ValidationError):
                detail = ", ".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())
            else:
                detail = str(exc)
            return _422(t.ERRO_WEBHOOK_ALERTA_INVALIDO.format(indice=index, detalhe=detail))
        if am.status == "firing":
            alerts.append(Alert(name=name, labels=am.labels, fired_at=am.startsAt))

    app = request.app
    results = []
    for alert in alerts:
        incident, created = await app.state.store.create_if_new(
            alert, app.state.settings.dedupe_window_seconds
        )
        if created:
            background.add_task(
                run_investigation,
                incident.id,
                alert,
                app.state.store,
                app.state.investigator,
                app.state.memory,
                app.state.settings.investigation_timeout_seconds,
            )
        results.append({"incident_id": incident.id, "duplicate": not created})
    return {"incidents": results}
