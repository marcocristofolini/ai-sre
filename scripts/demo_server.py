#!/usr/bin/env python
"""Servidor de demonstração do AI SRE (dados SINTÉTICOS, só para ver o dashboard e tirar capturas de tela).

Sobe o app real (`aisre.api.main.create_app`) com o armazenamento em memória preenchido com incidentes
inventados, marcados como "dados de demonstração", e com um login local configurado:

    usuário: admin
    senha:   demo-aisre-nao-e-segredo      (senha só desta demonstração; o hash é gerado ao iniciar)

Uso (a partir da raiz do repositório, com o venv e o `web/dist` construído por `npm --prefix web run build`):

    .venv/bin/python scripts/demo_server.py --port 8765
    # abra http://127.0.0.1:8765/

O que é real e o que é inventado:
- Real: as rotas, a autenticação, o dashboard e o detector (Isolation Forest) da página Risco.
- Inventado: os incidentes/RCAs, as aprovações e as séries "do Prometheus" (um MockTransport local, sem rede).
- NÃO existe placar: `scoreboard_path` aponta para um arquivo inexistente, então a página Precisão mostra
  "Sem medição ainda". Nenhum número de precisão é fabricado e nada foi medido com modelo real.
- Na página Integrações, só o Prometheus aparece "configurada" (é o MockTransport local, necessário para a página Risco);
  LLM, Kubernetes, Loki, GitHub, Slack e Postgres ficam "não configurada".
- O segredo de sessão e o token do webhook são aleatórios a cada execução (ninguém consegue postar alertas).
- Escuta só em 127.0.0.1 e com cookie sem `Secure` (http local); não use isto fora da sua máquina.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from pydantic import SecretStr

from aisre.agent.loop import Alert
from aisre.api.main import create_app
from aisre.auth import DEFAULT_N, hash_password
from aisre.config import Settings
from aisre.db import MemoryStore
from aisre.schemas import RCA, Evidence

DEMO_USER = "admin"
DEMO_PASSWORD = "demo-aisre-nao-e-segredo"
DEMO_TAG = "dados de demonstração"
ROOT = Path(__file__).resolve().parents[1]

WORKLOADS = [
    {"name": "demo/pagamentos-api", "query": "sum(rate(demo_requests_total[5m]))"},
    {"name": "demo/relatorios-batch", "query": "sum(rate(demo_batch_jobs_total[5m]))"},
]


def _prometheus_transport() -> httpx.MockTransport:
    """Prometheus falso: histórico suficiente para `pagamentos-api` (detector "ativo", ponto normal) e
    histórico vazio para `relatorios-batch` (detector "aprendendo")."""

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("query", "")
        learning = "batch" in query
        if request.url.path == "/api/v1/query_range":
            values = [] if learning else [[i, str(10.0 + ((i * 37) % 100) / 50)] for i in range(300)]
            result = [{"metric": {}, "values": values}] if values else []
            return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": result}})
        return httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1, "11.0"]}]}},
        )

    return httpx.MockTransport(handler)


async def _investigator_off(alert: Alert) -> RCA:
    raise RuntimeError("investigação desativada no servidor de demonstração")


def _seed(store: MemoryStore, clock_box: list[datetime], now: datetime) -> None:
    async def run() -> None:
        def label(ns: str, **extra: str) -> dict[str, str]:
            return {"namespace": ns, "ambiente": DEMO_TAG, **extra}

        async def add(minutes_ago: int, name: str, labels: dict[str, str]) -> int:
            clock_box[0] = now - timedelta(minutes=minutes_ago)
            alert = Alert(name=f"{name} ({DEMO_TAG})", labels=labels, fired_at=clock_box[0])
            incident, _ = await store.create_if_new(alert, 300)
            return incident.id

        i1 = await add(190, "KubePodCrashLooping", label("demo", pod="relatorios-batch-6c8d-x2k9p"))
        await store.complete(i1, RCA(
            cause=f"[{DEMO_TAG}] O contêiner reinicia em loop porque a variável DATABASE_URL não está definida "
                  "no Deployment, e o processo encerra com erro na inicialização.",
            confidence=0.7,
            evidence=[
                Evidence(kind="log", ref="log:{pod=\"relatorios-batch-6c8d-x2k9p\"}",
                         summary="Erro fatal na inicialização: variável de ambiente DATABASE_URL ausente (dado sintético)."),
                Evidence(kind="k8s", ref="k8s:pod/relatorios-batch-6c8d-x2k9p",
                         summary="Estado CrashLoopBackOff, 14 reinícios, último motivo: Error (dado sintético)."),
            ],
            suggested_command="kubectl logs relatorios-batch-6c8d-x2k9p -n demo --previous",
        ))

        i2 = await add(120, "KubeNodeDiskPressure", label("kube-system", node="no-demo-2"))
        await store.complete(i2, RCA(
            cause=None, confidence=0.2, unknown=True,
            evidence=[
                Evidence(kind="k8s", ref="k8s:node/no-demo-2",
                         summary=f"Condição DiskPressure=True; sem outros sinais ({DEMO_TAG})."),
                Evidence(kind="metric", ref="metric:node_filesystem_avail_bytes",
                         summary="Espaço livre baixo, mas nada aponta o que o consumiu (dado sintético)."),
            ],
        ))

        i3 = await add(75, "KubeDeploymentReplicasMismatch", label("demo", deployment="catalogo"))
        await store.fail(i3, f"Tempo limite da investigação excedido ({DEMO_TAG}).")

        i4 = await add(40, "KubePodNotReady", label("demo", pod="checkout-5b7f-q8w2z"))
        await store.complete(i4, RCA(
            cause=f"[{DEMO_TAG}] A imagem checkout:2.4.1 não foi encontrada no registro (ImagePullBackOff): a tag "
                  "publicada no manifesto não existe.",
            confidence=0.6,
            evidence=[
                Evidence(kind="k8s", ref="k8s:pod/checkout-5b7f-q8w2z",
                         summary="Evento Failed: manifest for checkout:2.4.1 not found (dado sintético)."),
                Evidence(kind="commit", ref="commit:demo/checkout@a1b2c3d",
                         summary="Commit fictício que alterou a tag da imagem no manifesto (dado sintético)."),
            ],
            suggested_command="kubectl describe pod checkout-5b7f-q8w2z -n demo",
        ))
        await store.record_approval(i4, "web:admin", "kubectl describe pod checkout-5b7f-q8w2z -n demo")

        i5 = await add(9, "KubePodOOMKilled", label("demo", pod="pagamentos-api-7d9c-m4n8p"))
        await store.complete(i5, RCA(
            cause=f"[{DEMO_TAG}] O contêiner pagamentos-api foi encerrado por falta de memória (OOMKilled): o "
                  "limite de 256Mi é menor que o consumo observado no pico.",
            confidence=0.8,
            evidence=[
                Evidence(kind="k8s", ref="k8s:pod/pagamentos-api-7d9c-m4n8p",
                         summary="lastState.terminated.reason=OOMKilled, exitCode=137, limits.memory=256Mi (dado sintético)."),
                Evidence(kind="metric", ref="metric:container_memory_working_set_bytes{pod=\"pagamentos-api-7d9c-m4n8p\"}",
                         summary="Memória sobe até o limite antes de cada reinício (dado sintético)."),
            ],
            suggested_command="kubectl describe pod pagamentos-api-7d9c-m4n8p -n demo",
        ))

        i6 = await add(2, "KubeJobFailed", label("demo", job="exporta-relatorio"))
        assert i6  # fica "em investigação": nunca recebe `complete`

    asyncio.run(run())


def build_demo_app(*, hash_cost: int = DEFAULT_N, web_dist_dir: str = ""):
    """Monta o app com dados de demonstração. `hash_cost` menor só acelera os testes."""
    now = datetime.now(UTC)
    clock_box = [now]
    store = MemoryStore(clock=lambda: clock_box[0])
    _seed(store, clock_box, now)
    clock_box[0] = datetime.now(UTC)
    scoreboard_path = Path(tempfile.mkdtemp(prefix="aisre-demo-")) / "sem-placar.json"  # nunca existe
    settings = Settings(
        _env_file=None,
        admin_user=DEMO_USER,
        admin_password_hash=SecretStr(hash_password(DEMO_PASSWORD, n=hash_cost)),
        session_secret=SecretStr(secrets.token_urlsafe(32)),
        session_cookie_secure=False,  # http local
        webhook_token=SecretStr(secrets.token_urlsafe(32)),  # aleatório: ninguém posta alertas
        scoreboard_path=str(scoreboard_path),
        web_dist_dir=web_dist_dir,
        prometheus_url="http://prometheus-demo.invalid:9090",
        predict_workloads=WORKLOADS,
        min_baseline_points=20,
        predict_interval_seconds=30.0,
    )
    return create_app(settings, store, _investigator_off, prometheus_transport=_prometheus_transport())


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    dist = ROOT / "web" / "dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("web/dist não existe: rode `npm --prefix web run build` antes.")
    print(f"AI SRE demo ({DEMO_TAG}): http://127.0.0.1:{args.port}/  usuário={DEMO_USER}  senha={DEMO_PASSWORD}")
    uvicorn.run(build_demo_app(web_dist_dir=str(dist)), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
