import os

import pytest

from aisre.harness.runner import run_all
from aisre.harness.scenarios import SCENARIOS
from aisre.schemas import RCA

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("AISRE_E2E") != "1",
        reason="requer cluster kind (make eval); defina AISRE_E2E=1",
    ),
]


def test_unknown_investigator_scores_zero_on_real_cluster():
    board = run_all(
        lambda _s: RCA(cause=None, confidence=0.0, unknown=True),
        "baseline-desconhecido",
        kubeconfig=os.environ["KUBECONFIG"],
    )
    assert (board.correct, board.total) == (0, len(SCENARIOS))
