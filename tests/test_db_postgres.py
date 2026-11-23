import asyncio
import os
from datetime import UTC, datetime

import pytest

from aisre.agent.loop import Alert
from aisre.memory import Memory
from aisre.schemas import RCA, Evidence

pytestmark = pytest.mark.integration
DSN = os.environ.get("AISRE_TEST_DATABASE_URL")


@pytest.mark.skipif(not DSN, reason="defina AISRE_TEST_DATABASE_URL (Postgres com pgvector)")
def test_postgres_store_roundtrip_dedupe_and_similarity():
    from aisre.db_postgres import PostgresStore

    async def run():
        store = PostgresStore(DSN)
        await store.migrate()
        alert = Alert(name=f"A{os.getpid()}", labels={"s": "x"}, fired_at=datetime.now(UTC))
        inc, created = await store.create_if_new(alert, 300)
        dup, created2 = await store.create_if_new(alert, 300)
        assert created and not created2 and dup.id == inc.id
        rca = RCA(cause="OOM", confidence=0.7, evidence=[Evidence(kind="log", ref="log:a", summary="oom")])
        await store.complete(inc.id, rca)
        assert (await store.get(inc.id)).rca == rca
        memory = Memory(store)
        await memory.save(inc.id, rca)
        assert (await memory.similar("OOM oom", k=1))[0].incident_id == inc.id

    asyncio.run(run())
