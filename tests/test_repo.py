"""I11: prontidão para código aberto (arquivos de raiz, .gitignore, dependências de teste)."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_root_readme_is_honest_about_status():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for needle in (
        "NÃO verificado em cluster real", "Não existe ainda nenhuma medição de precisão",
        "ainda não é usada pelo agente", "Authorization: Bearer", "Licença: a definir",
        "pytest tests -W error", "npm --prefix web test", "make eval", "deploy/helm/aisre/README.md",
    ):
        assert needle in readme, needle


def test_security_md_has_reporting_and_limitations():
    sec = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "Como reportar" in sec and "Limitações conhecidas" in sec


def test_no_license_file_yet():
    # Decisão do mantenedor: licença a definir; nenhum arquivo de licença é adicionado.
    assert not list(ROOT.glob("LICENSE*")) and not list(ROOT.glob("COPYING*"))


def test_gitignore_excludes_env_files():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert ".env" in lines and ".env.*" in lines


def test_dev_extras_include_pyyaml():
    dev = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]["dev"]
    assert any(d.lower().startswith("pyyaml") for d in dev)
