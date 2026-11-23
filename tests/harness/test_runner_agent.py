import asyncio
import functools
import json

import pytest
import yaml

from aisre import strings_pt as t
from aisre.config import Settings
from aisre.harness import runner
from aisre.harness.runner import InjectionHandle, run_all
from aisre.harness.scenarios import SCENARIOS
from aisre.llm.base import LLMResponse, ToolCall
from aisre.llm.gate import ConsentRequired
from aisre.tools.base import ToolResult


class FakeK8s:
    name = "kubernetes"
    schema = {"type": "object", "required": ["kind"]}

    def __init__(self):
        self.calls = []

    async def call(self, args):
        self.calls.append(args)
        return ToolResult(ok=True, data="{}", error=None, ref=f"k8s:{args['kind']}/{args['name']}")


class FakeLLM:
    """Consulta o pod uma vez e conclui OOMKilled citando o ref devolvido."""

    name = "fake"
    is_external = False

    def __init__(self, error=None):
        self.error = error
        self.alerts = []

    async def complete(self, messages, tools):
        if self.error:
            raise self.error
        tool_msgs = [m for m in messages if m.role == "tool"]
        if not tool_msgs:
            self.alerts.append(messages[1].content)
            ns = next(line.split(": ", 1)[1] for line in messages[1].content.splitlines()
                      if line.startswith("namespace: "))
            return LLMResponse(text=None, tool_calls=[ToolCall(
                id="1", name="kubernetes", args={"kind": "pod", "namespace": ns, "name": "worker"})])
        return LLMResponse(text=None, tool_calls=[ToolCall(id="2", name="finalizar", args={
            "cause": "OOMKilled", "confidence": 0.9,
            "evidence": [{"ref": "k8s:pod/worker", "summary": "lastState OOMKilled"}],
        })])


@pytest.fixture
def harness(monkeypatch):
    """Substitui cluster, provedor e ferramentas por falsos; devolve o estado observado."""
    state = {"settings": [], "cleanup": [], "llm": FakeLLM(), "tools": [FakeK8s()]}
    monkeypatch.delenv("AISRE_EVAL_AGENT", raising=False)
    monkeypatch.setenv("AISRE_LLM_MODEL", "modelo-teste")

    def provider(settings):
        state["settings"].append(settings)
        return state["llm"]

    def tools(settings):
        state["settings"].append(settings)
        return state["tools"]

    def inject(scenario, kubeconfig):
        return InjectionHandle(scenario_id=scenario.id, kubeconfig=kubeconfig)

    monkeypatch.setattr(runner, "build_provider", provider)
    monkeypatch.setattr(runner, "build_tools", tools)
    monkeypatch.setattr(
        runner, "run_all",
        functools.partial(run_all, inject=inject, cleanup=lambda h: state["cleanup"].append(h.scenario_id)),
    )
    return state


@pytest.mark.parametrize("enable", ["flag", "env"])
def test_agent_mode_builds_provider_and_tools_from_settings_and_investigates(
    harness, monkeypatch, tmp_path, enable
):
    out = tmp_path / "sb.json"
    argv = [str(out)]
    if enable == "flag":
        argv.append("--agent")
    else:
        monkeypatch.setenv("AISRE_EVAL_AGENT", "1")
    assert runner.main(argv) == 0
    board = json.loads(out.read_text())
    # só o cenário de OOM tem a causa que o LLM falso sempre responde
    assert (board["correct"], board["total"]) == (1, len(SCENARIOS))
    assert board["model"] == "fake:modelo-teste"
    # provedor: 1 validação + 1 por cenário; ferramentas: 1
    assert len(harness["settings"]) == 2 + len(SCENARIOS)
    assert all(isinstance(s, Settings) for s in harness["settings"])
    namespaces = [c["namespace"] for c in harness["tools"][0].calls]
    assert namespaces == [s.namespace for s in SCENARIOS]
    # o alerta entregue ao agente não traz a causa esperada nem o rótulo da falha
    for alert_text, s in zip(harness["llm"].alerts, SCENARIOS, strict=True):
        assert s.expected_cause not in alert_text
        assert f"alerta: {runner.EVAL_ALERT_NAME}" in alert_text
        assert "fault" not in alert_text


def test_default_mode_keeps_unknown_investigator(harness, tmp_path, monkeypatch):
    def must_not_build(_settings):
        raise AssertionError("provedor não deve ser construído sem opt-in")

    monkeypatch.setattr(runner, "build_provider", must_not_build)
    out = tmp_path / "sb.json"
    assert runner.main([str(out)]) == 0
    board = json.loads(out.read_text())
    assert board["model"] == "baseline-desconhecido" and board["correct"] == 0


def test_agent_mode_consent_required_aborts_with_message_and_cleans_up(harness, tmp_path, capsys):
    harness["llm"] = FakeLLM(error=ConsentRequired())
    assert runner.main([str(tmp_path / "sb.json"), "--agent"]) == 2
    assert t.ERRO_CONSENTIMENTO_LLM in capsys.readouterr().err
    assert harness["cleanup"] == [SCENARIOS[0].id]


def test_agent_mode_provider_config_error_exits_with_message(harness, tmp_path, capsys, monkeypatch):
    def no_key(_settings):
        raise ValueError(t.ERRO_LLM_SEM_CHAVE)

    monkeypatch.setattr(runner, "build_provider", no_key)
    assert runner.main([str(tmp_path / "sb.json"), "--agent"]) == 2
    assert t.ERRO_LLM_SEM_CHAVE in capsys.readouterr().err
    assert harness["cleanup"] == []


def test_run_all_propagates_consent_required_after_cleanup():
    cleaned = []

    def investigate(_scenario):
        raise ConsentRequired()

    with pytest.raises(ConsentRequired):
        run_all(
            investigate, "m",
            inject=lambda s, k: InjectionHandle(scenario_id=s.id, kubeconfig=k),
            cleanup=lambda h: cleaned.append(h.scenario_id),
        )
    assert cleaned == [SCENARIOS[0].id]


# --- fix round 1 ----------------------------------------------------------

class LoopBoundLLM(FakeLLM):
    """Como um cliente HTTP assíncrono: inutilizável em outro event loop."""

    def __init__(self):
        super().__init__()
        self.loop = None

    async def complete(self, messages, tools):
        loop = asyncio.get_running_loop()
        if self.loop is None:
            self.loop = loop
        elif self.loop is not loop:
            raise RuntimeError("Event loop is closed")
        return await super().complete(messages, tools)


def test_agent_mode_never_reuses_a_provider_across_event_loops(harness, monkeypatch, tmp_path):
    built = []

    def provider(settings):
        built.append(LoopBoundLLM())
        return built[-1]

    monkeypatch.setattr(runner, "build_provider", provider)
    out = tmp_path / "sb.json"
    assert runner.main([str(out), "--agent"]) == 0
    board = json.loads(out.read_text())
    assert board["correct"] == 1  # oom-kill não virou "não sei" por erro de loop
    assert len(harness["tools"][0].calls) == len(SCENARIOS)


def test_alert_and_namespace_do_not_reveal_the_scenario(harness, tmp_path):
    assert runner.main([str(tmp_path / "sb.json"), "--agent"]) == 0
    for alert_text, s in zip(harness["llm"].alerts, SCENARIOS, strict=True):
        assert f"namespace: {s.namespace}" in alert_text
        for word in [s.id, s.fault, s.expected_cause, *s.id.split("-"), *s.fault.split("-")]:
            if len(word) > 3:
                assert word.casefold() not in alert_text.casefold(), (s.id, word)


def test_scenario_namespaces_are_neutral_and_match_manifests():
    from aisre.harness.runner import manifest_path

    for s in SCENARIOS:
        docs = [d for d in yaml.safe_load_all(manifest_path(s).read_text()) if d]
        namespaces = {d["metadata"]["name"] for d in docs if d["kind"] == "Namespace"}
        assert namespaces == {s.namespace}
        assert all(d["metadata"].get("namespace", s.namespace) == s.namespace for d in docs)
        names = {d["metadata"]["name"] for d in docs}
        for word in [s.id, *s.id.split("-"), *s.fault.split("-")]:
            if len(word) > 3:
                assert not any(word in n for n in names), (s.id, word, names)
