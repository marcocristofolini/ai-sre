from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from aisre import strings_pt as t
from aisre.db import Incident

router = APIRouter()


class ApprovalOut(BaseModel):
    user_id: str
    command: str


class IncidentDetail(Incident):
    approvals: list[ApprovalOut] = []


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
    )
