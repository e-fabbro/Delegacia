#!/usr/bin/env bash
# hermes/nexo_run.sh — ponte entre o perfil Hermes (Telegram) e a Agência Nexo no Codex.
#
# Instalar em /usr/local/bin/nexo_run (root:root 755). O Hermes chama, como o usuário nexo:
#   nexo_run <acao> <CODINOME> [pergunta...]
# Quem chama é a ponte local hermes/ponte_nexo.py (systemd, usuário nexo); o perfil Hermes roda em container
# e fala com a ponte por 127.0.0.1 via hermes/agencia_cliente.py.
#
# Ações curtas (status, novo, diligencias) respondem na hora com o JSON de ops/nexo_exec.sh (campo `result`).
# Ações longas (ingerir, analisar) rodam em segundo plano e gravam /srv/casos/<COD>/log/run_<ts>.json;
# o Hermes avisa "em análise" e, ao encontrar o arquivo .done, envia o `result`.
# Nada além de codinome, pseudônimos, contagens e status deve sair por aqui.
set -euo pipefail

ACAO="${1:-}"; COD="${2:-}"
REPO_DIR="${REPO_DIR:-/opt/agencia-nexo}"
CASOS_DIR="${AGENCIA_CASOS:-/srv/casos}"
EXEC="${REPO_DIR}/ops/nexo_exec.sh"

if [[ -z "${ACAO}" || -z "${COD}" ]]; then
  echo '{"erro":"uso: nexo_run <status|novo|ingerir|analisar|diligencias> <CODINOME> [pergunta]"}'; exit 1
fi
if ! [[ "${COD}" =~ ^[A-Z][A-Z0-9_-]{1,31}$ ]]; then
  echo '{"erro":"codinome inválido"}'; exit 1
fi

case "${ACAO}" in
  status|novo|diligencias)
    exec bash "${EXEC}" "$@" ;;
  ingerir|analisar)
    if [[ ! -f "${CASOS_DIR}/${COD}/estado.json" ]]; then
      echo "{\"erro\":\"caso ${COD} não existe\"}"; exit 1
    fi
    LOG_DIR="${CASOS_DIR}/${COD}/log"; mkdir -p "${LOG_DIR}"
    SAIDA="${LOG_DIR}/run_$(date +%s%N).json"
    : > "${SAIDA}"   # existe antes da resposta: marca a execução em andamento (sem .done) sem corrida
    nohup bash -c 'bash "$1" "${@:3}" > "$2"; touch "${2%.json}.done"' _ "${EXEC}" "${SAIDA}" "$@" >/dev/null 2>&1 &
    echo "{\"status\":\"em_execucao\",\"caso\":\"${COD}\",\"acao\":\"${ACAO}\",\"resultado\":\"${SAIDA}\",\"pronto\":\"${SAIDA%.json}.done\"}"
    ;;
  *) echo '{"erro":"ação desconhecida"}'; exit 1 ;;
esac
