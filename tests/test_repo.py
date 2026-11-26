"""I11: prontidão para código aberto (arquivos de raiz, .gitignore, dependências de teste)."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_root_readme_is_honest_about_status():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for needle in (
        "NÃO verificado", "medição preliminar e ruidosa", "Resultados medidos",
        "docs/runbooks.md", "AISRE_RUNBOOKS_DIR", "Authorization: Bearer", "Licença: Apache-2.0",
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


def test_ci_workflow_is_least_privilege_and_covers_python_web_helm():
    import yaml

    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(text)
    assert wf["permissions"] == {"contents": "read"}
    assert "${{ secrets." not in text and "pull_request_target" not in text
    assert wf["jobs"]["python"]["strategy"]["matrix"]["python"] == ["3.12", "3.13"]
    uses = [step["uses"] for job in wf["jobs"].values() for step in job["steps"] if "uses" in step]
    assert all(re.fullmatch(r"[\w./-]+@v\d+", u) for u in uses), uses  # tag maior, nunca branch
    assert "azure/setup-helm@v4" in uses
    assert re.search(r"version: v3\.\d+\.\d+", text) and 'node-version: "22"' in text
    for needle in ("pytest tests -W error", "npm --prefix web ci", "npm --prefix web test",
                   "helm lint deploy/helm/aisre", "pytest tests/deploy"):
        assert needle in text, needle


def test_release_workflow_publishes_image_and_chart_safely():
    import yaml

    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(text)
    assert wf["permissions"] == {"contents": "read"}  # escrita só nos jobs que publicam
    on = wf.get(True, wf.get("on"))  # PyYAML lê a chave `on` como True
    assert on == {"push": {"tags": ["v*.*.*"]}}
    assert "pull_request" not in text
    jobs = wf["jobs"]
    assert jobs["imagem"]["permissions"]["packages"] == "write"
    assert jobs["chart"]["permissions"]["packages"] == "write"
    assert "packages" not in jobs["verificar"].get("permissions", {})
    uses = [step["uses"] for job in jobs.values() for step in job.get("steps", []) if "uses" in step]
    uses += [job["uses"] for job in jobs.values() if "uses" in job and not job["uses"].startswith("./")]
    assert all(re.fullmatch(r"[\w./-]+@v\d+", u) for u in uses), uses
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "secrets.GITHUB_TOKEN" in code and code.count("${{ secrets.") == code.count("${{ secrets.GITHUB_TOKEN")
    assert "latest=false" in text and "pull_request_target" not in text
    assert set(jobs["imagem"]["needs"]) >= {"verificar", "testes"}
    assert set(jobs["chart"]["needs"]) >= {"imagem"}
    # o chart reutilizável existe e o CI aceita ser chamado
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    assert "workflow_call" in ci.get(True, ci.get("on"))


def test_chart_default_image_points_to_ghcr_and_versions_agree():
    import yaml

    values = yaml.safe_load((ROOT / "deploy/helm/aisre/values.yaml").read_text(encoding="utf-8"))
    assert values["image"]["repository"] == "ghcr.io/marcocristofolini/ai-sre"
    assert values["image"]["tag"] == ""  # nunca latest; usa appVersion
    chart = yaml.safe_load((ROOT / "deploy/helm/aisre/Chart.yaml").read_text(encoding="utf-8"))
    py = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert chart["appVersion"] == py == chart["version"]
