import asyncio
from datetime import UTC, datetime

from aisre.agent.loop import Alert
from aisre.api import ingest
from aisre.config import Settings
from aisre.llm.base import LLMResponse, ToolCall
from aisre.agent.prompts import FINAL_TOOL_NAME

RUNBOOK = "---\ntitulo: CrashLoop\nalertas: KubePodCrashLooping\n---\nVeja os logs anteriores.\n"


class _LLM:
    name = "fake"
    is_external = False

    def __init__(self):
        self.prompts = []

    async def complete(self, messages, tools):
        self.prompts.append("\n".join(m.content or "" for m in messages))
        args = {"cause": None, "confidence": 0.0, "evidence": []}
        return LLMResponse(text=None, tool_calls=[ToolCall(id="f", name=FINAL_TOOL_NAME, args=args)])


def _alert(name="KubePodCrashLooping"):
    return Alert(name=name, labels={"namespace": "shop"}, fired_at=datetime(2026, 11, 26, tzinfo=UTC))


def _investigator(monkeypatch, tmp_path, llm, **settings):
    monkeypatch.setattr(ingest, "build_tools", lambda s: [])
    monkeypatch.setattr(ingest, "build_provider", lambda s: llm)
    return ingest.default_investigator(Settings(runbooks_dir=str(tmp_path), **settings))


def test_selected_runbook_reaches_the_llm_and_is_recorded_on_the_rca(monkeypatch, tmp_path):
    (tmp_path / "crashloop.md").write_text(RUNBOOK, encoding="utf-8")
    llm = _LLM()
    rca = asyncio.run(_investigator(monkeypatch, tmp_path, llm)(_alert()))
    assert "Veja os logs anteriores." in llm.prompts[0]
    assert rca.runbooks == ["crashloop"] and rca.unknown


def test_non_matching_alert_gets_no_runbook(monkeypatch, tmp_path):
    (tmp_path / "crashloop.md").write_text(RUNBOOK, encoding="utf-8")
    llm = _LLM()
    rca = asyncio.run(_investigator(monkeypatch, tmp_path, llm)(_alert("OutroAlerta")))
    assert "Veja os logs anteriores." not in llm.prompts[0]
    assert rca.runbooks == []


def test_runbooks_top_k_zero_disables_them(monkeypatch, tmp_path):
    (tmp_path / "crashloop.md").write_text(RUNBOOK, encoding="utf-8")
    llm = _LLM()
    rca = asyncio.run(_investigator(monkeypatch, tmp_path, llm, runbooks_top_k=0)(_alert()))
    assert "Veja os logs anteriores." not in llm.prompts[0] and rca.runbooks == []


def test_runbook_secret_is_redacted_before_reaching_the_provider(monkeypatch, tmp_path):
    # O texto do runbook passa pelo mesmo gate de redação de qualquer outro dado enviado ao LLM.
    from aisre.llm.gate import guarded

    secret_rb = "---\nalertas: KubePodCrashLooping\n---\nUse a chave AKIAIOSFODNN7EXAMPLE para acessar.\n"
    (tmp_path / "secret.md").write_text(secret_rb, encoding="utf-8")
    inner = _LLM()
    settings = Settings(runbooks_dir=str(tmp_path), llm_external_consent=True)
    monkeypatch.setattr(ingest, "build_tools", lambda s: [])
    monkeypatch.setattr(ingest, "build_provider", lambda s: guarded(inner, settings))
    asyncio.run(ingest.default_investigator(settings)(_alert()))
    assert "AKIAIOSFODNN7EXAMPLE" not in inner.prompts[0]
    assert "Use a chave" in inner.prompts[0]
