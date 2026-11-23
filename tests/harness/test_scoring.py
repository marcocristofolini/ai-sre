from aisre.harness.scenarios import SCENARIOS, Scenario
from aisre.harness.scoring import GradedResult, calibration, grade, scoreboard
from aisre.schemas import RCA, Evidence

SCEN = Scenario(
    id="oom-kill",
    fault="pod morto por OOM",
    expected_cause="OOMKilled",
    expected_evidence_refs=["k8s:pod/app/lastState"],
)


def _rca(cause, refs):
    return RCA(
        cause=cause,
        confidence=0.8,
        evidence=[Evidence(kind="k8s", ref=r, summary="x") for r in refs],
    )


def test_grade_true_when_cause_and_expected_evidence_match():
    assert grade(_rca("  oomkilled ", ["k8s:pod/app/lastState", "other"]), SCEN)


def test_grade_false_when_cause_right_but_no_expected_evidence():
    assert not grade(_rca("OOMKilled", ["other"]), SCEN)


def test_grade_false_when_unknown():
    assert not grade(RCA(cause=None, confidence=0.0, unknown=True), SCEN)


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
