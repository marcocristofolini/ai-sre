"""Contrato de armazenamento (`Store`) e implementação em memória usada nos testes."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from aisre.agent.loop import Alert
from aisre.schemas import RCA

Status = Literal["investigating", "done", "failed"]


class Incident(BaseModel):
    id: int
    fingerprint: str
    alert_name: str
    labels: dict[str, str] = Field(default_factory=dict)
    fired_at: datetime
    created_at: datetime
    status: Status = "investigating"
    error: str | None = None
    rca: RCA | None = None


class MemoryRow(BaseModel):
    incident_id: int
    content: str
    rca: RCA
    distance: float = 0.0


def fingerprint(name: str, labels: dict[str, str]) -> str:
    """Impressão digital estável: nome do alerta + rótulos ordenados."""
    canonical = json.dumps([name, sorted(labels.items())], ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


class Store(Protocol):
    async def create_if_new(self, alert: Alert, window_seconds: int) -> tuple[Incident, bool]:
        """Cria o incidente, a menos que exista outro com a mesma impressão digital na janela.

        Devolve `(incidente, criado)`; em duplicata devolve o existente e `False`."""

    async def complete(self, incident_id: int, rca: RCA) -> None: ...

    async def fail(self, incident_id: int, error: str) -> None: ...

    async def get(self, incident_id: int) -> Incident | None: ...

    async def list(self, limit: int = 100) -> list[Incident]:
        """Mais recentes primeiro."""

    async def add_memory(
        self, incident_id: int, content: str, rca: RCA, embedding: list[float]
    ) -> None: ...

    async def nearest(self, embedding: list[float], k: int) -> list[MemoryRow]:
        """Memórias por distância de cosseno crescente (mais próxima primeiro)."""


def cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - dot / (na * nb)


class MemoryStore:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._incidents: dict[int, Incident] = {}
        self._memories: list[tuple[MemoryRow, list[float]]] = []
        self._lock = asyncio.Lock()

    async def create_if_new(self, alert: Alert, window_seconds: int) -> tuple[Incident, bool]:
        fp = fingerprint(alert.name, alert.labels)
        async with self._lock:
            now = self._clock()
            cutoff = now - timedelta(seconds=window_seconds)
            for inc in reversed(list(self._incidents.values())):
                if inc.fingerprint == fp and inc.created_at >= cutoff:
                    return inc.model_copy(deep=True), False
            inc = Incident(
                id=len(self._incidents) + 1,
                fingerprint=fp,
                alert_name=alert.name,
                labels=dict(alert.labels),
                fired_at=alert.fired_at,
                created_at=now,
            )
            self._incidents[inc.id] = inc
            return inc.model_copy(deep=True), True

    async def complete(self, incident_id: int, rca: RCA) -> None:
        inc = self._incidents[incident_id]
        inc.rca, inc.status, inc.error = rca, "done", None

    async def fail(self, incident_id: int, error: str) -> None:
        inc = self._incidents[incident_id]
        inc.status, inc.error = "failed", error

    async def get(self, incident_id: int) -> Incident | None:
        inc = self._incidents.get(incident_id)
        return inc.model_copy(deep=True) if inc else None

    async def list(self, limit: int = 100) -> list[Incident]:
        ordered = sorted(self._incidents.values(), key=lambda i: i.id, reverse=True)
        return [i.model_copy(deep=True) for i in ordered[:limit]]

    async def add_memory(
        self, incident_id: int, content: str, rca: RCA, embedding: list[float]
    ) -> None:
        self._memories.append((MemoryRow(incident_id=incident_id, content=content, rca=rca), embedding))

    async def nearest(self, embedding: list[float], k: int) -> list[MemoryRow]:
        scored = [
            (cosine_distance(embedding, emb), row) for row, emb in self._memories
        ]
        scored.sort(key=lambda pair: (pair[0], pair[1].incident_id))
        return [row.model_copy(update={"distance": d}) for d, row in scored[:k]]
