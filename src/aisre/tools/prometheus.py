import httpx

from aisre import strings_pt as t
from aisre.tools.base import ReadOnlyHttpClient, ToolResult, error_result, truncate


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
