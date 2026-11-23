# Segurança

Licença do projeto: Apache-2.0 (arquivo `LICENSE`). O software é fornecido "como está", sem garantias.

## Como reportar uma vulnerabilidade

Não abra issue pública para vulnerabilidades. Use o recurso de "Report a vulnerability" (GitHub Security Advisories)
do repositório, ou contate os mantenedores em particular. Inclua passos para reproduzir, versão/commit afetado e
impacto. Não teste contra instalações de terceiros.

## Modelo de ameaça resumido (v1)

- O produto é **somente leitura**: o RBAC do chart tem só `get/list/watch` em `pods`, `nodes` e `events`; as
  ferramentas HTTP recusam qualquer método que não seja `GET`. Aprovar um comando só registra a aprovação.
- Entradas não confiáveis: rótulos de alertas, logs, métricas e mensagens de commit chegam ao LLM. O RCA e o comando
  sugerido podem ser influenciados por prompt injection; por isso são exibidos como texto puro (escapado no Slack,
  `verbatim`, sem unfurl), comandos com caracteres invisíveis/de controle/bidi não ganham botão de aprovação, e há um
  aviso explícito para revisar antes de executar manualmente.
- `/alerts/webhook`: token Bearer obrigatório (comparação em tempo constante), falha fechado sem token; fila de
  investigações limitada (`AISRE_MAX_PENDING_INVESTIGATIONS`) para conter abuso de custo de LLM.
- `/slack/actions`: assinatura do Slack verificada. Dashboard: login local com scrypt, cookie `HttpOnly`/`Secure`/
  `SameSite=Lax`, checagem de `Origin`, limite por IP e atraso progressivo por usuário.
- LLM externo só com consentimento explícito; redação de segredos em todo texto enviado; erros de ferramenta não
  carregam credenciais de URL (só o status HTTP); falhas são registradas pelo tipo da exceção.

## Limitações conhecidas

- Não verificado em cluster real, com modelo real, nem com o chart instalado; o placar de precisão ainda não existe.
- A redação de segredos é baseada em padrões: segredos em formatos desconhecidos podem passar.
- O Ingress padrão (`path: /`) também expõe `/alerts/webhook` (protegido por token); bloqueie-o no controlador de
  Ingress se o Alertmanager usar o Service interno (veja o README do chart).
- O limite de login e a deduplicação vivem em memória (1 réplica). Atrás de proxy, configure
  `proxy.forwardedAllowIps` / `FORWARDED_ALLOW_IPS`, senão todos os clientes parecem vir do IP do proxy.
- O admin do dashboard pode aprovar independentemente de `AISRE_SLACK_APPROVERS`.
- Sem rotação de segredos, backup do Postgres ou restrição de egress no chart.
