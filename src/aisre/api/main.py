from __future__ import annotations

import logging

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
    app = FastAPI(title="aisre")
    app.state.settings = settings
    app.state.store = store
    app.state.memory = Memory(store)
    app.state.investigator = investigator or default_investigator(settings)
    app.include_router(routes_alerts.router)
    app.include_router(routes_incidents.router)
    return app
