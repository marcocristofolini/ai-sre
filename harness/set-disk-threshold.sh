#!/usr/bin/env sh
# Ajusta o limiar de despejo nodefs.available do nó kind para (livre agora - MARGEM_KB).
# Assim o cenário node-disk-pressure provoca DiskPressure com um enchimento fixo e pequeno
# (FILL_KB no manifesto, 3 GiB > margem de 1.5 GiB), sem encher o disco do host.
# Uso: harness/set-disk-threshold.sh <nome-do-container-do-no>
# NÃO verificado num kind real neste repositório (sem Docker/kind no ambiente de desenvolvimento).
set -eu
NODE="${1:?container do nó kind}"
MARGIN_KB=$((1536 * 1024))
# Imagens das cargas antes de medir: o pull não pode consumir a margem depois.
docker exec "$NODE" crictl pull busybox:1.36 >/dev/null
docker exec "$NODE" crictl pull python:3.12-alpine >/dev/null
avail_kb=$(docker exec "$NODE" df -Pk /var/lib/kubelet | awk 'NR==2{print $4}')
threshold_kb=$((avail_kb - MARGIN_KB))
if [ "$threshold_kb" -le 0 ]; then
  echo "erro: espaço livre insuficiente no nó (${avail_kb} KiB)" >&2
  exit 1
fi
docker exec "$NODE" sed -i "s|nodefs.available: .*|nodefs.available: ${threshold_kb}Ki|" /var/lib/kubelet/config.yaml
docker exec "$NODE" systemctl restart kubelet
sleep 10  # o status Ready anterior ao reinício não deve satisfazer o `kubectl wait` seguinte
echo "nodefs.available < ${threshold_kb}Ki (livre: ${avail_kb} KiB)"
