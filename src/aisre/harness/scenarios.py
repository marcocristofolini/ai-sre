"""Cenários sintéticos do `make eval`.

Correção da causa (`scoring.grade`): a causa do RCA conta como certa se for igual (normalizada) a
`expected_cause` OU se casar com algum grupo de `accepted_causes` (qualquer grupo; dentro do grupo,
TODAS as palavras-chave). A comparação ignora caixa e acentos, e cada palavra-chave precisa começar
numa fronteira de palavra (ex.: "config" casa "configuração", mas "oom" não casa "room").
Um RCA "não sei" nunca é correto, nem com evidência.

Medição (`runner`): um cenário só é medível se ao menos uma ferramenta capaz de produzir uma das
evidências esperadas estiver habilitada (`metric:` -> prometheus, `log:` -> loki, `commit:` -> github,
`k8s:` -> kubernetes). O cluster `kind` do harness não traz Prometheus, Loki nem GitHub: sem essas
ferramentas configuradas, os cenários que dependem delas saem do total como "não medível neste
ambiente" (com o motivo no JSON).
"""

from pydantic import BaseModel, Field

# Prefixo do ref de evidência -> ferramenta que o produz.
EVIDENCE_TOOL = {"metric": "prometheus", "log": "loki", "commit": "github", "k8s": "kubernetes"}


class ClusterCheck(BaseModel):
    """Condição observada com `kubectl <args>`: satisfeita quando a saída contém `expect`."""

    args: list[str]
    expect: str
    timeout_seconds: int = 300


class Scenario(BaseModel):
    id: str
    fault: str
    expected_cause: str
    # Namespace neutro (eval-<n>) do manifesto: o agente o recebe no alerta,
    # então não pode revelar a falha.
    namespace: str
    # Padrões `<kind>:<recurso>` que as ferramentas reais produzem para a falha;
    # uma evidência conta quando o ref citado COMEÇA com um destes padrões.
    expected_evidence_refs: list[str]
    # Sinônimos aceitos (qualquer grupo; todas as palavras do grupo). Veja o docstring do módulo.
    accepted_causes: list[list[str]] = Field(default_factory=list)
    # Espera a falha se manifestar antes de investigar (a injeção só aplica o manifesto).
    settle: ClusterCheck | None = None
    # Espera o cluster voltar ao normal depois do cleanup (ex.: DiskPressure some).
    clear: ClusterCheck | None = None
    run_last: bool = False  # afeta o nó inteiro: roda depois dos demais

    def required_tools(self) -> set[str]:
        """Ferramentas que produzem alguma evidência esperada (basta uma estar habilitada)."""
        return {EVIDENCE_TOOL[p.split(":", 1)[0]] for p in self.expected_evidence_refs if ":" in p}


def _pods(ns: str, path: str, expect: str, timeout: int = 300) -> ClusterCheck:
    return ClusterCheck(args=["get", "pods", "-n", ns, "-o", f"jsonpath={path}"], expect=expect, timeout_seconds=timeout)


_TERMINATED = "{.items[*].status.containerStatuses[*].lastState.terminated.reason} {.items[*].status.containerStatuses[*].state.terminated.reason}"
_WAITING = "{.items[*].status.containerStatuses[*].state.waiting.reason}"
_READY = "{.items[*].status.containerStatuses[*].ready}"
_DISK_PRESSURE = '{.items[*].status.conditions[?(@.type=="DiskPressure")].status}'

SCENARIOS: list[Scenario] = [
    Scenario(
        id="oom-kill",
        namespace="eval-1",
        fault="memory-leak",
        expected_cause="OOMKilled",
        expected_evidence_refs=["k8s:pod", "k8s:events"],
        accepted_causes=[["oomkilled"], ["oom"], ["out of memory"], ["sem memoria"], ["limite de memoria"]],
        settle=_pods("eval-1", _TERMINATED, "OOMKilled"),
    ),
    Scenario(
        id="crashloop-bad-config",
        namespace="eval-2",
        fault="bad-config",
        expected_cause="CrashLoopBackOff por configuração inválida",
        expected_evidence_refs=["log:", "k8s:pod", "k8s:events"],
        accepted_causes=[["crashloop", "config"], ["configuracao invalida"], ["config", "invalid"], ["config", "erro"]],
        settle=_pods("eval-2", _WAITING, "CrashLoopBackOff"),
    ),
    Scenario(
        id="bad-deploy-latency",
        namespace="eval-3",
        fault="bad-deploy",
        expected_cause="Deploy recente aumentou a latência",
        expected_evidence_refs=["commit:", "metric:"],
        accepted_causes=[["deploy", "latencia"], ["deploy", "latency"], ["rollout", "latencia"], ["versao", "latencia"]],
        settle=_pods("eval-3", _READY, "true"),
    ),
    Scenario(
        id="node-disk-pressure",
        namespace="eval-4",
        fault="disk-fill",
        expected_cause="DiskPressure no nó",
        expected_evidence_refs=["k8s:nodes", "k8s:events"],
        accepted_causes=[["diskpressure"], ["disk pressure"], ["pressao de disco"], ["disco", "cheio"], ["disk", "full"]],
        settle=ClusterCheck(args=["get", "nodes", "-o", f"jsonpath={_DISK_PRESSURE}"], expect="True", timeout_seconds=600),
        clear=ClusterCheck(args=["get", "nodes", "-o", f"jsonpath={_DISK_PRESSURE}"], expect="False", timeout_seconds=600),
        run_last=True,
    ),
    Scenario(
        id="dns-failure",
        namespace="eval-5",
        fault="dns-break",
        expected_cause="Falha de resolução DNS",
        expected_evidence_refs=["log:"],
        accepted_causes=[["dns"], ["resolucao de nomes"], ["name resolution"]],
        settle=_pods("eval-5", _READY, "true"),
    ),
    Scenario(
        id="memory-leak-gradual",
        namespace="eval-6",
        fault="slow-memory-growth",
        expected_cause="Crescimento gradual de memória até o limite do contêiner",
        expected_evidence_refs=["metric:", "k8s:pod"],
        accepted_causes=[["vazamento", "memoria"], ["memory leak"], ["leak"], ["crescimento", "memoria"], ["memoria", "gradual"]],
        # ~1 MiB/s até 256 MiB: o primeiro OOMKilled leva uns 4-5 minutos.
        settle=_pods("eval-6", _TERMINATED, "OOMKilled", timeout=600),
    ),
    Scenario(
        id="latency-creep",
        namespace="eval-7",
        fault="slow-latency-growth",
        expected_cause="Latência crescendo gradualmente sem mudança de código",
        expected_evidence_refs=["metric:"],
        accepted_causes=[["latencia", "gradual"], ["latencia", "crescendo"], ["latency", "creep"], ["latencia", "aumentando"]],
        settle=_pods("eval-7", _READY, "true"),
    ),
]


def execution_order(scenarios: list[Scenario]) -> list[Scenario]:
    """Mantém a ordem declarada, com os cenários `run_last` (que afetam o nó) no fim."""
    return [s for s in scenarios if not s.run_last] + [s for s in scenarios if s.run_last]
