---
titulo: Pod em CrashLoopBackOff
alertas: KubePodCrashLooping, *CrashLoop*
---
Sintoma: o pod reinicia em laço.

Passos de diagnóstico (somente leitura):

1. `kubectl describe pod NOME -n NAMESPACE`: veja "Last State" e "Reason" (OOMKilled, Error, Completed).
2. `kubectl logs NOME -n NAMESPACE --previous`: logs da execução que caiu.
3. Compare com o último deploy: houve mudança de imagem, variável de ambiente ou limite de memória?

Causas comuns neste time: segredo ausente após rotação, limite de memória baixo, dependência (banco) indisponível.
