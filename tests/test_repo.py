"""I11: prontidão para código aberto (arquivos de raiz, .gitignore, dependências de teste)."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_root_readme_is_honest_about_status():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for needle in (
        "NÃO verificado em cluster real", "Não existe ainda nenhuma medição de precisão",
        "runbooks: não implementado", "Authorization: Bearer", "Licença: Apache-2.0",
        "pytest tests -W error", "npm --prefix web test", "make eval", "deploy/helm/aisre/README.md",
    ):
        assert needle in readme, needle


def test_security_md_has_reporting_and_limitations():
    sec = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "Como reportar" in sec and "Limitações conhecidas" in sec


def test_license_is_apache_2_0_everywhere():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in text and "Version 2.0, January 2004" in text
    assert "END OF TERMS AND CONDITIONS" in text
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["license"] == "Apache-2.0"
    assert json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))["license"] == "Apache-2.0"
    assert "Apache-2.0" in (ROOT / "deploy" / "helm" / "aisre" / "Chart.yaml").read_text(encoding="utf-8")
    assert "Apache-2.0" in (ROOT / "SECURITY.md").read_text(encoding="utf-8")


def test_gitignore_excludes_env_files():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert ".env" in lines and ".env.*" in lines


def test_dev_extras_include_pyyaml():
    dev = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]["dev"]
    assert any(d.lower().startswith("pyyaml") for d in dev)
