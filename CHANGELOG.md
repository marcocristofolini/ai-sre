# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/); versionamento semântico.

## [Não lançado]

- **Runbooks:** Markdown do operador (`AISRE_RUNBOOKS_DIR`, Helm `runbooks.configMap`) oferecido ao agente como contexto não
  confiável, sem id citável (nunca evidência). O RCA lista os runbooks oferecidos (`runbooks`) e o dashboard mostra o
  cartão. Veja `docs/runbooks.md` e `examples/runbooks/`.

## [0.1.1] - 2026-11-26

- **Chart:** novo initContainer `wait-for-postgres`. Na primeira instalação o app subia antes do Postgres, estourava o
  `PoolTimeout` e só funcionava depois de um restart (encontrado ao instalar o chart num kind real).
- Adiciona `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `docs/quickstart.md`, `README.en.md` e templates de issue/PR.

## [0.1.0] - 2026-11-25

Primeira versão. Somente leitura: investiga alertas e entrega causa provável com evidências citadas e confiança;
comandos são apenas sugeridos.

- Agente com Kubernetes, Prometheus, Loki, GitHub e Slack; memória de incidentes; detecção preditiva (Isolation
  Forest); LLM próprio (Anthropic ou endpoint compatível com OpenAI) com consentimento e redação.
- Dashboard em português, instalação por Helm, imagem e chart publicados no GHCR.
- Harness de precisão com cluster `kind` (`make eval`, `make eval-full`). **Medição preliminar e ruidosa**: veja o
  README. O provedor local repete falhas transitórias (429/5xx/timeouts) com backoff.
