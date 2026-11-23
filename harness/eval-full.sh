#!/usr/bin/env bash
# `make eval-full`: igual ao `make eval`, mas instala Prometheus (kube-prometheus-stack), Loki e Promtail no
# cluster kind e liga as ferramentas `prometheus` e `loki` do agente. Assim os cenários que dependem de
# métricas e logs deixam de ser "não medíveis". O GitHub continua ausente (cenários só de commit seguem fora).
#
# NÃO VERIFICADO: escrito sem helm, kind, Docker nem rede. Nenhum resultado foi produzido com este script.
# Requer: kind, kubectl, helm, docker e rede para baixar os charts.
# Versões dos charts: sem versão padrão (não foi possível consultá-las offline e nenhuma foi inventada). Defina-as:
#   helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
#   helm repo add grafana https://grafana.github.io/helm-charts && helm repo update
#   helm search repo prometheus-community/kube-prometheus-stack grafana/loki grafana/promtail
#   KPS_CHART_VERSION=... LOKI_CHART_VERSION=... PROMTAIL_CHART_VERSION=... make eval-full
# As ferramentas só entram no placar se REALMENTE configuradas: o harness deriva `enabled_tools` de
# build_tools(Settings), isto é, de AISRE_PROMETHEUS_URL / AISRE_LOKI_URL (definidos abaixo só depois que os
# port-forwards respondem). Se algo falhar antes, o script aborta e nada é medido como se existisse.
set -euo pipefail

: "${KPS_CHART_VERSION:?defina KPS_CHART_VERSION (versão do chart prometheus-community/kube-prometheus-stack)}"
: "${LOKI_CHART_VERSION:?defina LOKI_CHART_VERSION (versão do chart grafana/loki)}"
: "${PROMTAIL_CHART_VERSION:?defina PROMTAIL_CHART_VERSION (versão do chart grafana/promtail)}"

PYTHON="${PYTHON:-.venv/bin/python}"
CLUSTER="${KIND_CLUSTER:-aisre-eval}"
KUBECONFIG_EVAL="${KUBECONFIG_EVAL:-eval/kubeconfig}"
NS=observability
PROM_PORT="${PROM_PORT:-19090}"
LOKI_PORT="${LOKI_PORT:-13100}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PF_PIDS=()

cleanup() {
  for pid in "${PF_PIDS[@]:-}"; do [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; done
  kind delete cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG_EVAL" || true
  rm -f "$KUBECONFIG_EVAL"
}
trap cleanup EXIT

mkdir -p eval
kind create cluster --name "$CLUSTER" --config harness/kind-config.yaml --kubeconfig "$KUBECONFIG_EVAL"
export KUBECONFIG="$KUBECONFIG_EVAL"
"$HERE/set-disk-threshold.sh" "$CLUSTER-control-plane"
kubectl wait --for=condition=Ready node --all --timeout=180s

kubectl create namespace "$NS"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update
helm install kps prometheus-community/kube-prometheus-stack -n "$NS" --version "$KPS_CHART_VERSION" \
  -f "$HERE/full/kube-prometheus-stack-values.yaml" --wait --timeout 10m
helm install loki grafana/loki -n "$NS" --version "$LOKI_CHART_VERSION" \
  -f "$HERE/full/loki-values.yaml" --wait --timeout 10m
helm install promtail grafana/promtail -n "$NS" --version "$PROMTAIL_CHART_VERSION" \
  -f "$HERE/full/promtail-values.yaml" --wait --timeout 10m

kubectl -n "$NS" port-forward svc/kps-prometheus "$PROM_PORT":9090 >/dev/null 2>&1 &
PF_PIDS+=($!)
kubectl -n "$NS" port-forward svc/loki "$LOKI_PORT":3100 >/dev/null 2>&1 &
PF_PIDS+=($!)

wait_http() {  # url, rótulo: só segue se o serviço responde de verdade
  for _ in $(seq 1 60); do
    if curl -fsS "$1" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  echo "erro: $2 não respondeu em $1; abortando (nada será medido como se existisse)" >&2
  return 1
}
wait_http "http://127.0.0.1:$PROM_PORT/-/ready" Prometheus
wait_http "http://127.0.0.1:$LOKI_PORT/ready" Loki

AISRE_PROMETHEUS_URL="http://127.0.0.1:$PROM_PORT" AISRE_LOKI_URL="http://127.0.0.1:$LOKI_PORT" \
  AISRE_KUBERNETES_ENABLED=true "$PYTHON" -m aisre.harness.runner eval/scoreboard.json
