#!/usr/bin/env bash
# ops/nexo_montar_casos.sh — monta (ou desmonta) o volume cifrado dos casos.
# Instalado por setup_vps.sh em /usr/local/sbin/nexo-montar-casos. Uso manual após cada reboot.
#   nexo-montar-casos          monta (pede a senha do gocryptfs)
#   nexo-montar-casos -u       desmonta
# O volume é montado pelo próprio usuário nexo, sem allow_other: nem root vê o conteúdo em claro.
set -euo pipefail

NEXO_USER="nexo"
CASOS_DIR="/srv/casos"
CIFRADO_DIR="/srv/.casos.cifrado"

if [[ "${1:-}" == "-u" ]]; then
  sudo -u "${NEXO_USER}" fusermount3 -u "${CASOS_DIR}"
  echo "desmontado ${CASOS_DIR}"
  exit 0
fi

if mountpoint -q "${CASOS_DIR}"; then
  echo "${CASOS_DIR} já está montado"
  exit 0
fi

sudo -u "${NEXO_USER}" gocryptfs "${CIFRADO_DIR}" "${CASOS_DIR}"
echo "montado ${CASOS_DIR}"
