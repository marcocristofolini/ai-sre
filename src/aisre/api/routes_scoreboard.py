"""Placar de precisão: só lê o arquivo escrito por `make eval`; nunca inventa números."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from aisre import strings_pt as t
from aisre.auth import require_session
from aisre.harness.scoring import Scoreboard

router = APIRouter(dependencies=[Depends(require_session)])
log = logging.getLogger(__name__)


@router.get("/scoreboard")
async def get_scoreboard(request: Request):
    path = Path(request.app.state.settings.scoreboard_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"measured": False, "reason": "missing"}
    except OSError as exc:
        log.warning(t.AVISO_PLACAR_ILEGIVEL.format(tipo=type(exc).__name__))
        return {"measured": False, "reason": "invalid"}
    try:
        board = Scoreboard.model_validate(json.loads(raw))
    except (ValueError, ValidationError, RecursionError) as exc:
        log.warning(t.AVISO_PLACAR_ILEGIVEL.format(tipo=type(exc).__name__))
        return {"measured": False, "reason": "invalid"}
    if board.total < 0 or not 0 <= board.correct <= board.total:
        return {"measured": False, "reason": "invalid"}
    # O rótulo é fixo: o arquivo não pode apresentar a medição como se fosse de produção.
    return {"measured": True, **board.model_dump(), "label": t.ROTULO_CENARIOS_SINTETICOS}
