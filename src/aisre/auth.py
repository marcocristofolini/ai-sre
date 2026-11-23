"""Autenticação local: hash de senha (scrypt, só stdlib), sessão assinada e limitação de tentativas.

Formato do hash: ``scrypt$n$r$p$salt_hex$hash_hex`` (n = custo, potência de 2; r = bloco; p = paralelismo).
Gerar: ``python -m aisre.auth hash`` (lê a senha sem eco; nada é gravado em disco ou em log).
Sessão: cookie ``base64url(json{u,exp}).hex(HMAC-SHA256(segredo, base64url))``.
"""

from __future__ import annotations

import base64
import binascii
import getpass
import hashlib
import hmac
import json
import secrets
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from urllib.parse import urlparse

from fastapi import HTTPException, Request

from aisre import strings_pt as t

COOKIE_NAME = "aisre_session"
DEFAULT_N, DEFAULT_R, DEFAULT_P = 2**15, 8, 1
MAX_TOKEN_CHARS = 2048
_MAX_SCRYPT_BYTES = 256 * 1024 * 1024
_DKLEN = 32


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=dklen, maxmem=_MAX_SCRYPT_BYTES * 2
    )


def hash_password(
    password: str, *, n: int = DEFAULT_N, r: int = DEFAULT_R, p: int = DEFAULT_P, salt: bytes | None = None
) -> str:
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = _scrypt(password, salt, n, r, p, _DKLEN)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """False para qualquer hash malformado ou parâmetros fora dos limites (nunca levanta)."""
    try:
        kind, n_s, r_s, p_s, salt_hex, hash_hex = stored.split("$")
        n, r, p = int(n_s), int(r_s), int(p_s)
        if kind != "scrypt" or n < 2 or n & (n - 1) or not 1 <= r <= 32 or not 1 <= p <= 16:
            return False
        if 128 * n * r > _MAX_SCRYPT_BYTES:
            return False
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(hash_hex)
        if not salt or not 16 <= len(expected) <= 128:
            return False
        actual = _scrypt(password, salt, n, r, p, len(expected))
    except (ValueError, TypeError, OverflowError, MemoryError):
        return False
    return hmac.compare_digest(actual, expected)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def sign_session(secret: str, user: str, now: float, ttl_seconds: int) -> str:
    payload = _b64(json.dumps({"u": user, "exp": int(now) + ttl_seconds}, separators=(",", ":")).encode())
    return f"{payload}.{_mac(secret, payload)}"


def read_session(secret: str, token: str | None, now: float) -> str | None:
    """Usuário da sessão válida ou None (segredo vazio, assinatura errada, expirada, malformada)."""
    if not secret or not token or len(token) > MAX_TOKEN_CHARS:
        return None
    try:
        payload, sig = token.split(".")
        if not hmac.compare_digest(_mac(secret, payload), sig):
            return None
        data = json.loads(_unb64(payload))
        user, exp = data["u"], data["exp"]
    except (ValueError, KeyError, TypeError, binascii.Error):
        return None
    if not isinstance(user, str) or isinstance(exp, bool) or not isinstance(exp, int) or exp <= now:
        return None
    return user


class LoginThrottle:
    """Contador de falhas por IP e por usuário; excedido o limite, bloqueia por `lockout_seconds`.

    Em memória (por processo) e com relógio injetável."""

    def __init__(
        self,
        max_per_ip: int = 5,
        max_per_user: int = 20,
        window_seconds: float = 900.0,
        lockout_seconds: float = 900.0,
        clock: Callable[[], float] = time.time,
        max_entries: int = 10_000,
    ) -> None:
        self._limits = {"ip": max_per_ip, "user": max_per_user}
        self._window, self._lockout, self._clock, self._max = window_seconds, lockout_seconds, clock, max_entries
        self._state: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0, 0.0, 0.0])
        # valor: [falhas, início da janela, bloqueado até]

    def _keys(self, ip: str, user: str) -> list[tuple[str, str]]:
        return [("ip", ip), ("user", user.casefold()[:128])]

    def retry_after(self, ip: str, user: str) -> int:
        now = self._clock()
        wait = 0.0
        for key in self._keys(ip, user):
            st = self._state.get(key)
            if st and st[2] > now:
                wait = max(wait, st[2] - now)
        return int(wait) + 1 if wait > 0 else 0

    def failure(self, ip: str, user: str) -> None:
        now = self._clock()
        if len(self._state) >= self._max:
            self._prune(now)
        for key in self._keys(ip, user):
            st = self._state[key]
            if now - st[1] > self._window:
                st[0], st[1] = 0, now
            st[0] += 1
            if st[0] >= self._limits[key[0]]:
                st[2] = now + self._lockout

    def success(self, ip: str, user: str) -> None:
        for key in self._keys(ip, user):
            self._state.pop(key, None)

    def _prune(self, now: float) -> None:
        for key in [k for k, st in self._state.items() if st[2] <= now and now - st[1] > self._window]:
            del self._state[key]


def now_of(request: Request) -> float:
    return request.app.state.clock()


async def require_session(request: Request) -> str:
    """Dependência: devolve o usuário da sessão ou responde 401 (também sem configuração)."""
    settings = request.app.state.settings
    user = read_session(
        settings.session_secret.get_secret_value(), request.cookies.get(COOKIE_NAME), now_of(request)
    )
    if user is None or not settings.admin_user or user != settings.admin_user:
        raise HTTPException(status_code=401, detail=t.ERRO_NAO_AUTENTICADO)
    return user


async def require_same_origin(request: Request) -> None:
    """Defesa extra contra CSRF (além de SameSite=Lax): se há Origin, deve ser o próprio host."""
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host", ""):
        raise HTTPException(status_code=403, detail=t.ERRO_ORIGEM_INVALIDA)


def main(argv: list[str]) -> int:
    if argv[:1] == ["hash"]:
        pw = getpass.getpass("Senha: ")
        if not pw or pw != getpass.getpass("Repita a senha: "):
            print(t.ERRO_CLI_SENHA, file=sys.stderr)
            return 2
        print(hash_password(pw))
        return 0
    if argv[:1] == ["secret"]:
        print(secrets.token_urlsafe(32))
        return 0
    print(t.USO_CLI_AUTH, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
