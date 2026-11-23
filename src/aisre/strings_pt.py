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
