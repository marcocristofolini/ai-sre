PYTHON ?= .venv/bin/python
KIND_CLUSTER ?= aisre-eval
KUBECONFIG_EVAL ?= eval/kubeconfig

.PHONY: eval
# Cria o cluster kind, roda o harness (placar em eval/scoreboard.json) e remove o cluster.
# Por padrão o investigador responde sempre "desconhecido"; AISRE_EVAL_AGENT=1 make eval usa o agente real
# (provedor e ferramentas configurados por AISRE_*).
eval:
	@mkdir -p eval
	kind create cluster --name $(KIND_CLUSTER) --config harness/kind-config.yaml --kubeconfig $(KUBECONFIG_EVAL)
	KUBECONFIG=$(KUBECONFIG_EVAL) $(PYTHON) -m aisre.harness.runner eval/scoreboard.json; \
	status=$$?; \
	kind delete cluster --name $(KIND_CLUSTER) --kubeconfig $(KUBECONFIG_EVAL); \
	rm -f $(KUBECONFIG_EVAL); \
	exit $$status
