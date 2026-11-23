"""Status somente leitura das integrações. Nunca devolve valores de segredos nem URLs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from aisre.auth import require_session

router = APIRouter(dependencies=[Depends(require_session)])


@router.get("/integrations")
async def integrations(request: Request):
    s = request.app.state.settings
    items = [
        {"id": "llm", "configured": bool(s.llm_provider), "detail": s.llm_model if s.llm_provider else None},
        {"id": "prometheus", "configured": bool(s.prometheus_url), "detail": None},
        {"id": "loki", "configured": bool(s.loki_url), "detail": None},
        {"id": "kubernetes", "configured": s.kubernetes_enabled, "detail": None},
        {
            "id": "github",
            "configured": bool(s.github_token.get_secret_value() and s.github_repo),
            "detail": s.github_repo or None,
        },
        {
            "id": "slack",
            "configured": bool(s.slack_bot_token and s.slack_signing_secret and s.slack_channel),
            "detail": None,
        },
        {"id": "postgres", "configured": bool(s.database_url), "detail": None},
    ]
    return items
