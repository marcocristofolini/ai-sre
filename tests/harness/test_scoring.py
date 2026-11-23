import asyncio
import re

import httpx
import pytest

from aisre.harness.scenarios import SCENARIOS, Scenario
from aisre.harness.scoring import GradedResult, calibration, grade, scoreboard
from aisre.schemas import RCA, Evidence
from aisre.tools.github import GitHubTool
from aisre.tools.kubernetes import KubernetesTool
from aisre.tools.loki import LokiTool
from aisre.tools.prometheus import PrometheusTool

SCEN = Scenario(
    id="oom-kill",
    fault="pod morto por OOM",
    expected_cause="OOMKilled",
    expected_evidence_refs=["k8s:pod"],
)


def _rca(cause, refs):
    return RCA(
        cause=cause,
        confidence=0.8,
        evidence=[Evidence(kind=r.split(":", 1)[0], ref=r, summary="x") for r in refs],
    )


def test_grade_true_when_cause_matches_and_ref_starts_with_expected_pattern():
    assert grade(_rca("  oomkilled ", ["k8s:pod/leaker", "metric:up"]), SCEN)
    assert grade(_rca("OOMKilled", ["k8s:pods"]), SCEN)


def test_grade_false_when_cause_right_but_no_expected_evidence():
    assert not grade(_rca("OOMKilled", ["metric:up"]), SCEN)


def test_grade_false_when_ref_is_of_wrong_kind():
    assert not grade(_rca("OOMKilled", ["k8s:nodes", "log:{namespace=\"x\"}"]), SCEN)


def test_grade_false_when_pattern_appears_but_not_as_prefix():
    assert not grade(_rca("OOMKilled", ["log:k8s:pod/leaker"]), SCEN)


def test_grade_false_when_cause_wrong_even_with_expected_evidence():
    assert not grade(_rca("DiskPressure no nó", ["k8s:pod/leaker"]), SCEN)


def test_grade_false_when_unknown():
    assert not grade(RCA(cause=None, confidence=0.0, unknown=True), SCEN)


def test_grade_false_when_unknown_even_carrying_expected_evidence():
    rca = RCA(
        cause=None,
        confidence=0.0,
        unknown=True,
        evidence=[Evidence(kind="k8s", ref="k8s:pod/leaker", summary="x")],
    )
    assert not grade(rca, SCEN)


def test_scenario_patterns_have_a_tool_kind_and_resource():
    for s in SCENARIOS:
        assert s.expected_evidence_refs, s.id
        for pattern in s.expected_evidence_refs:
            assert re.fullmatch(r"(metric|log|commit|k8s):\S*", pattern), pattern
            if pattern.startswith("k8s:"):
                assert pattern.removeprefix("k8s:") in {"pod", "pods", "nodes", "events"}


class _FakeCore:
    def read_namespaced_pod(self, name, namespace, **kw):
        return {"status": {}}

    def list_namespaced_pod(self, namespace, **kw):
        return {"items": []}

    def list_node(self, **kw):
        return {"items": []}

    def list_namespaced_event(self, namespace, **kw):
        return {"items": []}


def _ok_transport():
    return httpx.MockTransport(
        lambda r: httpx.Response(200, json=[{"sha": "abcdef1234567"}] if "commits" in r.url.path else {})
    )


async def _real_ref(kind: str, args: dict) -> str:
    """Ref produzido por uma ferramenta real (com backend falso) para a consulta."""
    tools = {
        "k8s": KubernetesTool(client=_FakeCore(), timeout=5),
        "log": LokiTool("http://loki", timeout=5, transport=_ok_transport()),
        "metric": PrometheusTool("http://prom", timeout=5, transport=_ok_transport()),
        "commit": GitHubTool("org/app", "tok", timeout=5, transport=_ok_transport()),
    }
    res = await tools[kind].call(args)
    assert res.ok
    return res.ref


# Consultas que um agente perfeito faria para cada falha.
PERFECT_QUERIES = {
    "oom-kill": [("k8s", {"kind": "pod", "namespace": "aisre-oom-kill", "name": "leaker"})],
    "crashloop-bad-config": [("log", {"query": '{namespace="aisre-crashloop-bad-config"}'})],
    "bad-deploy-latency": [
        ("commit", {}),
        ("metric", {"query": "histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m]))"}),
    ],
    "node-disk-pressure": [("k8s", {"kind": "nodes"})],
    "dns-failure": [("log", {"query": '{namespace="aisre-dns-failure"} |= "dns-error"'})],
}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_perfect_agent_using_real_tool_refs_grades_true(scenario):
    refs = [asyncio.run(_real_ref(kind, args)) for kind, args in PERFECT_QUERIES[scenario.id]]
    assert grade(_rca(scenario.expected_cause, refs), scenario)


def test_calibration_flags_overconfidence():
    bins = calibration([(0.9, False)] * 5)
    assert bins == [(0.9, 0.0)]


def test_scoreboard_declares_model_and_synthetic_label():
    board = scoreboard(
        [GradedResult(scenario_id="a", correct=True, confidence=0.9),
         GradedResult(scenario_id="b", correct=False, confidence=0.5)],
        model="claude-x",
    )
    assert (board.correct, board.total) == (1, 2)
    assert board.model == "claude-x"
    assert board.label == "cenários sintéticos"


def test_initial_scenarios_declared():
    assert {s.id for s in SCENARIOS} == {
        "oom-kill", "crashloop-bad-config", "bad-deploy-latency",
        "node-disk-pressure", "dns-failure",
    }
