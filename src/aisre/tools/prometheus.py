import httpx
import numpy as np

from aisre import strings_pt as t
from aisre.tools.base import ReadOnlyHttpClient, ToolResult, error_result, truncate

# Limite de pontos por consulta do Prometheus (11000).
MAX_RANGE_POINTS = 11000


class PrometheusTool:
    name = "prometheus"
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Expressão PromQL"}},
        "required": ["query"],
    }

    def __init__(self, base_url: str, timeout: float = 10, transport: httpx.AsyncBaseTransport | None = None):
        self._base = base_url.rstrip("/")
        self._http = ReadOnlyHttpClient(timeout, transport=transport)

    async def call(self, args: dict) -> ToolResult:
        query = str(args.get("query") or "")
        ref = f"metric:{query or 'desconhecida'}"
        if not query:
            return error_result(ref, t.ERRO_FERRAMENTA_ARGUMENTO.format(nome="query"))
        try:
            resp = await self._http.get(f"{self._base}/api/v1/query", params={"query": query})
            return ToolResult(ok=True, data=truncate(resp.text), error=None, ref=ref)
        except Exception as exc:  # nunca propaga
            return error_result(ref, exc)

    async def query_range(self, query: str, start: float, end: float, step: float) -> np.ndarray:
        """Série numérica de uma única série (consulta agregadora) para backfill; somente GET.

        Levanta ValueError se vierem várias séries; levanta em falha de rede/HTTP; NaN/inf permanecem no array (o detector os descarta)."""
        if step <= 0 or end < start or (end - start) / step > MAX_RANGE_POINTS:
            raise ValueError("intervalo/passo inválido para query_range")
        resp = await self._http.get(
            f"{self._base}/api/v1/query_range",
            params={"query": query, "start": start, "end": end, "step": step},
        )
        result = resp.json().get("data", {}).get("result", [])
        if not result:
            return np.empty(0, dtype=np.float64)
        if len(result) > 1:
            raise ValueError("query_range devolveu mais de uma série; use uma consulta agregadora")
        return np.array([float(v[1]) for v in result[0].get("values", [])], dtype=np.float64)
