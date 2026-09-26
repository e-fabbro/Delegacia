#!/usr/bin/env bash
# hermes/nexo_run.sh — ponte entre o perfil Hermes (Telegram) e a Agência Nexo no Claude Code.
#
# GERADO NA F5. Instalar em /usr/local/bin/nexo_run (root:root 755) e permitir no sudoers do usuário
# do Hermes:  <hermes_user> ALL=(nexo) NOPASSWD: /usr/local/bin/nexo_run
# O Hermes chama:  sudo -u nexo /usr/local/bin/nexo_run <acao> <CODINOME> [pergunta...]
#
# Ações curtas (status, novo, diligencias) respondem na hora com o campo `result`.
# Ações longas (ingerir, analisar) rodam em segundo plano e gravam /srv/casos/<COD>/log/run_<ts>.json;
# o Hermes avisa "em análise" e, ao encontrar o arquivo .done, envia o `result`.
# Nada além de codinome, pseudônimos, contagens e status deve sair por aqui.
set -euo pipefail

ACAO="${1:-}"; COD="${2:-}"; shift 2 || true
PERGUNTA="${*:-}"
REPO_DIR="${REPO_DIR:-/opt/agencia-nexo}"
CASOS_DIR="${AGENCIA_CASOS:-/srv/casos}"

if [[ -z "${ACAO}" || -z "${COD}" ]]; then
  echo '{"erro":"uso: nexo_run <status|novo|ingerir|analisar|diligencias> <CODINOME> [pergunta]"}'; exit 1
fi
if ! [[ "${COD}" =~ ^[A-Z][A-Z0-9_-]{1,31}$ ]]; then
  echo '{"erro":"codinome inválido"}'; exit 1
fi
# a pergunta é dado do Fabbro; remove quebras de linha e aspas para não virar comando
PERGUNTA="$(printf '%s' "${PERGUNTA}" | tr -d '\r\n"' | cut -c1-500)"

cd "${REPO_DIR}"
case "${ACAO}" in
  status)      CMD="/status ${COD}" ;;
  novo)        CMD="/caso-novo ${COD}" ;;
  diligencias) CMD="/diligencias ${COD}" ;;
  ingerir)     CMD="/ingerir ${COD}" ;;
  analisar)    CMD="/analisar ${COD} ${PERGUNTA}" ;;
  *) echo '{"erro":"ação desconhecida"}'; exit 1 ;;
esac

if [[ "${ACAO}" == "ingerir" || "${ACAO}" == "analisar" ]]; then
  if [[ ! -f "${CASOS_DIR}/${COD}/estado.json" ]]; then
    echo "{\"erro\":\"caso ${COD} não existe\"}"; exit 1
  fi
  LOG_DIR="${CASOS_DIR}/${COD}/log"; mkdir -p "${LOG_DIR}"
  TS="$(date +%s)"
  SAIDA="${LOG_DIR}/run_${TS}.json"
  nohup bash -c "claude -p \"${CMD}\" --output-format json > '${SAIDA}' 2> '${SAIDA%.json}.err'; touch '${SAIDA%.json}.done'" >/dev/null 2>&1 &
  echo "{\"status\":\"em_execucao\",\"caso\":\"${COD}\",\"acao\":\"${ACAO}\",\"resultado\":\"${SAIDA}\",\"pronto\":\"${SAIDA%.json}.done\"}"
else
  claude -p "${CMD}" --output-format json
fi
