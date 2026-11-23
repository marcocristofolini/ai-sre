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
