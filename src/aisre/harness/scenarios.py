from pydantic import BaseModel


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


SCENARIOS: list[Scenario] = [
    Scenario(
        id="oom-kill",
        namespace="eval-1",
        fault="memory-leak",
        expected_cause="OOMKilled",
        expected_evidence_refs=["k8s:pod", "k8s:events"],
    ),
    Scenario(
        id="crashloop-bad-config",
        namespace="eval-2",
        fault="bad-config",
        expected_cause="CrashLoopBackOff por configuração inválida",
        expected_evidence_refs=["log:", "k8s:pod", "k8s:events"],
    ),
    Scenario(
        id="bad-deploy-latency",
        namespace="eval-3",
        fault="bad-deploy",
        expected_cause="Deploy recente aumentou a latência",
        expected_evidence_refs=["commit:", "metric:"],
    ),
    Scenario(
        id="node-disk-pressure",
        namespace="eval-4",
        fault="disk-fill",
        expected_cause="DiskPressure no nó",
        expected_evidence_refs=["k8s:nodes", "k8s:events"],
    ),
    Scenario(
        id="dns-failure",
        namespace="eval-5",
        fault="dns-break",
        expected_cause="Falha de resolução DNS",
        expected_evidence_refs=["log:"],
    ),
]
