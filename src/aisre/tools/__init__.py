from aisre.config import Settings
from aisre.tools.base import MAX_OUTPUT_CHARS, Tool, ToolResult
from aisre.tools.github import GitHubTool
from aisre.tools.kubernetes import KubernetesTool
from aisre.tools.loki import LokiTool
from aisre.tools.prometheus import PrometheusTool

__all__ = ["MAX_OUTPUT_CHARS", "Tool", "ToolResult", "build_tools"]


def build_tools(settings: Settings) -> list[Tool]:
    """Constrói apenas as ferramentas configuradas."""
    timeout = settings.tool_timeout_seconds
    tools: list[Tool] = []
    if settings.prometheus_url:
        tools.append(PrometheusTool(settings.prometheus_url, timeout=timeout))
    if settings.loki_url:
        tools.append(LokiTool(settings.loki_url, timeout=timeout))
    token = settings.github_token.get_secret_value()
    if token and settings.github_repo:
        tools.append(
            GitHubTool(settings.github_repo, token, timeout=timeout, api_url=settings.github_api_url)
        )
    if settings.kubernetes_enabled:
        tools.append(KubernetesTool(timeout=timeout))
    return tools
