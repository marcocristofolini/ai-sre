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
	harness/set-disk-threshold.sh $(KIND_CLUSTER)-control-plane && \
	kubectl --kubeconfig $(KUBECONFIG_EVAL) wait --for=condition=Ready node --all --timeout=180s; \
	status=$$?; [ $$status -eq 0 ] || { kind delete cluster --name $(KIND_CLUSTER) --kubeconfig $(KUBECONFIG_EVAL); rm -f $(KUBECONFIG_EVAL); exit $$status; }
	KUBECONFIG=$(KUBECONFIG_EVAL) AISRE_KUBERNETES_ENABLED=true $(PYTHON) -m aisre.harness.runner eval/scoreboard.json; \
	status=$$?; \
	kind delete cluster --name $(KIND_CLUSTER) --kubeconfig $(KUBECONFIG_EVAL); \
	rm -f $(KUBECONFIG_EVAL); \
	exit $$status
