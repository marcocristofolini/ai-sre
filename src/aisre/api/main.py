from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from aisre import strings_pt as t
from aisre.api import routes_alerts, routes_incidents
from aisre.api.ingest import Investigator, default_investigator
from aisre.config import Settings
from aisre.db import MemoryStore, Store
from aisre.memory import Memory

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    investigator: Investigator | None = None,
) -> FastAPI:
    settings = settings or Settings()
    if store is None:
        if settings.database_url:
            from aisre.db_postgres import PostgresStore

            store = PostgresStore(settings.database_url)
        else:
            log.warning(t.AVISO_STORE_EM_MEMORIA)
            store = MemoryStore()

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
            yield
        finally:
            closer = getattr(store, "close", None)
            if closer:
                await closer()

    app = FastAPI(title="aisre", lifespan=lifespan)
    app.state.investigation_semaphore = asyncio.Semaphore(max(1, settings.max_concurrent_investigations))
    app.state.settings = settings
    app.state.store = store
    app.state.memory = Memory(store)
    app.state.investigator = investigator or default_investigator(settings)
    app.include_router(routes_alerts.router)
    app.include_router(routes_incidents.router)
    return app
