"""I8 (validade do `make eval`) e I9 (nunca injetar fora do cluster kind dedicado)."""

import functools
import json
import subprocess

import pytest
import yaml

from aisre.harness import runner
from aisre.harness.runner import (
    KIND_CONTEXT,
    InjectionHandle,
    SettleTimeout,
    check_target_cluster,
    run_all,
    wait_for,
)
from aisre.harness.scenarios import SCENARIOS, ClusterCheck, execution_order
from aisre.harness.scoring import Scoreboard, cause_matches, grade
from aisre.schemas import RCA, Evidence

BY_ID = {s.id: s for s in SCENARIOS}


def _rca(cause, ref):
    return RCA(cause=cause, confidence=0.8, evidence=[Evidence(kind=ref.split(":")[0], ref=ref, summary="x")])


# ------------------------------------------------------------ (a) sinônimos


@pytest.mark.parametrize(
    "scenario_id,cause",
    [
        ("oom-kill", "O contêiner foi encerrado por OOM (OOMKilled) ao exceder o limite de memória"),
        ("oom-kill", "Out of memory no pod worker"),
        ("crashloop-bad-config", "O app entra em CrashLoopBackOff porque o arquivo de configuração é inválido"),
        ("bad-deploy-latency", "O último deploy elevou a latência p99"),
        ("node-disk-pressure", "O nó está com DiskPressure (disco cheio)"),
        ("dns-failure", "Falhas de DNS: o resolvedor configurado não responde"),
        ("memory-leak-gradual", "Vazamento de memória: uso cresce até o limite"),
        ("latency-creep", "A latência vem crescendo gradualmente sem deploy"),
    ],
)
def test_accepted_synonyms_count_as_correct(scenario_id, cause):
    s = BY_ID[scenario_id]
    assert cause_matches(cause, s)
    assert grade(_rca(cause, s.expected_evidence_refs[0] + "x"), s)


@pytest.mark.parametrize(
    "scenario_id,cause",
    [
        ("oom-kill", "Problema no room de chat"),  # 'oom' só em início de palavra
        ("oom-kill", "Falha de rede"),
        ("crashloop-bad-config", "CrashLoopBackOff"),  # sem a causa (configuração)
        ("bad-deploy-latency", "Latência alta"),  # sem o deploy
        ("node-disk-pressure", "Pod reiniciando"),
        ("latency-creep", "Latência alta após deploy"),
    ],
)
def test_wrong_or_incomplete_causes_are_not_accepted(scenario_id, cause):
    assert not cause_matches(cause, BY_ID[scenario_id])


def test_exact_expected_cause_still_matches_and_unknown_never_correct():
    for s in SCENARIOS:
        assert cause_matches(s.expected_cause, s)
        ref = s.expected_evidence_refs[0]
        assert not grade(RCA(cause=None, confidence=0.0, unknown=True,
                             evidence=[Evidence(kind=ref.split(":")[0], ref=ref, summary="x")]), s)


def test_every_scenario_declares_synonyms_settle_check():
    for s in SCENARIOS:
        assert s.accepted_causes and all(s.accepted_causes), s.id
        assert s.settle is not None and s.settle.args and s.settle.expect, s.id


# ------------------------------------------------------------ (b) medível


def _fakes():
    calls = {"inject": [], "cleanup": [], "settle": []}

    def inject(s, k):
        calls["inject"].append(s.id)
        return InjectionHandle(scenario_id=s.id, kubeconfig=k)

    return calls, inject, lambda h: calls["cleanup"].append(h.scenario_id), lambda s, k: calls["settle"].append(s.id)


def test_scenarios_needing_absent_tools_are_not_measurable_and_excluded():
    calls, inject, cleanup, settle = _fakes()
    board = run_all(lambda s: RCA(cause=None, confidence=0.0, unknown=True), "m", kubeconfig="k",
                    inject=inject, cleanup=cleanup, settle=settle, enabled_tools={"kubernetes"})
    by_id = {r.scenario_id: r for r in board.scenarios}
    for sid in ("bad-deploy-latency", "dns-failure", "latency-creep"):
        assert by_id[sid].measurable is False
        assert "não medível neste ambiente" in by_id[sid].reason
        assert sid not in calls["inject"]  # nem injetado
    assert "prometheus" in by_id["latency-creep"].reason and "loki" in by_id["dns-failure"].reason
    measurable = [s for s in SCENARIOS if "kubernetes" in s.required_tools()]
    assert board.total == len(measurable) and board.not_measurable == 3
    assert board.enabled_tools == ["kubernetes"]


def test_enabling_the_tools_makes_them_measurable():
    _, inject, cleanup, settle = _fakes()
    board = run_all(lambda s: RCA(cause=None, confidence=0.0, unknown=True), "m", kubeconfig="k",
                    inject=inject, cleanup=cleanup, settle=settle,
                    enabled_tools={"kubernetes", "prometheus", "loki", "github"})
    assert board.total == len(SCENARIOS) and board.not_measurable == 0


# ------------------------------------------------------------ (c) espera


def test_settle_runs_after_inject_and_before_investigate():
    order = []

    def inject(s, k):
        order.append(("inject", s.id))
        return InjectionHandle(scenario_id=s.id, kubeconfig=k)

    def investigate(s):
        order.append(("investigate", s.id))
        return RCA(cause=None, confidence=0.0, unknown=True)

    run_all(investigate, "m", kubeconfig="k", scenarios=[SCENARIOS[0]], inject=inject,
            cleanup=lambda h: None, settle=lambda s, k: order.append(("settle", s.id)))
    assert [o[0] for o in order] == ["inject", "settle", "investigate"]


def test_settle_timeout_is_an_injection_error_with_reason_and_cleanup():
    calls, inject, cleanup, _ = _fakes()

    def never(s, k):
        raise SettleTimeout(300)

    investigated = []
    board = run_all(lambda s: investigated.append(s) or RCA(cause=None, confidence=0.0, unknown=True), "m",
                    kubeconfig="k", scenarios=[SCENARIOS[0]], inject=inject, cleanup=cleanup, settle=never)
    assert board.injection_errors == 1 and investigated == []
    assert calls["cleanup"] == [SCENARIOS[0].id]
    assert "não se manifestou em 300s" in board.scenarios[0].reason


def test_wait_for_polls_until_expected_output_or_deadline():
    outputs = iter(["", "Running", "OOMKilled"])
    now = [0.0]
    sleeps = []

    def run(kubeconfig, *args):
        assert kubeconfig == "k" and args == ("get", "pods")
        return next(outputs)

    check = ClusterCheck(args=["get", "pods"], expect="OOMKilled", timeout_seconds=60)
    assert wait_for(check, "k", run=run, sleep=lambda s: (sleeps.append(s), now.__setitem__(0, now[0] + s)),
                    clock=lambda: now[0], interval=5)
    assert sleeps == [5, 5]

    def fails(kubeconfig, *args):
        raise subprocess.CalledProcessError(1, "kubectl")

    now[0] = 0.0
    assert not wait_for(check, "k", run=fails, sleep=lambda s: now.__setitem__(0, now[0] + s),
                        clock=lambda: now[0], interval=5)


# ------------------------------------------------------------ (d) disco


def test_disk_pressure_runs_last_and_waits_for_recovery():
    order = [s.id for s in execution_order(SCENARIOS)]
    assert order[-1] == "node-disk-pressure"
    s = BY_ID["node-disk-pressure"]
    assert s.run_last and s.clear is not None and s.clear.expect == "False"


def test_kind_config_sets_high_nodefs_eviction_threshold():
    doc = yaml.safe_load((runner.REPO_ROOT / "harness" / "kind-config.yaml").read_text())
    patches = doc["nodes"][0]["kubeadmConfigPatches"]
    text = "\n".join(patches)
    kubelet = next(d for p in patches for d in yaml.safe_load_all(p) if d and d.get("kind") == "KubeletConfiguration")
    hard = kubelet["evictionHard"]
    assert "nodefs.available" in hard and "memory.available" in hard and "imagefs.available" in hard
    assert text and "set-disk-threshold.sh" in (runner.REPO_ROOT / "harness" / "kind-config.yaml").read_text()
    # O script troca o limiar por um valor absoluto logo abaixo do livre medido, e o Makefile o chama.
    script = (runner.REPO_ROOT / "harness" / "set-disk-threshold.sh").read_text()
    assert "nodefs.available: ${threshold_kb}Ki" in script and "systemctl restart kubelet" in script
    assert "set-disk-threshold.sh" in (runner.REPO_ROOT / "Makefile").read_text()


def test_disk_fill_manifest_uses_small_bounded_fill_and_cleaner_exists():
    m = (runner.MANIFESTS_DIR / "node-disk-pressure.yaml").read_text()
    assert "MAX_FILL_KB" in m and "total / 20" not in m
    cleaner = runner.MANIFESTS_DIR / "node-disk-pressure-cleaner.yaml"
    docs = [d for d in yaml.safe_load_all(cleaner.read_text()) if d]
    pod = next(d for d in docs if d["kind"] == "Pod")
    keys = {t["key"] for t in pod["spec"]["tolerations"]}
    assert "node.kubernetes.io/disk-pressure" in keys


def test_cleanup_applies_cleaner_when_condition_does_not_clear(monkeypatch):
    s = BY_ID["node-disk-pressure"]
    cmds = []
    results = iter([False, True])
    monkeypatch.setattr(runner, "_kubectl", lambda k, *a: cmds.append(a) or "")
    monkeypatch.setattr(runner, "wait_for", lambda check, k: next(results))
    runner.cleanup(InjectionHandle(scenario_id=s.id, kubeconfig="k"))
    applied = [a for a in cmds if a[0] == "apply"]
    assert applied and applied[0][2].endswith("node-disk-pressure-cleaner.yaml")
    assert cmds[-1][0] == "delete" and cmds[-1][2].endswith("node-disk-pressure-cleaner.yaml")


# ------------------------------------------------------------ (e) placar


def test_scoreboard_json_has_provenance_and_per_scenario_flags():
    _, inject, cleanup, settle = _fakes()
    board = run_all(lambda s: RCA(cause=None, confidence=0.0, unknown=True), "m", kubeconfig="k",
                    inject=inject, cleanup=cleanup, settle=settle, enabled_tools={"kubernetes"}, git_commit="abc")
    data = json.loads(json.dumps(board.model_dump()))
    assert data["timestamp"] and data["git_commit"] == "abc" and data["enabled_tools"] == ["kubernetes"]
    assert {r["scenario_id"] for r in data["scenarios"]} == {s.id for s in SCENARIOS}
    assert all("measurable" in r for r in data["scenarios"])


def test_old_scoreboard_files_still_validate():
    old = {"correct": 1, "total": 7, "model": "x", "injection_errors": 0, "label": "cenários sintéticos",
           "calibration": [{"confidence": 0.9, "hit_rate": 1.0}]}
    board = Scoreboard.model_validate(old)
    assert board.scenarios == [] and board.enabled_tools is None and board.not_measurable == 0


# ------------------------------------------------------------ I9


def test_empty_kubeconfig_is_refused():
    assert "KUBECONFIG vazio" in check_target_cluster("")
    with pytest.raises(ValueError):
        runner._kubectl("", "get", "pods")
    with pytest.raises(ValueError):
        runner.inject(SCENARIOS[0], "")


def test_non_kind_context_is_refused_unless_explicitly_allowed():
    assert check_target_cluster("k", context_of=lambda k: KIND_CONTEXT) is None
    err = check_target_cluster("k", context_of=lambda k: "prod-eks")
    assert "prod-eks" in err and "--allow-non-kind" in err
    assert check_target_cluster("k", allow_non_kind=True, context_of=lambda k: "prod-eks") is None
    assert check_target_cluster("k", context_of=lambda k: (_ for _ in ()).throw(RuntimeError("x"))) is not None


def test_main_refuses_without_kubeconfig_or_with_wrong_context(tmp_path, monkeypatch, capsys):
    injected = []
    monkeypatch.setattr(runner, "run_all", functools.partial(
        runner.run_all, inject=lambda s, k: injected.append(s) or InjectionHandle(scenario_id=s.id, kubeconfig=k),
        cleanup=lambda h: None, settle=lambda s, k: None))
    monkeypatch.delenv("KUBECONFIG", raising=False)
    assert runner.main([str(tmp_path / "sb.json")]) == 2
    assert "KUBECONFIG vazio" in capsys.readouterr().err
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "kc"))
    monkeypatch.setattr(runner, "current_context", lambda k: "prod")
    monkeypatch.setattr(runner, "check_target_cluster", functools.partial(
        runner.check_target_cluster, context_of=lambda k: "prod"))
    assert runner.main([str(tmp_path / "sb.json")]) == 2
    assert injected == [] and not (tmp_path / "sb.json").exists()
    monkeypatch.setenv("AISRE_KUBERNETES_ENABLED", "true")
    assert runner.main([str(tmp_path / "sb.json"), "--allow-non-kind"]) == 0
    assert injected
