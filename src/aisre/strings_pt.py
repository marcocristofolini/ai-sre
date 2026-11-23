"""Todo texto de interface do produto, somente em português."""

ERRO_CAUSA_SEM_EVIDENCIA = "Uma causa só pode ser afirmada com ao menos uma evidência citada."
ERRO_DESCONHECIDO_COM_CAUSA = "Uma análise marcada como desconhecida não pode ter causa."
ERRO_SEM_CAUSA_NAO_DESCONHECIDO = (
    "Sem causa, a análise deve ser marcada como desconhecida."
)
ROTULO_CENARIOS_SINTETICOS = "cenários sintéticos"

# Ferramentas somente leitura
ERRO_FERRAMENTA_SOMENTE_LEITURA = "Operação bloqueada: as ferramentas são somente leitura."
ERRO_FERRAMENTA_TIMEOUT = "Tempo esgotado ao consultar o backend."
ERRO_FERRAMENTA_FALHA = "Falha ao consultar o backend: {detalhe}"
ERRO_FERRAMENTA_ARGUMENTO = "Argumento obrigatório ausente: {nome}"
ERRO_FERRAMENTA_TIPO_K8S = "Tipo de recurso Kubernetes não suportado: {tipo}"
AVISO_SAIDA_TRUNCADA = "\n[... saída truncada em {limite} caracteres ...]"

# LLM, consentimento e redação
MARCADOR_REDIGIDO = "[REDIGIDO]"
ERRO_CONSENTIMENTO_LLM = (
    "Este provedor de LLM é externo e os dados do incidente sairiam do seu ambiente. "
    "Defina AISRE_LLM_EXTERNAL_CONSENT=true para consentir explicitamente, ou use um provedor local."
)
ERRO_LLM_PROVEDOR_DESCONHECIDO = "Provedor de LLM não suportado: {nome}"
ERRO_LLM_SEM_CHAVE = "Provedor externo exige AISRE_LLM_API_KEY."
ERRO_LLM_SEM_URL_LOCAL = "Provedor local exige AISRE_LLM_BASE_URL."
ERRO_LLM_FALHA = "Falha ao chamar o provedor de LLM ({detalhe})."

# Agente de investigação
EVIDENCIA_COLETADA_SEM_CONCLUSAO = "Consulta executada com sucesso; dados coletados sem conclusão."
EVIDENCIA_CONSULTA_FALHOU = "Consulta falhou: {erro}"
ERRO_FERRAMENTA_SEM_DETALHE = "erro não informado"
ERRO_EVAL_CONFIGURACAO = "erro de configuração do agente no harness: {detalhe}"
AVISO_LLM_FALHA_INVESTIGACAO = "falha do provedor de LLM durante a investigação ({tipo}); resultado: não sei"

# API, armazenamento e memória
ERRO_WEBHOOK_JSON_INVALIDO = "Corpo da requisição não é um JSON válido."
ERRO_WEBHOOK_PAYLOAD_INVALIDO = "Payload do Alertmanager inválido: esperado um objeto com a lista 'alerts'."
ERRO_WEBHOOK_SEM_ALERTAS = "Payload do Alertmanager sem alertas."
ERRO_WEBHOOK_ALERTA_INVALIDO = "Alerta inválido no índice {indice}: {detalhe}"
ERRO_ALERTA_SEM_NOME = "o rótulo 'alertname' é obrigatório"
ERRO_INCIDENTE_NAO_ENCONTRADO = "Incidente não encontrado."
ERRO_INVESTIGACAO_CONSENTIMENTO = (
    "Investigação não executada por erro de configuração: " + ERRO_CONSENTIMENTO_LLM
)
ERRO_INVESTIGACAO_FALHOU = "A investigação falhou ({tipo})."
ERRO_INVESTIGACAO_TIMEOUT = "A investigação excedeu o tempo limite de {segundos}s."
AVISO_HOOK_RCA_FALHOU = "hook on_rca_ready falhou ({tipo})"
AVISO_MEMORIA_FALHOU = "não foi possível salvar o incidente na memória ({tipo})"
AVISO_STORE_EM_MEMORIA = "AISRE_DATABASE_URL não definido: incidentes ficam apenas em memória e são perdidos ao reiniciar."
