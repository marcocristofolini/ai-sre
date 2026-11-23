PYTHON ?= .venv/bin/python
KIND_CLUSTER ?= aisre-eval
KUBECONFIG_EVAL ?= eval/kubeconfig

.PHONY: eval
# Cria o cluster kind, roda o harness (placar em eval/scoreboard.json) e remove o cluster.
eval:
	@mkdir -p eval
	kind create cluster --name $(KIND_CLUSTER) --config harness/kind-config.yaml --kubeconfig $(KUBECONFIG_EVAL)
	KUBECONFIG=$(KUBECONFIG_EVAL) $(PYTHON) -m aisre.harness.runner eval/scoreboard.json; \
	status=$$?; \
	kind delete cluster --name $(KIND_CLUSTER) --kubeconfig $(KUBECONFIG_EVAL); \
	rm -f $(KUBECONFIG_EVAL); \
	exit $$status
