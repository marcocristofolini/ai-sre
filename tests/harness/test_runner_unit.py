from aisre.harness.runner import InjectionHandle, run_all
from aisre.harness.scenarios import SCENARIOS
from aisre.schemas import RCA, Evidence


def _fakes():
    calls = {"inject": [], "cleanup": []}

    def inject(scenario, kubeconfig):
        calls["inject"].append(scenario.id)
        return InjectionHandle(scenario_id=scenario.id, kubeconfig=kubeconfig)

    def cleanup(handle):
        calls["cleanup"].append(handle.scenario_id)

    return calls, inject, cleanup


def _unknown(_scenario):
    return RCA(cause=None, confidence=0.0, unknown=True)


def test_run_all_cleans_up_even_when_investigate_raises():
    calls, inject, cleanup = _fakes()

    def boom(_scenario):
        raise RuntimeError("falha")

    run_all(boom, "modelo-x", inject=inject, cleanup=cleanup)
    assert calls["cleanup"] == [s.id for s in SCENARIOS]
    assert calls["inject"] == calls["cleanup"]


def test_run_all_counts_exception_as_incorrect():
    _, inject, cleanup = _fakes()

    def boom(_scenario):
        raise RuntimeError("falha")

    board = run_all(boom, "modelo-x", inject=inject, cleanup=cleanup)
    assert (board.correct, board.total) == (0, len(SCENARIOS))


def test_run_all_grades_and_labels_scoreboard():
    _, inject, cleanup = _fakes()
    target = SCENARIOS[0]

    def investigate(scenario):
        if scenario.id != target.id:
            return _unknown(scenario)
        return RCA(
            cause=target.expected_cause,
            confidence=0.9,
            evidence=[
                Evidence(kind="k8s", ref=target.expected_evidence_refs[0], summary="x")
            ],
        )

    board = run_all(investigate, "modelo-x", inject=inject, cleanup=cleanup)
    assert (board.correct, board.total) == (1, len(SCENARIOS))
    assert board.model == "modelo-x"
    assert board.label == "cenários sintéticos"


def test_run_all_cleans_up_when_inject_succeeds_but_grading_input_invalid():
    calls, inject, cleanup = _fakes()
    run_all(_unknown, "m", inject=inject, cleanup=cleanup)
    assert len(calls["cleanup"]) == len(SCENARIOS)


def test_inject_failure_counts_incorrect_and_continues():
    calls = {"cleanup": []}

    def inject(scenario, kubeconfig):
        raise RuntimeError("kubectl falhou")

    board = run_all(
        _unknown, "m", inject=inject, cleanup=lambda h: calls["cleanup"].append(h)
    )
    assert (board.correct, board.total) == (0, len(SCENARIOS))
    assert calls["cleanup"] == []


def test_manifest_exists_for_every_scenario():
    from aisre.harness.runner import manifest_path

    for s in SCENARIOS:
        path = manifest_path(s)
        assert path.is_file() and path.read_text().strip()
