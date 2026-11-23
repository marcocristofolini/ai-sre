"""Auxiliar de testes: configura o usuário local e faz login no TestClient."""

from pydantic import SecretStr

from aisre.auth import hash_password
from aisre.config import Settings

USER = "admin"
PASSWORD = "senha-de-teste-123"
SESSION_SECRET = "segredo-de-sessao-de-teste-0123456789"
HASH = hash_password(PASSWORD, n=2**4)  # custo baixo só nos testes


def auth_kwargs(**kw):
    base = dict(
        admin_user=USER,
        admin_password_hash=SecretStr(HASH),
        session_secret=SecretStr(SESSION_SECRET),
        session_cookie_secure=False,  # TestClient fala http; o padrão (Secure) é testado à parte
    )
    base.update(kw)
    return base


def with_auth(settings: Settings, **kw) -> Settings:
    return settings.model_copy(update=auth_kwargs(**kw))


def login(client, password: str = PASSWORD, user: str = USER):
    return client.post("/auth/login", json={"username": user, "password": password})
