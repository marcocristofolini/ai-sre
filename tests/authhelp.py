"""Auxiliar de testes: configura o usuário local e faz login no TestClient."""

from pydantic import SecretStr

from aisre.auth import hash_password
from aisre.config import Settings

USER = "admin"
PASSWORD = "senha-de-teste-123"
SESSION_SECRET = "segredo-de-sessao-de-teste-0123456789"
HASH = hash_password(PASSWORD, n=2**4)  # custo baixo só nos testes
WEBHOOK_TOKEN = "token-do-webhook-de-teste"
WEBHOOK_HEADERS = {"Authorization": f"Bearer {WEBHOOK_TOKEN}"}


def auth_kwargs(**kw):
    base = dict(
        admin_user=USER,
        admin_password_hash=SecretStr(HASH),
        session_secret=SecretStr(SESSION_SECRET),
        session_cookie_secure=False,  # TestClient fala http; o padrão (Secure) é testado à parte
        webhook_token=SecretStr(WEBHOOK_TOKEN),  # o webhook exige token (sem ele: 503)
    )
    base.update(kw)
    return base


def with_auth(settings: Settings, **kw) -> Settings:
    return settings.model_copy(update=auth_kwargs(**kw))


def login(client, password: str = PASSWORD, user: str = USER):
    # O mesmo cliente também fala com o webhook (máquina): leva o token do webhook.
    client.headers.update(WEBHOOK_HEADERS)
    return client.post("/auth/login", json={"username": user, "password": password})
