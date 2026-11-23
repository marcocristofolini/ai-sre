import asyncio
import math

from aisre.db import MemoryStore
from aisre.memory import EMBEDDING_DIM, HashingEmbedder, Memory
from aisre.schemas import RCA, Evidence


def _rca(cause: str, summary: str) -> RCA:
    return RCA(
        cause=cause,
        confidence=0.8,
        evidence=[Evidence(kind="log", ref="log:x", summary=summary)],
    )


def test_hashing_embedder_is_deterministic_and_normalized():
    e = HashingEmbedder()
    a, b = e.embed("pod crashloop oomkilled"), e.embed("pod crashloop oomkilled")
    assert a == b and len(a) == EMBEDDING_DIM
    assert math.isclose(sum(x * x for x in a), 1.0, rel_tol=1e-9)
    assert e.embed("") == [0.0] * EMBEDDING_DIM


def test_similar_returns_past_incident_ordered_by_distance():
    async def run():
        memory = Memory(MemoryStore())
        await memory.save(1, _rca("Pod morto por OOMKilled", "container excedeu o limite de memória"))
        await memory.save(2, _rca("Certificado TLS expirado", "handshake falhou certificado vencido"))
        await memory.save(3, _rca("Disco cheio no nó", "filesystem sem espaço"), runbook_text="limpar logs")
        return await memory.similar("OOMKilled limite de memória do container", k=3)

    result = asyncio.run(run())
    assert [p.incident_id for p in result][0] == 1
    assert [p.distance for p in result] == sorted(p.distance for p in result)
    assert len(result) == 3
    assert result[0].rca.cause == "Pod morto por OOMKilled"


def test_similar_respects_k_and_empty_store():
    async def run():
        memory = Memory(MemoryStore())
        assert await memory.similar("qualquer") == []
        await memory.save(1, _rca("a b", "c d"))
        await memory.save(2, _rca("e f", "g h"))
        return await memory.similar("a b", k=1)

    assert len(asyncio.run(run())) == 1


def test_similar_excludes_current_incident_and_zero_k():
    async def run():
        memory = Memory(MemoryStore())
        await memory.save(1, _rca("Pod morto por OOMKilled", "limite de memória"))
        await memory.save(2, _rca("Pod morto por OOMKilled", "limite de memória"))
        assert await memory.similar("OOMKilled", k=0) == []
        return await memory.similar("OOMKilled", k=1, exclude_incident_id=1)

    assert [p.incident_id for p in asyncio.run(run())] == [2]
