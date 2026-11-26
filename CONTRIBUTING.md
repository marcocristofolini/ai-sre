# Como contribuir

Obrigado por ajudar. O projeto é Apache-2.0 e aceita contribuições por pull request. Idioma: português ou inglês nas
issues e PRs; a interface do produto e os textos do usuário ficam em português (`src/aisre/strings_pt.py`).

## Princípios que não mudam

- **Somente leitura.** O produto só sugere comandos e registra a aprovação humana; nada é executado. Mudanças que
  executem ações em clusters ou serviços não serão aceitas.
- **Evidência ou "não sei".** Toda causa precisa citar evidência real; sem evidência, a resposta é "não sei".
- **LLM externo só com consentimento explícito** e com redação de segredos em todo texto enviado.
- **Honestidade nos números.** Nenhuma medição de precisão entra no README sem data, commit, modelo, ferramentas e
  ressalvas. Não ajuste o corretor (`src/aisre/harness/scenarios.py`) depois de ver um resultado para melhorar a nota.

## Ambiente de desenvolvimento

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests -q          # use o .venv; o Python do sistema não tem as dependências
npm --prefix web ci && npm --prefix web test
```

Os testes de renderização do chart exigem `helm`; sem ele são pulados. O placar sintético exige `kind`, `kubectl` e
Docker (`make eval`); `make eval-full` também exige `helm` e as versões dos charts (veja o README).

## Pull requests

1. Abra uma issue antes de mudanças grandes.
2. Escreva o teste primeiro; a suíte inteira (`pytest tests -W error` e os testes do `web/`) precisa passar.
3. Mantenha o PR pequeno e com um propósito. Commits no formato `tipo(escopo): descrição` (`feat`, `fix`, `test`,
   `docs`, `ci`).
4. Mudanças de comportamento do agente, do harness ou do README que citem números devem trazer a execução que as
   sustenta.

## Segurança

Vulnerabilidades não vão em issue pública: veja `SECURITY.md`.
