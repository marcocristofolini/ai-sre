from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from aisre import strings_pt as t
from aisre.db import Incident

router = APIRouter()


@router.get("/incidents", response_model=list[Incident])
async def list_incidents(request: Request, limit: int = 100):
    return await request.app.state.store.list(max(1, min(limit, 500)))


@router.get("/incidents/{incident_id}", response_model=Incident)
async def get_incident(incident_id: int, request: Request):
    incident = await request.app.state.store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=t.ERRO_INCIDENTE_NAO_ENCONTRADO)
    return incident
