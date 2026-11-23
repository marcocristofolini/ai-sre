"""Store em Postgres + pgvector. Não executado nos testes unitários (ver teste de integração)."""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from aisre.agent.loop import Alert
from aisre.db import Incident, MemoryRow, fingerprint
from aisre.schemas import RCA

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "0001_init.sql"

_INCIDENT_COLS = (
    "i.id, i.fingerprint, i.alert_name, i.labels, i.fired_at, i.created_at, i.status, i.error, r.data AS rca"
)
_INCIDENT_FROM = "FROM incidents i LEFT JOIN rcas r ON r.incident_id = i.id"


def _vec(embedding: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def _incident(row: dict) -> Incident:
    return Incident(**{**row, "rca": RCA.model_validate(row["rca"]) if row["rca"] else None})


class PostgresStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self) -> psycopg.AsyncConnection:
        return psycopg.AsyncConnection.connect(self._dsn, row_factory=dict_row)

    async def migrate(self) -> None:
        async with await self._connect() as conn:
            await conn.execute(MIGRATION.read_text())

    async def create_if_new(self, alert: Alert, window_seconds: int) -> tuple[Incident, bool]:
        fp = fingerprint(alert.name, alert.labels)
        async with await self._connect() as conn:
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
        async with await self._connect() as conn:
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
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE incidents SET status = 'failed', error = %s WHERE id = %s",
                (error, incident_id),
            )

    async def get(self, incident_id: int) -> Incident | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_INCIDENT_COLS} {_INCIDENT_FROM} WHERE i.id = %s", (incident_id,)
            )
            row = await cur.fetchone()
            return _incident(row) if row else None

    async def list(self, limit: int = 100) -> list[Incident]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_INCIDENT_COLS} {_INCIDENT_FROM} ORDER BY i.id DESC LIMIT %s", (limit,)
            )
            return [_incident(r) for r in await cur.fetchall()]

    async def add_memory(
        self, incident_id: int, content: str, rca: RCA, embedding: list[float]
    ) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                "INSERT INTO memories (incident_id, content, rca, embedding) "
                "VALUES (%s, %s, %s, %s::vector)",
                (incident_id, content, Jsonb(rca.model_dump(mode="json")), _vec(embedding)),
            )

    async def nearest(self, embedding: list[float], k: int) -> list[MemoryRow]:
        async with await self._connect() as conn:
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
