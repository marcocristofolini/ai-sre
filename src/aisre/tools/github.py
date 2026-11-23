import json
import re

import httpx

from aisre import strings_pt as t
from aisre.tools.base import ReadOnlyHttpClient, ToolResult, error_result, truncate


class GitHubTool:
    name = "github"
    schema = {
        "type": "object",
        "properties": {
            "sha": {"type": "string", "description": "SHA do commit; omitido = commit mais recente"}
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
        sha = str(args.get("sha") or "")
        ref = f"commit:{self._repo}@{sha[:7]}" if sha else f"commit:{self._repo}"
        try:
            if sha:
                if not re.fullmatch(r"[0-9a-fA-F]{4,40}", sha):
                    raise ValueError(t.ERRO_FERRAMENTA_ARGUMENTO.format(nome="sha"))
                url, params = f"{self._api}/repos/{self._repo}/commits/{sha}", None
            else:
                # Sem sha: somente o commit mais recente, para que ref e dados coincidam.
                url, params = f"{self._api}/repos/{self._repo}/commits", {"per_page": 1}
            payload = (await self._http.get(url, params=params)).json()
            commit = payload[0] if isinstance(payload, list) and payload else payload
            if isinstance(commit, dict) and commit.get("sha"):
                ref = f"commit:{self._repo}@{str(commit['sha'])[:7]}"
            return ToolResult(ok=True, data=truncate(json.dumps(commit)), error=None, ref=ref)
        except Exception as exc:
            return error_result(ref, exc)
