from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from aisre import strings_pt as t
from aisre.auth import require_same_origin, require_session
from aisre.db import Incident
from aisre.slack import command_is_showable

router = APIRouter(dependencies=[Depends(require_session)])

# Aprovador do dashboard: só o usuário local configurado (`admin_user`), que é o único que
# consegue sessão. Identidade registrada como "web:<usuário>" para não colidir com ids do Slack.
APPROVER_PREFIX = "web:"


class ApprovalOut(BaseModel):
    user_id: str
    command: str


class IncidentDetail(Incident):
    approvals: list[ApprovalOut] = []
    approvable: bool = False


def _approvable(incident: Incident) -> bool:
    rca = incident.rca
    return bool(
        rca is not None and not rca.unknown and rca.suggested_command and command_is_showable(rca.suggested_command)
    )


@router.get("/incidents", response_model=list[Incident])
async def list_incidents(request: Request, limit: int = 100):
    return await request.app.state.store.list(max(1, min(limit, 500)))


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
async def get_incident(incident_id: int, request: Request):
    incident = await request.app.state.store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=t.ERRO_INCIDENTE_NAO_ENCONTRADO)
    approvals = await request.app.state.store.list_approvals(incident_id)
    return IncidentDetail(
        **incident.model_dump(),
        approvals=[ApprovalOut(user_id=a.user_id, command=a.command) for a in approvals],
        approvable=_approvable(incident),
    )


@router.post("/incidents/{incident_id}/approve", dependencies=[Depends(require_same_origin)])
async def approve(incident_id: int, request: Request, user: str = Depends(require_session)):
    """Registra a aprovação; o comando vem do RCA persistido (o corpo é ignorado). Não executa nada."""
    store = request.app.state.store
    incident = await store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=t.ERRO_INCIDENTE_NAO_ENCONTRADO)
    rca = incident.rca
    if rca is None or rca.unknown or not rca.suggested_command:
        raise HTTPException(status_code=409, detail=t.ERRO_SLACK_SEM_COMANDO)
    if not command_is_showable(rca.suggested_command):
        raise HTTPException(status_code=409, detail=t.ERRO_APROVACAO_COMANDO_NAO_EXIBIVEL)
    await store.record_approval(incident_id, APPROVER_PREFIX + user, rca.suggested_command)
    return {"ok": True, "text": t.APROVACAO_REGISTRADA}
