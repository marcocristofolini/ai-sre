"""Redação de segredos antes de qualquer dado sair para um provedor de LLM."""

from __future__ import annotations

import re

from aisre import strings_pt as t

_M = t.MARCADOR_REDIGIDO
_KEY = r"[\w.-]*(?:password|passwd|secret|token|api[_-]?key)[\w-]*"

# (regex, substituição). Ordem importa: padrões específicos antes dos genéricos.
_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+"), rf"\1{_M}"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), _M),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{20,})"), _M),
    (re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+:)[^\s@/]+(@)"), rf"\1{_M}\2"),
    (re.compile(r"(?i)(aws_?secret_?(?:access_?)?key\s*[=:]\s*[\"']?)[A-Za-z0-9/+=]{40}"), rf"\1{_M}"),
    # key=value (env, query-string)
    (re.compile(rf"(?i)(\b{_KEY}[\"']?\s*=\s*[\"']?)[^\s\"'&,;}}]+"), rf"\1{_M}"),
    # "key": "value" (JSON / YAML com aspas)
    (re.compile(rf"(?i)(\b{_KEY}[\"']?\s*:\s*[\"'])[^\"'\n]+"), rf"\1{_M}"),
]


def redact(text: str) -> str:
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text
