"""Store em Postgres + pgvector. Não executado nos testes unitários (ver teste de integração)."""

from __future__ import annotations

from importlib import resources

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from aisre.agent.loop import Alert
from aisre.db import Approval, Incident, MemoryRow, fingerprint
from aisre.schemas import RCA

_INCIDENT_COLS = (
    "i.id, i.fingerprint, i.alert_name, i.labels, i.fired_at, i.created_at, i.status, i.error, r.data AS rca"
)
_INCIDENT_FROM = "FROM incidents i LEFT JOIN rcas r ON r.incident_id = i.id"


def read_migrations() -> list[tuple[str, str]]:
    """Migrações empacotadas, em ordem de nome (0001_..., 0002_...)."""
    files = sorted(
        (f for f in resources.files("aisre").joinpath("migrations").iterdir() if f.name.endswith(".sql")),
        key=lambda f: f.name,
    )
    return [(f.name, f.read_text(encoding="utf-8")) for f in files]


def read_migration() -> str:
    return resources.files("aisre").joinpath("migrations/0001_init.sql").read_text(encoding="utf-8")


def _vec(embedding: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def _incident(row: dict) -> Incident:
    return Incident(**{**row, "rca": RCA.model_validate(row["rca"]) if row["rca"] else None})


class PostgresStore:
    def __init__(self, dsn: str, max_size: int = 5) -> None:
        self._pool = AsyncConnectionPool(
            dsn, min_size=1, max_size=max_size, open=False, kwargs={"row_factory": dict_row}
        )

    async def open(self) -> None:
        await self._pool.open(wait=True)

    async def close(self) -> None:
        await self._pool.close()

    async def migrate(self) -> None:
        """Idempotente (CREATE ... IF NOT EXISTS)."""
        async with self._pool.connection() as conn:
            for _name, sql in read_migrations():
                await conn.execute(sql)

    async def create_if_new(self, alert: Alert, window_seconds: int) -> tuple[Incident, bool]:
        fp = fingerprint(alert.name, alert.labels)
        async with self._pool.connection() as conn:
            async with conn.transaction():
                # Serializa alertas concorrentes com a mesma impressão digital.
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (fp,))
                cur = await conn.execute(
                    f"SELECT {_INCIDENT_COLS} {_INCIDENT_FROM} WHERE i.fingerprint = %s "
                    "AND i.created_at >= now() - make_interval(secs => %s) "
                    "ORDER BY i.id DESC LIMIT 1",
                    (fp, window_seconds),
                )
                row = await cur.fetchone()
                if row:
                    return _incident(row), False
                cur = await conn.execute(
                    "INSERT INTO incidents (fingerprint, alert_name, labels, fired_at) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    (fp, alert.name, Jsonb(alert.labels), alert.fired_at),
                )
                new_id = (await cur.fetchone())["id"]
                cur = await conn.execute(
                    f"SELECT {_INCIDENT_COLS} {_INCIDENT_FROM} WHERE i.id = %s", (new_id,)
                )
                return _incident(await cur.fetchone()), True

    async def complete(self, incident_id: int, rca: RCA) -> None:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO rcas (incident_id, data) VALUES (%s, %s) "
                    "ON CONFLICT (incident_id) DO UPDATE SET data = EXCLUDED.data",
                    (incident_id, Jsonb(rca.model_dump(mode="json"))),
                )
                await conn.execute(
                    "UPDATE incidents SET status = 'done', error = NULL WHERE id = %s", (incident_id,)
                )

    async def fail(self, incident_id: int, error: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE incidents SET status = 'failed', error = %s WHERE id = %s",
                (error, incident_id),
            )

    async def fail_stale(self, older_than_seconds: int, error: str) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE incidents SET status = 'failed', error = %s WHERE status = 'investigating' "
                "AND created_at < now() - make_interval(secs => %s)",
                (error, older_than_seconds),
            )
            return cur.rowcount

    async def get(self, incident_id: int) -> Incident | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_INCIDENT_COLS} {_INCIDENT_FROM} WHERE i.id = %s", (incident_id,)
            )
            row = await cur.fetchone()
            return _incident(row) if row else None

    async def list(self, limit: int = 100) -> list[Incident]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_INCIDENT_COLS} {_INCIDENT_FROM} ORDER BY i.id DESC LIMIT %s", (limit,)
            )
            return [_incident(r) for r in await cur.fetchall()]

    async def record_approval(self, incident_id: int, user_id: str, command: str) -> Approval:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO approvals (incident_id, user_id, command) VALUES (%s, %s, %s) "
                    "ON CONFLICT (incident_id, user_id) DO NOTHING",
                    (incident_id, user_id, command),
                )
                cur = await conn.execute(
                    "SELECT incident_id, user_id, command, at FROM approvals "
                    "WHERE incident_id = %s AND user_id = %s",
                    (incident_id, user_id),
                )
                return Approval(**await cur.fetchone())

    async def list_approvals(self, incident_id: int) -> list[Approval]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT incident_id, user_id, command, at FROM approvals "
                "WHERE incident_id = %s ORDER BY at, user_id",
                (incident_id,),
            )
            return [Approval(**r) for r in await cur.fetchall()]

    async def add_memory(
        self, incident_id: int, content: str, rca: RCA, embedding: list[float]
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO memories (incident_id, content, rca, embedding) "
                "VALUES (%s, %s, %s, %s::vector)",
                (incident_id, content, Jsonb(rca.model_dump(mode="json")), _vec(embedding)),
            )

    async def nearest(self, embedding: list[float], k: int) -> list[MemoryRow]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT incident_id, content, rca, embedding <=> %s::vector AS distance "
                "FROM memories ORDER BY embedding <=> %s::vector, id LIMIT %s",
                (_vec(embedding), _vec(embedding), k),
            )
            return [
                MemoryRow(
                    incident_id=r["incident_id"],
                    content=r["content"],
                    rca=RCA.model_validate(r["rca"]),
                    distance=float(r["distance"]),
                )
                for r in await cur.fetchall()
            ]
