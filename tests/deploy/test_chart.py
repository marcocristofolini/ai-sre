"""Testes do chart Helm.

Parte estática (sempre roda): lê values.yaml com PyYAML e inspeciona os templates como texto
(removendo as ações `{{ ... }}` quando se quer YAML). Parte de renderização: chama `helm template` e
é PULADA quando o binário `helm` não existe (motivo explícito), mas é escrita para funcionar quando existir.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from aisre.config import Settings

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "helm" / "aisre"
TEMPLATES = CHART / "templates"

HELM = shutil.which("helm")
needs_helm = pytest.mark.skipif(
    HELM is None, reason="binário `helm` ausente neste ambiente: renderização do chart não verificada aqui"
)

READ_VERBS = {"get", "list", "watch"}
# O que a ferramenta `kubernetes` realmente lê (src/aisre/tools/kubernetes.py):
# read_namespaced_pod, list_namespaced_pod, list_node, list_namespaced_event.
K8S_RESOURCES = {"pods", "nodes", "events"}


def _values() -> dict:
    return yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))


def _template_texts() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(TEMPLATES.glob("*")) if p.suffix in {".yaml", ".tpl", ".txt"}}


def _strip_actions(text: str) -> str:
    """Remove ações de template; linhas que ficam vazias somem."""
    text = re.sub(r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", "", text, flags=re.S)
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"\{\{.*?\}\}", "", line).rstrip()
        if line.strip() and not cleaned.strip():
            continue  # linha só de ação (if/end/include)
        lines.append(cleaned)
    return "\n".join(lines)


def _clusterrole_rules() -> list[dict]:
    text = (TEMPLATES / "clusterrole.yaml").read_text(encoding="utf-8")
    docs = [d for d in yaml.safe_load_all(_strip_actions(text)) if d]
    roles = [d for d in docs if d.get("kind") == "ClusterRole"]
    assert len(roles) == 1
    return roles[0]["rules"]


# ---------------------------------------------------------------- estáticos


def test_chart_files_exist():
    for name in ("Chart.yaml", "values.yaml", "README.md"):
        assert (CHART / name).is_file(), name
    for name in (
        "_helpers.tpl", "NOTES.txt", "deployment.yaml", "service.yaml", "ingress.yaml",
        "serviceaccount.yaml", "clusterrole.yaml", "clusterrolebinding.yaml", "configmap.yaml",
        "secret.yaml", "postgres-statefulset.yaml", "postgres-service.yaml", "networkpolicy.yaml",
    ):
        assert (TEMPLATES / name).is_file(), name


def test_clusterrole_has_only_read_verbs():
    rules = _clusterrole_rules()
    assert rules
    verbs = {v for r in rules for v in r["verbs"]}
    assert verbs == READ_VERBS
    for r in rules:
        assert set(r["verbs"]) <= READ_VERBS


def test_clusterrole_grants_only_what_the_k8s_tool_reads_and_no_secrets():
    rules = _clusterrole_rules()
    resources = {x for r in rules for x in r["resources"]}
    assert resources == K8S_RESOURCES
    assert "secrets" not in resources
    for r in rules:
        assert set(r.get("apiGroups", [""])) == {""}


def test_no_wildcards_or_write_verbs_in_any_template():
    for name, text in _template_texts().items():
        assert not re.search(r"""['"]\*['"]|^\s*-\s*\*\s*$""", text, flags=re.M), f"curinga em {name}"
        for verb in ("create", "update", "patch", "delete", "deletecollection", "escalate", "bind", "impersonate"):
            assert not re.search(rf"""^\s*-\s*['"]?{verb}['"]?\s*$""", text, flags=re.M), f"{verb} em {name}"
    # Nenhum Role/ClusterRole extra além do somente leitura.
    roles = [n for n, t in _template_texts().items() if re.search(r"^kind:\s*(Cluster)?Role\s*$", t, flags=re.M)]
    assert roles == ["clusterrole.yaml"]


def test_clusterrolebinding_is_gated_by_kubernetes_enabled():
    assert _values()["kubernetes"]["enabled"] in (True, False)
    for name in ("clusterrole.yaml", "clusterrolebinding.yaml"):
        assert ".Values.kubernetes.enabled" in (TEMPLATES / name).read_text(encoding="utf-8")


def test_external_consent_defaults_false_in_values():
    v = _values()
    assert v["llm"]["externalConsent"] is False
    assert v["llm"]["provider"] in {"anthropic", "local"}


def test_consent_flag_passes_through_unchanged():
    cm = (TEMPLATES / "configmap.yaml").read_text(encoding="utf-8")
    assert re.search(r"AISRE_LLM_EXTERNAL_CONSENT:\s*\{\{\s*\.Values\.llm\.externalConsent\s*\|\s*quote\s*\}\}", cm)
    # Nenhum template pode forçar o consentimento a true.
    for name, text in _template_texts().items():
        assert not re.search(r"AISRE_LLM_EXTERNAL_CONSENT\W+\"?true", text), name


def test_notes_warn_when_anthropic_without_consent_and_do_not_fail():
    notes = (TEMPLATES / "NOTES.txt").read_text(encoding="utf-8")
    assert re.search(r'eq \.Values\.llm\.provider "anthropic"', notes)
    assert "externalConsent" in notes
    # O app só recusa a investigação em tempo de execução (ConsentRequired); o chart não deve falhar.
    for name, text in _template_texts().items():
        assert "fail" not in notes.lower().replace("falh", "")
        if "externalConsent" in text:
            assert not re.search(r'fail\s+"[^"]*(consent|consentimento)', text, flags=re.I), name


def test_no_secret_in_defaults():
    v = _values()
    assert v["secrets"]["create"] is False
    assert all(val in ("", None) for val in v["secrets"]["values"].values())
    assert v["secrets"]["existingSecret"] == ""
    assert v["postgres"]["auth"]["existingSecret"] == ""
    assert v["postgres"]["external"]["existingSecret"] == ""
    assert v["postgres"]["external"]["url"] == ""

    suspicious = re.compile(r"(sk-ant|xox[bpa]-|ghp_|github_pat_|scrypt\$|://[^/\s:@]+:[^/\s@]+@)", re.I)

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, val in node.items():
                yield from walk(val, f"{path}.{k}")
        elif isinstance(node, list):
            for i, val in enumerate(node):
                yield from walk(val, f"{path}[{i}]")
        else:
            yield path, node

    for path, val in walk(v):
        if isinstance(val, str):
            assert not suspicious.search(val), f"possível credencial em values.yaml: {path}"
        low = path.lower()
        if any(w in low for w in ("password", "token", "apikey", "signingsecret", "sessionsecret", "hash")) and (
            "existingsecret" not in low and not low.endswith("key")
        ):
            assert val in ("", None, False), f"valor padrão não vazio em {path}"


def test_secret_template_only_uses_user_provided_values():
    text = (TEMPLATES / "secret.yaml").read_text(encoding="utf-8")
    assert ".Values.secrets.create" in text
    assert "randAlphaNum" not in text and "genPrivateKey" not in text
    assert not re.search(r"\bdefault\s+\"[^\"]+\"", text), "Secret não pode ter valor padrão"
    assert "b64enc" not in text or ".Values.secrets.values" in text


def test_image_is_pinned_not_latest():
    v = _values()
    assert v["image"]["tag"] != "latest"
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text(encoding="utf-8"))
    assert str(chart["appVersion"]) not in {"", "latest"}
    assert chart["apiVersion"] == "v2" and chart["name"] == "aisre"
    assert v["postgres"]["image"]["tag"] not in ("", "latest")
    assert "pgvector" in v["postgres"]["image"]["repository"]
    deploy = (TEMPLATES / "deployment.yaml").read_text(encoding="utf-8")
    assert ".Chart.AppVersion" in (TEMPLATES / "_helpers.tpl").read_text(encoding="utf-8") + deploy


def test_security_context_hardened_in_defaults_and_templates():
    v = _values()
    assert v["securityContext"]["runAsNonRoot"] is True
    assert v["securityContext"]["readOnlyRootFilesystem"] is True
    assert v["securityContext"]["allowPrivilegeEscalation"] is False
    assert v["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert v["podSecurityContext"]["runAsNonRoot"] is True
    assert v["podSecurityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert v["postgres"]["podSecurityContext"]["runAsNonRoot"] is True
    assert v["postgres"]["securityContext"]["capabilities"]["drop"] == ["ALL"]
    for name in ("deployment.yaml", "postgres-statefulset.yaml"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "securityContext" in text and "resources:" in text, name
    assert v["resources"]["requests"] and v["resources"]["limits"]
    assert v["postgres"]["resources"]["requests"] and v["postgres"]["resources"]["limits"]


def test_probes_use_healthz():
    text = (TEMPLATES / "deployment.yaml").read_text(encoding="utf-8")
    for probe in ("livenessProbe", "readinessProbe"):
        m = re.search(rf"^\s*{probe}:\n\s*httpGet:\n\s*path: /healthz\n\s*port: http\n", text, flags=re.M)
        assert m, probe
    # a porta nomeada "http" existe e é a 8000 em que o uvicorn escuta
    assert re.search(r"- name: http\n\s*containerPort: 8000", text)


def test_configmap_encodes_list_fields_as_json():
    cm = (TEMPLATES / "configmap.yaml").read_text(encoding="utf-8")
    assert re.search(r"AISRE_SLACK_APPROVERS:\s*\{\{\s*\.Values\.slack\.approvers\s*\|\s*toJson\s*\|\s*quote\s*\}\}", cm)
    assert re.search(r"AISRE_PREDICT_WORKLOADS:\s*\{\{\s*\.Values\.predict\.workloads\s*\|\s*toJson\s*\|\s*quote\s*\}\}", cm)
    v = _values()
    assert isinstance(v["slack"]["approvers"], list) and isinstance(v["predict"]["workloads"], list)


def test_local_model_is_only_emitted_when_set_and_notes_warn():
    cm = (TEMPLATES / "configmap.yaml").read_text(encoding="utf-8")
    assert re.search(r"with \.Values\.llm\.local\.model\s*\}\}\s*AISRE_LLM_MODEL:\s*\{\{\s*\.\s*\|\s*quote", cm)
    assert "AISRE_LLM_MODEL: {{ .Values.llm.local.model" not in cm
    notes = (TEMPLATES / "NOTES.txt").read_text(encoding="utf-8")
    assert re.search(r'eq \.Values\.llm\.provider "local"\) \(not \.Values\.llm\.local\.model', notes)


def test_extra_env_guard_rejects_managed_and_secret_like_keys():
    cm = (TEMPLATES / "configmap.yaml").read_text(encoding="utf-8")
    assert re.search(r"has \(upper \$k\) \$managed\s*\}\}\{\{\s*fail", cm), "guard deve normalizar com upper (pydantic-settings e case-insensitive)"
    assert re.search(r'regexMatch "\(\?i\)\(TOKEN\|SECRET\|PASSWORD\|KEY\|HASH\)" \$k\s*\}\}\{\{\s*fail', cm)
    guard = set(re.findall(r'"(AISRE_[A-Z_]+)"', cm))
    set_keys = set(re.findall(r"^\s*(AISRE_[A-Z_]+):", cm, flags=re.M))
    dep = (TEMPLATES / "deployment.yaml").read_text(encoding="utf-8")
    env_keys = set(re.findall(r"name:\s*(AISRE_[A-Z_]+)", dep))
    assert set_keys <= guard and env_keys <= guard
    assert "AISRE_LLM_EXTERNAL_CONSENT" in guard
    assert "extraEnv" in (CHART / "values.yaml").read_text(encoding="utf-8")


def test_ingress_and_networkpolicy_off_by_default_and_ingress_gated():
    v = _values()
    assert v["ingress"]["enabled"] is False
    assert ".Values.ingress.enabled" in (TEMPLATES / "ingress.yaml").read_text(encoding="utf-8")
    assert ".Values.networkPolicy.enabled" in (TEMPLATES / "networkpolicy.yaml").read_text(encoding="utf-8")


def test_cookie_secure_defaults_true():
    assert _values()["auth"]["cookieSecure"] is True


def test_every_aisre_env_var_is_a_real_setting():
    fields = {f.upper() for f in Settings.model_fields}
    found = set()
    for name, text in _template_texts().items():
        found |= set(re.findall(r"\bAISRE_([A-Z0-9_]+)\b", text))
    assert found, "nenhuma variável AISRE_* nos templates"
    assert found <= fields, f"variáveis que o app ignoraria: {sorted(found - fields)}"
    # Os campos centrais precisam estar mapeados.
    for must in (
        "LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_EXTERNAL_CONSENT", "LLM_API_KEY", "DATABASE_URL",
        "PROMETHEUS_URL", "LOKI_URL", "SLACK_APPROVERS", "SLACK_CHANNEL", "SLACK_BOT_TOKEN",
        "SLACK_SIGNING_SECRET", "GITHUB_REPO", "GITHUB_TOKEN", "ADMIN_USER", "ADMIN_PASSWORD_HASH",
        "SESSION_SECRET", "SESSION_COOKIE_SECURE", "PREDICT_WORKLOADS", "KUBERNETES_ENABLED", "WEB_DIST_DIR",
        "WEBHOOK_TOKEN", "WEBHOOK_ALLOW_UNAUTHENTICATED",
    ):
        assert must in found, must


def test_secret_env_vars_come_from_secret_refs_not_configmap():
    cm = (TEMPLATES / "configmap.yaml").read_text(encoding="utf-8")
    for s in ("LLM_API_KEY", "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "GITHUB_TOKEN", "ADMIN_PASSWORD_HASH", "SESSION_SECRET", "DATABASE_URL", "WEBHOOK_TOKEN"):
        assert not re.search(rf"^\s*AISRE_{s}:", cm, flags=re.M), s
    dep = (TEMPLATES / "deployment.yaml").read_text(encoding="utf-8")
    assert dep.count("secretKeyRef") >= 7


def test_postgres_can_be_replaced_by_external():
    assert _values()["postgres"]["enabled"] is True
    for name in ("postgres-statefulset.yaml", "postgres-service.yaml"):
        assert ".Values.postgres.enabled" in (TEMPLATES / name).read_text(encoding="utf-8")


def test_dockerfile_and_dockerignore():
    df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert len(re.findall(r"^FROM ", df, flags=re.M)) >= 2
    for line in re.findall(r"^FROM\s+(\S+)", df, flags=re.M):
        if line in {"web", "builder", "base"}:
            continue
        assert ":" in line and not line.endswith(":latest"), line
    users = re.findall(r"^USER\s+(\S+)", df, flags=re.M)
    assert users and users[-1] not in {"root", "0"}
    assert re.search(r'CMD \["uvicorn", "aisre\.api\.main:create_app", "--factory", "--host", "0\.0\.0\.0", "--port", "8000", "--proxy-headers"\]', df)
    # I6: padrão restritivo de proxies confiáveis embutido na imagem.
    assert re.search(r"^ENV FORWARDED_ALLOW_IPS=127\.0\.0\.1$", df, flags=re.M)
    assert not re.search(r"^\s*(ENV|ARG)\s+\S*(PASSWORD|TOKEN|SECRET|API_KEY)\S*\s*=?\s*\S+", df, flags=re.M | re.I)
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for must in (".venv", "node_modules", ".superpowers", "eval", ".git"):
        assert any(i.strip("/") == must or i.strip("/").endswith(f"/{must}") or i.strip("/") == f"**/{must}" for i in ignore), must


def test_readme_documents_required_topics():
    readme = (CHART / "README.md").read_text(encoding="utf-8")
    for needle in (
        "helm install aisre deploy/helm/aisre", "python -m aisre.auth hash", "python -m aisre.auth secret",
        "/alerts/webhook", "externalConsent", "NetworkPolicy", "NÃO foi verificado",
        # C2: o webhook agora exige token; o README documenta a chave, o curl e o Alertmanager.
        "webhook-token", "Authorization: Bearer", "http_config", "credentials_file", "allowUnauthenticated",
    ):
        assert needle in readme, needle
    assert "está sem autenticação" not in readme  # afirmação antiga, não vale mais


def test_scoreboard_configmap_is_optional_and_mounted_read_only():
    assert _values()["scoreboard"]["configMap"] == ""
    dep = (TEMPLATES / "deployment.yaml").read_text(encoding="utf-8")
    assert "with .Values.scoreboard.configMap" in dep and "mountPath: /app/eval" in dep and "readOnly: true" in dep
    cm = (TEMPLATES / "configmap.yaml").read_text(encoding="utf-8")
    assert 'AISRE_SCOREBOARD_PATH: "/app/eval/scoreboard.json"' in cm and '"AISRE_SCOREBOARD_PATH"' in cm
    readme = (CHART / "README.md").read_text(encoding="utf-8")
    assert "Sem medição ainda" in readme and "não medido" not in readme
    assert "independentemente" in readme and "slack.approvers" in readme


def test_forwarded_allow_ips_value_is_restrictive_and_wired():
    v = _values()
    assert v["proxy"]["forwardedAllowIps"] == "127.0.0.1"
    cm = (TEMPLATES / "configmap.yaml").read_text(encoding="utf-8")
    assert re.search(r"^\s*FORWARDED_ALLOW_IPS:\s*\{\{\s*\.Values\.proxy\.forwardedAllowIps\s*\|\s*quote\s*\}\}", cm, flags=re.M)
    assert '"FORWARDED_ALLOW_IPS"' in cm  # extraEnv não pode redefinir
    readme = (CHART / "README.md").read_text(encoding="utf-8")
    assert "proxy.forwardedAllowIps" in readme and "--proxy-headers" in readme


def test_webhook_token_secret_key_is_wired_without_default():
    v = _values()
    assert v["secrets"]["values"]["webhookToken"] == ""
    assert v["webhook"]["allowUnauthenticated"] is False
    secret = (TEMPLATES / "secret.yaml").read_text(encoding="utf-8")
    assert re.search(r"if \$v\.webhookToken \}\}\s*\n\s*webhook-token: \{\{ \$v\.webhookToken \| quote \}\}", secret)
    dep = (TEMPLATES / "deployment.yaml").read_text(encoding="utf-8")
    assert re.search(
        r"name: AISRE_WEBHOOK_TOKEN\s*\n\s*valueFrom:\s*\n\s*secretKeyRef:\s*\n\s*name: \{\{ include \"aisre\.secretName\" \. \}\}\s*\n\s*key: webhook-token",
        dep,
    )
    cm = (TEMPLATES / "configmap.yaml").read_text(encoding="utf-8")
    assert not re.search(r"^\s*AISRE_WEBHOOK_TOKEN:", cm, flags=re.M)
    assert re.search(r"AISRE_WEBHOOK_ALLOW_UNAUTHENTICATED:\s*\{\{\s*\.Values\.webhook\.allowUnauthenticated\s*\|\s*quote\s*\}\}", cm)
    guard = set(re.findall(r'"(AISRE_[A-Z_]+)"', cm))
    assert {"AISRE_WEBHOOK_TOKEN", "AISRE_WEBHOOK_ALLOW_UNAUTHENTICATED"} <= guard
    notes = (TEMPLATES / "NOTES.txt").read_text(encoding="utf-8")
    assert "Authorization: Bearer" in notes and "NÃO tem autenticação" not in notes
    assert ".Values.webhook.allowUnauthenticated" in notes and ".Values.ingress.enabled" in notes


# ---------------------------------------------------------------- renderização (exige helm)

MIN_ARGS = [
    "--set", "secrets.existingSecret=aisre-secrets",
    "--set", "postgres.auth.existingSecret=aisre-postgres",
]


def _render(*extra: str) -> list[dict]:
    out = subprocess.run(
        [HELM, "template", "aisre", str(CHART), *MIN_ARGS, *extra],
        check=True, capture_output=True, text=True, timeout=60,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


@needs_helm
def test_rendered_clusterrole_has_only_read_verbs():
    roles = [d for d in _render() if d["kind"] == "ClusterRole"]
    assert len(roles) == 1
    for rule in roles[0]["rules"]:
        assert set(rule["verbs"]) <= READ_VERBS
        assert "secrets" not in rule["resources"] and "*" not in rule["resources"]


@needs_helm
def test_no_secret_in_rendered_defaults():
    docs = _render()
    assert not [d for d in docs if d["kind"] == "Secret"]
    cm = next(d for d in docs if d["kind"] == "ConfigMap")
    assert cm["data"]["AISRE_LLM_EXTERNAL_CONSENT"] == "false"
    blob = yaml.safe_dump(docs)
    assert not re.search(r"(sk-ant|xox[bp]-|ghp_|scrypt\$)", blob)


@needs_helm
def test_rendered_secret_only_from_user_values():
    docs = _render("--set", "secrets.create=true", "--set", "secrets.values.sessionSecret=abc")
    secret = next(d for d in docs if d["kind"] == "Secret" and d["metadata"]["name"].endswith("secrets"))
    assert list(secret.get("stringData", {}) or secret.get("data", {})) == ["session-secret"]


@needs_helm
def test_rendered_external_db_skips_statefulset():
    docs = _render("--set", "postgres.enabled=false", "--set", "postgres.external.existingSecret=ext-db")
    assert not [d for d in docs if d["kind"] == "StatefulSet"]


@needs_helm
def test_rendered_extra_env_guard_fails():
    for bad in ("AISRE_LLM_EXTERNAL_CONSENT", "aisre_llm_external_consent", "Aisre_Database_Url", "MY_API_TOKEN",
                "FORWARDED_ALLOW_IPS"):
        r = subprocess.run(
            [HELM, "template", "aisre", str(CHART), *MIN_ARGS, "--set-string", f"extraEnv.{bad}=x"],
            capture_output=True, text=True, timeout=60,
        )
        assert r.returncode != 0, bad


@needs_helm
def test_rendered_lists_are_json_and_local_model_omitted_when_empty():
    docs = _render("--set", "slack.approvers={U1}")
    cm = next(d for d in docs if d["kind"] == "ConfigMap")
    assert cm["data"]["AISRE_SLACK_APPROVERS"] == '["U1"]'
    assert cm["data"]["AISRE_PREDICT_WORKLOADS"] == "[]"
    assert "AISRE_LLM_MODEL" not in cm["data"]


@needs_helm
def test_rendered_webhook_token_and_allow_unauthenticated_default_false():
    docs = _render()
    cm = next(d for d in docs if d["kind"] == "ConfigMap")
    assert cm["data"]["AISRE_WEBHOOK_ALLOW_UNAUTHENTICATED"] == "false"
    dep = next(d for d in docs if d["kind"] == "Deployment")
    env = {e["name"]: e for e in dep["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["AISRE_WEBHOOK_TOKEN"]["valueFrom"]["secretKeyRef"]["key"] == "webhook-token"
    r = subprocess.run(
        [HELM, "template", "aisre", str(CHART), *MIN_ARGS, "--set-string", "extraEnv.AISRE_WEBHOOK_ALLOW_UNAUTHENTICATED=true"],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode != 0
