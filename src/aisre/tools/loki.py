import httpx

from aisre import strings_pt as t
from aisre.tools.base import ReadOnlyHttpClient, ToolResult, error_result, truncate


class LokiTool:
    name = "loki"
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Consulta LogQL"},
            "limit": {"type": "integer", "description": "Máximo de linhas (padrão 100)"},
        },
        "required": ["query"],
    }

    def __init__(self, base_url: str, timeout: float = 10, transport: httpx.AsyncBaseTransport | None = None):
        self._base = base_url.rstrip("/")
        self._http = ReadOnlyHttpClient(timeout, transport=transport)

    async def call(self, args: dict) -> ToolResult:
        query = str(args.get("query") or "")
        ref = f"log:{query or 'desconhecido'}"
        if not query:
            return error_result(ref, t.ERRO_FERRAMENTA_ARGUMENTO.format(nome="query"))
        try:
            limit = int(args.get("limit") or 100)
            resp = await self._http.get(
                f"{self._base}/loki/api/v1/query_range", params={"query": query, "limit": limit}
            )
            return ToolResult(ok=True, data=truncate(resp.text), error=None, ref=ref)
        except Exception as exc:
            return error_result(ref, exc)
