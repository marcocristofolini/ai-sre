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
ERRO_WEBHOOK_MUITO_GRANDE = "Corpo da requisição excede o limite de {limite} bytes."
ERRO_WEBHOOK_ALERTAS_DEMAIS = "Payload com {total} alertas; o máximo por requisição é {limite}."
ERRO_TEXTO_INVALIDO = "texto inválido (caractere nulo ou codificação inválida)"
ERRO_ARMAZENAMENTO_INDISPONIVEL = "Falha ao registrar um ou mais alertas; tente novamente."
ERRO_INVESTIGACAO_INTERROMPIDA = "A investigação foi interrompida (reinício ou desligamento do serviço)."
AVISO_STORE_FALHOU = "falha ao gravar o resultado da investigação do incidente {id} ({tipo})"
AVISO_STORE_ALERTA_FALHOU = "falha ao registrar alerta no armazenamento ({tipo})"
AVISO_INCIDENTES_ORFAOS = "{n} incidente(s) em investigação antigos marcados como falhos na inicialização."

# Slack
SLACK_TITULO_RCA = "Incidente #{id}: causa provável"
SLACK_TITULO_DESCONHECIDO = "Incidente #{id}: não sei a causa"
SLACK_DESCONHECIDO_CORPO = "Não sei a causa deste incidente. Evidências coletadas:"
SLACK_SEM_EVIDENCIAS = "Nenhuma evidência coletada."
SLACK_CAUSA = "*Causa:* {causa}"
SLACK_CONFIANCA = "*Confiança:* {confianca}%"
SLACK_EVIDENCIAS = "*Evidências:*"
SLACK_EVIDENCIA_LINHA = "• [{tipo}] {ref}: {resumo}"
SLACK_EVIDENCIAS_OMITIDAS = "… e mais {n} evidência(s) omitida(s)."
SLACK_COMANDO = "*Comando sugerido (não é executado automaticamente):*"
SLACK_BOTAO_APROVAR = "Aprovar"
SLACK_AVISO_FALHA_API = "falha ao publicar RCA no Slack ({tipo})"
SLACK_AVISO_ERRO_API = "o Slack recusou a publicação do RCA (erro: {codigo})"
SLACK_AVISO_HTTP = "o Slack respondeu HTTP {status} ao publicar o RCA"
SLACK_CODIGO_DESCONHECIDO = "desconhecido"
ERRO_SLACK_NAO_CONFIGURADO = "Integração com o Slack não configurada."
ERRO_SLACK_ASSINATURA = "Assinatura do Slack inválida."
ERRO_SLACK_CORPO_GRANDE = "Corpo da requisição excede o limite de {limite} bytes."
ERRO_SLACK_PAYLOAD = "Payload de ação do Slack inválido."
ERRO_SLACK_NAO_APROVADOR = "Usuário não autorizado a aprovar."
ERRO_SLACK_SEM_COMANDO = "O incidente não tem comando sugerido para aprovar."
SLACK_APROVACAO_REGISTRADA = "Aprovação registrada; nada foi executado."
SLACK_COMANDO_NAO_EXIBIVEL = "O comando sugerido não pode ser exibido por inteiro e sem alterações; por isso não há botão de aprovação."
