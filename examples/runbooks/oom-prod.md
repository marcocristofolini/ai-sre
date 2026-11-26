---
titulo: Container morto por falta de memória (produção)
alertas: KubeContainerOOM*, *OOMKilled*
rotulos: namespace=prod-.*
---
Sintoma: `OOMKilled` em namespaces de produção.

1. `kubectl top pod -n NAMESPACE --containers`: consumo atual versus limite.
2. No Prometheus, observe `container_memory_working_set_bytes` nas últimas horas: crescimento contínuo sugere vazamento;
   salto após deploy sugere mudança de código ou de limite.
3. `kubectl rollout history deploy/NOME -n NAMESPACE`: o salto coincide com uma revisão?
