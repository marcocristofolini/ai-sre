"""Ingestão: cria o incidente (com deduplicação) e investiga em segundo plano."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aisre import strings_pt as t
from aisre.agent.loop import Alert, investigate
from aisre.api import hooks
from aisre.config import Settings
from aisre.db import Store
from aisre.llm.base import build_provider
from aisre.llm.gate import ConsentRequired
from aisre.memory import Memory
from aisre.schemas import RCA
from aisre.tools import build_tools

log = logging.getLogger(__name__)

Investigator = Callable[[Alert], Awaitable[RCA]]


def default_investigator(settings: Settings) -> Investigator:
    async def run(alert: Alert) -> RCA:
        # Construído por investigação: falhas de configuração viram incidente falho, não crash.
        return await investigate(alert, build_tools(settings), build_provider(settings))

    return run


async def run_investigation(
    incident_id: int,
    alert: Alert,
    store: Store,
    investigator: Investigator,
    memory: Memory,
    timeout_seconds: int,
) -> None:
    """Nunca levanta: qualquer falha é registrada no incidente."""
    try:
        rca = await asyncio.wait_for(investigator(alert), timeout=timeout_seconds)
    except ConsentRequired:
        log.error(t.ERRO_INVESTIGACAO_CONSENTIMENTO)
        await store.fail(incident_id, t.ERRO_INVESTIGACAO_CONSENTIMENTO)
        return
    except TimeoutError:
        await store.fail(incident_id, t.ERRO_INVESTIGACAO_TIMEOUT.format(segundos=timeout_seconds))
        return
    except Exception as exc:  # noqa: BLE001 - só o tipo: a mensagem pode conter segredos
        msg = t.ERRO_INVESTIGACAO_FALHOU.format(tipo=type(exc).__name__)
        log.warning(msg)
        await store.fail(incident_id, msg)
        return
    await store.complete(incident_id, rca)
    if not rca.unknown:
        try:
            await memory.save(incident_id, rca)
        except Exception as exc:  # noqa: BLE001
            log.warning(t.AVISO_MEMORIA_FALHOU.format(tipo=type(exc).__name__))
    for hook in list(hooks.on_rca_ready):
        try:
            await hook(incident_id, rca)
        except Exception as exc:  # noqa: BLE001
            log.warning(t.AVISO_HOOK_RCA_FALHOU.format(tipo=type(exc).__name__))
