"""Publicação do RCA no Slack. O texto do RCA vem de um LLM lendo logs não confiáveis:
todo texto dinâmico é escapado para não injetar menções, links ou fechar blocos de código."""

from __future__ import annotations

import logging
import re
import unicodedata

import httpx

from aisre import strings_pt as t
from aisre.config import Settings
from aisre.schemas import RCA

log = logging.getLogger(__name__)

APPROVE_ACTION_ID = "aisre_approve"
MAX_EVIDENCE = 5
SECTION_LIMIT = 3000  # limite do Slack para texto de bloco de seção
MESSAGE_TEXT_LIMIT = 3000
TIMEOUT_SECONDS = 10.0
_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


def escape(text: str) -> str:
    """Neutraliza os caracteres de controle do mrkdwn do Slack (`&`, `<`, `>`)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _oneline(text: str) -> str:
    return " ".join(text.split())


def fit(text: str, limit: int) -> str:
    """Escapa e trunca a `limit` caracteres sem cortar uma entidade (`&amp;`) ao meio."""
    raw = text
    while True:
        out = escape(raw)
        if len(out) <= limit:
            return out
        # corta o texto bruto (nunca o escapado) e marca a truncagem
        raw = raw[: max(0, min(len(raw) - 1, int(len(raw) * limit / len(out)) - 1))]
        if len(escape(raw + "…")) <= limit:
            return escape(raw + "…")


CODE_LIMIT = 2900  # cabe no limite de 3000 do bloco junto com as crases


# Controle (Cc, inclusive \r, \n e ESC), formatação invisível (Cf: bidi, zero-width, BOM),
# separadores de linha/parágrafo, uso privado, substitutos e não atribuídos.
_HIDDEN_CATEGORIES = {"Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn"}


def _has_hidden_chars(text: str) -> bool:
    return any(unicodedata.category(ch) in _HIDDEN_CATEGORIES for ch in text)


def command_block_reason(command: str) -> str | None:
    """Por que o comando não pode ser aprovado: "controle" (quebra de linha/caracteres de controle,
    invisíveis ou bidi: o texto exibido diferiria do executado), "crase" (fecharia o bloco de código),
    "longo" (não cabe por inteiro no bloco) ou None se pode. Uma quebra de linha simples continua
    recusada: um `\\n` num comando de shell separa comandos, e a aprovação é sobre o texto inteiro."""
    if _has_hidden_chars(command):
        return "controle"
    if "`" in command:
        return "crase"
    if len(escape(command)) > CODE_LIMIT:
        return "longo"
    return None


def command_is_showable(command: str) -> bool:
    """O comando só pode ser aprovado se o aprovador o vê por inteiro e sem alterações."""
    return command_block_reason(command) is None


NOT_APPROVABLE_SLACK = {
    "controle": t.SLACK_COMANDO_NAO_APROVAVEL_CONTROLE,
    "crase": t.SLACK_COMANDO_NAO_APROVAVEL_CRASE,
    "longo": t.SLACK_COMANDO_NAO_APROVAVEL_LONGO,
}


# Lista de leitura: só marca o comando como "somente leitura" (sinal visual), não autoriza nada.
_READ_ONLY = re.compile(r"kubectl[ ]+(?:get|describe|logs|top)(?:[ ]+[A-Za-z0-9_./:=,@%+\-\[\]{}*'\"]+)*[ ]*")
_SHELL_META = set(";&|`$<>()\\\n\r")


def command_is_read_only(command: str) -> bool:
    """True se o comando é `kubectl get|describe|logs|top` sem metacaracteres de shell."""
    cmd = command.strip()
    return (
        not _has_hidden_chars(cmd)
        and not any(ch in _SHELL_META for ch in cmd)
        and _READ_ONLY.fullmatch(cmd) is not None
    )


def _code(command: str) -> str:
    return f"```{escape(command)}```"


def _mrkdwn(text: str) -> dict:
    # verbatim: o Slack não transforma URLs, canais e menções do texto em links.
    return {"type": "section", "text": {"type": "mrkdwn", "text": text, "verbatim": True}}


def build_message(incident_id: int, rca: RCA) -> dict:
    unknown = rca.unknown
    title = (t.SLACK_TITULO_DESCONHECIDO if unknown else t.SLACK_TITULO_RCA).format(id=incident_id)
    blocks: list[dict] = [{"type": "header", "text": {"type": "plain_text", "text": title[:150]}}]
    head = []
    if unknown:
        head.append(t.SLACK_DESCONHECIDO_CORPO)
    else:
        head.append(t.SLACK_CAUSA.format(causa=fit(_oneline(rca.cause or ""), 1000)))
    head.append(t.SLACK_CONFIANCA.format(confianca=round(rca.confidence * 100)))
    blocks.append(_mrkdwn("\n".join(head)[:SECTION_LIMIT]))

    lines = [t.SLACK_EVIDENCIAS if not unknown else ""]
    shown = rca.evidence[:MAX_EVIDENCE]
    for ev in shown:
        lines.append(
            t.SLACK_EVIDENCIA_LINHA.format(
                tipo=escape(ev.kind),
                ref=fit(_oneline(ev.ref), 100),
                resumo=fit(_oneline(ev.summary), 200),
            )
        )
    if not shown:
        lines.append(t.SLACK_SEM_EVIDENCIAS)
    if len(rca.evidence) > len(shown):
        lines.append(t.SLACK_EVIDENCIAS_OMITIDAS.format(n=len(rca.evidence) - len(shown)))
    text = "\n".join(line for line in lines if line)
    blocks.append(_mrkdwn(text[:SECTION_LIMIT]))

    command = rca.suggested_command
    if not unknown and command:
        if command_is_showable(command):
            blocks.append(_mrkdwn(t.SLACK_COMANDO))
            blocks.append(_mrkdwn(_code(command)))
            aviso = [t.AVISO_COMANDO_GERADO_POR_IA]
            if not command_is_read_only(command):
                aviso.append(t.AVISO_COMANDO_FORA_LISTA_LEITURA)
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": " ".join(aviso), "verbatim": True}]})
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": APPROVE_ACTION_ID,
                            "text": {"type": "plain_text", "text": t.SLACK_BOTAO_APROVAR},
                            "style": "primary",
                            "value": str(incident_id),  # só o id: o comando vem do RCA salvo
                        }
                    ],
                }
            )
        else:
            blocks.append(_mrkdwn(NOT_APPROVABLE_SLACK.get(command_block_reason(command) or "", t.SLACK_COMANDO_NAO_EXIBIVEL)))
    return {"text": title[:MESSAGE_TEXT_LIMIT], "blocks": blocks}


def _safe_code(value: object) -> str:
    return value if isinstance(value, str) and _ERROR_CODE.match(value) else t.SLACK_CODIGO_DESCONHECIDO


async def post_rca(
    channel: str,
    incident_id: int,
    rca: RCA,
    *,
    token: str | None,
    api_url: str = "https://slack.com/api",
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = TIMEOUT_SECONDS,
) -> None:
    """Sem token ou canal é no-op. Falhas são registradas só por tipo/código e nunca propagam."""
    if not token or not channel:
        return
    payload = {"channel": channel, "unfurl_links": False, "unfurl_media": False, **build_message(incident_id, rca)}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            resp = await client.post(
                f"{api_url.rstrip('/')}/chat.postMessage",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code != 200:
            log.warning(t.SLACK_AVISO_HTTP.format(status=resp.status_code))
            return
        data = resp.json()
        if not isinstance(data, dict) or data.get("ok") is not True:
            code = data.get("error") if isinstance(data, dict) else None
            log.warning(t.SLACK_AVISO_ERRO_API.format(codigo=_safe_code(code)))
    except Exception as exc:  # noqa: BLE001 - só o tipo: a mensagem pode conter segredos
        log.warning(t.SLACK_AVISO_FALHA_API.format(tipo=type(exc).__name__))


def make_hook(settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
    """Hook `on_rca_ready` ligado à configuração deste app."""

    async def hook(incident_id: int, rca: RCA) -> None:
        token = settings.slack_bot_token.get_secret_value() if settings.slack_bot_token else None
        await post_rca(
            settings.slack_channel,
            incident_id,
            rca,
            token=token,
            api_url=settings.slack_api_url,
            transport=transport,
        )

    return hook
