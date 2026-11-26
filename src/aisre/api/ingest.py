"""Ingestão: cria o incidente (com deduplicação) e investiga em segundo plano."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar

from aisre import strings_pt as t
from aisre.agent.loop import Alert, investigate
from aisre.api import hooks
from aisre.config import Settings
from aisre.db import Store
from aisre.llm.base import build_provider
from aisre.llm.gate import ConsentRequired
from aisre.memory import Memory, PastIncident
from aisre.runbooks import Runbook, load_runbooks, select_runbooks
from aisre.schemas import RCA
from aisre.tools import build_tools

log = logging.getLogger(__name__)

Investigator = Callable[[Alert], Awaitable[RCA]]


# Incidente em investigação nesta tarefa (para excluí-lo da busca por semelhantes).
current_incident: ContextVar[int | None] = ContextVar("aisre_current_incident", default=None)


def alert_query(alert: Alert) -> str:
    return " ".join([alert.name, *(f"{k} {v}" for k, v in sorted(alert.labels.items()))])


async def recall_similar(memory: Memory | None, alert: Alert, top_k: int) -> list[PastIncident]:
    """Incidentes anteriores semelhantes; qualquer falha é registrada (só o tipo) e vira lista vazia."""
    if memory is None or top_k <= 0:
        return []
    try:
        return await memory.similar(alert_query(alert), top_k, exclude_incident_id=current_incident.get())
    except Exception as exc:  # noqa: BLE001 - só o tipo
        log.warning(t.AVISO_MEMORIA_CONSULTA_FALHOU.format(tipo=type(exc).__name__))
        return []


def pick_runbooks(runbooks: list[Runbook], alert: Alert, top_k: int) -> list[Runbook]:
    return select_runbooks(runbooks, alert.name, alert.labels, top_k)


def default_investigator(settings: Settings, memory: Memory | None = None) -> Investigator:
    # Lidos uma vez na criação: um runbook novo exige reiniciar o pod (o ConfigMap reinicia via checksum).
    runbooks = load_runbooks(settings.runbooks_dir, settings.runbooks_max_chars)

    async def run(alert: Alert) -> RCA:
        # Construído por investigação: falhas de configuração viram incidente falho, não crash.
        past = await recall_similar(memory, alert, settings.memory_top_k)
        chosen = pick_runbooks(runbooks, alert, settings.runbooks_top_k)
        rca = await investigate(
            alert, build_tools(settings), build_provider(settings), past=past, runbooks=chosen
        )
        return rca.model_copy(update={"runbooks": [rb.name for rb in chosen]}) if chosen else rca

    return run


async def _record_failure(store: Store, incident_id: int, message: str) -> None:
    try:
        await store.fail(incident_id, message)
    except Exception as exc:  # noqa: BLE001 - só o tipo: a mensagem pode conter segredos
        log.warning(t.AVISO_STORE_FALHOU.format(id=incident_id, tipo=type(exc).__name__))


async def run_investigation(
    incident_id: int,
    alert: Alert,
    store: Store,
    investigator: Investigator,
    memory: Memory,
    timeout_seconds: int,
    semaphore: asyncio.Semaphore | None = None,
    extra_hooks: Sequence[hooks.RcaHook] = (),
) -> None:
    """Falhas (inclusive do armazenamento) são registradas e engolidas; só o cancelamento
    propaga, depois de marcar o incidente como falho."""
    current_incident.set(incident_id)  # a tarefa de investigação tem contexto próprio
    try:
        if semaphore is None:
            rca = await asyncio.wait_for(investigator(alert), timeout=timeout_seconds)
        else:
            async with semaphore:  # a espera na fila não conta no tempo limite
                rca = await asyncio.wait_for(investigator(alert), timeout=timeout_seconds)
    except asyncio.CancelledError:
        await _record_failure(store, incident_id, t.ERRO_INVESTIGACAO_INTERROMPIDA)
        raise
    except ConsentRequired:
        log.error(t.ERRO_INVESTIGACAO_CONSENTIMENTO)
        await _record_failure(store, incident_id, t.ERRO_INVESTIGACAO_CONSENTIMENTO)
        return
    except TimeoutError:
        await _record_failure(
            store, incident_id, t.ERRO_INVESTIGACAO_TIMEOUT.format(segundos=timeout_seconds)
        )
        return
    except Exception as exc:  # noqa: BLE001 - só o tipo: a mensagem pode conter segredos
        msg = t.ERRO_INVESTIGACAO_FALHOU.format(tipo=type(exc).__name__)
        log.warning(msg)
        await _record_failure(store, incident_id, msg)
        return
    try:
        await store.complete(incident_id, rca)
    except Exception as exc:  # noqa: BLE001
        log.warning(t.AVISO_STORE_FALHOU.format(id=incident_id, tipo=type(exc).__name__))
        await _record_failure(
            store, incident_id, t.ERRO_INVESTIGACAO_FALHOU.format(tipo=type(exc).__name__)
        )
        # O RCA não foi salvo: não publica no Slack (o botão Aprovar apontaria para um RCA
        # inexistente) nem grava na memória.
        return
    if not rca.unknown:
        try:
            await memory.save(incident_id, rca)
        except Exception as exc:  # noqa: BLE001
            log.warning(t.AVISO_MEMORIA_FALHOU.format(tipo=type(exc).__name__))
    for hook in [*hooks.on_rca_ready, *extra_hooks]:
        try:
            await hook(incident_id, rca)
        except Exception as exc:  # noqa: BLE001
            log.warning(t.AVISO_HOOK_RCA_FALHOU.format(tipo=type(exc).__name__))


def spawn_investigation(state, incident_id: int, alert: Alert) -> asyncio.Task:
    """Investigação em tarefa própria, rastreada em `state.investigation_tasks` (corre em paralelo,
    limitada pelo semáforo, e é cancelada no desligamento; o cancelamento marca o incidente falho)."""
    task = asyncio.create_task(
        run_investigation(
            incident_id,
            alert,
            state.store,
            state.investigator,
            state.memory,
            state.settings.investigation_timeout_seconds,
            state.investigation_semaphore,
            state.rca_hooks,
        )
    )
    tasks: set[asyncio.Task] = state.investigation_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task


def investigation_queue_full(state) -> bool:
    return len(state.investigation_tasks) >= state.settings.max_pending_investigations
