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
        await store.open()
        await store.migrate()
        alert = Alert(name=f"A{os.getpid()}", labels={"s": "x"}, fired_at=datetime.now(UTC))
        inc, created = await store.create_if_new(alert, 300)
        dup, created2 = await store.create_if_new(alert, 300)
        assert created and not created2 and dup.id == inc.id
        # Causa única por execução: num banco reutilizado, memórias idênticas de execuções
        # anteriores empatariam em distância 0 (e a mais antiga venceria pelo id).
        cause = f"OOM {os.getpid()} {datetime.now(UTC).timestamp()}"
        rca = RCA(cause=cause, confidence=0.7, evidence=[Evidence(kind="log", ref="log:a", summary="oom")])
        await store.complete(inc.id, rca)
        assert (await store.get(inc.id)).rca == rca
        memory = Memory(store)
        await memory.save(inc.id, rca)
        assert (await memory.similar(f"{cause} oom", k=1))[0].incident_id == inc.id
        await store.close()

    asyncio.run(run())


# --- I7: cobertura de aprovações, fail_stale, migração 0002 e ciclo de vida do pool ---------------
# Estes testes só rodam com AISRE_TEST_DATABASE_URL (Postgres com pgvector) e NÃO foram executados
# no ambiente de desenvolvimento deste repositório (sem Postgres disponível).

needs_pg = pytest.mark.skipif(not DSN, reason="defina AISRE_TEST_DATABASE_URL (Postgres com pgvector)")


def _alert(tag: str) -> Alert:
    return Alert(name=f"I7-{tag}-{os.getpid()}-{datetime.now(UTC).timestamp()}", labels={"t": tag},
                 fired_at=datetime.now(UTC))


async def _store():
    from aisre.db_postgres import PostgresStore

    store = PostgresStore(DSN)
    await store.open()
    await store.migrate()
    return store


@needs_pg
def test_postgres_migrations_are_idempotent_and_create_approvals_table():
    from aisre.db_postgres import read_migrations

    async def run():
        store = await _store()
        try:
            await store.migrate()  # segunda vez: sem erro
            names = [n for n, _ in read_migrations()]
            assert names[:2] == ["0001_init.sql", "0002_approvals.sql"]
            async with store._pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'approvals'"
                )
                cols = {r["column_name"] for r in await cur.fetchall()}
            assert {"incident_id", "user_id", "command", "at"} <= cols
        finally:
            await store.close()

    asyncio.run(run())


@needs_pg
def test_postgres_record_and_list_approvals_idempotent_per_user():
    async def run():
        store = await _store()
        try:
            inc, _ = await store.create_if_new(_alert("aprov"), 300)
            assert await store.list_approvals(inc.id) == []
            a1 = await store.record_approval(inc.id, "U1", "kubectl get pods")
            again = await store.record_approval(inc.id, "U1", "outro comando")  # não sobrescreve
            assert again.command == "kubectl get pods" and again.at == a1.at
            await store.record_approval(inc.id, "web:admin", "kubectl get pods")
            listed = await store.list_approvals(inc.id)
            assert [(a.user_id, a.command) for a in listed] == [("U1", "kubectl get pods"), ("web:admin", "kubectl get pods")]
            assert all(a.incident_id == inc.id for a in listed)
        finally:
            await store.close()

    asyncio.run(run())


@needs_pg
def test_postgres_fail_stale_only_marks_old_investigating_incidents():
    async def run():
        store = await _store()
        try:
            old, _ = await store.create_if_new(_alert("velho"), 300)
            done, _ = await store.create_if_new(_alert("pronto"), 300)
            await store.complete(done.id, RCA(cause="x", confidence=0.5,
                                              evidence=[Evidence(kind="log", ref="log:a", summary="s")]))
            assert await store.fail_stale(3600, "interrompida") >= 0
            assert (await store.get(old.id)).status == "investigating"  # recente: intocado
            await asyncio.sleep(0.05)
            n = await store.fail_stale(0, "interrompida")
            assert n >= 1
            got = await store.get(old.id)
            assert got.status == "failed" and got.error == "interrompida"
            assert (await store.get(done.id)).status == "done"
        finally:
            await store.close()

    asyncio.run(run())


@needs_pg
def test_postgres_pool_lifecycle_and_app_lifespan():
    from fastapi.testclient import TestClient
    from psycopg_pool import PoolClosed

    from aisre.api.main import create_app
    from aisre.config import Settings
    from aisre.db_postgres import PostgresStore

    async def closed_store_refuses():
        store = PostgresStore(DSN)
        await store.open()
        await store.close()
        with pytest.raises(PoolClosed):
            await store.get(1)

    asyncio.run(closed_store_refuses())

    settings = Settings(_env_file=None, database_url=DSN, webhook_token="tok")
    app = create_app(settings, investigator=lambda alert: asyncio.sleep(0, RCA(
        cause="x", confidence=0.5, evidence=[Evidence(kind="log", ref="log:a", summary="s")])))
    with TestClient(app) as client:  # lifespan: open + migrate + fail_stale; close na saída
        assert client.get("/healthz").status_code == 200
        r = client.post("/alerts/webhook", headers={"Authorization": "Bearer tok"}, json={"alerts": [
            {"status": "firing", "labels": {"alertname": f"I7-app-{os.getpid()}"}, "startsAt": "2026-11-23T10:00:00Z"}]})
        assert r.status_code == 202
    with pytest.raises(PoolClosed):
        asyncio.run(app.state.store.get(1))


# Não executado aqui (sem Postgres): exclusão do incidente atual na busca por semelhantes.
@needs_pg
def test_postgres_similar_excludes_current_incident():
    async def run():
        store = await _store()
        tag = f"{os.getpid()}-{datetime.now(UTC).timestamp()}"
        a, _ = await store.create_if_new(_alert("mem-a"), 300)
        b, _ = await store.create_if_new(_alert("mem-b"), 300)
        cause = f"vazamento unico {tag}"
        for inc in (a, b):
            rca = RCA(cause=cause, confidence=0.7, evidence=[Evidence(kind="log", ref="log:a", summary="x")])
            await store.complete(inc.id, rca)
            await Memory(store).save(inc.id, rca)
        found = await Memory(store).similar(cause, k=1, exclude_incident_id=a.id)
        assert [p.incident_id for p in found] == [b.id]
        await store.close()

    asyncio.run(run())
