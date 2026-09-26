#!/usr/bin/env bash
# ops/setup_vps.sh — prepara a VPS para a Agência Nexo.
#
# GERADO NA F0. Revisar antes de executar. Executar como root, idempotente.
# O que faz:
#   1. instala gocryptfs, nftables e utilitários;
#   2. cria o usuário de sistema `nexo` (sem sudo, shell bash, sem senha) e o config do Codex dele
#      (~nexo/.codex/config.toml: sandbox workspace-write sem rede, hooks do projeto, sem busca web);
#   3. cria /srv/casos (ponto de montagem) sobre volume cifrado gocryptfs em
#      /srv/.casos.cifrado — a montagem é manual após reboot (ops/nexo_montar_casos.sh);
#   4. cria /var/log/agencia-nexo (auditoria dos hooks e saídas headless);
#   5. instala regras nftables por UID: o usuário `nexo` só sai para o Codex/ChatGPT (API e login)
#      e api.telegram.org (443) e DNS; o resto é registrado e descartado.
#      Carrega só a tabela `inet nexo_egress` por unidade própria (nexo-egress-nft.service):
#      não habilita nftables.service, cuja config padrão do Ubuntu esvazia o ruleset e apagaria o Docker;
#   6. instala o refresh periódico dos IPs permitidos (systemd timer);
#   7. clona/atualiza o repositório em /opt/agencia-nexo (root:root, só leitura para o nexo — o sandbox
#      do Codex grava no diretório de trabalho, e o nexo não pode alterar hooks nem agentes),
#      instala o uv em /usr/local/bin e roda
#      `uv sync --frozen` com o Python do sistema (como root, porque `nexo` não alcança PyPI/GitHub
#      por causa do item 5; Python gerenciado pelo uv ficaria em /root, ilegível pelo nexo).
#
# NÃO faz: login ChatGPT do Codex para o usuário `nexo` (ver "Depois de executar"). Chave de API: nunca.
set -euo pipefail

NEXO_USER="${NEXO_USER:-nexo}"
REPO_URL="${REPO_URL:-https://github.com/e-fabbro/Delegacia.git}"
REPO_DIR="${REPO_DIR:-/opt/agencia-nexo}"
CASOS_DIR="${CASOS_DIR:-/srv/casos}"
CIFRADO_DIR="${CIFRADO_DIR:-/srv/.casos.cifrado}"
LOG_DIR="${LOG_DIR:-/var/log/agencia-nexo}"
# Codex com login ChatGPT (backend, login e renovação do token) e Telegram para o canal.
HOSTS_PERMITIDOS="${HOSTS_PERMITIDOS:-chatgpt.com auth.openai.com api.openai.com api.telegram.org}"
HERMES_USER="${HERMES_USER:-}"   # usuário que roda o Hermes; vazio = não configura sudoers

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "execute como root" >&2
  exit 1
fi

echo ">> 1. pacotes"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq gocryptfs fuse3 nftables git python3.12 python3.12-venv curl ca-certificates \
  bubblewrap apparmor-profiles apparmor-utils >/dev/null
# Sandbox do Codex no Ubuntu 24.04: o Codex usa o bwrap do sistema; o perfil do próprio Ubuntu libera
# user namespace só para o bwrap (sem desligar kernel.apparmor_restrict_unprivileged_userns).
install -m 0644 /usr/share/apparmor/extra-profiles/bwrap-userns-restrict /etc/apparmor.d/bwrap-userns-restrict
apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict

echo ">> 2. usuário ${NEXO_USER}"
if ! id -u "${NEXO_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "/home/${NEXO_USER}" --shell /bin/bash "${NEXO_USER}"
fi
NEXO_UID="$(id -u "${NEXO_USER}")"
cat > "/home/${NEXO_USER}/.profile.d-agencia.sh" <<EOF
export AGENCIA_AUDIT_LOG=${LOG_DIR}/auditoria.jsonl
export PATH="${REPO_DIR}/.venv/bin:\$HOME/.local/bin:\$PATH"
EOF
grep -q '.profile.d-agencia.sh' "/home/${NEXO_USER}/.profile" 2>/dev/null \
  || echo '[ -f "$HOME/.profile.d-agencia.sh" ] && . "$HOME/.profile.d-agencia.sh"' >> "/home/${NEXO_USER}/.profile"
chown "${NEXO_USER}:${NEXO_USER}" "/home/${NEXO_USER}/.profile" "/home/${NEXO_USER}/.profile.d-agencia.sh"
# Codex do nexo: mesmas travas que ops/nexo_exec.sh passa por linha de comando (defesa em profundidade).
install -d -m 0700 -o "${NEXO_USER}" -g "${NEXO_USER}" "/home/${NEXO_USER}/.codex"
cat > "/home/${NEXO_USER}/.codex/config.toml" <<EOF
# Gerado por ops/setup_vps.sh. Runtime da Agência Nexo.
approval_policy = "never"
sandbox_mode = "workspace-write"
web_search = "disabled"

[features]
unified_exec = false
hooks = true
multi_agent = true
apps = false
plugins = false
remote_plugin = false
browser_use = false
browser_use_external = false
computer_use = false
in_app_browser = false
image_generation = false

[agents]
max_concurrent_threads_per_session = 3

[sandbox_workspace_write]
network_access = false
writable_roots = ["${CASOS_DIR}"]

[projects."${REPO_DIR}"]
trust_level = "trusted"
EOF
chown "${NEXO_USER}:${NEXO_USER}" "/home/${NEXO_USER}/.codex/config.toml"
chmod 0600 "/home/${NEXO_USER}/.codex/config.toml"

echo ">> 3. diretórios de casos (cifrado) e ${LOG_DIR}"
mkdir -p "${CIFRADO_DIR}" "${LOG_DIR}"
chown "${NEXO_USER}:${NEXO_USER}" "${CIFRADO_DIR}" "${LOG_DIR}"
chmod 700 "${CIFRADO_DIR}"
chmod 750 "${LOG_DIR}"
# Montado (sem allow_other), nem root enxerga o ponto de montagem: só prepara quando desmontado.
if sudo -u "${NEXO_USER}" mountpoint -q "${CASOS_DIR}"; then
  echo "   ${CASOS_DIR} montado; ponto de montagem mantido como está"
else
  mkdir -p "${CASOS_DIR}"
  chown "${NEXO_USER}:${NEXO_USER}" "${CASOS_DIR}"
  chmod 700 "${CASOS_DIR}"
fi
if [[ ! -f "${CIFRADO_DIR}/gocryptfs.conf" ]]; then
  echo "   inicializando volume gocryptfs em ${CIFRADO_DIR} (será pedida uma senha; guarde-a fora da VPS)"
  sudo -u "${NEXO_USER}" gocryptfs -init "${CIFRADO_DIR}"
fi
install -m 0755 "${AQUI}/nexo_montar_casos.sh" /usr/local/sbin/nexo-montar-casos
sed -i "s|^NEXO_USER=.*|NEXO_USER=\"${NEXO_USER}\"|; s|^CASOS_DIR=.*|CASOS_DIR=\"${CASOS_DIR}\"|; s|^CIFRADO_DIR=.*|CIFRADO_DIR=\"${CIFRADO_DIR}\"|" /usr/local/sbin/nexo-montar-casos

cat > /etc/logrotate.d/agencia-nexo <<EOF
# Auditoria dos hooks: guardada por 5 anos (dura mais que um inquérito); diretório do nexo exige `su`.
${LOG_DIR}/*.jsonl ${LOG_DIR}/*.log {
    su ${NEXO_USER} ${NEXO_USER}
    monthly
    rotate 60
    compress
    delaycompress
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
        meta l4proto icmp accept
        meta l4proto ipv6-icmp accept
        ct state established,related accept
        udp dport 53 accept
        tcp dport 53 accept
        ip daddr @permitidos_v4 tcp dport 443 accept
        ip6 daddr @permitidos_v6 tcp dport 443 accept
        log prefix "nexo-egress-drop " counter drop
    }
}
EOF
# Unidade própria: carrega só esta tabela no boot, sem tocar nas tabelas do Docker/UFW.
cat > /etc/systemd/system/nexo-egress-nft.service <<'EOF'
[Unit]
Description=Agência Nexo — tabela nftables inet nexo_egress (egress do usuário nexo)
Before=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/nftables.d/nexo-egress.nft
ExecStop=/usr/sbin/nft delete table inet nexo_egress

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable nexo-egress-nft.service >/dev/null
systemctl restart nexo-egress-nft.service   # recarrega a tabela (sets refeitos logo abaixo pelo refresh)

echo ">> 5. refresh periódico dos IPs permitidos"
install -m 0755 "${AQUI}/nexo_egress_refresh.sh" /usr/local/sbin/nexo-egress-refresh
sed -i "s|^HOSTS_PERMITIDOS=.*|HOSTS_PERMITIDOS=\"${HOSTS_PERMITIDOS}\"|" /usr/local/sbin/nexo-egress-refresh
cat > /etc/systemd/system/nexo-egress-refresh.service <<'EOF'
[Unit]
Description=Agência Nexo — atualiza IPs permitidos no nftables
After=network-online.target nexo-egress-nft.service
Requires=nexo-egress-nft.service
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
if [[ ! -x /usr/local/bin/uv ]]; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh >/dev/null
fi
(cd "${REPO_DIR}" && UV_PYTHON_PREFERENCE=only-system /usr/local/bin/uv sync --frozen --python /usr/bin/python3.12 --quiet)
chown -R root:root "${REPO_DIR}"
chmod -R a+rX,go-w "${REPO_DIR}"
if ! command -v codex >/dev/null 2>&1; then
  echo "   codex não encontrado; instale o Codex CLI (npm i -g @openai/codex) antes de usar o Nexo" >&2
fi

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
  - autenticar o Codex:         sudo -u ${NEXO_USER} -i codex login --device-auth   (login ChatGPT próprio do
                                nexo; nunca copiar auth.json de outro perfil. Se falhar, ver descartes abaixo)
  - conferir:                   sudo -u ${NEXO_USER} -i codex login status
  - testar o bloqueio:          sudo -u ${NEXO_USER} -i bash -c 'curl -sS -m 5 https://example.com || echo BLOQUEADO'
  - ver descartes:              journalctl -k | grep nexo-egress-drop
  - atualizar código:           como root, git -C ${REPO_DIR} pull && (cd ${REPO_DIR} && UV_PYTHON_PREFERENCE=only-system uv sync --frozen); depois chown -R root:root ${REPO_DIR}
EOF
