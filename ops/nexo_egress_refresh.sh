#!/usr/bin/env bash
# ops/nexo_egress_refresh.sh — resolve os hosts permitidos e alimenta os sets do nftables.
# Instalado por setup_vps.sh em /usr/local/sbin/nexo-egress-refresh; roda a cada 5 min via timer.
# Os elementos têm timeout de 24h (definido no set), então IPs antigos expiram sozinhos e
# nunca há janela sem IP válido durante o refresh (acrescenta, não limpa).
set -euo pipefail

HOSTS_PERMITIDOS="api.anthropic.com api.telegram.org"
TABELA="inet nexo_egress"

for host in ${HOSTS_PERMITIDOS}; do
  v4="$(getent ahostsv4 "${host}" 2>/dev/null | awk '{print $1}' | sort -u | paste -sd, -)"
  v6="$(getent ahostsv6 "${host}" 2>/dev/null | awk '$1 ~ /:/ {print $1}' | sort -u | paste -sd, -)"
  if [[ -n "${v4}" ]]; then
    nft add element ${TABELA} permitidos_v4 "{ ${v4} }"
  fi
  if [[ -n "${v6}" ]]; then
    nft add element ${TABELA} permitidos_v6 "{ ${v6} }"
  fi
  if [[ -z "${v4}${v6}" ]]; then
    echo "nexo-egress-refresh: ${host} não resolveu" >&2
  fi
done
