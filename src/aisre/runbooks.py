"""Runbooks: roteiros em Markdown escritos pelo operador, oferecidos ao agente como contexto.

Formato (arquivo `.md` no diretório configurado, sem subpastas):

    ---
    titulo: Pod em CrashLoopBackOff
    alertas: KubePodCrashLooping, KubePod*Crash*     # globs do nome do alerta (obrigatório)
    rotulos: namespace=prod-.*, severity=critical    # chave=regex (fullmatch), opcional
    ---
    Corpo em Markdown.

Um runbook diz o que costuma resolver um sintoma; NÃO prova o que acontece agora. Por isso é entregue ao
LLM como dado não confiável, sem id citável (nunca é evidência, não eleva a confiança e não dispensa o
"não sei"). Só arquivos locais são lidos: nenhuma busca na rede.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from aisre import strings_pt as t

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 64 * 1024
TRUNCATED = " [...truncado]"


@dataclass(frozen=True)
class Runbook:
    name: str
    title: str
    body: str
    alerts: tuple[str, ...]
    labels: tuple[tuple[str, str], ...] = ()


def _split_front_matter(text: str) -> tuple[dict[str, str], str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None
    meta: dict[str, str] = {}
    for raw in lines[1:end]:
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip().lower()] = value.strip()
    return meta, "\n".join(lines[end + 1 :]).strip()


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_runbook(name: str, text: str, max_chars: int) -> Runbook | None:
    """None se o arquivo não puder ser selecionado com segurança (sem cabeçalho, sem `alertas`,
    regex inválida ou corpo vazio)."""
    parsed = _split_front_matter(text)
    if parsed is None:
        return None
    meta, body = parsed
    alerts = tuple(_csv(meta.get("alertas", "")))
    if not alerts or not body:
        return None
    labels: list[tuple[str, str]] = []
    for item in _csv(meta.get("rotulos", "")):
        if "=" not in item:
            return None
        key, pattern = (s.strip() for s in item.split("=", 1))
        try:
            re.compile(pattern)
        except re.error:
            return None
        if not key:
            return None
        labels.append((key, pattern))
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + TRUNCATED
    return Runbook(
        name=name, title=meta.get("titulo") or name, body=body, alerts=alerts, labels=tuple(labels)
    )


def load_runbooks(directory: str, max_chars: int = 4000) -> list[Runbook]:
    """Lê `*.md` do diretório (sem subpastas). Arquivos inválidos, grandes demais ou ilegíveis são
    ignorados; o aviso traz só o nome do arquivo e o tipo do erro."""
    if not directory:
        return []
    root = Path(directory)
    if not root.is_dir():
        log.warning(t.AVISO_RUNBOOKS_DIR_INVALIDO)
        return []
    out: list[Runbook] = []
    for path in sorted(root.glob("*.md")):
        try:
            if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                log.warning(t.AVISO_RUNBOOK_IGNORADO.format(nome=path.name, motivo="tamanho ou tipo"))
                continue
            rb = parse_runbook(path.stem, path.read_text(encoding="utf-8"), max_chars)
        except (OSError, UnicodeDecodeError) as exc:
            log.warning(t.AVISO_RUNBOOK_IGNORADO.format(nome=path.name, motivo=type(exc).__name__))
            continue
        if rb is None:
            log.warning(t.AVISO_RUNBOOK_IGNORADO.format(nome=path.name, motivo="formato"))
            continue
        out.append(rb)
    return out


def _matches(rb: Runbook, alert_name: str, labels: dict[str, str]) -> bool:
    name = alert_name.lower()
    if not any(fnmatch.fnmatchcase(name, pattern.lower()) for pattern in rb.alerts):
        return False
    return all(key in labels and re.fullmatch(pattern, labels[key]) for key, pattern in rb.labels)


def select_runbooks(
    runbooks: list[Runbook], alert_name: str, labels: dict[str, str], top_k: int
) -> list[Runbook]:
    """Seleção determinística: mais rótulos exigidos = mais específico; empate por nome."""
    if top_k <= 0:
        return []
    matched = [rb for rb in runbooks if _matches(rb, alert_name, labels)]
    matched.sort(key=lambda rb: (-len(rb.labels), rb.name))
    return matched[:top_k]
