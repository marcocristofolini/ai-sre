import json

import httpx

from aisre.tools.base import ReadOnlyHttpClient, ToolResult, error_result, truncate


class GitHubTool:
    name = "github"
    schema = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Quantidade de commits recentes (padrão 5)"}
        },
    }

    def __init__(
        self,
        repo: str,
        token: str,
        timeout: float = 10,
        api_url: str = "https://api.github.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._repo = repo
        self._api = api_url.rstrip("/")
        headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"}
        self._http = ReadOnlyHttpClient(timeout, headers=headers, transport=transport)

    async def call(self, args: dict) -> ToolResult:
        ref = f"commit:{self._repo}"
        try:
            limit = int(args.get("limit") or 5)
            resp = await self._http.get(
                f"{self._api}/repos/{self._repo}/commits", params={"per_page": limit}
            )
            commits = resp.json()
            if isinstance(commits, list) and commits and isinstance(commits[0], dict):
                ref = f"commit:{self._repo}@{str(commits[0].get('sha', ''))[:7]}"
            return ToolResult(ok=True, data=truncate(json.dumps(commits)), error=None, ref=ref)
        except Exception as exc:
            return error_result(ref, exc)
