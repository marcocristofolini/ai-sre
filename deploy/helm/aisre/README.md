# Chart Helm do AI SRE

Instala no seu cluster a API (FastAPI), o dashboard (React, servido pela própria API na mesma imagem) e,
opcionalmente, um Postgres com pgvector. Um cliente por instalação; os dados ficam no seu cluster.

## Pré-requisitos

- Kubernetes 1.25+ e Helm 3.
- Imagem do app publicada no seu registro (a partir da raiz do repositório):
  `docker build -t REGISTRO/aisre:0.1.0 .` e `docker push REGISTRO/aisre:0.1.0`.
  O `Dockerfile` fica na raiz do repositório. A tag padrão é o `appVersion` do chart; `latest` é recusado.
- Prometheus e, opcionalmente, Loki e GitHub acessíveis de dentro do cluster.
- Um `StorageClass` padrão (para o PVC do Postgres) ou um Postgres externo com a extensão `vector`.

## Instalação

```bash
kubectl create namespace aisre
# crie os Secrets (próxima seção) e então:
helm install aisre deploy/helm/aisre -n aisre -f meus-valores.yaml \
  --set image.repository=REGISTRO/aisre
```

Exemplo mínimo de `meus-valores.yaml` (sem segredos):

```yaml
secrets:
  existingSecret: aisre-secrets
postgres:
  auth:
    existingSecret: aisre-postgres
llm:
  provider: local
  local:
    url: http://ollama.llm.svc:11434/v1
    model: llama3.1
prometheus:
  url: http://prometheus-server.monitoring.svc:80
loki:
  url: http://loki.monitoring.svc:3100
slack:
  channel: C0123456789
  approvers: ["U012ABC"]
github:
  repo: minha-org/meu-repo
predict:
  workloads:
    - name: producao/api
      query: sum(container_memory_working_set_bytes{namespace="producao",pod=~"api-.*"})
```

## Criando os Secrets

Nenhum segredo tem valor padrão no chart. Chaves do Secret da aplicação (`secrets.existingSecret`):

| Chave | Obrigatória | Uso |
|---|---|---|
| `admin-password-hash` | sim | hash scrypt da senha do admin |
| `session-secret` | sim | segredo de assinatura da sessão |
| `llm-api-key` | só com `anthropic` | chave do provedor LLM |
| `slack-bot-token` | não | token do bot do Slack |
| `slack-signing-secret` | não | segredo de assinatura das ações do Slack |
| `github-token` | não | token **somente leitura** do GitHub |
| `webhook-token` | sim, para receber alertas | token Bearer exigido em `/alerts/webhook` (sem ele: 503) |

Gere o hash da senha e o segredo de sessão com o utilitário do app (a senha é lida sem eco):

```bash
python -m aisre.auth hash      # imprime o hash scrypt
python -m aisre.auth secret    # imprime um segredo aleatório para a sessão (use também para o webhook-token)
```

```bash
kubectl -n aisre create secret generic aisre-secrets \
  --from-literal=admin-password-hash='scrypt$...' \
  --from-literal=session-secret='...' \
  --from-literal=slack-bot-token='xoxb-...' \
  --from-literal=slack-signing-secret='...' \
  --from-literal=github-token='...' \
  --from-literal=webhook-token="$(openssl rand -hex 32)"
kubectl -n aisre create secret generic aisre-postgres \
  --from-literal=postgres-password="$(openssl rand -hex 24)"
```

Use `'` (aspas simples) no hash: ele contém `$`. A senha do Postgres deve usar só `[A-Za-z0-9_-]`,
pois entra numa URL de conexão.

Alternativa: `secrets.create=true` faz o chart criar o Secret **somente** com as chaves de `secrets.values.*`
que você informar (use `--set-file`; evite gravar valores em arquivos versionados). Os valores padrão são vazios.

### Banco externo

`postgres.enabled=false` e `postgres.external.existingSecret` (Secret com a chave `database-url`, uma URL
`postgresql://usuario:senha@host:5432/banco`). A extensão `vector` precisa existir (a migração executa
`CREATE EXTENSION IF NOT EXISTS vector`, o que exige permissão adequada). `postgres.external.url` também
funciona, mas a URL contém credenciais: prefira o Secret existente.

## Slack, Prometheus, Loki e GitHub

- Slack: `slack.channel`, `slack.approvers` (IDs de usuário autorizados a aprovar) e, no Secret,
  `slack-bot-token` e `slack-signing-secret`. Aponte "Interactivity" do app Slack para `https://SEU-HOST/slack/actions`
  (a assinatura do Slack é verificada). Sem os três itens, as ações respondem 503.
- Prometheus: `prometheus.url`; Loki: `loki.url`. Ferramentas sem URL ficam desligadas.
- GitHub: `github.repo` (`owner/repo`) e `github-token` no Secret. Ambos são necessários.
- Preditivo: `predict.workloads` (vazio = desligado), cada item com `name` (`namespace/workload`) e `query`
  (PromQL que devolve UMA série).
- Kubernetes: `kubernetes.enabled` (padrão `true`) liga a ferramenta e o RBAC somente leitura.

## Consentimento de LLM externo

`llm.externalConsent` é `false` por padrão e é repassado sem alteração a `AISRE_LLM_EXTERNAL_CONSENT`.
O provedor `anthropic` é externo: sem consentimento, o app **não envia nada** e a investigação do incidente falha
com erro de consentimento (a recusa acontece em tempo de execução; por isso o chart não falha na renderização,
apenas avisa nas notas pós-instalação). Para autorizar: `--set llm.externalConsent=true`, ciente de que contexto
do incidente (com redação de segredos) será enviado ao provedor. Um endpoint `llm.local.url` fora do cluster/rede
privada também é tratado como externo. Para manter tudo no cluster, use `llm.provider=local` com um endpoint interno.

## extraEnv

`extraEnv` adiciona variáveis não secretas ao ConfigMap. O chart falha na renderização se a chave for uma variável
já gerenciada por valores dedicados (por exemplo `AISRE_LLM_EXTERNAL_CONSENT`, que só pode ser mudada por
`llm.externalConsent`) ou se o nome contiver `TOKEN`, `SECRET`, `PASSWORD`, `KEY` ou `HASH`. Com `llm.provider=local`,
informe `llm.local.model` (se vazio, `AISRE_LLM_MODEL` não é emitido e as notas avisam).

## Acesso ao dashboard

```bash
kubectl -n aisre port-forward svc/aisre 8080:80
```

O cookie de sessão é `Secure` por padrão (`auth.cookieSecure=true`), então via `http://localhost` o login não persiste.
Para teste local use `--set auth.cookieSecure=false` (nunca em produção). Em produção, habilite `ingress.*` com TLS.

Atrás de um Ingress, o limite de login por IP precisa do IP real do cliente: a imagem roda o uvicorn com
`--proxy-headers`, e `proxy.forwardedAllowIps` (variável `FORWARDED_ALLOW_IPS`) diz de quais proxies o
`X-Forwarded-For` é aceito. Padrão `127.0.0.1` (restritivo: atrás do Ingress todos parecem vir do mesmo IP, e
5 falhas de qualquer um bloqueiam o login por IP para todos). Informe o(s) IP/CIDR dos pods do seu controlador
de Ingress, ex.: `--set proxy.forwardedAllowIps=10.42.0.0/16`. Não use `*` se o Service for alcançável sem
passar pelo proxy. Por usuário não há bloqueio rígido: após 5 falhas, cada tentativa espera um atraso
progressivo (1s, 2s, 4s… até 30s).

## Enviando um alerta de teste

Formato do Alertmanager (campos aceitos: `alerts[].status`, `alerts[].labels` com `alertname`, `alerts[].startsAt`;
só alertas `firing` abrem incidente):

```bash
TOKEN=$(kubectl -n aisre get secret aisre-secrets -o jsonpath='{.data.webhook-token}' | base64 -d)
curl -X POST http://localhost:8080/alerts/webhook -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" -d '{
  "alerts": [{
    "status": "firing",
    "labels": {"alertname": "TesteAlerta", "namespace": "default", "pod": "meu-pod"},
    "startsAt": "2026-01-01T00:00:00Z"
  }]
}'
```

Resposta `202` com o `incident_id`; o RCA aparece no dashboard quando a investigação termina. Sem o token
correto a resposta é `401`; sem `webhook-token` configurado, `503` (falha fechado). Só alertas `firing` contam
para `AISRE_MAX_ALERTS_PER_REQUEST`: o excesso é descartado com `202` e o campo `truncated` (o Alertmanager não
reenvia respostas 4xx). Com a fila de investigações cheia (`AISRE_MAX_PENDING_INVESTIGATIONS`), `503` (o
Alertmanager reenvia).

### Configurando o Alertmanager

Monte o token num Secret do namespace do Alertmanager e use `http_config.authorization`:

```yaml
receivers:
  - name: aisre
    webhook_configs:
      - url: http://aisre.aisre.svc/alerts/webhook   # Service interno, não o Ingress
        send_resolved: false
        http_config:
          authorization:
            type: Bearer
            credentials_file: /etc/alertmanager/secrets/aisre-webhook/token
```

`webhook.allowUnauthenticated=true` aceita alertas sem token quando `webhook-token` não existe: **só em
desenvolvimento**.

## Segurança

- Somente leitura: o `ClusterRole` tem apenas `get`, `list`, `watch` em `pods`, `nodes` e `events` (exatamente o que a
  ferramenta `kubernetes` lê). Não há acesso a `secrets` nem verbos de escrita.
- v1 só **sugere** comandos e **registra** a aprovação humana; nada é executado no cluster.
- Contêineres: `runAsNonRoot`, `readOnlyRootFilesystem` (app), capabilities `ALL` removidas, seccomp `RuntimeDefault`.
- `/alerts/webhook` exige `Authorization: Bearer <webhook-token>` (comparação em tempo constante); requisições
  sem autenticação são recusadas (`401`), e sem token configurado o webhook falha fechado (`503`). A fila de
  investigações é limitada (`AISRE_MAX_PENDING_INVESTIGATIONS`) para conter abuso de custo de LLM.
- O Ingress padrão (`path: /`) também expõe `/alerts/webhook`. O Slack precisa de `/slack/actions` público, então
  restringir só por NetworkPolicy não basta. Recomendado: o Alertmanager usa o Service interno e o controlador de
  Ingress bloqueia `/alerts/webhook` (ex.: ingress-nginx com `nginx.ingress.kubernetes.io/server-snippet:
  "location = /alerts/webhook { return 404; }"`, se snippets estiverem permitidos, ou uma regra equivalente do seu
  controlador). Não verificado em cluster real.
- `networkPolicy.enabled=true` com `networkPolicy.ingressFrom` limita quem alcança o serviço dentro do cluster
  (inclua o controlador de Ingress se usar Ingress).
- O texto do RCA e o comando sugerido vêm de um LLM lendo dados não confiáveis (rótulos, logs): revise o comando
  antes de executá-lo manualmente.
- `/slack/actions` valida a assinatura do Slack; o dashboard exige login.

## Limitações conhecidas

- 1 réplica (limitador de login, deduplicação em andamento e preditivo vivem em memória); estratégia `Recreate`.
- Sem backup do Postgres, HA ou rotação de segredos no chart.
- Egress não é restrito pela NetworkPolicy.
- O Postgres roda com `readOnlyRootFilesystem: false` (emptyDirs montados em `/tmp` e `/var/run/postgresql`).
- O placar de precisão (`/scoreboard`) mostra "não medido" até haver um `eval/scoreboard.json` no contêiner.

## O que NÃO foi verificado

Este chart foi escrito e testado apenas de forma estática (testes em `tests/deploy`, PyYAML e inspeção de texto):

- NÃO foi executado `helm lint` nem `helm template` (o `helm` não estava disponível); os testes de renderização
  são pulados sem o binário e devem ser rodados onde ele existir.
- NÃO foi feita instalação em cluster real (nem `kind`), nem envio de alerta de teste ponta a ponta.
- NÃO foi feito `docker build` da imagem; a execução do Postgres (pgvector) como não-root com os emptyDirs também não foi exercitada.
