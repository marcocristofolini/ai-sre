from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from aisre.auth import require_session

router = APIRouter(dependencies=[Depends(require_session)])


@router.get("/risk")
async def risk(request: Request):
    """Estado ao vivo dos detectores; lista vazia quando o preditivo não está configurado."""
    predictive = request.app.state.predictive
    return predictive.risk() if predictive else []
