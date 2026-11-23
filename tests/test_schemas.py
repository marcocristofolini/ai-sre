import pytest
from pydantic import ValidationError

from aisre.config import Settings
from aisre.schemas import RCA, Evidence


def test_rca_rejects_confidence_above_one():
    with pytest.raises(ValidationError):
        RCA(cause=None, confidence=1.2, evidence=[], suggested_command=None, unknown=True)


def test_rca_with_cause_requires_evidence():
    with pytest.raises(ValidationError):
        RCA(cause="x", confidence=0.5, evidence=[], suggested_command=None, unknown=False)


def test_rca_with_cause_and_evidence_is_valid():
    ev = Evidence(kind="metric", ref="cpu", summary="CPU a 99%")
    rca = RCA(cause="x", confidence=0.5, evidence=[ev], suggested_command=None, unknown=False)
    assert rca.cause == "x"


def test_unknown_rca_has_no_cause():
    with pytest.raises(ValidationError):
        RCA(cause="x", confidence=0.1, evidence=[], suggested_command=None, unknown=True)
    rca = RCA(cause=None, confidence=0.0, evidence=[], suggested_command=None, unknown=True)
    assert rca.cause is None


def test_external_consent_defaults_false():
    assert Settings().llm_external_consent is False
    assert Settings().slack_approvers == []
