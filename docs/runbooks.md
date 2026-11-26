# Runbooks

Runbooks são roteiros em Markdown escritos por você ("quando o alerta X dispara, olhe Y"). O agente os recebe como
**contexto**, para investigar do jeito que o seu time investiga. Eles **não são evidência**.

## Formato

Um arquivo `.md` por runbook, direto no diretório (subpastas são ignoradas, máximo 64 KB por arquivo):

```markdown
---
titulo: Pod em CrashLoopBackOff
alertas: KubePodCrashLooping, *CrashLoop*
rotulos: namespace=prod-.*, severity=critical
---
Corpo em Markdown com os passos de diagnóstico.
```

| Campo | Obrigatório | Regra |
|---|---|---|
| `titulo` | não | Se faltar, usa o nome do arquivo (sem `.md`). |
| `alertas` | **sim** | Globs separados por vírgula, comparados com o nome do alerta (sem diferenciar maiúsculas). |
| `rotulos` | não | `chave=regex` separados por vírgula; o valor do rótulo do alerta precisa casar **inteiro** com a regex. |

No cabeçalho, `#` começa um comentário até o fim da linha (não use `#` em regex).

Arquivos sem cabeçalho, sem `alertas`, vazios ou com regex inválida são ignorados e registrados no log (aviso com o
nome do arquivo, sem o conteúdo). Exemplos prontos em [`examples/runbooks/`](../examples/runbooks).

## Seleção

Para cada alerta, o agente recebe até `AISRE_RUNBOOKS_TOP_K` runbooks (padrão 2; 0 desativa). Quando mais de um casa,
os mais específicos (mais `rotulos`) vêm primeiro; empate se resolve por nome do arquivo. Cada corpo é cortado em
`AISRE_RUNBOOKS_MAX_CHARS` (padrão 4000).

## Segurança

- O runbook entra no prompt como bloco **não confiável** (`origem="runbooks"`), delimitado e redigido como qualquer
  outro dado, **sem id citável**: não aumenta a confiança nem dispensa o "não sei".
- O agente só pode transformá-lo em comandos **somente leitura** sugeridos (`suggested_command`); nada é executado.
- Só arquivos locais são lidos; não há busca na rede.
- O RCA mostra quais runbooks foram oferecidos (campo `runbooks`, cartão no dashboard).
- Tudo que vai ao LLM passa pelo consentimento e pela redação de segredos, runbooks incluídos.

## Configuração

Local ou Docker Compose:

```bash
export AISRE_RUNBOOKS_DIR=./examples/runbooks
export AISRE_RUNBOOKS_TOP_K=2        # opcional
export AISRE_RUNBOOKS_MAX_CHARS=4000 # opcional
```

Kubernetes (Helm): crie um ConfigMap e aponte o chart para ele. O diretório é montado somente leitura em
`/app/runbooks`.

```bash
kubectl create configmap meus-runbooks -n aisre --from-file=examples/runbooks/
helm upgrade --install aisre ... --set runbooks.configMap=meus-runbooks --set runbooks.topK=2
```

Os runbooks são lidos na inicialização: depois de alterar o ConfigMap, reinicie o deployment
(`kubectl rollout restart deploy/aisre -n aisre`).
