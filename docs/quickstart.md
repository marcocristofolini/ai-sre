# Quickstart: instalar num cluster kind em ~5 minutos

Passo a passo **verificado em 2026-11-25** (kind + Docker, no WSL): build da imagem, `helm install`, login e as rotas
principais respondendo como esperado. Não inclui LLM nem Prometheus: serve para ver o produto no ar. Para investigar
alertas de verdade você precisa configurar um LLM (veja o README) e, opcionalmente, Prometheus, Loki e GitHub.

Requisitos: Docker, `kind`, `kubectl`, `helm` e Python 3.12 (só para gerar o hash da senha).

```bash
git clone https://github.com/marcocristofolini/ai-sre && cd ai-sre
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'

# 1. Imagem local e cluster
docker build -t aisre-local:test .
kind create cluster --name aisre
kind load docker-image aisre-local:test --name aisre

# 2. Segredos (a senha abaixo é só de exemplo; troque)
kubectl create namespace aisre
HASH=$(.venv/bin/python -c "from aisre.auth import hash_password; print(hash_password('troque-esta-senha'))")
kubectl -n aisre create secret generic aisre-secrets \
  --from-literal=admin-password-hash="$HASH" \
  --from-literal=session-secret="$(.venv/bin/python -c 'import secrets;print(secrets.token_hex(32))')" \
  --from-literal=webhook-token="$(.venv/bin/python -c 'import secrets;print(secrets.token_hex(32))')"
kubectl -n aisre create secret generic aisre-pg \
  --from-literal=postgres-password="pg$(.venv/bin/python -c 'import secrets;print(secrets.token_hex(12))')"

# 3. Instalação
helm install aisre deploy/helm/aisre -n aisre \
  --set image.repository=aisre-local --set image.tag=test --set image.pullPolicy=Never \
  --set secrets.existingSecret=aisre-secrets --set postgres.auth.existingSecret=aisre-pg \
  --set auth.cookieSecure=false --set postgres.persistence.size=1Gi --wait --timeout 6m

# 4. Acesso
kubectl -n aisre port-forward svc/aisre 8080:80
```

Abra <http://localhost:8080>, entre com `admin` e a senha que você escolheu. `auth.cookieSecure=false` é só para
`port-forward` em http; em produção deixe `true` e publique por HTTPS.

Sem LLM configurado (`llm.local.url` e `llm.local.model`), as investigações falham. O chart avisa isso na instalação.

## Para ver o dashboard só com dados de demonstração (sem cluster)

```bash
npm --prefix web ci && npm --prefix web run build
.venv/bin/python scripts/demo_server.py    # http://localhost:8765, login admin / demo-aisre-nao-e-segredo
```

## Limpeza

```bash
kind delete cluster --name aisre
```

Quando as imagens e o chart do GHCR forem públicos, dá para instalar sem build local:
`helm install aisre oci://ghcr.io/marcocristofolini/charts/aisre --version <versão> ...` (os pacotes novos do GHCR nascem
privados; veja a seção de Releases do README).
