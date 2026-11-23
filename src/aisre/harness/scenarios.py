from pydantic import BaseModel


class Scenario(BaseModel):
    id: str
    fault: str
    expected_cause: str
    expected_evidence_refs: list[str]


SCENARIOS: list[Scenario] = [
    Scenario(
        id="oom-kill",
        fault="memory-leak",
        expected_cause="OOMKilled",
        expected_evidence_refs=["k8s:pod/lastState.terminated.reason=OOMKilled"],
    ),
    Scenario(
        id="crashloop-bad-config",
        fault="bad-config",
        expected_cause="CrashLoopBackOff por configuração inválida",
        expected_evidence_refs=["k8s:configmap/app-config", "log:app/config-error"],
    ),
    Scenario(
        id="bad-deploy-latency",
        fault="bad-deploy",
        expected_cause="Deploy recente aumentou a latência",
        expected_evidence_refs=["commit:deploy/latest", "metric:http_request_duration_p99"],
    ),
    Scenario(
        id="node-disk-pressure",
        fault="disk-fill",
        expected_cause="DiskPressure no nó",
        expected_evidence_refs=["k8s:node/conditions/DiskPressure"],
    ),
    Scenario(
        id="dns-failure",
        fault="dns-break",
        expected_cause="Falha de resolução DNS",
        expected_evidence_refs=["log:dns-client/dns-error", "k8s:pod/dns-client/dnsConfig"],
    ),
]
