from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from aisre import strings_pt as t
from aisre.agent.loop import Alert
from aisre.api import (
    routes_alerts,
    routes_auth,
    routes_incidents,
    routes_integrations,
    routes_risk,
    routes_scoreboard,
    routes_slack,
)
from aisre.api.ingest import Investigator, default_investigator, investigation_queue_full, spawn_investigation
from aisre.auth import LoginThrottle
from aisre.config import Settings
from aisre.db import MemoryStore, Store
from aisre.memory import Memory
from aisre.predict.wiring import Predictive
from aisre.slack import make_hook
from aisre.tools.prometheus import PrometheusTool

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    investigator: Investigator | None = None,
    slack_transport: httpx.AsyncBaseTransport | None = None,
    prometheus_transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], float] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    if store is None:
        if settings.database_url:
            from aisre.db_postgres import PostgresStore

            store = PostgresStore(settings.database_url)
        else:
            log.warning(t.AVISO_STORE_EM_MEMORIA)
            store = MemoryStore()

    async def open_predictive_investigation(workload: str, score: float) -> None:
        """Cria o incidente e enfileira a investigação em tarefa de fundo (não bloqueia o agendador)."""
        alert = Alert(
            name=t.ALERTA_RISCO_PREDITIVO,
            labels={"workload": workload, "origem": "preditivo"},
            fired_at=datetime.now(UTC),
        )
        if investigation_queue_full(app.state):
            log.warning(t.AVISO_FILA_INVESTIGACOES_CHEIA.format(limite=settings.max_pending_investigations))
            return
        incident, created = await store.create_if_new(alert, settings.dedupe_window_seconds)
        if not created:
            return
        spawn_investigation(app.state, incident.id, alert)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Armazenamentos com ciclo de vida (Postgres) expõem open/migrate/close.
        opener = getattr(store, "open", None)
        if opener:
            await opener()
        try:
            migrate = getattr(store, "migrate", None)
            if migrate:
                await migrate()
            n = await store.fail_stale(
                settings.investigation_timeout_seconds, t.ERRO_INVESTIGACAO_INTERROMPIDA
            )
            if n:
                log.warning(t.AVISO_INCIDENTES_ORFAOS.format(n=n))
            predictive_task = None
            predictive = app.state.predictive
            if predictive is not None:
                predictive_task = asyncio.create_task(predictive.run())
                predictive_task.add_done_callback(_log_predictive_end)
            app.state.lifespan_active = True
            try:
                yield
            finally:
                app.state.lifespan_active = False
                # Encerra o preditivo e as investigações pendentes antes de fechar o armazenamento.
                pending = [*([predictive_task] if predictive_task else []), *app.state.investigation_tasks]
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            closer = getattr(store, "close", None)
            if closer:
                await closer()

    def _log_predictive_end(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            log.warning(t.AVISO_PREDITIVO_FALHOU.format(tipo=type(task.exception()).__name__))

    app = FastAPI(title="aisre", lifespan=lifespan)
    app.state.clock = clock or time.time
    app.state.login_throttle = LoginThrottle(clock=app.state.clock)
    app.state.investigation_semaphore = asyncio.Semaphore(max(1, settings.max_concurrent_investigations))
    app.state.investigation_tasks = set()
    app.state.lifespan_active = False
    if settings.webhook_allow_unauthenticated and not settings.webhook_token.get_secret_value():
        log.warning(t.AVISO_WEBHOOK_SEM_AUTENTICACAO)
    app.state.settings = settings
    app.state.store = store
    app.state.memory = Memory(store)
    app.state.investigator = investigator or default_investigator(settings)
    # Registro por app (não global): cada app publica no Slack com a sua configuração.
    app.state.rca_hooks = [make_hook(settings, slack_transport)]
    app.state.predictive = None
    if settings.prometheus_url and settings.predict_workloads:
        prometheus = PrometheusTool(
            settings.prometheus_url, settings.tool_timeout_seconds, transport=prometheus_transport
        )
        app.state.predictive = Predictive(settings, prometheus, open_predictive_investigation)
    else:
        log.info(t.AVISO_PREDITIVO_NAO_INICIADO)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    app.include_router(routes_auth.router)
    app.include_router(routes_alerts.router)
    app.include_router(routes_incidents.router)
    app.include_router(routes_risk.router)
    app.include_router(routes_scoreboard.router)
    app.include_router(routes_integrations.router)
    app.include_router(routes_slack.router)
    dist = Path(settings.web_dist_dir) if settings.web_dist_dir else Path(__file__).resolve().parents[3] / "web" / "dist"
    if (dist / "index.html").is_file():  # por último: as rotas da API têm precedência
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app
