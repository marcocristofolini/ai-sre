import pytest

from aisre import strings_pt as t
from aisre.llm.redaction import redact

CASES = [
    ("Authorization: Bearer abc.DEF-123_xyz==", "abc.DEF-123_xyz"),
    ("chave AKIAIOSFODNN7EXAMPLE no log", "AKIAIOSFODNN7EXAMPLE"),
    ("aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI"),
    ("AWS_SECRET_ACCESS_KEY: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI"),
    ("postgres://admin:s3cr3tpw@db.local:5432/app", "s3cr3tpw"),
    ("GET /x?user=a&password=hunter2&z=1", "hunter2"),
    ("DB_PASSWD=hunter2", "hunter2"),
    ("client_secret=abcd1234", "abcd1234"),
    ("token=tok_123456", "tok_123456"),
    ("API_KEY=sk-live-999", "sk-live-999"),
    ('{"password": "hunter2", "ok": 1}', "hunter2"),
    ("ghp_" + "a" * 36, "a" * 36),
    ("github_pat_11ABCDEFG0abcdefghij_klmnopqrstuvwxyz0123456789", "11ABCDEFG0"),
]


def test_redact_removes_bearer_tokens():
    out = redact("Authorization: Bearer abc.DEF-123_xyz==")
    assert "abc.DEF" not in out and t.MARCADOR_REDIGIDO in out


def test_redact_removes_aws_access_keys():
    out = redact("key AKIAIOSFODNN7EXAMPLE")
    assert "AKIAIOSFODNN7EXAMPLE" not in out


def test_redact_removes_passwords_in_connection_strings():
    out = redact("postgres://admin:s3cr3tpw@db.local:5432/app")
    assert "s3cr3tpw" not in out
    assert "admin" in out and "db.local" in out


@pytest.mark.parametrize("text,secret", CASES)
def test_redact_patterns(text, secret):
    out = redact(text)
    assert secret not in out
    assert t.MARCADOR_REDIGIDO in out
    assert redact(out) == out


def test_redact_json_keeps_structure():
    out = redact('{"password": "hunter2", "ok": 1}')
    assert '"ok": 1' in out and out.count(chr(34)) == 6


@pytest.mark.parametrize(
    "text",
    [
        "O pod api-7d9 reiniciou 3 vezes por OOMKilled",
        "password reset enviado; token expirado há 5 min",
        "latência p99 subiu de 120ms para 900ms às 14:03",
        "https://example.com/path?a=1&b=2",
        "",
    ],
)
def test_redact_leaves_ordinary_text(text):
    assert redact(text) == text
