"""Memória de incidentes: embeddings locais e busca por similaridade sobre um `Store`."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from pydantic import BaseModel

from aisre.db import Store
from aisre.schemas import RCA

EMBEDDING_DIM = 256  # deve coincidir com vector(256) em migrations/0001_init.sql

_TOKEN = re.compile(r"\w+", re.UNICODE)


class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Embedding determinístico por hashing de tokens; local, sem rede, normalizado (L2)."""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            vec[value % self.dim] += 1.0 if (value >> 63) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0:
            return vec
        return [x / norm for x in vec]


class PastIncident(BaseModel):
    incident_id: int
    content: str
    rca: RCA
    distance: float


def _content(rca: RCA, runbook_text: str | None) -> str:
    parts = [rca.cause or ""]
    parts += [e.summary for e in rca.evidence]
    if rca.suggested_command:
        parts.append(rca.suggested_command)
    if runbook_text:
        parts.append(runbook_text)
    return "\n".join(p for p in parts if p)


class Memory:
    def __init__(self, store: Store, embedder: Embedder | None = None) -> None:
        self._store = store
        self._embedder = embedder or HashingEmbedder()

    async def save(self, incident_id: int, rca: RCA, runbook_text: str | None = None) -> None:
        content = _content(rca, runbook_text)
        await self._store.add_memory(incident_id, content, rca, self._embedder.embed(content))

    async def similar(self, query: str, k: int = 3) -> list[PastIncident]:
        rows = await self._store.nearest(self._embedder.embed(query), k)
        return [
            PastIncident(
                incident_id=r.incident_id, content=r.content, rca=r.rca, distance=r.distance
            )
            for r in rows
        ]
