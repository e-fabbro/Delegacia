#!/usr/bin/env bash
# ops/instalar_ponte.sh — instala a ponte local Hermes → Agência Nexo (E3). Executar como root; idempotente.
#
# O que faz:
#   1. instala hermes/nexo_run.sh em /usr/local/bin/nexo_run (root:root 755);
#   2. gera o token da ponte em /etc/agencia-nexo/ponte.token (root:nexo 0640), se ainda não existir;
#   3. instala e liga hermes/ponte-nexo.service (usuário nexo, só 127.0.0.1:8791);
#   4. no perfil Hermes `nexo`: ferramenta scripts/agencia, cópia do token (0600), skill agencia-nexo
#      e uma linha no SOUL.md apontando para a skill;
#   5. confere a ponte com `agencia resultado TESTE` (não chama o Codex, não gasta cota).
# Não reinicia o gateway do Hermes: faça depois com `systemctl restart hermes-gateway-nexo`.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/agencia-nexo}"
NEXO_USER="${NEXO_USER:-nexo}"
PERFIL="${PERFIL:-/root/.hermes/profiles/nexo}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "execute como root" >&2
  exit 1
fi
[[ -d "${PERFIL}" ]] || { echo "perfil Hermes não encontrado: ${PERFIL}" >&2; exit 1; }

echo ">> 1. nexo_run"
install -m 0755 -o root -g root "${REPO_DIR}/hermes/nexo_run.sh" /usr/local/bin/nexo_run

echo ">> 2. token da ponte"
install -d -m 0750 -o root -g "${NEXO_USER}" /etc/agencia-nexo
if [[ ! -s /etc/agencia-nexo/ponte.token ]]; then
  python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > /etc/agencia-nexo/ponte.token
fi
chown root:nexo /etc/agencia-nexo/ponte.token
chmod 0640 /etc/agencia-nexo/ponte.token

echo ">> 3. serviço ponte-nexo"
install -m 0644 "${REPO_DIR}/hermes/ponte-nexo.service" /etc/systemd/system/ponte-nexo.service
systemctl daemon-reload
systemctl enable ponte-nexo.service >/dev/null
systemctl restart ponte-nexo.service

echo ">> 4. perfil Hermes ${PERFIL}"
install -d -m 0755 "${PERFIL}/scripts" "${PERFIL}/skills/agencia-nexo"
install -m 0755 "${REPO_DIR}/hermes/agencia_cliente.py" "${PERFIL}/scripts/agencia"
install -m 0600 /etc/agencia-nexo/ponte.token "${PERFIL}/scripts/.ponte_token"
install -m 0644 "${REPO_DIR}/hermes/skills/agencia-nexo/SKILL.md" "${PERFIL}/skills/agencia-nexo/SKILL.md"
LINHA='Para qualquer pedido de análise de caso por codinome (criar, ingerir, analisar, status, diligências, resultado), use obrigatoriamente a skill `agencia-nexo`. Você não lê arquivos de caso: só a ferramenta `scripts/agencia`.'
grep -qF 'skill `agencia-nexo`' "${PERFIL}/SOUL.md" || printf '\n%s\n' "${LINHA}" >> "${PERFIL}/SOUL.md"

echo ">> 5. conferência (sem Codex)"
sleep 1
"${PERFIL}/scripts/agencia" resultado TESTE || true

cat <<EOF

CONCLUÍDO.
  - reiniciar o Nexo do Hermes:  systemctl restart hermes-gateway-nexo
  - ver a ponte:                 systemctl status ponte-nexo --no-pager
  - trocar o token:              rm /etc/agencia-nexo/ponte.token && bash ${REPO_DIR}/ops/instalar_ponte.sh
EOF
