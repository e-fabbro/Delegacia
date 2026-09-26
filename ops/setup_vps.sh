#!/usr/bin/env bash
# ops/setup_vps.sh — prepara a VPS para a Agência Nexo.
#
# GERADO NA F0. Revisar antes de executar. Executar como root, idempotente.
# O que faz:
#   1. instala gocryptfs, nftables e utilitários;
#   2. cria o usuário de sistema `nexo` (sem sudo, shell bash, sem senha);
#   3. cria /srv/casos (ponto de montagem) sobre volume cifrado gocryptfs em
#      /srv/.casos.cifrado — a montagem é manual após reboot (ops/nexo_montar_casos.sh);
#   4. cria /var/log/agencia-nexo (auditoria dos hooks e saídas headless);
#   5. instala regras nftables por UID: o usuário `nexo` só sai para
#      api.anthropic.com e api.telegram.org (443) e DNS; o resto é registrado e descartado;
#   6. instala o refresh periódico dos IPs permitidos (systemd timer);
#   7. clona/atualiza o repositório em /opt/agencia-nexo e roda `uv sync` (como root,
#      porque `nexo` não alcança PyPI/GitHub por causa do item 5).
#
# NÃO faz: login do Claude Code para o usuário `nexo` (ver "Depois de executar").
set -euo pipefail

NEXO_USER="${NEXO_USER:-nexo}"
REPO_URL="${REPO_URL:-https://github.com/e-fabbro/Delegacia.git}"
REPO_DIR="${REPO_DIR:-/opt/agencia-nexo}"
CASOS_DIR="${CASOS_DIR:-/srv/casos}"
CIFRADO_DIR="${CIFRADO_DIR:-/srv/.casos.cifrado}"
LOG_DIR="${LOG_DIR:-/var/log/agencia-nexo}"
HOSTS_PERMITIDOS="${HOSTS_PERMITIDOS:-api.anthropic.com api.telegram.org}"
HERMES_USER="${HERMES_USER:-}"   # usuário que roda o Hermes; vazio = não configura sudoers

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "execute como root" >&2
  exit 1
fi

echo ">> 1. pacotes"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq gocryptfs fuse3 nftables git python3 ca-certificates >/dev/null

echo ">> 2. usuário ${NEXO_USER}"
if ! id -u "${NEXO_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "/home/${NEXO_USER}" --shell /bin/bash "${NEXO_USER}"
fi
NEXO_UID="$(id -u "${NEXO_USER}")"
# Claude Code: sem telemetria/tráfego não essencial (hosts bloqueados pelo item 5).
cat > "/home/${NEXO_USER}/.profile.d-agencia.sh" <<'EOF'
export DISABLE_TELEMETRY=1
export DISABLE_ERROR_REPORTING=1
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export AGENCIA_AUDIT_LOG=/var/log/agencia-nexo/auditoria.jsonl
export PATH="$HOME/.local/bin:$PATH"
EOF
grep -q '.profile.d-agencia.sh' "/home/${NEXO_USER}/.profile" 2>/dev/null \
  || echo '[ -f "$HOME/.profile.d-agencia.sh" ] && . "$HOME/.profile.d-agencia.sh"' >> "/home/${NEXO_USER}/.profile"
chown "${NEXO_USER}:${NEXO_USER}" "/home/${NEXO_USER}/.profile" "/home/${NEXO_USER}/.profile.d-agencia.sh"

echo ">> 3. diretórios de casos (cifrado) e ${LOG_DIR}"
mkdir -p "${CASOS_DIR}" "${CIFRADO_DIR}" "${LOG_DIR}"
chown "${NEXO_USER}:${NEXO_USER}" "${CASOS_DIR}" "${CIFRADO_DIR}" "${LOG_DIR}"
chmod 700 "${CASOS_DIR}" "${CIFRADO_DIR}"
chmod 750 "${LOG_DIR}"
if [[ ! -f "${CIFRADO_DIR}/gocryptfs.conf" ]]; then
  echo "   inicializando volume gocryptfs em ${CIFRADO_DIR} (será pedida uma senha; guarde-a fora da VPS)"
  sudo -u "${NEXO_USER}" gocryptfs -init "${CIFRADO_DIR}"
fi
install -m 0755 "${AQUI}/nexo_montar_casos.sh" /usr/local/sbin/nexo-montar-casos
sed -i "s|^NEXO_USER=.*|NEXO_USER=\"${NEXO_USER}\"|; s|^CASOS_DIR=.*|CASOS_DIR=\"${CASOS_DIR}\"|; s|^CIFRADO_DIR=.*|CIFRADO_DIR=\"${CIFRADO_DIR}\"|" /usr/local/sbin/nexo-montar-casos

cat > /etc/logrotate.d/agencia-nexo <<EOF
${LOG_DIR}/*.jsonl ${LOG_DIR}/*.log {
    weekly
    rotate 12
    compress
    missingok
    notifempty
    create 0640 ${NEXO_USER} ${NEXO_USER}
}
EOF

echo ">> 4. nftables: egress do UID ${NEXO_UID} restrito a ${HOSTS_PERMITIDOS}"
mkdir -p /etc/nftables.d
cat > /etc/nftables.d/nexo-egress.nft <<EOF
# Gerado por ops/setup_vps.sh. Só afeta pacotes originados pelo UID ${NEXO_UID} (${NEXO_USER}).
table inet nexo_egress
delete table inet nexo_egress
table inet nexo_egress {
    set permitidos_v4 { type ipv4_addr; flags timeout; timeout 24h; }
    set permitidos_v6 { type ipv6_addr; flags timeout; timeout 24h; }

    chain output {
        type filter hook output priority 0; policy accept;
        meta skuid != ${NEXO_UID} accept
        oif lo accept
        ct state established,related accept
        udp dport 53 accept
        tcp dport 53 accept
        ip daddr @permitidos_v4 tcp dport 443 accept
        ip6 daddr @permitidos_v6 tcp dport 443 accept
        log prefix "nexo-egress-drop " counter drop
    }
}
EOF
grep -q 'nftables.d/nexo-egress.nft' /etc/nftables.conf 2>/dev/null \
  || echo 'include "/etc/nftables.d/nexo-egress.nft"' >> /etc/nftables.conf
systemctl enable --now nftables >/dev/null
nft -f /etc/nftables.d/nexo-egress.nft

echo ">> 5. refresh periódico dos IPs permitidos"
install -m 0755 "${AQUI}/nexo_egress_refresh.sh" /usr/local/sbin/nexo-egress-refresh
sed -i "s|^HOSTS_PERMITIDOS=.*|HOSTS_PERMITIDOS=\"${HOSTS_PERMITIDOS}\"|" /usr/local/sbin/nexo-egress-refresh
cat > /etc/systemd/system/nexo-egress-refresh.service <<'EOF'
[Unit]
Description=Agência Nexo — atualiza IPs permitidos no nftables
After=network-online.target nftables.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/nexo-egress-refresh
EOF
cat > /etc/systemd/system/nexo-egress-refresh.timer <<'EOF'
[Unit]
Description=Agência Nexo — refresh dos IPs permitidos a cada 5 min

[Timer]
OnBootSec=30s
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now nexo-egress-refresh.timer >/dev/null
systemctl start nexo-egress-refresh.service

echo ">> 6. repositório em ${REPO_DIR}"
if [[ ! -d "${REPO_DIR}/.git" ]]; then
  git clone --quiet "${REPO_URL}" "${REPO_DIR}"
else
  git -C "${REPO_DIR}" pull --ff-only --quiet
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "   uv não encontrado; instale-o (https://docs.astral.sh/uv/) e rode: uv sync em ${REPO_DIR}" >&2
else
  (cd "${REPO_DIR}" && uv sync --python 3.12 --quiet)
fi
chown -R "${NEXO_USER}:${NEXO_USER}" "${REPO_DIR}"

if [[ -n "${HERMES_USER}" ]] && id -u "${HERMES_USER}" >/dev/null 2>&1; then
  echo ">> 7. sudoers: ${HERMES_USER} -> ${NEXO_USER} (só bash -lc, sem senha)"
  cat > /etc/sudoers.d/hermes-nexo <<EOF
${HERMES_USER} ALL=(${NEXO_USER}) NOPASSWD: /bin/bash -lc *
EOF
  chmod 0440 /etc/sudoers.d/hermes-nexo
  visudo -cf /etc/sudoers.d/hermes-nexo
fi

cat <<EOF

CONCLUÍDO.
Depois de executar:
  - montar o volume:            nexo-montar-casos            (a cada reboot; pede a senha)
  - autenticar o Claude Code:   sudo -u ${NEXO_USER} -i claude   (se usar login OAuth, liberar
                                temporariamente os hosts de login em HOSTS_PERMITIDOS ou usar ANTHROPIC_API_KEY)
  - testar o bloqueio:          sudo -u ${NEXO_USER} -i bash -c 'curl -sS -m 5 https://example.com || echo BLOQUEADO'
  - ver descartes:              journalctl -k | grep nexo-egress-drop
  - atualizar código:           como root, git -C ${REPO_DIR} pull && uv sync; depois chown -R ${NEXO_USER}
EOF
