"""Perfil `make eval-full`: só estático. NÃO foi executado (sem helm/kind/Docker)."""

import re
from pathlib import Path

import yaml

from aisre.config import Settings
from aisre.tools import build_tools

ROOT = Path(__file__).resolve().parents[2]


def test_enabled_tools_follow_real_configuration():
    names = lambda **kw: {t.name for t in build_tools(Settings(_env_file=None, **kw))}  # noqa: E731
    assert names() == set()
    assert names(prometheus_url="http://127.0.0.1:19090", loki_url="http://127.0.0.1:13100") == {"prometheus", "loki"}
    assert names(prometheus_url="http://127.0.0.1:19090") == {"prometheus"}


def test_eval_target_is_unchanged_and_eval_full_is_separate():
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    eval_body = re.search(r"^eval:\n((?:\t.*\n?)+)", make, re.M).group(1)
    assert "helm" not in eval_body and "AISRE_PROMETHEUS_URL" not in eval_body
    assert re.search(r"^eval-full:\n\t.*harness/eval-full\.sh", make, re.M)


def test_eval_full_script_has_no_invented_versions_and_sets_urls_after_readiness():
    sh = (ROOT / "harness" / "eval-full.sh").read_text(encoding="utf-8")
    for var in ("KPS_CHART_VERSION", "LOKI_CHART_VERSION", "PROMTAIL_CHART_VERSION"):
        assert f': "${{{var}:?' in sh
    assert "NÃO VERIFICADO" in sh and "set -euo pipefail" in sh
    assert sh.index('wait_http "http://127.0.0.1:$LOKI_PORT/ready"') < sh.index("AISRE_PROMETHEUS_URL=")
    assert not re.search(r"--version\s+\d", sh)


def test_eval_full_values_files_parse():
    for name in ("kube-prometheus-stack", "loki", "promtail"):
        data = yaml.safe_load((ROOT / "harness" / "full" / f"{name}-values.yaml").read_text(encoding="utf-8"))
        assert isinstance(data, dict) and data
    assert yaml.safe_load((ROOT / "harness" / "full" / "kube-prometheus-stack-values.yaml").read_text())["fullnameOverride"] == "kps"
