"""Redação de segredos antes de qualquer dado sair para um provedor de LLM.

Os padrões evitam prefixos ilimitados (sem backtracking quadrático): a palavra
sensível é localizada diretamente e só sufixos curtos e limitados são consumidos.
"""

from __future__ import annotations

import re

from aisre import strings_pt as t

_M = t.MARCADOR_REDIGIDO
_W = r"(?:password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key|access[_-]?key)"
_KEY = _W + r"[\w-]{0,40}"
_DQ = r'"(?:[^"\\\n]|\\.)*"'
_SQ = r"'(?:[^'\\\n]|\\.)*'"
_F = re.IGNORECASE


def _rule(pattern: str, repl: str, flags: int = 0) -> tuple[re.Pattern[str], str]:
    return re.compile(pattern, flags), repl


# Ordem importa: padrões específicos antes dos genéricos.
_RULES: list[tuple[re.Pattern[str], str]] = [
    # Chaves privadas PEM (inclusive truncadas, sem END)
    _rule(
        r"-----BEGIN [A-Z ]{0,30}PRIVATE KEY-----(?:[\s\S]*?-----END [A-Z ]{0,30}PRIVATE KEY-----|[\s\S]*)",
        _M,
    ),
    # Authorization: Basic/Bearer/Token/Digest <credencial>
    _rule(r"(\bAuthorization[\"']?\s*[:=]\s*[\"']?(?:Basic|Bearer|Token|Digest)\s+)[^\s\"']+", rf"\1{_M}", _F),
    _rule(r"(\bBearer\s+)[A-Za-z0-9._~+/=-]+", rf"\1{_M}", _F),
    _rule(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", _M),
    _rule(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{20,})", _M),
    _rule(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}", _M),
    # scheme://user:senha@host (usuário pode ser vazio; senha pode conter '@')
    _rule(r"\b([a-zA-Z][a-zA-Z0-9+.-]{0,30}://[^\s:/@]*:)[^\s/]*@", rf"\1{_M}@"),
    _rule(r"(aws_?secret_?(?:access_?)?key\s*[=:]\s*[\"']?)[A-Za-z0-9/+=]{40}", rf"\1{_M}", _F),
    # Cookies e cabeçalhos x-*-key/token/secret/auth: valor até o fim da linha
    _rule(r"\b((?:set-)?cookie\s*:\s*)[^\r\n]+", rf"\1{_M}", _F),
    _rule(r"\b(x-[\w-]{0,30}(?:key|token|secret|auth)[\w-]{0,20}\s*:\s*)[^\r\n]+", rf"\1{_M}", _F),
    # chave = / : "valor entre aspas" (aceita espaços e aspas escapadas)
    _rule(rf"({_KEY}[\"']?\s*[=:]\s*){_DQ}", rf'\1"{_M}"', _F),
    _rule(rf"({_KEY}[\"']?\s*[=:]\s*){_SQ}", rf"\1'{_M}'", _F),
    # JSON com valor numérico/booleano
    _rule(rf"({_KEY}\"\s*:\s*)(?:-?\d[\w.+-]*|true|false)", rf'\1"{_M}"', _F),
    # chave=valor sem aspas (env, query-string)
    _rule(rf"({_KEY}[\"']?\s*=\s*[\"']?)[^\s\"'&,;}}]+", rf"\1{_M}", _F),
    # YAML/log com ':' sem aspas, só no início da linha e com valor sem espaços (>= 6)
    _rule(
        rf"^([ \t]*(?:-[ \t]+)?[\w.-]{{0,40}}?{_KEY}[ \t]*:[ \t]*)(?![\"'])[^\s]{{6,}}[ \t]*$",
        rf"\1{_M}",
        _F | re.MULTILINE,
    ),
]


def redact(text: str) -> str:
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text
